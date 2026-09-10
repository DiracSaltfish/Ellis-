#include "modules/redemption/RedemptionEngine.h"

#include "modules/qmt/QmtClient.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QNetworkReply>
#include <QRegularExpression>
#include <QSqlError>
#include <QSqlQuery>
#include <QStandardPaths>
#include <QTcpSocket>
#include <QTimeZone>
#include <QUrl>
#include <QUrlQuery>
#include <QWebSocket>
#include <QWebSocketServer>
#include <QXmlStreamReader>
#include <algorithm>
#include <cmath>

namespace machome::redemption {
namespace {

const QStringList kProductionSymbols{
    QStringLiteral("159513.SZ"), QStringLiteral("159518.SZ"),
    QStringLiteral("159561.SZ"), QStringLiteral("159632.SZ"),
    QStringLiteral("159660.SZ"), QStringLiteral("159866.SZ"),
    QStringLiteral("159941.SZ")};

QString displaySymbol(const QString &windcode) {
    return windcode.endsWith(QStringLiteral(".SZ")) ? windcode.first(6) : windcode;
}

bool execute(QSqlDatabase &database, const QString &sql, QString *error = nullptr) {
    QSqlQuery query(database);
    if (query.exec(sql)) return true;
    if (error) *error = query.lastError().text();
    return false;
}

QString boolText(const QString &value) {
    const QString v = value.trimmed().toUpper();
    if (QStringList{QStringLiteral("Y"), QStringLiteral("YES"), QStringLiteral("TRUE"),
                    QStringLiteral("1"), QStringLiteral("OPEN")}.contains(v)) return QStringLiteral("true");
    if (QStringList{QStringLiteral("N"), QStringLiteral("NO"), QStringLiteral("FALSE"),
                    QStringLiteral("0"), QStringLiteral("CLOSED")}.contains(v)) return QStringLiteral("false");
    return {};
}

QJsonValue parsedNumber(const QString &text) {
    QString cleaned = text.trimmed();
    cleaned.remove(u',');
    bool ok = false;
    const double value = cleaned.toDouble(&ok);
    return ok && std::isfinite(value) ? QJsonValue(value) : QJsonValue();
}

} // namespace

RedemptionEngine::RedemptionEngine(QObject *parent)
    : hub::IModuleEngine(parent), compatibilityWs_(new QWebSocketServer(
          QStringLiteral("MachomeHub redemption compatibility"),
          QWebSocketServer::NonSecureMode, this)) {
    scheduleTimer_.setParent(this);
    scheduleTimer_.setInterval(200);
    connect(&scheduleTimer_, &QTimer::timeout, this, &RedemptionEngine::evaluateSchedule);
    pcfTimer_.setParent(this);
    pcfTimer_.setInterval(60'000);
    connect(&pcfTimer_, &QTimer::timeout, this, &RedemptionEngine::pcfTick);
    pcfGateTimer_.setParent(this);
    pcfGateTimer_.setSingleShot(true);
    connect(&pcfGateTimer_, &QTimer::timeout, this, [this] {
        if (running_ && pcfRefreshPending_) fetchNextPcf(false);
    });
    connect(&network_, &QNetworkAccessManager::finished,
            this, &RedemptionEngine::pcfFinished);
    connect(&helper_, &QProcess::readyReadStandardOutput,
            this, &RedemptionEngine::readHelperOutput);
    connect(&helper_, &QProcess::finished,
            this, &RedemptionEngine::helperFinished);
    connect(&helper_, &QProcess::errorOccurred,
            this, &RedemptionEngine::helperError);
    connect(&compatibilityTcp_, &QTcpServer::newConnection,
            this, &RedemptionEngine::acceptCompatibilityConnection);
    connect(compatibilityWs_, &QWebSocketServer::newConnection,
            this, &RedemptionEngine::acceptWebSocket);
}

RedemptionEngine::~RedemptionEngine() {
    stop(hub::StopMode::Immediate);
    if (database_.isValid()) database_.close();
    const QString name = connectionName_;
    database_ = {};
    if (!name.isEmpty()) QSqlDatabase::removeDatabase(name);
}

QString RedemptionEngine::databasePath() const {
    return QDir(context_.dataRoot).filePath(QStringLiteral("redemption.sqlite3"));
}

quint16 RedemptionEngine::compatibilityPortForTest() const {
    return compatibilityTcp_.serverPort();
}

QString RedemptionEngine::iso(const QDateTime &value) {
    return value.isValid() ? value.toUTC().toString(Qt::ISODateWithMs) : QString();
}

QDateTime RedemptionEngine::nowUtc() const {
    return testNowUtc_.isValid() ? testNowUtc_.toUTC() : QDateTime::currentDateTimeUtc();
}

void RedemptionEngine::setNowForTest(const QDateTime &utcNow) {
    testNowUtc_ = utcNow.toUTC();
}

bool RedemptionEngine::openRepository(QString *error) {
    QDir root(context_.dataRoot);
    if (!root.exists() && !root.mkpath(QStringLiteral("."))) {
        if (error) *error = QStringLiteral("无法创建申赎数据目录");
        return false;
    }
    QFileInfo info(root.absolutePath());
    if (info.isSymLink()) {
        if (error) *error = QStringLiteral("申赎数据目录不能是符号链接");
        return false;
    }
    QFile::setPermissions(root.absolutePath(), QFileDevice::ReadOwner
                                               | QFileDevice::WriteOwner
                                               | QFileDevice::ExeOwner);
    connectionName_ = QStringLiteral("redemption-%1").arg(
        reinterpret_cast<quintptr>(this), 0, 16);
    database_ = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connectionName_);
    database_.setDatabaseName(databasePath());
    if (!database_.open()) {
        if (error) *error = database_.lastError().text();
        return false;
    }
    QFile::setPermissions(databasePath(), QFileDevice::ReadOwner
                                          | QFileDevice::WriteOwner);
    QSqlQuery pragmas(database_);
    if (!pragmas.exec(QStringLiteral("PRAGMA busy_timeout=5000"))
        || !pragmas.exec(QStringLiteral("PRAGMA journal_mode=WAL"))
        || !pragmas.exec(QStringLiteral("PRAGMA synchronous=NORMAL"))) {
        if (error) *error = pragmas.lastError().text();
        return false;
    }
    return migrateRepository(error);
}

bool RedemptionEngine::migrateRepository(QString *error) {
    return execute(database_, QStringLiteral(
        "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);"), error)
        && execute(database_, QStringLiteral(
        "CREATE TABLE IF NOT EXISTS pcf(symbol TEXT PRIMARY KEY,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL);"), error)
        && execute(database_, QStringLiteral(
        "CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT,event_day TEXT NOT NULL,event_time TEXT NOT NULL,symbol TEXT NOT NULL,payload_json TEXT NOT NULL);"), error)
        && execute(database_, QStringLiteral(
        "CREATE INDEX IF NOT EXISTS idx_redemption_history ON history(event_day,symbol,id DESC);"), error);
}

bool RedemptionEngine::persistPcfGate() {
    QJsonObject symbols;
    for (auto it=pcfRuntime_.cbegin();it!=pcfRuntime_.cend();++it) {
        const auto &r=it.value();
        symbols.insert(it.key(),QJsonObject{{"day",r.day.toString(Qt::ISODate)},
            {"attempts",r.attempts},{"last_attempt",iso(r.lastAttemptUtc)},
            {"cooldown",iso(r.cooldownUntilUtc)},{"candidate",r.candidateIndex}});
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral("INSERT OR REPLACE INTO settings(key,value_json) VALUES('pcf_gate',?)"));
    query.addBindValue(QJsonDocument(QJsonObject{{"next",iso(pcfNextAllowedUtc_)},
        {"blocked",iso(pcfBlockedUntilUtc_)},{"failure_count",pcfFailureCount_},{"symbols",symbols}}).toJson(QJsonDocument::Compact));
    if (query.exec()) return true;
    lastError_=QStringLiteral("PCF gate persistence failed: ")+query.lastError().text();
    return false;
}

void RedemptionEngine::loadRepository() {
    watchlist_ = kProductionSymbols;
    QJsonObject names;
    QSqlQuery settings(database_);
    if (settings.exec(QStringLiteral("SELECT key,value_json FROM settings"))) {
        while (settings.next()) {
            const QJsonDocument document = QJsonDocument::fromJson(settings.value(1).toByteArray());
            if (settings.value(0).toString() == QStringLiteral("watchlist") && document.isArray()) {
                QStringList loaded;
                for (const auto &value : document.array()) {
                    QString error;
                    const QString symbol = RedemptionCore::normalizeSymbol(value.toString(), &error);
                    if (!symbol.isEmpty() && !loaded.contains(symbol)) loaded.append(symbol);
                }
                if (!loaded.isEmpty()) watchlist_ = loaded;
            } else if (settings.value(0).toString() == QStringLiteral("symbol_names")
                       && document.isObject()) {
                names = document.object();
            } else if (settings.value(0).toString() == QStringLiteral("pcf_gate") && document.isObject()) {
                const auto gate=document.object();
                pcfNextAllowedUtc_=QDateTime::fromString(gate.value("next").toString(),Qt::ISODateWithMs);
                pcfBlockedUntilUtc_=QDateTime::fromString(gate.value("blocked").toString(),Qt::ISODateWithMs);
                pcfFailureCount_=gate.value("failure_count").toInt();
                const auto symbols=gate.value("symbols").toObject();
                for(auto it=symbols.begin();it!=symbols.end();++it) {
                    const auto value=it.value().toObject(); auto &r=pcfRuntime_[it.key()];
                    r.day=QDate::fromString(value.value("day").toString(),Qt::ISODate);
                    r.attempts=value.value("attempts").toInt();
                    r.lastAttemptUtc=QDateTime::fromString(value.value("last_attempt").toString(),Qt::ISODateWithMs);
                    r.cooldownUntilUtc=QDateTime::fromString(value.value("cooldown").toString(),Qt::ISODateWithMs);
                    r.candidateIndex=value.value("candidate").toInt();
                }
            }
        }
    }
    for (const QString &symbol : std::as_const(watchlist_)) {
        SymbolState state;
        state.windcode = symbol;
        state.customName = names.value(displaySymbol(symbol)).toString();
        states_.insert(symbol, state);
    }
    QSqlQuery pcf(database_);
    if (pcf.exec(QStringLiteral("SELECT symbol,payload_json FROM pcf"))) {
        const QDate today = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
        while (pcf.next()) {
            const QString symbol = pcf.value(0).toString();
            const auto document = QJsonDocument::fromJson(pcf.value(1).toByteArray());
            if (states_.contains(symbol) && document.isObject()) {
                QJsonObject payload = document.object();
                payload.insert(QStringLiteral("requested_day"), today.toString(Qt::ISODate));
                payload.insert(QStringLiteral("status"),
                               payload.value(QStringLiteral("trading_day")).toString()
                                       == today.toString(Qt::ISODate)
                                   ? QStringLiteral("ready") : QStringLiteral("stale"));
                states_[symbol].pcf = payload;
            }
        }
    }
}

bool RedemptionEngine::persistSettings(QString *error) {
    QJsonArray symbols;
    QJsonObject names;
    for (const QString &symbol : std::as_const(watchlist_)) {
        symbols.append(displaySymbol(symbol));
        if (!states_.value(symbol).customName.isEmpty()) {
            names.insert(displaySymbol(symbol), states_.value(symbol).customName);
        }
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral("INSERT OR REPLACE INTO settings(key,value_json) VALUES(?,?)"));
    for (const auto &entry : {qMakePair(QStringLiteral("watchlist"), QJsonDocument(symbols)),
                              qMakePair(QStringLiteral("symbol_names"), QJsonDocument(names))}) {
        query.bindValue(0, entry.first);
        query.bindValue(1, entry.second.toJson(QJsonDocument::Compact));
        if (!query.exec()) {
            if (error) *error = query.lastError().text();
            return false;
        }
    }
    return true;
}

bool RedemptionEngine::persistPcf(const QString &windcode, const QJsonObject &pcf,
                                  QString *error) {
    QSqlQuery query(database_);
    query.prepare(QStringLiteral("INSERT OR REPLACE INTO pcf(symbol,payload_json,updated_at) VALUES(?,?,?)"));
    query.addBindValue(windcode);
    query.addBindValue(QJsonDocument(pcf).toJson(QJsonDocument::Compact));
    query.addBindValue(iso(nowUtc()));
    if (query.exec()) return true;
    if (error) *error = query.lastError().text();
    return false;
}

bool RedemptionEngine::appendHistory(const QJsonObject &record, QString *error) {
    const QDateTime eventTime = QDateTime::fromString(
        record.value(QStringLiteral("event_time")).toString(), Qt::ISODateWithMs);
    const QDate currentDay = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
    if (!pruneHistory(currentDay, error)) return false;
    QSqlQuery query(database_);
    query.prepare(QStringLiteral("INSERT INTO history(event_day,event_time,symbol,payload_json) VALUES(?,?,?,?)"));
    query.addBindValue(eventTime.toTimeZone(QTimeZone("Asia/Shanghai")).date().toString(Qt::ISODate));
    query.addBindValue(record.value(QStringLiteral("event_time")).toString());
    query.addBindValue(record.value(QStringLiteral("windcode")).toString());
    query.addBindValue(QJsonDocument(record).toJson(QJsonDocument::Compact));
    if (query.exec()) return true;
    if (error) *error = query.lastError().text();
    return false;
}

bool RedemptionEngine::pruneHistory(const QDate &today, QString *error) {
    if (!database_.isOpen() || !today.isValid()) return true;
    if (lastHistoryPruneDay_ == today) return true;
    // Keep the current day plus the preceding 119 calendar days, matching the
    // original ChangeHistoryStore retention_days=120 calculation.
    const QDate cutoff = today.addDays(-119);
    QSqlQuery query(database_);
    query.prepare(QStringLiteral("DELETE FROM history WHERE event_day < ?"));
    query.addBindValue(cutoff.toString(Qt::ISODate));
    if (!query.exec()) {
        if (error) *error = query.lastError().text();
        return false;
    }
    lastHistoryPruneDay_ = today;
    return true;
}

QJsonArray RedemptionEngine::queryHistory(const QDate &day, const QString &symbol,
                                          int limit) const {
    QJsonArray result;
    if (!database_.isOpen() || !day.isValid()) return result;
    QSqlQuery query(database_);
    QString sql = QStringLiteral("SELECT payload_json FROM history WHERE event_day=?");
    if (!symbol.isEmpty()) sql += QStringLiteral(" AND symbol=?");
    sql += QStringLiteral(" ORDER BY id DESC LIMIT ?");
    query.prepare(sql);
    query.addBindValue(day.toString(Qt::ISODate));
    if (!symbol.isEmpty()) query.addBindValue(symbol);
    query.addBindValue(std::clamp(limit <= 0 ? 500 : limit, 1, 5000));
    if (!query.exec()) return result;
    while (query.next()) {
        const auto document = QJsonDocument::fromJson(query.value(0).toByteArray());
        if (document.isObject()) result.append(document.object());
    }
    return result;
}

void RedemptionEngine::initialize(const hub::ModuleContext &context) {
    if (initialized_) return;
    // The test-time operating mode is deliberately process-local.  A new
    // process must always return to the production schedule.
    operatingMode_ = QStringLiteral("work");
    context_ = context;
    const QString allowed = QDir(QStandardPaths::writableLocation(
        QStandardPaths::GenericDataLocation)).filePath(QStringLiteral("MachomeHub/data/redemption"));
    const bool testMode = context.settings.value(QStringLiteral("test_mode")).toBool(false);
    if (!testMode && QDir::cleanPath(context.dataRoot) != QDir::cleanPath(allowed)) {
        lastError_ = QStringLiteral("原生申赎数据只能写入 MachomeHub/data/redemption");
        state_ = QStringLiteral("blocked");
        emit eventReady(QStringLiteral("redemption.configuration_error"),
                        {{QStringLiteral("message"), lastError_}});
        return;
    }
    QString error;
    if (!openRepository(&error)) {
        lastError_ = error;
        state_ = QStringLiteral("blocked");
        return;
    }
    loadRepository();
    initializeQmt();
    initialized_ = true;
    state_ = QStringLiteral("initialized");
    publishSnapshot();
}

void RedemptionEngine::start() {
    if (!initialized_ || running_) return;
    running_ = true;
    state_ = QStringLiteral("scheduled_idle");
    startCompatibilityServer();
    startWindHelper();
    scheduleTimer_.start();
    pcfTimer_.start();
    evaluateSchedule();
    pcfTick();
    publishSnapshot();
}

void RedemptionEngine::stop(hub::StopMode mode) {
    Q_UNUSED(mode)
    if (!running_ && !initialized_) return;
    scheduleTimer_.stop();
    pcfTimer_.stop();
    running_ = false;
    pcfGateTimer_.stop();
    pcfRefreshPending_=false;
    pcfForcedSymbols_.clear();
    if (activePcfReply_) activePcfReply_->abort();
    stopMonitoring(QStringLiteral("engine-stop"));
    stopQmt();
    stopWindHelper();
    stopCompatibilityServer();
    running_ = false;
    state_ = QStringLiteral("stopped");
    publishSnapshot();
}

QJsonObject RedemptionEngine::scheduleSnapshot() const {
    const ScheduleDecision value = RedemptionCore::evaluateSchedule(nowUtc());
    return {{QStringLiteral("timezone"), QStringLiteral("Asia/Shanghai")},
            {QStringLiteral("operating_mode"), operatingMode_},
            {QStringLiteral("automatic_schedule_enabled"),
             operatingMode_ == QStringLiteral("work")},
            {QStringLiteral("manual_time_window_bypass"),
             operatingMode_ == QStringLiteral("weekend_test")},
            {QStringLiteral("phase"), value.phase},
            {QStringLiteral("business_day"), value.businessDay},
            {QStringLiteral("pcf_window"), value.pcfWindow},
            {QStringLiteral("monitoring_desired"), value.monitoringDesired},
            {QStringLiteral("next_transition"), iso(value.nextTransitionUtc)},
            {QStringLiteral("daily_reset"), QStringLiteral("09:00:00")},
            {QStringLiteral("wind_launch"), QStringLiteral("09:10:00")},
            {QStringLiteral("tbapi_warmup"), QStringLiteral("09:15:05")},
            {QStringLiteral("subscription_start"), QStringLiteral("09:15:30")},
            {QStringLiteral("stop"), QStringLiteral("15:00:00")}};
}

QJsonObject RedemptionEngine::symbolSnapshot(const SymbolState &value) const {
    const qint64 age = value.updatedAt.isValid()
        ? qMax<qint64>(0, value.updatedAt.msecsTo(nowUtc())) : -1;
    return {{QStringLiteral("symbol"), displaySymbol(value.windcode)},
            {QStringLiteral("windcode"), value.windcode},
            {QStringLiteral("name"), value.customName.isEmpty()
                 ? value.pcf.value(QStringLiteral("fund_name")).toString() : value.customName},
            {QStringLiteral("custom_name"), value.customName},
            {QStringLiteral("status"), value.status},
            {QStringLiteral("sub_id"), value.subId < 0 ? QJsonValue() : QJsonValue(value.subId)},
            {QStringLiteral("values"), value.values},
            {QStringLiteral("updated_at"), value.updatedAt.isValid()
                 ? value.updatedAt.toTimeZone(QTimeZone("Asia/Shanghai")).toString(QStringLiteral("HH:mm:ss")) : QString()},
            {QStringLiteral("age_seconds"), age < 0 ? QJsonValue() : QJsonValue(age / 1000.0)},
            {QStringLiteral("last_change_at"), iso(value.lastChangeAt)},
            {QStringLiteral("last_change"), value.lastChange},
            {QStringLiteral("pcf"), value.pcf},
            {QStringLiteral("opportunity"), value.opportunity}};
}

QJsonObject RedemptionEngine::healthSnapshot() const {
    const QString helperMode = context_.settings.value(QStringLiteral("wind_helper_mode"))
                                   .toString(QStringLiteral("disabled"));
    const bool helperOk = helperMode == QStringLiteral("disabled")
        || helperState_ == QStringLiteral("ready")
        || helperState_ == QStringLiteral("subscribed");
    const bool collectionExpected = running_
        && operatingMode_ == QStringLiteral("work")
        && RedemptionCore::evaluateSchedule(nowUtc()).monitoringDesired;
    return {{QStringLiteral("ok"), initialized_ && running_ && lastError_.isEmpty()
                                      && (!collectionExpected || (helperOk && monitoring_))},
            {QStringLiteral("service"), QStringLiteral("machome-native-redemption")},
            {QStringLiteral("protocol"), 1},
            {QStringLiteral("monitoring"), monitoring_},
            {QStringLiteral("record_only"), context_.recordOnly},
            {QStringLiteral("wind_helper_state"), helperState_},
            {QStringLiteral("wind_helper_ok"), helperOk},
            {QStringLiteral("collection_expected"), collectionExpected},
            {QStringLiteral("live_orders_allowed"), liveOrdersAllowed()},
            {QStringLiteral("legacy_execution_allowed"), false}};
}

QJsonObject RedemptionEngine::snapshot() const {
    QJsonArray items;
    for (const QString &symbol : watchlist_) items.append(symbolSnapshot(states_.value(symbol)));
    QJsonObject pcfRuntime;
    for (auto it = pcfRuntime_.constBegin(); it != pcfRuntime_.constEnd(); ++it) {
        pcfRuntime.insert(displaySymbol(it.key()), QJsonObject{
                          {QStringLiteral("day"), it->day.toString(Qt::ISODate)},
                           {QStringLiteral("attempts"), it->attempts},
                           {QStringLiteral("in_flight"), it->inFlight},
                           {QStringLiteral("candidate_index"), it->candidateIndex},
                           {QStringLiteral("last_attempt_at"), iso(it->lastAttemptUtc)},
                           {QStringLiteral("cooldown_until"), iso(it->cooldownUntilUtc)},
                           {QStringLiteral("last_error"), it->lastError}});
    }
    return {{QStringLiteral("engine"), QStringLiteral("native")},
            {QStringLiteral("type"), QStringLiteral("snapshot")},
            {QStringLiteral("protocol"), 1},
            {QStringLiteral("server_time"), iso(nowUtc())},
            {QStringLiteral("operating_mode"), operatingMode_},
            {QStringLiteral("state"), state_},
            {QStringLiteral("running"), running_},
            {QStringLiteral("monitoring"), monitoring_},
            {QStringLiteral("record_only"), context_.recordOnly},
            {QStringLiteral("started_by"), monitoring_ ? QStringLiteral("native-engine") : QString()},
            {QStringLiteral("last_error"), lastError_},
            {QStringLiteral("wind"), QJsonObject{
                 {QStringLiteral("state"), helperState_},
                 {QStringLiteral("label"), helperState_ == QStringLiteral("subscribed")
                        ? QStringLiteral("Wind TBAPI2 已订阅")
                        : helperState_ == QStringLiteral("blocked")
                            ? QStringLiteral("Wind 探针已阻止，请查看 ABI 状态")
                            : helperState_ == QStringLiteral("degraded")
                                ? QStringLiteral("Wind 探针降级")
                                : helperState_ == QStringLiteral("quitting")
                                    ? QStringLiteral("Wind 正在安全退出")
                                    : helperReady_ && windRunning_ && tbapiReady_
                                        ? QStringLiteral("Wind 与 TBAPI2 已就绪")
                                        : helperReady_ ? QStringLiteral("Wind 探针在线，等待 Wind/TBAPI2")
                                                       : QStringLiteral("Wind 探针未启用")},
                 {QStringLiteral("running"), windRunning_},
                 {QStringLiteral("tbapi_loaded"), tbapiReady_},
                 {QStringLiteral("last_cleanup_at"), iso(lastWindCleanupAtUtc_)},
                 {QStringLiteral("cleanup_deleted_count"), windCleanupDeletedCount_ < 0
                      ? QJsonValue() : QJsonValue(windCleanupDeletedCount_)},
                 {QStringLiteral("last_error"), helperLastError_}}},
            {QStringLiteral("health"), healthSnapshot()},
            {QStringLiteral("schedule"), scheduleSnapshot()},
            {QStringLiteral("watchlist"), QJsonArray::fromStringList([this] {
                 QStringList display; for (const QString &s : watchlist_) display.append(displaySymbol(s)); return display;
             }())},
            {QStringLiteral("items"), items},
            {QStringLiteral("pcf_runtime"), pcfRuntime},
            {QStringLiteral("qmt_backends"), qmtSnapshots_},
            {QStringLiteral("sequence"), static_cast<qint64>(sequence_)}};
}

void RedemptionEngine::publishSnapshot() {
    ++sequence_;
    const QJsonObject value = snapshot();
    emit snapshotReady(value);
    broadcastCompat(value);
}

void RedemptionEngine::emitStatus(const QString &message) {
    const QJsonObject value{{QStringLiteral("type"), QStringLiteral("status")},
                            {QStringLiteral("protocol"), 1},
                            {QStringLiteral("server_time"), iso(nowUtc())},
                            {QStringLiteral("monitoring"), monitoring_},
                            {QStringLiteral("message"), message},
                            {QStringLiteral("wind"), snapshot().value(QStringLiteral("wind"))}};
    emit eventReady(QStringLiteral("redemption.status"), value);
    broadcastCompat(value);
}

bool RedemptionEngine::ingestCapture(const QJsonObject &capture, bool replay,
                                     QString *error) {
    const QJsonObject canonical = RedemptionCore::canonicalCapture(capture, error);
    if (canonical.isEmpty()) return false;
    const QString symbol = canonical.value(QStringLiteral("windcode")).toString();
    if (!states_.contains(symbol)) {
        if (error) *error = QStringLiteral("捕获标的不在观察列表中");
        return false;
    }
    QDateTime observed = QDateTime::fromString(
        canonical.value(QStringLiteral("observed_at")).toString(), Qt::ISODateWithMs);
    if (observed.isValid() && captureNotBeforeUtc_.isValid()
        && observed.toUTC() < captureNotBeforeUtc_) {
        // Match the original host's 09:00 cutoff: an asynchronous callback
        // from the previous day must never repopulate a freshly reset baseline.
        return true;
    }
    if (observed.isValid() && states_[symbol].updatedAt.isValid()
        && observed <= states_[symbol].updatedAt) return true;
    SymbolState &state = states_[symbol];
    const QJsonObject previous = state.baseline;
    const QJsonObject values = canonical.value(QStringLiteral("values")).toObject();
    state.values = values;
    state.updatedAt = observed;
    state.status = monitoring_ ? QStringLiteral("monitoring") : QStringLiteral("cached");
    state.subId = canonical.value(QStringLiteral("sub_id")).toInteger(-1);
    state.previousValues = previous;
    state.baseline = values;
    state.opportunity = RedemptionCore::classifyIntradayOpportunity(
        previous.isEmpty() ? nullptr : &previous, values, state.pcf,
        observed.toTimeZone(QTimeZone("Asia/Shanghai")).date());
    if (previous.isEmpty()) {
        publishSnapshot();
        return true;
    }
    const QJsonArray changes = RedemptionCore::changeDetails(previous, values);
    if (changes.isEmpty()) return true;
    state.lastChange = changes;
    state.lastChangeAt = observed;
    QJsonObject history{{QStringLiteral("event_time"), iso(observed)},
                        {QStringLiteral("timestamp"), iso(observed)},
                        {QStringLiteral("observed_at"), iso(nowUtc())},
                        {QStringLiteral("symbol"), displaySymbol(symbol)},
                        {QStringLiteral("windcode"), symbol},
                        {QStringLiteral("name"), state.customName},
                        {QStringLiteral("previous"), previous},
                        {QStringLiteral("current"), values},
                        {QStringLiteral("changes"), changes},
                        {QStringLiteral("opportunity"), state.opportunity}};
    const QJsonObject primaryChange = changes.first().toObject();
    history.insert(QStringLiteral("direction"),
                   state.opportunity.value(QStringLiteral("kind")));
    history.insert(QStringLiteral("old_value"), primaryChange.value(QStringLiteral("old")));
    history.insert(QStringLiteral("new_value"), primaryChange.value(QStringLiteral("new")));
    history.insert(QStringLiteral("delta"),
                   primaryChange.value(QStringLiteral("new")).toDouble()
                       - primaryChange.value(QStringLiteral("old")).toDouble());
    history.insert(QStringLiteral("basket_count"),
                   state.opportunity.value(QStringLiteral("net_baskets")));
    history.insert(QStringLiteral("status"),
                   state.opportunity.value(QStringLiteral("label")));
    QString storageError;
    if (!replay && !appendHistory(history, &storageError)) {
        lastError_ = storageError;
        if (error) *error = storageError;
        return false;
    }
    QJsonObject event{{QStringLiteral("type"), QStringLiteral("change")},
                      {QStringLiteral("protocol"), 1},
                      {QStringLiteral("server_time"), iso(nowUtc())},
                      {QStringLiteral("replay"), replay},
                      {QStringLiteral("items"), QJsonArray{QJsonObject{
                           {QStringLiteral("symbol"), displaySymbol(symbol)},
                           {QStringLiteral("windcode"), symbol},
                           {QStringLiteral("changes"), changes},
                           {QStringLiteral("current"), symbolSnapshot(state)}}}}};
    emit eventReady(QStringLiteral("redemption.change"), event);
    broadcastCompat(event);
    if (state.opportunity.value(QStringLiteral("actionable")).toBool()) {
        emit eventReady(QStringLiteral("redemption.notification_decision"),
                        {{QStringLiteral("enabled"), context_.settings
                              .value(QStringLiteral("notifications_enabled")).toBool(false)},
                         {QStringLiteral("sent"), false},
                         {QStringLiteral("record_only"), context_.recordOnly},
                         {QStringLiteral("delivery_channel"), QStringLiteral("native_ui")},
                         {QStringLiteral("delivery_state"), QStringLiteral("event_emitted")},
                         {QStringLiteral("symbol"), displaySymbol(symbol)},
                         {QStringLiteral("opportunity"), state.opportunity}});
    }
    publishSnapshot();
    return true;
}

bool RedemptionEngine::ingestCaptureForTest(const QJsonObject &capture, QString *error) {
    return ingestCapture(capture, false, error);
}

bool RedemptionEngine::installPcf(const QString &symbol, const QJsonObject &payload,
                                  bool persist, QString *error) {
    const QString windcode = RedemptionCore::normalizeSymbol(symbol, error);
    if (windcode.isEmpty() || !states_.contains(windcode)) {
        if (error && error->isEmpty()) *error = QStringLiteral("PCF 标的不在观察列表中");
        return false;
    }
    const QDate today = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
    const QJsonObject normalized = RedemptionCore::normalizePcf(payload, windcode, today, error);
    if (normalized.isEmpty()) return false;
    states_[windcode].pcf = normalized;
    if (!states_[windcode].values.isEmpty()) {
        const QJsonObject &previous = states_[windcode].previousValues;
        states_[windcode].opportunity = RedemptionCore::classifyIntradayOpportunity(
            previous.isEmpty() ? nullptr : &previous,
            states_[windcode].values, normalized, today);
    }
    if (persist && !persistPcf(windcode, normalized, error)) return false;
    return true;
}

bool RedemptionEngine::installPcfForTest(const QString &symbol,
                                         const QJsonObject &pcf, QString *error) {
    const bool ok = installPcf(symbol, pcf, true, error);
    if (ok) publishSnapshot();
    return ok;
}

void RedemptionEngine::dailyReset(const QDate &day, const QString &reason) {
    captureNotBeforeUtc_ = QDateTime(
        day, QTime(9, 0), QTimeZone("Asia/Shanghai")).toUTC();
    for (auto it = states_.begin(); it != states_.end(); ++it) {
        it->values = {};
        it->baseline = {};
        it->previousValues = {};
        it->lastChange = {};
        it->opportunity = {};
        it->updatedAt = {};
        it->lastChangeAt = {};
        it->status = monitoring_ ? QStringLiteral("monitoring") : QStringLiteral("waiting");
    }
    resetDay_ = day;
    lastError_.clear();
    QString pruneError;
    if (!pruneHistory(day, &pruneError)) lastError_ = pruneError;
    emitStatus(QStringLiteral("每日实时基线已重置：%1").arg(reason));
    publishSnapshot();
}

bool RedemptionEngine::windCollectionReady() const {
    const QString mode = context_.settings.value(QStringLiteral("wind_helper_mode"))
                             .toString(QStringLiteral("disabled"));
    if (mode == QStringLiteral("fixture")) return helperReady_;
    return mode == QStringLiteral("live") && helperReady_ && windRunning_ && tbapiReady_;
}

void RedemptionEngine::requestWarmup(const QString &reason, bool force) {
    if (!windCollectionReady()) return;
    const QDate day = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
    if (warmupDay_ == day || (!force && warmupAttemptDay_ == day)) return;
    warmupAttemptDay_ = day;
    warmupSettleElapsed_ = false;
    sendHelperCommand(QStringLiteral("warmup"),
                      {{QStringLiteral("symbols"),
                        QJsonArray::fromStringList(watchlist_.isEmpty()
                            ? QStringList{} : QStringList{watchlist_.first()})},
                       {QStringLiteral("reason"), reason}});
}

void RedemptionEngine::finishWarmupSettle() {
    warmupSettleElapsed_ = true;
    if (!monitoringWarmupPending_) return;
    const QString reason = pendingMonitoringReason_;
    monitoringWarmupPending_ = false;
    pendingMonitoringReason_.clear();
    startMonitoring(reason.isEmpty() ? QStringLiteral("post-warmup") : reason);
}

void RedemptionEngine::startMonitoring(const QString &reason) {
    // Recheck at the point of subscription: a warmup callback can arrive
    // after the close timer has already run.
    if (operatingMode_ == QStringLiteral("work")
        && !RedemptionCore::evaluateSchedule(nowUtc()).monitoringDesired) return;
    if (monitoring_ || monitoringRequested_ || monitoringWarmupPending_) return;
    lastMonitorAttemptUtc_ = nowUtc();
    if (!windCollectionReady()) {
        state_ = QStringLiteral("warming");
        lastError_ = QStringLiteral("Wind/TBAPI 采集边界未就绪，已禁止进入监控态");
        return;
    }
    const QDate day = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
    if (warmupDay_ != day || !warmupSettleElapsed_) {
        monitoringWarmupPending_ = true;
        pendingMonitoringReason_ = reason;
        state_ = QStringLiteral("warming");
        requestWarmup(reason, reason == QStringLiteral("manual"));
        emitStatus(QStringLiteral("正在执行 TBAPI 临时订阅预热，确认停订后等待 2 秒"));
        publishSnapshot();
        return;
    }
    monitoringRequested_ = true;
    state_ = QStringLiteral("subscribing");
    for (auto it = states_.begin(); it != states_.end(); ++it) it->status = QStringLiteral("warming");
    sendHelperCommand(QStringLiteral("subscribe"),
                      {{QStringLiteral("symbols"), QJsonArray::fromStringList(watchlist_)}});
    emitStatus(QStringLiteral("正在订阅 %1 个标的：%2").arg(watchlist_.size()).arg(reason));
    publishSnapshot();
}

void RedemptionEngine::stopMonitoring(const QString &reason, const QString &requestId) {
    if (!monitoring_ && !monitoringRequested_ && !monitoringWarmupPending_) {
        // A failed pre-subscription attempt may only have changed state_ to
        // warming. It still needs to leave that transient state at the close.
        if (state_ == QStringLiteral("warming") || state_ == QStringLiteral("subscribing")
            || state_ == QStringLiteral("active")) {
            state_ = QStringLiteral("scheduled_idle");
            for (auto it = states_.begin(); it != states_.end(); ++it) {
                it->status = QStringLiteral("stopped");
                it->subId = -1;
            }
            publishSnapshot();
        }
        if (!requestId.isEmpty()) {
            emit commandFinished(requestId, true, QStringLiteral("原生监控已经停止"), snapshot());
        }
        return;
    }
    QJsonObject arguments;
    if (!requestId.isEmpty()) arguments.insert(QStringLiteral("request_id"), requestId);
    sendHelperCommand(QStringLiteral("unsubscribe"), arguments);
    monitoring_ = false;
    monitoringRequested_ = false;
    monitoringWarmupPending_ = false;
    pendingMonitoringReason_.clear();
    state_ = QStringLiteral("scheduled_idle");
    for (auto it = states_.begin(); it != states_.end(); ++it) {
        it->status = QStringLiteral("stopped");
        it->subId = -1;
    }
    emitStatus(QStringLiteral("监控已停止：%1").arg(reason));
    publishSnapshot();
}

void RedemptionEngine::evaluateScheduleForTest() { evaluateSchedule(); }

void RedemptionEngine::evaluateSchedule() {
    const auto now = nowUtc();
    // Status reads are independent of subscriptions. Wind can be opened or
    // closed from the desktop while this module has no capture traffic.
    if (helperReady_ && (!lastWindStatusPollUtc_.isValid()
        || now < lastWindStatusPollUtc_ || lastWindStatusPollUtc_.secsTo(now) >= 10)) {
        lastWindStatusPollUtc_ = now;
        sendHelperCommand(QStringLiteral("status"));
    }
    const ScheduleDecision decision = RedemptionCore::evaluateSchedule(now);
    if (!lastScheduleSnapshotUtc_.isValid() || now < lastScheduleSnapshotUtc_
        || lastScheduleSnapshotUtc_.secsTo(now) >= 5 || lastSchedulePhase_ != decision.phase) {
        lastScheduleSnapshotUtc_ = now;
        lastSchedulePhase_ = decision.phase;
        publishSnapshot();
    }
    // weekend_test is intentionally manual-only.  It removes the clock's
    // automatic stop/relaunch pressure so an operator can exercise Wind,
    // PCF and QMT on a weekend, but it never synthesizes production traffic.
    if (operatingMode_ == QStringLiteral("weekend_test")) return;

    const QDate day = decision.localNow.date();
    if (decision.resetDue && resetDay_ != day) dailyReset(day, QStringLiteral("09:00 schedule"));
    if (decision.windLaunchDue && windLaunchDay_ != day) {
        if (windRunning_) {
            windLaunchDay_ = day;
        } else if (helperReady_
                   && context_.settings.value(QStringLiteral("wind_control_enabled")).toBool(false)
                   && (!lastWindLaunchAttemptUtc_.isValid()
                       || lastWindLaunchAttemptUtc_.secsTo(nowUtc()) >= 120)) {
            lastWindLaunchAttemptUtc_ = nowUtc();
            sendHelperCommand(QStringLiteral("start_wind"));
        }
    }
    if ((decision.warmupDue || decision.monitoringDesired) && warmupDay_ != day) {
        requestWarmup(QStringLiteral("schedule-09:15:05"));
    }
    if (decision.monitoringDesired && !monitoring_ && !monitoringRequested_
        && (!lastMonitorAttemptUtc_.isValid()
            || lastMonitorAttemptUtc_.secsTo(nowUtc()) >= 30)) {
        startMonitoring(QStringLiteral("09:15:30 schedule"));
    }
    if (!decision.windLaunchDue) {
        // Manual Wind cleanup earlier today must not suppress the actual
        // closing transition. Enforce desired state on every schedule tick.
        stopMonitoring(QStringLiteral("Wind 运行时段之外"));
        for (auto *client : std::as_const(qmtClients_)) {
            if (client->snapshot().value(QStringLiteral("want_connection")).toBool())
                client->disconnectBackend();
        }
        if (helperReady_
                   && (windRunning_ || lastWindCleanupAtUtc_.toTimeZone(QTimeZone("Asia/Shanghai")).date() != day)
                   && context_.settings.value(QStringLiteral("wind_control_enabled")).toBool(false)
                   && (!lastWindShutdownAttemptUtc_.isValid()
                       || lastWindShutdownAttemptUtc_.secsTo(nowUtc()) >= 120)) {
            lastWindShutdownAttemptUtc_ = nowUtc();
            sendHelperCommand(QStringLiteral("shutdown_wind"));
        }
    }
}

bool RedemptionEngine::setWatchlist(const QJsonArray &symbols, QString *error) {
    if (symbols.isEmpty() || symbols.size() > 200) {
        if (error) *error = QStringLiteral("观察列表必须包含 1–200 个标的");
        return false;
    }
    QStringList next;
    for (const auto &value : symbols) {
        const QString symbol = RedemptionCore::normalizeSymbol(value.toString(), error);
        if (symbol.isEmpty()) return false;
        if (!next.contains(symbol)) next.append(symbol);
    }
    const bool restart = monitoring_;
    if (restart) stopMonitoring(QStringLiteral("更新观察列表"));
    QHash<QString, SymbolState> updated;
    for (const QString &symbol : next) {
        if (states_.contains(symbol)) updated.insert(symbol, states_.value(symbol));
        else { SymbolState state; state.windcode = symbol; updated.insert(symbol, state); }
    }
    states_ = updated;
    watchlist_ = next;
    if (!persistSettings(error)) return false;
    if (restart) startMonitoring(QStringLiteral("观察列表更新"));
    return true;
}

bool RedemptionEngine::setSymbolName(const QString &symbol, const QString &name,
                                     QString *error) {
    const QString windcode = RedemptionCore::normalizeSymbol(symbol, error);
    if (windcode.isEmpty() || !states_.contains(windcode)) {
        if (error && error->isEmpty()) *error = QStringLiteral("只能修改观察列表中的标的");
        return false;
    }
    const QString clean = name.simplified();
    if (clean.size() > 40) {
        if (error) *error = QStringLiteral("标的名称不能超过 40 个字符");
        return false;
    }
    states_[windcode].customName = clean;
    return persistSettings(error);
}

void RedemptionEngine::initializeQmt() {
    for (const auto &value : context_.settings.value(QStringLiteral("qmt_backends")).toArray()) {
        const QJsonObject definition = value.toObject();
        if (!definition.value(QStringLiteral("enabled")).toBool(true)) continue;
        machome::qmt::QmtClientConfig config;
        config.id = definition.value(QStringLiteral("id")).toString().trimmed();
        config.host = definition.value(QStringLiteral("host")).toString().trimmed();
        config.port = static_cast<quint16>(definition.value(QStringLiteral("port")).toInt());
        config.reconnectIntervalMs = definition.value(QStringLiteral("reconnect_interval_ms")).toInt(5000);
        config.heartbeatIntervalMs = definition.value(QStringLiteral("heartbeat_interval_ms")).toInt(5000);
        config.silenceTimeoutMs = definition.value(QStringLiteral("silence_timeout_ms")).toInt(15000);
        config.orderThrottleMs = definition.value(QStringLiteral("order_throttle_ms")).toInt(5000);
        if (config.id.isEmpty() || config.host.isEmpty() || config.port == 0
            || qmtClients_.contains(config.id)) continue;
        auto *client = new machome::qmt::QmtClient(config, this);
        qmtClients_.insert(config.id, client);
        qmtSnapshots_.insert(config.id, client->snapshot());
        auto initial = qmtSnapshots_.value(config.id).toObject();
        initial.insert(QStringLiteral("connection_policy"),
            context_.settings.value(QStringLiteral("qmt_auto_connect_enabled")).toBool(false)
                ? QStringLiteral("configured") : QStringLiteral("on_demand"));
        qmtSnapshots_.insert(config.id, initial);
        connect(client, &machome::qmt::QmtClient::snapshotChanged, this,
                [this, id = config.id](const QJsonObject &snapshot) { updateQmtSnapshot(id, snapshot); });
        connect(client, &machome::qmt::QmtClient::eventOccurred, this,
                [this, id = config.id](const QString &kind, QJsonObject payload) {
            payload.insert(QStringLiteral("backend"), id);
            emit eventReady(QStringLiteral("redemption.qmt.") + kind, payload);
        });
        connect(client, &machome::qmt::QmtClient::orderFinished, this,
                [this, id = config.id](const QString &commandId, bool ok,
                                       const QString &message, QJsonObject details) {
            details.insert(QStringLiteral("backend"), id);
            emit commandFinished(commandId, ok, message, details);
        });
        // Server monitoring does not need account sessions. Old per-backend
        // auto_connect flags only take effect with an explicit module opt-in.
        if (context_.settings.value(QStringLiteral("qmt_auto_connect_enabled")).toBool(false)
            && definition.value(QStringLiteral("auto_connect")).toBool(false)
            && RedemptionCore::evaluateSchedule(nowUtc()).monitoringDesired)
            client->connectBackend();
    }
}

void RedemptionEngine::updateQmtSnapshot(const QString &id,
                                         const QJsonObject &snapshot) {
    auto annotated = snapshot;
    annotated.insert(QStringLiteral("connection_policy"),
        context_.settings.value(QStringLiteral("qmt_auto_connect_enabled")).toBool(false)
            ? QStringLiteral("configured") : QStringLiteral("on_demand"));
    qmtSnapshots_.insert(id, annotated);
    if (snapshot.value(QStringLiteral("ready")).toBool()) {
        if (pendingQmtConnect_.contains(id)) {
            emit commandFinished(pendingQmtConnect_.take(id), true,
                                 QStringLiteral("%1 已连接并完成订单/持仓全量同步").arg(id), snapshot);
        }
        if (pendingQmtSync_.contains(id)) {
            emit commandFinished(pendingQmtSync_.take(id), true,
                                 QStringLiteral("%1 订单/持仓全量同步完成").arg(id), snapshot);
        }
    }
    if (snapshot.value(QStringLiteral("connection_state")).toString()
            == QStringLiteral("disconnected") && pendingQmtDisconnect_.contains(id)) {
        emit commandFinished(pendingQmtDisconnect_.take(id), true,
                             QStringLiteral("%1 已断开").arg(id), snapshot);
    }
    publishSnapshot();
}

void RedemptionEngine::stopQmt() {
    for (auto *client : std::as_const(qmtClients_)) client->disconnectBackend();
}

bool RedemptionEngine::liveOrdersAllowed(QString *reason) const {
#if defined(MACHOME_ENABLE_LIVE_QMT_ORDERS) && MACHOME_ENABLE_LIVE_QMT_ORDERS
    const bool compiled = true;
#else
    const bool compiled = false;
#endif
    const bool configured = context_.settings.value(QStringLiteral("live_qmt_orders_enabled")).toBool(false);
    const bool allowed = compiled && configured && !context_.recordOnly;
    if (!allowed && reason) {
        *reason = !compiled ? QStringLiteral("构建未启用真实 QMT 下单")
            : !configured ? QStringLiteral("配置未启用真实 QMT 下单")
                          : QStringLiteral("record-only 模式禁止真实 QMT 下单");
    }
    return allowed;
}

void RedemptionEngine::handleQmtCommand(const QString &action,
                                        const QJsonObject &arguments,
                                        const QString &commandId) {
    const QString backend = arguments.value(QStringLiteral("backend")).toString();
    auto *client = qmtClients_.value(backend, nullptr);
    if (!client) {
        emit commandFinished(commandId, false, QStringLiteral("未知 QMT backend：%1").arg(backend), {});
        return;
    }
    if (action == QStringLiteral("redemption_qmt_connect")) {
        if (operatingMode_ == QStringLiteral("work")
            && !RedemptionCore::evaluateSchedule(nowUtc()).monitoringDesired) {
            emit commandFinished(commandId, false,
                QStringLiteral("当前为盘外休眠时段；临时连接请使用周末测试模式"), {});
            return;
        }
        pendingQmtConnect_.insert(backend, commandId);
        client->connectBackend();
    } else if (action == QStringLiteral("redemption_qmt_disconnect")) {
        pendingQmtDisconnect_.insert(backend, commandId);
        client->disconnectBackend();
    } else if (action == QStringLiteral("redemption_qmt_sync")) {
        pendingQmtSync_.insert(backend, commandId);
        client->requestSync();
    } else if (action == QStringLiteral("redemption_qmt_order")) {
        QString gateReason;
        if (!liveOrdersAllowed(&gateReason)) {
            emit commandFinished(commandId, false, gateReason,
                                 {{QStringLiteral("code"), QStringLiteral("live_order_gate_closed")},
                                  {QStringLiteral("compile_gate"), false},
                                  {QStringLiteral("config_gate"), context_.settings
                                       .value(QStringLiteral("live_qmt_orders_enabled")).toBool(false)},
                                  {QStringLiteral("record_only"), context_.recordOnly}});
            return;
        }
        QString error;
        if (!client->submitEtfOrder(arguments.value(QStringLiteral("symbol")).toString(),
                                    arguments.value(QStringLiteral("side")).toString(),
                                    commandId, &error)) {
            emit commandFinished(commandId, false, error, {});
        }
    }
}

void RedemptionEngine::submitCommand(const QString &action,
                                     const QJsonObject &arguments,
                                     const QString &commandId) {
    QString error;
    if (action == QStringLiteral("set_operating_mode")) {
        const QString mode = arguments.value(QStringLiteral("mode"))
                                 .toString().trimmed().toLower();
        if (mode != QStringLiteral("work")
            && mode != QStringLiteral("weekend_test")) {
            emit commandFinished(
                commandId, false,
                QStringLiteral("运行模式只能是 work 或 weekend_test"),
                {{QStringLiteral("code"), QStringLiteral("invalid_operating_mode")},
                 {QStringLiteral("operating_mode"), operatingMode_}});
            return;
        }
        operatingMode_ = mode;
        if (running_ && operatingMode_ == QStringLiteral("work")) {
            // Re-entering work mode immediately reapplies the production
            // boundary; it never starts an already stopped engine.
            evaluateSchedule();
        }
        const QString message = operatingMode_ == QStringLiteral("work")
            ? QStringLiteral("已恢复工作模式，生产时间边界生效")
            : QStringLiteral("已进入周末测试模式，仅允许手动操作，真实 QMT 下单仍被门禁禁止");
        emitStatus(message);
        publishSnapshot();
        emit commandFinished(commandId, true, message, snapshot());
        return;
    }
    static const QSet<QString> requiresRunning{
        QStringLiteral("redemption_monitor_start"),
        QStringLiteral("redemption_monitor_stop"),
        QStringLiteral("redemption_wind_start"),
        QStringLiteral("redemption_wind_shutdown_cleanup"),
        QStringLiteral("redemption_pcf_refresh"),
        QStringLiteral("redemption_qmt_connect"),
        QStringLiteral("redemption_qmt_disconnect"),
        QStringLiteral("redemption_qmt_sync"),
        QStringLiteral("redemption_qmt_order")};
    if (!running_ && requiresRunning.contains(action)) {
        emit commandFinished(commandId, false,
                             QStringLiteral("实时申购赎回原生模块已停止，请先在 UI 中启动模块"),
                             {{QStringLiteral("code"), QStringLiteral("module_stopped")}});
        return;
    }
    if (action == QStringLiteral("redemption_get_history")) {
        QDate day = QDate::fromString(arguments.value(QStringLiteral("date")).toString(), Qt::ISODate);
        if (!day.isValid()) day = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
        QString symbol;
        if (!arguments.value(QStringLiteral("symbol")).toString().trimmed().isEmpty()) {
            symbol = RedemptionCore::normalizeSymbol(arguments.value(QStringLiteral("symbol")).toString(), &error);
            if (symbol.isEmpty()) { emit commandFinished(commandId, false, error, {}); return; }
        }
        const QJsonObject result{{QStringLiteral("type"), QStringLiteral("history")},
                                 {QStringLiteral("protocol"), 1},
                                 {QStringLiteral("date"), day.toString(Qt::ISODate)},
                                 {QStringLiteral("symbol"), symbol.isEmpty() ? QString() : displaySymbol(symbol)},
                                 {QStringLiteral("items"), queryHistory(day, symbol,
                                      arguments.value(QStringLiteral("limit")).toInt(500))}};
        emit eventReady(QStringLiteral("redemption.history"), result);
        emit commandFinished(commandId, true, QStringLiteral("历史数据已返回"), result);
    } else if (action == QStringLiteral("redemption_get_pcf")) {
        const QString symbol = RedemptionCore::normalizeSymbol(arguments.value(QStringLiteral("symbol")).toString(), &error);
        if (symbol.isEmpty() || !states_.contains(symbol) || states_.value(symbol).pcf.isEmpty()) {
            emit commandFinished(commandId, false,
                                 error.isEmpty() ? QStringLiteral("PCF 尚未就绪") : error, {});
            return;
        }
        QJsonObject result = states_.value(symbol).pcf;
        result.insert(QStringLiteral("name"), states_.value(symbol).customName);
        result.insert(QStringLiteral("opportunity"), states_.value(symbol).opportunity);
        emit eventReady(QStringLiteral("redemption.pcf"), result);
        emit commandFinished(commandId, true, QStringLiteral("PCF 详情已返回"), result);
    } else if (action == QStringLiteral("redemption_set_watchlist")) {
        const bool ok = setWatchlist(arguments.value(QStringLiteral("symbols")).toArray(), &error);
        if (ok) publishSnapshot();
        emit commandFinished(commandId, ok, ok ? QStringLiteral("观察列表已更新") : error, snapshot());
    } else if (action == QStringLiteral("redemption_set_symbol_name")) {
        const bool ok = setSymbolName(arguments.value(QStringLiteral("symbol")).toString(),
                                      arguments.value(QStringLiteral("name")).toString(), &error);
        if (ok) publishSnapshot();
        emit commandFinished(commandId, ok, ok ? QStringLiteral("标的名称已更新") : error, snapshot());
    } else if (action == QStringLiteral("redemption_monitor_start")) {
        if (operatingMode_ == QStringLiteral("work")
            && !RedemptionCore::evaluateSchedule(nowUtc()).monitoringDesired) {
            emit commandFinished(commandId, false,
                QStringLiteral("当前为盘外休眠时段；临时采集请使用周末测试模式"), snapshot());
            return;
        }
        startMonitoring(QStringLiteral("manual"));
        const bool accepted = monitoring_ || monitoringRequested_ || monitoringWarmupPending_;
        emit commandFinished(commandId, accepted,
                             monitoring_ ? QStringLiteral("原生监控已启动")
                                         : monitoringRequested_
                                             ? QStringLiteral("原生 Wind 订阅请求已提交")
                                             : monitoringWarmupPending_
                                                 ? QStringLiteral("TBAPI 临时预热已提交，确认后将等待 2 秒再订阅")
                                             : lastError_, snapshot());
    } else if (action == QStringLiteral("redemption_monitor_stop")) {
        stopMonitoring(QStringLiteral("manual"), commandId);
    } else if (action == QStringLiteral("redemption_wind_start")) {
        if (helper_.state() != QProcess::Running || !helperReady_) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("Wind 探针尚未在线，启动请求未发送"),
                                 snapshot());
            return;
        }
        if (!context_.settings.value(QStringLiteral("wind_control_enabled")).toBool(false)) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("Wind 启停控制未在配置中启用"), snapshot());
            return;
        }
        sendHelperCommand(QStringLiteral("start_wind"),
                          {{QStringLiteral("request_id"), commandId}});
    } else if (action == QStringLiteral("redemption_wind_shutdown_cleanup")) {
        if (helper_.state() != QProcess::Running || !helperReady_) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("Wind 探针尚未在线，退出请求未发送"),
                                 snapshot());
            return;
        }
        if (!context_.settings.value(QStringLiteral("wind_control_enabled")).toBool(false)) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("Wind 启停控制未在配置中启用"), snapshot());
            return;
        }
        monitoringWarmupPending_ = false;
        sendHelperCommand(QStringLiteral("shutdown_wind"),
                          {{QStringLiteral("request_id"), commandId}});
    } else if (action == QStringLiteral("redemption_pcf_refresh")) {
        if (!context_.settings.value(QStringLiteral("pcf_network_enabled")).toBool(false)) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("PCF 网络采集未启用"), snapshot());
            return;
        }
        fetchNextPcf(true);
        emit commandFinished(commandId, true, QStringLiteral("已进入 PCF 串行刷新队列"), snapshot());
    } else if (action.startsWith(QStringLiteral("redemption_qmt_"))) {
        handleQmtCommand(action, arguments, commandId);
    } else if (action == QStringLiteral("redemption_ingest_fixture")
               && context_.settings.value(QStringLiteral("test_mode")).toBool(false)) {
        const bool ok = ingestCapture(arguments, true, &error);
        emit commandFinished(commandId, ok, ok ? QStringLiteral("fixture 已回放") : error, snapshot());
    } else {
        emit commandFinished(commandId, false, QStringLiteral("不支持的原生申赎动作：%1").arg(action), {});
    }
}

