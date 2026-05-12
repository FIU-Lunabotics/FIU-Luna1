"""
Local Jetson camera bring-up script for issue #12.

Goal:
- Open a connected camera on the Jetson.
- Validate that frames are actually arriving.
- Show a local live preview for quick hardware testing.
- Optionally report camera health into the existing rover status protocol.

Hardware target:
- This script is intended for Jetson developer boards in general, including
  Jetson Nano, Jetson Nano 2GB, and Jetson Orin Nano systems.
- IMX219 camera modules are a good match for this path, but board connector
  style, ribbon adapters, and Jetson image support can vary by model.
- The final proof is still whether the Jetson can open the camera and receive
  frames reliably.

Out of scope:
- Streaming video through the Go server or Raspberry Pi.
"""

import argparse
import asyncio
import json
import os
import socket
import struct
import sys
import threading
import time
import zlib

try:
    import cv2
except ImportError:
    print("OpenCV is required for camera preview. Install it on the Jetson, then retry.")
    raise SystemExit(1)

try:
    import numpy as np
except ImportError:
    np = None

try:
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstApp", "1.0")
    gi.require_version("GstVideo", "1.0")
    from gi.repository import Gst, GstVideo
except (ImportError, ValueError):
    Gst = None
    GstVideo = None

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from aiortc.exceptions import InvalidStateError
except ImportError:
    RTCPeerConnection = None
    RTCSessionDescription = None
    VideoStreamTrack = object
    InvalidStateError = RuntimeError

try:
    from av import VideoFrame
except ImportError:
    VideoFrame = None


DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 30
DEFAULT_SENSOR_ID = 0
DEFAULT_FLIP_METHOD = 0
DEFAULT_V4L2_DEVICE = "/dev/video0"
DEFAULT_STATUS_INTERVAL = 5.0
DEFAULT_MAX_FAILURES = 5
DEFAULT_REPORT_SOURCE = "jetson-camera"
DEFAULT_SIGNAL_BIND_HOST = "0.0.0.0"
DEFAULT_SIGNAL_PORT = 8081
DEFAULT_CV_SHM_PATH = "/tmp/luna_camera_cv.sock"
DEFAULT_CV_SHM_SIZE = 67108864
WINDOW_TITLE = "Jetson Camera Preview"


class StatusReporter:
    def __init__(self, target, source):
        self.target = parse_target(target)
        self.source = source
        self.connected_target = None

    def send(self, message, **extra_fields):
        payload = {
            "type": "status",
            "source": self.source,
            "message": message,
            "ts": int(time.time() * 1000),
            "component": "camera",
        }
        payload.update(extra_fields)
        self.send_packet(payload)

    def send_packet(self, payload):
        if self.target is None:
            return

        sock = None
        try:
            sock = socket.create_connection(self.target, timeout=3.0)
            sock.settimeout(5.0)
            if self.connected_target != self.target:
                self.connected_target = self.target
                print(
                    "Camera status reporting connected to "
                    f"{self.connected_target[0]}:{self.connected_target[1]}."
                )
            payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
            framed = payload_bytes + struct.pack(">I", crc)
            header = struct.pack(">I", len(framed))
            sock.sendall(header)
            sock.sendall(framed)
        except OSError as exc:
            print(f"Camera status reporter error: {exc}")
            self.connected_target = None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def close(self):
        self.connected_target = None


