#include "NativeIbkrBridge.h"

#include <Contract.h>
#include <Decimal.h>
#include <EClientSocket.h>
#include <EReader.h>
#include <TagValue.h>
#include <TickAttrib.h>

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QLocalServer>
#include <QLocalSocket>
#include <QMetaObject>
#include <QMutexLocker>
#include <QSaveFile>
#include <QTimeZone>
#include <QTimer>

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace machome::ibkr {
namespace {

QString iso(const QDateTime &value)
{
    return value.isValid() ? value.toUTC().toString(Qt::ISODateWithMs) : QString{};
}

QString marketDataTypeName(int value)
{
    switch (value) {
    case 1: return QStringLiteral("live");
    case 2: return QStringLiteral("frozen");
    case 3: return QStringLiteral("delayed");
    case 4: return QStringLiteral("delayed_frozen");
    default: return QStringLiteral("unknown");
    }
}

} // namespace

NativeIbkrBridge::NativeIbkrBridge(BridgeConfig config, QObject *parent)
    : QObject(parent), config_(std::move(config))
{
    quotes_.reset(config_.subscriptions);
    for (const ContractSpec &spec : config_.subscriptions) {
        pinnedSubscriptionIds_.insert(spec.id);
    }

    reconnectTimer_ = new QTimer(this);
    reconnectTimer_->setSingleShot(true);
    connect(reconnectTimer_, &QTimer::timeout, this, &NativeIbkrBridge::connectTws);

    scheduleTimer_ = new QTimer(this);
    scheduleTimer_->setSingleShot(true);
    connect(scheduleTimer_, &QTimer::timeout,
            this, &NativeIbkrBridge::reevaluateConnectionSchedule);

    connectTimeoutTimer_ = new QTimer(this);
    connectTimeoutTimer_->setSingleShot(true);
    connect(connectTimeoutTimer_, &QTimer::timeout, this, [this] {
        {
            QMutexLocker locker(&stateMutex_);
            if (ready_ || stopping_) return;
            state_ = QStringLiteral("backoff");
            lastError_ = QStringLiteral("TWS 连接后未在时限内收到 nextValidId");
            lastFailureAt_ = QDateTime::currentDateTimeUtc();
        }
        disconnectTws();
        scheduleReconnect(QStringLiteral("TWS 连接就绪超时"));
    });

    heartbeatTimer_ = new QTimer(this);
    heartbeatTimer_->setInterval(config_.heartbeatIntervalMs);
    connect(heartbeatTimer_, &QTimer::timeout, this, &NativeIbkrBridge::heartbeatTick);

    healthTimer_ = new QTimer(this);
    healthTimer_->setInterval(std::clamp(config_.heartbeatIntervalMs / 2, 1'000, 5'000));
    connect(healthTimer_, &QTimer::timeout, this, &NativeIbkrBridge::writeHealthFile);
}

NativeIbkrBridge::~NativeIbkrBridge()
{
    stop();
}

bool NativeIbkrBridge::start(QString *errorMessage)
{
    if (errorMessage == nullptr) return false;
    if (started_) return true;
    stopping_ = false;
    startLocalServer(errorMessage);
    if (!errorMessage->isEmpty()) return false;

    {
        QMutexLocker locker(&stateMutex_);
        state_ = QStringLiteral("starting");
        startedAt_ = QDateTime::currentDateTimeUtc();
        lastError_.clear();
    }
    started_ = true;
    heartbeatTimer_->start();
    healthTimer_->start();
    QTimer::singleShot(0, this, &NativeIbkrBridge::reevaluateConnectionSchedule);
    return true;
}

void NativeIbkrBridge::stop()
{
    if (!started_ && !readerThread_.joinable()) return;
    stopping_ = true;
    reconnectTimer_->stop();
    scheduleTimer_->stop();
    connectTimeoutTimer_->stop();
    heartbeatTimer_->stop();
    healthTimer_->stop();
    disconnectTws();

    for (QLocalSocket *socket : inputBuffers_.keys()) {
        socket->disconnect(this);
        socket->abort();
        socket->deleteLater();
    }
    inputBuffers_.clear();
    handshaken_.clear();
    clientSubscriptions_.clear();
    subscriptionClients_.clear();
    if (server_ != nullptr) {
        server_->close();
        server_->deleteLater();
        server_ = nullptr;
    }
    if (ownsSocket_) {
        QLocalServer::removeServer(config_.socketPath);
        ownsSocket_ = false;
    }
    {
        QMutexLocker locker(&stateMutex_);
        ready_ = false;
        state_ = QStringLiteral("stopped");
    }
    writeHealthFile();
    started_ = false;
}

QJsonObject NativeIbkrBridge::statusSnapshot() const
{
    QString state;
    QString lastError;
    QDateTime startedAt;
    QDateTime connectedAt;
    QDateTime lastHeartbeatAt;
    QDateTime lastSuccessAt;
    int serverVersion = 0;
    int reconnectAttempt = 0;
    bool ready = false;
    {
        QMutexLocker locker(&stateMutex_);
        state = state_;
        lastError = lastError_;
        startedAt = startedAt_;
        connectedAt = connectedAt_;
        lastHeartbeatAt = lastHeartbeatAt_;
        lastSuccessAt = lastSuccessAt_;
        serverVersion = serverVersion_;
        reconnectAttempt = reconnectAttempt_;
        ready = ready_;
    }
    const QDateTime now = QDateTime::currentDateTimeUtc();
    const qint64 heartbeatAgeMs = lastSuccessAt.isValid()
        ? std::max<qint64>(0, lastSuccessAt.msecsTo(now)) : -1;
    const bool heartbeatFresh = heartbeatAgeMs >= 0 && heartbeatAgeMs <= config_.heartbeatStaleMs;
    const ConnectionScheduleDecision schedule =
        config_.connectionSchedule.evaluate(QDateTime::currentDateTimeUtc());
    return {
        {QStringLiteral("protocol"), QString::fromLatin1(kBridgeProtocol)},
        {QStringLiteral("service"), QStringLiteral("machome-ibkr-bridge")},
        {QStringLiteral("state"), state},
        {QStringLiteral("ready"), ready && heartbeatFresh},
        {QStringLiteral("tws_connected"), ready},
        {QStringLiteral("schedule_enabled"), config_.connectionSchedule.enabled},
        {QStringLiteral("schedule_active"), schedule.active},
        {QStringLiteral("schedule_local_time"),
         schedule.localTime.toString(Qt::ISODateWithMs)},
        {QStringLiteral("schedule_next_transition"),
         schedule.nextTransition.toString(Qt::ISODateWithMs)},
        {QStringLiteral("server_version"), serverVersion},
        {QStringLiteral("started_at"), iso(startedAt)},
        {QStringLiteral("connected_at"), iso(connectedAt)},
        {QStringLiteral("last_heartbeat_at"), iso(lastHeartbeatAt)},
        {QStringLiteral("last_success_at"), iso(lastSuccessAt)},
        {QStringLiteral("heartbeat_age_ms"), heartbeatAgeMs},
        {QStringLiteral("heartbeat_fresh"), heartbeatFresh},
        {QStringLiteral("reconnect_attempt"), reconnectAttempt},
        {QStringLiteral("last_error"), lastError},
        {QStringLiteral("subscription_count"), quotes_.subscriptionCount()},
        {QStringLiteral("configured_subscription_count"), config_.subscriptions.size()},
        {QStringLiteral("dynamic_subscription_count"),
         quotes_.subscriptionCount() - config_.subscriptions.size()},
        {QStringLiteral("quote_update_count"), quotes_.updateCount()},
        {QStringLiteral("client_count"), inputBuffers_.size()},
        {QStringLiteral("market_data_type"), config_.marketDataType},
        {QStringLiteral("market_data_type_name"), marketDataTypeName(config_.marketDataType)},
    };
}

void NativeIbkrBridge::connectAck()
{
    QMetaObject::invokeMethod(this, [this] {
        if (!scheduleAllowsConnection()) {
            reevaluateConnectionSchedule();
            return;
        }
        QMutexLocker locker(&stateMutex_);
        if (!stopping_ && !ready_) state_ = QStringLiteral("handshaking");
    }, Qt::QueuedConnection);
}

void NativeIbkrBridge::nextValidId(OrderId orderId)
{
    const int value = static_cast<int>(orderId);
    QMetaObject::invokeMethod(this, [this, value] { markReady(value); }, Qt::QueuedConnection);
}

void NativeIbkrBridge::connectionClosed()
{
    QMetaObject::invokeMethod(this, [this] {
        if (stopping_) return;
        if (!scheduleAllowsConnection()) {
            reevaluateConnectionSchedule();
            return;
        }
        {
            QMutexLocker locker(&stateMutex_);
            ready_ = false;
            state_ = QStringLiteral("backoff");
            lastError_ = QStringLiteral("TWS socket 已关闭");
            lastFailureAt_ = QDateTime::currentDateTimeUtc();
        }
        disconnectTws();
        scheduleReconnect(QStringLiteral("TWS socket 已关闭"));
    }, Qt::QueuedConnection);
}

void NativeIbkrBridge::currentTime(long epochSeconds)
{
    const QDateTime observed = epochSeconds > 0
        ? QDateTime::fromSecsSinceEpoch(epochSeconds, QTimeZone::UTC)
        : QDateTime::currentDateTimeUtc();
    QMetaObject::invokeMethod(this, [this, observed] {
        if (!scheduleAllowsConnection()) {
            reevaluateConnectionSchedule();
            return;
        }
        QMutexLocker locker(&stateMutex_);
        lastHeartbeatAt_ = observed;
        lastSuccessAt_ = QDateTime::currentDateTimeUtc();
        if (ready_) {
            state_ = QStringLiteral("ready");
            lastError_.clear();
        }
    }, Qt::QueuedConnection);
}

void NativeIbkrBridge::error(int id, time_t, int errorCode,
                             const std::string &errorString,
                             const std::string &)
{
    const QString message = safeSdkMessage(errorString);
    QMetaObject::invokeMethod(this, [this, id, errorCode, message] {
        if (stopping_) return;
        if (!scheduleAllowsConnection()) {
            reevaluateConnectionSchedule();
            return;
        }
        if (errorCode == 1101 || errorCode == 1102) {
            {
                QMutexLocker locker(&stateMutex_);
                state_ = QStringLiteral("ready");
                ready_ = true;
                lastError_.clear();
            }
            if (errorCode == 1101) subscribeConfiguredContracts();
            return;
        }
        if (errorCode == 1300 || errorCode == 502 || errorCode == 504) {
            const QString reconnectReason = QStringLiteral("IBKR code=%1 id=%2 %3")
                                                .arg(errorCode).arg(id).arg(message);
            {
                QMutexLocker locker(&stateMutex_);
                ready_ = false;
                state_ = QStringLiteral("backoff");
                lastError_ = reconnectReason;
                lastFailureAt_ = QDateTime::currentDateTimeUtc();
            }
            disconnectTws();
            scheduleReconnect(reconnectReason);
            return;
        }
        QMutexLocker locker(&stateMutex_);
        if (!informationalError(errorCode)) {
            lastError_ = QStringLiteral("IBKR code=%1 id=%2 %3")
                             .arg(errorCode).arg(id).arg(message);
            lastFailureAt_ = QDateTime::currentDateTimeUtc();
            if (errorCode == 1100) state_ = QStringLiteral("degraded");
        }
    }, Qt::QueuedConnection);
}

void NativeIbkrBridge::tickPrice(TickerId tickerId, TickType field, double price,
                                 const TickAttrib &)
{
    using Field = QuoteBook::PriceField;
    if (field == BID || field == DELAYED_BID) {
        quotes_.updatePrice(static_cast<int>(tickerId), Field::Bid, price);
    } else if (field == ASK || field == DELAYED_ASK) {
        quotes_.updatePrice(static_cast<int>(tickerId), Field::Ask, price);
    } else if (field == LAST || field == DELAYED_LAST) {
        quotes_.updatePrice(static_cast<int>(tickerId), Field::Last, price);
    } else if (field == CLOSE || field == DELAYED_CLOSE) {
        quotes_.updatePrice(static_cast<int>(tickerId), Field::Close, price);
    }
}

void NativeIbkrBridge::tickSize(TickerId tickerId, TickType field, Decimal size)
{
    const QString text = QString::fromStdString(DecimalFunctions::decimalStringToDisplay(size));
    using Field = QuoteBook::SizeField;
    if (field == BID_SIZE || field == DELAYED_BID_SIZE) {
        quotes_.updateSize(static_cast<int>(tickerId), Field::Bid, text);
    } else if (field == ASK_SIZE || field == DELAYED_ASK_SIZE) {
        quotes_.updateSize(static_cast<int>(tickerId), Field::Ask, text);
    } else if (field == LAST_SIZE || field == DELAYED_LAST_SIZE) {
        quotes_.updateSize(static_cast<int>(tickerId), Field::Last, text);
    }
}

void NativeIbkrBridge::tickString(TickerId tickerId, TickType field,
                                  const std::string &value)
{
    if (field != LAST_TIMESTAMP && field != DELAYED_LAST_TIMESTAMP) return;
    bool ok = false;
    const qint64 epochSeconds = QString::fromStdString(value).toLongLong(&ok);
    if (ok) quotes_.updateExchangeTimestamp(static_cast<int>(tickerId), epochSeconds);
}

void NativeIbkrBridge::marketDataType(TickerId tickerId, int marketDataType)
{
    quotes_.updateMarketDataType(static_cast<int>(tickerId), marketDataType);
}

void NativeIbkrBridge::startLocalServer(QString *errorMessage)
{
    const QFileInfo socketInfo(config_.socketPath);
    QDir parent = socketInfo.dir();
    if (!parent.exists() && !QDir().mkpath(parent.absolutePath())) {
        *errorMessage = QStringLiteral("无法创建 IBKR bridge socket 目录");
        return;
    }
    QFile::setPermissions(parent.absolutePath(), QFileDevice::ReadOwner | QFileDevice::WriteOwner
                                                    | QFileDevice::ExeOwner);

    server_ = new QLocalServer(this);
    server_->setSocketOptions(QLocalServer::UserAccessOption);
    server_->setMaxPendingConnections(config_.maximumClients);
    connect(server_, &QLocalServer::newConnection, this, &NativeIbkrBridge::acceptConnections);
    if (!server_->listen(config_.socketPath)) {
        *errorMessage = QStringLiteral("无法监听 IBKR bridge socket（不会自动删除已有 socket）: %1")
                            .arg(server_->errorString());
        server_->deleteLater();
        server_ = nullptr;
        return;
    }
    ownsSocket_ = true;
}

void NativeIbkrBridge::connectTws()
{
    if (stopping_) return;
    const ConnectionScheduleDecision schedule =
        config_.connectionSchedule.evaluate(QDateTime::currentDateTimeUtc());
    armScheduleTimer(schedule);
    if (!schedule.active) {
        enterScheduledIdle(schedule);
        return;
    }
    disconnectTws();
    {
        QMutexLocker locker(&stateMutex_);
        ready_ = false;
        state_ = QStringLiteral("connecting");
        lastError_.clear();
    }

    bool connected = false;
    {
        QMutexLocker locker(&sdkMutex_);
        client_ = std::make_unique<EClientSocket>(this, &signal_);
        connected = client_->eConnect(config_.twsHost.toStdString().c_str(),
                                      config_.twsPort, config_.clientId);
        if (connected) {
            reader_ = std::make_unique<EReader>(client_.get(), &signal_);
            reader_->start();
            readerRunning_.store(true);
        } else {
            client_.reset();
        }
    }
    if (!connected) {
        {
            QMutexLocker locker(&stateMutex_);
            state_ = QStringLiteral("backoff");
            lastError_ = QStringLiteral("无法连接本机 TWS/IB Gateway %1:%2")
                             .arg(config_.twsHost).arg(config_.twsPort);
            lastFailureAt_ = QDateTime::currentDateTimeUtc();
        }
        scheduleReconnect(QStringLiteral("TWS eConnect 失败"));
        return;
    }
    readerThread_ = std::thread([this] { readerLoop(); });
    connectTimeoutTimer_->start(config_.connectTimeoutMs);
}

void NativeIbkrBridge::disconnectTws()
{
    readerRunning_.store(false);
    {
        QMutexLocker locker(&sdkMutex_);
        if (client_) client_->eDisconnect();
        signal_.issueSignal();
    }
    if (readerThread_.joinable()) readerThread_.join();
    {
        QMutexLocker locker(&sdkMutex_);
        reader_.reset();
        client_.reset();
    }
    QMutexLocker stateLocker(&stateMutex_);
    ready_ = false;
}

void NativeIbkrBridge::scheduleReconnect(const QString &reason)
{
    if (stopping_) return;
    const ConnectionScheduleDecision schedule =
        config_.connectionSchedule.evaluate(QDateTime::currentDateTimeUtc());
    armScheduleTimer(schedule);
    if (!schedule.active) {
        enterScheduledIdle(schedule);
        return;
    }
    int attempt = 0;
    {
        QMutexLocker locker(&stateMutex_);
        attempt = std::min(reconnectAttempt_++, 16);
        if (!reason.isEmpty()) lastError_ = reason.left(500);
        state_ = QStringLiteral("backoff");
    }
    qint64 delay = config_.reconnectMinimumMs;
    for (int i = 0; i < attempt && delay < config_.reconnectMaximumMs; ++i) {
        delay = std::min<qint64>(delay * 2, config_.reconnectMaximumMs);
    }
    reconnectTimer_->start(static_cast<int>(delay));
    writeHealthFile();
}

bool NativeIbkrBridge::scheduleAllowsConnection() const
{
    return config_.connectionSchedule.evaluate(QDateTime::currentDateTimeUtc()).active;
}

void NativeIbkrBridge::armScheduleTimer(const ConnectionScheduleDecision &decision)
{
    if (!config_.connectionSchedule.enabled || !decision.nextTransition.isValid()) {
        scheduleTimer_->stop();
        return;
    }
    const qint64 delay = QDateTime::currentDateTimeUtc().msecsTo(
        decision.nextTransition.toUTC());
    const qint64 bounded = std::clamp<qint64>(
        delay + 50, 250, std::numeric_limits<int>::max());
    scheduleTimer_->start(static_cast<int>(bounded));
}

void NativeIbkrBridge::enterScheduledIdle(const ConnectionScheduleDecision &decision)
{
    reconnectTimer_->stop();
    connectTimeoutTimer_->stop();
    disconnectTws();
    {
        QMutexLocker locker(&stateMutex_);
        ready_ = false;
        reconnectAttempt_ = 0;
        state_ = QStringLiteral("scheduled_idle");
        lastError_.clear();
    }
    armScheduleTimer(decision);
    writeHealthFile();
}

void NativeIbkrBridge::reevaluateConnectionSchedule()
{
    if (stopping_) return;
    const ConnectionScheduleDecision decision =
        config_.connectionSchedule.evaluate(QDateTime::currentDateTimeUtc());
    armScheduleTimer(decision);
    if (!decision.active) {
        enterScheduledIdle(decision);
        return;
    }

    bool connected = false;
    {
        QMutexLocker locker(&sdkMutex_);
        connected = client_ && client_->isConnected();
    }
    if (connected || reconnectTimer_->isActive() || connectTimeoutTimer_->isActive()) {
        writeHealthFile();
        return;
    }
    {
        QMutexLocker locker(&stateMutex_);
        reconnectAttempt_ = 0;
    }
    connectTws();
}

void NativeIbkrBridge::markReady(int)
{
    if (stopping_) return;
    if (!scheduleAllowsConnection()) {
        reevaluateConnectionSchedule();
        return;
    }
    connectTimeoutTimer_->stop();
    int serverVersion = 0;
    {
        QMutexLocker sdkLocker(&sdkMutex_);
        // EClientSocket's two-argument message-sink callback hides the
        // zero-argument EClient accessor in this SDK release.
        serverVersion = client_ ? client_->EClient::serverVersion() : 0;
    }
    {
        QMutexLocker locker(&stateMutex_);
        serverVersion_ = serverVersion;
        ready_ = true;
        state_ = QStringLiteral("ready");
        connectedAt_ = QDateTime::currentDateTimeUtc();
        lastSuccessAt_ = connectedAt_;
        lastHeartbeatAt_ = connectedAt_;
        lastError_.clear();
        reconnectAttempt_ = 0;
    }
    subscribeConfiguredContracts();
    writeHealthFile();
}

void NativeIbkrBridge::subscribeConfiguredContracts()
{
    if (stopping_) return;
    const auto subscriptions = quotes_.tickerContracts();
    QMutexLocker locker(&sdkMutex_);
    if (!client_ || !client_->isConnected()) return;
    client_->reqMarketDataType(config_.marketDataType);
    for (const auto &entry : subscriptions) {
        client_->reqMktData(entry.first, toIbContract(entry.second),
                            entry.second.genericTicks.toStdString(), false, false,
                            TagValueListSPtr());
    }
    client_->reqCurrentTime();
}

void NativeIbkrBridge::requestMarketData(int tickerId, const ContractSpec &contract)
{
    QMutexLocker locker(&sdkMutex_);
    if (!client_ || !client_->isConnected()) return;
    client_->reqMktData(tickerId, toIbContract(contract),
                        contract.genericTicks.toStdString(), false, false,
                        TagValueListSPtr());
}

void NativeIbkrBridge::cancelMarketData(int tickerId)
{
    QMutexLocker locker(&sdkMutex_);
    if (client_ && client_->isConnected()) client_->cancelMktData(tickerId);
}

void NativeIbkrBridge::heartbeatTick()
{
    if (stopping_) return;
    if (!scheduleAllowsConnection()) {
        reevaluateConnectionSchedule();
        return;
    }
    bool canRequest = false;
    {
        QMutexLocker locker(&sdkMutex_);
        canRequest = client_ && client_->isConnected();
        if (canRequest) client_->reqCurrentTime();
    }
    if (!canRequest) return;

    bool stale = false;
    {
        QMutexLocker locker(&stateMutex_);
        stale = lastSuccessAt_.isValid()
            && lastSuccessAt_.msecsTo(QDateTime::currentDateTimeUtc()) > config_.heartbeatStaleMs;
        if (stale) {
            state_ = QStringLiteral("degraded");
            lastError_ = QStringLiteral("TWS 心跳超时");
            lastFailureAt_ = QDateTime::currentDateTimeUtc();
        }
    }
    if (stale) {
        disconnectTws();
        scheduleReconnect(QStringLiteral("TWS 心跳超时"));
    }
}

void NativeIbkrBridge::writeHealthFile()
{
    const QFileInfo info(config_.healthFile);
    QDir parent = info.dir();
    if (!parent.exists() && !QDir().mkpath(parent.absolutePath())) return;

    QJsonObject status = statusSnapshot();
    QDateTime lastFailureAt;
    {
        QMutexLocker locker(&stateMutex_);
        lastFailureAt = lastFailureAt_;
    }
    QJsonArray symbols;
    for (const auto &entry : quotes_.tickerContracts()) symbols.append(entry.second.symbol);
    const QDateTime now = QDateTime::currentDateTimeUtc();
    const bool ready = status.value(QStringLiteral("ready")).toBool(false);
    const QString stage = status.value(QStringLiteral("state")).toString();
    const bool scheduledIdle = stage == QStringLiteral("scheduled_idle");
    QJsonObject health{
        {QStringLiteral("schema_version"), 1},
        {QStringLiteral("source"), QStringLiteral("ibkr_native_bridge")},
        {QStringLiteral("pid"), QCoreApplication::applicationPid()},
        {QStringLiteral("state"), ready ? QStringLiteral("ok")
                                         : scheduledIdle ? QStringLiteral("idle")
                                                         : QStringLiteral("error")},
        {QStringLiteral("stage"), stage},
        {QStringLiteral("scheduled_idle"), scheduledIdle},
        {QStringLiteral("schedule_active"), status.value(QStringLiteral("schedule_active"))},
        {QStringLiteral("schedule_next_transition"),
         status.value(QStringLiteral("schedule_next_transition"))},
        {QStringLiteral("updated_at"), iso(now)},
        {QStringLiteral("last_heartbeat_at"), iso(now)},
        {QStringLiteral("last_success_at"), status.value(QStringLiteral("last_success_at"))},
        {QStringLiteral("last_failure_at"), iso(lastFailureAt)},
        {QStringLiteral("accepted"), quotes_.updateCount()},
        {QStringLiteral("symbols"), symbols},
        {QStringLiteral("detail"), QStringLiteral("native TWS fan-out; protocol=%1; clients=%2")
             .arg(QString::fromLatin1(kBridgeProtocol))
             .arg(status.value(QStringLiteral("client_count")).toInt())},
        {QStringLiteral("last_error"), status.value(QStringLiteral("last_error"))},
    };

    QSaveFile file(config_.healthFile);
    file.setDirectWriteFallback(false);
    if (!file.open(QIODevice::WriteOnly)) return;
    file.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    if (file.write(QJsonDocument(health).toJson(QJsonDocument::Compact)) < 0) {
        file.cancelWriting();
        return;
    }
    file.write("\n");
    if (file.commit()) {
        QFile::setPermissions(config_.healthFile, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    }
}

void NativeIbkrBridge::readerLoop()
{
    while (readerRunning_.load()) {
        signal_.waitForSignal();
        if (!readerRunning_.load()) break;
        QMutexLocker locker(&sdkMutex_);
        if (reader_ && client_ && client_->isConnected()) reader_->processMsgs();
    }
}

void NativeIbkrBridge::acceptConnections()
{
    while (server_ && server_->hasPendingConnections()) {
        QLocalSocket *socket = server_->nextPendingConnection();
        if (socket == nullptr) break;
        if (inputBuffers_.size() >= config_.maximumClients) {
            failRequest(socket, QJsonValue(), QStringLiteral("too_many_clients"),
                        QStringLiteral("本机客户端数已达上限"), true);
            socket->deleteLater();
            continue;
        }
        socket->setReadBufferSize(config_.maximumRequestBytes * 2LL);
        inputBuffers_.insert(socket, {});
        connect(socket, &QLocalSocket::readyRead, this, [this, socket] { readClient(socket); });
        connect(socket, &QLocalSocket::disconnected, this, [this, socket] {
            removeClient(socket);
            socket->deleteLater();
        });
    }
}

void NativeIbkrBridge::readClient(QLocalSocket *socket)
{
    auto it = inputBuffers_.find(socket);
    if (it == inputBuffers_.end()) return;
    it.value().append(socket->readAll());
    int processed = 0;
    while (processed < 32) {
        const qsizetype newline = it.value().indexOf('\n');
        if (newline < 0) break;
        QByteArray frame = it.value().left(newline);
        it.value().remove(0, newline + 1);
        if (frame.endsWith('\r')) frame.chop(1);
        ++processed;
        if (frame.isEmpty()) continue;
        if (frame.size() > config_.maximumRequestBytes) {
            failRequest(socket, QJsonValue(), QStringLiteral("frame_too_large"),
                        QStringLiteral("请求帧超过大小限制"), true);
            return;
        }
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(frame, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            failRequest(socket, QJsonValue(), QStringLiteral("invalid_json"),
                        QStringLiteral("请求必须是单行 JSON 对象"), true);
            return;
        }
        handleRequest(socket, document.object());
        if (!inputBuffers_.contains(socket)) return;
        it = inputBuffers_.find(socket);
    }
    if (it != inputBuffers_.end() && it.value().size() > config_.maximumRequestBytes) {
        failRequest(socket, QJsonValue(), QStringLiteral("frame_too_large"),
                    QStringLiteral("请求帧未换行且超过大小限制"), true);
    }
}

void NativeIbkrBridge::removeClient(QLocalSocket *socket)
{
    const QSet<QString> subscriptions = clientSubscriptions_.value(socket);
    for (const QString &id : subscriptions) releaseSubscriptionLease(socket, id);
    clientSubscriptions_.remove(socket);
    inputBuffers_.remove(socket);
    handshaken_.remove(socket);
}

void NativeIbkrBridge::releaseSubscriptionLease(QLocalSocket *socket,
                                                 const QString &subscriptionId)
{
    auto clientIt = clientSubscriptions_.find(socket);
    if (clientIt != clientSubscriptions_.end()) clientIt->remove(subscriptionId);
    auto subscribers = subscriptionClients_.find(subscriptionId);
    if (subscribers == subscriptionClients_.end()) return;
    subscribers->remove(socket);
    if (!subscribers->isEmpty()) return;
    subscriptionClients_.erase(subscribers);
    if (pinnedSubscriptionIds_.contains(subscriptionId)) return;
    int tickerId = -1;
    if (quotes_.removeSubscription(subscriptionId, &tickerId)) cancelMarketData(tickerId);
}

void NativeIbkrBridge::handleRequest(QLocalSocket *socket, const QJsonObject &request)
{
    const QJsonValue requestId = request.value(QStringLiteral("request_id"));
    const QString type = request.value(QStringLiteral("type")).toString().trimmed();
    if (type == QStringLiteral("hello")) {
        if (request.value(QStringLiteral("protocol")).toString()
            != QString::fromLatin1(kBridgeProtocol)) {
            failRequest(socket, requestId, QStringLiteral("protocol_mismatch"),
                        QStringLiteral("protocol 必须为 %1").arg(QString::fromLatin1(kBridgeProtocol)), true);
            return;
        }
        handshaken_.insert(socket);
        sendResponse(socket, {
            {QStringLiteral("request_id"), requestId},
            {QStringLiteral("type"), QStringLiteral("hello")},
            {QStringLiteral("ok"), true},
            {QStringLiteral("capabilities"), QJsonArray{
                 QStringLiteral("status"), QStringLiteral("quotes"), QStringLiteral("quote"),
                 QStringLiteral("subscribe"), QStringLiteral("unsubscribe"),
                 QStringLiteral("ping")}},
        });
        return;
    }
    if (!handshaken_.contains(socket)) {
        failRequest(socket, requestId, QStringLiteral("hello_required"),
                    QStringLiteral("首帧必须是 hello"), true);
        return;
    }
    if (type == QStringLiteral("status")) {
        QJsonObject response = statusSnapshot();
        response.insert(QStringLiteral("request_id"), requestId);
        response.insert(QStringLiteral("type"), QStringLiteral("status"));
        response.insert(QStringLiteral("ok"), true);
        sendResponse(socket, response);
    } else if (type == QStringLiteral("quotes")) {
        const bool bridgeReady = statusSnapshot().value(QStringLiteral("ready")).toBool(false);
        sendResponse(socket, {
            {QStringLiteral("request_id"), requestId},
            {QStringLiteral("type"), QStringLiteral("quotes")},
            {QStringLiteral("ok"), true},
            {QStringLiteral("bridge_ready"), bridgeReady},
            {QStringLiteral("items"), quotes_.quotes(config_.heartbeatStaleMs)},
        });
    } else if (type == QStringLiteral("quote")) {
        const QString id = request.value(QStringLiteral("subscription_id")).toString().trimmed();
        if (!quotes_.containsId(id)) {
            failRequest(socket, requestId, QStringLiteral("unknown_subscription"),
                        QStringLiteral("未知 subscription_id"));
            return;
        }
        const bool bridgeReady = statusSnapshot().value(QStringLiteral("ready")).toBool(false);
        sendResponse(socket, {
            {QStringLiteral("request_id"), requestId},
            {QStringLiteral("type"), QStringLiteral("quote")},
            {QStringLiteral("ok"), true},
            {QStringLiteral("bridge_ready"), bridgeReady},
            {QStringLiteral("item"), quotes_.quote(id, config_.heartbeatStaleMs)},
        });
    } else if (type == QStringLiteral("subscribe")) {
        const QString id = request.value(QStringLiteral("subscription_id")).toString().trimmed();
        const QJsonValue contractValue = request.value(QStringLiteral("contract"));
        if (!contractValue.isObject()) {
            failRequest(socket, requestId, QStringLiteral("invalid_contract"),
                        QStringLiteral("contract 必须是 JSON 对象"));
            return;
        }
        ContractSpec spec;
        QString parseError;
        if (!ContractSpec::fromJson(contractValue.toObject(), id, &spec, &parseError)) {
            failRequest(socket, requestId, QStringLiteral("invalid_contract"), parseError);
            return;
        }
        const bool alreadyLeased = clientSubscriptions_.value(socket).contains(id);
        bool created = false;
        if (!quotes_.containsId(id)) {
            if (quotes_.subscriptionCount() >= config_.maximumSubscriptions) {
                failRequest(socket, requestId, QStringLiteral("subscription_limit"),
                            QStringLiteral("行情订阅数已达配置上限"));
                return;
            }
            const auto result = quotes_.registerSubscription(spec, nextDynamicTickerId_++);
            if (result != QuoteBook::RegisterResult::Added) {
                failRequest(socket, requestId, QStringLiteral("subscription_conflict"),
                            QStringLiteral("subscription_id 与现有合约冲突"));
                return;
            }
            created = true;
        } else {
            const int existingTicker = quotes_.tickerIdFor(id);
            const auto result = quotes_.registerSubscription(spec, existingTicker);
            if (result == QuoteBook::RegisterResult::Conflict) {
                failRequest(socket, requestId, QStringLiteral("subscription_conflict"),
                            QStringLiteral("同一 subscription_id 不允许对应不同合约"));
                return;
            }
        }
        clientSubscriptions_[socket].insert(id);
        subscriptionClients_[id].insert(socket);
        if (created) requestMarketData(quotes_.tickerIdFor(id), spec);
        sendResponse(socket, {
            {QStringLiteral("request_id"), requestId},
            {QStringLiteral("type"), QStringLiteral("subscribed")},
            {QStringLiteral("ok"), true},
            {QStringLiteral("created"), created},
            {QStringLiteral("already_leased"), alreadyLeased},
            {QStringLiteral("item"), quotes_.quote(id, config_.heartbeatStaleMs)},
        });
    } else if (type == QStringLiteral("unsubscribe")) {
        const QString id = request.value(QStringLiteral("subscription_id")).toString().trimmed();
        if (!clientSubscriptions_.value(socket).contains(id)) {
            failRequest(socket, requestId, QStringLiteral("lease_not_found"),
                        QStringLiteral("当前客户端未持有该订阅租约"));
            return;
        }
        releaseSubscriptionLease(socket, id);
        sendResponse(socket, {
            {QStringLiteral("request_id"), requestId},
            {QStringLiteral("type"), QStringLiteral("unsubscribed")},
            {QStringLiteral("ok"), true},
            {QStringLiteral("subscription_id"), id},
            {QStringLiteral("pinned"), pinnedSubscriptionIds_.contains(id)},
        });
    } else if (type == QStringLiteral("ping")) {
        sendResponse(socket, {
            {QStringLiteral("request_id"), requestId},
            {QStringLiteral("type"), QStringLiteral("pong")},
            {QStringLiteral("ok"), true},
            {QStringLiteral("observed_at"), iso(QDateTime::currentDateTimeUtc())},
        });
    } else {
        failRequest(socket, requestId, QStringLiteral("unsupported_request"),
                    QStringLiteral("不支持的请求类型"));
    }
}

void NativeIbkrBridge::sendResponse(QLocalSocket *socket, QJsonObject response)
{
    if (socket == nullptr || socket->state() != QLocalSocket::ConnectedState) return;
    response.insert(QStringLiteral("protocol"), QString::fromLatin1(kBridgeProtocol));
    QByteArray bytes = QJsonDocument(response).toJson(QJsonDocument::Compact);
    if (bytes.size() > 1024 * 1024) {
        bytes = QJsonDocument(QJsonObject{
            {QStringLiteral("protocol"), QString::fromLatin1(kBridgeProtocol)},
            {QStringLiteral("type"), QStringLiteral("error")},
            {QStringLiteral("ok"), false},
            {QStringLiteral("error"), QJsonObject{
                 {QStringLiteral("code"), QStringLiteral("response_too_large")},
                 {QStringLiteral("message"), QStringLiteral("响应超过 1 MiB")}}},
        }).toJson(QJsonDocument::Compact);
    }
    bytes.append('\n');
    if (socket->bytesToWrite() > 2 * 1024 * 1024) {
        socket->abort();
        removeClient(socket);
        return;
    }
    socket->write(bytes);
}

void NativeIbkrBridge::failRequest(QLocalSocket *socket, const QJsonValue &requestId,
                                   const QString &code, const QString &message,
                                   bool closeAfter)
{
    sendResponse(socket, {
        {QStringLiteral("request_id"), requestId},
        {QStringLiteral("type"), QStringLiteral("error")},
        {QStringLiteral("ok"), false},
        {QStringLiteral("error"), QJsonObject{
             {QStringLiteral("code"), code}, {QStringLiteral("message"), message}}},
    });
    if (closeAfter && socket != nullptr) {
        socket->flush();
        socket->disconnectFromServer();
    }
}

Contract NativeIbkrBridge::toIbContract(const ContractSpec &spec)
{
    Contract contract;
    contract.conId = static_cast<long>(spec.conId);
    contract.symbol = spec.symbol.toStdString();
    contract.secType = spec.securityType.toStdString();
    contract.exchange = spec.exchange.toStdString();
    contract.primaryExchange = spec.primaryExchange.toStdString();
    contract.currency = spec.currency.toStdString();
    contract.lastTradeDateOrContractMonth = spec.expiry.toStdString();
    contract.multiplier = spec.multiplier.toStdString();
    contract.tradingClass = spec.tradingClass.toStdString();
    return contract;
}

QString NativeIbkrBridge::safeSdkMessage(const std::string &message)
{
    QString text = QString::fromStdString(message).left(500);
    text.replace(QLatin1Char('\n'), QLatin1Char(' '));
    text.replace(QLatin1Char('\r'), QLatin1Char(' '));
    return text;
}

bool NativeIbkrBridge::informationalError(int errorCode)
{
    switch (errorCode) {
    case 1101:
    case 1102:
    case 2103:
    case 2104:
    case 2105:
    case 2106:
    case 2107:
    case 2108:
    case 2157:
    case 2158:
        return true;
    default:
        return false;
    }
}

} // namespace machome::ibkr
