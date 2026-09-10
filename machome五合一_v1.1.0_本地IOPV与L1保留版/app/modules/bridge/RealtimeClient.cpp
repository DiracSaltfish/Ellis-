#include "RealtimeClient.h"

#include <QAbstractSocket>
#include <QHostAddress>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QRandomGenerator>
#include <QRegularExpression>
#include <QTimer>
#include <QUrlQuery>
#include <QUuid>
#include <QWebSocket>
#include <QWebSocketProtocol>

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace machome::bridge {
namespace {

constexpr int kMaximumPendingRequests = 64;
constexpr int kMaximumWatchlistItems = 200;

QString newRequestId()
{
    return QUuid::createUuid().toString(QUuid::WithoutBraces);
}

QString jsonDetail(const QJsonObject &object)
{
    const QJsonValue detail = object.value(QStringLiteral("detail"));
    if (detail.isString()) {
        return detail.toString();
    }
    const QJsonValue message = object.value(QStringLiteral("message"));
    return message.isString() ? message.toString() : QString{};
}

bool isSuccessfulStatus(int status)
{
    return status >= 200 && status < 300;
}

bool nonNegativeInteger(const QJsonValue &value)
{
    if (!value.isDouble()) return false;
    const double number = value.toDouble(-1.0);
    return std::isfinite(number) && number >= 0.0 && std::floor(number) == number;
}

bool freshServerTime(const QJsonValue &value)
{
    if (!value.isString()) return false;
    QDateTime parsed = QDateTime::fromString(value.toString(), Qt::ISODateWithMs);
    if (!parsed.isValid()) parsed = QDateTime::fromString(value.toString(), Qt::ISODate);
    if (!parsed.isValid() || parsed.timeSpec() == Qt::LocalTime) return false;
    const qint64 skew = parsed.toUTC().msecsTo(QDateTime::currentDateTimeUtc());
    return skew >= -30'000 && skew <= 5 * 60'000;
}

QString realtimeHealthContractError(const QJsonObject &payload)
{
    const QString declaredModule = payload.value(QStringLiteral("module")).toString();
    if (!declaredModule.isEmpty()
        && declaredModule != QStringLiteral("etf-realtime-monitor")) {
        return QStringLiteral("health module 身份不匹配");
    }
    if (!payload.value(QStringLiteral("ok")).isBool()
        || !payload.value(QStringLiteral("monitoring")).isBool()
        || !payload.value(QStringLiteral("inside_schedule")).isBool()
        || !nonNegativeInteger(payload.value(QStringLiteral("symbols")))
        || !nonNegativeInteger(payload.value(QStringLiteral("connections")))
        || !payload.value(QStringLiteral("wind")).isObject()
        || !freshServerTime(payload.value(QStringLiteral("server_time")))) {
        return QStringLiteral("health 缺少实时申购赎回服务的独特字段/新鲜 server_time");
    }
    return {};
}

QString realtimeSnapshotContractError(const QJsonObject &payload)
{
    if (payload.value(QStringLiteral("type")).toString() != QStringLiteral("snapshot")
        || !payload.value(QStringLiteral("items")).isArray()
        || !payload.value(QStringLiteral("monitoring")).isBool()
        || !payload.value(QStringLiteral("wind")).isObject()
        || !freshServerTime(payload.value(QStringLiteral("server_time")))) {
        return QStringLiteral("snapshot 缺少实时申购赎回服务的独特字段/新鲜 server_time");
    }
    for (const auto &value : payload.value(QStringLiteral("items")).toArray()) {
        if (!value.isObject()) return QStringLiteral("snapshot items 必须全部为对象");
        const QJsonObject item = value.toObject();
        if ((!item.value(QStringLiteral("symbol")).isString()
             && !item.value(QStringLiteral("windcode")).isString())
            || !item.value(QStringLiteral("status")).isString()
            || !item.value(QStringLiteral("values")).isObject()
            || !item.value(QStringLiteral("pcf")).isObject()
            || !item.value(QStringLiteral("opportunity")).isObject()) {
            return QStringLiteral("snapshot item 不符合申购赎回数据合同");
        }
    }
    return {};
}

qint64 millisecondsFromSeconds(const QJsonValue &value)
{
    if (!value.isDouble()) {
        return -1;
    }
    const double seconds = value.toDouble(-1.0);
    if (!std::isfinite(seconds) || seconds < 0.0) {
        return -1;
    }
    const double maximum = static_cast<double>(std::numeric_limits<qint64>::max());
    if (seconds >= maximum / 1000.0) {
        return std::numeric_limits<qint64>::max();
    }
    return qRound64(seconds * 1000.0);
}

} // namespace

struct RealtimeClient::PendingRequest {
    QString id;
    QString operation;
    QString symbol;
    ResponseKind kind = ResponseKind::Snapshot;
    bool control = false;
    bool timedOut = false;
    bool tooLarge = false;
    qint64 maximumBytes = 0;
    QByteArray responseBody;
    QElapsedTimer elapsed;
    QTimer *timeout = nullptr;
};

RealtimeClient::RealtimeClient(QObject *parent)
    : QObject(parent)
    , m_network(new QNetworkAccessManager(this))
    , m_socket(new QWebSocket(QString(), QWebSocketProtocol::VersionLatest, this))
    , m_healthTimer(new QTimer(this))
    , m_snapshotTimer(new QTimer(this))
    , m_reconnectTimer(new QTimer(this))
    , m_connectTimeoutTimer(new QTimer(this))
    , m_streamWatchdogTimer(new QTimer(this))
    , m_freshnessTimer(new QTimer(this))
    , m_baseUrl(QStringLiteral("http://127.0.0.1:6787"))
{
    qRegisterMetaType<RealtimeDataFreshness>();
    qRegisterMetaType<StreamState>();

    m_clock.start();
    m_network->setProxy(QNetworkProxy::NoProxy);
    m_socket->setProxy(QNetworkProxy::NoProxy);
    m_socket->setMaxAllowedIncomingFrameSize(static_cast<quint64>(m_maximumResponseBytes));
    m_socket->setMaxAllowedIncomingMessageSize(static_cast<quint64>(m_maximumResponseBytes));

    m_healthTimer->setInterval(m_healthPollIntervalMs);
    connect(m_healthTimer, &QTimer::timeout, this, &RealtimeClient::pollHealth);

    m_snapshotTimer->setInterval(m_snapshotPollIntervalMs);
    connect(m_snapshotTimer, &QTimer::timeout, this, &RealtimeClient::pollSnapshot);

    m_reconnectTimer->setSingleShot(true);
    connect(m_reconnectTimer, &QTimer::timeout, this, [this] { openStream(false); });

    m_connectTimeoutTimer->setSingleShot(true);
    connect(m_connectTimeoutTimer, &QTimer::timeout, this, [this] {
        if (m_socket->state() != QAbstractSocket::ConnectingState) {
            return;
        }
        setStreamState(StreamState::Failed, 0, QStringLiteral("WebSocket 连接超时"));
        m_socket->abort();
    });

    m_streamWatchdogTimer->setInterval(10'000);
    connect(m_streamWatchdogTimer, &QTimer::timeout, this, [this] {
        if (m_socket->state() != QAbstractSocket::ConnectedState) {
            return;
        }
        if (m_lastStreamMessage.isValid()
            && m_lastStreamMessage.elapsed() > m_streamSilenceTimeoutMs) {
            setStreamState(StreamState::Stale, 0, QStringLiteral("实时流超过静默期限"));
            m_socket->abort();
            return;
        }
        m_socket->sendTextMessage(QStringLiteral("{\"type\":\"ping\"}"));
    });

    m_freshnessTimer->setInterval(1'000);
    connect(m_freshnessTimer, &QTimer::timeout, this, &RealtimeClient::publishFreshness);

    connect(m_socket, &QWebSocket::connected, this, [this] {
        m_connectTimeoutTimer->stop();
        m_reconnectTimer->stop();
        m_nextReconnectMs = m_reconnectInitialMs;
        m_lastStreamMessage.start();
        m_streamWatchdogTimer->start();
        setStreamState(StreamState::Connected);
    });
    connect(m_socket, &QWebSocket::disconnected, this, &RealtimeClient::handleStreamDisconnected);
    connect(m_socket, &QWebSocket::textMessageReceived, this, &RealtimeClient::handleStreamText);
    connect(m_socket, &QWebSocket::binaryMessageReceived, this, [this](const QByteArray &) {
        setStreamState(StreamState::Failed, 0, QStringLiteral("服务端返回了不支持的二进制消息"));
        m_socket->close(
            QWebSocketProtocol::CloseCodeDatatypeNotSupported,
            QStringLiteral("JSON text required"));
    });
    connect(m_socket, &QWebSocket::errorOccurred, this, [this](QAbstractSocket::SocketError) {
        handleStreamError(redact(m_socket->errorString()));
    });
}

RealtimeClient::~RealtimeClient()
{
    stop();
}

QUrl RealtimeClient::baseUrl() const
{
    return m_baseUrl;
}

QUrl RealtimeClient::monitorUrl() const
{
    return endpointUrl(QStringLiteral("/"));
}

QUrl RealtimeClient::websocketUrl() const
{
    QUrl url = m_baseUrl;
    url.setScheme(m_baseUrl.scheme() == QStringLiteral("https") ? QStringLiteral("wss")
                                                                 : QStringLiteral("ws"));
    url.setPath(QStringLiteral("/ws/v1/changes"));
    url.setQuery(QString());
    url.setFragment(QString());
    return url;
}

bool RealtimeClient::controlsAllowed() const
{
    return isLiteralLoopback(m_baseUrl);
}

int RealtimeClient::requiredProtocolVersion() const
{
    return m_requiredProtocolVersion;
}

int RealtimeClient::requestTimeoutMs() const
{
    return m_requestTimeoutMs;
}

int RealtimeClient::controlTimeoutMs() const
{
    return m_controlTimeoutMs;
}

qint64 RealtimeClient::maximumResponseBytes() const
{
    return m_maximumResponseBytes;
}

int RealtimeClient::healthPollIntervalMs() const
{
    return m_healthPollIntervalMs;
}

int RealtimeClient::snapshotPollIntervalMs() const
{
    return m_snapshotPollIntervalMs;
}

int RealtimeClient::reconnectInitialMs() const
{
    return m_reconnectInitialMs;
}

int RealtimeClient::reconnectMaximumMs() const
{
    return m_reconnectMaximumMs;
}

int RealtimeClient::streamSilenceTimeoutMs() const
{
    return m_streamSilenceTimeoutMs;
}

qint64 RealtimeClient::dataFreshnessMs() const
{
    return m_dataFreshnessMs;
}

RealtimeClient::StreamState RealtimeClient::streamState() const
{
    return m_streamState;
}

bool RealtimeClient::healthKnown() const
{
    return m_healthKnown;
}

bool RealtimeClient::healthReady() const
{
    return m_healthReady;
}

QString RealtimeClient::healthReason() const
{
    return m_healthReason;
}

RealtimeDataFreshness RealtimeClient::dataFreshness() const
{
    RealtimeDataFreshness freshness;
    freshness.totalItems = m_expectedSymbols.size() + m_unmeasuredSnapshotItems;
    freshness.staleItems = m_unmeasuredSnapshotItems;
    freshness.lastSampleAt = m_lastDataSampleAt;
    freshness.evaluatedAt = QDateTime::currentDateTimeUtc();
    const qint64 nowElapsed = m_clock.isValid() ? m_clock.elapsed() : 0;

    for (const QString &symbol : m_expectedSymbols) {
        const auto sample = m_symbolFreshness.constFind(symbol);
        if (sample == m_symbolFreshness.cend() || sample->sourceAgeMs < 0) {
            ++freshness.staleItems;
            continue;
        }
        ++freshness.measuredItems;
        const qint64 elapsedSinceObservation = std::max<qint64>(0, nowElapsed - sample->observedElapsedMs);
        qint64 age = sample->sourceAgeMs;
        if (age > std::numeric_limits<qint64>::max() - elapsedSinceObservation) {
            age = std::numeric_limits<qint64>::max();
        } else {
            age += elapsedSinceObservation;
        }
        freshness.maximumAgeMs = std::max(freshness.maximumAgeMs, age);
        if (age > m_dataFreshnessMs) {
            ++freshness.staleItems;
        }
    }

    freshness.known = freshness.totalItems > 0 && freshness.measuredItems > 0;
    freshness.fresh = freshness.known && freshness.measuredItems == freshness.totalItems
        && freshness.staleItems == 0;
    return freshness;
}

QJsonObject RealtimeClient::lastHealthPayload() const
{
    return m_lastHealthPayload;
}

QJsonObject RealtimeClient::lastSnapshotPayload() const
{
    return m_lastSnapshotPayload;
}

bool RealtimeClient::setBaseUrl(const QUrl &url, QString *errorMessage)
{
    QUrl normalized = url.adjusted(QUrl::StripTrailingSlash);
    const QString scheme = normalized.scheme().toLower();
    const int explicitPort = normalized.port(-1);
    if (!normalized.isValid() || scheme != QStringLiteral("http")
        || normalized.host() != QStringLiteral("127.0.0.1")
        || explicitPort < 1 || explicitPort > 65535
        || !normalized.path().isEmpty() || normalized.hasQuery()
        || !normalized.fragment().isEmpty()) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("实时服务地址必须是含显式端口的 http://127.0.0.1 origin");
        }
        return false;
    }
    if (!normalized.userInfo().isEmpty()) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("地址中禁止包含用户名或密码");
        }
        return false;
    }

    normalized.setScheme(scheme);
    normalized.setPath(QString());
    normalized.setQuery(QString());
    normalized.setFragment(QString());
    if (normalized == m_baseUrl) {
        return true;
    }

    const bool oldControlsAllowed = controlsAllowed();
    const bool restart = m_started;
    m_wantsStream = false;
    m_reconnectTimer->stop();
    m_connectTimeoutTimer->stop();
    m_streamWatchdogTimer->stop();
    m_socket->abort();
    cancelAllRequests();

    m_baseUrl = normalized;
    m_protocolBlocked = false;
    m_nextReconnectMs = m_reconnectInitialMs;
    m_healthKnown = false;
    m_healthReady = false;
    m_insideSchedule = false;
    m_snapshotPollingExpected = false;
    m_healthReason.clear();
    m_lastHealthPayload = {};
    m_lastSnapshotPayload = {};
    clearFreshness();
    emit baseUrlChanged(m_baseUrl);
    if (oldControlsAllowed != controlsAllowed()) {
        emit controlsAllowedChanged(controlsAllowed());
    }

    if (restart) {
        m_wantsStream = true;
        pollHealth();
        openStream(false);
    } else {
        setStreamState(StreamState::Stopped);
    }
    return true;
}

