# FIU-Luna1 Rover Operations

This guide describes how to start and operate the rover control stack when each
machine already has the repo, dependencies, and runnable binaries/scripts ready.

## System Layout

```text
Client-PC/           Operator laptop - dashboard and gamepad client
Client-Jetson/       Jetson onboard rover - status heartbeat and camera stream
Server-Pi/           Raspberry Pi onboard rover - state machine, TCP server, Arduino serial
Embedded-Processor/  Arduino firmware
```

The normal operator path is:

```text
Gamepad client -> Dashboard proxy -> Pi TCP server -> Arduino
Jetson status  -> Dashboard proxy
Jetson camera  -> Dashboard WebRTC panel
```

## What Runs Where

The Raspberry Pi runs:

- `Server-Pi/Rover/main` for the rover state machine
- `Server-Pi/Network-Stack` for the Go TCP server and Arduino serial output

The operator laptop runs:

- `Client-PC/GUI/dashboard.py` for the web dashboard and packet proxy
- `Client-PC/Network-Stack` for the gamepad client

The Jetson optionally runs:

- `Client-Jetson/Network-Stack` for heartbeat/status packets
- `Client-Jetson/Neural-Network/Gazebo/controllers/sensors/camera_stream.py` for WebRTC camera streaming

## Network Ports

| Port | Machine | Purpose |
|---|---|---|
| `8080` | Raspberry Pi | Go TCP server for controller/status packets |
| `8090` | Operator laptop | Dashboard packet listener/proxy |
| `8050` | Operator laptop | Dashboard web UI |
| `8081` | Jetson | Raw WebRTC signaling for the Jetson camera |

Important: on the current `main` branch, the Pi Go server handles TCP control
traffic but does not expose an HTTP `/rover/state` endpoint. The dashboard can
still proxy packets and show controller/camera traffic, but remote rover-state
display may show `UNKNOWN` until that endpoint is implemented or another state
source is provided.

## Startup Order

Start the rover in this order:

!!!!StartUps will have their own terminals!!!!

1. Start the rover state machine on the Pi.
2. Start the Pi Go server.
3. Start the dashboard on the operator laptop.
4. Start the PC gamepad client through the dashboard.
5. Open the dashboard in a browser.
6. Optional: start the Jetson heartbeat client.
7. Optional: start the Jetson WebRTC camera stream.



## 1. Start The Rover State Machine


SSH INTO PI:
Open a terminal and paste:
```bash
ssh admin@192.168.100.129
```

Inside the Raspberry Pi after SSH:

```bash
cd Lunabotics/FIU-Luna1/Server-Pi/Rover
./main
```
Manual state-machine controls in this terminal:

```text
i + Enter -> IDLE
t + Enter -> TELEOP
a + Enter -> AUTO
```

IMPORTANT!!!!! REMEMBER TO SWITCH TO TELEOP AFTER FINISHING SETUP

Leave this process running. It publishes the rover's current mode to:

```text
/tmp/rover_state
```

The Pi server reads that file before allowing serial writes to the Arduino.

## 2. Start The Pi Server

In a second terminal on the Raspberry Pi:

```bash
cd Lunabotics/FIU-Luna1/Server-Pi/Network-Stack
go run . -public -port 8080 -serial-device /dev/ttyACM0
```

Use a different serial device if the Arduino is not on `/dev/ttyACM0`.

If this error message appears: `bind address already in use` use
this command to resolve: 
```bash
sudo lsof -i :8080
kill <PID>
```

The server accepts packets from the dashboard, checks CRC32, formats controller
state into the Arduino byte format, and writes to serial only when the rover is
in `TELEOP`.

## 3. Start The Dashboard

NOTE!!!:
Make sure to run these command these before running the operator commands for this step:

