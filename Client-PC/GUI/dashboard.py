import argparse
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import deque
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import dcc, html
from dash.dependencies import Input, Output, State
from flask import jsonify, request
from plotly import graph_objects as go

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None


DEFAULT_CONTROLLER_STATE = {
    "N": 0,
    "E": 0,
    "S": 0,
    "W": 0,
    "LB": 0,
    "RB": 0,
    "LS": 0,
    "RS": 0,
    "SELECT": 0,
    "START": 0,
    "LjoyX": 127,
    "LjoyY": 127,
    "RjoyX": 127,
    "RjoyY": 127,
    "LT": 0,
    "RT": 0,
    "dX": 0,
    "dY": 0,
    "ts": 0,
    "seq": 0,
    "source": "pc",
}

ROVER_STATE_FILE = "/tmp/rover_state"
ROVER_STATE_REQUEST_FILE = "/tmp/rover_state_request"
ROVER_STATE_MAX_AGE_SECONDS = 2.0
STATE_CHANGE_HOLD_SECONDS = 0.5
VALID_HALL_STATES = {"001", "101", "100", "110", "010", "011"}
MAX_HALL_SERIES_POINTS = 600
DEFAULT_HALL_INPUT_FILE = Path(__file__).with_name("hall_feedback.log")


def parse_args():
    parser = argparse.ArgumentParser(
        description="FIU Lunabotics Rover Control. Monitors packets and can proxy them to the server."
    )
    parser.add_argument("--listen-host", default="0.0.0.0", help="TCP host for the dashboard packet listener")
    parser.add_argument("--listen-port", type=int, default=8090, help="TCP port for incoming packets from Go clients")
    parser.add_argument("--ui-host", default="127.0.0.1", help="Host for the Dash web UI")
    parser.add_argument("--ui-port", type=int, default=8050, help="Port for the Dash web UI")
    parser.add_argument(
        "--ui-refresh-ms",
        type=int,
        default=40,
        help="Dashboard polling interval in milliseconds",
    )
    parser.add_argument("--desktop", action="store_true", help="Open the UI in a native desktop window")
    parser.add_argument("--window-width", type=int, default=1400, help="Desktop window width")
    parser.add_argument("--window-height", type=int, default=920, help="Desktop window height")
    parser.add_argument(
        "--forward-to",
        default="",
        help="Optional upstream host:port. When set, packets are forwarded unchanged to the real server.",
    )
    parser.add_argument(
        "--state-url",
        default="",
        help="Optional rover state endpoint URL. Defaults to http://<forward-host>:<forward-port+1>/rover/state",
    )
    parser.add_argument(
        "--max-packet-size",
        type=int,
        default=8192,
        help="Maximum JSON payload size before CRC bytes are appended",
    )
    parser.add_argument(
        "--camera-one-label",
        default="Camera 1",
        help="Label for the first camera panel in the dashboard.",
    )
    parser.add_argument(
        "--camera-two-label",
        default="Camera 2",
        help="Label for the second camera panel in the dashboard.",
    )
    parser.add_argument(
        "--camera-one-url",
        default="",
        help="Deprecated legacy HTTP camera URL override for the first camera panel.",
    )
    parser.add_argument(
        "--camera-two-url",
        default="",
        help="Deprecated legacy HTTP camera URL override for the second camera panel.",
    )
    parser.add_argument(
        "--camera-one-source",
        default="jetson-camera-1",
        help="Status source name associated with camera panel one.",
    )
    parser.add_argument(
        "--camera-two-source",
        default="jetson-camera-2",
        help="Status source name associated with camera panel two.",
    )
    parser.add_argument(
        "--hall-motor-poles",
        type=int,
        default=8,
        help="Motor pole count used to convert Hall transitions to mechanical RPM",
    )
    parser.add_argument(
        "--hall-window-seconds",
        type=float,
        default=8.0,
        help="Rolling time window used for Hall RPM estimation",
    )
    parser.add_argument(
        "--hall-serial-port",
        default="",
        help="Optional serial port used for telemetry input",
    )
    parser.add_argument(
        "--hall-baudrate",
        type=int,
        default=9600,
        help="Serial baud rate when using --hall-serial-port",
    )
    parser.add_argument(
        "--hall-input-file",
        default="",
        help="Optional Hall feedback log file to tail",
    )
    return parser.parse_args()


CONFIG = parse_args()

if CONFIG.hall_motor_poles <= 0 or CONFIG.hall_motor_poles % 2 != 0:
    raise ValueError("--hall-motor-poles must be a positive even number")

if not CONFIG.hall_serial_port and not CONFIG.hall_input_file and DEFAULT_HALL_INPUT_FILE.exists():
    CONFIG.hall_input_file = str(DEFAULT_HALL_INPUT_FILE)


state_lock = threading.Lock()
latest_controller_state = dict(DEFAULT_CONTROLLER_STATE)
latest_controller_meta = {
    "crc_ok": False,
    "bytes": 0,
    "peer": "",
    "source": "pc",
    "seq": 0,
    "last_rx": 0.0,
    "forwarded": False,
    "packet_type": "none",
}
status_sources = {}
camera_signals = {}
status_history = deque(maxlen=40)
log_lines = deque(maxlen=300)
raw_packets = deque(maxlen=60)
metrics = {
    "connections_current": 0,
    "connections_total": 0,
    "packets_rx": 0,
    "packets_forwarded": 0,
    "forward_failures": 0,
    "controller_packets": 0,
    "status_packets": 0,
    "camera_signal_packets": 0,
    "crc_failures": 0,
    "json_failures": 0,
}
latest_state_combo = {
    "status": "idle",
    "requested_mode": "",
    "text": "Hold SELECT to enter state change mode.",
    "hold_started": 0.0,
    "request_issued": False,
}
hall_history = deque(maxlen=MAX_HALL_SERIES_POINTS)
hall_transition_times = deque()
hall_log_lines = deque(maxlen=120)
hall_state = {
    "hall": "---",
    "rpm": 0.0,
    "electrical_rpm": 0.0,
    "transitions_per_second": 0.0,
    "last_line": "",
    "last_update": 0.0,
    "source": "",
    "valid_samples": 0,
    "invalid_lines": 0,
    "enabled": bool(CONFIG.hall_serial_port or CONFIG.hall_input_file),
}


def log(message: str):
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    with state_lock:
        log_lines.append(line)


def hall_log(message: str):
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    with state_lock:
        hall_log_lines.appendleft(line)


def parse_target(target: str):
    if not target:
        return None
    if ":" not in target:
        raise ValueError(f"forward target must be host:port, got {target!r}")
    host, port = target.rsplit(":", 1)
    return host, int(port)


FORWARD_TARGET = parse_target(CONFIG.forward_to) if CONFIG.forward_to else None
STATE_ENDPOINT_URL = (
    CONFIG.state_url
    if CONFIG.state_url
    else (
        f"http://{FORWARD_TARGET[0]}:{FORWARD_TARGET[1] + 1}/rover/state"
        if FORWARD_TARGET
        else ""
    )
)
remote_state_cache = {
    "fetched_at": 0.0,
    "rover_state": None,
    "rover_request": None,
}


def hall_to_int(hall_value):
    if hall_value not in VALID_HALL_STATES:
        return None
    return int(hall_value, 2)


def parse_hall_state(line):
    compact = "".join(ch for ch in line if ch in "01")
    if len(compact) < 3:
        return None

    candidates = [compact[i : i + 3] for i in range(len(compact) - 2)]
    for candidate in reversed(candidates):
        if candidate in VALID_HALL_STATES:
            return candidate
    return None