class MockCapture:
    def __init__(self, width, height, fps):
        self.width = width
        self.height = height
        self.fps = max(1, fps)
        self.frame_index = 0
        self.opened = True

    def isOpened(self):
        return self.opened

    def read(self):
        if not self.opened:
            return False, None
        if np is None:
            return False, None

        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        frame[:, :, 0] = 18
        frame[:, :, 1] = 42
        frame[:, :, 2] = 84

        cx = 60 + ((self.frame_index * 12) % max(120, self.width - 120))
        cy = self.height // 2
        cv2.circle(frame, (cx, cy), 36, (0, 220, 120), -1)
        cv2.putText(
            frame,
            "Mock camera source",
            (30, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"frame={self.frame_index}",
            (30, 110),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (180, 220, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            time.strftime("%H:%M:%S"),
            (30, 160),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (180, 220, 255),
            2,
            cv2.LINE_AA,
        )

        self.frame_index += 1
        time.sleep(1.0 / self.fps)
        return True, frame

    def release(self):
        self.opened = False


class LatestFrameStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.frame = None
        self.frame_width = 0
        self.frame_height = 0
        self.frame_count = 0
        self.updated_at = 0.0

    def update(self, frame):
        height, width = frame.shape[:2]
        with self.lock:
            self.frame = frame.copy()
            self.frame_width = width
            self.frame_height = height
            self.frame_count += 1
            self.updated_at = time.time()
            self.condition.notify_all()
        return True

    def snapshot(self):
        with self.lock:
            return {
                "frame": None if self.frame is None else self.frame.copy(),
                "frame_width": self.frame_width,
                "frame_height": self.frame_height,
                "frame_count": self.frame_count,
                "updated_at": self.updated_at,
            }

    def wait_for_newer(self, last_frame_count, timeout):
        with self.lock:
            if self.frame_count <= last_frame_count:
                self.condition.wait(timeout=timeout)
            return {
                "frame": None if self.frame is None else self.frame.copy(),
                "frame_width": self.frame_width,
                "frame_height": self.frame_height,
                "frame_count": self.frame_count,
                "updated_at": self.updated_at,
            }


class BranchedGStreamerCapture:
    def __init__(self, args, gui_frame_store=None):
        if Gst is None:
            raise RuntimeError(
                "Jetson GStreamer branch mode requires PyGObject/Gst. "
                "Install python3-gi and GStreamer Python bindings on the Jetson."
            )
        if np is None:
            raise RuntimeError("Jetson GStreamer branch mode requires numpy.")

        Gst.init(None)
        self.args = args
        self.gui_frame_store = gui_frame_store or LatestFrameStore()
        self.cv_frame_store = LatestFrameStore()
        self.lock = threading.Lock()
        self.read_frame_count = 0
        self.opened = False
        self.pipeline = None
        self.bus = None
        self.last_error = ""
        self.cv_shm_enabled = bool(getattr(args, "serve_cv_shm", False))
        self.cv_shm_path = getattr(args, "cv_shm_path", DEFAULT_CV_SHM_PATH)
        self.branch_counts = {"gui": 0, "cv": 0}
        self.branch_last_update = {"gui": 0.0, "cv": 0.0}
        self.branch_dimensions = {
            "gui": (0, 0),
            "cv": (0, 0),
        }

        self.pipeline = Gst.parse_launch(build_jetson_branched_pipeline(args))
        self.bus = self.pipeline.get_bus()
        self._connect_sink("gui_sink", self._on_gui_sample)
        self._connect_sink("cv_sink", self._on_cv_sample)

        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self.last_error = "failed to set GStreamer pipeline to PLAYING"
            self.release()
            raise RuntimeError(self.last_error)

        self.opened = True

    def _connect_sink(self, sink_name, callback):
        sink = self.pipeline.get_by_name(sink_name)
        if sink is None:
            raise RuntimeError(f"GStreamer pipeline is missing appsink {sink_name!r}.")
        sink.set_property("emit-signals", True)
        sink.connect("new-sample", callback)

    def _on_gui_sample(self, sink):
        return self._consume_sample(sink, "gui", self.gui_frame_store)

    def _on_cv_sample(self, sink):
        return self._consume_sample(sink, "cv", self.cv_frame_store)

    def _consume_sample(self, sink, branch_name, frame_store):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        frame = self._sample_to_frame(sample)
        if frame is None:
            return Gst.FlowReturn.ERROR

        frame_store.update(frame)
        height, width = frame.shape[:2]
        with self.lock:
            self.branch_counts[branch_name] += 1
            self.branch_last_update[branch_name] = time.time()
            self.branch_dimensions[branch_name] = (width, height)
        return Gst.FlowReturn.OK

    def _sample_to_frame(self, sample):
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
            data = map_info.data
            if len(data) < expected_size:
                return None
            if stride == row_width:
                frame = np.frombuffer(data, dtype=np.uint8, count=row_width * height)
                return frame.reshape((height, width, 3)).copy()

            rows = np.frombuffer(data, dtype=np.uint8, count=expected_size)
            rows = rows.reshape((height, stride))
            return rows[:, :row_width].reshape((height, width, 3)).copy()
        finally:
            buffer.unmap(map_info)

    def isOpened(self):
        if not self.opened:
            return False
        return self._check_bus()

    def read(self):
        if not self.isOpened():
            return False, None

        snapshot = self.gui_frame_store.wait_for_newer(self.read_frame_count, timeout=2.0)
        frame = snapshot["frame"]
        if frame is None:
            self._check_bus()
            return False, None

        self.read_frame_count = snapshot["frame_count"]
        return True, frame

    def release(self):
        self.opened = False
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.bus = None

    def _check_bus(self):
        if self.bus is None:
            return self.opened

        while True:
            message = self.bus.pop_filtered(
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
            )
            if message is None:
                break
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                self.last_error = f"{error}: {debug or ''}".strip()
                print(f"GStreamer branch pipeline error: {self.last_error}")
                self.opened = False
                return False
            if message.type == Gst.MessageType.EOS:
                self.last_error = "GStreamer branch pipeline reached EOS"
                print(self.last_error)
                self.opened = False
                return False
            if message.type == Gst.MessageType.WARNING:
                warning, debug = message.parse_warning()
                self.last_error = f"{warning}: {debug or ''}".strip()
                print(f"GStreamer branch pipeline warning: {self.last_error}")
        return self.opened

    def branch_status_fields(self):
        now = time.time()
        with self.lock:
            gui_width, gui_height = self.branch_dimensions["gui"]
            cv_width, cv_height = self.branch_dimensions["cv"]
            gui_last = self.branch_last_update["gui"]
            cv_last = self.branch_last_update["cv"]
            return {
                "gstreamer_branch_mode": "tee",
                "gui_branch_frames": self.branch_counts["gui"],
                "cv_branch_frames": self.branch_counts["cv"],
                "gui_branch_active": gui_last > 0,
                "cv_branch_active": cv_last > 0,
                "gui_branch_age": round(now - gui_last, 3) if gui_last else None,
                "cv_branch_age": round(now - cv_last, 3) if cv_last else None,
                "gui_branch_width": gui_width,
                "gui_branch_height": gui_height,
                "cv_branch_width": cv_width,
                "cv_branch_height": cv_height,
                "cv_shm_enabled": self.cv_shm_enabled,
                "cv_shm_path": self.cv_shm_path if self.cv_shm_enabled else "",
                "branch_health": (
                    "both_active"
                    if self.branch_counts["gui"] > 0 and self.branch_counts["cv"] > 0
                    else "waiting_for_frames"
                ),
            }


class LatestFrameTrack(VideoStreamTrack):
    def __init__(self, frame_store, fps):
        super().__init__()
        self.frame_store = frame_store
        self.last_frame_count = 0
        self.timeout = max(0.05, 2.0 / max(1, int(fps)))

    async def recv(self):
        if VideoFrame is None:
            raise RuntimeError("PyAV is required for WebRTC video frames.")

        pts, time_base = await self.next_timestamp()
        snapshot = await asyncio.to_thread(
            self.frame_store.wait_for_newer,
            self.last_frame_count,
            self.timeout,
        )
        frame = snapshot["frame"]
        if frame is None:
            raise RuntimeError("No camera frame is available for WebRTC.")

        self.last_frame_count = snapshot["frame_count"]
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame


class CameraWebRTCManager:
    def __init__(self, bind_host, port, public_host, frame_store, reporter, report_source, fps):
        self.bind_host = bind_host
        self.port = port
        self.public_host = public_host
        self.frame_store = frame_store
        self.reporter = reporter
        self.report_source = report_source
        self.fps = fps
        self.loop = None
        self.thread = None
        self.server = None
        self.pc = None
        self.signal_id = ""
        self.connection_state = "idle"

    def advertised_host(self):
        return self.public_host or self.bind_host

    def signal_fields(self):
        return {
            "signal_host": self.advertised_host(),
            "signal_port": self.port,
            "webrtc_state": self.connection_state,
            "signal_id": self.signal_id,
        }

    def start(self):
        if RTCPeerConnection is None or RTCSessionDescription is None or VideoFrame is None:
            print(
                "WebRTC mode requires aiortc and av. "
                "Install them on the Jetson before using --serve-webrtc."
            )
            return False

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        future = asyncio.run_coroutine_threadsafe(self._async_start(), self.loop)
        future.result(timeout=10.0)
        print(
            "WebRTC signaling ready on "
            f"{self.bind_host}:{self.port} for source {self.report_source}."
        )
        return True

    def stop(self):
        if self.loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._async_stop(), self.loop)
        try:
            future.result(timeout=5.0)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        self.thread = None
        self.loop = None
        self.server = None
        self.pc = None

    def request_offer_refresh(self):
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._publish_offer(), self.loop)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _async_start(self):
        self.server = await asyncio.start_server(
            self._handle_signaling_client,
            self.bind_host,
            self.port,
        )
        await self._publish_offer()

    async def _async_stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.pc is not None:
            await self.pc.close()
            self.pc = None
        self.connection_state = "stopped"

    async def _publish_offer(self):
        if self.pc is not None:
            await self.pc.close()

        self.pc = RTCPeerConnection()
        self.connection_state = "negotiating"
        self.signal_id = str(int(time.time() * 1000))
        self.pc.addTrack(LatestFrameTrack(self.frame_store, self.fps))

        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self.connection_state = self.pc.connectionState
            self.reporter.send(
                "WebRTC connection state changed.",
                camera_state="webrtc_connection_state",
                **self.signal_fields(),
            )

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        await self._wait_for_ice_complete(self.pc)
        local_description = self.pc.localDescription

        self.reporter.send(
            "WebRTC offer ready.",
            camera_state="webrtc_offer_ready",
            **self.signal_fields(),
        )
        offer_payload = {
            "type": "camera_signal",
            "source": self.report_source,
            "component": "camera",
            "signal_kind": "offer",
            "signal_id": self.signal_id,
            "sdp_type": local_description.type,
            "sdp": local_description.sdp,
            "ts": int(time.time() * 1000),
            **self.signal_fields(),
        }
        self.reporter.send_packet(offer_payload)
        return offer_payload

    async def _wait_for_ice_complete(self, pc):
        if pc.iceGatheringState == "complete":
            return

        future = self.loop.create_future()

        @pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            if pc.iceGatheringState == "complete" and not future.done():
                future.set_result(True)

        try:
            await asyncio.wait_for(future, timeout=5.0)
        except asyncio.TimeoutError:
            pass

    async def _handle_signaling_client(self, reader, writer):
        try:
            header = await reader.readexactly(4)
            total_len = struct.unpack(">I", header)[0]
            packet = await reader.readexactly(total_len)
            payload, crc_ok = verify_framed_packet(packet)
            if not crc_ok or payload is None:
                return

            obj = json.loads(payload.decode("utf-8"))
            if not isinstance(obj, dict):
                return
            signal_kind = obj.get("signal_kind")
            if signal_kind == "request_offer":
                offer_payload = await self._publish_offer()
                payload_bytes = json.dumps(offer_payload, separators=(",", ":")).encode("utf-8")
                crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
                framed = payload_bytes + struct.pack(">I", crc)
                header = struct.pack(">I", len(framed))
                writer.write(header)
                writer.write(framed)
                await writer.drain()
            elif signal_kind == "answer":
                if not obj.get("sdp"):
                    return
                if obj.get("signal_id") and obj.get("signal_id") != self.signal_id:
                    return
                if self.pc is None:
                    print("Ignoring WebRTC answer because no peer connection is active.")
                    return
                if self.pc.signalingState != "have-local-offer":
                    print(
                        "Ignoring stale WebRTC answer while peer is in signaling state "
                        f"{self.pc.signalingState!r}."
                    )
                    self.reporter.send(
                        "Ignored stale WebRTC answer.",
                        camera_state="webrtc_answer_ignored",
                        **self.signal_fields(),
                    )
                    return
                description = RTCSessionDescription(
                    sdp=obj["sdp"],
                    type=obj.get("sdp_type", "answer"),
                )
                try:
                    await self.pc.setRemoteDescription(description)
                except InvalidStateError as exc:
                    print(f"Ignoring invalid WebRTC answer: {exc}")
                    self.reporter.send(
                        "Ignored invalid WebRTC answer.",
                        camera_state="webrtc_answer_ignored",
                        **self.signal_fields(),
                    )
                    return
                self.connection_state = "answer_applied"
                self.reporter.send(
                    "WebRTC answer applied.",
                    camera_state="webrtc_answer_applied",
                    **self.signal_fields(),
                )
        except (asyncio.IncompleteReadError, json.JSONDecodeError, OSError) as exc:
            print(f"WebRTC signaling error: {exc}")
        finally:
            writer.close()
            await writer.wait_closed()


