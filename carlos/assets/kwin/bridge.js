/* E.V. runtime KWin bridge. Loaded only while the local E.V. core is running. */

const EV_SERVICE = "com.ev.Core";
const EV_PATH = "/com/ev/KWinBridge";
const EV_INTERFACE = "com.ev.KWinBridge";

function rectangle(value) {
    return {
        x: Math.round(value.x),
        y: Math.round(value.y),
        width: Math.round(value.width),
        height: Math.round(value.height)
    };
}

function maximizeMode(window) {
    const mode = window.maximizeMode;
    return typeof mode === "number" && [0, 1, 2, 3].indexOf(mode) !== -1 ? mode : null;
}

function isMaximized(window) {
    const mode = maximizeMode(window);
    if (mode !== null) return mode === 3;
    try {
        const area = workspace.clientArea(KWin.MaximizeArea, window);
        const geometry = window.frameGeometry;
        return Math.abs(geometry.x - area.x) <= 1 &&
            Math.abs(geometry.y - area.y) <= 1 &&
            Math.abs(geometry.width - area.width) <= 1 &&
            Math.abs(geometry.height - area.height) <= 1;
    } catch (error) {
        return false;
    }
}

function serializeOutput(output) {
    if (!output) return null;
    return {
        name: String(output.name || ""),
        manufacturer: String(output.manufacturer || ""),
        model: String(output.model || ""),
        serial_number: String(output.serialNumber || ""),
        scale: Number(output.devicePixelRatio || 1),
        geometry: rectangle(output.geometry)
    };
}

function serializeWindow(window) {
    const desktops = [];
    for (let i = 0; i < window.desktops.length; ++i) {
        desktops.push(String(window.desktops[i].id));
    }
    return {
        id: String(window.internalId),
        pid: Number(window.pid || 0),
        title: String(window.caption || ""),
        app_id: String(window.desktopFileName || ""),
        resource_class: String(window.resourceClass || ""),
        resource_name: String(window.resourceName || ""),
        role: String(window.windowRole || ""),
        geometry: rectangle(window.frameGeometry),
        client_geometry: rectangle(window.clientGeometry),
        output: window.output ? String(window.output.name || "") : "",
        desktops: desktops,
        active: Boolean(window.active),
        deleted: Boolean(window.deleted),
        minimized: Boolean(window.minimized),
        fullscreen: Boolean(window.fullScreen),
        maximized: isMaximized(window),
        maximize_mode: maximizeMode(window),
        tiled: Boolean(window.tile),
        normal: Boolean(window.normalWindow),
        dialog: Boolean(window.dialog),
        special: Boolean(window.specialWindow),
        closeable: Boolean(window.closeable),
        moveable: Boolean(window.moveable),
        resizeable: Boolean(window.resizeable),
        maximizable: Boolean(window.maximizable),
        on_all_desktops: Boolean(window.onAllDesktops),
        unresponsive: Boolean(window.unresponsive),
        stacking_order: Number(window.stackingOrder || 0)
    };
}

function findWindow(id) {
    const windows = workspace.stackingOrder;
    for (let i = 0; i < windows.length; ++i) {
        if (windows[i] && !windows[i].deleted && String(windows[i].internalId) === String(id))
            return windows[i];
    }
    throw new Error("Window no longer exists: " + id);
}

function findOutput(name) {
    const outputs = workspace.screens;
    for (let i = 0; i < outputs.length; ++i) {
        if (String(outputs[i].name) === String(name)) return outputs[i];
    }
    throw new Error("Output is unavailable: " + name);
}

function findDesktop(id) {
    const desktops = workspace.desktops;
    for (let i = 0; i < desktops.length; ++i) {
        if (String(desktops[i].id) === String(id)) return desktops[i];
    }
    throw new Error("Virtual desktop is unavailable: " + id);
}

function snapshot() {
    const windows = [];
    const outputs = [];
    const desktops = [];
    const stack = workspace.stackingOrder;
    for (let i = 0; i < stack.length; ++i) {
        if (stack[i] && !stack[i].deleted) windows.push(serializeWindow(stack[i]));
    }
    for (let j = 0; j < workspace.screens.length; ++j) outputs.push(serializeOutput(workspace.screens[j]));
    for (let k = 0; k < workspace.desktops.length; ++k) {
        desktops.push({id: String(workspace.desktops[k].id), name: String(workspace.desktops[k].name || "")});
    }
    return {
        windows: windows,
        outputs: outputs,
        desktops: desktops,
        active_window_id: workspace.activeWindow && !workspace.activeWindow.deleted ? String(workspace.activeWindow.internalId) : "",
        active_output: workspace.activeScreen ? String(workspace.activeScreen.name || "") : "",
        current_desktop: workspace.currentDesktop ? String(workspace.currentDesktop.id) : "",
        cursor: {x: Math.round(workspace.cursorPos.x), y: Math.round(workspace.cursorPos.y)}
    };
}

