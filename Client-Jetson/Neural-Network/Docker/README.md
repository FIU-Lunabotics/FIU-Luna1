# LIDAR Mapping Setup (Unitree L2 + Point-LIO)

This directory contains the Docker environment and launch scripts required to run the Unitree L2 LiDAR with Point-LIO and RViz.

The recommended configuration is Ethernet mode. The USB UART breakout board proved unreliable during testing and should only be used as a fallback.

---

# Hardware Setup

## Recommended Wiring (Ethernet Mode)

```text
Unitree L2 barrel jack -> external power supply
Unitree L2 Ethernet port -> USB-C Ethernet adapter -> host PC / Jetson
```

The LiDAR must always receive external barrel power.

---

# Docker Architecture Notes

## Jetson Nano / ARM SBCs

Jetson boards are ARM systems and require ARM-compatible ROS containers.

Use the following Docker base image:

```dockerfile
FROM ros:noetic-ros-base-focal
```

## Desktop x86_64 PCs

The same image may still work through Docker multi-architecture support, but some systems may require an x86 ROS image instead.

---

# Building the Docker Image

Navigate into the LIDAR directory:

```bash
cd FIU-Luna1/Client-Jetson/Neural-Network/LIDAR
```

Build the image:

```bash
docker build --network=host -t unitree_point_lio .
```

The `--network=host` argument is important because some systems fail DNS resolution inside Docker during package installation.

---

# Configure Host Ethernet Interface

The Unitree L2 uses the following default IP address:

```text
192.168.1.62
```

The host machine must be manually configured onto the same subnet.

---

## 1. Identify Ethernet Interface

Run:

```bash
ip link
```

Typical output:

```text
enp0s20f0u5u5c2
```

---

## 2. Disable Wi-Fi Temporarily

If Wi-Fi is active on another `192.168.1.x` network, Linux routing conflicts may occur and communication with the LiDAR may fail.

Disable Wi-Fi:

```bash
sudo ip link set wlan0 down
```

Wi-Fi can be re-enabled later using:

```bash
sudo ip link set wlan0 up
```

---

## 3. Assign Static Ethernet IP

Replace the interface name below with your own:

```bash
sudo ip addr flush dev enp0s20f0u5u5c2
sudo ip addr add 192.168.1.2/24 dev enp0s20f0u5u5c2
sudo ip link set enp0s20f0u5u5c2 up
```

---

## 4. Verify Connectivity

Ping the LiDAR:

```bash
ping 192.168.1.62
```

Expected result:

```text
64 bytes from 192.168.1.62
```

If ping works only briefly and then fails, Wi-Fi routing conflicts are still active.

---

# Launch Docker Container

Run the launch script:

```bash
./ros1test.sh
```

The shell script automatically:

* configures Docker host networking
* mounts the project directory
* enables X11 forwarding for RViz
* launches the ROS container

---

# Configure Unitree SDK for Ethernet Mode

Inside the container, force Ethernet mode:

```bash
sed -i 's|initialize_type: .*|initialize_type: 2|' \
/root/catkin_ws/src/unilidar_sdk2/unitree_lidar_ros/src/unitree_lidar_ros/config/config.yaml
```

Verify the change:

```bash
grep initialize_type \
/root/catkin_ws/src/unilidar_sdk2/unitree_lidar_ros/src/unitree_lidar_ros/config/config.yaml
```

Expected result:

```text
initialize_type: 2
```

---

# Launch Unitree Backend

Inside the Docker container:

```bash
roslaunch unitree_lidar_ros run_without_rviz.launch --screen
```

Expected result:

```text
initialize_type_ = 2
```

Do NOT continue if you see:

```text
Unilidar is not initialized!
```

or

```text
Serial port timeout!
```

---

# Verify LiDAR Data

Open a second terminal:

```bash
docker exec -it ros1 bash
```

Check the ROS topics:

```bash
rostopic hz /unilidar/cloud
rostopic hz /unilidar/imu
```

Expected result:

```text
average rate: ...
```

If you see:

```text
no new messages
```

then the backend is not receiving LiDAR data correctly.

---

# Launch Point-LIO + RViz

Inside the second terminal:

```bash
roslaunch point_lio_unilidar mapping_unilidar_l2.launch
```

RViz should automatically open.

---

# RViz Configuration

If RViz opens but no point cloud appears:

Set:

```text
Fixed Frame -> map
```

To manually add the raw cloud:

```text
Add -> PointCloud2
Topic -> /unilidar/cloud
```

For raw LiDAR viewing without Point-LIO:

```text
Fixed Frame -> unilidar_lidar
```

---

# Saved Point Cloud Data

After scanning is complete, Point-LIO stores the generated map here:

```text
catkin_point_lio_unilidar/src/point_lio_unilidar/PCD/scans.pcd
```

View the PCD file:

```bash
pcl_viewer scans.pcd
```

---

# Common Issues

## RViz crashes with OpenGL errors

Before launching RViz:

```bash
export LIBGL_ALWAYS_SOFTWARE=1
```

---

## Docker build cannot resolve packages

Build using:

```bash
docker build --network=host -t unitree_point_lio .
```

---

## Duplicate ROS nodes

If duplicate nodes appear:

```bash
pkill -9 -f roslaunch
pkill -9 -f unitree_lidar_ros_node
```

---

## No point cloud visible

Verify:

```bash
rostopic hz /unilidar/cloud
```

If no messages appear, the backend is not receiving LiDAR data correctly.

---

## Ethernet communication fails after several successful pings

This is usually caused by Wi-Fi routing conflicts.

Disable Wi-Fi temporarily:

```bash
sudo ip link set wlan0 down
```

---

## USB UART mode issues

The USB UART breakout board may:

* enumerate correctly as `/dev/ttyACM0`
* but fail to transmit LiDAR packets

Ethernet mode is strongly recommended for reliability.

