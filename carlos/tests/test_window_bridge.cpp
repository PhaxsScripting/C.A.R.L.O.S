#include <QCoreApplication>
#include <QFile>
#include <QJSEngine>
#include <QtTest>

class WindowBridgeTests : public QObject {
    Q_OBJECT
  private slots:
    void monitorIdentityAtMutation() {
        QFile source(CARLOS_WINDOW_BRIDGE);
        QVERIFY(source.open(QIODevice::ReadOnly));
        QJSEngine engine;
        QVERIFY(!engine.evaluate(R"JS(
function callDBus() {}
var moves=0;
var panel={name:'HDMI-A-1',manufacturer:'Fixture',model:'Panel',serialNumber:'A',geometry:{x:0,y:0,width:800,height:600}};
var target={internalId:'owned',pid:123,desktops:[],frameGeometry:{x:0,y:0,width:800,height:600},
 clientGeometry:{x:0,y:0,width:800,height:600},normalWindow:true,maximizeMode:0};
var workspace={screens:[panel],stackingOrder:[target],sendClientToScreen:function(window,output){moves++;window.output=output;}};
var expected={manufacturer:'Fixture',model:'Panel',serial_number:'A'};
var command={action:'move_to_output',arguments:{window_id:'owned',output:'HDMI-A-1',expected_output_identity:expected}};
)JS").isError());
        QVERIFY(!engine.evaluate(QString::fromUtf8(source.readAll())).isError());
        auto check = [&engine](const QString &code) {
            const auto result = engine.evaluate(code);
            QVERIFY2(!result.isError(), qPrintable(result.toString()));
        };
        check("execute(command);if(moves!==1||target.output!==panel)throw Error('matching panel not moved');");
        check("panel.serialNumber='B';var rejected=false;try{execute(command);}catch(error){rejected=true;}if(!rejected||moves!==1)throw Error('replacement accepted');");
        check("panel.serialNumber='A';workspace.screens.push({name:'DP-3',manufacturer:'Fixture',model:'Panel',serialNumber:'A',geometry:panel.geometry});var rejected=false;try{execute(command);}catch(error){rejected=true;}if(!rejected||moves!==1)throw Error('duplicate accepted');");
        check("workspace.screens.pop();panel.name='DP-2';var rejected=false;try{execute(command);}catch(error){rejected=true;}if(!rejected||moves!==1)throw Error('stale connector accepted');command.arguments.output='DP-2';execute(command);if(moves!==2)throw Error('fresh connector rejected');");
        check("delete command.arguments.expected_output_identity;panel.serialNumber='';execute(command);if(moves!==3)throw Error('explicit connector move broken');");
    }
    void closedAnimationCannotBeObservedOrMutated() {
        QFile source(CARLOS_WINDOW_BRIDGE);
        QVERIFY(source.open(QIODevice::ReadOnly));
        QJSEngine engine;
        QVERIFY(!engine.evaluate(R"JS(
function callDBus() {}
var ghost={internalId:'closed',deleted:true};
Object.defineProperty(ghost,'desktops',{get:function(){throw Error('deleted properties read');}});
var live={internalId:'live',pid:123,desktops:[],frameGeometry:{x:0,y:0,width:800,height:600},
 clientGeometry:{x:0,y:0,width:800,height:600},normalWindow:true,maximizeMode:0};
var workspace={stackingOrder:[null,ghost,live],screens:[],desktops:[],activeWindow:ghost,cursorPos:{x:0,y:0}};
)JS").isError());
        QVERIFY(!engine.evaluate(QString::fromUtf8(source.readAll())).isError());
        const auto result = engine.evaluate(R"JS(
var observed=snapshot();
if(observed.windows.length!==1||observed.windows[0].id!=='live'||observed.active_window_id!=='')
 throw Error('closed animation is live');
for(var action of ['activate','close','minimize','maximize','restore','fullscreen','layout','move_resize','move_to_output','move_to_desktop']) {
 var rejected=false;
 try {execute({action:action,arguments:{window_id:'closed'}});}
 catch(error) {rejected=String(error).indexOf('no longer exists')!==-1;}
 if(!rejected) throw Error('closed ID reached '+action);
}
if(findWindow('live')!==live) throw Error('live window lost');
)JS");
        QVERIFY2(!result.isError(), qPrintable(result.toString()));
    }
    void nativeStateAndRestore() {
        QFile source(CARLOS_WINDOW_BRIDGE);
        QVERIFY(source.open(QIODevice::ReadOnly));
        QJSEngine engine;
        QVERIFY(!engine.evaluate(R"JS(
function callDBus() {}
var clock=1000; Date.now=function(){return clock;};
var changes=[], focusChanges=0;
var target={internalId:'owned',pid:123,desktops:[],frameGeometry:{x:0,y:0,width:800,height:600},
 clientGeometry:{x:0,y:0,width:800,height:600},normalWindow:true,maximizable:true,
 minimized:true,fullScreen:true,tile:{},maximizeMode:0,
 setMaximize:function(vertical,horizontal){changes.push([vertical,horizontal]);this.maximizeMode=(vertical?1:0)+(horizontal?2:0);}};
var other={internalId:'other'};
var workspace={activeWindow:other,stackingOrder:[target,other],windowList:function(){return [target,other];},
 clientArea:function(){return {x:0,y:0,width:800,height:600};},
 raiseWindow:function(){focusChanges++;},slotWindowMaximize:function(){focusChanges++;}};
var KWin={MaximizeArea:1};
)JS").isError());
        QVERIFY(!engine.evaluate(QString::fromUtf8(source.readAll())).isError());
        auto check = [&engine](const QString &code) {
            const auto result = engine.evaluate(code);
            QVERIFY2(!result.isError(), qPrintable(result.toString()));
        };
        check("if(isMaximized(target))throw Error('area-sized normal window');");
        check("target.maximizeMode=3;target.frameGeometry.width=780;if(!isMaximized(target))throw Error('native full state');");
        check("target.maximizeMode=1;if(isMaximized(target)||serializeWindow(target).maximize_mode!==1)throw Error('partial state');");
        check("execute({action:'restore',arguments:{window_id:'owned'}});if(target.maximizeMode!==0||target.minimized||target.fullScreen||target.tile!==null)throw Error('restore');if(workspace.activeWindow!==other||focusChanges!==0)throw Error('focus changed');");
        check("execute({action:'restore',arguments:{window_id:'owned'}});if(changes.length!==2||changes.some(function(v){return v[0]||v[1];}))throw Error('non-idempotent restore');");
        check("delete target.maximizeMode;target.frameGeometry.width=800;if(!isMaximized(target)||maximizeMode(target)!==null)throw Error('legacy area fallback');target.frameGeometry.width=600;if(isMaximized(target))throw Error('legacy normal');");
        check("var before=changes.length;try{execute({action:'restore',deadline_unix_ms:999,arguments:{window_id:'owned'}});throw Error('expiry bypass');}catch(error){if(changes.length!==before)throw Error('expired mutation');}");
    }
};
QTEST_GUILESS_MAIN(WindowBridgeTests)
#include "test_window_bridge.moc"