void RedemptionEngine::startWindHelper() {
    const QString mode = context_.settings.value(QStringLiteral("wind_helper_mode"))
                             .toString(QStringLiteral("disabled"));
    if (mode == QStringLiteral("disabled")) {
        helperState_ = QStringLiteral("disabled");
        return;
    }
    QString path = context_.settings.value(QStringLiteral("wind_helper_path")).toString();
    if (path.isEmpty()) {
        path = QDir(QCoreApplication::applicationDirPath())
                   .filePath(QStringLiteral("../Helpers/machome-wind-probe-helper"));
    }
    QFileInfo helperInfo(path);
    if (!helperInfo.isAbsolute() || helperInfo.isSymLink() || !helperInfo.isExecutable()) {
        helperState_ = QStringLiteral("blocked");
        helperLastError_ = QStringLiteral("包内 Wind helper 不存在、不可执行或是符号链接");
        return;
    }
    helper_.setProgram(path);
    QStringList arguments{QStringLiteral("--stdio"), QStringLiteral("--mode"), mode,
                          QStringLiteral("--data-root"), context_.dataRoot};
    // Starting/stopping Wind and managing its read-only subscription is an
    // operator lifecycle action, not a QMT order or outbound notification.
    // Keep those mutations behind their independent gates while allowing a
    // record-only RedemptionEngine to connect to its live read source.
    if (context_.settings.value(QStringLiteral("wind_control_enabled")).toBool(false)) {
        arguments.append(QStringLiteral("--allow-wind-control"));
    }
    helper_.setArguments(arguments);
    helper_.setProcessChannelMode(QProcess::SeparateChannels);
    helper_.start();
    helperState_ = QStringLiteral("starting");
}

