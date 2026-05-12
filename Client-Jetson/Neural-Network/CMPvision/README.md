# CMPvision

Simple scripts for testing the YOLO weights on a folder of images.

This script returns:
- detected object name
- confidence score
- bounding box coordinates

Bounding box format:
x1, y1 = top-left corner
x2, y2 = bottom-right corner

Example output:

Detected: Rock | Confidence: 0.91 | Box: x1=102, y1=88, x2=244, y2=301


Current function takes a JPG image path and returns detection data.
The returned box coordinates and class names can be used later by the camera feed code to draw bounding boxes every few frames.

## Whats in here

- vision_model.py loads the YOLO model
- test_model.py runs the model on a folder of jpg images local on your machine
- the weights file is expected to be here:

Client-Jetson/ComputerVision/best.pt

## Setup

From the repo root:

python -m venv .venv
source .venv/vin/activate
pip install ultralytics

## Run

Test images from a specific folder:

python test_model.py --images /path/to/images --save

You can also run a safer test on 50 images out of a bunch by using 

python test_model.py --images /path/to/images --save --limit 50

Higher confidence test:

python test_model.py --images /path/to/images --save --conf 0.70 --limit 50


## Output

Saved images go here:

CMPvision/runs/detect/predict/

If ran multiple times it make create predict1/ or predict2/ etc 



