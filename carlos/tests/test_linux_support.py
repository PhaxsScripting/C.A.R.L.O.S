import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("linux_support", SCRIPTS / "linux-support.py")
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class LinuxSupportTests(unittest.TestCase):
    def test_distro_derivatives_use_their_package_family(self):
        for distro, expected in {
            "ubuntu": "debian",
            "linuxmint": "debian",
            "debian": "debian",
            "fedora": "fedora",
            "endeavouros": "arch",
            "arch": "arch",
            "opensuse-tumbleweed": "opensuse",
            "opensuse-leap": "opensuse",
            "gentoo": "gentoo",
        }.items():
            with self.subTest(distro=distro):
                self.assertEqual(support.family({"ID": distro}), expected)
        self.assertEqual(support.family({"ID": "derivative", "ID_LIKE": "ubuntu debian"}), "debian")
        with self.assertRaises(ValueError):
            support.family({"ID": "unknown"})

    def test_os_release_is_data_not_shell_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "os-release"
            path.write_text(
                'ID=linuxmint\nID_LIKE="ubuntu debian"\nPRETTY_NAME="$(touch should-not-exist)"\n'
            )
            data = support.os_release(path)
            self.assertEqual(data["PRETTY_NAME"], "$(touch should-not-exist)")
            self.assertEqual(support.family(data), "debian")

    def test_package_preview_never_executes_commands(self):
        with patch.object(
            sys, "argv", ["linux-support", "packages", "--distro", "fedora"]
        ), patch.object(support.subprocess, "run") as run:
            self.assertEqual(support.main(), 0)
            run.assert_not_called()

    def test_package_managers_keep_confirmation_by_default(self):
        for name in support.PROFILES:
            args = support.command(name)
            self.assertNotIn("-y", args)
            self.assertNotIn("--noconfirm", args)
            self.assertNotIn("--non-interactive", args)
            self.assertTrue(all(isinstance(arg, str) for arg in args))
        self.assertIn("--ask", support.command("gentoo"))

    def test_launchers_use_the_private_runtime_even_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="carlos home ") as directory:
            data = Path(directory) / "data dir"
            python = data / "ev/app/venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            python.chmod(0o755)
            env = {
                **os.environ,
                "HOME": directory,
                "XDG_DATA_HOME": str(data),
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
            }
            for script, module in [("ev-core", "ev"), ("evctl", "ev.cli")]:
                result = subprocess.run(
                    ["sh", str(SCRIPTS / script), "--help"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(result.stdout.splitlines(), ["-m", module, "--help"])

    def test_startup_is_not_restricted_to_kde(self):
        for name in ("ev-core.desktop.in", "ev-shell.desktop.in"):
            text = (SCRIPTS.parent / "packaging" / name).read_text()
            self.assertNotIn("OnlyShowIn=KDE", text)
