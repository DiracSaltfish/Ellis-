#include "agent/AuditWriter.h"

#include "common/JsonUtil.h"

#include <QDir>
#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QMetaObject>
#include <QMutexLocker>
#include <QSqlError>
#include <QSqlQuery>
#include <QThread>

#include <utility>

#ifdef Q_OS_UNIX
#include <unistd.h>
#endif

namespace hub {

namespace {
constexpr qint64 MaximumCriticalEventBytes = 4LL * 1024 * 1024;
constexpr qint64 MaximumEmergencyBytes = 256LL * 1024 * 1024;
constexpr qsizetype MaximumQueuedCriticalEvents = 4096;
constexpr qint64 MaximumQueuedCriticalBytes = 32LL * 1024 * 1024;
}

AuditWriter::AuditWriter(QString databasePath, QObject *parent,
                         int simulatedCriticalIoDelayMs)
    : QObject(parent), databasePath_(std::move(databasePath)),
      emergencyPath_(databasePath_ + QStringLiteral(".critical-emergency.jsonl")),
      connectionName_(QStringLiteral("hub-audit-%1").arg(reinterpret_cast<quintptr>(this))),
      simulatedCriticalIoDelayMs_(qMax(0, simulatedCriticalIoDelayMs)) {}

AuditWriter::~AuditWriter() {
    close();
}

void AuditWriter::initialize() {
    const QFileInfo info(databasePath_);
    QDir directory(info.absolutePath());
    if (!directory.exists() && !directory.mkpath(QStringLiteral("."))) {
        const QString message = QStringLiteral("无法创建审计目录：%1").arg(directory.absolutePath());
        emit failed(message);
        emit readyChanged(false, message);
        return;
    }
    QFile::setPermissions(directory.absolutePath(), QFileDevice::ReadOwner | QFileDevice::WriteOwner |
                                                   QFileDevice::ExeOwner);

    database_ = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connectionName_);
    database_.setDatabaseName(databasePath_);
    if (!database_.open()) {
        const QString message = QStringLiteral("无法打开审计数据库：%1").arg(database_.lastError().text());
        emit failed(message);
        emit readyChanged(false, message);
        return;
    }
    QSqlQuery query(database_);
    if (!query.exec(QStringLiteral("PRAGMA journal_mode=WAL")) ||
        !query.exec(QStringLiteral("PRAGMA synchronous=FULL")) ||
        !query.exec(QStringLiteral("PRAGMA busy_timeout=5000")) ||
        !query.exec(QStringLiteral("PRAGMA wal_autocheckpoint=1000")) ||
        !query.exec(QStringLiteral("PRAGMA journal_size_limit=67108864")) ||
        !query.exec(QStringLiteral(
            "CREATE TABLE IF NOT EXISTS audit_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, module_id TEXT, "
            "event_type TEXT NOT NULL, command_id TEXT, source_event_id TEXT, "
            "payload_json TEXT NOT NULL)")) ||
        !query.exec(QStringLiteral(
            "CREATE INDEX IF NOT EXISTS audit_events_timestamp_idx ON audit_events(timestamp)")) ||
        !query.exec(QStringLiteral(
            "CREATE INDEX IF NOT EXISTS audit_events_type_id_idx ON audit_events(event_type,id)"))) {
        const QString message = QStringLiteral("初始化审计数据库失败：%1").arg(query.lastError().text());
        emit failed(message);
        emit readyChanged(false, message);
        database_.close();
        return;
    }
    bool hasSourceEventId = false;
    QSqlQuery columns(database_);
    if (!columns.exec(QStringLiteral("PRAGMA table_info(audit_events)"))) {
        const QString message = QStringLiteral("无法检查审计库结构：%1")
                                    .arg(columns.lastError().text());
        emit failed(message);
        emit readyChanged(false, message);
        database_.close();
        return;
    }
    while (columns.next()) {
        if (columns.value(1).toString() == QStringLiteral("source_event_id")) {
            hasSourceEventId = true;
            break;
        }
    }
    if ((!hasSourceEventId
         && !query.exec(QStringLiteral(
             "ALTER TABLE audit_events ADD COLUMN source_event_id TEXT")))
        || !query.exec(QStringLiteral(
            "CREATE UNIQUE INDEX IF NOT EXISTS audit_events_source_event_id_uidx "
            "ON audit_events(source_event_id) "
            "WHERE source_event_id IS NOT NULL AND source_event_id<>''"))) {
        const QString message = QStringLiteral("无法升级审计库幂等结构：%1")
                                    .arg(query.lastError().text());
        emit failed(message);
        emit readyChanged(false, message);
        database_.close();
        return;
    }
    QSqlQuery pageSizeQuery(database_);
    if (!pageSizeQuery.exec(QStringLiteral("PRAGMA page_size")) || !pageSizeQuery.next()
        || pageSizeQuery.value(0).toLongLong() <= 0) {
        const QString message = QStringLiteral("无法读取审计库 page_size");
        emit failed(message);
        emit readyChanged(false, message);
        database_.close();
        return;
    }
    constexpr qint64 MaximumDatabaseBytes = 1024LL * 1024 * 1024;
    const qint64 maximumPages = qMax<qint64>(1024,
        MaximumDatabaseBytes / pageSizeQuery.value(0).toLongLong());
    if (!query.exec(QStringLiteral("PRAGMA max_page_count=%1").arg(maximumPages))
        || !query.next() || query.value(0).toLongLong() > maximumPages) {
        const QString message = QStringLiteral("无法设置审计库 1 GiB 硬配额：%1")
                                    .arg(query.lastError().text());
        emit failed(message);
        emit readyChanged(false, message);
        database_.close();
        return;
    }
    QFile::setPermissions(databasePath_, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    const QFileInfo emergencyInfo(emergencyPath_);
    if (emergencyInfo.exists()) {
        if (!emergencyInfo.isFile() || emergencyInfo.isSymLink()
            || emergencyInfo.size() <= 0
            || emergencyInfo.size() > MaximumEmergencyBytes
#ifdef Q_OS_UNIX
            || emergencyInfo.ownerId() != static_cast<uint>(::geteuid())
#endif
        ) {
            const QString message = QStringLiteral("关键事件应急日志类型/属主/容量不安全，已拒绝启用审计");
            emit failed(message);
            emit readyChanged(false, message);
            database_.close();
            return;
        }
        QFile::setPermissions(emergencyPath_,
                              QFileDevice::ReadOwner | QFileDevice::WriteOwner);
        QMutexLocker locker(&criticalMutex_);
        emergencyActive_ = true;
        emergencyReadOffset_ = 0;
    }
    ready_ = true;
    emit readyChanged(true, QStringLiteral("审计日志已就绪"));
    if (emergencyActive_) {
        QMutexLocker locker(&criticalMutex_);
        criticalDrainScheduled_ = true;
        QMetaObject::invokeMethod(this, &AuditWriter::drainCriticalQueue,
                                  Qt::QueuedConnection);
    }
}

void AuditWriter::record(const QJsonObject &event) {
    if (!ready_) {
        emit failed(QStringLiteral("审计日志未就绪，事件未写入"));
        emit readyChanged(false, QStringLiteral("审计日志未就绪"));
        return;
    }
    const QJsonObject safe = redacted(event);
    persist(safe, nullptr);
}

void AuditWriter::recordCritical(const QJsonObject &event) {
    QString error;
    if (!enqueueCritical(event, &error)) {
        emit criticalRecordFailed(redacted(event), error);
    }
}

bool AuditWriter::enqueueCriticalAsync(const QJsonObject &event, QString *error) {
    const QJsonObject safe = redacted(event);
    const qint64 bytes = QJsonDocument(safe).toJson(QJsonDocument::Compact).size();
    {
        QMutexLocker locker(&criticalMutex_);
        if (!criticalAccepting_) {
            if (error) *error = QStringLiteral("审计写入器正在关闭");
            return false;
        }
        if (bytes <= 0 || bytes > MaximumCriticalEventBytes) {
            if (error) *error = QStringLiteral("关键事件本身超过 4 MiB 安全上限");
            return false;
        }
        if (criticalQueuedEvents_ >= MaximumQueuedCriticalEvents
            || bytes > MaximumQueuedCriticalBytes - criticalQueuedBytes_) {
            if (error) {
                *error = QStringLiteral("关键审计异步队列已达 4096 条/32 MiB 上限，已熔断控制");
            }
            return false;
        }
        ++criticalQueuedEvents_;
        criticalQueuedBytes_ += bytes;
    }
    const bool scheduled = QMetaObject::invokeMethod(
        this,
        [this, safe, bytes] {
            QString appendError;
            if (!enqueueCritical(safe, &appendError)) {
                emit criticalRecordFailed(safe, appendError);
            }
            QMutexLocker locker(&criticalMutex_);
            criticalQueuedEvents_ = qMax<qsizetype>(0, criticalQueuedEvents_ - 1);
            criticalQueuedBytes_ = qMax<qint64>(0, criticalQueuedBytes_ - bytes);
        },
        Qt::QueuedConnection);
    if (!scheduled) {
        QMutexLocker locker(&criticalMutex_);
        criticalQueuedEvents_ = qMax<qsizetype>(0, criticalQueuedEvents_ - 1);
        criticalQueuedBytes_ = qMax<qint64>(0, criticalQueuedBytes_ - bytes);
        if (error) *error = QStringLiteral("无法投递到审计 I/O 线程");
        return false;
    }
    return true;
}

bool AuditWriter::enqueueCritical(const QJsonObject &event, QString *error) {
    const QJsonObject safe = redacted(event);
    const qint64 bytes = QJsonDocument(safe).toJson(QJsonDocument::Compact).size();
    {
        QMutexLocker locker(&criticalMutex_);
        if (!criticalAccepting_) {
            if (error) *error = QStringLiteral("审计写入器正在关闭");
            return false;
        }
        if (bytes <= 0 || bytes > MaximumCriticalEventBytes) {
            if (error) {
                *error = QStringLiteral("关键事件本身超过 4 MiB 安全上限");
            }
            return false;
        }
    }
    // This synchronous primitive runs only on AuditWriter's thread. Never hold
    // the ingress-accounting mutex across filesystem I/O: HubAgent may submit
    // the next bounded item while this fsync is deliberately slow.
    if (!appendEmergencyLocked(safe, error)) return false;
    bool schedule = false;
    {
        QMutexLocker locker(&criticalMutex_);
        if (!criticalDrainScheduled_) {
            criticalDrainScheduled_ = true;
            schedule = true;
        }
    }
    if (schedule) {
        QMetaObject::invokeMethod(this, &AuditWriter::drainCriticalQueue,
                                  Qt::QueuedConnection);
    }
    return true;
}

bool AuditWriter::persistCriticalAndNotify(const QJsonObject &event) {
    const QJsonObject safe = redacted(event);
    if (!ready_) {
        const QString message = QStringLiteral("审计数据库不可用；关键事件将保留在应急日志");
        emit criticalRecordFailed(safe, message);
        return false;
    }
    QString error;
    bool inserted = false;
    const qint64 id = persist(safe, &error, &inserted);
    if (id < 0) {
        emit criticalRecordFailed(safe, error);
        return false;
    }
    // A spool record can be replayed after a crash between SQLite commit and
    // spool-offset commit. The source event id makes that retry idempotent.
    if (inserted) {
        QJsonObject recorded = safe;
        recorded.insert(QStringLiteral("audit_event_id"), id);
        emit criticalRecorded(recorded);
    }
    return true;
}

bool AuditWriter::appendEmergencyLocked(const QJsonObject &event, QString *error) {
    return appendEmergencyBatchLocked(QList<QJsonObject>{event}, error);
}

bool AuditWriter::appendEmergencyBatchLocked(const QList<QJsonObject> &events,
                                             QString *error) {
    if (events.isEmpty()) return true;
    if (simulatedCriticalIoDelayMs_ > 0) {
        // Deterministic slow-storage injection used by the isolation test. It
        // lives on the AuditWriter thread and therefore must never stall Hub.
        QThread::msleep(static_cast<unsigned long>(simulatedCriticalIoDelayMs_));
    }
    const QFileInfo existing(emergencyPath_);
    const auto exactOwnerPermissions = QFileDevice::ReadOwner | QFileDevice::WriteOwner;
    if (existing.exists()
        && (!existing.isFile() || existing.isSymLink()
            || existing.permissions() != exactOwnerPermissions
#ifdef Q_OS_UNIX
            || existing.ownerId() != static_cast<uint>(::geteuid())
#endif
        )) {
        if (error) *error = QStringLiteral("应急审计日志类型、属主或权限不安全");
        return false;
    }
    QList<QByteArray> lines;
    qint64 additional = 0;
    for (const auto &event : events) {
        QByteArray line = QJsonDocument(redacted(event)).toJson(QJsonDocument::Compact);
        if (line.isEmpty() || line.size() > MaximumCriticalEventBytes) {
            if (error) *error = QStringLiteral("应急审计事件超过 4 MiB 上限");
            return false;
        }
        line.append('\n');
        additional += line.size();
        lines.push_back(std::move(line));
    }
    const qint64 existingBytes = existing.exists() ? existing.size() : 0;
    if (existingBytes < 0 || additional > MaximumEmergencyBytes - existingBytes) {
        if (error) *error = QStringLiteral("应急审计日志达到 256 MiB 硬上限，已熔断控制");
        return false;
    }
    QFile file(emergencyPath_);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Append)
        || !QFile::setPermissions(emergencyPath_, exactOwnerPermissions)) {
        if (error) *error = QStringLiteral("无法创建权限 0600 的应急审计日志");
        return false;
    }
    for (const auto &line : lines) {
        if (file.write(line) != line.size()) {
            if (error) *error = QStringLiteral("应急审计日志写入不完整");
            return false;
        }
    }
    if (!file.flush()
#ifdef Q_OS_UNIX
        || ::fsync(file.handle()) != 0
#endif
    ) {
        if (error) *error = QStringLiteral("应急审计日志无法刷入稳定存储");
        return false;
    }
    emergencyActive_ = true;
    return true;
}