void RealtimeClient::setRequiredProtocolVersion(int version)
{
    const int normalized = std::clamp(version, 1, 10'000);
    if (normalized == m_requiredProtocolVersion) {
        return;
    }
    m_requiredProtocolVersion = normalized;
    m_protocolBlocked = false;
    m_nextReconnectMs = m_reconnectInitialMs;
    if (m_started) {
        pollHealth();
        m_wantsStream = false;
        m_socket->abort();
        m_wantsStream = true;
        openStream(false);
    }
}

void RealtimeClient::setRequestTimeoutMs(int timeoutMs)
{
    m_requestTimeoutMs = std::clamp(timeoutMs, 250, 300'000);
}

void RealtimeClient::setControlTimeoutMs(int timeoutMs)
{
    m_controlTimeoutMs = std::clamp(timeoutMs, 1'000, 30 * 60 * 1000);
}

void RealtimeClient::setMaximumResponseBytes(qint64 bytes)
{
    m_maximumResponseBytes = std::clamp<qint64>(bytes, 1'024, 64LL * 1024 * 1024);
    m_socket->setMaxAllowedIncomingFrameSize(static_cast<quint64>(m_maximumResponseBytes));
    m_socket->setMaxAllowedIncomingMessageSize(static_cast<quint64>(m_maximumResponseBytes));
}

void RealtimeClient::setHealthPollIntervalMs(int intervalMs)
{
    m_healthPollIntervalMs = std::clamp(intervalMs, 1'000, 3'600'000);
    m_healthTimer->setInterval(m_healthPollIntervalMs);
}

void RealtimeClient::setSnapshotPollIntervalMs(int intervalMs)
{
    m_snapshotPollIntervalMs = std::clamp(intervalMs, 1'000, 3'600'000);
    m_snapshotTimer->setInterval(m_snapshotPollIntervalMs);
}

void RealtimeClient::setReconnectBackoff(int initialMs, int maximumMs)
{
    const int initial = std::clamp(initialMs, 250, 300'000);
    const int maximum = std::clamp(maximumMs, initial, 3'600'000);
    m_reconnectInitialMs = initial;
    m_reconnectMaximumMs = maximum;
    m_nextReconnectMs = std::clamp(m_nextReconnectMs, initial, maximum);
}

void RealtimeClient::setStreamSilenceTimeoutMs(int timeoutMs)
{
    m_streamSilenceTimeoutMs = std::clamp(timeoutMs, 5'000, 3'600'000);
    m_streamWatchdogTimer->setInterval(std::clamp(m_streamSilenceTimeoutMs / 3, 1'000, 10'000));
}

void RealtimeClient::setDataFreshnessMs(qint64 freshnessMs)
{
    m_dataFreshnessMs = std::clamp<qint64>(freshnessMs, 1'000, 24LL * 60 * 60 * 1000);
    publishFreshness();
}

QString RealtimeClient::requestHealth()
{
    if (!m_healthRequestId.isEmpty()) {
        return m_healthRequestId;
    }
    return issueRest(
        QByteArrayLiteral("GET"),
        endpointUrl(QStringLiteral("/api/v1/health")),
        {},
        QStringLiteral("health"),
        ResponseKind::Health);
}

QString RealtimeClient::requestSnapshot()
{
    if (!m_snapshotRequestId.isEmpty()) {
        return m_snapshotRequestId;
    }
    return issueRest(
        QByteArrayLiteral("GET"),
        endpointUrl(QStringLiteral("/api/v1/snapshot")),
        {},
        QStringLiteral("snapshot"),
        ResponseKind::Snapshot);
}

QString RealtimeClient::requestWatchlist()
{
    return issueRest(
        QByteArrayLiteral("GET"),
        endpointUrl(QStringLiteral("/api/v1/watchlist")),
        {},
        QStringLiteral("watchlist"),
        ResponseKind::Watchlist);
}

QString RealtimeClient::requestHistory(const QDate &date, const QString &symbol, int limit)
{
    if (limit < 1 || limit > 20'000) {
        return rejectLocally(
            QStringLiteral("history"),
            QStringLiteral("invalid_limit"),
            QStringLiteral("历史记录条数必须介于 1 和 20000 之间"),
            false);
    }

    QString normalizedSymbol;
    if (!symbol.trimmed().isEmpty()) {
        normalizedSymbol = normalizeSymbol(symbol);
        if (normalizedSymbol.isEmpty()) {
            return rejectLocally(
                QStringLiteral("history"),
                QStringLiteral("invalid_symbol"),
                QStringLiteral("历史查询标的必须是 6 位深圳 ETF 代码"),
                false);
        }
    }

    QUrl url = endpointUrl(QStringLiteral("/api/v1/history"));
    QUrlQuery query;
    if (date.isValid()) {
        query.addQueryItem(QStringLiteral("date"), date.toString(Qt::ISODate));
    }
    if (!normalizedSymbol.isEmpty()) {
        query.addQueryItem(QStringLiteral("symbol"), normalizedSymbol);
    }
    query.addQueryItem(QStringLiteral("limit"), QString::number(limit));
    url.setQuery(query);
    return issueRest(
        QByteArrayLiteral("GET"), url, {}, QStringLiteral("history"), ResponseKind::History);
}

QString RealtimeClient::requestPcfList()
{
    return issueRest(
        QByteArrayLiteral("GET"),
        endpointUrl(QStringLiteral("/api/v1/pcf")),
        {},
        QStringLiteral("pcf_list"),
        ResponseKind::PcfList);
}

QString RealtimeClient::requestPcfDetail(const QString &symbol)
{
    const QString normalized = normalizeSymbol(symbol);
    if (normalized.isEmpty()) {
        return rejectLocally(
            QStringLiteral("pcf_detail"),
            QStringLiteral("invalid_symbol"),
            QStringLiteral("PCF 标的必须是 6 位深圳 ETF 代码"),
            false);
    }
    return issueRest(
        QByteArrayLiteral("GET"),
        endpointUrl(QStringLiteral("/api/v1/pcf/") + normalized),
        {},
        QStringLiteral("pcf_detail"),
        ResponseKind::PcfDetail,
        false,
        normalized);
}

QString RealtimeClient::requestWindStatus()
{
    if (!controlsAllowed()) {
        return rejectLocally(
            QStringLiteral("wind_status"),
            QStringLiteral("loopback_required"),
            QStringLiteral("Wind 状态接口只允许连接字面 loopback 地址时调用"),
            false);
    }
    return issueRest(
        QByteArrayLiteral("GET"),
        endpointUrl(QStringLiteral("/api/v1/wind/status")),
        {},
        QStringLiteral("wind_status"),
        ResponseKind::WindStatus);
}

QString RealtimeClient::updateWatchlist(const QStringList &symbols)
{
    QStringList normalized;
    for (const QString &symbol : symbols) {
        const QString value = normalizeSymbol(symbol);
        if (value.isEmpty()) {
            return rejectLocally(
                QStringLiteral("watchlist_update"),
                QStringLiteral("invalid_symbol"),
                QStringLiteral("观察列表只接受 6 位深圳 ETF 代码"),
                true);
        }
        if (!normalized.contains(value)) {
            normalized.append(value);
        }
    }
    if (normalized.isEmpty() || normalized.size() > kMaximumWatchlistItems) {
        return rejectLocally(
            QStringLiteral("watchlist_update"),
            QStringLiteral("invalid_watchlist_size"),
            QStringLiteral("观察列表必须包含 1 至 200 个标的"),
            true);
    }
    return issueControl(
        QByteArrayLiteral("PUT"),
        QStringLiteral("/api/v1/watchlist"),
        QJsonObject{{QStringLiteral("symbols"), QJsonArray::fromStringList(normalized)}},
        QStringLiteral("watchlist_update"));
}

QString RealtimeClient::updateSymbolName(const QString &symbol, const QString &name)
{
    const QString normalized = normalizeSymbol(symbol);
    const QString normalizedName = name.simplified();
    if (normalized.isEmpty()) {
        return rejectLocally(
            QStringLiteral("symbol_name_update"),
            QStringLiteral("invalid_symbol"),
            QStringLiteral("名称标的必须是 6 位深圳 ETF 代码"),
            true);
    }
    if (normalizedName.size() > 40) {
        return rejectLocally(
            QStringLiteral("symbol_name_update"),
            QStringLiteral("invalid_name"),
            QStringLiteral("自定义名称不能超过 40 个字符"),
            true);
    }
    return issueControl(
        QByteArrayLiteral("PUT"),
        QStringLiteral("/api/v1/symbols/") + normalized + QStringLiteral("/name"),
        QJsonObject{{QStringLiteral("name"), normalizedName}},
        QStringLiteral("symbol_name_update"));
}

QString RealtimeClient::startMonitor()
{
    return issueControl(
        QByteArrayLiteral("POST"),
        QStringLiteral("/api/v1/monitor/start"),
        {},
        QStringLiteral("monitor_start"));
}

QString RealtimeClient::stopMonitor()
{
    return issueControl(
        QByteArrayLiteral("POST"),
        QStringLiteral("/api/v1/monitor/stop"),
        {},
        QStringLiteral("monitor_stop"));
}

QString RealtimeClient::startWind()
{
    return issueControl(
        QByteArrayLiteral("POST"),
        QStringLiteral("/api/v1/wind/start"),
        {},
        QStringLiteral("wind_start"));
}

QString RealtimeClient::shutdownWindCleanly()
{
    return issueControl(
        QByteArrayLiteral("POST"),
        QStringLiteral("/api/v1/wind/shutdown-cleanup"),
        {},
        QStringLiteral("wind_shutdown_cleanup"));
}

QString RealtimeClient::refreshPcf()
{
    return issueControl(
        QByteArrayLiteral("POST"),
        QStringLiteral("/api/v1/pcf/refresh"),
        {},
        QStringLiteral("pcf_refresh"));
}

void RealtimeClient::start()
{
    if (m_started) {
        pollHealth();
        pollSnapshot();
        if (m_socket->state() == QAbstractSocket::UnconnectedState) {
            connectStream();
        }
        return;
    }
    m_started = true;
    m_healthTimer->start();
    m_snapshotTimer->start();
    m_freshnessTimer->start();
    pollHealth();
    pollSnapshot();
    connectStream();
}

void RealtimeClient::stop()
{
    m_started = false;
    m_healthTimer->stop();
    m_snapshotTimer->stop();
    m_snapshotPollingExpected = false;
    m_freshnessTimer->stop();
    m_wantsStream = false;
    m_protocolBlocked = false;
    m_reconnectTimer->stop();
    m_connectTimeoutTimer->stop();
    m_streamWatchdogTimer->stop();
    m_socket->abort();
    cancelAllRequests();
    setStreamState(StreamState::Stopped);
}

void RealtimeClient::refreshAll()
{
    requestHealth();
    requestSnapshot();
    requestWatchlist();
    requestPcfList();
}

void RealtimeClient::connectStream()
{
    m_protocolBlocked = false;
    m_nextReconnectMs = m_reconnectInitialMs;
    m_wantsStream = true;
    openStream(true);
}

void RealtimeClient::disconnectStream()
{
    m_wantsStream = false;
    m_reconnectTimer->stop();
    m_connectTimeoutTimer->stop();
    m_streamWatchdogTimer->stop();
    if (m_socket->state() == QAbstractSocket::ConnectedState) {
        m_socket->close(QWebSocketProtocol::CloseCodeNormal, QStringLiteral("client disconnect"));
    } else {
        m_socket->abort();
    }
    setStreamState(StreamState::Stopped);
}

void RealtimeClient::requestStreamSnapshot()
{
    if (m_socket->state() == QAbstractSocket::ConnectedState) {
        m_socket->sendTextMessage(QStringLiteral("{\"type\":\"get_snapshot\"}"));
        return;
    }
    requestSnapshot();
}

QString RealtimeClient::issueRest(
    const QByteArray &method,
    const QUrl &url,
    const QByteArray &body,
    const QString &operation,
    ResponseKind kind,
    bool control,
    const QString &symbol)
{
    if (m_pending.size() >= kMaximumPendingRequests) {
        return rejectLocally(
            operation,
            QStringLiteral("too_many_requests"),
            QStringLiteral("待处理请求过多，请稍后重试"),
            control);
    }

    QNetworkRequest request(url);
    request.setAttribute(QNetworkRequest::CacheLoadControlAttribute, QNetworkRequest::AlwaysNetwork);
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
    request.setRawHeader("Accept", "application/json");
    request.setRawHeader("User-Agent", "MachomeHub/1");
    if (!body.isEmpty()) {
        request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    }
    // PCF detail can synchronously populate a missing local cache, and control
    // calls can include Wind startup/shutdown. Both legitimately outlive a
    // short liveness timeout.
    const int timeoutMs = (control || kind == ResponseKind::PcfDetail)
        ? m_controlTimeoutMs
        : m_requestTimeoutMs;
    request.setTransferTimeout(timeoutMs);

    QNetworkReply *reply = m_network->sendCustomRequest(request, method, body);
    reply->setReadBufferSize(m_maximumResponseBytes + 1);
    auto *pending = new PendingRequest;
    pending->id = newRequestId();
    pending->operation = operation;
    pending->symbol = symbol;
    pending->kind = kind;
    pending->control = control;
    pending->maximumBytes = m_maximumResponseBytes;
    pending->elapsed.start();
    pending->timeout = new QTimer(reply);
    pending->timeout->setSingleShot(true);
    pending->timeout->setInterval(timeoutMs);
    m_pending.insert(reply, pending);
    if (kind == ResponseKind::Health) {
        m_healthRequestId = pending->id;
    } else if (kind == ResponseKind::Snapshot) {
        m_snapshotRequestId = pending->id;
    }

    connect(pending->timeout, &QTimer::timeout, this, [this, reply] {
        const auto found = m_pending.find(reply);
        if (found == m_pending.end()) {
            return;
        }
        found.value()->timedOut = true;
        reply->abort();
    });
    connect(reply, &QNetworkReply::readyRead, this, [this, reply] { consumeRestData(reply); });
    connect(reply, &QNetworkReply::finished, this, [this, reply] { finishRest(reply); });
    pending->timeout->start();
    emit requestStarted(pending->id, operation, control);
    return pending->id;
}

QString RealtimeClient::issueControl(
    const QByteArray &method,
    const QString &path,
    const QJsonObject &body,
    const QString &action)
{
    if (!controlsAllowed()) {
        return rejectLocally(
            action,
            QStringLiteral("loopback_required"),
            QStringLiteral("该控制操作只能在服务器本机通过字面 loopback 地址执行"),
            true);
    }
    return issueRest(
        method,
        endpointUrl(path),
        QJsonDocument(body).toJson(QJsonDocument::Compact),
        action,
        ResponseKind::Control,
        true);
}

QString RealtimeClient::rejectLocally(
    const QString &operation,
    const QString &code,
    const QString &safeMessage,
    bool control)
{
    const QString requestId = newRequestId();
    const QString safe = redact(safeMessage);
    QTimer::singleShot(0, this, [this, requestId, operation, code, safe, control] {
        if (control) {
            emit controlRejected(requestId, operation, safe);
        }
        emit requestFailed(requestId, operation, code, safe, false, false);
    });
    return requestId;
}

void RealtimeClient::consumeRestData(QNetworkReply *reply)
{
    const auto found = m_pending.find(reply);
    if (found == m_pending.end()) {
        return;
    }
    PendingRequest *pending = found.value();
    if (pending->tooLarge) {
        return;
    }
    const QByteArray chunk = reply->readAll();
    if (chunk.size() > pending->maximumBytes - pending->responseBody.size()) {
        pending->tooLarge = true;
        pending->responseBody.clear();
        reply->abort();
        return;
    }
    pending->responseBody.append(chunk);
}

void RealtimeClient::finishRest(QNetworkReply *reply)
{
    const auto found = m_pending.find(reply);
    if (found == m_pending.end()) {
        reply->deleteLater();
        return;
    }
    PendingRequest *pending = found.value();
    if (!pending->tooLarge && !pending->timedOut && reply->isOpen()) {
        const QByteArray tail = reply->readAll();
        if (tail.size() > pending->maximumBytes - pending->responseBody.size()) {
            pending->tooLarge = true;
            pending->responseBody.clear();
        } else {
            pending->responseBody.append(tail);
        }
    }
    m_pending.erase(found);
    pending->timeout->stop();
    const qint64 latencyMs = pending->elapsed.isValid() ? pending->elapsed.elapsed() : -1;
    const int httpStatus = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    const QNetworkReply::NetworkError networkError = reply->error();
    const QString networkErrorText = redact(reply->errorString());
    if (pending->kind == ResponseKind::Health && m_healthRequestId == pending->id) {
        m_healthRequestId.clear();
    } else if (pending->kind == ResponseKind::Snapshot && m_snapshotRequestId == pending->id) {
        m_snapshotRequestId.clear();
    }

    QJsonObject payload;
    bool validJson = false;
    if (pending->responseBody.isEmpty() && isSuccessfulStatus(httpStatus)) {
        validJson = true;
    } else if (!pending->responseBody.isEmpty()) {
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(pending->responseBody, &parseError);
        validJson = parseError.error == QJsonParseError::NoError && document.isObject();
        if (validJson) {
            payload = document.object();
        }
    }

    auto fail = [this, pending, httpStatus](const QString &code, const QString &message, bool retryable) {
        const QString safe = redact(message);
        if (pending->kind == ResponseKind::Health) {
            setHealthReady(false, safe);
        }
        const bool outcomeUncertain = pending->kind == ResponseKind::Control
            && (pending->timedOut || pending->tooLarge || httpStatus == 0
                || httpStatus >= 500 || (httpStatus >= 200 && httpStatus < 300));
        emit requestFailed(pending->id, pending->operation, code, safe, retryable, outcomeUncertain);
    };

    if (pending->timedOut) {
        fail(QStringLiteral("timeout"), QStringLiteral("请求超时"), true);
    } else if (pending->tooLarge) {
        fail(QStringLiteral("response_too_large"), QStringLiteral("响应超过大小限制"), false);
    } else if (!isSuccessfulStatus(httpStatus) && httpStatus > 0) {
        const QString detail = validJson ? jsonDetail(payload) : QString{};
        fail(
            QStringLiteral("http_%1").arg(httpStatus),
            detail.isEmpty() ? QStringLiteral("服务端返回 HTTP %1").arg(httpStatus) : detail,
            httpStatus == 408 || httpStatus == 425 || httpStatus == 429 || httpStatus >= 500);
    } else if (networkError != QNetworkReply::NoError) {
        fail(
            QStringLiteral("network_error"),
            networkErrorText.isEmpty() ? QStringLiteral("网络请求失败") : networkErrorText,
            true);
    } else if (!validJson) {
        fail(QStringLiteral("invalid_json"), QStringLiteral("服务端未返回 JSON 对象"), false);
    } else {
        QString shapeError;
        switch (pending->kind) {
        case ResponseKind::Health:
            shapeError = realtimeHealthContractError(payload);
            break;
        case ResponseKind::Snapshot:
            shapeError = realtimeSnapshotContractError(payload);
            break;
        case ResponseKind::WindStatus:
        case ResponseKind::Control:
            if (payload.value(QStringLiteral("type")).toString() != QStringLiteral("snapshot")
                || !payload.value(QStringLiteral("items")).isArray()) {
                shapeError = QStringLiteral("快照响应缺少 type=snapshot 或 items 数组");
            }
            break;
        case ResponseKind::Watchlist:
            if (!payload.value(QStringLiteral("symbols")).isArray()) {
                shapeError = QStringLiteral("观察列表响应缺少 symbols 数组");
            }
            break;
        case ResponseKind::History:
            if (payload.value(QStringLiteral("type")).toString() != QStringLiteral("history")
                || !payload.value(QStringLiteral("items")).isArray()) {
                shapeError = QStringLiteral("历史响应缺少 type=history 或 items 数组");
            }
            break;
        case ResponseKind::PcfList:
            if (!payload.value(QStringLiteral("items")).isArray()) {
                shapeError = QStringLiteral("PCF 列表响应缺少 items 数组");
            }
            break;
        case ResponseKind::PcfDetail:
            if (!payload.value(QStringLiteral("symbol")).isString()) {
                shapeError = QStringLiteral("PCF 详情响应缺少 symbol");
            }
            break;
        }

        const bool requiresProtocol = pending->kind == ResponseKind::Health
            || pending->kind == ResponseKind::Snapshot || pending->kind == ResponseKind::History
            || pending->kind == ResponseKind::WindStatus
            || (pending->kind == ResponseKind::Control
                && payload.value(QStringLiteral("type")).toString() == QStringLiteral("snapshot"));
        if (!shapeError.isEmpty()) {
            fail(QStringLiteral("invalid_payload"), shapeError, false);
        } else if (requiresProtocol && !validateProtocol(payload, pending->operation, false)) {
            fail(
                QStringLiteral("protocol_mismatch"),
                QStringLiteral("服务端协议版本与客户端不匹配"),
                false);
        } else {
            if (pending->kind == ResponseKind::Health) {
                payload.insert(QStringLiteral("_hub_module_identity"),
                               QStringLiteral("etf-realtime-monitor"));
                payload.insert(QStringLiteral("_hub_health_contract_verified"), true);
                payload.insert(QStringLiteral("_hub_instance_observed_at"),
                               payload.value(QStringLiteral("server_time")));
            } else if (pending->kind == ResponseKind::Snapshot) {
                payload.insert(QStringLiteral("_hub_module_identity"),
                               QStringLiteral("etf-realtime-monitor"));
                payload.insert(QStringLiteral("_hub_snapshot_contract_verified"), true);
                payload.insert(QStringLiteral("_hub_instance_observed_at"),
                               payload.value(QStringLiteral("server_time")));
            }
            emit requestFinished(
                pending->id, pending->operation, payload, std::max<qint64>(0, latencyMs));

            switch (pending->kind) {
            case ResponseKind::Health: {
                m_lastHealthPayload = payload;
                emit healthReceived(pending->id, payload, std::max<qint64>(0, latencyMs));
                const bool ready = payload.value(QStringLiteral("ok")).toBool(false);
                setHealthReady(
                    ready,
                    ready ? QString{} : QStringLiteral("健康端点未确认 ok=true"));
                m_insideSchedule = payload.value(QStringLiteral("inside_schedule")).toBool(false);
                const bool shouldPollSnapshot = ready
                    && (payload.value(QStringLiteral("monitoring")).toBool(false) || m_insideSchedule);
                const bool newlyExpected = shouldPollSnapshot && !m_snapshotPollingExpected;
                m_snapshotPollingExpected = shouldPollSnapshot;
                if (newlyExpected && m_started) {
                    requestSnapshot();
                }
                break;
            }
            case ResponseKind::Snapshot:
                m_lastSnapshotPayload = payload;
                updateFreshnessFromEvent(payload, true);
                emit snapshotReceived(payload);
                break;
            case ResponseKind::Watchlist: {
                QStringList symbols;
                const QJsonArray values = payload.value(QStringLiteral("symbols")).toArray();
                for (const QJsonValue &value : values) {
                    const QString symbol = normalizeSymbol(value.toString());
                    if (!symbol.isEmpty() && !symbols.contains(symbol)
                        && symbols.size() < kMaximumWatchlistItems) {
                        symbols.append(symbol);
                    }
                }
                emit watchlistReceived(pending->id, symbols);
                break;
            }
            case ResponseKind::History:
                emit historyReceived(pending->id, payload);
                break;
            case ResponseKind::PcfList:
                emit pcfListReceived(pending->id, payload);
                break;
            case ResponseKind::PcfDetail:
                emit pcfDetailReceived(pending->id, pending->symbol, payload);
                break;
            case ResponseKind::WindStatus:
                m_lastSnapshotPayload = payload;
                updateFreshnessFromEvent(payload, true);
                emit windStatusReceived(pending->id, payload);
                emit snapshotReceived(payload);
                break;
            case ResponseKind::Control:
                if (payload.value(QStringLiteral("type")).toString() == QStringLiteral("snapshot")) {
                    m_lastSnapshotPayload = payload;
                    updateFreshnessFromEvent(payload, true);
                    emit snapshotReceived(payload);
                }
                emit controlFinished(pending->id, pending->operation, payload);
                break;
            }
        }
    }

    delete pending;
    reply->deleteLater();
}

void RealtimeClient::cancelAllRequests()
{
    const auto replies = m_pending.keys();
    for (QNetworkReply *reply : replies) {
        PendingRequest *pending = m_pending.take(reply);
        if (pending != nullptr && pending->timeout != nullptr) {
            pending->timeout->stop();
        }
        disconnect(reply, nullptr, this, nullptr);
        reply->abort();
        reply->deleteLater();
        delete pending;
    }
    m_healthRequestId.clear();
    m_snapshotRequestId.clear();
}

void RealtimeClient::pollHealth()
{
    requestHealth();
}

void RealtimeClient::pollSnapshot()
{
    if (m_snapshotPollingExpected) {
        requestSnapshot();
    }
}

void RealtimeClient::openStream(bool explicitRequest)
{
    if (!m_wantsStream || (m_protocolBlocked && !explicitRequest)) {
        return;
    }
    if (explicitRequest) {
        m_protocolBlocked = false;
    }
    if (m_socket->state() != QAbstractSocket::UnconnectedState) {
        return;
    }

    m_reconnectTimer->stop();
    QNetworkRequest request(websocketUrl());
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
    request.setRawHeader("User-Agent", "MachomeHub/1");
    setStreamState(StreamState::Connecting);
    m_socket->open(request);
    m_connectTimeoutTimer->start(m_requestTimeoutMs);
}

void RealtimeClient::handleStreamText(const QString &message)
{
    const QByteArray encoded = message.toUtf8();
    if (encoded.size() > m_maximumResponseBytes) {
        setStreamState(StreamState::Failed, 0, QStringLiteral("WebSocket 消息超过大小限制"));
        m_socket->close(
            QWebSocketProtocol::CloseCodeTooMuchData,
            QStringLiteral("message too large"));
        return;
    }

    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(encoded, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        setStreamState(StreamState::Failed, 0, QStringLiteral("WebSocket 消息不是 JSON 对象"));
        m_socket->close(
            QWebSocketProtocol::CloseCodeProtocolError,
            QStringLiteral("invalid JSON"));
        return;
    }

    const QJsonObject payload = document.object();
    const QString type = payload.value(QStringLiteral("type")).toString().trimmed().left(64);
    const bool versionedEvent = type == QStringLiteral("snapshot") || type == QStringLiteral("change")
        || type == QStringLiteral("status") || type == QStringLiteral("heartbeat");
    if ((versionedEvent || payload.contains(QStringLiteral("protocol")))
        && !validateProtocol(payload, QStringLiteral("websocket:") + type, true)) {
        return;
    }

    m_lastStreamMessage.restart();
    emit streamEventReceived(type, payload);
    if (type == QStringLiteral("snapshot")) {
        m_lastSnapshotPayload = payload;
        updateFreshnessFromEvent(payload, true);
        emit snapshotReceived(payload);
    } else if (type == QStringLiteral("change")) {
        updateFreshnessFromEvent(payload, false);
        emit changeReceived(payload);
    } else if (type == QStringLiteral("status")) {
        emit statusReceived(payload);
    } else if (type == QStringLiteral("heartbeat")) {
        emit heartbeatReceived(payload);
    }
}

void RealtimeClient::handleStreamDisconnected()
{
    m_connectTimeoutTimer->stop();
    m_streamWatchdogTimer->stop();
    if (!m_wantsStream) {
        setStreamState(StreamState::Stopped);
        return;
    }
    if (m_protocolBlocked) {
        setStreamState(StreamState::Failed, 0, QStringLiteral("协议版本不匹配，已暂停自动重连"));
        return;
    }
    scheduleReconnect(QStringLiteral("实时流已断开"));
}

void RealtimeClient::handleStreamError(const QString &safeMessage)
{
    if (!m_wantsStream) {
        return;
    }
    setStreamState(
        StreamState::Failed,
        0,
        safeMessage.isEmpty() ? QStringLiteral("WebSocket 连接失败") : safeMessage);
    if (m_socket->state() == QAbstractSocket::UnconnectedState) {
        scheduleReconnect(safeMessage);
    }
}

void RealtimeClient::scheduleReconnect(const QString &safeMessage)
{
    if (!m_wantsStream || m_protocolBlocked || m_reconnectTimer->isActive()) {
        return;
    }
    const int baseDelay = std::clamp(m_nextReconnectMs, m_reconnectInitialMs, m_reconnectMaximumMs);
    const int spread = std::max(1, baseDelay / 5);
    const int randomWidth = std::max(1, spread * 2 + 1);
    const int jitter = static_cast<int>(QRandomGenerator::global()->bounded(randomWidth)) - spread;
    const int delay = std::clamp(baseDelay + jitter, 250, m_reconnectMaximumMs);
    m_reconnectTimer->start(delay);
    m_nextReconnectMs = static_cast<int>(std::min<qint64>(
        m_reconnectMaximumMs,
        std::max<qint64>(m_reconnectInitialMs, static_cast<qint64>(baseDelay) * 2)));
    setStreamState(
        StreamState::Backoff,
        delay,
        safeMessage.isEmpty() ? QStringLiteral("等待重连") : redact(safeMessage));
}

void RealtimeClient::setStreamState(StreamState state, int retryInMs, const QString &safeMessage)
{
    m_streamState = state;
    emit streamStateChanged(state, std::max(0, retryInMs), redact(safeMessage));
}

bool RealtimeClient::validateProtocol(
    const QJsonObject &payload,
    const QString &source,
    bool closeStream)
{
    const QJsonValue value = payload.value(QStringLiteral("protocol"));
    const int actual = value.isDouble() ? value.toInt(-1) : -1;
    if (actual == m_requiredProtocolVersion) {
        return true;
    }
    emit protocolMismatch(m_requiredProtocolVersion, actual, source.left(128));
    if (closeStream) {
        m_protocolBlocked = true;
        setStreamState(StreamState::Failed, 0, QStringLiteral("实时流协议版本不匹配"));
        m_socket->close(
            QWebSocketProtocol::CloseCodeProtocolError,
            QStringLiteral("protocol mismatch"));
    }
    return false;
}

void RealtimeClient::updateFreshnessFromEvent(const QJsonObject &payload, bool fullSnapshot)
{
    const QJsonArray items = payload.value(QStringLiteral("items")).toArray();
    const qint64 observedElapsed = m_clock.elapsed();
    bool acceptedSample = false;

    if (fullSnapshot) {
        m_expectedSymbols.clear();
        m_symbolFreshness.clear();
        m_unmeasuredSnapshotItems = 0;
        m_lastDataSampleAt = {};
    }

    for (const QJsonValue &value : items) {
        if (!value.isObject()) {
            if (fullSnapshot) {
                ++m_unmeasuredSnapshotItems;
            }
            continue;
        }
        const QJsonObject item = value.toObject();
        QJsonObject current = item;
        if (!fullSnapshot && item.value(QStringLiteral("current")).isObject()) {
            current = item.value(QStringLiteral("current")).toObject();
        }

        QString symbol = normalizeSymbol(current.value(QStringLiteral("symbol")).toString());
        if (symbol.isEmpty()) {
            symbol = normalizeSymbol(item.value(QStringLiteral("symbol")).toString());
        }
        if (symbol.isEmpty()) {
            symbol = normalizeSymbol(current.value(QStringLiteral("windcode")).toString());
        }
        if (symbol.isEmpty()) {
            symbol = normalizeSymbol(item.value(QStringLiteral("windcode")).toString());
        }
        if (symbol.isEmpty()) {
            if (fullSnapshot) {
                ++m_unmeasuredSnapshotItems;
            }
            continue;
        }
        if (fullSnapshot && m_expectedSymbols.contains(symbol)) {
            ++m_unmeasuredSnapshotItems;
            continue;
        }
        if (!m_expectedSymbols.contains(symbol)) {
            m_expectedSymbols.append(symbol);
        }

        qint64 ageMs = millisecondsFromSeconds(current.value(QStringLiteral("age_seconds")));
        if (ageMs < 0 && !fullSnapshot) {
            // A change event is emitted from a just-observed callback. Current
            // server versions include age_seconds; zero is a safe fallback for
            // older version-1 payloads that omitted it.
            ageMs = 0;
        }
        if (ageMs < 0) {
            m_symbolFreshness.remove(symbol);
            continue;
        }
        m_symbolFreshness.insert(symbol, SymbolFreshnessSample{ageMs, observedElapsed});
        acceptedSample = true;
    }

    if (acceptedSample) {
        m_lastDataSampleAt = QDateTime::currentDateTimeUtc();
    }
    publishFreshness();
}

void RealtimeClient::clearFreshness()
{
    m_symbolFreshness.clear();
    m_expectedSymbols.clear();
    m_unmeasuredSnapshotItems = 0;
    m_lastDataSampleAt = {};
    publishFreshness();
}

void RealtimeClient::publishFreshness()
{
    emit freshnessUpdated(dataFreshness());
}

void RealtimeClient::setHealthReady(bool ready, const QString &safeReason)
{
    const QString reason = redact(safeReason);
    if (m_healthKnown && m_healthReady == ready && m_healthReason == reason) {
        return;
    }
    m_healthKnown = true;
    m_healthReady = ready;
    if (!ready) {
        m_snapshotPollingExpected = false;
    }
    m_healthReason = reason;
    emit healthStateChanged(ready, reason, QDateTime::currentDateTimeUtc());
}

QUrl RealtimeClient::endpointUrl(const QString &path) const
{
    QUrl url = m_baseUrl;
    url.setPath(path);
    url.setQuery(QString());
    url.setFragment(QString());
    return url;
}

bool RealtimeClient::isLiteralLoopback(const QUrl &url)
{
    const QString host = url.host().trimmed().toLower();
    if (host == QStringLiteral("localhost") || host == QStringLiteral("localhost.")) {
        return true;
    }
    QHostAddress address;
    return address.setAddress(host) && address.isLoopback();
}

QString RealtimeClient::normalizeSymbol(const QString &symbol)
{
    const QString value = symbol.trimmed().toUpper();
    static const QRegularExpression plain(QStringLiteral("^[0-9]{6}$"));
    static const QRegularExpression suffixed(QStringLiteral("^([0-9]{6})\\.SZ$"));
    static const QRegularExpression prefixed(QStringLiteral("^SZ([0-9]{6})$"));
    if (plain.match(value).hasMatch()) {
        return value;
    }
    const auto suffixMatch = suffixed.match(value);
    if (suffixMatch.hasMatch()) {
        return suffixMatch.captured(1);
    }
    const auto prefixMatch = prefixed.match(value);
    return prefixMatch.hasMatch() ? prefixMatch.captured(1) : QString{};
}

QString RealtimeClient::redact(const QString &text)
{
    QString value = text.left(2'048);
    static const QRegularExpression assignment(
        QStringLiteral("(?i)\\b(token|password|passwd|authorization|cookie|secret)\\b\\s*[:=]\\s*[^\\s,;]+"));
    value.replace(assignment, QStringLiteral("\\1=[REDACTED]"));
    static const QRegularExpression bearer(QStringLiteral("(?i)\\bBearer\\s+[A-Za-z0-9._~+/=-]+"));
    value.replace(bearer, QStringLiteral("Bearer [REDACTED]"));
    return value;
}

} // namespace machome::bridge