```bash
cd Client-PC/GUI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
On the operator laptop:

```bash
cd /path/to/FIU-Luna1/Client-PC/GUI
python dashboard.py --listen-host 0.0.0.0 --listen-port 8090 --ui-host 127.0.0.1 --ui-port 8050 --forward-to 192.168.100.129:8080
```
Click on the URL that appears after running the command will open dashboard in browser. Below is the URL that will appear.

If the browser is on the same machine as the dashboard:

```text
http://127.0.0.1:8050
```

If browsing from another machine:

```text
http://<OPERATOR_PC_IP>:8050
```
The dashboard listens for repo clients on `8090`, displays controller/status
traffic, and forwards valid packets to the Pi server on `<PI_IP>:8080`.

If the browser is on a different machine than the dashboard process, use:

```bash
python dashboard.py --listen-host 0.0.0.0 --listen-port 8090 --ui-host 0.0.0.0 --ui-port 8050 --forward-to :8080
```

## 4. Start The PC Gamepad Client

On the operator laptop:

```bash
cd /path/to/FIU-Luna1/Client-PC/Network-Stack
go run . -server 127.0.0.1:8090
```

For normal dashboard operation, the PC client should point at the dashboard
listener on `8090`, not directly at the Pi on `8080`.

Useful runtime flags:

| Flag | Default | Purpose |
|---|---|---|
| `-server` | `localhost:8080` | Server or dashboard listener address |
| `-device` | empty | Specific `/dev/input/event*` controller path |
| `-y-north` | `true` | Swap X/Y mapping so Y acts as North |
| `-debug-events` | `false` | Print raw evdev events |

## 5. Open The Dashboard



## Operating Modes

The rover state machine has three modes:

| Mode | Meaning |
|---|---|
| `IDLE` | Safe standby. Controller packets are received but not sent to Arduino serial. |
| `TELEOP` | Manual driving mode. Controller packets may be sent to Arduino serial. |
| `AUTO` | Autonomous mode placeholder. Controller serial writes are blocked. |

Only `TELEOP` allows the Pi server to write controller bytes to the Arduino.
Missing, stale, malformed, `IDLE`, or `AUTO` state data blocks serial writes.

## Controller Mode Requests

The PC controller can request rover mode changes through the Pi server:

```text
Hold SELECT for at least 0.5 seconds, then:

Y / N -> TELEOP
B / E -> AUTO
X / W -> IDLE
```

The Go server writes a one-shot request to:

```text
/tmp/rover_state_request
```

The rover state machine consumes that request and updates `/tmp/rover_state`.

## Optional Jetson Heartbeat

On the Jetson:

```bash
cd /path/to/FIU-Luna1/Client-Jetson/Network-Stack
go run . -server <OPERATOR_PC_IP>:8090
```

Use the operator laptop's LAN address so the Jetson can reach the dashboard.

Useful runtime flags:

| Flag | Default | Purpose |
|---|---|---|
| `-server` | `localhost:8080` | Server or dashboard listener address |
| `-source` | `jetson` | Source label included in packets |
| `-message` | `connected` | Status message |
| `-hz` | `1` | Status send rate |

## Optional Jetson Camera

On the Jetson:

```bash
cd ~/Lunabotics/FIU-Luna1
python3 Client-Jetson/Neural-Network/Gazebo/controllers/sensors/camera_stream.py \
  --source auto \
  --headless \
  --serve-webrtc \
  --signal-bind-host 0.0.0.0 \
  --signal-port 8081 \
  --signal-public-host <JETSON_IP> \
  --report-to <OPERATOR_PC_IP>:8090 \
  --report-source jetson-camera-1
```

Use:

- `<JETSON_IP>` as the Jetson's LAN address
- `<OPERATOR_PC_IP>` as the operator laptop's LAN address
- `jetson-camera-1` for the dashboard's first camera panel

The camera script sends health and WebRTC offer packets to the dashboard on
`8090`. The dashboard/browser sends the WebRTC answer back to the Jetson on
`8081`.

## What Success Looks Like

- The rover state-machine terminal prints the current mode and keeps updating `/tmp/rover_state`.
- The Pi server terminal shows incoming dashboard/client connections.
- The dashboard opens on port `8050`.
- The dashboard packet listener is active on port `8090`.
- Controller packets appear in the dashboard.
- Forward status shows packets moving from the dashboard to the Pi server.
- In `IDLE` and `AUTO`, the Pi server blocks serial writes.
- In `TELEOP`, controller packets are allowed through to Arduino serial.
- Jetson status appears in the dashboard when the heartbeat client is running.
- The camera panel negotiates WebRTC when the Jetson camera stream is running.

## Quick Shutdown

To stop rover motion through the network stack, switch the rover state to
`IDLE` from the state-machine terminal or through the controller mode request.
Then stop the PC gamepad client, dashboard, Pi server, and rover state machine.

## Troubleshooting

If the dashboard page does not load:

- Make sure the dashboard process is still running.
- Make sure the browser is opening port `8050`, not `8090`.
- Use `--ui-host 0.0.0.0` if browsing from another machine.

If packets do not appear:

- Make sure the PC client is pointed at `127.0.0.1:8090`.
- Make sure the dashboard was started with `--listen-host 0.0.0.0` for remote Jetson clients.
- Make sure the Pi server is running before expecting forwarded packets to reach the rover.

If serial writes do not reach the Arduino:

- Confirm the rover is in `TELEOP`.
- Confirm the state machine is still updating `/tmp/rover_state`.
- Confirm the Pi server is using the correct `-serial-device`.
- Check the Pi server terminal for serial-open or stale-state messages.

If the Jetson cannot connect to the dashboard:

- Use the operator laptop's LAN IP, not `127.0.0.1`.
- Make sure the laptop firewall allows the dashboard listener on `8090`.
