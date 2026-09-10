#include "agent/CommandJournal.h"

#include "common/JsonUtil.h"

#include <QDir>
#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QSqlError>
#include <QSqlQuery>
#include <QSet>
#include <QThread>
#include <QUuid>

namespace hub {
namespace {

QString jsonText(const QJsonObject &object) {
    return QString::fromUtf8(QJsonDocument(redacted(object)).toJson(QJsonDocument::Compact));
}

bool terminalCertain(const QString &state, const QJsonObject &result) {
    if (result.value(QStringLiteral("details")).toObject()
            .value(QStringLiteral("outcome_uncertain")).toBool(false)) {
        return false;
    }
    return state == QStringLiteral("succeeded") || state == QStringLiteral("failed");
}

bool stateChangingAction(const QString &action) {
    static const QSet<QString> readOnly{
        QStringLiteral("refresh"),
        QStringLiteral("premium_sync"), QStringLiteral("premium_raw_snapshot"),
        QStringLiteral("premium_detail_subscribe"),
        QStringLiteral("premium_detail_unsubscribe"),
        QStringLiteral("webull_get_book"), QStringLiteral("redemption_get_history"),
        QStringLiteral("redemption_get_pcf")};
    return !readOnly.contains(action);
}

} // namespace

CommandJournal::CommandJournal()
    : connectionName_(QStringLiteral("hub-command-journal-%1")
                          .arg(QUuid::createUuid().toString(QUuid::WithoutBraces))) {}

CommandJournal::~CommandJournal() {
    close();
}

QString CommandJournal::sqlError(const QString &prefix, const QString &detail) {
    return QStringLiteral("%1：%2").arg(prefix, detail);
}

QJsonObject CommandJournal::parseObject(const QString &text) {
    QJsonParseError error;
    const auto document = QJsonDocument::fromJson(text.toUtf8(), &error);
    return error.error == QJsonParseError::NoError && document.isObject()
               ? document.object() : QJsonObject{};
}

bool CommandJournal::initialize(const QString &databasePath, QString *error) {
    if (ready_) return true;
    const QFileInfo info(databasePath);
    QDir directory(info.absolutePath());
    if (!directory.exists() && !directory.mkpath(QStringLiteral("."))) {
        if (error) *error = QStringLiteral("无法创建命令账本目录：%1").arg(directory.absolutePath());
        return false;
    }
    QFile::setPermissions(directory.absolutePath(), QFileDevice::ReadOwner | QFileDevice::WriteOwner |
                                                   QFileDevice::ExeOwner);
    database_ = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connectionName_);
    database_.setDatabaseName(databasePath);
    // This connection is used by the Agent control loop. Fail closed quickly
    // under unexpected lock contention instead of freezing all IPC/modules.
    database_.setConnectOptions(QStringLiteral("QSQLITE_BUSY_TIMEOUT=100"));
    if (!database_.open()) {
        if (error) *error = sqlError(QStringLiteral("无法打开命令账本"), database_.lastError().text());
        close();
        return false;
    }
    QSqlQuery query(database_);
    const QStringList statements{
        QStringLiteral("PRAGMA journal_mode=WAL"),
        QStringLiteral("PRAGMA synchronous=FULL"),
        QStringLiteral("PRAGMA busy_timeout=100"),
        QStringLiteral("PRAGMA wal_autocheckpoint=1000"),
        QStringLiteral("PRAGMA journal_size_limit=67108864"),
        QStringLiteral(
            "CREATE TABLE IF NOT EXISTS audit_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, module_id TEXT, "
            "event_type TEXT NOT NULL, command_id TEXT, payload_json TEXT NOT NULL)"),
        QStringLiteral(
            "CREATE INDEX IF NOT EXISTS audit_events_timestamp_idx ON audit_events(timestamp)"),
        QStringLiteral(
            "CREATE INDEX IF NOT EXISTS audit_events_type_id_idx ON audit_events(event_type,id)"),
        QStringLiteral(
            "CREATE TABLE IF NOT EXISTS hub_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"),
        QStringLiteral(
            "CREATE TABLE IF NOT EXISTS command_journal ("
            "command_id TEXT PRIMARY KEY, fingerprint BLOB NOT NULL, module_id TEXT NOT NULL, "
            "action TEXT NOT NULL, state TEXT NOT NULL, request_json TEXT NOT NULL, "
            "result_json TEXT NOT NULL, unresolved INTEGER NOT NULL DEFAULT 0, "
            "resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"),
        QStringLiteral(
            "CREATE INDEX IF NOT EXISTS command_journal_updated_idx "
            "ON command_journal(updated_at DESC)"),
        QStringLiteral(
            "CREATE INDEX IF NOT EXISTS command_journal_module_unresolved_idx "
            "ON command_journal(module_id, unresolved, resolved)")};
    for (const auto &statement : statements) {
        if (!query.exec(statement)) {
            if (error) *error = sqlError(QStringLiteral("初始化命令账本失败"), query.lastError().text());
            close();
            return false;
        }
    }
    QSqlQuery pageSizeQuery(database_);
    if (!pageSizeQuery.exec(QStringLiteral("PRAGMA page_size")) || !pageSizeQuery.next()
        || pageSizeQuery.value(0).toLongLong() <= 0) {
        if (error) *error = QStringLiteral("无法读取审计库 page_size");
        close();
        return false;
    }
    constexpr qint64 MaximumDatabaseBytes = 1024LL * 1024 * 1024;
    const qint64 maximumPages = qMax<qint64>(1024,
        MaximumDatabaseBytes / pageSizeQuery.value(0).toLongLong());
    if (!query.exec(QStringLiteral("PRAGMA max_page_count=%1").arg(maximumPages))
        || !query.next() || query.value(0).toLongLong() > maximumPages) {
        if (error) *error = sqlError(QStringLiteral("无法设置审计库 1 GiB 硬配额"),
                                     query.lastError().text());
        close();
        return false;
    }
    query.prepare(QStringLiteral("SELECT value FROM hub_metadata WHERE key='audit_epoch'"));
    if (!query.exec()) {
        if (error) *error = sqlError(QStringLiteral("读取审计世代失败"), query.lastError().text());
        close();
        return false;
    }
    if (query.next()) auditEpoch_ = query.value(0).toString();
    if (auditEpoch_.isEmpty()) {
        auditEpoch_ = QUuid::createUuid().toString(QUuid::WithoutBraces);
        QSqlQuery insert(database_);
        insert.prepare(QStringLiteral(
            "INSERT OR IGNORE INTO hub_metadata(key,value) VALUES('audit_epoch',?)"));
        insert.addBindValue(auditEpoch_);
        if (!insert.exec()) {
            if (error) *error = sqlError(QStringLiteral("创建审计世代失败"), insert.lastError().text());
            close();
            return false;
        }
        QSqlQuery reread(database_);
        if (!reread.exec(QStringLiteral(
                "SELECT value FROM hub_metadata WHERE key='audit_epoch'"))
            || !reread.next() || reread.value(0).toString().isEmpty()) {
            if (error) *error = QStringLiteral("审计世代未能持久化");
            close();
            return false;
        }
        auditEpoch_ = reread.value(0).toString();
    }
    QFile::setPermissions(databasePath, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    ready_ = true;
    return true;
}

void CommandJournal::close() {
    ready_ = false;
    auditEpoch_.clear();
    if (!database_.isValid()) return;
    database_.close();
    database_ = {};
    QSqlDatabase::removeDatabase(connectionName_);
}

bool CommandJournal::beginImmediate(QString *error) {
    QSqlQuery query(database_);
    if (query.exec(QStringLiteral("BEGIN IMMEDIATE"))) return true;
    if (error) *error = sqlError(QStringLiteral("无法锁定命令账本"), query.lastError().text());
    return false;
}

bool CommandJournal::commit(QString *error) {
    QSqlQuery query(database_);
    if (query.exec(QStringLiteral("COMMIT"))) return true;
    if (error) *error = sqlError(QStringLiteral("提交命令账本失败"), query.lastError().text());
    return false;
}

void CommandJournal::rollback() {
    QSqlQuery(database_).exec(QStringLiteral("ROLLBACK"));
}

bool CommandJournal::lookup(const QString &commandId, Entry *entry, QString *error) {
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return false;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "SELECT command_id,fingerprint,module_id,action,state,request_json,result_json,unresolved,resolved "
        "FROM command_journal WHERE command_id=?"));
    query.addBindValue(commandId);
    if (!query.exec()) {
        if (error) *error = sqlError(QStringLiteral("读取命令账本失败"), query.lastError().text());
        return false;
    }
    if (!query.next()) {
        if (entry) *entry = {};
        return true;
    }
    if (entry) {
        entry->commandId = query.value(0).toString();
        entry->fingerprint = query.value(1).toByteArray();
        entry->moduleId = query.value(2).toString();
        entry->action = query.value(3).toString();
        entry->state = query.value(4).toString();
        entry->request = parseObject(query.value(5).toString());
        entry->result = parseObject(query.value(6).toString());
        entry->unresolved = query.value(7).toBool();
        entry->resolved = query.value(8).toBool();
    }
    return true;
}

