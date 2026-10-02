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

The offline transport fixture runs the real Unix-socket command handler in a
separate Linux network namespace with no route. It verifies greetings, CPU/RAM
queries, file creation, memory readback, Local Only refusing cloud transport,
and a local command after an actual failed cloud connection. All data is
temporary; the desktop's network is unchanged. The fixture skips on hosts that
forbid unprivileged namespaces. It passed on Gentoo; the complete Python suite
passed 1,045 tests. This does not establish offline microphone, model or desktop
acceptance. Router shutdown also closes the cloud transport when the local
provider has no cleanup method or its cleanup raises an exception.

Idle model policy: the managed conversation model releases its owned server
after 900 seconds without model work. `idle_unload_seconds: 0` disables this.
Explicit reasoning loads it again; deterministic commands do not reset this
timer. Active requests and startup warming hold an activity lease, so idle
cleanup waits for work to finish. External endpoints are excluded, and existing
process identity checks still govern shutdown. The supervisor reports ON_DEMAND
instead of restarting an intentionally unloaded model. Provider status exposes
HOT (active request), WARM (resident process) or UNLOADED; WARM alone is not a
health claim. Six new tests include reaping a real owned child, request/cleanup
races, cancellation, invalid policy and supervisor behavior. 1,051 Python tests
passed. Model reload adds cold-start latency after a long idle period.

Scenes can now select an existing saved workspace. The scene editor keeps
restoration disabled until a workspace is selected explicitly. Saving validates
the name without restoring anything. Activation prepares the restoration and
command plan before effects, then runs both through one executor. Missing
workspaces and unplannable commands prevent the scene from running; Stop during
preparation prevents activation. Result references and dependencies retain exact
window identities. Partial restoration reports saved-item gaps and unsupported
context separately. Guest mode cannot restore personal workspaces.

Eight backend tests and a native QML interaction test cover selection/save,
missing/deleted workspaces, invalid commands, cancellation and failed/partial
execution. 1,059 Python tests and all four Qt/QML/pet groups passed on Gentoo.
Display wake, exact unsaved-buffer recovery and physical homecoming acceptance
remain separate.

Scenes have a Preview button and explicit `preview activate <name> scene`
commands. Preview prepares the same workspace/command plan and uses the dry-run
executor; it never applies scene HUD or quiet policy. Missing workspaces still
fail before effects. Negated, quoted and discussion text cannot become scene
requests. Three more tests check real executor dry-run results, unchanged scene
policy and grammar rejection. 1,062 Python tests and all four native Qt/QML/pet
groups passed. This is a plan preview, not proof of live restoration.

Optional media ducking is available in Settings under Voice, off by default.
While speech plays, Carlos lowers existing Playing MPRIS players with writable
volume to 35% of their observed volume, then restores the original. It never
changes the sink, microphone, output device, mute or playback state. Paused,
zero-volume, unsupported and amplified players are skipped. Restoration checks
the unique player owner and current volume; user changes and replacement
players are preserved. Failed restoration retains its record, reports
RESTORE_PENDING and retries on the next speech/close. Startup ducking has a
one-second deadline; restoration has a two-second deadline.

Nine new tests include actual typed D-Bus volume writes on an isolated session
bus, user override, player replacement/disappearance, cancellation after a
write, speech cleanup failures and setting persistence. 1,071 Python tests
passed. Existing native settings render this new boolean field dynamically;
no native/UI files changed. Unsupported players do not duck. The isolated bus
fixture emits no audio and does not alter the user's players.

Wake speech backup now confirms a candidate using the main local STT adapter
before emitting a wake. A small-model transcription of “Car loans are getting
expensive” incorrectly began with Carlos; main-model confirmation rejected that
exact PCM sample. Verification failure or an attention/privacy change discards
the candidate. Both transcription stages share an eight-second deadline. The
primary keyword listener is unchanged; this adds latency only to the speech
backup. Five new regressions cover rejection, confirmed command provenance,
unavailable verification, stale attention and manager wiring. 1,076 Python
tests passed; 18 speech-wake tests passed in the development tree.

