# __init__.py
#
# This file tells Python that this directory should be treated
# as a Python package.
#
# Because this file exists, code in scripts/ can do imports like:
#   from motor_monitor_pkg import MotorMonitor
#
# Without __init__.py, older Python/package tooling may not recognize
# the folder as a proper importable package in the ROS/catkin context.

from .motor_logic import MotorMonitor