bool CommandJournal::reserve(const QString &commandId, const QByteArray &fingerprint,
                             const QJsonObject &request, const QJsonObject &result,
                             QString *error) {
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return false;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "INSERT INTO command_journal(command_id,fingerprint,module_id,action,state,request_json,"
        "result_json,unresolved,resolved,created_at,updated_at) VALUES(?,?,?,?,?,?,?,0,0,?,?)"));
    const QString now = utcNow();
    query.addBindValue(commandId);
    query.addBindValue(fingerprint);
    query.addBindValue(request.value(QStringLiteral("module_id")).toString());
    query.addBindValue(request.value(QStringLiteral("action")).toString());
    query.addBindValue(result.value(QStringLiteral("state")).toString());
    query.addBindValue(jsonText(request));
    query.addBindValue(jsonText(result));
    query.addBindValue(now);
    query.addBindValue(now);
    if (query.exec()) return true;
    if (error) *error = sqlError(QStringLiteral("持久化命令失败"), query.lastError().text());
    return false;
}

bool CommandJournal::updateResult(const QString &commandId, const QJsonObject &result,
                                  bool unresolved, QString *error) {
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return false;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "UPDATE command_journal SET state=?,result_json=?,unresolved=?,resolved=CASE WHEN ? THEN 0 ELSE resolved END,"
        "updated_at=? WHERE command_id=?"));
    query.addBindValue(result.value(QStringLiteral("state")).toString());
    query.addBindValue(jsonText(result));
    query.addBindValue(unresolved ? 1 : 0);
    query.addBindValue(unresolved ? 1 : 0);
    query.addBindValue(utcNow());
    query.addBindValue(commandId);
    if (!query.exec() || query.numRowsAffected() != 1) {
        if (error) {
            *error = sqlError(QStringLiteral("更新命令终态失败"),
                              query.lastError().text().isEmpty()
                                  ? QStringLiteral("command_id 不存在") : query.lastError().text());
        }
        return false;
    }
    return true;
}

