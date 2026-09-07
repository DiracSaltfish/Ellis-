#include "modules/premium/engine/PremiumAEngine.h"
#include "modules/premium/PremiumClient.h"
#include "modules/premium/engine/common/MarketTypes.h"

#include <QDir>
#include <QFile>
#include <QHostAddress>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTcpServer>
#include <QTemporaryDir>
#include <QtTest>

using machome::premium::engine::PremiumAEngine;

namespace {
quint16 reserveLocalPort()
{
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return 0;
    return server.serverPort();
}
} // namespace

class PremiumAEngineTests final : public QObject {
    Q_OBJECT
private Q_SLOTS:
    void nativeDetailForwardsCompactSymbolOnlyWhileSubscribed() {
        QTemporaryDir root;
        PremiumAEngine engine;
        engine.initialize({"premium", root.path(), {{"summary_port", 0}, {"l1_port", 0}, {"replay", true}}, true});
        engine.start();
        QSignalSpy events(&engine, &PremiumAEngine::eventReady);
        engine.submitCommand("premium_detail_subscribe", {{"symbol", "159217.SZ"}}, "detail-readonly");
        events.clear();
        machome::premium::engine::QuoteSnapshot quote;
        quote.symbol = "159217.SZ";
        quote.lastPriceE6 = 1234000;
        QJsonObject wire = quote.toDetailJson();
        QVERIFY(wire.contains("s"));
        QVERIFY(!wire.contains("symbol"));
        QVERIFY(QMetaObject::invokeMethod(&engine, "forwardNativeDetail", Qt::DirectConnection, Q_ARG(QJsonObject, wire)));
        QCOMPARE(events.size(), 1);
        QCOMPARE(events[0][0].toString(), QStringLiteral("premium.detail"));
        QCOMPARE(events[0][1].toJsonObject().value("last_price_e6").toInteger(), qint64(1234000));
        wire.insert("s", "510300.SH");
        QVERIFY(QMetaObject::invokeMethod(&engine, "forwardNativeDetail", Qt::DirectConnection, Q_ARG(QJsonObject, wire)));
        QCOMPARE(events.size(), 1);
        engine.submitCommand("premium_detail_unsubscribe", {{"symbol", "159217.SZ"}}, "detail-stop");
        events.clear(); wire.insert("s", "159217.SZ");
        QVERIFY(QMetaObject::invokeMethod(&engine, "forwardNativeDetail", Qt::DirectConnection, Q_ARG(QJsonObject, wire)));
        QVERIFY(events.isEmpty());
        engine.stop();
    }

