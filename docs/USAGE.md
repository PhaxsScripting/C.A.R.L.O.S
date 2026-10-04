# Using Carlos

Start the core and UI with the [setup guide](SETUP.md). You can type in the control
center before setting up a microphone or voice model.

## Check things rq

From the repo root, with your venv active and the core running:

```sh
PYTHONPATH=carlos/core python3 -m ev.cli health
PYTHONPATH=carlos/core python3 -m ev.cli ask "what is my CPU usage?"
PYTHONPATH=carlos/core python3 -m ev.cli tools
PYTHONPATH=carlos/core python3 -m ev.cli events --limit 20
```

After a user install, `evctl` runs the same commands without the Python prefix.
`health` shows what's available. An available model still needs a real request
to check how well it runs on your hardware.

## Voice controls

These also work with `python3 -m ev.cli` from source:

| Command | What it does |
| --- | --- |
| `test-microphone` | Starts a microphone check |
| `test-transcription` | Starts a speech recognition check |
| `test-wake --seconds 30` | Opens a timed wake word test |
| `listen` / `stop-listening` | Starts or stops a manual capture |
| `resume-wake` / `pause-wake` | Enables or pauses wake listening |
| `stop-speaking` | Interrupts the current spoken reply |
| `privacy-on` / `privacy-off` | Turns voice privacy mode on or off |
| `latency` | Shows recent request timings |

If you get text back but no speech, check `health` for TTS availability and make
sure the configured Piper runtime and voice exist. Check the system output and
volume too. For missed speech, test the microphone first, then transcription,
then wake detection. That makes it easier to find which part is messing up.

## Tasks and permissions

Carlos can open apps, inspect the desktop, work with files, and run supported
tasks through registered tools. Some actions need approval. Read the target and
requested change before approving anything.

`open Firefox on my laptop monitor` resolves the display before launching.
Carlos reuses one matching native app window, or launches once and waits for
one new window. Multiple windows need an exact choice. It then moves and focuses
that window and checks the enabled output and focus again before completion.
A failed or uncertain launch is never replayed automatically. This path needs
native compositor inventory and the installed desktop entry; it does not
infer application readiness from a window title.

`put Firefox beside Konsole` arranges two existing normal windows on the same
monitor and current workspace, with Konsole on the left and Firefox on the right.
`put Firefox beside that one` uses the exact recent window reference. Both IDs
are resolved before any move, then KWin checks their identities and current
state together. Missing, changed, tiled or cross-monitor targets refuse. Window
minimum sizes and panel space are respected; both positions are read back.
`undo that` restores one window at a time, so reversing the pair takes two undo
requests. An uncertain or partially completed pair is never replayed automatically.

Use `plans` for plans, `agent-tasks` for task history, and `security` for the
current permission settings. `stop` shuts down the core. `--help` lists the rest
of the CLI commands.

Missing permission, privacy restrictions, ambiguous targets and expired visual
previews are setup or targeting problems. Carlos reports the blocking evidence
and stops without replaying them or proposing a coding repair. Native desktop
authorization still uses the operating system's dialog. A temporary disconnect
may get one retry for an explicitly audited idempotent tool. Unexpected defects
and missing tools can still be reviewed for an explicit engineering task.

## Keep your stuff local

Don't put keys, recordings, screenshots, memory databases or private logs in this
repo. The default provider is offline. Cloud providers need your own account and
configuration; read the relevant provider settings before enabling one.
Local speech and chat still need separately installed models.

Activity has two views: This session for live events and Saved history for
recorded activity from earlier sessions. Settings → Privacy → Save local
activity history enables recording; it starts off. Once enabled, it keeps the
latest 2,000 metadata events, including core/resume, actions, Codex job states,
power requests and component recovery. It skips private events, transcripts,
screenshots, audio, form fields and tool output. Turning recording off keeps
existing history. Clear history asks for approval and leaves explicit memories,
conversations and permission receipts alone. Leave Private Session before
clearing saved history. CLI access: `evctl tool memory.timeline --arguments
'{"limit":50}'`. This is workstation activity; mobile login history is separate.

