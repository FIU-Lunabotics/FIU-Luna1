"""
cv_model_inference.py

Connects the branched camera stream to the trained YOLO model.

Pipeline:
camera_stream.py CV branch -> cv_branch_capture.py -> YOLO model -> detection data

This script is meant to prepare the camera stream for future ROS/sensor-fusion use.
"""

import argparse
from pathlib import Path

from ultralytics import YOLO

from cv_branch_capture import (
    CVBranchConfig,
    retrieve_camera_stream,
    retrieve_single_frame,
)


MODEL_PATH = Path(__file__).resolve().parent / "best.pt"


def load_model(model_path=MODEL_PATH):
    """Load the trained YOLO model weights."""
    return YOLO(str(model_path))


def detect_objects_in_frame(model, frame, confidence=0.50):
    """
    Run YOLO on one camera frame.

    Args:
        model: Loaded YOLO model.
        frame: OpenCV/Numpy BGR image frame.
        confidence: Minimum confidence threshold.

    Returns:
        List of detections with class, confidence, and bounding box coordinates.
    """

    results = model.predict(
        source=frame,
        conf=confidence,
        save=False,
        verbose=False,
    )

    result = results[0]
    detections = []

    if result.boxes is None or len(result.boxes) == 0:
        return detections

    for box in result.boxes:
        class_id = int(box.cls[0])
        class_name = result.names[class_id]
        score = float(box.conf[0])

    
        x1, y1, x2, y2 = box.xyxy[0].tolist()

        detections.append(
            {
                "class": class_name,
                "confidence": round(score, 4),
                "box": {
                    "x1": int(x1),
                    "y1": int(y1),
                    "x2": int(x2),
                    "y2": int(y2),
                },
            }
        )

    return detections


def detect_single_camera_frame(config=None, confidence=0.50, timeout=2.0):
    """
    Open the CV camera branch, retrieve one frame, and run YOLO detection on it.

    Returns:
        List of detection dictionaries.
    """

    model = load_model()

    with retrieve_camera_stream(config) as stream:
        frame = retrieve_single_frame(stream, timeout=timeout)
        detections = detect_objects_in_frame(
            model=model,
            frame=frame,
            confidence=confidence,
        )

    return detections


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run YOLO object detection on frames from the CV camera branch."
    )

    parser.add_argument(
        "--model",
        default=str(MODEL_PATH),
        help="Path to YOLO weights file. Default: ComputerVision/best.pt",
    )

    parser.add_argument(
        "--socket-path",
        default=None,
        help="Path to GStreamer shared-memory socket. Uses default if not provided.",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Camera frame width. Uses default if not provided.",
    )

    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Camera frame height. Uses default if not provided.",
    )

    parser.add_argument(
        "--fps",
        type=int,
        default=None,
        help="Camera stream FPS. Uses default if not provided.",
    )

    parser.add_argument(
        "--conf",
        type=float,
        default=0.50,
        help="Confidence threshold for YOLO detections.",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for a camera frame before timing out.",
    )

    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of frames to process.",
    )

    return parser.parse_args()


def build_config(args):
    """
    Build CVBranchConfig only if the user overrides stream settings.
    Otherwise, return None so cv_branch_capture uses its defaults.
    """

    if args.socket_path is None and args.width is None and args.height is None and args.fps is None:
        return None

    default_config = CVBranchConfig()

    return CVBranchConfig(
        socket_path=args.socket_path or default_config.socket_path,
        width=args.width or default_config.width,
        height=args.height or default_config.height,
        fps=args.fps or default_config.fps,
    )


def main():
    args = parse_args()

    model_path = Path(args.model).expanduser()

    if not model_path.exists():
        raise FileNotFoundError(f"YOLO model weights not found: {model_path}")

    model = load_model(model_path)
    config = build_config(args)

    print(f"Loaded model: {model_path}")
    print(f"Confidence threshold: {args.conf}")
    print(f"Processing {args.count} frame(s) from the CV camera branch.")

    with retrieve_camera_stream(config) as stream:
        for index in range(args.count):
            frame = retrieve_single_frame(stream, timeout=args.timeout)
            detections = detect_objects_in_frame(
                model=model,
                frame=frame,
                confidence=args.conf,
            )

            print(f"\nFrame {index + 1}")

            if not detections:
                print("  No detections.")
                continue

            for detection in detections:
                box = detection["box"]

                print(
                    f"  Detected: {detection['class']} | "
                    f"Confidence: {detection['confidence']} | "
                    f"Box: x1={box['x1']}, y1={box['y1']}, "
                    f"x2={box['x2']}, y2={box['y2']}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