def parse_target(target):
    if not target:
        return None
    if ":" not in target:
        raise argparse.ArgumentTypeError(
            f"report target must be host:port, got {target!r}"
        )
    host, port = target.rsplit(":", 1)
    try:
        return host, int(port)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"report target port must be an integer, got {port!r}"
        ) from exc


def verify_framed_packet(packet):
    if len(packet) < 4:
        return None, False
    payload = packet[:-4]
    expected_crc = struct.unpack(">I", packet[-4:])[0]
    actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
    return payload, actual_crc == expected_crc


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bring up a Jetson camera feed and preview it locally."
    )
    parser.add_argument(
        "--source",
        choices=("auto", "jetson", "v4l2", "mock"),
        default="auto",
        help="Camera source to try first. 'auto' prefers Jetson CSI and falls back to V4L2.",
    )
    parser.add_argument(
        "--sensor-id",
        type=int,
        default=DEFAULT_SENSOR_ID,
        help="CSI sensor ID for Jetson cameras.",
    )
    parser.add_argument(
        "--v4l2-device",
        default=DEFAULT_V4L2_DEVICE,
        help="Fallback V4L2 device path, such as /dev/video0.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help="Requested frame width.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help="Requested frame height.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help="Requested frames per second.",
    )
    parser.add_argument(
        "--flip-method",
        type=int,
        default=DEFAULT_FLIP_METHOD,
        help="Jetson GStreamer flip method.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Skip the preview window and only validate frame capture.",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Open the camera, require one valid frame, print success, and exit.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=DEFAULT_STATUS_INTERVAL,
        help="Seconds between preview status messages.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=DEFAULT_MAX_FAILURES,
        help="Consecutive frame failures allowed before exiting.",
    )
    parser.add_argument(
        "--frame-limit",
        type=int,
        default=0,
        help="Optional maximum frames to process before exiting cleanly.",
    )
    parser.add_argument(
        "--report-to",
        default="",
        help="Optional status listener target in host:port form for camera health packets.",
    )
    parser.add_argument(
        "--report-source",
        default=DEFAULT_REPORT_SOURCE,
        help="Source label used in status packets when --report-to is set.",
    )
    parser.add_argument(
        "--serve-webrtc",
        action="store_true",
        help="Expose the camera feed through WebRTC instead of HTTP streaming.",
    )
    parser.add_argument(
        "--signal-bind-host",
        default=DEFAULT_SIGNAL_BIND_HOST,
        help="Bind host for the Jetson-side raw TCP signaling server.",
    )
    parser.add_argument(
        "--signal-port",
        type=int,
        default=DEFAULT_SIGNAL_PORT,
        help="Port for the Jetson-side raw TCP signaling server.",
    )
    parser.add_argument(
        "--signal-public-host",
        default="",
        help="Optional host/IP advertised in status packets for dashboard signaling.",
    )
    parser.add_argument(
        "--serve-cv-shm",
        action="store_true",
        help="Publish the CV GStreamer branch to a local shmsink for a separate CV process.",
    )
    parser.add_argument(
        "--cv-shm-path",
        default=DEFAULT_CV_SHM_PATH,
        help="Local Unix socket path used by the CV branch shmsink.",
    )
    parser.add_argument(
        "--cv-shm-size",
        type=int,
        default=DEFAULT_CV_SHM_SIZE,
        help="Shared-memory buffer size in bytes for the CV branch shmsink.",
    )
    return parser.parse_args()


