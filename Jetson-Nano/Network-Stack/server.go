package main

import (
    "encoding/binary"
    "encoding/json"
    "flag"
    "fmt"
    "io"
    "log"
    "net"
    "os"
    "time"

    "go.bug.st/serial"
)

const (
    ARDUINO_PORT = "/dev/ttyACM0"
    BAUD_RATE    = 9600
)

// ControllerState matches client state
type ControllerState struct {
    North        uint8 `json:"N"`
    East         uint8 `json:"E"`
    South        uint8 `json:"S"`
    West         uint8 `json:"W"`
    LeftBumper   uint8 `json:"LB"`
    RightBumper  uint8 `json:"RB"`
    LeftStick    uint8 `json:"LS"`
    RightStick   uint8 `json:"RS"`
    Select       uint8 `json:"SELECT"`
    Start        uint8 `json:"START"`
    LeftX        uint8 `json:"LjoyX"`
    LeftY        uint8 `json:"LjoyY"`
    RightX       uint8 `json:"RjoyX"`
    RightY       uint8 `json:"RjoyY"`
    LeftTrigger  uint8 `json:"LT"`
    RightTrigger uint8 `json:"RT"`
    DPadX        int8  `json:"dX"`
    DPadY        int8  `json:"dY"`
    Timestamp    int64 `json:"ts"`
}

// ByteFormatter handles conversion from controller state to Arduino bytes
type ByteFormatter struct {
    Config *ByteConfig
}

// ByteConfig defines the byte mapping configuration
type ByteConfig struct {
    OutputSize int           `json:"output_size"`
    Bytes      []ByteMapping `json:"bytes"`
}

// ByteMapping defines how each byte is constructed
type ByteMapping struct {
    Type  string       `json:"type"`            // "const", "field", "bits"
    Value uint8        `json:"value,omitempty"` // For const
    Field string       `json:"field,omitempty"` // For field mapping
    Bits  []BitMapping `json:"bits,omitempty"`  // For bitmask
}

// BitMapping maps a bit position to a field
type BitMapping struct {
    Pos   uint8  `json:"pos"`   // 0-7
    Field string `json:"field"` // Field name from ControllerState
}

// DefaultConfig returns the Python-compatible 6-byte format
func DefaultConfig() *ByteConfig {
    return &ByteConfig{
        OutputSize: 6,
        Bytes: []ByteMapping{
            {
                Type: "bits",
                Bits: []BitMapping{
                    {Pos: 0, Field: "W"},
                    {Pos: 1, Field: "E"},
                    {Pos: 2, Field: "S"},
                },
            },
            {Type: "field", Field: "LjoyX"},
            {Type: "field", Field: "LjoyY"},
            {Type: "field", Field: "RjoyY"},
            {Type: "field", Field: "RT"},
            {
                Type: "bits",
                Bits: []BitMapping{
                    {Pos: 5, Field: "LB"},
                    {Pos: 6, Field: "RB"},
                    {Pos: 7, Field: "N"},
                },
            },
        },
    }
}

// Format converts controller state to Arduino bytes
func (f *ByteFormatter) Format(state *ControllerState) []byte {
    if f.Config == nil {
        f.Config = DefaultConfig()
    }

    // Pre-fill with Python-compatible start/end bytes
    output := make([]byte, f.Config.OutputSize)
    if f.Config.OutputSize == 6 {
        output[0] = 0b10101000 // Default start byte
        output[5] = 0b00010101 // Default end byte
    }

    // Build each byte according to config
    for i, byteMap := range f.Config.Bytes {
        if i >= len(output) {
            break
        }

        switch byteMap.Type {
        case "const":
            output[i] = byteMap.Value

        case "field":
            output[i] = f.getFieldValue(state, byteMap.Field)

        case "bits":
            var b uint8
            if f.Config.OutputSize == 6 && (i == 0 || i == 5) {
                // Preserve default bits for Python compatibility
                b = output[i]
            }
            for _, bit := range byteMap.Bits {
                if f.getFieldValue(state, bit.Field) != 0 {
                    b |= (1 << bit.Pos)
                }
            }
            output[i] = b
        }
    }

    return output
}

// getFieldValue gets value from state by field name
func (f *ByteFormatter) getFieldValue(state *ControllerState, field string) uint8 {
    switch field {
    case "N":
        return state.North
    case "E":
        return state.East
    case "S":
        return state.South
    case "W":
        return state.West
    case "LB":
        return state.LeftBumper
    case "RB":
        return state.RightBumper
    case "LS":
        return state.LeftStick
    case "RS":
        return state.RightStick
    case "SELECT":
        return state.Select
    case "START":
        return state.Start
    case "LjoyX":
        return state.LeftX
    case "LjoyY":
        return state.LeftY
    case "RjoyX":
        return state.RightX
    case "RjoyY":
        return state.RightY
    case "LT":
        return state.LeftTrigger
    case "RT":
        return state.RightTrigger
    case "dX":
        return uint8(state.DPadX)
    case "dY":
        return uint8(state.DPadY)
    default:
        return 0
    }
}

// LoadConfig loads configuration from file
func LoadConfig(filename string) (*ByteConfig, error) {
    data, err := os.ReadFile(filename)
    if err != nil {
        return nil, err
    }

    var config ByteConfig
    if err := json.Unmarshal(data, &config); err != nil {
        return nil, err
    }

    return &config, nil
}

