#pragma once

#include "common/ModuleEngine.h"
#include "modules/redemption/RedemptionCore.h"

#include <QDateTime>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QPointer>
#include <QProcess>
#include <QSqlDatabase>
#include <QTcpServer>
#include <QTimer>

class QNetworkReply;
class QTcpSocket;
class QWebSocket;
class QWebSocketServer;

namespace machome::qmt { class QmtClient; }

namespace machome::redemption {

class RedemptionEngine final : public hub::IModuleEngine {
    Q_OBJECT
public:
    explicit RedemptionEngine(QObject *parent = nullptr);
    ~RedemptionEngine() override;

    QJsonObject snapshot() const;
    QString databasePath() const;
    quint16 compatibilityPortForTest() const;
    bool ingestCaptureForTest(const QJsonObject &capture, QString *error = nullptr);
    bool installPcfForTest(const QString &symbol, const QJsonObject &pcf,
                           QString *error = nullptr);
    QJsonObject parsePcfForTest(const QByteArray &body, const QString &symbol,
                                QString *error = nullptr) const;
    void setNowForTest(const QDateTime &utcNow);
    void evaluateScheduleForTest();

public slots:
    void initialize(const hub::ModuleContext &context) override;
    void start() override;
    void stop(hub::StopMode mode = hub::StopMode::Graceful) override;
    void submitCommand(const QString &action, const QJsonObject &arguments,
                       const QString &commandId) override;

private slots:
    void evaluateSchedule();
    void readHelperOutput();
    void helperFinished(int exitCode, QProcess::ExitStatus status);
    void helperError(QProcess::ProcessError error);
    void acceptCompatibilityConnection();
    void acceptWebSocket();
    void pcfTick();
    void pcfFinished(QNetworkReply *reply);

private:
    struct SymbolState {
        QString windcode;
        QString customName;
        QString status = QStringLiteral("waiting");
        QJsonObject values;
        QJsonObject baseline;
        QJsonObject previousValues;
        QJsonArray lastChange;
        QJsonObject pcf;
        QJsonObject opportunity;
        QDateTime updatedAt;
        QDateTime lastChangeAt;
        qint64 subId = -1;
    };
    struct PcfRuntime {
        int attempts = 0;
        QDate day;
        QDateTime lastAttemptUtc;
        QDateTime cooldownUntilUtc;
        bool inFlight = false;
        int candidateIndex = 0;
        QString lastError;
    };

    QDateTime nowUtc() const;
    bool openRepository(QString *error);
    bool migrateRepository(QString *error);
    void loadRepository();
    bool persistSettings(QString *error = nullptr);
    bool persistPcf(const QString &windcode, const QJsonObject &pcf,
                    QString *error = nullptr);
    bool appendHistory(const QJsonObject &record, QString *error = nullptr);
    bool pruneHistory(const QDate &today, QString *error = nullptr);
    QJsonArray queryHistory(const QDate &day, const QString &symbol, int limit) const;
    QJsonObject symbolSnapshot(const SymbolState &state) const;
    QJsonObject healthSnapshot() const;
    QJsonObject scheduleSnapshot() const;
    void publishSnapshot();
    void emitStatus(const QString &message);
    bool ingestCapture(const QJsonObject &capture, bool replay, QString *error);
    bool installPcf(const QString &symbol, const QJsonObject &payload,
                    bool persist, QString *error);
    bool setWatchlist(const QJsonArray &symbols, QString *error);
    bool setSymbolName(const QString &symbol, const QString &name, QString *error);
    void dailyReset(const QDate &day, const QString &reason);
    bool windCollectionReady() const;
    void requestWarmup(const QString &reason, bool force = false);
    void finishWarmupSettle();
    void startMonitoring(const QString &reason);
    void stopMonitoring(const QString &reason, const QString &requestId = {});
    void startWindHelper();
    void stopWindHelper();
    void sendHelperCommand(const QString &action, const QJsonObject &arguments = {});
    void handleHelperMessage(const QJsonObject &message);
    void initializeQmt();
    void stopQmt();
    void handleQmtCommand(const QString &action, const QJsonObject &arguments,
                          const QString &commandId);
    void updateQmtSnapshot(const QString &id, const QJsonObject &snapshot);
    bool liveOrdersAllowed(QString *reason = nullptr) const;
    void startCompatibilityServer();
    void stopCompatibilityServer();
    void handleHttpRequest(QTcpSocket *socket);
    void writeHttp(QTcpSocket *socket, int status, const QJsonObject &body) const;
    void broadcastCompat(const QJsonObject &message);
    void fetchNextPcf(bool force);
    bool persistPcfGate();
    QJsonObject parsePcfResponse(const QByteArray &body, const QString &symbol,
                                 QString *error) const;
    static QString iso(const QDateTime &value);

    hub::ModuleContext context_;
    QString connectionName_;
    QSqlDatabase database_;
    QHash<QString, SymbolState> states_;
    QStringList watchlist_;
    QHash<QString, PcfRuntime> pcfRuntime_;
    QTimer scheduleTimer_;
    QTimer pcfTimer_;
    QTimer pcfGateTimer_;
    QPointer<QNetworkReply> activePcfReply_;
    QDateTime pcfNextAllowedUtc_;
    QDateTime pcfBlockedUntilUtc_;
    int pcfFailureCount_ = 0;
    bool pcfRefreshPending_ = false;
    QSet<QString> pcfForcedSymbols_;
    QNetworkAccessManager network_;
    QProcess helper_;
    QByteArray helperBuffer_;
    QTcpServer compatibilityTcp_;
    QWebSocketServer *compatibilityWs_ = nullptr;
    QList<QPointer<QWebSocket>> webSockets_;
    QHash<QString, machome::qmt::QmtClient *> qmtClients_;
    QJsonObject qmtSnapshots_;
    QHash<QString, QString> pendingQmtConnect_;
    QHash<QString, QString> pendingQmtDisconnect_;
    QHash<QString, QString> pendingQmtSync_;
    QDateTime testNowUtc_;
    QDateTime captureNotBeforeUtc_;
    QDateTime lastMonitorAttemptUtc_;
    QDateTime lastWindLaunchAttemptUtc_;
    QDateTime lastWindShutdownAttemptUtc_;
    QDateTime lastWindStatusPollUtc_;
    QDateTime lastScheduleSnapshotUtc_;
    QDateTime lastWindCleanupAtUtc_;
    QString lastSchedulePhase_;
    int windCleanupDeletedCount_ = -1;
    QDate resetDay_;
    QDate windLaunchDay_;
    QDate warmupDay_;
    QDate warmupAttemptDay_;
    QDate lastHistoryPruneDay_;
    QString state_ = QStringLiteral("created");
    QString lastError_;
    QString helperState_ = QStringLiteral("disabled");
    QString helperLastError_;
    QString pendingMonitoringReason_;
    QString operatingMode_ = QStringLiteral("work");
    bool initialized_ = false;
    bool running_ = false;
    bool monitoring_ = false;
    bool monitoringRequested_ = false;
    bool monitoringWarmupPending_ = false;
    bool warmupSettleElapsed_ = false;
    bool helperReady_ = false;
    bool windRunning_ = false;
    bool tbapiReady_ = false;
    quint64 sequence_ = 0;
};

} // namespace machome::redemption
