# Linux setup

Carlos has package profiles for these distro families:

| Distro | Baseline | Package manager |
| --- | --- | --- |
| Ubuntu, Linux Mint | Ubuntu 24.04+, Mint 22+ | apt |
| Debian | Debian 12+ | apt |
| Fedora | Fedora 44 | dnf |
| Arch, EndeavourOS | Current rolling release | pacman |
| openSUSE | Tumbleweed | zypper |
| Gentoo | Python 3.11+, Qt 6.4+ | Portage |

The Linux build workflow builds the full UI without KDE development libraries,
runs the Qt tests, renders both windows, and installs into a temporary home on
Ubuntu 24.04, Debian 13, Fedora 44, Arch and Tumbleweed. These are container checks,
not claims of a live microphone or desktop session test on every distro.
Gentoo KDE Wayland is the live development machine. Mint and EndeavourOS reuse
their parent profiles; they don't have separate desktop test machines.

## Install

Clone the repo and run these from its root as your desktop user:

```sh
python3 carlos/scripts/linux-support.py packages
python3 carlos/scripts/linux-support.py install-deps
sh carlos/scripts/install-linux.sh
```

The first command only prints the package list. The second uses your distro's
package manager, with sudo or doas if needed, and keeps its normal confirmation
prompt. It doesn't add repositories or replace your desktop/audio configuration.
Refresh your package index first if needed. On Arch, keep the system fully updated;
don't do a partial upgrade. Gentoo users need matching Python targets for
`dbus-python` and `pygobject`, and Qt's widgets, QML and Quick components enabled.

The last command checks dependencies, builds with two jobs, runs the Qt tests,
then installs the assistant and pet. It enables login startup. Add `--no-start`
to skip launching them immediately; this still enables startup at the next login.
`CARLOS_BUILD_JOBS=4` changes the build job count. Don't run the user installer
with sudo.

Carlos installs its pinned Python dependencies into a private venv. It links only
the distro's native GI/D-Bus bindings into that environment. It doesn't pip-install
into system Python. Reinstall Carlos after upgrading to a different Python minor
version so these native bindings and its venv match again.

Run this for a read-only dependency report:

```sh
python3 carlos/scripts/linux-support.py doctor
```

Speech models and inference engines still need configuration. See
[setup](SETUP.md#models-and-voice). Installing Carlos doesn't download a model,
start always-on microphone recording, or turn the offline provider into a chat model.

## Desktop support

| Feature | KDE Plasma | GNOME, Cinnamon, Xfce, MATE and other desktops |
| --- | --- | --- |
| Main assistant, chat, settings and configured voice providers | Yes | Yes, with the same runtime/model requirements |
| Login startup | XDG autostart + session D-Bus | Same; no systemd or OpenRC service required |
| Pet, pats, menu, dragging, saved position | Yes | Portable X11/XWayland window |
| Pet app-aware comments | KWin app classification | Generic comments; no guessed native Wayland app activity |
| Always above fullscreen | Native KDE Wayland layer overlay | Window-manager policy; cannot promise fullscreen stacking |
| Exact window automation and verified desktop input | KWin backend | Unavailable; commands fail with a capability error |

The normal Qt UI no longer imports KDE-only QML modules. On KDE Wayland with
LayerShellQt 6.6+, the pet and voice HUD retain their native overlays. Otherwise
Carlos uses normal Qt windows. On non-KDE Wayland sessions it selects XWayland
when available, so placement and dragging work without a shell extension.
Pure Wayland sessions without XWayland have compositor-managed positioning and
more limited overlay behavior. The package profiles include XWayland.

You can force the portable backend for troubleshooting:

```sh
CARLOS_DESKTOP_BACKEND=portable carlos-pet
```

Quit an existing pet first. An explicit `QT_QPA_PLATFORM` takes precedence.
The pet supports the standard, GNOME, Cinnamon, MATE and Xfce screen-lock services;
if it cannot establish the lock state, it stays hidden. Lock/privacy hiding is
intentional. A missing tray extension on GNOME doesn't prevent opening Carlos
or the pet through their application-menu entries.

No distribution installer changes your theme, panels, widgets, shortcuts,
compositor settings, audio routing, firewall or sandbox security policy.
Ubuntu may restrict unprivileged Bubblewrap execution through AppArmor; Carlos
reports that sandbox operation as unavailable instead of disabling the restriction.

## Versions and manual builds

The common UI minimum is Qt 6.4 and CMake 3.21. LayerShellQt is optional. To test
a fully portable build even on KDE:

```sh
cmake -S carlos/ui -B carlos/build/portable -DCARLOS_LAYER_SHELL=OFF -DBUILD_TESTING=ON
cmake --build carlos/build/portable -j2
ctest --test-dir carlos/build/portable --output-on-failure
```

Unknown distros get a clear unsupported package-profile message; the script does
not guess a package manager. Manually install the dependencies in [setup](SETUP.md)
or add a reviewed profile. Immutable distros, NixOS, musl-based distributions,
older Ubuntu/Mint releases and FreeBSD are outside this Linux installer matrix.
The existing FreeBSD scripts remain separate.

Native package references: [Ubuntu Qt](https://packages.ubuntu.com/noble/qt6-base-dev),
[Debian Qt](https://packages.debian.org/trixie/qt6-base-dev),
[Fedora Qt](https://packages.fedoraproject.org/pkgs/qt6-qtbase/qt6-qtbase-devel/),
[Arch Qt](https://archlinux.org/packages/extra/x86_64/qt6-base/),
[openSUSE Qt](https://software.opensuse.org/package/qt6-base-devel),
[Gentoo Qt](https://packages.gentoo.org/packages/dev-qt/qtbase).