bool CommandJournal::resolveModule(const QString &moduleId, QString *error) {
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return false;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "UPDATE command_journal SET resolved=1,updated_at=? "
        "WHERE module_id=? AND unresolved=1 AND resolved=0"));
    query.addBindValue(utcNow());
    query.addBindValue(moduleId);
    if (query.exec()) return true;
    if (error) *error = sqlError(QStringLiteral("解除未知终态锁失败"), query.lastError().text());
    return false;
}

bool CommandJournal::hasUnresolved(const QString &moduleId, bool *value, QString *error) {
    if (value) *value = true;
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return false;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "SELECT 1 FROM command_journal WHERE module_id=? AND unresolved=1 AND resolved=0 LIMIT 1"));
    query.addBindValue(moduleId);
    if (!query.exec()) {
        if (error) *error = sqlError(QStringLiteral("读取未知终态锁失败"), query.lastError().text());
        return false;
    }
    if (value) *value = query.next();
    return true;
}

QList<CommandJournal::Entry> CommandJournal::recent(int limit, qint64 maximumBytes,
                                                    QString *error) {
    QList<Entry> result;
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return result;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "SELECT command_id,fingerprint,module_id,action,state,request_json,result_json,unresolved,resolved "
        "FROM command_journal ORDER BY updated_at DESC LIMIT ?"));
    query.addBindValue(qBound(1, limit, 2000));
    if (!query.exec()) {
        if (error) *error = sqlError(QStringLiteral("读取最近命令失败"), query.lastError().text());
        return result;
    }
    qint64 retainedBytes = 0;
    const qint64 safeBudget = qBound<qint64>(qint64{64 * 1024}, maximumBytes,
                                             qint64{16 * 1024 * 1024});
    while (query.next()) {
        const QString requestText = query.value(5).toString();
        const QString resultText = query.value(6).toString();
        const qint64 entryBytes = requestText.toUtf8().size() + resultText.toUtf8().size();
        if (retainedBytes + entryBytes > safeBudget) {
            // Never let one historically oversized row create a reconnect
            // loop. Return a small terminal/state-preserving record for the
            // newest row; after that, stop at the byte budget.
            if (!result.isEmpty()) break;
            Entry entry;
            entry.commandId = query.value(0).toString();
            entry.fingerprint = query.value(1).toByteArray();
            entry.moduleId = query.value(2).toString();
            entry.action = query.value(3).toString();
            entry.state = query.value(4).toString();
            entry.result = QJsonObject{
                {QStringLiteral("schema_version"), 1},
                {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                {QStringLiteral("type"), QStringLiteral("command_result")},
                {QStringLiteral("instance_id"), QStringLiteral("journal-replay")},
                {QStringLiteral("command_id"), entry.commandId},
                {QStringLiteral("module_id"), entry.moduleId},
                {QStringLiteral("action"), entry.action},
                {QStringLiteral("state"), entry.state},
                {QStringLiteral("message"),
                 QStringLiteral("历史命令明细超过重放预算；终态已保留，请从审计库核对完整记录")},
                {QStringLiteral("timestamp"), utcNow()},
                {QStringLiteral("details"), QJsonObject{
                    {QStringLiteral("truncated"), true},
                    {QStringLiteral("original_bytes"), entryBytes},
                    {QStringLiteral("journal_replay_stub"), true}}}};
            entry.unresolved = query.value(7).toBool();
            entry.resolved = query.value(8).toBool();
            result.push_back(entry);
            break;
        }
        Entry entry;
        entry.commandId = query.value(0).toString();
        entry.fingerprint = query.value(1).toByteArray();
        entry.moduleId = query.value(2).toString();
        entry.action = query.value(3).toString();
        entry.state = query.value(4).toString();
        entry.request = parseObject(requestText);
        entry.result = parseObject(resultText);
        entry.unresolved = query.value(7).toBool();
        entry.resolved = query.value(8).toBool();
        result.push_back(entry);
        retainedBytes += entryBytes;
    }
    return result;
}

