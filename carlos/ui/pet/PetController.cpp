#include "PetController.h"
#include "../platform/DesktopPlatform.h"
#include <QApplication>
#include <QCursor>
#include <QDBusConnection>
#include <QDBusConnectionInterface>
#include <QDBusInterface>
#include <QDBusReply>
#include <QDBusServiceWatcher>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMenu>
#include <QProcess>
#include <QQuickWindow>
#include <QRegion>
#include <QScreen>
#include <QStandardPaths>
#include <QTemporaryFile>

namespace {
const QString service = "org.phax.CarlosPet";
const QString plugin = "org.phax.carlos.pet.runtime";
const QString dragPlugin = "org.phax.carlos.pet.drag";
} // namespace

PetController::PetController(bool preview, QObject *parent)
    : QObject(parent), m_settings("Carlos", "DesktopPet"), m_preview(preview) {
    m_clock.start();
    m_right = qMax(0, m_settings.value("right", 24).toInt());
    m_bottom = qMax(0, m_settings.value("bottom", 64).toInt());
    if (preview) {
        m_corePrivate = false;
        m_locked = false;
        m_bubble = "tiny rubber duck reporting for duty";
        return;
    }
    auto bus = QDBusConnection::sessionBus();
    bus.registerObject("/Pet", this,
                       QDBusConnection::ExportScriptableSlots |
                           QDBusConnection::ExportAllProperties);
    connect(&m_timer, &QTimer::timeout, this, &PetController::tick);
    m_timer.start(1000);
    connect(&m_coreTimer, &QTimer::timeout, this, &PetController::pollCore);
    m_coreTimer.start(15000);
    m_coreTimeout.setSingleShot(true);
    connect(&m_coreTimeout, &QTimer::timeout, this, [this] { coreUnavailable(); m_core.abort(); });
    connect(&m_core, &QLocalSocket::disconnected, this, &PetController::coreUnavailable);
    connect(&m_core, &QLocalSocket::errorOccurred, this, [this] { coreUnavailable(); });
    connect(&m_core, &QLocalSocket::connected, this,
            [this] { m_core.write("{\"type\":\"panel.subscribe\",\"id\":\"pet-watch\",\"payload\":{}}\n"); });
    connect(&m_core, &QLocalSocket::readyRead, this, [this] {
        m_buffer += m_core.readAll();
        if (m_buffer.size() > 1048576) {
            m_buffer.clear();
            coreUnavailable();
            m_core.abort();
            return;
        }
        while (m_buffer.contains('\n')) {
            const auto end = m_buffer.indexOf('\n');
            const auto message = QJsonDocument::fromJson(m_buffer.left(end)).object();
            m_buffer.remove(0, end + 1);
            if (message.value("type") == "error") {
                coreUnavailable();
                m_core.abort();
                return;
            }
            if (message.value("type") != "panel.state" &&
                (message.value("id") != "pet" || message.value("type") != "response"))
                continue;
            const auto data = message.value("payload").toObject();
            applyCoreState(data);
            m_coreTimeout.stop();
        }
    });
    auto *kwin = new QDBusServiceWatcher("org.kde.KWin", bus,
                                         QDBusServiceWatcher::WatchForOwnerChange, this);
    connect(kwin, &QDBusServiceWatcher::serviceOwnerChanged, this,
            [this](const QString &, const QString &, const QString &owner) {
                m_tracking = false;
                m_loaded = false;
                clearContext();
                if (!owner.isEmpty())
                    startTracking();
                emit changed();
            });
    pollCore();
}

