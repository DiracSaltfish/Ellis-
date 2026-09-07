#include "PremiumClient.h"

#include <QAbstractSocket>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QMetaObject>
#include <QRegularExpression>
#include <QTcpSocket>
#include <QThread>
#include <QTimer>
#include <QUrl>
#include <QWebSocket>
#include <QWebSocketProtocol>

#include <algorithm>
#include <cmath>
#include <limits>

namespace machome::premium {
namespace {

constexpr auto SummaryChannel = "summary";
constexpr auto DetailChannel = "detail";
constexpr auto L1Channel = "l1";
constexpr int ClientDetailSymbolLimit = 4;

QString normalizedDetailSymbol(const QString &value)
{
    const QString symbol = value.trimmed().toUpper();
    static const QRegularExpression pattern(
        QStringLiteral("^[0-9]{6}\\.(?:SH|SZ)$"));
    return pattern.match(symbol).hasMatch() ? symbol : QString{};
}

QString socketReason(const QString &prefix, const QString &error)
{
    return error.isEmpty() ? prefix : QStringLiteral("%1: %2").arg(prefix, error);
}

} // namespace

PremiumClient::PremiumClient(QObject *parent)
    : PremiumClient(Config{}, parent)
{
}

PremiumClient::PremiumClient(const Config &config, QObject *parent)
    : QObject(parent)
    , config_(normalizedConfig(config))
{
    monotonicClock_.start();
    initializeObjects();
    applyLimits();
}

PremiumClient::~PremiumClient()
{
    // QObject instances must be destroyed in their affinity thread. Do the
    // eager cleanup only in that valid case; child destruction is otherwise
    // left to QObject's normal teardown diagnostics.
    if (!onOwnerThread()) {
        return;
    }

    running_ = false;
    summaryReconnectTimer_->stop();
    detailReconnectTimer_->stop();
    l1ReconnectTimer_->stop();
    summaryConnectTimeoutTimer_->stop();
    detailConnectTimeoutTimer_->stop();
    detailHelloTimeoutTimer_->stop();
    l1ConnectTimeoutTimer_->stop();
    l1HelloTimeoutTimer_->stop();
    freshnessTimer_->stop();
    l1PingTimer_->stop();
    l1StatusTimer_->stop();
    summarySocket_->abort();
    detailSocket_->abort();
    l1Socket_->abort();
}

PremiumClient::Config PremiumClient::normalizedConfig(Config config)
{
    config.host = config.host.trimmed();
    if (config.host.isEmpty()) {
        config.host = QStringLiteral("127.0.0.1");
    }

    config.connectTimeoutMs = std::max(config.connectTimeoutMs, 100);
    config.reconnectInitialMs = std::max(config.reconnectInitialMs, 50);
    config.reconnectMaximumMs = std::max(config.reconnectMaximumMs,
                                         config.reconnectInitialMs);
    config.freshnessPollMs = std::max(config.freshnessPollMs, 100);
    config.summaryStaleAfterMs = std::max(config.summaryStaleAfterMs, 500);
    config.l1StaleAfterMs = std::max(config.l1StaleAfterMs, 500);
    config.silenceDisconnectAfterMs = std::max(
        config.silenceDisconnectAfterMs,
        std::max(config.summaryStaleAfterMs, config.l1StaleAfterMs) + 500);
    config.l1PingIntervalMs = std::max(config.l1PingIntervalMs, 250);
    config.l1StatusIntervalMs = std::max(config.l1StatusIntervalMs, 250);

    config.maximumWebSocketMessageBytes = std::max<qint64>(
        config.maximumWebSocketMessageBytes, 1024);
    config.maximumL1LineBytes = std::max<qint64>(config.maximumL1LineBytes, 1024);
    config.maximumL1BufferBytes = std::max(
        config.maximumL1BufferBytes, config.maximumL1LineBytes + 1);
    config.maximumPendingWriteBytes = std::max<qint64>(
        config.maximumPendingWriteBytes, 1024);
    return config;
}

void PremiumClient::initializeObjects()
{
    summarySocket_ = new QWebSocket(QString(), QWebSocketProtocol::VersionLatest, this);
    detailSocket_ = new QWebSocket(QString(), QWebSocketProtocol::VersionLatest, this);
    l1Socket_ = new QTcpSocket(this);

    summaryReconnectTimer_ = new QTimer(this);
    detailReconnectTimer_ = new QTimer(this);
    l1ReconnectTimer_ = new QTimer(this);
    summaryConnectTimeoutTimer_ = new QTimer(this);
    detailConnectTimeoutTimer_ = new QTimer(this);
    detailHelloTimeoutTimer_ = new QTimer(this);
    l1ConnectTimeoutTimer_ = new QTimer(this);
    l1HelloTimeoutTimer_ = new QTimer(this);
    freshnessTimer_ = new QTimer(this);
    l1PingTimer_ = new QTimer(this);
    l1StatusTimer_ = new QTimer(this);

    summaryReconnectTimer_->setSingleShot(true);
    detailReconnectTimer_->setSingleShot(true);
    l1ReconnectTimer_->setSingleShot(true);
    summaryConnectTimeoutTimer_->setSingleShot(true);
    detailConnectTimeoutTimer_->setSingleShot(true);
    detailHelloTimeoutTimer_->setSingleShot(true);
    l1ConnectTimeoutTimer_->setSingleShot(true);
    l1HelloTimeoutTimer_->setSingleShot(true);

    connect(summaryReconnectTimer_, &QTimer::timeout, this, &PremiumClient::connectSummary);
    connect(detailReconnectTimer_, &QTimer::timeout, this, &PremiumClient::connectDetail);
    connect(l1ReconnectTimer_, &QTimer::timeout, this, &PremiumClient::connectL1);
    connect(summaryConnectTimeoutTimer_, &QTimer::timeout, this, [this] {
        if (!running_) {
            return;
        }
        Q_EMIT protocolError(QString::fromLatin1(SummaryChannel),
                             QStringLiteral("connection timed out"));
        summarySocket_->abort();
        scheduleSummaryReconnect(QStringLiteral("connection timeout"));
    });
    connect(detailConnectTimeoutTimer_, &QTimer::timeout, this, [this] {
        if (!running_ || desiredDetailSymbols_.isEmpty()) return;
        Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                             QStringLiteral("connection timed out"));
        detailSocket_->abort();
        scheduleDetailReconnect(QStringLiteral("connection timeout"));
    });
    connect(detailHelloTimeoutTimer_, &QTimer::timeout, this, [this] {
        if (!running_ || desiredDetailSymbols_.isEmpty() || detailHelloSeen_) return;
        Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                             QStringLiteral("hello timed out"));
        detailSocket_->abort();
        scheduleDetailReconnect(QStringLiteral("hello timeout"));
    });
    connect(l1ConnectTimeoutTimer_, &QTimer::timeout, this, [this] {
        if (!running_) {
            return;
        }
        Q_EMIT protocolError(QString::fromLatin1(L1Channel),
                             QStringLiteral("TCP connection timed out"));
        l1Socket_->abort();
        scheduleL1Reconnect(QStringLiteral("connection timeout"));
    });
    connect(l1HelloTimeoutTimer_, &QTimer::timeout, this, [this] {
        if (!running_ || l1HelloSeen_) {
            return;
        }
        failL1Protocol(QStringLiteral("hello timed out"));
    });
    connect(freshnessTimer_, &QTimer::timeout, this, &PremiumClient::checkFreshness);
    connect(l1PingTimer_, &QTimer::timeout, this, &PremiumClient::pingL1);
    connect(l1StatusTimer_, &QTimer::timeout, this, &PremiumClient::requestL1Status);

    connect(summarySocket_, &QWebSocket::connected,
            this, &PremiumClient::handleSummaryConnected);
    connect(summarySocket_, &QWebSocket::disconnected,
            this, &PremiumClient::handleSummaryDisconnected);
    connect(summarySocket_, &QWebSocket::textMessageReceived,
            this, &PremiumClient::handleSummaryText);
    connect(summarySocket_, &QWebSocket::binaryMessageReceived,
            this, &PremiumClient::handleSummaryBinary);
    connect(summarySocket_, &QWebSocket::errorOccurred,
            this, [this](QAbstractSocket::SocketError) { handleSummarySocketError(); });

    connect(detailSocket_, &QWebSocket::connected,
            this, &PremiumClient::handleDetailConnected);
    connect(detailSocket_, &QWebSocket::disconnected,
            this, &PremiumClient::handleDetailDisconnected);
    connect(detailSocket_, &QWebSocket::textMessageReceived,
            this, &PremiumClient::handleDetailText);
    connect(detailSocket_, &QWebSocket::binaryMessageReceived,
            this, &PremiumClient::handleDetailBinary);
    connect(detailSocket_, &QWebSocket::errorOccurred,
            this, [this](QAbstractSocket::SocketError) { handleDetailSocketError(); });

    connect(l1Socket_, &QTcpSocket::connected,
            this, &PremiumClient::handleL1Connected);
    connect(l1Socket_, &QTcpSocket::disconnected,
            this, &PremiumClient::handleL1Disconnected);
    connect(l1Socket_, &QTcpSocket::readyRead,
            this, &PremiumClient::handleL1ReadyRead);
    connect(l1Socket_, &QTcpSocket::errorOccurred,
            this, [this](QAbstractSocket::SocketError) { handleL1SocketError(); });
}

