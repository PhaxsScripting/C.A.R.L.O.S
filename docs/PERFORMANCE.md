# Performance observations

Sampled 2026-10-03T11:57:21.373259+00:00. The development desktop stayed active.
This is a 5.052-second sample of the running Core and its OS-descended
workers, with 9 observed process identities. It excludes separate UI,
pet, mobile services and reparented runtimes. It is not a clean desktop idle
benchmark and cannot establish the requested whole-desktop RAM target.

| Measurement | Observed value | Scope |
| --- | --- | --- |
| Core plus descended-worker PSS | 1899.688 MiB | Shared pages apportioned; completeness: True |
| Core plus descended-worker summed RSS | 2013.691 MiB | Shared pages can be counted repeatedly; completeness: True |
| Interval CPU | 29.691% of one logical CPU | Identity-stable sample, including measurement overhead; completeness: True |
| Core health | DORMANT, healthy | Observation after checked deployments |
| Runtime readiness | FULLY_READY | Current component subset; does not certify full specification acceptance |
| Full Python suite | 1,555 tests, 116.621 seconds | One low-priority CPU, development run |

The resource sample includes whichever ambient/model workers were alive; it
is not a model-only or quiescent-idle measurement. Source/import/container
results and generated or silent fixtures remain separate from room/device use.

| Required measurement | Current evidence |
| --- | --- |
| Carlos quiescent idle RAM/CPU | Not measured under the final idle acceptance conditions |
| Wake latency | Normal-distance acoustic latency not measured |
| STT partial latency | Room partial latency not measured |
| First-response latency | Current end-to-end room latency not measured |
| TTS first audio | Physical playback first-audio latency not measured |
| Barge-in latency | Spoken interruption latency not measured |
| Local model, quantization, model-only RAM | Not sampled by this aggregate resource observation |
| Tokens/second and first-token latency | No current controlled model benchmark in this pass |
| HoloHand camera/inference FPS | Prior no-hand sample: 30 camera FPS and 18.8 inference FPS; current runtime UNAVAILABLE |
| Gesture latency, precision, recall, false activations/hour | No labelled real-hand acceptance measurement |
| Remote resolution/FPS/bitrate/CPU/GPU encode/latency | No current controlled device-stream benchmark in this pass |
| Boot-to-ready and resume-to-ready | No fresh boot/resume acceptance measurement |

The unknown measurements above are gaps, not zeroes or claims that the goals
are impossible. Earlier scoped experiments are described in
[VALIDATION.md](VALIDATION.md); they should not be relabelled as current room,
physical device or clean-idle results.

October 3 regression timing follow-up: an unrelated editor file-listing process
had saturated all eight CPUs for almost an hour. The initial suite had three
timing/native-capture failures. Cancelling that exact owned scan reduced sampled
CPU load; the same eleven checks passed in 2.917 seconds without test or runtime
changes. The complete suite then passed as listed above. The earlier Core memory
sample is unchanged; this does not establish quiescent idle or acoustic latency.