def build_jetson_pipeline(sensor_id, width, height, fps, flip_method):
    return (
        "nvarguscamerasrc sensor-id={sensor_id} ! "
        "video/x-raw(memory:NVMM), width=(int){width}, height=(int){height}, "
        "format=(string)NV12, framerate=(fraction){fps}/1 ! "
        "nvvidconv flip-method={flip_method} ! "
        "video/x-raw, width=(int){width}, height=(int){height}, format=(string)BGRx ! "
        "videoconvert ! "
        "video/x-raw, format=(string)BGR ! "
        "appsink drop=true sync=false"
    ).format(
        sensor_id=sensor_id,
        width=width,
        height=height,
        fps=fps,
        flip_method=flip_method,
    )


def build_jetson_branched_pipeline(args):
    serve_cv_shm = bool(getattr(args, "serve_cv_shm", False))
    cv_shm_path = getattr(args, "cv_shm_path", DEFAULT_CV_SHM_PATH)
    cv_shm_size = int(getattr(args, "cv_shm_size", DEFAULT_CV_SHM_SIZE))

    cv_branch = (
        "camera_branch. ! queue name=cv_queue leaky=downstream max-size-buffers=2 "
        "max-size-bytes=0 max-size-time=0 ! "
    )
    if serve_cv_shm:
        cv_branch += (
            "tee name=cv_output "
            "cv_output. ! queue name=cv_monitor_queue leaky=downstream max-size-buffers=2 "
            "max-size-bytes=0 max-size-time=0 ! "
            "appsink name=cv_sink emit-signals=true drop=true max-buffers=1 sync=false "
            "cv_output. ! queue name=cv_shm_queue leaky=downstream max-size-buffers=2 "
            "max-size-bytes=0 max-size-time=0 ! "
            "shmsink name=cv_shm_sink socket-path={cv_shm_path} wait-for-connection=false "
            "sync=false shm-size={cv_shm_size}"
        ).format(cv_shm_path=cv_shm_path, cv_shm_size=cv_shm_size)
    else:
        cv_branch += "appsink name=cv_sink emit-signals=true drop=true max-buffers=1 sync=false"

    return (
        "nvarguscamerasrc sensor-id={sensor_id} ! "
        "video/x-raw(memory:NVMM), width=(int){width}, height=(int){height}, "
        "format=(string)NV12, framerate=(fraction){fps}/1 ! "
        "nvvidconv flip-method={flip_method} ! "
        "video/x-raw, width=(int){width}, height=(int){height}, format=(string)BGRx ! "
        "videoconvert ! "
        "video/x-raw, format=(string)BGR ! "
        "tee name=camera_branch "
        "camera_branch. ! queue name=gui_queue leaky=downstream max-size-buffers=2 "
        "max-size-bytes=0 max-size-time=0 ! "
        "appsink name=gui_sink emit-signals=true drop=true max-buffers=1 sync=false "
        "{cv_branch}"
    ).format(
        sensor_id=args.sensor_id,
        width=args.width,
        height=args.height,
        fps=args.fps,
        flip_method=args.flip_method,
        cv_branch=cv_branch,
    )


