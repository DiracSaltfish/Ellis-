#pragma once

#include <QByteArray>
#include <QDate>
#include <QDateTime>
#include <QElapsedTimer>
#include <QHash>
#include <QJsonObject>
#include <QObject>
#include <QStringList>
#include <QUrl>

class QNetworkAccessManager;
class QNetworkReply;
class QTimer;
class QWebSocket;

namespace machome::bridge {

struct RealtimeDataFreshness {
    bool known = false;
    bool fresh = false;
    int totalItems = 0;
    int measuredItems = 0;
    int staleItems = 0;
    qint64 maximumAgeMs = -1;
    QDateTime lastSampleAt;
    QDateTime evaluatedAt;
};

/**
 * Asynchronous Qt adapter for the ETF real-time subscription service.
 *
 * The object never waits for HTTP, WebSocket, DNS, or timers synchronously.
 * Put one instance in the owning module's event-loop thread and cross thread
 * boundaries with queued signals/slots.
 */
class RealtimeClient final : public QObject {
    Q_OBJECT

public:
    enum class StreamState {
        Stopped,
        Connecting,
        Connected,
        Backoff,
        Stale,
        Failed,
    };
    Q_ENUM(StreamState)

    explicit RealtimeClient(QObject *parent = nullptr);
    ~RealtimeClient() override;

    QUrl baseUrl() const;
    QUrl monitorUrl() const;
    QUrl websocketUrl() const;
    bool controlsAllowed() const;
    int requiredProtocolVersion() const;
    int requestTimeoutMs() const;
    int controlTimeoutMs() const;
    qint64 maximumResponseBytes() const;
    int healthPollIntervalMs() const;
    int snapshotPollIntervalMs() const;
    int reconnectInitialMs() const;
    int reconnectMaximumMs() const;
    int streamSilenceTimeoutMs() const;
    qint64 dataFreshnessMs() const;
    StreamState streamState() const;
    bool healthKnown() const;
    bool healthReady() const;
    QString healthReason() const;
    RealtimeDataFreshness dataFreshness() const;
    QJsonObject lastHealthPayload() const;
    QJsonObject lastSnapshotPayload() const;

    // Only an http(s) origin without user-info is retained. Path, query, and
    // fragment are discarded. Mutations are enabled only for literal loopback
    // hosts (localhost or a numeric loopback address); hostnames are not DNS-
    // resolved for this decision.
    bool setBaseUrl(const QUrl &url, QString *errorMessage = nullptr);
    void setRequiredProtocolVersion(int version);
    void setRequestTimeoutMs(int timeoutMs);
    void setControlTimeoutMs(int timeoutMs);
    void setMaximumResponseBytes(qint64 bytes);
    void setHealthPollIntervalMs(int intervalMs);
    void setSnapshotPollIntervalMs(int intervalMs);
    void setReconnectBackoff(int initialMs, int maximumMs);
    void setStreamSilenceTimeoutMs(int timeoutMs);
    void setDataFreshnessMs(qint64 freshnessMs);

    QString requestHealth();
    QString requestSnapshot();
    QString requestWatchlist();
    QString requestHistory(const QDate &date = {}, const QString &symbol = {}, int limit = 500);
    QString requestPcfList();
    QString requestPcfDetail(const QString &symbol);
    QString requestWindStatus();

    QString updateWatchlist(const QStringList &symbols);
    QString updateSymbolName(const QString &symbol, const QString &name);
    QString startMonitor();
    QString stopMonitor();
    QString startWind();
    QString shutdownWindCleanly();
    QString refreshPcf();

public slots:
    void start();
    void stop();
    void refreshAll();
    void connectStream();
    void disconnectStream();
    void requestStreamSnapshot();

signals:
    void baseUrlChanged(const QUrl &url);
    void controlsAllowedChanged(bool allowed);
    void streamStateChanged(
        machome::bridge::RealtimeClient::StreamState state,
        int retryInMs,
        const QString &safeMessage);
    void streamEventReceived(const QString &type, const QJsonObject &payload);
    void snapshotReceived(const QJsonObject &payload);
    void changeReceived(const QJsonObject &payload);
    void statusReceived(const QJsonObject &payload);
    void heartbeatReceived(const QJsonObject &payload);
    void freshnessUpdated(const machome::bridge::RealtimeDataFreshness &freshness);

    void requestStarted(const QString &requestId, const QString &operation, bool control);
    void requestFinished(
        const QString &requestId,
        const QString &operation,
        const QJsonObject &payload,
        qint64 latencyMs);
    void requestFailed(
        const QString &requestId,
        const QString &operation,
        const QString &code,
        const QString &safeMessage,
        bool retryable,
        bool outcomeUncertain);
    void controlRejected(
        const QString &requestId,
        const QString &action,
        const QString &safeReason);
    void controlFinished(
        const QString &requestId,
        const QString &action,
        const QJsonObject &payload);

