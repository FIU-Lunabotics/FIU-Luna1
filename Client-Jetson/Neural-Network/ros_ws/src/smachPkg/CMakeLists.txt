cmake_minimum_required(VERSION 3.0.2)
project(smachPkg)

find_package(catkin REQUIRED COMPONENTS
  rospy
  roscpp
  std_msgs
  geometry_msgs
)

catkin_package(
  CATKIN_DEPENDS rospy roscpp std_msgs geometry_msgs
)

include_directories(
  ${catkin_INCLUDE_DIRS}
)

add_executable(controller_node src/controller_node.cpp)
add_dependencies(controller_node ${${PROJECT_NAME}_EXPORTED_TARGETS} ${catkin_EXPORTED_TARGETS})
target_link_libraries(controller_node ${catkin_LIBRARIES})

catkin_install_python(PROGRAMS
  scripts/teleop_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
