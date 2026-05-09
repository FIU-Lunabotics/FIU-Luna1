#!/usr/bin/env bash
set -e

IMAGE_NAME="unitree_point_lio"
CONTAINER_NAME="ros1"
NETWORK_NAME="rosnet"

xhost +local:docker

export DISPLAY=${DISPLAY:-:0}

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run -it \
  --name "$CONTAINER_NAME" \
  --network "$NETWORK_NAME" \
  --env DISPLAY="$DISPLAY" \
  --env QT_X11_NO_MITSHM=1 \
  --volume /tmp/.X11-unix:/tmp/.X11-unix:rw \
  "$IMAGE_NAME"
