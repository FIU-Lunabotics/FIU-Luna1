import rospy
import smach

class RecoveryState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["recovered", "fatal"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("RECOVERY")
        self.context.stop_rover()
        
        if rospy.is_shutdown():
            return "fatal"
            
        # Give the rover a moment to breathe and reset network connections
        rospy.sleep(self.context.recovery_delay_s)
        
        return "recovered"