def prune_hall_transitions(now, window_seconds):
    while hall_transition_times and (now - hall_transition_times[0]) > window_seconds:
        hall_transition_times.popleft()


def update_hall_from_line(line, source):
    now = time.time()
    parsed_state = parse_hall_state(line)

    with state_lock:
        hall_state["last_line"] = line.strip()
        hall_state["source"] = source
        hall_state["last_update"] = now

        if parsed_state is None:
            hall_state["invalid_lines"] += 1
            return

        last_hall = hall_state["hall"]
        if parsed_state != last_hall and last_hall in VALID_HALL_STATES:
            hall_transition_times.append(now)

        prune_hall_transitions(now, CONFIG.hall_window_seconds)

        transitions_per_second = (
            len(hall_transition_times) / CONFIG.hall_window_seconds if CONFIG.hall_window_seconds else 0.0
        )
        electrical_rpm = transitions_per_second * 10.0
        mechanical_rpm = electrical_rpm / (CONFIG.hall_motor_poles / 2.0)

        hall_state["hall"] = parsed_state
        hall_state["transitions_per_second"] = transitions_per_second
        hall_state["electrical_rpm"] = electrical_rpm
        hall_state["rpm"] = mechanical_rpm
        hall_state["valid_samples"] += 1

        hall_history.append(
            {
                "t": now,
                "rpm": mechanical_rpm,
                "electrical_rpm": electrical_rpm,
                "hall": parsed_state,
                "hall_value": hall_to_int(parsed_state),
            }
        )


def hall_serial_reader():
    if serial is None:
        hall_log("pyserial is not installed, so serial telemetry is unavailable.")
        return

    try:
        with serial.Serial(CONFIG.hall_serial_port, CONFIG.hall_baudrate, timeout=1.0) as ser:
            hall_log(f"reading telemetry from {CONFIG.hall_serial_port} @ {CONFIG.hall_baudrate}")
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore")
                update_hall_from_line(line, f"serial:{CONFIG.hall_serial_port}")
    except Exception as exc:  # pragma: no cover
        hall_log(f"serial telemetry stopped: {exc}")


def hall_file_reader():
    path = Path(CONFIG.hall_input_file)
    if not path.exists():
        hall_log(f"telemetry log not found: {path}")
        return

    hall_log(f"tailing telemetry log {path}")
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if not line:
                time.sleep(0.1)
                continue
            update_hall_from_line(line, f"file:{path.name}")


def start_hall_reader_thread():
    if CONFIG.hall_serial_port:
        target = hall_serial_reader
    elif CONFIG.hall_input_file:
        target = hall_file_reader
    else:
        hall_log("Telemetry disabled. Set --hall-serial-port or --hall-input-file to enable it.")
        return None

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def read_exact(sock: socket.socket, size: int):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def open_upstream_connection():
    if not FORWARD_TARGET:
        return None
    upstream = socket.create_connection(FORWARD_TARGET, timeout=3.0)
    upstream.settimeout(5.0)
    return upstream


def verify_packet(packet: bytes):
    if len(packet) < 4:
        return None, False
    payload = packet[:-4]
    expected = struct.unpack(">I", packet[-4:])[0]
    actual = zlib.crc32(payload) & 0xFFFFFFFF
    return payload, actual == expected


def send_framed_packet(host, port, payload):
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
    framed = payload_bytes + struct.pack(">I", crc)
    header = struct.pack(">I", len(framed))
    sock = socket.create_connection((host, port), timeout=3.0)
    try:
        sock.settimeout(5.0)
        sock.sendall(header)
        sock.sendall(framed)
    finally:
        sock.close()


def slugify_source(source):
    return "".join(ch if ch.isalnum() else "-" for ch in source.lower()).strip("-") or "camera"


def signal_target_for_source(source):
    signal = camera_signals.get(source) or {}
    status = status_sources.get(source) or {}
    host = signal.get("signal_host") or status.get("signal_host") or ""
    port = signal.get("signal_port") or status.get("signal_port") or 0
    if not host or not port:
        return None
    return host, int(port)


def display_mode_label(rover_state, rover_request):
    if rover_state and rover_state.get("valid"):
        if is_fresh_state_info(rover_state):
            return rover_state.get("state", "UNKNOWN")
        return f"{rover_state.get('state', 'UNKNOWN')} (last known)"
    if is_fresh_state_info(rover_request):
        return f"{rover_request.get('state', 'UNKNOWN')} (pending)"
    return "UNKNOWN"


def controller_requested_mode(state):
    if int(state.get("N", 0)) == 1:
        return "TELEOP", "SELECT + Y/N -> TELEOP"
    if int(state.get("E", 0)) == 1:
        return "AUTO", "SELECT + B/E -> AUTO"
    if int(state.get("W", 0)) == 1:
        return "IDLE", "SELECT + X/W -> IDLE"
    return "", ""


def update_state_combo_tracker(state, now):
    if int(state.get("SELECT", 0)) == 0:
        latest_state_combo.update(
            {
                "status": "idle",
                "requested_mode": "",
                "text": "Hold SELECT to enter state change mode.",
                "hold_started": 0.0,
                "request_issued": False,
            }
        )
        return

    hold_started = latest_state_combo.get("hold_started", 0.0)
    if not hold_started:
        hold_started = now
        latest_state_combo["hold_started"] = hold_started
        latest_state_combo["request_issued"] = False

    held_for = now - hold_started
    requested_mode, combo_text = controller_requested_mode(state)

    if held_for < STATE_CHANGE_HOLD_SECONDS:
        remaining = max(0.0, STATE_CHANGE_HOLD_SECONDS - held_for)
        latest_state_combo.update(
            {
                "status": "arming",
                "requested_mode": "",
                "text": f"Holding SELECT... wait {remaining:.2f}s before mode buttons can send.",
            }
        )
        return

    if latest_state_combo.get("request_issued"):
        mode = latest_state_combo.get("requested_mode", "")
        latest_state_combo.update(
            {
                "status": "sent",
                "requested_mode": mode,
                "text": (
                    f"Sent request for {mode}. Release SELECT to arm again."
                    if mode
                    else "Sent request. Release SELECT to arm again."
                ),
            }
        )
        return

    if requested_mode:
        latest_state_combo.update(
            {
                "status": "request",
                "requested_mode": requested_mode,
                "text": f"Server-compatible request sent: {combo_text}",
                "request_issued": True,
            }
        )
        return

    latest_state_combo.update(
        {
            "status": "armed",
            "requested_mode": "",
            "text": "SELECT held long enough. Press Y, B, or X to send a mode request.",
        }
    )


def read_rover_state_file(path):
    try:
        raw = open(path, "r", encoding="utf-8").read().strip()
    except OSError:
        return None

    if not raw:
        return None

    parts = [part.strip() for part in raw.split(",")]
    if len(parts) < 2:
        return {"raw": raw, "valid": False}

    try:
        timestamp = int(parts[1])
    except ValueError:
        return {"raw": raw, "valid": False}

    return {
        "raw": raw,
        "valid": True,
        "state": parts[0],
        "timestamp": timestamp,
        "source": parts[2] if len(parts) > 2 else "",
        "seq": parts[3] if len(parts) > 3 else "",
    }


def state_age_text(timestamp_ms):
    if not timestamp_ms:
        return "unknown"
    return age_text(max(0.0, time.time() - (timestamp_ms / 1000.0)))


def is_fresh_state_info(info):
    if not info or not info.get("valid") or not info.get("timestamp"):
        return False
    age_seconds = time.time() - (int(info["timestamp"]) / 1000.0)
    return 0.0 <= age_seconds <= ROVER_STATE_MAX_AGE_SECONDS


