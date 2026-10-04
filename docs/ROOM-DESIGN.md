# Desk and room interfaces

This is the interface plan for future hardware. No device, driver or room
integration is required to install Carlos. Nothing here reports an imaginary
sensor or registers a tool for absent hardware.

## Device kinds

| Kind | Observations | Possible local actions | Important boundary |
| --- | --- | --- | --- |
| Light | Power, supported brightness range, optional colour capability | Set power, brightness or supported colour | Read back the exact device; unsupported colour remains absent |
| Display | Power/input and advertised wake support | Select input or request supported power state | An external display device is distinct from KWin window placement |
| Speaker | Power, volume, mute and supported playback transport | Set volume/mute; explicitly play an approved source | Output control does not grant permission to record room audio |
| Fan | Power, supported speed range, optional measured RPM | Set supported speed or power | Unknown RPM is not zero; cooling-critical control needs elevated risk |
| Outlet | Power and optional measured watts | Set power with confirmation | Never automate cutting power to Core, networking or a Sentinel without a separately reviewed power plan |
| Sensor | Typed reading, units, sample clock and freshness | Read; explicitly configure supported sampling | A sensor reading does not identify a person or grant action permission |

A reading contains an immutable node/device identity, the kind, capability
version, observation time and clock, declared units, and availability/freshness.
Unknown or stale values remain null with a reason. Each concrete adapter must
declare ranges and units; names such as “desk light” are aliases, not identity.
Replacing a device under the same name must invalidate outstanding actions.

## Integration boundary

Use an explicitly enabled installed `carlos.tools` plugin. Each tool declares
its input/output schemas, risk, offline availability, cancellation scope,
reversibility, deadline and required native capability. An adapter translates
only its own declared device capabilities. Local control is preferred; any
network transport must name and authenticate the exact approved node/device.
There is no ambient network scan or account integration in this design.

A scene uses the ordinary planner and executor. Before dispatch it checks the
fresh identity, current state, capability and permission. After dispatch it
reads back the declared postcondition. Acknowledgement alone is unverified.
Lost replies do not trigger automatic retries of uncertain effects. Undo is
offered only when the adapter can restore an exact captured state and refuse
intervening user/device changes.

## Physical controls

| Control | Core interaction |
| --- | --- |
| Rotary knob | Versioned semantic request such as volume adjustment; bounded rate; no arbitrary shell text |
| Push-to-talk button | Explicit capture start/end using current microphone and privacy policy |
| Physical mute | Hardware state reported independently; physical mute always wins over a software request |
| Status LED/OLED | Minimal observed readiness/privacy/attention state, with stale/offline states |
| mmWave presence | Presence evidence with timestamp and uncertainty; never person identification |
| Microphone array | A separately consented input capability; normal raw-audio retention stays off |
| Environment sensor | Typed local observations; no fabricated calibration or accuracy |

When a node disconnects, pending controls expire. A reconnect performs a new
capability observation; it does not replay an old button press, lighting command
or microphone start. Nodes do not receive Core's private databases. A hardware
adapter must pass its own identity, denial, cancellation, stale-state and real
device readback tests before being marked working.
