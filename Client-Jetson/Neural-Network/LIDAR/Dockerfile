FROM ros:noetic-ros-base-focal 

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

# Install build tools and PCL / visualization deps
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
    ros-noetic-eigen-conversions \
    && rm -rf /var/lib/apt/lists/*

# Install Noetic PCL and visualization messages
RUN apt-get update && apt-get install -y \
    ros-noetic-pcl-ros \
    ros-noetic-pcl-conversions \
    ros-noetic-visualization-msgs \
    && rm -rf /var/lib/apt/lists/*

#Add Rviz without changing steps before to avoid having to rebuild those steps as they took a while
RUN apt-get update && apt-get install -y \
    ros-noetic-rviz \
    ros-noetic-rqt \
    ros-noetic-rqt-common-plugins \
    && rm -rf /var/lib/apt/lists/*

# Initialize rosdep
RUN rosdep init || true
RUN rosdep update

# Create workspace
WORKDIR /root/catkin_ws

RUN mkdir -p /root/catkin_ws/src

#ARG CLONE_VERSION=1

# Clone Unitree SDK + Point-LIO (Noetic version)
RUN cd /root/catkin_ws/src && \
    git clone https://github.com/unitreerobotics/unilidar_sdk.git unilidar_sdk && \
    mkdir -p catkin_point_lio_unilidar/src && \
    cd catkin_point_lio_unilidar/src && \
    git clone https://github.com/unitreerobotics/point_lio_unilidar.git point_lio_unilidar

# Work around YAML_CPP in Unitree SDK if needed
# RUN sed -i '/find_package(YAML_CPP REQUIRED)/s/^/#/' /root/catkin_ws/src/unilidar_sdk/CMakeLists.txt

#Build the toolchain

RUN /bin/bash -c "source /opt/ros/noetic/setup.bash && \
    cd /root/catkin_ws/src/unilidar_sdk/unitree_lidar_ros && \
    catkin_make && \
    cd /root/catkin_ws/src/catkin_point_lio_unilidar && \
    catkin_make"

# Add ROS + workspace setup to shell
RUN echo "source /opt/ros/noetic/setup.bash" >> /root/.bashrc && \
    echo "source /root/catkin_ws/devel/setup.bash" >> /root/.bashrc

# Default command: give a shell where ROS is ready
CMD ["/bin/bash"]
