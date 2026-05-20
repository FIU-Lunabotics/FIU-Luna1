cmake_minimum_required(VERSION 3.0.2)
project(autonomyPkg)

# 1. ADD message_generation to build our .srv file
find_package(catkin REQUIRED COMPONENTS
  geometry_msgs
  nav_msgs
  rospy
  smach
  smach_ros
  std_msgs
  message_generation
)

# 2. UNCOMMENT to tell ROS to look for our internal Python library in the src/ folder
catkin_python_setup()

# 3. UNCOMMENT and add our specific service file
add_service_files(
  FILES
  SetAutonomy.srv
)

# 4. UNCOMMENT to actually generate the Python code for the service
generate_messages(
  DEPENDENCIES
  std_msgs
)

# 5. UNCOMMENT and add message_runtime so other packages can use our service
catkin_package(
  CATKIN_DEPENDS geometry_msgs nav_msgs rospy smach smach_ros std_msgs message_runtime
)

# 6. UNCOMMENT and point to our main executable script
catkin_install_python(PROGRAMS
  scripts/mission_manager_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
