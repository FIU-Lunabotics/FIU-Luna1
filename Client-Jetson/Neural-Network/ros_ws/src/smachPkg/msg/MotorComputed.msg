# MotorComputed.msg
#
# This file defines a CUSTOM ROS MESSAGE TYPE.
# A ROS message is the data structure that gets sent over a TOPIC.
#
# In this example, our node will publish this message on a topic such as:
#   /motor/computed
#
# Any other node that subscribes to that topic must know this exact message type.
#
# After catkin builds the package, ROS generates code so that in Python
# you can write:
#   from motor_monitor_pkg.msg import MotorComputed
#
# Every line below is one field in the message.
# Syntax is:
#   <type> <field_name>

# Estimated motor speed in revolutions per minute.
float32 rpm

# Motor current draw in amps.
float32 current

# Motor temperature in degrees Celsius.
float32 temperature

# Computed electrical power in watts.
# In our example logic this is voltage * current.
float32 power

# A computed health score from 0 to 100.
# Higher is better.
float32 health_score

# True if the node thinks the motor should be reset.
bool needs_reset

# Human-readable status string such as:
# "OK", "WARNING", "FAULT"
string state
