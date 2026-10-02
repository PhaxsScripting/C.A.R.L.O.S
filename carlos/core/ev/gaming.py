"""Let Minecraft keep the CPU while ambient speech waits."""

import asyncio
import os
import stat
from pathlib import Path

import psutil


def minecraft_clients():
    matches, complete, checked = [], True, 0
    for process in psutil.process_iter(["name"]):
        try:
            if str(process.info.get("name", "")).casefold() not in {"java", "javaw"}:
                continue
            if process.uids().effective != os.getuid():
                continue
            checked += 1
            if checked > 64:
                complete = False
                break
            started = process.create_time()
            directory = Path(process.cwd())
            if directory.name not in {"minecraft", ".minecraft"}:
                continue
            options = (directory / "options.txt").lstat()
            if (not stat.S_ISREG(options.st_mode) or options.st_uid != os.getuid()
                    or not 0 < options.st_size <= 1024 * 1024
                    or process.status() == psutil.STATUS_ZOMBIE):
                continue
            if psutil.Process(process.pid).create_time() != started:
                complete = False
                continue
            matches.append((process.pid, started))
        except (psutil.NoSuchProcess, psutil.ZombieProcess, FileNotFoundError):
            continue
        except (psutil.AccessDenied, OSError):
            complete = False
    return {"clients": matches, "complete": complete, "checked": checked}


class GamingPolicy:
    def __init__(self, core, probe=minecraft_clients):
        self.core, self.probe = core, probe
        self.empty_observations = 0
        self.state = "UNCHECKED"
        self._lock = asyncio.Lock()

    async def refresh(self):
        async with self._lock:
            enabled = self.core.config.get("resources", {}).get("yield_ambient_for_minecraft", True)
            if not enabled:
                self.empty_observations = 0
                self.state = "DISABLED"
                await self.core.voice.set_gaming_suspended(False)
                return
            try:
                observed = await asyncio.wait_for(asyncio.to_thread(self.probe), 2)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state = "UNKNOWN"
                self.empty_observations = 0
                return
            if observed["clients"]:
                self.state = "YIELDING"
                self.empty_observations = 0
                await self.core.voice.set_gaming_suspended(True)
            elif observed["complete"]:
                self.empty_observations += 1
                if self.empty_observations >= 2:
                    self.state = "NORMAL"
                    await self.core.voice.set_gaming_suspended(False)
            else:
                self.state = "UNKNOWN"
                self.empty_observations = 0

    async def run(self):
        while True:
            await asyncio.sleep(3)
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state = "UNKNOWN"