def fetch_remote_state_snapshot():
    if not STATE_ENDPOINT_URL:
        return None, None

    now = time.time()
    if now - remote_state_cache["fetched_at"] < 0.25:
        return remote_state_cache["rover_state"], remote_state_cache["rover_request"]

    try:
        with urllib.request.urlopen(STATE_ENDPOINT_URL, timeout=0.35) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        remote_state_cache.update({"fetched_at": now, "rover_state": None, "rover_request": None})
        return None, None

    rover_state = payload.get("rover_state")
    rover_request = payload.get("rover_request")
    remote_state_cache.update(
        {
            "fetched_at": now,
            "rover_state": rover_state if isinstance(rover_state, dict) else None,
            "rover_request": rover_request if isinstance(rover_request, dict) else None,
        }
    )
    return remote_state_cache["rover_state"], remote_state_cache["rover_request"]


def record_raw_packet(peer, total_len, packet_type, source, crc_ok, forwarded, packet):
    raw_packets.appendleft(
        {
            "t": time.strftime("%H:%M:%S"),
            "peer": peer,
            "bytes": total_len,
            "packet_type": packet_type,
            "source": source,
            "crc_ok": crc_ok,
            "forwarded": forwarded,
            "raw_hex": packet.hex()[:480] + ("..." if len(packet) > 240 else ""),
        }
    )


def update_state_from_packet(peer, total_len, packet, forwarded):
    payload, crc_ok = verify_packet(packet)
    packet_type = "unknown"
    source = peer
    now = time.time()
    log_message = None

    with state_lock:
        metrics["packets_rx"] += 1

        if not crc_ok or payload is None:
            metrics["crc_failures"] += 1
            latest_controller_meta.update(
                {
                    "crc_ok": False,
                    "bytes": total_len,
                    "peer": peer,
                    "last_rx": now,
                    "forwarded": forwarded,
                    "packet_type": "crc_fail",
                }
            )
            record_raw_packet(peer, total_len, "crc_fail", source, False, forwarded, packet)
            log_message = f"CRC mismatch from {peer}"
        else:
            try:
                obj = json.loads(payload.decode("utf-8"))
            except Exception as exc:
                metrics["json_failures"] += 1
                latest_controller_meta.update(
                    {
                        "crc_ok": True,
                        "bytes": total_len,
                        "peer": peer,
                        "last_rx": now,
                        "forwarded": forwarded,
                        "packet_type": "json_error",
                    }
                )
                record_raw_packet(peer, total_len, "json_error", source, True, forwarded, packet)
                log_message = f"JSON parse failed from {peer}: {exc}"
            else:
                if not isinstance(obj, dict):
                    metrics["json_failures"] += 1
                    latest_controller_meta.update(
                        {
                            "crc_ok": True,
                            "bytes": total_len,
                            "peer": peer,
                            "last_rx": now,
                            "forwarded": forwarded,
                            "packet_type": "json_error",
                        }
                    )
                    record_raw_packet(peer, total_len, "json_error", source, True, forwarded, packet)
                    log_message = f"Unexpected JSON payload type from {peer}"
                elif obj.get("type") == "status":
                    packet_type = "status"
                    source = obj.get("source") or peer
                    metrics["status_packets"] += 1
                    details = []
                    if obj.get("component"):
                        details.append(f"component={obj.get('component')}")
                    if obj.get("camera_state"):
                        details.append(f"state={obj.get('camera_state')}")
                    if obj.get("camera_source"):
                        details.append(f"path={obj.get('camera_source')}")
                    if obj.get("frame_width") and obj.get("frame_height"):
                        details.append(
                            f"frame={obj.get('frame_width')}x{obj.get('frame_height')}"
                        )
                    if obj.get("fps"):
                        details.append(f"fps={obj.get('fps')}")
                    if obj.get("frame_count"):
                        details.append(f"frames={obj.get('frame_count')}")
                    if obj.get("signal_host") and obj.get("signal_port"):
                        details.append(f"signal={obj.get('signal_host')}:{obj.get('signal_port')}")
                    if obj.get("webrtc_state"):
                        details.append(f"webrtc={obj.get('webrtc_state')}")
                    status_sources[source] = {
                        "message": obj.get("message", ""),
                        "ts": obj.get("ts", 0),
                        "peer": peer,
                        "last_rx": now,
                        "details": ", ".join(details),
                        "signal_host": obj.get("signal_host", ""),
                        "signal_port": obj.get("signal_port", 0),
                        "signal_id": obj.get("signal_id", ""),
                        "webrtc_state": obj.get("webrtc_state", ""),
                    }
                    status_history.appendleft(
                        {
                            "source": source,
                            "message": obj.get("message", ""),
                            "peer": peer,
                            "ts": obj.get("ts", 0),
                            "received": now,
                            "details": ", ".join(details),
                        }
                    )
                    latest_controller_meta.update(
                        {
                            "crc_ok": True,
                            "bytes": total_len,
                            "peer": peer,
                            "last_rx": now,
                            "forwarded": forwarded,
                            "packet_type": packet_type,
                        }
                    )
                    log_message = f"Status packet from {source}: {obj.get('message', '')}"
                    record_raw_packet(peer, total_len, packet_type, source, True, forwarded, packet)
                elif obj.get("type") == "camera_signal":
                    packet_type = "camera_signal"
                    source = obj.get("source") or peer
                    metrics["camera_signal_packets"] += 1
                    camera_signals[source] = {
                        "signal_kind": obj.get("signal_kind", ""),
                        "signal_id": obj.get("signal_id", ""),
                        "sdp_type": obj.get("sdp_type", ""),
                        "sdp": obj.get("sdp", ""),
                        "signal_host": obj.get("signal_host", ""),
                        "signal_port": obj.get("signal_port", 0),
                        "ts": obj.get("ts", 0),
                        "last_rx": now,
                    }
                    status_entry = status_sources.get(source, {})
                    details = status_entry.get("details", "")
                    details_bits = [bit for bit in details.split(", ") if bit]
                    if obj.get("signal_host") and obj.get("signal_port"):
                        details_bits = [
                            bit for bit in details_bits if not bit.startswith("signal=")
                        ]
                        details_bits.append(f"signal={obj.get('signal_host')}:{obj.get('signal_port')}")
                    details_bits = [
                        bit for bit in details_bits if not bit.startswith("webrtc=")
                    ]
                    details_bits.append(f"webrtc={obj.get('signal_kind', 'signal')}")
                    status_sources[source] = {
                        **status_entry,
                        "message": status_entry.get("message", "WebRTC signaling available."),
                        "ts": max(status_entry.get("ts", 0), obj.get("ts", 0)),
                        "peer": peer,
                        "last_rx": now,
                        "details": ", ".join(details_bits),
                        "signal_host": obj.get("signal_host", status_entry.get("signal_host", "")),
                        "signal_port": obj.get("signal_port", status_entry.get("signal_port", 0)),
                        "signal_id": obj.get("signal_id", status_entry.get("signal_id", "")),
                        "webrtc_state": obj.get("signal_kind", status_entry.get("webrtc_state", "")),
                    }
                    latest_controller_meta.update(
                        {
                            "crc_ok": True,
                            "bytes": total_len,
                            "peer": peer,
                            "last_rx": now,
                            "forwarded": forwarded,
                            "packet_type": packet_type,
                        }
                    )
                    log_message = (
                        f"Camera signal from {source}: {obj.get('signal_kind', 'unknown')}"
                    )
                    record_raw_packet(peer, total_len, packet_type, source, True, forwarded, packet)
                else:
                    packet_type = "controller"
                    source = obj.get("source") or peer
                    metrics["controller_packets"] += 1
                    for key in DEFAULT_CONTROLLER_STATE:
                        if key in obj:
                            latest_controller_state[key] = obj[key]
                    latest_controller_state["source"] = source
                    latest_controller_meta.update(
                        {
                            "crc_ok": True,
                            "bytes": total_len,
                            "peer": peer,
                            "source": source,
                            "seq": obj.get("seq", 0),
                            "last_rx": now,
                            "forwarded": forwarded,
                            "packet_type": packet_type,
                        }
                    )
                    update_state_combo_tracker(latest_controller_state, now)
                    record_raw_packet(peer, total_len, packet_type, source, True, forwarded, packet)

    if log_message:
        log(log_message)


