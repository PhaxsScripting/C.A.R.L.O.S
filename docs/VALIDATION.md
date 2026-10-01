# Test results

Checked on Gentoo Linux, September 26, 2026, after splitting out the other apps:

- 972 Python tests passed in a fresh venv with only Carlos's requirements.
- Qt UI built; both the client and QML interface CTest groups passed.
- Python dependency check passed.
- Local documentation links resolved.
- Comment edits left the Python implementation AST unchanged.
- No emoji characters were found in the tracked text files.

The separate wake server and its one test moved out with the other projects.
Carlos's integration client tests are still here. You don't need the hand tracker,
phone server or wake node installed to run the assistant tests.

The release scan checks the Git index for common token patterns, personal paths
and generated files. It can miss things, so don't treat it as a security audit.

These results cover the source checkout. They don't prove microphone quality,
wake reliability or response speed in a real room. FreeBSD and other desktops
still need testing. No installed apps were upgraded during this repo cleanup.

## Desktop pet

September 26, 2026: 973 Python tests passed, including the pet privacy status
check. The UI build and four CTest groups passed: client, QML interface (including
pet click/drag/reduced motion), comment policy, and pet controller. The controller
checks lock hiding, quiet pats and rejection of direct app observations.

The pet was also launched on KDE Wayland without taking keyboard focus. A short
idle sample measured 0.3% CPU and about 37 MiB proportional memory (115 MiB RSS,
including shared Qt libraries). This is one idle sample, not a gaming benchmark.

The follow-up pet fix passed all four CTest groups and both installer tests.
Live KWin checks covered slow dragging and 120 rapid mouse movements while
holding the button: the pet followed the pointer without needing another click.
The temporary pointer listener was gone after release. Dragging now uses KWin's
actual pointer coordinates and requests a frame for each position update.
A screenshot confirmed the pet remained visible above a focused fullscreen test
window on the laptop display. The screen menu also moved it between both monitors.

## Linux portability

September 27, 2026: 983 Python tests passed. The Linux build matrix passed on Ubuntu 24.04, Debian 13,
Fedora 44, Arch and openSUSE Tumbleweed. Each job built the full UI without
LayerShellQt, ran all four CTest groups, rendered the pet and main window,
exercised GNOME/Cinnamon/MATE/Xfce lock interfaces on an isolated D-Bus session,
and installed the app and private Python runtime into a temporary home.

On Gentoo, the native build and portable pet build passed their checks. A fresh
temporary-home install started the core, answered a health request and shut down
cleanly on a separate D-Bus session without KWin. The private runtime also passed
imports and dependency checks after moving to its final path.

Container rendering and simulated lock services do not replace interactive
testing on those desktops. Voice models, audio devices, fullscreen stacking on
non-KDE desktops, and compositor-specific automation have the limits described
in [Linux setup](LINUX.md).

The pet's KWin startup check now simulates an unusable per-script D-Bus ID and a
script file read after the start call returns. It confirms that activity arrives,
unlocking does not load duplicate listeners, and exit unloads the script. On the
live KDE session, a normal pet restart reported activity tracking successfully.

## September 30: recovery

990 Python tests passed. All four Qt/QML/pet test groups passed on Gentoo. New
connection tests cover a hung socket, reconnection without command replay,
healthy connections during long requests, explicit Stop and offline submissions.
Worker tests cover a stalled probe and recovery after a model health exception.
Readiness tests reject stale, missing and invalid observation timestamps.

The control center checks health every five seconds, with a 15-second response
deadline. Losing the connection does not cancel or repeat an already submitted
task. Check task history before retrying an action whose result is unknown.

This does not measure room acoustics or establish general desktop-task accuracy.


September 30 settings and telemetry pass:

