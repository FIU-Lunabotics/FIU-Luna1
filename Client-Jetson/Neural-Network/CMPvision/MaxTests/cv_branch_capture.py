"""
Local frame capture helper for the GStreamer CV branch.

camera_stream.py owns the physical Jetson camera and duplicates the feed with a
GStreamer tee. When camera_stream.py is started with --serve-cv-shm, the CV
branch is published to a local shmsink. This module connects to that local
GStreamer shared-memory branch and provides ROS-free helper functions for the
future computer-vision program.
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import cv2
except ImportError:
    print("OpenCV is required to encode CV branch frames as JPEG.")
    raise SystemExit(1)

try:
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstVideo", "1.0")
    from gi.repository import Gst, GstVideo
except (ImportError, ValueError):
    Gst = None
    GstVideo = None

try:
    import numpy as np
except ImportError:
    np = None

CLIENT_JETSON_DIR = Path(__file__).resolve().parents[1]
SENSOR_CONTROLLER_DIR = (
    CLIENT_JETSON_DIR / "Neural-Network" / "Gazebo" / "controllers" / "sensors"
)
if str(SENSOR_CONTROLLER_DIR) not in sys.path:
    sys.path.insert(0, str(SENSOR_CONTROLLER_DIR))

from camera_stream import (
    DEFAULT_CV_SHM_PATH,
    DEFAULT_FPS,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
)


DEFAULT_JPEG_QUALITY = 85


@dataclass
class CVBranchConfig:
    socket_path: str = DEFAULT_CV_SHM_PATH
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS


class CVBranchStream:
    def __init__(self, config=None):
        if Gst is None:
            raise RuntimeError(
                "CV branch capture requires PyGObject/Gst. "
                "Install python3-gi and GStreamer Python bindings on the Jetson."
            )
        if np is None:
            raise RuntimeError("CV branch capture requires numpy.")

        Gst.init(None)
        self.config = config or CVBranchConfig()
        self.pipeline = None
        self.bus = None
        self.sink = None
        self.last_error = ""

    def start(self):
        if self.pipeline is not None:
            return self

        self.pipeline = Gst.parse_launch(build_cv_branch_pipeline(self.config))
        self.bus = self.pipeline.get_bus()
        self.sink = self.pipeline.get_by_name("cv_branch_sink")
        if self.sink is None:
            self.release()
            raise RuntimeError("CV branch pipeline is missing appsink cv_branch_sink.")

        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self.release()
            raise RuntimeError("Failed to set CV branch pipeline to PLAYING.")
        return self

    def release(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.bus = None
        self.sink = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, traceback):
        self.release()

    def read_frame(self, timeout=2.0):
        if self.pipeline is None:
            self.start()

        sample = self.sink.emit("try-pull-sample", int(max(0.0, timeout) * Gst.SECOND))
        if sample is None:
            self._check_bus()
            raise TimeoutError("Timed out waiting for a frame from the CV branch.")

        frame = sample_to_bgr_frame(sample)
        if frame is None:
            raise RuntimeError("Failed to decode frame from the CV branch.")
        return frame

    def _check_bus(self):
        if self.bus is None:
            return

        while True:
            message = self.bus.pop_filtered(
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
            )
            if message is None:
                return
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                self.last_error = f"{error}: {debug or ''}".strip()
                raise RuntimeError(f"CV branch pipeline error: {self.last_error}")
            if message.type == Gst.MessageType.EOS:
                self.last_error = "CV branch pipeline reached EOS"
                raise RuntimeError(self.last_error)
            if message.type == Gst.MessageType.WARNING:
                warning, debug = message.parse_warning()
                self.last_error = f"{warning}: {debug or ''}".strip()
                print(f"CV branch pipeline warning: {self.last_error}")


def build_cv_branch_pipeline(config):
    return (
        "shmsrc socket-path={socket_path} is-live=true do-timestamp=true ! "
        "video/x-raw, width=(int){width}, height=(int){height}, "
        "format=(string)BGR, framerate=(fraction){fps}/1 ! "
        "queue leaky=downstream max-size-buffers=2 max-size-bytes=0 max-size-time=0 ! "
        "appsink name=cv_branch_sink emit-signals=false drop=true max-buffers=1 sync=false"
    ).format(
        socket_path=config.socket_path,
        width=config.width,
        height=config.height,
        fps=config.fps,
    )


def sample_to_bgr_frame(sample):
    caps = sample.get_caps()
    if caps is None or caps.get_size() == 0:
        return None

    structure = caps.get_structure(0)
    width = int(structure.get_value("width"))
    height = int(structure.get_value("height"))
    buffer = sample.get_buffer()
    if buffer is None:
        return None

    ok, map_info = buffer.map(Gst.MapFlags.READ)
    if not ok:
        return None
    try:
        row_width = width * 3
        stride = row_width
        if GstVideo is not None:
            video_info = GstVideo.VideoInfo.new_from_caps(caps)
            if video_info is not None:
                stride = abs(video_info.stride[0]) or row_width

        expected_size = stride * height
        if len(map_info.data) < expected_size:
            return None
        if stride == row_width:
            frame = np.frombuffer(map_info.data, dtype=np.uint8, count=row_width * height)
            return frame.reshape((height, width, 3)).copy()

        rows = np.frombuffer(map_info.data, dtype=np.uint8, count=expected_size)
        rows = rows.reshape((height, stride))
        return rows[:, :row_width].reshape((height, width, 3)).copy()
    finally:
        buffer.unmap(map_info)


def retrieve_camera_stream(config=None):
    """Connect to the local GStreamer CV branch and return a stream object."""
    return CVBranchStream(config=config).start()


def retrieve_stream_data(stream, timeout=2.0):
    """Retrieve one raw BGR frame from an active CV branch stream."""
    return stream.read_frame(timeout=timeout)


def retrieve_single_frame(stream, timeout=2.0):
    """Retrieve one raw BGR frame from an active CV branch stream."""
    return retrieve_stream_data(stream, timeout=timeout)


def retrieve_single_frame_jpg(stream, timeout=2.0, quality=DEFAULT_JPEG_QUALITY):
    """Retrieve one JPEG-encoded frame from an active CV branch stream."""
    frame = retrieve_single_frame(stream, timeout=timeout)
    quality = max(1, min(100, int(quality)))
    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), quality],
    )
    if not ok:
        raise RuntimeError("OpenCV failed to encode CV branch frame as JPEG.")
    return encoded.tobytes()


def poll_single_frame_jpg(stream, fps=5.0, count=0, timeout=2.0, quality=DEFAULT_JPEG_QUALITY):
    """Yield JPEG frames from the CV branch at roughly fps. count=0 runs forever."""
    interval = 1.0 / max(0.1, float(fps))
    frames_read = 0
    while count <= 0 or frames_read < count:
        started_at = time.time()
        yield retrieve_single_frame_jpg(stream, timeout=timeout, quality=quality)
        frames_read += 1
        elapsed = time.time() - started_at
        time.sleep(max(0.0, interval - elapsed))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pull JPEG frames from the local GStreamer CV branch."
    )
    parser.add_argument("--socket-path", default=DEFAULT_CV_SHM_PATH)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    parser.add_argument("--output", default="cv_frame.jpg")
    parser.add_argument(
        "--poll-fps",
        type=float,
        default=0.0,
        help="When greater than zero, save frames repeatedly at this FPS.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of frames to save in polling mode. Use 0 to run until interrupted.",
    )
    return parser.parse_args()


def output_path(base_path, index, total_count):
    if total_count == 1:
        return base_path
    stem, dot, suffix = base_path.rpartition(".")
    if dot:
        return f"{stem}_{index:04d}.{suffix}"
    return f"{base_path}_{index:04d}.jpg"


def write_jpg(path, frame_jpg):
    with open(path, "wb") as handle:
        handle.write(frame_jpg)
    print(f"Wrote {len(frame_jpg)} bytes to {path}")


def main():
    args = parse_args()
    config = CVBranchConfig(
        socket_path=args.socket_path,
        width=args.width,
        height=args.height,
        fps=args.fps,
    )

    if not os.path.exists(config.socket_path):
        print(
            "CV branch socket does not exist yet. Start camera_stream.py with "
            f"--serve-cv-shm first: {config.socket_path}"
        )
        return 1

    with retrieve_camera_stream(config) as stream:
        if args.poll_fps > 0:
            for index, frame_jpg in enumerate(
                poll_single_frame_jpg(
                    stream,
                    fps=args.poll_fps,
                    count=args.count,
                    timeout=args.timeout,
                    quality=args.jpeg_quality,
                ),
                start=1,
            ):
                write_jpg(output_path(args.output, index, args.count), frame_jpg)
        else:
            frame_jpg = retrieve_single_frame_jpg(
                stream,
                timeout=args.timeout,
                quality=args.jpeg_quality,
            )
            write_jpg(args.output, frame_jpg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
