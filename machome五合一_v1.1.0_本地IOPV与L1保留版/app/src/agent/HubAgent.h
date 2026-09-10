#pragma once

#include "common/Config.h"

#include <QHash>
#include <QJsonObject>
#include <QLocalServer>
#include <QLocalSocket>
#include <QObject>
#include <QPointer>
#include <QQueue>
#include <QSet>
#include <QThread>

namespace hub {

class AuditWriter;
class CommandJournal;
class ModuleWorker;

class HubAgent final : public QObject {
    Q_OBJECT
public:
    explicit HubAgent(AppConfig config, QObject *parent = nullptr);
    ~HubAgent() override;

    bool start(QString *error = nullptr);
    void shutdown();

private slots:
    void acceptClients();
    void clientReadyRead();
    void clientDisconnected();
    void onSnapshot(const QJsonObject &message);
    void onEvent(const QJsonObject &message);
    void onCommandResult(const QJsonObject &message);
    void onAuditReadyChanged(bool ready, const QString &message);
    void onCriticalRecorded(const QJsonObject &event);
    void onCriticalRecordFailed(const QJsonObject &event, const QString &message);

signals:
    void audit(const QJsonObject &event);

private:
    struct ClientState {
        QByteArray buffer;
        bool helloComplete = false;
        bool configBound = false;
        QString clientInstanceId;
        QQueue<QJsonObject> replayQueue;
        qint64 replayQueueBytes = 0;
        qint64 lastCriticalAck = 0;
        qint64 criticalReplayQueuedThrough = 0;
        bool criticalReplayActive = false;
    };

    struct ApprovalTicket {
        QPointer<QLocalSocket> socket;
        QByteArray fingerprint;
        qint64 expiresAtMs = 0;
    };

    void processClientMessage(QLocalSocket *socket, const QJsonObject &message);
    void drainClientReplay(QLocalSocket *socket);
    void enqueueOrdered(QLocalSocket *socket, const QJsonObject &message);
    void enqueueCriticalReplay(QLocalSocket *socket, qint64 afterId);
    void send(QLocalSocket *socket, const QJsonObject &message);
    void broadcast(const QJsonObject &message);
    void cacheCommandResult(const QString &commandId, const QByteArray &fingerprint,
                            const QJsonObject &result);
    QJsonObject boundedMessage(const QJsonObject &message) const;
    QJsonObject replayedResult(const QJsonObject &result) const;
    bool durableLookup(const QString &commandId, QByteArray *fingerprint,
                       QJsonObject *result, bool *found);
    bool persistNewCommand(const QString &commandId, const QByteArray &fingerprint,
                           const QJsonObject &request, const QJsonObject &result,
                           QLocalSocket *socket);
    bool actionAllowed(const QString &moduleId, const QString &action) const;
    bool approvalRequired(const QString &moduleId, const QString &action) const;
    bool validateBinding(QLocalSocket *socket, const QJsonObject &message,
                         const QString &commandId = {});
    void processApprovalRequest(QLocalSocket *socket, const QJsonObject &message);
    bool consumeApproval(QLocalSocket *socket, const QJsonObject &message,
                         QString *error);
    void reject(QLocalSocket *socket, const QString &code, const QString &message,
                const QString &commandId = {});
    bool sameUid(QLocalSocket *socket) const;
    QJsonObject helloMessage() const;

    AppConfig config_;
    QLocalServer server_;
    QHash<QLocalSocket *, ClientState> clients_;
    QHash<QString, QJsonObject> snapshots_;
    QHash<QString, qint64> moduleControlRevisions_;
    QHash<QString, QSet<QString>> activeCommandIds_;
    QHash<QString, ModuleWorker *> workers_;
    QList<QThread *> workerThreads_;
    QHash<QString, QJsonObject> commandResults_;
    QHash<QString, QByteArray> commandFingerprints_;
    // Heap allocated without a QObject parent so an OS-level I/O stall during
    // final shutdown can be leaked until process exit instead of invoking the
    // unsafe QThread::terminate() fallback.
    QThread *auditThread_ = nullptr;
    AuditWriter *auditWriter_ = nullptr;
    CommandJournal *commandJournal_ = nullptr;
    QSet<QString> unresolvedModules_;
    QHash<QString, ApprovalTicket> approvalTickets_;
    QString instanceId_;
    QString artifactHash_;
    QString auditEpoch_;
    qint64 criticalEventFloor_ = 0;
    qint64 criticalEventHighWater_ = 0;
    bool started_ = false;
    bool auditHealthy_ = false;
    bool commandJournalHealthy_ = false;
};

} // namespace hub
