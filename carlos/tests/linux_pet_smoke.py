#!/usr/bin/env python3
"""Exercise portable pet lock handling on an isolated session bus."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
server = dbus.SessionBus(private=True)
server.set_exit_on_disconnect(False)
loop = GLib.MainLoop()
thread = threading.Thread(target=loop.run, daemon=True)
thread.start()


def wait_property(client, prop, expected):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            obj = client.get_object("org.phax.CarlosPet", "/Pet", introspect=False)
            value = obj.Get(
                "org.phax.CarlosPet",
                prop,
                dbus_interface="org.freedesktop.DBus.Properties",
                timeout=1,
            )
            if bool(value) == expected:
                return
        except dbus.DBusException:
            pass
        time.sleep(0.1)
    raise AssertionError(f"Pet {prop} never became {expected}")


def wait_shown(client, expected):
    wait_property(client, "shown", expected)


class KWinScripts(dbus.service.Object):
    """The script ID is unusable; starting via the manager must still work."""

    def __init__(self):
        super().__init__(server, "/Scripting")
        self.sources = {}
        self.errors = []
        self.starts = 0

    @dbus.service.method("org.kde.kwin.Scripting", in_signature="ss", out_signature="i")
    def loadScript(self, path, plugin):
        self.sources[str(plugin)] = Path(str(path))
        return 3

    @dbus.service.method("org.kde.kwin.Scripting", in_signature="s", out_signature="b")
    def unloadScript(self, plugin):
        return self.sources.pop(str(plugin), None) is not None

    @dbus.service.method("org.kde.kwin.Scripting", in_signature="", out_signature="")
    def start(self):
        self.starts += 1

        def read_later():
            try:
                source = next(iter(self.sources.values())).read_text()
                assert "Observe" in source
                pet = server.get_object("org.phax.CarlosPet", "/Pet", introspect=False)
                pet.Observe(
                    "org.kde.kate",
                    False,
                    "",
                    dbus_interface="org.phax.CarlosPet",
                    reply_handler=lambda: None,
                    error_handler=lambda error: self.errors.append(str(error)),
                )
            except Exception as error:
                self.errors.append(str(error))
            return False

        GLib.timeout_add(200, read_later)


def check(service, path, scripts=None):
    class ScreenLock(dbus.service.Object):
        @dbus.service.method(service, in_signature="", out_signature="b")
        def GetActive(self):
            return False

        @dbus.service.signal(service, signature="b")
        def ActiveChanged(self, active):
            pass

    name = dbus.service.BusName(service, server)
    lock = ScreenLock(server, path)
    client = dbus.SessionBus(private=True)
    client.set_exit_on_disconnect(False)
    try:
        with tempfile.TemporaryDirectory(prefix="carlos-pet-lock-") as directory:
            env = {
                **os.environ,
                "QT_QPA_PLATFORM": "offscreen",
                "QT_QUICK_BACKEND": "software",
                "XDG_CONFIG_HOME": directory,
                "XDG_CURRENT_DESKTOP": "GNOME",
            }
            process = subprocess.Popen([str(Path(sys.argv[1]).resolve())], env=env)
            try:
                wait_shown(client, True)
                if scripts:
                    wait_property(client, "observing", True)
                lock.ActiveChanged(True)
                wait_shown(client, False)
                lock.ActiveChanged(False)
                wait_shown(client, True)
                if scripts:
                    assert scripts.starts == 1, scripts.starts
                    assert not scripts.errors, scripts.errors
                print(service, "unlock / lock / unlock passed", flush=True)
            finally:
                process.terminate()
                process.wait(timeout=5)
                assert process.returncode == 0, process.returncode
                if scripts:
                    assert not scripts.sources, scripts.sources
    finally:
        client.close()
        lock.remove_from_connection()
        server.release_name(service)
        del name


try:
    desktop = sys.argv[2] if len(sys.argv) > 2 else "gnome"
    if desktop == "kwin":
        kwin_name = dbus.service.BusName("org.kde.KWin", server)
        scripts = KWinScripts()
        check("org.freedesktop.ScreenSaver", "/ScreenSaver", scripts)
        scripts.remove_from_connection()
        server.release_name("org.kde.KWin")
        del kwin_name
    else:
        check(f"org.{desktop}.ScreenSaver", f"/org/{desktop}/ScreenSaver")
finally:
    loop.quit()
    thread.join(timeout=2)
    server.close()
