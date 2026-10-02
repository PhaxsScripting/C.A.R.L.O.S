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

Use `plans` for plans, `agent-tasks` for task history, and `security` for the
current permission settings. `stop` shuts down the core. `--help` lists the rest
of the CLI commands.

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
