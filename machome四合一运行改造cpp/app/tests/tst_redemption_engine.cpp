#include "modules/redemption/RedemptionCore.h"
#include "modules/redemption/RedemptionEngine.h"

#include <QFile>
#include <QDir>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTcpSocket>
#include <QTcpServer>
#include <QTemporaryDir>
#include <QTest>
#include <QTimeZone>

using machome::redemption::RedemptionCore;
using machome::redemption::RedemptionEngine;

namespace {

QDateTime shanghai(int hour, int minute, int second = 0,
                   int year = 2026, int month = 9, int day = 4) {
    return QDateTime(QDate(year, month, day), QTime(hour, minute, second),
                     QTimeZone("Asia/Shanghai")).toUTC();
}

QJsonObject fixture() {
    QFile file(QStringLiteral(MACHOME_TEST_SOURCE_DIR)
               + QStringLiteral("/fixtures/redemption/golden_intraday_creation.json"));
    if (!file.open(QIODevice::ReadOnly)) return {};
    return QJsonDocument::fromJson(file.readAll()).object();
}

hub::ModuleContext context(const QString &root) {
    return {QStringLiteral("redemption"), root,
            QJsonObject{{QStringLiteral("test_mode"), true},
                        {QStringLiteral("wind_helper_mode"), QStringLiteral("disabled")},
                        {QStringLiteral("pcf_network_enabled"), false},
                        {QStringLiteral("compatibility_api_enabled"), false},
                        {QStringLiteral("live_qmt_orders_enabled"), false},
                        {QStringLiteral("qmt_backends"), QJsonArray{QJsonObject{
                             {QStringLiteral("id"), QStringLiteral("QMT1")},
                             {QStringLiteral("host"), QStringLiteral("127.0.0.1")},
                             {QStringLiteral("port"), 65534},
                             {QStringLiteral("auto_connect"), false}}}}}, true};
}

} // namespace

class RedemptionEngineTest final : public QObject {
    Q_OBJECT
private slots:
    void windLifecycleChecksRealStatusAcrossIdleAndLaunchWindows() {
        QTemporaryDir root;
        QFile helper(root.filePath("lifecycle.py")); QVERIFY(helper.open(QIODevice::WriteOnly));
        helper.write(R"PY(#!/usr/bin/env python3
import json,sys,pathlib
running=True
marker=pathlib.Path(__file__).with_suffix('.opened')
def emit(x): print(json.dumps(x),flush=True)
def status(): emit({'type':'status','state':'ready','wind_running':running,'tbapi_loaded':running})
emit({'type':'hello','protocol':1});status()
for line in sys.stdin:
 c=json.loads(line);a=c['action']
 if a=='quit':break
 if a=='status':
  if marker.exists(): running=True;marker.unlink()
  status()
 if a in ('shutdown_wind','start_wind'):
  running=a=='start_wind';status()
  emit({'type':'command_result','action':a,'ok':True,'details':{'cleanup_deleted_count':2}})
)PY");
        helper.close(); QVERIFY(helper.setPermissions(QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner));
        auto ctx=context(root.path()); ctx.settings.insert("wind_helper_mode","fixture");
        ctx.settings.insert("wind_helper_path",helper.fileName());ctx.settings.insert("wind_control_enabled",true);
        RedemptionEngine engine;engine.setNowForTest(shanghai(8,40));engine.initialize(ctx);
        QSignalSpy snapshots(&engine,&hub::IModuleEngine::snapshotReady);engine.start();
        QTRY_VERIFY_WITH_TIMEOUT(!engine.snapshot()["wind"].toObject()["last_cleanup_at"].toString().isEmpty(),3000);
        QVERIFY(!engine.snapshot()["wind"].toObject()["running"].toBool());
        QCOMPARE(engine.snapshot()["wind"].toObject()["cleanup_deleted_count"].toInt(),2);
        engine.setNowForTest(shanghai(9,10));engine.evaluateScheduleForTest();
        QTRY_VERIFY_WITH_TIMEOUT(engine.snapshot()["wind"].toObject()["running"].toBool(),3000);
        engine.setNowForTest(shanghai(15,0));engine.evaluateScheduleForTest();
        QTRY_VERIFY_WITH_TIMEOUT(!engine.snapshot()["wind"].toObject()["running"].toBool(),3000);
        QFile marker(root.filePath("lifecycle.opened"));QVERIFY(marker.open(QIODevice::WriteOnly));marker.close();
        // Same day cleanup was already done: real process state still wins.
        engine.setNowForTest(shanghai(15,3));engine.evaluateScheduleForTest();
        QTRY_COMPARE_WITH_TIMEOUT(engine.snapshot()["wind"].toObject()["last_cleanup_at"].toString(),
            shanghai(15,3).toString(Qt::ISODateWithMs),3000);
        QVERIFY(!engine.snapshot()["wind"].toObject()["running"].toBool());
        snapshots.clear();engine.setNowForTest(shanghai(7,51,0,2026,9,5));engine.evaluateScheduleForTest();
        QVERIFY(!snapshots.isEmpty());
        QCOMPARE(snapshots.last().first().toJsonObject()["schedule"].toObject()["phase"].toString(),QString("weekend"));
        engine.stop();
    }

