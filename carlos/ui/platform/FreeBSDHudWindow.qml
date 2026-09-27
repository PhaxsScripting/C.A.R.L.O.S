import QtQuick
Window {
 property bool surfaceReady: false
 readonly property bool passiveSurface: true
 flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
}
