# Screen clips

Say or type `record my screen for ten seconds`. Carlos asks for confirmation,
then the desktop's native dialog lets you choose one screen or window. The clip
stays on this computer. Recording does not include microphone or system audio.

`record desktop` uses ten seconds. Explicit durations can be one to 120 seconds;
`record my screen for two minutes` uses the maximum. Say `stop` to cancel Carlos's
current actions, including an unfinished recording. Completed recordings stay.

`show recording status` probes dependencies and the portal without opening a
screen selector. `show my recordings` lists the latest twenty completed clips.
The status distinguishes available dependencies from an actual verified capture.

The structured tools are:

| Tool | Arguments | Result |
| --- | --- | --- |
| `desktop.recording.status` | none | Dependencies, portal support, active state and limits |
| `desktop.recording.capture` | `seconds`, optional | Exact recording ID, private path, bytes, SHA-256, dimensions, frames and duration |
| `desktop.recording.list` | none | Latest twenty completed paths and the total count |
| `desktop.recording.delete` | `recording_id` | Confirmation required; removes this exact clip permanently |

Clips live under Carlos's data directory in `recordings`, normally
`~/.local/share/ev/recordings`. The directory is private to your user and files
are mode 600. Unfinished files have a separate name and don't appear in the list.
Cancellation joins the owned worker before discarding its unfinished file.
The Linux worker also ends if its parent dies; a hard crash can leave an unlisted
partial file in this private directory. Carlos doesn't silently resume capture.

The current format is Matroska, at 1280 by 720 and up to 15 frames per second.
Aspect ratio is preserved with borders. The encoder uses two threads, with H.264
when x264 is installed and VP8 otherwise. A static screen keeps its timeline
through PipeWire's keepalive support. Each clip is limited to 64 MiB. Recording
needs 128 MiB free to start and stops if free space falls below 16 MiB.

The tool checks a finalized container through ffprobe, positive duration and
frame observations, dimensions, size, file identity and SHA-256 before publishing
the clip. These checks verify the saved video; they don't independently identify
what was visible in the selected source.

## Linux dependencies

The distro package profiles include native Python GI, the GStreamer typelib,
PipeWire capture, the base/good plugins and ffprobe. The native worker uses the
distro's Python and doesn't import GI into a model or Core worker. It starts only
for an explicit probe or capture. The [Linux installer](LINUX.md) uses the normal
package-manager confirmation and doesn't add repositories or alter audio routing.

The desktop must provide the [XDG ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html).
Missing encoders, an absent portal or a denied grant produce a capability error;
Carlos doesn't fall back to a screenshot loop or bypass the desktop permission.
Exact KWin automation is separate from this portal recording backend.

Gentoo users need `gstreamer` enabled on PipeWire and `introspection` on GStreamer
and gst-plugins-base. gst-plugins-good supplies the Matroska muxer; gst-plugins-vpx
supplies VP8. An installed x264 plugin is used when present.

Package references: [Debian PipeWire plugin](https://packages.debian.org/trixie/amd64/gstreamer1.0-pipewire),
[Fedora PipeWire plugin](https://packages.fedoraproject.org/pkgs/pipewire/pipewire-gstreamer/),
[Arch GStreamer](https://wiki.archlinux.org/title/GStreamer),
[openSUSE PipeWire package source](https://api.opensuse.org/public/source/multimedia:libs/pipewire/pipewire.spec).
Other desktops and distribution sessions still need their own granted-capture
acceptance; package profiles and fixture tests alone don't prove that experience.