def open_jetson_camera(args, frame_store=None):
    pipeline = build_jetson_branched_pipeline(args)
    print("Trying Jetson CSI camera path via branched GStreamer tee pipeline.")
    print(f"GStreamer branch pipeline: {pipeline}")
    try:
        capture = BranchedGStreamerCapture(args, gui_frame_store=frame_store)
    except Exception as exc:
        print(f"Branched GStreamer camera path failed: {exc}")
        return None, None
    if capture.isOpened():
        return capture, f"Jetson CSI sensor {args.sensor_id} via GStreamer tee"
    capture.release()
    return None, None


def open_legacy_jetson_camera(args):
    pipeline = build_jetson_pipeline(
        args.sensor_id,
        args.width,
        args.height,
        args.fps,
        args.flip_method,
    )
    print("Trying legacy Jetson CSI camera path via single-sink GStreamer.")
    capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if capture.isOpened():
        return capture, f"Jetson CSI sensor {args.sensor_id}"
    capture.release()
    return None, None


def open_v4l2_camera(args):
    print(f"Trying V4L2 fallback at {args.v4l2_device}.")
    if os.name != "nt" and not os.path.exists(args.v4l2_device):
        print(f"V4L2 device not found: {args.v4l2_device}")
        return None, None

    capture = cv2.VideoCapture(args.v4l2_device, cv2.CAP_V4L2)
    if not capture.isOpened():
        capture.release()
        return None, None

    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    capture.set(cv2.CAP_PROP_FPS, args.fps)
    return capture, f"V4L2 device {args.v4l2_device}"


