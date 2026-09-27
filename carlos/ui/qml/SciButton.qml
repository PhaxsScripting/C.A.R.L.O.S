import QtQuick
import QtQuick.Controls

Button {
    id: control
    property color accent: "#67e4ff"
    Accessible.name: text
    Accessible.role: Accessible.Button
    implicitHeight: 40
    focusPolicy: Qt.StrongFocus
    leftPadding: 16; rightPadding: 16; topPadding: 10; bottomPadding: 10
    font.family: "Liberation Sans"; font.pixelSize: 12; font.bold: true
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    contentItem: Text {
        text: control.text; color: control.enabled ? "#dff9ff" : "#728491"
        font: control.font
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: HudPanel {
        color: control.down ? "#254557" : control.hovered ? "#183242" : "#0c1c29"
        accent: control.enabled ? control.accent : "#526574"
        lineColor: control.visualFocus ? "#e6fcff" : control.hovered ? control.accent : "#657f90"
        cut: 0; technical: false; tint: 0
    }
}
