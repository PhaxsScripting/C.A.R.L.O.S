import tempfile
import unittest
from pathlib import Path
from ev.memory.store import MemoryStore
from ev.logging_utils import redact_credentials
from ev.task_journal import private_summary


class ConversationPrivacyTests(unittest.TestCase):
    def test_automatic_history_never_writes_recognizable_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"memory.db"
            store=MemoryStore(path)
            canaries=["secret_canary_123", "quoted secret canary", "key-canary-123"]
            message='password is '+canaries[0]+'; token="'+canaries[1]+'"; '+"sk-"+canaries[2]
            store.add_conversation("fixture", "user", message)
            store.add_conversation("fixture", "assistant", message)
            history=str(store.recent_conversation())
            store.close()
            raw=b"".join(p.read_bytes() for p in Path(directory).iterdir() if p.is_file())
            for canary in canaries:
                self.assertNotIn(canary,history)
                self.assertNotIn(canary.encode(),raw)
            self.assertIn("REDACTED",history)

    def test_regular_conversation_is_preserved(self):
        text="Explain password managers, token limits, and the secret level in this game."
        self.assertEqual(redact_credentials(text),text)

    def test_task_receipts_use_the_same_filter(self):
        result=private_summary({"request":"my password is receipt_canary"})
        self.assertNotIn("receipt_canary",str(result))

    def test_private_key_blocks_are_removed(self):
        text="-----BEGIN "+"PRIVATE KEY-----\nprivate-canary\n-----END "+"PRIVATE KEY-----"
        self.assertNotIn("private-canary",redact_credentials(text))

    def test_json_credentials_and_authorization_headers_are_removed(self):
        for text in ['{"password":"json_canary"}', 'Authorization: Bearer header_canary', 'Authorization=Basic basic_canary']:
            self.assertNotIn("canary",redact_credentials(text))
