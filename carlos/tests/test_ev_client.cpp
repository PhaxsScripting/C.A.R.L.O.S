#include "EvClient.h"

#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLocalServer>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>
#include <memory>

class EvClientTests : public QObject {
    Q_OBJECT

  private:
    QTemporaryDir runtime;
    QByteArray previousRuntime;
    QLocalServer server;
    std::unique_ptr<EvClient> client;
    QLocalSocket *peer = nullptr;
    QHash<QString, QString> requestIds;

    void send(const QJsonObject &message) {
        peer->write(QJsonDocument(message).toJson(QJsonDocument::Compact) + '\n');
        peer->flush();
    }

    void sendEvent(const QString &type, const QJsonObject &payload = {}) {
        send({{"type", "event"},
              {"payload", QJsonObject{{"type", type}, {"source", "voice"}, {"payload", payload}}}});
    }

  private slots:
    void offlineCommandIsNotReportedAsSent()
    {
        client->disconnectFromCore();
        const auto before = client->timeline().size();
        client->sendCommand("open calculator");
        QCOMPARE(client->timeline().size(), before);
        QVERIFY(client->statusMessage().contains("offline"));
    }

    void stoppedCoreDoesNotClaimItIsReconnecting()
    {
        client->stopCore();
        peer->disconnectFromServer();
        QTRY_VERIFY(!client->connected());
        QVERIFY(client->statusMessage().contains("automatic reconnect is off"));
    }

    void hungConnectionRecoversWithoutReplayingCommands()
    {
        auto *poll = client->findChild<QTimer *>("coreHealthPoll");
        auto *deadline = client->findChild<QTimer *>("coreHealthDeadline");
        QVERIFY(poll); QVERIFY(deadline);
        poll->setInterval(20);
        deadline->setInterval(150);
        client->sendCommand("open calculator");
        QTRY_VERIFY_WITH_TIMEOUT(!client->connected(), 1000);
        QVERIFY(client->statusMessage().contains("not resent"));
        delete peer; peer = nullptr;
        QTRY_VERIFY_WITH_TIMEOUT(server.hasPendingConnections(), 3000);
        peer = server.nextPendingConnection();
        QTRY_VERIFY(client->connected());
        poll->stop();
        QTRY_VERIFY(peer->canReadLine());
        while (peer->canReadLine()) {
            const auto request = QJsonDocument::fromJson(peer->readLine()).object();
            QVERIFY(request.value("type").toString() != "command.submit");
        }
    }

    void healthRepliesKeepLongCommandsConnected()
    {
        auto *poll = client->findChild<QTimer *>("coreHealthPoll");
        auto *deadline = client->findChild<QTimer *>("coreHealthDeadline");
        QVERIFY(poll); QVERIFY(deadline);
        poll->setInterval(20);
        deadline->setInterval(300);
        client->sendCommand("inspect this project");
        int heartbeats = 0;
        QElapsedTimer elapsed; elapsed.start();
        while (elapsed.elapsed() < 650) {
            QTest::qWait(10);
            while (peer->canReadLine()) {
                const auto request = QJsonDocument::fromJson(peer->readLine()).object();
                if (request.value("type").toString() == "health") {
                    ++heartbeats;
                    send({{"type", "response"}, {"id", request.value("id")}, {"payload", QJsonObject{{"ok", true}}}});
                }
            }
        }
        QVERIFY(heartbeats >= 3);
        QVERIFY(client->connected());
    }

    void recoveryBudgetStopsCrashLoopUntilExplicitRetry() {
        BigBootyBudget budget;
        QVERIFY(budget.take(0));
        QVERIFY(budget.take(15000));
        QVERIFY(budget.take(30000));
        QVERIFY(!budget.take(45000));
        QVERIFY(!budget.take(700000));
        budget.reset();
        QVERIFY(budget.take(700000));
        QVERIFY(budget.take(1400000));
    }
    void insightsAreDisplayOnlyAndDoNotSendCommands() {
        sendEvent("agent.insights_changed",
                  {{"items", QJsonArray{QJsonObject{{"id", "thermal"}, {"title", "CPU hot"}}}}});
        QTRY_COMPARE(client->insights().size(), 1);
        QCOMPARE(client->insights().first().toMap().value("id").toString(), QString("thermal"));
        QTest::qWait(50);
        QVERIFY(!peer->canReadLine());
        sendEvent("agent.insights_changed", {{"items", QJsonArray{}}});
        QTRY_VERIFY(client->insights().isEmpty());
    }

    void steeringRepliesAreVisibleAndRetainExactTaskIdentity() {
        client->steerTask("exact-task", "use another folder");
        QTRY_VERIFY(peer->canReadLine());
        const auto request = QJsonDocument::fromJson(peer->readLine()).object();
        QCOMPARE(request.value("type").toString(), QString("agent.tasks.steer"));
        QCOMPARE(request.value("payload").toObject().value("task_id").toString(),
                 QString("exact-task"));
        send({{"type", "response"},
              {"id", request.value("id")},
              {"payload", QJsonObject{{"status", "blocked"},
                                      {"response", "Cancel the pending approval first."}}}});
        QTRY_VERIFY(client->statusMessage().contains("pending approval"));
        QTRY_COMPARE(client->timeline().size(), 2);
    }

