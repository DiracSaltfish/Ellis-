#pragma once

#include <QByteArray>
#include <QDateTime>
#include <QJsonObject>
#include <QList>
#include <QMetaType>
#include <QObject>
#include <QString>
#include <QUrl>

class QThread;
class WebullClientWorker;

namespace Machome::Webull {

enum class LogSeverity {
    Debug,
    Info,
    Warning,
    Error,
};

struct LogEntry {
    QDateTime occurredAt;
    LogSeverity severity = LogSeverity::Info;
    QString code;
    QString message;
};

struct ApiError {
    QDateTime occurredAt;
    QString code;
    QString endpoint;
    QString message;
    int httpStatus = 0;
    int networkError = 0;
    bool retryable = false;
};

struct PriceLevel {
    int level = 0;
    QString price;
    QString volume;
};

struct InsideMarket {
    QString bestBid;
    QString bestAsk;
    QString spread;
    QString midPrice;
    QString state;
};

struct BookSnapshot {
    QString symbol;
    QString tickerId;
    QString sessionId;
    qint64 sequence = 0;
    QDateTime capturedAt;
    QDateTime publishedAt;
    bool changed = false;
    QString contentHash;
    double sourceIntervalMs = 0.0;
    bool hasSourceInterval = false;
    double gatewayLatencyMs = 0.0;
    InsideMarket inside;
    QList<PriceLevel> bids;
    QList<PriceLevel> asks;
    QString source;
    qint64 ageMs = -1;
    bool fresh = false;
};

struct ClientInfo {
    QString clientId;
    QString remote;
    QDateTime connectedAt;
    QDateTime lastSentAt;
    bool hasLastSentAt = false;
    qint64 messagesSent = 0;
};

struct SymbolInfo {
    QString symbol;
    QString tickerId;
    int depthSize = 0;
    qint64 staleAfterMs = 0;
};

struct GatewayStatus {
    QDateTime observedAt;
    qint64 readyProbeIssuedMonotonicMs = -1;
    qint64 statusProbeIssuedMonotonicMs = -1;
    bool apiLive = false;
    bool apiReady = false;
    bool apiReportedRunning = false;
    bool streamConnected = false;
    bool pollingFallback = false;
    bool collectorRunning = false;
    bool dataFresh = false;
    qint64 dataAgeMs = -1;
    qint64 staleAfterMs = 90'000;
    int clientCount = 0;

    QString service;
    QString browserState;
    QString authState;
    QString dataState;
    QString browserMessage;
    QString authMessage;
    QString dataMessage;
    QString scheduleMode;
    QString scheduleMessage;
    QString loginMonitorMessage;
    QString apiAddress;
    QString lastDepthAt;
    QString lastAuthSuccessAt;
    QString lastAuthRequiredAt;
    QString lastAuthCheckedAt;
    QString lastLoginCheckAt;
    QString lastLoginAlertAt;
    QString lastLoginAlertError;
    QString lastError;

    qint64 validResponses = 0;
    qint64 invalidResponses = 0;
    double sourceIntervalMs = 0.0;
    bool hasSourceInterval = false;
    double gatewayLatencyMs = 0.0;
    bool hasGatewayLatency = false;
    QJsonObject rawStatus;
};

struct WebullClientConfig {
    // Either http://host:port/v2 or http://host:port. The latter is normalized
    // to /v2. Other paths, query, user-info and URL fragments are rejected.
    QUrl apiBaseUrl = QUrl(QStringLiteral("http://127.0.0.1:18765/v2"));

    // Empty means derive ws(s)://host:port/v2/stream from apiBaseUrl. An
    // explicit URL must use exactly /v2/stream; its query is replaced with the
    // configured symbol.
    QUrl streamUrl;
    // Optional loopback-only companion runner, for example
    // http://127.0.0.1:18766/v1. Empty keeps all control disabled.
    QUrl controlBaseUrl;
    QString symbol = QStringLiteral("XOP");

    // Direct token takes precedence over tokenFile. The value is never emitted
    // in a signal or diagnostic. Prefer tokenFile for production.
    QByteArray bearerToken;
    QString tokenFile;
    // Mutating loopback control uses an independent owner-only credential.
    // It must never reuse a data API token that may be distributed to LAN
    // subscribers.
    QString controlTokenFile;

