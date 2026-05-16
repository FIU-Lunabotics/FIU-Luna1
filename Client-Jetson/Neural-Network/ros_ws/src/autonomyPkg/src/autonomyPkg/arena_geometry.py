import rospy
from geometry_msgs.msg import PoseStamped, Point, Quaternion

class ArenaGeometry:
    def __init__(self, starting_corner="A"):
        self.starting_corner = starting_corner.upper()
        
        # A priori dimensions (in meters) based on standard Lunabotics rules
        # You can adjust these in your YAML file later if needed
        self.berm_center_x = rospy.get_param("~berm_x", 1.5)
        self.berm_center_y = rospy.get_param("~berm_y", 1.0)
        
    def get_berm_pose(self, frame_id="map"):
        """Returns a PoseStamped for move_base to navigate back to the Berm"""
        pose = PoseStamped()
        pose.header.frame_id = frame_id
        pose.header.stamp = rospy.Time.now()
        
        # Calculate real coordinates based on corner
        # (Assuming Corner A is 0,0. You can expand logic for B, C, D here)
        pose.pose.position = Point(x=self.berm_center_x, y=self.berm_center_y, z=0.0)
        pose.pose.orientation = Quaternion(x=0.0, y=0.0, z=0.0, w=1.0) # Facing forward
        
        return pose