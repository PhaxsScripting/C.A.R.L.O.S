# C.A.R.L.O.S.

> I fucking hate Carlos. He doesn't respond on time.

My desktop AI assistant. He talks, handles desktop tasks, remembers things you
ask him to, and has a Qt control center with a voice HUD. Python for the core,
C++ and QML for the UI. Built on Gentoo with KDE Plasma.

Still working on him. Voice latency and wake reliability need work, and other
desktops haven't had the same testing. This is a source release, so you'll need
to set up your own speech runtimes and models.

## What's here

| Folder | What's in it |
| --- | --- |
| [`carlos/core/ev`](carlos/core/ev) | AI providers, voice, memory, desktop tools, permissions and local IPC |
| [`carlos/ui`](carlos/ui) | Control center, voice HUD and desktop pet |
| [`carlos/plasma`](carlos/plasma) | Optional voice activity widget for Plasma |
| [`carlos/assets`](carlos/assets) | Desktop bridge and wake word files |
| [`carlos/scripts`](carlos/scripts) | Install, rollback, diagnostics and benchmarks |
| [`carlos/tests`](carlos/tests) | Core and UI tests |
| [`docs`](docs) | Setup, usage and test results |

This repo is just Carlos. HoloHand and the phone app are installed separately.
Carlos has optional hooks for them and a small [Sentinel reference](docs/SENTINEL.md)
for future independent wake hardware. No separate device is provisioned automatically;
these integrations are not required to build or use the assistant.

## Get started

Read [setup](docs/SETUP.md) for dependencies and the UI build.

```sh
git clone https://github.com/PhaxsScripting/C.A.R.L.O.S..git
cd C.A.R.L.O.S.
python3 -m venv .venv
. .venv/bin/activate
pip install -r carlos/requirements.txt
PYTHONPATH=carlos/core python3 -m unittest discover -s carlos/tests
```

Cloning and building won't start Carlos. The user installer does enable login
startup, so read its notes before running it. Models, keys and personal data stay
on your machine. The old `ev` names are still there so existing installs work.

[Desktop pet](docs/PET.md) · [Using Carlos](docs/USAGE.md) · [Test results](docs/VALIDATION.md) ·
[Security](SECURITY.md) · [Third-party notices](THIRD_PARTY.md)

## License

Original code is [MIT](LICENSE), copyright Phax. Dependencies, models and voices
keep their own licenses.

## Linux support

Package setup for Ubuntu/Mint, Debian, Fedora, Arch/EndeavourOS, openSUSE
Tumbleweed and Gentoo is in [Linux setup](docs/LINUX.md). The UI builds with Qt 6.4+
and runs outside KDE too. The pet has a portable X11/XWayland backend; KWin app
comments and verified desktop automation remain KDE features. The guide lists
those differences and the actual test coverage.

Current evidence: [acceptance matrix](docs/STATUS.md) and [performance observations](docs/PERFORMANCE.md).
