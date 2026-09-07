#pragma once

#include <QJsonObject>
#include <QList>
#include <QMutex>
#include <QObject>
#include <QSqlDatabase>

namespace hub {

class AuditWriter final : public QObject {
    Q_OBJECT
public:
    explicit AuditWriter(QString databasePath, QObject *parent = nullptr,
                         int simulatedCriticalIoDelayMs = 0);
    ~AuditWriter() override;
    bool isReady() const { return ready_; }
    // Thread-safe bounded asynchronous ingress. This method performs only
    // redaction/size accounting on the caller and schedules all file/SQLite
    // I/O on AuditWriter's thread. A criticalRecorded signal is emitted only
    // after the append-first spool and SQLite record are durable.
    bool enqueueCriticalAsync(const QJsonObject &event, QString *error = nullptr);
    // Synchronous durability primitive. Call only on AuditWriter's own thread
    // (or before it is moved); HubAgent intentionally never calls this method.
    bool enqueueCritical(const QJsonObject &event, QString *error = nullptr);

public slots:
    void initialize();
    void record(const QJsonObject &event);
    void recordCritical(const QJsonObject &event);
    void close();

private slots:
    void drainCriticalQueue();

signals:
    void failed(const QString &message);
    void readyChanged(bool ready, const QString &message);
    void criticalRecorded(const QJsonObject &event);
    void criticalRecordFailed(const QJsonObject &event, const QString &message);

private:
    qint64 persist(const QJsonObject &safe, QString *error,
                   bool *inserted = nullptr);
    bool persistCriticalAndNotify(const QJsonObject &event);
    bool appendEmergencyLocked(const QJsonObject &event, QString *error);
    bool appendEmergencyBatchLocked(const QList<QJsonObject> &events,
                                    QString *error);
    bool takeEmergency(QJsonObject *event, qint64 *consumedBytes, QString *error);
    void commitEmergency(qint64 consumedBytes);
    void pruneRetention();
    QString databasePath_;
    QString emergencyPath_;
    QString connectionName_;
    QSqlDatabase database_;
    quint64 writesSincePrune_ = 0;
    QMutex criticalMutex_;
    qint64 emergencyReadOffset_ = 0;
    bool emergencyActive_ = false;
    bool criticalDrainScheduled_ = false;
    bool criticalAccepting_ = true;
    qsizetype criticalQueuedEvents_ = 0;
    qint64 criticalQueuedBytes_ = 0;
    int simulatedCriticalIoDelayMs_ = 0;
    bool ready_ = false;
};

} // namespace hub