`carlos/scripts/check-offline-voice.py --run` requires a disposable Linux network
namespace with loopback only. It synthesizes silent Piper samples, runs the
actual keyword worker, neural VAD, main STT and configured small STT backup.
`--samples` caches only the fixed synthetic corpus for identical-audio
comparisons. Eight samples passed after confirmation: Hey Carlos plus three
standalone Carlos samples, and four negative phrases including car loans/call
us. This is synthetic offline acceptance, not normal-distance or media/AEC
acceptance. Earlier freshly synthesized short-name samples occasionally missed
both recognizers, so this does not establish a physical wake success rate.
No microphone, playback, desktop changes or cloud request is involved.

The optional offline fixture flags also exercised real inference: `--include-model`
loaded the owned conversation model in 1.636 seconds and returned a short reply
in 1.122 seconds. `--include-vision` described a temporary synthetic image of a
red circle and blue square correctly in 22.814 seconds, with no external route,
no tools or coordinate actions. Each model child was closed afterward. This is
a single synthetic image, not general scene accuracy or desktop interaction
acceptance; the visual result remains explicitly unverified inference.

Diagnostics and sanitized support bundles expose measured event delivery: current
queue depth/capacity, subscriber count and dropped deliveries since core startup.
Drops count subscriber deliveries, so one event lost by two subscribers counts
twice. Tool counters distinguish successful execution, failed execution,
verified results and unclassified legacy results. No observations means a null
success percentage. Composite plans, private-session results and non-tool
sources are excluded; no payloads or tool names are added to these metrics.
This is execution accounting, not a labelled desktop accuracy benchmark.

Five new tests cover overflow accounting with Queue.join balance, failure
results, separate verification, exclusions and malformed metadata. 1,081 Python
tests passed, plus the five development tests. Existing diagnostics render two
new observation rows; those rows do not decide overall component health.

`carlos/scripts/check-model-owner-recovery.py --run` exercised abrupt owner
SIGKILL against disposable real llama.cpp and whisper.cpp servers. A second
adapter refused takeover while the original owner was alive. After that owner
exited, the adapter stopped and reaped the exact recorded orphan and loaded a
fresh ready server. End-to-end fixture times were 11.667 seconds for llama and
0.835 seconds for Whisper, including initial loading and the live-owner check.
Private ports/storage and pidfds constrained cleanup to fixture children; the
running Carlos core and its models were unchanged. This tests model ownership
recovery, not the entire Core/HUD failure or physical chaos matrix.

Native voice status now shows active thinking, execution, speech and approval
waits before an idle microphone policy label. A paused/muted microphone no
longer hides a typed request's progress. Speech synthesis says Preparing your
reply; resource suspension says Voice paused / resource limit. The native
interaction test covers those combinations, and all four Qt/QML/pet groups
passed after rebuilding. No OS appearance or microphone policy changed.
The Python suite remains the last 1,081-test result; no Python source changed
in this status-label pass.

Explicit preview requests now stop before ordinary reasoning when the planner
cannot safely construct a plan. They return preview_unavailable with zero
actions and are not resumable tasks. Prefixes preview, please preview and
dry-run use the dry executor; quoted text to type is not treated as policy.
Three new boundary tests and all 1,084 Python tests passed. General previews
of model-generated actions remain unavailable rather than running those actions.

Core failure acceptance now has a reusable check-core-recovery.py --run fixture.
It launches the real core on a separate session bus and temporary XDG storage,
with audio/display endpoints isolated and speech disabled. A duplicate core was
refused. SIGKILL closed the old IPC stream; explicitly restarting the fixture
produced responsive IPC in 469.5 ms. A completed real file-tool receipt survived,
and the file kept its inode, mtime and SHA-256 after recovery and another local
request. A seeded unfinished receipt became INTERRUPTED_UNCERTAIN. This does
not prove automatic HUD activation, interruption mid-mutation, or boot timing.

