"""Bounded owned subprocesses with joined cancellation cleanup."""

import asyncio
import os


async def command(argv, timeout=5, maximum=131072):
    process = None
    tasks = []
    try:
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={**os.environ, "LC_ALL": "C"}, limit=8192))
        try:
            await asyncio.wait({spawn})
            process = spawn.result()
        except asyncio.CancelledError:
            try:
                process, _ = await settle(spawn)
            except OSError:
                pass
            raise

        async def read(stream):
            output = bytearray()
            while chunk := await stream.read(8192):
                if len(output) + len(chunk) > maximum:
                    raise ValueError("command output limit")
                output.extend(chunk)
            return output.decode("utf-8", errors="replace")

        async with asyncio.timeout(timeout):
            tasks = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr)),
                     asyncio.create_task(process.wait())]
            out, err, code = await asyncio.gather(*tasks)
        return {"code": code, "out": out, "err": err}
    except (OSError, TimeoutError, ValueError):
        return {"code": None, "out": "", "err": "Command unavailable, timed out or exceeded its output limit"}
    finally:
        async def cleanup():
            for task in tasks:
                if not task.done():
                    task.cancel()
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process is not None:
                async def discard(stream):
                    while await stream.read(8192):
                        pass
                try:
                    async with asyncio.timeout(1):
                        await asyncio.gather(discard(process.stdout), discard(process.stderr))
                        await process.wait()
                except TimeoutError:
                    # A descendant may retain a pipe after its owner exited.
                    # Stop retaining transports rather than growing a buffer.
                    process._transport.close()
                    await process.wait()
        _, interrupted = await settle(asyncio.create_task(cleanup()))
        if interrupted:
            raise asyncio.CancelledError


async def settle(task):
    interrupted = False
    while True:
        try:
            await asyncio.wait({task})
            return task.result(), interrupted
        except asyncio.CancelledError:
            if task.done():
                return task.result(), True
            interrupted = True
