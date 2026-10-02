"""Small, typed user settings surface; never exposes arbitrary config or secrets."""

import asyncio
import copy
import json
import logging
import os
import tempfile
import stat
import time
from collections import deque

from .permissions import Permission
from .tools.base import ToolSpec, ValidationError
from .tools.builtin import object_schema

FIELDS = {
    'security_monitoring': ('security.monitoring', 'enabled', True, 'Security', 'Monitor local security evidence'),
    'activity_timeline': ('memory', 'activity_timeline', False, 'Privacy', 'Save local activity history (latest 2,000 events)'),
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
                           value=SettingsCenter._value(config, path, name, default),
                           choices=[dict(value=value, label=title) for value, title in choices]))
    return fields


class SettingsCenter:
    def __init__(self, core):
        self.core = core
        self.lock = asyncio.Lock()
        self.history = deque(maxlen=32)

    def snapshot(self):
        return {
            "fields": [
                dict(
                    key=key,
                    section=group,
                    label=label,
                    value=bool(self._value(self.core.config, path, name, default)),
                )
                for key, (path, name, default, group, label) in FIELDS.items()
            ],
            "choices": choice_fields(self.core.config),
            "undo_available": bool(self.history and self.history[-1]["expires"] >= time.monotonic()
                                   and not self.core.privacy.ephemeral),
            "privacy": self.core.privacy.mode,
            "routing": "Local first. Cloud requires an explicit use cloud: request in NORMAL mode.",
            "wake_active": self.core.voice.snapshot().get("wake_active", False),
            "wake_enabled": self.core.voice.wake_desired,
        }

    def clear_undo(self):
        self.history.clear()

    def _guard(self, key, value):
        if self.core.privacy.ephemeral or getattr(self.core.privacy, "changing", False):
            raise ValidationError("Persistent settings cannot be changed during private or guest sessions or privacy transitions")
        if key == "wake_enabled" and value and self.core.voice.privacy_mode:
            raise ValidationError("Leave DO NOT LISTEN before enabling wake detection")
        if key == "echo_cancel" and (
            getattr(self.core.voice, "capture_active", False)
            or getattr(self.core.voice, "speaking", False)
        ):
            raise ValidationError("Finish the current voice interaction before changing echo cancellation")

    def _check_policy(self, key, value, mode, generation):
        self._guard(key, value)
        if mode != self.core.privacy.mode or generation != getattr(self.core, "_action_generation", 0):
            raise ValidationError("Settings request was stopped or privacy changed")

    def _read(self):
        path = self.core.paths.config_file
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1_048_576:
            raise ValidationError("Settings require a regular config file smaller than 1 MiB")
        data = json.loads(path.read_text())
        identity = self._identity(before)
        if identity != self._identity(path.lstat()) or not isinstance(data, dict):
            raise ValidationError("Configuration changed during read or is not an object")
        return data, identity

    @staticmethod
    def _identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @staticmethod
    def _field(data, path, name):
        missing = []
        parts = path.split(".")
        for index, key in enumerate(parts):
            if not isinstance(data, dict):
                raise ValidationError("Setting section is not an object")
            if key not in data:
                missing.append(".".join(parts[:index + 1]))
            data = data.get(key, {})
        if not isinstance(data, dict):
            raise ValidationError("Setting section is not an object")
        return (name in data, copy.deepcopy(data.get(name)), tuple(missing))

    @classmethod
    def _value(cls, data, path, name, default):
        field = cls._field(data, path, name)
        return field[1] if field[0] else default

    @staticmethod
    def _put(data, path, name, field):
        target = data
        parents = []
        for key in path.split("."):
            parents.append((target, key))
            target = target.setdefault(key, {})
        if field[0]:
            target[name] = copy.deepcopy(field[1])
        else:
            target.pop(name, None)
            missing = field[2] if len(field) > 2 else ()
            for index in range(len(parents) - 1, -1, -1):
                parent, key = parents[index]
                if parent[key] or ".".join(path.split(".")[:index + 1]) not in missing:
                    break
                del parent[key]

    @staticmethod
    def _same(left, right):
        return left[0] == right[0] and type(left[1]) is type(right[1]) and left[1] == right[1]

    @staticmethod
    def _valid(key, value):
        if key in FIELDS:
            return type(value) is bool
        return key in CHOICES and any(type(value) is type(option) and value == option
                                      for option, _ in CHOICES[key][5])

    async def update(self, args, context):
        key, value = args["key"], args["value"]
        if not self._valid(key, value):
            raise ValidationError("Unknown setting or invalid value")
        generation = getattr(self.core, "_action_generation", 0)
        async with self.lock:
            self._guard(key, value)
            if generation != getattr(self.core, "_action_generation", 0):
                raise ValidationError("Settings request was stopped while waiting")
            data, identity = self._read()
            path, name, default, _, _ = FIELDS[key] if key in FIELDS else CHOICES[key][:5]
            before = self._field(data, path, name)
            runtime_before = self._field(self.core.config, path, name)
            result = await self._apply(key, (True, value), (True, value), data, identity)
            if not self._same(before, (True, value)) or not self._same(runtime_before, (True, value)):
                if self._valid(key, before[1] if before[0] else default) and self._valid(
                    key, runtime_before[1] if runtime_before[0] else default
                ):
                    self.history.append(dict(key=key, before=before, runtime_before=runtime_before,
                                             after=(True, value), expires=time.monotonic() + 600))
            return result

    async def undo(self, args, context):
        generation = getattr(self.core, "_action_generation", 0)
        async with self.lock:
            self._guard("", False)
            if generation != getattr(self.core, "_action_generation", 0):
                raise ValidationError("Settings request was stopped while waiting")
            while self.history and self.history[-1]["expires"] < time.monotonic():
                self.history.pop()
            if not self.history:
                return dict(verified=False, restored=False, key="", value=None, reason="No recent managed setting change")
            entry = self.history[-1]
            key = entry["key"]
            if args.get("key", key) != key:
                return dict(verified=False, restored=False, key=key, value=None, reason="Latest change belongs to another setting")
            path, name, default, _, _ = FIELDS[key] if key in FIELDS else CHOICES[key][:5]
            value = entry["before"][1] if entry["before"][0] else default
            self._guard(key, value)
            data, identity = self._read()
            if not self._same(self._field(data, path, name), entry["after"]) or not self._same(
                self._field(self.core.config, path, name), entry["after"]
            ):
                self.history.pop()
                return dict(verified=False, restored=False, key=key, value=None, reason="Setting changed outside this undo record")
            result = await self._apply(key, entry["before"], entry["runtime_before"], data, identity)
            if self.history and self.history[-1] is entry:
                self.history.pop()
            return dict(result, restored=True, reason="Saved field and runtime setting restored")

    async def _apply(self, key, disk_target, runtime_target, data, identity):
        path, name, default, _, _ = FIELDS[key] if key in FIELDS else CHOICES[key][:5]
        before = self._field(data, path, name)
        runtime_before = self._field(self.core.config, path, name)
        previous_desired = self.core.voice.wake_desired
        previous_paused = getattr(self.core.voice, "wake_paused", True)
        previous_echo = getattr(getattr(self.core.voice, "echo", None), "enabled", False)
        generation = getattr(self.core, "_action_generation", 0)
        mode = self.core.privacy.mode
        value = runtime_target[1] if runtime_target[0] else default
        self._guard(key, value)
        self._put(data, path, name, disk_target)
        self._save(data, identity)
        try:
            self._put(self.core.config, path, name, runtime_target)
            if key == "operating_mode":
                apply_mode(self.core)
            if key == "wake_enabled":
                self.core.voice.wake_desired = value
                await self.core.voice.set_wake_paused(not value)
            if key == "echo_cancel":
                await self.core.voice.set_wake_paused(True)
                self._check_policy(key, value, mode, generation)
                self.core.voice.echo.enabled = value
                await self.core.voice.echo.close()
                self._check_policy(key, value, mode, generation)
                await self.core.voice.set_wake_paused(previous_paused)
            self._check_policy(key, value, mode, generation)
            saved, _ = self._read()
            if not self._same(self._field(saved, path, name), disk_target) or not self._same(
                self._field(self.core.config, path, name), runtime_target
            ):
                raise ValidationError("Setting changed before verification")
        except BaseException:
            try:
                current, current_identity = self._read()
                if self._same(self._field(current, path, name), disk_target):
                    self._put(current, path, name, before)
                    self._save(current, current_identity)
            except Exception:
                self.core.bus.publish("system.warning", "settings", {"message": "Saved setting rollback requires review."})
            if self._same(self._field(self.core.config, path, name), runtime_target):
                self._put(self.core.config, path, name, runtime_before)
            if key == "operating_mode":
                apply_mode(self.core)
            if key in {"wake_enabled", "echo_cancel"}:
                self.core.voice.wake_desired = previous_desired
                if key == "echo_cancel":
                    self.core.voice.echo.enabled = previous_echo
                try:
                    if key == "echo_cancel":
                        await self.core.voice.echo.close()
                    paused = True if (self.core.voice.privacy_mode or self.core.privacy.mode != mode
                                      or getattr(self.core.privacy, "changing", False)) else previous_paused
                    await self.core.voice.set_wake_paused(paused)
                except Exception:
                    self.core.bus.publish("system.warning", "settings", {"message": "Voice settings restored; capture recovery is required."})
            raise
        if key == "security_monitoring" and hasattr(self.core, "security_monitor"):
            self.core.bus.publish("security.monitor_status", "security_monitor", self.core.security_monitor.snapshot())
        saved_value = disk_target[1] if disk_target[0] else default
        self.core.bus.publish("carlos.settings_changed", "settings", {"key": key, "value": saved_value})
        return dict(verified=True, key=key, value=saved_value,
                    wake_active=self.core.voice.snapshot().get("wake_active", False),
                    scope="Saved setting applied; wake capture availability is reported separately")

    def _save(self, data, expected):
        fd, temporary = tempfile.mkstemp(prefix=".carlos-settings-", dir=self.core.paths.config_dir)
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(data, out, indent=2)
                out.flush()
                os.fsync(out.fileno())
            if expected != self._identity(self.core.paths.config_file.lstat()):
                raise ValidationError("Configuration changed before save")
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
                reversible=False,
                output_schema=object_schema({
                    "fields": {"type": "array", "items": {"type": "object"}, "maxItems": 32},
                    "choices": {"type": "array", "items": {"type": "object"}, "maxItems": 32},
                    "privacy": {"type": "string"}, "routing": {"type": "string"},
                    "undo_available": {"type": "boolean"}, "wake_active": {"type": "boolean"},
                    "wake_enabled": {"type": "boolean"},
                }, ["fields", "choices", "privacy", "routing", "undo_available", "wake_active", "wake_enabled"]),
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
                output_schema=object_schema({
                    "verified": {"type": "boolean"}, "key": {"type": "string"},
                    "value": {"type": ["boolean", "string", "integer"]},
                    "wake_active": {"type": "boolean"}, "scope": {"type": "string"},
                }, ["verified", "key", "value", "wake_active", "scope"]),
                side_effects=("One managed config field and its runtime setting",),
                verification="Saved field and runtime value readback; capture availability reported separately",
            )
        )
        registry.register(ToolSpec(
            "carlos.settings.undo_last", "SETTINGS",
            "Restore the latest managed setting within ten minutes; refuses external edits and never changes arbitrary config.",
            Permission.LOW_RISK,
            object_schema({"key": {"type": "string", "enum": list(FIELDS) + list(CHOICES)}}, []),
            self.undo, offline_available=True, reversible=False,
            output_schema=object_schema({
                "verified": {"type": "boolean"}, "restored": {"type": "boolean"},
                "key": {"type": "string"}, "value": {"type": ["boolean", "string", "integer", "null"]},
                "reason": {"type": "string"}, "wake_active": {"type": "boolean"}, "scope": {"type": "string"},
            }, ["verified", "restored", "key", "value", "reason"]),
            side_effects=("Restore one previously changed managed field and runtime setting",),
            verification="Saved field and runtime readback; no overwrite after external field changes",
        ))
