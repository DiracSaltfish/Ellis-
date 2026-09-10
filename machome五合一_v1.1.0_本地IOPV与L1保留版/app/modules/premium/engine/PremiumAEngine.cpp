#include "modules/premium/engine/PremiumAEngine.h"

#include "modules/premium/engine/common/MarketTypes.h"
#include "modules/premium/engine/server/CoreServer.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QCoreApplication>
#include <QProcess>
#include <QProcessEnvironment>
#include <QSaveFile>
#include <QRegularExpression>

namespace machome::premium::engine {
namespace {
bool saveJson(const QString &path, const QJsonDocument &document, QString *error)
{
    QDir().mkpath(QFileInfo(path).absolutePath());
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        if (error) *error = file.errorString();
        return false;
    }
    if (file.write(document.toJson(QJsonDocument::Indented)) < 0 || !file.commit()) {
        if (error) *error = file.errorString();
        return false;
    }
    return true;
}

QJsonArray normalizedSymbols(const QJsonValue &value, const QStringList &fallback)
{
    QJsonArray result;
    QSet<QString> seen;
    const QJsonArray source = value.isArray() ? value.toArray()
                                              : QJsonArray::fromStringList(fallback);
    for (const QJsonValue &entry : source) {
        const QString symbol = normalizeSymbol(entry.toString());
        if (!symbol.isEmpty() && !seen.contains(symbol)) {
            result.append(symbol);
            seen.insert(symbol);
        }
    }
    return result;
}

QString containedCredentialPath(const QString &dataRoot, QString path)
{
    path = path.trimmed();
    if (path.startsWith(QStringLiteral("~/"))) path = QDir::homePath() + path.mid(1);
    if (!QDir::isAbsolutePath(path)) path = QDir(dataRoot).absoluteFilePath(path);
    const QFileInfo candidate(path);
    const QString canonicalFile = candidate.canonicalFilePath();
    const QString canonicalRoot = QFileInfo(dataRoot).canonicalFilePath();
    if (!candidate.isFile() || canonicalFile.isEmpty() || canonicalRoot.isEmpty()) return {};
    const QString prefix = canonicalRoot.endsWith(QDir::separator())
        ? canonicalRoot : canonicalRoot + QDir::separator();
    return canonicalFile.startsWith(prefix) ? canonicalFile : QString{};
}
} // namespace

PremiumAEngine::PremiumAEngine(QObject *parent)
    : hub::IModuleEngine(parent)
{
    helperRestartTimer_.setParent(this);
    helperRestartTimer_.setSingleShot(true);
    helperRestartClock_.start();
    connect(&helperRestartTimer_, &QTimer::timeout, this, [this] {
        if (running_ && !helperFault_) startTgwHelper();
    });
    iopvRestart_.setParent(this);
    iopvRestart_.setSingleShot(true);
    connect(&iopvRestart_, &QTimer::timeout, this, &PremiumAEngine::startIopv);
    snapshotTimer_.setParent(this);
    snapshotTimer_.setInterval(1000);
    connect(&snapshotTimer_, &QTimer::timeout, this, &PremiumAEngine::publishSnapshot);
}

PremiumAEngine::~PremiumAEngine()
{
    stop(hub::StopMode::Immediate);
}

void PremiumAEngine::initialize(const hub::ModuleContext &context)
{
    if (running_) {
        lastError_ = QStringLiteral("cannot reinitialize a running PremiumAEngine");
        return;
    }
    context_ = context;
    // Operator test mode is deliberately ephemeral. Every Agent restart or
    // engine reinitialization returns to the production schedule baseline.
    operatingMode_ = QStringLiteral("work");
    initialized_ = true;
    lastError_.clear();
}