// openArduino opens serial connection
// openArduino opens serial connection to the given device path.
func openArduino(device string) (serial.Port, error) {
    if device == "" {
        device = ARDUINO_PORT
    }
    mode := &serial.Mode{
        BaudRate: BAUD_RATE,
        DataBits: 8,
        StopBits: serial.OneStopBit,
        Parity:   serial.NoParity,
    }

    port, err := serial.Open(device, mode)
    if err != nil {
        return nil, err
    }

    port.SetReadTimeout(100 * time.Millisecond)
    return port, nil
}

// handleClient processes client connection
func handleClient(conn net.Conn, formatter *ByteFormatter, serialAppendCRC bool, serialAck bool, serialDevice string) {
    defer conn.Close()

    log.Printf("Client connected: %s", conn.RemoteAddr())

    arduino, err := openArduino(serialDevice)
    if err != nil {
        log.Printf("Arduino not connected: %v (debug mode)", err)
    } else {
        defer arduino.Close()
        log.Println("Arduino connected")
    }

    lastPrint := time.Now()

    for {
        // Read 4-byte length prefix
        hdr := make([]byte, 4)
        if _, err := io.ReadFull(conn, hdr); err != nil {
            if err == io.EOF {
                log.Printf("Client disconnected")
                return
            }
            log.Printf("Read header error: %v", err)
            return
        }
        totalLen := binary.BigEndian.Uint32(hdr)
        if totalLen == 0 {
            log.Printf("Zero-length packet, skipping")
            continue
        }
        if totalLen > uint32(crc.MaxPacketSize+4) { // payload + crc shouldn't exceed MaxPacketSize+4
            log.Printf("Packet too large: %d bytes (max %d)", totalLen, crc.MaxPacketSize+4)
            // Drain and continue (attempt to read and discard)
            if _, err := io.CopyN(io.Discard, conn, int64(totalLen)); err != nil {
                log.Printf("drain error: %v", err)
                return
            }
            continue
        }

        buf := make([]byte, totalLen)
        if _, err := io.ReadFull(conn, buf); err != nil {
            log.Printf("Read packet error: %v", err)
            return
        }

        payload, ok := crc.VerifyPacket(buf)
        if !ok {
            log.Printf("CRC mismatch from %s, dropping packet", conn.RemoteAddr())
            continue
        }

        var state ControllerState
        if err := json.Unmarshal(payload, &state); err != nil {
            log.Printf("JSON unmarshal error: %v", err)
            continue
        }

        // Format to Arduino bytes
        data := formatter.Format(&state)

        // Debug print every second
        if time.Since(lastPrint) > time.Second {
            fmt.Printf("State: %v\n", &state)
            fmt.Printf("Arduino bytes: [")
            for i, b := range data {
                if i > 0 {
                    fmt.Printf(" ")
                }
                fmt.Printf("%02X", b)
            }
            fmt.Printf("]\n")
            lastPrint = time.Now()
        }

        // Send to Arduino (optionally append CRC and optionally expect ACK)
        if arduino != nil {
            out := data
            if serialAppendCRC {
                out = crc.AppendCRC(data)
            }
            if err := writeAll(arduino, out); err != nil {
                log.Printf("Arduino write error: %v", err)
                arduino.Close()
                arduino = nil
                continue
            }

            if serialAck {
                // read one-byte ACK (0x06) with the port's read timeout
                ack := make([]byte, 1)
                n, err := arduino.Read(ack)
                if err != nil {
                    log.Printf("Arduino ack read error: %v", err)
                } else if n == 0 {
                    log.Printf("Arduino ack timeout (no data)")
                } else if ack[0] != 0x06 {
                    log.Printf("Unexpected Arduino ack: 0x%02X", ack[0])
                }
            }
        }
    }
}

// writeAll writes the full buffer to the serial port, handling partial writes.
func writeAll(port serial.Port, buf []byte) error {
    written := 0
    for written < len(buf) {
        n, err := port.Write(buf[written:])
        if err != nil {
            return err
        }
        if n == 0 {
            return fmt.Errorf("serial write returned 0 bytes")
        }
        written += n
    }
    return nil
}

func main() {
    port := flag.Int("port", 8080, "Server port")
    public := flag.Bool("public", false, "Allow external connections")
    configFile := flag.String("config", "", "Byte mapping config file")
    serialCRC := flag.Bool("serial-crc", false, "Append CRC32 to bytes sent over serial")
    serialAck := flag.Bool("serial-ack", false, "Expect 1-byte ACK (0x06) from serial device after each packet")
    serialDevice := flag.String("serial-device", ARDUINO_PORT, "Serial device path to write to (overrides ARDUINO_PORT)")
    flag.Parse()

    // Load configuration
    formatter := &ByteFormatter{}
    if *configFile != "" {
        config, err := LoadConfig(*configFile)
        if err != nil {
            log.Printf("Config load failed, using defaults: %v", err)
        } else {
            formatter.Config = config
            log.Printf("Loaded config: %d bytes output", config.OutputSize)
        }
    } else {
        formatter.Config = DefaultConfig()
        log.Println("Using default 6-byte format")
    }

    // Setup listener
    addr := fmt.Sprintf("localhost:%d", *port)
    if *public {
        addr = fmt.Sprintf("0.0.0.0:%d", *port)
    }

    listener, err := net.Listen("tcp", addr)
    if err != nil {
        log.Fatal(err)
    }
    defer listener.Close()

    log.Printf("Server listening on %s", addr)

    // Accept connections
    for {
        conn, err := listener.Accept()
        if err != nil {
            log.Printf("Accept error: %v", err)
            continue
        }

        // open Arduino per-connection so each handler can manage reconnects
        go func(c net.Conn, dev string) {
            handleClient(c, formatter, *serialCRC, *serialAck, dev)
        }(conn, *serialDevice)
    }
}
