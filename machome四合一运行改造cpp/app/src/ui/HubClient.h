#pragma once

#include <QHash>
#include <QJsonObject>
#include <QLocalSocket>
#include <QObject>
#include <QQueue>
#include <QSet>
#include <QTimer>

namespace hub {

class HubClient final : public QObject {
    Q_OBJECT
public:
    explicit HubClient(QString socketPath, QString configPath, QString agentProgram,
                       int frameLimitBytes, QString expectedConfigHash,
                       QObject *parent = nullptr);

public slots:
    void start();
    void stop();
    void refresh(const QString &moduleId = {});
    void sendCommand(const QString &moduleId, const QString &action,
                     const QJsonObject &arguments = {}, int deadlineMs = 45000,
                     qint64 expectedRevision = -1,
                     const QString &reason = QStringLiteral("interactive_ui"));
    void acknowledgeCriticalEvent(qint64 auditEventId, const QString &auditEpoch,
                                  quint64 deliveryGeneration);

signals:
    void connectionChanged(bool connected, const QString &description);
    void helloReceived(const QJsonObject &message);
    void snapshotReceived(const QJsonObject &message);
    void eventReceived(const QJsonObject &message);
    void commandReceived(const QJsonObject &message);
    void protocolError(const QString &message);
    void bindingChanged(bool controlReady, const QString &description);

private slots:
    void connectNow();
    void onConnected();
    void onDisconnected();
    void onReadyRead();
    void onError(QLocalSocket::LocalSocketError error);
    void flushDelivery();

private:
    void write(const QJsonObject &message);
    void scheduleReconnect();
    void maybeStartAgent();
    void queueSnapshot(const QJsonObject &message);
    void queueEvent(const QJsonObject &message);
    void persistCriticalCursor();
    QJsonObject commandEnvelope(const QString &moduleId, const QString &action,
                                const QJsonObject &arguments, int deadlineMs,
                                qint64 expectedRevision, const QString &reason) const;

    QString socketPath_;
    QString configPath_;
    QString agentProgram_;
    int frameLimitBytes_;
    QString expectedConfigHash_;
    QString agentInstanceId_;
    QString clientInstanceId_;
    QLocalSocket socket_;
    QTimer reconnectTimer_;
    QTimer deliveryTimer_;
    QByteArray readBuffer_;
    QHash<QString, QJsonObject> pendingSnapshots_;
    QHash<QString, QJsonObject> coalescedEvents_;
    QQueue<QJsonObject> criticalEvents_;
    QQueue<QJsonObject> queuedEvents_;
    QHash<QString, QJsonObject> pendingApprovals_;
    QHash<QString, QSet<QString>> approvalActions_;
    int reconnectAttempt_ = 0;
    bool stopping_ = false;
    bool agentStartAttempted_ = false;
    bool configBound_ = false;
    bool controlReady_ = false;
    bool helloComplete_ = false;
    qint64 lastCriticalEventId_ = 0;
    qint64 highestCriticalQueuedId_ = 0;
    QString lastCriticalEventEpoch_;
    QString auditEpoch_;
    quint64 connectionGeneration_ = 0;
    QSet<qint64> pendingCriticalEventIds_;
    bool overflowWarningQueued_ = false;
};

} // namespace hub
