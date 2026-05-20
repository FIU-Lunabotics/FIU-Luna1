# motor_logic.py
#
# This file lives in:
#   src/motor_monitor_pkg/
#
# That means it is part of the package's importable Python source code.
#
# This file is NOT the ROS node executable itself.
# Instead, it contains reusable application logic that the ROS node imports.
#
# The idea is:
#   - scripts/motor_monitor_node.py handles ROS publishers/services/timers
#   - src/motor_monitor_pkg/motor_logic.py handles the actual motor logic
#
# This separation is a very common and clean ROS Python pattern.

class MotorMonitor:
    """
    A small example class that stores motor telemetry, computes some derived
    values, and supports a reset operation.

    In a real project this class might:
      - read from serial
      - call into a hardware SDK
      - talk to a CAN bus
      - parse incoming packets
      - filter noisy data
      - estimate faults or health
    """

    def __init__(self):
        """
        Constructor: called once when we create the MotorMonitor object.

        Here we initialize internal state for a single motor.
        In a real system, these values may come from hardware, configuration,
        or parameters.
        """

        # Example raw/monitored values.
        self.rpm = 1200.0
        self.current = 4.2
        self.temperature = 42.0

        # High-level motor state string.
        self.state = "OK"

        # Record of the last motor reset request we handled.
        self.last_reset_motor_id = None

        # Example fixed supply voltage used to compute power.
        # In a real robot this could also be measured live.
        self.bus_voltage = 24.0

    def update(self):
        """
        Update the internal motor data and compute output values.

        In a real system, this method would likely:
          1. read new telemetry from hardware,
          2. run filtering/validation,
          3. compute derived quantities,
          4. detect faults,
          5. return a structured result.

        Here we just simulate changing data.
        """

        # Simulate the motor speed drifting upward slightly over time.
        self.rpm += 5.0

        # Simulate current increasing slightly.
        self.current += 0.02

        # Simulate temperature increasing slowly.
        self.temperature += 0.05

        # Compute derived values.
        power = self.compute_power()
        health_score = self.compute_health_score()

        # Decide whether the motor needs a reset.
        # This is just example logic.
        needs_reset = self.temperature > 70.0 or self.current > 12.0

        # Update the state string based on simple thresholds.
        if needs_reset:
            self.state = "FAULT"
        elif self.temperature > 55.0:
            self.state = "WARNING"
        else:
            self.state = "OK"

        # Return the data in a dictionary so the ROS node can easily
        # fill in the custom MotorComputed message.
        return {
            "rpm": self.rpm,
            "current": self.current,
            "temperature": self.temperature,
            "power": power,
            "health_score": health_score,
            "needs_reset": needs_reset,
            "state": self.state,
        }

    def compute_power(self):
        """
        Compute electrical power in watts.

        Very simple example:
            power = voltage * current
        """
        return self.bus_voltage * self.current

    def compute_health_score(self):
        """
        Compute a rough health score from 0 to 100.

        This is arbitrary example logic.
        We reduce the score if temperature is too high or if current rises
        above a nominal value.

        In a real system, health metrics could be based on:
          - current spikes,
          - motor efficiency,
          - temperature trends,
          - vibration,
          - encoder consistency,
          - fault history.
        """

        score = 100.0

        # Penalize temperature above 40 C.
        score -= max(0.0, self.temperature - 40.0) * 1.5

        # Penalize current above 5 A.
        score -= max(0.0, self.current - 5.0) * 4.0

        # Clamp the score so it never goes below 0.
        return max(0.0, score)

    def reset_motor(self, motor_id, clear_faults):
        """
        Simulate resetting the motor.

        Arguments:
          motor_id (int): which motor to reset
          clear_faults (bool): whether to clear the FAULT state

        Returns:
          (success, message) tuple

        In a real system this might:
          - send a CAN command,
          - write a serial packet,
          - call a vendor API,
          - wait for an acknowledgment,
          - update internal state based on the result.
        """

        # Record which motor was last requested for reset.
        self.last_reset_motor_id = motor_id

        # Simulate that reset brings temperature/current back down.
        self.temperature = 35.0
        self.current = 2.0

        # Optionally clear fault state.
        if clear_faults:
            self.state = "OK"

        # Return a typical service-style result.
        return True, f"Motor {motor_id} reset complete"
