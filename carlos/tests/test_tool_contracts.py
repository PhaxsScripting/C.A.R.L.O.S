import logging
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ev.config import DEFAULT_CONFIG
from ev.events import PhaxEventBus
from ev.paths import Paths
from ev.permissions import Permission
from ev.plugins import load_enabled_plugins
from ev.service import CarlosCore
from ev.tools import ToolContext, ToolRegistry, ToolSpec
from types import SimpleNamespace


class ToolContractTests(unittest.IsolatedAsyncioTestCase):
    def registry(self, strict=False):
        config = deepcopy(DEFAULT_CONFIG)
        config['carlos']['strict_permissions'] = strict
        return ToolRegistry(ToolContext(config, PhaxEventBus(), logging.getLogger('fixture')))

    def spec(self, **kwargs):
        return ToolSpec('fixture.observe', 'FIXTURE', 'Fixture operation', Permission.SAFE,
                        {'type': 'object', 'properties': {'target': {'type': 'string', 'maxLength': 4}},
                         'required': ['target'], 'additionalProperties': False},
                        AsyncMock(return_value={'ok': True}), **kwargs)

    def test_catalogue_snapshots_cannot_change_input_or_output_execution_schemas(self):
        registry = self.registry()
        spec = self.spec(output_schema={'type': 'object', 'required': ['ok'],
                                       'properties': {'ok': {'type': 'boolean'}}})
        registry.register(spec)
        snapshot = registry.catalog()[0]
        snapshot['schema']['required'].clear()
        snapshot['schema']['properties']['target']['maxLength'] = 1000
        snapshot['output_schema']['required'].clear()
        snapshot['contract_gaps'].clear()
        fresh = registry.catalog()[0]
        self.assertEqual(fresh['schema']['required'], ['target'])
        self.assertEqual(fresh['schema']['properties']['target']['maxLength'], 4)
        self.assertEqual(fresh['output_schema']['required'], ['ok'])
        self.assertEqual(fresh['contract_gaps'], ['offline_available', 'reversible'])

    def test_unknown_contracts_stay_undeclared_without_claiming_support(self):
        spec = self.spec()
        public = spec.public()
        self.assertIsNone(public['offline_available'])
        self.assertIsNone(public['reversible'])
        self.assertEqual(public['contract_gaps'], ['offline_available', 'reversible', 'output_schema'])
        self.assertIn('already-dispatched effects', public['cancellation_scope'])
        spec.offline_available = False
        spec.reversible = True
        self.assertEqual(spec.public()['contract_gaps'], ['output_schema'])

    def test_critical_permissions_cannot_be_downgraded_by_strict_setting_or_read_only_flag(self):
        for strict in (False, True):
            for read_only in (False, True):
                for permission in Permission:
                    with self.subTest(strict=strict, read_only=read_only, permission=permission):
                        spec = self.spec(read_only=read_only)
                        spec.permission = permission
                        self.registry(strict).register(spec)
                        expected = permission in {Permission.DESTRUCTIVE, Permission.PRIVILEGED} or (
                            strict and permission == Permission.HIGH and not read_only)
                        self.assertEqual(spec.requires_confirmation, expected)

    def test_invalid_declarations_never_enter_registry(self):
        for field, value in [('timeout_seconds', True), ('timeout_seconds', float('nan')),
                ('timeout_seconds', float('inf')), ('timeout_seconds', 0), ('timeout_seconds', 3601),
                ('permission', 'SAFE'), ('executor', None), ('schema', []),
                ('output_schema', {'type': 'array'}), ('read_only', 'yes'), ('reversible', 1),
                ('offline_available', 'yes'), ('name', 'shell;command')]:
            with self.subTest(field=field, value=value):
                registry = self.registry()
                spec = self.spec()
                setattr(spec, field, value)
                with self.assertRaises(ValueError):
                    registry.register(spec)
                self.assertEqual(registry.catalog(), [])

    async def test_nonfinite_nested_result_cannot_break_json_or_report_success(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value):
                registry = self.registry()
                spec = self.spec()
                spec.executor = AsyncMock(return_value={'ok': True, 'nested': [{'sample': value}]})
                registry.register(spec)
                with self.assertRaises(ValueError):
                    await registry.execute(spec, {'target': 'test'})
                spec.executor.assert_awaited_once()

    def test_plugin_preflight_remains_atomic_for_new_declaration_guards(self):
        registry = self.registry()
        first, bad = self.spec(), self.spec()
        for spec, name in [(first, 'plugin.fixture.first'), (bad, 'plugin.fixture.bad')]:
            spec.name = name
            spec.offline_available = True
            spec.reversible = False
            spec.output_schema = {'type': 'object'}
        bad.timeout_seconds = True
        point = SimpleNamespace(name='fixture', load=lambda: lambda: [first, bad])
        with patch('ev.plugins.entry_points', return_value=[point]):
            result = load_enabled_plugins(['fixture'], registry)
        self.assertEqual(result[0]['state'], 'FAILED')
        self.assertEqual(registry.catalog(), [])

    async def test_real_core_refuses_to_execute_critical_tools_without_confirmation_in_relaxed_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core = CarlosCore(paths=Paths(*(root / name for name in ('config', 'data', 'state', 'cache', 'run'))))
            try:
                core.config['carlos']['strict_permissions'] = False
                for index, permission in enumerate((Permission.DESTRUCTIVE, Permission.PRIVILEGED)):
                    spec = self.spec(read_only=True)
                    spec.name = 'fixture.critical' + str(index)
                    spec.permission = permission
                    core.tools.register(spec)
                    result = await core.handle_request({'type': 'tool.call', 'id': 'critical' + str(index),
                        'payload': {'name': spec.name, 'arguments': {'target': 'test'}}})
                    self.assertEqual(result['status'], 'confirmation_required')
                    spec.executor.assert_not_awaited()
            finally:
                core.daily.close()
                core.memory.close()
                core.task_journal.close()
