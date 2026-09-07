#include "agent/HubAgent.h"

#include "agent/AuditWriter.h"
#include "agent/CommandJournal.h"
#include "agent/ModuleWorker.h"
#include "common/FrameCodec.h"
#include "common/JsonUtil.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDate>
#include <QDateTime>
#include <QDeadlineTimer>
#include <QDir>
#include <QEvent>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMetaObject>
#include <QRegularExpression>
#include <QSet>
#include <QSysInfo>
#include <algorithm>
#include <utility>
#include <unistd.h>
#ifdef Q_OS_MACOS
#include <sys/ucred.h>
#endif

namespace hub {
namespace {

QString fileSha256(const QString &path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    QCryptographicHash hash(QCryptographicHash::Sha256);
    if (!hash.addData(&file)) return {};
    return QString::fromLatin1(hash.result().toHex());
}

bool revisionRequired(const QString &action) {
    static const QSet<QString> readOnly{
        QStringLiteral("refresh"),
        QStringLiteral("premium_sync"), QStringLiteral("premium_raw_snapshot"),
        QStringLiteral("premium_detail_subscribe"),
        QStringLiteral("premium_detail_unsubscribe"),
        QStringLiteral("webull_get_book"), QStringLiteral("redemption_get_history"),
        QStringLiteral("redemption_get_pcf")};
    return !readOnly.contains(action);
}

QByteArray commandFingerprint(const QJsonObject &message) {
    QJsonObject stable{{QStringLiteral("module_id"), message.value(QStringLiteral("module_id"))},
                       {QStringLiteral("action"), message.value(QStringLiteral("action"))},
                       {QStringLiteral("arguments"), message.value(QStringLiteral("arguments"))},
                       {QStringLiteral("expected_revision"), message.value(QStringLiteral("expected_revision"))},
                       {QStringLiteral("requested_at"), message.value(QStringLiteral("requested_at"))},
                       {QStringLiteral("requested_by"), message.value(QStringLiteral("requested_by"))},
                       {QStringLiteral("reason"), message.value(QStringLiteral("reason"))},
                       {QStringLiteral("deadline_ms"), message.value(QStringLiteral("deadline_ms"))}};
    return QCryptographicHash::hash(QJsonDocument(stable).toJson(QJsonDocument::Compact),
                                    QCryptographicHash::Sha256);
}

bool validIsoTimestamp(const QString &value) {
    return !value.isEmpty() && QDateTime::fromString(value, Qt::ISODateWithMs).isValid();
}

QByteArray approvalFingerprint(const QJsonObject &message) {
    const QJsonObject stable{
        {QStringLiteral("module_id"), message.value(QStringLiteral("module_id"))},
        {QStringLiteral("action"), message.value(QStringLiteral("action"))},
        {QStringLiteral("arguments"), message.value(QStringLiteral("arguments"))},
        {QStringLiteral("expected_revision"), message.value(QStringLiteral("expected_revision"))},
        {QStringLiteral("requested_by"), message.value(QStringLiteral("requested_by"))},
        {QStringLiteral("reason"), message.value(QStringLiteral("reason"))},
        {QStringLiteral("agent_instance_id"), message.value(QStringLiteral("agent_instance_id"))},
        {QStringLiteral("config_sha256"), message.value(QStringLiteral("config_sha256"))}};
    return QCryptographicHash::hash(QJsonDocument(stable).toJson(QJsonDocument::Compact),
                                    QCryptographicHash::Sha256);
}

bool uncertainResult(const QJsonObject &result) {
    return result.value(QStringLiteral("state")).toString() == QStringLiteral("timed_out")
        || result.value(QStringLiteral("details")).toObject()
               .value(QStringLiteral("outcome_uncertain")).toBool(false);
}

bool onlyKeys(const QJsonObject &arguments, const QSet<QString> &allowed) {
    for (auto it = arguments.begin(); it != arguments.end(); ++it) {
        if (!allowed.contains(it.key())) return false;
    }
    return true;
}

bool validEtfSymbol(const QString &value, bool suffixRequired = false) {
    static const QRegularExpression plain(QStringLiteral("^[0-9]{6}(\\.(SH|SZ))?$"));
    static const QRegularExpression suffixed(QStringLiteral("^[0-9]{6}\\.(SH|SZ)$"));
    return (suffixRequired ? suffixed : plain).match(value.trimmed().toUpper()).hasMatch();
}

bool validPremiumHotSymbol(const QString &value) {
    static const QRegularExpression pattern(
        QStringLiteral("^(?:[0-9]{6}\\.(?:SH|SZ)|[0-9]{5}\\.HK)$"));
    return pattern.match(value.trimmed().toUpper()).hasMatch();
}

bool validateActionArguments(const ModuleConfig &module, const QString &action,
                             const QJsonObject &arguments, QString *error) {
    if (QJsonDocument(arguments).toJson(QJsonDocument::Compact).size() > 64 * 1024) {
        if (error) *error = QStringLiteral("arguments 超过 64 KiB");
        return false;
    }
    const QSet<QString> noArguments{
        QStringLiteral("refresh"), QStringLiteral("open_legacy_ui"),
        QStringLiteral("premium_sync"),
        QStringLiteral("premium_raw_snapshot"), QStringLiteral("webull_collector_start"),
        QStringLiteral("webull_collector_stop"), QStringLiteral("webull_restart_browser"),
        QStringLiteral("webull_show_login"), QStringLiteral("redemption_monitor_start"),
        QStringLiteral("redemption_monitor_stop"), QStringLiteral("redemption_wind_start"),
        QStringLiteral("redemption_wind_shutdown_cleanup"),
        QStringLiteral("redemption_pcf_refresh")};
    if (noArguments.contains(action)) {
        if (arguments.isEmpty()) return true;
        if (error) *error = QStringLiteral("动作 %1 不接受参数").arg(action);
        return false;
    }
    if (action == QStringLiteral("set_operating_mode")) {
        const QString mode = arguments.value(QStringLiteral("mode")).toString();
        const bool ok = onlyKeys(arguments, {QStringLiteral("mode")})
            && (mode == QStringLiteral("work")
                || mode == QStringLiteral("weekend_test"));
        if (!ok && error) {
            *error = QStringLiteral("运行模式必须为 work 或 weekend_test，且不得含其他参数");
        }
        return ok;
    }
    if (action == QStringLiteral("start_service")
        || action == QStringLiteral("stop_service")
        || action == QStringLiteral("restart_service")) {
        if (arguments.isEmpty()) return true; // Backward-compatible whole group.
        const QString target = arguments.value(QStringLiteral("target_unit")).toString();
        bool configuredUnit = false;
        for (const auto &unit : module.launchdUnits) {
            if (unit.id == target) {
                configuredUnit = true;
                break;
            }
        }
        const bool ok = module.id == QStringLiteral("upload")
            && onlyKeys(arguments, {QStringLiteral("target_unit")})
            && arguments.value(QStringLiteral("target_unit")).isString()
            && !target.isEmpty() && target == target.trimmed() && configuredUnit;
        if (!ok && error) {
            *error = QStringLiteral("Upload 单服务控制只接受配置中的 target_unit id；"
                                    "不接受 label、路径或其他参数");
        }
        return ok;
    }
    if (action == QStringLiteral("acknowledge_uncertain")) {
        const QString evidence = arguments.value(QStringLiteral("evidence")).toString().trimmed();
        const bool ok = onlyKeys(arguments, {QStringLiteral("evidence")})
            && evidence.size() >= 8 && evidence.size() <= 512;
        if (!ok && error) *error = QStringLiteral("evidence 必须为 8–512 字符且不得含其他参数");
        return ok;
    }
    if (action == QStringLiteral("upload_run_job")) {
        static const QRegularExpression jobId(
            QStringLiteral("^[a-z0-9][a-z0-9-]{0,63}$"));
        const QString id = arguments.value(QStringLiteral("job_id")).toString();
        const QString key = arguments.value(QStringLiteral("idempotency_key")).toString();
        const bool ok = onlyKeys(arguments, {QStringLiteral("job_id"),
                                             QStringLiteral("idempotency_key")})
            && jobId.match(id).hasMatch() && key.size() <= 128;
        if (!ok && error) *error = QStringLiteral("Upload job_id/idempotency_key 参数无效");
        return ok;
    }
    if (action == QStringLiteral("upload_ingest_quote")) {
        const bool ok = onlyKeys(arguments, {QStringLiteral("quote")})
            && arguments.value(QStringLiteral("quote")).isObject();
        if (!ok && error) *error = QStringLiteral("Upload 行情导入只接受 quote 对象");
        return ok;
    }
    if (action == QStringLiteral("upload_ingest_valuation")) {
        const bool ok = onlyKeys(arguments, {QStringLiteral("input")})
            && arguments.value(QStringLiteral("input")).isObject();
        if (!ok && error) *error = QStringLiteral("Upload 估值导入只接受 input 对象");
        return ok;
    }
    if (action == QStringLiteral("upload_ingest_dataset")) {
        const bool ok = onlyKeys(arguments, {QStringLiteral("dataset")})
            && arguments.value(QStringLiteral("dataset")).isObject();
        if (!ok && error) *error = QStringLiteral("Upload 业务数据导入只接受 dataset 对象");
        return ok;
    }
    if (action == QStringLiteral("upload_ack")) {
        static const QRegularExpression hex(QStringLiteral("^[0-9a-f]{64}$"));
        const bool ok = onlyKeys(arguments, {QStringLiteral("record_id"),
                                             QStringLiteral("sha256"),
                                             QStringLiteral("response")})
            && !arguments.value(QStringLiteral("record_id")).toString().isEmpty()
            && hex.match(arguments.value(QStringLiteral("sha256")).toString()).hasMatch()
            && (!arguments.contains(QStringLiteral("response"))
                || arguments.value(QStringLiteral("response")).isObject());
        if (!ok && error) *error = QStringLiteral("Upload ACK 需要 record_id 与 64 位 sha256");
        return ok;
    }
    if (action == QStringLiteral("upload_set_fund")) {
        const QString symbol = arguments.value(QStringLiteral("symbol")).toString();
        const auto optionalNumber = [&arguments](const QString &key) {
            return !arguments.contains(key) || arguments.value(key).isDouble();
        };
        const bool ok = onlyKeys(arguments, {QStringLiteral("symbol"), QStringLiteral("name"),
                                             QStringLiteral("branch"), QStringLiteral("nav"),
                                             QStringLiteral("shares"), QStringLiteral("position_ratio")})
            && !symbol.isEmpty() && symbol.size() <= 16
            && optionalNumber(QStringLiteral("nav"))
            && optionalNumber(QStringLiteral("shares"))
            && optionalNumber(QStringLiteral("position_ratio"));
        if (!ok && error) *error = QStringLiteral("Upload 基金设置参数无效");
        return ok;
    }
    if (action == QStringLiteral("upload_add_message")) {
        const QString message = arguments.value(QStringLiteral("message")).toString().trimmed();
        const bool ok = onlyKeys(arguments, {QStringLiteral("message")})
            && !message.isEmpty() && message.size() <= 4000;
        if (!ok && error) *error = QStringLiteral("Upload 留言必须为 1–4000 字符");
        return ok;
    }
    if (action == QStringLiteral("premium_detail_subscribe")
        || action == QStringLiteral("premium_detail_unsubscribe")) {
        const bool ok = onlyKeys(arguments, {QStringLiteral("symbol")})
            && arguments.value(QStringLiteral("symbol")).isString()
            && validEtfSymbol(arguments.value(QStringLiteral("symbol")).toString(),
                              true);
        if (!ok && error) {
            *error = QStringLiteral("详情订阅必须指定唯一的 NNNNNN.SH/SZ 标的");
        }
        return ok;
    }
    if (action == QStringLiteral("premium_set_watchlist")
        || action == QStringLiteral("premium_set_l1_hotlist")
        || action == QStringLiteral("redemption_set_watchlist")) {
        if (!onlyKeys(arguments, {QStringLiteral("symbols"), QStringLiteral("confirm_empty")})
            || !arguments.value(QStringLiteral("symbols")).isArray()) {
            if (error) *error = QStringLiteral("symbols 必须是数组");
            return false;
        }
        const auto symbols = arguments.value(QStringLiteral("symbols")).toArray();
        const int maximum = action == QStringLiteral("redemption_set_watchlist") ? 200 : 500;
        const bool emptyAllowed = action == QStringLiteral("premium_set_l1_hotlist")
            && arguments.value(QStringLiteral("confirm_empty")).toBool(false);
        if (symbols.size() > maximum || (symbols.isEmpty() && !emptyAllowed)) {
            if (error) {
                *error = action == QStringLiteral("premium_set_l1_hotlist")
                    ? QStringLiteral("symbols 需 1–500 项；仅清空额外 L1 列表时可用 confirm_empty=true")
                    : QStringLiteral("symbols 必须包含 1–%1 项，该观察列表不支持清空").arg(maximum);
            }
            return false;
        }
        QSet<QString> unique;
        for (const auto &value : symbols) {
            const bool symbolOk = value.isString()
                && (action == QStringLiteral("premium_set_l1_hotlist")
                        ? validPremiumHotSymbol(value.toString())
                        : validEtfSymbol(value.toString(), action == QStringLiteral("premium_set_watchlist")));
            if (!symbolOk) {
                if (error) *error = QStringLiteral("symbols 含无效 ETF 代码");
                return false;
            }
            unique.insert(value.toString().trimmed().toUpper());
        }
        if (unique.size() != symbols.size()) {
            if (error) *error = QStringLiteral("symbols 不得重复");
            return false;
        }
        return true;
    }
    if (action == QStringLiteral("webull_set_mode")) {
        const QString mode = arguments.value(QStringLiteral("mode")).toString();
        const bool ok = onlyKeys(arguments, {QStringLiteral("mode")})
            && QSet<QString>{QStringLiteral("auto"), QStringLiteral("force_running"),
                             QStringLiteral("force_stopped")}.contains(mode);
        if (!ok && error) *error = QStringLiteral("mode 必须为 auto/force_running/force_stopped");
        return ok;
    }
    if (action == QStringLiteral("webull_get_book")) {
        const bool ok = onlyKeys(arguments, {QStringLiteral("symbol")})
            && (!arguments.contains(QStringLiteral("symbol"))
                || (arguments.value(QStringLiteral("symbol")).isString()
                    && arguments.value(QStringLiteral("symbol")).toString().size() <= 16));
        if (!ok && error) *error = QStringLiteral("symbol 必须是最多 16 字符的字符串");
        return ok;
    }
    if (action == QStringLiteral("redemption_get_history")) {
        const QString date = arguments.value(QStringLiteral("date")).toString();
        const QString symbol = arguments.value(QStringLiteral("symbol")).toString();
        const bool limitOk = !arguments.contains(QStringLiteral("limit"))
            || (arguments.value(QStringLiteral("limit")).isDouble()
                && arguments.value(QStringLiteral("limit")).toInt() >= 1
                && arguments.value(QStringLiteral("limit")).toInt() <= 5000);
        const bool ok = onlyKeys(arguments, {QStringLiteral("date"), QStringLiteral("symbol"),
                                             QStringLiteral("limit")})
            && QDate::fromString(date, Qt::ISODate).isValid()
            && (symbol.trimmed().isEmpty() || validEtfSymbol(symbol)) && limitOk;
        if (!ok && error) *error = QStringLiteral("历史参数需有效 date、可选 ETF symbol、limit 1–5000");
        return ok;
    }
    if (action == QStringLiteral("redemption_get_pcf")) {
        const bool ok = onlyKeys(arguments, {QStringLiteral("symbol")})
            && validEtfSymbol(arguments.value(QStringLiteral("symbol")).toString());
        if (!ok && error) *error = QStringLiteral("PCF 查询必须指定有效 ETF symbol");
        return ok;
    }
    if (action == QStringLiteral("redemption_set_symbol_name")) {
        const QString name = arguments.value(QStringLiteral("name")).toString().simplified();
        const bool ok = onlyKeys(arguments, {QStringLiteral("symbol"), QStringLiteral("name")})
            && validEtfSymbol(arguments.value(QStringLiteral("symbol")).toString())
            && arguments.value(QStringLiteral("name")).isString() && name.size() <= 40;
        if (!ok && error) *error = QStringLiteral("标的名称参数无效");
        return ok;
    }
    if (action.startsWith(QStringLiteral("redemption_qmt_"))) {
        const QString backend = arguments.value(QStringLiteral("backend")).toString();
        QSet<QString> configuredBackends;
        for (const auto &value : module.settings.value(QStringLiteral("qmt_backends")).toArray()) {
            configuredBackends.insert(value.toObject().value(QStringLiteral("id")).toString());
        }
        if (!configuredBackends.contains(backend)) {
            if (error) *error = QStringLiteral("backend 未在 qmt_backends 配置中");
            return false;
        }
        if (action == QStringLiteral("redemption_qmt_order")) {
            const QString side = arguments.value(QStringLiteral("side")).toString().trimmed().toUpper();
            const bool ok = onlyKeys(arguments, {QStringLiteral("backend"), QStringLiteral("symbol"),
                                                 QStringLiteral("side")})
                && validEtfSymbol(arguments.value(QStringLiteral("symbol")).toString())
                && (side == QStringLiteral("PURCHASE") || side == QStringLiteral("REDEEM"));
            if (!ok && error) *error = QStringLiteral("QMT order 必须指定 backend/symbol/PURCHASE|REDEEM");
            return ok;
        }
        const bool ok = onlyKeys(arguments, {QStringLiteral("backend")});
        if (!ok && error) *error = QStringLiteral("QMT 控制只接受 backend 参数");
        return ok;
    }
    if (error) *error = QStringLiteral("动作没有已注册的参数合同：%1").arg(action);
    return false;
}

} // namespace

HubAgent::HubAgent(AppConfig config, QObject *parent)
    : QObject(parent), config_(std::move(config)), instanceId_(randomId()),
      artifactHash_(fileSha256(QCoreApplication::applicationFilePath())) {
    connect(&server_, &QLocalServer::newConnection, this, &HubAgent::acceptClients);
}

HubAgent::~HubAgent() {
    shutdown();
}

bool HubAgent::start(QString *error) {
    if (started_) return true;
    QString validationError;
    if (!config_.isValid(&validationError)) {
        if (error) *error = validationError;
        return false;
    }

    commandJournal_ = new CommandJournal;
    if (!commandJournal_->initialize(config_.auditDatabase, &validationError)
        || !commandJournal_->recoverInterrupted(&unresolvedModules_, &validationError)) {
        if (error) *error = validationError;
        delete commandJournal_;
        commandJournal_ = nullptr;
        return false;
    }
    commandJournalHealthy_ = true;
    auditEpoch_ = commandJournal_->auditEpoch();
    if (!commandJournal_->criticalEventBounds(&criticalEventFloor_,
                                               &criticalEventHighWater_,
                                               &validationError)) {
        if (error) *error = validationError;
        commandJournal_->close();
        delete commandJournal_;
        commandJournal_ = nullptr;
        commandJournalHealthy_ = false;
        return false;
    }
    moduleControlRevisions_ = commandJournal_->maximumControlRevisions(&validationError);
    if (!validationError.isEmpty()) {
        if (error) *error = validationError;
        commandJournal_->close();
        delete commandJournal_;
        commandJournal_ = nullptr;
        commandJournalHealthy_ = false;
        return false;
    }

    const QFileInfo socketInfo(config_.socketPath);
    QDir socketDirectory(socketInfo.absolutePath());
    if (!socketDirectory.exists() && !socketDirectory.mkpath(QStringLiteral("."))) {
        if (error) *error = QStringLiteral("无法创建 socket 目录：%1").arg(socketDirectory.absolutePath());
        commandJournal_->close();
        delete commandJournal_;
        commandJournal_ = nullptr;
        commandJournalHealthy_ = false;
        return false;
    }
    QFile::setPermissions(socketDirectory.absolutePath(), QFileDevice::ReadOwner |
                                                          QFileDevice::WriteOwner |
                                                          QFileDevice::ExeOwner);
    QLocalServer::removeServer(config_.socketPath);
    server_.setSocketOptions(QLocalServer::UserAccessOption);
    if (!server_.listen(config_.socketPath)) {
        if (error) *error = QStringLiteral("无法监听 %1：%2").arg(config_.socketPath, server_.errorString());
        commandJournal_->close();
        delete commandJournal_;
        commandJournal_ = nullptr;
        commandJournalHealthy_ = false;
        return false;
    }
    QFile::setPermissions(config_.socketPath, QFileDevice::ReadOwner | QFileDevice::WriteOwner);

    auditThread_ = new QThread;
    auditWriter_ = new AuditWriter(config_.auditDatabase);
    auditWriter_->moveToThread(auditThread_);
    connect(this, &HubAgent::audit, auditWriter_, &AuditWriter::record, Qt::QueuedConnection);
    connect(auditThread_, &QThread::finished, auditWriter_, &QObject::deleteLater);
    connect(auditWriter_, &AuditWriter::failed, this, [](const QString &message) {
        qWarning().noquote() << message;
    });
    connect(auditWriter_, &AuditWriter::readyChanged,
            this, &HubAgent::onAuditReadyChanged, Qt::QueuedConnection);
    connect(auditWriter_, &AuditWriter::criticalRecorded,
            this, &HubAgent::onCriticalRecorded, Qt::QueuedConnection);
    connect(auditWriter_, &AuditWriter::criticalRecordFailed,
            this, &HubAgent::onCriticalRecordFailed, Qt::QueuedConnection);
    auditThread_->setObjectName(QStringLiteral("AuditWriter"));
    auditThread_->start();
    bool auditReady = false;
    QMetaObject::invokeMethod(auditWriter_, [this, &auditReady] {
        auditWriter_->initialize();
        auditReady = auditWriter_->isReady();
    }, Qt::BlockingQueuedConnection);
    auditHealthy_ = auditReady;
    if (!auditReady) {
        if (error) *error = QStringLiteral("审计数据库无法初始化；为防止无审计控制，Agent 未启动");
        auditThread_->quit();
        auditThread_->wait(5000);
        delete auditThread_;
        auditThread_ = nullptr;
        auditWriter_ = nullptr;
        server_.close();
        QLocalServer::removeServer(config_.socketPath);
        commandJournal_->close();
        delete commandJournal_;
        commandJournal_ = nullptr;
        commandJournalHealthy_ = false;
        return false;
    }

    for (const auto &moduleConfig : config_.modules) {
        if (!moduleConfig.enabled) continue;
        auto *thread = new QThread(this);
        thread->setObjectName(QStringLiteral("Module-%1").arg(moduleConfig.id));
        auto *worker = new ModuleWorker(moduleConfig,
                                        moduleControlRevisions_.value(moduleConfig.id, 0));
        worker->moveToThread(thread);
        connect(thread, &QThread::started, worker, &ModuleWorker::start);
        connect(thread, &QThread::finished, worker, &QObject::deleteLater);
        connect(worker, &ModuleWorker::snapshotChanged, this, &HubAgent::onSnapshot, Qt::QueuedConnection);
        connect(worker, &ModuleWorker::detailEvent, this, &HubAgent::onEvent, Qt::QueuedConnection);
        connect(worker, &ModuleWorker::commandChanged, this, &HubAgent::onCommandResult, Qt::QueuedConnection);
        workers_.insert(moduleConfig.id, worker);
        workerThreads_.push_back(thread);
        thread->start();
    }

    started_ = true;
    emit audit(QJsonObject{{QStringLiteral("type"), QStringLiteral("agent_started")},
                           {QStringLiteral("timestamp"), utcNow()},
                           {QStringLiteral("instance_id"), instanceId_},
                           {QStringLiteral("module_count"), workers_.size()}});
    return true;
}

void HubAgent::shutdown() {
    if (!started_) return;
    server_.close();
    for (auto *socket : clients_.keys()) {
        socket->disconnectFromServer();
    }
    clients_.clear();
    for (auto it = workers_.begin(); it != workers_.end(); ++it) {
        ModuleWorker *worker = it.value();
        QThread *thread = worker ? worker->thread() : nullptr;
        if (!worker || !thread) continue;
        if (!QMetaObject::invokeMethod(worker, [worker, thread] {
                worker->stop();
                thread->quit();
            }, Qt::QueuedConnection)) {
            thread->quit();
        }
    }
    QDeadlineTimer workerDeadline(10000);
    for (auto *thread : std::as_const(workerThreads_)) {
        if (!thread->wait(qMax<qint64>(0, workerDeadline.remainingTime()))) {
            qWarning().noquote() << "模块线程未在统一期限内退出：" << thread->objectName();
            thread->requestInterruption();
            thread->quit();
        }
    }
    QDeadlineTimer cooperativeDeadline(5000);
    for (auto *thread : std::as_const(workerThreads_)) {
        if (thread->isRunning()
            && !thread->wait(qMax<qint64>(0, cooperativeDeadline.remainingTime()))) {
            qCritical().noquote()
                << "模块线程拒绝协作退出，已与 Agent 解绑并交由进程退出回收："
                << thread->objectName();
            thread->setParent(nullptr);
        }
    }
    // ModuleWorker::stop() flushes its critical mailbox through queued
    // detailEvent signals. Deliver those metacalls while AuditWriter is still
    // accepting its append-first ingress. Clear raw worker pointers first:
    // command-result metacalls must not invoke an already deleted worker.
    workers_.clear();
    QCoreApplication::sendPostedEvents(this, QEvent::MetaCall);
    workerThreads_.clear();

    if (auditWriter_ && auditThread_) {
        AuditWriter *writer = auditWriter_;
        QThread *thread = auditThread_;
        QMetaObject::invokeMethod(writer, [writer, thread] {
            writer->close();
            thread->quit();
        }, Qt::QueuedConnection);
    } else if (auditThread_) {
        auditThread_->quit();
    }
    if (auditThread_ && !auditThread_->wait(10000)) {
        qCritical().noquote()
            << "审计线程遭遇操作系统 I/O 卡死；拒绝强制终止 SQLite，交由进程退出回收";
        auditThread_->requestInterruption();
        auditThread_->quit();
        // Deliberately leak the thread object until process exit. Deleting a
        // running QThread would abort; terminate() could corrupt SQLite/WAL.
        auditThread_ = nullptr;
    } else if (auditThread_) {
        delete auditThread_;
        auditThread_ = nullptr;
    }
    auditWriter_ = nullptr;
    auditHealthy_ = false;
    if (commandJournal_) {
        commandJournal_->close();
        delete commandJournal_;
        commandJournal_ = nullptr;
    }
    commandJournalHealthy_ = false;
    approvalTickets_.clear();
    QLocalServer::removeServer(config_.socketPath);
    started_ = false;
}

bool HubAgent::sameUid(QLocalSocket *socket) const {
#ifdef Q_OS_MACOS
    uid_t uid = 0;
    gid_t gid = 0;
    if (::getpeereid(static_cast<int>(socket->socketDescriptor()), &uid, &gid) != 0) return false;
    Q_UNUSED(gid)
    return uid == ::getuid();
#else
    Q_UNUSED(socket)
    return true;
#endif
}

void HubAgent::acceptClients() {
    while (server_.hasPendingConnections()) {
        auto *socket = server_.nextPendingConnection();
        if (!socket || !sameUid(socket)) {
            if (socket) {
                socket->abort();
                socket->deleteLater();
            }
            continue;
        }
        clients_.insert(socket, {});
        connect(socket, &QLocalSocket::readyRead, this, &HubAgent::clientReadyRead);
        connect(socket, &QLocalSocket::disconnected, this, &HubAgent::clientDisconnected);
        connect(socket, &QLocalSocket::bytesWritten, this,
                [this, socket](qint64) { drainClientReplay(socket); });
    }
}

void HubAgent::clientReadyRead() {
    auto *socket = qobject_cast<QLocalSocket *>(sender());
    if (!socket || !clients_.contains(socket)) return;
    QList<QJsonObject> messages;
    QString error;
    auto &state = clients_[socket];
    if (!FrameCodec::consume(state.buffer, socket->readAll(), &messages, &error, config_.frameLimitBytes)) {
        reject(socket, QStringLiteral("invalid_frame"), error);
        socket->disconnectFromServer();
        return;
    }
    for (const auto &message : messages) {
        processClientMessage(socket, message);
    }
}

void HubAgent::clientDisconnected() {
    auto *socket = qobject_cast<QLocalSocket *>(sender());
    if (!socket) return;
    clients_.remove(socket);
    for (auto it = approvalTickets_.begin(); it != approvalTickets_.end();) {
        if (it.value().socket == socket) it = approvalTickets_.erase(it);
        else ++it;
    }
    socket->deleteLater();
}

void HubAgent::onAuditReadyChanged(bool ready, const QString &message) {
    auditHealthy_ = ready;
    if (!ready) qWarning().noquote() << message;
    if (started_) {
        for (auto it = clients_.begin(); it != clients_.end(); ++it) {
            if (!it.value().helloComplete) continue;
            QJsonObject hello = helloMessage();
            hello.insert(QStringLiteral("client_config_bound"), it.value().configBound);
            send(it.key(), hello);
        }
    }
}

QJsonObject HubAgent::helloMessage() const {
    QJsonArray modules;
    for (const auto &module : config_.modules) {
        if (!module.enabled) continue;
        QStringList activeCommands = activeCommandIds_.value(module.id).values();
        activeCommands.sort();
        modules.append(QJsonObject{{QStringLiteral("id"), module.id},
                                   {QStringLiteral("display_name"), module.displayName},
                                   {QStringLiteral("adapter"), module.adapter},
                                   {QStringLiteral("ownership"), module.ownership},
                                   {QStringLiteral("control_enabled"), module.controlEnabled},
                                   {QStringLiteral("allowed_actions"),
                                    QJsonArray::fromStringList(module.allowedActions)},
                                   {QStringLiteral("approval_required_actions"),
                                    QJsonArray::fromStringList(module.approvalRequiredActions)},
                                   {QStringLiteral("active_command_ids"),
                                    QJsonArray::fromStringList(activeCommands)}});
    }
    return {{QStringLiteral("schema_version"), 1},
            {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
            {QStringLiteral("type"), QStringLiteral("hello")},
            {QStringLiteral("instance_id"), instanceId_},
            {QStringLiteral("version"), QCoreApplication::applicationVersion()},
            {QStringLiteral("build_time"), QStringLiteral(MACHOME_HUB_BUILD_TIME)},
            {QStringLiteral("git_commit"), QStringLiteral(MACHOME_HUB_GIT_COMMIT)},
            {QStringLiteral("artifact_sha256"), artifactHash_},
            {QStringLiteral("config_sha256"), config_.configHash},
            {QStringLiteral("config_path"), config_.sourcePath},
            {QStringLiteral("audit_ready"), auditHealthy_},
            {QStringLiteral("audit_epoch"), auditEpoch_},
            {QStringLiteral("critical_event_floor"), criticalEventFloor_},
            {QStringLiteral("critical_event_high_water"), criticalEventHighWater_},
            {QStringLiteral("command_journal_ready"), commandJournalHealthy_},
            {QStringLiteral("control_ready"), auditHealthy_ && commandJournalHealthy_},
            {QStringLiteral("unresolved_modules"),
             QJsonArray::fromStringList(QStringList(unresolvedModules_.begin(), unresolvedModules_.end()))},
            {QStringLiteral("pid"), QCoreApplication::applicationPid()},
            {QStringLiteral("host"), QSysInfo::machineHostName()},
            {QStringLiteral("timestamp"), utcNow()},
            {QStringLiteral("modules"), modules}};
}

void HubAgent::processClientMessage(QLocalSocket *socket, const QJsonObject &message) {
    auto &client = clients_[socket];
    const QString type = message.value(QStringLiteral("type")).toString();
    if (message.value(QStringLiteral("schema_version")).toInt() != 1 ||
        message.value(QStringLiteral("protocol")).toString() != QStringLiteral("module.control.v1")) {
        reject(socket, QStringLiteral("unsupported_protocol"),
               QStringLiteral("仅支持 schema_version=1 / module.control.v1"),
               message.value(QStringLiteral("command_id")).toString());
        if (!client.helloComplete) socket->disconnectFromServer();
        return;
    }
    if (!client.helloComplete) {
        if (type != QStringLiteral("hello")) {
            reject(socket, QStringLiteral("hello_required"), QStringLiteral("首帧必须是 module.control.v1 hello"));
            socket->disconnectFromServer();
            return;
        }
        client.helloComplete = true;
        client.clientInstanceId = message.value(QStringLiteral("client_instance_id")).toString();
        QString boundsError;
        if (!commandJournal_->criticalEventBounds(&criticalEventFloor_,
                                                   &criticalEventHighWater_,
                                                   &boundsError)) {
            commandJournalHealthy_ = false;
            reject(socket, QStringLiteral("critical_replay_unavailable"), boundsError);
            socket->disconnectFromServer();
            return;
        }
        const QString cursorEpoch = message.value(
            QStringLiteral("last_critical_event_epoch")).toString();
        const qint64 requestedCursor = qMax<qint64>(
            0, message.value(QStringLiteral("last_critical_event_id")).toInteger());
        const bool cursorMatches = cursorEpoch == auditEpoch_
            && requestedCursor <= criticalEventHighWater_;
        client.lastCriticalAck = cursorMatches ? requestedCursor : 0;
        if (criticalEventFloor_ > 0
            && client.lastCriticalAck < criticalEventFloor_ - 1) {
            client.lastCriticalAck = criticalEventFloor_ - 1;
        }
        client.criticalReplayQueuedThrough = client.lastCriticalAck;
        client.configBound = !client.clientInstanceId.isEmpty()
            && message.value(QStringLiteral("config_sha256")).toString() == config_.configHash;
        QJsonObject hello = helloMessage();
        hello.insert(QStringLiteral("client_config_bound"), client.configBound);
        send(socket, hello);
        for (const auto &snapshot : std::as_const(snapshots_)) send(socket, snapshot);
        enqueueCriticalReplay(socket, client.lastCriticalAck);
        QString journalError;
        const auto recent = commandJournal_ ? commandJournal_->recent(200, 4 * 1024 * 1024,
                                                                      &journalError)
                                            : QList<CommandJournal::Entry>{};
        if (!journalError.isEmpty()) {
            commandJournalHealthy_ = false;
            reject(socket, QStringLiteral("command_journal_unavailable"), journalError);
        } else {
            // recent() returns newest-first. Queue oldest-first for a stable UI
            // timeline, but drain incrementally as the local socket accepts data.
            for (auto it = recent.crbegin(); it != recent.crend(); ++it) {
                enqueueOrdered(socket, replayedResult(it->result));
            }
            drainClientReplay(socket);
        }
        return;
    }

    if (type == QStringLiteral("critical_ack")) {
        if (message.value(QStringLiteral("audit_epoch")).toString() != auditEpoch_) {
            reject(socket, QStringLiteral("invalid_critical_ack"),
                   QStringLiteral("关键事件确认不属于当前审计 epoch"));
            return;
        }
        const QJsonValue idValue = message.value(QStringLiteral("audit_event_id"));
        if (!idValue.isDouble() || idValue.toInteger() < 0) {
            reject(socket, QStringLiteral("invalid_critical_ack"),
                   QStringLiteral("audit_event_id 必须是非负整数"));
            return;
        }
        const qint64 id = idValue.toInteger();
        if (id > client.criticalReplayQueuedThrough) {
            reject(socket, QStringLiteral("invalid_critical_ack"),
                   QStringLiteral("不得确认 Agent 尚未投递的关键事件"));
            return;
        }
        client.lastCriticalAck = qMax(client.lastCriticalAck, id);
        if (client.lastCriticalAck >= client.criticalReplayQueuedThrough) {
            enqueueCriticalReplay(socket, client.lastCriticalAck);
        }
        return;
    }

    if (type == QStringLiteral("refresh")) {
        const QString moduleId = message.value(QStringLiteral("module_id")).toString();
        if (moduleId.isEmpty()) {
            for (auto *worker : std::as_const(workers_)) {
                QMetaObject::invokeMethod(worker, &ModuleWorker::requestSnapshot, Qt::QueuedConnection);
            }
        } else if (workers_.contains(moduleId)) {
            QMetaObject::invokeMethod(workers_.value(moduleId), &ModuleWorker::requestSnapshot,
                                      Qt::QueuedConnection);
        } else {
            reject(socket, QStringLiteral("unknown_module"), QStringLiteral("未知模块：%1").arg(moduleId));
        }
        return;
    }

    if (type == QStringLiteral("approval_request")) {
        processApprovalRequest(socket, message);
        return;
    }

    if (type != QStringLiteral("command")) {
        reject(socket, QStringLiteral("unknown_type"), QStringLiteral("不支持的消息类型：%1").arg(type));
        return;
    }
    const QString commandId = message.value(QStringLiteral("command_id")).toString();
    const QString moduleId = message.value(QStringLiteral("module_id")).toString();
    const QString action = message.value(QStringLiteral("action")).toString();
    static const QRegularExpression commandIdentifier(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9._:-]{7,79}$"));
    static const QRegularExpression actionIdentifier(QStringLiteral("^[a-z][a-z0-9_]{2,79}$"));
    const QString requestedAt = message.value(QStringLiteral("requested_at")).toString();
    const QString requestedBy = message.value(QStringLiteral("requested_by")).toString();
    const QString reason = message.value(QStringLiteral("reason")).toString();
    const bool expectedRevisionValid = message.value(QStringLiteral("expected_revision")).isDouble()
        && message.value(QStringLiteral("expected_revision")).toInteger(-2) >= -1;
    const bool deadlineValid = message.value(QStringLiteral("deadline_ms")).isDouble()
        && message.value(QStringLiteral("deadline_ms")).toInt() >= 1000
        && message.value(QStringLiteral("deadline_ms")).toInt() <= 30 * 60 * 1000;
    if (!commandIdentifier.match(commandId).hasMatch() || !actionIdentifier.match(action).hasMatch()
        || !message.value(QStringLiteral("arguments")).isObject() || !workers_.contains(moduleId)
        || !expectedRevisionValid || !deadlineValid || !validIsoTimestamp(requestedAt)
        || requestedBy.isEmpty() || requestedBy.size() > 128 || reason.isEmpty() || reason.size() > 512) {
        reject(socket, QStringLiteral("invalid_command"), QStringLiteral("命令缺少有效 command_id/module_id/action"), commandId);
        return;
    }
    if (!validateBinding(socket, message, commandId)) return;
    const QByteArray fingerprint = commandFingerprint(message);
    QByteArray previousFingerprint;
    QJsonObject previousResult;
    bool found = false;
    if (!durableLookup(commandId, &previousFingerprint, &previousResult, &found)) {
        reject(socket, QStringLiteral("command_journal_unavailable"),
               QStringLiteral("命令账本不可用，命令未执行"), commandId);
        return;
    }
    if (found) {
        if (previousFingerprint == fingerprint) {
            send(socket, replayedResult(previousResult));
        } else {
            reject(socket, QStringLiteral("command_id_conflict"),
                   QStringLiteral("command_id 已被另一组参数使用"), commandId);
        }
        return;
    }
    QString argumentsError;
    const ModuleConfig *moduleConfig = config_.module(moduleId);
    if (!moduleConfig || !validateActionArguments(*moduleConfig, action,
                                                   message.value(QStringLiteral("arguments")).toObject(),
                                                   &argumentsError)) {
        reject(socket, QStringLiteral("invalid_arguments"), argumentsError, commandId);
        return;
    }
    if (!actionAllowed(moduleId, action)) {
        reject(socket, QStringLiteral("action_not_allowed"),
               QStringLiteral("动作未列入模块的代理端 allowed_actions：%1").arg(action), commandId);
        return;
    }
    if (revisionRequired(action) && (!commandJournalHealthy_ || !auditHealthy_)) {
        reject(socket, QStringLiteral("control_plane_not_ready"),
               QStringLiteral("命令账本或审计日志不可用；变更命令已熔断"), commandId);
        return;
    }
    if (revisionRequired(action) && action != QStringLiteral("acknowledge_uncertain")
        && unresolvedModules_.contains(moduleId)) {
        reject(socket, QStringLiteral("previous_outcome_unresolved"),
               QStringLiteral("该模块存在未知执行结果；权威核对并执行“解除未知结果锁”前禁止新变更"),
               commandId);
        return;
    }
    const qint64 expectedRevision = message.value(QStringLiteral("expected_revision")).toInteger(-1);
    const qint64 currentRevision = moduleControlRevisions_.value(moduleId, -1);
    if (revisionRequired(action) && (expectedRevision < 0 || currentRevision < 0
                                    || expectedRevision != currentRevision)) {
        const QString code = expectedRevision < 0 || currentRevision < 0
                                 ? QStringLiteral("revision_unavailable")
                                 : QStringLiteral("stale_revision");
        QJsonObject stale{{QStringLiteral("schema_version"), 1},
                          {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                          {QStringLiteral("type"), QStringLiteral("command_result")},
                          {QStringLiteral("instance_id"), instanceId_},
                          {QStringLiteral("command_id"), commandId},
                          {QStringLiteral("module_id"), moduleId},
                          {QStringLiteral("action"), action},
                          {QStringLiteral("state"), QStringLiteral("failed")},
                          {QStringLiteral("code"), code},
                          {QStringLiteral("message"), QStringLiteral("页面状态已变化，请刷新后重新确认")},
                          {QStringLiteral("expected_revision"), expectedRevision},
                          {QStringLiteral("current_revision"), currentRevision},
                          {QStringLiteral("control_revision"), currentRevision},
                          {QStringLiteral("timestamp"), utcNow()}};
        if (!persistNewCommand(commandId, fingerprint, message, stale, socket)) return;
        send(socket, stale);
        emit audit(stale);
        return;
    }
    if (approvalRequired(moduleId, action)) {
        QString approvalError;
        if (!consumeApproval(socket, message, &approvalError)) {
            reject(socket, QStringLiteral("approval_required"), approvalError, commandId);
            return;
        }
    }

    QJsonObject accepted{{QStringLiteral("schema_version"), 1},
                         {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                         {QStringLiteral("type"), QStringLiteral("command_result")},
                         {QStringLiteral("instance_id"), instanceId_},
                         {QStringLiteral("command_id"), commandId},
                         {QStringLiteral("module_id"), moduleId},
                         {QStringLiteral("action"), action},
                         {QStringLiteral("expected_revision"), expectedRevision},
                         {QStringLiteral("accepted_revision"), currentRevision},
                         {QStringLiteral("requested_by"),
                          message.value(QStringLiteral("requested_by")).toString(QStringLiteral("unknown"))},
                         {QStringLiteral("reason"),
                          message.value(QStringLiteral("reason")).toString(QStringLiteral("unspecified"))},
                         {QStringLiteral("requested_at"), requestedAt},
                         {QStringLiteral("state"), QStringLiteral("accepted")},
                         {QStringLiteral("message"), QStringLiteral("命令已持久化，正等待模块串行门校验")},
                         {QStringLiteral("control_revision"), currentRevision},
                         {QStringLiteral("timestamp"), utcNow()}};
    if (!persistNewCommand(commandId, fingerprint, message, accepted, socket)) return;
    activeCommandIds_[moduleId].insert(commandId);
    broadcast(accepted);
    emit audit(accepted);
    QJsonObject dispatch = message;
    dispatch.insert(QStringLiteral("deadline_ms"),
                    std::clamp(message.value(QStringLiteral("deadline_ms")).toInt(45000),
                               1000, 30 * 60 * 1000));
    QMetaObject::invokeMethod(workers_.value(moduleId), "submitCommand", Qt::QueuedConnection,
                              Q_ARG(QJsonObject, dispatch));
}

bool HubAgent::actionAllowed(const QString &moduleId, const QString &action) const {
    const auto *module = config_.module(moduleId);
    return module && module->enabled && module->allowedActions.contains(action);
}

bool HubAgent::approvalRequired(const QString &moduleId, const QString &action) const {
    const auto *module = config_.module(moduleId);
    return module && module->approvalRequiredActions.contains(action);
}

bool HubAgent::validateBinding(QLocalSocket *socket, const QJsonObject &message,
                               const QString &commandId) {
    if (!socket || !clients_.contains(socket) || !clients_[socket].configBound
        || message.value(QStringLiteral("agent_instance_id")).toString() != instanceId_
        || message.value(QStringLiteral("config_sha256")).toString() != config_.configHash) {
        reject(socket, QStringLiteral("agent_binding_mismatch"),
               QStringLiteral("UI 未绑定当前 Agent 实例/配置；为防止执行到错误目标，命令未执行"),
               commandId);
        return false;
    }
    return true;
}

void HubAgent::processApprovalRequest(QLocalSocket *socket, const QJsonObject &message) {
    const QString requestId = message.value(QStringLiteral("request_id")).toString();
    const QString moduleId = message.value(QStringLiteral("module_id")).toString();
    const QString action = message.value(QStringLiteral("action")).toString();
    static const QRegularExpression identifier(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9._:-]{7,79}$"));
    if (!identifier.match(requestId).hasMatch() || !workers_.contains(moduleId)
        || !message.value(QStringLiteral("arguments")).isObject()
        || !message.value(QStringLiteral("expected_revision")).isDouble()
        || message.value(QStringLiteral("requested_by")).toString().isEmpty()
        || message.value(QStringLiteral("reason")).toString().isEmpty()) {
        reject(socket, QStringLiteral("invalid_approval_request"),
               QStringLiteral("审批请求字段不完整"), requestId);
        return;
    }
    if (!validateBinding(socket, message, requestId)) return;
    QString argumentsError;
    const ModuleConfig *moduleConfig = config_.module(moduleId);
    if (!moduleConfig || !validateActionArguments(*moduleConfig, action,
                                                   message.value(QStringLiteral("arguments")).toObject(),
                                                   &argumentsError)) {
        reject(socket, QStringLiteral("invalid_arguments"), argumentsError, requestId);
        return;
    }
    if (!actionAllowed(moduleId, action) || !approvalRequired(moduleId, action)) {
        reject(socket, QStringLiteral("approval_not_applicable"),
               QStringLiteral("该动作未配置代理端二阶段审批"), requestId);
        return;
    }
    if (action == QStringLiteral("acknowledge_uncertain")) {
        const QString evidence = message.value(QStringLiteral("arguments")).toObject()
                                     .value(QStringLiteral("evidence")).toString().trimmed();
        if (evidence.size() < 8 || evidence.size() > 512) {
            reject(socket, QStringLiteral("reconciliation_evidence_required"),
                   QStringLiteral("解除锁必须附带 8–512 字符的权威核对证据"), requestId);
            return;
        }
    }
    if (!auditHealthy_ || !commandJournalHealthy_) {
        reject(socket, QStringLiteral("control_plane_not_ready"),
               QStringLiteral("审计或命令账本不可用，无法签发审批票据"), requestId);
        return;
    }
    const qint64 expected = message.value(QStringLiteral("expected_revision")).toInteger(-1);
    const qint64 current = moduleControlRevisions_.value(moduleId, -1);
    if (expected < 0 || current < 0 || expected != current) {
        reject(socket, QStringLiteral("stale_revision"),
               QStringLiteral("审批请求的页面版本已过期，请刷新后重新确认"), requestId);
        return;
    }
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    for (auto it = approvalTickets_.begin(); it != approvalTickets_.end();) {
        if (!it.value().socket || it.value().expiresAtMs <= now) it = approvalTickets_.erase(it);
        else ++it;
    }
    const QString token = randomId();
    approvalTickets_.insert(token, ApprovalTicket{socket, approvalFingerprint(message), now + 60'000});
    send(socket, QJsonObject{
        {QStringLiteral("schema_version"), 1},
        {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
        {QStringLiteral("type"), QStringLiteral("approval_ticket")},
        {QStringLiteral("instance_id"), instanceId_},
        {QStringLiteral("request_id"), requestId},
        {QStringLiteral("module_id"), moduleId},
        {QStringLiteral("action"), action},
        {QStringLiteral("approval_token"), token},
        {QStringLiteral("expires_at"), QDateTime::fromMSecsSinceEpoch(now + 60'000)
                                           .toUTC().toString(Qt::ISODateWithMs)},
        {QStringLiteral("timestamp"), utcNow()}});
}

bool HubAgent::consumeApproval(QLocalSocket *socket, const QJsonObject &message,
                               QString *error) {
    const QString token = message.value(QStringLiteral("approval_token")).toString();
    const auto it = approvalTickets_.find(token);
    if (token.isEmpty() || it == approvalTickets_.end()) {
        if (error) *error = QStringLiteral("缺少一次性审批票据；请从受信 UI 重新确认");
        return false;
    }
    const ApprovalTicket ticket = it.value();
    approvalTickets_.erase(it);
    if (ticket.socket != socket || ticket.expiresAtMs <= QDateTime::currentMSecsSinceEpoch()
        || ticket.fingerprint != approvalFingerprint(message)) {
        if (error) *error = QStringLiteral("审批票据已过期、已使用或与操作目标不匹配");
        return false;
    }
    return true;
}

bool HubAgent::durableLookup(const QString &commandId, QByteArray *fingerprint,
                             QJsonObject *result, bool *found) {
    if (found) *found = false;
    if (!commandJournal_ || !commandJournalHealthy_) return false;
    CommandJournal::Entry entry;
    QString error;
    if (!commandJournal_->lookup(commandId, &entry, &error)) {
        commandJournalHealthy_ = false;
        qWarning().noquote() << error;
        return false;
    }
    if (entry.commandId.isEmpty()) return true;
    if (found) *found = true;
    if (fingerprint) *fingerprint = entry.fingerprint;
    if (result) *result = entry.result;
    cacheCommandResult(entry.commandId, entry.fingerprint, entry.result);
    return true;
}

bool HubAgent::persistNewCommand(const QString &commandId, const QByteArray &fingerprint,
                                 const QJsonObject &request, const QJsonObject &result,
                                 QLocalSocket *socket) {
    QString error;
    if (!commandJournal_ || !commandJournalHealthy_
        || !commandJournal_->reserve(commandId, fingerprint, request, boundedMessage(result), &error)) {
        commandJournalHealthy_ = false;
        reject(socket, QStringLiteral("command_journal_unavailable"),
               error.isEmpty() ? QStringLiteral("命令账本不可用；命令未执行") : error,
               commandId);
        return false;
    }
    cacheCommandResult(commandId, fingerprint, boundedMessage(result));
    return true;
}

QJsonObject HubAgent::replayedResult(const QJsonObject &source) const {
    QJsonObject result = source;
    QJsonObject details = result.value(QStringLiteral("details")).toObject();
    details.insert(QStringLiteral("replayed"), true);
    details.insert(QStringLiteral("original_instance_id"),
                   result.value(QStringLiteral("instance_id")).toString());
    details.insert(QStringLiteral("replayed_by_instance_id"), instanceId_);
    result.insert(QStringLiteral("details"), details);
    result.insert(QStringLiteral("instance_id"), instanceId_);
    return boundedMessage(result);
}

QJsonObject HubAgent::boundedMessage(const QJsonObject &source) const {
    QJsonObject safe = redacted(source);
    // Approval tickets are intentionally delivered only over the same-UID 0600
    // local socket. Restore this one ephemeral secret after general telemetry
    // redaction; persisted audit/journal copies remain redacted independently.
    if (source.value(QStringLiteral("type")).toString() == QStringLiteral("approval_ticket")) {
        safe.insert(QStringLiteral("approval_token"), source.value(QStringLiteral("approval_token")));
    }
    // Ordered replay is bounded independently from the configurable framing
    // ceiling. A single valid frame must always fit the ordered client queue.
    const int limit = std::min(qMax(64 * 1024, config_.frameLimitBytes - 4),
                               4 * 1024 * 1024);
    const int originalBytes = QJsonDocument(safe).toJson(QJsonDocument::Compact).size();
    if (originalBytes <= limit) return safe;
    const QJsonObject marker{
        {QStringLiteral("truncated"), true},
        {QStringLiteral("original_bytes"), originalBytes},
        {QStringLiteral("wire_limit_bytes"), limit},
        {QStringLiteral("message"),
         QStringLiteral("数据超过本地 IPC 单帧上限；已保留终态并省略明细，请缩小查询范围")}};
    const QString type = safe.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("command_result")) {
        safe.insert(QStringLiteral("details"), marker);
        safe.insert(QStringLiteral("message"),
                    safe.value(QStringLiteral("message")).toString().left(512)
                        + QStringLiteral("（明细过大，已安全截断）"));
    } else if (type == QStringLiteral("event")) {
        safe.insert(QStringLiteral("payload"), marker);
    } else if (type == QStringLiteral("snapshot")) {
        QJsonObject payload = safe.value(QStringLiteral("payload")).toObject();
        payload.insert(QStringLiteral("telemetry"), marker);
        payload.insert(QStringLiteral("last_error"), marker.value(QStringLiteral("message")));
        safe.insert(QStringLiteral("payload"), payload);
    } else {
        safe = QJsonObject{{QStringLiteral("schema_version"), 1},
                           {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                           {QStringLiteral("type"), QStringLiteral("error")},
                           {QStringLiteral("instance_id"), instanceId_},
                           {QStringLiteral("code"), QStringLiteral("response_too_large")},
                           {QStringLiteral("message"), marker.value(QStringLiteral("message"))},
                           {QStringLiteral("timestamp"), utcNow()}};
    }
    return safe;
}

void HubAgent::send(QLocalSocket *socket, const QJsonObject &message) {
    if (!socket || socket->state() != QLocalSocket::ConnectedState) return;
    if (socket->bytesToWrite() > 4 * 1024 * 1024) {
        socket->disconnectFromServer();
        return;
    }
    const QByteArray frame = FrameCodec::encode(boundedMessage(message));
    if (frame.size() > config_.frameLimitBytes + 4) {
        qWarning().noquote() << "拒绝发送超过 IPC 上限的消息" << frame.size();
        return;
    }
    socket->write(frame);
}

void HubAgent::drainClientReplay(QLocalSocket *socket) {
    if (!socket || !clients_.contains(socket)
        || socket->state() != QLocalSocket::ConnectedState) return;
    auto &queue = clients_[socket].replayQueue;
    constexpr qint64 ReplayHighWaterBytes = 1024 * 1024;
    while (!queue.isEmpty() && socket->bytesToWrite() < ReplayHighWaterBytes) {
        const QJsonObject message = queue.dequeue();
        clients_[socket].replayQueueBytes = qMax<qint64>(
            0, clients_[socket].replayQueueBytes
                   - QJsonDocument(message).toJson(QJsonDocument::Compact).size());
        send(socket, message);
        if (socket->state() != QLocalSocket::ConnectedState) return;
    }
}

void HubAgent::enqueueOrdered(QLocalSocket *socket, const QJsonObject &message) {
    if (!socket || !clients_.contains(socket)) return;
    auto &client = clients_[socket];
    const QJsonObject bounded = boundedMessage(message);
    const qint64 bytes = QJsonDocument(bounded).toJson(QJsonDocument::Compact).size();
    constexpr qint64 MaximumOrderedQueueBytes = 8 * 1024 * 1024;
    if (client.replayQueueBytes + bytes > MaximumOrderedQueueBytes) {
        // Command results and critical events are durable. Disconnecting a
        // non-reading UI forces cursor-based recovery without unbounded RAM.
        socket->disconnectFromServer();
        return;
    }
    client.replayQueue.enqueue(bounded);
    client.replayQueueBytes += bytes;
    drainClientReplay(socket);
}

void HubAgent::enqueueCriticalReplay(QLocalSocket *socket, qint64 afterId) {
    if (!socket || !clients_.contains(socket) || !commandJournal_) return;
    QString error;
    auto &client = clients_[socket];
    if (!commandJournal_->criticalEventBounds(&criticalEventFloor_,
                                               &criticalEventHighWater_, &error)) {
        commandJournalHealthy_ = false;
        reject(socket, QStringLiteral("critical_replay_unavailable"), error);
        return;
    }
    qint64 replayAfter = qMax(afterId, client.criticalReplayQueuedThrough);
    if (criticalEventFloor_ > 0 && replayAfter < criticalEventFloor_ - 1) {
        replayAfter = criticalEventFloor_ - 1;
        client.lastCriticalAck = replayAfter;
        client.criticalReplayQueuedThrough = replayAfter;
        reject(socket, QStringLiteral("critical_replay_gap"),
               QStringLiteral("关键事件游标早于 90 天/100000 条保留窗口；已从最旧可用事件继续"));
    }
    error.clear();
    const auto events = commandJournal_->criticalEventsAfter(
        replayAfter, 500, 4 * 1024 * 1024, &error);
    if (!error.isEmpty()) {
        commandJournalHealthy_ = false;
        reject(socket, QStringLiteral("critical_replay_unavailable"), error);
        return;
    }
    client.criticalReplayActive = !events.isEmpty();
    for (const auto &event : events) {
        const qint64 id = event.value(QStringLiteral("audit_event_id")).toInteger();
        if (id <= client.lastCriticalAck || id <= client.criticalReplayQueuedThrough) continue;
        client.criticalReplayQueuedThrough = id;
        enqueueOrdered(socket, event);
        if (!clients_.contains(socket)) return;
    }
}

void HubAgent::broadcast(const QJsonObject &message) {
    const auto sockets = clients_.keys();
    for (auto *socket : sockets) {
        if (!clients_.contains(socket) || !clients_[socket].helloComplete) continue;
        const QString type = message.value(QStringLiteral("type")).toString();
        const qint64 criticalId = type == QStringLiteral("event")
            ? message.value(QStringLiteral("audit_event_id")).toInteger() : 0;
        if (criticalId > 0) {
            auto &client = clients_[socket];
            // While a client is paging historical critical events, every new
            // critical event is already in SQLite. Let the cursor query pick it
            // up so a high live ID can never jump over an older undelivered ID.
            if (client.criticalReplayActive) continue;
            if (criticalId <= client.lastCriticalAck
                || criticalId <= client.criticalReplayQueuedThrough) continue;
            client.criticalReplayQueuedThrough = criticalId;
            enqueueOrdered(socket, message);
        } else if (type == QStringLiteral("command_result")) {
            enqueueOrdered(socket, message);
        } else {
            send(socket, message);
        }
    }
}

void HubAgent::cacheCommandResult(const QString &commandId, const QByteArray &fingerprint,
                                  const QJsonObject &result) {
    if (commandId.isEmpty()) return;
    commandResults_.insert(commandId, result);
    if (!fingerprint.isEmpty()) commandFingerprints_.insert(commandId, fingerprint);
    while (commandResults_.size() > 1000) {
        const QString key = *commandResults_.keyBegin();
        commandResults_.remove(key);
        commandFingerprints_.remove(key);
    }
}

void HubAgent::reject(QLocalSocket *socket, const QString &code, const QString &message,
                      const QString &commandId) {
    send(socket, QJsonObject{{QStringLiteral("schema_version"), 1},
                             {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                             {QStringLiteral("type"), QStringLiteral("error")},
                             {QStringLiteral("instance_id"), instanceId_},
                             {QStringLiteral("code"), code},
                             {QStringLiteral("message"), message},
                             {QStringLiteral("command_id"), commandId},
                             {QStringLiteral("timestamp"), utcNow()}});
}

void HubAgent::onSnapshot(const QJsonObject &source) {
    QJsonObject message = source;
    message.insert(QStringLiteral("instance_id"), instanceId_);
    const QString moduleId = message.value(QStringLiteral("module_id")).toString();
    if (moduleId.isEmpty()) return;
    QJsonObject payload = message.value(QStringLiteral("payload")).toObject();
    payload.insert(QStringLiteral("safety_interlock"), QJsonObject{
        {QStringLiteral("outcome_unresolved"), unresolvedModules_.contains(moduleId)},
        {QStringLiteral("message"), unresolvedModules_.contains(moduleId)
             ? QStringLiteral("存在未知执行结果：新变更已锁定，权威核对后执行“解除未知结果锁”")
             : QStringLiteral("无未知执行结果")}});
    message.insert(QStringLiteral("payload"), payload);
    moduleControlRevisions_.insert(moduleId,
                                   message.value(QStringLiteral("control_revision")).toInteger(0));
    message = boundedMessage(message);
    snapshots_.insert(moduleId, message);
    broadcast(message);
}

void HubAgent::onEvent(const QJsonObject &source) {
    QJsonObject message = source;
    message.insert(QStringLiteral("instance_id"), instanceId_);
    if (!message.contains(QStringLiteral("event_id"))) message.insert(QStringLiteral("event_id"), randomId());
    message = boundedMessage(message);
    const QString kind = message.value(QStringLiteral("event_kind")).toString();
    const bool critical = kind == QStringLiteral("redemption.change")
        || kind.contains(QStringLiteral("signal"), Qt::CaseInsensitive)
        || kind.contains(QStringLiteral("alert"), Qt::CaseInsensitive)
        || kind.contains(QStringLiteral("error"), Qt::CaseInsensitive)
        || kind.contains(QStringLiteral("order"), Qt::CaseInsensitive);
    if (!critical) {
        broadcast(message);
        return;
    }
    message.insert(QStringLiteral("audit_epoch"), auditEpoch_);
    QString journalError;
    if (!auditWriter_ || !auditWriter_->enqueueCriticalAsync(message, &journalError)) {
        auditHealthy_ = false;
        QJsonObject fallback = message;
        fallback.insert(QStringLiteral("audit_persisted"), false);
        QJsonObject payload = fallback.value(QStringLiteral("payload")).toObject();
        payload.insert(QStringLiteral("audit_error"), journalError.left(500));
        fallback.insert(QStringLiteral("payload"), payload);
        broadcast(fallback);
        for (auto *socket : clients_.keys()) {
            if (!clients_.contains(socket) || !clients_[socket].helloComplete) continue;
            QJsonObject hello = helloMessage();
            hello.insert(QStringLiteral("client_config_bound"), clients_[socket].configBound);
            send(socket, hello);
        }
        return;
    }
}

void HubAgent::onCriticalRecorded(const QJsonObject &source) {
    QJsonObject event = source;
    const qint64 id = event.value(QStringLiteral("audit_event_id")).toInteger();
    if (id <= 0) return;
    event.insert(QStringLiteral("audit_persisted"), true);
    event.insert(QStringLiteral("audit_epoch"), auditEpoch_);
    event.insert(QStringLiteral("replayed"), false);
    if (criticalEventFloor_ <= 0) criticalEventFloor_ = id;
    criticalEventHighWater_ = qMax(criticalEventHighWater_, id);
    broadcast(event);
}

void HubAgent::onCriticalRecordFailed(const QJsonObject &source, const QString &error) {
    auditHealthy_ = false;
    QJsonObject fallback = source;
    fallback.insert(QStringLiteral("audit_persisted"), false);
    QJsonObject payload = fallback.value(QStringLiteral("payload")).toObject();
    payload.insert(QStringLiteral("audit_error"), error.left(500));
    fallback.insert(QStringLiteral("payload"), payload);
    broadcast(fallback);
}

void HubAgent::onCommandResult(const QJsonObject &source) {
    QJsonObject message = boundedMessage(source);
    message.insert(QStringLiteral("instance_id"), instanceId_);
    const QString commandId = message.value(QStringLiteral("command_id")).toString();
    const QString moduleId = message.value(QStringLiteral("module_id")).toString();
    const QString action = message.value(QStringLiteral("action")).toString();
    const QString state = message.value(QStringLiteral("state")).toString();
    if (!commandId.isEmpty() && !moduleId.isEmpty()) {
        if (state == QStringLiteral("accepted") || state == QStringLiteral("running")) {
            activeCommandIds_[moduleId].insert(commandId);
        } else if (state == QStringLiteral("succeeded") || state == QStringLiteral("failed")
                   || state == QStringLiteral("timed_out")) {
            activeCommandIds_[moduleId].remove(commandId);
        }
    }
    const qint64 revision = message.value(QStringLiteral("control_revision")).toInteger(-1);
    if (!moduleId.isEmpty() && revision >= 0) moduleControlRevisions_.insert(moduleId, revision);
    if (!commandId.isEmpty()) {
        const bool unresolved = revisionRequired(action) && uncertainResult(message);
        QString journalError;
        if (!commandJournal_ || !commandJournal_->updateResult(commandId, message, unresolved, &journalError)) {
            commandJournalHealthy_ = false;
            QJsonObject details = message.value(QStringLiteral("details")).toObject();
            details.insert(QStringLiteral("journal_error"), journalError);
            message.insert(QStringLiteral("details"), details);
            qWarning().noquote() << journalError;
        }
        if (unresolved) unresolvedModules_.insert(moduleId);
        if (!unresolved && action != QStringLiteral("acknowledge_uncertain")
            && unresolvedModules_.contains(moduleId) && commandJournalHealthy_) {
            bool stillUnresolved = true;
            if (commandJournal_->hasUnresolved(moduleId, &stillUnresolved, &journalError)
                && !stillUnresolved) {
                unresolvedModules_.remove(moduleId);
            } else if (!journalError.isEmpty()) {
                commandJournalHealthy_ = false;
                qWarning().noquote() << journalError;
            }
        }
        if (action == QStringLiteral("acknowledge_uncertain")
            && state == QStringLiteral("succeeded") && commandJournal_) {
            if (commandJournal_->resolveModule(moduleId, &journalError)) {
                unresolvedModules_.remove(moduleId);
            } else {
                commandJournalHealthy_ = false;
                unresolvedModules_.insert(moduleId);
                qWarning().noquote() << journalError;
            }
        }
        if (workers_.contains(moduleId)
            && (unresolved || action == QStringLiteral("acknowledge_uncertain"))) {
            QMetaObject::invokeMethod(workers_.value(moduleId), &ModuleWorker::requestSnapshot,
                                      Qt::QueuedConnection);
        }
        cacheCommandResult(commandId, commandFingerprints_.value(commandId), message);
    }
    broadcast(message);
    emit audit(message);
}

} // namespace hub
