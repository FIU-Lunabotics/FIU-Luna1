#!/usr/bin/env bash
set -e

IMAGE_NAME="unitree_point_lio"
CONTAINER_NAME="ros1"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ETH_IFACE="enp0s20f0u5u5c2"
PC_IP="192.168.1.2"

if ! docker image inspect "$IMAGE_NAME:latest" >/dev/null 2>&1; then
  echo "Docker image not found. Building $IMAGE_NAME..."
  docker build --network=host -t "$IMAGE_NAME" "$PROJECT_DIR"
fi

echo "Configuring Ethernet interface $ETH_IFACE..."
sudo ip link set wlan0 down || true
sudo ip addr flush dev "$ETH_IFACE"
sudo ip addr add "$PC_IP/24" dev "$ETH_IFACE"
sudo ip link set "$ETH_IFACE" up

xhost +local:

export DISPLAY="${DISPLAY:-:0}"

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run -it \
  --name "$CONTAINER_NAME" \
  --net=host \
  --privileged \
  -e DISPLAY="$DISPLAY" \
  -e QT_X11_NO_MITSHM=1 \
  -e LIBGL_ALWAYS_SOFTWARE=1 \
  -e XDG_RUNTIME_DIR=/tmp/runtime-root \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v "$PROJECT_DIR:/root/neural_network" \
  "$IMAGE_NAME"
