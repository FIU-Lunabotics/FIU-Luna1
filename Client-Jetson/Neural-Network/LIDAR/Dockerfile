FROM ros:noetic-ros-base-focal

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

# Install build tools, ROS deps, PCL, RViz, and debugging tools
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    python3-catkin-tools \
    python3-rosdep \
    python3-vcstool \
    python3-pip \
    python3-yaml \
    libpcl-dev \
    libeigen3-dev \
    usbutils \
    udev \
    lsof \
    ros-noetic-eigen-conversions \
    ros-noetic-pcl-ros \
    ros-noetic-pcl-conversions \
    ros-noetic-visualization-msgs \
    ros-noetic-rviz \
    ros-noetic-rqt \
    ros-noetic-rqt-common-plugins \
    && rm -rf /var/lib/apt/lists/*

RUN rosdep init || true
RUN rosdep update

WORKDIR /root/catkin_ws
RUN mkdir -p /root/catkin_ws/src

# Clone Unitree SDK2 for L2 + Point-LIO
RUN cd /root/catkin_ws/src && \
    git clone https://github.com/unitreerobotics/unilidar_sdk2.git unilidar_sdk2 && \
    mkdir -p catkin_point_lio_unilidar/src && \
    cd catkin_point_lio_unilidar/src && \
    git clone https://github.com/unitreerobotics/point_lio_unilidar.git point_lio_unilidar

# Find and patch the SDK2 config file automatically
RUN CONFIG_FILE="$(find /root/catkin_ws/src/unilidar_sdk2 -name config.yaml | head -n 1)" && \
    echo "Using config file: $CONFIG_FILE" && \
    sed -i 's|^initialize_type:.*|initialize_type: 1|' "$CONFIG_FILE" && \
    sed -i 's|^port:.*|port: "/dev/unitree_lidar"|' "$CONFIG_FILE"



# Build Unitree SDK2 ROS driver and Point-LIO workspace
RUN source /opt/ros/noetic/setup.bash && \
    cd /root/catkin_ws/src/unilidar_sdk2/unitree_lidar_ros && \
    catkin_make && \
    source devel/setup.bash && \
    cd /root/catkin_ws/src/catkin_point_lio_unilidar && \
    catkin_make

# Source ROS and both workspaces automatically
RUN echo "source /opt/ros/noetic/setup.bash" >> /root/.bashrc && \
    echo "source /root/catkin_ws/src/unilidar_sdk2/unitree_lidar_ros/devel/setup.bash" >> /root/.bashrc && \
    echo "source /root/catkin_ws/src/catkin_point_lio_unilidar/devel/setup.bash" >> /root/.bashrc

CMD ["/bin/bash"]
