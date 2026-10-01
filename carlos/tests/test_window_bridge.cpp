#include <QCoreApplication>
#include <QFile>
#include <QJSEngine>
#include <QtTest>

class WindowBridgeTests : public QObject {
    Q_OBJECT
  private slots:
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
