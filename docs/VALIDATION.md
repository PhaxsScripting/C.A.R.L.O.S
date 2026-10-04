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

## October 1: CPU sensor selection

1,202 Python tests passed in 69.110 seconds. Twenty-three hardware checks now
include GPU/NVMe/board exclusion, hottest-package selection, AMD die preference,
control-only labelling and spoken answer semantics. The Gentoo Intel package
sensor remained readable. Other CPU sensor layouts were tested with disposable
sysfs files; they are not physical AMD or ARM acceptance. Unknown CPU chips
return unavailable instead of relabelling an unrelated hot device.

## October 1: notice overload

1,215 Python tests passed in 68.454 seconds. Twelve notice tests cover priority
delivery, FIFO ties, bounded retention, rejected/evicted accounting, join after
replacement, waiting consumers, shutdown, diagnostic content filtering, repeat
cooldowns, thermal escalation and a temporary real core integration fixture.

With a simulated notifier held stalled, the real core's IPC health response
took 0.225 ms in one sample and telemetry advanced. A synthetic emergency was
retained during 100 background events and delivered first after release. The
32-slot queue discarded 69 background notices and no emergency; no actions were
executed. This is IPC/persistence acceptance with a simulated notifier, not an
actual desktop notification, acoustic preemption or end-to-end latency target.

## October 1: notice text

1,218 Python tests passed in 69.089 seconds. The final fifteen notice checks
passed again after boundary handling was added. New checks inspect the actual
subprocess arguments with a simulated notifier: recognized credentials and a
long complete private-key block are filtered before truncation. XML parsing
confirms literal markup, complete entities at the display boundary, and removal
of invalid control/surrogate characters. No real desktop notification was sent
by these checks; arbitrary unlabelled secrets remain outside the filter.

## October 1: Guest clock

The seventeen Carlos integration checks passed in both source trees after
adding the current `system.clock` name to Guest's existing basic-information
allowlist. The direct tool and natural time request return a read-only clock
observation; personal history and memory access remain denied. Mode-transition
voice callbacks are isolated in this fixture, so it does not test physical
guest use or audio. The preceding full suite result remains 1,218 tests.

## October 1: monitor nicknames

The full Python suite passed 1,263 tests in 71.653 seconds. The final thirteen
monitor checks passed in both source trees, including an additional real core
IPC fixture with simulated displays/windows. Naming, listing, a changed-port
move and postcondition readback all stayed local. A disconnected panel stopped
before mutation; Guest denied nickname reads and writes.

The fixture also covers replacement panels, duplicate hardware identities,
unknown active output and vertically stacked displays with tied horizontal
edges. Fresh metadata from the actual two-output KDE desktop resolved a saved
nickname in a disposable database. No host display names or layout changed.
Actual monitor hotplug and a physical window move are separate acceptance work.

## October 1: OCR worker lifecycle

The full Python suite passed 1,270 tests in 72.455 seconds. Six new lifecycle
checks cover real owned worker cancellation/timeout, a busy-worker refusal,
responsive temporary core IPC, malformed/oversized output and cancellation
during process creation. A second cancellation still joins the returned child;
a failed spawn preserves cancellation. Twelve perception and twenty-two
security checks passed with the shared bounded runner.

The real installed RapidOCR runtime recognized CARLOS RETRY 42 in a generated
PNG and exited afterward. With the final OpenCV cap, one sample took 3,059 ms,
peaked at seven process threads and sampled 369 MiB RSS. This is a synthetic
image check, not desktop recognition accuracy or a latency distribution. No
host screen, microphone or audio output was used. OCR results explicitly remain
unverified inference for scene truth and do not grant coordinate actions.

## October 1: monitor identity through execution

1,272 Python tests passed in 71.538 seconds; all five native groups passed in
12.18 seconds. Plans carry resolved display metadata into the move. The Python
preflight rejects changed/duplicate hardware, KWin checks identity again at the
mutation, and postcondition readback cannot verify a later replacement. Native
QJSEngine tests execute the actual bridge source for matching, replaced, duplicate
and changed-connector cases; simulated topology is not physical hotplug.

The installed core and actual KWin moved a disposable owned GTK window to the
external HDMI display with verified identity, then to the laptop connector.
Both placement readbacks passed and the unrelated foreground window stayed
unchanged. The laptop lacks a usable unique serial and therefore has explicit
connector-only coverage. No other windows or saved nickname preferences changed.

## October 1: capability probe evidence

1,276 Python tests passed in 74.366 seconds. Four new status checks preserve
the existing four execution-evidence tests. Tailscale Connected requires exit
zero, valid bounded JSON, Running state and local node identity/address evidence.
Failed commands, invalid JSON and missing node data remain UNVERIFIED. These
observations do not establish Internet, school-network or cellular reachability.
Returned capability/status dictionaries cannot mutate the internal probe cache.

## October 1: Core process-tree resources

1,285 Python tests passed in 72.373 seconds; nine final resource checks passed
in both source trees. Five native groups passed in 12.72 seconds after changing
the two main-process metric labels. The new read-only tool checks process
identities and separates summed RSS, proportional memory and interval CPU.
Tests cover process births/exits, PID reuse, counter resets, missing PSS,
incomplete reads, the detailed-read cap and cancellation before a second read.

The resource tool runs only on request and does not control processes or change
resource policy. Counts/measurement overhead and tree scope stay explicit.
No main-process memory figure is presented as all Carlos components.

## October 2: direct tool cancellation

1,298 Python tests passed in 81.403 seconds. Thirteen focused checks passed in
both source trees. Actual isolated Unix-socket requests ran a real owned Python
worker, stopped it through a second client, reaped its PID, returned a cancelled
reply and reused the original connection for health. Direct diagnostic calls
still create no implicit task.

Checks cover nested tools, repeated cancellation while a journal row is being
written, completed-evidence preservation, externally cancelled callers,
concurrent stop gates, stale state ownership and permission/confirmation races.
A synthetic transcript passed through the actual capture-stop lock and action
cancellation callback without reentering that lock or calling a language model.
A blocked synchronous fixture finished its write after cancellation; the reply
correctly left effects unknown and never replayed it.

This establishes execution cancellation and cleanup behavior, not microphone
recognition quality or acoustic interruption latency. Same-connection short-stop
queue preemption is being audited separately.

The installed three source files match the tested development tree and import
with the host Python. The session Core was stopped; installation preserved that
stopped state and did not launch microphone/model workers or the UI.

## October 2: IPC stop preemption

1,302 Python tests passed in 79.321 seconds. All seventeen cancellation checks
passed in both trees, and the nine existing IPC responsiveness checks passed.
Actual isolated sockets exercise all four short stop phrases on the same busy
connection, discard queued tool/command actions without invoking their executors,
and cancel accepted queued work on another client. Eight blocked status requests
do not consume the two reserved cancellation slots. Ordinary commands retain
FIFO ordering; application-specific commands cannot become accidental stops.

The installed IPC source matches the tested tree and imports with host Python.
Core was stopped and remains stopped; no UI/model/microphone workers were launched.
Physical acoustic interruption timing remains unmeasured.

## October 2: engineering validation cancellation

1,306 Python tests passed in 81.209 seconds. Four new cancellation checks passed
in both trees; all nine existing coding-gateway checks passed. An actual isolated
Git worktree ran a fixed Python test suite with a child ignoring SIGTERM. Cancel
stopped its owned process group, preserved the dirty worktree, left the live
project unchanged, and created no review commit. A separate actual child-group
fixture confirms cancellation kills the descendant after its group leader exits.
The test allows an exited descendant's temporary zombie state while init reaps it.

Pre-cancelled review commands never spawn or stage files. Excess validator output
is bounded, and validator JSON cannot spoof engineering progress events. These
checks use an explicitly labelled CLI fixture; the previously recorded real
Codex integration remains separate. Installed code matches and imports; session
Core stays stopped. No automatic source deployment is introduced.

## October 2: engineering tool deadlines

1,309 Python tests passed in 82.390 seconds. Three new tool-lifecycle checks
passed in both trees. Actual ToolRegistry executions of an isolated CLI fixture
were cancelled and timed out; both paths set the shared job cancellation token,
joined worker cleanup, reaped the owned process and left a CANCELLED job receipt
without a review commit. A token cancelled before thread registration prevents
preflight and worktree creation. The fixture is not a real Codex latency sample.