def open_mock_camera(args):
    print("Trying mock camera source.")
    if np is None:
        print("Mock source requires numpy, but it is not installed.")
        return None, None
    capture = MockCapture(args.width, args.height, args.fps)
    if capture.isOpened():
        return capture, "Mock camera"
    return None, None


def open_camera(args, frame_store=None):
    attempts = []
    if args.source == "auto":
        attempts = (open_jetson_camera, open_v4l2_camera)
    elif args.source == "jetson":
        attempts = (open_jetson_camera,)
    elif args.source == "mock":
        attempts = (open_mock_camera,)
    else:
        attempts = (open_v4l2_camera,)

    for attempt in attempts:
        if attempt is open_jetson_camera:
            capture, label = attempt(args, frame_store)
        else:
            capture, label = attempt(args)
        if capture is not None:
            print(f"Camera opened successfully using {label}.")
            return capture, label

    return None, None


def validate_first_frame(capture):
    ok, frame = capture.read()
    if not ok or frame is None or frame.size == 0:
        return False, None
    return True, frame


def describe_frame(frame):
    height, width = frame.shape[:2]
    return f"{width}x{height}"


def frame_dimensions(frame):
    height, width = frame.shape[:2]
    return width, height


def emit_camera_status(reporter, args, message, source_label, frame=None, **extra_fields):
    if reporter is None:
        return

    payload = {
        "camera_source": source_label,
        "requested_source": args.source,
        "headless": args.headless,
        "probe_only": args.probe_only,
    }
    payload.update(extra_fields)

    if frame is not None:
        width, height = frame_dimensions(frame)
        payload["frame_width"] = width
        payload["frame_height"] = height

    reporter.send(message, **payload)


