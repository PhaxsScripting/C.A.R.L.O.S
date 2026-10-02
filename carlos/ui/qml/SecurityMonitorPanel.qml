import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    property var monitor: ({})
    readonly property var sources: monitor.sources || ({})
    readonly property var labels: ({listeners: "Listeners", tailscale: "Tailscale devices",
        failed_logins: "Failed logins", services: "Failed services", firewall: "Firewall evidence",
        startup: "Startup files", smart: "Disk health", updates: "Security advisories"})
    spacing: 7

    RowLayout {
        Layout.fillWidth: true
        Text { text: "BACKGROUND MONITOR"; color: "#6ee7ff"; font.pixelSize: 10; font.bold: true }
        Item { Layout.fillWidth: true }
        Text { text: root.monitor.state || "NOT CONNECTED"; color: "#91adbf"; font.pixelSize: 9 }
    }
    GridLayout {
        Layout.fillWidth: true
        columns: 2
        columnSpacing: 12
        rowSpacing: 7
        Repeater {
            model: ["listeners", "tailscale", "failed_logins", "services", "firewall", "startup", "smart", "updates"]
            ColumnLayout {
                required property string modelData
                readonly property var reading: root.sources[modelData] || ({})
                Layout.fillWidth: true
                spacing: 2
                Text { Layout.fillWidth: true; text: root.labels[modelData]; color: "#daf3ff"; font.pixelSize: 9; elide: Text.ElideRight }
                Text {
                    Layout.fillWidth: true
                    text: (reading.status === "OK" ? "READABLE" : reading.status || "NOT READ")
                        + (reading.count !== undefined ? " · " + reading.count : "")
                    color: reading.status === "OK" ? "#91adbf" : "#ffca58"
                    font.pixelSize: 8
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
    Text {
        Layout.fillWidth: true
        text: root.monitor.observed_at
            ? "Last read " + Qt.formatTime(new Date(root.monitor.observed_at * 1000), "hh:mm:ss") + " · polls every " + root.monitor.poll_interval_seconds + " s"
            : root.monitor.state === "PAUSED" ? "Paused for this privacy mode." : "Waiting for a local observation."
        color: "#91adbf"; font.pixelSize: 8; wrapMode: Text.WordWrap
    }
    Text {
        Layout.fillWidth: true
        text: "Readable means evidence is available, not that the system is safe. Missing disk/login data stays unknown. Firewall service activity alone does not confirm filtering."
        color: "#91adbf"; font.pixelSize: 8; wrapMode: Text.WordWrap
    }
}