void PremiumClient::applyLimits()
{
    const auto websocketLimit = static_cast<quint64>(config_.maximumWebSocketMessageBytes);
    summarySocket_->setMaxAllowedIncomingFrameSize(websocketLimit);
    summarySocket_->setMaxAllowedIncomingMessageSize(websocketLimit);
    detailSocket_->setMaxAllowedIncomingFrameSize(websocketLimit);
    detailSocket_->setMaxAllowedIncomingMessageSize(websocketLimit);
    l1Socket_->setReadBufferSize(config_.maximumL1BufferBytes);

    freshnessTimer_->setInterval(config_.freshnessPollMs);
    l1PingTimer_->setInterval(config_.l1PingIntervalMs);
    l1StatusTimer_->setInterval(config_.l1StatusIntervalMs);
}

PremiumClient::Config PremiumClient::config() const
{
    return config_;
}

bool PremiumClient::isRunning() const
{
    return running_;
}

PremiumClient::ChannelState PremiumClient::summaryState() const
{
    return summaryState_;
}

PremiumClient::ChannelState PremiumClient::detailState() const
{
    return detailState_;
}

PremiumClient::ChannelState PremiumClient::l1State() const
{
    return l1State_;
}

bool PremiumClient::isSummaryFresh() const
{
    const qint64 age = summaryAgeMs();
    return running_ && age >= 0 && age <= config_.summaryStaleAfterMs
        && (summaryState_ == ChannelState::Connected
            || summaryState_ == ChannelState::Stale);
}

bool PremiumClient::isL1Fresh() const
{
    const qint64 age = l1AgeMs();
    return running_ && l1HelloSeen_ && age >= 0 && age <= config_.l1StaleAfterMs
        && (l1State_ == ChannelState::Connected
            || l1State_ == ChannelState::Stale);
}

qint64 PremiumClient::summaryAgeMs() const
{
    return ageSince(lastSummaryActivityMs_);
}

qint64 PremiumClient::l1AgeMs() const
{
    return ageSince(lastL1ActivityMs_);
}

QJsonObject PremiumClient::lastStatus() const
{
    return lastStatus_;
}

QJsonObject PremiumClient::lastL1Status() const
{
    return lastL1Status_;
}

QStringList PremiumClient::desiredDetailSymbols() const
{
    QStringList result(desiredDetailSymbols_.begin(), desiredDetailSymbols_.end());
    result.sort(Qt::CaseInsensitive);
    return result;
}

QStringList PremiumClient::acknowledgedDetailSymbols() const
{
    QStringList result(acknowledgedDetailSymbols_.begin(),
                       acknowledgedDetailSymbols_.end());
    result.sort(Qt::CaseInsensitive);
    return result;
}

void PremiumClient::setConfig(const Config &config)
{
    if (!onOwnerThread()) {
        const Config copy = config;
        QMetaObject::invokeMethod(this, [this, copy] { setConfig(copy); },
                                  Qt::QueuedConnection);
        return;
    }

    const bool restartAfterChange = running_;
    if (restartAfterChange) {
        stop();
    }
    config_ = normalizedConfig(config);
    applyLimits();
    if (restartAfterChange) {
        start();
    }
}

void PremiumClient::start()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::start, Qt::QueuedConnection);
        return;
    }
    if (running_) {
        return;
    }

    running_ = true;
    clearPendingSummaryMutations();
    summaryReconnectAttempt_ = 0;
    detailReconnectAttempt_ = 0;
    l1ReconnectAttempt_ = 0;
    consecutiveSummaryProtocolErrors_ = 0;
    consecutiveDetailProtocolErrors_ = 0;
    consecutiveL1ProtocolErrors_ = 0;
    lastSummaryActivityMs_ = -1;
    lastL1ActivityMs_ = -1;
    freshnessTimer_->start();
    Q_EMIT runningChanged(true);
    publishFreshness();
    connectSummary();
    if (!desiredDetailSymbols_.isEmpty()) connectDetail();
    connectL1();
}

void PremiumClient::stop()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::stop, Qt::QueuedConnection);
        return;
    }
    clearPendingSummaryMutations();
    if (!running_ && summaryState_ == ChannelState::Stopped
        && detailState_ == ChannelState::Stopped
        && l1State_ == ChannelState::Stopped) {
        return;
    }

    const bool wasRunning = running_;
    running_ = false;
    summaryReconnectTimer_->stop();
    detailReconnectTimer_->stop();
    l1ReconnectTimer_->stop();
    summaryConnectTimeoutTimer_->stop();
    detailConnectTimeoutTimer_->stop();
    detailHelloTimeoutTimer_->stop();
    l1ConnectTimeoutTimer_->stop();
    l1HelloTimeoutTimer_->stop();
    freshnessTimer_->stop();
    l1PingTimer_->stop();
    l1StatusTimer_->stop();
    summarySocket_->abort();
    detailSocket_->abort();
    l1Socket_->abort();
    resetDetailGeneration();
    l1InputBuffer_.clear();
    l1HelloSeen_ = false;
    lastSummaryActivityMs_ = -1;
    lastL1ActivityMs_ = -1;
    setSummaryState(ChannelState::Stopped, QStringLiteral("stopped"));
    setDetailState(ChannelState::Stopped,
                   desiredDetailSymbols_.isEmpty()
                       ? QStringLiteral("idle")
                       : QStringLiteral("stopped; subscriptions retained"));
    setL1State(ChannelState::Stopped, QStringLiteral("stopped"));
    publishFreshness();
    if (wasRunning) {
        Q_EMIT runningChanged(false);
    }
}

