// Client-PC Network Stack
// Reads Linux evdev controller input and sends controller state to the server
// over TCP using a length-prefixed JSON+CRC32 protocol.
//
// Wire format: [4-byte big-endian length][JSON payload][4-byte CRC32]
// Requires Linux: go get golang.org/x/sys/unix

package main

import (
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"hash/crc32"
	"log"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
)

// ============================================================================
// Constants
// ============================================================================

const (
	DefaultPort = 8080
	SendRateHz  = 33 // ~30ms between sends

	// evdev event types
	EvKey = 0x01
	EvAbs = 0x03

	// Analog axes (linux/input-event-codes.h)
	AbsX     = 0x00
	AbsY     = 0x01
	AbsZ     = 0x02
	AbsRX    = 0x03
	AbsRY    = 0x04
	AbsRZ    = 0x05
	AbsGas   = 0x09 // right trigger
	AbsBrake = 0x0A // left trigger
	AbsHat0X = 0x10
	AbsHat0Y = 0x11

	// Gamepad buttons (linux/input-event-codes.h)
	BtnSouth  = 0x130
	BtnEast   = 0x131
	BtnNorth  = 0x133
	BtnWest   = 0x134
	BtnTL     = 0x136
	BtnTR     = 0x137
	BtnTL2    = 0x138
	BtnTR2    = 0x139
	BtnSelect = 0x13a
	BtnStart  = 0x13b
	BtnThumbL = 0x13d
	BtnThumbR = 0x13e

	// Aliases for X/Y swap logic when yNorth is enabled
	BtnX = 0x133
	BtnY = 0x134
)

// MaxPacketSize is the upper bound for a single JSON payload in bytes.
var MaxPacketSize = 8192

// ============================================================================
// Types
// ============================================================================

// ControllerState holds all gamepad inputs sent to the server.
type ControllerState struct {
	Source       string `json:"source,omitempty"`
	North        uint8  `json:"N"`
	East         uint8  `json:"E"`
	South        uint8  `json:"S"`
	West         uint8  `json:"W"`
	LeftBumper   uint8  `json:"LB"`
	RightBumper  uint8  `json:"RB"`
	LeftStick    uint8  `json:"LS"`
	RightStick   uint8  `json:"RS"`
	Select       uint8  `json:"SELECT"`
	Start        uint8  `json:"START"`
	LeftX        uint8  `json:"LjoyX"`
	LeftY        uint8  `json:"LjoyY"`
	RightX       uint8  `json:"RjoyX"`
	RightY       uint8  `json:"RjoyY"`
	LeftTrigger  uint8  `json:"LT"`
	RightTrigger uint8  `json:"RT"`
	DPadX        int8   `json:"dX"`
	DPadY        int8   `json:"dY"`
	Timestamp    int64  `json:"ts"`
	Seq          uint32 `json:"seq"`
}

func (c *ControllerState) String() string {
	source := c.Source
	if source == "" {
		source = "pc"
	}
	return fmt.Sprintf(
		"Source:%s Btns[N:%d E:%d S:%d W:%d LB:%d RB:%d SEL:%d START:%d] "+
			"Joy[LX:%d LY:%d RX:%d RY:%d] Trig[L:%d R:%d] DPad[%d,%d]",
		source,
		c.North, c.East, c.South, c.West,
		c.LeftBumper, c.RightBumper, c.Select, c.Start,
		c.LeftX, c.LeftY, c.RightX, c.RightY,
		c.LeftTrigger, c.RightTrigger,
		c.DPadX, c.DPadY,
	)
}

// inputEvent matches the Linux kernel struct input_event layout.
type inputEvent struct {
	Time  unix.Timeval
	Type  uint16
	Code  uint16
	Value int32
}

// absInfo holds axis calibration data from the EVIOCGABS ioctl.
type absInfo struct {
	Value      int32
	Min        int32
	Max        int32
	Fuzz       int32
	Flat       int32
	Resolution int32
}

// evdevDevice wraps a file descriptor to a /dev/input/event* node.
type evdevDevice struct {
	fd       int
	path     string
	absCache map[uint16]absInfo
	yNorth   bool
	debug    bool
}

// ============================================================================
// CRC (IEEE CRC-32)
// ============================================================================

func ComputeCRC(data []byte) uint32 {
	return crc32.ChecksumIEEE(data)
}

// AppendCRC appends a 4-byte big-endian CRC32 to the payload.
func AppendCRC(data []byte) []byte {
	c := ComputeCRC(data)
	out := make([]byte, len(data)+4)
	copy(out, data)
	binary.BigEndian.PutUint32(out[len(data):], c)
	return out
}

// ============================================================================
// TCP helpers
// ============================================================================

// writeAll writes the entire buffer to conn, handling partial writes.
func writeAll(conn net.Conn, buf []byte) error {
	written := 0
	for written < len(buf) {
		n, err := conn.Write(buf[written:]) //part of the buffer is successfully written witn n value
		if err != nil {
			return err
		}
		if n == 0 {
			return fmt.Errorf("tcp write returned 0 bytes")
		}
		written += n
	}
	return nil
}