bool PremiumAEngine::writeRuntimeConfiguration(QString *error)
{
    if (context_.dataRoot.trimmed().isEmpty()) {
        if (error) *error = QStringLiteral("premium data_root is empty");
        return false;
    }
    const QDir root(context_.dataRoot);
    if (!QDir().mkpath(root.absolutePath())) {
        if (error) *error = QStringLiteral("cannot create premium data root");
        return false;
    }
    const QString configDir = root.filePath(QStringLiteral("config"));
    QString writeError;
    auto loadOrCreate = [&](const QString &name, const QJsonValue &seed,
                            const QStringList &fallback, bool allowEmpty,
                            QJsonArray *result) {
        const QString path = QDir(configDir).filePath(name + QStringLiteral(".json"));
        if (QFileInfo::exists(path)) {
            QFile file(path);
            if (!file.open(QIODevice::ReadOnly) || file.size() > 1024 * 1024) {
                writeError = QStringLiteral("cannot read saved %1").arg(name); return false;
            }
            QJsonParseError parse;
            const auto document = QJsonDocument::fromJson(file.readAll(), &parse);
            const auto symbols = document.object().value(QStringLiteral("symbols"));
            if (parse.error != QJsonParseError::NoError || !symbols.isArray()) {
                writeError = QStringLiteral("saved %1 is damaged; preserved for recovery").arg(name);
                return false;
            }
            *result = symbols.toArray();
        } else {
            *result = normalizedSymbols(seed, fallback);
        }
        QSet<QString> seen;
        for (const auto &item : *result) {
            const QString raw = item.toString();
            const QString symbol = normalizeSymbol(raw);
            const bool valid = QRegularExpression(QStringLiteral("^[0-9]{6}\\.(SH|SZ)$")).match(symbol).hasMatch()
                || (name == QStringLiteral("l1_hotlist") && QRegularExpression(QStringLiteral("^[0-9]{5}\\.HK$")).match(symbol).hasMatch()
                    && context_.settings.value(QStringLiteral("enable_hkt_l1")).toBool(true));
            if (!item.isString() || raw != symbol || !valid || seen.contains(symbol)) {
                writeError = QStringLiteral("invalid or duplicate symbol in saved %1").arg(name);
                return false;
            }
            seen.insert(symbol);
        }
        if ((!allowEmpty && result->isEmpty()) || result->size() >
                context_.settings.value(QStringLiteral("max_upstream_symbols")).toInt(1000)) {
            writeError = QStringLiteral("invalid %1 size").arg(name); return false;
        }
        if (QFileInfo::exists(path)) return true;
        return saveJson(path, QJsonDocument(QJsonObject{{QStringLiteral("version"), 1},
                         {QStringLiteral("symbols"), *result}}), &writeError);
    };
    QJsonArray watchlist, hotlist;
    if (!loadOrCreate(QStringLiteral("watchlist"), context_.settings.value(QStringLiteral("watchlist")),
                      {QStringLiteral("159217.SZ")}, false, &watchlist)
        || !loadOrCreate(QStringLiteral("l1_hotlist"), context_.settings.value(QStringLiteral("l1_hotlist")),
                         {}, true, &hotlist)) {
        if (error) *error = writeError;
        return false;
    }

    // Runtime changes (including changes made by compatible clients) own these
    // files after first launch. Never overwrite them with initial JSON seeds.
    const QString namesPath = QDir(configDir).filePath(QStringLiteral("security_names.tsv"));
    if (!QFileInfo::exists(namesPath)) {
        QSaveFile names(namesPath);
        if (!names.open(QIODevice::WriteOnly | QIODevice::Text)) {
            if (error) *error = names.errorString(); return false;
        }
        const QJsonObject mapping = context_.settings.value(QStringLiteral("security_names")).toObject();
        for (const auto &entry : watchlist) {
            const QString symbol = entry.toString();
            QString name = mapping.value(symbol).toString(mapping.value(symbol.left(6)).toString(symbol.left(6)));
            name.replace(u'\t', u' '); name.replace(u'\n', u' '); name.replace(u'\r', u' ');
            names.write(symbol.left(6).toUtf8() + '\t' + name.toUtf8() + '\n');
        }
        if (!names.commit()) { if (error) *error = names.errorString(); return false; }
    }

    const bool notificationEnabled = !context_.recordOnly
        && context_.settings.value(QStringLiteral("notifications_enabled")).toBool(false);
    QJsonObject config{
        {QStringLiteral("iopv_port"), context_.settings.value("iopv_port").toInt(18680)},
        {QStringLiteral("local_iopv_basis"), context_.settings.value("local_iopv_basis").toString("midpoint")},
        {QStringLiteral("local_iopv_enabled"), context_.settings.value(QStringLiteral("local_iopv_enabled")).toBool(false)},
        {QStringLiteral("mode"), context_.settings.value(QStringLiteral("mode"))
             .toString(QStringLiteral("production"))},
        {QStringLiteral("listen_host"), context_.settings.value(QStringLiteral("listen_host"))
             .toString(QStringLiteral("127.0.0.1"))},
        {QStringLiteral("monitor_port"), context_.settings.value(QStringLiteral("summary_port")).toInt(8421)},
        {QStringLiteral("legacy_l1_port"), context_.settings.value(QStringLiteral("l1_port")).toInt(19195)},
        {QStringLiteral("adapter_socket"), QStringLiteral("runtime/tgw.sock")},
        {QStringLiteral("watchlist"), QStringLiteral("config/watchlist.json")},
        {QStringLiteral("l1_hotlist"), QStringLiteral("config/l1_hotlist.json")},
        {QStringLiteral("security_names"), QStringLiteral("config/security_names.tsv")},
        {QStringLiteral("data_dir"), QStringLiteral("data")},
        {QStringLiteral("log_dir"), QStringLiteral("logs")},
        {QStringLiteral("enable_hkt_l1"), context_.settings.value(QStringLiteral("enable_hkt_l1")).toBool(true)},
        {QStringLiteral("max_monitor_clients"), context_.settings.value(QStringLiteral("max_monitor_clients")).toInt(256)},
        {QStringLiteral("max_detail_symbols_per_client"), 4},
        {QStringLiteral("max_l1_clients"), context_.settings.value(QStringLiteral("max_l1_clients")).toInt(256)},
        {QStringLiteral("max_l1_symbols_per_client"), context_.settings.value(QStringLiteral("max_l1_symbols_per_client")).toInt(256)},
        {QStringLiteral("max_upstream_symbols"), context_.settings.value(QStringLiteral("max_upstream_symbols")).toInt(1000)},
        {QStringLiteral("dynamic_unsubscribe_grace_sec"), context_.settings.value(QStringLiteral("dynamic_unsubscribe_grace_sec")).toInt(60)},
        {QStringLiteral("capture_dynamic_market_data"), false},
        {QStringLiteral("pushplus"), QJsonObject{
             {QStringLiteral("enabled"), notificationEnabled},
             {QStringLiteral("startup_notification"), false},
             {QStringLiteral("endpoint"), context_.settings.value(QStringLiteral("pushplus_endpoint"))
                  .toString(QStringLiteral("https://www.pushplus.plus/send"))},
             {QStringLiteral("state_file"), QStringLiteral("runtime/pushplus-state.json")}
         }}
    };
    configPath_ = QDir(configDir).filePath(QStringLiteral("app.json"));
    if (!saveJson(configPath_, QJsonDocument(config), &writeError)) {
        if (error) *error = QStringLiteral("cannot persist native premium config: %1").arg(writeError);
        return false;
    }
    return true;
}

