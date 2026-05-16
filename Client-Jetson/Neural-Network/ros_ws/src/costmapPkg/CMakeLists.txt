cmake_minimum_required(VERSION 3.0.2)
project(costmapPkg)

# 1. Look for the build dependencies you defined in package.xml
find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  sensor_msgs
  nav_msgs
  geometry_msgs
)

# 2. Export your dependencies to any other package that might rely on yours
catkin_package(
  CATKIN_DEPENDS rospy std_msgs sensor_msgs nav_msgs geometry_msgs
)

# 3. Tell the compiler to install your Python script as an executable node
catkin_install_python(PROGRAMS
  scripts/costmap_controller.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
