cmake_minimum_required(VERSION 3.0.2)
project(costmapPkg)

find_package(catkin REQUIRED COMPONENTS
  rospy
  roscpp
  std_msgs
  sensor_msgs
  nav_msgs
  geometry_msgs
  visualization_msgs
)

catkin_package(
  CATKIN_DEPENDS rospy roscpp std_msgs sensor_msgs nav_msgs geometry_msgs visualization_msgs
)

include_directories(
  ${catkin_INCLUDE_DIRS}
)