The two installed source files match and import with host Python. Core remains
stopped; no resident workers/UI were launched.

## October 2: tool contract boundaries

1,316 Python tests passed in 82.736 seconds. Seven focused contract checks passed
in both trees. The full strict/read-only/risk permutation table confirms
DESTRUCTIVE and PRIVILEGED cannot lose confirmation. Actual temporary Core
requests with strict permissions disabled return confirmation_required without
invoking critical executors. Catalogue input/output schema mutations do not
change later validation.

Invalid names, risks, executors, schemas, flags and deadlines cannot register.
Plugin preflight stays atomic when a later declaration fails the new checks.
Nested NaN/infinities fail JSON output validation without retry. Contract gaps
remain explicit; this does not claim the older tools' offline/undo semantics
have all been independently accepted. Installed files match and import with
host Python; Core remains stopped.

## October 2: live engineering status questions

1,325 Python tests passed in 86.233 seconds. Eight new status checks passed in
both trees, plus the four engineering tool-lifecycle checks in the development
tree. An actual isolated same-socket question returns current simulated job
metadata while its owned action stays blocked; no model call, action interruption
or implicit task receipt occurs. Other mutations stay blocked if the display
state is idle while an action remains owned.

The existing actual isolated Git/validator fixture now also observes the
VALIDATING phase from its tracked running worker before cancellation. Snapshot
mutations do not corrupt later status, phase/elapsed rows refresh under cached
CLI readiness, and a stale running receipt cannot imply live execution.
A blocked CLI status probe leaves health responsive; a privacy transition
discards its result. Exact phrase/fast-command/IPC routing and transcribed
interaction-state completion passed. Physical spoken recognition remains
unverified. No native UI appearance changes were made in this batch.

Six installed source files match and import with host Python. Core remains
stopped, preserving the current Minecraft session's background state.

## Diagnostics responsiveness, October 2

Capability queries and self diagnostics gather CLI readiness and AT-SPI observations outside the owner loop. Their result builders stay on the owner loop; tools, event history, voice state and desktop input are not moved into worker threads. Both requests can pass an in-flight tool on the same IPC connection.

Validation: four isolated Core/Unix-socket checks in both source trees held each backend probe while a tool remained active, received health on the same connection, then completed diagnostics without interrupting the tool. The full public Python suite passed 1,329 tests in 86.789 seconds. Installed source imports and hashes passed while the desktop Core remained stopped. This is automated IPC evidence, not a live voice or authenticated engineering acceptance.

## Local screen clips, October 2

Added four typed desktop.recording tools for capability status, explicit capture, listing and exact-ID deletion. Capture has mandatory confirmation plus native source selection, a one-to-120-second bound, two encoder threads, no audio/network upload, a private 64 MiB artifact limit and free-space guards. A separate owned native GI worker uses the granted PipeWire FD, prefers stream serials when provided, keeps static screen timestamps alive and verifies the finalized Matroska video through ffprobe. Only a checked, hashed private file is published; Stop joins cancellation and discards the unfinished clip. Linux parent-death protection stops the capture worker after a Core crash. The helper's GStreamer registry cache and optional private plugin are isolated from the audio setup.

Fifteen recording tests passed in both source trees. These include a real native H.264 test-pattern clip decoded independently through ffprobe, real fixture-worker cancellation/reaping over an isolated Core's Unix socket, same-socket status while recording, confirmation/Guest policy, denied/timed-out/revoked portal fixtures, FD/session cleanup, symlink guards and joined file verification. The full public Python suite passed 1,344 tests in 90.568 seconds. The six existing Linux bootstrap tests passed with the extended package profiles. No native UI source changed.

On the development KDE host, the actual ScreenCast portal advertised version 5 with monitor/window sources. Real CreateSession and SelectSources requests and a Close acknowledgement passed without calling Start or opening a selector. Installed imports, file hashes and the native dependency/portal probe passed while the desktop Core stayed stopped. Actual selected desktop capture, other compositor sessions, source hotplug and acoustic/device acceptance remain unverified. The dependency probe does not assert a working capture grant.


## Audio undo, October 2

Output volume, relative adjustments and mute changes retain up to 32 in-memory undo records for ten minutes. Undo checks the original local socket instance, sink index/serial, channel order and affected setting before writing, then reads back exact channel values or mute. Later changes to the affected setting are preserved; other settings and the current default output stay intact. Explicit volume/mute requests cannot undo the other setting. Missing metadata or original amplification above 100% disables undo without disabling normal controls. Observations and writes are separate, so an external adjustment during their interval cannot be excluded. Stop is not automatic rollback.

Twelve audio checks passed in both source trees, including actual PipeWire/Pulse protocol reads and writes on an isolated private server with a two-channel null sink. Exact asymmetric channel restoration, independent mute/volume changes, manual overrides, replacement/restart refusal, bounded/expired history, no-op/failure handling and command authority were covered. Nine existing wait/cancellation checks passed with complete synthetic audio readbacks. All five audio tools now declare offline, reversibility and output-schema contracts. The final full public Python suite passed 1,356 tests in 88.373 seconds.

Four installed source files passed native imports and contract preflight, then a graceful restart and actual Core health/catalogue/empty-undo IPC checks. Protected Plasma/KWin configuration hashes stayed unchanged. No real playback level was changed during validation. This is native protocol and IPC evidence; physical speaker/headphone quality, cross-distro acceptance and the remaining full specification are still incomplete.


## Window undo, October 2

Window reversal now lives in its own module. It restores native horizontal/vertical/full maximization, fullscreen and minimized flags independently, and complete multiple/all-workspace assignments. History captures the original monitor metadata. Restoration refuses unavailable or ambiguous monitors, removed workspaces, changed window identity, invalid state/geometry, displaced monitor placement and unsupported compositor tiles before any mutation. A saved unique monitor serial can follow a changed connector; missing serials explicitly use connector-only checks. Optional window_id pins undo to the caller's exact target. Verification checks final geometry, exact workspace membership, native maximize mode, window identity and guarded monitor identity; an incomplete restore keeps the record instead of claiming success.

Nine focused checks passed in both source trees. They include the actual KWin bridge JavaScript evaluated by Qt's JavaScript engine, with partial maximize axes, multiple/all-workspace assignment, legacy commands and invalid-command refusal. The final full public Python suite passed 1,365 tests in 92.257 seconds.

Four installed files passed preflight, graceful restart, catalogue/empty-undo IPC checks and an actual compositor snapshot with focus preserved. A disposable native Qt/Wayland window then passed fullscreen, minimize, restore and three exact undo operations, with hardware identity verified at each undo. The optional ID guard refused older history belonging to another window. The child was reaped and original focus restored. Protected desktop configuration hashes remained unchanged during installation.

A separate disposable GTK/Wayland test received a fullscreen configuration, but both Carlos and KWin's independent getWindowInfo still reported the old geometry/fullscreen state after three seconds. It was reported as failure and cleaned up. Its cause remains unresolved. This is a remaining acceptance gap; the successful Qt test does not establish GTK, monitor hotplug, partial-maximize or multiple-workspace behavior on every real application/desktop. Tile restoration and layout changes after topology movement are refused rather than approximated.

The maximize-axis and all-workspace semantics follow KDE's KWin Window API: https://develop.kde.org/docs/plasma/kwin/api/ and the installed KWin headers.


October 2 managed settings reversal:

- 1,379 Python tests passed. The settings group includes 25 tests and a real isolated Core Unix-socket save/undo/health sequence using disposable config files. The development tree passed its 24 settings tests.
- Cancellation, failed voice initialization, external config edits, wrong-key guards, expiry, bounded history, private-session refusal and hard-mute transitions are covered. Recovery restores only the affected field and does not overwrite unrelated edits.
- All five native Qt test groups passed, with the changed interface group rerun after correcting its repeater lookup. Native client tests cover duplicate requests, matching response/error cleanup, disconnect cleanup and no replay. The interface checks undo availability and disabled controls during pending requests or disconnection.
- Three installed Core files and the control-center executable were backed up and updated. Native imports, all three settings contracts, read-only settings inspection, empty undo refusal, health and focus preservation passed. Protected Plasma configuration hashes were unchanged. Real microphone recovery and other-desktop acceptance remain separate.