def connection_thread(conn: socket.socket, addr):
    peer = f"{addr[0]}:{addr[1]}"
    upstream = None

    with state_lock:
        metrics["connections_current"] += 1
        metrics["connections_total"] += 1

    try:
        if FORWARD_TARGET:
            try:
                upstream = open_upstream_connection()
                log(f"{peer} connected, forwarding to {FORWARD_TARGET[0]}:{FORWARD_TARGET[1]}")
            except OSError as exc:
                with state_lock:
                    metrics["forward_failures"] += 1
                log(f"Upstream unavailable for {peer}: {exc}")
                return
        else:
            log(f"{peer} connected in monitor-only mode")

        conn.settimeout(5.0)
        while True:
            hdr = read_exact(conn, 4)
            if not hdr:
                break

            total_len = struct.unpack(">I", hdr)[0]
            if total_len == 0:
                log(f"Zero-length packet from {peer}")
                continue
            if total_len > (CONFIG.max_packet_size + 4):
                log(f"Oversized packet {total_len}B from {peer}; closing connection")
                break

            packet = read_exact(conn, total_len)
            if not packet:
                break

            forwarded = False
            if upstream is not None:
                try:
                    upstream.sendall(hdr)
                    upstream.sendall(packet)
                    forwarded = True
                    with state_lock:
                        metrics["packets_forwarded"] += 1
                except OSError as exc:
                    with state_lock:
                        metrics["forward_failures"] += 1
                    log(f"Forwarding failed for {peer}: {exc}")
                    break

            update_state_from_packet(peer, total_len, packet, forwarded)
    except socket.timeout:
        log(f"Connection timeout from {peer}")
    except OSError as exc:
        log(f"Socket error from {peer}: {exc}")
    finally:
        if upstream is not None:
            upstream.close()
        conn.close()
        with state_lock:
            metrics["connections_current"] = max(0, metrics["connections_current"] - 1)
        log(f"{peer} disconnected")


def proxy_server_thread():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((CONFIG.listen_host, CONFIG.listen_port))
    server.listen(8)

    if FORWARD_TARGET:
        log(
            f"Packet listener on {CONFIG.listen_host}:{CONFIG.listen_port}, proxying to "
            f"{FORWARD_TARGET[0]}:{FORWARD_TARGET[1]}"
        )
    else:
        log(f"Packet listener on {CONFIG.listen_host}:{CONFIG.listen_port} (monitor only)")

    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=connection_thread, args=(conn, addr), daemon=True)
        thread.start()


def joystick_widget(title, x, y, size=170):
    px = max(0, min(100, (x / 255) * 100))
    py = max(0, min(100, (y / 255) * 100))

    box_style = {
        "position": "relative",
        "width": f"{size}px",
        "height": f"{size}px",
        "border": "1px solid #556",
        "borderRadius": "12px",
        "background": "#0f1724",
        "margin": "6px auto",
    }
    dot_style = {
        "position": "absolute",
        "left": f"calc({px}% - 7px)",
        "top": f"calc({py}% - 7px)",
        "width": "14px",
        "height": "14px",
        "borderRadius": "50%",
        "background": "#18d2a6",
        "boxShadow": "0 0 12px rgba(24,210,166,0.6)",
    }
    cross_vertical = {
        "position": "absolute",
        "left": "50%",
        "top": "0",
        "width": "1px",
        "height": "100%",
        "background": "#263247",
    }
    cross_horizontal = {
        "position": "absolute",
        "left": "0",
        "top": "50%",
        "width": "100%",
        "height": "1px",
        "background": "#263247",
    }

    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(title, style={"textAlign": "center", "fontWeight": "bold"}),
                html.Div(
                    [
                        html.Div(style=cross_vertical),
                        html.Div(style=cross_horizontal),
                        html.Div(style=dot_style),
                    ],
                    style=box_style,
                ),
                html.Div(f"X={x}  Y={y}", style={"textAlign": "center", "fontFamily": "monospace"}),
            ]
        ),
        className="mb-3",
    )


def trigger_bar(title, value):
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(title, style={"fontWeight": "bold"}),
                dbc.Progress(
                    value=int(value),
                    max=255,
                    color="info",
                    animated=False,
                    striped=False,
                    style={"height": "18px", "transition": "none"},
                ),
                html.Div(f"{value}/255", style={"fontFamily": "monospace", "marginTop": "6px"}),
            ]
        ),
        className="mb-3",
    )


def button_light(label, enabled):
    return html.Div(
        [
            html.Div(
                style={
                    "width": "14px",
                    "height": "14px",
                    "borderRadius": "50%",
                    "background": "#3ff58f" if enabled else "#2c3548",
                    "display": "inline-block",
                    "marginRight": "8px",
                    "boxShadow": "0 0 10px rgba(63,245,143,0.5)" if enabled else "none",
                }
            ),
            html.Span(label, style={"fontFamily": "monospace"}),
        ],
        style={"marginBottom": "6px"},
    )


def age_text(seconds):
    if seconds is None:
        return "never"
    return f"{seconds:.2f}s ago"


