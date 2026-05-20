import rospy
import smach

class DumpState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["dumped", "complete", "timeout", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("DUMP")
        self.context.stop_rover()

        if not self.context.autonomy_active:
            return "fault"

        r1 = self.context.run_command_step(
            "dump_raise",
            self.context.dump_raise_duration_s,
            stop_feedback_field="linear_actuator_set_2_upper_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_2=self.context.linear_actuator_set_2_dump_command,
        )
        if r1 != "done": return r1

        r2 = self.context.run_command_step(
            "dump_release",
            self.context.dump_release_duration_s,
            vibration_motor=self.context.vibration_motor_on_command,
        )
        if r2 != "done": return r2

        r3 = self.context.run_command_step(
            "dump_reset",
            self.context.dump_reset_duration_s,
            stop_feedback_field="linear_actuator_set_2_lower_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_2=self.context.linear_actuator_set_2_reset_command,
        )
        if r3 != "done": return r3

        self.context.mark_cycle_complete()
        return "complete" if self.context.mission_complete() else "dumped"

class CompleteState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["done"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("COMPLETE")
        self.context.stop_rover()
        rospy.sleep(0.5)
        
        # Turn off the service automatically when done
        self.context.autonomy_active = False 
        return "done"