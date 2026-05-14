#!/usr/bin/env python3
#
# motor_monitor_node.py
#
# This file lives in:
#   scripts/
#
# In a catkin Python package, scripts/ usually contains EXECUTABLE entrypoints.
# This is the file that ROS runs as a node, for example with:
#
#   rosrun motor_monitor_pkg motor_monitor_node.py
#
# This script does three main things:
#   1. Starts a ROS node
#   2. Publishes a custom message on a topic
#   3. Offers a custom service that resets the motor

import rospy

# Import the reusable Python logic from src/motor_monitor_pkg/
# Because of setup.py + catkin_python_setup() + __init__.py,
# this can be imported as a normal Python package.
from motor_monitor_pkg import MotorMonitor

# Import the custom message type generated from msg/MotorComputed.msg
from motor_monitor_pkg.msg import MotorComputed

# Import the custom service type generated from srv/ResetMotor.srv
# ResetMotor is the service TYPE
# ResetMotorResponse is the generated Python class used to build a response
from motor_monitor_pkg.srv import ResetMotor, ResetMotorResponse

# Global variables used by the node.
# These are set inside main().
monitor = None
publisher = None


def handle_reset_motor(req):
    """
    Service callback function.

    ROS calls this function automatically whenever another node calls
    the /motor/reset service.

    The 'req' object contains the request fields defined in ResetMotor.srv:
      req.motor_id
      req.clear_faults

    The return value must match the RESPONSE part of ResetMotor.srv.
    """
    global monitor

    rospy.loginfo(
        "Received reset request for motor_id=%d clear_faults=%s",
        req.motor_id,
        req.clear_faults
    )

    # Call into our reusable Python logic.
    success, message = monitor.reset_motor(req.motor_id, req.clear_faults)

    # Build and return the service response.
    return ResetMotorResponse(success=success, message=message)


def main():
    """
    Main entrypoint for the ROS node.

    This function:
      1. initializes ROS,
      2. creates the MotorMonitor object,
      3. creates a publisher,
      4. creates a service server,
      5. loops forever publishing computed motor data.
    """
    global monitor, publisher

    # Register this process as a ROS node.
    #
    # This is what makes the Python process appear in the ROS graph.
    # The string here is the node name.
    rospy.init_node('motor_monitor_node')

    # Create our reusable logic object.
    monitor = MotorMonitor()

    # Create a publisher.
    #
    # Arguments:
    #   topic name: '/motor/computed'
    #   message type: MotorComputed
    #   queue_size: outgoing queue length
    #
    # Any node subscribing to /motor/computed using the same message type
    # can receive these messages.
    publisher = rospy.Publisher('/motor/computed', MotorComputed, queue_size=10)

    # Create a service server.
    #
    # Arguments:
    #   service name: '/motor/reset'
    #   service type: ResetMotor
    #   callback: handle_reset_motor
    #
    # Any node can now call this service and receive a ResetMotorResponse.
    rospy.Service('/motor/reset', ResetMotor, handle_reset_motor)

    rospy.loginfo("motor_monitor_node started")
    rospy.loginfo("Publishing computed motor data on /motor/computed")
    rospy.loginfo("Providing reset service on /motor/reset")

    # Set loop frequency to 2 Hz.
    # That means this loop runs approximately 2 times per second.
    rate = rospy.Rate(2)

    # Main ROS loop.
    #
    # Continue running until ROS tells us to shut down
    # (for example Ctrl+C or rosnode kill).
    while not rospy.is_shutdown():
        # Ask our source-code logic object to update itself and compute values.
        data = monitor.update()

        # Create an instance of the custom message.
        msg = MotorComputed()

        # Fill each field in the message from the computed dictionary.
        msg.rpm = data["rpm"]
        msg.current = data["current"]
        msg.temperature = data["temperature"]
        msg.power = data["power"]
        msg.health_score = data["health_score"]
        msg.needs_reset = data["needs_reset"]
        msg.state = data["state"]

        # Publish the message to the topic.
        publisher.publish(msg)

        rospy.loginfo(
            "Published motor data: rpm=%.2f current=%.2f temp=%.2f power=%.2f health=%.2f state=%s",
            msg.rpm,
            msg.current,
            msg.temperature,
            msg.power,
            msg.health_score,
            msg.state
        )

        # Sleep long enough to maintain the requested loop frequency.
        rate.sleep()


# Standard Python entrypoint check.
# This ensures main() is called only when this file is run directly.
if __name__ == '__main__':
    try:
        main()
    except rospy.ROSInterruptException:
        # This exception is commonly raised during ROS shutdown.
        pass