    void serverQmtIsOptInAndCloseStopsReconnect() {
        QTemporaryDir root;
        QTcpServer backend; QVERIFY(backend.listen(QHostAddress::LocalHost, 0));
        auto ctx = context(root.path());
        ctx.settings.insert("qmt_backends", QJsonArray{QJsonObject{
            {"id", "QMT1"}, {"host", "127.0.0.1"}, {"port", backend.serverPort()},
            {"auto_connect", true}, {"reconnect_interval_ms", 500}}});
        RedemptionEngine engine; engine.setNowForTest(shanghai(10, 0));
        engine.initialize(ctx); engine.start();
        QTest::qWait(600);
        QVERIFY(!backend.hasPendingConnections()); // stale auto_connect cannot dial by default
        QCOMPARE(engine.snapshot()["qmt_backends"].toObject()["QMT1"].toObject()["connection_policy"].toString(), QString("on_demand"));
        engine.submitCommand("redemption_qmt_connect", {{"backend", "QMT1"}}, "connect");
        QTRY_VERIFY_WITH_TIMEOUT(backend.hasPendingConnections(), 1000);
        auto *peer = backend.nextPendingConnection();
        peer->abort();
        engine.setNowForTest(shanghai(15, 0)); engine.evaluateScheduleForTest();
        QTest::qWait(1100);
        QCOMPARE(engine.snapshot()["state"].toString(), QString("scheduled_idle"));
        for (const auto &item : engine.snapshot()["items"].toArray())
            QCOMPARE(item.toObject()["status"].toString(), QString("stopped"));
        const auto state = engine.snapshot()["qmt_backends"].toObject()["QMT1"].toObject();
        QVERIFY(!state["want_connection"].toBool());
        QCOMPARE(state["connection_state"].toString(), QString("disconnected"));
        QVERIFY(!backend.hasPendingConnections());
        QSignalSpy finished(&engine, &hub::IModuleEngine::commandFinished);
        engine.submitCommand("redemption_qmt_connect", {{"backend", "QMT1"}}, "closed-connect");
        engine.submitCommand("redemption_monitor_start", {}, "closed-monitor");
        QCOMPARE(finished.count(), 2);
        QVERIFY(!finished.at(0).at(1).toBool()); QVERIFY(!finished.at(1).at(1).toBool());
        engine.setNowForTest(shanghai(10, 0, 0, 2026, 9, 7)); engine.evaluateScheduleForTest();
        QTest::qWait(600); QVERIFY(!backend.hasPendingConnections());
        engine.stop();
    }

    void manualWindCleanupCannotSuppressClose_data() {
        QTest::addColumn<bool>("pendingWarmup");
        QTest::addColumn<bool>("lateAck");
        QTest::newRow("subscribed-again-after-cleanup") << false << false;
        QTest::newRow("warmup-crosses-close") << true << false;
        QTest::newRow("subscribe-ack-crosses-close") << false << true;
    }
    void manualWindCleanupCannotSuppressClose() {
        QFETCH(bool, pendingWarmup);
        QFETCH(bool, lateAck);
        QTemporaryDir root;
        const auto helperPath = QDir(root.path()).filePath("helper.py");
        QFile helper(helperPath); QVERIFY(helper.open(QIODevice::WriteOnly));
        helper.write(R"PY(#!/usr/bin/env python3
import json,sys
def emit(x): print(json.dumps(x),flush=True)
emit({'type':'hello','protocol':1})
emit({'type':'status','state':'ready','wind_running':True,'tbapi_loaded':True})
for line in sys.stdin:
 c=json.loads(line); a=c['action']
 if a=='quit': break
 if a=='warmup': emit({'type':'command_result','action':a,'ok':True,'details':{'warmup_completed':True,'unsubscribe_confirmed':True}})
 elif a=='subscribe':
  import time
  time.sleep(.3)
  emit({'type':'status','state':'subscribed','wind_running':True,'tbapi_loaded':True})
 elif a=='unsubscribe': emit({'type':'status','state':'ready','unsubscribed':True})
 elif a in ('start_wind','shutdown_wind'):
  emit({'type':'status','state':'ready','wind_running':a=='start_wind','tbapi_loaded':True})
  emit({'type':'command_result','action':a,'request_id':c.get('request_id',''),'ok':True})
)PY");
        helper.close(); QVERIFY(helper.setPermissions(QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner));
        auto ctx = context(root.path()); ctx.settings.insert("wind_helper_mode", "fixture");
        ctx.settings.insert("wind_helper_path", helperPath); ctx.settings.insert("wind_control_enabled", true);
        RedemptionEngine engine; engine.setNowForTest(shanghai(9, 15, 5)); engine.initialize(ctx); engine.start();
        QTRY_COMPARE_WITH_TIMEOUT(engine.snapshot()["wind"].toObject()["state"].toString(), QString("ready"), 3000);
        engine.setNowForTest(shanghai(10, 0));
        engine.evaluateScheduleForTest();
        if (lateAck) {
            QTRY_COMPARE_WITH_TIMEOUT(engine.snapshot()["state"].toString(), QString("subscribing"), 4000);
        } else if (!pendingWarmup) {
            QTRY_VERIFY_WITH_TIMEOUT(engine.snapshot()["monitoring"].toBool(), 5000);
            QSignalSpy finished(&engine, &hub::IModuleEngine::commandFinished);
            engine.submitCommand("redemption_wind_shutdown_cleanup", {}, "cleanup");
            QTRY_VERIFY_WITH_TIMEOUT(!finished.isEmpty(), 2000);
            QVERIFY(finished.takeFirst().at(1).toBool());
            engine.submitCommand("redemption_wind_start", {}, "restart-wind");
            QTRY_VERIFY_WITH_TIMEOUT(!finished.isEmpty(), 2000);
            engine.submitCommand("redemption_monitor_start", {}, "resume");
            QTRY_VERIFY_WITH_TIMEOUT(engine.snapshot()["monitoring"].toBool(), 3000);
        }
        engine.setNowForTest(shanghai(15, 0)); engine.evaluateScheduleForTest();
        QTest::qWait(2300); // late warmup completion must not resubscribe
        QVERIFY(!engine.snapshot()["monitoring"].toBool());
        QCOMPARE(engine.snapshot()["state"].toString(), QString("scheduled_idle"));
        QCOMPARE(engine.snapshot()["schedule"].toObject()["phase"].toString(), QString("closed_pcf_cache"));
        QVERIFY(engine.snapshot()["health"].toObject()["ok"].toBool());
        engine.stop();
    }

