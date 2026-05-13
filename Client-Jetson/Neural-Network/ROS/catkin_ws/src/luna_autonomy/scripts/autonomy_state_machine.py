#!/usr/bin/env python3

import json

import rospy
import smach
import smach_ros
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid
from std_msgs.msg import Bool, String


class MissionContext:
    def __init__(self):
        self.state_topic = rospy.get_param("~state_topic", "/luna/autonomy/state")
        self.command_topic = rospy.get_param(
            "~command_topic", "/luna/rover/command_json"
        )
        self.active_goal_topic = rospy.get_param(
            "~active_goal_topic", "/luna/autonomy/active_goal"
        )
        self.start_topic = rospy.get_param("~start_topic", "/luna/autonomy/start")
        self.costmap_topic = rospy.get_param("~costmap_topic", "/luna/costmap")
        self.berm_center_topic = rospy.get_param(
            "~berm_center_topic", "/luna/berm/center"
        )
        self.feedback_topic = rospy.get_param(
            "~feedback_topic", "/luna/rover/feedback_json"
        )
        self.bridge_connected_topic = rospy.get_param(
            "~bridge_connected_topic", "/luna/rover_bridge/connected"
        )

        self.auto_start = bool(rospy.get_param("~auto_start", True))
        self.require_costmap = bool(rospy.get_param("~require_costmap", True))
        self.require_berm = bool(rospy.get_param("~require_berm", False))
        self.require_bridge = bool(rospy.get_param("~require_bridge", False))
        self.require_feedback = bool(rospy.get_param("~require_feedback", False))
        self.topic_timeout_s = float(rospy.get_param("~topic_timeout_s", 2.0))
        self.loop_rate_hz = float(rospy.get_param("~loop_rate_hz", 2.0))
        self.recovery_delay_s = float(rospy.get_param("~recovery_delay_s", 1.0))
        self.unknown_is_unsafe = bool(rospy.get_param("~unknown_is_unsafe", True))
        self.obstacle_threshold = int(rospy.get_param("~obstacle_threshold", 50))
        self.target_clearance_cells = int(rospy.get_param("~target_clearance_cells", 3))
        self.safety_score_radius_cells = int(
            rospy.get_param("~safety_score_radius_cells", 5)
        )
        self.scan_stride_cells = max(1, int(rospy.get_param("~scan_stride_cells", 1)))
        self.forward_score_weight = float(rospy.get_param("~forward_score_weight", 1.0))
        self.center_score_weight = float(rospy.get_param("~center_score_weight", 0.1))
        self.clearance_score_weight = float(
            rospy.get_param("~clearance_score_weight", 0.2)
        )
        self.movement_speed = int(rospy.get_param("~movement_speed", 120))
        self.movement_duration_s = float(rospy.get_param("~movement_duration_s", 2.0))
        self.travel_to_berm_speed = int(rospy.get_param("~travel_to_berm_speed", 120))
        self.travel_to_berm_duration_s = float(
            rospy.get_param("~travel_to_berm_duration_s", 3.0)
        )
        self.dig_lower_duration_s = float(rospy.get_param("~dig_lower_duration_s", 1.5))
        self.dig_scoop_duration_s = float(rospy.get_param("~dig_scoop_duration_s", 2.0))
        self.dig_raise_duration_s = float(rospy.get_param("~dig_raise_duration_s", 1.5))
        self.dump_raise_duration_s = float(rospy.get_param("~dump_raise_duration_s", 1.5))
        self.dump_release_duration_s = float(
            rospy.get_param("~dump_release_duration_s", 2.0)
        )
        self.dump_reset_duration_s = float(rospy.get_param("~dump_reset_duration_s", 1.5))
        self.linear_actuator_set_1_lower_command = int(
            rospy.get_param("~linear_actuator_set_1_lower_command", -120)
        )
        self.linear_actuator_set_1_raise_command = int(
            rospy.get_param("~linear_actuator_set_1_raise_command", 120)
        )
        self.linear_actuator_set_2_dig_command = int(
            rospy.get_param("~linear_actuator_set_2_dig_command", 120)
        )
        self.linear_actuator_set_2_dump_command = int(
            rospy.get_param("~linear_actuator_set_2_dump_command", 120)
        )
        self.linear_actuator_set_2_reset_command = int(
            rospy.get_param("~linear_actuator_set_2_reset_command", -120)
        )
        self.vibration_motor_on_command = int(
            rospy.get_param("~vibration_motor_on_command", 1)
        )
        self.require_limit_switches = bool(
            rospy.get_param("~require_limit_switches", False)
        )
        self.max_dig_dump_cycles = int(rospy.get_param("~max_dig_dump_cycles", 1))

        self.start_requested = False
        self.latest_costmap = None
        self.latest_feedback = None
        self.latest_feedback_payload = {}
        self.latest_berm_center = None
        self.active_goal = None
        self.bridge_connected = False
        self.last_costmap_time = None
        self.last_feedback_time = None
        self.last_berm_center_time = None
        self.completed_cycles = 0

        self.command_seq = 0

        self.state_pub = rospy.Publisher(self.state_topic, String, queue_size=10)
        self.command_pub = rospy.Publisher(self.command_topic, String, queue_size=10)
        self.active_goal_pub = rospy.Publisher(
            self.active_goal_topic, String, queue_size=10, latch=True
        )

        rospy.Subscriber(self.start_topic, String, self._start_callback, queue_size=1)
        rospy.Subscriber(
            self.costmap_topic,
            OccupancyGrid,
            self._costmap_callback,
            queue_size=1,
        )
        rospy.Subscriber(
            self.berm_center_topic,
            PoseStamped,
            self._berm_center_callback,
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

    def _berm_center_callback(self, msg):
        self.latest_berm_center = msg
        self.last_berm_center_time = rospy.Time.now()

    def _feedback_callback(self, msg):
        self.latest_feedback = msg
        self.latest_feedback_payload = self._decode_feedback_payload(msg.data)
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
        if self.require_berm and not self._recent(self.last_berm_center_time):
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
        if self.require_berm and not self._recent(self.last_berm_center_time):
            missing.append("berm_center")
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

    def _decode_feedback_payload(self, data):
        try:
            payload = json.loads(data)
        except (TypeError, ValueError):
            return {}
        if isinstance(payload, dict):
            if payload.get("encoding") == "json" and isinstance(payload.get("value"), dict):
                return payload["value"]
            return payload
        return {}

    def is_costmap_ready(self):
        return self.latest_costmap is not None and self._recent(self.last_costmap_time)

    def is_cell_safe(self, costmap, x, y):
        if not self._cell_in_bounds(costmap, x, y):
            return False

        value = costmap.data[self._cell_index(costmap, x, y)]
        if value == -1:
            return not self.unknown_is_unsafe
        return value < self.obstacle_threshold

    def has_clearance(self, costmap, x, y, radius_cells):
        radius_sq = radius_cells * radius_cells
        for dy in range(-radius_cells, radius_cells + 1):
            for dx in range(-radius_cells, radius_cells + 1):
                if dx * dx + dy * dy > radius_sq:
                    continue
                if not self.is_cell_safe(costmap, x + dx, y + dy):
                    return False
        return True

    def find_safe_target(self):
        if not self.is_costmap_ready():
            return None

        costmap = self.latest_costmap
        width = costmap.info.width
        height = costmap.info.height
        if width <= 0 or height <= 0 or not costmap.data:
            return None

        best_cell = None
        best_score = None
        for y in range(0, height, self.scan_stride_cells):
            for x in range(0, width, self.scan_stride_cells):
                if not self.has_clearance(costmap, x, y, self.target_clearance_cells):
                    continue

                score = self.score_cell(costmap, x, y)
                if best_score is None or score > best_score:
                    best_score = score
                    best_cell = (x, y)

        if best_cell is None:
            return None

        world_x, world_y = self.cell_to_world(costmap, best_cell[0], best_cell[1])
        return {
            "grid_x": best_cell[0],
            "grid_y": best_cell[1],
            "world_x": world_x,
            "world_y": world_y,
            "score": best_score,
            "frame_id": costmap.header.frame_id,
        }

    def score_cell(self, costmap, x, y):
        width = costmap.info.width
        forward_score = float(y)
        center_x = (width - 1) / 2.0
        center_score = -abs(float(x) - center_x)
        clearance_score = self.clearance_score(costmap, x, y)
        return (
            self.forward_score_weight * forward_score
            + self.center_score_weight * center_score
            + self.clearance_score_weight * clearance_score
        )

    def clearance_score(self, costmap, x, y):
        safe_cells = 0
        radius = self.safety_score_radius_cells
        radius_sq = radius * radius
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy > radius_sq:
                    continue
                if self.is_cell_safe(costmap, x + dx, y + dy):
                    safe_cells += 1
        return safe_cells

    def cell_to_world(self, costmap, x, y):
        resolution = costmap.info.resolution
        origin = costmap.info.origin.position
        world_x = origin.x + (float(x) + 0.5) * resolution
        world_y = origin.y + (float(y) + 0.5) * resolution
        return world_x, world_y

    def set_active_goal(self, goal):
        self.active_goal = goal
        self.active_goal_pub.publish(String(data=json.dumps(goal, sort_keys=True)))

    def set_berm_goal(self):
        if self.latest_berm_center is None:
            return None

        pose = self.latest_berm_center.pose.position
        goal = {
            "goal_type": "berm",
            "world_x": pose.x,
            "world_y": pose.y,
            "frame_id": self.latest_berm_center.header.frame_id,
            "score": 0.0,
        }
        self.set_active_goal(goal)
        return goal

    def active_goal_is_safe(self):
        if self.active_goal is None:
            return False
        if not self.is_costmap_ready():
            return not self.require_costmap
        if "grid_x" not in self.active_goal or "grid_y" not in self.active_goal:
            return True
        return self.has_clearance(
            self.latest_costmap,
            int(self.active_goal["grid_x"]),
            int(self.active_goal["grid_y"]),
            self.target_clearance_cells,
        )

    def feedback_active(self, field_name):
        value = self.latest_feedback_payload.get(field_name, 0)
        try:
            return int(value) != 0
        except (TypeError, ValueError):
            return False

    def run_command_for(self, duration_s, check_active_goal=False, **command):
        end_time = rospy.Time.now() + rospy.Duration(max(0.0, duration_s))
        while not rospy.is_shutdown() and rospy.Time.now() < end_time:
            if check_active_goal and not self.active_goal_is_safe():
                self.stop_rover()
                return "blocked"
            self.publish_rover_command(**command)
            self.sleep_once()

        self.stop_rover()
        if rospy.is_shutdown():
            return "fault"
        return "done"

    def run_command_step(
        self,
        label,
        duration_s,
        stop_feedback_field=None,
        require_stop_feedback=False,
        **command
    ):
        rospy.loginfo("Starting autonomy command step: %s", label)
        end_time = rospy.Time.now() + rospy.Duration(max(0.0, duration_s))
        while not rospy.is_shutdown() and rospy.Time.now() < end_time:
            if stop_feedback_field and self.feedback_active(stop_feedback_field):
                self.stop_rover()
                rospy.loginfo("Command step %s stopped by %s", label, stop_feedback_field)
                return "done"
            self.publish_rover_command(**command)
            self.sleep_once()

        self.stop_rover()
        if rospy.is_shutdown():
            return "fault"
        if require_stop_feedback and stop_feedback_field:
            rospy.logwarn("Command step %s timed out before %s", label, stop_feedback_field)
            return "timeout"
        return "done"

    def mark_cycle_complete(self):
        self.completed_cycles += 1

    def mission_complete(self):
        return self.completed_cycles >= self.max_dig_dump_cycles

    def _cell_in_bounds(self, costmap, x, y):
        return 0 <= x < costmap.info.width and 0 <= y < costmap.info.height

    def _cell_index(self, costmap, x, y):
        return y * costmap.info.width + x


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


class ExploreState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["target_found", "searching", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("EXPLORE")
        self.context.stop_rover()

        if self.context.has_fault():
            return "fault"
        if not self.context.is_costmap_ready():
            rospy.logwarn_throttle(2.0, "EXPLORE waiting for a recent costmap.")
            self.context.sleep_once()
            return "searching"

        goal = self.context.find_safe_target()
        if goal is None:
            rospy.logwarn_throttle(2.0, "EXPLORE did not find a safe costmap target.")
            self.context.sleep_once()
            return "searching"

        self.context.set_active_goal(goal)
        rospy.loginfo(
            "EXPLORE selected goal grid=(%d,%d) world=(%.2f,%.2f) score=%.2f",
            goal["grid_x"],
            goal["grid_y"],
            goal["world_x"],
            goal["world_y"],
            goal["score"],
        )
        return "target_found"


class MovementState(smach.State):
    def __init__(self, context):
        smach.State.__init__(
            self,
            outcomes=["arrived", "blocked", "no_goal", "fault"],
        )
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("MOVEMENT")

        if self.context.has_fault():
            return "fault"
        if self.context.active_goal is None:
            rospy.logwarn("MOVEMENT has no active goal.")
            self.context.stop_rover()
            return "no_goal"
        if not self.context.active_goal_is_safe():
            rospy.logwarn("MOVEMENT active goal is no longer safe.")
            self.context.stop_rover()
            return "blocked"

        result = self.context.run_command_for(
            self.context.movement_duration_s,
            check_active_goal=True,
            left_side_motors=self.context.movement_speed,
            right_side_motors=self.context.movement_speed,
        )
        if result == "blocked":
            return "blocked"
        if result == "fault":
            return "fault"
        return "arrived"


class DigState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["dug", "timeout", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("DIG")
        self.context.stop_rover()

        if self.context.has_fault():
            return "fault"

        lower_result = self.context.run_command_step(
            "dig_lower_actuator_set_1",
            self.context.dig_lower_duration_s,
            stop_feedback_field="linear_actuator_set_1_lower_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_1=self.context.linear_actuator_set_1_lower_command,
        )
        if lower_result != "done":
            return lower_result

        scoop_result = self.context.run_command_step(
            "dig_scoop",
            self.context.dig_scoop_duration_s,
            linear_actuator_set_2=self.context.linear_actuator_set_2_dig_command,
            vibration_motor=self.context.vibration_motor_on_command,
        )
        if scoop_result != "done":
            return scoop_result

        raise_result = self.context.run_command_step(
            "dig_raise_actuator_set_1",
            self.context.dig_raise_duration_s,
            stop_feedback_field="linear_actuator_set_1_upper_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_1=self.context.linear_actuator_set_1_raise_command,
        )
        if raise_result != "done":
            return raise_result

        return "dug"


class TravelToBermState(smach.State):
    def __init__(self, context):
        smach.State.__init__(
            self,
            outcomes=["arrived", "blocked", "no_goal", "fault"],
        )
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("TRAVEL_TO_BERM")

        if self.context.has_fault():
            return "fault"

        goal = self.context.set_berm_goal()
        if goal is None and self.context.require_berm:
            rospy.logwarn("TRAVEL_TO_BERM has no berm center goal.")
            self.context.stop_rover()
            return "no_goal"

        result = self.context.run_command_for(
            self.context.travel_to_berm_duration_s,
            check_active_goal=False,
            left_side_motors=self.context.travel_to_berm_speed,
            right_side_motors=self.context.travel_to_berm_speed,
        )
        if result == "blocked":
            return "blocked"
        if result == "fault":
            return "fault"
        return "arrived"


class DumpState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["dumped", "complete", "timeout", "fault"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("DUMP")
        self.context.stop_rover()

        if self.context.has_fault():
            return "fault"

        raise_result = self.context.run_command_step(
            "dump_raise",
            self.context.dump_raise_duration_s,
            stop_feedback_field="linear_actuator_set_2_upper_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_2=self.context.linear_actuator_set_2_dump_command,
        )
        if raise_result != "done":
            return raise_result

        release_result = self.context.run_command_step(
            "dump_release",
            self.context.dump_release_duration_s,
            vibration_motor=self.context.vibration_motor_on_command,
        )
        if release_result != "done":
            return release_result

        reset_result = self.context.run_command_step(
            "dump_reset",
            self.context.dump_reset_duration_s,
            stop_feedback_field="linear_actuator_set_2_lower_limit",
            require_stop_feedback=self.context.require_limit_switches,
            linear_actuator_set_2=self.context.linear_actuator_set_2_reset_command,
        )
        if reset_result != "done":
            return reset_result

        self.context.mark_cycle_complete()
        if self.context.mission_complete():
            return "complete"
        return "dumped"


class CompleteState(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=["done"])
        self.context = context

    def execute(self, userdata):
        self.context.publish_state("COMPLETE")
        self.context.stop_rover()
        rospy.sleep(0.5)
        return "done"


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
                "ready": "EXPLORE",
                "waiting": "CHECK_SYSTEMS",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "EXPLORE",
            ExploreState(context),
            transitions={
                "target_found": "MOVEMENT",
                "searching": "EXPLORE",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "MOVEMENT",
            MovementState(context),
            transitions={
                "arrived": "DIG",
                "blocked": "EXPLORE",
                "no_goal": "EXPLORE",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "DIG",
            DigState(context),
            transitions={
                "dug": "TRAVEL_TO_BERM",
                "timeout": "RECOVERY",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "TRAVEL_TO_BERM",
            TravelToBermState(context),
            transitions={
                "arrived": "DUMP",
                "blocked": "EXPLORE",
                "no_goal": "RECOVERY",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "DUMP",
            DumpState(context),
            transitions={
                "dumped": "EXPLORE",
                "complete": "COMPLETE",
                "timeout": "RECOVERY",
                "fault": "RECOVERY",
            },
        )
        smach.StateMachine.add(
            "COMPLETE",
            CompleteState(context),
            transitions={
                "done": "SHUTDOWN",
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