Memory → Project notes stores approved notes separately for an exact existing
directory. Enter its full path, then Refresh. Saving and forgetting use the
ordinary permission prompt; switching paths clears the previous results. Private
sessions keep changes in RAM. General notes remain separate. Loading a project
on this page does not select the assistant's preferred project. Set that through
`personalization.set_preference` with key `project` when you want its approved notes included
as historical model context. These notes do not grant permission or prove live
project state. The `memory.project.search`, `.remember` and `.forget` tools work
offline. Search can filter content and limits responses; it does not scan files.

Use this project for conversation explicitly selects its approved notes and chat
history. Use general conversation removes that selection without deleting notes
or past turns. The equivalent tool is `memory.project.select` with an existing
directory, or `{"project":""}` for general conversation. The conversation page
loads only the selected context. Old unassigned turns stay in general history.
Changing projects clears historical window references. A reply or approval
started earlier stays in its original context; selection does not cancel it or
change approved arguments. Global explicit notes are still shared preferences.
Task receipts and resumption use the selected project too. `agent-tasks`,
`agent.history` and `agent.task_status` show only that scope; old unassigned
tasks stay in General. Select the original project before inspecting or revising
one of its tasks. “Continue” considers the latest attempt in that project and
observes current state before doing more work. A later successful request in the
same project prevents resurrection of an older failure. Changing the selection
does not cancel active work or reuse approval. Optional activity history remains
workstation metadata.

Ask “What was I working on last night?”, “What was I doing yesterday?” or “What
happened with HoloHand?” to read saved receipts locally without a provider call.
`memory.recall` supports last_night, yesterday, today and past_week, an optional
text query and limit. It uses the selected project unless you explicitly supply
an allowed directory or an empty path for General. Last night means the prior
evening at 18:00 through 06:00, capped at now, in the machine's local timezone.
Replies show a short preview; the tool returns bounded entries and has_more.
Matching explicit notes are separately labelled undated context. Empty history
cannot establish what happened outside saved conversations, and old text is
never proof of current files, windows or completed actions.

`evctl tool holohand.measure --arguments '{"seconds":5}'` samples metadata from
an existing hand controller. It never starts the app/camera, enables input or
saves frames. Use `carlos/scripts/measure-holohand.py --seconds 10` for a standalone
report. Capture/inference FPS need counters and verified process continuity;
older instances, resets and missing identity return unavailable rates. Timing
percentiles are sampled readings that may repeat or miss model results. Physical
gesture accuracy and desktop-action latency are separate, unmeasured fields.

## Monitor names

On KDE, `desktop.world` lists the currently enabled connector names. Say
"Name monitor HDMI-A-1 big monitor", then "Put Firefox on the big monitor".
"Show my monitor names" checks saved names against the current screens;
"Forget monitor name big monitor" removes that nickname.

With a usable unique manufacturer/model/serial identity, the nickname follows
the panel when its connector changes. Without one, Carlos explicitly binds the
name to the connector, which can also refer to a replacement panel on that port.
A disconnected or ambiguous binding cannot silently select another screen.
Current/other remain contextual names. Private sessions keep nickname changes
in RAM; Guest cannot read or change them. Naming does not change display layout.

## Hardware readings

Say `diagnose my network`, `show network diagnostics`, or `why can't I look
anything up?` for a local configuration report. It reads both route families,
interfaces, resolver generator evidence, Tailscale daemon state and the mobile
backend's loopback health. It changes no settings and makes no DNS lookup or
Internet connection. `dry run, diagnose my network` previews without observing.
Linux route inventory needs `ip` from iproute2; missing commands yield an unknown
or partial reading. A resolver marker is evidence of its generator, not proof of
current ownership. Split DNS, browser DNS-over-HTTPS, policy route selection,
gateway reachability and public mobile access remain separate checks. A link
being up or Tailscale reporting Running does not establish Internet access.

Ask "What is my fan speed?", "Check CPU clocks", "Show GPU usage", or
"Is my CPU throttling?". These use local read-only sensors without a model
request. `system.get_hardware_metrics` also returns them through the tool API.