bool AuditWriter::takeEmergency(QJsonObject *event, qint64 *consumedBytes,
                                QString *error) {
    QFile file(emergencyPath_);
    if (!file.open(QIODevice::ReadOnly) || !file.seek(emergencyReadOffset_)) {
        if (error) *error = QStringLiteral("无法读取应急审计日志");
        return false;
    }
    QByteArray line = file.readLine(MaximumCriticalEventBytes + 2);
    if (line.isEmpty() && file.atEnd()) {
        if (emergencyReadOffset_ >= file.size()) {
            file.close();
            if (QFile::remove(emergencyPath_)) {
                emergencyReadOffset_ = 0;
                emergencyActive_ = false;
                return false;
            }
        }
        if (error) *error = QStringLiteral("应急审计日志状态不一致");
        return false;
    }
    if (line.size() > MaximumCriticalEventBytes + 1 || !line.endsWith('\n')) {
        if (error) *error = QStringLiteral("应急审计日志存在过长或未完整记录；文件已保留待人工核对");
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error) *error = QStringLiteral("应急审计日志存在无效 JSON；文件已保留待人工核对");
        return false;
    }
    if (event) *event = document.object();
    if (consumedBytes) *consumedBytes = line.size();
    return true;
}

void AuditWriter::commitEmergency(qint64 consumedBytes) {
    emergencyReadOffset_ += qMax<qint64>(0, consumedBytes);
    const QFileInfo info(emergencyPath_);
    if (info.exists() && emergencyReadOffset_ >= info.size()) {
        if (QFile::remove(emergencyPath_)) {
            emergencyReadOffset_ = 0;
            emergencyActive_ = false;
        }
    }
}