void PremiumClient::reconnect()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::reconnect, Qt::QueuedConnection);
        return;
    }
    stop();
    start();
}

void PremiumClient::connectSummary()
{
    if (!running_) {
        return;
    }

    summaryReconnectTimer_->stop();
    if (summarySocket_->state() != QAbstractSocket::UnconnectedState) {
        summarySocket_->abort();
        // abort() may synchronously emit disconnected(), whose handler starts
        // a retry timer. This attempt is already reconnecting now.
        summaryReconnectTimer_->stop();
    }

    QUrl url;
    url.setScheme(QStringLiteral("ws"));
    url.setHost(config_.host);
    url.setPort(config_.summaryPort);
    url.setPath(QStringLiteral("/ws/v2/summary"));

    setSummaryState(ChannelState::Connecting,
                    QStringLiteral("connecting to %1").arg(url.toString()));
    summaryConnectTimeoutTimer_->start(config_.connectTimeoutMs);
    summarySocket_->open(url);
}

void PremiumClient::connectDetail()
{
    if (!running_ || desiredDetailSymbols_.isEmpty()) {
        closeIdleDetailChannel();
        return;
    }

    detailReconnectTimer_->stop();
    detailHelloTimeoutTimer_->stop();
    resetDetailGeneration();
    if (detailSocket_->state() != QAbstractSocket::UnconnectedState) {
        detailSocket_->abort();
        detailReconnectTimer_->stop();
    }

    QUrl url;
    url.setScheme(QStringLiteral("ws"));
    url.setHost(config_.host);
    url.setPort(config_.summaryPort);
    url.setPath(QStringLiteral("/ws/v2/detail"));
    setDetailState(ChannelState::Connecting,
                   QStringLiteral("connecting to %1").arg(url.toString()));
    detailConnectTimeoutTimer_->start(config_.connectTimeoutMs);
    detailSocket_->open(url);
}

void PremiumClient::connectL1()
{
    if (!running_) {
        return;
    }

    l1ReconnectTimer_->stop();
    l1HelloTimeoutTimer_->stop();
    l1PingTimer_->stop();
    l1StatusTimer_->stop();
    l1HelloSeen_ = false;
    l1InputBuffer_.clear();
    if (l1Socket_->state() != QAbstractSocket::UnconnectedState) {
        l1Socket_->abort();
        l1ReconnectTimer_->stop();
    }

    setL1State(ChannelState::Connecting,
               QStringLiteral("connecting to %1:%2")
                   .arg(config_.host)
                   .arg(config_.l1Port));
    l1ConnectTimeoutTimer_->start(config_.connectTimeoutMs);
    l1Socket_->connectToHost(config_.host, config_.l1Port);
}

int PremiumClient::nextReconnectDelay(int &attempt) const
{
    const int shift = std::min(attempt, 20);
    const qint64 multiplier = qint64{1} << shift;
    const qint64 delay = std::min<qint64>(
        config_.reconnectMaximumMs,
        qint64{config_.reconnectInitialMs} * multiplier);
    if (attempt < std::numeric_limits<int>::max()) {
        ++attempt;
    }
    return static_cast<int>(delay);
}

void PremiumClient::scheduleSummaryReconnect(const QString &reason)
{
    if (!running_ || summaryReconnectTimer_->isActive()) {
        return;
    }
    summaryConnectTimeoutTimer_->stop();
    const int delay = nextReconnectDelay(summaryReconnectAttempt_);
    setSummaryState(ChannelState::Backoff,
                    QStringLiteral("%1; retry in %2 ms").arg(reason).arg(delay));
    summaryReconnectTimer_->start(delay);
}

void PremiumClient::scheduleDetailReconnect(const QString &reason)
{
    if (!running_ || desiredDetailSymbols_.isEmpty()
        || detailReconnectTimer_->isActive()) {
        return;
    }
    detailConnectTimeoutTimer_->stop();
    detailHelloTimeoutTimer_->stop();
    resetDetailGeneration();
    const int delay = nextReconnectDelay(detailReconnectAttempt_);
    setDetailState(ChannelState::Backoff,
                   QStringLiteral("%1; retry in %2 ms").arg(reason).arg(delay));
    detailReconnectTimer_->start(delay);
}

void PremiumClient::scheduleL1Reconnect(const QString &reason)
{
    if (!running_ || l1ReconnectTimer_->isActive()) {
        return;
    }
    l1ConnectTimeoutTimer_->stop();
    l1HelloTimeoutTimer_->stop();
    l1PingTimer_->stop();
    l1StatusTimer_->stop();
    l1HelloSeen_ = false;
    const int delay = nextReconnectDelay(l1ReconnectAttempt_);
    setL1State(ChannelState::Backoff,
               QStringLiteral("%1; retry in %2 ms").arg(reason).arg(delay));
    l1ReconnectTimer_->start(delay);
}

void PremiumClient::handleSummaryConnected()
{
    summaryConnectTimeoutTimer_->stop();
    summaryReconnectAttempt_ = 0;
    consecutiveSummaryProtocolErrors_ = 0;
    markSummaryActivity();
    setSummaryState(ChannelState::Connected, QStringLiteral("WebSocket connected"));
    publishFreshness();
    requestStatus();
}

void PremiumClient::handleSummaryDisconnected()
{
    summaryConnectTimeoutTimer_->stop();
    // ACKs are scoped to one WebSocket generation. Once that generation is
    // gone, retaining either flag would make every later list replacement
    // look permanently busy even after a clean reconnect or operator review.
    clearPendingSummaryMutations();
    if (!running_) {
        return;
    }
    // Ignore a queued notification belonging to an intentional abort if the
    // same QWebSocket has already begun its next connection attempt.
    if (summarySocket_->state() != QAbstractSocket::UnconnectedState) {
        return;
    }

    QString reason = summarySocket_->closeReason();
    if (reason.isEmpty()) {
        reason = summarySocket_->errorString();
    }
    scheduleSummaryReconnect(socketReason(QStringLiteral("WebSocket disconnected"), reason));
}

void PremiumClient::handleSummarySocketError()
{
    if (!running_) {
        return;
    }
    scheduleSummaryReconnect(socketReason(QStringLiteral("WebSocket error"),
                                          summarySocket_->errorString()));
}

void PremiumClient::handleSummaryText(const QString &message)
{
    const QByteArray payload = message.toUtf8();
    if (payload.size() > config_.maximumWebSocketMessageBytes) {
        const QString reason = QStringLiteral("message exceeds %1 bytes")
                                   .arg(config_.maximumWebSocketMessageBytes);
        Q_EMIT protocolError(QString::fromLatin1(SummaryChannel), reason);
        summarySocket_->close(QWebSocketProtocol::CloseCodeTooMuchData,
                              QStringLiteral("message too large"));
        return;
    }

    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(payload, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        ++consecutiveSummaryProtocolErrors_;
        Q_EMIT protocolError(
            QString::fromLatin1(SummaryChannel),
            QStringLiteral("invalid JSON object: %1").arg(parseError.errorString()));
        if (consecutiveSummaryProtocolErrors_ >= 3) {
            summarySocket_->close(QWebSocketProtocol::CloseCodeWrongDatatype,
                                  QStringLiteral("invalid JSON"));
        }
        return;
    }

    if (!dispatchSummaryObject(document.object())) {
        ++consecutiveSummaryProtocolErrors_;
        if (consecutiveSummaryProtocolErrors_ >= 3) {
            summarySocket_->close(QWebSocketProtocol::CloseCodeWrongDatatype,
                                  QStringLiteral("invalid message contract"));
        }
        return;
    }
    consecutiveSummaryProtocolErrors_ = 0;
    markSummaryActivity();
}

