cmake_minimum_required(VERSION 3.0.2)
project(luna_network_bridge)

find_package(catkin REQUIRED COMPONENTS
  geometry_msgs
  rospy
  std_msgs
)

catkin_package(
  CATKIN_DEPENDS geometry_msgs rospy std_msgs
)

catkin_install_python(PROGRAMS
  scripts/ros_network_bridge_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY
  launch
  config
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