- Daily/Development settings persist and apply at runtime. Development enables debug file logging and a core CPU/RAM readout; Daily hides the readout and returns file logging to INFO. Neither mode changes permissions, microphone state, or loads plugins automatically.
- Greeting delay and cooldown can be changed from Settings. The first return no longer depends on machine uptime.
- Network telemetry excludes loopback and reports link state separately from unverified internet reachability. Counter resets cannot produce negative traffic. Network link and power-source transitions emit bounded events through the existing sampler.
- 1,000 Python tests passed. Qt settings coverage verifies typed selection and performance-readout visibility. Acoustic and remote-device acceptance remain separate.


September 30 live acceptance:

- A real Codex repair fixed a disposable Python function in an isolated worktree in 25.1 seconds. All three unchanged tests passed; only the implementation changed. The review commit used the project owner's identity and the original checkout stayed unchanged.
- Installed desktop controls minimized, restored, placed, typed into and closed an owned test window; independent text readback matched. File/archive operations and composite postconditions passed against disposable files. Same-connection cancellation completed in 101 ms.
- Silent synthesized speech recognized three phrases across base beam-3, base beam-1 and tiny-preview configurations. Recognition took 1.83–2.05 seconds for base and 1.09–1.11 seconds for tiny-preview on these samples. This is not a room-acoustic or end-to-end voice latency measurement.
- Speech workers now report their own dependency versions when they start. Support bundles distinguish running workers from cached startup evidence, include no worker paths, and retain compatibility with older workers that omit version metadata.
- 1,006 Python tests passed after the worker diagnostics change.


Automatic conversation history now redacts recognizable credentials before SQLite insertion: named password/token assignments, common API-key formats, authorization headers and private-key blocks. Task receipts use the same filter. This is pattern filtering, not a guarantee that arbitrary unlabeled secrets can be recognized. Ordinary discussion remains intact; private-session storage policy is unchanged. Synthetic canaries were absent from history and database files. 1,011 Python tests passed.

September 30 installer recovery: 15 targeted tests passed. The real installer script was exercised in temporary homes with fixture runtimes and an isolated command stub for the session bus. Tests cover failed runtime preparation, failed backup, interrupted file installation, rejected health checks, a core that refuses to stop, unavailable session bus, repeated installs, and offline rollback. Backups are copied before replacement; the old core must release its session name before activation. Broken launcher symlinks restore without following their targets. User conversation storage and Plasma configuration were unchanged in every scenario. These are fault-injection tests, not a power-loss test on the live machine.

Startup/resume observations now record the first observed READY time per component. Resume samples carry the measured detection interval and expire pre-sleep health evidence; Settings displays an observed range, waiting state, or no sample. No boot benchmark or forced suspend was performed. 1,028 Python tests and all four native Qt/QML/pet groups passed. On the live Gentoo core, hard mute released microphone capture and its wake listener; original privacy settings were restored. Terminating the owned VAD worker recovered in 1.08 seconds while core health remained available.

Power status now keeps AC connection separate from observed charging state. Linux reads documented kernel power-supply status, optional wattage and temperature without writing policy. Unknown, missing and mixed battery states stay explicit; device batteries are excluded. The desktop and battery tool share this observation. Sensor units follow the [kernel power-supply ABI](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-class-power). 1,036 Python tests and all four Qt/QML/pet groups passed. This does not diagnose the cause of a charging fault.

## October 1: session presence

The core now listens to desktop lock signals directly on the session bus, with
bounded refresh calls for missed signals. It recognizes the freedesktop, GNOME,
Cinnamon, MATE and Xfce interfaces without requiring KDE command-line tools.
Signals must match the service's current bus owner, interface and object path.
Losing the service clears old presence and attention evidence. Unsupported idle
time stays unknown; it does not disable lock monitoring.

Six new tests cover invalid signals, a stalled service, unsupported idle time,
owner changes and loss of service. The transport fixture runs actual method
calls and signals on a disposable D-Bus session for every supported object path.
It does not lock the user's desktop. The Python suite passed 1,042 tests before
the subsequent offline transport checks. Interactive testing on other desktops
and physical occupancy detection remain separate.
