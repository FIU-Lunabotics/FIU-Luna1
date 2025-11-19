package main

import (
    "encoding/binary"
    "encoding/json"
    "flag"
    "fmt"
    "log"
    "net"
    "strings"
    "time"

    "github.com/0xcafed00d/joystick"
)

const (
    DEFAULT_PORT = 8080
    SEND_RATE_HZ = 33 // ~30ms between sends
)

// ControllerState holds all controller inputs
type ControllerState struct {
    // Buttons (0 or 1)
    North       uint8 `json:"N"`
    East        uint8 `json:"E"`
    South       uint8 `json:"S"`
    West        uint8 `json:"W"`
    LeftBumper  uint8 `json:"LB"`
    RightBumper uint8 `json:"RB"`
    LeftStick   uint8 `json:"LS"`
    RightStick  uint8 `json:"RS"`
    Select      uint8 `json:"SELECT"`
    Start       uint8 `json:"START"`

    // Axes (0-255)
    LeftX        uint8 `json:"LjoyX"`
    LeftY        uint8 `json:"LjoyY"`
    RightX       uint8 `json:"RjoyX"`
    RightY       uint8 `json:"RjoyY"`
    LeftTrigger  uint8 `json:"LT"`
    RightTrigger uint8 `json:"RT"`
    DPadX        int8  `json:"dX"`
    DPadY        int8  `json:"dY"`

    // Metadata
    Timestamp int64 `json:"ts"`
}

func (c *ControllerState) String() string {
    return fmt.Sprintf("Btns[N:%d E:%d S:%d W:%d] Joy[LX:%d LY:%d RX:%d RY:%d] Trig[L:%d R:%d]",
        c.North, c.East, c.South, c.West,
        c.LeftX, c.LeftY, c.RightX, c.RightY,
        c.LeftTrigger, c.RightTrigger)
}

// readController continuously reads joystick and sends state over connection
func readController(js joystick.Joystick, conn net.Conn) error {
    ticker := time.NewTicker(time.Second / SEND_RATE_HZ)
    defer ticker.Stop()

    state := &ControllerState{}

    for range ticker.C {
        jsState, err := js.Read()
        if err != nil {
            return fmt.Errorf("reading joystick: %w", err)
        }

        // Map axes (convert from int16 to uint8)
        if len(jsState.AxisData) > 0 {
            state.LeftX = uint8((int32(jsState.AxisData[0]) + 32768) >> 8)
        }
        if len(jsState.AxisData) > 1 {
            state.LeftY = uint8((int32(jsState.AxisData[1]) + 32768) >> 8)
        }
        if len(jsState.AxisData) > 2 {
            state.RightX = uint8((int32(jsState.AxisData[2]) + 32768) >> 8)
        }
        if len(jsState.AxisData) > 3 {
            state.RightY = uint8((int32(jsState.AxisData[3]) + 32768) >> 8)
        }
        if len(jsState.AxisData) > 4 {
            state.LeftTrigger = uint8((int32(jsState.AxisData[4]) + 32768) >> 8)
        }
        if len(jsState.AxisData) > 5 {
            state.RightTrigger = uint8((int32(jsState.AxisData[5]) + 32768) >> 8)
        }

        // Map buttons
        state.South = uint8((jsState.Buttons >> 0) & 1)
        state.East = uint8((jsState.Buttons >> 1) & 1)
        state.West = uint8((jsState.Buttons >> 2) & 1)
        state.North = uint8((jsState.Buttons >> 3) & 1)
        state.LeftBumper = uint8((jsState.Buttons >> 4) & 1)
        state.RightBumper = uint8((jsState.Buttons >> 5) & 1)
        state.Select = uint8((jsState.Buttons >> 6) & 1)
        state.Start = uint8((jsState.Buttons >> 7) & 1)
        state.LeftStick = uint8((jsState.Buttons >> 8) & 1)
        state.RightStick = uint8((jsState.Buttons >> 9) & 1)

        state.Timestamp = time.Now().UnixMilli()

        // Marshal JSON without newline
        b, err := json.Marshal(state)
        if err != nil {
            return fmt.Errorf("marshal state: %w", err)
        }

        if len(b) > crc.MaxPacketSize {
            // Skip sending if exceeding configured max
            log.Printf("state too large (%d bytes), skipping send", len(b))
            continue
        }

        pkt := crc.AppendCRC(b)
        totalLen := uint32(len(pkt))
        hdr := make([]byte, 4)
        binary.BigEndian.PutUint32(hdr, totalLen)

        if _, err := conn.Write(hdr); err != nil {
            return fmt.Errorf("write header: %w", err)
        }
        if _, err := conn.Write(pkt); err != nil {
            return fmt.Errorf("write packet: %w", err)
        }

        fmt.Println(state)
    }

    return nil
}

func findController() (joystick.Joystick, error) {
    for i := 0; i < 4; i++ {
        js, err := joystick.Open(i)
        if err == nil {
            name := js.Name()
            log.Printf("Controller found: %s", name)

            return js, nil
        }
    }
    return nil, fmt.Errorf("no controller found")
}

func runClient(serverAddr string) error {
    conn, err := net.Dial("tcp", serverAddr)
    if err != nil {
        return err
    }
    defer conn.Close()

    log.Println("Connected to server")

    for {
        js, err := findController()
        if err != nil {
            log.Println("Waiting for controller...")
            time.Sleep(2 * time.Second)
            continue
        }
        defer js.Close()

        if err := readController(js, conn); err != nil {
            js.Close()
            if strings.Contains(err.Error(), "broken pipe") {
                return fmt.Errorf("server disconnected")
            }
            log.Printf("Controller error: %v", err)
            time.Sleep(time.Second)
        }
    }
}

func main() {
    serverAddr := flag.String("server", fmt.Sprintf("localhost:%d", DEFAULT_PORT), "Server address")
    flag.Parse()

    if flag.NArg() > 0 {
        *serverAddr = flag.Arg(0)
    }

    if !strings.Contains(*serverAddr, ":") {
        *serverAddr = fmt.Sprintf("%s:%d", *serverAddr, DEFAULT_PORT)
    }

    log.Printf("Connecting to %s (Ctrl+C to stop)", *serverAddr)

    for {
        if err := runClient(*serverAddr); err != nil {
            log.Printf("Connection error: %v", err)
        }
        time.Sleep(3 * time.Second)
    }
}