The native client now invalidates current wake/provider/telemetry/readiness,
approvals, activity and active-plan readings when IPC disconnects. Conversation
history remains visible. The real socket test verifies invalidation; existing
reconnect/no-replay tests passed with all four native groups in 11.63 seconds.

Structured and stderr logging now redact recognized credential patterns after
message interpolation and from exception tracebacks, as well as from structured
fields. Two real-handler tests write disposable logs, capture stderr and verify
that credential canaries never reach either output while ordinary error text
remains useful. All 1,086 Python tests and both development tests passed.
This protects recognizable credential patterns; it is not a detector for an
unlabelled arbitrary secret. Existing historical log files were not rewritten.

Activity history is now optional, off by default, and available in Activity →
Saved history. It records selected scalar metadata, keeps the newest 2,000
events and never records private events. Clearing requires approval, preserves
other memory tiers and discards older events still waiting in the queue. New
events after the clear can still be recorded. Old history is retained while
recording is off; legacy payloads are filtered when displayed. Nine tests cover
the actual persistence worker, setting changes, filtering, retention, private
mode, approval requirements and the clear boundary.

The history pass reproduced a worker-thread queue race: synchronous tools could
publish while the event consumer was waiting, causing a lost wakeup under asyncio
debug checks and a stuck cancellation. Publications now deliver on the owning
loop through a bounded ingress queue, preserving priority, sequence and original
recipients. History/counters and privacy transitions share the publication lock.
Eight thread tests cover waiting/cancelled consumers, order, overflow, bounded
ingress, new subscribers and privacy. The stalled insight-worker test passes.

All 1,103 Python tests passed in 53.374 seconds; nine development timeline tests
and eight development thread tests passed. All four native groups passed in
11.85 seconds, including saved-history empty states and the actual socket route.
The native UI clears its saved-history cache on disconnect and privacy changes.
This is workstation metadata, not full room observation or mobile login history.

Project notes use a separate table keyed through a canonical, allowed project
directory. Ten focused tests cover exact scope, symlinks, wrong-project deletion,
real executor approvals, guest refusal, RAM-only private changes, explicit
preferred-project context, response bounds, reopen and an actual pre-table schema
upgrade preserving global notes, project records and conversations. All 1,112
Python tests passed before the additional migration case; the final ten focused
tests passed in both public and development trees. Four native groups passed in
16.22 seconds, including actual socket response ordering and scoped memory UI.
Long notes now expand instead of overlapping a fixed-height row. Installed
read-only project search completed with zero notes without adding host test data.
Project notes are scoped; conversation history is still shared at this stage.

Conversation context now captures the explicitly selected project once per
request and filters model chat history and project-note hints by that scope.
Assistant receipts inherit the initial turn's scope, including actual pending
approval completion after a project switch. An unavailable scope read fails
without sending general history to the provider. Selecting a project clears
working window references; an explicit general selection keeps all saved data.
A legacy database upgrade leaves unassigned conversations in general history.
The native client rejects late history and older-project reply payloads.

All 1,124 Python tests passed in 60.535 seconds. Eleven development isolation
tests and ten development project/upgrade tests passed. Four native groups
passed in 13.14 seconds. The actual installed IPC returned a consistent scoped
conversation list without dumping or changing host content. Task journal and
optional activity history are workstation records, not isolated chat sessions;
no claim of complete project data segregation follows from this change.

