#!/usr/bin/env python3

import json

import rospy
import smach
import smach_ros
from nav_msgs.msg import OccupancyGrid
from std_msgs.msg import Bool, String


class MissionContext:
    def __init__(self):
        self.state_topic = rospy.get_param("~state_topic", "/luna/autonomy/state")
        self.command_topic = rospy.get_param(
            "~command_topic", "/luna/rover/command_json"
        )
        self.start_topic = rospy.get_param("~start_topic", "/luna/autonomy/start")
        self.costmap_topic = rospy.get_param("~costmap_topic", "/luna/costmap")
        self.feedback_topic = rospy.get_param(
            "~feedback_topic", "/luna/rover/feedback_json"
        )
        self.bridge_connected_topic = rospy.get_param(
            "~bridge_connected_topic", "/luna/rover_bridge/connected"
        )

        self.auto_start = bool(rospy.get_param("~auto_start", True))
        self.require_costmap = bool(rospy.get_param("~require_costmap", True))
        self.require_bridge = bool(rospy.get_param("~require_bridge", False))
        self.require_feedback = bool(rospy.get_param("~require_feedback", False))
        self.topic_timeout_s = float(rospy.get_param("~topic_timeout_s", 2.0))
        self.loop_rate_hz = float(rospy.get_param("~loop_rate_hz", 2.0))
        self.recovery_delay_s = float(rospy.get_param("~recovery_delay_s", 1.0))

        self.start_requested = False
        self.latest_costmap = None
        self.latest_feedback = None
        self.bridge_connected = False
        self.last_costmap_time = None
        self.last_feedback_time = None

        self.command_seq = 0

        self.state_pub = rospy.Publisher(self.state_topic, String, queue_size=10)
        self.command_pub = rospy.Publisher(self.command_topic, String, queue_size=10)

        rospy.Subscriber(self.start_topic, String, self._start_callback, queue_size=1)
        rospy.Subscriber(
            self.costmap_topic,
            OccupancyGrid,
            self._costmap_callback,
            queue_size=1,
        )
        rospy.Subscriber(
            self.feedback_topic,
            String,
            self._feedback_callback,
            queue_size=1,
        )
        rospy.Subscriber(
            self.bridge_connected_topic,
            Bool,
            self._bridge_callback,
            queue_size=1,
        )

        self.rate = rospy.Rate(max(0.1, self.loop_rate_hz))

    def _start_callback(self, msg):
        command = msg.data.strip().lower()
        if command in ("start", "auto", "run", "true", "1"):
            self.start_requested = True
        elif command in ("stop", "idle", "false", "0"):
            self.start_requested = False

    def _costmap_callback(self, msg):
        self.latest_costmap = msg
        self.last_costmap_time = rospy.Time.now()

    def _feedback_callback(self, msg):
        self.latest_feedback = msg
        self.last_feedback_time = rospy.Time.now()

    def _bridge_callback(self, msg):
        self.bridge_connected = bool(msg.data)

    def publish_state(self, state_name):
        self.state_pub.publish(String(data=state_name))

    def publish_rover_command(
        self,
        *,
        linear_actuator_set_1=0,
        linear_actuator_set_2=0,
        left_side_motors=0,
        right_side_motors=0,
        vibration_motor=0
    ):
        self.command_seq += 1
        payload = {
            "type": "rover_command",
            "source": "luna_autonomy",
            "seq": self.command_seq,
            "ts": int(rospy.Time.now().to_sec() * 1000),
            "linear_actuator_set_1": int(linear_actuator_set_1),
            "linear_actuator_set_2": int(linear_actuator_set_2),
            "left_side_motors": int(left_side_motors),
            "right_side_motors": int(right_side_motors),
            "vibration_motor": int(vibration_motor),
        }
        self.command_pub.publish(String(data=json.dumps(payload, sort_keys=True)))

    def stop_rover(self):
        self.publish_rover_command()

    def start_command_received(self):
        return self.auto_start or self.start_requested

    def has_fault(self):
        return rospy.is_shutdown()

    def systems_ok(self):
        if self.require_costmap and not self._recent(self.last_costmap_time):
            return False
        if self.require_feedback and not self._recent(self.last_feedback_time):
            return False
        if self.require_bridge and not self.bridge_connected:
            return False
        return True

    def missing_systems(self):
        missing = []
        if self.require_costmap and not self._recent(self.last_costmap_time):
            missing.append("costmap")
        if self.require_feedback and not self._recent(self.last_feedback_time):
            missing.append("feedback")
        if self.require_bridge and not self.bridge_connected:
            missing.append("bridge")
        return missing

    def _recent(self, stamp):
        if stamp is None:
            return False
        return (rospy.Time.now() - stamp).to_sec() <= self.topic_timeout_s

    def sleep_once(self):
        self.rate.sleep()


class IdleState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["idle", "start", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("IDLE")
        self.context.stop_rover()

        if self.context.has_fault():
            return "fault"
        if self.context.start_command_received():
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

        if self.context.has_fault():
            return "fault"
        if self.context.systems_ok():
            rospy.loginfo("Autonomy system checks passed.")
            return "ready"

        missing = ", ".join(self.context.missing_systems()) or "unknown"
        rospy.logwarn_throttle(2.0, "Waiting for required autonomy data: %s", missing)
        self.context.sleep_once()
        return "waiting"


class RecoveryState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["recovered", "fatal"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("RECOVERY")
        self.context.stop_rover()

        if rospy.is_shutdown():
            return "fatal"

        rospy.sleep(self.context.recovery_delay_s)
        return "recovered"


def build_state_machine(context):
    sm = smach.StateMachine(outcomes=["SHUTDOWN"])

    with sm:
        smach.StateMachine.add(
            "IDLE",
            IdleState(context),
            transitions={
                "idle": "IDLE",
                "start": "CHECK_SYSTEMS",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "CHECK_SYSTEMS",
            CheckSystemsState(context),
            transitions={
                "ready": "SHUTDOWN",
                "waiting": "CHECK_SYSTEMS",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "RECOVERY",
            RecoveryState(context),
            transitions={
                "recovered": "CHECK_SYSTEMS",
                "fatal": "SHUTDOWN",
            },
        )

    return sm


def main():
    rospy.init_node("luna_autonomy_state_machine")
    context = MissionContext()
    state_machine = build_state_machine(context)

    introspection_server = smach_ros.IntrospectionServer(
        "luna_autonomy_smach",
        state_machine,
        "/LUNA_AUTONOMY",
    )
    introspection_server.start()

    try:
        outcome = state_machine.execute()
        rospy.loginfo("luna_autonomy state machine finished with outcome: %s", outcome)
    finally:
        introspection_server.stop()


if __name__ == "__main__":
    main()
