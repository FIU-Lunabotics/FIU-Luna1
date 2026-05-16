#!/usr/bin/env python3

import threading
import rospy
import numpy as np
from geometry_msgs.msg import Pose
from nav_msgs.msg import MapMetaData, OccupancyGrid
from sensor_msgs.msg import PointCloud2
import sensor_msgs.point_cloud2 as pc2

class LunaCostmapNode:
    def __init__(self):
        rospy.init_node("luna_costmap")

        # Parameters
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
        self.occupied_cost = int(rospy.get_param("~occupied_cost", 100))
        self.free_cost = int(rospy.get_param("~free_cost", 0))
        self.costmap_frame = rospy.get_param("~costmap_frame", "map")

        # Calculate grid dimensions in cells
        self.width_cells = int(self.width_m / self.resolution)
        self.height_cells = int(self.height_m / self.resolution)

        # Threading and State
        self.lock = threading.Lock()
        self.latest_cloud = None

        # Publishers / Subscribers / Timers
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

        rospy.loginfo(f"LunaCostmapNode initialized. Listening on {self.point_topic}")

    def _point_cloud_callback(self, msg):
        # Safely store the incoming cloud
        with self.lock:
            self.latest_cloud = msg

    def _publish_costmap(self, _event):
        # Safely grab the latest cloud
        with self.lock:
            cloud = self.latest_cloud

        if cloud is None:
            rospy.logwarn_throttle(5.0, "Waiting for point cloud data...")
            return

        # Build and publish
        occupancy_grid = self._build_occupancy_grid(cloud)
        self.costmap_pub.publish(occupancy_grid)

    def _build_occupancy_grid(self, cloud):
        # 1. Extract points from the PointCloud2 message into a Python list, 
        # then immediately convert to a high-performance NumPy array.
        gen = pc2.read_points(cloud, field_names=("x", "y", "z"), skip_nans=True)
        points = np.array(list(gen))

        # Create a blank 1D array filled with free_cost (0)
        # Size = total number of cells (width * height)
        grid_data = np.full(self.width_cells * self.height_cells, self.free_cost, dtype=np.int8)

        if len(points) > 0:
            # --- NUMPY VECTORIZED MATH ---
            
            # 2. Z-Axis Filter (Masking)
            # points[:, 2] means "give me all rows, but only the 3rd column (Z)"
            z_mask = (points[:, 2] >= self.min_obstacle_z) & (points[:, 2] <= self.max_obstacle_z)
            valid_points = points[z_mask]

            # 3. Convert physical coordinates (meters) to grid indices (cells)
            # We subtract the origin to shift the map, then divide by resolution
            cols = ((valid_points[:, 0] - self.origin_x) / self.resolution).astype(np.int32)
            rows = ((valid_points[:, 1] - self.origin_y) / self.resolution).astype(np.int32)

            # 4. Bounds Checking
            # Ensure the calculated cells actually fit inside our map dimensions
            bounds_mask = (cols >= 0) & (cols < self.width_cells) & (rows >= 0) & (rows < self.height_cells)
            valid_cols = cols[bounds_mask]
            valid_rows = rows[bounds_mask]

            # 5. Flatten the 2D indices into 1D indices
            # Formula: index = (row * width) + column
            flat_indices = (valid_rows * self.width_cells) + valid_cols

            # 6. Stamp the obstacles into the grid!
            # We assign the occupied_cost to all calculated indices simultaneously.
            grid_data[flat_indices] = self.occupied_cost

        # 7. Construct the ROS Message
        occupancy = OccupancyGrid()
        
        # FIX: Use the cloud's original timestamp, not Time.now(), to prevent TF drift.
        occupancy.header.stamp = cloud.header.stamp 
        occupancy.header.frame_id = self.costmap_frame or cloud.header.frame_id
        
        occupancy.info = self._build_map_metadata()
        
        # ROS requires a list or tuple for the data field, so we convert the numpy array back
        occupancy.data = grid_data.tolist() 
        
        return occupancy

    def _build_map_metadata(self):
        metadata = MapMetaData()
        metadata.map_load_time = rospy.Time.now()
        metadata.resolution = self.resolution
        metadata.width = self.width_cells
        metadata.height = self.height_cells

        origin = Pose()
        origin.position.x = self.origin_x
        origin.position.y = self.origin_y
        origin.position.z = 0.0
        origin.orientation.w = 1.0
        metadata.origin = origin
        
        return metadata

if __name__ == "__main__":
    try:
        LunaCostmapNode()
        rospy.spin()
    except rospy.ROSInterruptException:
        pass