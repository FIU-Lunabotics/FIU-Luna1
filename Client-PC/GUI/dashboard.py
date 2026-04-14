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

import dash
import dash_bootstrap_components as dbc
from dash import dcc, html
from dash.dependencies import Input, Output, State


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


def parse_args():
    parser = argparse.ArgumentParser(
        description="FIU Luna1 teleop dashboard. Monitors packets and can proxy them to the server."
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
    return parser.parse_args()


CONFIG = parse_args()


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


def log(message: str):
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    with state_lock:
        log_lines.append(line)


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


def state_age_text(timestamp_ms):
    if not timestamp_ms:
        return "unknown"
    return age_text(max(0.0, time.time() - (timestamp_ms / 1000.0)))


def is_fresh_state_info(info):
    if not info or not info.get("valid") or not info.get("timestamp"):
        return False
    age_seconds = time.time() - (int(info["timestamp"]) / 1000.0)
    return 0.0 <= age_seconds <= ROVER_STATE_MAX_AGE_SECONDS


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
                    status_sources[source] = {
                        "message": obj.get("message", ""),
                        "ts": obj.get("ts", 0),
                        "peer": peer,
                        "last_rx": now,
                    }
                    status_history.appendleft(
                        {
                            "source": source,
                            "message": obj.get("message", ""),
                            "peer": peer,
                            "ts": obj.get("ts", 0),
                            "received": now,
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


def build_main_controller_view(controller, combo_text, mode, rover_state):
    actuator_state = (
        "Extend" if int(controller["dY"]) < 0
        else "Retract" if int(controller["dY"]) > 0
        else "Idle"
    )
    vibration_input = "Y pressed" if controller["N"] == 1 else "Idle"
    rover_state_label = rover_state.get("state", "unknown") if rover_state and rover_state.get("valid") else "unknown"

    return dbc.Row(
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


app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG])
app.title = "FIU Luna1 Teleop Dashboard"
app.layout = dbc.Container(
    fluid=True,
    children=[
        html.H2("FIU Luna1 Teleop Dashboard", className="dashboard-title"),
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
    Output("controller-view-toggle", "style"),
    Input("tabs", "active_tab"),
)
def toggle_controller_view_buttons(active_tab):
    if active_tab == "controller":
        return {}
    return {"display": "none"}


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

        statuses = {}
        status_log = []
        logs_snapshot = []
        raw_snapshot = []

        if active_tab == "status":
            statuses = dict(status_sources)
            status_log = list(status_history)
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
        html.Div(f"crc failures: {traffic['crc_failures']}"),
        html.Div(f"forward failures: {traffic['forward_failures']}"),
    ]

    if active_tab == "controller":
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
            content = build_main_controller_view(controller, combo_text, mode, rover_state)
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
                            html.Td(entry["peer"]),
                            html.Td(age_text(age)),
                            html.Td(entry["ts"]),
                        ]
                    )
                )
        else:
            rows = [html.Tr([html.Td("No status packets received yet", colSpan=5)])]

        recent = []
        for entry in status_log[:12]:
            recent.append(
                html.Div(
                    f"{entry['source']} | {entry['message']} | peer={entry['peer']} | ts={entry['ts']}",
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


def run_browser_mode():
    # Dash's hot reloader starts a parent supervisor process and a child process.
    # Only the child should own the packet listener socket.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_proxy_server_thread()
    print(
        "Starting dashboard UI on "
        f"http://{CONFIG.ui_host}:{CONFIG.ui_port} | "
        f"packet listener on {CONFIG.listen_host}:{CONFIG.listen_port}"
    )
    app.run(host=CONFIG.ui_host, port=CONFIG.ui_port, debug=True, dev_tools_hot_reload=True)


def run_desktop_mode():
    start_proxy_server_thread()
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
        "FIU Luna1 Teleop Dashboard",
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
