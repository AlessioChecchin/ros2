import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("ros2", Path(__file__).resolve().parents[1] / "ros2.py")
ros2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ros2)


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.containers = {}
        self.images = set()
        self.unmanaged = set()
        self.fail_pull = False
        self.vnc_available = True

    def __call__(self, *args, **_kwargs):
        self.calls.append(args)
        if args[:2] == ("container", "inspect"):
            name = args[-1]
            if name not in self.containers:
                return subprocess.CompletedProcess(args, 1, "", "No such object")
            if ".State.Running" in args[3]:
                return subprocess.CompletedProcess(args, 0, "true" if self.containers[name] else "false", "")
            labels = "{}" if name in self.unmanaged else '{"io.airfarmdev.ros2-env":"1"}'
            return subprocess.CompletedProcess(args, 0, labels, "")
        if args[:2] == ("image", "inspect"):
            return subprocess.CompletedProcess(args, 0 if args[-1] in self.images else 1, "", "")
        if args[:4] == ("run", "--rm", "--entrypoint", "test"):
            return subprocess.CompletedProcess(args, 0 if self.vnc_available else 1, "", "")
        if args[0] == "pull":
            if self.fail_pull:
                raise ros2.SetupError("Pull failed")
            self.images.add(args[-1])
        elif args[0] == "run":
            self.containers[args[args.index("--name") + 1]] = "--detach" in args
        elif args[0] == "start":
            self.containers[args[-1]] = True
        elif args[0] == "rm":
            self.containers.pop(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")


class Ros2LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.config_dir = directory / "config"
        self.shared = directory / "shared folder"
        self.config = {
            "image": "airfarmdev/ros2_env:latest",
            "shared_folder": str(self.shared),
            "container_name": "ros2_env",
            "display_mode": "none",
        }
        self.fake = FakeDocker()
        patchers = (
            patch.object(ros2, "CONFIG_DIR", self.config_dir),
            patch.object(ros2, "CONFIG_FILE", self.config_dir / "config.json"),
            patch.object(ros2, "docker", self.fake),
            patch.object(ros2, "check_docker"),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_first_run_pulls_saves_and_mounts_shared_folder(self):
        with patch.object(ros2, "collect_config", return_value=self.config):
            self.assertEqual(ros2.main([]), 0)
        self.assertEqual(ros2.read_config(), self.config)
        self.assertTrue(self.shared.is_dir())
        self.assertIn(("pull", self.config["image"]), self.fake.calls)
        run = next(args for args in self.fake.calls if args[0] == "run" and "--name" in args)
        self.assertIn(f"type=bind,source={self.shared},target=/workspace", run)

    def test_next_run_restarts_existing_container(self):
        ros2.save_config(self.config)
        self.fake.containers["ros2_env"] = False
        self.assertEqual(ros2.main([]), 0)
        self.assertIn(("start", "--attach", "--interactive", "ros2_env"), self.fake.calls)
        self.assertFalse(any(args[0] == "run" for args in self.fake.calls))

    def test_first_run_uses_local_image_when_pull_fails(self):
        self.fake.images.add(self.config["image"])
        self.fake.fail_pull = True
        with patch.object(ros2, "collect_config", return_value=self.config):
            self.assertEqual(ros2.main([]), 0)
        self.assertTrue(any(args[0] == "run" for args in self.fake.calls))

    def test_configure_replaces_container_but_preserves_shared_files(self):
        ros2.save_config(self.config)
        self.fake.containers["ros2_env"] = False
        self.fake.images.add(self.config["image"])
        self.shared.mkdir()
        sample = self.shared / "input.txt"
        sample.write_text("keep", encoding="utf-8")
        changed = {**self.config, "container_name": "ros2_new"}
        with patch.object(ros2, "collect_config", return_value=changed):
            self.assertEqual(ros2.main(["configure"]), 0)
        self.assertIn(("rm", "--force", "ros2_env"), self.fake.calls)
        self.assertEqual(ros2.read_config(), changed)
        self.assertEqual(sample.read_text(encoding="utf-8"), "keep")

    def test_does_not_start_or_remove_an_unmanaged_container(self):
        ros2.save_config(self.config)
        self.fake.containers["ros2_env"] = False
        self.fake.unmanaged.add("ros2_env")
        self.assertEqual(ros2.main([]), 1)
        self.assertFalse(any(args[0] in {"start", "rm", "run"} for args in self.fake.calls))

    def test_xquartz_warning_when_tcp_display_is_unavailable(self):
        output = io.StringIO()
        with patch.object(ros2.platform, "system", return_value="Darwin"):
            with patch.object(ros2.socket, "create_connection", side_effect=ConnectionRefusedError):
                with redirect_stdout(output):
                    ros2.check_xquartz()
        self.assertIn("TCP port 6000", output.getvalue())
        self.assertIn("fully quit and restart XQuartz", output.getvalue())

    def test_vnc_mode_opens_browser_display_and_shell(self):
        config = {**self.config, "display_mode": "vnc", "vnc_port": 6081}
        with patch.object(ros2, "collect_config", return_value=config):
            self.assertEqual(ros2.main([]), 0)
        run = next(args for args in self.fake.calls if args[0] == "run" and "--name" in args)
        self.assertIn("--detach", run)
        self.assertIn("127.0.0.1:6081:6080", run)
        self.assertIn("DISPLAY=:99", run)
        self.assertEqual(run[-1], "ros2-vnc-start")
        self.assertEqual(self.fake.calls[-1][0], "exec")

    def test_vnc_mode_restarts_in_background(self):
        config = {**self.config, "display_mode": "vnc", "vnc_port": 6080}
        ros2.save_config(config)
        self.fake.containers["ros2_env"] = False
        self.assertEqual(ros2.main([]), 0)
        self.assertIn(("start", "ros2_env"), self.fake.calls)
        self.assertEqual(self.fake.calls[-1][0], "exec")

    def test_reconfigure_rejects_image_without_vnc_before_removing_container(self):
        ros2.save_config(self.config)
        self.fake.containers["ros2_env"] = False
        self.fake.vnc_available = False
        changed = {**self.config, "display_mode": "vnc", "vnc_port": 6080}
        with patch.object(ros2, "collect_config", return_value=changed):
            self.assertEqual(ros2.main(["configure"]), 1)
        self.assertIn("ros2_env", self.fake.containers)
        self.assertFalse(any(args[0] == "rm" for args in self.fake.calls))
        self.assertEqual(ros2.read_config(), self.config)


if __name__ == "__main__":
    unittest.main()