QList<QJsonObject> CommandJournal::criticalEventsAfter(qint64 afterId, int limit,
                                                       qint64 maximumBytes,
                                                       QString *error) {
    QList<QJsonObject> result;
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return result;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "SELECT id,payload_json FROM audit_events "
        "WHERE event_type='event' AND id>? ORDER BY id ASC LIMIT ?"));
    query.addBindValue(qMax<qint64>(0, afterId));
    query.addBindValue(qBound(1, limit, 2000));
    if (!query.exec()) {
        if (error) *error = sqlError(QStringLiteral("读取关键事件游标失败"), query.lastError().text());
        return result;
    }
    const qint64 safeBudget = qBound<qint64>(qint64{64 * 1024}, maximumBytes,
                                             qint64{16 * 1024 * 1024});
    qint64 retained = 0;
    while (query.next()) {
        const qint64 id = query.value(0).toLongLong();
        const QByteArray bytes = query.value(1).toString().toUtf8();
        if (!result.isEmpty() && retained + bytes.size() > safeBudget) break;
        QJsonObject event = parseObject(QString::fromUtf8(bytes));
        if (event.isEmpty()) continue;
        event.insert(QStringLiteral("audit_event_id"), id);
        event.insert(QStringLiteral("audit_epoch"), auditEpoch_);
        event.insert(QStringLiteral("audit_persisted"), true);
        event.insert(QStringLiteral("replayed"), true);
        result.append(event);
        retained += bytes.size();
    }
    return result;
}

