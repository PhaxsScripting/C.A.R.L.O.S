#!/usr/bin/env python3
"""Distro packages and a read-only preflight. No distro guessing from uname."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PROFILES = {
    "debian": {
        "manager": ["apt-get", "install"],
        "packages": "build-essential cmake ninja-build pkg-config python3 python3-venv python3-dev python3-dbus python3-gi qt6-base-dev qt6-declarative-dev qt6-declarative-dev-tools qt6-tools-dev-tools qt6-wayland qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-templates qml6-module-qtquick-window qml6-module-qtqml-workerscript qml6-module-qttest libgl1-mesa-dev libglib2.0-bin dbus-x11 xwayland desktop-file-utils pulseaudio-utils speech-dispatcher bubblewrap socat",
    },
    "fedora": {
        "manager": ["dnf", "install"],
        "packages": "gcc-c++ cmake ninja-build pkgconf-pkg-config python3 python3-pip python3-devel python3-dbus python3-gobject qt6-qtbase-devel qt6-qtdeclarative-devel qt6-qttools-devel qt6-qtwayland glib2 dbus-daemon xorg-x11-server-Xwayland desktop-file-utils pulseaudio-utils speech-dispatcher bubblewrap socat",
    },
    "arch": {
        "manager": ["pacman", "-S", "--needed"],
        "packages": "base-devel cmake ninja pkgconf python python-pip python-dbus python-gobject qt6-base qt6-declarative qt6-tools qt6-wayland glib2 dbus xorg-xwayland desktop-file-utils libpulse speech-dispatcher bubblewrap socat",
    },
    "opensuse": {
        "manager": ["zypper", "install"],
        "packages": "gcc-c++ cmake ninja pkgconf-pkg-config python3 python3-pip python3-devel python3-dbus-python python3-gobject qt6-base-devel qt6-declarative-devel qt6-tools-devel qt6-wayland glib2-tools dbus-1 xwayland desktop-file-utils pulseaudio-utils speech-dispatcher bubblewrap socat",
    },
    "gentoo": {
        "manager": ["emerge", "--ask", "--noreplace"],
        "packages": "sys-devel/gcc dev-build/cmake dev-build/ninja virtual/pkgconfig dev-lang/python dev-python/pip dev-python/virtualenv dev-python/dbus-python dev-python/pygobject dev-qt/qtbase:6 dev-qt/qtdeclarative:6 dev-qt/qttools:6 dev-qt/qtwayland:6 dev-libs/glib sys-apps/dbus x11-base/xwayland dev-util/desktop-file-utils media-libs/libpulse media-sound/speech-dispatcher sys-apps/bubblewrap net-misc/socat",
    },
}
ALIASES = {
    "ubuntu": "debian",
    "linuxmint": "debian",
    "pop": "debian",
    "neon": "debian",
    "endeavouros": "arch",
    "manjaro": "arch",
    "opensuse-tumbleweed": "opensuse",
    "opensuse-leap": "opensuse",
}


def os_release(path=Path("/etc/os-release")):
    result = {}
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        words = shlex.split(value, comments=True)
        if len(words) == 1:
            result[key] = words[0]
    return result


def family(release):
    for ident in [release.get("ID", ""), *release.get("ID_LIKE", "").split()]:
        candidate = ALIASES.get(ident, ident)
        if candidate in PROFILES:
            return candidate
    raise ValueError("No package profile for this distro. See docs/LINUX.md for manual setup.")


def command(profile, assume_yes=False):
    plan = PROFILES[profile]
    args = list(plan["manager"])
    if assume_yes:
        if profile == "arch":
            args += ["--noconfirm"]
        elif profile == "gentoo":
            args.remove("--ask")
        elif profile == "opensuse":
            args.insert(1, "--non-interactive")
        else:
            args += ["-y"]
    return args + plan["packages"].split()


def doctor():
    report = {
        "distro": os_release(),
        "python": sys.version.split()[0],
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
        "session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "startup": "XDG autostart and session D-Bus; no systemd requirement",
        "commands": {
            name: shutil.which(name)
            for name in ("cmake", "c++", "gdbus", "bwrap", "pactl", "parec", "spd-say")
        },
    }
    report["python_bindings"] = {}
    for name in ("dbus", "gi"):
        try:
            __import__(name)
            report["python_bindings"][name] = True
        except ImportError:
            report["python_bindings"][name] = False
    report["pet"] = (
        "KDE native overlay when LayerShellQt 6.6+ is available; otherwise portable X11/XWayland window"
    )
    report["limitations"] = (
        "KWin desktop automation is KDE-only. Portable pet comments are generic; fullscreen stacking depends on the window manager."
    )
    print(json.dumps(report, indent=2))
    return (
        0
        if sys.version_info >= (3, 11)
        and all(report["commands"].get(n) for n in ("cmake", "c++", "gdbus"))
        and all(report["python_bindings"].values())
        else 1
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("packages", "install-deps", "doctor"))
    parser.add_argument("--distro", choices=sorted(PROFILES))
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.action == "doctor":
        return doctor()
    profile = args.distro or family(os_release())
    cmd = command(profile, args.yes)
    print(shlex.join(cmd), flush=True)
    if args.action == "install-deps":
        if os.geteuid() != 0:
            sudo = shutil.which("sudo") or shutil.which("doas")
            if not sudo:
                parser.error("No sudo or doas found. Run the printed package command as root.")
            cmd.insert(0, sudo)
        subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
