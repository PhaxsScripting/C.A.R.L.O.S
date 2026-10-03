"""Local perception and guarded visual actions."""

from ..permissions import Permission
from .base import ToolSpec
from .builtin import object_schema


def register_local_vision_tools(registry):
    def require_local(context):
        # Tool outputs are returned to the active reasoning model. Prevent a
        # cloud model from indirectly receiving private screen descriptions.
        active = context.config.get("providers", {}).get("active", "offline")
        from ..execution import local_visual_execution

        if active not in {"offline", "local_hybrid", "local_agent"} and not local_visual_execution.get():
            raise ValueError(
                "Private visual descriptions require a local active brain; cloud tool-result upload is blocked"
            )

    async def describe(arguments, context):
        require_local(context)
        return await context.vision.describe(arguments["capture_id"], arguments["question"])

    async def inspect_window(arguments, context):
        require_local(context)
        return await context.vision.inspect_window(arguments["window_id"], arguments["question"])

    async def candidates(arguments, context):
        require_local(context)
        return await context.vision.visual_targets.prepare(
            arguments["window_id"], arguments["text"], arguments.get("minimum_score", .8)
        )

    async def review(arguments, context):
        require_local(context)
        return await context.vision.visual_targets.review(arguments["candidate_id"])

    async def click(arguments, context):
        require_local(context)

        async def observe(payload, correlation):
            spec, validated = registry.validate(payload["name"], payload["arguments"])
            if not spec.read_only and spec.name != "desktop.world":
                raise ValueError("Visual result checks must use read-only observations")
            return {"status": "completed", "result": await registry.execute(spec, validated)}

        return await context.vision.visual_targets.click(arguments, observe)

    async def click_text(arguments, context):
        if not arguments.get("candidate_id"):
            raise ValueError("Visual text clicks must go through Carlos's preview confirmation")
        require_local(context)
        input_controller = context.vision.desktop.input
        if not input_controller.status().get("connected"):
            await input_controller.connect()
        return await click({"candidate_id": arguments["candidate_id"], "expected": arguments["expected"]}, context)

    def normalize_click(arguments, context):
        require_local(context)
        return context.vision.visual_targets.validate_click(arguments)

    candidate_properties = {
        "candidate_id": {"type": "string", "pattern": "[0-9a-f]{32}"},
        "capture_id": {"type": "string", "pattern": "[0-9a-f]{32}"},
        "preview_id": {"type": "string", "pattern": "[0-9a-f]{32}"},
        "preview_path": {"type": "string", "minLength": 1},
        "text": {"type": "string", "minLength": 1, "maxLength": 4096},
        "confidence": {"type": "number", "minimum": .8, "maximum": 1},
        "window_id": {"type": "string", "minLength": 1},
        "window_title": {"type": ["string", "null"]},
        "expires_in_seconds": {"type": "integer", "minimum": 0, "maximum": 60},
    }
    candidate_schema = object_schema(candidate_properties, list(candidate_properties))
    click_result_schema = object_schema({
            "candidate_id": candidate_properties["candidate_id"], "window_id": {"type": "string"},
            "input_sent": {"type": "boolean"}, "verified": {"type": "boolean"},
            "delivery_unknown": {"type": "boolean"}, "replay_allowed": {"type": "boolean", "enum": [False]},
            "verification_scope": {"type": "string", "enum": ["declared_native_transition"]},
            "before": {"type": "object"}, "after": {"type": "object"}, "message": {"type": "string"},
        }, ["candidate_id", "window_id", "input_sent", "verified", "replay_allowed", "verification_scope", "message"])
    registry.register(ToolSpec(
        "vision.click_text", "VISION",
        "Find exact visible text in one window, show its highlighted confirmation preview, then send one "
        "guarded left click and check declared native changes. Duplicate labels require an explicit ordinal "
        "in top-to-bottom, then left-to-right order. Carlos fills candidate_id when preparing confirmation.",
        Permission.HIGH,
        object_schema({
            "window_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "text": {"type": "string", "minLength": 1, "maxLength": 500},
            "ordinal": {"type": "integer", "minimum": 1, "maximum": 20},
            "expected": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "object"}},
            "candidate_id": candidate_properties["candidate_id"],
        }, ["window_id", "text", "expected"]),
        click_text, normalizer=normalize_click,
        requires_confirmation=True, offline_available=True, reversible=False, timeout_seconds=90,
        output_schema=click_result_schema,
        verification="Fresh image pixels and native identity before input; declared native transition after one click",
        platform_requirements=("Private capture/OCR", "Consented desktop input"),
        side_effects=("prepares bounded private preview before confirmation", "may request native input consent",
                      "focuses exact window", "sends one left click"),
        expected_latency_ms=5000,
    ))
    registry.register(ToolSpec(
        "vision.candidate.click", "VISION",
        "Request one left click on an existing highlighted candidate. Mandatory local confirmation shows "
        "its preview; execution consumes the candidate, rechecks native identity and current image pixels, "
        "then checks 1-3 declared native changes in that same window. No retry after uncertain delivery.",
        Permission.HIGH,
        object_schema({
            "candidate_id": candidate_properties["candidate_id"],
            "expected": {"type": "array", "minItems": 1, "maxItems": 3,
                         "items": {"type": "object"}},
        }, ["candidate_id", "expected"]),
        click, normalizer=normalize_click,
        confirmation_reason="Review the highlighted candidate and expected native change before one click.",
        requires_confirmation=True, offline_available=True, reversible=False, timeout_seconds=30,
        output_schema=click_result_schema,
        verification="Native predicates must be observable and unmet before input, then satisfied after input",
        platform_requirements=("Native window identity", "Private capture", "Consented desktop input"),
        side_effects=("focuses exact window", "sends one left click", "consumes related visual proposals"),
        expected_latency_ms=1500,
    ))
    registry.register(ToolSpec(
        "vision.candidates", "VISION",
        "Find exact visible text in one native window using local OCR and return private highlighted previews. "
        "Labels remain uncertain historical inference, never permission to click. Duplicate labels stay separate. "
        "Requires a local active brain, exact native identity and one-to-one image geometry.",
        Permission.SENSITIVE,
        object_schema({
            "window_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "text": {"type": "string", "minLength": 1, "maxLength": 500},
            "minimum_score": {"type": "number", "minimum": .8, "maximum": 1},
        }, ["window_id", "text"]),
        candidates,
        output_schema=object_schema({
            "candidates": {"type": "array", "maxItems": 20, "items": candidate_schema},
            "ambiguous": {"type": "boolean"}, "matched": {"type": "integer", "minimum": 0, "maximum": 20},
            "coordinate_actions_allowed": {"type": "boolean", "enum": [False]},
            "scene_accuracy_verified": {"type": "boolean", "enum": [False]},
            "local_only": {"type": "boolean", "enum": [True]},
            "uploaded": {"type": "boolean", "enum": [False]}, "message": {"type": "string"},
        }, ["candidates", "ambiguous", "matched", "coordinate_actions_allowed", "scene_accuracy_verified",
            "local_only", "uploaded", "message"]),
        offline_available=True, reversible=True, timeout_seconds=55,
        platform_requirements=("Exact native window metadata", "Private PNG capture", "Local OCR"),
        verification="Image geometry and process/window/output identity; label meaning remains unverified",
        side_effects=("briefly focuses exact window for capture", "retains bounded private previews for at most sixty seconds"),
        expected_latency_ms=3500,
    ))
    registry.register(ToolSpec(
        "vision.candidate.review", "VISION",
        "Read one existing highlighted proposal after checking exact process/window/output identity, expiry "
        "and source/preview hashes. This does not inspect current window contents or send input.",
        Permission.SENSITIVE,
        object_schema({"candidate_id": candidate_properties["candidate_id"]}, ["candidate_id"]),
        review, output_schema=candidate_schema, read_only=True,
        offline_available=True, reversible=False, timeout_seconds=8,
        verification="Exact native metadata and private file hashes; historical image only",
        side_effects=(), expected_latency_ms=100,
    ))

    registry.register(
        ToolSpec(
            "vision.describe",
            "VISION",
            "Ask the local vision model about one existing private capture. Returns uncertain visual inference, not verified facts or permission to click. No screenshots/descriptions are sent to cloud models. Requires an active local brain and configured multimodal weights.",
            Permission.SENSITIVE,
            object_schema(
                {
                    "capture_id": {"type": "string", "pattern": "[0-9a-f]{32}"},
                    "question": {"type": "string", "minLength": 1, "maxLength": 1000},
                },
                ["capture_id", "question"],
            ),
            describe,
            read_only=True,
            timeout_seconds=100,
            verification="Local model inference only; scene accuracy not independently verified",
            side_effects=("temporarily loads bounded local multimodal runtime",),
            expected_latency_ms=30000,
        )
    )

    registry.register(
        ToolSpec(
            "vision.inspect_window",
            "VISION",
            "Inspect one exact KWin window using a temporary local screenshot and multimodal model; delete the capture afterward. Rechecks window PID/title/geometry after inference and marks stale changes. This is uncertain historical image interpretation, not coordinates or permission to click. Requires local active brain.",
            Permission.SENSITIVE,
            object_schema(
                {
                    "window_id": {"type": "string", "minLength": 1, "maxLength": 100},
                    "question": {"type": "string", "minLength": 1, "maxLength": 1000},
                },
                ["window_id", "question"],
            ),
            inspect_window,
            timeout_seconds=115,
            verification="Visual inference only; exact target identity rechecked, scene truth unverified",
            side_effects=(
                "temporarily focuses exact window for capture",
                "creates and deletes one private screenshot",
                "temporarily loads local model",
            ),
            expected_latency_ms=30000,
        )
    )