def build_main_controller_view(controller, combo_text, mode, rover_state, camera_panels):
    actuator_state = (
        "Extend" if int(controller["dY"]) < 0
        else "Retract" if int(controller["dY"]) > 0
        else "Idle"
    )
    vibration_input = "Y pressed" if controller["N"] == 1 else "Idle"
    rover_state_label = rover_state.get("state", "unknown") if rover_state and rover_state.get("valid") else "unknown"

    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Left Control", className="controller-panel-title"),
                                    joystick_widget("Left Stick", int(controller["LjoyX"]), int(controller["LjoyY"])),
                                    trigger_bar("Left Trigger", int(controller["LT"])),
                                    html.Div(
                                        f"LB: {'Pressed' if controller['LB'] == 1 else 'Idle'}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"LS: {'Pressed' if controller['LS'] == 1 else 'Idle'}",
                                        className="controller-meta-line",
                                    ),
                                ]
                            ),
                            className="controller-panel h-100",
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Right Control", className="controller-panel-title"),
                                    joystick_widget("Right Stick", int(controller["RjoyX"]), int(controller["RjoyY"])),
                                    trigger_bar("Right Trigger", int(controller["RT"])),
                                    html.Div(
                                        f"RB: {'Pressed' if controller['RB'] == 1 else 'Idle'}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"RS: {'Pressed' if controller['RS'] == 1 else 'Idle'}",
                                        className="controller-meta-line",
                                    ),
                                ]
                            ),
                            className="controller-panel h-100",
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Xbox Mapping", className="controller-panel-title"),
                                    html.Div(
                                        [
                                            html.Div(
                                                "Y",
                                                className=f"face-button face-button-y{' active' if controller['N'] == 1 else ''}",
                                            ),
                                            html.Div(
                                                "X",
                                                className=f"face-button face-button-x{' active' if controller['W'] == 1 else ''}",
                                            ),
                                            html.Div(
                                                "B",
                                                className=f"face-button face-button-b{' active' if controller['E'] == 1 else ''}",
                                            ),
                                            html.Div(
                                                "A",
                                                className=f"face-button face-button-a{' active' if controller['S'] == 1 else ''}",
                                            ),
                                        ],
                                        className="face-button-grid",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                "U",
                                                className=f"dpad-cell dpad-up{' active' if controller['dY'] < 0 else ''}",
                                            ),
                                            html.Div(
                                                "L",
                                                className=f"dpad-cell dpad-left{' active' if controller['dX'] < 0 else ''}",
                                            ),
                                            html.Div(
                                                "R",
                                                className=f"dpad-cell dpad-right{' active' if controller['dX'] > 0 else ''}",
                                            ),
                                            html.Div(
                                                "D",
                                                className=f"dpad-cell dpad-down{' active' if controller['dY'] > 0 else ''}",
                                            ),
                                        ],
                                        className="dpad-grid",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(f"DPad: x={controller['dX']} y={controller['dY']}", className="controller-meta-line"),
                                            html.Div(f"Actuator: {actuator_state}", className="controller-meta-line"),
                                            html.Div(f"Vibration Input: {vibration_input}", className="controller-meta-line"),
                                            html.Div(f"Rover: {rover_state_label}", className="controller-meta-line"),
                                            html.Div(f"Mode: {mode}", className="controller-meta-line"),
                                            html.Div(f"State Combo: {combo_text}", className="controller-meta-line"),
                                        ],
                                        className="subsystem-stack",
                                    ),
                                ]
                            ),
                            className="controller-panel h-100",
                        ),
                        md=4,
                    ),
                ],
                className="g-3",
            ),
            dbc.Row(camera_panels, className="mt-2"),
        ]
    )


def build_debug_controller_view(controller, combo_state, requested_mode, combo_text, rover_state, rover_request):
    return dbc.Row(
        [
            dbc.Col(
                [
                    joystick_widget("Left Stick", int(controller["LjoyX"]), int(controller["LjoyY"])),
                    trigger_bar("Left Trigger", int(controller["LT"])),
                ],
                md=4,
            ),
            dbc.Col(
                [
                    joystick_widget("Right Stick", int(controller["RjoyX"]), int(controller["RjoyY"])),
                    trigger_bar("Right Trigger", int(controller["RT"])),
                ],
                md=4,
            ),
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Div("Buttons", style={"fontWeight": "bold", "marginBottom": "10px"}),
                            button_light("N", controller["N"] == 1),
                            button_light("E", controller["E"] == 1),
                            button_light("S", controller["S"] == 1),
                            button_light("W", controller["W"] == 1),
                            html.Hr(),
                            button_light("LB", controller["LB"] == 1),
                            button_light("RB", controller["RB"] == 1),
                            button_light("LS", controller["LS"] == 1),
                            button_light("RS", controller["RS"] == 1),
                            html.Hr(),
                            button_light("SELECT", controller["SELECT"] == 1),
                            button_light("START", controller["START"] == 1),
                            html.Hr(),
                            html.Div(
                                f"DPad: x={controller['dX']} y={controller['dY']}",
                                style={"fontFamily": "monospace"},
                            ),
                            html.Div(
                                f"Source: {controller.get('source', 'pc')}",
                                style={"fontFamily": "monospace", "marginTop": "8px"},
                            ),
                            html.Div(
                                f"Timestamp: {controller.get('ts', 0)}",
                                style={"fontFamily": "monospace"},
                            ),
                            html.Hr(),
                            html.Div("State Switch", style={"fontWeight": "bold", "marginBottom": "10px"}),
                            html.Div(
                                f"Combo status: {combo_state}",
                                style={"fontFamily": "monospace"},
                            ),
                            html.Div(
                                f"Requested mode: {requested_mode or 'none'}",
                                style={"fontFamily": "monospace"},
                            ),
                            html.Div(
                                combo_text,
                                style={"fontFamily": "monospace"},
                            ),
                            html.Div(
                                f"Pending request: "
                                f"{rover_request.get('state', 'none') if is_fresh_state_info(rover_request) else 'none'}",
                                style={"fontFamily": "monospace", "marginTop": "8px"},
                            ),
                            html.Div(
                                (
                                    f"Pending seq/source: "
                                    f"{rover_request.get('seq', '-')}/{rover_request.get('source', '-')}"
                                    if is_fresh_state_info(rover_request)
                                    else "Pending seq/source: -"
                                ),
                                style={"fontFamily": "monospace"},
                            ),
                            html.Div(
                                (
                                    f"Rover state: {rover_state.get('state', 'unknown')}"
                                    if rover_state and rover_state.get("valid")
                                    else "Rover state: unknown"
                                ),
                                style={"fontFamily": "monospace", "marginTop": "8px"},
                            ),
                            html.Div(
                                (
                                    f"Rover state age: {state_age_text(rover_state.get('timestamp'))}"
                                    if rover_state and rover_state.get("valid")
                                    else "Rover state age: unknown"
                                ),
                                style={"fontFamily": "monospace"},
                            ),
                        ]
                    )
                ),
                md=4,
            ),
        ]
    )


def camera_view_card(title, source_name, statuses):
    entry = statuses.get(source_name)
    slug = slugify_source(source_name)
    if entry:
        age = time.time() - entry["last_rx"]
        status_lines = [
            html.Div(f"source: {source_name}", style={"fontFamily": "monospace"}),
            html.Div(f"message: {entry.get('message', '-')}", style={"fontFamily": "monospace"}),
            html.Div(f"details: {entry.get('details', '-') or '-'}", style={"fontFamily": "monospace"}),
            html.Div(f"age: {age_text(age)}", style={"fontFamily": "monospace"}),
        ]
    else:
        status_lines = [
            html.Div(f"source: {source_name}", style={"fontFamily": "monospace"}),
            html.Div("message: waiting for camera status", style={"fontFamily": "monospace"}),
            html.Div("details: -", style={"fontFamily": "monospace"}),
            html.Div("age: never", style={"fontFamily": "monospace"}),
        ]

    viewer = html.Div(
        [
            html.Video(
                id=f"webrtc-video-{slug}",
                autoPlay=True,
                muted=True,
                controls=False,
                style={
                    "width": "100%",
                    "height": "260px",
                    "objectFit": "cover",
                    "borderRadius": "12px",
                    "border": "1px solid #263247",
                    "background": "#05070b",
                },
                **{"data-webrtc-video": source_name},
            ),
            html.Div(
                "Waiting for WebRTC session.",
                id=f"webrtc-state-{slug}",
                style={
                    "fontFamily": "monospace",
                    "marginTop": "10px",
                    "color": "#9fb2c8",
                },
                **{"data-webrtc-state": source_name},
            ),
        ],
        **{"data-webrtc-source": source_name},
    )

    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(title, style={"fontWeight": "bold", "marginBottom": "10px"}),
                viewer,
                html.Div(
                    "transport: WebRTC",
                    style={"fontFamily": "monospace", "marginTop": "10px", "marginBottom": "10px"},
                ),
                html.Div(status_lines),
            ],
            style={
                "minHeight": "420px",
            },
        ),
        className="mb-3",
    )


