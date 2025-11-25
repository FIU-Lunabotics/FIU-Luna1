package main

import (
    "encoding/binary"
    "encoding/json"
    "flag"
    "fmt"
    "math"
    "math/rand"
    "net"
    "time"

    
)

// Must match server.go's JSON field names
type ControllerState struct {
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

    LeftX        uint8 `json:"LjoyX"`
    LeftY        uint8 `json:"LjoyY"`
    RightX       uint8 `json:"RjoyX"`
    RightY       uint8 `json:"RjoyY"`
    LeftTrigger  uint8 `json:"LT"`
    RightTrigger uint8 `json:"RT"`
    DPadX        int8  `json:"dX"`
    DPadY        int8  `json:"dY"`

    Timestamp int64 `json:"ts"`
}

// simple wave 0..255 centered on 127 for pretty output
func wave(t float64, phase float64) uint8 {
    s := 0.5 + 0.5*math.Sin(2*math.Pi*(t+phase))
    return uint8(s * 255.0)
}

func main() {
    server := flag.String("server", "127.0.0.1:8080", "server address host:port")
    hz := flag.Float64("hz", 33, "send frequency")
    random := flag.Bool("random", false, "send random values instead of smooth wave")
    flag.Parse()

    conn, err := net.Dial("tcp", *server)
    if err != nil {
        panic(err)
    }
    defer conn.Close()
    fmt.Println("Connected to", *server)

    ticker := time.NewTicker(time.Duration(float64(time.Second) / *hz))
    defer ticker.Stop()
    start := time.Now()

    for range ticker.C {
        elapsed := time.Since(start).Seconds()

        var lx, ly, rx, ry, lt, rt uint8
        if *random {
            lx = uint8(rand.Intn(256))
            ly = uint8(rand.Intn(256))
            rx = uint8(rand.Intn(256))
            ry = uint8(rand.Intn(256))
            lt = uint8(rand.Intn(256))
            rt = uint8(rand.Intn(256))
        } else {
            lx = wave(elapsed, 0)
            ly = wave(elapsed, 0.25)
            rx = wave(elapsed, 0.5)
            ry = wave(elapsed, 0.75)
            lt = wave(elapsed, 0.1)
            rt = wave(elapsed, 0.6)
        }

        state := ControllerState{
            North:       0,
            East:        0,
            South:       0,
            West:        0,
            LeftBumper:  0,
            RightBumper: 0,
            LeftStick:   0,
            RightStick:  0,
            Select:      0,
            Start:       0,
            LeftX:       lx,
            LeftY:       ly,
            RightX:      rx,
            RightY:      ry,
            LeftTrigger: lt,
            RightTrigger: rt,
            DPadX:       0,
            DPadY:       0,
            Timestamp:   time.Now().UnixMilli(),
        }

        b, err := json.Marshal(&state)
        if err != nil {
            fmt.Println("marshal error:", err)
            continue
        }

        if len(b) > crc.MaxPacketSize {
            fmt.Printf("payload %d too large, skipping\n", len(b))
            continue
        }

        pkt := crc.AppendCRC(b)
        totalLen := uint32(len(pkt))
        hdr := make([]byte, 4)
        binary.BigEndian.PutUint32(hdr, totalLen)

        if _, err := conn.Write(hdr); err != nil {
            fmt.Println("write header error:", err)
            return
        }
        if _, err := conn.Write(pkt); err != nil {
            fmt.Println("write packet error:", err)
            return
        }

        fmt.Println(state)
    }
}
