# Server-Pi Network Stack – Architecture and Extension Guide

## Overview

This document explains how the `server.go` file in the **Server-Pi Network Stack** is organized, how its components interact, and how to extend it for new TCP clients (for example, ROS nodes) while reusing the existing reliability and safety mechanisms.

The server runs on the Raspberry Pi on the rover and:

- Listens for TCP connections from multiple clients (PC controller, Jetson status, future ROS nodes).
- Receives framed JSON packets with CRC32 integrity protection.
- Logs packets in **batches** to a JSONL file when any error occurs in that batch.
- Converts valid `ControllerState` packets into a fixed-size byte array according to a configurable mapping, then forwards those bytes to the Arduino over serial.
- Debounces controller-driven **mode change** requests and writes them to a request file for the rover FSM.
- Optionally mirrors raw Arduino telemetry to external TCP subscribers.

---

## Wire Protocol

The on-the-wire format is:

```text
[4-byte big-endian length] [JSON payload] [4-byte CRC32]
```

- **Length** is the size in bytes of `JSON payload + CRC32`, not including the length prefix itself.
- **CRC32** is the IEEE CRC-32 of the JSON payload only.

On receive, the server:

1. Reads 4 bytes to get `length`.
2. Reads `length` bytes into a buffer.
3. Splits the buffer into `payload` and `crc`.
4. Recomputes CRC32 over `payload` and compares with `crc`.
5. If CRC matches, attempts to parse JSON.
6. Routes the parsed JSON to one of:
   - `StatusPacket` (from Jetson / status clients).
   - `ControllerState` (from gamepad / control clients).

---

## File Layout and Constants

At the top of `server.go`, constants and top-level config values are declared:

- `ArduinoPort` – default serial device (`/dev/ttyACM0`).
- `BaudRate` – serial baud rate (9600).
- `BatchSize` – how many packets are grouped into a logging batch (10).
- `roverStateFilePath` – path where the rover FSM publishes its current mode.
- `roverStateRequestFilePath` – path where the server writes mode change requests.
- `roverStateMaxAge` – any rover state older than this is treated as unsafe.
- `stateChangeHoldDuration` – how long `SELECT` must be held before a mode request is allowed.
- `MaxPacketSize` – upper bound for JSON payload size in bytes (8192 by default).

These values control safety behavior, log granularity, and serial settings.

---

## Protocol Types

### `ControllerState`

```go
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
```

Represents **one sampled controller frame** from a client:

- Buttons and sticks are packed into numeric fields.
- `Seq` is a monotonically increasing sequence number used for gap detection.
- `Timestamp` is the client-side time in milliseconds.
- `Source` identifies the client (e.g., `pc`, `jetson`, or the TCP remote address).

This struct is the primary input to the **Byte Formatter** and **StateSwitchTracker**.

### `StatusPacket`

```go
type StatusPacket struct {
    Type      string `json:"type"`
    Source    string `json:"source"`
    Message   string `json:"message"`
    Timestamp int64  `json:"ts"`
}
```

Lightweight JSON used by Jetson or other status publishers. A packet is considered a status packet when `Type == "status"`.

These are **not** forwarded to the Arduino; they are only printed and logged as `StatusOK` with the CRC value.

---

## Byte Formatting Types

### `ByteConfig`

```go
type ByteConfig struct {
    OutputSize int           `json:"output_size"`
    Bytes      []ByteMapping `json:"bytes"`
}

type ByteMapping struct {
    Type  string       `json:"type"`            // "const", "field", or "bits"
    Value uint8        `json:"value,omitempty"` // for "const"
    Field string       `json:"field,omitempty"` // for "field"
    Bits  []BitMapping `json:"bits,omitempty"`  // for "bits"
}

type BitMapping struct {
    Pos   uint8  `json:"pos"`
    Field string `json:"field"`
}
```

`ByteConfig` describes how to convert a `ControllerState` into a fixed-length byte array for the Arduino. The default JSON config (or `DefaultConfig()`) is an 8-byte layout:

```json
{
  "output_size": 8,
  "bytes": [
    { "type": "const", "value": 255 },
    { "type": "bits", "bits": [ ... ] },
    { "type": "bits", "bits": [ ... ] },
    { "type": "field", "field": "LjoyY" },
    { "type": "field", "field": "RjoyY" },
    { "type": "field", "field": "LT" },
    { "type": "field", "field": "RT" },
    { "type": "const", "value": 255 }
  ]
}
```

- `const` – fixed literal byte value (e.g., framing bytes).
- `field` – copies a named field from the `ControllerState`.
- `bits` – builds a byte by OR-ing individual bits if specific fields are non-zero (e.g., button pressed).

