import QtQuick
import QtQuick.Controls

ScrollBar {
    id: bar
    policy: ScrollBar.AsNeeded
    minimumSize: 0.05
    contentItem: Rectangle {
        implicitWidth: 6
        implicitHeight: 6
        radius: 3
        color: bar.pressed || bar.hovered ? "#a6bdcb" : "#657f90"
        visible: bar.size < 1
    }
}
