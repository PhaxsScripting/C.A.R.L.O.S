import logging
import unittest
from unittest.mock import AsyncMock

from ev.events import EventBus
from ev.permissions import Permission
from ev.tools.base import ToolContext, ToolRegistry, ToolSpec, ValidationError, validate_schema


class SchemaVariantTests(unittest.IsolatedAsyncioTestCase):
    variants = {'type': 'object', 'oneOf': [
        {'type': 'object', 'properties': {'value': {'type': 'number'}},
         'required': ['value'], 'additionalProperties': False},
        {'type': 'object', 'properties': {'error': {'type': 'string'}},
         'required': ['error'], 'additionalProperties': False},
    ]}

    def test_success_and_failure_shapes_validate_independently(self):
        for result in ({'value': 12.5}, {'error': 'Unavailable'}):
            self.assertIs(validate_schema(result, self.variants), result)

    def test_missing_mixed_or_unknown_fields_cannot_match_a_variant(self):
        for result in ({}, {'value': 1, 'error': 'Mixed'}, {'value': 1, 'unexpected': True}):
            with self.subTest(result=result), self.assertRaises(ValidationError):
                validate_schema(result, self.variants)

    def test_ambiguous_variants_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_schema({}, {'type': 'object', 'oneOf': [{'type': 'object'}, {'type': 'object'}]})

    def test_nested_nonfinite_and_boolean_values_are_not_numbers(self):
        schema = {'type': 'array', 'items': self.variants}
        for value in (float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_schema([{'value': value}], schema)

    def test_outer_constraints_apply_after_variant_selection(self):
        schema = {'type': 'integer', 'minimum': 0,
                  'oneOf': [{'type': 'integer'}, {'type': 'string'}]}
        self.assertEqual(validate_schema(2, schema), 2)
        with self.assertRaises(ValidationError):
            validate_schema(-1, schema)

    def test_invalid_variant_declarations_fail_instead_of_accepting_a_value(self):
        for choices in (None, {}, [], [None], ['object']):
            with self.subTest(choices=choices), self.assertRaises(ValidationError):
                validate_schema({}, {'type': 'object', 'oneOf': choices})

    async def test_registry_rejects_mixed_output_before_reporting_execution_success(self):
        registry = ToolRegistry(ToolContext({'security': {'max_tool_output_bytes': 65536}},
                                            EventBus(), logging.getLogger('variant-fixture')))
        executor = AsyncMock(return_value={'value': 1, 'error': 'Mixed'})
        spec = ToolSpec('fixture.variant', 'FIXTURE', 'Private output fixture', Permission.SAFE,
                        {'type': 'object'}, executor, output_schema=self.variants)
        registry.register(spec)
        with self.assertRaises(ValidationError):
            await registry.execute(spec, {})
        executor.assert_awaited_once()