### `ByteFormatter`

```go
type ByteFormatter struct {
    Config *ByteConfig
}
```

#### `DefaultConfig()`

Returns the built-in 8-byte configuration equivalent to the JSON above. This is used when `-config` is not provided or fails to load.

#### `Format(state *ControllerState) []byte`

- Ensures `Config` is non-nil (falling back to `DefaultConfig`).
- Allocates an output buffer of length `OutputSize`.
- For each `ByteMapping`:
  - `const`: writes the static byte.
  - `field`: reads the mapped field via `getFieldValue` and writes it.
  - `bits`: builds a bitfield byte by checking a list of `BitMapping`s.
- Returns the filled byte slice.

This function is called from `handleClient` after a valid `ControllerState` is decoded and before sending to the Arduino.

#### `LoadConfig(filename string) (*ByteConfig, error)`

Reads a custom JSON config from disk and unmarshals it into `ByteConfig`. This allows changing the Arduino byte-level protocol without recompiling the server.

---

## Packet Logging

### Types

```go
type PacketStatus string

const (
    StatusOK        PacketStatus = "OK"
    StatusCRCFail   PacketStatus = "CRC_FAIL"
    StatusJSONError PacketStatus = "JSON_ERROR"
    StatusSizeError PacketStatus = "SIZE_ERROR"
)

type PacketLog struct {
    Seq        uint32       `json:"seq"`
    CRC32      uint32       `json:"crc32"`
    ReceivedAt int64        `json:"received_at"`
    Status     PacketStatus `json:"status"`
    RawPayload string       `json:"raw_payload,omitempty"`
}

type Batch struct {
    Packets  [BatchSize]PacketLog
    Count    int
    HasError bool
}

type BatchLogger struct {
    mu        sync.Mutex
    current   Batch
    logFile   *os.File
    lastSeq   uint32
    seqInited bool
}
```

### `NewBatchLogger(path string)`

- Opens (or creates) the JSONL log file.
- Returns a `BatchLogger` that buffers up to `BatchSize` entries at a time.

### `Record(entry PacketLog)`

- Acquires a mutex to protect batch state.
- Maintains `lastSeq` to detect sequence gaps.
- If `entry.Status != StatusOK`, marks the current batch as having an error.
- Stores the entry into the `Batch.Packets` array.
- When `Count` reaches `BatchSize`, calls `flush()`.

### `flush()`

- If `HasError` is `true`, writes all packets in the batch as line-delimited JSON entries.
- Appends an empty line as a **batch separator**.
- Resets `current` to an empty `Batch`.

### `Close()`

- Flushes any partial error batch if needed and closes the underlying log file.

### `NewErrorLog(status PacketStatus, rawBytes []byte)`

- Convenience function used on CRC or other failures to create a `PacketLog` with `RawPayload` filled using base64-encoded raw bytes.

### Where it is used

`handleClient()` creates a `BatchLogger` per TCP client connection and calls `Record()` for:

- CRC failures.
- JSON parse failures.
- Size violations.
- Successful packets (`StatusOK`).

Only batches containing at least one non-OK packet are persisted, which keeps logs compact and focused on anomalies.

---

## Serial Manager and Telemetry Hub

### `SerialManager`

```go
type SerialManager struct {
    mu              sync.Mutex
    port            serial.Port
    appendCRC       bool
    expectAck       bool
    debugOnly       bool
    lastOpenFailure time.Time
    device          string
    telemetryHub    *TelemetryHub
}
```

Responsible for writing bytes to the Arduino and optionally reading back telemetry lines.

#### `openArduino(device string) (serial.Port, error)`

- Wraps `serial.Open` with fixed serial mode (8N1, configured baud).
- Sets a short read timeout for non-blocking reads.

#### `NewSerialManager(device string, appendCRC, expectAck bool, telemetryHub *TelemetryHub)`

- Attempts to open the Arduino serial port.
- If successful:
  - Stores the port and flags.
  - Spawns `telemetryReadLoop()` to stream lines into the `TelemetryHub`.
- If opening fails:
  - Logs a message and enters **debug mode** where no writes will occur until a later reconnect.

#### `Write(source string, data []byte)`

- Mutex-protected.
- If port is nil, attempts reconnect via `reconnectLocked()`.
- Optionally appends a CRC to the outgoing bytes before writing.
- Uses `serialWriteAll` to guarantee full-buffer writes.
- If `expectAck` is true, reads one byte and verifies it equals `0x06`.
- On any error, closes the port and sets `debugOnly` so future writes are suppressed until reconnection.