October 2 closed-window handling:

- 1,380 Python tests passed. Focused desktop-world and undo groups passed in both source trees. Tests cover a closing window beside a new window with the same title, exact-ID refusal and stale active-window refusal.
- The real Qt JavaScript engine verifies that snapshot skips deleted animation entries without reading their obsolete properties and that all ten window mutation routes reject their IDs. The native Qt bridge group passed.
- An owned Qt/Wayland window was observed, closed through the installed tool and independently reaped. Its old ID then failed activation without changing focus; original focus was restored. No user window was closed.
- The installed world model and KWin bridge were backed up and updated with healthy Core restart, current bridge readback and unchanged protected Plasma configuration. This addresses deleted animation targets; the previously observed GTK fullscreen mismatch remains unresolved.


October 2 native power-profile portability:

- 1,388 Python tests passed. Eight new power-profile tests and ten existing native-settings tests passed; corresponding development tests passed.
- Fourteen actual checks ran on a disposable D-Bus session for both current and older daemon interfaces. They cover native registered-tool routing, exact readback, external changes, replacement owners, malformed properties and loss of the daemon after dispatch. The real system bus was not used by this fixture.
- Two installed modules were backed up and updated. Native imports, both explicit tool contracts, read-only live inspection, healthy Core restart, focus preservation and protected Plasma hashes passed. The first installation check caught a blank unavailable KDE profile; rollback restored both source states, and the corrected result now uses null.
- This laptop reports no usable profile backend. No live power-profile write was attempted. These checks validate the protocol and unavailable state, not a physical power-policy test across all distributions.


October 2 HoloHand swipe and control identity:

- The optional three-finger horizontal swipe is disabled by default. It requires stable entry, palm-relative distance and speed, horizontal direction, confidence and a full hand. It emits one four-step horizontal wheel burst; tracking uncertainty, jumps, pause and timeout cancel it. Pose exit and cooldown are required before another swipe.
- All seven native companion test groups passed. Dedicated cases cover 20/30/60 FPS, both directions, repeated holds, slow/diagonal motion, jumps, crop, loss and hot disable. AddressSanitizer and UndefinedBehaviorSanitizer checks passed. These synthesized observations do not establish real-hand accuracy.
- Carlos exposes optional swipe status and verifies pause/resume against the exact peer PID and process start ticks. Missing identity or a replacement process cannot produce verified success, and no request is replayed. Both integration contracts are explicit. Ten focused integration tests passed in each source tree; the full public Python suite passed 1,391 tests in 92.183 seconds.
- The companion executable was backed up and installed while stopped. Libraries, saved calibration and protected Plasma configuration were preserved; it remains stopped, with no camera opened. The integration module was backed up and deployed through a graceful Core restart; native imports, contracts, health and read-only unavailable status passed. Physical gestures and calibration remain unverified.


October 2 gaming yield and visual proposals:

- Carlos now pauses ambient wake listening while an owned Minecraft Java client is running. The gate is separate from manual pause, wake preference and privacy mode. Startup skips background model prewarm during gameplay; automatic worker recovery also yields. Typed commands and explicit microphone capture remain available. Two complete empty process observations are required before resuming. Missing inventory evidence does not resume listening. The policy can be disabled with `resources.yield_ambient_for_minecraft=false`.
- The observed live wake recognizer used 83.9% of one core and its echo worker 21.3%. After temporary wake pause, the recognizer was idle and the echo process exited; sampled total CPU fell from 27.5% to 10.5%. Minecraft heap inspection found no full collections, with about 618 MiB used in a 4 GiB reserved heap. These samples identify competing load; they do not establish an FPS or frame-time improvement. No game process, world, graphics setting, compositor or audio output was changed.
- Local OCR validates private owned single-link PNG input, reads bounded immutable bytes, checks file hashes after inference and validates finite four-corner convex boxes and matching centers within the image. Parent output validation rejects malformed geometry; returned text is derived from the checked elements.
- `vision.candidates` locates exact text in a native window and returns private highlighted proposals. Duplicate labels remain distinct. Exact process lifetime, window identity/geometry, output metadata and image hashes are checked before review. The twenty-entry history expires after sixty seconds; deletion, privacy transitions and cancellation revoke captures/previews. Proposals do not send input or certify scene accuracy. Direct visual click/transition remains the next step.
- All 1,412 Python tests passed in 91.881 seconds. Eight gaming checks passed in the development tree. Twelve proposal checks include actual generated PNG highlights. A real isolated OCR worker recognized the disposable fixture in 3.323 seconds, sampled seven threads and 382 MiB peak RSS, then exited. No desktop image or microphone recording was retained by that fixture.
- Twelve installed modules were backed up and deployed through a graceful Core restart. Native imports, both proposal contracts, gaming-state observation, Core health, original focus and protected Plasma configuration passed. No desktop input was sent and no user screen captured during deployment. Acoustic voice effects, physical gestures and cross-desktop acceptance remain separate.

October 2 confirmed visual clicks:

- Added mandatory preview confirmation, one-use candidate consumption, a fresh pixel comparison and native result checks. Candidate expiry/privacy revocation also apply during execution. PNG metadata changes alone do not change pixel identity. Preview publication and threaded cleanup now share a lock, and candidates become visible only as a complete batch.
- Twenty-five visual checks passed in both source trees. An actual isolated Core socket exercised request, preview, approval, native transition and rejected approval-token replay. The input fixture uses the real pointer guard/press/release implementation with an isolated transport; cancellation after pressing released the button. Changed images, moved targets, already satisfied/unobservable goals, missing delivery and absent results never retry.
- All 1,425 Python tests passed in 92.272 seconds. Five native groups passed, including missing/loaded-preview gating, keyboard denial and duplicate submission. The development confirmation component also passed its separate Qt checks.
- On this KDE/Wayland desktop an owned Qt window was captured and its Retry label recognized at 1.0 OCR confidence. A 480 by 265 highlight preview passed private file and native identity checks. The fixture deleted its captures/previews, reaped its child and restored the original foreground window. This verifies the capture/OCR/proposal path on that fixture, not general scene accuracy or actual compositor click delivery.
- Tested core modules and the native UI binary were installed with backups, native import/contract preflight, graceful Core shutdown/restart and health checks. Wake-pause state and protected desktop configuration were preserved. Actual device clicks and broad application acceptance remain separate.

October 2 visual command flow:

- Added explicit local commands for visual proposals and preview-confirmed text clicks. The planner resolves one native window and binds that same ID to the click and native result predicate. Quoted labels remain literal; explanations, negations, hypotheticals, missing results and trailing action clauses do not become clicks. Duplicate labels require an explicit bounded ordinal; an externally supplied candidate ID is replaced during fresh preparation.
- The actual isolated Core socket now also exercised the complete typed-command/planner/preview/approval/result flow. Native input connection is requested only during execution of the bound confirmed action when needed; no operating-system grant is fabricated. Twenty-eight visual checks and seven command checks passed in both trees. UI checks also reject text-click approval without its loaded preview.
- All 1,435 Python tests passed in 92.621 seconds; all five native groups passed. An older cancellation fixture was fixed to atomically publish its PID readiness file after a loaded run exposed a partial read. This was a fixture coordination race, not a production cancellation failure.
- A fresh owned GTK3/Wayland window now passed fullscreen, restore, minimize and restore through the installed native tool with matching readback. It closed and original focus was restored. The earlier GTK failure was not reproduced in this run; this does not establish its historical cause or complete the application matrix.

October 2 local visual routing:

