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


def wait_shown(client, expected):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            obj = client.get_object("org.phax.CarlosPet", "/Pet", introspect=False)
            value = obj.Get(
                "org.phax.CarlosPet",
                "shown",
                dbus_interface="org.freedesktop.DBus.Properties",
                timeout=1,
            )
            if bool(value) == expected:
                return
        except dbus.DBusException:
            pass
        time.sleep(0.1)
    raise AssertionError(f"Pet shown never became {expected}")


def check(service, path):
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
                lock.ActiveChanged(True)
                wait_shown(client, False)
                lock.ActiveChanged(False)
                wait_shown(client, True)
                print(service, "unlock / lock / unlock passed", flush=True)
            finally:
                process.terminate()
                process.wait(timeout=5)
                assert process.returncode == 0, process.returncode
    finally:
        client.close()
        lock.remove_from_connection()
        server.release_name(service)
        del name


try:
    desktop = sys.argv[2] if len(sys.argv) > 2 else "gnome"
    check(f"org.{desktop}.ScreenSaver", f"/org/{desktop}/ScreenSaver")
finally:
    loop.quit()
    thread.join(timeout=2)
    server.close()
