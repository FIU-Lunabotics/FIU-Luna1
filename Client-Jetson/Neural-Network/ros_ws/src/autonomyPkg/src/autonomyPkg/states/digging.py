import rospy
import smach

class DigState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["dug", "timeout", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("DIG")
        self.context.stop_rover()
        
        if not self.context.autonomy_active:
            return "fault"

        lower_result = self.context.run_command_step(
            "dig_lower_actuator_set_1",
            self.context.dig_lower_duration_s,
            stop_feedback_field="linear_actuator_set_1_lower_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_1=self.context.linear_actuator_set_1_lower_command,
        )
        if lower_result != "done": return lower_result

        scoop_result = self.context.run_command_step(
            "dig_scoop",
            self.context.dig_scoop_duration_s,
            linear_actuator_set_2=self.context.linear_actuator_set_2_dig_command,
            vibration_motor=self.context.vibration_motor_on_command,
        )
        if scoop_result != "done": return scoop_result

        raise_result = self.context.run_command_step(
            "dig_raise_actuator_set_1",
            self.context.dig_raise_duration_s,
            stop_feedback_field="linear_actuator_set_1_upper_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_1=self.context.linear_actuator_set_1_raise_command,
        )
        if raise_result != "done": return raise_result

        return "dug"