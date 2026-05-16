import rospy
import smach
from geometry_msgs.msg import PoseStamped, Point, Quaternion

class ExploreState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["target_found", "searching", "fault"], output_keys=['target_pose_out'])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("EXPLORE")
        self.context.stop_rover()
        
        if self.context.has_fault() or not self.context.autonomy_active:
            return "fault"
            
        if not self.context.is_costmap_ready():
            self.context.sleep_once()
            return "searching"

        goal_dict = self.context.find_safe_target()
        
        if goal_dict is None:
            self.context.sleep_once()
            return "searching"

        # Convert finding to move_base PoseStamped
        pose = PoseStamped()
        pose.header.frame_id = goal_dict["frame_id"]
        pose.header.stamp = rospy.Time.now()
        pose.pose.position = Point(x=goal_dict["world_x"], y=goal_dict["world_y"], z=0.0)
        pose.pose.orientation = Quaternion(x=0.0, y=0.0, z=0.0, w=1.0)
        
        # We assign the Berm Goal to userdata here too, so the return trip has it!
        userdata.berm_goal = self.context.geometry.get_berm_pose(frame_id=goal_dict["frame_id"])
        userdata.target_pose_out = pose
        
        rospy.loginfo("Target found. Transitioning to TRAVEL_TO_DIG.")
        return "target_found"