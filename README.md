# ROS 2 Jazzy in Docker

A standalone image based on Ubuntu 24.04 and ROS 2 Jazzy. The `Dockerfile`
installs ROS Desktop and the packages listed in the course PDF. It does not
`COPY` any project files during the build. A folder from your computer is
mounted at `/workspace` in the container, so files stored there persist on
your computer.

## Requirements

- A running Docker Engine or Docker Desktop.
- Python 3 on the host for `ros2.py`. The script uses only Python's standard library.
- An X11 server for `x11` mode, or XQuartz for `xquartz` mode. Browser display
  mode (`vnc`) needs neither on the host.
- Enough free disk space for ROS Desktop, Gazebo, and the course packages.

## Build and publish

Run these commands in the directory containing the `Dockerfile`:

```bash
docker build -t airfarmdev/ros2_env:latest .
docker image inspect airfarmdev/ros2_env:latest
docker login
docker push airfarmdev/ros2_env:latest
```

To publish **both amd64 and arm64** with Buildx, run this after `docker login`:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t airfarmdev/ros2_env:latest --push .
```

Docker Hub stores the **image**, not a Docker Compose file. The official
`ros:jazzy-ros-base-noble` base image supports amd64 and arm64; this Dockerfile
adds the desktop and course packages for both architectures.

## Start with `ros2.py`

After publishing the image, or after building it locally with the command
above, run:

```bash
python3 ros2.py
```

On the first run, the script asks for the image, shared folder, container
name, and display mode. On Linux it also asks for `DISPLAY` and an optional
Xauthority file. It then tries `docker pull` and uses the local image if the
download fails and that image is already available. It saves the configuration at
`~/.config/ros2_env/config.json` (or under `$XDG_CONFIG_HOME/ros2_env`),
creates the container, and opens a shell in `/workspace`.

On later runs, use the same command:

```bash
python3 ros2.py
```

The script restarts and attaches to the same container. If it is already
running, the script opens another shell. `exit` closes that shell. In `vnc`
mode, the container stays running to keep the browser display available; stop
it with `docker stop ros2_env` when finished. In other modes, exiting the main
shell stops the container. The shared folder remains on your computer.

To change the setup, run:

```bash
python3 ros2.py configure
```

This asks for new values, downloads the image, removes the old container
created by the script, and creates a new one. **It does not delete the shared
folder or its files.** Changes made only inside the old container, outside
`/workspace`, are lost.

The shared folder may be new; the script creates it. If Docker Desktop denies
the mount, enable file sharing for that path in Docker Desktop settings.

If the image build fails with `No space left on device`, check Docker's own
storage with `docker system df`. Docker Desktop's virtual disk can fill up even
when the host still has free space. Increase Docker Desktop's disk limit or
clear unused build cache with `docker builder prune` if those cached layers are
no longer needed. The course package set is large.

## Graphical applications

### Browser display (recommended on macOS)

Choose `vnc` during setup. This is the default on macOS and also works on
Linux. The image runs Xvfb and Mesa inside the container and serves the display
through noVNC. The script publishes the chosen port only on the host's
loopback interface. Open the address printed by the script, by default:

```text
http://localhost:6080/vnc.html
```

After clicking **Connect**, the desktop may look black or empty. This is the
normal Openbox background; applications started from the container shell appear
there. Keep the browser page open, then run:

```bash
python3 ros2.py
# In the container shell:
gz sim shapes.sdf -v 4
```

The Gazebo window appears in the browser. Rendering uses the CPU and may be slow
for complex worlds.
If port 6080 is occupied, choose another browser display port during setup.

If you already configured `xquartz`, run `python3 ros2.py configure` and select
`vnc`. Rebuild the image first to add the browser display tools. If your Hub
image has not been updated yet, tag the new local build separately:

```bash
docker build -t airfarmdev/ros2_env:local .
```

Select `airfarmdev/ros2_env:local` as the image during `configure`.

### Linux with X11 or XWayland

Choose `x11` during setup. The script mounts `/tmp/.X11-unix`, passes
`DISPLAY`, uses the host user's UID/GID, and mounts Xauthority when available.
If Xauthority is unavailable and the X server denies access, run this in a
**host** terminal:

```bash
xhost +si:localuser:$(id -un)
```

Then try `python3 ros2.py` again. To test the display from the container shell:

```bash
xeyes
rviz2
```

### macOS with XQuartz

Install and start [XQuartz](https://www.xquartz.org/). In its security
settings, enable **Allow connections from network clients**, then quit and
restart XQuartz. In an XQuartz terminal, run:

```bash
xhost +localhost
```

Choose `xquartz` during setup to share the host X server. The script sets
`DISPLAY=host.docker.internal:0`. Test basic X11 with `xeyes`, then check GLX
with `glxinfo -B` inside the container. If `glxinfo` cannot create a GLX
context, Gazebo and RViz cannot use that XQuartz display; use `vnc` mode.

If Gazebo reports `could not connect to display host.docker.internal:0`, check
that XQuartz is listening on TCP port 6000 on the Mac:

```bash
lsof -nP -iTCP:6000 -sTCP:LISTEN
```

If there is no listener, enable **Allow connections from network clients** in
XQuartz Settings > Security and **fully quit and restart XQuartz**. Then run
`xhost +localhost` in an XQuartz terminal and try `xeyes` inside the container
before starting Gazebo. The subsequent Qt `xcb` message is a result of the
failed display connection; reinstalling Qt is not needed.

If Gazebo instead reports `Failed to create OpenGL context` or `glxinfo -B`
reports `No matching fbConfigs or visuals found`, X11 connectivity works but
XQuartz does not provide a compatible GLX context. Switch to `vnc` mode.

Choose `none` if you do not need graphical applications. To change the mode
later, run `python3 ros2.py configure`.

## Launch Gazebo

First open the configured container from a host terminal:

```bash
python3 ros2.py
```

Then, inside the container, start Gazebo Sim with a sample world:

```bash
gz sim shapes.sdf -v 4
```

This starts the simulator and its GUI. In `vnc` mode, open the browser address
printed by `ros2.py` to see it. For a server without a GUI, use
`gz sim -s shapes.sdf -v 4`. To open your own world from the shared folder,
use `gz sim /workspace/your_world.sdf -v 4`. Gazebo Harmonic uses the `gz sim`
command; `gazebo` is the older Gazebo Classic command.

## Run without the script

For a quick test on Linux with X11, use this command with your shared folder:

```bash
docker run --rm -it \
  --mount "type=bind,source=$PWD/shared,target=/workspace" \
  --mount type=bind,source=/tmp/.X11-unix,target=/tmp/.X11-unix,readonly \
  --env DISPLAY="$DISPLAY" \
  --user "$(id -u):$(id -g)" --env HOME=/workspace \
  airfarmdev/ros2_env:latest bash
```

Create the `shared` directory first with `mkdir -p shared`. The `--rm` option
deletes this test container on exit; files in `shared` remain.

## References

- [ROS 2 Jazzy installation on Ubuntu 24.04](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html)
- [Official ROS Docker image](https://hub.docker.com/_/ros)
- [Docker Desktop networking and `host.docker.internal`](https://docs.docker.com/desktop/features/networking/networking-how-tos/)
