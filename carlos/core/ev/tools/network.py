from ..network import NetworkDiagnostics
from ..permissions import Permission
from .base import ToolSpec


def register_network_tools(registry):
    diagnostics = NetworkDiagnostics()

    async def observe(_arguments, _context):
        return await diagnostics.observe()

    registry.register(ToolSpec(
        'system.diagnose_network', 'SYSTEM',
        'Read local routes, interface state, resolver generator evidence, Tailscale daemon state '
        'and Carlos Mobile loopback health. No settings changes or Internet/DNS probes. '
        'Configured DNS and an up link do not prove Internet access.',
        Permission.SAFE, {'type': 'object', 'properties': {}, 'additionalProperties': False}, observe,
        timeout_seconds=10, read_only=True, offline_available=True, reversible=False,
        platform_requirements=('Linux network inventory',),
        output_schema={'type': 'object', 'required': ['status', 'routes', 'dns', 'interfaces',
                        'tailscale', 'mobile_backend', 'internet', 'findings', 'settings_changed'],
                       'properties': {'status': {'type': 'string', 'enum': ['OBSERVED', 'PARTIAL']},
                                      'routes': {'type': 'array', 'maxItems': 2},
                                      'dns': {'type': 'object'}, 'interfaces': {'type': 'object'},
                                      'tailscale': {'type': 'object'}, 'mobile_backend': {'type': 'object'},
                                      'internet': {'type': 'object'}, 'findings': {'type': 'array'},
                                      'settings_changed': {'type': 'boolean', 'enum': [False]}}},
        verification='Returned local configuration observations; reachability remains unverified',
    ))
