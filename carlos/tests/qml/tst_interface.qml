import QtQuick
import QtQuick.Controls
import QtTest
import "../../ui/qml" as EV

TestCase {
    id: testCase
    name: "NexusInterface"
    when: windowShown
    width: 1080; height: 680
    property bool backgroundMode: false
    property bool brainExploreMode: false
    property var evClient: mock
    property var ui
    QtObject {
        id: mock
        signal sceneActivated(string hud)
        property bool connected: true
        property string state: "DORMANT"
        property string detail: "Isolated UI test; no desktop connection"
        property string statusMessage: "TEST CONNECTION"
        property var voice: ({wake_active:true})
        property var telemetry: ({resource_mode:"NORMAL"})
        property var provider: ({active:"test",model:"fixture"})
        property var cognition: ({})
        property var confirmation: ({})
        property var events: []
        property var timeline: [{kind:"USER",title:"ME",body:"Fixture request"},{kind:"ASSISTANT",title:"E.V.",body:"Fixture response, never executed."}]
        property var memories: []
        property var tools: []
        property var plans: []
        property var activePlan: ({})
        property var activity: ({phase:"IDLE"})
        property var insights: []
        property var security: ({})
        property var latency: ({})
        property var diagnostics: ({})
        property var personality: ({})
        property var daily: ({reminders:[],aliases:{},routines:{},scenes:[],spotify:{}})
        property var toolResult: ({})
        property var inputWaveform: []
        property var outputWaveform: []
        property var activeNodes: []
        property var commands: []
        property var calls: []
        function refreshDaily() {}
        function retryCore() {}
        function rememberFailureCard(id) {}
        function refreshMemories() {}
        function refreshPhase3() {}
        function refreshTools() {}
        function refreshSnapshot() {}
        function sendCommand(text) { commands = commands.concat([text]) }
        function remember(text) { calls = calls.concat([{memory:text}]) }
        function respondToConfirmation(approved) { calls = calls.concat([{approved:approved}]) }
        function steerTask(id,text) { calls = calls.concat([{task_id:id,text:text}]) }
        function callTool(name,args) { calls = calls.concat([{name:name,args:args}]) }
        function stopSpeaking() {}
    }
    Component { id: appComponent; EV.Main { width:1080; height:680 } }
    function init() {
        mock.commands=[]; mock.calls=[]
        mock.connected=true; mock.confirmation={}; mock.state="DORMANT"
        mock.timeline=[{kind:"USER",title:"ME",body:"Fixture request"}]
        mock.voice={wake_active:true}
        mock.daily={reminders:[],aliases:{},routines:{},scenes:[{id:"test-scene",name:"Fixture scene",live:false,preview:Qt.resolvedUrl("../../ui/assets/ev-neural-brain.png"),accent:"#70e6ff",background:"#061320",secondary:"#b1baff"}],spotify:{}}
        ui=createTemporaryObject(appComponent,null)
        verify(ui !== null)
        findChild(ui, "voice-hud").surfaceReady = true
        ui.reducedMotion=true
        waitForRendering(ui.contentItem)
    }
    function cleanup() { ui.close() }
    function test_insight_never_executes_until_explicit_click() {
        mock.insights = [{id:"thermal", title:"CPU hot", detail:"Inspect only", action:{tool:"system.get_temperature", arguments:{}}}]
        click("nav-10")
        const repeater = item("system-insights")
        tryVerify(function() { return repeater.itemAt(0) !== null })
        const inspectButton = findChild(repeater.itemAt(0), "insight-inspect-thermal")
        const dismissButton = findChild(repeater.itemAt(0), "insight-dismiss-thermal")
        verify(inspectButton !== null)
        verify(dismissButton !== null)
        compare(mock.calls.length, 0)
        inspectButton.clicked()
        compare(mock.calls.length, 1)
        compare(mock.calls[0].name, "system.get_temperature")
        dismissButton.clicked()
        compare(mock.calls[1].name, "agent.insights.dismiss")
        compare(mock.calls[1].args.id, "thermal")
        mock.insights = []
    }
    function test_voice_overlay_never_requests_keyboard_focus_or_reserves_panel_space() {
        const hud = findChild(ui, "voice-hud")
        verify(hud !== null)
        verify(hud.passiveSurface)
        compare(hud.transientParent, null)
        verify((hud.flags & Qt.WindowDoesNotAcceptFocus) !== 0)
        for (const state of ["LISTENING", "USING_TOOL", "SPEAKING", "DORMANT"]) {
            mock.state = state
            tryCompare(hud, "visible", state !== "DORMANT")
            verify(hud.passiveSurface)
        }
    }
    function test_voice_status_explains_idle_listening_and_privacy() {
        mock.voice = {wake_active:true,diagnostics:{}}
        compare(ui.voiceStatus(), "LISTENING FOR Carlos")
        mock.voice = {wake_active:false,privacy_mode:true}
        compare(ui.voiceStatus(), "MICROPHONE OFF / PRIVACY")
        mock.voice = {wake_active:true,diagnostics:{wake_speech_backup:{state:"CHECKING"}}}
        compare(ui.voiceStatus(), "CHECKING YOUR NAME / LOCAL")
        mock.voice = {wake_active:true,diagnostics:{wake_input_quality:"LOUD_NON_SPEECH"}}
        compare(ui.voiceStatus(), "CHECK MIC / LOUD INPUT WITHOUT CLEAR SPEECH")
        mock.voice = {wake_active:true,diagnostics:{}}
    }
    function test_orb_shows_real_wait_state_and_expands_input() {
        mock.activity = {phase:"WAITING",task_id:"exact-task",wait_reason:"Observing declared conditions"}
        const orb = item("agent-orb")
        tryCompare(orb, "phase", "WAITING")
        compare(ui.commandInputExpanded,false)
        orb.activated()
        tryVerify(function() { return item("orb-command-input").visible })
        item("orb-command-input").text = "use another folder"
        ui.reviseCurrentTask = true
        item("orb-command-input").submit()
        compare(mock.calls.length,1)
        compare(mock.calls[0].task_id,"exact-task")
        compare(mock.commands.length,0)
        mock.activity = {phase:"IDLE"}
    }
    function test_stop_all_sends_only_the_explicit_stop_request() {
        click("stop-all-actions")
        compare(mock.commands.length,1)
        compare(mock.commands[0],"stop everything")
        compare(mock.calls.length,0)
    }
    function test_listener_health_is_readable_without_executing_actions() {
        click("nav-9")
        mock.voice={wake_active:true,microphone_active:true,diagnostics:{wake_worker_health:{backlog_ms:250},wake_speech_backup:{state:"LISTENING"}}}
        const label=item("wake-worker-health")
        tryVerify(function() { return label.text.indexOf("MIC STREAM / LIVE") >= 0 && label.text.indexOf("250 ms") >= 0 })
        mock.voice={wake_active:false,microphone_active:false,diagnostics:{}}
        tryVerify(function() { return label.text.indexOf("NO AUDIO") >= 0 })
        compare(mock.commands.length,0)
        compare(mock.calls.length,0)
    }
    function item(name) {
        let found
        if(name.indexOf("nav-") === 0) {
            const nav=findChild(ui,"navigation")
            const index=Number(name.slice(4))
            tryVerify(function() { return nav.itemAtIndex(index) !== null })
            found=nav.itemAtIndex(index)
        } else found=findChild(ui,name)
        verify(found !== null,"Missing " + name)
        return found
    }
    function click(name) {
        const target = item(name)
        tryVerify(function() { return target.visible && target.width > 0 && target.height > 0 })
        waitForRendering(target)
        mouseClick(target, target.width / 2, target.height / 2)
    }
    function test_all_navigation_pages_fit_and_do_not_execute() {
        const stack=item("page-stack")
        for(let i=0;i<11;++i) {
            click("nav-"+i)
            compare(stack.currentIndex,i)
            verify(stack.width>600 && stack.height>450)
        }
        compare(mock.commands.length,0); compare(mock.calls.length,0)
    }
    function test_brain_entry_zoom_and_return() {
        click("nav-1")
        const brain=item("brain-view")
        compare(brain.stage,0)
        click("enter-brain-network")
        compare(brain.stage,1)
        const graph=item("brain-explorer")
        click("brain-zoom-in")
        verify(graph.zoomLevel>1)
        click("brain-back")
        compare(brain.stage,0)
        compare(mock.commands.length,0)
    }
    function test_wallpaper_picker_opens_closes_without_applying() {
        click("nav-10")
        click("open-scene-picker")
        tryCompare(item("scene-picker"),"opened",true)
        click("close-scene-picker")
        tryCompare(item("scene-picker"),"opened",false)
        compare(mock.calls.length,0)
    }
    function test_personal_library_save_is_explicit_and_keeps_title() {
        click("nav-10")
        item("personal-library-kind").currentIndex=1
        item("personal-library-title").text="Fixture task, not a command"
        const save=item("personal-library-save")
        verify(save.enabled)
        save.clicked()
        compare(mock.calls.length,1)
        compare(mock.calls[0].name,"tasks.create")
        compare(mock.calls[0].args.title,"Fixture task, not a command")
        compare(mock.commands.length,0)
    }
    function test_mode_setting_sends_typed_choice_and_controls_performance_readout() {
        mock.daily = {settings: {choices: [{key:"operating_mode", section:"General", label:"Mode", value:"DAILY", choices:[{value:"DAILY",label:"Daily"},{value:"DEV",label:"Development"}]}]}}
        click("nav-9")
        const choices = item("settings-choices")
        tryVerify(function() { return choices.itemAt(0) !== null })
        const mode = findChild(choices.itemAt(0), "setting-operating_mode")
        verify(mode !== null)
        compare(mode.currentValue, "DAILY")
        compare(item("dev-performance").visible, false)
        mode.currentIndex = 1
        mode.activated(1)
        compare(mock.calls[mock.calls.length-1].name, "carlos.settings.set")
        compare(mock.calls[mock.calls.length-1].args.value, "DEV")
        mock.daily = {settings: {choices: [{key:"operating_mode", section:"General", label:"Mode", value:"DEV", choices:[{value:"DAILY",label:"Daily"},{value:"DEV",label:"Development"}]}]}}
        tryCompare(item("dev-performance"), "visible", true)
    }
    function test_native_settings_button_requests_only_selected_page() {
        click("nav-10")
        const pages=item("native-settings-pages")
        tryVerify(function() { return pages.itemAt(7) !== null })
        compare(pages.itemAt(7).modelData,"touchpad")
        pages.itemAt(7).clicked()
        compare(mock.calls.length,1)
        compare(mock.calls[0].name,"settings.open")
        compare(mock.calls[0].args.page,"touchpad")
        compare(mock.commands.length,0)
    }
    function test_command_button_keeps_exact_payload_and_clears_field() {
        click("nav-2")
        item("command-input").text="test fixture only"
        click("send-command")
        compare(mock.commands,["test fixture only"])
        compare(item("command-input").text,"")
    }
    function test_brand_fits_small_window_and_wide_content_is_bounded() {
        const brand = item("brand-name")
        verify(brand.contentWidth <= brand.width)
        brand.font.pixelSize = 72
        waitForRendering(ui.contentItem)
        verify(brand.contentWidth <= brand.width)
        ui.width = 3840
        waitForRendering(ui.contentItem)
        verify(item("page-stack").width < 1760)
    }
    function test_empty_conversation_and_disconnected_draft() {
        mock.timeline = []
        click("nav-2")
        tryCompare(item("conversation-empty"), "visible", true)
        const timeline = item("conversation-empty").parent
        tryCompare(timeline.ScrollBar.vertical.contentItem, "visible", false)
        const input = item("command-input")
        const send = item("send-command")
        input.text = "   "
        verify(!send.enabled)
        input.text = "keep this draft"
        mock.connected = false
        input.accepted()
        verify(!send.enabled)
        compare(input.text, "keep this draft")
        compare(mock.commands.length, 0)
        mock.connected = true
        send.clicked()
        input.accepted()
        compare(mock.commands, ["keep this draft"])
    }
    function test_memory_requires_content_and_connection() {
        click("nav-3")
        const input = item("memory-input")
        const save = item("save-memory")
        verify(!save.enabled)
        input.text = "remember the fixture"
        mock.connected = false
        input.accepted()
        compare(mock.calls.length, 0)
        mock.connected = true
        save.clicked()
        save.clicked()
        compare(mock.calls.length, 1)
        compare(mock.calls[0].memory, "remember the fixture")
    }
    function test_confirmation_uses_keyboard_and_submits_once() {
        mock.confirmation = {id:"test-request",tool:"fixture.tool",reason:"Long reason ".repeat(200)}
        const prompt = item("confirmation-prompt")
        tryCompare(prompt, "opened", true)
        const deny = item("confirmation-deny")
        tryCompare(deny, "activeFocus", true)
        keyClick(Qt.Key_Escape)
        tryCompare(mock, "calls", [{approved:false}])
        prompt.respond(true)
        compare(mock.calls.length, 1)
        mock.confirmation = {}
        tryCompare(prompt, "opened", false)
    }
    function test_scene_card_sends_only_selected_fixture_id() {
        click("nav-10")
        click("open-scene-picker")
        tryCompare(item("scene-picker"),"opened",true)
        const list=item("scene-list")
        tryVerify(function() { return list.itemAtIndex(0) !== null })
        const card=list.itemAtIndex(0)
        mouseClick(card,card.width/2,card.height/2)
        compare(mock.calls.length,1)
        compare(mock.calls[0].name,"scenes.apply")
        compare(mock.calls[0].args.name,"test-scene")
    }
    function test_reduced_motion_switch_stops_ambient_animation() {
        click("motion-toggle")
        compare(mock.calls[0].name,"carlos.settings.set")
        compare(mock.calls[0].args.key,"hud_reduce_motion")
        compare(mock.calls[0].args.value,false)
        ui.reducedMotion = Qt.binding(function() { return ui.settingValue("hud_reduce_motion", false) })
        mock.daily = {settings:{fields:[{key:"hud_reduce_motion",value:false}]}}
        tryCompare(ui,"reducedMotion",false)
        click("motion-toggle")
        compare(mock.calls[1].args.value,true)
        mock.daily = {settings:{fields:[{key:"hud_reduce_motion",value:true}]}}
        tryCompare(ui,"reducedMotion",true)
        compare(ui.animationsRunning,false)
    }
    function test_readiness_timings_distinguish_unknown_from_zero() {
        ui.testPage = 9
        mock.daily = {readiness:{timing:{startup_to_all_ready_seconds:null,last_resume:null}}}
        tryCompare(item("startup-readiness-time"), "text", "Startup readiness: waiting for all components")
        compare(item("resume-readiness-time").text, "Resume readiness: no resume observed")
        mock.daily = {readiness:{timing:{startup_to_all_ready_seconds:0,last_resume:{resume_to_all_ready_range_seconds:null}}}}
        tryCompare(item("startup-readiness-time"), "text", "Startup readiness: 0.0 s")
        compare(item("resume-readiness-time").text, "Resume readiness: waiting for fresh checks")
        mock.daily = {readiness:{timing:{startup_to_all_ready_seconds:3.2,last_resume:{resume_to_all_ready_range_seconds:[2,7.5]}}}}
        tryCompare(item("resume-readiness-time"), "text", "Resume readiness: 2.0 to 7.5 s (observed range)")
    }
    function test_battery_power_source_is_separate_from_charging() {
        ui.testPage = 4
        mock.telemetry = {battery:null}
        tryCompare(item("battery-status-card"), "detail", "UNAVAILABLE")
        mock.telemetry = {battery:{percent:0,plugged:true,status:"Not charging"}}
        tryCompare(item("battery-status-card"), "detail", "AC POWER / NOT CHARGING")
        mock.telemetry = {battery:{percent:30,plugged:true,status:"Charging"}}
        tryCompare(item("battery-status-card"), "detail", "AC POWER / CHARGING")
    }
}
