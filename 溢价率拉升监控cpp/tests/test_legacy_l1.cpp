#include "server/LegacyL1Server.h"

#include <QHostAddress>
#include <QElapsedTimer>
#include <QJsonArray>
#include <QJsonDocument>
#include <QTcpSocket>
#include <QtTest>

#include <algorithm>

using namespace premium;

namespace {

QJsonObject readObject(QTcpSocket &socket, QByteArray &buffer, int timeoutMs = 2'000)
{
    QElapsedTimer timer;
    timer.start();
    while (timer.elapsed() < timeoutMs) {
        const qsizetype newline = buffer.indexOf('\n');
        if (newline >= 0) {
            const QByteArray line = buffer.left(newline);
            buffer.remove(0, newline + 1);
            return QJsonDocument::fromJson(line).object();
        }
        socket.waitForReadyRead(std::min(100, timeoutMs - static_cast<int>(timer.elapsed())));
        buffer += socket.readAll();
        QCoreApplication::processEvents();
    }
    return {};
}

QJsonObject readType(QTcpSocket &socket, QByteArray &buffer, const QString &type)
{
    for (int index = 0; index < 12; ++index) {
        const QJsonObject object = readObject(socket, buffer);
        if (object.value(QStringLiteral("t")).toString() == type) return object;
    }
    return {};
}

} // namespace

class LegacyL1Tests final : public QObject {
    Q_OBJECT

private Q_SLOTS:
    void oldV1AndExactHktShareOneCompatibleEndpoint()
    {
        LegacyL1Server::Limits limits;
        limits.maxClients = 4;
        limits.maxSymbolsPerClient = 8;
        limits.maxMaintainedSymbols = 8;
        limits.unsubscribeGraceSeconds = 1;
        LegacyL1Server server({QStringLiteral("159866.SZ")}, {QStringLiteral("02800.HK")}, limits);
        QString error;
        QVERIFY2(server.listen(QHostAddress::LocalHost, 0, &error), qPrintable(error));

        QSignalSpy desiredSpy(&server, &LegacyL1Server::desiredSymbolsChanged);
        QTcpSocket client;
        client.connectToHost(QHostAddress::LocalHost, server.listeningPort());
        QVERIFY(client.waitForConnected(2'000));
        QByteArray buffer;
        const QJsonObject hello = readType(client, buffer, QStringLiteral("hello"));
        QCOMPARE(hello.value(QStringLiteral("v")).toInt(), 1);
        QVERIFY(hello.value(QStringLiteral("defaults")).toArray().contains(QStringLiteral("159866.SZ")));
        QVERIFY(!hello.value(QStringLiteral("defaults")).toArray().contains(QStringLiteral("02800.HK")));

        const QJsonObject request{{"v", 1}, {"t", "subscribe"}, {"id", "mixed"},
                                  {"symbols", QJsonArray{"159866.SZ", "02800.HK", "2800.HK", "513520.SH"}},
                                  {"interval_ms", 0}};
        client.write(QJsonDocument(request).toJson(QJsonDocument::Compact) + '\n');
        client.flush();
        const QJsonObject ack = readType(client, buffer, QStringLiteral("ack"));
        QCOMPARE(ack.value(QStringLiteral("op")).toString(), QStringLiteral("subscribe"));
        const QJsonArray accepted = ack.value(QStringLiteral("symbols")).toArray();
        QVERIFY(accepted.contains(QStringLiteral("159866.SZ")));
        QVERIFY(accepted.contains(QStringLiteral("02800.HK")));
        QVERIFY(accepted.contains(QStringLiteral("513520.SH")));
        QCOMPARE(ack.value(QStringLiteral("rejected")).toArray().size(), 1);
        QCOMPARE(ack.value(QStringLiteral("rejected")).toArray().first().toObject()
                     .value(QStringLiteral("s")).toString(), QStringLiteral("2800.HK"));
        QCOMPARE(ack.value(QStringLiteral("temporary")).toArray(), QJsonArray{QStringLiteral("513520.SH")});

        QuoteSnapshot hkt;
        hkt.symbol = QStringLiteral("02800.HK");
        hkt.origTime = 20'260'827'154'936'000LL;
        hkt.receiveWallNs = QDateTime::currentMSecsSinceEpoch() * 1'000'000;
        hkt.lastPriceE6 = 26'080'000;
        hkt.preClosePriceE6 = 26'140'000;
        hkt.bidPricesE6[0] = 26'080'000;
        hkt.askPricesE6[0] = 26'100'000;
        hkt.bidVolumesE2[0] = 1'617'100'000;
        hkt.askVolumesE2[0] = 938'450'000;
        hkt.totalVolumeE2 = 74'786'891'300LL;
        hkt.totalAmountE5 = 1'952'183'035'644'000LL;
        hkt.levelCount = 5;
        server.publish(hkt);
        QJsonObject l1;
        for (int attempt = 0; attempt < 8; ++attempt) {
            const QJsonObject candidate = readType(client, buffer, QStringLiteral("l1"));
            if (!candidate.value(QStringLiteral("books")).toArray().isEmpty()) {
                l1 = candidate;
                break;
            }
        }
        QVERIFY(!l1.isEmpty());
        const QJsonObject book = l1.value(QStringLiteral("books")).toArray().first().toObject();
        QCOMPARE(book.value(QStringLiteral("s")).toString(), QStringLiteral("02800.HK"));
        QCOMPARE(book.value(QStringLiteral("lp")).toDouble(), 26.08);
        QCOMPARE(book.value(QStringLiteral("ap")).toArray().size(), 5);
        QCOMPARE(book.value(QStringLiteral("bp")).toArray().size(), 5);
        QVERIFY(!book.contains(QStringLiteral("iopv")));

        client.write(QByteArrayLiteral("{\"v\":1,\"t\":\"status\",\"id\":\"s\"}\n"));
        client.flush();
        const QJsonObject status = readType(client, buffer, QStringLiteral("status"));
        QVERIFY(status.value(QStringLiteral("hot")).toArray().contains(QStringLiteral("02800.HK")));
        QVERIFY(status.value(QStringLiteral("pinned")).toArray().contains(QStringLiteral("02800.HK")));
        QVERIFY(status.value(QStringLiteral("maintained")).toArray().contains(QStringLiteral("513520.SH")));
        QVERIFY(desiredSpy.count() >= 1);
        client.disconnectFromHost();
        if (client.state() != QAbstractSocket::UnconnectedState) client.waitForDisconnected(1'000);
        QCoreApplication::processEvents();
    }

    void releasedDynamicSymbolDoesNotRetainAnUnboundedStaleCache()
    {
        LegacyL1Server::Limits limits;
        limits.maxClients = 2;
        limits.maxSymbolsPerClient = 4;
        limits.maxMaintainedSymbols = 4;
        limits.unsubscribeGraceSeconds = 0;
        LegacyL1Server server({QStringLiteral("159866.SZ")}, {}, limits);
        QString error;
        QVERIFY2(server.listen(QHostAddress::LocalHost, 0, &error), qPrintable(error));

        QTcpSocket client;
        client.connectToHost(QHostAddress::LocalHost, server.listeningPort());
        QVERIFY(client.waitForConnected(2'000));
        QByteArray buffer;
        QVERIFY(!readType(client, buffer, QStringLiteral("hello")).isEmpty());

        client.write(QByteArrayLiteral(
            "{\"v\":1,\"t\":\"subscribe\",\"id\":\"first\",\"symbols\":[\"513520.SH\"]}\n"));
        client.flush();
        QVERIFY(!readType(client, buffer, QStringLiteral("ack")).isEmpty());
        const QJsonObject firstInitial = readType(client, buffer, QStringLiteral("l1"));
        QVERIFY(firstInitial.value(QStringLiteral("missing")).toArray().contains(QStringLiteral("513520.SH")));

        QuoteSnapshot snapshot;
        snapshot.symbol = QStringLiteral("513520.SH");
        snapshot.lastPriceE6 = 1'234'000;
        snapshot.bidPricesE6[0] = 1'233'000;
        snapshot.askPricesE6[0] = 1'234'000;
        snapshot.levelCount = 5;
        server.publish(snapshot);
        const QJsonObject live = readType(client, buffer, QStringLiteral("l1"));
        QCOMPARE(live.value(QStringLiteral("books")).toArray().first().toObject()
                     .value(QStringLiteral("s")).toString(), QStringLiteral("513520.SH"));

        client.write(QByteArrayLiteral(
            "{\"v\":1,\"t\":\"unsubscribe\",\"id\":\"drop\",\"symbols\":[\"513520.SH\"]}\n"));
        client.flush();
        QVERIFY(!readType(client, buffer, QStringLiteral("ack")).isEmpty());
        QTest::qWait(1'200);

        client.write(QByteArrayLiteral(
            "{\"v\":1,\"t\":\"subscribe\",\"id\":\"second\",\"symbols\":[\"513520.SH\"]}\n"));
        client.flush();
        QVERIFY(!readType(client, buffer, QStringLiteral("ack")).isEmpty());
        const QJsonObject secondInitial = readType(client, buffer, QStringLiteral("l1"));
        QVERIFY(secondInitial.value(QStringLiteral("books")).toArray().isEmpty());
        QVERIFY(secondInitial.value(QStringLiteral("missing")).toArray().contains(QStringLiteral("513520.SH")));

        client.disconnectFromHost();
        if (client.state() != QAbstractSocket::UnconnectedState) client.waitForDisconnected(1'000);
        QCoreApplication::processEvents();
    }

    void reconnectInvalidationNeverServesAnOldInitialFrame()
    {
        LegacyL1Server::Limits limits;
        limits.maxClients = 3;
        limits.maxSymbolsPerClient = 4;
        limits.maxMaintainedSymbols = 4;
        LegacyL1Server server({QStringLiteral("159866.SZ")}, {}, limits);
        QString error;
        QVERIFY2(server.listen(QHostAddress::LocalHost, 0, &error), qPrintable(error));

        QuoteSnapshot oldSnapshot;
        oldSnapshot.symbol = QStringLiteral("159866.SZ");
        oldSnapshot.lastPriceE6 = 1'000'000;
        oldSnapshot.bidPricesE6[0] = 999'000;
        oldSnapshot.askPricesE6[0] = 1'001'000;
        oldSnapshot.levelCount = 5;
        server.setMarketOnline(true);
        server.publish(oldSnapshot);
        server.setMarketOnline(false);

        QTcpSocket client;
        client.connectToHost(QHostAddress::LocalHost, server.listeningPort());
        QVERIFY(client.waitForConnected(2'000));
        QByteArray buffer;
        QVERIFY(!readType(client, buffer, QStringLiteral("hello")).isEmpty());
        client.write(QByteArrayLiteral(
            "{\"v\":1,\"t\":\"subscribe\",\"symbols\":[\"159866.SZ\"],\"interval_ms\":60000}\n"));
        client.flush();
        QVERIFY(!readType(client, buffer, QStringLiteral("ack")).isEmpty());
        const QJsonObject staleInitial = readType(client, buffer, QStringLiteral("l1"));
        QVERIFY(staleInitial.value(QStringLiteral("books")).toArray().isEmpty());
        QVERIFY(staleInitial.value(QStringLiteral("missing")).toArray().contains(QStringLiteral("159866.SZ")));

        QuoteSnapshot freshSnapshot = oldSnapshot;
        freshSnapshot.lastPriceE6 = 1'100'000;
        freshSnapshot.bidPricesE6[0] = 1'099'000;
        freshSnapshot.askPricesE6[0] = 1'101'000;
        server.setMarketOnline(true);
        server.publish(freshSnapshot);
        const QJsonObject fresh = readType(client, buffer, QStringLiteral("l1"));
        QCOMPARE(fresh.value(QStringLiteral("books")).toArray().first().toObject()
                     .value(QStringLiteral("lp")).toDouble(), 1.1);

        client.disconnectFromHost();
        if (client.state() != QAbstractSocket::UnconnectedState) client.waitForDisconnected(1'000);
        QCoreApplication::processEvents();
    }
};

QTEST_MAIN(LegacyL1Tests)
#include "test_legacy_l1.moc"