#### `telemetryReadLoop()`

- Runs continuously in a goroutine.
- Reads raw bytes from the Arduino, buffering them until newline (`\n`).
- For each complete line, trims whitespace and publishes it to the `TelemetryHub`.
- On read errors, closes the port and periodically attempts reconnection.

### `TelemetryHub`

```go
type TelemetryHub struct {
    mu          sync.RWMutex
    subscribers map[chan string]struct{}
}
```

A simple pub/sub for telemetry lines:

- `Subscribe()` returns a channel and an `unsubscribe` function.
- `Publish(line string)` pushes a line to all subscribers, dropping stale messages if a client is slow.

### Telemetry TCP server

- `startTelemetryTCPServer(addr string, hub *TelemetryHub)` listens on `telemetry-port`.
- Each accepted client is handled by `handleTelemetryClient`, which:
  - Subscribes to the hub.
  - Writes each line with a trailing newline to the TCP connection.

This allows external tools (e.g., desktop apps or ROS nodes) to tail Arduino telemetry without touching the serial port.

---

## Rover State Integration and Mode Requests

### `readRoverState()`

- Reads `/tmp/rover_state`.
- Expected format: `STATE_NAME,timestamp`.
- Valid states are `IDLE`, `TELEOP`, `AUTO`.
- Returns `(stateName, timestamp, ok)`.

### `validRoverState(stateName string) bool`

- Currently only returns `true` for `TELEOP`.
- Any other mode blocks serial writes.

### `newRoverState(stateTimestamp int64) bool`

- Treats state as valid only if `0 <= age <= roverStateMaxAge`.
- Prevents stale `TELEOP` files from leaving the rover in a moving-enabled state.

### `StateSwitchTracker`

```go
type StateSwitchTracker struct {
    selectHeldSince time.Time
    requestIssued   bool
}
```

#### `controllerRequestedMode(state *ControllerState) (string, bool)`

Maps face buttons to modes:

- `North` -> `TELEOP`.
- `East` -> `AUTO`.
- `West` -> `IDLE`.

#### `writeStateRequest(mode string, timestamp int64, source string, seq uint32) error`

- Atomically writes a request file to `/tmp/rover_state_request` using a temp file and `os.Rename`.
- The file format is: `mode,timestamp,source,seq`.

#### `(t *StateSwitchTracker) Handle(state *ControllerState) (string, bool, error)`

Implements the **"hold SELECT then press button"** gesture:

1. If `SELECT == 0`, resets internal state and returns.
2. If `SELECT` just became non-zero, records `selectHeldSince`.
3. Only if `SELECT` has been held longer than `stateChangeHoldDuration` and no request has been issued:
   - Calls `controllerRequestedMode` to see if a face button is pressed.
   - If so, calls `writeStateRequest` and marks `requestIssued`.

`handleClient` uses this after each valid `ControllerState` to queue requests for the rover FSM.

---

## TCP Handling and Orchestration

### `formatBytes(data []byte) string`

Utility function that returns a human-readable hex string (e.g., `"FF 01 02 03"`) for debug logging of Arduino payloads.

### `tryParseStatusPacket(payload []byte) (*StatusPacket, bool)`

- Attempts to unmarshal the payload as a `StatusPacket`.
- If `Type != "status"` or JSON is invalid, returns `(nil, false)`.
- On success, fills missing `Source` with `"unknown"` and returns the packet.

### `handleClient(conn net.Conn, formatter *ByteFormatter, serialMgr *SerialManager, logPath string)`

This is the **core per-connection loop**. For each client:

1. Logs connection details.
2. Creates a `BatchLogger` tied to the provided `logPath`.
3. Creates a `StateSwitchTracker`.
4. Enters an infinite loop:
   - Reads 4-byte length prefix.
   - Validates the total length (non-zero, under `MaxPacketSize + 4`).
   - Reads exactly `totalLen` bytes.
   - Extracts the wire CRC.
   - Calls `VerifyPacket` to validate CRC.
   - On CRC failure:
     - Logs and records a `StatusCRCFail` entry with base64 payload.
     - Continues to next packet.
   - On CRC success:
     - First tries `tryParseStatusPacket`:
       - If `status` packet, prints and records `StatusOK` in the batch log, then continues.
     - Otherwise, unmarshals into `ControllerState`:
       - On JSON error, records `StatusJSONError` and continues.
       - Fills `Source` if missing.
       - Records `StatusOK` with sequence.
       - Passes the state to `StateSwitchTracker.Handle` for potential mode changes.
       - Uses `formatter.Format` to create Arduino bytes.
       - Calls `readRoverState` and applies `newRoverState` & `validRoverState` gating.
       - If rover state is valid and fresh:
         - Logs controller state and Arduino bytes at a throttled rate.
         - Calls `serialMgr.Write` to send bytes to the Arduino.

