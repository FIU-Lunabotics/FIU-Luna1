Here’s the plan:

1. A ROS Noetic Python node that talks to the Pi server on a **different port** but uses the **same framing and CRC logic**, plus its own packet config.
2. A separate markdown doc (already generated and attached) that walks through *every* server function and how to extend it; you can drop that straight into your repo as `docs/server_pi_network_stack.md`.

Below I’ll give you the ROS node example inline, then recap how to plug in a different packet config. The detailed server walkthrough is in the attached file. 

***

## 1. ROS Noetic Python client node

Assumptions:

- You will build a `catkin` package, e.g. `server_pi_client`.
- The Pi server will be started on a **different port**, for example `9090`:
  ```bash
  ./server -port 9090 -config byte_config_ros.json -serial-device /dev/ttyACM1
  ```
- The ROS node will:
  - Subscribe to some topic that provides `ControllerState`-like data (you can adapt this).
  - Optionally publish rover status or server responses.
  - Use the exact wire format: `[4-byte big-endian length][JSON][4-byte CRC32]`.
- CRC is IEEE CRC32, same as Go’s `crc32.ChecksumIEEE`.

### Package layout

Minimal layout:

```text
server_pi_client/
  CMakeLists.txt
  package.xml
  src/
    server_pi_client_node.py
  scripts/   # (optional, if you prefer to install node here)
```

Your `CMakeLists.txt` can be essentially boilerplate Python package config; I’ll focus on the node.

### Example node: `src/server_pi_client_node.py`

This uses raw sockets and the same framing as the Go server. It also shows where you would plug in a **different packet schema** if you want your ROS node to send a different JSON shape than the PC gamepad client.

```python
#!/usr/bin/env python3
import rospy
import socket
import struct
import json
import threading
import time
import zlib  # CRC32

from std_msgs.msg import String
# Replace with your own message type if you have a custom ControllerState msg
# from your_msgs.msg import ControllerState

class ServerPiClient(object):
    def __init__(self):
        # Params
        self.host = rospy.get_param("~server_host", "raspberrypi.local")
        self.port = rospy.get_param("~server_port", 9090)
        self.source = rospy.get_param("~source", "ros_node")
        self.seq_start = rospy.get_param("~seq_start", 1)

        # This node can:
        # - Subscribe to a topic that carries controller-like commands
        # - Publish status lines from the server (if we ever read back)
        self.cmd_sub = rospy.Subscriber("server_pi/commands",
                                        String,  # or your custom msg
                                        self.cmd_callback,
                                        queue_size=10)
        self.status_pub = rospy.Publisher("server_pi/status",
                                          String,
                                          queue_size=10)

        self.sock = None
        self.seq = self.seq_start
        self.lock = threading.Lock()
        self.reader_thread = threading.Thread(target=self.reader_loop)
        self.reader_thread.daemon = True

        self.connect()

    # ----------------------------------------------------------------------
    # Low-level framing helpers
    # ----------------------------------------------------------------------
    def compute_crc(self, payload_bytes):
        # zlib.crc32 returns a signed int in Python 2; mask for consistency
        return zlib.crc32(payload_bytes) & 0xFFFFFFFF

    def send_packet(self, payload_dict):
        """
        payload_dict -> JSON -> bytes
        frame = [len(payload+crc)][payload][crc]
        """
        with self.lock:
            if self.sock is None:
                rospy.logwarn("Socket not connected, dropping packet")
                return

            try:
                payload_json = json.dumps(payload_dict, separators=(",", ":"))
                payload_bytes = payload_json.encode("utf-8")

                crc = self.compute_crc(payload_bytes)
                crc_bytes = struct.pack(">I", crc)  # big-endian uint32

                body = payload_bytes + crc_bytes
                length = len(body)
                header = struct.pack(">I", length)

                self.sock.sendall(header + body)
            except Exception as e:
                rospy.logerr("Error sending packet: %s", e)
                self.close()
                self.connect()

    def recv_exact(self, n):
        """
        Read exactly n bytes from the socket or raise IOError.
        """
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise IOError("Socket closed")
            buf += chunk
        return buf

    def reader_loop(self):
        """
        Optional: if the server ever sends us data, we can decode it here.
        Currently the Go server only reads from clients, but this makes the
        connection fully duplex if you extend the server later.
        """
        while not rospy.is_shutdown():
            if self.sock is None:
                time.sleep(0.1)
                continue
            try:
                # Read length
                hdr = self.recv_exact(4)
                total_len = struct.unpack(">I", hdr)[0]
                if total_len == 0:
                    rospy.logwarn("Received zero-length packet, ignoring")
                    continue
                if total_len > 8192 + 4:
                    rospy.logwarn("Received oversized packet: %d bytes", total_len)
                    # Drain and skip
                    _ = self.recv_exact(total_len)
                    continue

                body = self.recv_exact(total_len)
                if len(body) < 4:
                    rospy.logwarn("Body too short for CRC")
                    continue

                payload_bytes = body[:-4]
                wire_crc = struct.unpack(">I", body[-4:])[0]
                local_crc = self.compute_crc(payload_bytes)

                if wire_crc != local_crc:
                    rospy.logwarn("CRC mismatch in inbound packet")
                    continue

                # Try to parse as JSON
                try:
                    obj = json.loads(payload_bytes.decode("utf-8"))
                except Exception as e:
                    rospy.logwarn("JSON decode error in inbound packet: %s", e)
                    continue

                # You can now inspect obj["type"], etc.
                # For now, just publish raw JSON on a status topic
                self.status_pub.publish(json.dumps(obj))

            except IOError:
                rospy.logwarn("Socket closed in reader_loop, reconnecting...")
                self.close()
                self.connect()
            except Exception as e:
                rospy.logerr("Error in reader_loop: %s", e)
                self.close()
                self.connect()

    # ----------------------------------------------------------------------
    # Connection management
    # ----------------------------------------------------------------------
    def connect(self):
        """
        Establish the TCP connection to the server and start the reader thread.
        """
        while not rospy.is_shutdown():
            try:
                rospy.loginfo("Connecting to %s:%d", self.host, self.port)
                self.sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                rospy.loginfo("Connected to Server-Pi")
                if not self.reader_thread.is_alive():
                    self.reader_thread = threading.Thread(target=self.reader_loop)
                    self.reader_thread.daemon = True
                    self.reader_thread.start()
                return
            except Exception as e:
                rospy.logwarn("Connect failed: %s; retrying...", e)
                time.sleep(1.0)

    def close(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None

    # ----------------------------------------------------------------------
    # ROS integration
    # ----------------------------------------------------------------------
    def cmd_callback(self, msg):
        """
        Convert some ROS message into a ControllerState-like JSON and send.
        Here msg is a std_msgs/String for simplicity; interpret as a mode or
        arbitrary command. Replace with your own controller mapping.
        """
        now_ms = int(time.time() * 1000)

        # Example: use a very minimal ControllerState subset
        # In your real implementation, populate fields from joystick, Twist, etc.
        payload = {
            "source": self.source,
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
            "LjoyX": 0,
            "LjoyY": 0,
            "RjoyX": 0,
            "RjoyY": 0,
            "LT": 0,
            "RT": 0,
            "dX": 0,
            "dY": 0,
            "ts": now_ms,
            "seq": self.seq,
        }

        # Example: map simple command strings into button presses
        cmd = msg.data.strip().upper()
        if cmd == "TELEOP":
            payload["N"] = 1   # request TELEOP
        elif cmd == "AUTO":
            payload["E"] = 1   # request AUTO
        elif cmd == "IDLE":
            payload["W"] = 1   # request IDLE

        # A more advanced version would map actual joystick ranges, etc.

        self.send_packet(payload)
        self.seq += 1


def main():
    rospy.init_node("server_pi_client")
    client = ServerPiClient()
    rospy.loginfo("ServerPiClient node started")
    rospy.spin()
    client.close()


if __name__ == "__main__":
    main()
```

