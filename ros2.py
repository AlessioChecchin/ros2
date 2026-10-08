#!/usr/bin/env python3
"""Configure and open a persistent ROS 2 Jazzy Docker container."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys


IMAGE = "airfarmdev/ros2_env:latest"
LABEL = "io.airfarmdev.ros2-env"
WORKSPACE = "/workspace"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ros2_env"
CONFIG_FILE = CONFIG_DIR / "config.json"


class SetupError(Exception):
    pass


def docker(*args, check=True, capture=False):
    try:
        result = subprocess.run(
            ["docker", *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise SetupError("Docker is not installed or is not in PATH.") from exc
    if check and result.returncode:
        detail = (result.stderr or "").strip()
        raise SetupError(f"Docker command failed: docker {' '.join(args)}\n{detail}".strip())
    return result


def check_docker():
    if shutil.which("docker") is None:
        raise SetupError("Docker is not installed or is not in PATH.")
    if docker("info", "--format", "{{.ServerVersion}}", check=False, capture=True).returncode:
        raise SetupError("Docker is not responding. Start Docker Desktop or the Docker daemon and try again.")


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SetupError("Setup interrupted; no container was changed.") from exc
    return answer or default


def ask_choice(prompt, choices, default):
    while True:
        answer = ask(f"{prompt} ({'/'.join(choices)})", default).lower()
        if answer in choices:
            return answer
        print(f"Choose one of: {', '.join(choices)}")


def collect_config(previous=None):
    previous = previous or {}
    image = ask("Docker image", previous.get("image", IMAGE))
    if not image or any(char.isspace() for char in image):
        raise SetupError("The image name cannot contain spaces.")

    default_folder = previous.get("shared_folder", str(Path.cwd() / "shared"))
    folder = Path(ask("Shared folder on your computer", default_folder)).expanduser().resolve()
    if folder.exists() and not folder.is_dir():
        raise SetupError(f"The path is not a directory: {folder}")
    if "," in str(folder):
        raise SetupError("The shared folder path cannot contain a comma.")

    name = ask("Container name", previous.get("container_name", "ros2_env"))
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name):
        raise SetupError("Invalid container name.")

    system = platform.system()
    if system == "Darwin":
        choices = ("vnc", "xquartz", "none")
        default_display = "vnc"
    elif system == "Linux":
        choices = ("x11", "vnc", "none")
        default_display = "x11" if os.environ.get("DISPLAY") else "none"
    else:
        choices = ("none",)
        default_display = "none"
    display_mode = ask_choice("Graphical display", choices, previous.get("display_mode", default_display))

    config = {
        "image": image,
        "shared_folder": str(folder),
        "container_name": name,
        "display_mode": display_mode,
    }
    if display_mode == "x11":
        display = ask("X11 DISPLAY", previous.get("display", os.environ.get("DISPLAY", ":0")))
        if not display:
            raise SetupError("DISPLAY cannot be empty.")
        detected_auth = os.environ.get("XAUTHORITY", str(Path.home() / ".Xauthority"))
        if not Path(detected_auth).is_file():
            detected_auth = ""
        auth = ask("Xauthority file (enter '-' to skip)", previous.get("xauthority", detected_auth))
        if auth and auth != "-":
            auth_path = Path(auth).expanduser().resolve()
            if not auth_path.is_file() or "," in str(auth_path):
                raise SetupError("The Xauthority file does not exist or its path contains a comma.")
            config["xauthority"] = str(auth_path)
        config["display"] = display
    elif display_mode == "xquartz":
        config["display"] = "host.docker.internal:0"
    elif display_mode == "vnc":
        port = ask("Browser display port", str(previous.get("vnc_port", 6080)))
        if not port.isdecimal() or not 1024 <= int(port) <= 65535:
            raise SetupError("The browser display port must be between 1024 and 65535.")
        config["vnc_port"] = int(port)
    return config


def read_config():
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"Cannot read configuration: {CONFIG_FILE}. Run 'ros2.py configure'.") from exc
    required = {"image", "shared_folder", "container_name", "display_mode"}
    if not isinstance(config, dict) or not required.issubset(config):
        raise SetupError(f"Incomplete configuration: {CONFIG_FILE}. Run 'ros2.py configure'.")
    return config


def save_config(config):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(CONFIG_FILE)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Configuration saved to {CONFIG_FILE}")


def container_label(name):
    result = docker(
        "container", "inspect", "--format", "{{json .Config.Labels}}",
        name, check=False, capture=True,
    )
    if result.returncode:
        if "No such" in (result.stderr or ""):
            return None
        raise SetupError((result.stderr or "Could not inspect the container.").strip())
    try:
        labels = json.loads(result.stdout)
    except ValueError as exc:
        raise SetupError(f"Could not read the labels of container '{name}'.") from exc
    return (labels or {}).get(LABEL, "")


def require_owned_container(name):
    label = container_label(name)
    if label is not None and label != "1":
        raise SetupError(f"A container named '{name}' already exists and is not managed by ros2.py.")
    return label is not None


def require_vnc_image(image):
    result = docker(
        "run", "--rm", "--entrypoint", "test", image,
        "-x", "/usr/local/bin/ros2-vnc-start", check=False, capture=True,
    )
    if result.returncode:
        raise SetupError(
            f"Image '{image}' does not contain browser display support. "
            "Build this Dockerfile as airfarmdev/ros2_env:local and select that image "
            "with 'python3 ros2.py configure'."
        )


def run_arguments(config):
    folder = Path(config["shared_folder"])
    if folder.exists() and not folder.is_dir():
        raise SetupError(f"The shared path is not a directory: {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    mode = config["display_mode"]
    args = [
        "run", "--name", config["container_name"],
        "--label", f"{LABEL}=1", "--shm-size", "1g",
        "--mount", f"type=bind,source={folder},target={WORKSPACE}",
        "--workdir", WORKSPACE,
    ]
    if mode == "vnc":
        port = config.get("vnc_port", 6080)
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise SetupError("Invalid browser display port. Run 'ros2.py configure'.")
        args += [
            "--detach", "--publish", f"127.0.0.1:{port}:6080",
            "--env", "DISPLAY=:99", "--env", "LIBGL_ALWAYS_SOFTWARE=1",
        ]
    else:
        args += ["--interactive", "--tty"]
    if mode == "x11":
        if platform.system() != "Linux":
            raise SetupError("X11 mode requires a Linux host. Run 'ros2.py configure'.")
        if not Path("/tmp/.X11-unix").is_dir():
            raise SetupError("X11 socket missing: /tmp/.X11-unix. Start X11 or run 'ros2.py configure'.")
        args += [
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--env", f"HOME={WORKSPACE}",
            "--env", f"DISPLAY={config['display']}",
            "--mount", "type=bind,source=/tmp/.X11-unix,target=/tmp/.X11-unix,readonly",
        ]
        if config.get("xauthority"):
            auth = Path(config["xauthority"])
            if not auth.is_file():
                raise SetupError(f"Xauthority file missing: {auth}. Run 'ros2.py configure'.")
            args += [
                "--mount", f"type=bind,source={auth},target=/tmp/.Xauthority,readonly",
                "--env", "XAUTHORITY=/tmp/.Xauthority",
            ]
        if Path("/dev/dri").exists():
            args += ["--device", "/dev/dri:/dev/dri"]
    elif mode == "xquartz":
        if platform.system() != "Darwin":
            raise SetupError("XQuartz mode requires macOS. Run 'ros2.py configure'.")
        args += ["--env", f"DISPLAY={config['display']}", "--env", "LIBGL_ALWAYS_SOFTWARE=1"]
    elif mode not in ("none", "vnc"):
        raise SetupError(f"Unknown display mode: {mode}. Run 'ros2.py configure'.")
    args += [config["image"], "ros2-vnc-start" if mode == "vnc" else "bash"]
    return args


def display_note(config):
    if config["display_mode"] == "vnc":
        print(f"Open http://localhost:{config.get('vnc_port', 6080)}/vnc.html in your browser.")
    elif config["display_mode"] == "xquartz":
        print("XQuartz: enable 'Allow connections from network clients', restart XQuartz, and")
        print("run 'xhost +localhost' in an XQuartz terminal before opening GUI apps such as rviz2.")
        print("For Gazebo on macOS, choose 'vnc' with 'python3 ros2.py configure'.")
    elif config["display_mode"] == "x11" and not config.get("xauthority"):
        print("X11: if the GUI cannot connect, run this on the host:")
        print(f"  xhost +si:localuser:{os.environ.get('USER', str(os.getuid()))}")


def check_xquartz():
    if platform.system() != "Darwin":
        return
    try:
        with socket.create_connection(("127.0.0.1", 6000), timeout=1):
            return
    except OSError:
        print("Warning: XQuartz is not listening on TCP port 6000; graphical apps will fail.")
        print("In XQuartz Settings > Security, enable 'Allow connections from network clients',")
        print("fully quit and restart XQuartz, then run 'xhost +localhost' in an XQuartz terminal.")


def start_container(config):
    name = config["container_name"]
    vnc = config["display_mode"] == "vnc"
    if config["display_mode"] == "xquartz":
        check_xquartz()
    if vnc:
        require_vnc_image(config["image"])
    if require_owned_container(name):
        running = docker("container", "inspect", "--format", "{{.State.Running}}", name, capture=True)
        if running.stdout.strip() == "true":
            print(f"Container '{name}' is already running; opening a new shell.")
            if not vnc:
                docker("exec", "--interactive", "--tty", "--workdir", WORKSPACE, name, "bash")
        else:
            print(f"Restarting container '{name}'.")
            if vnc:
                docker("start", name)
            else:
                docker("start", "--attach", "--interactive", name)
    else:
        image = config["image"]
        if docker("image", "inspect", image, check=False, capture=True).returncode:
            print(f"Image {image} is missing; downloading it.")
            docker("pull", image)
        if not vnc:
            display_note(config)
        print(f"Creating and starting container '{name}'.")
        docker(*run_arguments(config))
    if vnc:
        display_note(config)
        docker("exec", "--interactive", "--tty", "--workdir", WORKSPACE, name, "bash")


def configure(previous=None):
    config = collect_config(previous)
    check_docker()
    old_name = previous["container_name"] if previous else None
    new_name = config["container_name"]
    if old_name != new_name and container_label(new_name) is not None:
        raise SetupError(f"The name '{new_name}' is already used by another container.")
    if old_name:
        require_owned_container(old_name)
    print(f"Downloading image {config['image']}...")
    try:
        docker("pull", config["image"])
    except SetupError:
        if docker("image", "inspect", config["image"], check=False, capture=True).returncode:
            raise
        print("Download failed; using the image already available locally.")

    if config["display_mode"] == "vnc":
        require_vnc_image(config["image"])

    if old_name and require_owned_container(old_name):
        print(f"Removing old container '{old_name}'.")
        docker("rm", "--force", old_name)

    save_config(config)
    start_container(config)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["-h"], ["--help"]):
        print("Usage: python3 ros2.py [configure]\nWithout arguments, open the configured container.")
        return 0
    if argv not in ([], ["configure"]):
        print("Usage: python3 ros2.py [configure]", file=sys.stderr)
        return 2
    try:
        previous = read_config() if CONFIG_FILE.exists() else None
        if argv == ["configure"] or previous is None:
            configure(previous)
        else:
            check_docker()
            start_container(previous)
    except SetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