// ============================================================================
// evdev helpers
// ============================================================================

// evioCGAbs computes the ioctl number for EVIOCGABS(axis).
func evioCGAbs(axis uint) uintptr {
	const (
		iocRead      = 2
		iocNrbits    = 8
		iocTypebits  = 8
		iocSizebits  = 14
		iocDirshift  = iocNrbits + iocTypebits + iocSizebits
		iocTypeshift = iocNrbits
		iocSizeshift = iocNrbits + iocTypebits
	)
	ioc := func(dir, typ, nr, size uintptr) uintptr {
		return (dir << iocDirshift) | (typ << iocTypeshift) | (nr << 0) | (size << iocSizeshift)
	}
	return ioc(iocRead, 0x45, uintptr(0x40)+uintptr(axis), unsafe.Sizeof(absInfo{}))
}

// normalizeAbs maps a raw axis value to 0..255 using the device's min/max range.
// Returns (0, false) for DPad hats which are handled separately.
func (d *evdevDevice) normalizeAbs(code uint16, v int32) (uint8, bool) {
	if code == AbsHat0X || code == AbsHat0Y {
		return 0, false
	}

	info, ok := d.absCache[code] //sroes the axis info
	if !ok {
		var ai absInfo
		_, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(d.fd), evioCGAbs(uint(code)), uintptr(unsafe.Pointer(&ai)))
		if errno != 0 {
			return 127, true
		}
		d.absCache[code] = ai
		info = ai
	}

	den := float64(info.Max - info.Min)
	if den <= 0 {
		return 127, true
	}
	norm := (float64(v-info.Min) / den) * 255.0
	if norm < 0 {
		norm = 0
	}
	if norm > 255 {
		norm = 255
	}
	return uint8(norm), true
}

func openEvdev(path string, yNorth bool, debug bool) (*evdevDevice, error) {
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	return &evdevDevice{
		fd:       fd,
		path:     path,
		absCache: make(map[uint16]absInfo),
		yNorth:   yNorth,
		debug:    debug,
	}, nil
}

func (d *evdevDevice) close() {
	_ = unix.Close(d.fd)
}

// readEvent reads one input event from the device (non-blocking).
func (d *evdevDevice) readEvent() (inputEvent, bool, error) {
	var ev inputEvent
	buf := (*[unsafe.Sizeof(ev)]byte)(unsafe.Pointer(&ev))[:]
	n, err := unix.Read(d.fd, buf)
	if err != nil {
		if err == unix.EAGAIN || err == unix.EWOULDBLOCK {
			return inputEvent{}, false, nil
		}
		return inputEvent{}, false, err
	}
	if n != len(buf) {
		return inputEvent{}, false, fmt.Errorf("short read: got %d, want %d", n, len(buf))
	}
	return ev, true, nil
}

