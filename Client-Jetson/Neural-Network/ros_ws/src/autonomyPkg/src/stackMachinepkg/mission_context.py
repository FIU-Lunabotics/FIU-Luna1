import json
import rospy
from std_msgs.msg import String, Bool
from nav_msgs.msg import OccupancyGrid
from luna_autonomy.srv import SetAutonomy, SetAutonomyResponse
from luna_autonomy.arena_geometry import ArenaGeometry

class MissionContext:
    def __init__(self):
        # Topics
        self.command_topic = rospy.get_param("~command_topic", "/luna/rover/command_json")
        self.state_topic = rospy.get_param("~state_topic", "/luna/autonomy/state")
        self.costmap_topic = rospy.get_param("~costmap_topic", "/luna/costmap")
        self.feedback_topic = rospy.get_param("~feedback_topic", "/luna/rover/feedback_json")
        self.bridge_connected_topic = rospy.get_param("~bridge_connected_topic", "/luna/rover_bridge/connected")
        
        # Timing and Parameters
        self.loop_rate_hz = float(rospy.get_param("~loop_rate_hz", 10.0))
        self.topic_timeout_s = float(rospy.get_param("~topic_timeout_s", 2.0))
        self.recovery_delay_s = float(rospy.get_param("~recovery_delay_s", 1.0))
        self.unknown_is_unsafe = bool(rospy.get_param("~unknown_is_unsafe", True))
        self.obstacle_threshold = int(rospy.get_param("~obstacle_threshold", 50))
        self.target_clearance_cells = int(rospy.get_param("~target_clearance_cells", 3))
        self.safety_score_radius_cells = int(rospy.get_param("~safety_score_radius_cells", 5))
        self.scan_stride_cells = max(1, int(rospy.get_param("~scan_stride_cells", 1)))
        self.forward_score_weight = float(rospy.get_param("~forward_score_weight", 1.0))
        self.center_score_weight = float(rospy.get_param("~center_score_weight", 0.1))
        self.clearance_score_weight = float(rospy.get_param("~clearance_score_weight", 0.2))
        
        # Actuator Durations
        self.dig_lower_duration_s = float(rospy.get_param("~dig_lower_duration_s", 1.5))
        self.dig_scoop_duration_s = float(rospy.get_param("~dig_scoop_duration_s", 2.0))
        self.dig_raise_duration_s = float(rospy.get_param("~dig_raise_duration_s", 1.5))
        self.dump_raise_duration_s = float(rospy.get_param("~dump_raise_duration_s", 1.5))
        self.dump_release_duration_s = float(rospy.get_param("~dump_release_duration_s", 2.0))
        self.dump_reset_duration_s = float(rospy.get_param("~dump_reset_duration_s", 1.5))
        
        # Actuator Commands
        self.linear_actuator_set_1_lower_command = int(rospy.get_param("~linear_actuator_set_1_lower_command", -120))
        self.linear_actuator_set_1_raise_command = int(rospy.get_param("~linear_actuator_set_1_raise_command", 120))
        self.linear_actuator_set_2_dig_command = int(rospy.get_param("~linear_actuator_set_2_dig_command", 120))
        self.linear_actuator_set_2_dump_command = int(rospy.get_param("~linear_actuator_set_2_dump_command", 120))
        self.linear_actuator_set_2_reset_command = int(rospy.get_param("~linear_actuator_set_2_reset_command", -120))
        self.vibration_motor_on_command = int(rospy.get_param("~vibration_motor_on_command", 1))
        
        self.require_limit_switches = bool(rospy.get_param("~require_limit_switches", False))
        self.max_dig_dump_cycles = int(rospy.get_param("~max_dig_dump_cycles", 1))

        # Internal Variables
        self.autonomy_active = False
        self.bridge_connected = False
        self.latest_costmap = None
        self.latest_feedback_payload = {}
        self.last_costmap_time = None
        self.last_feedback_time = None
        self.completed_cycles = 0
        self.command_seq = 0
        
        # Geometry Math
        starting_corner = rospy.get_param("~starting_corner", "A")
        self.geometry = ArenaGeometry(starting_corner)

        # Service, Publishers, Subscribers
        self.autonomy_service = rospy.Service('/luna/autonomy/toggle', SetAutonomy, self._handle_autonomy_toggle)
        self.state_pub = rospy.Publisher(self.state_topic, String, queue_size=10)
        self.command_pub = rospy.Publisher(self.command_topic, String, queue_size=10)
        rospy.Subscriber(self.costmap_topic, OccupancyGrid, self._costmap_callback, queue_size=1)
        rospy.Subscriber(self.feedback_topic, String, self._feedback_callback, queue_size=1)
        rospy.Subscriber(self.bridge_connected_topic, Bool, self._bridge_callback, queue_size=1)
        
        self.rate = rospy.Rate(max(0.1, self.loop_rate_hz))

    def _handle_autonomy_toggle(self, req):
        self.autonomy_active = req.enable_autonomy
        state_str = "ON" if self.autonomy_active else "OFF"
        rospy.loginfo("Autonomy toggled to: %s", state_str)
        return SetAutonomyResponse(success=True, message=f"Autonomy is {state_str}")

    def _costmap_callback(self, msg):
        self.latest_costmap = msg
        self.last_costmap_time = rospy.Time.now()

    def _feedback_callback(self, msg):
        self.latest_feedback_payload = self._decode_feedback_payload(msg.data)
        self.last_feedback_time = rospy.Time.now()

    def _bridge_callback(self, msg):
        self.bridge_connected = bool(msg.data)

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

    def publish_state(self, state_name):
        self.state_pub.publish(String(data=state_name))

    def stop_rover(self):
        self.publish_rover_command()

    def publish_rover_command(self, *, linear_actuator_set_1=0, linear_actuator_set_2=0,
                              left_side_motors=0, right_side_motors=0, vibration_motor=0):
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

    def has_fault(self):
        return rospy.is_shutdown()

    def _recent(self, stamp):
        if stamp is None: return False
        return (rospy.Time.now() - stamp).to_sec() <= self.topic_timeout_s

    def systems_ok(self):
        if not self._recent(self.last_costmap_time): return False
        if not self._recent(self.last_feedback_time): return False
        if not self.bridge_connected: return False
        return True

    def missing_systems(self):
        missing = []
        if not self._recent(self.last_costmap_time): missing.append("costmap")
        if not self._recent(self.last_feedback_time): missing.append("feedback")
        if not self.bridge_connected: missing.append("bridge")
        return missing

    def sleep_once(self):
        self.rate.sleep()

    def feedback_active(self, field_name):
        try:
            return int(self.latest_feedback_payload.get(field_name, 0)) != 0
        except (TypeError, ValueError):
            return False

    def run_command_step(self, label, duration_s, stop_feedback_field=None,
                         require_stop_feedback=False, **command):
        rospy.loginfo("Starting autonomy command step: %s", label)
        end_time = rospy.Time.now() + rospy.Duration(max(0.0, duration_s))
        
        while not rospy.is_shutdown() and rospy.Time.now() < end_time:
            if not self.autonomy_active:
                self.stop_rover()
                return "fault" # Exit if toggle is switched off midway
            if stop_feedback_field and self.feedback_active(stop_feedback_field):
                self.stop_rover()
                return "done"
            self.publish_rover_command(**command)
            self.sleep_once()

        self.stop_rover()
        if rospy.is_shutdown() or not self.autonomy_active:
            return "fault"
        if require_stop_feedback and stop_feedback_field:
            return "timeout"
        return "done"

    def is_costmap_ready(self):
        return self.latest_costmap is not None and self._recent(self.last_costmap_time)

    def is_cell_safe(self, costmap, x, y):
        if not (0 <= x < costmap.info.width and 0 <= y < costmap.info.height): return False
        value = costmap.data[y * costmap.info.width + x]
        if value == -1: return not self.unknown_is_unsafe
        return value < self.obstacle_threshold

    def has_clearance(self, costmap, x, y, radius_cells):
        radius_sq = radius_cells * radius_cells
        for dy in range(-radius_cells, radius_cells + 1):
            for dx in range(-radius_cells, radius_cells + 1):
                if dx * dx + dy * dy > radius_sq: continue
                if not self.is_cell_safe(costmap, x + dx, y + dy): return False
        return True

    def score_cell(self, costmap, x, y):
        center_x = (costmap.info.width - 1) / 2.0
        safe_cells = 0
        radius = self.safety_score_radius_cells
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius and self.is_cell_safe(costmap, x + dx, y + dy):
                    safe_cells += 1
        return (self.forward_score_weight * float(y) + 
                self.center_score_weight * -abs(float(x) - center_x) + 
                self.clearance_score_weight * safe_cells)

    def find_safe_target(self):
        if not self.is_costmap_ready(): return None
        costmap = self.latest_costmap
        best_cell = None
        best_score = None

        for y in range(0, costmap.info.height, self.scan_stride_cells):
            for x in range(0, costmap.info.width, self.scan_stride_cells):
                if not self.has_clearance(costmap, x, y, self.target_clearance_cells): continue
                score = self.score_cell(costmap, x, y)
                if best_score is None or score > best_score:
                    best_score = score
                    best_cell = (x, y)

        if best_cell is None: return None
        
        origin = costmap.info.origin.position
        world_x = origin.x + (float(best_cell[0]) + 0.5) * costmap.info.resolution
        world_y = origin.y + (float(best_cell[1]) + 0.5) * costmap.info.resolution
        
        return {"world_x": world_x, "world_y": world_y, "frame_id": costmap.header.frame_id}

    def mark_cycle_complete(self):
        self.completed_cycles += 1

    def mission_complete(self):
        return self.completed_cycles >= self.max_dig_dump_cycles