- A live installed typed-command check exposed an overly broad cloud-provider gate: the deterministic desktop flow was blocked despite never invoking the conversation model. Added a task-local scope for explicit desktop plans and authenticated local visual calls. Model and model-plan callbacks reset that scope before validating/executing tools. Confirmation origin is server-owned, stays outside the public payload, and survives approval without changing provider configuration.
- Thirty visual checks now include concurrent local/cloud requests, inherited-scope rejection on model callbacks, and the full typed-command/approval path with a cloud provider selected and both provider request methods forbidden. All 1,437 Python tests passed in 92.372 seconds; thirty-seven focused checks also passed in the development tree. The previous five-group native UI result remains applicable because no UI source changed in this patch.
- Installed source passed native import/contract preflight and health checks after a graceful restart. Desktop configuration and the complete Carlos configuration file kept their hashes. The installed command found Retry in the owned Qt fixture, returned its private preview, deleted its capture and preview, and refused review of the deleted candidate. No pointer/keyboard input was sent, the child exited, and original focus was restored. Actual compositor click acceptance remains unverified.
- The owned GTK3/Wayland fixture also passed four guarded undo operations after fullscreen/restore/minimize/restore. Every reversal matched native state, all its restore points were consumed, the child exited, and original focus was restored. This adds actual GTK undo evidence without claiming the full application/tile matrix.

October 2 offline desktop vision and failure guidance:

- The owned Qt capture/OCR/proposal path also passed in a user/network namespace with no Internet route. Keeping the desktop user's UID let Spectacle contact the existing compositor; mapping it to namespace root was refused. Capture, OCR and proposal code ran inside the no-route namespace; the host Core supplied native metadata and focus operations outside it. This is scoped offline vision evidence, not a whole-host network isolation claim. The fixture recognized Retry at 1.0 confidence, checked image/native identity, removed its images, reaped its window and restored focus. No input was sent.
- Ordinary setup, policy, ambiguity and stale-target gaps no longer propose coding tasks. Permission/privacy/target refusals are not replayed, including a failed returned result. Audited idempotent observations keep their one bounded retry for a transient disconnect. Unexpected defects and missing tools remain available for explicit engineering review; no task is created by reporting a gap.
- All 1,446 Python tests passed in 91.459 seconds and all five native groups passed in 12.93 seconds. Nineteen focused development checks and twenty-one development QML checks passed. The development interface check exposed a missing existing ListScrollBar component; restored its source and QML registration without replacing the development frontend.
- The tested planner and native UI were installed with backups, native imports/contract checks, a graceful Core restart, health verification and preserved configuration/focus. Native OS desktop-input authorization is still required; no grant was fabricated.
- Installed capability diagnostics reported missing desktop authorization as a setup gap with no coding task. A nonexistent saved native window ID failed after exactly one observation with no recovery/follow-up action; original focus was preserved and no input or speech requested. The first live check exposed this native stale-ID wording, which was added to the regression suite before the final deployment.

October 2 passive network diagnosis:

- Added explicit local network commands for both route families, interface state, resolver generator evidence, Tailscale daemon inventory and local mobile health. No configuration writes or DNS/Internet probes are part of this tool. Failed route inventories remain unknown, resolver comments do not establish current ownership, a daemon state does not verify peer reachability, and localhost mobile health does not verify public access.
- Thirteen focused tests passed in both trees. The complete 1,459-test Python suite passed in 91.519 seconds. The earlier five-group native UI result remains applicable because this patch changes no UI source. The first loaded suite exposed early cancellation return from the probe group; joined cleanup now handles repeated cancellation. A real three-child subprocess fixture reaped every owned child after repeated cancellation in 1.269 milliseconds, without network or desktop input.
- Actual source Core IPC exercised both natural commands without model callbacks and a dry run with zero attempts. On the host, both route inventories and resolver configuration were observed with the resolver checksum unchanged. In a no-route network namespace, both inventories correctly returned no default routes and Internet state stayed unknown. Tailscale's host daemon remained a separate local observation. These fixtures prove passive diagnosis and cleanup, not DNS resolution or Internet connectivity.
- Six tested source modules were deployed with backups, native import/contract preflight, a graceful Core restart and health checks. Carlos configuration, protected desktop settings and original focus were preserved.
- Installed Core IPC also passed the natural network question and dry-run command. Each real diagnosis made one typed observation; its verification scope was observation_only, Internet stayed UNKNOWN, and resolver/Carlos configuration hashes were preserved.

October 2 thermal warning escalation:

- A HIGH thermal sample no longer suppresses an EMERGENCY sample during the repeat interval. The producer keeps one repeat timestamp per priority, including repeated readings around the boundary. The first warning shortly after boot is eligible immediately. Missing, boolean, nonfinite or overflowing temperature samples cannot consume a warning budget; malformed threshold/interval settings use finite defaults without writing configuration.
- Eight new tests passed in both source trees, and all 1,467 Python tests passed in 91.685 seconds. A real temporary Core ran the telemetry producer, persistence, notice queue and IPC with synthetic 91 then 99 degree samples and a held notifier. The escalation reached the queue during the existing cooldown, was delivered first after release, and health responded in 0.403 milliseconds. No tool action or real thermal stress was performed. Acoustic preemption and physical thermal-response measurements remain separate.
- The earlier five native groups remain applicable; this patch changes no UI source.
- The tested telemetry module was backed up and installed through a graceful Core restart. Native import/observer checks and Core health passed; wake-pause state, gaming policy, original focus, Carlos settings and protected desktop configuration were preserved. Synthetic warnings stayed in temporary fixtures.

October 2 installed native visual action:

- The installed Core clicked Retry in one disposable Qt/Wayland window using the actual compositor grant and read back its fullscreen transition. The direct tool path passed in 1,176 milliseconds after approval. After a Core restart, the typed-command path restored the saved grant only during confirmed execution and passed in 1,207 milliseconds. Both private highlights were inspected before approval; each action sent one click, prohibited replay, removed its capture/preview, reaped the owned child and restored foreground focus and disconnected input state. No user application was clicked. This accepts that owned native transition, not general scene accuracy, fractional-scale mapping or the full application matrix. The typed result correctly kept independent goal verification false.
- An explicit `speak: false` on a confirmation response now scopes both streamed continuation and finished reply to silence. Default desktop confirmation speech remains unchanged. Four handler tests cover defaults, error cleanup and concurrent scope isolation; all 1,471 Python tests passed in 92.798 seconds, and four development checks passed. The previous five native groups remain applicable with no UI change. The service module passed installed import/contract checks, a graceful restart and health checks with unchanged Carlos/Plasma configuration. The owned native command used explicit silent submission and confirmation; acoustic speech acceptance was not measured.

October 3 application placement:

- Opening an app on a monitor now resolves the output before launch, reuses one exact installed-application window or launches once, then moves, focuses and independently reads back the enabled connector and focus. Duplicate windows/shared identities refuse rather than opening another instance. Concurrent requests share a bounded launch/wait lock, and cancellation joins an already-dispatched bounded launcher. Window presence remains distinct from app readiness and launch acknowledgement.
- Twelve new checks passed in both source trees; all 1,483 Python tests passed in 95.999 seconds. The previous five native UI groups remain applicable with no UI changes. Six modules passed native import/contract preflight and a graceful installed Core restart with unchanged protected settings.
- Installed typed commands launched one disposable Qt application in 1,448 milliseconds, then reused that same native window without launching in 838 milliseconds. Fresh output and focus goal predicates passed. Dry-run attempted zero actions; an unknown monitor failed before application discovery/launch. The owned process and temporary desktop entry were removed and original focus restored. Only eDP-1 is currently enabled, so this run does not accept cross-monitor placement or the wider application/desktop matrix.

October 3 window undo history:

- A conclusively closed/deleted window's undo record is discarded without replaying any earlier change. Incomplete, stale, hidden/special or truncated evidence retains the record. Explicit window guards still refuse a different target before observation or mutation. Successful restoration consumes its own exact bounded record, preserving a newer action that arrived while native readbacks were awaited. Failure guidance keeps the concrete undo reason and does not propose coding repairs for a closed target.
- Fifteen focused checks passed in both source trees, including six new history/evidence/race cases; all 1,489 Python tests passed in 97.060 seconds. The previous five native groups remain applicable without UI changes. Five source modules passed native import/contract preflight and a graceful installed restart with unchanged protected settings.
- The installed Core changed two disposable Qt windows. After the latest window exited, its guarded undo discarded only that dead record in 75 milliseconds, preserving the preceding window's geometry and foreground focus. A separate guarded undo then restored that preceding window and consumed its record. Both children were reaped and original foreground focus restored. No user window changed; broader physical/application undo remains separate.

