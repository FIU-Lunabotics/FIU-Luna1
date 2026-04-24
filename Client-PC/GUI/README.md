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

## Useful flags

| Flag | Default | Purpose |
|---|---|---|
| `--listen-host` | `0.0.0.0` | TCP host for incoming Go client packets |
| `--listen-port` | `8090` | TCP port for incoming Go client packets |
| `--ui-host` | `127.0.0.1` | Host for the Dash UI |
| `--ui-port` | `8050` | Port for the Dash UI |
| `--forward-to` | empty | Optional real server target in `host:port` form |
| `--state-url` | derived from `--forward-to` | Optional rover state endpoint URL |
| `--max-packet-size` | `8192` | Protocol guard for payload size |
| `--camera-one-label` | `Camera 1` | Label for the first camera panel |
| `--camera-two-label` | `Camera 2` | Label for the second camera panel |
| `--camera-one-url` | empty | Deprecated legacy HTTP camera URL override |
| `--camera-two-url` | empty | Deprecated legacy HTTP camera URL override |
| `--camera-one-source` | `jetson-camera-1` | Status source name shown under camera panel one |
| `--camera-two-source` | `jetson-camera-2` | Status source name shown under camera panel two |

## What the UI shows

- Controller stick, trigger, button, and d-pad state from `Client-PC`
- Sequence number, packet size, CRC result, peer address, and forward status
- Live Jetson status packets from `Client-Jetson`
- Gabriel's newer Xbox-style main view and separate debug view toggle
- Two camera display slots inside the existing controller dashboard view
- Camera-health status details in the existing `Jetson Status` tab when the camera script reports them
- Raw packet previews and connection logs for debugging

## Camera Streaming

The live camera panels now expect WebRTC instead of HTTP MJPEG. The dashboard
acts as a small signaling bridge:

- the Jetson camera script reports a WebRTC offer and its raw signaling target
- the browser creates an SDP answer
- the dashboard forwards that answer back to the Jetson over the signaling TCP port
- the video itself flows over WebRTC media transport instead of HTTP

Run the dashboard as usual:

```bash
python dashboard.py --listen-port 8090
```

Then run the Jetson camera script with `--serve-webrtc` and the matching
`--report-source`, for example `jetson-camera-1`. Once the first frame and
WebRTC offer arrive, the matching camera panel will negotiate the browser peer
connection automatically.
