"""Local observations only. None of these probes repair or elevate anything."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
from itertools import islice
from pathlib import Path
from .process_runner import command


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def observation(source, items=None, status="OK", **fields):
    return {"source": source, "status": status, "items": items or {}, **fields}


def init_system():
    try:
        if Path("/proc/1/comm").read_text().strip() == "systemd":
            return "systemd"
    except OSError:
        pass
    if Path("/run/openrc").is_dir() and shutil.which("rc-status"):
        return "openrc"
    return "UNKNOWN"


class SecuritySources:
    def __init__(self, center, runner=command, manager=None, home=None):
        self.center = center
        self.run = runner
        self.manager = manager or init_system()
        self.home = home or Path.home()
        self.config_home = (Path(os.environ.get("XDG_CONFIG_HOME", self.home / ".config"))
                            if home is None else self.home / ".config")
        self.config_dirs = os.environ.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":")

    async def listeners(self):
        result = await self.run(["ss", "-H", "-lntup"])
        if result["code"] != 0:
            return observation("ss", status="UNAVAILABLE")
        items = {}
        for line in result["out"].splitlines():
            fields = line.split()
            if len(fields) < 6 or fields[0] not in {"tcp", "udp"}:
                return observation("ss", status="INCOMPLETE")
            address = fields[4]
            host, _, port = address.rpartition(":")
            try:
                local = ipaddress.ip_address(host.strip("[]").split("%")[0]).is_loopback
            except ValueError:
                local = host in {"localhost", "[localhost]"}
            if not port.isdigit():
                return observation("ss", status="INCOMPLETE")
            if not local:
                process = re.search(r'users:\(\("([^"\n]{1,100})"', line)
                key = fingerprint(f"{fields[0]}|{address}|{process[1] if process else ''}")
                items[key] = "NETWORK_BOUND"
            if len(items) > 1024:
                return observation("ss", status="INCOMPLETE")
        return observation("ss -H -lntup", items, count=len(items))

    async def tailscale(self):
        result = await self.run(["tailscale", "status", "--json"])
        try:
            data = json.loads(result["out"])
            peers = data["Peer"]
            if peers is None:
                peers = {}
            identity = data["Self"]["ID"]
            if result["code"] != 0 or data.get("BackendState") != "Running" or not isinstance(peers, dict):
                raise ValueError("No running netmap")
            if not isinstance(identity, str) or not identity or len(peers) > 1024:
                raise ValueError("Unrecognized netmap")
            items = {}
            for peer in peers.values():
                peer_id = peer.get("ID") if isinstance(peer, dict) else None
                if not isinstance(peer_id, str) or not peer_id:
                    raise ValueError("Missing stable node ID")
                items[fingerprint(peer_id)] = "VISIBLE"
            return observation("tailscale local netmap", items, scope=fingerprint(identity), count=len(items))
        except (ValueError, KeyError, TypeError):
            return observation("tailscale local netmap", status="UNAVAILABLE")

    async def failed_logins(self):
        result = await self.run(["lastb", "-n", "100", "--time-format", "iso"])
        if result["code"] != 0:
            return observation("lastb/btmp", status="UNAVAILABLE")
        items = {}
        for line in result["out"].splitlines():
            if not line.strip() or line.startswith("btmp begins"):
                continue
            if not re.search(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", line):
                return observation("lastb/btmp", status="INCOMPLETE")
            items[fingerprint(line)] = "FAILED"
            if len(items) > 100:
                return observation("lastb/btmp", status="INCOMPLETE")
        return observation("lastb/btmp latest 100 records", items, count=len(items),
                           coverage="Bounded records; not a full authentication audit")

    async def services(self):
        if self.manager == "systemd":
            result = await self.run(["systemctl", "list-units", "--failed", "--type=service",
                                     "--no-legend", "--plain", "--no-pager"])
            items = {}
            if result["code"] != 0:
                return observation("systemd failed units", status="UNAVAILABLE")
            for line in result["out"].splitlines():
                fields = line.split()
                if len(fields) < 4 or not re.fullmatch(r"[A-Za-z0-9_.@:\\-]{1,192}\.service", fields[0]) or fields[2] != "failed":
                    return observation("systemd failed units", status="INCOMPLETE")
                items[fields[0]] = "FAILED"
        elif self.manager == "openrc":
            result = await self.run(["rc-status", "--crashed", "--nocolor"])
            if result["code"] not in {0, 1} or result["err"].strip():
                return observation("OpenRC crashed services", status="UNAVAILABLE")
            if result["code"] == 1 and result["out"].strip():
                return observation("OpenRC crashed services", status="INCOMPLETE")
            items = {}
            for line in result["out"].splitlines():
                if not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,200}", line.strip()):
                    return observation("OpenRC crashed services", status="INCOMPLETE")
                items[line.strip()] = "CRASHED"
        else:
            return observation("init discovery", status="UNAVAILABLE")
        if len(items) > 256:
            return observation("failed service inventory", status="INCOMPLETE")
        return observation(f"{self.manager} failed services", items, count=len(items), manager=self.manager)

    async def firewall(self):
        result = await self.run(["nft", "-j", "list", "ruleset"])
        try:
            ruleset = json.loads(result["out"])["nftables"]
            if result["code"] != 0 or not isinstance(ruleset, list):
                raise ValueError("No readable ruleset")
            rules = sum(isinstance(row, dict) and "rule" in row for row in ruleset)
            return observation("nft kernel ruleset", {"ruleset": fingerprint(json.dumps(ruleset, sort_keys=True))},
                               rule_count=rules, coverage="nftables only; rules alone do not prove protection")
        except (ValueError, KeyError, TypeError):
            pass
        if self.manager not in {"systemd", "openrc"}:
            return observation("nft kernel ruleset", status="UNAVAILABLE",
                               coverage="Kernel rules were not readable; no firewall protection claim")
        names = ("nftables", "iptables", "firewalld", "ufw")
        async def service(name):
            if self.manager == "openrc":
                value = await self.run(["rc-service", name, "status"])
                text = (value["out"] + value["err"]).casefold()
                if "does not exist" in text or "not found" in text:
                    return name, None
                if value["code"] == 0 and "started" in text:
                    return name, "ACTIVE"
                if "stopped" in text or "crashed" in text:
                    return name, "INACTIVE"
            else:
                value = await self.run(["systemctl", "show", "--property=LoadState,ActiveState", name + ".service"])
                fields = dict(line.split("=", 1) for line in value["out"].splitlines() if "=" in line)
                if fields.get("LoadState") == "not-found":
                    return name, None
                if value["code"] == 0 and fields.get("LoadState") == "loaded" and fields.get("ActiveState"):
                    return name, "ACTIVE" if fields["ActiveState"] == "active" else "INACTIVE"
            return name, "UNKNOWN"
        items = dict((name, state) for name, state in await asyncio.gather(*(service(name) for name in names)) if state is not None)
        return observation(f"{self.manager} firewall service state", items,
                           status="OK" if items and "UNKNOWN" not in items.values() else "UNAVAILABLE",
                           active_service_count=sum(state == "ACTIVE" for state in items.values()),
                           filtering_verified=None,
                           coverage="Firewall service activity only; kernel rules are unreadable, so protection is unverified")

    def startup(self):
        config_home = self.config_home
        if not config_home.is_absolute() or len(self.config_dirs) > 8 or any(not Path(root).is_absolute() for root in self.config_dirs):
            return observation("startup file fingerprints", status="INCOMPLETE")
        roots = [config_home / "autostart", *(Path(root) / "autostart" for root in self.config_dirs),
                 config_home / "systemd/user", config_home / "autostart-scripts",
                 config_home / "plasma-workspace/env"]
        items = {}
        paths = []
        for root in roots:
            try:
                info = root.stat()
            except FileNotFoundError:
                continue
            except OSError:
                return observation("startup file fingerprints", status="UNAVAILABLE")
            if not stat.S_ISDIR(info.st_mode):
                return observation("startup file fingerprints", status="INCOMPLETE")
            try:
                entries = list(islice(root.iterdir(), 257))
            except OSError:
                return observation("startup file fingerprints", status="UNAVAILABLE")
            if len(entries) > 256:
                return observation("startup file fingerprints", status="INCOMPLETE")
            scripts = root.name in {"autostart-scripts", "env"}
            paths.extend(path for path in entries if scripts or path.suffix in {".desktop", ".service", ".timer", ".socket", ".path", ".sh"})
            for directory in entries:
                if root.name != "user" or not directory.name.endswith(".d") or directory.is_symlink() or not directory.is_dir():
                    continue
                try:
                    dropins = list(islice(directory.glob("*.conf"), 33))
                except OSError:
                    return observation("startup file fingerprints", status="UNAVAILABLE")
                if len(dropins) > 32:
                    return observation("startup file fingerprints", status="INCOMPLETE")
                paths.extend(dropins)
        paths.extend(self.home / name for name in (".profile", ".bash_profile", ".bashrc", ".zshrc", ".xprofile", ".xinitrc"))
        if len(paths) > 512:
            return observation("startup file fingerprints", status="INCOMPLETE")
        bytes_read = 0
        for path in paths:
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    content = os.readlink(path).encode()
                elif stat.S_ISREG(info.st_mode):
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as stream:
                        current = os.fstat(stream.fileno())
                        if not stat.S_ISREG(current.st_mode) or current.st_size > 65536:
                            return observation("startup file fingerprints", status="INCOMPLETE")
                        info = current
                        content = stream.read(65537)
                    if len(content) > 65536:
                        return observation("startup file fingerprints", status="INCOMPLETE")
                else:
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                return observation("startup file fingerprints", status="UNAVAILABLE")
            bytes_read += len(content)
            if bytes_read > 1048576:
                return observation("startup file fingerprints", status="INCOMPLETE")
            items[fingerprint(str(path))] = hashlib.sha256(
                str((info.st_mode, info.st_uid, info.st_gid)).encode() + content).hexdigest()
        return observation("startup file fingerprints", items, count=len(items),
                           coverage="XDG/user units, user drop-ins, Plasma env and shell startup; symlink targets are not audited")

    async def smart(self):
        result = await self.run(["smartctl", "--scan", "-j"])
        try:
            devices = json.loads(result["out"])["devices"]
            if result["code"] != 0 or not isinstance(devices, list) or len(devices) > 16:
                raise ValueError("Incomplete device discovery")
        except (ValueError, KeyError, TypeError):
            return observation("smartctl", status="UNAVAILABLE")
        items = {}
        for device in devices:
            if not isinstance(device, dict):
                return observation("smartctl", status="INCOMPLETE")
            name, kind = device.get("name", ""), device.get("type", "")
            if not isinstance(name, str) or not re.fullmatch(r"/dev/(?:nvme\d+(?:n\d+)?|sd[a-z]+)", name) or kind not in {"nvme", "ata", "sat", "scsi"}:
                return observation("smartctl", status="INCOMPLETE")
            health = await self.run(["smartctl", "-j", "-H", "-n", "standby,2,2", "-d", kind, name])
            value = "UNAVAILABLE"
            try:
                data = json.loads(health["out"])
                passed = data["smart_status"]["passed"]
                code = health["code"]
                if isinstance(code, int) and code >= 0 and not code & 7 and isinstance(passed, bool):
                    value = "PASSED" if passed else "FAILED"
            except (ValueError, KeyError, TypeError):
                pass
            items[fingerprint(name)] = value
        status = "OK" if items and all(value != "UNAVAILABLE" for value in items.values()) else "INCOMPLETE"
        return observation("smartctl health; no tests or writes", items, status=status, count=len(items),
                           failed_count=sum(value == "FAILED" for value in items.values()),
                           coverage="No elevation; sleeping or inaccessible devices stay unknown")

    async def updates(self):
        result = await asyncio.to_thread(self.center.updates, True)
        items = {fingerprint(line): "REVIEW" for line in result.get("unresolved", []) if isinstance(line, str)}
        return observation("local advisory scan", items,
                           status="OK" if result.get("complete") else "INCOMPLETE",
                           advisory_status=result.get("status"), count=result.get("unresolved_count", len(items)))
