from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..events import Event, PhaxEventBus
from .protocol import PROTOCOL_VERSION, ProtocolError, decode_message, encode_message

RequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

# Let status and stop requests through while a command is busy.
# Normal commands and tools still run in order on each connection.
RESPONSIVE_REQUESTS = frozenset(
    {
        "carlos.status",
        "carlos.capabilities",
        "carlos.support",
        "carlos.privacy.set",
        "health",
        "snapshot",
        "panel.state",
        "panel.summary",
        "voice.diagnostics",
        "events.history",
        "latency.report",
        "plan.list",
        "tool.catalog",
        "capability.query",
        "self.diagnostics",
        "confirmation.list",
        "agent.tasks.list",
        "agent.tasks.get",
        "agent.tasks.steer",
        "coding.status",
        "coding.result",
        "tts.stop",
        "voice.privacy.set",
        "wake.pause.set",
        "plan.cancel",
        "core.stop",
    }
)


QUEUED_ACTIONS = frozenset({
    "command.submit", "tool.call", "confirmation.respond", "coding.propose",
    "tts.speak", "voice.capture.start", "voice.capture.stop",
    "voice.microphone_test.start", "voice.transcription_test.start",
    "voice.full_test.start", "wake.test.start", "carlos.voice.synthesize",
    "carlos.voice.transcribe",
})


def _is_action_stop_request(request: dict[str, Any]) -> bool:
    payload = request.get("payload", {})
    if request.get("type") == "command.submit" and isinstance(payload, dict):
        text = payload.get("text")
        if not isinstance(text, str):
            return False
        from ..voice.normalization import is_conversation_stop

        return is_conversation_stop(text) or bool(re.fullmatch(
            r"\s*(?:please\s+)?(?:stop|cancel|abort)\s+(?:everything|all(?:\s+(?:tasks|actions|commands))?)[.!?]*\s*",
            text,
            re.I,
        ))
    return False


def _is_responsive_request(request: dict[str, Any]) -> bool:
    if request.get("type") in RESPONSIVE_REQUESTS or _is_action_stop_request(request):
        return True
    payload = request.get("payload", {})
    if request.get("type") == "command.submit" and isinstance(payload, dict):
        from ..engineering_queries import is_engineering_status_query

        if is_engineering_status_query(payload.get("text")):
            return True
    return (
        request.get("type") == "tool.call"
        and isinstance(payload, dict)
        and payload.get("name") in ("desktop.input.disconnect", "development.coding_agent_status",
                                    "development.coding_agent_result")
    )


