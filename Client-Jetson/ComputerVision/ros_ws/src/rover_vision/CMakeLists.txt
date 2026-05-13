cmake_minimum_required(VERSION 3.0.2)
project(rover_vision)

find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  sensor_msgs
  cv_bridge
  image_transport
)

catkin_package()

catkin_install_python(PROGRAMS
  scripts/cv_detection_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