    void pcfManualRefreshIsSerializedAndCooldownSurvivesRestart() {
        QTemporaryDir root;QTcpServer server;QVERIFY(server.listen(QHostAddress::LocalHost,0));
        QList<QTcpSocket*> requests;
        connect(&server,&QTcpServer::newConnection,&server,[&]{while(auto *socket=server.nextPendingConnection())requests.append(socket);});
        auto ctx=context(root.path());ctx.settings.insert("pcf_network_enabled",true);
        ctx.settings.insert("pcf_url_template",QStringLiteral("http://127.0.0.1:%1/{symbol}/{date}").arg(server.serverPort()));
        const auto now=shanghai(10,0);
        {
            RedemptionEngine engine;engine.setNowForTest(now);engine.initialize(ctx);
            engine.submitCommand("set_operating_mode",{{"mode","weekend_test"}},"mode");engine.start();
            engine.submitCommand("redemption_pcf_refresh",{},"one");
            engine.submitCommand("redemption_pcf_refresh",{},"two");
            QTRY_COMPARE_WITH_TIMEOUT(requests.size(),1,1000);QTest::qWait(100);QCOMPARE(requests.size(),1);
            requests.first()->write("HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
            requests.first()->disconnectFromHost();QTest::qWait(100);engine.stop();
        }
        RedemptionEngine next;next.setNowForTest(now.addSecs(10));next.initialize(ctx);
        next.submitCommand("set_operating_mode",{{"mode","weekend_test"}},"mode");next.start();
        next.submitCommand("redemption_pcf_refresh",{},"three");QTest::qWait(100);QCOMPARE(requests.size(),1);
        next.setNowForTest(now.addSecs(601));next.submitCommand("redemption_pcf_refresh",{},"four");
        QTRY_COMPARE_WITH_TIMEOUT(requests.size(),2,1000);next.stop();
    }
    void oversizedHttpWithoutNewlineIsClosed() {
        QTemporaryDir root;auto ctx=context(root.path());ctx.settings.insert("compatibility_api_enabled",true);ctx.settings.insert("compatibility_port",0);
        RedemptionEngine engine;engine.initialize(ctx);engine.start();QTcpSocket socket;
        socket.connectToHost(QHostAddress::LocalHost,engine.compatibilityPortForTest());
        QTRY_COMPARE_WITH_TIMEOUT(socket.state(),QAbstractSocket::ConnectedState,1000);
        socket.write(QByteArray(20*1024,'a'));
        QTRY_COMPARE_WITH_TIMEOUT(socket.state(),QAbstractSocket::UnconnectedState,1500);
    }

    void exactScheduleBoundariesMatchBaseline() {
        QCOMPARE(RedemptionCore::evaluateSchedule(shanghai(8, 29, 59)).phase,
                 QStringLiteral("overnight"));
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(8, 30)).pcfWindow);
        QVERIFY(!RedemptionCore::evaluateSchedule(shanghai(8, 59, 59)).resetDue);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(9, 0)).resetDue);
        QVERIFY(!RedemptionCore::evaluateSchedule(shanghai(9, 9, 59)).windLaunchDue);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(9, 10)).windLaunchDue);
        QVERIFY(!RedemptionCore::evaluateSchedule(shanghai(9, 15, 4)).warmupDue);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(9, 15, 5)).warmupDue);
        QVERIFY(!RedemptionCore::evaluateSchedule(shanghai(9, 15, 29)).monitoringDesired);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(9, 15, 30)).monitoringDesired);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(14, 59, 59)).monitoringDesired);
        QVERIFY(!RedemptionCore::evaluateSchedule(shanghai(15, 0)).monitoringDesired);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(15, 0)).shutdownDue);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(11, 30)).monitoringDesired);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(13, 0)).monitoringDesired);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(14, 57)).monitoringDesired);
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(15, 6)).shutdownDue);
        QCOMPARE(RedemptionCore::evaluateSchedule(shanghai(16, 0)).phase,
                 QStringLiteral("closed_pcf_cache"));
        QVERIFY(RedemptionCore::evaluateSchedule(shanghai(23, 0)).pcfWindow);
        QVERIFY(!RedemptionCore::evaluateSchedule(shanghai(23, 0, 1)).pcfWindow);
        QVERIFY(!RedemptionCore::evaluateSchedule(
            shanghai(10, 0, 0, 2026, 9, 5)).businessDay);
    }

    void operatingModeDefaultsToWorkAndCanChangeWhileStopped() {
        QTemporaryDir root;
        QJsonObject settings = context(root.path()).settings;
        // Configuration must not make the test bypass survive a restart.
        settings.insert(QStringLiteral("operating_mode"), QStringLiteral("weekend_test"));
        RedemptionEngine engine;
        engine.initialize({QStringLiteral("redemption"), root.path(), settings, true});
        QCOMPARE(engine.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("work"));

        QSignalSpy finished(&engine, &hub::IModuleEngine::commandFinished);
        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("weekend_test")}},
                             QStringLiteral("mode-weekend"));
        QCOMPARE(finished.count(), 1);
        QVERIFY(finished.takeFirst().at(1).toBool());
        QCOMPARE(engine.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("weekend_test"));
        QVERIFY(engine.snapshot().value(QStringLiteral("schedule")).toObject()
                    .value(QStringLiteral("manual_time_window_bypass")).toBool());

        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("unsafe")}},
                             QStringLiteral("mode-invalid"));
        QCOMPARE(finished.count(), 1);
        QVERIFY(!finished.takeFirst().at(1).toBool());
        QCOMPARE(engine.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("weekend_test"));
    }

    void weekendTestAllowsManualMonitoringButNeverOpensOrderGate() {
        QTemporaryDir root;
        QVERIFY(QDir().mkpath(QDir(root.path()).filePath(QStringLiteral("captures"))));
        QFile capture(QDir(root.path()).filePath(QStringLiteral("captures/first.json")));
        QVERIFY(capture.open(QIODevice::WriteOnly));
        capture.write(QJsonDocument(fixture().value(QStringLiteral("baseline")).toObject())
                          .toJson(QJsonDocument::Compact));
        capture.close();

        QJsonObject settings = context(root.path()).settings;
        settings.insert(QStringLiteral("wind_helper_mode"), QStringLiteral("fixture"));
        settings.insert(QStringLiteral("wind_helper_path"),
                        QStringLiteral(MACHOME_TEST_WIND_HELPER));
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(10, 0, 0, 2026, 9, 5)); // Saturday
        engine.initialize({QStringLiteral("redemption"), root.path(), settings, true});
        QSignalSpy finished(&engine, &hub::IModuleEngine::commandFinished);
        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("weekend_test")}},
                             QStringLiteral("mode-weekend"));
        QCOMPARE(finished.count(), 1);
        QVERIFY(finished.takeFirst().at(1).toBool());
        engine.start();
        QTRY_COMPARE_WITH_TIMEOUT(engine.snapshot().value(QStringLiteral("wind")).toObject()
                                      .value(QStringLiteral("state")).toString(),
                                  QStringLiteral("ready"), 3000);

        QElapsedTimer warmupElapsed;
        warmupElapsed.start();
        engine.submitCommand(QStringLiteral("redemption_monitor_start"), {},
                             QStringLiteral("weekend-monitor"));
        QTRY_VERIFY_WITH_TIMEOUT(engine.snapshot().value(QStringLiteral("monitoring")).toBool(),
                                 5000);
        QVERIFY(warmupElapsed.elapsed() >= 1800);
        engine.evaluateScheduleForTest();
        QVERIFY(engine.snapshot().value(QStringLiteral("monitoring")).toBool());

        engine.submitCommand(QStringLiteral("redemption_qmt_order"),
                             {{QStringLiteral("backend"), QStringLiteral("QMT1")},
                              {QStringLiteral("symbol"), QStringLiteral("159518")},
                              {QStringLiteral("side"), QStringLiteral("PURCHASE")}},
                             QStringLiteral("weekend-order"));
        QTRY_VERIFY_WITH_TIMEOUT(finished.count() >= 2, 1000);
        bool orderRejected = false;
        while (!finished.isEmpty()) {
            const QList<QVariant> result = finished.takeFirst();
            if (result.at(0).toString() == QStringLiteral("weekend-order")) {
                orderRejected = !result.at(1).toBool();
            }
        }
        QVERIFY(orderRejected);

        // Returning to work at the exact close boundary immediately restores
        // the old 15:00 stop semantics without starting another module.
        engine.setNowForTest(shanghai(15, 0));
        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("work")}},
                             QStringLiteral("mode-work"));
        QVERIFY(!engine.snapshot().value(QStringLiteral("monitoring")).toBool());
        QCOMPARE(engine.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("work"));
        engine.stop();
    }

    void dailyResetRejectsCaptureObservedBeforeNineOClock() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(9, 0));
        engine.initialize(context(root.path()));
        engine.evaluateScheduleForTest();
        QJsonObject oldFrame = fixture().value(QStringLiteral("baseline")).toObject();
        oldFrame.insert(QStringLiteral("observed_at"),
                        shanghai(8, 59, 59).toString(Qt::ISODateWithMs));
        QString error;
        QVERIFY2(engine.ingestCaptureForTest(oldFrame, &error), qPrintable(error));
        const QJsonObject item = engine.snapshot().value(QStringLiteral("items"))
                                     .toArray().at(1).toObject();
        QVERIFY(item.value(QStringLiteral("values")).toObject().isEmpty());
    }

    void historyRetentionPrunesRowsOlderThanOneHundredTwentyDays() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(10, 0, 0, 2026, 1, 5));
        engine.initialize(context(root.path()));
        QJsonObject first = fixture().value(QStringLiteral("baseline")).toObject();
        QJsonObject second = fixture().value(QStringLiteral("change")).toObject();
        first.insert(QStringLiteral("observed_at"),
                     shanghai(10, 0, 0, 2026, 1, 5).toString(Qt::ISODateWithMs));
        second.insert(QStringLiteral("observed_at"),
                      shanghai(10, 1, 0, 2026, 1, 5).toString(Qt::ISODateWithMs));
        QString error;
        QVERIFY2(engine.ingestCaptureForTest(first, &error), qPrintable(error));
        QVERIFY2(engine.ingestCaptureForTest(second, &error), qPrintable(error));

        QSignalSpy finished(&engine, &hub::IModuleEngine::commandFinished);
        engine.submitCommand(QStringLiteral("redemption_get_history"),
                             {{QStringLiteral("date"), QStringLiteral("2026-01-05")}},
                             QStringLiteral("history-before"));
        QCOMPARE(finished.count(), 1);
        QVERIFY(!finished.takeFirst().at(3).toJsonObject()
                     .value(QStringLiteral("items")).toArray().isEmpty());

        engine.setNowForTest(shanghai(9, 0, 0, 2026, 9, 4));
        engine.evaluateScheduleForTest();
        engine.submitCommand(QStringLiteral("redemption_get_history"),
                             {{QStringLiteral("date"), QStringLiteral("2026-01-05")}},
                             QStringLiteral("history-after"));
        QCOMPARE(finished.count(), 1);
        QVERIFY(finished.takeFirst().at(3).toJsonObject()
                    .value(QStringLiteral("items")).toArray().isEmpty());
    }

    void firstFrameIsBaselineThenGoldenChangeIsActionable() {
        QTemporaryDir root;
        QVERIFY(root.isValid());
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(9, 30));
        engine.initialize(context(root.path()));
        const QJsonObject data = fixture();
        QVERIFY(!data.isEmpty());
        QString error;
        QVERIFY2(engine.installPcfForTest(QStringLiteral("159518"),
                                         data.value(QStringLiteral("pcf")).toObject(), &error),
                 qPrintable(error));
        QSignalSpy changes(&engine, &hub::IModuleEngine::eventReady);
        QVERIFY2(engine.ingestCaptureForTest(data.value(QStringLiteral("baseline")).toObject(), &error),
                 qPrintable(error));
        QCOMPARE(changes.count(), 0);
        QVERIFY2(engine.ingestCaptureForTest(data.value(QStringLiteral("change")).toObject(), &error),
                 qPrintable(error));
        QVERIFY(changes.count() >= 1);
        const QJsonObject item = engine.snapshot().value(QStringLiteral("items")).toArray().at(1).toObject();
        QCOMPARE(item.value(QStringLiteral("windcode")).toString(), QStringLiteral("159518.SZ"));
        const QJsonObject opportunity = item.value(QStringLiteral("opportunity")).toObject();
        QCOMPARE(opportunity.value(QStringLiteral("kind")).toString(), QStringLiteral("creation"));
        QVERIFY(opportunity.value(QStringLiteral("actionable")).toBool());
        QCOMPARE(opportunity.value(QStringLiteral("full_baskets")).toInt(), 1);
        QCOMPARE(item.value(QStringLiteral("last_change")).toArray().first().toObject()
                     .value(QStringLiteral("field")).toString(), QStringLiteral("etfsellamount"));
    }

    void latePcfReclassifiesTheLastIntradayChange() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(9, 30));
        engine.initialize(context(root.path()));
        const QJsonObject data = fixture();
        QString error;
        QVERIFY(engine.ingestCaptureForTest(
            data.value(QStringLiteral("baseline")).toObject(), &error));
        QVERIFY(engine.ingestCaptureForTest(
            data.value(QStringLiteral("change")).toObject(), &error));
        QCOMPARE(engine.snapshot().value(QStringLiteral("items")).toArray().at(1).toObject()
                     .value(QStringLiteral("opportunity")).toObject()
                     .value(QStringLiteral("kind")).toString(), QStringLiteral("pending"));
        QVERIFY(engine.installPcfForTest(QStringLiteral("159518"),
                                        data.value(QStringLiteral("pcf")).toObject(), &error));
        QCOMPARE(engine.snapshot().value(QStringLiteral("items")).toArray().at(1).toObject()
                     .value(QStringLiteral("opportunity")).toObject()
                     .value(QStringLiteral("kind")).toString(), QStringLiteral("creation"));
    }

    void malformedAndRollbackFramesFailClosed() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.initialize(context(root.path()));
        QString error;
        QVERIFY(!engine.ingestCaptureForTest(
            {{QStringLiteral("windcode"), QStringLiteral("159518.SZ")},
             {QStringLiteral("values"), QJsonObject{
                  {QStringLiteral("etfbuynumber"), 1},
                  {QStringLiteral("etfbuyamount"), QStringLiteral("bad")},
                  {QStringLiteral("etfsellnumber"), 0},
                  {QStringLiteral("etfsellamount"), -1}}}}, &error));
        QVERIFY(!error.isEmpty());

        const QJsonObject previous{{QStringLiteral("etfbuyamount"), 2'000'000},
                                   {QStringLiteral("etfsellamount"), 1'000'000},
                                   {QStringLiteral("netamount"), 1'000'000}};
        const QJsonObject current{{QStringLiteral("etfbuyamount"), 1'000'000},
                                  {QStringLiteral("etfsellamount"), 1'000'000},
                                  {QStringLiteral("netamount"), 0}};
        const QJsonObject result = RedemptionCore::classifyIntradayOpportunity(
            &previous, current, {}, QDate(2026, 9, 4));
        QCOMPARE(result.value(QStringLiteral("kind")).toString(), QStringLiteral("baseline"));
        QVERIFY(!result.value(QStringLiteral("actionable")).toBool());
    }

    void pcfXmlParserPreservesMetadataAndBasketRows() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.initialize(context(root.path()));
        const QByteArray xml = R"xml(<?xml version="1.0"?>
          <PCF><Header><Symbol>测试ETF</Symbol><TradingDay>20260904</TradingDay>
          <CreationRedemptionUnit>1000000</CreationRedemptionUnit><Creation>Y</Creation>
          <Redemption>N</Redemption></Header><ComponentList><Component>
          <UnderlyingSecurityID>000001</UnderlyingSecurityID><ComponentShare>1200</ComponentShare>
          </Component></ComponentList></PCF>)xml";
        QString error;
        const QJsonObject parsed = engine.parsePcfForTest(
            xml, QStringLiteral("159518.SZ"), &error);
        QVERIFY2(!parsed.isEmpty(), qPrintable(error));
        QCOMPARE(parsed.value(QStringLiteral("trading_day")).toString(),
                 QStringLiteral("2026-09-04"));
        QCOMPARE(parsed.value(QStringLiteral("creation_redemption_unit")).toInt(), 1'000'000);
        QVERIFY(parsed.value(QStringLiteral("creation_allowed")).toBool());
        QVERIFY(!parsed.value(QStringLiteral("redemption_allowed")).toBool(true));
        QCOMPARE(parsed.value(QStringLiteral("components")).toArray().size(), 1);
        QCOMPARE(parsed.value(QStringLiteral("components")).toArray().first().toObject()
                     .value(QStringLiteral("UnderlyingSecurityID")).toString(),
                 QStringLiteral("000001"));
        QVERIFY(!parsed.value(QStringLiteral("summary_fields")).toArray().isEmpty());
    }

    void sevenProductionSymbolsAndDailyResetRemainNative() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(8, 59, 59));
        engine.initialize(context(root.path()));
        QCOMPARE(engine.snapshot().value(QStringLiteral("items")).toArray().size(), 7);
        engine.setNowForTest(shanghai(9, 0));
        engine.evaluateScheduleForTest();
        const QJsonObject snapshot = engine.snapshot();
        QCOMPARE(snapshot.value(QStringLiteral("schedule")).toObject()
                     .value(QStringLiteral("daily_reset")).toString(), QStringLiteral("09:00:00"));
        QVERIFY(!snapshot.value(QStringLiteral("health")).toObject()
                      .value(QStringLiteral("legacy_execution_allowed")).toBool(true));
    }

    void monitorFailsClosedUntilWindBoundaryIsReady() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(9, 15, 30));
        engine.initialize(context(root.path()));
        engine.evaluateScheduleForTest();
        QCOMPARE(engine.snapshot().value(QStringLiteral("state")).toString(),
                 QStringLiteral("warming"));
        QVERIFY(!engine.snapshot().value(QStringLiteral("monitoring")).toBool());

        QSignalSpy finished(&engine, &hub::IModuleEngine::commandFinished);
        engine.submitCommand(QStringLiteral("redemption_monitor_start"), {},
                             QStringLiteral("monitor-1"));
        QCOMPARE(finished.count(), 1);
        QVERIFY(!finished.takeFirst().at(1).toBool());
    }

    void helperCrashIsContainedInsideRedemptionModule() {
        QTemporaryDir root;
        QJsonObject settings = context(root.path()).settings;
        settings.insert(QStringLiteral("wind_helper_mode"), QStringLiteral("fixture"));
        settings.insert(QStringLiteral("wind_helper_path"), QStringLiteral("/usr/bin/false"));
        RedemptionEngine engine;
        engine.initialize({QStringLiteral("redemption"), root.path(), settings, true});
        engine.start();
        QTRY_COMPARE_WITH_TIMEOUT(
            engine.snapshot().value(QStringLiteral("wind")).toObject()
                .value(QStringLiteral("state")).toString(),
            QStringLiteral("degraded"), 3000);
        QVERIFY(!engine.snapshot().value(QStringLiteral("monitoring")).toBool());
        engine.stop();
    }

    void fixtureHelperMustConfirmSubscriptionBeforeEngineBecomesActive() {
        QTemporaryDir root;
        QVERIFY(QDir().mkpath(QDir(root.path()).filePath(QStringLiteral("captures"))));
        QFile capture(QDir(root.path()).filePath(QStringLiteral("captures/first.json")));
        QVERIFY(capture.open(QIODevice::WriteOnly));
        capture.write(QJsonDocument(fixture().value(QStringLiteral("baseline")).toObject())
                          .toJson(QJsonDocument::Compact));
        capture.close();
        QJsonObject settings = context(root.path()).settings;
        settings.insert(QStringLiteral("wind_helper_mode"), QStringLiteral("fixture"));
        settings.insert(QStringLiteral("wind_helper_path"),
                        QStringLiteral(MACHOME_TEST_WIND_HELPER));
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(9, 15, 5));
        engine.initialize({QStringLiteral("redemption"), root.path(), settings, true});
        engine.start();
        QTRY_COMPARE_WITH_TIMEOUT(engine.snapshot().value(QStringLiteral("wind")).toObject()
                                      .value(QStringLiteral("state")).toString(),
                                  QStringLiteral("ready"), 3000);
        engine.setNowForTest(shanghai(9, 15, 30));
        engine.evaluateScheduleForTest();
        QTRY_VERIFY_WITH_TIMEOUT(engine.snapshot().value(QStringLiteral("monitoring")).toBool(),
                                 5000);
        QCOMPARE(engine.snapshot().value(QStringLiteral("state")).toString(),
                 QStringLiteral("active"));
        QSignalSpy stopped(&engine, &hub::IModuleEngine::commandFinished);
        engine.submitCommand(QStringLiteral("redemption_monitor_stop"), {},
                             QStringLiteral("stop-confirmed"));
        QTRY_COMPARE_WITH_TIMEOUT(stopped.count(), 1, 3000);
        const QList<QVariant> result = stopped.takeFirst();
        QCOMPARE(result.at(0).toString(), QStringLiteral("stop-confirmed"));
        QVERIFY(result.at(1).toBool());
        QVERIFY(!engine.snapshot().value(QStringLiteral("monitoring")).toBool());
        engine.stop();
    }

    void firstWindNullFaultRequiresSubsequentSubscriptionAck_data() {
        QTest::addColumn<QString>("fault");
        QTest::addColumn<bool>("subscribeAck");
        QTest::addColumn<bool>("expectedActive");
        QTest::newRow("known-first-call") << "EXC_BAD_ACCESS (code=1, address=0x0)." << true << true;
        QTest::newRow("zero-padded") << "EXC_BAD_ACCESS (address = 0x0000000000000000)" << true << true;
        QTest::newRow("no-formal-ack") << "EXC_BAD_ACCESS (address=0x0)" << false << false;
        QTest::newRow("non-null-fault") << "EXC_BAD_ACCESS (address=0x0000010)" << true << false;
        QTest::newRow("permission-error") << "attach failed: Operation not permitted" << true << false;
    }

    void firstWindNullFaultRequiresSubsequentSubscriptionAck() {
        QFETCH(QString, fault);
        QFETCH(bool, subscribeAck);
        QFETCH(bool, expectedActive);
        QTemporaryDir root;
        const QString path = root.filePath("helper.py");
        QFile helper(path);
        QVERIFY(helper.open(QIODevice::WriteOnly));
        const QByteArray encodedFault = QJsonDocument(QJsonArray{fault}).toJson(QJsonDocument::Compact);
        helper.write("#!/usr/bin/env python3\nimport json,sys\n"
                     "def emit(value): print(json.dumps(value),flush=True)\n"
                     "emit({'type':'hello','protocol':1})\n"
                     "emit({'type':'status','state':'ready','wind_running':True,'tbapi_loaded':True})\n");
        helper.write("fault=" + encodedFault + "[0]\n");
        helper.write(subscribeAck ? "ack=True\n" : "ack=False\n");
        helper.write("for line in sys.stdin:\n"
                     " c=json.loads(line);a=c.get('action')\n"
                     " with open(__file__+'.commands','a') as log: log.write(str(a)+'\\n')\n"
                     " if a=='quit': break\n"
                     " if a=='warmup': emit({'type':'error','action':'warmup','code':'warmup_subscribe_failed','message':fault})\n"
                     " if a=='subscribe' and ack: emit({'type':'status','state':'subscribed'})\n"
                     " if a=='unsubscribe': emit({'type':'status','state':'ready','unsubscribed':True})\n");
        helper.close();
        QVERIFY(QFile::setPermissions(path, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        auto settings = context(root.path()).settings;
        settings.insert("wind_helper_mode", "fixture");
        settings.insert("wind_helper_path", path);
        RedemptionEngine engine;
        // Observe startup before allowing the asynchronous warmup fault.
        engine.setNowForTest(shanghai(9, 15, 4));
        engine.initialize({"redemption", root.path(), settings, true});
        engine.start();
        QTRY_COMPARE_WITH_TIMEOUT(engine.snapshot().value("wind").toObject().value("state").toString(),
                                  QStringLiteral("ready"), 3000);
        engine.setNowForTest(shanghai(9, 15, 30));
        engine.evaluateScheduleForTest();
        if (expectedActive) {
            QTRY_VERIFY_WITH_TIMEOUT(engine.snapshot().value("monitoring").toBool(), 7000);
            QVERIFY(engine.snapshot().value("last_error").toString().isEmpty());
        } else {
            QTest::qWait(4000);
            QVERIFY(!engine.snapshot().value("monitoring").toBool());
        }
        QFile commands(path + ".commands");
        QVERIFY(commands.open(QIODevice::ReadOnly));
        const auto actions = commands.readAll().split('\n');
        QCOMPARE(actions.count("warmup"), 1);
        QCOMPARE(actions.contains("subscribe"), expectedActive || !subscribeAck);
        // No order commands or real Wind process are used by this fixture.
        QVERIFY(!engine.snapshot().value("health").toObject().value("live_orders_allowed").toBool());
        engine.stop();
    }

    void dualQmtBackendsStartUnsyncedAndIndependent() {
        QTemporaryDir root;
        QJsonObject settings = context(root.path()).settings;
        settings.insert(QStringLiteral("qmt_backends"), QJsonArray{
            QJsonObject{{QStringLiteral("id"), QStringLiteral("QMT1")},
                        {QStringLiteral("host"), QStringLiteral("127.0.0.1")},
                        {QStringLiteral("port"), 65533}},
            QJsonObject{{QStringLiteral("id"), QStringLiteral("QMT2")},
                        {QStringLiteral("host"), QStringLiteral("127.0.0.1")},
                        {QStringLiteral("port"), 65534}}});
        RedemptionEngine engine;
        engine.initialize({QStringLiteral("redemption"), root.path(), settings, true});
        const QJsonObject backends = engine.snapshot()
                                             .value(QStringLiteral("qmt_backends"))
                                             .toObject();
        QCOMPARE(backends.size(), 2);
        QVERIFY(!backends.value(QStringLiteral("QMT1")).toObject()
                      .value(QStringLiteral("ready")).toBool());
        QVERIFY(!backends.value(QStringLiteral("QMT2")).toObject()
                      .value(QStringLiteral("ready")).toBool());
    }

    void compatibilityApiIsReadOnly() {
        QTemporaryDir root;
        QJsonObject settings = context(root.path()).settings;
        settings.insert(QStringLiteral("compatibility_api_enabled"), true);
        settings.insert(QStringLiteral("compatibility_port"), 0);
        RedemptionEngine engine;
        engine.setNowForTest(shanghai(16, 0));
        engine.initialize({QStringLiteral("redemption"), root.path(), settings, true});
        engine.start();
        QVERIFY(engine.compatibilityPortForTest() > 0);

        QTcpSocket get;
        get.connectToHost(QHostAddress::LocalHost, engine.compatibilityPortForTest());
        QTRY_COMPARE(get.state(), QAbstractSocket::ConnectedState);
        get.write("GET /api/v1/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
        get.flush();
        QTRY_VERIFY(get.bytesAvailable() > 0 || get.state() == QAbstractSocket::UnconnectedState);
        const QByteArray getReply = get.readAll();
        QVERIFY(getReply.startsWith("HTTP/1.1 200 OK"));

        QTcpSocket post;
        post.connectToHost(QHostAddress::LocalHost, engine.compatibilityPortForTest());
        QTRY_COMPARE(post.state(), QAbstractSocket::ConnectedState);
        post.write("POST /api/v1/monitor/start HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n");
        post.flush();
        QTRY_VERIFY(post.bytesAvailable() > 0 || post.state() == QAbstractSocket::UnconnectedState);
        QVERIFY(post.readAll().startsWith("HTTP/1.1 403 Forbidden"));
        engine.stop();
    }

    void liveOrderHasIndependentCompileAndConfigGates() {
        QTemporaryDir root;
        RedemptionEngine engine;
        engine.initialize(context(root.path()));
        QSignalSpy finished(&engine, &hub::IModuleEngine::commandFinished);
        engine.submitCommand(QStringLiteral("redemption_qmt_order"),
                             {{QStringLiteral("backend"), QStringLiteral("QMT1")},
                              {QStringLiteral("symbol"), QStringLiteral("159518")},
                              {QStringLiteral("side"), QStringLiteral("PURCHASE")}},
                             QStringLiteral("order-1"));
        QCOMPARE(finished.count(), 1);
        QVERIFY(!finished.takeFirst().at(1).toBool());
    }
};

QTEST_MAIN(RedemptionEngineTest)
#include "tst_redemption_engine.moc"
