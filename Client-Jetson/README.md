# Client-Jetson

Jetson-side components for rover status reporting and camera bring-up.

## Camera Bring-Up

Use these commands on the Jetson to verify that an IMX219-based camera is
connected correctly and can be opened locally before attempting full camera
integration. The camera script can also emit camera-health status packets into
the existing dashboard/server TCP protocol.

### 1. Start with NVIDIA's camera preview tool

```bash
DISPLAY=:0.0 nvgstcapture-1.0
```

If needed, explicitly try the first sensor:

```bash
DISPLAY=:0.0 nvgstcapture-1.0 --sensor-id=0
```

If the camera is detected but preview fails with EGL or display errors, test
headless capture directly:

```bash
gst-launch-1.0 nvarguscamerasrc sensor-id=0 num-buffers=1 ! 'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1,format=NV12' ! fakesink
```

### 2. Check whether a V4L2 device appears

```bash
ls /dev/video*
```

If `v4l2-ctl` is installed:

```bash
v4l2-ctl --list-devices
```

### 3. Test the local camera script

After implementing
`Neural-Network/Gazebo/controllers/sensors/camera_stream.py`, run:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source jetson --probe-only
```

This is the smallest software checkpoint: the script opens the camera, requires
one valid frame, prints success, and exits.

Then move to the ongoing preview loop:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source auto
```

If you want the camera script to report health into the dashboard/server
listener while it runs:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source jetson --probe-only --report-to 127.0.0.1:8090
```

If the Jetson CSI path does not open, try the V4L2 fallback:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source v4l2 --v4l2-device /dev/video0 --probe-only
```

If you only want to validate frame capture without opening a preview window:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source auto --headless
```

## WebRTC Streaming To The Dashboard

The live-video path now uses WebRTC for the media stream instead of HTTP MJPEG.
The Jetson camera script still reports health into the existing dashboard/server
TCP protocol, but the actual video frames are sent through WebRTC. The Jetson
also opens a raw TCP signaling port so the dashboard can return the browser's
SDP answer without tunneling video through HTTP.

The Jetson CSI path now opens one GStreamer camera pipeline and branches it
with `tee` into two appsinks:

```text
nvarguscamerasrc -> conversion -> tee
  -> queue -> gui_sink -> WebRTC/dashboard
  -> queue -> cv_sink  -> future computer vision input
```

WebRTC mode requires `aiortc` and `av` on the Jetson in addition to OpenCV.
The branched camera path also requires GStreamer Python bindings:

```bash
sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
```

```bash
python3 -m pip install aiortc av
```

Example on the Jetson:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source jetson --headless --serve-webrtc --signal-bind-host 0.0.0.0 --signal-port 8081 --signal-public-host <jetson-ip> --report-to <dashboard-ip>:8090 --report-source jetson-camera-1
```

What this does:

- opens the camera locally on the Jetson
- publishes camera-health packets to the dashboard listener
- publishes a WebRTC offer to the dashboard over the existing packet protocol
- waits for the dashboard/browser answer on the raw signaling port
- sends the live camera frames through WebRTC once the peer connection forms
- reports `gui_branch_frames`, `cv_branch_frames`, and `branch_health` so you
  can confirm both GStreamer branches are receiving frames

## Local CV Branch Frame Capture

Issue #30 adds a ROS-free helper script that pulls frames from the local
GStreamer CV branch. This is meant to become the camera input layer for
computer vision later.

Start the normal camera stream with `--serve-cv-shm` so the `cv_sink` branch is
also published through a local GStreamer shared-memory socket:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source jetson --headless --serve-webrtc --serve-cv-shm --signal-bind-host 0.0.0.0 --signal-port 8081 --signal-public-host <jetson-ip> --report-to <dashboard-ip>:8090 --report-source jetson-camera-1
```

This keeps one process in charge of the physical camera. The GStreamer `tee`
feeds the GUI/WebRTC branch and the local CV branch at the same time.

The CV helper connects to that local branch and provides importable functions:

```python
from cv_branch_capture import retrieve_camera_stream, retrieve_single_frame_jpg

stream = retrieve_camera_stream()
frame_jpg = retrieve_single_frame_jpg(stream)
```

For a quick one-frame test on the Jetson while `camera_stream.py` is running:

```bash
python3 ComputerVision/cv_branch_capture.py --output cv_frame.jpg
```

To poll frames at a specific FPS for future neural-network input:

```bash
python3 ComputerVision/cv_branch_capture.py --poll-fps 5 --count 20 --output cv_frame.jpg
```

This does not create a network stream. It connects to the local GStreamer
shared-memory branch and returns JPEG bytes that can be passed directly into the
future computer-vision pipeline.

## Hardware-Free Testing

If you want to test the camera script logic and dashboard plumbing on a machine
without a real Jetson camera attached, use the mock camera source:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source mock --frame-limit 90
```

To run the mock source headlessly and report camera status into the dashboard:

```bash
python3 Neural-Network/Gazebo/controllers/sensors/camera_stream.py --source mock --headless --frame-limit 60 --report-to 127.0.0.1:8090
```

## What Success Looks Like

- `nvgstcapture-1.0` opens a live preview on the Jetson display.
- `gst-launch-1.0 ... ! fakesink` captures one buffer and exits cleanly in
  headless mode.
- `camera_stream.py --probe-only` opens the device and receives one valid frame.
- `camera_stream.py --source mock` shows a moving synthetic preview or exits
  cleanly in headless mode after the frame limit.
- When `--report-to` is set, the dashboard's status tab shows camera-specific
  status rows such as `first_frame_ok`, `preview_running`, or `probe_complete`.
- When `--serve-webrtc` is set, the dashboard can negotiate a WebRTC session
  and show the camera feed without using HTTP video transport.
- The camera script opens the device, receives a valid first frame, and stays
  stable during preview.
- If preview fails, check ribbon seating, Jetson camera support, and whether
  the correct CSI or V4L2 path is available.