This function orchestrates:

- **Integrity** (CRC and size checks).
- **Classification** (status vs. controller).
- **Safety gating** (rover mode and age).
- **Logging** (batch logging of all outcomes).
- **Hardware IO** (serial writes).

### `main()`

Entry point wiring everything together:

1. Parses CLI flags:
   - `-port` – primary TCP server port.
   - `-telemetry-port` – optional TCP telemetry mirror port.
   - `-public` – binds to `0.0.0.0` instead of `localhost`.
   - `-config` – path to byte-mapping JSON.
   - `-serial-device`, `-serial-crc`, `-serial-ack` – serial options.
   - `-packet-log` – path to packet error log.
2. Loads the byte-mapping config via `LoadConfig` or `DefaultConfig`.
3. Starts the main TCP listener on `addr`.
4. Creates a `TelemetryHub` and optional telemetry TCP listener.
5. Constructs a `SerialManager`.
6. Accepts client connections in a loop and spawns a goroutine per connection to run `handleClient`.

This setup means multiple control or status clients can coexist, each handled independently, sharing the same serial and logging infrastructure.

---

## Adding a New Client Type (e.g., ROS Node)

To add a new client type that talks to this server (such as a ROS Noetic node), you do **not** need to modify `server.go` as long as you:

1. Respect the wire format: length prefix, JSON payload, CRC32 trailer.
2. Use JSON shapes that the server can understand:
   - Either `StatusPacket` (`{"type":"status", ...}`).
   - Or a superset of `ControllerState`, with all required fields present.
3. Optionally use a different TCP port by running a separate server binary (see below) or by starting the main server with a different `-port` value.

### Option 1 – Reuse Existing Server, New Client

If your ROS node wants to act like an additional controller client:

- Connect to the same `-port` and generate `ControllerState` JSON.
- Sequence numbers (`seq`) can be independent per client; the batch logger will still catch gaps per connection.
- Ensure CRC is computed over the JSON bytes only.

If it wants to send status messages:

- Use `{"type":"status", "source":"ros_node", "message":"...", "ts": <millis>}`.

### Option 2 – Run a Second Server Binary on a Different Port

If you want a fully separate port and possibly a different byte-mapping config (e.g., for ROS-specific actuation or a different Arduino protocol):

1. Build the same `server.go` into a second binary or reuse with different flags.
2. Run it with a different `-port` and `-config`:

   ```bash
   ./server -port 9090 -config ros_byte_config.json -serial-device /dev/ttyACM1
   ```

3. Your ROS node connects to `9090` and sends packets using the same wire framing.

Because all the safety, logging, CRC, and serial handling live inside `server.go`, you get those guarantees for free.

---

## How to Extend the Server for New Behavior

You can extend or specialize behavior in a few ways:

1. **New JSON message types**:
   - In `handleClient`, after CRC validation, inspect the payload before defaulting to `ControllerState`.
   - For example, define a `TelemetryCommand` struct and add a `tryParseTelemetryCommand(payload)` helper similar to `tryParseStatusPacket`.

2. **Alternative byte mapping**:
   - Create a new JSON config file describing a different `ByteConfig`.
   - Run the server with `-config path/to/new_config.json`.
   - No code changes required.

3. **Different gating rules**:
   - Modify `validRoverState` or `newRoverState` to change when serial writes are allowed.
   - Or add new modes and adjust `controllerRequestedMode` and the FSM integration.

4. **Additional telemetry channels**:
   - Use `TelemetryHub` to broadcast other sources (e.g., internal debug events) to TCP clients.

The existing functions are intentionally decomposed into small units (CRC, byte formatting, logging, serial, TCP helpers, rover state logic, client handler) to make surgical extensions straightforward without breaking the overall architecture.

---

## Summary

- The server is a **framed JSON over TCP** multiplexer that feeds a safety-gated serial link.
- Packet integrity and anomalies are tracked via CRC, size checks, and batch logging.
- Arduino communication is configurable via a JSON byte-mapping layer.
- Rover mode gating and SELECT+button gestures ensure safe transitions between `IDLE`, `TELEOP`, and `AUTO`.
- Telemetry is fan-out via a hub to avoid multiple processes competing for the serial port.
- New clients (PC, Jetson, ROS) only need to implement the same wire protocol and JSON fields to integrate cleanly.