October 3 installed voice/resource observations:

- Native diagnostics reported active microphone capture, wake, echo cancellation and persistent VAD/STT/TTS workers. One two-second Core-descendant sample measured 1,952 MiB PSS and 30.4 percent of one logical CPU; the model accounted for roughly 1,270 MiB in a separate component sample. These exclude UI, pet, mobile and reparented services and are not a clean desktop idle benchmark.
- The installed managed local model naturally released at the unchanged 900-second idle policy. Observation began at 899 seconds and read back release at 917 seconds, the owned process gone, Local AI ON_DEMAND and VAD/STT/TTS still running. Measured Core-tree PSS fell from 2,189,006,848 to 857,618,432 bytes. No forced unload, model request or configuration change was used to produce this result. A later source deployment restarts normal prewarming; this is policy acceptance, not a permanent memory level.
- The installed local speech transport synthesized one fixed generated question in 319 milliseconds, transcribed its 2.589-second PCM in 1,494 milliseconds and completed the read-only CPU query in 223 milliseconds. The public silent checker also passed through the actual Core: 299/1,517/221 milliseconds on its current workers. It rejects changed recognition before submitting anything; both runs used a repeated generated phrase with warm workers. No microphone route changed, audio playback occurred or audio was retained. Physical wake, barge-in, echo rejection and human-speech acceptance remain separate.
- Completed the resource observer's reversible/output-schema catalogue fields without changing its measurement implementation. Ten focused checks passed in both source trees, including complete, partial and failed result schemas. Native installed catalogue and live observation validation passed after a graceful restart with protected settings unchanged. The previous 1,489-test full suite and five native UI groups cover the unchanged measurement/UI implementations; this metadata/checker patch used its focused and actual native checks rather than claiming another full-suite run.

October 3 speech-stop timing scope:

- Generic typed/privacy/synthesis stops no longer overwrite the wake-interruption metric. Diagnostics expose separate stop latency, reason, scope and observation time. Pending synthesis measures setting a cancellation flag; a running player measures accepted stop to owned process exit. The dated wake metric updates only when an accepted wake interrupts a live player. Interruption events explicitly state that acoustic latency was not verified. Playback/cancellation behavior is unchanged.
- Five new scope tests passed in both source trees; all 1,495 Python tests passed in 96.146 seconds. The new temporary Core/private PipeWire/Pulse/Piper/paplay fixture stopped real null-sink playback through a typed hold-up IPC request in 7.739 milliseconds, with 0.677 milliseconds measured to player exit. The player was reaped, pending speech cleared, typed stopping left the barge-in metric empty, and the next read-only request completed. Worker/audio-server cleanup completed; host microphone/playback stayed untouched. This is backend stopping, not acoustic barge-in or heard speech quality.
- The voice manager passed staged native import/default-metric checks and installed health/default-metric readbacks after a graceful restart. Protected settings remained unchanged. Native UI code did not change; the previous five-group UI result remains applicable.

October 3 owned speech-worker recovery:

- The private audio fixture now kills and reaps its own idle persistent Piper worker, verifies a different live replacement, completes real paplay playback into an explicitly linked private null sink, and accepts the next read-only Core IPC request. Public playback started after recovery in 2,421 milliseconds; the mirrored development fixture measured 2,404 milliseconds. Both completed cleanup of workers and private audio servers. No host microphone, playback route or user application was touched.
- The first completion checks exposed missing ports/links in the minimal private audio setup; adding a private clock, configured native null sink and explicit private link fixed the fixture. No production voice change was needed. Existing 1,495 Python tests and native UI results remain applicable to the unchanged runtime. These checks accept between-request worker recovery and private software playback, not heard speech, room acoustics or an in-flight inference crash.

October 3 owned recognition-worker recovery:

- The native fixture first binds its own unrelated loopback listener and verifies that recognition prewarm refuses it without creating an ownership record or mutating the listener. It then uses an available ephemeral port and private runtime, transcribes generated PCM through a verified owned whisper.cpp listener, kills/reaps only that worker and verifies replacement identity and another successful transcription. Both source trees passed and removed workers, ownership records and the temporary runtime; no microphone, speaker playback or desktop command was used.
- Public first recognition/recovery requests measured 1,521/1,555 milliseconds; development measured 1,604/1,730 milliseconds. These include worker startup where needed and use a repeated generated question, not human or acoustic recognition. One earlier development run recognized the equivalent contraction What's my CPU usage; the fixture now accepts only that contraction or What is my CPU usage with bounded exact wording. It never submits either transcript as a command. Runtime adapters matched in both trees; no production change or reinstall was needed. Existing 1,495 Python tests and native UI results remain applicable.

October 3 bounded conversation references:

- Window pronouns, correction targets, numbered choices and model hints now share finite bounded monotonic-age checks. Missing, future, nonnumeric, boolean and overflowing timestamps refuse rather than guessing or crashing. Common that-one/this-one follow-ups preserve the saved native window ID; a missing ID never falls back to an app or title. Explicit named requests retain their normal resolution behavior.
- Seven new checks passed in both source trees; all 1,502 Python tests passed in 91.814 seconds. No native UI source changed, so the previous five native groups remain applicable. The helper, planner and preference context source passed staged native imports, a graceful installed restart, health/focus checks and unchanged protected settings.
- Installed actual Qt/Wayland checks refused a missing reference without changing focus, centered one owned exact window and followed that-one to the same ID. After it closed, center-it failed in 74 milliseconds and left a new window with the same app/title unchanged. Both owned children were reaped, their closed undo records pruned with exact guards and original focus restored. No user window moved; acoustic conversation and broader application semantics remain separate.

October 3 release CI repair:

- GitHub's source gate stopped on a synthetic notice-redaction key marker. The fixture now assembles that marker at runtime; the scanner stays unchanged and the indexed release scan has zero findings. All seventeen notice tests passed locally; runtime code is unchanged.
- The Linux jobs built and passed all five native test groups, then failed an outdated pet check that omitted the required Core policy connection. The portable fixture now feeds the production SessionLockMonitor through the production authenticated IpcServer to the actual Qt pet. GNOME, Cinnamon, MATE, Xfce and freedesktop/KWin service cases passed offscreen on this host with private session/runtime and joined child/server/monitor cleanup. These are native transport fixtures, not physical sessions on five desktops.
- The workflow runs that check with the installed private runtime after dependency verification. Its container host is pinned to Ubuntu 24.04. Tumbleweed bootstrap now runs the standard full snapshot upgrade before installing dependencies, because the prior job mixed incompatible base/repository pcre2 versions. This is confined to disposable CI containers; remote acceptance is pending this push. No workstation configuration or installed runtime code changed.

October 3 portable cancellation fixture:

- All five Linux container jobs passed for 095b6c1: Ubuntu, Debian, Fedora, Arch and openSUSE. Their native builds, five test groups, installed-runtime imports and portable pet checks completed. These remain container/transport checks, not physical desktop acceptance.
- The source job reached all 1,502 tests and found one cancellation fixture that mocked recognition output without declaring its mocked recognizer available. It now declares that availability explicitly, so the test does not require a host speech model. All seventeen cancellation checks passed in both source trees. Production recognition availability checks stay unchanged; the next remote source run will verify the correction.

October 3 relative window placement:

- Added a typed same-monitor/current-workspace window pair operation and deterministic put/place/move-beside/next-to plans. Follow-up references are bound before resolving the requested window. KWin validates both exact identities, geometry, state, workspace, monitor and minimum sizes before either mutation, then arranges reference left/requested right in the panel-aware area. Fresh readbacks verify both identities, restored state and geometry; independent plan conditions check both final rectangles. No automatic replay is allowed. The normal bounded history saves both separately; two undo requests restore the pair. Corrections retain the pair operation rather than a preceding unrelated layout.
- Nine focused checks passed in both trees; all 1,511 Python tests passed in 98.820 seconds. All five native groups passed in 14.42 seconds, including actual Qt JS-engine before-either-move guards and odd-width/no-gap checks. Staged native imports, catalogue contracts, graceful installed restart and protected settings/focus checks passed.
- Installed Core arranged two disposable Qt/Wayland windows in 543 milliseconds using an exact that-one reference, verified both independent goal rectangles and left focus unchanged. Two guarded undo requests restored both. Dry-run changed nothing; different-monitor and closed-anchor requests refused without moving either target. Both children were reaped, owned history cleaned, original focus restored and existing user-window geometry/state unchanged. This accepts the owned native sequence, not room speech, other compositors or the whole application matrix.
- GitHub source checks and all five Linux container jobs passed for the preceding cancellation-fixture correction, 1875ada. Remote acceptance of this new window-pair batch is pending its push.

