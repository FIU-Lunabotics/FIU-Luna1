# FIU Lunabotics Controller System (Go Implementation)

## Overview
The **Lunabotics Controller System** is a real-time control and telemetry layer designed to connect a human-operated controller (e.g., DualShock 4) to the rover’s microcontroller through a modular and configurable communication interface.

This folder focuses on the Client. The client runs on the operator workstation, reads the joystick, and transmits framed JSON controller state packets to the server at ~33 Hz.

---

## Quick start — Client (PC)

Build:

```bash
cd client-pc
go build -o client_bin .
```

Run (connects to server):

```bash
./client_bin -server <server-host>:8080
```

Use the mock client to simulate a controller for testing:

```bash
go run mock_client.go crc.go -server 127.0.0.1:8080
```

### Client behavior
- Location: `client.go` (real controller) and `mock_client.go` (simulated input).
- Purpose: read controller or generate test data, send JSON payloads to server.

### Sending format (on the wire)
1) 4-byte big-endian uint32 = length (payload + 4 CRC bytes)
2) payload bytes (JSON-marshalled ControllerState)
3) 4-byte big-endian CRC-32 (CRC computed over the payload only)

Example: if payload is 5 bytes, the wire will be: [00 00 00 09] [5 payload bytes] [4 CRC bytes].

---

## CRC support (crc.go)
- File: `crc.go` (included in this folder).
- Algorithm: CRC-32 using polynomial 0x04C11DB7 (IEEE). Implemented using `crc32.ChecksumIEEE`.
- API:
  - `var MaxPacketSize` — maximum payload size in bytes (default 8192). Adjust as needed.
  - `ComputeCRC([]byte) uint32` — compute CRC for a payload.
  - `AppendCRC([]byte) []byte` — returns payload with 4 CRC bytes appended (big-endian).
  - `VerifyPacket([]byte) (payload []byte, ok bool)` — given payload+crc, returns payload and whether CRC matched.

---

## ByteFormatter templates (configurable JSON)
- The server converts JSON ControllerState into a fixed-length byte array for Arduino using `ByteFormatter` and a `ByteConfig` (see `jetson-server` for details).
- Config structure (high level):
  - `output_size` (int): total bytes in the formatted packet.
  - `bytes` (array): list of per-byte mappings. Each entry maps one output byte and has a `type` field.

Supported byte mapping types
1) `const`
	- Fields: `value` (0-255)
	- Behavior: the output byte is the constant value.

2) `field`
	- Fields: `field` (string, name of ControllerState field)
	- Behavior: output byte = value of the named field (uint8 fields are used directly).

3) `bits`
	- Fields: `bits` (array of `{pos, field}`)
	- Behavior: construct an output byte by setting bits at positions `pos` when the corresponding ControllerState `field` is non-zero.

Default templates included in the repo root:
- `byte_config.json` — a 6-byte Python-compatible format.
- `byte_config_8byte.json` — an 8-byte extended format.

Switching templates
- The server chooses the ByteFormatter template with the `-config <file>` flag. See `jetson-server` README for details.

---

## Notes & troubleshooting
- Multi-binary layout: `client.go` and `mock_client.go` are both `package main` with `main()` in this folder. Build them separately.
- If the server port is in use, run the client with another port using `-server <host>:<port>`.
- `MaxPacketSize` guards the client from sending very large payloads.

---

If you want this client to import a shared `crc` package instead of the local `crc.go`, I can refactor that — but this folder currently contains its own `crc.go` so the client is self-contained.