def build_telemetry_rpm_figure(points):
    figure = go.Figure()
    if points:
        base_time = points[0]["t"]
        x_values = [point["t"] - base_time for point in points]
        y_values = [point["rpm"] for point in points]
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="lines",
                name="Mechanical RPM",
                line={"color": "#0b3d91", "width": 3},
                fill="tozeroy",
                fillcolor="rgba(79, 141, 247, 0.12)",
            )
        )

    figure.update_layout(
        paper_bgcolor="#f4f7fb",
        plot_bgcolor="#ffffff",
        margin={"l": 40, "r": 20, "t": 18, "b": 32},
        xaxis_title="Seconds in rolling buffer",
        yaxis_title="RPM",
        font={"family": "JetBrains Mono, monospace", "color": "#142033"},
        xaxis={"gridcolor": "#dbe7f7", "zerolinecolor": "#dbe7f7"},
        yaxis={"gridcolor": "#dbe7f7", "zerolinecolor": "#dbe7f7"},
    )
    return figure


def build_telemetry_hall_figure(points):
    figure = go.Figure()
    if points:
        base_time = points[0]["t"]
        x_values = [point["t"] - base_time for point in points]
        y_values = [point["hall_value"] for point in points]
        labels = [point["hall"] for point in points]
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="lines+markers",
                name="Hall state",
                text=labels,
                hovertemplate="t=%{x:.2f}s<br>hall=%{text}<extra></extra>",
                line={"shape": "hv", "color": "#fc3d21", "width": 2},
                marker={"size": 6, "color": "#0b3d91"},
            )
        )

    figure.update_layout(
        paper_bgcolor="#f4f7fb",
        plot_bgcolor="#ffffff",
        margin={"l": 40, "r": 20, "t": 18, "b": 32},
        xaxis_title="Seconds in rolling buffer",
        yaxis_title="3-bit Hall value",
        font={"family": "JetBrains Mono, monospace", "color": "#142033"},
        xaxis={"gridcolor": "#dbe7f7", "zerolinecolor": "#dbe7f7"},
        yaxis={"gridcolor": "#dbe7f7", "zerolinecolor": "#dbe7f7"},
    )
    return figure


def build_telemetry_view(snapshot, points, logs):
    source_text = snapshot["source"] or "waiting for telemetry"
    if not snapshot["enabled"]:
        source_text = "disabled"

    update_text = (
        "last update: waiting"
        if not snapshot["last_update"]
        else f"last update: {age_text(max(0.0, time.time() - snapshot['last_update']))}"
    )

    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Telemetry Config", className="controller-panel-title"),
                                    html.Div(
                                        f"motor poles: {CONFIG.hall_motor_poles}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"pole pairs: {CONFIG.hall_motor_poles // 2}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"window: {CONFIG.hall_window_seconds:.1f}s",
                                        className="controller-meta-line",
                                    ),
                                ]
                            ),
                            className="h-100",
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Telemetry Source", className="controller-panel-title"),
                                    html.Div(source_text, className="controller-meta-line"),
                                    html.Div(
                                        (
                                            f"serial: {CONFIG.hall_serial_port}"
                                            if CONFIG.hall_serial_port
                                            else f"log: {CONFIG.hall_input_file or 'not set'}"
                                        ),
                                        className="controller-meta-line",
                                    ),
                                    html.Div(update_text, className="controller-meta-line"),
                                ]
                            ),
                            className="h-100",
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Telemetry Stats", className="controller-panel-title"),
                                    html.Div(
                                        f"valid lines: {snapshot['valid_samples']}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"invalid lines: {snapshot['invalid_lines']}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"points buffered: {len(points)}",
                                        className="controller-meta-line",
                                    ),
                                ]
                            ),
                            className="h-100",
                        ),
                        md=4,
                    ),
                ],
                className="g-3 mb-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Estimated Mechanical RPM", className="controller-panel-title"),
                                    dcc.Graph(
                                        id="telemetry-rpm-graph",
                                        figure=build_telemetry_rpm_figure(points),
                                        config={"displayModeBar": False},
                                        className="telemetry-graph",
                                    ),
                                    html.Div("Hall State Timeline", className="controller-panel-title telemetry-subtitle"),
                                    dcc.Graph(
                                        id="telemetry-hall-graph",
                                        figure=build_telemetry_hall_figure(points),
                                        config={"displayModeBar": False},
                                        className="telemetry-graph",
                                    ),
                                ]
                            ),
                            className="h-100",
                        ),
                        md=8,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.Div("Latest Feedback", className="controller-panel-title"),
                                    html.H2(f"{snapshot['rpm']:.2f} RPM", className="telemetry-rpm-readout"),
                                    html.Div(
                                        f"electrical rpm: {snapshot['electrical_rpm']:.2f}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"hall transitions/sec: {snapshot['transitions_per_second']:.2f}",
                                        className="controller-meta-line",
                                    ),
                                    html.Div(
                                        f"hall state: {snapshot['hall']}",
                                        className="controller-meta-line",
                                    ),
                                    html.Pre(
                                        snapshot["last_line"] or "No telemetry line received yet.",
                                        className="telemetry-pre",
                                    ),
                                    html.Div("Reader Logs", className="controller-panel-title telemetry-subtitle"),
                                    html.Pre(
                                        "\n".join(logs) if logs else "Telemetry reader is waiting for data.",
                                        className="telemetry-pre telemetry-log",
                                    ),
                                ]
                            ),
                            className="h-100",
                        ),
                        md=4,
                    ),
                ],
                className="g-3",
            ),
        ]
    )

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG])
app.title = "FIU Lunabotics Rover Control"
app.layout = dbc.Container(
    fluid=True,
    children=[
        html.Img(
            src=app.get_asset_url("lunaboticslogo.png"),
            className="dashboard-logo",
        ),
        html.H2("FIU Lunabotics Rover Control", className="dashboard-title"),
        html.Div(
            "Monitor the repo's wire protocol live and optionally proxy packets to Server-Pi.",
            className="dashboard-description",
        ),
        dcc.Store(id="controller-view", data="main"),
        html.Div(id="status-bar"),
        dcc.Interval(id="tick", interval=max(16, CONFIG.ui_refresh_ms), n_intervals=0),
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Input Activity", style={"fontWeight": "bold"}),
                                html.H4(id="mode-label", style={"marginTop": "10px"}),
                                html.Div(id="controller-summary", style={"fontFamily": "monospace"}),
                            ]
                        )
                    ),
                    md=4,
                ),
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Network", style={"fontWeight": "bold"}),
                                html.Div(id="network-summary", style={"fontFamily": "monospace", "marginTop": "10px"}),
                            ]
                        )
                    ),
                    md=4,
                ),
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Traffic", style={"fontWeight": "bold"}),
                                html.Div(id="traffic-summary", style={"fontFamily": "monospace", "marginTop": "10px"}),
                            ]
                        )
                    ),
                    md=4,
                ),
            ],
            className="mb-3",
        ),
        dbc.Tabs(
            [
                dbc.Tab(label="Controller", tab_id="controller"),
                dbc.Tab(label="Jetson Status", tab_id="status"),
                dbc.Tab(label="Telemetry", tab_id="telemetry"),
                dbc.Tab(label="Logs / Raw", tab_id="logs"),
            ],
            id="tabs",
            active_tab="controller",
            className="mb-3",
        ),
        dbc.ButtonGroup(
            [
                dbc.Button("Xbox View", id="btn-main-view", n_clicks=0, color="primary", outline=False),
                dbc.Button("Debug View", id="btn-debug-view", n_clicks=0, color="secondary", outline=True),
            ],
            id="controller-view-toggle",
            className="controller-view-toggle mb-3",
        ),
        html.Div(id="tab-content"),
    ],
)


