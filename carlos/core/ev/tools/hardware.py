import asyncio

from .base import ToolSpec
from ..permissions import Permission


def register_hardware_tools(registry, sampler):
    async def sample(arguments, context):
        return await asyncio.to_thread(sampler.sample)

    registry.register(ToolSpec(
        'system.get_hardware_metrics', 'SYSTEM',
        'Read CPU clock policies, thermal event counters, fans and driver-provided GPU load. '
        'Missing readings are unavailable; past throttle counts do not prove current throttling.',
        Permission.SAFE,
        {'type': 'object', 'properties': {}, 'additionalProperties': False}, sample,
        read_only=True, offline_available=True,
    ))