void PremiumAEngine::start()
{
    if (running_) return;
    helperRestartTimes_.clear();
    helperFault_ = false;
    if (!initialized_) {
        lastError_ = QStringLiteral("PremiumAEngine is not initialized");
        publishSnapshot();
        return;
    }
    QString error;
    if (!writeRuntimeConfiguration(&error)) {
        lastError_ = error;
        publishSnapshot();
        return;
    }
    core_ = std::make_unique<CoreServer>(
        configPath_, false,
        context_.settings.value(QStringLiteral("replay")).toBool(false),
        context_.settings.value(QStringLiteral("force_quotes")).toBool(false));
    connect(core_.get(), &CoreServer::nativeStatusChanged, this,
            [this](const QJsonObject &) { publishSnapshot(); });
    connect(core_.get(), &CoreServer::nativeSummaryPublished, this,
            [this](const QJsonObject &summary) {
                const QString kind = summary.value(QStringLiteral("type")).toString()
                        == QStringLiteral("symbol_removed")
                    ? QStringLiteral("premium.symbol_removed")
                    : QStringLiteral("premium.summary");
                Q_EMIT eventReady(kind, summary);
            });
    connect(core_.get(), &CoreServer::nativeDetailPublished, this,
            &PremiumAEngine::forwardNativeDetail);
    connect(core_.get(), &CoreServer::nativeSignalPublished, this,
            [this](const QJsonObject &signal) {
                Q_EMIT eventReady(QStringLiteral("premium.signal"), signal);
            });
    if (!core_->start(&error)) {
        lastError_ = error;
        core_.reset();
        publishSnapshot();
        return;
    }
    core_->setForceQuotesNative(operatingMode_ == QStringLiteral("weekend_test"));
    running_ = true;
    lastError_.clear();
    startTgwHelper();
    startIopv();
    snapshotTimer_.start();
    Q_EMIT eventReady(QStringLiteral("premium.native_started"),
                      {{QStringLiteral("record_only"), context_.recordOnly},
                       {QStringLiteral("config_path"), configPath_}});
    publishSnapshot();
}

