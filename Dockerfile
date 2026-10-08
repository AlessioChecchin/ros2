FROM ros:jazzy-ros-base-noble

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
    QT_X11_NO_MITSHM=1

# ROS 2 Jazzy desktop and the packages listed in the course instructions.
# This Dockerfile needs no files from the build context.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-desktop \
    ros-jazzy-ament-cmake \
    ros-jazzy-ur \
    ros-jazzy-ur-simulation-gz \
    ros-jazzy-navigation2 \
    ros-jazzy-nav2-bringup \
    ros-jazzy-nav2-minimal-tb3-sim \
    ros-jazzy-nav2-minimal-tb4-description \
    ros-jazzy-nav2-minimal-tb4-sim \
    ros-jazzy-turtlebot3-gazebo \
    ros-jazzy-rviz-visual-tools \
    ros-jazzy-gz-ros2-control \
    ros-jazzy-ros-gz-sim \
    ros-jazzy-joint-state-broadcaster \
    ros-jazzy-joint-trajectory-controller \
    ros-jazzy-pcl-ros \
    ros-jazzy-realsense2-description \
    ros-jazzy-robotiq-controllers \
    ros-jazzy-robotiq-description \
    ros-jazzy-ros-gz \
    ros-jazzy-apriltag \
    ros-jazzy-apriltag-ros \
    ros-jazzy-apriltag-detector \
    ros-jazzy-image-proc \
    ros-jazzy-depth-image-proc \
    ros-jazzy-nav2-route \
    xauth \
    x11-apps \
    mesa-utils \
    libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*

# An internal X server keeps OpenGL rendering inside Linux. noVNC exposes
# that display in a browser when the host X server cannot provide GLX.
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc novnc websockify openbox \
    && rm -rf /var/lib/apt/lists/*

RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'export DISPLAY=:99' \
    'export LIBGL_ALWAYS_SOFTWARE=1' \
    'Xvfb :99 -screen 0 1600x900x24 -nolisten tcp +extension GLX -noreset &' \
    'sleep 2' \
    'openbox &' \
    'x11vnc -display :99 -localhost -forever -shared -nopw -rfbport 5900 &' \
    'exec websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900' \
    > /usr/local/bin/ros2-vnc-start \
    && chmod +x /usr/local/bin/ros2-vnc-start

# The official ROS entrypoint sources Jazzy for `docker run`; this also covers
# shells opened later with `docker exec`.
RUN printf '\nsource /opt/ros/jazzy/setup.bash\n' >> /etc/bash.bashrc

WORKDIR /workspace
CMD ["bash"]
