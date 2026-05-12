#!/usr/bin/env bash
set -e

IMAGE_NAME="unitree_point_lio"
CONTAINER_NAME="ros2"
NETWORK_NAME="rosnet"

xhost +local:docker

export DISPLAY=${DISPLAY:-:0}

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run -it \
  --name "$CONTAINER_NAME" \
  --network host \
  --privileged \
  --env DISPLAY="$DISPLAY" \
  --env QT_X11_NO_MITSHM=1 \
  --env LIBGL_ALWAYS_SOFTWARE=1 \
  --env XDG_RUNTIME_DIR=/tmp/runtime-root \
  --volume /tmp/.X11-unix:/tmp/.X11-unix:rw \
  --volume ~/Desktop/Engineering/FIU-Luna1/Client-Jetson/Neural-Network/LIDAR:/root/project \
  "$IMAGE_NAME"