PetController::~PetController() { stop(); }
void PetController::stop() {
    endDrag();
    m_timer.stop();
    m_coreTimer.stop();
    m_core.abort();
    if (m_loaded) {
        QDBusInterface scripts("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting");
        scripts.call("unloadScript", plugin);
        m_loaded = false;
    }
    m_trackingSource.reset();
    m_dragSource.reset();
}
bool PetController::shown() const {
    return !m_locked && !m_hidden && !m_corePrivate && m_clock.elapsed() >= m_snoozeUntil &&
           !(m_fullscreen && m_settings.value("hideFullscreen", false).toBool());
}
void PetController::attach(QWindow *window) {
    m_window = window;
    m_nativeOverlay = CarlosDesktop::nativeOverlay();
    CarlosDesktop::configureOverlay(window, true);
    moveToScreen(QGuiApplication::screenAt(QCursor::pos()));
    position();
    auto mask = [this] {
        QRegion input(121, 92, 96, 100);
        if (!m_bubble.isEmpty())
            input += QRect(8, 8, 226, 83);
        m_window->setMask(input);
    };
    connect(this, &PetController::changed, window, mask);
    mask();
    connect(window, &QWindow::screenChanged, this, [this] { position(); });
    connect(qApp, &QGuiApplication::screenRemoved, this, [this] { position(); });
}
void PetController::moveToScreen(QScreen *screen) {
    if (!m_window || !screen || m_preview)
        return;
    const bool wasHidden = m_hidden;
    // Remap the layer surface so KWin actually changes its output.
    m_hidden = true;
    emit changed();
    m_window->setScreen(screen);
#ifdef CARLOS_HAVE_LAYER_SHELL
    if (m_nativeOverlay)
        LayerShellQt::Window::get(m_window)->setScreen(screen);
#endif
    position();
    m_hidden = wasHidden;
    emit changed();
}
void PetController::position() {
    if (!m_window || m_preview)
        return;
    auto *screen = m_window->screen() ? m_window->screen() : QGuiApplication::primaryScreen();
    if (!screen)
        return;
    const auto size = screen->availableGeometry().size();
    m_right = qBound(0, m_right, qMax(0, size.width() - m_window->width()));
    m_bottom = qBound(0, m_bottom, qMax(0, size.height() - m_window->height()));
#ifdef CARLOS_HAVE_LAYER_SHELL
    if (m_nativeOverlay)
        LayerShellQt::Window::get(m_window)->setMargins(QMargins(0, 0, m_right, m_bottom));
    else
#endif
        m_window->setPosition(screen->availableGeometry().topLeft() +
                              QPoint(size.width() - m_window->width() - m_right,
                                     size.height() - m_window->height() - m_bottom));
    // Margins wait for a surface commit. Don't make the next click do it.
    if (auto *quick = qobject_cast<QQuickWindow *>(m_window))
        quick->update();
}
void PetController::beginDrag() {
    endDrag();
    m_dragging = true;
    m_dragMoved = false;
    m_hasDragPointer = false;
    m_dragRight = m_right;
    m_dragBottom = m_bottom;
    ++m_dragSerial;
    emit dragChanged();
    if (m_preview)
        return;
    if (!m_nativeOverlay) {
        m_dragStart = QCursor::pos();
        if (QGuiApplication::platformName() == "wayland" && m_window)
            m_window->startSystemMove();
        return;
    }
    QFile source(":/pet/drag.js");
    if (!source.open(QIODevice::ReadOnly)) {
        m_dragging = false;
        return;
    }
    m_dragSource = std::make_unique<QTemporaryFile>();
    auto &file = *m_dragSource;
    if (!file.open()) {
        m_dragging = false;
        return;
    }
    file.write(source.readAll()
                   .replace("@SERIAL@", QByteArray::number(m_dragSerial))
                   .replace("@PID@", QByteArray::number(QCoreApplication::applicationPid())));
    file.flush();
    QDBusInterface scripts("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting");
    scripts.call("unloadScript", dragPlugin);
    QDBusReply<int> loaded = scripts.call("loadScript", file.fileName(), dragPlugin);
    if (!loaded.isValid() || loaded.value() < 0) {
        m_dragging = false;
        return;
    }
    // KWin reuses script IDs. Start through the manager so this one actually runs.
    if (scripts.call("start").type() == QDBusMessage::ErrorMessage)
        endDrag();
}
void PetController::DragPointer(int x, int y, int serial) {
    if (!calledFromDBus() || message().service() != m_kwinOwner || !m_dragging ||
        serial != m_dragSerial)
        return;
    const QPointF pointer(x, y);
    if (!m_hasDragPointer) {
        m_dragStart = pointer;
        m_hasDragPointer = true;
        return;
    }
    const auto delta = pointer - m_dragStart;
    if (!m_dragMoved) {
        if (qAbs(delta.x()) + qAbs(delta.y()) <= 5)
            return;
        m_dragMoved = true;
        emit dragChanged();
    }
    m_right = m_dragRight - qRound(delta.x());
    m_bottom = m_dragBottom - qRound(delta.y());
    position();
}
void PetController::dragPortable() {
    if (!m_dragging || m_nativeOverlay || !m_window)
        return;
    const auto delta = QCursor::pos() - m_dragStart;
    if (delta.manhattanLength() <= 5 && !m_dragMoved)
        return;
    if (!m_dragMoved) {
        m_dragMoved = true;
        emit dragChanged();
    }
    if (QGuiApplication::platformName() == "wayland")
        return;
    m_right = m_dragRight - qRound(delta.x());
    m_bottom = m_dragBottom - qRound(delta.y());
    position();
}
void PetController::endDrag() {
    if (!m_dragging)
        return;
    m_dragging = false;
    if (!m_preview) {
        if (m_nativeOverlay) {
            QDBusInterface scripts("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting");
            scripts.call("unloadScript", dragPlugin);
        }
        m_settings.setValue("right", m_right);
        m_settings.setValue("bottom", m_bottom);
    }
    m_dragSource.reset();
}
void PetController::clearContext() {
    m_bubble.clear();
    m_policy.reset(m_clock.elapsed());
}
void PetController::SetLocked(bool locked) {
    m_locked = locked;
    if (locked) {
        endDrag();
        clearContext();
    } else if (!m_preview) {
        startTracking();
        pollCore();
    }
    emit changed();
}
void PetController::Observe(const QString &app, bool fullscreen, const QString &screenName) {
    if (!calledFromDBus() || message().service() != m_kwinOwner)
        return;
    m_tracking = true;
    if (m_needsScreen && m_window && MoveToScreen(screenName))
        m_needsScreen = false;
    m_fullscreen = fullscreen;
    if (m_locked || m_corePrivate || quiet())
        clearContext();
    else
        m_policy.observe(app, m_clock.elapsed());
    if (!shown())
        m_bubble.clear();
    emit changed();
}
void PetController::Quit() { QTimer::singleShot(0, qApp, &QCoreApplication::quit); }
bool PetController::MoveToScreen(const QString &name) {
    for (auto *screen : QGuiApplication::screens()) {
        if (screen->name() == name) {
            moveToScreen(screen);
            return true;
        }
    }
    return false;
}
void PetController::Show() {
    m_needsScreen = true;
    if (!m_preview)
        startTracking();
    moveToScreen(QGuiApplication::screenAt(QCursor::pos()));
    m_hidden = false;
    m_snoozeUntil = 0;
    emit changed();
}
void PetController::say(const QString &line) {
    if (line.isEmpty())
        return;
    m_bubble = line;
    m_bubbleUntil = m_clock.elapsed() + 7500;
    emit changed();
}
void PetController::pet() {
    if (!shown() || m_clock.elapsed() - m_lastPet < 600)
        return;
    m_lastPet = m_clock.elapsed();
    m_policy.interacted(m_lastPet);
    if (quiet())
        return;
    static unsigned pats = 0;
    const QStringList lines = {"okay yeah i needed that", "head pats acquired",
                               "morale increased by a silly amount",
                               "your tiny coworker appreciates you"};
    say(lines.at(pats++ % lines.size()));
}
void PetController::tick() {
    if (!shown() || m_clock.elapsed() >= m_bubbleUntil) {
        if (!m_bubble.isEmpty()) {
            m_bubble.clear();
            emit changed();
        }
    }
    if (m_snoozeUntil && m_clock.elapsed() >= m_snoozeUntil) {
        m_snoozeUntil = 0;
        emit changed();
    }
    say(m_policy.next(m_clock.elapsed(), shown() && !quiet() && !m_dragging));
}
void PetController::pollCore() {
    if (m_core.state() == QLocalSocket::ConnectedState) {
        m_core.write("{\"type\":\"panel.summary\",\"id\":\"pet\",\"payload\":{}}\n");
        m_coreTimeout.start(1200);
        return;
    }
    if (m_core.state() != QLocalSocket::UnconnectedState) return;
    const auto path =
        QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation) + "/ev/ev.sock";
    if (!QFile::exists(path)) {
        coreUnavailable();
        return;
    }
    m_buffer.clear();
    m_core.connectToServer(path);
    m_coreTimeout.start(1200);
}
void PetController::applyCoreState(const QJsonObject &data) {
    const auto privacy = data.value("privacy_mode");
    m_corePrivate = !privacy.isBool() || privacy.toBool() || data.value("status") == "denied";
    const auto locked = data.value("session_locked");
    const bool lockState = !locked.isBool() || locked.toBool();
    if (m_locked != lockState) SetLocked(lockState);
    const auto state = data.value("state").toString();
    m_mood = !m_corePrivate && (state == "THINKING" || state == "USING_TOOL") ? "thinking" : "happy";
    if (m_corePrivate) clearContext();
    emit changed();
}
void PetController::coreUnavailable() {
    m_corePrivate = true;
    if (!m_locked) SetLocked(true);
    m_mood = "happy";
    m_buffer.clear();
    clearContext();
    emit changed();
}
void PetController::startTracking() {
    auto bus = QDBusConnection::sessionBus();
    const auto ownerReply = bus.interface()->serviceOwner("org.kde.KWin");
    if (m_loaded && ownerReply.value() == m_kwinOwner)
        return;
    m_kwinOwner = ownerReply.value();
    if (m_kwinOwner.isEmpty())
        return;
    QFile source(":/pet/activity.js");
    if (!source.open(QIODevice::ReadOnly)) {
        qWarning("Pet activity resource could not be opened");
        return;
    }
    auto file = std::make_unique<QTemporaryFile>();
    if (!file->open()) {
        qWarning("Pet activity script could not be created");
        return;
    }
    file->write(source.readAll());
    file->flush();
    QDBusInterface scripts("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus);
    if (!m_dragging)
        scripts.call("unloadScript", dragPlugin);
    scripts.call("unloadScript", plugin);
    // KWin can read the file after run() returns. Keep it until unload.
    m_trackingSource = std::move(file);
    QDBusReply<int> loaded = scripts.call("loadScript", m_trackingSource->fileName(), plugin);
    if (!loaded.isValid() || loaded.value() < 0) {
        qWarning() << "Pet activity script load failed:" << loaded.error().message();
        return;
    }
    m_loaded = true;
    // A reused ScriptN path can point at an older script. The manager knows ours.
    const auto started = scripts.call("start");
    if (started.type() == QDBusMessage::ErrorMessage) {
        qWarning() << "Pet activity script start failed:" << started.errorMessage();
        scripts.call("unloadScript", plugin);
        m_loaded = false;
        m_trackingSource.reset();
    }
}
void PetController::menu() {
    QMenu menu;
    menu.addSection("Carlos, your tiny desk buddy");
    auto option = [this, &menu](const QString &label, const QString &key, bool fallback) {
        auto *action = menu.addAction(label);
        action->setCheckable(true);
        action->setChecked(m_settings.value(key, fallback).toBool());
        connect(action, &QAction::toggled, this, [this, key](bool value) {
            m_settings.setValue(key, value);
            clearContext();
            emit changed();
        });
    };
    option("Quiet mode", "quiet", false);
    option("Reduced motion", "still", false);
    option("Hide during fullscreen apps", "hideFullscreen", false);
    option("Launch with Carlos", "launchWithCarlos", true);
    auto *screens = menu.addMenu("Move to screen");
    for (auto *screen : QGuiApplication::screens()) {
        auto *action = screens->addAction(screen->name());
        connect(action, &QAction::triggered, this, [this, screen] { moveToScreen(screen); });
    }
    menu.addSeparator();
    auto *snooze = menu.addAction("Nap for 15 minutes");
    auto *hide = menu.addAction("Hide pet (restore from tray)");
    auto *reset = menu.addAction("Back to the corner");
    auto *open = menu.addAction("Open Carlos");
    menu.addSeparator();
    menu.addSection(m_tracking ? "App identity only. No screenshots or typing."
                               : "App context unavailable on this desktop.");
    auto *quit = menu.addAction("Quit pet");
    auto *selected = menu.exec(QCursor::pos());
    if (selected == snooze) {
        m_snoozeUntil = m_clock.elapsed() + 900000;
        clearContext();
    }
    if (selected == hide) {
        m_hidden = true;
        clearContext();
    }
    if (selected == reset) {
        m_right = 24;
        m_bottom = 64;
        position();
        endDrag();
    }
    if (selected == open) {
        const auto binary = QStandardPaths::findExecutable("ev-ui");
        if (!binary.isEmpty())
            QProcess::startDetached(binary, {});
    }
    if (selected == quit)
        QCoreApplication::quit();
    emit changed();
}