    int pollIntervalMs = 2'000;
    int metadataPollIntervalMs = 60'000;
    int requestTimeoutMs = 5'000;
    int controlRequestTimeoutMs = 45'000;
    int freshnessCheckIntervalMs = 1'000;
    int websocketConnectTimeoutMs = 8'000;
    // The gateway emits an application heartbeat after 25 seconds without a
    // book, so 60 seconds tolerates one delayed heartbeat while still
    // detecting a silent/half-open v2 stream promptly.
    int websocketSilenceTimeoutMs = 60'000;
    int reconnectInitialMs = 1'000;
    int reconnectMaximumMs = 30'000;
    int restBackoffMaximumMs = 30'000;
    qint64 maximumResponseBytes = 1 * 1024 * 1024;
    qint64 maximumWebSocketMessageBytes = 1 * 1024 * 1024;
    qint64 defaultStaleAfterMs = 90'000;
    qint64 maximumFutureSkewMs = 5'000;
    int maximumBookLevels = 50;
    int maximumClients = 4'096;
};

using ClientList = QList<ClientInfo>;
using SymbolList = QList<SymbolInfo>;

}  // namespace Machome::Webull

Q_DECLARE_METATYPE(Machome::Webull::LogSeverity)
Q_DECLARE_METATYPE(Machome::Webull::LogEntry)
Q_DECLARE_METATYPE(Machome::Webull::ApiError)
Q_DECLARE_METATYPE(Machome::Webull::PriceLevel)
Q_DECLARE_METATYPE(Machome::Webull::InsideMarket)
Q_DECLARE_METATYPE(Machome::Webull::BookSnapshot)
Q_DECLARE_METATYPE(Machome::Webull::ClientInfo)
Q_DECLARE_METATYPE(Machome::Webull::ClientList)
Q_DECLARE_METATYPE(Machome::Webull::SymbolInfo)
Q_DECLARE_METATYPE(Machome::Webull::SymbolList)
Q_DECLARE_METATYPE(Machome::Webull::GatewayStatus)
Q_DECLARE_METATYPE(Machome::Webull::WebullClientConfig)

// Thread-safe facade. Network objects are created and remain in an internal
// QThread. Every public slot queues work and returns immediately.
class WebullClient final : public QObject {
    Q_OBJECT

public:
    explicit WebullClient(
        Machome::Webull::WebullClientConfig config = {},
        QObject *parent = nullptr
    );
    ~WebullClient() override;

    WebullClient(const WebullClient &) = delete;
    WebullClient &operator=(const WebullClient &) = delete;

    QString symbol() const;
    QUrl apiBaseUrl() const;

public slots:
    void start();
    void stop();
    void refreshNow();
    void reloadCredentials();
    void setBearerToken(const QByteArray &token);
    void setTokenFile(const QString &path);
    // Idempotent request_id is supplied by Hub Agent and may be safely reused
    // to query/retry an uncertain runner response. Supported actions are
    // set_schedule_mode, collector_start, collector_stop, open_login and
    // restart_browser.
    void submitControl(
        const QString &requestId,
        const QString &action,
        const QJsonObject &arguments = {}
    );

signals:
    void started();
    void stopped();
    void statusUpdated(const Machome::Webull::GatewayStatus &status);
    void bookUpdated(const Machome::Webull::BookSnapshot &snapshot);
    void clientsUpdated(const Machome::Webull::ClientList &clients);
    void symbolsUpdated(const Machome::Webull::SymbolList &symbols);
    void logEntry(const Machome::Webull::LogEntry &entry);
    void errorOccurred(const Machome::Webull::ApiError &error);
    void freshnessChanged(bool fresh, qint64 ageMs);
    void transportStateChanged(
        bool apiLive,
        bool streamConnected,
        bool pollingFallback
    );
    void controlCompleted(
        const QString &requestId,
        const QString &action,
        const QJsonObject &result
    );
    void controlFailed(
        const QString &requestId,
        const QString &action,
        const QString &code,
        const QString &message,
        bool outcomeUncertain
    );

private:
    friend class WebullClientWorker;

    QThread *thread_ = nullptr;
    WebullClientWorker *worker_ = nullptr;
    Machome::Webull::WebullClientConfig config_;
};