function placeBeside(args) {
    const ids = {anchor: args.anchor_id, window: args.window_id};
    if (!ids.anchor || !ids.window || ids.anchor === ids.window)
        throw new Error("Choose two different windows; no window moved");
    const desktop = workspace.currentDesktop ? String(workspace.currentDesktop.id) : "";
    if (!desktop || desktop !== args.expected_desktop)
        throw new Error("Workspace changed before execution; no window moved");
    const windows = {anchor: findWindow(ids.anchor), window: findWindow(ids.window)};
    for (const role of ["anchor", "window"]) {
        const window = windows[role];
        const observed = serializeWindow(window);
        const expected = (args.expected_windows || {})[role];
        if (!expected || !observed.normal || observed.special || observed.deleted ||
            observed.tiled || observed.unresponsive || !observed.moveable || !observed.resizeable ||
            (!observed.on_all_desktops && observed.desktops.indexOf(desktop) === -1))
            throw new Error("Window cannot be arranged on this workspace; no window moved");
        for (const key of Object.keys(expected)) {
            if (JSON.stringify(observed[key]) !== JSON.stringify(expected[key]))
                throw new Error("Window changed before execution; no window moved");
        }
    }
    const output = windows.anchor.output;
    if (!output || windows.window.output !== output || workspace.screens.indexOf(output) === -1)
        throw new Error("Both windows must remain on the same monitor; no window moved");
    const observedOutput = serializeOutput(output);
    const expectedOutput = args.expected_output;
    if (!expectedOutput) throw new Error("Missing monitor identity; no window moved");
    for (const key of Object.keys(expectedOutput)) {
        if (JSON.stringify(observedOutput[key]) !== JSON.stringify(expectedOutput[key]))
            throw new Error("Monitor changed before execution; no window moved");
    }
    const area = rectangle(workspace.clientArea(KWin.MaximizeArea, windows.anchor));
    const otherArea = rectangle(workspace.clientArea(KWin.MaximizeArea, windows.window));
    if (JSON.stringify(area) !== JSON.stringify(otherArea) ||
        !Object.values(area).every(Number.isFinite) || area.width < 2 || area.height < 1)
        throw new Error("Shared work area is unavailable; no window moved");
    const left = Math.floor(area.width / 2);
    const geometries = {
        anchor: {x: area.x, y: area.y, width: left, height: area.height},
        window: {x: area.x + left, y: area.y, width: area.width - left, height: area.height}
    };
    for (const role of ["anchor", "window"]) {
        const minimum = windows[role].minSize;
        if (minimum && (minimum.width > geometries[role].width || minimum.height > area.height))
            throw new Error("A window needs more space than half the monitor; no window moved");
    }
    for (const role of ["anchor", "window"]) {
        const window = windows[role];
        window.tile = null;
        window.minimized = false;
        window.fullScreen = false;
        window.setMaximize(false, false, geometries[role]);
        window.frameGeometry = geometries[role];
    }
    return {target_geometries: geometries};
}