    void activityComesFromRuntimeEvents() {
        sendEvent("agent.activity",
                  {{"phase", "WAITING"}, {"task_id", "fixture-task"}, {"goal_verified", false}});
        QTRY_COMPARE(client->activity().value("phase").toString(), QStringLiteral("WAITING"));
        QCOMPARE(client->activity().value("task_id").toString(), QStringLiteral("fixture-task"));
        QVERIFY(!client->activity().value("goal_verified").toBool());
    }

    void reconnectsWhenSocketReturns() {
        peer->disconnectFromServer();
        QTRY_VERIFY(!client->connected());
        delete peer;
        peer = nullptr;
        server.close();
        QTest::qWait(1700);
        QVERIFY(!client->connected());
        QVERIFY(server.listen(runtime.filePath("ev/ev.sock")));
        QTRY_VERIFY_WITH_TIMEOUT(server.hasPendingConnections(), 5000);
        peer = server.nextPendingConnection();
        QTRY_VERIFY(client->connected());
    }

    void disconnectInvalidatesLiveEvidenceAndApprovals() {
        client->refreshSnapshot();
        client->refreshDaily();
        QTRY_VERIFY(peer->canReadLine());
        while (peer->canReadLine()) {
            const auto request = QJsonDocument::fromJson(peer->readLine()).object();
            requestIds.insert(request.value("type").toString(), request.value("id").toString());
        }
        send({{"type", "response"}, {"id", requestIds.value("snapshot")},
              {"payload", QJsonObject{
                  {"core", QJsonObject{{"state", "THINKING"}}},
                  {"voice", QJsonObject{{"wake_active", true}}},
                  {"telemetry", QJsonObject{{"cpu_percent", 12}}},
                  {"provider", QJsonObject{{"status", "READY"}}},
                  {"activity", QJsonObject{{"phase", "EXECUTING"}}},
                  {"planner", QJsonObject{{"active", QJsonObject{{"id", "old-plan"}}}}}
              }}});
        send({{"type", "response"}, {"id", requestIds.value("daily.snapshot")},
              {"payload", QJsonObject{{"readiness", QJsonObject{{"state", "FULLY_READY"}}}}}});
        client->refreshConfirmations();
        QTRY_VERIFY(peer->canReadLine());
        while (peer->canReadLine()) {
            const auto request = QJsonDocument::fromJson(peer->readLine()).object();
            if (request.value("type").toString() == "confirmation.list")
                send({{"type", "response"}, {"id", request.value("id")},
                      {"payload", QJsonObject{{"confirmations", QJsonArray{QJsonObject{
                          {"id", "old-approval"}, {"approval_token", "old-token"}}}}}}});
        }
        QTRY_VERIFY(!client->confirmation().isEmpty());
        QTRY_VERIFY(!client->voice().isEmpty());
        QTRY_VERIFY(!client->daily().isEmpty());
        peer->disconnectFromServer();
        QTRY_VERIFY(!client->connected());
        QCOMPARE(client->state(), QString("OFFLINE"));
        QVERIFY(client->voice().isEmpty());
        QVERIFY(client->telemetry().isEmpty());
        QVERIFY(client->provider().isEmpty());
        QVERIFY(client->confirmation().isEmpty());
        QVERIFY(client->activePlan().isEmpty());
        QVERIFY(client->activity().isEmpty());
        QVERIFY(client->daily().isEmpty());
    }

    void explicitStopDoesNotAutoReconnect() {
        client->stopCore();
        peer->disconnectFromServer();
        QTRY_VERIFY(!client->connected());
        QTest::qWait(1700);
        QVERIFY(!server.hasPendingConnections());
        client->connectToCore();
        QTRY_VERIFY(server.hasPendingConnections());
        delete peer;
        peer = server.nextPendingConnection();
        QTRY_VERIFY(client->connected());
    }

    void init() {
        previousRuntime = qgetenv("XDG_RUNTIME_DIR");
        qputenv("XDG_RUNTIME_DIR", runtime.path().toLocal8Bit());
        QDir().mkpath(runtime.filePath("ev"));
        QVERIFY(server.listen(runtime.filePath("ev/ev.sock")));
        client = std::make_unique<EvClient>();
        client->connectToCore();
        QTRY_VERIFY(server.hasPendingConnections());
        peer = server.nextPendingConnection();
        QTRY_VERIFY(client->connected());
        QTRY_VERIFY(peer->bytesAvailable() > 0);
        requestIds.clear();
        while (peer->canReadLine()) {
            const auto request = QJsonDocument::fromJson(peer->readLine()).object();
            requestIds.insert(request.value("type").toString(), request.value("id").toString());
        }
        QVERIFY(requestIds.contains("events.history"));
        send({{"type", "response"},
              {"id", requestIds.value("snapshot")},
              {"payload",
               QJsonObject{{"core", QJsonObject{{"state", "DORMANT"}, {"detail", "Ready"}}}}}});
        QTRY_COMPARE(client->state(), QString("DORMANT"));
    }

