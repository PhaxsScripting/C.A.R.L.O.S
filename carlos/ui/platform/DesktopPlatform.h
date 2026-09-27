#pragma once
#include <QGuiApplication>
#include <QWindow>
#ifdef CARLOS_HAVE_LAYER_SHELL
#include <LayerShellQt/Window>
#endif

namespace CarlosDesktop {
inline bool kdeSession() {
    return qEnvironmentVariable("XDG_CURRENT_DESKTOP")
        .split(':')
        .contains("KDE", Qt::CaseInsensitive);
}
inline void choosePlatform() {
    if (qEnvironmentVariableIsSet("QT_QPA_PLATFORM"))
        return;
    bool native = false;
#ifdef CARLOS_HAVE_LAYER_SHELL
    native = kdeSession() && qEnvironmentVariable("CARLOS_DESKTOP_BACKEND") != "portable";
#endif
    // XWayland gives portable overlays real placement and dragging on GNOME too.
    if (!native && !qEnvironmentVariable("WAYLAND_DISPLAY").isEmpty() &&
        !qEnvironmentVariable("DISPLAY").isEmpty())
        qputenv("QT_QPA_PLATFORM", "xcb");
}
inline bool nativeOverlay() {
#ifdef CARLOS_HAVE_LAYER_SHELL
    return kdeSession() && QGuiApplication::platformName() == "wayland" &&
           qEnvironmentVariable("CARLOS_DESKTOP_BACKEND") != "portable";
#else
    return false;
#endif
}
inline void configureOverlay(QWindow *window, bool pet) {
#ifdef CARLOS_HAVE_LAYER_SHELL
    if (nativeOverlay()) {
        auto *layer = LayerShellQt::Window::get(window);
        layer->setScope(pet ? "carlos-pet" : "ev-voice-hud");
        layer->setLayer(LayerShellQt::Window::LayerOverlay);
        auto anchors = LayerShellQt::Window::Anchors(LayerShellQt::Window::AnchorBottom);
        if (pet)
            anchors |= LayerShellQt::Window::AnchorRight;
        layer->setAnchors(anchors);
        layer->setExclusiveZone(0);
        layer->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
        layer->setActivateOnShow(false);
    }
#else
    Q_UNUSED(window);
    Q_UNUSED(pet);
#endif
}
} // namespace CarlosDesktop
