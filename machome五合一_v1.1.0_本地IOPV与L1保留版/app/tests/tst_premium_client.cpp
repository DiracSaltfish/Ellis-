#include "modules/premium/PremiumClient.h"

#include <QAbstractSocket>
#include <QHash>
#include <QHostAddress>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QSet>
#include <QSignalSpy>
#include <QTcpServer>
#include <QTcpSocket>
#include <QWebSocket>
#include <QWebSocketProtocol>
#include <QWebSocketServer>
#include <QtTest>

using machome::premium::PremiumClient;

namespace {

class FakePremiumServers final : public QObject
{
public:
    explicit FakePremiumServers(QObject *parent = nullptr)
        : QObject(parent)
        , summaryServer_(QStringLiteral("premium-client-test"),
                         QWebSocketServer::NonSecureMode, this)
        , l1Server_(this)
    {
        connect(&summaryServer_, &QWebSocketServer::newConnection,
                this, [this] { acceptWebSocketConnection(); });
        connect(&l1Server_, &QTcpServer::newConnection,
                this, [this] { acceptL1Connection(); });
    }

    ~FakePremiumServers() override
    {
        // QTcpServer/QWebSocketServer own accepted sockets. Disconnect their
        // callbacks before this class's containers are destroyed; otherwise a
        // socket teardown signal could access an already-destroyed QHash.
        const auto descendants = findChildren<QObject *>();
        for (QObject *object : descendants) {
            QObject::disconnect(object, nullptr, this, nullptr);
        }
        if (summaryClient_) {
            summaryClient_->abort();
        }
        if (detailClient_) {
            detailClient_->abort();
        }
        if (l1Client_) {
            l1Client_->abort();
        }
        summaryServer_.close();
        l1Server_.close();
    }

    bool listen()
    {
        if (!summaryServer_.listen(QHostAddress::LocalHost, 0)) {
            return false;
        }
        if (!l1Server_.listen(QHostAddress::LocalHost, 0)) {
            summaryServer_.close();
            return false;
        }
        return true;
    }

    quint16 summaryPort() const { return summaryServer_.serverPort(); }
    quint16 l1Port() const { return l1Server_.serverPort(); }

    int summaryConnectionCount() const { return summaryConnectionCount_; }
    int detailConnectionCount() const { return detailConnectionCount_; }
    int l1ConnectionCount() const { return l1ConnectionCount_; }
    QString lastSummaryPath() const { return lastSummaryPath_; }
    QString lastDetailPath() const { return lastDetailPath_; }

    const QList<QJsonObject> &summaryCommands() const { return summaryCommands_; }
    const QList<QJsonObject> &detailCommands() const { return detailCommands_; }
    const QList<QJsonObject> &l1Commands() const { return l1Commands_; }

    bool hasSummaryOperation(const QString &operation) const
    {
        return findOperation(summaryCommands_, operation) != nullptr;
    }

    bool hasL1Operation(const QString &operation) const
    {
        return findOperation(l1Commands_, operation) != nullptr;
    }

    bool hasDetailOperation(const QString &operation,
                            const QString &symbol = {}) const
    {
        for (const auto &command : detailCommands_) {
            if (command.value(QStringLiteral("op")).toString() != operation) continue;
            if (symbol.isEmpty()
                || command.value(QStringLiteral("symbol")).toString() == symbol) return true;
        }
        return false;
    }

    const QJsonObject *summaryOperation(const QString &operation) const
    {
        return findOperation(summaryCommands_, operation);
    }

    void sendSummary(const QJsonObject &object)
    {
        sendSummaryTo(summaryClient_, object);
    }

    void setRespondToSummaryMutations(bool enabled)
    {
        respondToSummaryMutations_ = enabled;
    }

    void sendDetail(const QJsonObject &object)
    {
        sendSummaryTo(detailClient_, object);
    }

    void abortSummaryClient()
    {
        if (summaryClient_) {
            summaryClient_->abort();
        }
    }

    void abortL1Client()
    {
        if (l1Client_) {
            l1Client_->abort();
        }
    }