void RedemptionEngine::stopWindHelper() {
    if (helper_.state() == QProcess::NotRunning) return;
    sendHelperCommand(QStringLiteral("quit"));
    helper_.closeWriteChannel();
    if (!helper_.waitForFinished(50'000)) {
        helper_.terminate();
        helper_.waitForFinished(3000);
    }
    helperReady_ = false;
    helperState_ = QStringLiteral("stopped");
}

void RedemptionEngine::sendHelperCommand(const QString &action,
                                         const QJsonObject &arguments) {
    if (helper_.state() != QProcess::Running) return;
    QJsonObject command = arguments;
    command.insert(QStringLiteral("action"), action);
    QByteArray wire = QJsonDocument(command).toJson(QJsonDocument::Compact);
    wire.append('\n');
    helper_.write(wire);
}

void RedemptionEngine::readHelperOutput() {
    while (helper_.bytesAvailable()>0) {
        helperBuffer_.append(helper_.read(qMin<qint64>(64*1024,helper_.bytesAvailable())));
        if (helperBuffer_.size()>2*1024*1024) {
            helperBuffer_.clear();
            helperLastError_=QStringLiteral("Wind helper 输出超过安全上限");
            helper_.terminate(); return;
        }
        if(helperBuffer_.contains('\n')) break;
    }
    while (true) {
        const qsizetype newline = helperBuffer_.indexOf('\n');
        if (newline < 0) break;
        const QByteArray line = helperBuffer_.left(newline).trimmed();
        helperBuffer_.remove(0, newline + 1);
        if (line.isEmpty()) continue;
        if (line.size() > 2 * 1024 * 1024) {
            helperLastError_ = QStringLiteral("Wind helper 输出超过安全上限");
            helper_.terminate();
            return;
        }
        const QJsonDocument document = QJsonDocument::fromJson(line);
        if (!document.isObject()) {
            helperLastError_ = QStringLiteral("Wind helper 返回损坏的 NDJSON");
            continue;
        }
        handleHelperMessage(document.object());
    }
    if(helper_.bytesAvailable()>0) QTimer::singleShot(0,this,&RedemptionEngine::readHelperOutput);
}

void RedemptionEngine::handleHelperMessage(const QJsonObject &message) {
    const QString type = message.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("hello")) {
        helperReady_ = message.value(QStringLiteral("protocol")).toInt() == 1;
        helperState_ = helperReady_ ? QStringLiteral("ready") : QStringLiteral("blocked");
    } else if (type == QStringLiteral("status")) {
        // Some acknowledged transitions intentionally report only the field
        // they changed (for example `unsubscribed`). Missing booleans must not
        // be interpreted as proof that the Wind process disappeared.
        if (message.contains(QStringLiteral("wind_running")))
            windRunning_ = message.value(QStringLiteral("wind_running")).toBool();
        if (message.contains(QStringLiteral("tbapi_loaded")))
            tbapiReady_ = message.value(QStringLiteral("tbapi_loaded")).toBool();
        helperState_ = message.value(QStringLiteral("state")).toString(helperState_);
        if (helperState_ == QStringLiteral("subscribed")
            && operatingMode_ == QStringLiteral("work")
            && !RedemptionCore::evaluateSchedule(nowUtc()).monitoringDesired) {
            // Late subscribe acknowledgement: send an actual unsubscribe,
            // even if the close transition already cleared our local flags.
            monitoringRequested_ = true;
            stopMonitoring(QStringLiteral("盘外迟到订阅已停订"));
            return;
        }
        if (helperState_ == QStringLiteral("subscribed") && monitoringRequested_) {
            monitoringRequested_ = false;
            monitoring_ = true;
            state_ = QStringLiteral("active");
            lastError_.clear();
            for (auto it = states_.begin(); it != states_.end(); ++it)
                it->status = QStringLiteral("monitoring");
            emitStatus(QStringLiteral("Wind TBAPI2 原生订阅已确认"));
        }
        if (message.value(QStringLiteral("unsubscribed")).toBool(false)) {
            monitoring_ = false;
            monitoringRequested_ = false;
            state_ = QStringLiteral("scheduled_idle");
            for (auto it = states_.begin(); it != states_.end(); ++it) {
                it->status = QStringLiteral("stopped");
                it->subId = -1;
            }
        }
    } else if (type == QStringLiteral("command_result")) {
        const QString action = message.value(QStringLiteral("action")).toString();
        const QString requestId = message.value(QStringLiteral("request_id")).toString();
        const bool ok = message.value(QStringLiteral("ok")).toBool(false);
        const QJsonObject details = message.value(QStringLiteral("details")).toObject();
        if (action == QStringLiteral("warmup")) {
            if (ok && details.value(QStringLiteral("warmup_completed")).toBool(false)
                && details.value(QStringLiteral("unsubscribe_confirmed")).toBool(false)) {
                warmupDay_ = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
                helperLastError_.clear();
                QTimer::singleShot(2000, this, &RedemptionEngine::finishWarmupSettle);
            } else {
                monitoringWarmupPending_ = false;
                pendingMonitoringReason_.clear();
                state_ = QStringLiteral("degraded");
            }
        } else if (action == QStringLiteral("shutdown_wind") && ok) {
            monitoring_ = false;
            monitoringRequested_ = false;
            monitoringWarmupPending_ = false;
            pendingMonitoringReason_.clear();
            warmupDay_ = {};
            warmupAttemptDay_ = {};
            warmupSettleElapsed_ = false;
            state_ = QStringLiteral("scheduled_idle");
            for (auto it = states_.begin(); it != states_.end(); ++it) {
                it->status = QStringLiteral("stopped");
                it->subId = -1;
            }
            windRunning_ = false;
            tbapiReady_ = false;
            helperState_ = QStringLiteral("cleaned");
            lastError_.clear();
            helperLastError_.clear();
            lastWindCleanupAtUtc_ = nowUtc();
            windCleanupDeletedCount_ = details.value(QStringLiteral("cleanup_deleted_count")).toInt(-1);
        }
        if (!requestId.isEmpty()) {
            emit commandFinished(requestId, ok,
                                 message.value(QStringLiteral("message")).toString(),
                                 details.isEmpty() ? snapshot() : details);
        }
    } else if (type == QStringLiteral("capture")) {
        QString error;
        if (!ingestCapture(message.value(QStringLiteral("payload")).toObject(), false, &error)) {
            helperLastError_ = error;
        }
    } else if (type == QStringLiteral("error")) {
        const QString detail = message.value(QStringLiteral("message")).toString();
        const QDate day = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
        // The original host consumes Wind's first-call lazy-initialization
        // null fault once. The helper has already resumed Wind after lldb
        // exits. Formal subscription must still receive its normal ACK.
        static const QRegularExpression nullAddress(QStringLiteral("address\\s*=\\s*0x0+\\b"));
        if (message.value(QStringLiteral("action")).toString() == QStringLiteral("warmup")
            && message.value(QStringLiteral("code")).toString() == QStringLiteral("warmup_subscribe_failed")
            && detail.contains(QStringLiteral("EXC_BAD_ACCESS"))
            && nullAddress.match(detail).hasMatch()
            && warmupAttemptDay_ == day && warmupDay_ != day && windCollectionReady()) {
            warmupDay_ = day;
            helperLastError_.clear();
            lastError_.clear();
            helperState_ = QStringLiteral("ready");
            emitStatus(QStringLiteral("已处理 Wind 首次初始化空地址异常，等待 2 秒后确认正式订阅"));
            QTimer::singleShot(2000, this, &RedemptionEngine::finishWarmupSettle);
            publishSnapshot();
            return;
        }
        helperLastError_ = message.value(QStringLiteral("message")).toString();
        helperState_ = QStringLiteral("degraded");
        if (monitoringRequested_) {
            monitoringRequested_ = false;
            monitoring_ = false;
            state_ = QStringLiteral("degraded");
            lastError_ = helperLastError_;
        }
        if (message.value(QStringLiteral("action")).toString() == QStringLiteral("warmup")) {
            monitoringWarmupPending_ = false;
            pendingMonitoringReason_.clear();
        }
        const QString requestId = message.value(QStringLiteral("request_id")).toString();
        if (!requestId.isEmpty()) {
            emit commandFinished(requestId, false, helperLastError_, message);
        }
        emit eventReady(QStringLiteral("redemption.wind_error"), message);
    }
    publishSnapshot();
}

