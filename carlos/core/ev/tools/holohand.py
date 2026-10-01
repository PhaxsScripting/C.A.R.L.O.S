"""Bounded control of an already-running, same-user HoloHand instance."""

import asyncio
import os
import re
import socket
import stat
import struct
from pathlib import Path

from .base import ToolSpec, ValidationError
from .builtin import object_schema
from ..permissions import Permission


def socket_path():
    return (
        Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/holohand-{os.getuid()}") / "holohand.sock"
    )


async def exchange(command):
    if command not in {"--status", "--pause", "--resume"}:
        raise ValidationError("Unsupported HoloHand command")
    path = socket_path()
    parent = path.parent.lstat()
    node = path.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
        or not stat.S_ISSOCK(node.st_mode)
        or node.st_uid != os.getuid()
    ):
        raise ValidationError("HoloHand socket ownership or permissions are unsafe")
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), 1)
    try:
        peer = writer.get_extra_info("socket")
        peer_pid = None
        if hasattr(socket, "SO_PEERCRED"):
            peer_pid, uid, _ = struct.unpack(
                "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid != os.getuid():
                raise ValidationError("HoloHand peer belongs to another user")
        peer_start_ticks = None
        if peer_pid is not None:
            try:
                raw = Path(f'/proc/{peer_pid}/stat').read_text()
                peer_start_ticks = int(raw[raw.rfind(')') + 2:].split()[19])
            except (OSError, ValueError, IndexError):
                pass
        writer.write(command.encode())
        await writer.drain()

        async def receive():
            data = bytearray()
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return bytes(data)
                data.extend(chunk)
                if len(data) > 8192:
                    raise ValidationError("HoloHand status exceeds the protocol limit")

        data = await asyncio.wait_for(receive(), 2)
        text = data.decode("utf-8", errors="strict")
        state = text.split("|", 1)[0].strip()
        if state not in {"READY", "PAUSED", "CALIBRATION REQUIRED"}:
            raise ValidationError("Unrecognized HoloHand status")
        match = re.search(
            r"Hand: (visible|not detected); confidence ([0-9.]+); inference ([0-9.]+) ms; age ([0-9.]+) ms;",
            text,
        )
        tracking = {}
        demand = re.search(r"^Pipeline demand: (ACTIVE|IDLE)$", text, re.MULTILINE)
        pipeline_demand = demand[1] if demand else None
        if match:
            try:
                confidence, inference, age = map(float, match.groups()[1:])
                if 0 <= confidence <= 1 and 0 <= inference <= 60000 and 0 <= age <= 60000:
                    tracking = {
                        "hand_visible": match[1] == "visible",
                        "confidence": confidence,
                        "inference_ms": inference,
                        "age_ms": age,
                    }
            except ValueError:
                pass
        if pipeline_demand == 'IDLE':
            tracking = {'hand_visible': False}
        counters = {}
        capture = re.search(r"Capture counter: ([0-9]+)", text)
        inference = re.search(r"Inference counters: inferred ([0-9]+); skipped ([0-9]+)", text)
        if capture and inference:
            values = tuple(int(value) for value in (capture[1], inference[1], inference[2]))
            if all(0 <= value <= 2**63 - 1 for value in values):
                counters = dict(zip(('captured', 'inferred', 'skipped'), values))
        return {
            "available": True,
            "peer_pid": peer_pid,
            "peer_start_ticks": peer_start_ticks,
            "counters": counters,
            "state": state,
            "details": text,
            "tracking": tracking,
            "pipeline_demand": pipeline_demand,
            "tracking_quality": "UNVERIFIED",
            "camera_started": False,
        }
    finally:
        writer.close()
        await writer.wait_closed()


async def status(arguments, context):
    try:
        return await exchange("--status")
    except (OSError, TimeoutError):
        return {"available": False, "state": "UNAVAILABLE", "camera_started": False}


async def set_paused(arguments, context):
    desired = arguments["paused"]
    # No launch fallback: resuming here only controls an existing instance.
    await exchange("--pause" if desired else "--resume")
    observed = await exchange("--status")
    observed["verified"] = (
        observed["state"] == "PAUSED" if desired else observed["state"] == "READY"
    )
    observed["requested_paused"] = desired
    return observed


async def measure(arguments, context):
    from ..hand_metrics import sample
    return await sample(arguments.get('seconds', 5))


def register_holohand_tools(registry):
    registry.register(ToolSpec(
        'holohand.measure', 'HOLOHAND',
        'Sample metadata from an already-running HoloHand instance. Reports observed pipeline rates and sampled timing readings, not physical gesture accuracy or input latency. Never launches the app, starts a camera, enables input or records frames.',
        Permission.SAFE, object_schema({'seconds': {'type':'number','minimum':1,'maximum':30}}),
        measure, read_only=True, offline_available=True, timeout_seconds=65,
    ))
    registry.register(
        ToolSpec(
            "holohand.status",
            "HOLOHAND",
            "Read the running HoloHand gesture controller. Does not start a camera or launch the app. Tracking quality remains unverified.",
            Permission.SAFE,
            object_schema({}, []),
            status,
            read_only=True,
            offline_available=True,
            reversible=True,
            timeout_seconds=4,
        )
    )
    registry.register(
        ToolSpec(
            "holohand.set_paused",
            "HOLOHAND",
            "Pause or resume an already-running HoloHand instance and read back its state. Pause releases held gesture input; resume enables gesture control.",
            Permission.LOW_RISK,
            object_schema({"paused": {"type": "boolean"}}, ["paused"]),
            set_paused,
            offline_available=True,
            reversible=True,
            timeout_seconds=7,
            side_effects=("Changes gesture input state on the running HoloHand instance",),
        )
    )
