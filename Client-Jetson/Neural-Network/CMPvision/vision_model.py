"""
vision_model.py

Loads the trained YOLO model and provides a function that takes in a JPG image,
runs object detection, and returns the detected object names and bounding box
coordinates.
"""

from pathlib import Path
from ultralytics import YOLO


# Path to the trained YOLO weights file.
# Expected repo structure:
# Client-Jetson/
# ├── ComputerVision/
# │   └── best.pt
# └── Neural-Network/
#     └── CMPvision/
#         └── vision_model.py
MODEL_PATH = Path(__file__).resolve().parents[2] / "ComputerVision" / "best.pt"


# Load the model once when this file is imported.
# This is better than reloading the model every time a picture is analyzed.
model = YOLO(str(MODEL_PATH))


def analyze_jpeg(image_path, confidence=0.50):
    """
    Runs the YOLO model on one JPG/JPEG image.

    Args:
        image_path: Path to the input .jpg/.jpeg image.
        confidence: Minimum confidence threshold for detections.

    Returns:
        A list of dictionaries. Each dictionary contains:
            - class: detected object name
            - confidence: model confidence score
            - box: bounding box coordinates

        Bounding box format:
            x1, y1 = top-left corner
            x2, y2 = bottom-right corner
    """

    image_path = Path(image_path).expanduser()

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # Run YOLO inference.
    results = model.predict(
        source=str(image_path),
        conf=confidence,
        save=False,
        verbose=False
    )

    result = results[0]
    detections = []

    # If there are no boxes, return an empty list.
    if result.boxes is None or len(result.boxes) == 0:
        return detections

    # Convert each YOLO box into a simple dictionary that other code can use.
    for box in result.boxes:
        class_id = int(box.cls[0])
        class_name = result.names[class_id]
        score = float(box.conf[0])

        # xyxy format gives: x1, y1, x2, y2
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
