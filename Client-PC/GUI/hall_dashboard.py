import argparse
import math
import threading
import time
from collections import deque
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
import pydash
from dash import Input, Output, dcc, html
from plotly import graph_objects as go

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None


VALID_HALL_STATES = {"001", "101", "100", "110", "010", "011"}
MAX_SERIES_POINTS = 600


def parse_args():
    parser = argparse.ArgumentParser(
        description="Graph Hall-effect motor feedback and estimate RPM over time."
    )
    parser.add_argument("--ui-host", default="127.0.0.1", help="Host for the Dash UI")
    parser.add_argument("--ui-port", type=int, default=8060, help="Port for the Dash UI")
    parser.add_argument(
        "--ui-refresh-ms",
        type=int,
        default=200,
        help="UI refresh interval in milliseconds",
    )
    parser.add_argument(
        "--motor-poles",
        type=int,
        default=8,
        help="Motor pole count used to convert Hall transitions to mechanical RPM",
    )
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=8.0,
        help="Rolling time window used for RPM estimation",
    )
    parser.add_argument(
        "--serial-port",
        default="",
        help="Optional serial port to read directly from the microcontroller",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=9600,
        help="Serial baud rate when using --serial-port",
    )
    parser.add_argument(
        "--input-file",
        default="",
        help="Optional log file to tail instead of reading from serial",
    )
    return parser.parse_args()


CONFIG = parse_args()

if CONFIG.motor_poles <= 0 or CONFIG.motor_poles % 2 != 0:
    raise ValueError("--motor-poles must be a positive even number")

if not CONFIG.serial_port and not CONFIG.input_file:
    raise ValueError("Provide either --serial-port or --input-file")


state_lock = threading.Lock()
hall_history = deque(maxlen=MAX_SERIES_POINTS)
transition_times = deque()
log_lines = deque(maxlen=120)
current_state = {
    "hall": "---",
    "rpm": 0.0,
    "electrical_rpm": 0.0,
    "transitions_per_second": 0.0,
    "last_line": "",
    "last_update": 0.0,
    "source": "",
    "valid_samples": 0,
    "invalid_lines": 0,
}


def log(message):
    timestamp = time.strftime("%H:%M:%S")
    with state_lock:
        log_lines.appendleft(f"[{timestamp}] {message}")


def hall_to_int(hall_state):
    if hall_state not in VALID_HALL_STATES:
        return None
    return int(hall_state, 2)


def parse_hall_state(line):
    compact = "".join(ch for ch in line if ch in "01")
    if len(compact) < 3:
        return None

    candidates = [compact[i : i + 3] for i in range(len(compact) - 2)]
    valid = pydash.find_last(candidates, lambda candidate: candidate in VALID_HALL_STATES)
    return valid


def prune_transitions(now, window_seconds):
    while transition_times and (now - transition_times[0]) > window_seconds:
        transition_times.popleft()


def update_from_line(line, source):
    now = time.time()
    hall_state = parse_hall_state(line)

    with state_lock:
        current_state["last_line"] = line.strip()
        current_state["source"] = source
        current_state["last_update"] = now

        if hall_state is None:
            current_state["invalid_lines"] += 1
            return

        last_hall = current_state["hall"]
        if hall_state != last_hall and last_hall in VALID_HALL_STATES:
            transition_times.append(now)

        prune_transitions(now, CONFIG.window_seconds)

        transitions_per_second = len(transition_times) / CONFIG.window_seconds if CONFIG.window_seconds else 0.0
        electrical_rpm = transitions_per_second * 10.0  # 6 Hall transitions per electrical revolution
        mechanical_rpm = electrical_rpm / (CONFIG.motor_poles / 2.0)

        current_state["hall"] = hall_state
        current_state["transitions_per_second"] = transitions_per_second
        current_state["electrical_rpm"] = electrical_rpm
        current_state["rpm"] = mechanical_rpm
        current_state["valid_samples"] += 1

        hall_history.append(
            {
                "t": now,
                "rpm": mechanical_rpm,
                "electrical_rpm": electrical_rpm,
                "hall": hall_state,
                "hall_value": hall_to_int(hall_state),
            }
        )


def serial_reader():
    if serial is None:
        raise RuntimeError("pyserial is required for --serial-port mode")

    with serial.Serial(CONFIG.serial_port, CONFIG.baudrate, timeout=1.0) as ser:
        log(f"reading serial feedback from {CONFIG.serial_port} @ {CONFIG.baudrate}")
        while True:
            raw = ser.readline()
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", errors="ignore")
            except Exception:
                continue
            update_from_line(line, f"serial:{CONFIG.serial_port}")


def file_reader():
    path = Path(CONFIG.input_file)
    log(f"tailing feedback log {path}")
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if not line:
                time.sleep(0.1)
                continue
            update_from_line(line, f"file:{path.name}")


def start_reader_thread():
    target = serial_reader if CONFIG.serial_port else file_reader
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def build_rpm_figure(points):
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
                line={"color": "#4dd4ac", "width": 3},
            )
        )

    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#111723",
        plot_bgcolor="#111723",
        margin={"l": 40, "r": 20, "t": 30, "b": 40},
        xaxis_title="Seconds in rolling buffer",
        yaxis_title="RPM",
    )
    return figure


def build_hall_figure(points):
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
                line={"shape": "hv", "color": "#f0b34a", "width": 2},
                marker={"size": 6},
            )
        )

    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="#111723",
        plot_bgcolor="#111723",
        margin={"l": 40, "r": 20, "t": 30, "b": 40},
        xaxis_title="Seconds in rolling buffer",
        yaxis_title="3-bit Hall value",
    )
    return figure


app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG])
app.title = "FIU Luna1 Hall Telemetry"

