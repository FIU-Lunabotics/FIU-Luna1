"""
test_model.py

Runs the trained YOLO model on JPG/JPEG images in a folder.

This script prints the detected object name, confidence score, and bounding box
coordinates for each image. It can also save images with bounding boxes drawn.
"""

import argparse
from pathlib import Path
from vision_model import analyze_jpeg, model


# Save YOLO prediction outputs inside this same CMPvision folder.
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "runs" / "detect"


def main():
    parser = argparse.ArgumentParser(
        description="Run the trained YOLO model on a folder of JPG images."
    )

    parser.add_argument(
        "--images",
        required=True,
        help="Path to folder containing .jpg/.jpeg images."
    )

    parser.add_argument(
        "--conf",
        type=float,
        default=0.50,
        help="Confidence threshold for detections. Default is 0.50."
    )

    parser.add_argument(
        "--save",
        action="store_true",
        help="Save prediction images with bounding boxes."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of images to process. Default is 50."
    )

    args = parser.parse_args()

    image_folder = Path(args.images).expanduser()

    if not image_folder.exists():
        raise FileNotFoundError(f"Image folder not found: {image_folder}")

    # Collect JPG/JPEG images from the folder.
    image_files = (
        list(image_folder.glob("*.jpg")) +
        list(image_folder.glob("*.jpeg")) +
        list(image_folder.glob("*.JPG")) +
        list(image_folder.glob("*.JPEG"))
    )

    if not image_files:
        print(f"No JPG/JPEG images found in: {image_folder}")
        return

    # Limit the amount of images processed so weaker systems do not get overloaded.
    image_files = image_files[:args.limit]

    print(f"Image folder: {image_folder}")
    print(f"Processing {len(image_files)} image(s).")
    print(f"Confidence threshold: {args.conf}")

    # Print detection data for each image.
    for image_path in image_files:
        print(f"\nImage: {image_path.name}")

        detections = analyze_jpeg(image_path, confidence=args.conf)

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

    # Optional: save output images with YOLO bounding boxes drawn.
    # This is separate from analyze_jpeg because analyze_jpeg is meant to return data.
    if args.save:
        print("\nSaving prediction images...")

        model.predict(
            source=[str(image) for image in image_files],
            conf=args.conf,
            save=True,
            project=str(OUTPUT_DIR),
            name="predict",
            exist_ok=False,
            verbose=True
        )

        print(f"Saved output folder: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
