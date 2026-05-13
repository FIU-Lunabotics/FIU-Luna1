#!/usr/bin/env python3

from pathlib import Path
import sys
import threading

import rospy
from geometry_msgs.msg import Pose
from nav_msgs.msg import MapMetaData, OccupancyGrid
from sensor_msgs import point_cloud2
from sensor_msgs.msg import PointCloud2


def _find_navigation_dir():
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if parent.name == "Neural-Network":
            candidate = parent / "Gazebo" / "controllers" / "navigation"
            if candidate.exists():
                return candidate
    raise ImportError("Unable to locate Gazebo navigation directory for costmap_handler.py")


NAVIGATION_DIR = _find_navigation_dir()
if str(NAVIGATION_DIR) not in sys.path:
    sys.path.insert(0, str(NAVIGATION_DIR))

from costmap_handler import CostmapBuilder


class LunaCostmapNode:
    def __init__(self):
        self.point_topic = rospy.get_param("~point_topic", "/lidar/points")
        self.costmap_topic = rospy.get_param("~costmap_topic", "/luna/costmap")
        self.publish_rate = float(rospy.get_param("~publish_rate", 5.0))
        self.resolution = float(rospy.get_param("~resolution", 0.10))
        self.width_m = float(rospy.get_param("~width_m", 20.0))
        self.height_m = float(rospy.get_param("~height_m", 20.0))
        self.origin_x = float(rospy.get_param("~origin_x", -10.0))
        self.origin_y = float(rospy.get_param("~origin_y", -10.0))
        self.min_obstacle_z = float(rospy.get_param("~min_obstacle_z", -0.20))
        self.max_obstacle_z = float(rospy.get_param("~max_obstacle_z", 1.50))
        self.inflation_radius_m = float(rospy.get_param("~inflation_radius_m", 0.20))
        self.occupied_cost = int(rospy.get_param("~occupied_cost", 100))
        self.free_cost = int(rospy.get_param("~free_cost", 0))
        self.costmap_frame = rospy.get_param("~costmap_frame", "")

        self.builder = CostmapBuilder(
            resolution=self.resolution,
            width_m=self.width_m,
            height_m=self.height_m,
            origin_x=self.origin_x,
            origin_y=self.origin_y,
            min_obstacle_z=self.min_obstacle_z,
            max_obstacle_z=self.max_obstacle_z,
            inflation_radius_m=self.inflation_radius_m,
            occupied_cost=self.occupied_cost,
            free_cost=self.free_cost,
        )

        self.lock = threading.Lock()
        self.latest_cloud = None

        self.costmap_pub = rospy.Publisher(
            self.costmap_topic, OccupancyGrid, queue_size=1, latch=True
        )
        self.cloud_sub = rospy.Subscriber(
            self.point_topic, PointCloud2, self._point_cloud_callback, queue_size=1
        )
        self.publish_timer = rospy.Timer(
            rospy.Duration(1.0 / max(0.1, self.publish_rate)),
            self._publish_costmap,
        )

        rospy.loginfo(
            "luna_costmap listening on %s and publishing %s",
            self.point_topic,
            self.costmap_topic,
        )

    def _point_cloud_callback(self, msg):
        with self.lock:
            self.latest_cloud = msg

    def _publish_costmap(self, _event):
        with self.lock:
            cloud = self.latest_cloud

        if cloud is None:
            rospy.logwarn_throttle(5.0, "luna_costmap has not received a point cloud yet.")
            return

        self.costmap_pub.publish(self._build_occupancy_grid(cloud))

    def _build_occupancy_grid(self, cloud):
        points = point_cloud2.read_points(
            cloud,
            field_names=("x", "y", "z"),
            skip_nans=True,
        )
        grid = self.builder.build_grid(points)

        occupancy = OccupancyGrid()
        occupancy.header.stamp = rospy.Time.now()
        occupancy.header.frame_id = self.costmap_frame or cloud.header.frame_id
        occupancy.info = self._build_map_metadata()
        occupancy.data = grid
        return occupancy

    def _build_map_metadata(self):
        metadata = MapMetaData()
        metadata.map_load_time = rospy.Time.now()
        metadata.resolution = self.builder.resolution
        metadata.width = self.builder.width_cells
        metadata.height = self.builder.height_cells

        origin = Pose()
        origin.position.x = self.builder.origin_x
        origin.position.y = self.builder.origin_y
        origin.orientation.w = 1.0
        metadata.origin = origin
        return metadata


def main():
    rospy.init_node("luna_costmap")
    LunaCostmapNode()
    rospy.spin()


if __name__ == "__main__":
    main()