void PremiumClient::handleSummaryBinary(const QByteArray &message)
{
    const bool tooLarge = message.size() > config_.maximumWebSocketMessageBytes;
    Q_EMIT protocolError(
        QString::fromLatin1(SummaryChannel),
        tooLarge ? QStringLiteral("binary message exceeds configured limit")
                 : QStringLiteral("binary message is not supported"));
    summarySocket_->close(
        tooLarge ? QWebSocketProtocol::CloseCodeTooMuchData
                 : QWebSocketProtocol::CloseCodeDatatypeNotSupported,
        tooLarge ? QStringLiteral("message too large")
                 : QStringLiteral("text JSON required"));
}

bool PremiumClient::dispatchSummaryObject(const QJsonObject &object)
{
    const QString type = object.value(QStringLiteral("type")).toString();
    if (type.isEmpty()) {
        Q_EMIT protocolError(QString::fromLatin1(SummaryChannel),
                             QStringLiteral("message has no string type"));
        return false;
    }

    Q_EMIT messageReceived(QString::fromLatin1(SummaryChannel), type, object);
    if (type == QStringLiteral("status")) {
        lastStatus_ = object;
        Q_EMIT statusReceived(object);
    } else if (type == QStringLiteral("summary")) {
        const QString symbol = object.value(QStringLiteral("s")).toString(
            object.value(QStringLiteral("symbol")).toString());
        if (symbol.isEmpty()) {
            Q_EMIT protocolError(QString::fromLatin1(SummaryChannel),
                                 QStringLiteral("summary has no symbol s/symbol"));
            return false;
        }
        Q_EMIT summaryReceived(symbol, object);
    } else if (type == QStringLiteral("signal")) {
        const QString symbol = object.value(QStringLiteral("symbol")).toString();
        if (symbol.isEmpty()) {
            Q_EMIT protocolError(QString::fromLatin1(SummaryChannel),
                                 QStringLiteral("signal has no symbol"));
            return false;
        }
        Q_EMIT signalReceived(symbol, object);
    } else if (type == QStringLiteral("sync_begin")) {
        Q_EMIT syncEventReceived(QStringLiteral("begin"), object);
        Q_EMIT syncBeginReceived(object);
    } else if (type == QStringLiteral("sync_complete")) {
        Q_EMIT syncEventReceived(QStringLiteral("complete"), object);
        Q_EMIT syncCompleteReceived(object);
    } else if (type == QStringLiteral("sync")) {
        Q_EMIT syncEventReceived(object.value(QStringLiteral("phase")).toString(), object);
    } else if (type == QStringLiteral("raw_snapshot")) {
        Q_EMIT rawSnapshotReceived(object);
    } else if (type == QStringLiteral("watchlist_ack")
               || type == QStringLiteral("l1_hotlist_ack")) {
        const bool watchlist = type == QStringLiteral("watchlist_ack");
        const bool pending = watchlist ? watchlistAckPending_ : l1HotlistAckPending_;
        const QJsonValue accepted = object.value(QStringLiteral("accepted"));
        const QJsonValue symbolsValue = object.value(QStringLiteral("symbols"));
        QStringList authoritative;
        bool symbolsValid = symbolsValue.isArray();
        if (symbolsValid) {
            for (const auto &value : symbolsValue.toArray()) {
                if (!value.isString() || value.toString().trimmed().isEmpty()) {
                    symbolsValid = false;
                    break;
                }
                authoritative.push_back(value.toString().trimmed());
            }
        }
        QStringList expected = watchlist ? pendingWatchlist_ : pendingL1Hotlist_;
        QStringList actual = authoritative;
        expected.sort(Qt::CaseInsensitive);
        actual.sort(Qt::CaseInsensitive);
        if (!pending || !accepted.isBool() || !symbolsValid || expected != actual) {
            Q_EMIT protocolError(
                QString::fromLatin1(SummaryChannel),
                QStringLiteral("%1 missing/invalid accepted+symbols or does not match the pending request")
                    .arg(type));
            return false;
        }
        if (watchlist) {
            watchlistAckPending_ = false;
            pendingWatchlist_.clear();
            Q_EMIT watchlistAcknowledged(object);
        } else {
            l1HotlistAckPending_ = false;
            pendingL1Hotlist_.clear();
            Q_EMIT l1HotlistAcknowledged(object);
        }
    } else if (type == QStringLiteral("symbol_removed")) {
        Q_EMIT symbolRemoved(object.value(QStringLiteral("symbol")).toString(), object);
    } else if (type == QStringLiteral("error")) {
        Q_EMIT serverErrorReceived(QString::fromLatin1(SummaryChannel), object);
    } else {
        Q_EMIT protocolError(QString::fromLatin1(SummaryChannel),
                             QStringLiteral("unsupported message type: %1")
                                 .arg(type.left(80)));
        return false;
    }
    return true;
}

bool PremiumClient::sendSummaryCommand(const QString &operation, QJsonObject command)
{
    if (summarySocket_->state() != QAbstractSocket::ConnectedState) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(SummaryChannel), operation,
                                      QStringLiteral("WebSocket is not connected"));
        return false;
    }
    command.insert(QStringLiteral("op"), operation);
    const QByteArray payload = QJsonDocument(command).toJson(QJsonDocument::Compact);
    if (payload.size() > config_.maximumWebSocketMessageBytes) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(SummaryChannel), operation,
                                      QStringLiteral("command exceeds configured limit"));
        return false;
    }
    if (summarySocket_->bytesToWrite()
            > config_.maximumPendingWriteBytes - payload.size()) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(SummaryChannel), operation,
                                      QStringLiteral("outgoing buffer is full"));
        return false;
    }

    const qint64 accepted = summarySocket_->sendTextMessage(QString::fromUtf8(payload));
    if (accepted < 0) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(SummaryChannel), operation,
                                      summarySocket_->errorString());
        return false;
    }
    Q_EMIT commandSent(QString::fromLatin1(SummaryChannel), operation, command);
    return true;
}

void PremiumClient::handleDetailConnected()
{
    detailConnectTimeoutTimer_->stop();
    detailHelloSeen_ = false;
    acknowledgedDetailSymbols_.clear();
    pendingDetailOperations_.clear();
    consecutiveDetailProtocolErrors_ = 0;
    setDetailState(ChannelState::Connecting,
                   QStringLiteral("WebSocket connected; waiting for detail hello"));
    detailHelloTimeoutTimer_->start(config_.connectTimeoutMs);
}

void PremiumClient::handleDetailDisconnected()
{
    detailConnectTimeoutTimer_->stop();
    detailHelloTimeoutTimer_->stop();
    resetDetailGeneration();
    if (!running_ || desiredDetailSymbols_.isEmpty()) {
        setDetailState(ChannelState::Stopped, QStringLiteral("idle"));
        return;
    }
    if (detailSocket_->state() != QAbstractSocket::UnconnectedState) return;
    QString reason = detailSocket_->closeReason();
    if (reason.isEmpty()) reason = detailSocket_->errorString();
    scheduleDetailReconnect(
        socketReason(QStringLiteral("detail WebSocket disconnected"), reason));
}

