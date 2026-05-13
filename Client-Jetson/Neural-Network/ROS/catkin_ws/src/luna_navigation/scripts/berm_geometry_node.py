#!/usr/bin/env python3

import json
import math
from pathlib import Path
import sys

import rospy
from geometry_msgs.msg import Point32, PolygonStamped, PoseStamped, Quaternion
from std_msgs.msg import String


def _find_navigation_dir():
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if parent.name == "Neural-Network":
            candidate = parent / "Gazebo" / "controllers" / "navigation"
            if candidate.exists():
                return candidate
    raise ImportError("Unable to locate Gazebo navigation directory for berm_geometry.py")


NAVIGATION_DIR = _find_navigation_dir()
if str(NAVIGATION_DIR) not in sys.path:
    sys.path.insert(0, str(NAVIGATION_DIR))

from berm_geometry import BermGeometryConfig, compute_berm_region


def quaternion_from_yaw(yaw_rad):
    half_yaw = yaw_rad / 2.0
    return Quaternion(
        x=0.0,
        y=0.0,
        z=math.sin(half_yaw),
        w=math.cos(half_yaw),
    )


class BermGeometryNode:
    def __init__(self):
        self.frame_id = rospy.get_param("~frame_id", rospy.get_param("frame_id", "map"))
        self.center_topic = rospy.get_param(
            "~center_topic", rospy.get_param("center_topic", "/luna/berm/center")
        )
        self.region_topic = rospy.get_param(
            "~region_topic", rospy.get_param("region_topic", "/luna/berm/region")
        )
        self.metadata_topic = rospy.get_param(
            "~metadata_topic", rospy.get_param("metadata_topic", "/luna/berm/metadata")
        )
        self.publish_rate = float(
            rospy.get_param("~publish_rate", rospy.get_param("publish_rate", 1.0))
        )
        self.config = self._load_config()
        self.region = compute_berm_region(self.config)

        self.center_pub = rospy.Publisher(
            self.center_topic, PoseStamped, queue_size=1, latch=True
        )
        self.region_pub = rospy.Publisher(
            self.region_topic, PolygonStamped, queue_size=1, latch=True
        )
        self.metadata_pub = rospy.Publisher(
            self.metadata_topic, String, queue_size=1, latch=True
        )

        if not self.region.inside_arena:
            rospy.logwarn(
                "Configured berm region extends outside the arena; check berm geometry YAML."
            )

        self.publish_timer = rospy.Timer(
            rospy.Duration(1.0 / max(0.1, self.publish_rate)),
            self._publish,
        )
        rospy.loginfo(
            "berm_geometry publishing center=%s region=%s frame=%s",
            self.center_topic,
            self.region_topic,
            self.frame_id,
        )
        self._publish(None)

    def _load_config(self):
        return BermGeometryConfig(
            arena_width_m=float(rospy.get_param("~arena_width_m", rospy.get_param("arena_width_m"))),
            arena_height_m=float(
                rospy.get_param("~arena_height_m", rospy.get_param("arena_height_m"))
            ),
            start_corner=rospy.get_param(
                "~start_corner", rospy.get_param("start_corner", "southwest")
            ),
            start_heading=rospy.get_param(
                "~start_heading", rospy.get_param("start_heading", "north")
            ),
            berm_center_forward_m=float(
                rospy.get_param(
                    "~berm_center_forward_m",
                    rospy.get_param("berm_center_forward_m"),
                )
            ),
            berm_center_left_m=float(
                rospy.get_param(
                    "~berm_center_left_m",
                    rospy.get_param("berm_center_left_m", 0.0),
                )
            ),
            berm_width_m=float(
                rospy.get_param("~berm_width_m", rospy.get_param("berm_width_m"))
            ),
            berm_length_m=float(
                rospy.get_param("~berm_length_m", rospy.get_param("berm_length_m"))
            ),
            berm_yaw_deg=float(
                rospy.get_param("~berm_yaw_deg", rospy.get_param("berm_yaw_deg", 0.0))
            ),
        )

    def _publish(self, _event):
        stamp = rospy.Time.now()
        self.center_pub.publish(self._build_center_msg(stamp))
        self.region_pub.publish(self._build_region_msg(stamp))
        self.metadata_pub.publish(String(data=json.dumps(self._build_metadata(), sort_keys=True)))

    def _build_center_msg(self, stamp):
        msg = PoseStamped()
        msg.header.stamp = stamp
        msg.header.frame_id = self.frame_id
        msg.pose.position.x = self.region.center_x
        msg.pose.position.y = self.region.center_y
        msg.pose.orientation = quaternion_from_yaw(self.region.yaw_rad)
        return msg

    def _build_region_msg(self, stamp):
        msg = PolygonStamped()
        msg.header.stamp = stamp
        msg.header.frame_id = self.frame_id
        msg.polygon.points = [
            Point32(x=float(x), y=float(y), z=0.0) for x, y in self.region.corners
        ]
        return msg

    def _build_metadata(self):
        return {
            "arena_width_m": self.config.arena_width_m,
            "arena_height_m": self.config.arena_height_m,
            "start_corner": self.config.start_corner,
            "start_heading": self.config.start_heading,
            "start_x": self.region.start_x,
            "start_y": self.region.start_y,
            "start_yaw_rad": self.region.start_yaw_rad,
            "berm_center_x": self.region.center_x,
            "berm_center_y": self.region.center_y,
            "berm_yaw_rad": self.region.yaw_rad,
            "berm_width_m": self.region.width_m,
            "berm_length_m": self.region.length_m,
            "inside_arena": self.region.inside_arena,
        }


def main():
    rospy.init_node("berm_geometry")
    BermGeometryNode()
    rospy.spin()


if __name__ == "__main__":
    main()