bool CommandJournal::appendCriticalEvent(const QJsonObject &event, qint64 *eventId,
                                         QString *error) {
    if (eventId) *eventId = -1;
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本/审计库尚未就绪");
        return false;
    }
    const QJsonObject safe = redacted(event);
    QString lastError;
    for (int attempt = 0; attempt < 3; ++attempt) {
        QSqlQuery query(database_);
        query.prepare(QStringLiteral(
            "INSERT INTO audit_events(timestamp,module_id,event_type,command_id,payload_json) "
            "VALUES(?,?,?,?,?)"));
        query.addBindValue(safe.value(QStringLiteral("timestamp")).toString(utcNow()));
        query.addBindValue(safe.value(QStringLiteral("module_id")).toString());
        query.addBindValue(QStringLiteral("event"));
        query.addBindValue(safe.value(QStringLiteral("command_id")).toString());
        query.addBindValue(QString::fromUtf8(
            QJsonDocument(safe).toJson(QJsonDocument::Compact)));
        if (query.exec()) {
            const qint64 id = query.lastInsertId().toLongLong();
            if (eventId) *eventId = id;
            if (++auditWritesSincePrune_ >= 256) {
                auditWritesSincePrune_ = 0;
                pruneAuditRetention();
            }
            return id > 0;
        }
        lastError = query.lastError().text();
        const QString native = query.lastError().nativeErrorCode();
        if (native != QStringLiteral("5") && native != QStringLiteral("6")) break;
        QThread::msleep(static_cast<unsigned long>(25 * (attempt + 1)));
    }
    if (error) *error = sqlError(QStringLiteral("持久化关键事件失败"), lastError);
    return false;
}

bool CommandJournal::criticalEventBounds(qint64 *floor, qint64 *highWater,
                                         QString *error) {
    if (floor) *floor = 0;
    if (highWater) *highWater = 0;
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本/审计库尚未就绪");
        return false;
    }
    QSqlQuery query(database_);
    if (!query.exec(QStringLiteral(
            "SELECT COALESCE(MIN(id),0),COALESCE(MAX(id),0) FROM audit_events "
            "WHERE event_type='event'"))
        || !query.next()) {
        if (error) *error = sqlError(QStringLiteral("读取关键事件窗口失败"),
                                     query.lastError().text());
        return false;
    }
    if (floor) *floor = query.value(0).toLongLong();
    if (highWater) *highWater = query.value(1).toLongLong();
    return true;
}

void CommandJournal::pruneAuditRetention() {
    // A hard logical cap prevents unbounded database growth. SQLite reuses the
    // freed pages; WAL auto-checkpointing bounds the sidecar. Ninety days is a
    // second, time-based guard for low-volume installations.
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

QHash<QString, qint64> CommandJournal::maximumControlRevisions(QString *error) {
    QHash<QString, qint64> revisions;
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return revisions;
    }
    QSqlQuery query(database_);
    if (!query.exec(QStringLiteral("SELECT module_id,result_json FROM command_journal"))) {
        if (error) *error = sqlError(QStringLiteral("恢复控制版本失败"), query.lastError().text());
        return revisions;
    }
    while (query.next()) {
        const QString moduleId = query.value(0).toString();
        const qint64 revision = parseObject(query.value(1).toString())
                                    .value(QStringLiteral("control_revision")).toInteger(-1);
        if (revision >= 0) revisions[moduleId] = qMax(revisions.value(moduleId, 0), revision);
    }
    return revisions;
}

