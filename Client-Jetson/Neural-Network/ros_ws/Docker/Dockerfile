FROM ros:noetic-ros-base-focal

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

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
    xxd \
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
RUN mkdir -p /root/catkin_ws/src/catkin_point_lio_unilidar/src

COPY dependencies/unilidar_sdk2 /root/catkin_ws/src/unilidar_sdk2
COPY dependencies/point_lio_unilidar /root/catkin_ws/src/catkin_point_lio_unilidar/src/point_lio_unilidar

RUN CONFIG_FILE="$(find /root/catkin_ws/src/unilidar_sdk2 -name config.yaml | head -n 1)" && \
    echo "Using config file: $CONFIG_FILE" && \
    sed -i 's|^initialize_type:.*|initialize_type: 2|' "$CONFIG_FILE" && \
    sed -i 's|^lidar_ip:.*|lidar_ip: "192.168.1.62"|' "$CONFIG_FILE" && \
    sed -i 's|^pc_ip:.*|pc_ip: "192.168.1.2"|' "$CONFIG_FILE" && \
    sed -i 's|^lidar_port:.*|lidar_port: 6101|' "$CONFIG_FILE" && \
    sed -i 's|^pc_port:.*|pc_port: 6201|' "$CONFIG_FILE"

RUN source /opt/ros/noetic/setup.bash && \
    cd /root/catkin_ws/src/unilidar_sdk2/unitree_lidar_ros && \
    catkin_make && \
    source devel/setup.bash && \
    cd /root/catkin_ws/src/catkin_point_lio_unilidar && \
    catkin_make

RUN echo "source /opt/ros/noetic/setup.bash" >> /root/.bashrc && \
    echo "source /root/catkin_ws/src/unilidar_sdk2/unitree_lidar_ros/devel/setup.bash" >> /root/.bashrc && \
    echo "source /root/catkin_ws/src/catkin_point_lio_unilidar/devel/setup.bash" >> /root/.bashrc && \
    echo "if [ -f /root/neural_network/ros_ws/devel/setup.bash ]; then source /root/neural_network/ros_ws/devel/setup.bash; fi" >> /root/.bashrc

WORKDIR /root/neural_network

CMD ["/bin/bash"]
