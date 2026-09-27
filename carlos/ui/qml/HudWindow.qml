import QtQuick
Window {
    property bool surfaceReady: false
    onTransientParentChanged: if (transientParent) transientParent = null
    Component.onCompleted: transientParent = null
    readonly property bool passiveSurface: true
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
}
