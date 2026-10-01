from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "audio",
    "content",
    "file_content",
    "screen_image",
    "text",
    "old_text",
    "new_text",
}


def redact_credentials(value: str) -> str:
    value = re.sub(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                   "[REDACTED_PRIVATE_KEY]", value, flags=re.S)
    value = re.sub(r"\b(?:nvapi-|sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{8,}", "[REDACTED_KEY]", value)
    value = re.sub(r"(?i)\b(?:password|passwd|api[_ -]?key|authorization|cookie|token|secret)[\"']?\s*(?::|=|\bis\b)\s*(?:(?:Bearer|Basic)\s+[^\s,;]+|\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)",
                   "[REDACTED_CREDENTIAL]", value)
    return value


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = redact_credentials(value)
    if isinstance(value, str) and len(value) > 4096:
        return value[:4096] + "...[TRUNCATED]"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_credentials(record.getMessage()),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload["fields"] = redact(fields)
        if record.exc_info:
            payload["exception"] = redact_credentials(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class RedactedFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_credentials(super().format(record))


def configure_logging(log_file: Path, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("ev")
    logger.disabled = False
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    file_handler = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=4, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    stderr_handler.setFormatter(RedactedFormatter("Carlos %(levelname)s: %(message)s"))
    logger.addHandler(stderr_handler)
    return logger