class IpcServer:
    def __init__(
        self,
        socket_path: Path,
        bus: PhaxEventBus,
        handler: RequestHandler,
        logger: logging.Logger,
        max_message_bytes: int = 1_048_576,
    ) -> None:
        self.socket_path = socket_path
        self.bus = bus
        self.handler = handler
        self.logger = logger
        self.max_message_bytes = max_message_bytes
        self.server: asyncio.AbstractServer | None = None
        self.clients = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self._write_locks: dict[asyncio.StreamWriter, asyncio.Lock] = {}
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._request_queues: dict[asyncio.StreamWriter, asyncio.Queue] = {}

    async def start(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()
        # Bind and restrict synchronously before yielding or accepting clients.
        # start_unix_server(path=...) can publish the path before it returns,
        # leaving observers racing a later chmod and briefly wider permissions.
        bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            bound.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            bound.setblocking(False)
            self.server = await asyncio.start_unix_server(
                self._client_connected,
                sock=bound,
                limit=self.max_message_bytes + 1,
            )
        except BaseException:
            bound.close()
            raise

    async def stop(self) -> None:
        server = self.server
        if server is not None:
            server.close()
        writers = tuple(self._writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in writers), return_exceptions=True
            )
        client_tasks = tuple(self._client_tasks)
        for task in client_tasks:
            task.cancel()
        if client_tasks:
            await asyncio.gather(*client_tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
            self.server = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _peer_is_current_user(self, writer: asyncio.StreamWriter) -> bool:
        from ..platform import peer_uid

        return peer_uid(writer.get_extra_info("socket")) == os.getuid()

    async def _write(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        lock = self._write_locks.setdefault(writer, asyncio.Lock())
        async with lock:
            writer.write(encode_message(message))
            await asyncio.wait_for(writer.drain(), timeout=5)

    async def _respond(self, writer: asyncio.StreamWriter, request: dict[str, Any]) -> None:
        try:
            result = await self.handler(request)
            message = {"type": "response", "id": request["id"], "payload": result}
        except Exception as error:
            self.logger.exception("IPC request failed", extra={"fields": {"error": str(error)}})
            message = {
                "type": "error",
                "id": request["id"],
                "payload": {"code": "request_failed", "message": str(error)[:500]},
            }
        await self._write(writer, message)

    def _cancel_queued_actions(self):
        cancelled = []
        for writer, queue in tuple(self._request_queues.items()):
            retained = []
            while not queue.empty():
                request = queue.get_nowait()
                queue.task_done()
                if request["type"] in QUEUED_ACTIONS:
                    cancelled.append((writer, request))
                else:
                    retained.append(request)
            for request in retained:
                queue.put_nowait(request)
        return cancelled

    async def _respond_action_stop(self, writer, request, cancelled):
        await self._respond(writer, request)
        for destination, queued in cancelled:
            if destination.is_closing():
                continue
            try:
                await self._write(destination, {
                    "type": "response", "id": queued["id"],
                    "payload": {"status": "cancelled", "queued": True,
                                "dispatched": False, "reason": "user_stop"},
                })
            except (ConnectionError, BrokenPipeError, TimeoutError):
                destination.close()

    async def _serve_requests(
        self,
        writer: asyncio.StreamWriter,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        while True:
            request = await queue.get()
            try:
                await self._respond(writer, request)
            finally:
                queue.task_done()

    async def _send_events(self, writer: asyncio.StreamWriter, queue: asyncio.Queue[Event]) -> None:
        while True:
            event = await queue.get()
            try:
                await self._write(writer, {"type": "event", "payload": event.as_dict()})
            finally:
                queue.task_done()

    async def _send_panel(self, writer: asyncio.StreamWriter, queue: asyncio.Queue[Event]) -> None:
        async def send():
            result = await self.handler({'type': 'panel.summary', 'payload': {}})
            private = result.get('privacy_mode')
            locked = result.get('session_locked')
            await self._write(writer, {'type': 'panel.state', 'payload': {
                'privacy_mode': private if type(private) is bool else True,
                'state': str(result.get('state', 'UNKNOWN')),
                'session_locked': locked if type(locked) is bool else True,
            }})
        await send()
        while True:
            event = await queue.get()
            try:
                if event.type in {'carlos.privacy_changed', 'carlos.privacy_transition',
                                  'voice.privacy_changed', 'core.state_changed',
                                  'presence.session_changed', 'system.resume_observed'}:
                    await send()
            finally:
                queue.task_done()

    async def _client_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if not self._peer_is_current_user(writer):
            writer.close()
            await writer.wait_closed()
            return
        self.clients += 1
        self._writers.add(writer)
        self._write_locks[writer] = asyncio.Lock()
        client_task = asyncio.current_task()
        assert client_task is not None
        self._client_tasks.add(client_task)
        subscriber_id: str | None = None
        subscription_type: str | None = None
        event_task: asyncio.Task[None] | None = None
        request_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
        self._request_queues[writer] = request_queue
        request_task = asyncio.create_task(self._serve_requests(writer, request_queue))
        responsive_tasks: set[asyncio.Task[None]] = set()
        interrupt_tasks: set[asyncio.Task[None]] = set()

        def response_done(task: asyncio.Task[None]) -> None:
            responsive_tasks.discard(task)
            interrupt_tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                writer.close()

        # Wake the reader on a failed event/response writer, instead of keeping
        # a broken client subscribed indefinitely.
        def writer_done(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and task.exception() is not None:
                writer.close()
                if task is request_task:
                    while not request_queue.empty():
                        request_queue.get_nowait()
                        request_queue.task_done()

        request_task.add_done_callback(writer_done)
        try:
            await self._write(
                writer,
                {
                    "type": "hello",
                    "payload": {
                        "protocol": PROTOCOL_VERSION,
                        "service": "Carlos",
                        "compatibility_service": "E.V.",
                        "pid": os.getpid(),
                    },
                },
            )
            while line := await reader.readline():
                try:
                    request = decode_message(line, self.max_message_bytes)
                    if request["type"] in {"subscribe", "panel.subscribe"}:
                        if subscriber_id is not None and subscription_type != request['type']:
                            raise ProtocolError('This connection already has a different subscription')
                        if subscriber_id is None:
                            subscriber_id, queue = self.bus.subscribe()
                            subscription_type = request['type']
                            send = self._send_panel if subscription_type == 'panel.subscribe' else self._send_events
                            event_task = asyncio.create_task(send(writer, queue))
                            event_task.add_done_callback(writer_done)
                        result = {"subscribed": True, "sequence": self.bus.sequence}
                        await self._write(
                            writer, {"type": "response", "id": request["id"], "payload": result}
                        )
                    elif _is_action_stop_request(request):
                        if len(interrupt_tasks) >= 2:
                            await self._write(writer, {"type": "error", "id": request["id"],
                                "payload": {"code": "busy", "message": "Cancellation is already in progress."}})
                            continue
                        cancelled = self._cancel_queued_actions()
                        task = asyncio.create_task(self._respond_action_stop(writer, request, cancelled))
                        responsive_tasks.add(task)
                        interrupt_tasks.add(task)
                        task.add_done_callback(response_done)
                    elif _is_responsive_request(request) and len(responsive_tasks) - len(interrupt_tasks) < 8:
                        task = asyncio.create_task(self._respond(writer, request))
                        responsive_tasks.add(task)
                        task.add_done_callback(response_done)
                    else:
                        try:
                            request_queue.put_nowait(request)
                        except asyncio.QueueFull:
                            await self._write(
                                writer,
                                {
                                    "type": "error",
                                    "id": request["id"],
                                    "payload": {
                                        "code": "busy",
                                        "message": "Too many queued requests; try again shortly.",
                                    },
                                },
                            )
                except ProtocolError as error:
                    await self._write(writer, {"type": "error", "payload": {"code": str(error)}})
                except Exception as error:
                    self.logger.exception(
                        "IPC request failed", extra={"fields": {"error": str(error)}}
                    )
                    await self._write(
                        writer,
                        {
                            "type": "error",
                            "payload": {"code": "request_failed", "message": str(error)[:500]},
                        },
                    )
            # Let an accepted operation finish if a client closes its socket.
            # Core shutdown explicitly cancels client tasks in stop().
            if not request_task.done():
                await request_queue.join()
            if responsive_tasks:
                await asyncio.gather(*responsive_tasks, return_exceptions=True)
        except (ConnectionError, BrokenPipeError, TimeoutError, ValueError):
            pass
        finally:
            if subscriber_id is not None:
                self.bus.unsubscribe(subscriber_id)
            tasks = [request_task, *responsive_tasks]
            if event_task is not None:
                tasks.append(event_task)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.clients -= 1
            self._client_tasks.discard(client_task)
            self._writers.discard(writer)
            self._request_queues.pop(writer, None)
            self._write_locks.pop(writer, None)
            if not writer.is_closing():
                writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