    void abortDetailClient()
    {
        if (detailClient_) detailClient_->abort();
    }

private:
    static const QJsonObject *findOperation(const QList<QJsonObject> &commands,
                                            const QString &operation)
    {
        for (const QJsonObject &command : commands) {
            QString candidate = command.value(QStringLiteral("op")).toString();
            if (candidate.isEmpty()) {
                candidate = command.value(QStringLiteral("t")).toString();
            }
            if (candidate == operation) {
                return &command;
            }
        }
        return nullptr;
    }

    static void sendSummaryTo(QWebSocket *socket, const QJsonObject &object)
    {
        if (!socket || socket->state() != QAbstractSocket::ConnectedState) {
            return;
        }
        socket->sendTextMessage(QString::fromUtf8(
            QJsonDocument(object).toJson(QJsonDocument::Compact)));
    }

    void acceptWebSocketConnection()
    {
        while (summaryServer_.hasPendingConnections()) {
            QWebSocket *socket = summaryServer_.nextPendingConnection();
            const QString path = socket->requestUrl().path();
            if (path == QStringLiteral("/ws/v2/detail")) {
                ++detailConnectionCount_;
                detailClient_ = socket;
                lastDetailPath_ = path;
                connect(socket, &QWebSocket::textMessageReceived,
                        this, [this, socket](const QString &text) {
                    const QJsonDocument document = QJsonDocument::fromJson(text.toUtf8());
                    if (!document.isObject()) return;
                    const QJsonObject command = document.object();
                    detailCommands_.append(command);
                    respondToDetailCommand(socket, command);
                });
                connect(socket, &QWebSocket::disconnected, this, [this, socket] {
                    if (detailClient_ == socket) detailClient_.clear();
                    socket->deleteLater();
                });
                sendSummaryTo(socket,
                              {{QStringLiteral("type"), QStringLiteral("hello")},
                               {QStringLiteral("channel"), QStringLiteral("detail")},
                               {QStringLiteral("max_symbols"), 4}});
                continue;
            }
            ++summaryConnectionCount_;
            summaryClient_ = socket;
            lastSummaryPath_ = path;

            connect(socket, &QWebSocket::textMessageReceived,
                    this, [this, socket](const QString &text) {
                const QJsonDocument document = QJsonDocument::fromJson(text.toUtf8());
                if (!document.isObject()) {
                    return;
                }
                const QJsonObject command = document.object();
                summaryCommands_.append(command);
                respondToSummaryCommand(socket, command);
            });
            connect(socket, &QWebSocket::disconnected, this, [this, socket] {
                if (summaryClient_ == socket) {
                    summaryClient_.clear();
                }
                socket->deleteLater();
            });
        }
    }

    void respondToSummaryCommand(QWebSocket *socket, const QJsonObject &command)
    {
        const QString operation = command.value(QStringLiteral("op")).toString();
        if (operation == QStringLiteral("status")) {
            sendSummaryTo(socket,
                          {{QStringLiteral("type"), QStringLiteral("status")},
                           {QStringLiteral("phase"), QStringLiteral("test")},
                           {QStringLiteral("ready_symbols"), 2}});
        } else if (operation == QStringLiteral("sync")) {
            sendSummaryTo(socket,
                          {{QStringLiteral("type"), QStringLiteral("sync_begin")},
                           {QStringLiteral("replay"), false}});
            sendSummaryTo(socket,
                          {{QStringLiteral("type"), QStringLiteral("sync_complete")}});
        } else if (operation == QStringLiteral("raw_snapshot")) {
            sendSummaryTo(socket,
                          {{QStringLiteral("type"), QStringLiteral("raw_snapshot")},
                           {QStringLiteral("available"), true}});
        } else if (operation == QStringLiteral("set_watchlist")) {
            if (!respondToSummaryMutations_) return;
            sendSummaryTo(socket,
                          {{QStringLiteral("type"), QStringLiteral("watchlist_ack")},
                           {QStringLiteral("accepted"), true},
                           {QStringLiteral("symbols"),
                            command.value(QStringLiteral("symbols"))}});
        } else if (operation == QStringLiteral("set_l1_hotlist")) {
            if (!respondToSummaryMutations_) return;
            sendSummaryTo(socket,
                          {{QStringLiteral("type"), QStringLiteral("l1_hotlist_ack")},
                           {QStringLiteral("accepted"), true},
                           {QStringLiteral("symbols"),
                            command.value(QStringLiteral("symbols"))}});
        }
    }

