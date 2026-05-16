import rospy
import smach

class IdleState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["idle", "start", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("IDLE")
        self.context.stop_rover()
        
        if self.context.has_fault():
            return "fault"
        if self.context.autonomy_active:
            return "start"
            
        self.context.sleep_once()
        return "idle"

class CheckSystemsState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["ready", "waiting", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("CHECK_SYSTEMS")
        self.context.stop_rover()
        
        if self.context.has_fault() or not self.context.autonomy_active:
            return "fault" 
            
        if self.context.systems_ok():
            return "ready"
            
        rospy.logwarn_throttle(2.0, "Waiting for required autonomy data: %s", ", ".join(self.context.missing_systems()))
        self.context.sleep_once()
        return "waiting"