import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    required property var client
    property bool projectScope: false
    readonly property bool validScope: !projectScope || projectPath.text.trim().length > 0
    readonly property var saved: projectScope ? (client.projectMemories.memories || []) : client.memories
    function refresh() {
        if (projectScope) client.refreshProjectMemories(projectPath.text)
        else client.refreshMemories()
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 18; spacing: 12
        RowLayout {
            SciButton { objectName: "memory-global"; text: "General notes"; onClicked: { page.projectScope = false; page.refresh() } }
            SciButton { objectName: "memory-project"; text: "Project notes"; onClicked: page.projectScope = true }
            Item { Layout.fillWidth: true }
            SciButton { objectName: "refresh-memory"; text: "Refresh"; enabled: page.client.connected && page.validScope; onClicked: page.refresh() }
        }
        SciField {
            id: projectPath
            objectName: "memory-project-path"
            Layout.fillWidth: true; visible: page.projectScope
            placeholderText: "Full path to an existing project directory"
            onTextChanged: page.client.refreshProjectMemories("")
            onAccepted: if (page.client.connected && page.validScope) page.refresh()
        }
        Text {
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#8da6b8"; font.pixelSize: 12
            text: page.projectScope ? "Notes belong to this directory. Loading them here does not change your preferred project." : "General notes are shared across projects."
        }
        RowLayout {
            Layout.fillWidth: true
            SciField {
                id: memoryInput
                objectName: "memory-input"
                Layout.fillWidth: true; maximumLength: 8000
                placeholderText: page.projectScope ? "Remember something for this project…" : "Create an explicit memory…"
                function submit() {
                    if (!page.client.connected || !page.validScope || text.trim().length === 0) return
                    if (page.projectScope) page.client.callTool("memory.project.remember", {project:projectPath.text.trim(), content:text.trim()})
                    else page.client.remember(text)
                    text = ""
                }
                onAccepted: submit()
            }
            SciButton { objectName: "save-memory"; text: "Remember"; enabled: page.client.connected && page.validScope && memoryInput.text.trim().length > 0; onClicked: memoryInput.submit() }
        }
        HudPanel {
            Layout.fillWidth: true; Layout.fillHeight: true; color: "#091522"; lineColor: "#2a4558"; cut: 0
            ListView {
                id: notes
                objectName: "memory-list"
                anchors.fill: parent; anchors.margins: 16; clip: true; spacing: 9; model: page.saved
                ScrollBar.vertical: ListScrollBar {}
                delegate: HudPanel {
                    id: note
                    required property var modelData
                    property bool expanded: false
                    width: notes.width; height: noteContent.implicitHeight + 24
                    color: "#0b1c2b"; lineColor: "#30526a"; cut: 0
                    RowLayout {
                        id: noteContent
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 6
                            Text {
                                Layout.fillWidth: true; text: note.modelData.content; textFormat: Text.PlainText
                                color: "#d9edf2"; font.pixelSize: 13; wrapMode: Text.Wrap
                                maximumLineCount: note.expanded ? 1000 : 4; elide: Text.ElideRight
                            }
                            RowLayout {
                                Text { text: (page.projectScope ? "PROJECT" : "GENERAL") + " / " + note.modelData.id.slice(0,8); color: "#8da6b8"; font.pixelSize: 10 }
                                SciButton { visible: note.modelData.content.length > 200; text: note.expanded ? "Less" : "Read more"; onClicked: note.expanded = !note.expanded }
                            }
                        }
                        SciButton {
                            text: "Forget"; accent: "#ff6478"; enabled: page.client.connected
                            onClicked: {
                                if (page.projectScope) page.client.callTool("memory.project.forget", {project:projectPath.text.trim(), id:note.modelData.id})
                                else page.client.forget(note.modelData.id)
                            }
                        }
                    }
                }
                Text {
                    objectName: "memory-empty"
                    anchors.centerIn: parent; width: Math.min(440,parent.width - 32)
                    visible: notes.count === 0; color: "#8da6b8"; font.pixelSize: 13; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                    text: !page.client.connected ? "Connect to Carlos to load notes." : !page.validScope ? "Choose a project directory above." : page.projectScope && page.client.projectMemories.loading ? "Loading project notes…" : page.projectScope && page.client.projectMemories.error ? page.client.projectMemories.error : "No saved notes in this context."
                }
            }
        }
        Text {
            Layout.fillWidth: true; visible: page.projectScope; color: "#8da6b8"; font.pixelSize: 12; wrapMode: Text.WordWrap
            text: page.client.projectMemories.has_more ? "Showing the latest notes within the response limit. Search older notes through memory.project.search." : page.client.projectMemories.storage === "RAM_ONLY" ? "Private session: these project notes stay in RAM." : page.client.projectMemories.project ? "Project: " + page.client.projectMemories.project : "Press Refresh to load notes for the selected directory."
        }
    }
}
