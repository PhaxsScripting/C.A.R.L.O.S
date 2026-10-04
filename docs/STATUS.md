# Current acceptance

Updated October 4, 2026 after proactive warnings, archive contracts and Sentinel reference validation. This is a progress
matrix, not a declaration that the entire specification is complete. WORKING
means tested in the stated scope; PARTIAL names the remaining work; BLOCKED
names the observed blocker. Component availability and FULLY_READY diagnostics
are runtime readiness observations, not acceptance of this whole matrix.

Evidence and test boundaries are recorded in [VALIDATION.md](VALIDATION.md).
The latest full suite passed 1,624 Python tests. Reminder checks use real private Core IPC and temporary SQLite data with a
silent mocked speech sink. File checks use owned temporary files, native
permissions and installed-source private IPC; no host personal content is read.
The installed catalogue contains 257 tools and 173 incomplete declarations.

| Feature | Status | Evidence and remaining scope |
| --- | --- | --- |
| Carlos Core | WORKING | Earlier installed Core health/IPC checks passed; latest installed file/transfer checks used an isolated instance and preserved the stopped host Core. Latest installed archive fixtures also passed in an isolated instance. Other-machine boot and long-run acceptance remain separate. |
| Event Fabric | WORKING | Actual private Core event subscriptions and Unix IPC passed reminder privacy and stop checks; this is local transport evidence. |
| Capability Registry | PARTIAL | 257 registered tools; 173 declarations still have contract gaps. Registration and live availability do not prove an action succeeded. |
| Planner | PARTIAL | Deterministic plans and bounded no-replay recovery pass regressions; unrestricted natural requests and room conversation need broader acceptance. |
| Executor | WORKING | Shared execution path passed owned-window actions and actual private SQLite mutations. Tool-specific limits still apply. |
| Verifier | PARTIAL | Exact window/database and file-content readbacks and scoped receipts pass; input delivery, inference and OS power dispatch cannot prove their final effect. |
| Permission System | WORKING | Local permission tokens, argument binding and strict action boundaries are tested. Remote passkey/device acceptance is separate. |
| Cancellation | WORKING | Owned IPC stop, waiting actions and private speech-worker cleanup were exercised; an already dispatched OS action can still finish. |
| Wake Detection | PARTIAL | Native generated-speech and worker lifecycle checks pass. Normal-distance wake and false activations per hour are not measured. |
| Streaming STT | PARTIAL | Native persistent recognition, pending-upload crash and replacement/fallback pass. Room partial latency and recognition quality remain unmeasured. |
| Streaming TTS | PARTIAL | Native PCM, persistent worker and queued-request crash recovery pass. Acoustic first-audio and perceived clarity remain unmeasured. |
| Full Duplex | PARTIAL | Local pipeline and silent fixtures exist; simultaneous room speech/media acceptance is incomplete. |
| Barge-In | PARTIAL | Typed/private stop and cleanup work. Spoken interruption latency and room success rates remain unmeasured. |
| Echo Cancellation | PARTIAL | Private audio transport checks do not validate physical echo cancellation with speakers or headphones. |
| Attention Model | PARTIAL | Lock, freshness and invalid-signal checks pass; physical attention and false-engagement rates remain unmeasured. |
| Offline AI | PARTIAL | Actual no-route local reasoning/IPC checks are recorded. Broad task-quality and long-running offline acceptance remain. |
| Model Router | PARTIAL | Provider selection and privacy rejection pass; broader provider failure/latency comparison remains. |
| Offline Voice | PARTIAL | Native offline synthesis/recognition checks pass; physical microphone and audible room acceptance remain. |
| Carlos Presence | PARTIAL | Private Core lock/hand-context fixture and freshness checks pass with synthetic signals. Real identity/desk accuracy remain unverified. |
| Greeting Logic | PARTIAL | Configured cooldown and presence gating are tested. Actual home-coming behavior remains unverified. |
| Scene Engine | PARTIAL | Preview and owned-window scene restoration pass. Full-session and physical display wake remain unverified. |
| Desktop Control | PARTIAL | Installed owned-window layout, typing, pairing and guarded undo passed. The full application/desktop matrix remains. |
| Desktop Verification | PARTIAL | Independent geometry/identity readbacks work for checked cases; arbitrary application state is not universally observable. |
| Multi Monitor | PARTIAL | Owned-window and display-placement checks cover the local setup. Physical hotplug and other display arrangements remain. |
| Carlos Vision | PARTIAL | On-demand capture and inference boundaries are implemented. Broad physical scene accuracy is not established. |
| Carlos HUD | PARTIAL | Native UI checks and scoped status controls pass; contextual real-world acceptance remains. |
| HoloHand Tracking | PARTIAL | Native engine/counter checks are recorded; no-hand FPS is not accuracy evidence. Latest runtime observation was UNAVAILABLE. |
| HoloHand Gestures | PARTIAL | Control/calibration interfaces exist. Saved calibration and the complete real-hand matrix remain unverified. |
| Gesture Desktop Control | PARTIAL | Native controls exist; fatigue, false activation and real-hand application acceptance remain. |
| Codex Bridge | PARTIAL | Actual isolated repair gateway check passed. Contextual voice proposal and deployment acceptance remain. |
| Carlos Engineering | PARTIAL | Reviewed isolated coding jobs are supported. Broad real-project and contextual initiation acceptance remain. |
| Self Healing | PARTIAL | Owned VAD, STT, Piper and Core failures were exercised. Physical device/network and whole-session chaos coverage remain. |
| Rollback | PARTIAL | Scoped installed backups and guarded owned-window undo are verified. Arbitrary external actions are not automatically reversible. |
| Carlos Memory | PARTIAL | Project-scoped history and explicit personal data pass native/private SQLite checks. General/project saves and deletions now read back exact committed rows; corruption and reinsertion refuse success without replay. Private labels/readbacks share the store lock. Broader retention and physical acceptance remain. |
| Workspace Snapshots | PARTIAL | Owned saved layout snapshots work. Unsaved editor buffers and arbitrary terminal state remain unsupported. |
| Workspace Restoration | PARTIAL | Exact saved layouts and native relaunch checks pass. Unsaved application state cannot be reconstructed generically. |
| Carlos Mobile | PARTIAL | Backend/browser checks and local health pass. Latest school-iPad/device-wide acceptance remains unverified. |
| Remote Desktop | PARTIAL | Live local KDE browser stream/reconnect checks are recorded. Current remote-device latency and network matrix remain. |
| Mobile Touch | PARTIAL | Browser input exists and earlier device control was reported. Current gesture/input matrix on the iPad remains unverified. |
| Mobile Carlos Voice | PARTIAL | Backend voice transport/unit checks exist. Current device microphone, playback and cellular voice acceptance remain. |
| Mobile Codex | PARTIAL | Gateway capability exists. Current mobile job/proposal/review flow remains unverified. |
| Mobile Terminal | PARTIAL | Backend/browser terminal checks pass. Current device-side acceptance remains. |
| Mobile Files | PARTIAL | Browser upload/edit/download/copy/move/delete checks pass; current device-side acceptance remains. |
| Tailscale | PARTIAL | Current local node reports CONNECTED; forced public HTTPS health and protected-route refusal were verified after VPN reconnect. This is not school-network acceptance. |
| Cellular | PARTIAL | Earlier phone acceptance does not establish the latest iPad/school/current-cellular matrix. |
| Reconnect | PARTIAL | Browser socket and heartbeat recovery passed. Full device/network switching and long-run matrix remain. |
| Wake-on-LAN | PARTIAL | End-to-end sleeping-host wake is unverified; configured firmware/NIC and an independent wake sender need acceptance. |
| Wake-on-WLAN | PARTIAL | Firmware/adapter support and end-to-end wireless wake are not established. |
| Intel AMT/vPro | PARTIAL | No tested management provisioning or power-state control evidence is available; hardware support is not claimed. |
| Remote Power | PARTIAL | Native scheduled power intent/cancellation is checked. Final shutdown/suspend/wake effects were not exercised in this pass. |
| Carlos Sentinel | PARTIAL | Bundled authenticated reference node, safe no-network simulation, actual private Unix client/server and trusted loopback TLS checks pass. Replay survives restart; fresh signed host heartbeat is required for ONLINE. Independent hardware remains NOT_INSTALLED; physical wake is unverified. See SENTINEL.md. |
| Privacy Modes | PARTIAL | Actual private audio worker stop, RAM-only storage and queued-reminder scope checks pass. Physical hardware mute and whole-session network audit remain. |
| Authentication | PARTIAL | Protected public routes refuse unauthenticated requests. Current device token/passkey lifecycle acceptance remains. |
| Security Monitoring | PARTIAL | Local inspection and scoped observations exist. Complete exposure, retention and remote-device audit remain. |
| Boot Integration | PARTIAL | Installer/startup and explicit runtime restart are checked. Fresh boot/login and resume-to-all-ready measurements remain. |
| Daily Stability | PARTIAL | Regression and owned recovery fixtures pass. A full daily-session soak has not been performed. |

Personality settings now include humor, sarcasm, name frequency and speaking
rate. Native controls, private IPC/CLI persistence and actual silent Piper rate
changes passed. Prompt preferences do not establish subjective conversational
quality. Alert Voice now defaults off and supports high/emergency thresholds
with fresh-session, privacy, quiet, stop and gaming gates. Private source and
installed fixtures routed a warning through event persistence and generated
silent Piper PCM. This does not verify room hearing or a daily-session soak;
explicit Codex waiting prompts are not connected to the speech queue.

See [PERFORMANCE.md](PERFORMANCE.md) for measured values and missing measurements.