    void editedListsAndNamesSurviveFreshEngine() {
        QTemporaryDir root;
        const hub::ModuleContext ctx{"premium",root.path(),{{"summary_port",0},{"l1_port",0},
            {"watchlist",QJsonArray{"159217.SZ"}},{"security_names",QJsonObject{{"159217.SZ","测试基金"}}}},true};
        {
            PremiumAEngine engine;engine.initialize(ctx);engine.start();
            QSignalSpy result(&engine,&PremiumAEngine::commandFinished);
            engine.submitCommand("premium_set_watchlist",{{"symbols",QJsonArray{"510300.SH"}}},"watch");
            QVERIFY(result.last().at(1).toBool());
            engine.submitCommand("premium_set_l1_hotlist",{{"symbols",QJsonArray{"00001.HK"}}},"hot");
            QVERIFY(result.last().at(1).toBool());
            engine.stop();engine.start();
            QCOMPARE(engine.snapshot().value("watchlist").toArray(),QJsonArray{"510300.SH"});
        }
        PremiumAEngine engine;engine.initialize(ctx);engine.start();
        QCOMPARE(engine.snapshot().value("watchlist").toArray(),QJsonArray{"510300.SH"});
        QCOMPARE(engine.snapshot().value("l1_hotlist").toArray(),QJsonArray{"00001.HK"});
        QFile names(root.filePath("config/security_names.tsv"));QVERIFY(names.open(QIODevice::ReadOnly));
        QVERIFY(names.readAll().contains(QStringLiteral("测试基金").toUtf8()));
        engine.stop();
        QFile broken(root.filePath("config/watchlist.json"));QVERIFY(broken.open(QIODevice::WriteOnly));
        broken.write("broken-json");broken.close();engine.start();
        QVERIFY(!engine.snapshot().value("running").toBool());
        QVERIFY(broken.open(QIODevice::ReadOnly));QCOMPARE(broken.readAll(),QByteArray("broken-json"));
    }
    void helperCrashRecoveryTripsBreakerAndStopDoesNotRevive() {
        QTemporaryDir root("/private/tmp/premium-recovery-XXXXXX");
        QFile fake(root.filePath("fake-helper"));QVERIFY(fake.open(QIODevice::WriteOnly));
        fake.write("#!/bin/sh\nexit 7\n");fake.close();
        QVERIFY(fake.setPermissions(QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner));
        PremiumAEngine engine;
        engine.initialize({"premium",root.path(),{{"summary_port",0},{"l1_port",0},
            {"tgw_helper_enabled",true},{"test_mode",true},{"test_tgw_helper",fake.fileName()},
            {"test_helper_restart_ms",10}},true});engine.start();
        QTRY_VERIFY_WITH_TIMEOUT(engine.snapshot().value("tgw_helper").toObject().value("fault").toBool(),5000);
        QCOMPARE(engine.snapshot().value("tgw_helper").toObject().value("restarts_in_window").toInt(),3);
        engine.stop();QTest::qWait(100);
        QVERIFY(!engine.snapshot().value("running").toBool());
    }

    void startsSelfContainedAndRejectsTrading()
    {
        QTemporaryDir root;
        QVERIFY(root.isValid());
        PremiumAEngine engine;
        QSignalSpy snapshots(&engine, &PremiumAEngine::snapshotReady);
        QSignalSpy commands(&engine, &PremiumAEngine::commandFinished);
        engine.initialize(hub::ModuleContext{
            QStringLiteral("premium"), root.path(),
            QJsonObject{{QStringLiteral("summary_port"), 0},
                        {QStringLiteral("l1_port"), 0},
                        {QStringLiteral("mode"), QStringLiteral("replay")},
                        {QStringLiteral("watchlist"),
                         QJsonArray{QStringLiteral("159217.SZ")}}},
            true});
        engine.start();
        QTRY_VERIFY_WITH_TIMEOUT(!snapshots.isEmpty(), 3000);
        const QJsonObject started = snapshots.last().at(0).toJsonObject();
        QVERIFY2(started.value(QStringLiteral("running")).toBool(),
                 qPrintable(started.value(QStringLiteral("last_error")).toString()));
        QCOMPARE(started.value(QStringLiteral("engine")).toString(),
                 QStringLiteral("native_premium_a"));
        QCOMPARE(started.value(QStringLiteral("b_side_trading_supported")).toBool(), false);
        QVERIFY(QFileInfo::exists(root.filePath(QStringLiteral("config/app.json"))));
        QVERIFY(QFileInfo::exists(root.filePath(QStringLiteral("runtime/tgw.sock"))));

        engine.submitCommand(QStringLiteral("premium_raw_snapshot"), {},
                             QStringLiteral("raw-1"));
        QTRY_COMPARE_WITH_TIMEOUT(commands.size(), 1, 1000);
        QVERIFY(commands.at(0).at(1).toBool());
        QCOMPARE(commands.at(0).at(3).toJsonObject()
                     .value(QStringLiteral("available")).toBool(), false);

        engine.submitCommand(QStringLiteral("place_order"),
                             {{QStringLiteral("symbol"), QStringLiteral("159217.SZ")}},
                             QStringLiteral("forbidden-1"));
        QTRY_COMPARE_WITH_TIMEOUT(commands.size(), 2, 1000);
        QCOMPARE(commands.at(1).at(0).toString(), QStringLiteral("forbidden-1"));
        QCOMPARE(commands.at(1).at(1).toBool(), false);
        QCOMPARE(commands.at(1).at(3).toJsonObject()
                     .value(QStringLiteral("code")).toString(),
                 QStringLiteral("b_side_command_forbidden"));
        engine.stop();
    }