Dated recall is a read-only local tool with literal text filtering, exact project
scope, recognized credential filtering on read, bounded UTF-8 output and explicit
empty/partial results. Eight tests cover local evening windows, spring/fall DST,
early morning caps, scope changes, undated note labels, legacy credentials, query
parameters, private/guest rules and provider-free natural requests. Task receipts
do not duplicate recalled content. All 1,132 Python tests passed in 61.799 s;
eight development tests passed. The no-route network namespace passed seven
actual IPC checks, including scoped dated recall and its natural command. The
installed read completed without adding fixture memories or exposing content in
the report. History remains limited to data Carlos actually recorded.

The separate local HoloHand companion received a native queued-navigation fix:
150 ms single-use claims, trusted KWin peer checks, revocation on uncertainty or
pose exit, and focus/clock checks in the compositor callback. Five native groups
and standalone ASan/UBSan checks passed; the installed endpoint refused an
ordinary client's claim. Window state and calibration-file state were preserved.
Current host status is CALIBRATION REQUIRED, not physical acceptance. HoloHand
source is not included in this assistant-only public repository.

Hand-controller diagnostics now read optional pipeline counters and verified
peer PID/start ticks over the existing private socket. Eight rate/sampling tests
and six control tests cover counter resets, PID reuse, missing protocols, pause
zeros, failed observations, cancellation, invalid duration and no-launch behavior.
All 1,142 Python tests passed in 61.302 s; both development groups passed.

The actual existing Gentoo instance was sampled for 10.001 seconds after build
workers finished: capture 30.00 FPS, inference 18.80 FPS, 49 available observations
and no failed reads. Sampled inference duration was 12.94 ms mean / 25.70 ms p95;
result age was 54.92 ms mean / 82.00 ms p95. No hand was detected in those samples,
and input remained CALIBRATION REQUIRED. No physical gesture/action latency or
accuracy follows from these figures. Five companion native groups passed (0.52 s);
installed `holohand.measure` IPC returned a verified continuous metadata sample.
The companion source/counter addition remains outside this public repository.

Uncalibrated HoloHand now stops its camera/inference/alignment pipeline while
hidden or minimized; calibrated control remains active in the background and
pause always stops capture. Wayland minimization comes from the trusted KWin
peer watching only the app's exact PID, since Qt does not report that state on
this host. Ordinary clients were refused by both new metadata endpoints.
Six companion native groups passed in 0.50 s. Actual minimized sampling showed
1.0% of one CPU core over five seconds (earlier active sample: 69%), no camera
FD and zero counter increments. Activating the preview reopened /dev/video0
and captured 30 frames in a one-second interval; reminimizing closed it again.
Calibration-file state and window state were preserved; input stayed disabled.
Carlos reports pipeline demand and excludes idle timing readings rather than
presenting stale model results as live latency. All 1,144 Python tests passed
in 64.575 s. This is pipeline verification, not physical gesture acceptance.

Task journal lookup and explicit continuation are now pinned to the selected
project, including model receipt pages and CLI/IPC list/get. Existing unassigned
tasks stay in General through an additive schema upgrade. Parent scope is checked
inside the database transaction; wrong-project revisions do not cancel active
work. Captured request scope survives mid-request selection changes. Pending
approvals stay blocked until explicitly resolved; restart marks uncertain steps
without replay. Private scope changes stay in RAM and guest cannot inspect saved
project receipts. Fifteen focused scope tests passed, and 58 development task
checks passed. The actual no-route namespace passed eight IPC checks, including
cross-project list/detail filtering and refusal to continue unrelated work.
All 1,159 Python tests passed in 67.483 s. Activity metadata is still workstation
history; this change does not claim every Carlos record is a private project silo.

The KWin bridge now reads the native maximize mode when available, instead of
classifying an ordinary area-sized window as maximized. Restore clears tile,
fullscreen, minimization and native maximize state directly; it never toggles
or deliberately focuses another window. Older APIs retain the area fallback.
The actual bridge JavaScript runs under QJSEngine tests for native/partial/legacy
state, idempotence, focus preservation and expiry. Five native groups passed in
12.59 s, three development state checks passed, and all 1,162 Python tests passed
in 68.106 s. Actual installed IPC passed maximize → minimize → restore, repeated
restore, area-sized ordinary placement and unchanged unrelated focus on an owned
disposable GTK window; native modes were 3 then 0 and the test window was closed.
No existing application content or desktop layout was changed by that test.