void PremiumAEngine::stop(hub::StopMode)
{
    running_ = false;
    helperRestartTimer_.stop();
    iopvRestart_.stop();
    stopIopv();
    snapshotTimer_.stop();
    detailSubscriptions_.clear();
    stopTgwHelper();
    if (core_) core_.reset();
    running_ = false;
    publishSnapshot();
}

QJsonObject PremiumAEngine::snapshot() const
{
    QJsonObject status;
    if (core_) status = core_->currentStatus();
    const bool notificationsEnabled = !context_.recordOnly
        && context_.settings.value(QStringLiteral("notifications_enabled")).toBool(false);
    const bool desired = status.value(QStringLiteral("cn_quotes_desired")).toBool()
        || status.value(QStringLiteral("hk_quotes_desired")).toBool();
    const bool upstreamHealthy = status.value(QStringLiteral("upstream_healthy")).toBool();
    QJsonObject snapshot{
        {QStringLiteral("local_iopv_state"), iopvState_},
        {QStringLiteral("engine"), QStringLiteral("native_premium_a")},
        {QStringLiteral("state"), !lastError_.isEmpty() ? QStringLiteral("blocked")
             : running_ ? (desired ? (upstreamHealthy ? QStringLiteral("active")
                                                     : QStringLiteral("degraded"))
                                  : QStringLiteral("scheduled_idle"))
                        : QStringLiteral("stopped")},
        {QStringLiteral("running"), running_},
        {QStringLiteral("operating_mode"), operatingMode_},
        {QStringLiteral("record_only"), context_.recordOnly},
        {QStringLiteral("notifications_enabled"), notificationsEnabled},
        {QStringLiteral("b_side_trading_supported"), false},
        {QStringLiteral("tgw_helper"), QJsonObject{
             {QStringLiteral("state"), tgwHelperState_},
             {QStringLiteral("fault"), helperFault_},
             {QStringLiteral("restarts_in_window"), helperRestartTimes_.size()},
             {QStringLiteral("bundled"), true},
             {QStringLiteral("external_launch_agent"), false}}},
        {QStringLiteral("compatibility_ports"), QJsonObject{
             {QStringLiteral("summary"), context_.settings.value(QStringLiteral("summary_port")).toInt(8421)},
             {QStringLiteral("l1"), context_.settings.value(QStringLiteral("l1_port")).toInt(19195)}}},
        {QStringLiteral("detail_subscriptions"),
         QJsonArray::fromStringList(QStringList(detailSubscriptions_.begin(), detailSubscriptions_.end()))},
        {QStringLiteral("watchlist"), core_ ? core_->currentWatchlist() : QJsonArray{}},
        {QStringLiteral("l1_hotlist"), core_ ? core_->currentHotlist() : QJsonArray{}},
        {QStringLiteral("status"), status},
        {QStringLiteral("last_error"), lastError_}
    };
    return snapshot;
}