    static QJsonObject detailSnapshot(const QString &symbol, bool cached)
    {
        QJsonArray bidPrices;
        QJsonArray askPrices;
        QJsonArray bidVolumes;
        QJsonArray askVolumes;
        for (int level = 0; level < 10; ++level) {
            bidPrices.append(3'910'000 - level * 1'000);
            askPrices.append(3'911'000 + level * 1'000);
            bidVolumes.append(10'000 + level * 100);
            askVolumes.append(20'000 + level * 100);
        }
        return {{QStringLiteral("type"), QStringLiteral("detail")},
                {QStringLiteral("s"), symbol},
                {QStringLiteral("name"), QStringLiteral("测试 ETF")},
                {QStringLiteral("cached"), cached},
                {QStringLiteral("orig_time"), 20260904093105000LL},
                {QStringLiteral("last_price_e6"), 3'910'000},
                {QStringLiteral("bid1_price_e6"), 3'910'000},
                {QStringLiteral("iopv_e6"), 3'900'000},
                {QStringLiteral("sell_premium_ppm"), 2'564},
                {QStringLiteral("bid_prices_e6"), bidPrices},
                {QStringLiteral("ask_prices_e6"), askPrices},
                {QStringLiteral("bid_volumes_e2"), bidVolumes},
                {QStringLiteral("ask_volumes_e2"), askVolumes},
                {QStringLiteral("level_count"), 10},
                {QStringLiteral("trading_phase"), QStringLiteral("T")}};
    }

    static void respondToDetailCommand(QWebSocket *socket,
                                       const QJsonObject &command)
    {
        const QString operation = command.value(QStringLiteral("op")).toString();
        const QString symbol = command.value(QStringLiteral("symbol")).toString();
        if (operation != QStringLiteral("subscribe")
            && operation != QStringLiteral("unsubscribe")) return;
        sendSummaryTo(socket,
                      {{QStringLiteral("type"), QStringLiteral("detail_ack")},
                       {QStringLiteral("op"), operation},
                       {QStringLiteral("symbol"), symbol}});
        if (operation == QStringLiteral("subscribe")) {
            sendSummaryTo(socket, detailSnapshot(symbol, true));
        }
    }

    void acceptL1Connection()
    {
        while (l1Server_.hasPendingConnections()) {
            QTcpSocket *socket = l1Server_.nextPendingConnection();
            ++l1ConnectionCount_;
            l1Client_ = socket;
            l1Buffers_.insert(socket, {});

            connect(socket, &QTcpSocket::readyRead,
                    this, [this, socket] { readL1Commands(socket); });
            connect(socket, &QTcpSocket::disconnected, this, [this, socket] {
                l1Buffers_.remove(socket);
                if (l1Client_ == socket) {
                    l1Client_.clear();
                }
                socket->deleteLater();
            });

            writeL1(socket,
                    {{QStringLiteral("v"), 1},
                     {QStringLiteral("t"), QStringLiteral("hello")},
                     {QStringLiteral("service"), QStringLiteral("qmt_l1")},
                     {QStringLiteral("client_ping_interval_ms"), 15'000},
                     {QStringLiteral("client_idle_timeout_ms"), 45'000}});
        }
    }

    void readL1Commands(QTcpSocket *socket)
    {
        QByteArray &buffer = l1Buffers_[socket];
        buffer.append(socket->readAll());
        while (true) {
            const qsizetype newline = buffer.indexOf('\n');
            if (newline < 0) {
                return;
            }
            QByteArray line = buffer.left(newline);
            buffer.remove(0, newline + 1);
            if (line.endsWith('\r')) {
                line.chop(1);
            }
            if (line.trimmed().isEmpty()) {
                continue;
            }

            const QJsonDocument document = QJsonDocument::fromJson(line);
            if (!document.isObject()) {
                continue;
            }
            const QJsonObject command = document.object();
            l1Commands_.append(command);
            respondToL1Command(socket, command);
        }
    }

    static void writeL1(QTcpSocket *socket, const QJsonObject &object)
    {
        if (!socket || socket->state() == QAbstractSocket::UnconnectedState) {
            return;
        }
        socket->write(QJsonDocument(object).toJson(QJsonDocument::Compact) + '\n');
    }

    static void respondToL1Command(QTcpSocket *socket, const QJsonObject &command)
    {
        const QString operation = command.value(QStringLiteral("t")).toString();
        QJsonObject response{{QStringLiteral("v"), 1},
                             {QStringLiteral("id"),
                              command.value(QStringLiteral("id"))}};
        if (operation == QStringLiteral("status")) {
            response.insert(QStringLiteral("t"), QStringLiteral("status"));
            response.insert(QStringLiteral("market_online"), true);
            response.insert(QStringLiteral("active_clients"), 1);
        } else if (operation == QStringLiteral("ping")) {
            response.insert(QStringLiteral("t"), QStringLiteral("pong"));
        } else {
            return;
        }
        writeL1(socket, response);
    }

    QWebSocketServer summaryServer_;
    QTcpServer l1Server_;
    QPointer<QWebSocket> summaryClient_;
    QPointer<QWebSocket> detailClient_;
    QPointer<QTcpSocket> l1Client_;
    QHash<QTcpSocket *, QByteArray> l1Buffers_;
    QList<QJsonObject> summaryCommands_;
    QList<QJsonObject> detailCommands_;
    QList<QJsonObject> l1Commands_;
    QString lastSummaryPath_;
    QString lastDetailPath_;
    int summaryConnectionCount_ = 0;
    int detailConnectionCount_ = 0;
    int l1ConnectionCount_ = 0;
    bool respondToSummaryMutations_ = true;
};

PremiumClient::Config clientConfig(const FakePremiumServers &servers)
{
    PremiumClient::Config config;
    config.host = QStringLiteral("127.0.0.1");
    config.summaryPort = servers.summaryPort();
    config.l1Port = servers.l1Port();
    config.connectTimeoutMs = 1'000;
    config.reconnectInitialMs = 50;
    config.reconnectMaximumMs = 200;
    config.freshnessPollMs = 100;
    config.summaryStaleAfterMs = 10'000;
    config.l1StaleAfterMs = 10'000;
    config.silenceDisconnectAfterMs = 20'000;
    config.l1PingIntervalMs = 60'000;
    config.l1StatusIntervalMs = 60'000;
    return config;
}

int operationCount(const QList<QJsonObject> &commands, const QString &operation)
{
    int count = 0;
    for (const QJsonObject &command : commands) {
        QString candidate = command.value(QStringLiteral("op")).toString();
        if (candidate.isEmpty()) {
            candidate = command.value(QStringLiteral("t")).toString();
        }
        if (candidate == operation) {
            ++count;
        }
    }
    return count;
}

} // namespace

class PremiumClientTests final : public QObject
{
    Q_OBJECT

private Q_SLOTS:
    void summaryEventsAndCommands()
    {
        FakePremiumServers servers;
        QVERIFY2(servers.listen(), "failed to bind ephemeral localhost test ports");

        PremiumClient client(clientConfig(servers));
        QSignalSpy statusSpy(&client, &PremiumClient::statusReceived);
        QSignalSpy summarySpy(&client, &PremiumClient::summaryReceived);
        QSignalSpy signalSpy(&client, &PremiumClient::signalReceived);
        QSignalSpy syncBeginSpy(&client, &PremiumClient::syncBeginReceived);
        QSignalSpy syncCompleteSpy(&client, &PremiumClient::syncCompleteReceived);
        QSignalSpy rawSpy(&client, &PremiumClient::rawSnapshotReceived);
        QSignalSpy watchAckSpy(&client, &PremiumClient::watchlistAcknowledged);
        QSignalSpy hotAckSpy(&client, &PremiumClient::l1HotlistAcknowledged);
        QSignalSpy genericSpy(&client, &PremiumClient::messageReceived);
        QSignalSpy protocolSpy(&client, &PremiumClient::protocolError);

        client.start();
        QTRY_COMPARE_WITH_TIMEOUT(servers.summaryConnectionCount(), 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.summaryState()
                                     == PremiumClient::ChannelState::Connected,
                                 2'000);
        QCOMPARE(servers.lastSummaryPath(), QStringLiteral("/ws/v2/summary"));
        QTRY_VERIFY_WITH_TIMEOUT(statusSpy.count() >= 1, 2'000);

        servers.sendSummary({{QStringLiteral("type"), QStringLiteral("summary")},
                             {QStringLiteral("s"), QStringLiteral("510300.SH")},
                             {QStringLiteral("lp"), 3.91}});
        servers.sendSummary({{QStringLiteral("type"), QStringLiteral("signal")},
                             {QStringLiteral("symbol"), QStringLiteral("510300.SH")},
                             {QStringLiteral("signal_seq"), 7}});
        QTRY_COMPARE_WITH_TIMEOUT(summarySpy.count(), 1, 2'000);
        QTRY_COMPARE_WITH_TIMEOUT(signalSpy.count(), 1, 2'000);
        QCOMPARE(summarySpy.at(0).at(0).toString(), QStringLiteral("510300.SH"));
        QCOMPARE(signalSpy.at(0).at(0).toString(), QStringLiteral("510300.SH"));

        client.requestStatus();
        client.requestSync();
        client.requestRawSnapshot();
        client.setWatchlist({QStringLiteral("510300.SH"),
                             QStringLiteral("159919.SZ")});
        client.setL1Hotlist({QStringLiteral("513100.SH")});

        QTRY_VERIFY_WITH_TIMEOUT(servers.hasSummaryOperation(QStringLiteral("sync")),
                                 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(
            servers.hasSummaryOperation(QStringLiteral("raw_snapshot")), 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(
            servers.hasSummaryOperation(QStringLiteral("set_watchlist")), 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(
            servers.hasSummaryOperation(QStringLiteral("set_l1_hotlist")), 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(operationCount(servers.summaryCommands(),
                                                QStringLiteral("status")) >= 2,
                                 2'000);

        const QJsonObject *watch = servers.summaryOperation(QStringLiteral("set_watchlist"));
        QVERIFY(watch != nullptr);
        const QJsonArray watchSymbols = watch->value(QStringLiteral("symbols")).toArray();
        QCOMPARE(watchSymbols.size(), 2);
        QCOMPARE(watchSymbols.at(0).toString(), QStringLiteral("510300.SH"));
        QCOMPARE(watchSymbols.at(1).toString(), QStringLiteral("159919.SZ"));

        const QJsonObject *hot = servers.summaryOperation(QStringLiteral("set_l1_hotlist"));
        QVERIFY(hot != nullptr);
        QCOMPARE(hot->value(QStringLiteral("symbols")).toArray().at(0).toString(),
                 QStringLiteral("513100.SH"));

        QTRY_VERIFY_WITH_TIMEOUT(syncBeginSpy.count() >= 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(syncCompleteSpy.count() >= 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(rawSpy.count() >= 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(watchAckSpy.count() >= 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(hotAckSpy.count() >= 1, 2'000);
        QVERIFY(genericSpy.count() >= statusSpy.count() + summarySpy.count()
                                     + signalSpy.count());
        QVERIFY(client.isSummaryFresh());

        const int acknowledgements = watchAckSpy.count();
        const int protocolErrors = protocolSpy.count();
        servers.sendSummary({{QStringLiteral("type"), QStringLiteral("watchlist_ack")},
                             {QStringLiteral("symbols"), QJsonArray{QStringLiteral("510300.SH")}}});
        QTRY_VERIFY_WITH_TIMEOUT(protocolSpy.count() > protocolErrors, 2'000);
        QCOMPARE(watchAckSpy.count(), acknowledgements);

        client.stop();
    }

    void l1HelloStatusAndPing()
    {
        FakePremiumServers servers;
        QVERIFY2(servers.listen(), "failed to bind ephemeral localhost test ports");

        PremiumClient client(clientConfig(servers));
        QSignalSpy helloSpy(&client, &PremiumClient::l1HelloReceived);
        QSignalSpy statusSpy(&client, &PremiumClient::l1StatusReceived);
        QSignalSpy pongSpy(&client, &PremiumClient::l1PongReceived);

        client.start();
        QTRY_COMPARE_WITH_TIMEOUT(servers.l1ConnectionCount(), 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(helloSpy.count() >= 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(statusSpy.count() >= 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(pongSpy.count() >= 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.l1State() == PremiumClient::ChannelState::Connected,
                                 2'000);

        const QJsonObject hello = helloSpy.at(0).at(0).toJsonObject();
        QCOMPARE(hello.value(QStringLiteral("v")).toInt(), 1);
        QCOMPARE(hello.value(QStringLiteral("service")).toString(),
                 QStringLiteral("qmt_l1"));
        QVERIFY(servers.hasL1Operation(QStringLiteral("status")));
        QVERIFY(servers.hasL1Operation(QStringLiteral("ping")));

        const int statusBefore = operationCount(servers.l1Commands(),
                                                QStringLiteral("status"));
        const int pingBefore = operationCount(servers.l1Commands(),
                                              QStringLiteral("ping"));
        client.requestL1Status();
        client.pingL1();
        QTRY_COMPARE_WITH_TIMEOUT(operationCount(servers.l1Commands(),
                                                 QStringLiteral("status")),
                                  statusBefore + 1, 2'000);
        QTRY_COMPARE_WITH_TIMEOUT(operationCount(servers.l1Commands(),
                                                 QStringLiteral("ping")),
                                  pingBefore + 1, 2'000);

        QSet<QString> requestIds;
        int probeCount = 0;
        for (const QJsonObject &command : servers.l1Commands()) {
            const QString operation = command.value(QStringLiteral("t")).toString();
            if (operation != QStringLiteral("status")
                && operation != QStringLiteral("ping")) {
                continue;
            }
            QCOMPARE(command.value(QStringLiteral("v")).toInt(), 1);
            const QString id = command.value(QStringLiteral("id")).toString();
            QVERIFY(!id.isEmpty());
            requestIds.insert(id);
            ++probeCount;
        }
        QCOMPARE(requestIds.size(), probeCount);
        QVERIFY(client.isL1Fresh());
        QCOMPARE(client.lastL1Status().value(QStringLiteral("market_online")).toBool(),
                 true);

        client.stop();
    }

    void reconnectsChannelsIndependently()
    {
        FakePremiumServers servers;
        QVERIFY2(servers.listen(), "failed to bind ephemeral localhost test ports");

        PremiumClient client(clientConfig(servers));
        bool summaryBackoffSeen = false;
        bool l1BackoffSeen = false;
        connect(&client, &PremiumClient::summaryStateChanged,
                this, [&](PremiumClient::ChannelState state, const QString &) {
            summaryBackoffSeen |= state == PremiumClient::ChannelState::Backoff;
        });
        connect(&client, &PremiumClient::l1StateChanged,
                this, [&](PremiumClient::ChannelState state, const QString &) {
            l1BackoffSeen |= state == PremiumClient::ChannelState::Backoff;
        });

        client.start();
        QTRY_COMPARE_WITH_TIMEOUT(servers.summaryConnectionCount(), 1, 2'000);
        QTRY_COMPARE_WITH_TIMEOUT(servers.l1ConnectionCount(), 1, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.summaryState()
                                     == PremiumClient::ChannelState::Connected,
                                 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.l1State() == PremiumClient::ChannelState::Connected,
                                 2'000);

        servers.abortSummaryClient();
        QTRY_VERIFY_WITH_TIMEOUT(summaryBackoffSeen, 2'000);
        QTRY_COMPARE_WITH_TIMEOUT(servers.summaryConnectionCount(), 2, 3'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.summaryState()
                                     == PremiumClient::ChannelState::Connected,
                                 2'000);
        QCOMPARE(servers.l1ConnectionCount(), 1);

        servers.abortL1Client();
        QTRY_VERIFY_WITH_TIMEOUT(l1BackoffSeen, 2'000);
        QTRY_COMPARE_WITH_TIMEOUT(servers.l1ConnectionCount(), 2, 3'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.l1State() == PremiumClient::ChannelState::Connected,
                                 2'000);
        QCOMPARE(servers.summaryConnectionCount(), 2);

        client.stop();
        QTRY_VERIFY_WITH_TIMEOUT(client.summaryState()
                                     == PremiumClient::ChannelState::Stopped,
                                 1'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.l1State() == PremiumClient::ChannelState::Stopped,
                                 1'000);
        const int summaryConnectionsAtStop = servers.summaryConnectionCount();
        const int l1ConnectionsAtStop = servers.l1ConnectionCount();
        QTest::qWait(300);
        QCOMPARE(servers.summaryConnectionCount(), summaryConnectionsAtStop);
        QCOMPARE(servers.l1ConnectionCount(), l1ConnectionsAtStop);
    }

    void clearsSummaryMutationAckGenerationOnStopAndReconcile()
    {
        FakePremiumServers servers;
        QVERIFY2(servers.listen(), "failed to bind ephemeral localhost test ports");
        servers.setRespondToSummaryMutations(false);

        PremiumClient client(clientConfig(servers));
        QSignalSpy rejected(&client, &PremiumClient::commandRejectedLocally);
        client.start();
        QTRY_VERIFY_WITH_TIMEOUT(client.summaryState()
                                     == PremiumClient::ChannelState::Connected,
                                 2'000);

        client.setWatchlist({QStringLiteral("510300.SH")});
        QTRY_COMPARE_WITH_TIMEOUT(operationCount(servers.summaryCommands(),
                                                 QStringLiteral("set_watchlist")),
                                  1, 2'000);
        client.setWatchlist({QStringLiteral("159919.SZ")});
        QTRY_COMPARE_WITH_TIMEOUT(rejected.size(), 1, 1'000);

        client.stop();
        client.start();
        QTRY_COMPARE_WITH_TIMEOUT(servers.summaryConnectionCount(), 2, 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.summaryState()
                                     == PremiumClient::ChannelState::Connected,
                                 2'000);
        client.setWatchlist({QStringLiteral("159919.SZ")});
        QTRY_COMPARE_WITH_TIMEOUT(operationCount(servers.summaryCommands(),
                                                 QStringLiteral("set_watchlist")),
                                  2, 2'000);

        client.setL1Hotlist({QStringLiteral("513100.SH")});
        QTRY_COMPARE_WITH_TIMEOUT(operationCount(servers.summaryCommands(),
                                                 QStringLiteral("set_l1_hotlist")),
                                  1, 2'000);
        client.setL1Hotlist({QStringLiteral("159915.SZ")});
        QTRY_COMPARE_WITH_TIMEOUT(rejected.size(), 2, 1'000);
        client.reconcilePendingMutations();
        client.setL1Hotlist({QStringLiteral("159915.SZ")});
        QTRY_COMPARE_WITH_TIMEOUT(operationCount(servers.summaryCommands(),
                                                 QStringLiteral("set_l1_hotlist")),
                                  2, 2'000);
        client.stop();
    }

    void detailUsesDedicatedContractAndEnforcesFourSymbols()
    {
        FakePremiumServers servers;
        QVERIFY2(servers.listen(), "failed to bind ephemeral localhost test ports");

        PremiumClient client(clientConfig(servers));
        QSignalSpy hello(&client, &PremiumClient::detailHelloReceived);
        QSignalSpy acknowledgements(&client, &PremiumClient::detailAcknowledged);
        QSignalSpy details(&client, &PremiumClient::detailReceived);
        QSignalSpy failures(&client, &PremiumClient::detailSubscriptionFailed);
        client.start();

        const QStringList symbols{QStringLiteral("510300.SH"),
                                  QStringLiteral("159919.SZ"),
                                  QStringLiteral("513100.SH"),
                                  QStringLiteral("159915.SZ")};
        for (int index = 0; index < symbols.size(); ++index) {
            client.subscribeDetail(symbols.at(index));
            QTRY_VERIFY_WITH_TIMEOUT(
                servers.hasDetailOperation(QStringLiteral("subscribe"),
                                           symbols.at(index)),
                2'000);
            QTRY_COMPARE_WITH_TIMEOUT(acknowledgements.size(), index + 1, 2'000);
            QTRY_COMPARE_WITH_TIMEOUT(details.size(), index + 1, 2'000);
        }
        QCOMPARE(servers.detailConnectionCount(), 1);
        QCOMPARE(servers.lastDetailPath(), QStringLiteral("/ws/v2/detail"));
        QCOMPARE(hello.size(), 1);
        QCOMPARE(client.detailState(), PremiumClient::ChannelState::Connected);
        QStringList sortedSymbols = symbols;
        sortedSymbols.sort(Qt::CaseInsensitive);
        QCOMPARE(client.desiredDetailSymbols(), sortedSymbols);
        QCOMPARE(client.acknowledgedDetailSymbols(), sortedSymbols);
        const QJsonObject firstDetail = details.at(0).at(1).toJsonObject();
        QCOMPARE(firstDetail.value(QStringLiteral("cached")).toBool(), true);
        QCOMPARE(firstDetail.value(QStringLiteral("bid_prices_e6")).toArray().size(), 10);
        QCOMPARE(firstDetail.value(QStringLiteral("ask_prices_e6")).toArray().size(), 10);

        client.subscribeDetail(QStringLiteral("588000.SH"));
        QTRY_COMPARE_WITH_TIMEOUT(failures.size(), 1, 1'000);
        QCOMPARE(failures.at(0).at(2).toJsonObject()
                     .value(QStringLiteral("code")).toString(),
                 QStringLiteral("detail_limit"));
        QCOMPARE(client.desiredDetailSymbols().size(), 4);

        for (int index = 0; index < symbols.size(); ++index) {
            client.unsubscribeDetail(symbols.at(index));
            QTRY_VERIFY_WITH_TIMEOUT(
                servers.hasDetailOperation(QStringLiteral("unsubscribe"),
                                           symbols.at(index)),
                2'000);
            QTRY_COMPARE_WITH_TIMEOUT(acknowledgements.size(),
                                      symbols.size() + index + 1, 2'000);
        }
        QTRY_VERIFY_WITH_TIMEOUT(client.desiredDetailSymbols().isEmpty(), 1'000);
        QTRY_VERIFY_WITH_TIMEOUT(client.acknowledgedDetailSymbols().isEmpty(), 1'000);
        QTRY_COMPARE_WITH_TIMEOUT(client.detailState(),
                                  PremiumClient::ChannelState::Stopped, 1'000);
        client.stop();
    }

    void detailResubscribesDesiredSymbolsAfterGenerationChange()
    {
        FakePremiumServers servers;
        QVERIFY2(servers.listen(), "failed to bind ephemeral localhost test ports");
        PremiumClient client(clientConfig(servers));
        QSignalSpy details(&client, &PremiumClient::detailReceived);
        client.start();
        client.subscribeDetail(QStringLiteral("510300.SH"));
        QTRY_COMPARE_WITH_TIMEOUT(details.size(), 1, 2'000);

        servers.abortDetailClient();
        QTRY_COMPARE_WITH_TIMEOUT(servers.detailConnectionCount(), 2, 3'000);
        QTRY_COMPARE_WITH_TIMEOUT(details.size(), 2, 3'000);
        QCOMPARE(client.desiredDetailSymbols(),
                 QStringList{QStringLiteral("510300.SH")});
        QCOMPARE(client.acknowledgedDetailSymbols(),
                 QStringList{QStringLiteral("510300.SH")});

        client.stop();
        client.start();
        QTRY_COMPARE_WITH_TIMEOUT(servers.detailConnectionCount(), 3, 3'000);
        QTRY_COMPARE_WITH_TIMEOUT(details.size(), 3, 3'000);
        QCOMPARE(client.desiredDetailSymbols(),
                 QStringList{QStringLiteral("510300.SH")});
        client.unsubscribeDetail(QStringLiteral("510300.SH"));
        QTRY_COMPARE_WITH_TIMEOUT(client.detailState(),
                                  PremiumClient::ChannelState::Stopped, 2'000);
        client.stop();
    }
};

QTEST_GUILESS_MAIN(PremiumClientTests)
#include "tst_premium_client.moc"
