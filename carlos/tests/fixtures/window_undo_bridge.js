function check(condition, reason) {
    if (!condition) throw new Error(reason);
}
const first = {id: "work"};
const second = {id: "games"};
const target = {
    internalId: "owned", desktops: [first], minimized: true, fullScreen: true,
    frameGeometry: {x: 20, y: 20, width: 800, height: 600}, clientGeometry: {x: 20, y: 20, width: 800, height: 600},
    setMaximize: function(vertical, horizontal) { this.maximizeMode = (vertical ? 1 : 0) | (horizontal ? 2 : 0); },
    maximizeMode: 0
};
const workspace = {desktops: [first, second], stackingOrder: [target]};
for (const mode of [1, 2, 3]) {
    execute({action: "maximize", arguments: {window_id: "owned", mode: mode}});
    check(target.maximizeMode === mode, "Partial maximize axes were lost");
}
execute({action: "maximize", arguments: {window_id: "owned"}});
check(target.maximizeMode === 3, "Existing maximize command changed");
for (const mode of [0, true, "1", 4, -1]) {
    target.minimized = true;
    target.fullScreen = true;
    let rejected = false;
    try { execute({action: "maximize", arguments: {window_id: "owned", mode: mode}}); }
    catch (error) { rejected = true; }
    check(rejected && target.minimized && target.fullScreen && target.maximizeMode === 3,
          "Invalid mode mutated a window");
}
execute({action: "move_to_desktop", arguments: {window_id: "owned", desktop_ids: ["work", "games"]}});
check(target.desktops.length === 2 && target.desktops[0] === first && target.desktops[1] === second,
      "Multiple workspace assignment was lost");
execute({action: "move_to_desktop", arguments: {window_id: "owned", all_desktops: true, desktop_ids: []}});
check(target.desktops.length === 0, "All-workspace assignment was lost");
execute({action: "move_to_desktop", arguments: {window_id: "owned", desktop_id: "work"}});
check(target.desktops.length === 1 && target.desktops[0] === first, "Existing workspace command changed");
for (const ids of [[], ["missing"], ["work", "missing"], ["work", "work"], "work"]) {
    let rejected = false;
    try { execute({action: "move_to_desktop", arguments: {window_id: "owned", desktop_ids: ids}}); }
    catch (error) { rejected = true; }
    check(rejected && target.desktops.length === 1 && target.desktops[0] === first,
          "Invalid assignment partially moved a window");
}