    void healthReceived(const QString &requestId, const QJsonObject &payload, qint64 latencyMs);
    void healthStateChanged(bool ready, const QString &safeReason, const QDateTime &observedAt);
    void watchlistReceived(const QString &requestId, const QStringList &symbols);
    void historyReceived(const QString &requestId, const QJsonObject &payload);
    void pcfListReceived(const QString &requestId, const QJsonObject &payload);
    void pcfDetailReceived(const QString &requestId, const QString &symbol, const QJsonObject &payload);
    void windStatusReceived(const QString &requestId, const QJsonObject &payload);
    void protocolMismatch(int expected, int actual, const QString &source);

private:
    enum class ResponseKind {
        Health,
        Snapshot,
        Watchlist,
        History,
        PcfList,
        PcfDetail,
        WindStatus,
        Control,
    };

    struct PendingRequest;
    struct SymbolFreshnessSample {
        qint64 sourceAgeMs = -1;
        qint64 observedElapsedMs = 0;
    };

    QString issueRest(
        const QByteArray &method,
        const QUrl &url,
        const QByteArray &body,
        const QString &operation,
        ResponseKind kind,
        bool control = false,
        const QString &symbol = {});
    QString issueControl(
        const QByteArray &method,
        const QString &path,
        const QJsonObject &body,
        const QString &action);
    QString rejectLocally(
        const QString &operation,
        const QString &code,
        const QString &safeMessage,
        bool control);
    void consumeRestData(QNetworkReply *reply);
    void finishRest(QNetworkReply *reply);
    void cancelAllRequests();
    void pollHealth();
    void pollSnapshot();

    void openStream(bool explicitRequest);
    void handleStreamText(const QString &message);
    void handleStreamDisconnected();
    void handleStreamError(const QString &safeMessage);
    void scheduleReconnect(const QString &safeMessage);
    void setStreamState(StreamState state, int retryInMs = 0, const QString &safeMessage = {});
    bool validateProtocol(const QJsonObject &payload, const QString &source, bool closeStream);

    void updateFreshnessFromEvent(const QJsonObject &payload, bool fullSnapshot);
    void clearFreshness();
    void publishFreshness();
    void setHealthReady(bool ready, const QString &safeReason);

    QUrl endpointUrl(const QString &path) const;
    static bool isLiteralLoopback(const QUrl &url);
    static QString normalizeSymbol(const QString &symbol);
    static QString redact(const QString &text);

    QNetworkAccessManager *m_network = nullptr;
    QWebSocket *m_socket = nullptr;
    QTimer *m_healthTimer = nullptr;
    QTimer *m_snapshotTimer = nullptr;
    QTimer *m_reconnectTimer = nullptr;
    QTimer *m_connectTimeoutTimer = nullptr;
    QTimer *m_streamWatchdogTimer = nullptr;
    QTimer *m_freshnessTimer = nullptr;
    QHash<QNetworkReply *, PendingRequest *> m_pending;

    QUrl m_baseUrl;
    int m_requiredProtocolVersion = 1;
    int m_requestTimeoutMs = 5'000;
    int m_controlTimeoutMs = 120'000;
    qint64 m_maximumResponseBytes = 8LL * 1024 * 1024;
    int m_healthPollIntervalMs = 5'000;
    int m_snapshotPollIntervalMs = 5'000;
    int m_reconnectInitialMs = 1'000;
    int m_reconnectMaximumMs = 30'000;
    int m_nextReconnectMs = 1'000;
    int m_streamSilenceTimeoutMs = 45'000;
    qint64 m_dataFreshnessMs = 10'000;
    bool m_started = false;
    bool m_wantsStream = false;
    bool m_protocolBlocked = false;
    bool m_healthKnown = false;
    bool m_healthReady = false;
    bool m_insideSchedule = false;
    bool m_snapshotPollingExpected = false;
    QString m_healthReason;
    QString m_healthRequestId;
    QString m_snapshotRequestId;
    StreamState m_streamState = StreamState::Stopped;

    QElapsedTimer m_clock;
    QElapsedTimer m_lastStreamMessage;
    QHash<QString, SymbolFreshnessSample> m_symbolFreshness;
    QStringList m_expectedSymbols;
    int m_unmeasuredSnapshotItems = 0;
    QDateTime m_lastDataSampleAt;
    QJsonObject m_lastHealthPayload;
    QJsonObject m_lastSnapshotPayload;
};

} // namespace machome::bridge

Q_DECLARE_METATYPE(machome::bridge::RealtimeDataFreshness)
Q_DECLARE_METATYPE(machome::bridge::RealtimeClient::StreamState)