@app.server.get("/api/webrtc/<path:source>/offer")
def get_webrtc_offer(source):
    with state_lock:
        signal = dict(camera_signals.get(source, {}))
        status = dict(status_sources.get(source, {}))

    if signal.get("signal_kind") != "offer" or not signal.get("sdp"):
        return jsonify(
            {
                "available": False,
                "source": source,
                "message": status.get("message", "Waiting for WebRTC offer from Jetson."),
                "webrtc_state": status.get("webrtc_state", ""),
            }
        )

    return jsonify(
        {
            "available": True,
            "source": source,
            "signal_id": signal.get("signal_id", ""),
            "sdp_type": signal.get("sdp_type", "offer"),
            "sdp": signal.get("sdp", ""),
            "signal_host": signal.get("signal_host", ""),
            "signal_port": signal.get("signal_port", 0),
            "webrtc_state": status.get("webrtc_state", ""),
            "message": status.get("message", ""),
        }
    )


@app.server.post("/api/webrtc/<path:source>/restart")
def restart_webrtc_offer(source):
    with state_lock:
        target = signal_target_for_source(source)

    if target is None:
        return jsonify({"ok": False, "error": f"No signaling target is known for {source}."}), 404

    try:
        send_framed_packet(
            target[0],
            target[1],
            {
                "type": "camera_signal",
                "source": "dashboard",
                "target_source": source,
                "signal_kind": "request_offer",
                "ts": int(time.time() * 1000),
            },
        )
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify({"ok": True, "source": source})


@app.server.post("/api/webrtc/<path:source>/answer")
def post_webrtc_answer(source):
    payload = request.get_json(silent=True) or {}
    if not payload.get("sdp"):
        return jsonify({"ok": False, "error": "Missing SDP answer payload."}), 400

    with state_lock:
        target = signal_target_for_source(source)

    if target is None:
        return jsonify({"ok": False, "error": f"No signaling target is known for {source}."}), 404

    try:
        send_framed_packet(
            target[0],
            target[1],
            {
                "type": "camera_signal",
                "source": "dashboard",
                "target_source": source,
                "signal_kind": "answer",
                "signal_id": payload.get("signal_id", ""),
                "sdp_type": payload.get("sdp_type", "answer"),
                "sdp": payload["sdp"],
                "ts": int(time.time() * 1000),
            },
        )
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify({"ok": True, "source": source})



@app.callback(
    Output("controller-view-toggle", "style"),
    Input("tabs", "active_tab"),
)
def toggle_controller_view_buttons(active_tab):
    if active_tab == "controller":
        return {}
    return {"display": "none"}


@app.callback(
    Output("controller-view", "data"),
    Output("btn-main-view", "color"),
    Output("btn-debug-view", "color"),
    Output("btn-main-view", "outline"),
    Output("btn-debug-view", "outline"),
    Input("btn-main-view", "n_clicks"),
    Input("btn-debug-view", "n_clicks"),
    State("controller-view", "data"),
)
def set_controller_view(_, __, current_view):
    if not current_view:
        current_view = "main"

    triggered = dash.ctx.triggered_id
    if triggered == "btn-debug-view":
        current_view = "debug"
    elif triggered == "btn-main-view":
        current_view = "main"

    return (
        current_view,
        "primary" if current_view == "main" else "secondary",
        "primary" if current_view == "debug" else "secondary",
        current_view != "main",
        current_view != "debug",
    )


@app.callback(
    Output("status-bar", "children"),
    Output("mode-label", "children"),
    Output("controller-summary", "children"),
    Output("network-summary", "children"),
    Output("traffic-summary", "children"),
    Output("tab-content", "children"),
    Input("tick", "n_intervals"),
    Input("tabs", "active_tab"),
    Input("controller-view", "data"),
)
def update_ui(_, active_tab, controller_view):
    with state_lock:
        controller = dict(latest_controller_state)
        meta = dict(latest_controller_meta)
        combo_info = dict(latest_state_combo)
        traffic = dict(metrics)
        status_source_count = len(status_sources)
        all_statuses = dict(status_sources)

        statuses = {}
        status_log = []
        logs_snapshot = []
        raw_snapshot = []
        telemetry_snapshot = {}
        telemetry_points = []
        telemetry_logs = []

        if active_tab == "status":
            statuses = dict(status_sources)
            status_log = list(status_history)
        elif active_tab == "telemetry":
            telemetry_snapshot = dict(hall_state)
            telemetry_points = list(hall_history)
            telemetry_logs = list(hall_log_lines)
        elif active_tab == "logs":
            logs_snapshot = list(log_lines)
            raw_snapshot = list(raw_packets)

    last_rx_age = (time.time() - meta["last_rx"]) if meta["last_rx"] else None
    remote_rover_state, remote_rover_request = fetch_remote_state_snapshot()
    rover_state = remote_rover_state or read_rover_state_file(ROVER_STATE_FILE)
    rover_request = remote_rover_request or read_rover_state_file(ROVER_STATE_REQUEST_FILE)
    mode = display_mode_label(rover_state, rover_request)
    forward_label = CONFIG.forward_to if CONFIG.forward_to else "disabled"
    combo_state = combo_info.get("status", "idle")
    requested_mode = combo_info.get("requested_mode", "")
    combo_text = combo_info.get("text", "Hold SELECT to enter state change mode.")

    status_bar = (
        f"listener={CONFIG.listen_host}:{CONFIG.listen_port} | "
        f"ui=http://{CONFIG.ui_host}:{CONFIG.ui_port} | "
        f"forward={forward_label} | "
        f"last_packet={meta['packet_type']} | "
        f"last_rx={age_text(last_rx_age)}"
    )

    controller_summary = [
        html.Div(f"source: {meta.get('source') or controller.get('source') or 'pc'}"),
        html.Div(f"seq: {meta.get('seq', 0)}"),
        html.Div(f"peer: {meta.get('peer', '-') or '-'}"),
        html.Div(f"bytes: {meta.get('bytes', 0)}"),
        html.Div(f"crc_ok: {meta.get('crc_ok', False)}"),
        html.Div(f"forwarded: {meta.get('forwarded', False)}"),
        html.Div(f"state combo: {combo_text}"),
        html.Div(
            f"latched mode: {mode}",
            style={"fontWeight": "bold", "marginTop": "6px"},
        ),
    ]

    network_summary = [
        html.Div(f"listen: {CONFIG.listen_host}:{CONFIG.listen_port}"),
        html.Div(f"forward: {forward_label}"),
        html.Div(f"active clients: {traffic['connections_current']}"),
        html.Div(f"client sessions: {traffic['connections_total']}"),
        html.Div(f"ui refresh: {max(16, CONFIG.ui_refresh_ms)}ms"),
        html.Div(f"jetson sources: {status_source_count}"),
    ]

    traffic_summary = [
        html.Div(f"rx packets: {traffic['packets_rx']}"),
        html.Div(f"forwarded: {traffic['packets_forwarded']}"),
        html.Div(f"controller packets: {traffic['controller_packets']}"),
        html.Div(f"status packets: {traffic['status_packets']}"),
        html.Div(f"camera signals: {traffic['camera_signal_packets']}"),
        html.Div(f"crc failures: {traffic['crc_failures']}"),
        html.Div(f"forward failures: {traffic['forward_failures']}"),
    ]

    if active_tab == "controller":
        camera_panels = [
            dbc.Col(
                camera_view_card(
                    CONFIG.camera_one_label,
                    CONFIG.camera_one_source,
                    all_statuses,
                ),
                md=6,
            ),
            dbc.Col(
                camera_view_card(
                    CONFIG.camera_two_label,
                    CONFIG.camera_two_source,
                    all_statuses,
                ),
                md=6,
            ),
        ]
        if controller_view == "debug":
            content = build_debug_controller_view(
                controller,
                combo_state,
                requested_mode,
                combo_text,
                rover_state,
                rover_request,
            )
        else:
            content = build_main_controller_view(
                controller,
                combo_text,
                mode,
                rover_state,
                camera_panels,
            )
    elif active_tab == "status":
        if statuses:
            rows = []
            for source, entry in sorted(statuses.items(), key=lambda item: item[1]["last_rx"], reverse=True):
                age = time.time() - entry["last_rx"]
                rows.append(
                    html.Tr(
                        [
                            html.Td(source),
                            html.Td(entry["message"]),
                            html.Td(entry.get("details", "")),
                            html.Td(entry["peer"]),
                            html.Td(age_text(age)),
                            html.Td(entry["ts"]),
                        ]
                    )
                )
        else:
            rows = [html.Tr([html.Td("No status packets received yet", colSpan=6)])]

        recent = []
        for entry in status_log[:12]:
            detail_suffix = ""
            if entry.get("details"):
                detail_suffix = f" | {entry['details']}"
            recent.append(
                html.Div(
                    f"{entry['source']} | {entry['message']}{detail_suffix} | peer={entry['peer']} | ts={entry['ts']}",
                    style={"fontFamily": "monospace", "fontSize": "12px", "marginBottom": "6px"},
                )
            )

        content = dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Latest status by source", style={"fontWeight": "bold", "marginBottom": "10px"}),
                                dbc.Table(
                                    [
                                        html.Thead(
                                            html.Tr(
                                                [
                                                    html.Th("Source"),
                                                    html.Th("Message"),
                                                    html.Th("Details"),
                                                    html.Th("Peer"),
                                                    html.Th("Age"),
                                                    html.Th("ts"),
                                                ]
                                            )
                                        ),
                                        html.Tbody(rows),
                                    ],
                                    bordered=True,
                                    hover=True,
                                    responsive=True,
                                    size="sm",
                                ),
                            ]
                        )
                    ),
                    md=7,
                ),
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Recent status traffic", style={"fontWeight": "bold", "marginBottom": "10px"}),
                                html.Div(
                                    recent or [html.Div("No status traffic yet.", style={"fontFamily": "monospace"})],
                                    style={"maxHeight": "420px", "overflowY": "auto"},
                                ),
                            ]
                        )
                    ),
                    md=5,
                ),
            ]
        )
    elif active_tab == "telemetry":
        content = build_telemetry_view(telemetry_snapshot, telemetry_points, telemetry_logs)
    else:
        log_text = "\n".join(logs_snapshot[-200:])
        raw_text = "\n".join(
            [
                (
                    f"{pkt['t']}  {pkt['packet_type']}  src={pkt['source']}  peer={pkt['peer']}  "
                    f"{pkt['bytes']}B  crc_ok={pkt['crc_ok']}  forwarded={pkt['forwarded']}  hex={pkt['raw_hex']}"
                )
                for pkt in raw_snapshot[:25]
            ]
        )

        content = dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Logs", style={"fontWeight": "bold", "marginBottom": "10px"}),
                                html.Pre(
                                    log_text or "No logs yet.",
                                    style={
                                        "background": "#0b1119",
                                        "border": "1px solid #263247",
                                        "padding": "12px",
                                        "height": "420px",
                                        "overflowY": "auto",
                                        "fontFamily": "Consolas, monospace",
                                        "fontSize": "12px",
                                        "color": "#d9e2f2",
                                    },
                                ),
                            ]
                        )
                    ),
                    md=6,
                ),
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Raw packet preview", style={"fontWeight": "bold", "marginBottom": "10px"}),
                                html.Pre(
                                    raw_text or "No packets received yet.",
                                    style={
                                        "background": "#0b1119",
                                        "border": "1px solid #263247",
                                        "padding": "12px",
                                        "height": "420px",
                                        "overflowY": "auto",
                                        "fontFamily": "Consolas, monospace",
                                        "fontSize": "12px",
                                        "color": "#d9e2f2",
                                        "whiteSpace": "pre-wrap",
                                    },
                                ),
                            ]
                        )
                    ),
                    md=6,
                ),
            ]
        )

    return status_bar, mode, controller_summary, network_summary, traffic_summary, content