void RedemptionEngine::helperFinished(int exitCode, QProcess::ExitStatus status) {
    helperReady_ = false;
    monitoringRequested_ = false;
    monitoringWarmupPending_ = false;
    warmupDay_ = {};
    warmupAttemptDay_ = {};
    warmupSettleElapsed_ = false;
    monitoring_ = false;
    if (running_) {
        helperState_ = QStringLiteral("degraded");
        helperLastError_ = QStringLiteral("Wind helper 异常退出：%1/%2")
            .arg(exitCode).arg(static_cast<int>(status));
        emit eventReady(QStringLiteral("redemption.wind_error"),
                        {{QStringLiteral("message"), helperLastError_},
                         {QStringLiteral("isolated"), true}});
    }
    publishSnapshot();
}

void RedemptionEngine::helperError(QProcess::ProcessError error) {
    helperReady_ = false;
    monitoringRequested_ = false;
    monitoring_ = false;
    helperState_ = QStringLiteral("degraded");
    helperLastError_ = QStringLiteral("Wind helper 进程错误：%1").arg(static_cast<int>(error));
    publishSnapshot();
}

void RedemptionEngine::pcfTick() {
    if (operatingMode_ != QStringLiteral("work")) return;
    const ScheduleDecision decision = RedemptionCore::evaluateSchedule(nowUtc());
    if (decision.pcfWindow) fetchNextPcf(false);
}