### Using a different packet config on the server

Your ROS node does **not** need to know the byte mapping used for the Arduino; that’s controlled entirely by the server via `ByteConfig` and `ByteFormatter`:

- Create a `ros_byte_config.json` with whatever output format you need for ROS-originated commands.
- Start a second server instance using that config and a different serial device or same device (depending on how you want to multiplex):

  ```bash
  ./server -port 9090 \
           -config ros_byte_config.json \
           -serial-device /dev/ttyACM0 \
           -packet-log ros_packet_errors.jsonl
  ```

As long as your ROS node sends `ControllerState`-compatible JSON, the Go code will:

1. Verify CRC and size.
2. Parse into `ControllerState`.
3. Use the **ROS-specific** byte mapping to format bytes.
4. Apply rover-mode gating.
5. Write to the Arduino via the same `SerialManager` logic.

If you later add a new JSON message type (e.g., `{"type":"ros_cmd", ...}`) you can add a `tryParseRosCmd()` function in Go similar to `tryParseStatusPacket` and branch in `handleClient` before the controller-state path.

***

## 2. Server architecture / function orchestration doc

I’ve generated a markdown file named `server_pi_network_stack.md` that:

- Walks function-by-function through:
  - Protocol structs (`ControllerState`, `StatusPacket`).
  - Byte-formatting types and `ByteFormatter`.
  - Batch logging (`BatchLogger`, `PacketLog`).
  - Serial manager and telemetry hub.
  - Rover state integration (`readRoverState`, `StateSwitchTracker`, etc.).
  - TCP helpers and the full `handleClient` path.
  - `main()` and how everything is wired.
- Explains how these pieces collaborate in the critical loop:
  - TCP receive → framing → CRC → classify → log → gate → format → serial write.
- Shows how to extend the system for:
  - New client types (like this ROS node).
  - New JSON message types.
  - New byte-level output formats via JSON config.
  - Different gating rules (states beyond `TELEOP/AUTO/IDLE`).

You can open or commit that file directly from the attachment. 

If you tell me the exact ROS message type you want to use (e.g., a custom `ControllerState` msg vs. `sensor_msgs/Joy`), I can tighten the node to do real mapping from that msg to the JSON the server expects.
