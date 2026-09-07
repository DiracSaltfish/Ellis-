#pragma once

#include "common/ModuleBackend.h"
#include "common/WorkerSchedule.h"

#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QPointer>
#include <QQueue>
#include <QSet>
#include <QTimer>

namespace machome::bridge {
class RealtimeClient;
struct RealtimeDataFreshness;
}
namespace machome::upload { class UploadEngine; }
namespace machome::premium::engine { class PremiumAEngine; }
namespace machome::premium { class PremiumClient; }
namespace machome::qmt { class QmtClient; }
namespace machome::redemption { class RedemptionEngine; }
namespace machome::webull { class WebullEngine; }
namespace Machome::Webull {
struct GatewayStatus;
struct BookSnapshot;
struct ClientInfo;
struct SymbolInfo;
struct LogEntry;
struct ApiError;
}

namespace hub {

class LifecycleController;

class ModuleWorker final : public ModuleBackend {
    Q_OBJECT
public:
    explicit ModuleWorker(ModuleConfig config, quint64 initialControlRevision = 0,
                          QObject *parent = nullptr);
    ~ModuleWorker() override;

public slots:
    void start() override;
    void stop() override;
    void requestSnapshot() override;
    void submitCommand(const QJsonObject &command) override;
    void setControlRevision(quint64 revision);

private:
    void startUpload();
    void startPremium();
    void startPremiumProbe();
    void startWebull();
    void startRealtime();
    void startRedemption();
    void publishSnapshot();
    void scheduleSnapshot();
    void publishEvent(const QString &kind, const QJsonObject &payload);
    void drainEventMailbox();
    void publishCommand(const QJsonObject &command, const QString &state,
                        const QString &message, const QJsonObject &details = {});
    bool allowMutation(const QJsonObject &command);
    bool rememberPending(const QString &key, const QJsonObject &command, int timeoutMs = 15000);
    void finishPending(const QString &key, bool ok, const QString &message,
                       const QJsonObject &details = {});
    void beginLifecycleReadiness(const QJsonObject &result);
    void checkLifecycleReadiness();
    void suspendAdaptersForReadinessProbe();
    void startFreshReadinessProbe(quint64 generation, const QString &commandId);
    bool businessReadinessSatisfied(QString *reason = nullptr) const;
    void markTelemetryObserved(const QString &root, qint64 sourceEpochMs = 0);
    void updatePremiumBusinessReadiness();
    void updateRealtimeServiceIdentity(bool adapterObservation);
    void resumeAdaptersAfterReadinessProbe();

    QJsonObject webullStatusJson(const Machome::Webull::GatewayStatus &status) const;
    QJsonObject webullBookJson(const Machome::Webull::BookSnapshot &book) const;
    void verifyWebullControlPostconditions(const Machome::Webull::GatewayStatus &status);

    LifecycleController *lifecycle_ = nullptr;
    machome::upload::UploadEngine *upload_ = nullptr;
    machome::premium::engine::PremiumAEngine *premium_ = nullptr;
    machome::premium::PremiumClient *premiumProbe_ = nullptr;
    machome::webull::WebullEngine *webull_ = nullptr;
    machome::bridge::RealtimeClient *realtime_ = nullptr;
    machome::redemption::RedemptionEngine *redemption_ = nullptr;
    QHash<QString, machome::qmt::QmtClient *> qmtClients_;
    QTimer publishTimer_;
    QTimer eventDrainTimer_;
    QTimer lifecycleReadinessTimer_;
    QJsonObject process_;
    QJsonObject telemetry_;
    QString lastError_;
    QString workState_ = QStringLiteral("warming");
    QString headline_;
    int signalCount_ = 0;
    quint64 controlRevision_ = 0;
    QHash<QString, QJsonObject> pendingCommands_;
    QHash<QString, qint64> pendingTransportQuiescenceUntilMs_;
    QSet<QString> timedOutPending_;
    QHash<QString, QString> realtimeRequestToPendingKey_;
    QHash<QString, QJsonObject> webullControlRequests_;
    QString activeCommandId_;
    QJsonObject pendingLifecycleResult_;
    qint64 lifecycleReadinessDeadlineMs_ = 0;
    qint64 lifecycleReadinessNotBeforeMs_ = 0;
    quint64 lifecycleReadinessObservationBaseline_ = 0;
    bool lifecycleReadinessTimeoutEmitted_ = false;
    bool lifecycleReadinessProbeIssued_ = false;
    bool readinessAdaptersSuspended_ = false;
    quint64 lifecycleReadinessGeneration_ = 0;
    quint64 telemetryObservationSequence_ = 0;
    QHash<QString, quint64> telemetryObservedAt_;
    QHash<QString, qint64> telemetryObservedAtEpochMs_;
    QHash<QString, QJsonObject> latestEventMailbox_;
    QQueue<QJsonObject> ordinaryEventMailbox_;
    QQueue<QJsonObject> criticalEventMailbox_;
    qint64 criticalEventMailboxBytes_ = 0;
    bool criticalIngressTripped_ = false;
    quint64 droppedNonCriticalEvents_ = 0;
    WorkerSchedule uploadWorkerSchedule_;
    bool started_ = false;
};

} // namespace hub
