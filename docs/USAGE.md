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