void PremiumAEngine::publishSnapshot() { Q_EMIT snapshotReady(snapshot()); }

void PremiumAEngine::startTgwHelper()
{
    stopTgwHelper();
    if (!context_.settings.value(QStringLiteral("tgw_helper_enabled")).toBool(false)) {
        tgwHelperState_ = QStringLiteral("disabled");
        return;
    }
    QString executable = QDir(QCoreApplication::applicationDirPath())
        .absoluteFilePath(QStringLiteral("../Helpers/machome-premium-tgw-helper"));
    if (context_.settings.value(QStringLiteral("test_mode")).toBool(false)) {
        const QString testHelper = context_.settings.value(QStringLiteral("test_tgw_helper")).toString();
        if (testHelper.startsWith(QStringLiteral("/private/tmp/"))) executable = testHelper;
    }
    if (!QFileInfo(executable).isExecutable()) {
        tgwHelperState_ = QStringLiteral("missing");
        lastError_ = QStringLiteral("bundled TGW helper is unavailable");
        return;
    }
    QStringList arguments{
        QStringLiteral("--socket"), QDir(context_.dataRoot).filePath(QStringLiteral("runtime/tgw.sock")),
        QStringLiteral("--watchlist"), QDir(context_.dataRoot).filePath(QStringLiteral("config/watchlist.json")),
        QStringLiteral("--log"), QDir(context_.dataRoot).filePath(QStringLiteral("logs/tgw-helper.jsonl"))
    };
    if (context_.recordOnly
        || context_.settings.value(QStringLiteral("tgw_simulation")).toBool(false)) {
        arguments.append(QStringLiteral("--simulate"));
    } else {
        const QString account = containedCredentialPath(
            context_.dataRoot,
            context_.settings.value(QStringLiteral("tgw_account_file")).toString());
        if (account.isEmpty()) {
            tgwHelperState_ = QStringLiteral("configuration_error");
            lastError_ = QStringLiteral(
                "live TGW helper requires tgw_account_file inside the Premium data root");
            return;
        }
        arguments.append({QStringLiteral("--account"), account});
        const QString requestedUsername = context_.settings
            .value(QStringLiteral("tgw_username_file")).toString();
        if (!requestedUsername.trimmed().isEmpty()) {
            const QString username = containedCredentialPath(context_.dataRoot, requestedUsername);
            if (username.isEmpty()) {
                tgwHelperState_ = QStringLiteral("configuration_error");
                lastError_ = QStringLiteral(
                    "tgw_username_file must be inside the Premium data root");
                return;
            }
            arguments.append({QStringLiteral("--username-file"), username});
        }
        const QString ca = QDir(QCoreApplication::applicationDirPath())
            .absoluteFilePath(QStringLiteral("../Resources/premium/vendor-dgw-ca.crt"));
        arguments.append({QStringLiteral("--ca-file"), ca});
    }
    tgwHelper_ = new QProcess(this);
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.remove(QStringLiteral("DYLD_INSERT_LIBRARIES"));
    environment.remove(QStringLiteral("DYLD_LIBRARY_PATH"));
    environment.remove(QStringLiteral("PYTHONPATH"));
    tgwHelper_->setProcessEnvironment(environment);
    tgwHelper_->setWorkingDirectory(context_.dataRoot);
    tgwHelper_->setProgram(executable);
    tgwHelper_->setArguments(arguments);
    connect(tgwHelper_, &QProcess::started, this, [this] {
        tgwHelperState_ = QStringLiteral("running");
        lastError_.clear();
        publishSnapshot();
    });
    connect(tgwHelper_, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        tgwHelperState_ = QStringLiteral("failed");
        lastError_ = tgwHelper_ ? tgwHelper_->errorString()
                               : QStringLiteral("TGW helper failed");
        scheduleTgwRecovery();
        publishSnapshot();
    });
    connect(tgwHelper_,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this, [this](int code, QProcess::ExitStatus status) {
        if (tgwHelperState_ != QStringLiteral("stopping")) {
            tgwHelperState_ = QStringLiteral("exited");
            lastError_ = QStringLiteral("TGW helper exited (%1/%2)")
                .arg(code).arg(static_cast<int>(status));
            scheduleTgwRecovery();
            publishSnapshot();
        }
    });
    tgwHelperState_ = QStringLiteral("starting");
    tgwHelper_->start();
}

