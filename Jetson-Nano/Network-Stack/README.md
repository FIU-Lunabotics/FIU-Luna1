# FIU Lunabotics Controller System (Go Implementation)

## Overview
The **Lunabotics Controller System** is a real-time control and telemetry layer designed to connect a human-operated controller (e.g., DualShock 4) to the rover’s microcontroller through a modular and configurable communication interface.

This folder focuses on the Server that runs on the Jetson (or other onboard computer). The server listens for framed JSON controller state packets, verifies CRC, maps the JSON to Arduino bytes using a configurable `ByteFormatter`, and writes bytes to the Arduino over serial.

---

## Quick start — Server (Jetson)

Build (on Jetson or cross-compile):

```bash
cd jetson-server
go build -o server_bin .

# or from repo root
go build -o bin/jetson-server ./jetson-server
```

Run (example):

```bash
./server_bin -serial-device /dev/ttyACM0 -serial-crc
```

### Server behavior
- Location: `server.go` in this folder.
- Purpose: listen for controller packets, verify CRC, format bytes and send to serial Arduino.

- Default start (port 8080):

	go run server.go crc.go

- To select a different port:

	go run server.go crc.go -port 18080

- Behavior details:
	- Reads framed packets from TCP: 4-byte big-endian length N, then N bytes (payload + 4-byte CRC).
	- Verifies CRC before decoding JSON payload. If CRC fails, packet is dropped and logged.
	- Converts verified ControllerState JSON into Arduino bytes using `ByteFormatter` and writes to serial (default `/dev/ttyACM0`) or logs in debug mode.

---

## CRC support (crc.go)
- File: `crc.go` (included in this folder).
- Algorithm: CRC-32 using polynomial 0x04C11DB7 (IEEE). Implemented using `crc32.ChecksumIEEE`.
- API:
	- `var MaxPacketSize` — maximum payload size in bytes (default 8192). Adjust as needed.
	- `ComputeCRC([]byte) uint32` — compute CRC for a payload.
	- `AppendCRC([]byte) []byte` — returns payload with 4 CRC bytes appended (big-endian).
	- `VerifyPacket([]byte) (payload []byte, ok bool)` — given payload+crc, returns payload and whether CRC matched.

Server flags related to serial handling:
- `-serial-device` : set serial device path (default `/dev/ttyACM0`).
- `-serial-crc` : if set, the server will append a 4-byte CRC to the bytes written to the Arduino.
- `-serial-ack` : if set, the server will expect a 1-byte ACK (0x06) from the Arduino after each write (uses serial port read timeout).

---

## ByteFormatter templates (configurable JSON)
- The server converts JSON ControllerState into a fixed-length byte array for Arduino using `ByteFormatter` and a `ByteConfig`.
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
- Start server with `-config <file>` to load a different byte mapping config:

	go run server.go crc.go -config byte_config_8byte.json

---

## Notes & troubleshooting
- Multi-binary layout: the client lives in `client-pc/` and contains a local `crc.go`. Both targets ship their own CRC helper and ByteFormatter usage; this keeps the deployments self-contained.
- If `go run server.go` fails with port in use, pick another port with `-port`.
- `MaxPacketSize` guards the server from very large or malicious packet lengths; increase it only if necessary.

If you'd like the CRC and ByteFormatter code shared as a package instead of duplicated, I can refactor the repo to a shared `crc` package and a shared `byteformatter` package — tell me if you want that.
