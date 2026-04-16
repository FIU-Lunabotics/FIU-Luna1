# Client-PC GUI

## What this is

This dashboard is the operator-side GUI for live controller monitoring. It
understands the same wire protocol used by the Go clients and the Pi server:

```
[4-byte big-endian length] [JSON payload] [4-byte CRC32]
```

It can run in two useful modes:

- **Monitor mode**: accept packets and visualize them locally.
- **Proxy mode**: accept packets, visualize them, and forward them unchanged to
  the real `Server-Pi/Network-Stack` server.

Proxy mode is the easiest way to test the GUI with the existing repo code,
because the Go clients keep talking their normal protocol while the dashboard
shows everything live.

## Install

```bash
cd Client-PC/GUI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run in proxy mode

Start the Pi server first, then start the dashboard in front of it.

```bash
cd Server-Pi/Network-Stack
go run . -public -port 8080
```

```bash
cd Client-PC/GUI
python dashboard.py --listen-port 8090 --forward-to 127.0.0.1:8080
```

Then point the repo clients at the dashboard listener instead of the server:

```bash
cd Client-PC/Network-Stack
go run . -server 127.0.0.1:8090
```

```bash
cd Client-Jetson/Network-Stack
go run . -server 127.0.0.1:8090
```

Open the web UI at:

```bash
http://127.0.0.1:8050
```

## Run as a desktop app

If you do not want to use a browser tab, launch the dashboard in desktop mode:

```bash
cd Client-PC/GUI
python dashboard.py --desktop --listen-port 8090 --ui-port 8050 --forward-to 127.0.0.1:8080
```

This opens the Dash UI inside a native window using `pywebview`.

## Run in monitor-only mode

```bash
cd Client-PC/GUI
python dashboard.py --listen-port 8090
```

This is useful if you want to test packet parsing without forwarding to a live
server.

## Hall telemetry graph

The repo also includes a small Hall-effect telemetry UI for issue `#19`. It is
built with the same Dash stack as `dashboard.py`, but it focuses on serial/log
feedback lines that contain Hall states such as `101`.

Install the extra GUI dependencies if needed:

```bash
cd Client-PC/GUI
pip install -r requirements.txt
```

Run against a serial port:

```bash
cd Client-PC/GUI
python hall_dashboard.py --serial-port /dev/tty.usbmodemXXXX --baudrate 9600 --motor-poles 8
```

Run by tailing a saved feedback log:

```bash
cd Client-PC/GUI
python hall_dashboard.py --input-file hall_feedback.log --motor-poles 8
```

Then open:

```bash
http://127.0.0.1:8060
```

The Hall dashboard:

- parses raw Hall states like `101`
- tracks valid Hall transitions over a rolling time window
- estimates electrical RPM and mechanical RPM
- uses the configured motor pole count to convert to shaft RPM
- graphs speed over time and keeps the latest raw feedback line visible

## Useful flags

| Flag | Default | Purpose |
|---|---|---|
| `--listen-host` | `0.0.0.0` | TCP host for incoming Go client packets |
| `--listen-port` | `8090` | TCP port for incoming Go client packets |
| `--ui-host` | `127.0.0.1` | Host for the Dash UI |
| `--ui-port` | `8050` | Port for the Dash UI |
| `--forward-to` | empty | Optional real server target in `host:port` form |
| `--max-packet-size` | `8192` | Protocol guard for payload size |

## What the UI shows

- Controller stick, trigger, button, and d-pad state from `Client-PC`
- Sequence number, packet size, CRC result, peer address, and forward status
- Live Jetson status packets from `Client-Jetson`
- Raw packet previews and connection logs for debugging