    void cleanup() {
        client->disconnectFromCore();
        client.reset();
        delete peer;
        peer = nullptr;
        server.close();
        if (previousRuntime.isNull())
            qunsetenv("XDG_RUNTIME_DIR");
        else
            qputenv("XDG_RUNTIME_DIR", previousRuntime);
    }

    void waveformSamplesDoNotRebuildEventHistory() {
        QSignalSpy eventChanges(client.get(), &EvClient::eventsChanged);
        QSignalSpy inputChanges(client.get(), &EvClient::inputWaveformChanged);
        QSignalSpy snapshotChanges(client.get(), &EvClient::snapshotChanged);
        for (int index = 0; index < 200; ++index)
            sendEvent("voice.audio_level", {{"rms", 0.2}, {"waveform", QJsonArray{index / 200.0}}});
        QTRY_COMPARE(inputChanges.count(), 200);
        QCOMPARE(client->events().size(), 0);
        QCOMPARE(eventChanges.count(), 0);
        QCOMPARE(snapshotChanges.count(), 0);
        QCOMPARE(client->inputWaveform().size(), 1);
    }

    void eventBurstsAreCoalescedWithoutLosingEvents() {
        QSignalSpy eventChanges(client.get(), &EvClient::eventsChanged);
        for (int index = 0; index < 50; ++index)
            sendEvent("test.event", {{"index", index}});
        QTRY_COMPARE(client->events().size(), 50);
        QTRY_COMPARE(eventChanges.count(), 1);
    }

    void historyDoesNotReplayOldStateOrToolActions() {
        const QJsonArray events{
            QJsonObject{{"type", "core.state_changed"},
                        {"payload", QJsonObject{{"to", "THINKING"}}}},
            QJsonObject{{"type", "tool.started"}, {"payload", QJsonObject{{"tool", "old.tool"}}}},
        };
        send({{"type", "response"},
              {"id", requestIds.value("events.history")},
              {"payload", QJsonObject{{"events", events}}}});
        QTRY_COMPARE(client->events().size(), 2);
        QCOMPARE(client->state(), QString("DORMANT"));
        QVERIFY(client->timeline().isEmpty());
        QVERIFY(client->activeNodes().isEmpty());
    }

    void liveEventsAndOverlappingHistoryAreMergedBySequence() {
        send({{"type", "event"},
              {"payload", QJsonObject{{"sequence", 2},
                                      {"type", "test.live"},
                                      {"source", "test"},
                                      {"payload", QJsonObject{}}}}});
        QTRY_COMPARE(client->events().size(), 1);
        const QJsonArray history{
            QJsonObject{{"sequence", 1},
                        {"type", "test.old"},
                        {"source", "test"},
                        {"payload", QJsonObject{}}},
            QJsonObject{{"sequence", 2},
                        {"type", "test.live"},
                        {"source", "test"},
                        {"payload", QJsonObject{}}},
        };
        send({{"type", "response"},
              {"id", requestIds.value("events.history")},
              {"payload", QJsonObject{{"events", history}}}});
        QTRY_COMPARE(client->events().size(), 2);
        QCOMPARE(client->events().at(0).toMap().value("sequence").toLongLong(), 1);
        QCOMPARE(client->events().at(1).toMap().value("sequence").toLongLong(), 2);
    }

    void refreshRequestsAreCoalescedAndErrorsAllowRetry() {
        for (int index = 0; index < 10; ++index)
            client->refreshSnapshot();
        QTRY_VERIFY(peer->canReadLine());
        const auto request = QJsonDocument::fromJson(peer->readLine()).object();
        QCOMPARE(request.value("type").toString(), QString("snapshot"));
        QVERIFY(!peer->canReadLine());
        send({{"type", "error"},
              {"id", request.value("id")},
              {"payload", QJsonObject{{"message", "test error"}}}});
        QTRY_VERIFY(client->statusMessage().contains("test error"));
        client->refreshSnapshot();
        QTRY_VERIFY(peer->canReadLine());
        const auto retry = QJsonDocument::fromJson(peer->readLine()).object();
        QVERIFY(retry.value("id") != request.value("id"));
    }

    void disconnectClearsTransientActivityAndWaveforms() {
        sendEvent("tool.started", {{"tool", "desktop.window.activate"}, {"category", "DESKTOP"}});
        sendEvent("voice.audio_level", {{"rms", 0.3}, {"waveform", QJsonArray{0.3}}});
        sendEvent("tts.audio_level", {{"waveform", QJsonArray{0.4}}});
        QTRY_VERIFY(!client->activeNodes().isEmpty());
        QTRY_VERIFY(!client->inputWaveform().isEmpty());
        QTRY_VERIFY(!client->outputWaveform().isEmpty());
        client->disconnectFromCore();
        QTRY_VERIFY(!client->connected());
        QVERIFY(client->activeNodes().isEmpty());
        QVERIFY(client->inputWaveform().isEmpty());
        QVERIFY(client->outputWaveform().isEmpty());
    }
};

QTEST_GUILESS_MAIN(EvClientTests)
#include "test_ev_client.moc"
