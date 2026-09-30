import re
import sys
from importlib.metadata import PackageNotFoundError, version

PACKAGES = {"python", "numpy", "onnxruntime", "sherpa-onnx", "piper-tts"}


def safe_versions(values):
    if not isinstance(values, dict):
        return {}
    return {name: value for name, value in values.items()
            if name in PACKAGES and isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", value)}


def runtime_versions(*packages):
    result = {"python": ".".join(map(str, sys.version_info[:3]))}
    for package in packages:
        if package not in PACKAGES:
            continue
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "NOT_INSTALLED"
    return safe_versions(result)


def worker_report(worker, process):
    return {"running": process is not None and process.returncode is None,
            "versions": safe_versions(getattr(worker, "worker_versions", {})),
            "source": "last successful worker startup"}