void AuditWriter::drainCriticalQueue() {
    int drained = 0;
    while (drained < 256) {
        QJsonObject event;
        qint64 emergencyBytes = 0;
        {
            QMutexLocker locker(&criticalMutex_);
            if (!emergencyActive_) {
                criticalDrainScheduled_ = false;
                return;
            }
        }
        QString error;
        if (!takeEmergency(&event, &emergencyBytes, &error)) {
            if (!error.isEmpty()) {
                ready_ = false;
                emit failed(error);
                emit readyChanged(false, error);
            }
            QMutexLocker locker(&criticalMutex_);
            criticalDrainScheduled_ = false;
            return;
        }
        if (!persistCriticalAndNotify(event)) {
            QMutexLocker locker(&criticalMutex_);
            criticalDrainScheduled_ = false;
            return;
        }
        commitEmergency(emergencyBytes);
        ++drained;
    }
    QMetaObject::invokeMethod(this, &AuditWriter::drainCriticalQueue,
                              Qt::QueuedConnection);
}

qint64 AuditWriter::persist(const QJsonObject &safe, QString *error,
                            bool *inserted) {
    if (inserted) *inserted = false;
    QString lastError;
    for (int attempt = 0; attempt < 3; ++attempt) {
        QSqlQuery query(database_);
        query.prepare(QStringLiteral(
            "INSERT OR IGNORE INTO audit_events(timestamp,module_id,event_type,command_id,"
            "source_event_id,payload_json) VALUES(?,?,?,?,?,?)"));
        query.addBindValue(safe.value(QStringLiteral("timestamp")).toString(utcNow()));
        query.addBindValue(safe.value(QStringLiteral("module_id")).toString());
        query.addBindValue(safe.value(QStringLiteral("type")).toString(QStringLiteral("event")));
        query.addBindValue(safe.value(QStringLiteral("command_id")).toString());
        const QString sourceEventId = safe.value(QStringLiteral("event_id")).toString();
        query.addBindValue(sourceEventId.isEmpty() ? QVariant{} : QVariant{sourceEventId});
        query.addBindValue(QString::fromUtf8(QJsonDocument(safe).toJson(QJsonDocument::Compact)));
        if (query.exec()) {
            qint64 id = -1;
            const bool wasInserted = query.numRowsAffected() > 0;
            if (wasInserted) {
                id = query.lastInsertId().toLongLong();
            } else if (!sourceEventId.isEmpty()) {
                QSqlQuery lookup(database_);
                lookup.prepare(QStringLiteral(
                    "SELECT id FROM audit_events WHERE source_event_id=?"));
                lookup.addBindValue(sourceEventId);
                if (lookup.exec() && lookup.next()) id = lookup.value(0).toLongLong();
            }
            if (id < 0) {
                lastError = QStringLiteral("审计幂等写入未返回稳定记录 ID");
                break;
            }
            if (inserted) *inserted = wasInserted;
            if (++writesSincePrune_ >= 256) {
                writesSincePrune_ = 0;
                pruneRetention();
            }
            return id;
        }
        lastError = query.lastError().text();
        const QString native = query.lastError().nativeErrorCode();
        if (native != QStringLiteral("5") && native != QStringLiteral("6")) break;
        QThread::msleep(static_cast<unsigned long>(25 * (attempt + 1)));
    }
    ready_ = false;
    const QString message = QStringLiteral("写入审计事件失败（已有界重试）：%1").arg(lastError);
    if (error) *error = message;
    emit failed(message);
    emit readyChanged(false, message);
    return -1;
}