bool CommandJournal::recoverInterrupted(QSet<QString> *unresolvedModules, QString *error) {
    if (!ready_) {
        if (error) *error = QStringLiteral("命令账本尚未就绪");
        return false;
    }
    if (!beginImmediate(error)) return false;
    QSqlQuery select(database_);
    if (!select.exec(QStringLiteral(
            "SELECT command_id,module_id,action,state,result_json FROM command_journal "
            "WHERE resolved=0"))) {
        if (error) *error = sqlError(QStringLiteral("扫描中断命令失败"), select.lastError().text());
        rollback();
        return false;
    }
    struct Recovery {
        QString id;
        QString module;
        QString state;
        QJsonObject result;
        bool unresolved;
        bool resolved;
    };
    QList<Recovery> recoveries;
    while (select.next()) {
        const QString state = select.value(3).toString();
        const QString action = select.value(2).toString();
        QJsonObject result = parseObject(select.value(4).toString());
        const bool alreadyUnresolved = result.value(QStringLiteral("details")).toObject()
                                           .value(QStringLiteral("outcome_uncertain")).toBool(false);
        if (terminalCertain(state, result) && !alreadyUnresolved) continue;
        const bool mutation = stateChangingAction(action);
        QJsonObject details = result.value(QStringLiteral("details")).toObject();
        details.insert(QStringLiteral("outcome_uncertain"), mutation);
        details.insert(QStringLiteral("reconciliation_required"), mutation);
        details.insert(QStringLiteral("previous_state"), state);
        const QString recoveredState = mutation ? QStringLiteral("timed_out")
                                                : QStringLiteral("failed");
        result.insert(QStringLiteral("state"), recoveredState);
        result.insert(QStringLiteral("code"), mutation
            ? QStringLiteral("agent_restarted_outcome_unknown")
            : QStringLiteral("agent_restarted_read_only_interrupted"));
        result.insert(QStringLiteral("message"), mutation
            ? QStringLiteral("Agent 重启前未取得确定终态；已锁定本模块变更，需权威核对后人工解除")
            : QStringLiteral("Agent 重启中断了只读查询；业务状态未变，可重新查询"));
        result.insert(QStringLiteral("details"), details);
        result.insert(QStringLiteral("timestamp"), utcNow());
        recoveries.push_back({select.value(0).toString(), select.value(1).toString(),
                              recoveredState, result, mutation, !mutation});
    }
    QSqlQuery update(database_);
    update.prepare(QStringLiteral(
        "UPDATE command_journal SET state=?,result_json=?,unresolved=?,resolved=?,updated_at=? "
        "WHERE command_id=?"));
    for (const auto &recovery : recoveries) {
        update.bindValue(0, recovery.state);
        update.bindValue(1, jsonText(recovery.result));
        update.bindValue(2, recovery.unresolved ? 1 : 0);
        update.bindValue(3, recovery.resolved ? 1 : 0);
        update.bindValue(4, utcNow());
        update.bindValue(5, recovery.id);
        if (!update.exec()) {
            if (error) *error = sqlError(QStringLiteral("写入中断命令恢复状态失败"), update.lastError().text());
            rollback();
            return false;
        }
        if (unresolvedModules && recovery.unresolved) unresolvedModules->insert(recovery.module);
    }
    // Include unresolved records from an earlier start until explicitly reconciled.
    QSqlQuery unresolved(database_);
    if (!unresolved.exec(QStringLiteral(
            "SELECT DISTINCT module_id FROM command_journal WHERE unresolved=1 AND resolved=0"))) {
        if (error) *error = sqlError(QStringLiteral("读取未知终态锁失败"), unresolved.lastError().text());
        rollback();
        return false;
    }
    while (unresolved.next()) {
        if (unresolvedModules) unresolvedModules->insert(unresolved.value(0).toString());
    }
    if (!commit(error)) {
        rollback();
        return false;
    }
    return true;
}

} // namespace hub
