"""Explicit monitor names bind to live hardware evidence, never a guessed index."""

import re
import time


RESERVED = frozenset({"this", "current", "this monitor", "current monitor", "this screen", "current screen",
                      "monitor i'm using", "other", "other monitor", "other screen"})


class AliasResolutionError(ValueError):
    def __init__(self, message, candidates=None):
        super().__init__(message)
        self.candidates = candidates or []


def alias_name(value):
    name = " ".join(value.strip().casefold().split())
    name = re.sub(r"^(?:the|my) ", "", name)
    if not re.fullmatch(r"[a-z][a-z0-9 '_-]{0,47}", name) or name in RESERVED:
        raise ValueError("Use a short monitor nickname; current/other are reserved for live context")
    return name


def identity(output):
    return tuple(output.get(key, "") for key in ("manufacturer", "model", "serial_number"))


def usable_identity(output):
    fields = identity(output)
    return (all(isinstance(value, str) and len(value) <= 256 for value in fields)
            and bool(fields[2].strip())
            and fields[2].strip().casefold() not in {'unknown', 'none', 'n/a', 'default', 'not available'}
            and not re.fullmatch(r'(?:0x)?0+', fields[2].strip().casefold().replace(' ', '')))


def make_binding(output, outputs):
    name = output.get("name")
    if not isinstance(name, str) or not name or len(name) > 256:
        raise ValueError("The compositor did not report an exact connector name")
    hardware = usable_identity(output) and sum(identity(item) == identity(output) for item in outputs) == 1
    return {"output_name": name, "binding": "HARDWARE" if hardware else "CONNECTOR",
            "manufacturer": output.get("manufacturer", "") if hardware else "",
            "model": output.get("model", "") if hardware else "",
            "serial_number": output.get("serial_number", "") if hardware else "",
            "source": "explicit_monitor_alias", "updated_at": time.time()}


def resolve_alias(description, aliases, outputs):
    query = re.sub(r"^(?:the|my) ", "", " ".join(description.strip().casefold().split()))
    if query not in aliases:
        return None
    record = aliases[query]
    if not isinstance(record, dict) or record.get("source") != "explicit_monitor_alias":
        raise AliasResolutionError("The saved monitor alias is invalid; save it again using a live output")
    if record.get("binding") == "HARDWARE" and usable_identity(record):
        matches = [output for output in outputs if identity(output) == identity(record)]
    elif record.get("binding") == "CONNECTOR" and isinstance(record.get("output_name"), str):
        matches = [output for output in outputs if output.get("name") == record["output_name"]]
    else:
        raise AliasResolutionError("The saved monitor binding is invalid; no fallback monitor was chosen")
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AliasResolutionError(f"Monitor alias {query!r} is not connected; no fallback monitor was chosen")
    raise AliasResolutionError(f"Monitor alias {query!r} matches multiple outputs", matches)