October 3 local system observation contracts:

- Eight local observations now declare offline support, read-only behavior and strict returned-field schemas. Real host outputs passed for clock, CPU, memory, temperature, disk, battery, identity and mounts. An absent battery remains absent, and unavailable temperature readings remain unknown. Mounts validate their actual total/free fields without inventing a used-byte measurement. Tool execution validates output before returning it; this does not establish a separate action postcondition.
- The existing twenty-three hardware checks and eight battery checks passed in both trees; all 1,511 Python tests passed in 95.757 seconds. No UI source changed, so the preceding five native groups remain applicable. Native staged imports and all eight installed Core observations passed after a backed-up graceful restart. Carlos/desktop settings, focus, wake pause and gaming policy stayed unchanged. The installed catalogue has 257 tools, with 224 still missing one or more contract declarations; this pass does not complete the whole catalogue.
- GitHub source checks and all five Linux container jobs passed for the preceding window-pair batch, daca9ea: [source checks](https://github.com/PhaxsScripting/C.A.R.L.O.S/actions/runs/37110954784) and [Linux jobs](https://github.com/PhaxsScripting/C.A.R.L.O.S/actions/runs/37110954787). These are automated source/container checks, separate from physical desktop or acoustic acceptance.

October 3 pending recognition request recovery:

- The native recognition fixture now observes the actual aiohttp upload to its owned loopback listener while that worker is paused, verifies the pending request and recorded listener identity, then kills and reaps only that worker. The real CLI fallback recognized the fixed generated CPU question. The failed listener record and audio file were removed; a subsequent request started a new verified persistent listener and recognized the same question. The HTTP client still performs the actual request; its trace only observes uploaded chunks. This proves recovery of an uploaded pending request, not that the suspended server started or completed inference.
- Both source trees passed with all owned workers and temporary files removed. Runs were deliberately restricted to one CPU at low priority: pending-crash fallback took 4,644 milliseconds in the public tree and 4,622 milliseconds in development. These are controlled fixture timings, not normal voice latency. Eight existing listener-lifecycle checks passed in each tree. Production recognition code and the installed runtime were unchanged; the preceding 1,511-test suite and five native groups remain applicable. No microphone, playback or desktop command was used.

October 3 presence freshness and lock boundaries:

- Lock observations now clear the previous explicit address. Queued wake, command, transcription, listening and interruption events cannot promote a known locked session to engaged. Unlocking alone stays unknown; a fresh explicit interaction can engage again. Hand confidence/age and idle evidence reject boolean, nonfinite, negative and out-of-range values; malformed metadata clears the presence claim instead of crashing the observer. Explicit interaction/privacy changes clear old camera evidence. Person identity stays unverified.
- Thirteen focused checks passed per tree, including five new regression cases; all 1,516 Python tests passed in 94.373 seconds. Actual private Core event persistence and IPC snapshots passed in both trees with synthetic lock/hand inputs, no camera/desktop action, owned cleanup and temporary-file removal. This is software boundary evidence, not a physical lock/presence or real-hand acceptance test. The presence module was backed up and installed after native import checks; Core health, IPC state, focus, settings, wake pause and gaming policy were preserved. No UI source changed; preceding native groups remain applicable.
- Source checks and all five Linux jobs passed for a9aeedf (system observations) and fe94875 (pending recognition recovery). The new presence batch still awaits its own remote run.

October 3 device and backend observation boundaries:

- Added strict returned-field contracts for process, interface, audio-device, composite device and OpenRC inventories. Composite device queries distinguish successful empty lists from missing/failed/timed-out/truncated backends. Other successful readings remain available; unavailable ones are null with a bounded reason. Partial inventory execution stays unverified with no replay, and all-failed inventory execution fails. No devices or services are changed. Interface state/counters are not Internet, DNS or VPN reachability evidence.
- Four failure/empty/truncation checks passed in both trees, alongside seven development declaration/permission checks. All 1,520 Python tests passed in 94.944 seconds. All thirteen local observation outputs conformed on the host; USB, Bluetooth and audio inventories were available in that sample. Actual private Core IPC exercised synthetic partial and all-failed backends in both trees, preserving null/unknown fields and the execution evidence flags. Synthetic faults were confined to temporary instances.
- Three backed-up source modules passed staged native imports and thirteen installed Core observations after a graceful restart. Health, settings, focus, wake pause and gaming policy were preserved. The installed catalogue still has 219 incomplete contracts among 257 tools. No UI source changed; preceding native groups remain applicable. Source checks and all five Linux jobs passed for the preceding presence batch, 8a715b0.

October 3 queued synthesis request recovery:

- Added a native Piper fixture that first completes generated PCM synthesis, pauses only its verified live child, observes 170 new request bytes in that child's actual kernel input pipe and confirms the synthesis request remains pending. Killing/reaping that owned worker triggers the real one-shot fallback, which returned nonempty PCM in the original format. The next request creates a different persistent worker and completes synthesis. Cleanup checks known persistent children and confirms no new direct child, including fallback, remains. No audio is played; a queued request does not prove the paused worker began inference.
- Both source trees passed. These runs shared one CPU at low priority; fallback took 3,762 and 3,751 milliseconds, controlled fixture timings rather than normal voice latency. Twenty-three existing speech checks passed per tree. Production synthesis and installed runtime were unchanged; the preceding 1,520-test suite and native UI groups remain applicable. Microphone use, audible quality and acoustic recovery remain unverified.

October 3 native listening privacy boundary:

- Added a real private Core/IPC fixture backed by isolated PipeWire/Pulse servers and their null-sink monitor. The configured native STT, preview-STT, VAD, wake and recorder children were alive before DO NOT LISTEN. The actual privacy transition reaped them, cleared capture/wake buffers, reported PRIVATE in IPC diagnostics and refused manual capture with the expected privacy error. Returning to Normal started different owned wake/recorder children. Private configuration retained every setting except the requested mode; host audio/configuration were not changed.
- Final runs passed in both trees: the privacy transition took 55.292 and 57.499 milliseconds. Cleanup verified all recorded workers, the local synthesis worker and audio servers exited, and no new direct child remained after Core cleanup. This proves actual worker/IPC behavior on private virtual audio; it is not physical microphone, room-acoustic or hardware-mute acceptance. The first fixture attempt had no private monitor source; the final helper explicitly enables its own monitor while keeping the original speech fixture default unchanged. Both original native speech-stop/recovery checks still passed with complete cleanup.
- Twenty-five settings and eighteen privacy checks passed in the public tree; twenty-four settings and eighteen privacy checks passed in development. Those include the router's mocked-cloud rejection case, separate from a whole-session network audit. Production Core/voice and installed runtime were unchanged; the preceding 1,520-test suite and native UI groups remain applicable. Source checks and all five Linux jobs passed for the preceding device inventory and synthesis-recovery batches, 8584906/eaf72f1.

October 3 personal observation contracts:

- Fourteen local observation tools now declare strict output fields, offline support and read-only behavior: lists/reads for notes, tasks, bookmarks and snippets; arithmetic, units, world clock, clipboard counts, recent files and unavailable selection guidance. List item schemas reject content fields and wrong item kinds; explicit read schemas allow content. Create/append/archive/complete/copy flags and permissions remain unchanged. These are observation contracts, not a new authorization or action verifier.
- Thirty-two existing personal/productivity checks passed in each tree; all 1,520 Python tests passed in 96.544 seconds. Actual temporary SQLite stores exercised all four item kinds, their list/read contracts and rejection of an inserted full-content field. Local arithmetic, conversion, timezone and owned-file readings conformed in both trees. Clipboard count validation used synthetic private data and did not read or change the host clipboard. All fourteen contracts conformed; temporary stores/files were removed.
- The two modules passed staged native imports and installed catalogue checks after a backed-up graceful restart. Installed arithmetic, Fahrenheit conversion and Tokyo timezone values passed independent expected-value checks. Mutation flags, health, focus, Carlos/desktop settings, wake pause and gaming policy stayed unchanged. Installed saved-item/clipboard operations were not exercised against personal content; those execution checks belong to the temporary stores. The catalogue still has 205 incomplete contracts among 257 tools. No UI source changed; preceding native groups remain applicable.

October 3 execution-history result contracts:

- Added exactly-one schema variants so a valid missing-task error keeps its existing shape while successful history pages validate their task/step fields. Mixed, absent, ambiguous and malformed variants fail validation; parent constraints and nested finite/type checks still apply. History schemas enforce historical/fresh-observation flags and reject reusable saved approvals. Dynamic redacted argument/detail/receipt dictionaries remain objects; they are not declared fresh executable plans.
- Seven variant checks and twenty-six existing journal checks passed per tree; all 1,527 Python tests passed in 97.693 seconds. Actual temporary SQLite receipts exercised empty/recent/project-scoped/paged/running/missing/foreign cases and the fixed step-list boundary after a later append. Invalid historical flags and mixed success/error outputs were rejected. Actual private Core IPC also preserved missing-task failure and accepted a running synthetic receipt as observation-only in both trees. No desktop actions or saved approval reuse occurred.
- Three backed-up modules passed staged native imports and a graceful installed restart. The installed missing-task response kept its exact error and failed/unverified execution status. Health, focus, Carlos/desktop settings, wake pause and gaming policy were preserved. The installed catalogue still has 203 incomplete contracts among 257 tools. No UI source changed; preceding native groups remain applicable. Source checks and all five Linux jobs passed for the preceding personal-observation batch, e698cc9, and listening-privacy batch, 0180aa8.

October 3 committed personal-item readback:

- Fixed personal saves and updates that previously returned verified success directly after SQL commit. They now reread the exact item ID and kind through a separate store connection and compare every field. A missing or changed row raises a bounded readback error without exposing item contents, replaying the write, recreating a deleted row or undoing a later edit. Fifteen mutation contracts declare strict kind-specific output, committed readback scope, local availability and no automatic undo; existing permissions and clipboard/browser actions are unchanged. Execution evidence names the database readback scope.
- Seven new regressions passed in both trees, with 41 focused checks per tree and all 1,534 Python tests in 95.556 seconds. Tests used actual temporary SQLite databases, a real second connection editing/deleting after commit, an ID/title collision, all fifteen registered mutations, RAM-only private/guest stores and invalid success claims. Timing hooks schedule the competing writes; they do not substitute the database or readback. Actual private Core IPC in both trees accepted successful committed reads, returned the expected IPC error on a later edit without replay and preserved that edit. Private-session writes stayed off disk; guest requests were denied. The IPC error is an expected refusal, not a structured successful tool response.
- Four backed-up installed modules passed staged native imports and the seven regressions. A private installed Core IPC fixture repeated the database checks without accessing host personal data. The running Core retained health, focus, desktop settings, wake pause and gaming policy after restart. The catalogue has 188 incomplete declarations among 257 tools. No UI source changed; preceding native UI groups remain applicable. Source checks and all five Linux jobs passed for the preceding history batch, 72bfda3. These checks do not establish room-acoustic, mobile-device or long-run acceptance.

October 3 reminder privacy and stop boundary:

- Reproduced two old-label leaks against the saved pre-change reminder loop: queued reminder speech survived a Guest/Normal round trip, and an actual SQLite worker could return an old label after a privacy transition and publish it. The loop now owns its queue by privacy mode and stop generation, clears it on transitions, skips personal reminder claims in Guest and refuses worker results from another scope. Ordinary one-shot delivery remains unchanged; explicit stop discards waiting speech.
- Six regressions passed in both trees using real temporary Core instances, Unix IPC privacy requests, SQLite reminder claims and event subscriptions. A timing hook delayed an actual database worker return across the transition. Speech used an explicitly silent mocked sink and declared availability; no microphone, model synthesis or host playback was exercised. Normal once-only delivery, Guest refusal, Guest/Normal and Private/Normal queue changes, late worker suppression and stop behavior passed. The pre-change loop failed the two intended regressions. Thirty-four focused checks passed per tree; all 1,540 Python tests passed in 107.954 seconds with testing confined to one low-priority CPU.
- Both native source runs passed six checks; the installed module passed staged native checks and six further private installed Core/IPC checks. A backed-up graceful restart preserved Core health, focus, desktop settings, wake pause and gaming policy. The catalogue remains 257 tools with 188 incomplete declarations. No UI source changed; previous native UI checks remain applicable. Source checks and Linux builds passed for the personal readback commit ed364c7. Real room audio, mobile-device and long-run acceptance remain separate.

October 3 queued reminder occurrence readback:

- Reproduced stale speech after a real IPC snooze and after cancellation of a recurring timer against the previous loop. Before speech dispatch, the loop now rereads the exact claimed reminder row, including the expected fired state or computed next recurrence deadline. Changed/missing rows discard the old occurrence without writing, recreating or replaying it. New claims for the same reminder replace the older queue entry. Privacy mode and stop generation are checked again after the database await, so a transition during that read cannot dispatch an old label.
- Five additional regressions cover snooze, recurring cancellation, row deletion, unchanged recurring delivery and a real SQLite delivery check delayed across actual IPC privacy transitions. All eleven reminder regressions passed in both native trees; the previous loop failed both intended snooze/cancel cases. Thirty-nine focused checks passed per tree and all 1,545 Python tests passed in 117.046 seconds on one low-priority CPU. Instances and databases were temporary; speech used a silent mocked sink. This proves queue/IPC/database behavior, not audible room delivery or atomic cancellation of speech already in progress.
- Two installed source modules were backed up, passed staged native imports/regressions and eleven further checks against installed source in private Core instances. Core health, focus, protected desktop/configuration settings, wake pause and gaming policy were preserved. No UI source changed; preceding native UI groups remain applicable. The catalogue remains 257 tools with 188 incomplete declarations. GitHub Source checks and Linux builds passed for a9f4a83, the preceding reminder privacy commit.

October 3 bounded file observations:

- File search no longer follows file/directory symlinks or reports outside-target metadata. Iterative traversal bounds entries, visited/pending directories and elapsed checks. Directory listing retains at most the requested rows plus one instead of materializing the complete directory. Listing reports link-inode metadata. Exact result limits remain complete; extra matches, refused directories, scan errors and exhausted budgets are explicit partial results, retained but unverified. Filesystem calls themselves are not interruptible by this elapsed budget; concurrent replacement is not excluded by an atomic sandbox.
- Added strict read-only/offline output contracts for find, info, list, read and SHA-256 hash. Existing permissions and mutation behavior are preserved. Ten new regressions plus four file-action checks passed in each tree, including actual permission-denied traversal under the ordinary user. The saved previous functions reproduced the outside-link metadata and exact-limit truncation defects. Both native trees and installed source passed actual private Core/Unix IPC checks using owned temporary files; partial search stayed failed/unverified with changed_state false.
- The initial full suite had two timing failures and one native capture error while an unrelated editor enumeration process saturated every CPU. After cancelling only that owned scan, the same eleven checks passed in 2.917 seconds without test/runtime edits; all 1,555 Python tests passed in 116.621 seconds. Earlier failure evidence was retained. No UI source changed; previous native UI checks remain applicable.
- Three installed files were backed up and passed staged native imports/regressions and private installed-source IPC. The host Core was already stopped and remains stopped; Carlos/Plasma configuration hashes were unchanged. The installed-source catalogue has 257 tools and 183 incomplete declarations. No host personal files, microphone, playback or desktop input were used. Source checks and Linux builds passed for the preceding evidence-docs commit, 928ed8c. Device, acoustic and long-run acceptance remain separate.

October 3 file-transfer readbacks:

- Move no longer verifies success from destination presence/source absence alone. Copy and move compare complete preflight and destination inventories of entry type, byte size and SHA-256; empty/hidden directories are included. Traversal errors raise during preflight instead of silently omitting unreadable subdirectories. Link/special-file, nested destination, entry-limit and allowed-root move refusals precede dispatch. Destination readback failure returns unverified with no replay, cleanup or replacement of a later edit. Inventories do not make dispatch an atomic filesystem transaction or provide generic undo.
- Seven new checks cover real moves with same-size corruption or a removed child, actual ordinary-user chmod refusal before destination creation, links/FIFO/budgets, root/nested refusal, copy-link replacement and typed outputs. The saved previous move reproduced both false-success cases. Seventy focused checks passed per tree; all 1,562 Python tests passed in 115.144 seconds. Both native source trees and installed source passed real private Core/Unix IPC with four owned file/directory transfers, a corrupted move refusal and independent content readings. The later contents stayed intact and dispatch occurred once.
- Added strict offline/action/non-reversible contracts for both transfers and filesystem_content_readback receipts. Existing sensitive permissions are preserved. Four installed files passed backed-up staged native imports/regressions and private IPC. Host Core was already stopped and remains stopped; protected configuration hashes were unchanged. Catalogue: 257 tools, 181 incomplete declarations. No host personal content, microphone, playback or desktop input. No UI source changed; prior native UI groups remain applicable. GitHub Source checks and Linux builds passed for the preceding file-inventory commit, efa6609. Cross-filesystem/device/room and long-run acceptance remain separate.

October 3 conversation delivery controls:

- Added humor, sarcasm, name-use frequency and numeric speech-rate controls to native Settings and the CLI. Local full/compact prompts include delivery preferences while retaining action truthfulness and refusing invented names. Speaking rate updates the next Piper request; unrelated personality changes preserve the actual existing voice rate. Validation rejects out-of-range/nonfinite/boolean rates and invalid mixed updates before persistence. Direct personality updates now refuse private/guest/transition states; the actual Guest IPC denial remains in place. The saved previous method reproduced a persistent Private Session write through this settings path.
- Six new regressions and 39 focused checks passed in each tree; all 1,568 Python tests passed in 121.827 seconds. Native build and all five Qt groups passed in 16.21 seconds, including typed control requests, offline disabling, actual-rate display and panel/text fit. Both source trees and installed source passed private Core IPC, an actual numeric CLI request and real silent Piper PCM at two rates through one worker. Actual Private Session/Guest transitions refused updates without changing their temporary config; each owned worker was reaped. This verifies plumbing and synthesis timing controls, not perceived voice quality or model style consistency.
- Six source/script files and the UI executable were backed up and installed with prior modes retained. Staged native imports/six regressions, dynamic dependencies and the installed-source delivery fixture passed. Host configuration hashes were unchanged; Core and control center were already stopped and were not opened. Catalogue remains 257 tools/181 incomplete declarations. No microphone, audio playback or host desktop input. Source checks and Linux builds passed for the preceding transfer commit, 0e04265. Proactive speech thresholds, room/device and long-run acceptance remain unfinished.

October 4 personality refusal display:

- The Qt client previously cleared known personality settings and displayed Updated after a Guest denial returned as a normal response. It now requires explicit updated/applies-immediately flags and a nonempty object, refuses error/denied/failed responses even with contradictory success flags, preserves the last settings and shows the refusal. A genuine later success still updates normally.
- Actual private QLocalSocket transport reproduced the failure with the previous client before the fix. The regression covers six denied/incomplete/contradictory/error results plus accepted updates. Native rebuild and all five Qt groups passed in 13.36 seconds; backend is unchanged, so the preceding 1,568 Python tests and silent installed Piper/IPC/CLI checks remain applicable.
- The installed UI binary was backed up and replaced while Core/control center were already stopped. Original executable mode and protected configuration hashes were preserved; no applications, microphone or playback were opened. Source checks and all Linux builds passed for the preceding personality controls commit, 86eb9f6.

October 4 proactive warning speech:

- Added opt-in Alert Voice off/high/emergency controls to native Settings and the CLI, with off as the default. Local thermal and fresh security/storage observations produce fixed warning text, never private event bodies or filenames. HIGH waits for idle; EMERGENCY can stop Carlos's owned speech once before rechecking dispatch. Neither opens a microphone/follow-up window nor interrupts a tool. Explicit Codex waiting prompts are not yet connected.
- Queue size is bounded by three known warning kinds; same-kind events coalesce without downgrading an emergency. Events expire after ten seconds, with a two-minute cooldown per kind/priority after an attempt. Fresh unlocked-session, privacy, quiet, gaming, speech-output and TTS gates apply. Stop, lock, privacy, scene and resume changes invalidate old work. Policy changes during an alert cancel and reap only its owned speech task. The disabled worker waits without an idle polling loop.
- Twelve focused regressions passed in both trees. Tests use private Core Unix IPC, actual event subscriptions and temporary SQLite data with a silent mocked speech sink. Malformed sources/values, stale/future observations, invalid choices, priority/coalescing/cooldowns, interactive-state waiting, emergency preemption, cancellation and backend failures were covered. An injected alert-callback failure still recorded the event in SQLite. The first full run exposed callback/persistence coupling in the notification fixture; the callback is now isolated, the nine notification regressions passed, and all 1,580 Python tests passed in 113.132 seconds with final stop handling.
- Native build and all five Qt groups passed in 14.40 seconds. The new setting sends typed values and disables offline; personality controls remain inside the scrollable panel. The initial UI test needed to reveal each control before clicking after the extra row; the passing test clicks actual visible controls.
- Both source trees and installed source passed private IPC, actual CLI rate/threshold updates and real silent Piper PCM through one persistent worker. A synthetic emergency passed through actual event persistence before warning synthesis; quiet delivery was refused, private/guest changes refused persistence and the owned worker was reaped. This checks local routing/synthesis, not physical hearing, real sensor danger, subjective voice style or a daily-session soak.
- Six source/script files and the UI executable were backed up and installed with prior modes retained. Staged native imports/twelve regressions and dynamic dependencies passed. Host Core/control center were already stopped and stayed stopped; protected settings hashes were unchanged. No microphone, audio playback or host desktop input. Catalogue remains 257 tools/181 incomplete declarations. Source checks and all Linux builds passed for the preceding refusal-display commit, 32be00d.

October 4 archive contracts and constant validation:

- The saved previous shared validator accepted contradictory const values and treated boolean true as numeric 1. It now enforces constants and enum values with JSON type semantics, including nested objects/arrays and numeric equivalence. This fixes enforcement for existing contract fields, not just archives. A saved prior incomplete archive result containing only verified:true also reproduced a generic verified receipt.
- Inspection and extraction now declare offline support, strict output schemas and no automatic undo. Inspection verifies metadata only, keeps payload_verified false and distinguishes a 50-entry preview from inventory limits. Extraction receipts require complete typed evidence and identify staged SHA-256 readback/no-overwrite publication; later edits and parent-directory crash durability are outside that claim. Preview totals/counts and publication counts must agree. Missing/contradictory outputs fail without retry.
- Eight new regressions and 23 existing archive/file checks passed in each tree. Owned native ZIP operations checked actual extracted content, bad CRC refusal/staging cleanup, existing-destination preservation, wrong constant fields, numeric/boolean distinctions, preview boundaries and incomplete receipts. All 1,588 Python tests passed in 115.109 seconds. No UI changed; the prior five native Qt groups remain applicable. Source checks and all Linux builds passed for the preceding proactive-warning commit, 78dec55.
- Both source trees and installed source passed actual private Core Unix IPC with owned ZIP files. Metadata-only inspection, content-matched publication, existing-destination refusal, contradictory payload verification refusal and corrupted-payload refusal were observed at that transport boundary. Refusals returned error messages, not synthetic completed receipts. Four installed files were backed up, staged native regressions passed and protected configuration hashes were unchanged; the host Core was already stopped and remains stopped. No host personal content, microphone, playback or desktop input. Catalogue now has 257 tools/179 incomplete declarations. Daily-session, physical/device and the rest of the full specification remain incomplete.