// readDeviceName reads the human-friendly name from sysfs.
func readDeviceName(path string) string {
	base := filepath.Base(path)
	namePath := filepath.Join("/sys/class/input", base, "device/name")
	data, err := os.ReadFile(namePath)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func logSelectedDevice(path string) {
	name := readDeviceName(path)
	if name != "" {
		log.Printf("Using evdev device: %s (%s)", path, name)
	} else {
		log.Printf("Using evdev device: %s", path)
	}
}

// findEvdevController opens the requested controller device, or falls back to
// the first available /dev/input/event* device when no path is provided.
func findEvdevController(devicePath string, yNorth bool, debug bool) (*evdevDevice, error) {
	if devicePath != "" {
		d, err := openEvdev(devicePath, yNorth, debug)
		if err != nil {
			return nil, fmt.Errorf("open controller device %s: %w", devicePath, err)
		}
		logSelectedDevice(devicePath)
		return d, nil
	}

	paths, err := filepath.Glob("/dev/input/event*")
	if err != nil || len(paths) == 0 {
		return nil, fmt.Errorf("no /dev/input/event* devices found")
	}
	for _, p := range paths {
		d, err := openEvdev(p, yNorth, debug)
		if err == nil {
			logSelectedDevice(p)
			return d, nil
		}
	}
	return nil, fmt.Errorf("could not open any /dev/input/event*")
}

// ============================================================================
// Core logic
// ============================================================================

// readController polls the evdev device and sends state to the server at SendRateHz.
func readController(dev *evdevDevice, conn net.Conn) error {
	ticker := time.NewTicker(time.Second / SendRateHz)
	defer ticker.Stop()

	var nextSeq uint32 = 1

	sendLog, err := os.OpenFile("sent_packets.jsonl", os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0644)
	if err != nil {
		log.Printf("Warning: could not open send log: %v", err)
	} else {
		defer sendLog.Close()
	}

	state := &ControllerState{
		Source: "pc",
		LeftX:  127, LeftY: 127,
		RightX: 127, RightY: 127,
	}

	for range ticker.C {
		// Drain all pending events to get the freshest state
		for {
			ev, ok, err := dev.readEvent()
			if err != nil {
				return fmt.Errorf("evdev read: %w", err)
			}
			if !ok {
				break
			}
			applyEvent(dev, state, ev)
		}

		state.Timestamp = time.Now().UnixMilli()
		state.Seq = nextSeq
		nextSeq++

		b, err := json.Marshal(state)
		if err != nil {
			return fmt.Errorf("marshal state: %w", err)
		}
		if len(b) > MaxPacketSize {
			log.Printf("state too large (%d bytes), skipping send", len(b))
			continue
		}

		pkt := AppendCRC(b)
		crc := binary.BigEndian.Uint32(pkt[len(b):])
		hdr := make([]byte, 4)
		binary.BigEndian.PutUint32(hdr, uint32(len(pkt)))

		if err := writeAll(conn, hdr); err != nil {
			return fmt.Errorf("write header: %w", err)
		}
		if err := writeAll(conn, pkt); err != nil {
			return fmt.Errorf("write packet: %w", err)
		}

		if sendLog != nil {
			fmt.Fprintf(sendLog, "{\"seq\":%d,\"crc32\":%d,\"sent_at\":%d}\n", state.Seq, crc, state.Timestamp)
		}

		fmt.Println(state)
	}

	return nil
}

// applyEvent updates the controller state from a single evdev event.
func applyEvent(dev *evdevDevice, state *ControllerState, ev inputEvent) {
	switch ev.Type {
	case EvAbs:
		code := uint16(ev.Code)
		val := ev.Value

		// DPad hats: clamp to -1/0/1
		if code == AbsHat0X {
			state.DPadX = clampHat(val)
			return
		}
		if code == AbsHat0Y {
			state.DPadY = clampHat(val)
			return
		}

		// Analog axes: normalize to 0..255
		norm, isAnalog := dev.normalizeAbs(code, val)
		if !isAnalog {
			return
		}
		if dev.debug {
			log.Printf("ABS code=0x%X value=%d norm=%d", code, val, norm)
		}

		switch code {
		case AbsX:
			state.LeftX = norm
		case AbsY:
			state.LeftY = norm
		case AbsRX:
			state.RightX = norm
		case AbsRY:
			state.RightY = norm
		case AbsZ, AbsBrake:
			state.LeftTrigger = norm
		case AbsRZ, AbsGas:
			state.RightTrigger = norm
		}

	case EvKey:
		code := uint16(ev.Code)
		pressed := uint8(0)
		if ev.Value != 0 {
			pressed = 1
		}
		if dev.debug {
			log.Printf("KEY code=0x%X value=%d", code, ev.Value)
		}

		// Swap X/Y when yNorth is enabled
		if dev.yNorth {
			if code == BtnX {
				code = BtnY
			} else if code == BtnY {
				code = BtnX
			}
		}

		switch code {
		case BtnNorth:
			state.North = pressed
		case BtnEast:
			state.East = pressed
		case BtnSouth:
			state.South = pressed
		case BtnWest:
			state.West = pressed
		case BtnTL:
			state.LeftBumper = pressed
		case BtnTR:
			state.RightBumper = pressed
		case BtnSelect:
			state.Select = pressed
		case BtnStart:
			state.Start = pressed
		case BtnThumbL:
			state.LeftStick = pressed
		case BtnThumbR:
			state.RightStick = pressed
		}
	}
}

func clampHat(val int32) int8 {
	if val < -1 {
		return -1
	}
	if val > 1 {
		return 1
	}
	return int8(val)
}

// runClient connects to the server and streams controller state.
func runClient(serverAddr string, devicePath string, yNorth bool, debug bool) error {
	conn, err := net.Dial("tcp", serverAddr)
	if err != nil {
		return err
	}
	defer conn.Close()

	log.Println("Connected to server")

	for {
		dev, err := findEvdevController(devicePath, yNorth, debug)
		if err != nil {
			log.Println("Waiting for controller (evdev)...")
			log.Printf("Controller error: %v", err)
			time.Sleep(2 * time.Second)
			continue
		}

		err = readController(dev, conn)
		dev.close()

		if err != nil {
			if strings.Contains(err.Error(), "broken pipe") {
				return fmt.Errorf("server disconnected")
			}
			log.Printf("Controller error: %v", err)
			time.Sleep(time.Second)
		}
	}
}

// ============================================================================
// main
// ============================================================================

func main() {
	serverAddr := flag.String("server", fmt.Sprintf("localhost:%d", DefaultPort), "Server address")
	devicePath := flag.String("device", "", "evdev controller path, for example /dev/input/by-id/...-event-joystick")
	yNorth := flag.Bool("y-north", true, "Swap X/Y mapping so Y acts as North")
	debugEvents := flag.Bool("debug-events", false, "Log raw evdev events for debugging")
	flag.Parse()

	if flag.NArg() > 0 {
		*serverAddr = flag.Arg(0)
	}
	if !strings.Contains(*serverAddr, ":") {
		*serverAddr = fmt.Sprintf("%s:%d", *serverAddr, DefaultPort)
	}

	log.Printf("Connecting to %s (Ctrl+C to stop)", *serverAddr)

	for {
		if err := runClient(*serverAddr, *devicePath, *yNorth, *debugEvents); err != nil {
			log.Printf("Connection error: %v", err)
		}
		time.Sleep(3 * time.Second)
	}
}