function execute(command) {
    if (command.deadline_unix_ms && Date.now() > Number(command.deadline_unix_ms))
        throw new Error("E.V. request expired before execution");
    const action = String(command.action || "");
    const args = command.arguments || {};
    if (action === "ping") return {pong: true};
    if (action === "snapshot") return snapshot();
    if (action === "beside") return placeBeside(args);
    if (action === "workspace_switch") {
        workspace.currentDesktop = findDesktop(String(args.desktop_id));
        return {desktop_id: String(workspace.currentDesktop.id)};
    }
    const window = findWindow(args.window_id);
    let details = {};
    if (action === "activate") {
        window.minimized = false;
        workspace.activeWindow = window;
        workspace.raiseWindow(window);
    } else if (action === "move_resize") {
        const targetGeometry = {
            x: Number(args.x), y: Number(args.y),
            width: Number(args.width), height: Number(args.height)
        };
        window.tile = null;
        // KWin's native method accepts an explicit restore rectangle.  Supplying
        // it is important for windows that were launched maximized and therefore
        // have no useful historic restore geometry.
        window.setMaximize(false, false, targetGeometry);
        window.frameGeometry = targetGeometry;
    } else if (action === "minimize") {
        window.minimized = true;
    } else if (action === "maximize") {
        const mode = args.mode === undefined ? 3 : args.mode;
        if (typeof mode !== "number" || [1, 2, 3].indexOf(mode) === -1)
            throw new Error("Invalid maximize mode; no window changed");
        window.minimized = false;
        window.fullScreen = false;
        window.setMaximize(Boolean(mode & 1), Boolean(mode & 2));
    } else if (action === "restore") {
        window.minimized = false;
        window.fullScreen = false;
        window.tile = null;
        window.setMaximize(false, false);
    } else if (action === "fullscreen") {
        window.minimized = false;
        window.fullScreen = Boolean(args.enabled);
    } else if (action === "close") {
        if (!window.closeable) throw new Error("Window is not closeable");
        window.closeWindow();
    } else if (action === "move_to_output") {
        const output = findOutput(args.output);
        const expected = args.expected_output_identity;
        if (expected) {
            const matches = [];
            for (let i = 0; i < workspace.screens.length; ++i) {
                const candidate = workspace.screens[i];
                const metadata = serializeOutput(candidate);
                if (metadata.manufacturer === expected.manufacturer &&
                    metadata.model === expected.model &&
                    metadata.serial_number === expected.serial_number) matches.push(candidate);
            }
            if (!expected.serial_number || matches.length !== 1 || matches[0] !== output)
                throw new Error("Monitor identity changed before execution; no window moved");
        }
        // This native primitive preserves KWin's own maximize/tile semantics and
        // is more reliable than synthesizing cross-output coordinates.
        workspace.sendClientToScreen(window, output);
    } else if (action === "move_to_desktop") {
        if (args.all_desktops === true) {
            window.desktops = [];
        } else if (args.desktop_ids !== undefined) {
            if (!Array.isArray(args.desktop_ids) || args.desktop_ids.length < 1 ||
                args.desktop_ids.length > workspace.desktops.length)
                throw new Error("Invalid saved workspace assignment; no window changed");
            const ids = args.desktop_ids.map(String);
            if (ids.some(function(id, index) { return ids.indexOf(id) !== index; }))
                throw new Error("Duplicate workspace assignment; no window changed");
            window.desktops = ids.map(findDesktop);
        } else {
            window.desktops = [findDesktop(args.desktop_id)];
        }
    } else if (action === "layout") {
        const layout = String(args.layout || "");
        const area = workspace.clientArea(KWin.MaximizeArea, window);
        const current = window.frameGeometry;
        let targetGeometry = null;
        if (layout === "center") {
            const width = Math.min(Math.round(current.width), Math.round(area.width));
            const height = Math.min(Math.round(current.height), Math.round(area.height));
            targetGeometry = {
                x: Math.round(area.x + (area.width - width) / 2),
                y: Math.round(area.y + (area.height - height) / 2),
                width: width,
                height: height
            };
        } else if (layout === "left" || layout === "right") {
            const width = Math.floor(area.width / 2);
            targetGeometry = {
                x: layout === "left" ? Math.round(area.x) : Math.round(area.x + area.width - width),
                y: Math.round(area.y), width: width, height: Math.round(area.height)
            };
        } else if (layout === "top" || layout === "bottom") {
            const height = Math.floor(area.height / 2);
            targetGeometry = {
                x: Math.round(area.x),
                y: layout === "top" ? Math.round(area.y) : Math.round(area.y + area.height - height),
                width: Math.round(area.width), height: height
            };
        } else if (["top-left", "top-right", "bottom-left", "bottom-right"].indexOf(layout) !== -1) {
            const width = Math.floor(area.width / 2);
            const height = Math.floor(area.height / 2);
            targetGeometry = {
                x: layout.endsWith("left") ? Math.round(area.x) : Math.round(area.x + area.width - width),
                y: layout.startsWith("top") ? Math.round(area.y) : Math.round(area.y + area.height - height),
                width: width, height: height
            };
        } else {
            throw new Error("Unsupported window layout: " + layout);
        }
        window.tile = null;
        window.fullScreen = false;
        window.setMaximize(false, false, targetGeometry);
        window.frameGeometry = targetGeometry;
        details = {layout: layout, target_geometry: rectangle(targetGeometry)};
    } else {
        throw new Error("Unsupported KWin action: " + action);
    }
    details.requested = action;
    details.window = serializeWindow(window);
    return details;
}

function poll() {
    callDBus(EV_SERVICE, EV_PATH, EV_INTERFACE, "NextCommand", function(raw) {
        if (!raw) {
            poll();
            return;
        }
        let command = null;
        let reply = null;
        try {
            command = JSON.parse(String(raw));
            reply = {id: String(command.id), ok: true, result: execute(command)};
        } catch (error) {
            reply = {id: command ? String(command.id) : "invalid", ok: false, error: String(error)};
        }
        callDBus(EV_SERVICE, EV_PATH, EV_INTERFACE, "Report", JSON.stringify(reply), function() { poll(); });
    });
}

poll();
