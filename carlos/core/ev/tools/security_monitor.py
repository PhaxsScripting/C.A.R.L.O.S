from .base import ToolSpec
from ..permissions import Permission


def register_security_monitor(registry, monitor):
    registry.register(ToolSpec(
        "security.monitor", "SECURITY",
        "Read the background security monitor's latest source status, counts and coverage limits.",
        Permission.SAFE, {"type": "object", "properties": {}, "additionalProperties": False},
        lambda arguments, context: monitor.snapshot(), read_only=True, offline_available=True,
    ))
