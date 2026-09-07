#pragma once

#include <QByteArray>
#include <QHash>
#include <QJsonObject>
#include <QList>
#include <QSet>
#include <QSqlDatabase>
#include <QString>

namespace hub {

// Synchronous, durable command ledger. Commands are rare and safety-sensitive,
// so the Agent commits their state with SQLite synchronous=FULL before dispatch.
// Module telemetry and network I/O remain on their own asynchronous threads.
class CommandJournal final {
public:
    struct Entry {
        QString commandId;
        QByteArray fingerprint;
        QString moduleId;
        QString action;
        QString state;
        QJsonObject request;
        QJsonObject result;
        bool unresolved = false;
        bool resolved = false;
    };

    CommandJournal();
    ~CommandJournal();

    bool initialize(const QString &databasePath, QString *error = nullptr);
    void close();
    bool isReady() const { return ready_; }

    bool lookup(const QString &commandId, Entry *entry, QString *error = nullptr);
    bool reserve(const QString &commandId, const QByteArray &fingerprint,
                 const QJsonObject &request, const QJsonObject &result,
                 QString *error = nullptr);
    bool updateResult(const QString &commandId, const QJsonObject &result,
                      bool unresolved, QString *error = nullptr);
    bool resolveModule(const QString &moduleId, QString *error = nullptr);
    bool hasUnresolved(const QString &moduleId, bool *value, QString *error = nullptr);
    bool recoverInterrupted(QSet<QString> *unresolvedModules,
                            QString *error = nullptr);
    QList<Entry> recent(int limit, qint64 maximumBytes, QString *error = nullptr);
    QList<QJsonObject> criticalEventsAfter(qint64 afterId, int limit,
                                           qint64 maximumBytes,
                                           QString *error = nullptr);
    bool appendCriticalEvent(const QJsonObject &event, qint64 *eventId,
                             QString *error = nullptr);
    bool criticalEventBounds(qint64 *floor, qint64 *highWater,
                             QString *error = nullptr);
    QString auditEpoch() const { return auditEpoch_; }
    QHash<QString, qint64> maximumControlRevisions(QString *error = nullptr);

private:
    bool beginImmediate(QString *error);
    bool commit(QString *error);
    void rollback();
    static QJsonObject parseObject(const QString &text);
    static QString sqlError(const QString &prefix, const QString &detail);
    void pruneAuditRetention();

    QString connectionName_;
    QSqlDatabase database_;
    QString auditEpoch_;
    quint64 auditWritesSincePrune_ = 0;
    bool ready_ = false;
};

} // namespace hub
