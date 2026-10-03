import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: prompt
    required property var client
    readonly property string requestId: String(client.confirmation.id || "")
    property bool submitted: false
    readonly property bool visualClick: client.confirmation.tool === "vision.candidate.click"
                                       || client.confirmation.tool === "vision.click_text"
    readonly property var review: client.confirmation.review || ({})
    onRequestIdChanged: submitted = false
    objectName: "confirmation-prompt"
    visible: requestId.length > 0
    modal: true
    focus: true
    closePolicy: Popup.NoAutoClose
    width: Math.min(600, parent.width - 48)
    height: Math.min(visualClick ? 650 : 400, parent.height - 48)
    anchors.centerIn: parent
    padding: 24
    onOpened: deny.forceActiveFocus()

    function respond(approved) {
        if (submitted || !client.connected) return;
        if (approved && visualClick && preview.status !== Image.Ready) return;
        submitted = true;
        client.respondToConfirmation(approved);
    }

    background: Rectangle { color: "#0b1720"; border.color: "#657f90"; radius: 4 }
    Overlay.modal: Rectangle { color: "#b0060b10" }
    contentItem: ColumnLayout {
        spacing: 16
        Keys.onEscapePressed: prompt.respond(false)
        Text { text: "Allow this action?"; color: "#e6f9fd"; font.pixelSize: 22; font.bold: true }
        Text {
            Layout.fillWidth: true
            text: String(prompt.client.confirmation.tool || "Requested action")
            textFormat: Text.PlainText
            color: "#70e6ff"; font.pixelSize: 14; wrapMode: Text.Wrap
        }
        Image {
            id: preview
            objectName: "confirmation-preview"
            visible: prompt.visualClick
            Layout.fillWidth: true
            Layout.preferredHeight: prompt.visualClick ? Math.min(220, prompt.height * .38) : 0
            fillMode: Image.PreserveAspectFit
            source: prompt.visualClick ? String(prompt.review.preview_url || "") : ""
            asynchronous: true
            cache: false
        }
        Text {
            visible: prompt.visualClick
            Layout.fillWidth: true
            text: preview.status === Image.Error || preview.status === Image.Null
                  ? "Preview unavailable. Deny and inspect the window again."
                  : String(prompt.review.caption || "Review the highlighted label.")
            textFormat: Text.PlainText
            color: "#c2d3de"; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            TextArea {
                text: String(prompt.client.confirmation.reason || "This action needs your permission.")
                readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                color: "#c2d3de"; font.pixelSize: 14
                background: null
            }
        }
        Text {
            Layout.fillWidth: true
            text: prompt.submitted ? "Sending your choice…" : String(prompt.client.confirmation.permission || "SENSITIVE")
            color: "#a9c0cb"; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            SciButton {
                id: deny
                objectName: "confirmation-deny"
                text: "Deny"
                enabled: prompt.client.connected && !prompt.submitted
                onClicked: prompt.respond(false)
            }
            SciButton {
                objectName: "confirmation-approve"
                text: "Allow once"
                enabled: prompt.client.connected && !prompt.submitted
                         && (!prompt.visualClick || preview.status === Image.Ready)
                onClicked: prompt.respond(true)
            }
        }
    }
}