void AuditWriter::pruneRetention() {
    const QString cutoff = QDateTime::currentDateTimeUtc().addDays(-90)
                               .toString(Qt::ISODateWithMs);
    QSqlQuery old(database_);
    old.prepare(QStringLiteral(
        "DELETE FROM audit_events WHERE id IN (SELECT id FROM audit_events "
        "WHERE timestamp<? ORDER BY id ASC LIMIT 5000)"));
    old.addBindValue(cutoff);
    old.exec();

    QSqlQuery threshold(database_);
    if (threshold.exec(QStringLiteral(
            "SELECT id FROM audit_events ORDER BY id DESC LIMIT 1 OFFSET 99999"))
        && threshold.next()) {
        QSqlQuery excess(database_);
        excess.prepare(QStringLiteral(
            "DELETE FROM audit_events WHERE id IN (SELECT id FROM audit_events "
            "WHERE id<? ORDER BY id ASC LIMIT 5000)"));
        excess.addBindValue(threshold.value(0).toLongLong());
        excess.exec();
    }

    QSqlQuery pageSize(database_);
    QSqlQuery pageCount(database_);
    QSqlQuery freeCount(database_);
    if (!pageSize.exec(QStringLiteral("PRAGMA page_size")) || !pageSize.next()
        || !pageCount.exec(QStringLiteral("PRAGMA page_count")) || !pageCount.next()
        || !freeCount.exec(QStringLiteral("PRAGMA freelist_count")) || !freeCount.next()) return;
    const qint64 usedBytes = qMax<qint64>(0, pageCount.value(0).toLongLong()
                                               - freeCount.value(0).toLongLong())
        * pageSize.value(0).toLongLong();
    if (usedBytes <= 768LL * 1024 * 1024) return;
    QSqlQuery pressure(database_);
    pressure.exec(QStringLiteral(
        "DELETE FROM audit_events WHERE id IN (SELECT id FROM audit_events "
        "ORDER BY id ASC LIMIT 20000)"));
}

void AuditWriter::close() {
    {
        QMutexLocker locker(&criticalMutex_);
        criticalAccepting_ = false;
        criticalDrainScheduled_ = false;
    }
    // Every event whose criticalRecorded signal was emitted was first fsync'd.
    // Leave any unconsumed FIFO intact; startup replays it idempotently before
    // control is enabled.
    const bool wasReady = ready_;
    ready_ = false;
    if (database_.isValid()) {
        database_.close();
        database_ = {};
        QSqlDatabase::removeDatabase(connectionName_);
    }
    if (wasReady) emit readyChanged(false, QStringLiteral("审计日志已关闭"));
}

} // namespace hub