void PremiumClient::handleDetailSocketError()
{
    if (!running_ || desiredDetailSymbols_.isEmpty()) return;
    scheduleDetailReconnect(socketReason(QStringLiteral("detail WebSocket error"),
                                         detailSocket_->errorString()));
}

void PremiumClient::handleDetailText(const QString &message)
{
    const QByteArray payload = message.toUtf8();
    if (payload.size() > config_.maximumWebSocketMessageBytes) {
        Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                             QStringLiteral("message exceeds %1 bytes")
                                 .arg(config_.maximumWebSocketMessageBytes));
        detailSocket_->close(QWebSocketProtocol::CloseCodeTooMuchData,
                             QStringLiteral("message too large"));
        return;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(payload, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        ++consecutiveDetailProtocolErrors_;
        Q_EMIT protocolError(
            QString::fromLatin1(DetailChannel),
            QStringLiteral("invalid JSON object: %1").arg(parseError.errorString()));
        if (consecutiveDetailProtocolErrors_ >= 3) {
            detailSocket_->close(QWebSocketProtocol::CloseCodeWrongDatatype,
                                 QStringLiteral("invalid JSON"));
        }
        return;
    }
    if (!dispatchDetailObject(document.object())) {
        ++consecutiveDetailProtocolErrors_;
        if (consecutiveDetailProtocolErrors_ >= 3) {
            detailSocket_->close(QWebSocketProtocol::CloseCodeWrongDatatype,
                                 QStringLiteral("invalid detail contract"));
        }
        return;
    }
    consecutiveDetailProtocolErrors_ = 0;
}

void PremiumClient::handleDetailBinary(const QByteArray &message)
{
    const bool tooLarge = message.size() > config_.maximumWebSocketMessageBytes;
    Q_EMIT protocolError(
        QString::fromLatin1(DetailChannel),
        tooLarge ? QStringLiteral("binary message exceeds configured limit")
                 : QStringLiteral("binary message is not supported"));
    detailSocket_->close(
        tooLarge ? QWebSocketProtocol::CloseCodeTooMuchData
                 : QWebSocketProtocol::CloseCodeDatatypeNotSupported,
        tooLarge ? QStringLiteral("message too large")
                 : QStringLiteral("text JSON required"));
}

bool PremiumClient::dispatchDetailObject(const QJsonObject &object)
{
    const QString type = object.value(QStringLiteral("type")).toString();
    if (type.isEmpty()) {
        Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                             QStringLiteral("message has no string type"));
        return false;
    }
    if (!detailHelloSeen_) {
        const QJsonValue maximumValue = object.value(QStringLiteral("max_symbols"));
        const double maximumNumber = maximumValue.toDouble(-1.0);
        const int maximum = maximumValue.isDouble()
                && std::isfinite(maximumNumber)
                && std::floor(maximumNumber) == maximumNumber
            ? static_cast<int>(maximumNumber) : -1;
        if (type != QStringLiteral("hello")
            || object.value(QStringLiteral("channel")).toString()
                   != QStringLiteral("detail")
            || maximum < 1) {
            Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                                 QStringLiteral("detail hello identity/limit mismatch"));
            return false;
        }
        detailHelloSeen_ = true;
        detailHelloTimeoutTimer_->stop();
        detailReconnectAttempt_ = 0;
        detailServerMaximumSymbols_ = std::min(ClientDetailSymbolLimit, maximum);
        setDetailState(ChannelState::Connected,
                       QStringLiteral("detail ready; max %1 symbols")
                           .arg(detailServerMaximumSymbols_));
        Q_EMIT messageReceived(QString::fromLatin1(DetailChannel), type, object);
        Q_EMIT detailHelloReceived(object);

        QStringList desired = desiredDetailSymbols();
        while (desired.size() > detailServerMaximumSymbols_) {
            const QString rejected = desired.takeLast();
            desiredDetailSymbols_.remove(rejected);
            const QJsonObject error{{QStringLiteral("type"), QStringLiteral("error")},
                                    {QStringLiteral("code"), QStringLiteral("detail_limit")},
                                    {QStringLiteral("symbol"), rejected},
                                    {QStringLiteral("maximum"), detailServerMaximumSymbols_}};
            Q_EMIT detailSubscriptionFailed(QStringLiteral("subscribe"), rejected,
                                             error);
        }
        Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                          acknowledgedDetailSymbols());
        reconcileDetailSubscriptions();
        return true;
    }

    Q_EMIT messageReceived(QString::fromLatin1(DetailChannel), type, object);
    if (type == QStringLiteral("hello")) {
        if (object.value(QStringLiteral("channel")).toString()
                != QStringLiteral("detail")) {
            Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                                 QStringLiteral("detail hello identity mismatch"));
            return false;
        }
        Q_EMIT detailHelloReceived(object);
        return true;
    }
    if (type == QStringLiteral("detail_ack")) {
        const QString operation = object.value(QStringLiteral("op")).toString();
        const QString symbol = normalizedDetailSymbol(
            object.value(QStringLiteral("symbol")).toString());
        if ((operation != QStringLiteral("subscribe")
             && operation != QStringLiteral("unsubscribe"))
            || symbol.isEmpty()
            || pendingDetailOperations_.value(symbol) != operation) {
            Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                                 QStringLiteral("detail_ack does not match a pending operation"));
            return false;
        }
        pendingDetailOperations_.remove(symbol);
        if (operation == QStringLiteral("subscribe")) {
            if (!desiredDetailSymbols_.contains(symbol)) {
                Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                                     QStringLiteral("subscribe ACK arrived after local removal"));
                return false;
            }
            acknowledgedDetailSymbols_.insert(symbol);
        } else {
            acknowledgedDetailSymbols_.remove(symbol);
        }
        Q_EMIT detailAcknowledged(operation, symbol, object);
        Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                          acknowledgedDetailSymbols());
        closeIdleDetailChannel();
        return true;
    }
    if (type == QStringLiteral("detail")) {
        const QString symbol = normalizedDetailSymbol(
            object.value(QStringLiteral("s")).toString(
                object.value(QStringLiteral("symbol")).toString()));
        if (symbol.isEmpty() || !desiredDetailSymbols_.contains(symbol)
            || !acknowledgedDetailSymbols_.contains(symbol)) {
            Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                                 QStringLiteral("detail payload is not for an acknowledged subscription"));
            return false;
        }
        Q_EMIT detailReceived(symbol, object);
        return true;
    }
    if (type == QStringLiteral("error")) {
        const QString symbol = normalizedDetailSymbol(
            object.value(QStringLiteral("symbol")).toString());
        const QString operation = pendingDetailOperations_.take(symbol);
        if (operation == QStringLiteral("subscribe")) {
            desiredDetailSymbols_.remove(symbol);
            acknowledgedDetailSymbols_.remove(symbol);
        }
        Q_EMIT serverErrorReceived(QString::fromLatin1(DetailChannel), object);
        if (!operation.isEmpty()) {
            Q_EMIT detailSubscriptionFailed(operation, symbol, object);
            Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                              acknowledgedDetailSymbols());
        }
        closeIdleDetailChannel();
        return true;
    }
    Q_EMIT protocolError(QString::fromLatin1(DetailChannel),
                         QStringLiteral("unsupported message type: %1")
                             .arg(type.left(80)));
    return false;
}