void PremiumAEngine::scheduleTgwRecovery()
{
    if (!running_ || helperFault_ || helperRestartTimer_.isActive()) return;
    const qint64 now = helperRestartClock_.elapsed();
    while (!helperRestartTimes_.isEmpty() && now - helperRestartTimes_.front() >= 300000)
        helperRestartTimes_.removeFirst();
    if (helperRestartTimes_.size() >= 3) {
        helperFault_ = true;
        tgwHelperState_ = QStringLiteral("fault");
        lastError_ = QStringLiteral("TGW 五分钟内已重启三次；请检查后人工重启模块");
        return;
    }
    helperRestartTimes_.append(now);
    tgwHelperState_ = QStringLiteral("recovering");
    const int delay = context_.settings.value(QStringLiteral("test_mode")).toBool(false)
        ? qBound(10, context_.settings.value(QStringLiteral("test_helper_restart_ms")).toInt(1000), 1000)
        : 1000;
    helperRestartTimer_.start(delay);
}

void PremiumAEngine::stopTgwHelper()
{
    if (!tgwHelper_) return;
    tgwHelperState_ = QStringLiteral("stopping");
    tgwHelper_->disconnect(this);
    tgwHelper_->terminate();
    if (!tgwHelper_->waitForFinished(3000)) {
        tgwHelper_->kill();
        tgwHelper_->waitForFinished(1000);
    }
    delete tgwHelper_;
    tgwHelper_ = nullptr;
    tgwHelperState_ = QStringLiteral("stopped");
}

void PremiumAEngine::complete(const QString &commandId, bool ok,
                              const QString &message, const QJsonObject &details)
{
    Q_EMIT commandFinished(commandId, ok, message, details);
}

void PremiumAEngine::forwardNativeDetail(const QJsonObject &detail)
{
    const QString symbol = normalizeSymbol(detail.value(QStringLiteral("s")).toString(
        detail.value(QStringLiteral("symbol")).toString()));
    if (!symbol.isEmpty() && detailSubscriptions_.contains(symbol))
        Q_EMIT eventReady(QStringLiteral("premium.detail"), detail);
}

void PremiumAEngine::publishSync()
{
    Q_EMIT eventReady(QStringLiteral("premium.sync_begin"),
                      {{QStringLiteral("type"), QStringLiteral("sync_begin")}});
    if (core_) {
        for (const QJsonObject &summary : core_->currentSummaries()) {
            Q_EMIT eventReady(QStringLiteral("premium.summary"), summary);
        }
        for (QJsonObject signal : core_->currentSignals()) {
            signal.insert(QStringLiteral("backfill"), true);
            Q_EMIT eventReady(QStringLiteral("premium.signal"), signal);
        }
    }
    Q_EMIT eventReady(QStringLiteral("premium.sync_complete"),
                      {{QStringLiteral("type"), QStringLiteral("sync_complete")}});
}