    void nativeDetailLimitIsFour()
    {
        QTemporaryDir root;
        PremiumAEngine engine;
        QSignalSpy commands(&engine, &PremiumAEngine::commandFinished);
        engine.initialize(hub::ModuleContext{
            QStringLiteral("premium"), root.path(),
            QJsonObject{{QStringLiteral("summary_port"), 0},
                        {QStringLiteral("l1_port"), 0},
                        {QStringLiteral("mode"), QStringLiteral("replay")}},
            true});
        engine.start();
        for (int index = 0; index < 5; ++index) {
            engine.submitCommand(
                QStringLiteral("premium_detail_subscribe"),
                {{QStringLiteral("symbol"),
                  QStringLiteral("159%1.SZ").arg(index + 100, 3, 10, QLatin1Char('0'))}},
                QStringLiteral("detail-%1").arg(index));
        }
        QTRY_COMPARE_WITH_TIMEOUT(commands.size(), 5, 2000);
        for (int index = 0; index < 4; ++index) QVERIFY(commands.at(index).at(1).toBool());
        QVERIFY(!commands.at(4).at(1).toBool());
        engine.stop();
    }

    void existingBClientContractsRemainCompatible()
    {
        QTemporaryDir root;
        QVERIFY(root.isValid());
        const quint16 summaryPort = reserveLocalPort();
        const quint16 l1Port = reserveLocalPort();
        QVERIFY(summaryPort != 0);
        QVERIFY(l1Port != 0);
        QVERIFY(summaryPort != l1Port);

        PremiumAEngine engine;
        engine.initialize(hub::ModuleContext{
            QStringLiteral("premium"), root.path(),
            QJsonObject{{QStringLiteral("summary_port"), summaryPort},
                        {QStringLiteral("l1_port"), l1Port},
                        {QStringLiteral("listen_host"), QStringLiteral("127.0.0.1")},
                        {QStringLiteral("replay"), true},
                        {QStringLiteral("watchlist"),
                         QJsonArray{QStringLiteral("159217.SZ")}}},
            true});
        engine.start();

        machome::premium::PremiumClient::Config clientConfig;
        clientConfig.host = QStringLiteral("127.0.0.1");
        clientConfig.summaryPort = summaryPort;
        clientConfig.l1Port = l1Port;
        clientConfig.connectTimeoutMs = 1000;
        clientConfig.reconnectInitialMs = 100;
        clientConfig.reconnectMaximumMs = 500;
        clientConfig.l1PingIntervalMs = 500;
        clientConfig.l1StatusIntervalMs = 500;
        machome::premium::PremiumClient client(clientConfig);
        QSignalSpy syncComplete(&client, &machome::premium::PremiumClient::syncCompleteReceived);
        QSignalSpy l1Hello(&client, &machome::premium::PremiumClient::l1HelloReceived);
        QSignalSpy protocolErrors(&client, &machome::premium::PremiumClient::protocolError);
        client.start();

        QTRY_VERIFY_WITH_TIMEOUT(!syncComplete.isEmpty(), 3000);
        QTRY_VERIFY_WITH_TIMEOUT(!l1Hello.isEmpty(), 3000);
        QCOMPARE(protocolErrors.size(), 0);
        QCOMPARE(l1Hello.last().at(0).toJsonObject()
                     .value(QStringLiteral("v")).toInt(), 1);

        client.stop();
        engine.stop();
    }