def start_proxy_server_thread():
    thread = threading.Thread(target=proxy_server_thread, daemon=True)
    thread.start()
    return thread


def start_background_threads():
    start_hall_reader_thread()
    return start_proxy_server_thread()


def run_browser_mode():
    # When the reloader is disabled, Werkzeug never sets WERKZEUG_RUN_MAIN.
    # Start the background listeners in the single main process in that case.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or "WERKZEUG_RUN_MAIN" not in os.environ:
        start_background_threads()
    print(
        "Starting dashboard UI on "
        f"http://{CONFIG.ui_host}:{CONFIG.ui_port} | "
        f"packet listener on {CONFIG.listen_host}:{CONFIG.listen_port}"
    )
    app.run(
        host=CONFIG.ui_host,
        port=CONFIG.ui_port,
        debug=False,
        use_reloader=False,
        dev_tools_hot_reload=False,
    )


def run_desktop_mode():
    start_background_threads()
    if os.environ.get("SNAP_NAME") == "code":
        # VS Code's snap injects GTK/GIO paths that break pywebview's desktop backend.
        for key in list(os.environ):
            if key.startswith("SNAP"):
                os.environ.pop(key, None)

        for key in [
            "GDK_PIXBUF_MODULEDIR",
            "GDK_PIXBUF_MODULE_FILE",
            "GIO_LAUNCHED_DESKTOP_FILE",
            "GIO_LAUNCHED_DESKTOP_FILE_PID",
            "GIO_MODULE_DIR",
            "GSETTINGS_SCHEMA_DIR",
            "GTK_EXE_PREFIX",
            "GTK_IM_MODULE_FILE",
            "GTK_MODULES",
            "GTK_PATH",
            "LOCPATH",
            "VSCODE_NLS_CONFIG",
        ]:
            os.environ.pop(key, None)

        if "XDG_DATA_DIRS_VSCODE_SNAP_ORIG" in os.environ:
            os.environ["XDG_DATA_DIRS"] = os.environ["XDG_DATA_DIRS_VSCODE_SNAP_ORIG"]
        if "XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG" in os.environ:
            os.environ["XDG_CONFIG_DIRS"] = os.environ["XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG"]

    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Desktop mode requires pywebview. Install it with:\n"
            "  pip install pywebview\n"
            "or install from requirements.txt again."
        ) from exc

    ui_url = f"http://{CONFIG.ui_host}:{CONFIG.ui_port}"

    def run_dash_server():
        app.run(
            host=CONFIG.ui_host,
            port=CONFIG.ui_port,
            debug=False,
            use_reloader=False,
        )

    threading.Thread(target=run_dash_server, daemon=True).start()

    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            probe = socket.create_connection((CONFIG.ui_host, CONFIG.ui_port), timeout=0.5)
            probe.close()
            break
        except OSError:
            time.sleep(0.1)

    print(
        "Starting desktop dashboard window | "
        f"embedded UI={ui_url} | "
        f"packet listener={CONFIG.listen_host}:{CONFIG.listen_port}"
    )
    window = webview.create_window(
        "FIU Lunabotics Rover Control",
        ui_url,
        width=CONFIG.window_width,
        height=CONFIG.window_height,
    )
    webview.start()


if __name__ == "__main__":
    if CONFIG.desktop:
        run_desktop_mode()
    else:
        run_browser_mode()
