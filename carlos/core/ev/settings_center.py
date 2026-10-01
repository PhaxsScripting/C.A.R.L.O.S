"""Small, typed user settings surface; never exposes arbitrary config or secrets."""

import asyncio
import copy
import json
import logging
import os
import tempfile

from .permissions import Permission
from .tools.base import ToolSpec, ValidationError
from .tools.builtin import object_schema

FIELDS = {
    'media_ducking': ('voice.tts', 'duck_media', False, 'Voice', 'Lower media volume while speaking'),
    "spoken_replies": ("assistant", "speak_responses", True, "Voice", "Speak replies"),
    "wake_enabled": ("voice.wake", "enabled", False, "Wake", "Listen for Carlos"),
    "echo_cancel": (
        "voice",
        "echo_cancel",
        False,
        "Voice",
        "Echo cancellation for speaker playback",
    ),
    "follow_up": (
        "voice.follow_up",
        "enabled",
        True,
        "Voice",
        "Listen for a follow-up after speaking",
    ),
    "notifications": ("notifications", "enabled", False, "Notifications", "Desktop notifications"),
    "presence_greetings": ("presence", "greetings", True, "Presence", "Welcome-back cards"),
    "hand_presence": (
        "presence",
        "hand_presence",
        True,
        "Presence",
        "Use existing HoloHand presence metadata",
    ),
    "hud_enabled": ("hud", "enabled", True, "Carlos HUD", "Voice and engineering overlay"),
    "hud_reduce_motion": ("hud", "reduce_motion", False, "Carlos HUD", "Reduce animation"),
}


CHOICES = {
    "operating_mode": ("carlos", "mode", "DAILY", "General", "Operating mode",
                       [("DAILY", "Daily"), ("DEV", "Development")]),
    "greeting_away_seconds": ("presence", "away_seconds", 300, "Presence", "Greet after being away",
                              [(60, "1 minute"), (300, "5 minutes"), (900, "15 minutes")]),
    "greeting_cooldown_seconds": ("presence", "cooldown_seconds", 1800, "Presence", "Time between greetings",
                                  [(300, "5 minutes"), (1800, "30 minutes"), (3600, "1 hour")]),
}


def apply_mode(core):
    level = logging.DEBUG if core.config.get("carlos", {}).get("mode") == "DEV" else logging.INFO
    core.logger.setLevel(level)


def choice_fields(config):
    fields = []
    for key, (path, name, default, group, label, choices) in CHOICES.items():
        fields.append(dict(key=key, section=group, label=label,
                           value=section(config, path).get(name, default),
                           choices=[dict(value=value, label=title) for value, title in choices]))
    return fields


def section(config, path):
    for key in path.split("."):
        config = config.setdefault(key, {})
    return config


class SettingsCenter:
    def __init__(self, core):
        self.core = core
        self.lock = asyncio.Lock()

    def snapshot(self):
        return {
            "fields": [
                dict(
                    key=key,
                    section=group,
                    label=label,
                    value=bool(section(self.core.config, path).get(name, default)),
                )
                for key, (path, name, default, group, label) in FIELDS.items()
            ],
            "choices": choice_fields(self.core.config),
            "privacy": self.core.privacy.mode,
            "routing": "Local first. Cloud requires an explicit use cloud: request in NORMAL mode.",
            "wake_active": self.core.voice.snapshot().get("wake_active", False),
            "wake_enabled": self.core.voice.wake_desired,
        }

    async def update(self, args, context):
        key, value = args["key"], args["value"]
        if key in FIELDS:
            valid = type(value) is bool
        elif key in CHOICES:
            valid = any(type(value) is type(option) and value == option
                        for option, _ in CHOICES[key][5])
        else:
            valid = False
        if not valid:
            raise ValidationError("Unknown setting or invalid value")
        if self.core.privacy.ephemeral:
            raise ValidationError(
                "Persistent settings cannot be changed in a private or guest session"
            )
        if key == "wake_enabled" and value and self.core.voice.privacy_mode:
            raise ValidationError("Leave DO NOT LISTEN before enabling wake detection")
        if key == "echo_cancel" and (
            getattr(self.core.voice, "capture_active", False)
            or getattr(self.core.voice, "speaking", False)
        ):
            raise ValidationError(
                "Finish the current voice interaction before changing echo cancellation"
            )
        async with self.lock:
            path, name, default, _, _ = (FIELDS[key] if key in FIELDS else CHOICES[key][:5])
            data = json.loads(self.core.paths.config_file.read_text())
            section(data, path)[name] = value
            previous = copy.deepcopy(self.core.config)
            previous_disk = json.loads(self.core.paths.config_file.read_text())
            previous_desired = self.core.voice.wake_desired
            previous_paused = getattr(self.core.voice, "wake_paused", True)
            previous_echo = getattr(getattr(self.core.voice, "echo", None), "enabled", False)
            self._save(data)
            try:
                section(self.core.config, path)[name] = value
                if key == "operating_mode":
                    apply_mode(self.core)
                if key == "wake_enabled":
                    self.core.voice.wake_desired = value
                    await self.core.voice.set_wake_paused(not value)
                if key == "echo_cancel":
                    await self.core.voice.set_wake_paused(True)
                    self.core.voice.echo.enabled = value
                    await self.core.voice.echo.close()
                    await self.core.voice.set_wake_paused(previous_paused)
            except BaseException:
                self._save(previous_disk)
                target = section(self.core.config, path)
                before = section(previous, path)
                if name in before:
                    target[name] = before[name]
                else:
                    target.pop(name, None)
                if key == "operating_mode":
                    apply_mode(self.core)
                self.core.voice.wake_desired = previous_desired
                if key in {"wake_enabled", "echo_cancel"}:
                    if key == "echo_cancel":
                        self.core.voice.echo.enabled = previous_echo
                    try:
                        if key == "echo_cancel":
                            await self.core.voice.echo.close()
                        await self.core.voice.set_wake_paused(previous_paused)
                    except Exception:
                        self.core.bus.publish(
                            "system.warning",
                            "settings",
                            {"message": "Voice settings restored; capture recovery is required."},
                        )
                raise
            self.core.bus.publish(
                "carlos.settings_changed", "settings", {"key": key, "value": value}
            )
            return {
                "verified": section(json.loads(self.core.paths.config_file.read_text()), path)[name]
                == value,
                "key": key,
                "value": value,
                "wake_active": self.core.voice.snapshot().get("wake_active", False),
                "scope": "Saved setting applied; wake capture availability is reported separately",
            }

    def _save(self, data):
        fd, temporary = tempfile.mkstemp(prefix=".carlos-settings-", dir=self.core.paths.config_dir)
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(data, out, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.core.paths.config_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def register(self, registry):
        registry.register(
            ToolSpec(
                "carlos.settings.get",
                "SETTINGS",
                "Read editable Carlos settings without secrets.",
                Permission.SAFE,
                object_schema({}, []),
                lambda a, c: self.snapshot(),
                read_only=True,
                offline_available=True,
            )
        )
        registry.register(
            ToolSpec(
                "carlos.settings.set",
                "SETTINGS",
                "Change one named Carlos setting. Enabling wake starts local microphone listening when available. Hard mute is never overridden.",
                Permission.LOW_RISK,
                object_schema(
                    {"key": {"type": "string", "enum": list(FIELDS) + list(CHOICES)}, "value": {"type": ["boolean", "string", "integer"]}},
                    ["key", "value"],
                ),
                self.update,
                offline_available=True,
                reversible=True,
            )
        )