def webrtc_status_fields(webrtc_manager):
    if webrtc_manager is None:
        return {}
    return webrtc_manager.signal_fields()


def branch_status_fields(capture):
    if capture is None or not hasattr(capture, "branch_status_fields"):
        return {}
    return capture.branch_status_fields()


def runtime_status_fields(capture, webrtc_manager):
    fields = {}
    fields.update(webrtc_status_fields(webrtc_manager))
    fields.update(branch_status_fields(capture))
    return fields


def preview_loop(capture, first_frame, args, source_label, reporter, frame_store, webrtc_manager):
    print("Entering live preview. Press 'q' or Esc to exit.")
    emit_camera_status(
        reporter,
        args,
        "Preview started.",
        source_label,
        frame=first_frame,
        camera_state="preview_start",
        **runtime_status_fields(capture, webrtc_manager),
    )
    if frame_store is not None:
        frame_store.update(first_frame)

    frame_count = 1
    consecutive_failures = 0
    start_time = time.time()
    last_status_time = start_time
    frame = first_frame

    while True:
        if not args.headless:
            cv2.imshow(WINDOW_TITLE, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                print("Exit requested by user.")
                emit_camera_status(
                    reporter,
                    args,
                    "Preview stopped by user.",
                    source_label,
                    frame=frame,
                    camera_state="preview_exit",
                    frame_count=frame_count,
                    **runtime_status_fields(capture, webrtc_manager),
                )
                return "user_exit"

        ok, next_frame = capture.read()
        if not ok or next_frame is None or next_frame.size == 0:
            consecutive_failures += 1
            print(
                f"Frame read failed from {source_label} "
                f"({consecutive_failures}/{args.max_failures})."
            )
            if consecutive_failures >= args.max_failures:
                print("Camera stream became unstable. Exiting preview.")
                emit_camera_status(
                    reporter,
                    args,
                    "Camera stream became unstable.",
                    source_label,
                    frame=frame,
                    camera_state="stream_unstable",
                    frame_count=frame_count,
                    consecutive_failures=consecutive_failures,
                    **runtime_status_fields(capture, webrtc_manager),
                )
                return "stream_unstable"
            continue

        frame = next_frame
        frame_count += 1
        consecutive_failures = 0
        if frame_store is not None:
            frame_store.update(frame)

        if args.frame_limit > 0 and frame_count >= args.frame_limit:
            print(f"Frame limit reached ({args.frame_limit}). Exiting preview.")
            emit_camera_status(
                reporter,
                args,
                "Preview frame limit reached.",
                source_label,
                frame=frame,
                camera_state="frame_limit_reached",
                frame_count=frame_count,
                **runtime_status_fields(capture, webrtc_manager),
            )
            return "frame_limit"

        now = time.time()
        if now - last_status_time >= args.status_interval:
            elapsed = max(now - start_time, 1e-6)
            fps = frame_count / elapsed
            print(
                f"Preview running from {source_label}: "
                f"{frame_count} frames, approx {fps:.1f} FPS."
            )
            emit_camera_status(
                reporter,
                args,
                "Preview running.",
                source_label,
                frame=frame,
                camera_state="preview_running",
                frame_count=frame_count,
                fps=round(fps, 2),
                **runtime_status_fields(capture, webrtc_manager),
            )
            last_status_time = now

    return "preview_complete"


def cleanup(capture, headless, reporter, webrtc_manager):
    print("Starting camera cleanup.")
    if capture is not None:
        capture.release()
    if not headless:
        cv2.destroyAllWindows()
    if webrtc_manager is not None:
        webrtc_manager.stop()
    if reporter is not None:
        reporter.close()
    print("Cleanup complete.")


def main():
    args = parse_args()
    reporter = StatusReporter(args.report_to, args.report_source)
    frame_store = LatestFrameStore() if args.serve_webrtc else None
    webrtc_manager = None

    print("Starting Jetson camera bring-up test.")
    print(
        "Requested settings: "
        f"source={args.source}, resolution={args.width}x{args.height}, fps={args.fps}"
    )

    capture = None
    try:
        emit_camera_status(
            reporter,
            args,
            "Camera bring-up starting.",
            source_label="pending",
            camera_state="startup",
        )
        capture, source_label = open_camera(args, frame_store)
        if capture is None:
            print("Camera could not be opened with the selected settings.")
            print(
                "Confirm the camera ribbon and connector match the target "
                "Jetson board, the camera is supported by the installed Jetson "
                "image, and OpenCV has access to the expected CSI or V4L2 path."
            )
            emit_camera_status(
                reporter,
                args,
                "Camera could not be opened with the selected settings.",
                source_label="unavailable",
                camera_state="open_failed",
            )
            return 1

        ok, first_frame = validate_first_frame(capture)
        if not ok:
            print("Camera opened, but no valid frame was received.")
            emit_camera_status(
                reporter,
                args,
                "Camera opened, but no valid frame was received.",
                source_label,
                camera_state="first_frame_failed",
            )
            return 1

        print(
            "First frame received successfully: "
            f"{describe_frame(first_frame)}."
        )
        if frame_store is not None:
            frame_store.update(first_frame)

        if args.serve_webrtc:
            webrtc_manager = CameraWebRTCManager(
                args.signal_bind_host,
                args.signal_port,
                args.signal_public_host,
                frame_store,
                reporter,
                args.report_source,
                args.fps,
            )
            if not webrtc_manager.start():
                emit_camera_status(
                    reporter,
                    args,
                    "WebRTC dependencies are missing on the Jetson.",
                    source_label,
                    frame=first_frame,
                    camera_state="webrtc_unavailable",
                )
                return 1
        emit_camera_status(
            reporter,
            args,
            "First frame received successfully.",
            source_label,
            frame=first_frame,
            camera_state="first_frame_ok",
            **runtime_status_fields(capture, webrtc_manager),
        )

        if args.probe_only:
            print("Probe-only mode complete.")
            emit_camera_status(
                reporter,
                args,
                "Probe-only mode complete.",
                source_label,
                frame=first_frame,
                camera_state="probe_complete",
                **runtime_status_fields(capture, webrtc_manager),
            )
            return 0

        preview_result = preview_loop(
            capture,
            first_frame,
            args,
            source_label,
            reporter,
            frame_store,
            webrtc_manager,
        )
        emit_camera_status(
            reporter,
            args,
            "Preview loop exited.",
            source_label,
            frame=first_frame,
            camera_state=preview_result,
            **runtime_status_fields(capture, webrtc_manager),
        )
        return 0
    except KeyboardInterrupt:
        print("Interrupted by user.")
        emit_camera_status(
            reporter,
            args,
            "Camera script interrupted by user.",
            source_label="interrupted",
            camera_state="interrupted",
        )
        return 0
    finally:
        cleanup(capture, args.headless, reporter, webrtc_manager)


if __name__ == "__main__":
    sys.exit(main())
