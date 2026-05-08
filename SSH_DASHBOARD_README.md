# SSH + Dashboard Remote Setup

This guide shows the most common remote workflow for this repo:

- The Raspberry Pi runs the rover state machine in `Server-Pi/Rover`
- The Raspberry Pi runs `Server-Pi/Network-Stack`
- The operator laptop runs the dashboard in `Client-PC/GUI`
- The operator laptop also runs `Client-PC/Network-Stack`
- An optional Jetson client can also report through the dashboard
- An optional Jetson camera stream can also publish WebRTC through the dashboard

## How Many Terminals You Need

For the dashboard + teleop setup without Jetson camera, plan on **4 terminals plus 1 browser tab**:

1. `Terminal 1` on the operator laptop:
   SSH into the Raspberry Pi and run the rover state machine from `Server-Pi/Rover`
2. `Terminal 2` on the operator laptop:
   Open a second SSH session into the Raspberry Pi and run `Server-Pi/Network-Stack`
3. `Terminal 3` on the operator laptop:
   Run the dashboard from `Client-PC/GUI`
4. `Terminal 4` on the operator laptop:
   Run the operator client from `Client-PC/Network-Stack`
5. Browser tab on the operator laptop:
   Open the dashboard UI at `http://127.0.0.1:8050`

Optional additions:

- Add **1 more terminal on the Jetson** if you also want to run `Client-Jetson/Network-Stack`
- Add **1 more terminal on the Jetson** if you also want to run the Jetson WebRTC camera stream
- Add **1 more terminal on the operator laptop** if you want a dedicated log-view window

For the full setup with Jetson heartbeat and Jetson camera, plan on **6 terminals plus 1 browser tab**:

1. `Terminal 1` on the operator laptop:
   SSH into the Raspberry Pi and run the rover state machine from `Server-Pi/Rover`
2. `Terminal 2` on the operator laptop:
   Open a second SSH session into the Raspberry Pi and run `Server-Pi/Network-Stack`
3. `Terminal 3` on the operator laptop:
   Run the dashboard from `Client-PC/GUI`
4. `Terminal 4` on the operator laptop:
   Run the operator client from `Client-PC/Network-Stack`
5. `Terminal 5` on the Jetson:
   Run the Jetson heartbeat client from `Client-Jetson/Network-Stack`
6. `Terminal 6` on the Jetson:
   Run the Jetson WebRTC camera stream from `Client-Jetson/Neural-Network/Gazebo/controllers/sensors/camera_stream.py`
7. Browser tab on the operator laptop:
   Open the dashboard UI at `http://127.0.0.1:8050`

If you use `tmux` on the Pi, you can keep the state machine and server running in managed sessions instead of leaving both SSH terminals attached the whole time.

## Connection Layout

```text
Client-PC ------------------\
                             > Dashboard proxy on operator PC (:8090) ---> Pi server (:8080) ---> Arduino
Client-Jetson -------------/

Browser UI ------------------------------------------------------------> Dashboard UI on operator PC (:8050)
Jetson camera status/WebRTC offer ------------------------------------> Dashboard proxy on operator PC (:8090)
Dashboard/browser WebRTC signaling -----------------------------------> Jetson camera signaling (:8081)
```

## Startup Order

Start the pieces in this order:

1. Start the rover state machine on the Pi
2. Start the Go server on the Pi
3. Start the dashboard on the operator laptop
4. Start the PC client on the operator laptop
5. Open the dashboard in the browser
6. Optional: start the Jetson heartbeat client
7. Optional: start the Jetson WebRTC camera stream

## Before You Start

On the Raspberry Pi:

- Clone this repo
- Install Go 1.21+
- Connect the Arduino if you want live serial output

On the operator laptop:

- Clone this repo
- Install Python 3
- Install Go 1.21+
- Make sure the laptop is on the same network as the Pi

## 1. Find the Pi IP Address

On the Pi, run:

```bash
hostname -I
```

Use the Pi's LAN address in the steps below. In this guide it is written as `<PI_IP>`.

Important:

- On this Pi, the repo lives at `~/Lunabotics/FIU-Luna1`
- `~/FIU-Luna1/...` will fail unless the repo was cloned directly in your home directory

## 2. SSH Into the Pi

From the operator laptop, open `Terminal 1` and run:

```bash
ssh admin@<PI_IP>
```

Example:

```bash
ssh admin@192.168.1.50
```

## 3. Start the Rover State Machine on the Pi

Inside `Terminal 1`, after SSHing into the Pi:

```bash
cd ~/Lunabotics/FIU-Luna1/Server-Pi/Rover
./main
```

Notes:

- Leave `Terminal 1` running while you test unless you move the state machine into `tmux`
- If `./main` does not exist on the Pi, build it with:

```bash
gcc -O2 -std=c11 -o main main.c
./main
```

- This process publishes `/tmp/rover_state`, which the Go server uses to decide whether serial writes are allowed
- Manual test controls in this terminal are:

```text
i + Enter -> IDLE
t + Enter -> TELEOP
a + Enter -> AUTO
```

## 4. Start the Pi Server

Open `Terminal 2` on the operator laptop and SSH into the Pi again:

```bash
ssh admin@<PI_IP>
```

Inside `Terminal 2`, run:

```bash
cd ~/Lunabotics/FIU-Luna1/Server-Pi/Network-Stack
go mod tidy
go run . -public -port 8080 -serial-device /dev/ttyACM0
```

Notes:

- Leave `Terminal 2` running while you test unless you move the server into `tmux`
- If your Arduino is not on `/dev/ttyACM0`, update the `-serial-device` value
- To check the device name on the Pi, run:

```bash
ls /dev/ttyACM* /dev/ttyUSB*
```