void RedemptionEngine::fetchNextPcf(bool force) {
    if (!running_ || !context_.settings.value(QStringLiteral("pcf_network_enabled")).toBool(false)) return;
    pcfRefreshPending_=true;
    if (force) {
        for(const auto &symbol:watchlist_) {
            if(!activePcfReply_ || activePcfReply_->property("symbol").toString()!=symbol)
                pcfForcedSymbols_.insert(symbol);
        }
    }
    if (activePcfReply_) return;
    const QDateTime now = nowUtc();
    const QDateTime gate=qMax(pcfNextAllowedUtc_,pcfBlockedUntilUtc_);
    if (gate>now) {
        pcfGateTimer_.start(int(qBound<qint64>(1LL,now.msecsTo(gate),600'000LL)));
        return;
    }
    pcfRefreshPending_=false;
    const QDate day = now.toTimeZone(QTimeZone("Asia/Shanghai")).date();
    for (const QString &symbol : watchlist_) {
        PcfRuntime &runtime = pcfRuntime_[symbol];
        if (runtime.day != day) { runtime = {}; runtime.day = day; }
        const bool cached = states_.value(symbol).pcf.value(QStringLiteral("trading_day")).toString()
                            == day.toString(Qt::ISODate);
        const bool forced=pcfForcedSymbols_.contains(symbol);
        if (!forced && cached) continue;
        if (runtime.inFlight || runtime.attempts >= 8 || runtime.cooldownUntilUtc > now) continue;
        if (!forced && runtime.lastAttemptUtc.isValid()
            && runtime.lastAttemptUtc.secsTo(now) < 900) continue;
        QStringList patterns;
        const QString configured = context_.settings.value(QStringLiteral("pcf_url_template")).toString();
        if (!configured.isEmpty()) patterns.append(configured);
        else patterns.append(QStringLiteral(
            "https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{symbol}_{date}.xml"));
        patterns.append(QStringLiteral(
            "https://reportdocs.static.szse.cn/files/text/ETFDown/{symbol}ETF{date}.xml"));
        runtime.candidateIndex = qBound(0, runtime.candidateIndex, patterns.size() - 1);
        QString pattern = patterns.at(runtime.candidateIndex);
        pattern.replace(QStringLiteral("{symbol}"), displaySymbol(symbol));
        pattern.replace(QStringLiteral("{date}"), day.toString(QStringLiteral("yyyyMMdd")));
        QNetworkRequest request{QUrl(pattern)};
        request.setHeader(QNetworkRequest::UserAgentHeader,
                          QStringLiteral("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                                         "Chrome/126.0.0.0 Safari/537.36"));
        runtime.inFlight = true;
        runtime.lastAttemptUtc = now;
        if (runtime.candidateIndex == 0) ++runtime.attempts;
        pcfNextAllowedUtc_=now.addSecs(8);
        if (!persistPcfGate()) {runtime.inFlight=false;return;}
        QNetworkReply *reply = network_.get(request);
        activePcfReply_=reply;
        pcfForcedSymbols_.remove(symbol);
        reply->setReadBufferSize(8*1024*1024+1);
        connect(reply,&QNetworkReply::readyRead,this,[reply] {
            if(reply->bytesAvailable()>8*1024*1024) reply->abort();
        });
        reply->setProperty("symbol", symbol);
        reply->setProperty("force", force);
        reply->setProperty("candidate_index", runtime.candidateIndex);
        reply->setProperty("started_ms", now.toMSecsSinceEpoch());
        QTimer::singleShot(20'000, reply, [reply] {
            if (reply->isRunning()) reply->abort();
        });
        break; // one exchange request at a time
    }
}

QJsonObject RedemptionEngine::parsePcfResponse(const QByteArray &body,
                                               const QString &symbol,
                                               QString *error) const {
    QJsonDocument document = QJsonDocument::fromJson(body);
    if (document.isObject()) return document.object();
    QXmlStreamReader xml(body);
    QJsonObject metadata;
    QJsonArray components;
    QJsonObject component;
    int componentDepth = -1;
    QStringList elementStack;
    QStringList textStack;
    while (!xml.atEnd()) {
        xml.readNext();
        if (xml.isStartElement()) {
            const QString name = xml.name().toString();
            elementStack.append(name);
            textStack.append(QString());
            if (name.compare(QStringLiteral("Component"), Qt::CaseInsensitive) == 0
                || name.compare(QStringLiteral("ComponentRecord"), Qt::CaseInsensitive) == 0) {
                componentDepth = elementStack.size();
                component = {};
            }
        } else if (xml.isCharacters() && !textStack.isEmpty()) {
            textStack.last().append(xml.text().toString());
        } else if (xml.isEndElement() && !elementStack.isEmpty()) {
            const QString name = elementStack.takeLast();
            const QString value = textStack.takeLast().trimmed();
            const bool wrapper = name.compare(QStringLiteral("Component"), Qt::CaseInsensitive) == 0
                || name.compare(QStringLiteral("ComponentRecord"), Qt::CaseInsensitive) == 0;
            if (!value.isEmpty() && !wrapper) {
                if (componentDepth > 0 && elementStack.size() + 1 > componentDepth) {
                    component.insert(name, value);
                } else {
                    metadata.insert(name, value);
                }
            }
            if (wrapper && componentDepth == elementStack.size() + 1) {
                if (!component.isEmpty()) components.append(component);
                component = {};
                componentDepth = -1;
            }
        }
    }
    if (xml.hasError()) {
        if (error) *error = QStringLiteral("PCF XML 损坏：%1").arg(xml.errorString());
        return {};
    }
    const QString tradingRaw = metadata.value(QStringLiteral("TradingDay")).toString();
    QString tradingDay = tradingRaw;
    tradingDay.remove(u'-');
    if (tradingDay.size() == 8) tradingDay = QStringLiteral("%1-%2-%3")
        .arg(tradingDay.first(4), tradingDay.mid(4, 2), tradingDay.last(2));
    QJsonArray summaryFields;
    for (auto it = metadata.constBegin(); it != metadata.constEnd(); ++it) {
        summaryFields.append(QJsonObject{{QStringLiteral("field"), it.key()},
                                         {QStringLiteral("label"), it.key()},
                                         {QStringLiteral("value"), it.value()}});
    }
    QJsonArray componentColumns;
    QStringList seenColumns;
    for (const QJsonValue &value : components) {
        const QJsonObject row = value.toObject();
        for (auto it = row.constBegin(); it != row.constEnd(); ++it) {
            if (seenColumns.contains(it.key())) continue;
            seenColumns.append(it.key());
            componentColumns.append(QJsonObject{{QStringLiteral("field"), it.key()},
                                                 {QStringLiteral("label"), it.key()}});
        }
    }
    QJsonObject result{{QStringLiteral("symbol"), displaySymbol(symbol)},
                       {QStringLiteral("fund_name"), metadata.value(QStringLiteral("Symbol"))},
                       {QStringLiteral("trading_day"), tradingDay},
                       {QStringLiteral("creation_redemption_unit"), parsedNumber(metadata.value(QStringLiteral("CreationRedemptionUnit")).toString())},
                       {QStringLiteral("components"), components},
                       {QStringLiteral("component_columns"), componentColumns},
                       {QStringLiteral("summary_fields"), summaryFields}};
    const QString creation = boolText(metadata.value(QStringLiteral("Creation")).toString());
    const QString redemption = boolText(metadata.value(QStringLiteral("Redemption")).toString());
    if (!creation.isEmpty()) result.insert(QStringLiteral("creation_allowed"), creation == QStringLiteral("true"));
    if (!redemption.isEmpty()) result.insert(QStringLiteral("redemption_allowed"), redemption == QStringLiteral("true"));
    for (const auto &entry : {qMakePair(QStringLiteral("NetCreationLimit"), QStringLiteral("net_creation_limit")),
                              qMakePair(QStringLiteral("NetRedemptionLimit"), QStringLiteral("net_redemption_limit")),
                              qMakePair(QStringLiteral("CreationLimit"), QStringLiteral("creation_limit")),
                              qMakePair(QStringLiteral("RedemptionLimit"), QStringLiteral("redemption_limit"))}) {
        const QJsonValue value = parsedNumber(metadata.value(entry.first).toString());
        if (!value.isNull()) result.insert(entry.second, value);
    }
    return result;
}

QJsonObject RedemptionEngine::parsePcfForTest(const QByteArray &body,
                                              const QString &symbol,
                                              QString *error) const {
    return parsePcfResponse(body, symbol, error);
}

void RedemptionEngine::pcfFinished(QNetworkReply *reply) {
    const QString symbol = reply->property("symbol").toString();
    if (activePcfReply_ == reply) activePcfReply_.clear();
    PcfRuntime &runtime = pcfRuntime_[symbol];
    runtime.inFlight = false;
    pcfNextAllowedUtc_=qMax(pcfNextAllowedUtc_,nowUtc().addSecs(8));
    if (!running_) {persistPcfGate();reply->deleteLater();return;}
    const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    if (reply->error() != QNetworkReply::NoError) {
        runtime.lastError = reply->errorString();
        if ((status == 404 || status == 410) && runtime.candidateIndex == 0
            && context_.settings.value(QStringLiteral("pcf_url_template")).toString().isEmpty()) {
            runtime.candidateIndex = 1;
            pcfFailureCount_=0;pcfBlockedUntilUtc_={};
            runtime.lastAttemptUtc = {};
            persistPcfGate();
            reply->deleteLater();
            fetchNextPcf(false);
            return;
        }
        runtime.candidateIndex = 0;
        if(status==404||status==410)pcfFailureCount_=0;else ++pcfFailureCount_;
        const int cooldown = (status == 403 || status == 429 || status == 503) ? 600 : (pcfFailureCount_>=2?180:0);
        runtime.cooldownUntilUtc = nowUtc().addSecs(cooldown);
        pcfBlockedUntilUtc_=runtime.cooldownUntilUtc;
        emit eventReady(QStringLiteral("redemption.pcf_error"),
                        {{QStringLiteral("symbol"), displaySymbol(symbol)},
                         {QStringLiteral("http_status"), status},
                         {QStringLiteral("cooldown_seconds"), cooldown},
                         {QStringLiteral("message"), runtime.lastError}});
    } else {
        pcfFailureCount_=0;pcfBlockedUntilUtc_={};
        QString error;
        const QJsonObject payload = parsePcfResponse(reply->readAll(), symbol, &error);
        if (payload.isEmpty() || !installPcf(symbol, payload, true, &error)) {
            runtime.lastError = error;
        } else {
            runtime.candidateIndex = 0;
            runtime.lastError.clear();
            emit eventReady(QStringLiteral("redemption.pcf"), states_.value(symbol).pcf);
        }
    }
    reply->deleteLater();
    publishSnapshot();
    persistPcfGate();
    fetchNextPcf(false);
}

void RedemptionEngine::startCompatibilityServer() {
    if (!context_.settings.value(QStringLiteral("compatibility_api_enabled")).toBool(false)) return;
    const quint16 port = static_cast<quint16>(context_.settings
        .value(QStringLiteral("compatibility_port")).toInt(6787));
    const bool exposeLan = context_.settings.value(QStringLiteral("compatibility_expose_lan")).toBool(false);
    if (!compatibilityTcp_.listen(exposeLan ? QHostAddress::AnyIPv4 : QHostAddress::LocalHost, port)) {
        lastError_ = QStringLiteral("6787 兼容只读服务启动失败：%1").arg(compatibilityTcp_.errorString());
    }
}

void RedemptionEngine::stopCompatibilityServer() {
    compatibilityTcp_.close();
    for (auto socket : std::as_const(webSockets_)) if (socket) socket->close();
    webSockets_.clear();
}

void RedemptionEngine::acceptCompatibilityConnection() {
    while (QTcpSocket *socket = compatibilityTcp_.nextPendingConnection()) {
        socket->setReadBufferSize(16*1024+1);
        connect(socket,&QTcpSocket::disconnected,socket,&QObject::deleteLater);
        QTimer::singleShot(10'000,socket,[socket] {
            if (!socket->property("handled").toBool()) socket->abort();
        });
        connect(socket, &QTcpSocket::readyRead, this, [this, socket] {
            if (socket->property("handled").toBool()) return;
            if (socket->bytesAvailable()>16*1024) {socket->abort();return;}
            const QByteArray peek = socket->peek(16 * 1024);
            if (!peek.contains("\r\n\r\n")) {
                if (peek.size() >= 16 * 1024) socket->disconnectFromHost();
                return;
            }
            socket->setProperty("handled", true);
            if (peek.toLower().contains("upgrade: websocket")) {
                disconnect(socket,&QTcpSocket::disconnected,socket,&QObject::deleteLater);
                socket->setParent(nullptr);
                compatibilityWs_->handleConnection(socket);
            } else {
                handleHttpRequest(socket);
            }
        });
    }
}

void RedemptionEngine::acceptWebSocket() {
    while (QWebSocket *socket = compatibilityWs_->nextPendingConnection()) {
        const QUrl request = socket->requestUrl();
        if (request.path() != QStringLiteral("/ws/v1/changes")) {
            socket->close(QWebSocketProtocol::CloseCodePolicyViolated,
                          QStringLiteral("read-only endpoint only"));
            socket->deleteLater();
            continue;
        }
        socket->setMaxAllowedIncomingFrameSize(64*1024);
        socket->setMaxAllowedIncomingMessageSize(64*1024);
        webSockets_.append(socket);
        connect(socket,&QWebSocket::textMessageReceived,this,[socket](const QString &text) {
            if(text==QStringLiteral("ping") && socket->bytesToWrite()<256*1024)
                socket->sendTextMessage(QStringLiteral("{\"type\":\"pong\"}"));
        });
        connect(socket, &QWebSocket::disconnected, this, [this, socket] {
            webSockets_.removeAll(socket); socket->deleteLater();
        });
        socket->sendTextMessage(QString::fromUtf8(
            QJsonDocument(snapshot()).toJson(QJsonDocument::Compact)));
    }
}

void RedemptionEngine::broadcastCompat(const QJsonObject &message) {
    const QString wire = QString::fromUtf8(QJsonDocument(message).toJson(QJsonDocument::Compact));
    const auto clients=webSockets_;
    for (auto socket : clients) if (socket) {
        if (socket->bytesToWrite()+wire.size()*3>1024*1024) {
            socket->abort(); // critical history remains in SQLite and reconnect snapshot
        } else socket->sendTextMessage(wire);
    }
}

void RedemptionEngine::writeHttp(QTcpSocket *socket, int status,
                                 const QJsonObject &body) const {
    const QByteArray payload = QJsonDocument(body).toJson(QJsonDocument::Compact);
    const QByteArray phrase = status == 200 ? "OK" : status == 403 ? "Forbidden" : "Not Found";
    socket->write("HTTP/1.1 " + QByteArray::number(status) + " " + phrase + "\r\n"
                  "Content-Type: application/json; charset=utf-8\r\n"
                  "Cache-Control: no-store\r\nConnection: close\r\n"
                  "Content-Length: " + QByteArray::number(payload.size()) + "\r\n\r\n" + payload);
    socket->disconnectFromHost();
}

void RedemptionEngine::handleHttpRequest(QTcpSocket *socket) {
    const QByteArray request = socket->readAll();
    const qsizetype firstNewline = request.indexOf('\n');
    const QByteArray requestLine = firstNewline < 0 ? request : request.left(firstNewline);
    const QList<QByteArray> parts = requestLine.trimmed().split(' ');
    if (parts.size() < 2 || parts.first() != "GET") {
        writeHttp(socket, 403, {{QStringLiteral("error"), QStringLiteral("compatibility API is read-only")}});
        return;
    }
    const QUrl url(QString::fromUtf8(parts.at(1)));
    const QString path = url.path();
    if (path == QStringLiteral("/api/v1/health")) writeHttp(socket, 200, healthSnapshot());
    else if (path == QStringLiteral("/api/v1/snapshot")) writeHttp(socket, 200, snapshot());
    else if (path == QStringLiteral("/api/v1/wind/status"))
        writeHttp(socket, 200, snapshot().value(QStringLiteral("wind")).toObject());
    else if (path == QStringLiteral("/api/v1/watchlist")) {
        QStringList values; for (const QString &s : watchlist_) values.append(displaySymbol(s));
        writeHttp(socket, 200, {{QStringLiteral("symbols"), QJsonArray::fromStringList(values)}});
    } else if (path == QStringLiteral("/api/v1/history")) {
        const QUrlQuery query(url);
        QDate day = QDate::fromString(query.queryItemValue(QStringLiteral("date")), Qt::ISODate);
        if (!day.isValid()) day = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date();
        QString symbol;
        QString error;
        if (!query.queryItemValue(QStringLiteral("symbol")).isEmpty())
            symbol = RedemptionCore::normalizeSymbol(query.queryItemValue(QStringLiteral("symbol")), &error);
        writeHttp(socket, 200, {{QStringLiteral("type"), QStringLiteral("history")},
                                {QStringLiteral("protocol"), 1},
                                {QStringLiteral("date"), day.toString(Qt::ISODate)},
                                {QStringLiteral("items"), queryHistory(day, symbol,
                                    query.queryItemValue(QStringLiteral("limit")).toInt())}});
    } else if (path == QStringLiteral("/api/v1/pcf")) {
        QJsonArray items;
        for (const QString &symbol : watchlist_) {
            if (!states_.value(symbol).pcf.isEmpty()) items.append(states_.value(symbol).pcf);
        }
        writeHttp(socket, 200, {{QStringLiteral("items"), items}});
    } else if (path.startsWith(QStringLiteral("/api/v1/pcf/"))) {
        QString error;
        const QString symbol = RedemptionCore::normalizeSymbol(path.section(u'/', -1), &error);
        if (!states_.contains(symbol) || states_.value(symbol).pcf.isEmpty())
            writeHttp(socket, 404, {{QStringLiteral("error"), QStringLiteral("PCF 尚未就绪")}});
        else writeHttp(socket, 200, states_.value(symbol).pcf);
    } else writeHttp(socket, 404, {{QStringLiteral("error"), QStringLiteral("not found")}});
}

} // namespace machome::redemption
