# Rover Vision ROS Package

This ROS workspace contains the `rover_vision` package for the Lunabotics rover computer vision pipeline.

The package wraps the existing camera branch capture and YOLO inference scripts and publishes detection output for autonomy.

## Package

`rover_vision`

## Node

`cv_detection_node.py`

## Published Topic

`/vision/detections`

Message type:

`std_msgs/String`

The message data is JSON containing timestamp, item type/class, confidence, and bounding box coordinates.

Example output:

{
  "stamp": 1778650000.0,
  "detections": [
    {
      "class": "example_object",
      "confidence": 0.95,
      "box": {
        "x1": 100,
        "y1": 80,
        "x2": 240,
        "y2": 200
      }
    }
  ]
}

## Parameters

- `~cv_code_dir`: Path to the ComputerVision folder.
- `~model_path`: Path to YOLO best.pt weights.
- `~confidence`: YOLO confidence threshold. Default: 0.50.
- `~timeout`: Frame capture timeout. Default: 2.0 seconds.
- `~publish_rate`: Detection publish rate. Default: 5.0 Hz.
- `~socket_path`: Optional GStreamer CV branch socket path.
- `~width`: Optional frame width.
- `~height`: Optional frame height.
- `~fps`: Optional stream FPS.

## Runtime Dependencies

This package depends on the existing ComputerVision scripts and their runtime dependencies:

- ROS Noetic
- Python 3
- rospy
- std_msgs
- sensor_msgs
- cv_bridge
- image_transport
- OpenCV / cv2
- NumPy
- Ultralytics YOLO
- PyGObject / python3-gi
- GStreamer 1.0 and plugins

Install model dependency:

pip3 install ultralytics

## Build

From this workspace:

cd Client-Jetson/ComputerVision/ros_ws
source /opt/ros/noetic/setup.bash
catkin_make
source devel/setup.bash

## Run

Start the camera stream with the CV shared-memory branch enabled first:

python3 camera_stream.py --serve-cv-shm

Then launch the ROS node:

roslaunch rover_vision cv_detection.launch

In another terminal, view published detections:

rostopic echo /vision/detections

Full end-to-end testing should be done on the Jetson or a system where the camera stream and GStreamer CV branch are available.
