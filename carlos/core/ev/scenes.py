"""Named assistant scenes; explicit plans reuse the verified routine executor."""

import re
import time
from copy import deepcopy
from .commands import request_text
from .permissions import Permission
from .tools.base import ToolSpec
from .tools.builtin import object_schema

DEFAULTS = {
    "homecoming": {
        "description": "Return to the desktop and inspect Carlos status",
        "hud": "CARLOS",
        "quiet": False,
    },
    "coding": {"description": "Project and build attention", "hud": "PROJECT", "quiet": False},
    "gaming": {
        "description": "Keep background assistant notifications quiet",
        "hud": "SYSTEM",
        "quiet": True,
    },
    "night": {"description": "Quiet assistant interaction", "hud": "CARLOS", "quiet": True},
    "leaving": {
        "description": "Remote status and quiet assistant notifications",
        "hud": "REMOTE",
        "quiet": True,
    },
    "focus": {
        "description": "Keep background success messages out of the conversation",
        "hud": "PROJECT",
        "quiet": True,
    },
    "movie": {
        "description": "Require explicit addressing during media",
        "hud": "MEDIA",
        "quiet": True,
    },
    "remote": {"description": "Remote connection context", "hud": "REMOTE", "quiet": False},
}


class SceneEngine:
    def __init__(self, core):
        self.core = core
        self.current = {"name": None, "state": "IDLE"}

    def definitions(self):
        custom = self.core.daily.records("carlos_scene")
        return {name: {**value, "commands": []} for name, value in DEFAULTS.items()} | custom

    async def prepare(self, name, correlation):
        definition = self.definitions()[name]
        planner = self.core.planner
        commands = definition.get("commands", [])
        command_plan = None
        if commands:
            planner.saved_routines['carlos-scene-active'] = commands
            command_plan = planner.try_plan('run routine carlos-scene-active', correlation)
            if command_plan is None:
                raise ValueError('Scene contains a command I cannot safely plan. Nothing ran.')
        workspace = definition.get('workspace', '')
        if not workspace:
            return command_plan, {}
        observed = await self.core._request_model_tool({
            'name': 'workspaces.restore_plan', 'arguments': {'name': workspace}}, correlation)
        layout = observed.get('result', {})
        if observed.get('status') != 'completed' or not layout.get('plan'):
            raise ValueError('The saved workspace cannot be restored. No scene actions ran.')
        from .tools.plans import build_plan
        plan = build_plan(layout['plan'], planner, correlation)
        if command_plan:
            if len(plan.steps) + len(command_plan.steps) > 32:
                raise ValueError('Scene exceeds 32 actions. Nothing ran.')
            mapping = {step.id: 'scene_' + step.id for step in command_plan.steps}

            def remap(value):
                if isinstance(value, dict):
                    if set(value) == {'$ref'}:
                        head, tail = value['$ref'].split('.', 1)
                        return {'$ref': mapping[head] + '.' + tail}
                    return {key: remap(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [remap(item) for item in value]
                return value

            previous = plan.steps[-1].id
            for original in command_plan.steps:
                step = deepcopy(original)
                step.id = mapping[step.id]
                step.arguments = remap(step.arguments)
                step.dependencies = [mapping[item] for item in step.dependencies] or [previous]
                plan.steps.append(step)
                previous = step.id
            plan.goal_conditions += remap(command_plan.goal_conditions)
        plan.request = f'Activate {name} scene'
        plan.goal = f'Restore saved workspace {workspace} and activate {name}'
        return plan, layout

    def resolve(self, text):
        request = self.parse_request(text)
        return request[0] if request else None

    def parse_request(self, text):
        preview = re.fullmatch(r'\s*(?:please\s+)?(?:preview|dry[- ]run|just show me)\s+(.+)', text, re.I)
        candidate = request_text(preview[1] if preview else text)
        if not candidate:
            return None
        if (preview and not re.match(r'^(?:activate|start|switch to|enter)\b', candidate, re.I)
                and re.fullmatch(r'[\w -]{1,50}\s+(?:scene|mode)[.!?]*', candidate, re.I)):
            candidate = 'activate ' + candidate
        clean = candidate
        match = re.fullmatch(
            r"(?:activate|start|switch to|enter)\s+(?:the\s+)?([\w -]{1,50})\s+(?:scene|mode)[.!?]*",
            clean,
            re.I,
        )
        if match:
            return match[1].strip().casefold(), bool(preview)
        # This grammar sees only explicit user commands after the wake/attention gate.
        if re.fullmatch(r"wake up[, ]+daddy's home[.!?]*", clean, re.I):
            return "homecoming", bool(preview)
        return None

    def activate(self, name, *, running=False):
        definition = self.definitions().get(name)
        if definition is None:
            raise ValueError("Unknown scene")
        self.current = {
            "name": name,
            "state": "RUNNING" if running else "ACTIVE",
            "activated_at": time.time(),
            "hud": definition["hud"],
            "quiet": definition["quiet"],
        }
        self.core.bus.publish("carlos.scene_changed", "scenes", dict(self.current))
        return definition

    def finish(self, activation, status):
        # An older cancelled run must not overwrite a newer selected scene.
        if self.current is not activation:
            return
        self.current["state"] = {"completed": "ACTIVE", "cancelled": "CANCELLED"}.get(
            status, "FAILED"
        )
        self.current["finished_at"] = time.time()
        self.core.bus.publish("carlos.scene_changed", "scenes", dict(self.current))

    def register(self, registry):
        name = {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "[A-Za-z0-9 _-]+"}
        registry.register(
            ToolSpec(
                "carlos.scenes.list",
                "SCENES",
                "List assistant scenes and their explicit command plans; separate from wallpaper themes.",
                Permission.SAFE,
                object_schema({}, []),
                lambda a, c: {"scenes": self.definitions(), "active": dict(self.current)},
                read_only=True,
            )
        )

        def save(a, c):
            workspace = a.get('workspace', '').strip().casefold()
            if workspace and workspace not in c.daily.records('workspace_layout'):
                raise ValueError('Choose an existing saved workspace')
            for command in a["commands"]:
                if request_text(command) is None or self.resolve(command):
                    raise ValueError(
                        "Scene commands must be explicit and cannot recursively activate scenes"
                    )
            return c.daily.save(
                "carlos_scene",
                a["name"].strip().casefold(),
                {
                    "description": a.get("description", "Custom scene"),
                    "commands": a["commands"],
                    "hud": a["hud"],
                    "quiet": a["quiet"],
                    "workspace": workspace,
                },
            )

        registry.register(
            ToolSpec(
                "carlos.scenes.save",
                "SCENES",
                "Save a named assistant scene; does not execute commands. Running still requires normal permissions and verification.",
                Permission.LOW_RISK,
                object_schema(
                    {
                        "name": name,
                        "description": {"type": "string", "maxLength": 200},
                        "commands": {
                            "type": "array",
                            "maxItems": 8,
                            "items": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "hud": {
                            "type": "string",
                            "enum": ["CARLOS", "PROJECT", "SYSTEM", "MEDIA", "REMOTE"],
                        },
                        "quiet": {"type": "boolean"},
                        "workspace": {"type": "string", "maxLength": 100},
                    },
                    ["name", "commands", "hud", "quiet"],
                ),
                save,
            )
        )