    void weekendTestOnlyForcesQuotesAndDefaultsBackToWork()
    {
        QTemporaryDir root;
        QVERIFY(root.isValid());
        PremiumAEngine engine;
        QSignalSpy snapshots(&engine, &PremiumAEngine::snapshotReady);
        QSignalSpy commands(&engine, &PremiumAEngine::commandFinished);
        engine.initialize(hub::ModuleContext{
            QStringLiteral("premium"), root.path(),
            QJsonObject{{QStringLiteral("summary_port"), 0},
                        {QStringLiteral("l1_port"), 0},
                        {QStringLiteral("watchlist"),
                         QJsonArray{QStringLiteral("159217.SZ")}}},
            true});
        engine.start();
        QTRY_VERIFY_WITH_TIMEOUT(!snapshots.isEmpty(), 2000);
        QCOMPARE(snapshots.last().at(0).toJsonObject()
                     .value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("work"));

        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("weekend_test")}},
                             QStringLiteral("weekend-mode-1"));
        QTRY_COMPARE_WITH_TIMEOUT(commands.size(), 1, 1000);
        QVERIFY(commands.last().at(1).toBool());
        QVERIFY(commands.last().at(3).toJsonObject()
                    .value(QStringLiteral("signal_schedule_preserved")).toBool());
        QTRY_COMPARE_WITH_TIMEOUT(
            snapshots.last().at(0).toJsonObject()
                .value(QStringLiteral("operating_mode")).toString(),
            QStringLiteral("weekend_test"), 1000);
        QCOMPARE(snapshots.last().at(0).toJsonObject()
                     .value(QStringLiteral("status")).toObject()
                     .value(QStringLiteral("force_quotes")).toBool(), true);

        engine.stop();
        engine.initialize(hub::ModuleContext{
            QStringLiteral("premium"), root.path(),
            QJsonObject{{QStringLiteral("summary_port"), 0},
                        {QStringLiteral("l1_port"), 0},
                        {QStringLiteral("watchlist"),
                         QJsonArray{QStringLiteral("159217.SZ")}}},
            true});
        engine.start();
        QTRY_COMPARE_WITH_TIMEOUT(
            snapshots.last().at(0).toJsonObject()
                .value(QStringLiteral("operating_mode")).toString(),
            QStringLiteral("work"), 1000);
        engine.stop();
    }

    void nativeListMutationPersistsAndPublishesAuthoritativeState()
    {
        QTemporaryDir root;
        PremiumAEngine engine;
        QSignalSpy commands(&engine, &PremiumAEngine::commandFinished);
        QSignalSpy snapshots(&engine, &PremiumAEngine::snapshotReady);
        QSignalSpy events(&engine, &PremiumAEngine::eventReady);
        engine.initialize(hub::ModuleContext{
            QStringLiteral("premium"), root.path(),
            QJsonObject{{QStringLiteral("summary_port"), 0},
                        {QStringLiteral("l1_port"), 0},
                        {QStringLiteral("replay"), true}},
            true});
        engine.start();
        engine.submitCommand(
            QStringLiteral("premium_set_watchlist"),
            {{QStringLiteral("symbols"),
              QJsonArray{QStringLiteral("510300.SH"), QStringLiteral("159915.SZ")}}},
            QStringLiteral("watchlist-1"));
        QTRY_VERIFY_WITH_TIMEOUT(!commands.isEmpty(), 1000);
        QVERIFY(commands.last().at(1).toBool());
        QTRY_VERIFY_WITH_TIMEOUT(!snapshots.isEmpty(), 1000);
        const QJsonArray watchlist = snapshots.last().at(0).toJsonObject()
                                         .value(QStringLiteral("watchlist")).toArray();
        QCOMPARE(watchlist,
                 QJsonArray({QStringLiteral("510300.SH"), QStringLiteral("159915.SZ")}));

        QFile persisted(root.filePath(QStringLiteral("config/watchlist.json")));
        QVERIFY(persisted.open(QIODevice::ReadOnly));
        QCOMPARE(QJsonDocument::fromJson(persisted.readAll()).object()
                     .value(QStringLiteral("symbols")).toArray(),
                 watchlist);
        bool removalSeen = false;
        for (const auto &event : events) {
            if (event.at(0).toString() == QStringLiteral("premium.symbol_removed")
                && event.at(1).toJsonObject().value(QStringLiteral("symbol")).toString()
                    == QStringLiteral("159217.SZ")) {
                removalSeen = true;
            }
        }
        QVERIFY(removalSeen);
        engine.stop();
    }
};

QTEST_MAIN(PremiumAEngineTests)
#include "tst_premium_a_engine.moc"
