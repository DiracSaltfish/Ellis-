#include "client/ClientSettings.h"
#include "client/QmtClient.h"

#include <QDateTime>
#include <QTcpServer>
#include <QTemporaryDir>
#include <QtTest>

namespace premium {

class QmtClientTests final : public QObject {
    Q_OBJECT

private Q_SLOTS:
    void parsesBackend101FullAndDelta()
    {
        QmtClient client({QStringLiteral("TEST"), QStringLiteral("127.0.0.1"), 1});
        client.handleMessage({{"type", "positions_data"}, {"sync_mode", "full"},
                              {"data", QJsonArray{QJsonObject{{"code", "159518.SZ"}, {"available", 180000}}}}});
        QCOMPARE(client.availableQuantity(QStringLiteral("159518.SZ")), 180000);

        client.handleMessage({{"type", "orders_data"}, {"sync_mode", "full"},
                              {"data", QJsonArray{QJsonObject{{"code", "159518.SZ"}, {"order_id", "A1"},
                                                               {"status", "部分成交"}, {"qty", 100000},
                                                               {"traded_qty", 20000}}}}});
        QCOMPARE(client.ordersFor(QStringLiteral("159518.SZ")).size(), 1);

        client.handleMessage({{"type", "orders_data"}, {"sync_mode", "delta"},
                              {"upserts", QJsonArray{QJsonObject{{"code", "159518.SZ"}, {"order_id", "A1"},
                                                                  {"status", "部分成交"}, {"qty", 100000},
                                                                  {"traded_qty", 50000}}}},
                              {"remove_ids", QJsonArray{}}});
        const QJsonObject updated = client.ordersFor(QStringLiteral("159518.SZ")).at(0).toObject();
        QCOMPARE(updated.value(QStringLiteral("traded_qty")).toInt(), 50000);

        client.handleMessage({{"type", "orders_data"}, {"sync_mode", "delta"},
                              {"upserts", QJsonArray{}}, {"remove_ids", QJsonArray{"A1"}}});
        QVERIFY(client.ordersFor(QStringLiteral("159518.SZ")).isEmpty());
    }

    void explicitBackendResultUnlocks()
    {
        QmtClient client({QStringLiteral("TEST"), QStringLiteral("127.0.0.1"), 1});
        client.lockedUntilMs_.insert(QStringLiteral("159518.SZ|SELL"), QDateTime::currentMSecsSinceEpoch() + 10000);
        client.requestLocks_.insert(QStringLiteral("request-1"), QStringLiteral("159518.SZ|SELL"));
        client.handleMessage({{"type", "order_result"}, {"success", true},
                              {"client_order_id", "request-1"}, {"message", "订单已提交"}});
        QVERIFY(!client.lockedUntilMs_.contains(QStringLiteral("159518.SZ|SELL")));
        QVERIFY(!client.requestLocks_.contains(QStringLiteral("request-1")));
    }

    void readOnlyModeRejectsEveryMutation()
    {
        QmtClient client({QStringLiteral("TEST"), QStringLiteral("127.0.0.1"), 1}, false);
        QVERIFY(!client.sendEtf(QStringLiteral("159518.SZ"), QStringLiteral("PURCHASE")));
        QVERIFY(!client.sendEtf(QStringLiteral("159518.SZ"), QStringLiteral("REDEEM")));
        QVERIFY(!client.sendSell(QStringLiteral("159518.SZ"), 100000, 1234000));
        QVERIFY(!client.cancelOrder(QStringLiteral("ORDER-1")));
        QVERIFY(client.lockedUntilMs_.isEmpty());
        QVERIFY(client.requestLocks_.isEmpty());
    }

    void destroyingConnectedClientDoesNotUseDestroyedReconnectTimer()
    {
        QTcpServer server;
        QVERIFY(server.listen(QHostAddress::LocalHost, 0));
        for (int iteration = 0; iteration < 10; ++iteration) {
            auto *client = new QmtClient({QStringLiteral("TEST"), QStringLiteral("127.0.0.1"), server.serverPort()});
            client->connectBackend();
            QTRY_VERIFY_WITH_TIMEOUT(client->isConnected(), 1'000);
            delete client;
            QCoreApplication::processEvents();
        }
    }

    void clientSettingsRoundTrip()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QString path = directory.filePath(QStringLiteral("client-settings.json"));
        ClientSettings input;
        input.serverBase = QUrl(QStringLiteral("ws://192.168.1.23:18421"));
        input.profiles = {{QStringLiteral("QMT1"), QStringLiteral("192.168.1.112"), 9527},
                          {QStringLiteral("QMT2"), QStringLiteral("192.168.1.113"), 9528}};
        input.soundEnabled = false;
        input.alertSoundPreset = QStringLiteral("rising");
        input.alertSoundRepeat = 3;
        input.popupEnabled = true;
        input.summaryRefreshMs = 500;
        QString error;
        QVERIFY2(saveClientSettings(path, input, &error), qPrintable(error));

        ClientSettings output;
        QVERIFY2(loadClientSettings(path, &output, &error), qPrintable(error));
        QCOMPARE(output.serverBase, input.serverBase);
        QCOMPARE(output.profiles.size(), 2);
        QCOMPARE(output.profiles.at(0).host, QStringLiteral("192.168.1.112"));
        QCOMPARE(output.profiles.at(1).port, static_cast<quint16>(9528));
        QVERIFY(!output.soundEnabled);
        QCOMPARE(output.alertSoundPreset, QStringLiteral("rising"));
        QCOMPARE(output.alertSoundRepeat, 3);
        QVERIFY(output.popupEnabled);
        QCOMPARE(output.summaryRefreshMs, 500);
    }

    void clientSettingsUsePublishedDefaults()
    {
        const ClientSettings settings;
        QCOMPARE(settings.serverBase, QUrl(QStringLiteral("ws://192.168.1.113:8421")));
    }
};

} // namespace premium

QTEST_GUILESS_MAIN(premium::QmtClientTests)

#include "test_qmt_client.moc"