The server also exposes the rover state HTTP endpoint on port `8081`, which the dashboard can read automatically.

## 5. Start the Dashboard on the Operator Laptop

Open `Terminal 3` on the operator laptop:

```bash
cd /path/to/FIU-Luna1/Client-PC/GUI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python dashboard.py --listen-host 0.0.0.0 --listen-port 8090 --ui-host 127.0.0.1 --ui-port 8050 --forward-to <PI_IP>:8080
```

What this does:

- The dashboard listens for repo clients on port `8090`
- The dashboard forwards validated packets to the Pi server on `<PI_IP>:8080`
- The dashboard automatically checks rover state at `http://<PI_IP>:8081/rover/state`
- The web UI is served on port `8050`
- Leave `Terminal 3` running while the dashboard is in use

If your browser is on a different machine than the one running the dashboard, use:

```bash
python dashboard.py --listen-host 0.0.0.0 --listen-port 8090 --ui-host 0.0.0.0 --ui-port 8050 --forward-to <PI_IP>:8080
```

## 6. Open the Dashboard

If the browser is on the same machine as the dashboard:

```text
http://127.0.0.1:8050
```

If the browser is on another machine, use:

```text
http://<OPERATOR_PC_IP>:8050
```

## 7. Start the Operator Client and Point It at the Dashboard

Open `Terminal 4` on the operator laptop:

```bash
cd /path/to/FIU-Luna1/Client-PC/Network-Stack
go run . -server 127.0.0.1:8090
```

Important:

- The client should connect to the dashboard on port `8090`
- The client should not connect directly to the Pi on port `8080` when you want the dashboard in the middle
- Leave `Terminal 4` running while you are sending controller input

## 8. Optional: Start the Jetson Client Through the Dashboard

If you also want Jetson status packets to appear in the dashboard:

```bash
cd /path/to/FIU-Luna1/Client-Jetson/Network-Stack
go run . -server <OPERATOR_PC_IP>:8090
```

Use the operator laptop's actual LAN address for `<OPERATOR_PC_IP>` so the Jetson can reach the dashboard.

This usually means:

- `Terminal 5` on the Jetson in the full Jetson setup, or
- a separate SSH session into the Jetson from another machine

## 9. Optional: Start the Jetson WebRTC Camera Through the Dashboard

If you also want the Jetson camera to appear in the dashboard, start the camera
script on the Jetson and point `--report-to` at the operator laptop's dashboard
listener:

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

- `<JETSON_IP>` as the Jetson's LAN address from `hostname -I`
- `<OPERATOR_PC_IP>` as the operator laptop's LAN address from `hostname -I`
- `jetson-camera-1` to match the dashboard's default first camera panel

The camera script sends camera status and WebRTC offers to the dashboard on
port `8090`. The dashboard/browser sends WebRTC signaling answers back to the
Jetson on port `8081`. The video itself flows over WebRTC media transport.

## 10. What Success Looks Like

- The Pi state-machine terminal is running and updating `/tmp/rover_state`
- The Pi terminal shows incoming TCP connections
- The dashboard opens at port `8050`
- The dashboard packet listener is active on port `8090`
- Controller packets appear in the dashboard
- Forwarded packet status shows traffic moving to the Pi
- Jetson status packets appear in the dashboard if the Jetson heartbeat client is running
- Camera status shows `first_frame_ok`, `webrtc_offer_ready`, or `preview_running` if the Jetson camera script is running
- Camera status details show `branch=tee`, increasing `gui` and `cv` branch frame counts, and `branch_health=both_active`
- The dashboard camera panel negotiates WebRTC and shows the Jetson camera feed

## One-Line Pi Startup Over SSH

If you prefer not to open interactive shells first, you can start the two Pi-side processes with two commands:

```bash
ssh <PI_USER>@<PI_IP> 'cd ~/Lunabotics/FIU-Luna1/Server-Pi/Rover && ./main'
```

```bash
ssh <PI_USER>@<PI_IP> 'cd ~/Lunabotics/FIU-Luna1/Server-Pi/Network-Stack && go run . -public -port 8080 -serial-device /dev/ttyACM0'
```

This is convenient for quick tests, but each process will stop when its SSH session ends unless you use a session manager like `tmux`.

## Keeping the Pi State Machine and Server Running After Disconnect

On the Pi:

```bash
tmux new -s luna-state
cd ~/Lunabotics/FIU-Luna1/Server-Pi/Rover
./main
```

Detach from `tmux` with `Ctrl+b`, then `d`.

Then start a second session:

```bash
tmux new -s luna-server
cd ~/Lunabotics/FIU-Luna1/Server-Pi/Network-Stack
go run . -public -port 8080 -serial-device /dev/ttyACM0
```

Detach from `tmux` with `Ctrl+b`, then `d`.

Reattach later with:

```bash
tmux attach -t luna-state
```

or:

```bash
tmux attach -t luna-server
```

## Troubleshooting

### The dashboard page does not load

- Make sure the dashboard terminal is still running
- If you are browsing from another machine, use `--ui-host 0.0.0.0`
- Make sure you are opening port `8050`, not `8090`

### The dashboard loads but no packets appear

- Make sure the rover state machine is already running on the Pi
- Make sure the client is pointed at the dashboard address on port `8090`
- Make sure `--forward-to <PI_IP>:8080` uses the correct Pi address
- Make sure the Pi server is already running

### The Pi server starts but Arduino control does not work

- Verify the serial device path
- Confirm the Arduino is connected and powered
- Check the Pi terminal for serial-open errors

### The Jetson cannot connect to the dashboard

- Make sure the dashboard was started with `--listen-host 0.0.0.0`
- Use the operator laptop's LAN IP, not `127.0.0.1`