void PremiumAEngine::submitCommand(const QString &action, const QJsonObject &arguments,
                                   const QString &commandId)
{
    static const QSet<QString> forbidden{
        QStringLiteral("order"), QStringLiteral("place_order"),
        QStringLiteral("cancel_order"), QStringLiteral("account"),
        QStringLiteral("redeem"), QStringLiteral("subscribe_fund")
    };
    if (forbidden.contains(action) || action.startsWith(QStringLiteral("qmt_"))
        || action.startsWith(QStringLiteral("trade_"))) {
        complete(commandId, false,
                 QStringLiteral("Premium A 原生引擎不包含且拒绝 B 端交易能力"),
                 {{QStringLiteral("code"), QStringLiteral("b_side_command_forbidden")}});
        return;
    }
    if (action == QStringLiteral("set_operating_mode")) {
        const QString mode = arguments.value(QStringLiteral("mode")).toString();
        if (mode != QStringLiteral("work") && mode != QStringLiteral("weekend_test")) {
            complete(commandId, false, QStringLiteral("运行模式必须为工作模式或周末测试模式"),
                     {{QStringLiteral("mode"), mode}});
        } else {
            operatingMode_ = mode;
            // The old standalone A side exposes force-quotes for weekend and
            // off-hours diagnosis. It never opens the real signal time gate.
            if (core_) {
                core_->setForceQuotesNative(mode == QStringLiteral("weekend_test"));
            }
            complete(commandId, true,
                     mode == QStringLiteral("work")
                         ? QStringLiteral("已恢复工作模式，严格按原 A 端时段运行")
                         : QStringLiteral("已进入周末测试模式，仅强制行情订阅；真实信号时段未放宽"),
                     {{QStringLiteral("operating_mode"), operatingMode_},
                      {QStringLiteral("force_quotes"),
                       mode == QStringLiteral("weekend_test")},
                      {QStringLiteral("signal_schedule_preserved"), true}});
        }
        publishSnapshot();
        return;
    }
    if (!running_ || !core_) {
        complete(commandId, false, QStringLiteral("Premium A 原生引擎未运行"));
        return;
    }
    if (action == QStringLiteral("refresh") || action == QStringLiteral("premium_status")) {
        publishSnapshot();
        complete(commandId, true, QStringLiteral("原生 A 端状态已刷新"), core_->currentStatus());
    } else if (action == QStringLiteral("premium_sync")) {
        publishSync();
        complete(commandId, true, QStringLiteral("原生 A 端同步完成"));
    } else if (action == QStringLiteral("premium_raw_snapshot")) {
        QJsonObject raw = core_->latestRawRecord();
        const bool available = !raw.isEmpty();
        raw.insert(QStringLiteral("type"), QStringLiteral("raw_snapshot"));
        raw.insert(QStringLiteral("available"), available);
        Q_EMIT eventReady(QStringLiteral("premium.raw_snapshot"), raw);
        complete(commandId, true, QStringLiteral("原始快照已返回"), raw);
    } else if (action == QStringLiteral("premium_detail_subscribe")) {
        const QString symbol = normalizeSymbol(arguments.value(QStringLiteral("symbol")).toString());
        if (symbol.isEmpty()) {
            complete(commandId, false, QStringLiteral("详情代码无效"));
        } else if (!detailSubscriptions_.contains(symbol) && detailSubscriptions_.size() >= 4) {
            complete(commandId, false, QStringLiteral("详情订阅上限为 4"));
        } else {
            detailSubscriptions_.insert(symbol);
            QJsonObject ack{{QStringLiteral("type"), QStringLiteral("detail_ack")},
                            {QStringLiteral("op"), QStringLiteral("subscribe")},
                            {QStringLiteral("symbol"), symbol}};
            Q_EMIT eventReady(QStringLiteral("premium.detail_ack"), ack);
            const QJsonObject cached = core_->currentDetail(symbol);
            if (!cached.isEmpty()) Q_EMIT eventReady(QStringLiteral("premium.detail"), cached);
            complete(commandId, true, QStringLiteral("详情订阅已由原生 A 端确认"), ack);
        }
    } else if (action == QStringLiteral("premium_detail_unsubscribe")) {
        const QString symbol = normalizeSymbol(arguments.value(QStringLiteral("symbol")).toString());
        detailSubscriptions_.remove(symbol);
        QJsonObject ack{{QStringLiteral("type"), QStringLiteral("detail_ack")},
                        {QStringLiteral("op"), QStringLiteral("unsubscribe")},
                        {QStringLiteral("symbol"), symbol}};
        Q_EMIT eventReady(QStringLiteral("premium.detail_ack"), ack);
        complete(commandId, true, QStringLiteral("详情退订已确认"), ack);
    } else if (action == QStringLiteral("premium_set_watchlist")
               || action == QStringLiteral("premium_set_l1_hotlist")) {
        QJsonObject result;
        QString error;
        const bool ok = action == QStringLiteral("premium_set_watchlist")
            ? core_->replaceWatchlistNative(
                  arguments.value(QStringLiteral("symbols")).toArray(), &result, &error)
            : core_->replaceHotlistNative(
                  arguments.value(QStringLiteral("symbols")).toArray(), &result, &error);
        const QString kind = action == QStringLiteral("premium_set_watchlist")
            ? QStringLiteral("premium.watchlist_ack")
            : QStringLiteral("premium.l1_hotlist_ack");
        if (!ok) {
            result = {{QStringLiteral("accepted"), false},
                      {QStringLiteral("error"), error}};
        }
        Q_EMIT eventReady(kind, result);
        complete(commandId, ok,
                 ok ? QStringLiteral("原生 A 端清单已更新") : error, result);
    } else {
        complete(commandId, false, QStringLiteral("未知 Premium A 命令"),
                 {{QStringLiteral("action"), action}});
    }
    publishSnapshot();
}
} // namespace machome::premium::engine

