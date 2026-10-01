import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from ev.logging_utils import JsonFormatter, configure_logging


class LogPrivacyTests(unittest.TestCase):
    def test_real_handlers_filter_formatted_messages_fields_and_tracebacks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'core.jsonl'
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                logger = configure_logging(path)
                try:
                    logger.warning('Provider returned password=%s', 'message_canary_123',
                                   extra={'fields': {'token': 'field_canary_123'}})
                    try:
                        raise RuntimeError('Authorization: Bearer exception_canary_123')
                    except RuntimeError:
                        logger.exception('Request failed with api_key=%s', 'argument_canary_123')
                finally:
                    for handler in logger.handlers:
                        handler.flush()
                        handler.close()
                    logger.handlers.clear()
            combined = path.read_text() + stderr.getvalue()
            for canary in ('message_canary_123', 'field_canary_123',
                           'exception_canary_123', 'argument_canary_123'):
                self.assertNotIn(canary, combined)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(records[0]['fields']['token'], '[REDACTED]')
            self.assertIn('RuntimeError', records[1]['exception'])
            self.assertIn('REDACTED', stderr.getvalue())

    def test_ordinary_errors_and_source_location_remain_useful(self):
        record = logging.LogRecord('ev', logging.WARNING, 'worker.py', 42,
                                   'Worker %s exited with code %d', ('wake', 1), None)
        result = json.loads(JsonFormatter().format(record))
        self.assertEqual(result['message'], 'Worker wake exited with code 1')