bool PremiumClient::sendDetailCommand(const QString &operation,
                                      const QString &symbol)
{
    if (detailSocket_->state() != QAbstractSocket::ConnectedState
        || !detailHelloSeen_) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(DetailChannel), operation,
                                      QStringLiteral("detail handshake is not ready"));
        return false;
    }
    const QJsonObject command{{QStringLiteral("op"), operation},
                              {QStringLiteral("symbol"), symbol}};
    const QByteArray payload = QJsonDocument(command).toJson(QJsonDocument::Compact);
    if (payload.size() > config_.maximumWebSocketMessageBytes
        || detailSocket_->bytesToWrite()
               > config_.maximumPendingWriteBytes - payload.size()) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(DetailChannel), operation,
                                      QStringLiteral("outgoing buffer is full"));
        return false;
    }
    pendingDetailOperations_.insert(symbol, operation);
    const qint64 accepted = detailSocket_->sendTextMessage(QString::fromUtf8(payload));
    if (accepted < 0) {
        pendingDetailOperations_.remove(symbol);
        Q_EMIT commandRejectedLocally(QString::fromLatin1(DetailChannel), operation,
                                      detailSocket_->errorString());
        return false;
    }
    Q_EMIT commandSent(QString::fromLatin1(DetailChannel), operation, command);
    return true;
}

void PremiumClient::reconcileDetailSubscriptions()
{
    if (!running_ || !detailHelloSeen_
        || detailSocket_->state() != QAbstractSocket::ConnectedState) return;
    const QStringList desired = desiredDetailSymbols();
    for (const QString &symbol : desired) {
        if (acknowledgedDetailSymbols_.contains(symbol)
            || pendingDetailOperations_.contains(symbol)) continue;
        if (!sendDetailCommand(QStringLiteral("subscribe"), symbol)) break;
    }
}

void PremiumClient::closeIdleDetailChannel()
{
    if (!desiredDetailSymbols_.isEmpty() || !pendingDetailOperations_.isEmpty()) return;
    detailReconnectTimer_->stop();
    detailConnectTimeoutTimer_->stop();
    detailHelloTimeoutTimer_->stop();
    resetDetailGeneration();
    if (detailSocket_->state() != QAbstractSocket::UnconnectedState) {
        detailSocket_->abort();
    }
    setDetailState(ChannelState::Stopped, QStringLiteral("idle"));
}

void PremiumClient::resetDetailGeneration()
{
    detailHelloSeen_ = false;
    detailServerMaximumSymbols_ = ClientDetailSymbolLimit;
    acknowledgedDetailSymbols_.clear();
    pendingDetailOperations_.clear();
    Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                      acknowledgedDetailSymbols());
}

void PremiumClient::clearPendingSummaryMutations()
{
    pendingWatchlist_.clear();
    pendingL1Hotlist_.clear();
    watchlistAckPending_ = false;
    l1HotlistAckPending_ = false;
}

void PremiumClient::requestStatus()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::requestStatus, Qt::QueuedConnection);
        return;
    }
    sendSummaryCommand(QStringLiteral("status"), {});
}

void PremiumClient::requestSync()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::requestSync, Qt::QueuedConnection);
        return;
    }
    sendSummaryCommand(QStringLiteral("sync"), {});
}

void PremiumClient::setWatchlist(const QStringList &symbols)
{
    if (!onOwnerThread()) {
        const QStringList copy = symbols;
        QMetaObject::invokeMethod(this, [this, copy] { setWatchlist(copy); },
                                  Qt::QueuedConnection);
        return;
    }
    if (watchlistAckPending_) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(SummaryChannel),
                                      QStringLiteral("set_watchlist"),
                                      QStringLiteral("a watchlist acknowledgement is already pending"));
        return;
    }
    if (sendSummaryCommand(QStringLiteral("set_watchlist"),
                           {{QStringLiteral("symbols"), QJsonArray::fromStringList(symbols)}})) {
        pendingWatchlist_ = symbols;
        for (auto &symbol : pendingWatchlist_) symbol = symbol.trimmed();
        watchlistAckPending_ = true;
    }
}

void PremiumClient::reconcilePendingMutations()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::reconcilePendingMutations,
                                  Qt::QueuedConnection);
        return;
    }
    clearPendingSummaryMutations();
}

void PremiumClient::setL1Hotlist(const QStringList &symbols)
{
    if (!onOwnerThread()) {
        const QStringList copy = symbols;
        QMetaObject::invokeMethod(this, [this, copy] { setL1Hotlist(copy); },
                                  Qt::QueuedConnection);
        return;
    }
    if (l1HotlistAckPending_) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(SummaryChannel),
                                      QStringLiteral("set_l1_hotlist"),
                                      QStringLiteral("an L1 hotlist acknowledgement is already pending"));
        return;
    }
    if (sendSummaryCommand(QStringLiteral("set_l1_hotlist"),
                           {{QStringLiteral("symbols"), QJsonArray::fromStringList(symbols)}})) {
        pendingL1Hotlist_ = symbols;
        for (auto &symbol : pendingL1Hotlist_) symbol = symbol.trimmed();
        l1HotlistAckPending_ = true;
    }
}

void PremiumClient::requestRawSnapshot()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::requestRawSnapshot,
                                  Qt::QueuedConnection);
        return;
    }
    sendSummaryCommand(QStringLiteral("raw_snapshot"), {});
}

void PremiumClient::subscribeDetail(const QString &requestedSymbol)
{
    if (!onOwnerThread()) {
        const QString copy = requestedSymbol;
        QMetaObject::invokeMethod(this, [this, copy] { subscribeDetail(copy); },
                                  Qt::QueuedConnection);
        return;
    }
    const QString symbol = normalizedDetailSymbol(requestedSymbol);
    if (symbol.isEmpty()) {
        Q_EMIT detailSubscriptionFailed(
            QStringLiteral("subscribe"), requestedSymbol,
            {{QStringLiteral("type"), QStringLiteral("error")},
             {QStringLiteral("code"), QStringLiteral("invalid_symbol")},
             {QStringLiteral("message"),
              QStringLiteral("symbol must match NNNNNN.SH/SZ")}});
        return;
    }
    if (!running_) {
        Q_EMIT detailSubscriptionFailed(
            QStringLiteral("subscribe"), symbol,
            {{QStringLiteral("type"), QStringLiteral("error")},
             {QStringLiteral("code"), QStringLiteral("client_stopped")}});
        return;
    }
    if (desiredDetailSymbols_.contains(symbol)) {
        if (acknowledgedDetailSymbols_.contains(symbol)) {
            const QJsonObject acknowledgement{
                {QStringLiteral("type"), QStringLiteral("detail_ack")},
                {QStringLiteral("op"), QStringLiteral("subscribe")},
                {QStringLiteral("symbol"), symbol},
                {QStringLiteral("already_subscribed"), true}};
            Q_EMIT detailAcknowledged(QStringLiteral("subscribe"), symbol,
                                      acknowledgement);
        } else {
            reconcileDetailSubscriptions();
        }
        return;
    }
    if (desiredDetailSymbols_.size() >= ClientDetailSymbolLimit) {
        Q_EMIT detailSubscriptionFailed(
            QStringLiteral("subscribe"), symbol,
            {{QStringLiteral("type"), QStringLiteral("error")},
             {QStringLiteral("code"), QStringLiteral("detail_limit")},
             {QStringLiteral("maximum"), ClientDetailSymbolLimit}});
        return;
    }
    desiredDetailSymbols_.insert(symbol);
    Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                      acknowledgedDetailSymbols());
    if (detailSocket_->state() == QAbstractSocket::ConnectedState
        && detailHelloSeen_) {
        if (!sendDetailCommand(QStringLiteral("subscribe"), symbol)) {
            desiredDetailSymbols_.remove(symbol);
            Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                              acknowledgedDetailSymbols());
            Q_EMIT detailSubscriptionFailed(
                QStringLiteral("subscribe"), symbol,
                {{QStringLiteral("type"), QStringLiteral("error")},
                 {QStringLiteral("code"), QStringLiteral("transport_rejected")}});
        }
    } else if (detailSocket_->state() == QAbstractSocket::UnconnectedState
               && !detailReconnectTimer_->isActive()) {
        connectDetail();
    }
}

