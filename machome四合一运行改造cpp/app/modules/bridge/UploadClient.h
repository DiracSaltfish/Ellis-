#pragma once

#include <QByteArray>
#include <QDateTime>
#include <QElapsedTimer>
#include <QJsonObject>
#include <QList>
#include <QObject>
#include <QStringList>
#include <QUrl>

class QNetworkAccessManager;
class QNetworkReply;
class QTimer;

namespace machome::bridge {

struct UploadSiteStatus {
    bool reachable = false;
    bool healthy = false;
    int httpStatus = 0;
    qint64 latencyMs = -1;
    QDateTime observedAt;
    QString errorCode;
    QString errorMessage;
    QJsonObject payload;
};

struct UploadWorkerStatus {
    QString source;
    qint64 pid = -1;
    QString state;
    QString stage;
    QDateTime updatedAt;
    QDateTime lastSuccessAt;
    QDateTime lastFailureAt;
    QDateTime lastHeartbeatAt;
    qint64 statusAgeMs = -1;
    qint64 successAgeMs = -1;
    bool statusFresh = false;
    bool successFresh = false;
    qint64 accepted = -1;
    QStringList symbols;
    QString detail;
    QString lastError;
};

using UploadWorkerStatusList = QList<UploadWorkerStatus>;

/**
 * Read-only adapter for the local NewNavNav site and its uploader health files.
 *
 * Network operations are event-driven. Health files are read on QThreadPool so
 * a slow directory or filesystem cannot block the object's event-loop thread.
 */
class UploadClient final : public QObject {
    Q_OBJECT

public:
    enum class AggregateState {
        Unknown,
        Healthy,
        ScheduledIdle,
        Degraded,
        Offline,
    };
    Q_ENUM(AggregateState)

    explicit UploadClient(QObject *parent = nullptr);
    ~UploadClient() override;

    QUrl baseUrl() const;
    QString healthDirectory() const;
    int probeIntervalMs() const;
    int requestTimeoutMs() const;
    qint64 maximumResponseBytes() const;
    qint64 workerFreshnessMs() const;
    bool workersExpected() const;

    // Only http(s) origins without user-info are accepted. Path/query/fragment
    // are discarded so credentials cannot accidentally become part of probes.
    bool setBaseUrl(const QUrl &url, QString *errorMessage = nullptr);
    void setHealthDirectory(const QString &directory);
    void setProbeIntervalMs(int intervalMs);
    void setRequestTimeoutMs(int timeoutMs);
    void setMaximumResponseBytes(qint64 bytes);
    void setWorkerFreshnessMs(qint64 freshnessMs);
    void setWorkersExpected(bool expected);

    UploadSiteStatus siteStatus() const;
    UploadWorkerStatusList workerStatuses() const;
    AggregateState aggregateState() const;

    QUrl deepLink(const QString &absolutePath) const;
    QUrl homeUrl() const;
    QUrl debugUrl() const;
    QUrl navSettingsUrl() const;
    QUrl fundUrl(const QString &symbol) const;
    QUrl effectiveRatioHistoryUrl(const QString &symbol) const;
    QUrl shareHistoryUrl(const QString &symbol) const;

public slots:
    void start();
    void stop();
    void refresh();

signals:
    void baseUrlChanged(const QUrl &url);
    void healthDirectoryChanged(const QString &directory);
    void siteStatusChanged(const machome::bridge::UploadSiteStatus &status);
    void workerStatusesChanged(const machome::bridge::UploadWorkerStatusList &workers);
    void healthScanFinished(int validFiles, int invalidFiles, bool limited);
    void aggregateStatusChanged(
        machome::bridge::UploadClient::AggregateState state,
        int healthyWorkers,
        int totalWorkers,
        const QString &summary);
    void probeError(const QString &code, const QString &safeMessage);

private:
    struct HealthScanResult;

    void beginSiteProbe();
    void beginSiteProbeRequest(const QString &path, bool identityStage);
    void finishSiteProbe();
    void beginHealthScan();
    void applyHealthScan(quint64 generation, const HealthScanResult &result);
    void recomputeAggregate();
    QUrl endpointUrl(const QString &path) const;
    static QString normalizeFundSymbol(const QString &symbol);
    static QString redact(const QString &text);

    QNetworkAccessManager *m_network = nullptr;
    QTimer *m_probeTimer = nullptr;
    QTimer *m_probeTimeout = nullptr;
    QNetworkReply *m_probeReply = nullptr;
    QByteArray m_probeBody;
    bool m_probeTimedOut = false;
    bool m_probeTooLarge = false;
    bool m_probeIdentityStage = false;
    bool m_refreshQueued = false;
    QElapsedTimer m_probeElapsed;
    QJsonObject m_probeHealthPayload;

    QUrl m_baseUrl;
    QString m_healthDirectory;
    int m_probeIntervalMs = 5'000;
    int m_requestTimeoutMs = 4'000;
    qint64 m_maximumResponseBytes = 256 * 1024;
    qint64 m_workerFreshnessMs = 35'000;
    bool m_workersExpected = true;
    bool m_started = false;
    bool m_healthScanInFlight = false;
    bool m_healthScanQueued = false;
    quint64 m_healthScanGeneration = 0;

    UploadSiteStatus m_siteStatus;
    UploadWorkerStatusList m_workerStatuses;
    AggregateState m_aggregateState = AggregateState::Unknown;
};

} // namespace machome::bridge

Q_DECLARE_METATYPE(machome::bridge::UploadSiteStatus)
Q_DECLARE_METATYPE(machome::bridge::UploadWorkerStatus)
Q_DECLARE_METATYPE(machome::bridge::UploadWorkerStatusList)
