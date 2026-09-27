#!/usr/bin/env python3
"""Build the private Python runtime before replacing any installed files."""

from pathlib import Path
import importlib.util
import subprocess
import sys
import venv


def prepare(stage: Path, destination: Path, requirements: Path):
    venv.EnvBuilder(with_pip=True).create(stage)
    python = stage / "bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        ],
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    site = Path(
        subprocess.check_output(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True
        ).strip()
    )
    # Borrow just the native bindings, not every package on the machine.
    for name in ("dbus", "gi", "_dbus_bindings", "_dbus_glib_bindings", "cairo"):
        spec = importlib.util.find_spec(name)
        if not spec or not spec.origin:
            if name == "cairo":
                continue
            raise RuntimeError(
                f"Missing distro Python binding: {name}. Run linux-support.py install-deps."
            )
        origin = Path(spec.origin)
        source = origin.parent if spec.submodule_search_locations else origin
        target = site / source.name
        if not target.exists():
            target.symlink_to(source, target_is_directory=source.is_dir())
    # Pip's launchers and activate scripts need their final home after the swap.
    for path in [stage / "pyvenv.cfg", *(stage / "bin").iterdir()]:
        if path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        if b"\0" not in content and str(stage).encode() in content:
            path.write_bytes(content.replace(str(stage).encode(), str(destination).encode()))


if __name__ == "__main__":
    prepare(*(Path(arg).resolve() for arg in sys.argv[1:]))