CPU clocks include their sysfs source: a scaling reading can reflect a
requested frequency. Thermal counters are lifetime event counts and per-sample
deltas; the first reading, a reset, or a missing baseline leaves the delta
unknown. Package counters repeat per CPU and are not added together. They do
not establish whether throttling is happening at this instant. Faulted or
disabled fan sensors have no trusted RPM. Zero RPM is a valid reading.

GPU load is available when the driver provides `gpu_busy_percent`; otherwise
it is unavailable. Carlos does not install drivers, request elevated access,
or change CPU, fan, GPU or charging settings to obtain these readings.

The status screen lists VAD, STT and TTS separately. A persistent worker needs
a recent health observation and a live process to show READY. STOPPED overrides
an older READY result immediately. ON_DEMAND means the adapter is configured
and available but no persistent worker is promised. DISABLED applies to an
unused neural detector. Voice becomes degraded when a required worker lacks
current evidence. These labels do not replace microphone or playback tests.

CPU temperature readings exclude generic board, GPU and disk sensors. Intel
package readings and AMD Tdie readings take preference; the hottest matching
package is used on a multi-socket system. If AMD only exposes Tctl, its control
value stays labelled as such. An unidentified CPU sensor is unavailable.
The [kernel's k10temp documentation](https://docs.kernel.org/hwmon/k10temp.html)
explains the distinction between AMD die and control readings.

Desktop notices use a bounded priority queue. Emergency/high notices are sent
before lower-priority queued notices; equal priorities keep their order. On
overflow, less important notices are discarded first. The support bundle
reports queue capacity, queued count and discarded counts by priority, without
message contents. A first warning has no repeat cooldown; escalating from a
high thermal warning to an emergency is a new priority and can be reported.
This ordering does not interrupt speech, executing tools or an in-flight
notification backend call. Existing quiet-mode and notification settings apply.

Desktop notice text uses the same recognizable credential filter as logs.
Filtering happens before the 500-character display limit, so truncation cannot
cut a private-key block before it is recognized. Markup and invalid XML control
characters are handled as text, without interpreting error output as a link
or image. Unlabelled secrets still cannot be reliably identified.

Security advisory checks report incomplete when the command fails, times out or
returns unrecognized/truncated output. Gentoo uses local GLSA data; it does not
sync repositories or install anything. Cached results carry a timestamp and
expire after fifteen minutes. A clear GLSA result covers that local advisory
scan, not every vulnerability. Permission audits inspect the path itself and
report missing, unreadable or symlinked required paths as incomplete. Their
confirmation list comes from the running tool catalog.

The Security page now shows the background monitor's read time and source
coverage. `evctl tool security.monitor` reads that same snapshot. Settings →
Security → Monitor local security evidence can disable it without changing
permissions, voice or desktop behavior. The fast probes run once a minute;
SMART reads run every fifteen minutes and local GLSA scans once an hour.

It watches network-bound listeners, readable failed-login records, visible
Tailscale peers, OpenRC crashed services or systemd failed units, firewall
evidence, startup fingerprints, SMART health and local advisories. Ordinary
listener/device/startup changes are observations, not intrusion claims. Initial
listener/device/login readings establish a baseline. Tailscale visibility does
not prove when a device joined an account. Restarted processes and device
online/offline changes do not create new identities.

Startup comparison survives core restarts using a private file containing only
path/content fingerprints. It covers XDG autostart, user units and drop-ins,
shell startup and Plasma environment scripts; symlink destinations are not
audited. Private Session, Guest and privacy transitions pause collection and
baseline writes. Probes finishing after a privacy change are discarded.

The monitor never requests admin access, changes firewall rules, restarts system
services, installs updates or runs disk tests. An active firewall service does
not confirm filtering. Inaccessible login records, sleeping/inaccessible disks
and failed advisory scans stay unknown. SMART commands use the device type and
low-power check; they skip supported sleeping ATA disks instead of starting a
test. Advisory coverage currently uses Gentoo's local GLSA data. Other Linux
advisory sources still need adapters. This is bounded best-effort monitoring,
not a replacement for a full security audit.

When desktop notifications are enabled, monitor changes use the same bounded
notification queue. Quiet scenes suppress informational changes, while important
verified failures retain their priority. Only monitor-origin observations use
this notification path; model statements do not become security evidence.

OCR runs on demand with one worker per core. Cancellation and timeout stop and
reap its owned child; output is capped at 1 MiB per stream. OpenCV uses one
processing thread and each ONNX session uses two intra-op threads and one
inter-op thread. These limits are per pool, not a total process thread limit.
Source images are bounded to 16 MiB and 16 million pixels. The source capture
keeps its ordinary expiry/deletion policy. Confidence is an OCR estimate, not
independent proof of visible text, a click target or a completed action.

Deterministic monitor-move/open-on-monitor plans carry the resolved output
metadata through execution. A usable unique serial is checked before dispatch,
inside KWin immediately before the move, and during the placement readback.
If identity changes or becomes ambiguous, resolve the target again. A screen
without a usable serial still has connector-only semantics.

## Core and worker resources

Ask "How much RAM is Carlos using?" or "Check Carlos resources" for an
on-demand process-tree reading. The CLI equivalent is:

```sh
evctl tool system.carlos_resources --arguments '{"seconds":3}'
```

The interval can be 1–15 seconds, with detailed reads capped at 64 processes.
RSS sums can repeat shared pages; PSS apportions those pages when available.
Incomplete reads leave totals unknown and return observed lower bounds. CPU
needs the same continuously observed process identities throughout the interval;
birth, exit, PID reuse or counter reset leaves it unknown. One hundred percent
means one busy logical CPU, and the observation includes its own small overhead.

This covers the Core OS process tree. Separate UI, pet, mobile services and
reparented runtimes are excluded. It is not a whole-machine idle benchmark.
The existing RAM/CPU cards are labelled Core RAM and Core CPU to reflect their
original main-process measurements; they do not add workers to those values.

## Stopping pending work

"Stop", "wait", "actually don't", "cancel that" and "stop everything" cancel
pending tool work, planner actions, engineering jobs, watches, permissions,
voice interaction and queued speech. Application-specific requests such as
"stop Spotify" still target that application.

Owned asynchronous tool workers join their cleanup. A direct tool client receives
a cancelled reply and can keep using the connection. Already-dispatched
synchronous or OS actions may finish: cancellation is not rollback. Their
changed-state evidence remains unknown unless execution actually finished and
returned its postcondition before cancellation. Permission persistence cannot
start an old operation after a stop.

Short stop phrases bypass the normal IPC action queue on the same connection.
A stop also removes accepted, undispatched action requests from other connected
clients, returning a cancelled reply for each request ID. Read-only status
requests retain their order, and ordinary application commands remain serialized.
Two cancellation slots are reserved separately from eight status slots.

Engineering-job cancellation also stops owned fixed-validation processes and
prevents remaining validators or review commits from starting. Captured
validation output is bounded and is never interpreted as agent progress.
The isolated worktree remains for inspection. A review commit already sent to
Git may have completed; inspect that worktree before assuming anything was undone.

The engineering tool's own timeout also cancels the underlying job and joins
cleanup. A shared cancellation token covers work queued before its execution
thread registers, so a cancelled request cannot start fresh work later.

## Tool contracts

Catalogue snapshots include contract_gaps for undeclared offline support,
reversibility or output schemas. Undeclared values are not a support claim;
request cancellation does not mean an already-dispatched effect can be undone.

The local clock, CPU, memory, temperature, disk, battery, system identity and
mount tools validate their returned fields before reporting a result. Missing
temperature and battery sensors remain unknown or absent. These observations
work offline and do not change settings; they do not verify a later action.
Process, interface, audio-device and OpenRC inventories also validate their
returned fields. Interface state does not establish Internet connectivity.
`system.devices` reports each USB/Bluetooth/audio inventory separately: a failed
or truncated inventory returns null with an unavailable reason, while a
successful empty inventory returns an empty list. Partial results keep the
readings that succeeded and remain unverified; all failed backends fail the tool.

File search, listing, metadata, text reading and SHA-256 hashing validate their
returned fields. Search skips symlinks and excludes hidden entries unless asked;
`.git`, `node_modules`, `build` and `target` directories remain excluded. Searches
stop after 20,000 inspected entries, 2,000 directories or a five-second elapsed
budget. Listings keep only the requested number of sorted rows plus one while
scanning, with the same entry/time budgets. The time budget is checked between
filesystem calls; it cannot interrupt a blocked filesystem call.

A `truncated` result keeps available rows and reports `stop_reason` and
`scan_errors`; Carlos does not verify it as a complete inventory. Reaching the
requested row limit exactly is complete if the scan finishes without another
match. Listed symlinks describe the link itself, not its target. These readings
are observations of a changing filesystem, not an atomic snapshot or sandbox.

Copy and move compare destination type, byte size and SHA-256 content against a
preflight inventory, including empty and hidden directories. Moves also require
source absence. Unreadable directories, links, special files and trees over
10,000 entries refuse preflight; an allowed-root directory cannot be moved.
A failed readback leaves available paths for inspection and does not replay,
delete a later edit or automatically restore the source. These tools do not
provide automatic undo. Filesystem changes during dispatch/readback are still
possible; this is content observation, not an atomic filesystem transaction.

Say `undo volume`, `undo mute`, or `undo the audio change` to reverse Carlos's
most recent output-volume or mute adjustment. The undo history stays in memory,
holds up to 32 changes, and expires after ten minutes. It restores exact channel
levels on the original output without changing the default route. A later manual
adjustment to that setting, a replaced device, or an audio-server restart prevents
restoration. Volume undo keeps the current mute state; mute undo keeps current
channel levels. A request naming volume or mute refuses if the latest change
affects the other setting. Generic `undo that` still uses the existing window-undo path.
Unsupported device metadata, remote audio servers, or an original level above
100% leave ordinary audio controls usable but report `undo_available: false`.
These checks use separate observations and writes; they cannot exclude an external
adjustment happening in the interval between them. Stop does not restore audio
automatically, and an already-dispatched audio command may still finish.
Destructive and privileged tools always require confirmation, including with
strict permissions disabled or a read-only declaration. High-risk modification
confirmation still follows the strict-permissions setting.

Registry declarations require object schemas, boolean action flags and a finite
positive timeout of at most one hour. Plugin batches pass the same checks before
any tool is registered. Catalogue dictionaries cannot alter execution schemas,
and nonfinite JSON results are rejected instead of reaching clients as success.

## Engineering status while work continues

Ask "What's Codex doing?", "Check engineering status" or "Is Codex still running?"
for a short local status reply. These exact questions can pass a busy connection
and do not interrupt its action, invoke a model or create an implicit task.
Only engineering status/result observations bypass that busy tool gate.

Job phases and monotonic elapsed time are read fresh independently of cached
CLI readiness. A saved RUNNING receipt is not a tracked live worker. Cancelled
workers finishing cleanup are reported as cleanup. Status results cannot mutate
the readiness cache. The observations obey the existing local/private/guest
engineering restrictions and discard results crossing a privacy transition.

The coding-tool outer deadline is one hour, including its bounded agent run and
fixed validation stages; it is an upper bound, not a target duration. Its own
timeout still cancels the job and joins cleanup.

Capability queries and self diagnostics remain available while a command is busy. Slow CLI and accessibility readiness probes run outside the main loop.

Record a local clip with `record my screen for ten seconds`. Confirm, then choose one screen or window in the native dialog. `show recording status` and `show my recordings` inspect support and completed clips. The global `stop` command cancels an unfinished clip. See [screen clips](RECORDING.md) for limits, privacy, deletion and Linux dependencies.


Window undo restores complete workspace assignments and independent maximize,
fullscreen and minimized states. A missing/replaced monitor or removed workspace
stops restoration before movement. Custom compositor tiles cannot currently be
restored exactly. An optional `window_id` on `desktop.window.undo_last` refuses
another window's newer history record. Monitor serials are guarded when available;
connector-only checks are reported when hardware identity is unavailable. Geometry
and state still need real-application readback: the native Qt test passed, while
GTK fullscreen acceptance remains unresolved on the development host.


Settings now has an **Undo last change** button. You can also say
`undo my last Carlos setting change`, or call `carlos.settings.undo_last`
with an optional exact `key` guard. This restores the latest managed field,
including whether the original field was absent, and its runtime setting.
Unrelated edits stay intact. External edits to the affected field, private/guest
sessions, privacy transitions, hard microphone mute and active voice interactions
can prevent restoration. No-op updates do not hide the previous change.

Settings undo keeps at most 32 records in memory for ten minutes. Restarting the
core or changing privacy mode clears them. It does not undo arbitrary files or
destructive actions. Voice setting readback does not prove that capture is available.
The interface disables managed settings while a change is pending and does not
replay changes after a disconnect.


Window discovery excludes KWin entries retained only for closing animations.
Exact actions against their old IDs fail before mutation. A new window with the
same title is a separate target and does not inherit the old identity or undo record.


Power profile inspection and selection use an existing PowerDevil backend first,
then a running standard Power Profiles Daemon. Both current UPower and older
net.hadess interfaces are supported. Carlos does not start a daemon or install a
power manager. `settings.power_profile.set` accepts an optional
`expected_current` guard. The daemon's unique owner and system-bus identity must
match the observation before dispatch; readback stays on that backend. Failed or
missing readback does not trigger another write. There is no automatic undo.


HoloHand has an optional **Three-finger horizontal swipe** control. Enable it in the companion only after calibration; extend index, middle and ring with the pinky curled and move horizontally. One recognized swipe sends four horizontal wheel steps. Release the pose before repeating. It is off by default and does not enable other system gestures. Save calibration to persist the choice. Carlos reports `swipe_enabled` when the companion supports it; older companions report null. Pause/resume checks process identity before reporting success.


While Minecraft Java runs, Carlos yields ambient listening and background prewarm. Use typed commands or the microphone button when needed. It resumes according to your existing wake and privacy settings after the game closes. Set `resources.yield_ambient_for_minecraft` to false to disable this behavior.

Local visual proposals are available through `vision.candidates` with an exact window ID and visible text. Results contain confidence, separate duplicate candidates and a private highlighted preview. `vision.candidate.review` checks one candidate's current native identity and retained image hashes. Proposals expire after sixty seconds and are historical inference; they do not click or establish current scene truth.

`vision.candidate.click` requests one left click on a specific candidate with 1-3 expected native result conditions in that same window. It always asks for confirmation with the private highlighted preview. Supported conditions cover window state/geometry/absence, exact semantic control state/text and native browser URL. Every condition must be observable and unmet before input. Carlos captures the window again, compares pixels, checks native process/window/output identity, and consumes the candidate and its siblings before attempting input. Delivery errors, timeouts and missing transitions never replay it. Changed images need a new inspection. The preview stays on the authenticated local client; event listings omit it. A verified result certifies only the declared native transition.

You can request visual targets without handling identifiers:

- `show visual targets for "Retry" in Firefox`
- `visually click "Fullscreen" in Firefox expecting window to be fullscreen`
- `visually click the second "Close" in Firefox expecting window to close`

Labels must be quoted. Named window resolution uses native metadata first. Matching labels require an ordinal when there is more than one, ordered from top to bottom, then left to right. Click commands require a declared window change: close, be minimized, be maximized, be fullscreen or exit fullscreen. Carlos creates the preview before requesting approval. Native input consent may also be needed; expiration or any target/image change stops the action. `preview visually click ...` remains a dry run with no capture or input. Regular semantic-control click commands still use accessibility.

Direct desktop commands and authenticated local calls for visual targets/clicks remain local even when a cloud conversation provider is selected. They do not invoke that provider or change its settings. Model-engine tool callbacks cannot inherit this local execution scope; cloud models remain blocked from private visual tool results. The server records the local origin on its pending confirmation internally, then rechecks it when resolving the single-use approval.

Local clients can send `speak: false` on `command.submit` and on its later
`confirmation.respond` to keep both request stages silent. Each request scopes
its own speech policy; a normal desktop confirmation retains the default reply.

Window undo checks its exact saved record. If fresh, complete native evidence
shows that window has closed, Carlos removes that record without moving an
older window. Request undo again for the preceding change. Incomplete or stale
evidence keeps the record; a newer action's history is preserved during undo.

With Carlos already running, `python3 carlos/scripts/check-voice-transport.py
--run` checks local synthesis, transcription and one CPU query silently. It
needs ffmpeg, uses a fixed generated phrase and submits no command if recognition
differs from that exact read-only question. It changes no microphone settings
and plays no audio. Its timings cover transport/computation, not room acoustics,
first speaker audio, human wake detection or cold model loading.

Speech diagnostics distinguish `speech_stop_latency_ms` from accepted wake
interruptions. Each has a scope and observation timestamp. Pending synthesis
measures a cancellation flag; running playback measures the owned process exit.
Neither measures microphone recognition, speaker buffering or room acoustics.
The dated `barge_in_latency_ms` is populated only for an accepted wake while a
playback process was running.

`python3 carlos/tests/fixtures/speech_stop_live.py` checks typed interruption and
persistent Piper worker recovery with your installed voice model. It requires
PipeWire/Pulse command tools and creates its own temporary audio servers, clock,
null sink and explicit link. It never captures the host microphone or plays
through your speakers. A successful exit confirms child cleanup and a completed
private playback stream; it does not establish acoustic voice quality.

`python3 carlos/tests/fixtures/recognition_recovery_live.py` checks persistent
whisper.cpp listener ownership and recovery using generated audio and an
available loopback port. It refuses an unrelated listener, kills only its own
worker and checks replacement recognition. It also pauses its own server,
observes a real pending HTTP audio upload, kills that worker and checks the
CLI fallback plus the next persistent request. This does not prove that server
inference started or completed. It needs your configured local
Piper/Whisper models and ffmpeg. It never records the microphone, plays audio or
submits a desktop command; temporary files and owned workers are removed.

Follow-ups such as `center that one` use the saved native window ID for up to
five minutes. They never switch to another window with the same app or title
when that ID closes. Numbered list selections expire after two minutes. Missing
or invalid timestamps expire the reference; name or inspect a current target.

## Presence observations

A locked session clears recent assistant interaction. Queued wake, listening
or command events cannot mark that locked session engaged; unlocking alone
does not establish desk occupancy. Hand presence uses fresh bounded metadata
from an already-running HoloHand tracker. Missing or malformed readings stay
unknown, and a hand reading never identifies a person. Explicit interaction
and privacy changes clear the previous camera-evidence flag.

`python3 carlos/tests/fixtures/presence_context_live.py` checks the real private
Core event and IPC path with synthetic lock/hand observations. It does not
lock your desktop, start a camera or establish physical presence.

`python3 carlos/tests/fixtures/synthesis_recovery_live.py` checks Piper recovery
with no playback. On Linux it pauses its own worker, observes the new request
in that worker's input pipe, kills only that child and checks actual one-shot
synthesis plus the next persistent request. It needs your configured local
Piper runtime/model. It does not prove speech was heard or that the paused
worker began synthesis; all owned children are checked after cleanup.

`python3 carlos/tests/fixtures/privacy_listening_live.py` checks DO NOT LISTEN
through a real private Core and its IPC API. It creates isolated audio servers
and captures only their null-sink monitor. It starts the configured local wake,
STT, preview-STT and VAD workers, verifies they and the recorder exit on mute,
checks capture refusal, then verifies Normal mode creates new ambient workers.
It needs the local models and PipeWire/Pulse tools. Host microphone, playback
routes and Carlos settings are not changed; this is not a room-acoustic test.

Saved note/task/bookmark/snippet list and read results validate their fields
and item kind. List item schemas reject a full-content field; reading content
uses the explicit read tool. The calculator, unit converter, world clock,
clipboard counts, recent-file list and expired-selection response also validate
their returned fields. Existing save/archive/append/copy permissions are unchanged.

Execution-history responses validate separate success and missing-task shapes.
History remains project-scoped and explicitly historical; saved approvals stay
unusable and further actions require fresh observations. Paged running steps
retain a null finish time rather than appearing completed. The schema validator
supports `oneOf` for exactly one allowed shape and rejects conflicting variants.

Personal-item saves, appends, task state changes and archive/restore check the
exact committed SQLite row before reporting success. If a later writer changes
or removes it before the check, the action reports a readback error; Carlos does
not repeat the write or undo the later edit. This verifies one observed database
state, not that the item will remain unchanged. Private-session writes still use
RAM-only storage, and guest mode still denies personal tools.

Waiting reminder speech belongs to the privacy mode and action generation that
claimed it. Changing privacy mode or explicitly stopping actions discards that
waiting speech. Guest mode does not claim or publish personal reminders. A
worker returning after a privacy transition cannot publish the old labels.

Queued reminder speech also checks the exact claimed database occurrence before
delivery. Snoozing it, cancelling a recurring reminder or removing the record
prevents the stale occurrence from being spoken. Repeated claims for the same
reminder replace its older queued occurrence. A privacy change during the
readback still discards the old speech. This observes database state before
speech dispatch; subsequent changes can race with speech already in progress.

## Conversation controls

Settings includes Humor (off/light/normal), Sarcasm (off/light), Use my name
(rare/normal/often) and Speaking rate. Humor, name frequency and working chatter
change the local prompt; they do not let Carlos invent names or claim actions.
Speaking rate changes the next speech request without restarting Piper. The
existing rate is preserved when another personality setting changes. Response
length, tone, technical language, acknowledgements and voice color remain.

The same settings are available through `evctl personality`, for example:

```sh
evctl personality humor light
evctl personality sarcasm off
evctl personality name_usage rare
evctl personality speaking_rate 1.15
```

Rates accept numeric values from 0.65 through 1.50. Invalid mixed updates leave
settings unchanged. Persistent personality changes are refused during Private
Session, Guest mode and privacy transitions. Greeting frequency is controlled
separately in Presence settings.

Alert Voice defaults to off. Set it in Settings or with
`evctl personality proactive_speech_threshold high` (HIGH and EMERGENCY),
`emergency` (EMERGENCY only), or `off`. Thermal alerts come from telemetry;
storage and security alerts require a recent local monitor observation. These
warnings use fixed text and never read event messages or filenames aloud.
HIGH waits until Carlos is idle. EMERGENCY may interrupt his current speech,
but does not interrupt tools, open the microphone or start a follow-up window.

Delivery requires a fresh unlocked session, enabled speech output and available
TTS. Privacy/guest sessions, quiet scenes and gaming suspension suppress alerts.
Stop, lock, privacy, scene and resume changes discard queued warnings; policy
changes during delivery cancel only the owned alert speech task. Queued events
expire after ten seconds. Same-kind events coalesce, with a two-minute cooldown
per kind and priority after an attempt, including failed or cancelled attempts.
The disabled worker waits for an event without an idle polling loop. Explicit
Codex waiting prompts are not currently connected to this speech queue.

For a silent native check with installed Piper/model dependencies, run
`python3 carlos/scripts/check-personality-delivery.py`. It uses private Core IPC,
numeric rate and alert-threshold CLI requests, temporary settings, two rate
samples and one warning routed through actual event persistence and silent Piper;
it does not play audio or open a microphone. `--core-dir` selects an installed
Core tree; `--config` selects the configuration providing TTS dependency paths.


Archive evidence

`files.archive_inspect` reports a ZIP/TAR inventory with a preview of at most
50 members. Its verified receipt covers metadata only; `payload_verified` stays
false, including when a compressed file has a bad CRC. A limited preview is
not a partial inventory: accepted input was checked against the existing entry,
expansion, path and member-type limits before returning it.

`files.archive_extract` requires a new destination. It fully reads payloads,
checks staged SHA-256 hashes and uses Linux atomic no-overwrite publication.
Its receipt covers that staged publication, not protection against later edits
or crash durability of the parent directory. Existing destinations and bad
payload CRCs are refused; temporary staging is cleaned up. Neither tool runs
archive content, and extraction does not retain executable permissions.

Both tools declare offline support, strict typed outputs and no automatic undo.
Constant output fields are enforced, including false mutation/recovery flags;
JSON booleans and numbers are distinct, while equivalent numeric constants
remain accepted. Missing, contradictory or inconsistent archive evidence never
receives a generic verified result.