void PremiumClient::unsubscribeDetail(const QString &requestedSymbol)
{
    if (!onOwnerThread()) {
        const QString copy = requestedSymbol;
        QMetaObject::invokeMethod(this, [this, copy] { unsubscribeDetail(copy); },
                                  Qt::QueuedConnection);
        return;
    }
    const QString symbol = normalizedDetailSymbol(requestedSymbol);
    if (symbol.isEmpty()) {
        Q_EMIT detailSubscriptionFailed(
            QStringLiteral("unsubscribe"), requestedSymbol,
            {{QStringLiteral("type"), QStringLiteral("error")},
             {QStringLiteral("code"), QStringLiteral("invalid_symbol")},
             {QStringLiteral("message"),
              QStringLiteral("symbol must match NNNNNN.SH/SZ")}});
        return;
    }
    if (pendingDetailOperations_.value(symbol) == QStringLiteral("subscribe")) {
        Q_EMIT detailSubscriptionFailed(
            QStringLiteral("unsubscribe"), symbol,
            {{QStringLiteral("type"), QStringLiteral("error")},
             {QStringLiteral("code"), QStringLiteral("subscribe_pending")}});
        return;
    }
    if (!desiredDetailSymbols_.contains(symbol)
        && !acknowledgedDetailSymbols_.contains(symbol)) {
        const QJsonObject acknowledgement{
            {QStringLiteral("type"), QStringLiteral("detail_ack")},
            {QStringLiteral("op"), QStringLiteral("unsubscribe")},
            {QStringLiteral("symbol"), symbol},
            {QStringLiteral("already_unsubscribed"), true}};
        Q_EMIT detailAcknowledged(QStringLiteral("unsubscribe"), symbol,
                                  acknowledgement);
        return;
    }
    if (!running_ || detailSocket_->state() != QAbstractSocket::ConnectedState
        || !detailHelloSeen_) {
        desiredDetailSymbols_.remove(symbol);
        acknowledgedDetailSymbols_.remove(symbol);
        pendingDetailOperations_.remove(symbol);
        const QJsonObject acknowledgement{
            {QStringLiteral("type"), QStringLiteral("detail_ack")},
            {QStringLiteral("op"), QStringLiteral("unsubscribe")},
            {QStringLiteral("symbol"), symbol},
            {QStringLiteral("connection_lease_absent"), true}};
        Q_EMIT detailAcknowledged(QStringLiteral("unsubscribe"), symbol,
                                  acknowledgement);
        Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                          acknowledgedDetailSymbols());
        closeIdleDetailChannel();
        return;
    }
    desiredDetailSymbols_.remove(symbol);
    if (!sendDetailCommand(QStringLiteral("unsubscribe"), symbol)) {
        desiredDetailSymbols_.insert(symbol);
        Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                          acknowledgedDetailSymbols());
        Q_EMIT detailSubscriptionFailed(
            QStringLiteral("unsubscribe"), symbol,
            {{QStringLiteral("type"), QStringLiteral("error")},
             {QStringLiteral("code"), QStringLiteral("transport_rejected")}});
        return;
    }
    Q_EMIT detailSubscriptionsChanged(desiredDetailSymbols(),
                                      acknowledgedDetailSymbols());
}

void PremiumClient::handleL1Connected()
{
    l1ConnectTimeoutTimer_->stop();
    consecutiveL1ProtocolErrors_ = 0;
    l1InputBuffer_.clear();
    l1HelloSeen_ = false;
    setL1State(ChannelState::Connecting,
               QStringLiteral("TCP connected; waiting for qmt_l1 hello"));
    l1HelloTimeoutTimer_->start(config_.connectTimeoutMs);
}

void PremiumClient::handleL1Disconnected()
{
    l1ConnectTimeoutTimer_->stop();
    l1HelloTimeoutTimer_->stop();
    l1PingTimer_->stop();
    l1StatusTimer_->stop();
    l1HelloSeen_ = false;
    l1InputBuffer_.clear();
    if (!running_) {
        return;
    }
    if (l1Socket_->state() != QAbstractSocket::UnconnectedState) {
        return;
    }
    scheduleL1Reconnect(socketReason(QStringLiteral("TCP disconnected"),
                                     l1Socket_->errorString()));
}

void PremiumClient::handleL1SocketError()
{
    if (!running_) {
        return;
    }
    scheduleL1Reconnect(socketReason(QStringLiteral("TCP error"),
                                     l1Socket_->errorString()));
}

void PremiumClient::handleL1ReadyRead()
{
    l1InputBuffer_.append(l1Socket_->readAll());

    while (true) {
        const qsizetype newline = l1InputBuffer_.indexOf('\n');
        if (newline < 0) {
            break;
        }
        if (newline > config_.maximumL1LineBytes) {
            failL1Protocol(QStringLiteral("line exceeds %1 bytes")
                               .arg(config_.maximumL1LineBytes));
            return;
        }

        QByteArray line = l1InputBuffer_.left(newline);
        l1InputBuffer_.remove(0, newline + 1);
        if (line.endsWith('\r')) {
            line.chop(1);
        }
        if (line.trimmed().isEmpty()) {
            continue;
        }

        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            ++consecutiveL1ProtocolErrors_;
            Q_EMIT protocolError(
                QString::fromLatin1(L1Channel),
                QStringLiteral("invalid NDJSON object: %1").arg(parseError.errorString()));
            if (consecutiveL1ProtocolErrors_ >= 3) {
                failL1Protocol(QStringLiteral("too many invalid NDJSON messages"));
                return;
            }
            continue;
        }

        consecutiveL1ProtocolErrors_ = 0;
        dispatchL1Object(document.object());
        if (l1Socket_->state() == QAbstractSocket::UnconnectedState) {
            return;
        }
    }

    if (l1InputBuffer_.size() > config_.maximumL1LineBytes
        || l1InputBuffer_.size() > config_.maximumL1BufferBytes) {
        failL1Protocol(QStringLiteral("unterminated line exceeds configured limit"));
    }
}

