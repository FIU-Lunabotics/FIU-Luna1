#!/usr/bin/env python3

import json
import socket
import struct
import threading
import time
import zlib

import rospy
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool, String


def unix_millis():
    return int(time.time() * 1000)


def append_crc(payload):
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack(">I", crc)


def build_wire_packet(payload_obj, max_packet_size=8192):
    payload = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > max_packet_size:
        raise ValueError("JSON payload is too large: %d bytes" % len(payload))

    packet = append_crc(payload)
    return struct.pack(">I", len(packet)) + packet


def parse_topic_entries(entries):
    parsed = []
    for entry in entries or []:
        if isinstance(entry, str):
            key = entry.strip("/").replace("/", "_") or "topic"
            parsed.append({"key": key, "name": entry})
            continue

        key = str(entry.get("key", "")).strip()
        name = str(entry.get("name", "")).strip()
        if not key or not name:
            raise ValueError("topic entries must include key and name")
        parsed.append({"key": key, "name": name})
    return parsed


def string_payload(msg):
    try:
        value = json.loads(msg.data)
        return {"encoding": "json", "value": value}
    except (TypeError, ValueError):
        return {"encoding": "text", "value": msg.data}


def twist_payload(msg):
    return {
        "linear": {
            "x": msg.linear.x,
            "y": msg.linear.y,
            "z": msg.linear.z,
        },
        "angular": {
            "x": msg.angular.x,
            "y": msg.angular.y,
            "z": msg.angular.z,
        },
    }


class RosNetworkBridgeNode:
    def __init__(self):
        self.server_host = rospy.get_param("~server_host", rospy.get_param("server_host", "127.0.0.1"))
        self.server_port = int(rospy.get_param("~server_port", rospy.get_param("server_port", 8080)))
        self.source = rospy.get_param("~source", rospy.get_param("source", "ros-bridge"))
        self.publish_rate = float(rospy.get_param("~publish_rate", rospy.get_param("publish_rate", 2.0)))
        self.reconnect_delay_s = float(
            rospy.get_param("~reconnect_delay_s", rospy.get_param("reconnect_delay_s", 3.0))
        )
        self.max_packet_size = int(
            rospy.get_param("~max_packet_size", rospy.get_param("max_packet_size", 8192))
        )
        self.send_empty = bool(rospy.get_param("~send_empty", rospy.get_param("send_empty", True)))
        self.packet_type = rospy.get_param("~packet_type", rospy.get_param("packet_type", "status"))
        self.message = rospy.get_param("~message", rospy.get_param("message", "ros_bridge"))
        self.string_topics = parse_topic_entries(
            rospy.get_param("~string_topics", rospy.get_param("string_topics", []))
        )
        self.twist_topics = parse_topic_entries(
            rospy.get_param("~twist_topics", rospy.get_param("twist_topics", []))
        )

        self.latest_by_key = {}
        self.lock = threading.Lock()
        self.seq = 0
        self.sock = None

        self.connected_pub = rospy.Publisher(
            "/luna/network_bridge/connected", Bool, queue_size=1, latch=True
        )

        for topic in self.string_topics:
            rospy.Subscriber(
                topic["name"],
                String,
                self._string_callback,
                callback_args=topic,
                queue_size=1,
            )
        for topic in self.twist_topics:
            rospy.Subscriber(
                topic["name"],
                Twist,
                self._twist_callback,
                callback_args=topic,
                queue_size=1,
            )

        rospy.loginfo(
            "ros_network_bridge configured for %s:%d with %d string topics and %d twist topics",
            self.server_host,
            self.server_port,
            len(self.string_topics),
            len(self.twist_topics),
        )

    def _string_callback(self, msg, topic):
        self._store_topic(
            topic,
            "std_msgs/String",
            string_payload(msg),
        )

    def _twist_callback(self, msg, topic):
        self._store_topic(
            topic,
            "geometry_msgs/Twist",
            twist_payload(msg),
        )

    def _store_topic(self, topic, msg_type, data):
        with self.lock:
            self.latest_by_key[topic["key"]] = {
                "topic": topic["name"],
                "msg_type": msg_type,
                "stamp_ms": unix_millis(),
                "data": data,
            }

    def _build_payload(self):
        with self.lock:
            topics = dict(self.latest_by_key)

        self.seq += 1
        return {
            "type": self.packet_type,
            "source": self.source,
            "message": self.message,
            "ts": unix_millis(),
            "seq": self.seq,
            "bridge_type": "ros_topics",
            "topics": topics,
        }

    def _connect(self):
        while not rospy.is_shutdown():
            try:
                rospy.loginfo(
                    "ros_network_bridge connecting to %s:%d",
                    self.server_host,
                    self.server_port,
                )
                sock = socket.create_connection(
                    (self.server_host, self.server_port),
                    timeout=3.0,
                )
                sock.settimeout(3.0)
                self.sock = sock
                self.connected_pub.publish(Bool(data=True))
                rospy.loginfo("ros_network_bridge connected")
                return
            except OSError as exc:
                self.connected_pub.publish(Bool(data=False))
                rospy.logwarn("ros_network_bridge connect failed: %s", exc)
                rospy.sleep(self.reconnect_delay_s)

    def _close_socket(self):
        self.connected_pub.publish(Bool(data=False))
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def run(self):
        rate = rospy.Rate(max(0.1, self.publish_rate))
        while not rospy.is_shutdown():
            if self.sock is None:
                self._connect()
                continue

            try:
                if self.send_empty or self.latest_by_key:
                    wire_packet = build_wire_packet(
                        self._build_payload(),
                        max_packet_size=self.max_packet_size,
                    )
                    self.sock.sendall(wire_packet)
                rate.sleep()
            except (OSError, ValueError) as exc:
                rospy.logerr("ros_network_bridge send failed: %s", exc)
                self._close_socket()
                rospy.sleep(self.reconnect_delay_s)


def main():
    rospy.init_node("ros_network_bridge")
    node = RosNetworkBridgeNode()
    node.run()


if __name__ == "__main__":
    main()