The pet uses a persistent metadata-only panel subscription, not a 15-second
privacy poll. It hides and clears contextual comments at the start of a privacy
transition, and remains hidden for unknown/missing/invalid policy or core loss.
The stream sends only privacy plus a generic state. Six IPC tests cover immediate
updates, failed slow transitions, transcript/activity exclusion, guest denial,
duplicate subscriptions, subscription-kind isolation and disconnect cleanup.
Native policy tests cover clearing, invalid state and loss. All five native
groups passed (12.51 s); all 1,168 Python tests passed (65.925 s), followed by the
added real-transition fixture and six passing subscription checks. Development
subscription/panel checks passed and the installed development core's missing
privacy flag was corrected without changing unrelated checkout behavior.

An actual temporary installed pet reported its visibility property hidden 4.2 ms
after a DO NOT LISTEN request began, its bubble was empty, and it reported visible
after Normal was restored. This is status-delivery timing, not compositor frame
latency or room-acoustic mute timing. Pet settings were unchanged, including
fullscreen hiding and disabled auto-launch. A normal disposable window was used;
all owned test processes were closed and previous application focus restored.

The pet now uses the core's shared verified session-lock monitor, which already
covers the freedesktop endpoints plus GNOME/Cinnamon/MATE/Xfce and owner replacement
fixtures. Its duplicate one-path native lock check was removed. Persistent updates
and heartbeat replies carry only privacy, generic state and a lock boolean; unknown
lock evidence remains hidden. Fresh unlock is required after core reconnection.
All 1,171 Python tests passed (66.940 s), seven development stream checks and three
summary checks passed, and all five native groups passed (12.22 s). The actual
native Qt pet also passed five policy groups in an isolated D-Bus/offscreen
session: fresh unlock, lock/lost-owner/unlock, privacy, core disconnect and fresh
reconnect. This does not certify physical screen locking or every compositor.
No host desktop settings or lock state were modified by that isolated check.

## October 1: hardware telemetry

1,188 Python tests passed in 66.969 seconds. Seventeen hardware checks cover
shared frequency policies, reported units and sources, throttle baselines,
counter overflow/replacement, disappearing sensors, faulted fans, invalid
temperatures, read-only tool classification and offline routing. The final
bounded-reader changes passed those seventeen checks again. The actual
no-route IPC fixture now also runs a natural fan-speed request.

Ten live sysfs samples on Gentoo took a median 5.43 ms and a maximum 7.51 ms.
Eight clock policies, sixteen thermal counters and a Dell fan reading were
available. The installed core exposed the same readings through telemetry and
a read-only tool; GPU load was unavailable on this Intel driver.

Counters do not prove instantaneous throttling. AMD GPU paths were exercised
with a disposable sysfs fixture, not AMD hardware. Thermal load/overheating
acceptance, other machines and remote stream adaptation remain unverified.

## October 1: speech readiness

1,196 Python tests passed in 68.454 seconds. All five native groups passed in
12.55 seconds. Speech readiness now combines current child-process liveness
with recent supervised health observations. Fresh READY history cannot hide
a stopped process; missing, stale or pre-resume observations remain unverified.
The status includes separate VAD, STT and TTS rows, with explicit disabled and
on-demand modes. Disabled neural VAD and on-demand engines do not spend
automatic repair attempts.

A separate real local VAD worker accepted a PCM request, was terminated, and
changed from READY to STOPPED before the old health row changed. One verified
repair restored READY and accepted a new request in 480 ms. No microphone or
playback was started by this fixture. This does not establish acoustic
readiness, physical barge-in latency or whole-system recovery time.