namespace machome::premium::engine {
void PremiumAEngine::startIopv() {
 if (!running_ || iopv_ || !context_.settings.value("local_iopv_enabled").toBool(false)) return;
 const QDir app(QCoreApplication::applicationDirPath());
 const QString binary=app.absoluteFilePath("../Helpers/machome-iopv-server");
 const QString universe=app.absoluteFilePath("../Resources/iopv/universe.json");
 const QString root=QDir(context_.dataRoot).filePath("local-iopv");
 const QString config=QDir(root).filePath("config.json");
 QString error;
 if (!QFileInfo(binary).isExecutable() || !QFileInfo::exists(universe) || !saveJson(config,QJsonDocument(QJsonObject{
 {"listen",context_.settings.value("iopv_listen").toString("0.0.0.0:18680")},
 {"signal_basis",context_.settings.value("local_iopv_basis").toString("midpoint")},
 {"data_dir",QDir(root).filePath("data")},{"universe_file",universe},
 {"l1_address",context_.settings.value("test_mode").toBool(false) ? context_.settings.value("test_iopv_l1_address").toString(QString("127.0.0.1:%1").arg(context_.settings.value("l1_port").toInt(19195))) : QString("127.0.0.1:%1").arg(context_.settings.value("l1_port").toInt(19195))},
 {"parent_pid",static_cast<qint64>(QCoreApplication::applicationPid())}}),&error)) {
 iopvState_="configuration_error";publishSnapshot();return;
 }
 iopv_=new QProcess(this);iopv_->setProgram(binary);iopv_->setArguments({"-config",config});
 iopv_->setStandardOutputFile(QDir(root).filePath("service.log"),QIODevice::Append);
 iopv_->setStandardErrorFile(QDir(root).filePath("service.log"),QIODevice::Append);
 connect(iopv_,&QProcess::started,this,[this]{iopvState_="running";publishSnapshot();});
 auto failed=[this]{if(!iopv_)return;iopv_->deleteLater();iopv_=nullptr;iopvState_="restarting";if(running_)iopvRestart_.start(10000);publishSnapshot();};
 connect(iopv_,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[failed](int,QProcess::ExitStatus){failed();});
 connect(iopv_,&QProcess::errorOccurred,this,[failed](QProcess::ProcessError e){if(e==QProcess::FailedToStart)failed();});
 iopvState_="starting";iopv_->start();
}
void PremiumAEngine::stopIopv(){
 if(!iopv_)return;auto *p=iopv_;iopv_=nullptr;p->disconnect(this);p->terminate();
 if(!p->waitForFinished(6000)){p->kill();p->waitForFinished(1000);}delete p;iopvState_="stopped";
}
}
