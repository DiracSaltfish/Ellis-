#include "tgw/NativeTgwAdapter.h"

#include "tgw/SubscriptionPlan.h"

#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QUuid>

#include <algorithm>
#include <cmath>
#include <exception>
#include <limits>
#include <memory>
#include <random>
#include <utility>

namespace premium::native_tgw {
namespace {

QByteArray compact(const QJsonObject &object)
{
    return QJsonDocument(object).toJson(QJsonDocument::Compact);
}

qint64 wallNanoseconds()
{
    const auto now = std::chrono::system_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

qint64 monotonicNanoseconds()
{
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

QString newSessionId(const QString &prefix)
{
    return prefix + QUuid::createUuid().toString(QUuid::WithoutBraces);
}

QString feedName(const QString &symbol)
{
    return symbol.endsWith(QStringLiteral(".HK")) ? QStringLiteral("hkt")
                                                   : QStringLiteral("domestic");
}

QJsonArray tenLevels(qint64 first, qint64 step)
{
    QJsonArray values;
    for (int index = 0; index < 10; ++index) values.append(first + index * step);
    return values;
}

QString pipeLevels(qint64 first, qint64 step, int levels)
{
    QStringList values;
    for (int index = 0; index < levels; ++index) values.append(QString::number(first + index * step));
    return values.join(u'|');
}

struct SimulationState {
    qint64 iopv = 0;
    qint64 last = 0;
    qint64 high = 0;
    qint64 low = 0;
    qint64 volume = 1'000'000;
    qint64 amount = 0;
    qint64 trades = 100;
};

} // namespace

NativeTgwAdapter::NativeTgwAdapter(AdapterOptions options, QObject *parent)
    : QObject(parent), options_(std::move(options))
{
    reconnectTimer_.setSingleShot(true);
    reconnectTimer_.setInterval(250);
    QObject::connect(&reconnectTimer_, &QTimer::timeout, this,
                     [this] { connectCore(); });
    QObject::connect(&coreSocket_, &QLocalSocket::connected, this,
                     [this] { coreConnected(); });
    QObject::connect(&coreSocket_, &QLocalSocket::readyRead, this,
                     [this] { readControl(); });
    QObject::connect(&coreSocket_, &QLocalSocket::disconnected, this,
                     [this] { coreDisconnected(); });
    QObject::connect(&coreSocket_, &QLocalSocket::errorOccurred, this,
                     [this](QLocalSocket::LocalSocketError) {
        if (!stopRequested_.load() && coreSocket_.state() == QLocalSocket::UnconnectedState
            && !reconnectTimer_.isActive()) reconnectTimer_.start();
    });
}

NativeTgwAdapter::~NativeTgwAdapter()
{
    stop();
    if (options_.liveConfig) {
        std::fill(options_.liveConfig->password.begin(), options_.liveConfig->password.end(), '\0');
        options_.liveConfig->password.clear();
    }
}

bool NativeTgwAdapter::start(QString *error)
{
    if (started_) return true;
    if (options_.socketPath.isEmpty()) {
        if (error) *error = QStringLiteral("adapter socket path is empty");
        return false;
    }
    if (!options_.simulation && !options_.liveConfig.has_value()) {
        if (error) *error = QStringLiteral("live mode requires a native TGW configuration");
        return false;
    }
    if (!loadWatchlist(error)) return false;
    if (!options_.logPath.isEmpty()) {
        QDir().mkpath(QFileInfo(options_.logPath).absolutePath());
        logFile_.setFileName(options_.logPath);
        if (!logFile_.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
            if (error) *error = QStringLiteral("cannot open adapter log: %1").arg(logFile_.errorString());
            return false;
        }
    }
    stopRequested_.store(false);
    setCurrentSessionId(newSessionId(QStringLiteral("native-inactive-")));
    started_ = true;
    worker_ = std::thread([this] { workerMain(); });
    connectCore();
    writeLog(QStringLiteral("INFO"), QStringLiteral("native_adapter_started"),
             {{"simulation", options_.simulation}, {"defaults", static_cast<int>(desired_.size())}});
    return true;
}

void NativeTgwAdapter::stop()
{
    if (!started_) return;
    stopRequested_.store(true);
    {
        std::scoped_lock lock(stateMutex_);
        bridgeConnected_ = false;
        quotesDesired_ = false;
        ++resetGeneration_;
    }
    stateChanged_.notify_all();
    reconnectTimer_.stop();
    coreSocket_.abort();
    if (worker_.joinable()) worker_.join();
    clearEventQueue();
    writeLog(QStringLiteral("INFO"), QStringLiteral("native_adapter_stopped"));
    logFile_.close();
    started_ = false;
}

bool NativeTgwAdapter::loadWatchlist(QString *error)
{
    QFile file(options_.watchlistPath);
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("cannot open watchlist: %1").arg(file.errorString());
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    QJsonArray values;
    if (document.isArray()) values = document.array();
    else if (document.isObject()) values = document.object().value(QStringLiteral("symbols")).toArray();
    else {
        if (error) *error = QStringLiteral("invalid watchlist JSON: %1").arg(parseError.errorString());
        return false;
    }
    std::set<QString> loaded;
    for (const QJsonValue &value : values) {
        if (!value.isString()) {
            if (error) *error = QStringLiteral("watchlist contains a non-string symbol");
            return false;
        }
        const QString symbol = value.toString().trimmed().toUpper();
        QString itemError;
        if (!subscriptionItemForSymbol(symbol, &itemError)) {
            if (error) *error = itemError;
            return false;
        }
        loaded.insert(symbol);
    }
    std::scoped_lock lock(stateMutex_);
    desired_ = std::move(loaded);
    quotesDesired_ = options_.simulation;
    return true;
}

void NativeTgwAdapter::connectCore()
{
    if (stopRequested_.load()
        || coreSocket_.state() == QLocalSocket::ConnectedState
        || coreSocket_.state() == QLocalSocket::ConnectingState) return;
    coreSocket_.connectToServer(options_.socketPath, QIODevice::ReadWrite);
}

void NativeTgwAdapter::coreConnected()
{
    controlBuffer_.clear();
    sequence_ = 0;
    bridgeEpoch_.fetch_add(1);
    bridgeResetScheduled_.store(false);
    {
        std::scoped_lock lock(stateMutex_);
        bridgeConnected_ = true;
        ++resetGeneration_;
    }
    clearEventQueue();
    stateChanged_.notify_all();
    sendStatus(QStringLiteral("connected_to_core"),
               {{"bridge_epoch", static_cast<qint64>(bridgeEpoch_.load())},
                {"implementation", "native-cpp"}});
}

void NativeTgwAdapter::coreDisconnected()
{
    bool wasConnected = false;
    {
        std::scoped_lock lock(stateMutex_);
        wasConnected = bridgeConnected_;
        bridgeConnected_ = false;
        ++resetGeneration_;
    }
    bridgeEpoch_.fetch_add(1);
    clearEventQueue();
    stateChanged_.notify_all();
    if (wasConnected) writeLog(QStringLiteral("WARN"), QStringLiteral("core_bridge_disconnected"));
    if (!stopRequested_.load() && !reconnectTimer_.isActive()) reconnectTimer_.start();
}

void NativeTgwAdapter::readControl()
{
    controlBuffer_.append(coreSocket_.readAll());
    if (controlBuffer_.size() > 64 * 1024 * 1024) {
        resetBridge(QStringLiteral("control_buffer_limit"));
        return;
    }
    const QList<QByteArray> payloads = takeLengthPrefixedFrames(controlBuffer_);
    for (const QByteArray &payload : payloads) {
        BridgeFrame frame;
        QString decodeError;
        if (!BridgeFrame::decodeProtobuf(payload, &frame, &decodeError)
            || frame.kind != BridgeFrame::Kind::Control) {
            sendStatus(QStringLiteral("control_rejected"), {{"error", decodeError}});
            continue;
        }
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(frame.payloadJson, &parseError);
        if (!document.isObject()) {
            sendStatus(QStringLiteral("control_rejected"),
                       {{"error", QStringLiteral("invalid JSON: %1").arg(parseError.errorString())}});
            continue;
        }
        applyControl(document.object());
    }
}

void NativeTgwAdapter::applyControl(const QJsonObject &request)
{
    if (request.value(QStringLiteral("op")).toString() != QStringLiteral("set_symbols")
        || !request.value(QStringLiteral("symbols")).isArray()
        || !request.value(QStringLiteral("quotes_desired")).isBool()) {
        sendStatus(QStringLiteral("control_rejected"), {{"error", "invalid set_symbols schema"}});
        return;
    }
    std::set<QString> next;
    for (const QJsonValue &value : request.value(QStringLiteral("symbols")).toArray()) {
        if (!value.isString()) {
            sendStatus(QStringLiteral("control_rejected"), {{"error", "symbol must be string"}});
            return;
        }
        const QString symbol = value.toString().trimmed().toUpper();
        QString itemError;
        if (!subscriptionItemForSymbol(symbol, &itemError)) {
            sendStatus(QStringLiteral("control_rejected"), {{"error", itemError}});
            return;
        }
        next.insert(symbol);
    }
    bool changed = false;
    bool quotes = request.value(QStringLiteral("quotes_desired")).toBool() && !next.empty();
    {
        std::scoped_lock lock(stateMutex_);
        changed = next != desired_ || quotes != quotesDesired_;
        desired_ = std::move(next);
        quotesDesired_ = quotes;
    }
    if (changed) stateChanged_.notify_all();
    sendStatus(QStringLiteral("desired_applied"),
               {{"symbols", static_cast<int>(stateSnapshot().desired.size())},
                {"quotes_desired", quotes}});
}

bool NativeTgwAdapter::sendFrame(BridgeFrame frame, const QString &explicitSession)
{
    if (coreSocket_.state() != QLocalSocket::ConnectedState) return false;
    if (coreSocket_.bytesToWrite() > BridgeWriteLimit) {
        resetBridge(QStringLiteral("bridge_write_backlog"));
        return false;
    }
    frame.sequence = ++sequence_;
    frame.sessionId = explicitSession.isEmpty() ? currentSessionId() : explicitSession;
    const QByteArray encoded = frame.encodeLengthPrefixed();
    if (coreSocket_.write(encoded) != encoded.size()) {
        resetBridge(QStringLiteral("bridge_write_failed"));
        return false;
    }
    return true;
}

void NativeTgwAdapter::sendStatus(const QString &message, const QJsonObject &detail)
{
    BridgeFrame frame;
    frame.kind = BridgeFrame::Kind::AdapterStatus;
    frame.message = message;
    frame.payloadJson = compact(detail);
    {
        std::scoped_lock lock(eventMutex_);
        frame.sdkQueueDepth = static_cast<quint32>(std::min<std::size_t>(
            events_.size(), std::numeric_limits<quint32>::max()));
    }
    (void)sendFrame(std::move(frame));
    writeLog(message.contains(QStringLiteral("failed")) ? QStringLiteral("WARN")
                                                         : QStringLiteral("INFO"),
             message, detail);
}

void NativeTgwAdapter::queueStatus(const QString &message, const QJsonObject &detail)
{
    QMetaObject::invokeMethod(this, [this, message, detail] {
        if (!stopRequested_.load()) sendStatus(message, detail);
    }, Qt::QueuedConnection);
}

void NativeTgwAdapter::writeLog(const QString &level, const QString &message,
                                const QJsonObject &detail)
{
    QJsonObject record{{"time", QDateTime::currentDateTime().toString(Qt::ISODateWithMs)},
                       {"level", level}, {"component", "native_tgw"},
                       {"message", message}, {"detail", detail}};
    const QByteArray line = compact(record) + '\n';
    if (logFile_.isOpen()) {
        logFile_.write(line);
        logFile_.flush();
    }
    if (level == QStringLiteral("WARN") || level == QStringLiteral("ERROR"))
        qWarning().noquote() << QString::fromUtf8(line).trimmed();
    else
        qInfo().noquote() << QString::fromUtf8(line).trimmed();
}

QString NativeTgwAdapter::currentSessionId() const
{
    std::scoped_lock lock(sessionMutex_);
    return sessionId_;
}

void NativeTgwAdapter::setCurrentSessionId(QString sessionId)
{
    std::scoped_lock lock(sessionMutex_);
    sessionId_ = std::move(sessionId);
}

QString NativeTgwAdapter::redact(QString value) const
{
    if (!options_.liveConfig) return value;
    for (const std::string &secret : {options_.liveConfig->username, options_.liveConfig->password}) {
        if (!secret.empty()) value.replace(QString::fromStdString(secret), QStringLiteral("<redacted>"));
    }
    return value;
}

NativeTgwAdapter::StateSnapshot NativeTgwAdapter::stateSnapshot() const
{
    std::scoped_lock lock(stateMutex_);
    return {desired_, quotesDesired_, bridgeConnected_, resetGeneration_};
}

void NativeTgwAdapter::requestSessionReset()
{
    {
        std::scoped_lock lock(stateMutex_);
        ++resetGeneration_;
    }
    stateChanged_.notify_all();
}

void NativeTgwAdapter::enqueueEvent(PendingEvent event)
{
    bool overflow = false;
    {
        std::scoped_lock lock(eventMutex_);
        if (events_.size() >= EventLimit) {
            events_.clear();
            queueDrops_.fetch_add(1);
            overflow = true;
        } else {
            events_.push_back(std::move(event));
        }
    }
    if (overflow) {
        requestBridgeReset(QStringLiteral("native_event_queue_overflow"));
        return;
    }
    if (!drainScheduled_.exchange(true)) {
        QMetaObject::invokeMethod(this, [this] { drainEvents(); }, Qt::QueuedConnection);
    }
}

void NativeTgwAdapter::drainEvents()
{
    drainScheduled_.store(false);
    std::vector<PendingEvent> batch;
    std::size_t remaining = 0;
    {
        std::scoped_lock lock(eventMutex_);
        const std::size_t count = std::min<std::size_t>(events_.size(), 512);
        batch.reserve(count);
        for (std::size_t index = 0; index < count; ++index) {
            batch.push_back(std::move(events_.front()));
            events_.pop_front();
        }
        remaining = events_.size();
    }
    const StateSnapshot state = stateSnapshot();
    for (const PendingEvent &event : batch) {
        if (!state.bridgeConnected || !state.quotesDesired
            || event.bridgeEpoch != bridgeEpoch_.load()
            || !state.desired.contains(event.symbol)) continue;
        BridgeFrame frame;
        frame.kind = BridgeFrame::Kind::MarketEvent;
        frame.receiveWallNs = event.receiveWallNs;
        frame.receiveMonotonicNs = event.receiveMonotonicNs;
        frame.isDelta = event.isDelta;
        frame.tag = event.tag;
        frame.payloadJson = event.payload;
        frame.sdkQueueDepth = static_cast<quint32>(std::min<std::size_t>(
            remaining, std::numeric_limits<quint32>::max()));
        if (!sendFrame(std::move(frame), event.sessionId)) break;
    }
    {
        std::scoped_lock lock(eventMutex_);
        remaining = events_.size();
    }
    if (remaining > 0 && !drainScheduled_.exchange(true)) {
        QMetaObject::invokeMethod(this, [this] { drainEvents(); }, Qt::QueuedConnection);
    }
}

void NativeTgwAdapter::requestBridgeReset(const QString &reason)
{
    if (bridgeResetScheduled_.exchange(true)) return;
    QMetaObject::invokeMethod(this, [this, reason] { resetBridge(reason); }, Qt::QueuedConnection);
}

void NativeTgwAdapter::resetBridge(const QString &reason)
{
    writeLog(QStringLiteral("ERROR"), reason,
             {{"queue_drops", static_cast<qint64>(queueDrops_.load())}});
    {
        std::scoped_lock lock(stateMutex_);
        bridgeConnected_ = false;
        ++resetGeneration_;
    }
    stateChanged_.notify_all();
    clearEventQueue();
    coreSocket_.abort();
    if (!stopRequested_.load() && !reconnectTimer_.isActive()) reconnectTimer_.start();
}

void NativeTgwAdapter::clearEventQueue()
{
    std::scoped_lock lock(eventMutex_);
    events_.clear();
}

void NativeTgwAdapter::workerMain()
{
    try {
        if (options_.simulation) simulationLoop();
        else liveLoop();
    } catch (const std::exception &exception) {
        queueStatus(QStringLiteral("native_worker_failed"),
                    {{"error", redact(QString::fromUtf8(exception.what()))}});
    }
}

void NativeTgwAdapter::waitForState(std::chrono::milliseconds duration)
{
    std::unique_lock lock(stateMutex_);
    stateChanged_.wait_for(lock, duration);
}

void NativeTgwAdapter::unsubscribeAll(tgw::Session &session,
                                      std::map<QString, tgw::SubscribeItem> &active,
                                      bool report)
{
    for (const QString &feed : {QStringLiteral("domestic"), QStringLiteral("hkt")}) {
        std::vector<std::pair<QString, tgw::SubscribeItem>> values;
        for (const auto &[symbol, item] : active) {
            if (feedName(symbol) == feed) values.emplace_back(symbol, item);
        }
        for (std::size_t offset = 0; offset < values.size(); offset += SubscriptionBatchSize) {
            const std::size_t end = std::min(values.size(), offset + SubscriptionBatchSize);
            std::vector<tgw::SubscribeItem> request;
            for (std::size_t index = offset; index < end; ++index) request.push_back(values[index].second);
            session.unsubscribe(request);
            for (std::size_t index = offset; index < end; ++index) active.erase(values[index].first);
            if (report) queueStatus(QStringLiteral("unsubscribe_accepted"),
                                    {{"symbols", static_cast<int>(request.size())},
                                     {"feed", feed}, {"active", static_cast<int>(active.size())}});
        }
    }
}

void NativeTgwAdapter::subscribeBatch(
    tgw::Session &session,
    const std::vector<std::pair<QString, tgw::SubscribeItem>> &batch,
    std::map<QString, tgw::SubscribeItem> &active,
    std::map<QString, RetryState> &retries,
    int &callBudget)
{
    if (batch.empty()) return;
    if (callBudget <= 0) {
        const auto now = std::chrono::steady_clock::now();
        for (const auto &[symbol, item] : batch) {
            (void)item;
            RetryState &retry = retries[symbol];
            retry.failures += 1;
            retry.delay = std::min(std::chrono::seconds(300),
                                   retry.failures == 1 ? std::chrono::seconds(1)
                                                       : retry.delay * 2);
            retry.retryAt = now + retry.delay;
        }
        queueStatus(QStringLiteral("subscribe_batch_backoff"),
                    {{"symbols", static_cast<int>(batch.size())},
                     {"reason", "reconcile_call_budget"}});
        return;
    }
    --callBudget;
    std::vector<tgw::SubscribeItem> request;
    request.reserve(batch.size());
    for (const auto &[symbol, item] : batch) {
        (void)symbol;
        request.push_back(item);
    }
    const auto started = std::chrono::steady_clock::now();
    try {
        session.subscribe(request);
        for (const auto &[symbol, item] : batch) {
            active[symbol] = item;
            retries.erase(symbol);
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count();
        queueStatus(QStringLiteral("subscribe_accepted"),
                    {{"symbols", static_cast<int>(batch.size())},
                     {"feed", feedName(batch.front().first)},
                     {"active", static_cast<int>(active.size())},
                     {"latency_ms", elapsed / 1000.0}});
    } catch (const tgw::ProtocolError &exception) {
        const QString message = QString::fromUtf8(exception.what());
        if (!message.contains(QStringLiteral("subscription was rejected"))) throw;
        if (batch.size() > 1) {
            const auto middle = batch.begin() + static_cast<std::ptrdiff_t>(batch.size() / 2);
            subscribeBatch(session,
                           std::vector<std::pair<QString, tgw::SubscribeItem>>(batch.begin(), middle),
                           active, retries, callBudget);
            subscribeBatch(session,
                           std::vector<std::pair<QString, tgw::SubscribeItem>>(middle, batch.end()),
                           active, retries, callBudget);
            return;
        }
        RetryState &retry = retries[batch.front().first];
        retry.failures += 1;
        retry.delay = std::min(std::chrono::seconds(300),
                               retry.failures == 1 ? std::chrono::seconds(1)
                                                   : retry.delay * 2);
        retry.retryAt = std::chrono::steady_clock::now() + retry.delay;
        queueStatus(QStringLiteral("subscribe_symbol_backoff"),
                    {{"symbol", batch.front().first}, {"failures", retry.failures},
                     {"retry_sec", static_cast<int>(retry.delay.count())}});
    }
}

void NativeTgwAdapter::reconcileSubscriptions(
    tgw::Session &session,
    const std::set<QString> &desired,
    std::map<QString, tgw::SubscribeItem> &active,
    std::map<QString, RetryState> &retries)
{
    for (const QString &feed : {QStringLiteral("domestic"), QStringLiteral("hkt")}) {
        std::vector<std::pair<QString, tgw::SubscribeItem>> remove;
        for (const auto &[symbol, item] : active) {
            if (!desired.contains(symbol) && feedName(symbol) == feed) remove.emplace_back(symbol, item);
        }
        for (std::size_t offset = 0; offset < remove.size(); offset += SubscriptionBatchSize) {
            const std::size_t end = std::min(remove.size(), offset + SubscriptionBatchSize);
            std::vector<tgw::SubscribeItem> request;
            for (std::size_t index = offset; index < end; ++index) request.push_back(remove[index].second);
            session.unsubscribe(request);
            for (std::size_t index = offset; index < end; ++index) {
                active.erase(remove[index].first);
                retries.erase(remove[index].first);
            }
            queueStatus(QStringLiteral("unsubscribe_accepted"),
                        {{"symbols", static_cast<int>(request.size())}, {"feed", feed},
                         {"active", static_cast<int>(active.size())}});
        }
    }

    int callBudget = SubscribeCallBudget;
    const auto now = std::chrono::steady_clock::now();
    for (const QString &feed : {QStringLiteral("domestic"), QStringLiteral("hkt")}) {
        std::vector<std::pair<QString, tgw::SubscribeItem>> add;
        for (const QString &symbol : desired) {
            if (active.contains(symbol) || feedName(symbol) != feed) continue;
            const auto retry = retries.find(symbol);
            if (retry != retries.end() && retry->second.retryAt > now) continue;
            QString error;
            const auto item = subscriptionItemForSymbol(symbol, &error);
            if (!item) {
                queueStatus(QStringLiteral("subscription_item_rejected"),
                            {{"symbol", symbol}, {"error", error}});
                continue;
            }
            add.emplace_back(symbol, *item);
        }
        for (std::size_t offset = 0; offset < add.size(); offset += SubscriptionBatchSize) {
            const std::size_t end = std::min(add.size(), offset + SubscriptionBatchSize);
            subscribeBatch(session,
                           std::vector<std::pair<QString, tgw::SubscribeItem>>(
                               add.begin() + static_cast<std::ptrdiff_t>(offset),
                               add.begin() + static_cast<std::ptrdiff_t>(end)),
                           active, retries, callBudget);
        }
    }
}

void NativeTgwAdapter::liveLoop()
{
    std::unique_ptr<tgw::Session> session;
    std::map<QString, tgw::SubscribeItem> active;
    std::map<QString, RetryState> retries;
    quint64 observedReset = std::numeric_limits<quint64>::max();
    int reconnectSeconds = 1;

    while (!stopRequested_.load()) {
        const StateSnapshot state = stateSnapshot();
        if (state.resetGeneration != observedReset) {
            observedReset = state.resetGeneration;
            if (session) session->close();
            session.reset();
            active.clear();
            retries.clear();
            clearEventQueue();
            setCurrentSessionId(newSessionId(QStringLiteral("native-inactive-")));
        }
        if (!state.bridgeConnected || !state.quotesDesired || state.desired.empty()) {
            if (session) {
                try {
                    if (state.bridgeConnected && !active.empty()) unsubscribeAll(*session, active, true);
                } catch (const std::exception &exception) {
                    queueStatus(QStringLiteral("unsubscribe_close_failed"),
                                {{"error", redact(QString::fromUtf8(exception.what()))}});
                }
                session->close();
                session.reset();
                active.clear();
                retries.clear();
                clearEventQueue();
                setCurrentSessionId(newSessionId(QStringLiteral("native-closed-")));
                queueStatus(QStringLiteral("tgw_session_closed"),
                            {{"reason", state.bridgeConnected ? "quotes_not_desired" : "core_disconnected"}});
            }
            waitForState(std::chrono::milliseconds(250));
            continue;
        }

        try {
            if (!session) {
                session = std::make_unique<tgw::Session>(*options_.liveConfig);
                const tgw::LoginInfo login = session->connect_and_login();
                if (!login.authenticated) {
                    throw tgw::ProtocolError("TGW login rejected status="
                        + std::to_string(login.status) + " tag=" + login.response_tag);
                }
                setCurrentSessionId(newSessionId(QStringLiteral("native-live-")));
                reconnectSeconds = 1;
                queueStatus(QStringLiteral("tgw_logged_in"),
                            {{"desired", static_cast<int>(state.desired.size())},
                             {"active_host", QString::fromStdString(session->active_host())},
                             {"client_version", QString::fromStdString(options_.liveConfig->client_version)},
                             {"implementation", "native-cpp"}});
            }
            reconcileSubscriptions(*session, state.desired, active, retries);
            try {
                const std::string raw = session->receive_raw_event(std::chrono::milliseconds(250));
                const QByteArray payload(raw.data(), static_cast<qsizetype>(raw.size()));
                RawEventMetadata metadata;
                QString validationError;
                if (!inspectRawEvent(payload, &metadata, &validationError)) {
                    const quint64 invalid = invalidEvents_.fetch_add(1) + 1;
                    if (invalid == 1 || invalid % 100 == 0) {
                        queueStatus(QStringLiteral("raw_event_quarantined"),
                                    {{"error", validationError}, {"count", static_cast<qint64>(invalid)}});
                    }
                    continue;
                }
                enqueueEvent({payload, metadata.tag, metadata.symbol, currentSessionId(),
                              wallNanoseconds(), monotonicNanoseconds(), bridgeEpoch_.load(),
                              metadata.isDelta});
            } catch (const tgw::TimeoutError &) {
            }
        } catch (const std::exception &exception) {
            const QString safe = redact(QString::fromUtf8(exception.what()));
            if (session) session->close();
            session.reset();
            active.clear();
            retries.clear();
            clearEventQueue();
            setCurrentSessionId(newSessionId(QStringLiteral("native-failed-")));
            queueStatus(QStringLiteral("tgw_connection_failed"),
                        {{"error", safe}, {"retry_sec", reconnectSeconds}});
            waitForState(std::chrono::seconds(reconnectSeconds));
            reconnectSeconds = std::min(300, reconnectSeconds * 2);
        }
    }
    if (session) session->close();
}

void NativeTgwAdapter::simulationLoop()
{
    std::map<QString, SimulationState> states;
    std::set<QString> fullPending;
    std::set<QString> previousDesired;
    quint64 observedReset = std::numeric_limits<quint64>::max();
    std::mt19937 random(8421);
    quint64 tick = 0;
    bool sessionAnnounced = false;
    bool sessionActive = false;

    while (!stopRequested_.load()) {
        const StateSnapshot state = stateSnapshot();
        if (state.resetGeneration != observedReset) {
            observedReset = state.resetGeneration;
            states.clear();
            fullPending = state.desired;
            clearEventQueue();
            setCurrentSessionId(newSessionId(QStringLiteral("native-sim-")));
            sessionAnnounced = false;
        }
        if (!state.bridgeConnected || !state.quotesDesired || state.desired.empty()) {
            if (sessionActive) {
                states.clear();
                fullPending.clear();
                previousDesired.clear();
                clearEventQueue();
                setCurrentSessionId(newSessionId(QStringLiteral("native-sim-closed-")));
                queueStatus(QStringLiteral("tgw_session_closed"),
                            {{"reason", state.bridgeConnected ? "quotes_not_desired" : "core_disconnected"},
                             {"simulation", true}});
                sessionAnnounced = false;
                sessionActive = false;
            }
            waitForState(std::chrono::milliseconds(250));
            continue;
        }
        if (!sessionActive) {
            states.clear();
            previousDesired.clear();
            fullPending = state.desired;
            clearEventQueue();
            setCurrentSessionId(newSessionId(QStringLiteral("native-sim-")));
            sessionAnnounced = false;
            sessionActive = true;
        }
        if (!sessionAnnounced) {
            queueStatus(QStringLiteral("simulation_session_started"),
                        {{"desired", static_cast<int>(state.desired.size())}});
            sessionAnnounced = true;
        }
        for (const QString &symbol : state.desired) {
            if (!previousDesired.contains(symbol)) fullPending.insert(symbol);
            if (!states.contains(symbol)) {
                bool ok = false;
                const qint64 seed = symbol.left(symbol.indexOf(u'.')).toLongLong(&ok);
                const qint64 base = 800'000 + (ok ? seed % 400 : 0) * 1'000;
                states[symbol] = {base, base, base, base, 1'000'000, base * 10, 100};
            }
        }
        for (auto it = states.begin(); it != states.end();) {
            if (!state.desired.contains(it->first)) {
                fullPending.erase(it->first);
                it = states.erase(it);
            } else ++it;
        }
        previousDesired = state.desired;
        std::vector<QString> symbols(state.desired.begin(), state.desired.end());
        const std::size_t count = std::min<std::size_t>(20, symbols.size());
        const std::size_t start = symbols.empty() ? 0 : static_cast<std::size_t>(tick % symbols.size());
        for (std::size_t index = 0; index < count; ++index) {
            const QString symbol = symbols[(start + index) % symbols.size()];
            SimulationState &value = states[symbol];
            const qint64 movement = (static_cast<int>(random() % 3) - 1) * 1'000;
            value.last = std::max<qint64>(1'000, value.last + movement);
            value.high = std::max(value.high, value.last);
            value.low = std::min(value.low, value.last);
            value.volume += 10'000;
            value.amount += value.last;
            ++value.trades;
            const bool full = fullPending.erase(symbol) > 0 || tick % 2'000 == 0;
            QJsonObject data;
            const bool hkt = symbol.endsWith(QStringLiteral(".HK"));
            if (hkt) {
                const qint64 exchangeTime = QDateTime::currentDateTime().toString(
                    QStringLiteral("yyyyMMddHHmmsszzz")).toLongLong();
                if (full) {
                    data = {{"1", 102}, {"2", symbol.left(5)}, {"3", exchangeTime},
                            {"4", QString::fromLatin1("T0\0\0\0\0\0", 7)},
                            {"5", value.volume}, {"6", value.amount}, {"7", value.iopv},
                            {"8", value.last}, {"9", value.high}, {"10", value.low},
                            {"11", value.last}, {"12", pipeLevels(value.last, -1'000, 5)},
                            {"13", pipeLevels(100'000, 10'000, 5)},
                            {"14", pipeLevels(value.last + 1'000, 1'000, 5)},
                            {"15", pipeLevels(110'000, 10'000, 5)},
                            {"16", 0}, {"17", 0}, {"18", 0}, {"19", 0},
                            {"20", 0}, {"21", 0}, {"22", 0}, {"23", 6}};
                } else {
                    data = {{"1", 102}, {"2", symbol.left(5)}, {"3", exchangeTime},
                            {"5", value.volume}, {"6", value.amount}, {"8", value.last},
                            {"9", value.high}, {"10", value.low}, {"11", value.last},
                            {"12", pipeLevels(value.last, -1'000, 5)},
                            {"13", pipeLevels(100'000, 10'000, 5)},
                            {"14", pipeLevels(value.last + 1'000, 1'000, 5)},
                            {"15", pipeLevels(110'000, 10'000, 5)}};
                }
            } else if (full) {
                data = {{"security_code", symbol},
                        {"market_type", symbol.endsWith(QStringLiteral(".SH")) ? 101 : 102},
                        {"variety_category", 2}, {"orig_time", QDateTime::currentMSecsSinceEpoch()},
                        {"last_price", value.last}, {"open_price", value.iopv},
                        {"high_price", value.high}, {"low_price", value.low}, {"close_price", 0},
                        {"pre_close_price", value.iopv},
                        {"bid_price", tenLevels(value.last, -1'000)},
                        {"offer_price", tenLevels(value.last + 1'000, 1'000)},
                        {"bid_volume", tenLevels(100'000, 10'000)},
                        {"offer_volume", tenLevels(110'000, 10'000)},
                        {"total_volume_trade", value.volume}, {"total_value_trade", value.amount},
                        {"num_trades", value.trades}, {"trading_phase_code", "T"},
                        {"IOPV", value.iopv}, {"high_limited", value.iopv * 11 / 10},
                        {"low_limited", value.iopv * 9 / 10}};
            } else {
                data = {{"security_code", symbol}, {"orig_time", QDateTime::currentMSecsSinceEpoch()},
                        {"last_price", value.last}, {"high_price", value.high},
                        {"low_price", value.low}, {"bid_price", tenLevels(value.last, -1'000)},
                        {"offer_price", tenLevels(value.last + 1'000, 1'000)},
                        {"total_volume_trade", value.volume}, {"num_trades", value.trades}};
            }
            const QString tag = hkt ? QStringLiteral("16") : QStringLiteral("14");
            const QByteArray payload = compact({{"headers", QJsonObject{{"tag", tag}}},
                                                {"status", 0}, {"is_delta", full ? 0 : 1},
                                                {"data", data}, {"simulation", true}});
            RawEventMetadata metadata;
            QString validationError;
            if (!inspectRawEvent(payload, &metadata, &validationError)) {
                queueStatus(QStringLiteral("simulation_event_invalid"), {{"error", validationError}});
                continue;
            }
            enqueueEvent({payload, metadata.tag, metadata.symbol, currentSessionId(),
                          wallNanoseconds(), monotonicNanoseconds(), bridgeEpoch_.load(),
                          metadata.isDelta});
        }
        tick += count;
        waitForState(std::chrono::milliseconds(50));
    }
}

} // namespace premium::native_tgw
