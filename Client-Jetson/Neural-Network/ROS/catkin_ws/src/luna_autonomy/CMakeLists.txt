cmake_minimum_required(VERSION 3.0.2)
project(luna_autonomy)

find_package(catkin REQUIRED COMPONENTS
  geometry_msgs
  nav_msgs
  rospy
  smach
  smach_ros
  std_msgs
)

catkin_package(
  CATKIN_DEPENDS geometry_msgs nav_msgs rospy smach smach_ros std_msgs
)

catkin_install_python(PROGRAMS
  scripts/autonomy_state_machine.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY
  launch
  config
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
