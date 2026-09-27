import QtQuick
import QtQuick.Controls

Window {
    id: petWindow
    width: 242
    height: 202
    property bool surfaceReady: false
    visible: surfaceReady && pet.shown
    color: "transparent"
    title: "Carlos Pet"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowStaysOnTopHint | Qt.Tool

    PetSprite {
        id: sprite
        anchors.fill: parent
        bubble: pet.bubble
        still: pet.still || !petWindow.visible
        mood: pet.mood
        dragMoved: pet.dragMoved
        onPatted: pet.pet()
        onMenuRequested: pet.menu()
        onDragStarted: pet.beginDrag()
        onDragged: pet.dragPortable()
        onDragFinished: pet.endDrag()
    }
}