void PremiumClient::dispatchL1Object(const QJsonObject &object)
{
    if (object.value(QStringLiteral("v")).toInt(1) != 1) {
        failL1Protocol(QStringLiteral("unsupported protocol version"));
        return;
    }

    QString type = object.value(QStringLiteral("t")).toString();
    if (type.isEmpty()) {
        type = object.value(QStringLiteral("type")).toString();
    }
    if (type.isEmpty()) {
        Q_EMIT protocolError(QString::fromLatin1(L1Channel),
                             QStringLiteral("message has no string t/type"));
        return;
    }

    if (!l1HelloSeen_) {
        if (type != QStringLiteral("hello")) {
            failL1Protocol(QStringLiteral("first message is not hello"));
            return;
        }
        if (object.value(QStringLiteral("v")).toInt(-1) != 1
            || object.value(QStringLiteral("service")).toString()
                != QStringLiteral("qmt_l1")) {
            failL1Protocol(QStringLiteral("hello identity/version mismatch"));
            return;
        }

        l1HelloSeen_ = true;
        l1HelloTimeoutTimer_->stop();
        l1ReconnectAttempt_ = 0;
        markL1Activity();
        setL1State(ChannelState::Connected, QStringLiteral("qmt_l1 protocol v1 ready"));
        publishFreshness();
        Q_EMIT messageReceived(QString::fromLatin1(L1Channel), type, object);
        Q_EMIT l1HelloReceived(object);
        l1PingTimer_->start();
        l1StatusTimer_->start();
        requestL1Status();
        pingL1();
        return;
    }

    markL1Activity();
    Q_EMIT messageReceived(QString::fromLatin1(L1Channel), type, object);
    if (type == QStringLiteral("hello")) {
        if (object.value(QStringLiteral("v")).toInt(-1) != 1
            || object.value(QStringLiteral("service")).toString()
                != QStringLiteral("qmt_l1")) {
            failL1Protocol(QStringLiteral("hello identity/version mismatch"));
            return;
        }
        Q_EMIT l1HelloReceived(object);
    } else if (type == QStringLiteral("status")) {
        lastL1Status_ = object;
        Q_EMIT l1StatusReceived(object);
    } else if (type == QStringLiteral("pong")) {
        Q_EMIT l1PongReceived(object);
    } else if (type == QStringLiteral("error")) {
        Q_EMIT serverErrorReceived(QString::fromLatin1(L1Channel), object);
    }
}

bool PremiumClient::sendL1Command(const QString &operation)
{
    if (l1Socket_->state() != QAbstractSocket::ConnectedState || !l1HelloSeen_) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(L1Channel), operation,
                                      QStringLiteral("qmt_l1 handshake is not ready"));
        return false;
    }
    const QString id = QStringLiteral("premium-%1-%2")
                           .arg(operation)
                           .arg(nextL1RequestId_++);
    const QJsonObject command{{QStringLiteral("v"), 1},
                              {QStringLiteral("t"), operation},
                              {QStringLiteral("id"), id}};
    const QByteArray payload = QJsonDocument(command).toJson(QJsonDocument::Compact) + '\n';
    if (payload.size() > config_.maximumL1LineBytes) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(L1Channel), operation,
                                      QStringLiteral("command exceeds configured line limit"));
        return false;
    }
    if (l1Socket_->bytesToWrite()
            > config_.maximumPendingWriteBytes - payload.size()) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(L1Channel), operation,
                                      QStringLiteral("outgoing buffer is full"));
        return false;
    }
    const qint64 accepted = l1Socket_->write(payload);
    if (accepted != payload.size()) {
        Q_EMIT commandRejectedLocally(QString::fromLatin1(L1Channel), operation,
                                      accepted < 0 ? l1Socket_->errorString()
                                                   : QStringLiteral("partial socket write"));
        if (accepted > 0) {
            l1Socket_->abort();
            scheduleL1Reconnect(QStringLiteral("partial socket write"));
        }
        return false;
    }
    Q_EMIT commandSent(QString::fromLatin1(L1Channel), operation, command);
    return true;
}

void PremiumClient::requestL1Status()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::requestL1Status,
                                  Qt::QueuedConnection);
        return;
    }
    sendL1Command(QStringLiteral("status"));
}

void PremiumClient::pingL1()
{
    if (!onOwnerThread()) {
        QMetaObject::invokeMethod(this, &PremiumClient::pingL1, Qt::QueuedConnection);
        return;
    }
    sendL1Command(QStringLiteral("ping"));
}

void PremiumClient::failL1Protocol(const QString &reason)
{
    Q_EMIT protocolError(QString::fromLatin1(L1Channel), reason);
    l1Socket_->abort();
    scheduleL1Reconnect(QStringLiteral("protocol failure: %1").arg(reason));
}

void PremiumClient::markSummaryActivity()
{
    lastSummaryActivityMs_ = monotonicClock_.elapsed();
    if (summaryState_ == ChannelState::Stale) {
        setSummaryState(ChannelState::Connected, QStringLiteral("message flow recovered"));
        publishFreshness();
    }
}

void PremiumClient::markL1Activity()
{
    lastL1ActivityMs_ = monotonicClock_.elapsed();
    if (l1State_ == ChannelState::Stale) {
        setL1State(ChannelState::Connected, QStringLiteral("probe response recovered"));
        publishFreshness();
    }
}

void PremiumClient::checkFreshness()
{
    const qint64 summaryAge = summaryAgeMs();
    if (summarySocket_->state() == QAbstractSocket::ConnectedState && summaryAge >= 0) {
        if (summaryAge > config_.silenceDisconnectAfterMs) {
            Q_EMIT protocolError(QString::fromLatin1(SummaryChannel),
                                 QStringLiteral("message silence timeout"));
            summarySocket_->abort();
            scheduleSummaryReconnect(QStringLiteral("message silence timeout"));
        } else if (summaryAge > config_.summaryStaleAfterMs
                   && summaryState_ == ChannelState::Connected) {
            setSummaryState(ChannelState::Stale,
                            QStringLiteral("no message for %1 ms").arg(summaryAge));
            requestStatus();
        }
    }

    const qint64 currentL1Age = l1AgeMs();
    if (l1Socket_->state() == QAbstractSocket::ConnectedState && l1HelloSeen_
        && currentL1Age >= 0) {
        if (currentL1Age > config_.silenceDisconnectAfterMs) {
            Q_EMIT protocolError(QString::fromLatin1(L1Channel),
                                 QStringLiteral("probe response silence timeout"));
            l1Socket_->abort();
            scheduleL1Reconnect(QStringLiteral("probe response silence timeout"));
        } else if (currentL1Age > config_.l1StaleAfterMs
                   && l1State_ == ChannelState::Connected) {
            setL1State(ChannelState::Stale,
                       QStringLiteral("no probe response for %1 ms").arg(currentL1Age));
            pingL1();
            requestL1Status();
        }
    }
    publishFreshness();
}

void PremiumClient::publishFreshness()
{
    Q_EMIT freshnessChanged(isSummaryFresh(), isL1Fresh(),
                            summaryAgeMs(), l1AgeMs());
}

void PremiumClient::setSummaryState(ChannelState state, const QString &detail)
{
    if (summaryState_ == state && summaryStateDetail_ == detail) {
        return;
    }
    summaryState_ = state;
    summaryStateDetail_ = detail;
    Q_EMIT summaryStateChanged(state, detail);
}

void PremiumClient::setDetailState(ChannelState state, const QString &detail)
{
    if (detailState_ == state && detailStateDetail_ == detail) return;
    detailState_ = state;
    detailStateDetail_ = detail;
    Q_EMIT detailStateChanged(state, detail);
}

void PremiumClient::setL1State(ChannelState state, const QString &detail)
{
    if (l1State_ == state && l1StateDetail_ == detail) {
        return;
    }
    l1State_ = state;
    l1StateDetail_ = detail;
    Q_EMIT l1StateChanged(state, detail);
}

qint64 PremiumClient::ageSince(qint64 timestampMs) const
{
    if (timestampMs < 0 || !monotonicClock_.isValid()) {
        return -1;
    }
    return std::max<qint64>(0, monotonicClock_.elapsed() - timestampMs);
}

bool PremiumClient::onOwnerThread() const
{
    return QThread::currentThread() == thread();
}

} // namespace machome::premium
