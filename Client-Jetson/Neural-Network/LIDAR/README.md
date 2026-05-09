# LIDAR mapping 

This directory contains the Dockerfile which allows you to automatically build a dockerimage from any computer. This Dockerfile uses the arm image, if you are running from a PC running a 64-bit arch and not a arm processor, you must change the image of ROS. For 64-bit systems, replace the 1st line with the following

> FROM ros:noetic-ros-base-focal

To build the docker image, navigate into the directory FIU-Luna1/Client-Jetson/Neural-Network/LIDAR and run the following command

> docker build -t unilidar_point_lio .

This will build the image and save it to dockers filepath. 

If the pc has not setup a docker network named rosnet yet for individual containers to interact with one another, the following will command will have to be done

> docker network create rosnet

You can check for created networks using

> docker network ls

To clarify terminology, a Dockerfie describes how to build a docker image which is a image which contains all the information needed to run the docker container which is the active process thats created when you run a docker image.

Once you've done this, you can run the bash scripts "ros1.sh" and "ros2.sh" which are shell scripts that automatically launch the docker container.

If you receive an error saying the container already exists, first print all active and inactive containers using the following:

> docker ps -a

From here you have 2 options:

> [!INFO] Option 1: Delete the container and rerun the shell script
docker rm -rf (containerName)
./(bashScript)

> [!INFO] Option 2: Run the inactive container
docker start (containerName)

There is no meaningful difference between ros1 and 2, they are the exact same but we need 2 instances to run both programs for the LIDAR. 

Once you have the both of these docker containers open, run the following

> [!INFO] ros1
cd src/unilidar_sdk/unitree_lidar_ros
source devel/setup.bash
roslaunch unitree_lidar_ros run_without_rviz.launch

> [!INFO] ros2
cd catkin_point_lio_unilidar
source devel/setup.bash
roslaunch point_lio_unilidar mapping_unilidar_l2.launch 

After completion of the run, all cached pointcloud map will be saved to the following path:

> catkin_point_lio_unilidar/src/point_lio_unilidar/PCD/scans.pcd

You can use the pcl_viewer tool to view this pcd file:

> pcl_viewer scans.pcd 