app.layout = dbc.Container(
    fluid=True,
    children=[
        html.H2("FIU Luna1 Hall Telemetry"),
        html.Div(
            "Live Hall-effect feedback monitor with estimated motor speed over time.",
            className="mb-3 text-muted",
        ),
        dcc.Interval(id="tick", interval=max(50, CONFIG.ui_refresh_ms), n_intervals=0),
        dbc.Row(
            [
                dbc.Col(html.Div(id="status-summary"), md=4),
                dbc.Col(html.Div(id="source-summary"), md=4),
                dbc.Col(html.Div(id="sample-summary"), md=4),
            ],
            className="mb-3",
        ),
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Estimated Mechanical RPM", style={"fontWeight": "bold"}),
                                dcc.Graph(id="rpm-graph", config={"displayModeBar": False}),
                            ]
                        )
                    ),
                    md=8,
                ),
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Latest Feedback", style={"fontWeight": "bold", "marginBottom": "10px"}),
                                html.H3(id="current-hall", style={"fontFamily": "monospace"}),
                                html.Div(id="current-rpm", style={"fontFamily": "monospace", "marginBottom": "6px"}),
                                html.Div(
                                    id="current-electrical-rpm",
                                    style={"fontFamily": "monospace", "marginBottom": "6px"},
                                ),
                                html.Div(
                                    id="current-transition-rate",
                                    style={"fontFamily": "monospace", "marginBottom": "6px"},
                                ),
                                html.Div(
                                    id="last-update-age",
                                    style={"fontFamily": "monospace", "marginBottom": "10px"},
                                ),
                                html.Pre(
                                    id="last-line",
                                    style={
                                        "fontFamily": "monospace",
                                        "whiteSpace": "pre-wrap",
                                        "background": "#161d2d",
                                        "padding": "10px",
                                        "borderRadius": "8px",
                                        "minHeight": "88px",
                                    },
                                ),
                            ]
                        )
                    ),
                    md=4,
                ),
            ],
            className="mb-3",
        ),
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Hall State Timeline", style={"fontWeight": "bold"}),
                                dcc.Graph(id="hall-graph", config={"displayModeBar": False}),
                            ]
                        )
                    ),
                    md=8,
                ),
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Div("Recent Reader Logs", style={"fontWeight": "bold", "marginBottom": "10px"}),
                                html.Pre(
                                    id="reader-logs",
                                    style={
                                        "fontFamily": "monospace",
                                        "whiteSpace": "pre-wrap",
                                        "background": "#161d2d",
                                        "padding": "10px",
                                        "borderRadius": "8px",
                                        "minHeight": "350px",
                                    },
                                ),
                            ]
                        )
                    ),
                    md=4,
                ),
            ]
        ),
    ],
)


@app.callback(
    Output("status-summary", "children"),
    Output("source-summary", "children"),
    Output("sample-summary", "children"),
    Output("rpm-graph", "figure"),
    Output("hall-graph", "figure"),
    Output("current-hall", "children"),
    Output("current-rpm", "children"),
    Output("current-electrical-rpm", "children"),
    Output("current-transition-rate", "children"),
    Output("last-update-age", "children"),
    Output("last-line", "children"),
    Output("reader-logs", "children"),
    Input("tick", "n_intervals"),
)
def update_ui(_):
    with state_lock:
        snapshot = dict(current_state)
        points = list(hall_history)
        logs = list(log_lines)

    age = max(0.0, time.time() - snapshot["last_update"]) if snapshot["last_update"] else math.inf
    status_summary = dbc.Card(
        dbc.CardBody(
            [
                html.Div("Motor Config", style={"fontWeight": "bold"}),
                html.Div(f"motor poles: {CONFIG.motor_poles}", style={"fontFamily": "monospace"}),
                html.Div(f"pole pairs: {CONFIG.motor_poles // 2}", style={"fontFamily": "monospace"}),
                html.Div(f"window: {CONFIG.window_seconds:.1f}s", style={"fontFamily": "monospace"}),
            ]
        )
    )
    source_summary = dbc.Card(
        dbc.CardBody(
            [
                html.Div("Input Source", style={"fontWeight": "bold"}),
                html.Div(snapshot["source"] or "waiting for data", style={"fontFamily": "monospace"}),
                html.Div(f"ui: http://{CONFIG.ui_host}:{CONFIG.ui_port}", style={"fontFamily": "monospace"}),
            ]
        )
    )
    sample_summary = dbc.Card(
        dbc.CardBody(
            [
                html.Div("Samples", style={"fontWeight": "bold"}),
                html.Div(f"valid lines: {snapshot['valid_samples']}", style={"fontFamily": "monospace"}),
                html.Div(f"invalid lines: {snapshot['invalid_lines']}", style={"fontFamily": "monospace"}),
                html.Div(f"points buffered: {len(points)}", style={"fontFamily": "monospace"}),
            ]
        )
    )

    return (
        status_summary,
        source_summary,
        sample_summary,
        build_rpm_figure(points),
        build_hall_figure(points),
        snapshot["hall"],
        f"mechanical rpm: {snapshot['rpm']:.2f}",
        f"electrical rpm: {snapshot['electrical_rpm']:.2f}",
        f"hall transitions/sec: {snapshot['transitions_per_second']:.2f}",
        "last update: waiting"
        if not snapshot["last_update"]
        else f"last update: {age:.2f}s ago",
        snapshot["last_line"] or "No feedback line received yet.",
        "\n".join(logs) if logs else "Reader has started. Waiting for Hall feedback...",
    )


if __name__ == "__main__":
    start_reader_thread()
    app.run(host=CONFIG.ui_host, port=CONFIG.ui_port, debug=False)
