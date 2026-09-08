#include "agent/ModuleWorker.h"

#include "agent/LifecycleController.h"
#include "common/JsonUtil.h"
#include "modules/bridge/RealtimeClient.h"
#include "modules/upload/UploadEngine.h"
#include "modules/monitor_sync/MonitorSyncEngine.h"
#include "modules/premium/PremiumClient.h"
#include "modules/premium/engine/PremiumAEngine.h"
#include "modules/qmt/QmtClient.h"
#include "modules/redemption/RedemptionEngine.h"
#include "modules/webull/WebullEngine.h"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSet>
#include <QUrl>
#include <algorithm>
#include <cmath>
#include <utility>

namespace hub {
namespace {

QString channelStateName(machome::premium::PremiumClient::ChannelState state) {
    using State = machome::premium::PremiumClient::ChannelState;
    switch (state) {
    case State::Stopped: return QStringLiteral("stopped");
    case State::Connecting: return QStringLiteral("connecting");
    case State::Connected: return QStringLiteral("connected");
    case State::Stale: return QStringLiteral("stale");
    case State::Backoff: return QStringLiteral("backoff");
    }
    return QStringLiteral("unknown");
}

QString iso(const QDateTime &value) {
    return value.isValid() ? value.toUTC().toString(Qt::ISODateWithMs) : QString();
}

QJsonObject priceLevelJson(const Machome::Webull::PriceLevel &level) {
    return {{QStringLiteral("level"), level.level}, {QStringLiteral("price"), level.price},
            {QStringLiteral("size"), level.volume}};
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

bool premiumDetailObservationAction(const QString &action) {
    return action == QStringLiteral("premium_detail_subscribe")
        || action == QStringLiteral("premium_detail_unsubscribe");
}

bool criticalEventKind(const QString &kind) {
    return kind == QStringLiteral("redemption.change")
        || kind.contains(QStringLiteral("signal"), Qt::CaseInsensitive)
        || kind.contains(QStringLiteral("alert"), Qt::CaseInsensitive)
        || kind.contains(QStringLiteral("error"), Qt::CaseInsensitive)
        || kind.contains(QStringLiteral("order"), Qt::CaseInsensitive);
}

bool nonNegativeInteger(const QJsonValue &value) {
    if (!value.isDouble()) return false;
    const double number = value.toDouble(-1.0);
    return std::isfinite(number) && number >= 0.0
        && std::floor(number) == number;
}

} // namespace

ModuleWorker::ModuleWorker(ModuleConfig config, quint64 initialControlRevision, QObject *parent)
    : ModuleBackend(std::move(config), parent), controlRevision_(initialControlRevision) {
    // ModuleWorker is constructed on the agent thread and then moved to its
    // dedicated module thread. Parenting the value-member timer makes Qt move
    // its thread affinity together with the worker; otherwise queued adapter
    // updates cannot safely start the debounce timer.
    publishTimer_.setParent(this);
    publishTimer_.setSingleShot(true);
    publishTimer_.setInterval(100);
    connect(&publishTimer_, &QTimer::timeout, this, &ModuleWorker::publishSnapshot);
    eventDrainTimer_.setParent(this);
    eventDrainTimer_.setSingleShot(true);
    // Adapter callbacks keep ingesting at full speed. Only the observational
    // IPC stream is coalesced here; this prevents hundreds of summaries from
    // scheduling GUI work every 50 ms.
    eventDrainTimer_.setInterval(250);
    connect(&eventDrainTimer_, &QTimer::timeout, this, &ModuleWorker::drainEventMailbox);
    lifecycleReadinessTimer_.setParent(this);
    lifecycleReadinessTimer_.setInterval(500);
    connect(&lifecycleReadinessTimer_, &QTimer::timeout,
            this, &ModuleWorker::checkLifecycleReadiness);
}

ModuleWorker::~ModuleWorker() = default;

void ModuleWorker::start() {
    if (started_) return;
    started_ = true;
    lifecycle_ = new LifecycleController(config_, this);
    connect(lifecycle_, &LifecycleController::snapshotChanged, this, [this](const QJsonObject &snapshot) {
        process_ = snapshot;
        if (config_.adapter == QStringLiteral("realtime")) {
            updateRealtimeServiceIdentity(false);
        }
        scheduleSnapshot();
    });
    connect(lifecycle_, &LifecycleController::commandFinished, this, [this](const QJsonObject &result) {
        const QJsonObject command{{QStringLiteral("command_id"), result.value(QStringLiteral("command_id"))},
                                  {QStringLiteral("action"), result.value(QStringLiteral("action"))}};
        QJsonObject details = result;
        details.remove(QStringLiteral("command_id"));
        details.remove(QStringLiteral("module_id"));
        details.remove(QStringLiteral("action"));
        details.remove(QStringLiteral("state"));
        details.remove(QStringLiteral("message"));
        const QString state = result.value(QStringLiteral("state")).toString();
        const QString action = result.value(QStringLiteral("action")).toString();
        const QString lifecycleUnknownKey = QStringLiteral("lifecycle_unknown:")
            + command.value(QStringLiteral("command_id")).toString();
        if (state == QStringLiteral("succeeded")
            && (action == QStringLiteral("start_service")
                || action == QStringLiteral("restart_service"))) {
            pendingTransportQuiescenceUntilMs_.remove(lifecycleUnknownKey);
            if (!result.value(QStringLiteral("target_unit")).toString().isEmpty()) {
                details.insert(QStringLiteral("module_readiness_not_evaluated"), true);
                publishCommand(command, QStringLiteral("succeeded"),
                               result.value(QStringLiteral("message")).toString(), details);
                return;
            }
            beginLifecycleReadiness(result);
            return;
        }
        if (details.value(QStringLiteral("outcome_uncertain")).toBool(false)) {
            const QString key = QStringLiteral("lifecycle_unknown:")
                + command.value(QStringLiteral("command_id")).toString();
            pendingCommands_.insert(key, command);
            timedOutPending_.insert(key);
            publishCommand(command, QStringLiteral("timed_out"),
                           result.value(QStringLiteral("message")).toString(), details);
            return;
        }
        pendingTransportQuiescenceUntilMs_.remove(lifecycleUnknownKey);
        publishCommand(command, state, result.value(QStringLiteral("message")).toString(), details);
    });
    connect(lifecycle_, &LifecycleController::logEvent, this, [this](const QJsonObject &event) {
        publishEvent(event.value(QStringLiteral("event_kind")).toString(
                         QStringLiteral("process.log")), event);
    });
    lifecycle_->start();

    if (config_.adapter == QStringLiteral("monitor_sync")) startMonitorSync();
    else if (config_.adapter == QStringLiteral("upload")) startUpload();
    else if (config_.adapter == QStringLiteral("premium")) startPremium();
    else if (config_.adapter == QStringLiteral("webull")) startWebull();
    else if (config_.engine == QStringLiteral("native")) startRedemption();
    else startRealtime();
    publishSnapshot();
}

void ModuleWorker::stop() {
    if (!started_) return;
    publishTimer_.stop();
    lifecycleReadinessTimer_.stop();
    ++lifecycleReadinessGeneration_;
    lifecycleReadinessProbeIssued_ = false;
    readinessAdaptersSuspended_ = false;
    if (sync_) sync_->stop();
    if (upload_) upload_->stop();
    if (premium_) premium_->stop();
    if (premiumProbe_) premiumProbe_->stop();
    if (webull_) webull_->stop();
    if (realtime_) realtime_->stop();
    if (redemption_) redemption_->stop();
    for (auto *client : std::as_const(qmtClients_)) client->disconnectBackend();
    if (lifecycle_) lifecycle_->stop();
    // Timers can be stopped only after the transports, because their shutdown
    // callbacks may enqueue a final critical state/error. Flush every critical
    // item into HubAgent's durable audit ingress before this thread quits.
    eventDrainTimer_.stop();
    while (!criticalEventMailbox_.isEmpty()) {
        const QJsonObject message = criticalEventMailbox_.dequeue();
        criticalEventMailboxBytes_ = qMax<qint64>(
            0, criticalEventMailboxBytes_
                   - QJsonDocument(message).toJson(QJsonDocument::Compact).size());
        emit detailEvent(message);
    }
    latestEventMailbox_.clear();
    ordinaryEventMailbox_.clear();
    started_ = false;
}

void ModuleWorker::startUpload() {
    upload_ = new machome::upload::UploadEngine(this);
    connect(upload_, &machome::upload::UploadEngine::snapshotReady, this,
            [this](const QJsonObject &snapshot) {
        telemetry_.insert(QStringLiteral("engine"), snapshot);
        telemetry_.insert(QStringLiteral("workers"), snapshot.value(QStringLiteral("jobs")));
        telemetry_.insert(QStringLiteral("worker_count"),
                          snapshot.value(QStringLiteral("jobs")).toArray().size());
        telemetry_.insert(QStringLiteral("funds"), snapshot.value(QStringLiteral("funds")));
        telemetry_.insert(QStringLiteral("upload_records"),
                          snapshot.value(QStringLiteral("upload_records")));
        telemetry_.insert(QStringLiteral("job_runs"), snapshot.value(QStringLiteral("job_runs")));
        telemetry_.insert(QStringLiteral("history"), snapshot.value(QStringLiteral("history")));
        telemetry_.insert(QStringLiteral("workers_expected"),
                          snapshot.contains(QStringLiteral("collection_expected"))
                              ? snapshot.value(QStringLiteral("collection_expected"))
                              : QJsonValue(true));
        workState_ = snapshot.value(QStringLiteral("state")).toString();
        if (workState_ == QStringLiteral("running")) workState_ = QStringLiteral("active");
        lastError_ = snapshot.value(QStringLiteral("last_error")).toString();
        headline_ = !snapshot.value(QStringLiteral("running")).toBool(false)
            ? QStringLiteral("原生 Upload 模块已停止")
            : snapshot.value(QStringLiteral("record_only")).toBool()
                ? QStringLiteral("原生 UploadEngine 已就绪；外部只读采集已放行，上传仍仅记录")
                : QStringLiteral("原生 UploadEngine 状态已更新");
        markTelemetryObserved(QStringLiteral("engine"));
        scheduleSnapshot();
    });
    connect(upload_, &machome::upload::UploadEngine::eventReady, this,
            [this](const QString &kind, const QJsonObject &payload) {
        publishEvent(kind, payload);
    });
    connect(upload_, &machome::upload::UploadEngine::commandFinished, this,
            [this](const QString &commandId, bool ok, const QString &message,
                   const QJsonObject &details) {
        finishPending(QStringLiteral("upload:") + commandId, ok, message, details);
    });
    QString dataRoot = AppConfig::expandPath(
        config_.settings.value(QStringLiteral("data_root"))
            .toString(QStringLiteral("~/Library/Application Support/MachomeHub/data/upload")));
    upload_->initialize(hub::ModuleContext{config_.id, dataRoot, config_.settings, true});
    upload_->start();
}

void ModuleWorker::startPremium() {
    if (config_.engine != QStringLiteral("native")) {
        startPremiumProbe();
        return;
    }
    premium_ = new machome::premium::engine::PremiumAEngine(this);
    connect(premium_, &machome::premium::engine::PremiumAEngine::snapshotReady, this,
            [this](const QJsonObject &snapshot) {
        telemetry_.insert(QStringLiteral("engine"), snapshot);
        const QJsonObject status = snapshot.value(QStringLiteral("status")).toObject();
        telemetry_.insert(QStringLiteral("status"), status);
        telemetry_.insert(QStringLiteral("watchlist"),
                          snapshot.value(QStringLiteral("watchlist")));
        telemetry_.insert(QStringLiteral("l1_hotlist"),
                          snapshot.value(QStringLiteral("l1_hotlist")));
        telemetry_.insert(QStringLiteral("detail_channel"), QJsonObject{
            {QStringLiteral("state"), snapshot.value(QStringLiteral("running")).toBool()
                 ? QStringLiteral("connected") : QStringLiteral("stopped")},
            {QStringLiteral("detail"), QStringLiteral("四合一进程内原生事件通道")}});
        telemetry_.insert(QStringLiteral("detail_subscriptions"), QJsonObject{
            {QStringLiteral("acknowledged"),
             snapshot.value(QStringLiteral("detail_subscriptions"))}});
        telemetry_.insert(QStringLiteral("premium_readiness"), QJsonObject{
            {QStringLiteral("ready"), snapshot.value(QStringLiteral("running")).toBool()},
            {QStringLiteral("native"), true},
            {QStringLiteral("b_side_trading_supported"), false}});
        workState_ = snapshot.value(QStringLiteral("state")).toString();
        lastError_ = snapshot.value(QStringLiteral("last_error")).toString();
        headline_ = status.value(QStringLiteral("phase")).toString(
            snapshot.value(QStringLiteral("running")).toBool()
                ? QStringLiteral("原生 Premium A 已运行")
                : QStringLiteral("原生 Premium A 已停止"));
        markTelemetryObserved(QStringLiteral("engine"));
        markTelemetryObserved(QStringLiteral("status"));
        scheduleSnapshot();
    });
    connect(premium_, &machome::premium::engine::PremiumAEngine::eventReady, this,
            [this](const QString &kind, const QJsonObject &payload) {
        if (kind == QStringLiteral("premium.signal")) {
            telemetry_.insert(QStringLiteral("signal_count"), ++signalCount_);
        }
        publishEvent(kind, payload);
    });
    connect(premium_, &machome::premium::engine::PremiumAEngine::commandFinished, this,
            [this](const QString &commandId, bool ok, const QString &message,
                   const QJsonObject &details) {
        finishPending(QStringLiteral("premium:") + commandId, ok, message, details);
    });
    const QString dataRoot = AppConfig::expandPath(
        config_.settings.value(QStringLiteral("data_root"))
            .toString(QStringLiteral("~/Library/Application Support/MachomeHub/data/premium")));
    premium_->initialize(hub::ModuleContext{
        config_.id, dataRoot, config_.settings,
        config_.settings.value(QStringLiteral("record_only")).toBool(true)});
    premium_->start();
}

void ModuleWorker::startPremiumProbe() {
    machome::premium::PremiumClient::Config options;
    options.host = config_.settings.value(QStringLiteral("host"))
                       .toString(QStringLiteral("127.0.0.1"));
    options.summaryPort = static_cast<quint16>(
        config_.settings.value(QStringLiteral("summary_port")).toInt(8421));
    options.l1Port = static_cast<quint16>(
        config_.settings.value(QStringLiteral("l1_port")).toInt(19195));
    options.maximumWebSocketMessageBytes = config_.settings
        .value(QStringLiteral("maximum_message_bytes")).toInteger(512 * 1024);
    premiumProbe_ = new machome::premium::PremiumClient(options, this);
    connect(premiumProbe_, &machome::premium::PremiumClient::summaryStateChanged,
            this, [this](auto state, const QString &detail) {
        telemetry_.insert(QStringLiteral("summary_channel"),
                          QJsonObject{{QStringLiteral("state"), channelStateName(state)},
                                      {QStringLiteral("detail"), detail}});
        scheduleSnapshot();
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::l1StateChanged,
            this, [this](auto state, const QString &detail) {
        telemetry_.insert(QStringLiteral("l1_channel"),
                          QJsonObject{{QStringLiteral("state"), channelStateName(state)},
                                      {QStringLiteral("detail"), detail}});
        scheduleSnapshot();
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::statusReceived,
            this, [this](const QJsonObject &status) {
        telemetry_.insert(QStringLiteral("status"), status);
        const bool desired = status.value(QStringLiteral("cn_quotes_desired")).toBool()
            || status.value(QStringLiteral("hk_quotes_desired")).toBool();
        workState_ = desired ? QStringLiteral("active")
                             : QStringLiteral("scheduled_idle");
        headline_ = status.value(QStringLiteral("phase")).toString();
        scheduleSnapshot();
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::l1StatusReceived,
            this, [this](const QJsonObject &status) {
        telemetry_.insert(QStringLiteral("l1_status"), status);
        publishEvent(QStringLiteral("premium.l1_status"), status);
        scheduleSnapshot();
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::summaryReceived,
            this, [this](const QString &symbol, QJsonObject summary) {
        summary.insert(QStringLiteral("symbol"), symbol);
        publishEvent(QStringLiteral("premium.summary"), summary);
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::detailReceived,
            this, [this](const QString &symbol, QJsonObject detail) {
        detail.insert(QStringLiteral("symbol"), symbol);
        publishEvent(QStringLiteral("premium.detail"), detail);
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::detailAcknowledged,
            this, [this](const QString &op, const QString &symbol, QJsonObject ack) {
        ack.insert(QStringLiteral("symbol"), symbol);
        publishEvent(QStringLiteral("premium.detail_ack"), ack);
        finishPending((op == QStringLiteral("subscribe")
                           ? QStringLiteral("premium_detail_subscribe:")
                           : QStringLiteral("premium_detail_unsubscribe:")) + symbol,
                      true, QStringLiteral("详情订阅状态已确认"), ack);
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::signalReceived,
            this, [this](const QString &symbol, QJsonObject signal) {
        signal.insert(QStringLiteral("symbol"), symbol);
        telemetry_.insert(QStringLiteral("signal_count"), ++signalCount_);
        publishEvent(QStringLiteral("premium.signal"), signal);
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::syncBeginReceived,
            this, [this](const QJsonObject &event) {
        publishEvent(QStringLiteral("premium.sync_begin"), event);
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::syncCompleteReceived,
            this, [this](const QJsonObject &event) {
        publishEvent(QStringLiteral("premium.sync_complete"), event);
        finishPending(QStringLiteral("premium_sync"), true,
                      QStringLiteral("LegacyProbe 同步完成"), event);
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::rawSnapshotReceived,
            this, [this](const QJsonObject &event) {
        publishEvent(QStringLiteral("premium.raw_snapshot"), event);
        finishPending(QStringLiteral("premium_raw_snapshot"), true,
                      QStringLiteral("原始快照已返回"), event);
    });
    connect(premiumProbe_, &machome::premium::PremiumClient::protocolError,
            this, [this](const QString &channel, const QString &reason) {
        lastError_ = QStringLiteral("%1: %2").arg(channel, reason);
        publishEvent(QStringLiteral("premium.protocol_error"),
                     {{QStringLiteral("channel"), channel},
                      {QStringLiteral("message"), reason}});
        scheduleSnapshot();
    });
    premiumProbe_->start();
}

void ModuleWorker::startWebull() {
    webull_ = new machome::webull::WebullEngine(this);
    connect(webull_, &machome::webull::WebullEngine::commandFinished, this,
            [this](const QString &commandId, bool ok, const QString &message,
                   const QJsonObject &details) {
        finishPending(QStringLiteral("webull-engine:") + commandId,
                      ok, message, details);
    });
    connect(webull_, &machome::webull::WebullEngine::statusUpdated, this, [this](const Machome::Webull::GatewayStatus &status) {
        telemetry_.insert(QStringLiteral("status"), webullStatusJson(status));
        markTelemetryObserved(QStringLiteral("status"), status.observedAt.toMSecsSinceEpoch());
        telemetry_.insert(QStringLiteral("client_count"), status.clientCount);
        workState_ = status.collectorRunning && status.dataFresh ? QStringLiteral("active")
                     : !status.collectorRunning && status.apiLive ? QStringLiteral("scheduled_idle")
                     : status.apiLive ? QStringLiteral("degraded") : QStringLiteral("blocked");
        const auto statusText = [](const QString &state) {
            static const QHash<QString, QString> names{
                {QStringLiteral("unknown"), QStringLiteral("未知")},
                {QStringLiteral("authenticated"), QStringLiteral("已登录")},
                {QStringLiteral("login_required"), QStringLiteral("需要登录")},
                {QStringLiteral("captcha_required"), QStringLiteral("需要验证码")},
                {QStringLiteral("no_data"), QStringLiteral("暂无数据")},
                {QStringLiteral("flowing"), QStringLiteral("行情正常")},
                {QStringLiteral("stale"), QStringLiteral("行情已过期")},
            };
            return names.value(state, state);
        };
        headline_ = status.apiLive
            ? QStringLiteral("API 在线 · 登录 %1 · 数据 %2")
                  .arg(statusText(status.authState), statusText(status.dataState))
            : QStringLiteral("Webull API 不可用");
        if (!status.lastError.isEmpty()) lastError_ = status.lastError;
        verifyWebullControlPostconditions(status);
        scheduleSnapshot();
    });
    connect(webull_, &machome::webull::WebullEngine::bookUpdated, this, [this](const Machome::Webull::BookSnapshot &book) {
        const auto object = webullBookJson(book);
        telemetry_.insert(QStringLiteral("book"), object);
        markTelemetryObserved(QStringLiteral("book"));
        telemetry_.insert(QStringLiteral("freshness"), book.fresh ? QStringLiteral("fresh") : QStringLiteral("stale"));
        publishEvent(QStringLiteral("webull.book"), object);
        finishPending(QStringLiteral("webull_get_book"), true, QStringLiteral("盘口已刷新"), object);
        scheduleSnapshot();
    });
    connect(webull_, &machome::webull::WebullEngine::clientsUpdated, this, [this](const Machome::Webull::ClientList &clients) {
        QJsonArray array;
        for (const auto &client : clients) {
            array.append(QJsonObject{{QStringLiteral("client_id"), client.clientId},
                                     {QStringLiteral("remote"), client.remote},
                                     {QStringLiteral("connected_at"), iso(client.connectedAt)},
                                     {QStringLiteral("last_sent_at"), iso(client.lastSentAt)},
                                     {QStringLiteral("message_count"), client.messagesSent}});
        }
        telemetry_.insert(QStringLiteral("clients"), array);
        telemetry_.insert(QStringLiteral("client_count"), array.size());
        publishEvent(QStringLiteral("webull.clients"), {{QStringLiteral("clients"), array}});
        scheduleSnapshot();
    });
    connect(webull_, &machome::webull::WebullEngine::symbolsUpdated, this, [this](const Machome::Webull::SymbolList &symbols) {
        QJsonArray array;
        for (const auto &symbol : symbols) {
            array.append(QJsonObject{{QStringLiteral("symbol"), symbol.symbol},
                                     {QStringLiteral("ticker_id"), symbol.tickerId},
                                     {QStringLiteral("depth_size"), symbol.depthSize},
                                     {QStringLiteral("stale_after_ms"), symbol.staleAfterMs}});
        }
        telemetry_.insert(QStringLiteral("symbols"), array);
        scheduleSnapshot();
    });
    connect(webull_, &machome::webull::WebullEngine::logEntry, this, [this](const Machome::Webull::LogEntry &entry) {
        publishEvent(QStringLiteral("webull.log"), {{QStringLiteral("timestamp"), iso(entry.occurredAt)},
                                                     {QStringLiteral("code"), entry.code},
                                                     {QStringLiteral("message"), entry.message},
                                                     {QStringLiteral("severity"), static_cast<int>(entry.severity)}});
    });
    connect(webull_, &machome::webull::WebullEngine::errorOccurred, this, [this](const Machome::Webull::ApiError &error) {
        lastError_ = QStringLiteral("%1: %2").arg(error.code, error.message);
        publishEvent(QStringLiteral("webull.error"), {{QStringLiteral("timestamp"), iso(error.occurredAt)},
                                                       {QStringLiteral("code"), error.code},
                                                       {QStringLiteral("endpoint"), error.endpoint},
                                                       {QStringLiteral("message"), error.message},
                                                       {QStringLiteral("http_status"), error.httpStatus},
                                                       {QStringLiteral("retryable"), error.retryable}});
        scheduleSnapshot();
    });
    connect(webull_, &machome::webull::WebullEngine::controlCompleted, this,
            [this](const QString &requestId, const QString &action, const QJsonObject &result) {
        if (!webullControlRequests_.contains(requestId)) return;
        QJsonObject tracking = webullControlRequests_.value(requestId);
        tracking.insert(QStringLiteral("accepted_result"), result);
        tracking.insert(QStringLiteral("accepted_at_epoch_ms"),
                        QDateTime::currentMSecsSinceEpoch());
        tracking.insert(QStringLiteral("probe_barrier_monotonic_ms"),
                        result.value(QStringLiteral("client_control_completed_monotonic_ms")));
        tracking.insert(QStringLiteral("awaiting_postcondition"), true);
        webullControlRequests_.insert(requestId, tracking);
        publishEvent(QStringLiteral("webull.control"), result);
        const auto pending = pendingCommands_.value(QStringLiteral("webull_control:") + requestId);
        publishCommand(pending, QStringLiteral("running"),
                       QStringLiteral("Webull 已受理 %1，正在用新鲜 v2 status/ready 核验业务后置条件")
                           .arg(action),
                       {{QStringLiteral("control_accepted"), true},
                        {QStringLiteral("accepted_result"), result}});
        webull_->refreshNow();
    });
    connect(webull_, &machome::webull::WebullEngine::controlFailed, this,
            [this](const QString &requestId, const QString &action, const QString &code,
                   const QString &message, bool outcomeUncertain) {
        lastError_ = QStringLiteral("%1: %2").arg(code, message);
        const QJsonObject detail{{QStringLiteral("request_id"), requestId},
                                 {QStringLiteral("action"), action},
                                 {QStringLiteral("code"), code},
                                 {QStringLiteral("message"), message},
                                 {QStringLiteral("outcome_uncertain"), outcomeUncertain}};
        publishEvent(QStringLiteral("webull.control_error"), detail);
        // A runner may have restarted after applying a mutation but before its
        // HTTP response arrived. Its request cache is process-local, therefore
        // blindly replaying even the same request_id could execute twice.
        // Preserve the unknown outcome and require authoritative reconciliation.
        webullControlRequests_.remove(requestId);
        finishPending(QStringLiteral("webull_control:") + requestId, false, message, detail);
        scheduleSnapshot();
    });
    const QString dataRoot = AppConfig::expandPath(config_.settings.value(QStringLiteral("data_root"))
        .toString(QStringLiteral("~/Library/Application Support/MachomeHub/data/webull")));
    const bool recordOnly = config_.settings.value(QStringLiteral("record_only")).toBool(true);
    webull_->initialize(hub::ModuleContext{config_.id, dataRoot, config_.settings, recordOnly});
    webull_->start();
}

void ModuleWorker::verifyWebullControlPostconditions(
    const Machome::Webull::GatewayStatus &status) {
    const qint64 observedAt = status.observedAt.toMSecsSinceEpoch();
    const auto requestIds = webullControlRequests_.keys();
    for (const auto &requestId : requestIds) {
        const QJsonObject tracking = webullControlRequests_.value(requestId);
        if (!tracking.value(QStringLiteral("awaiting_postcondition")).toBool(false)
            || observedAt < tracking.value(QStringLiteral("accepted_at_epoch_ms")).toInteger()) {
            continue;
        }
        const QString action = tracking.value(QStringLiteral("bridge_action")).toString();
        const QJsonObject arguments = tracking.value(QStringLiteral("arguments")).toObject();
        const qint64 probeBarrier = tracking.value(
            QStringLiteral("probe_barrier_monotonic_ms")).toInteger(-1);
        const bool nativeEngine = status.rawStatus.value(QStringLiteral("engine")).toString()
            == QStringLiteral("native");
        const bool freshStatus = nativeEngine || probeBarrier >= 0
            && status.statusProbeIssuedMonotonicMs > probeBarrier;
        const bool freshReady = nativeEngine || probeBarrier >= 0
            && status.readyProbeIssuedMonotonicMs > probeBarrier;
        bool satisfied = false;
        QString expectation;
        if (action == QStringLiteral("set_schedule_mode")) {
            const QString mode = arguments.value(QStringLiteral("mode")).toString();
            satisfied = freshStatus && status.apiLive && status.scheduleMode == mode;
            if (mode == QStringLiteral("force_running")) {
                // A native collector can be authoritatively running while the
                // market is closed or before the first fresh depth frame.  In
                // that state `/ready` is intentionally false, but treating the
                // locally-confirmed mode change as an unknown outcome locks all
                // subsequent controls until manual reconciliation.  Preserve
                // the stronger ready-probe requirement for the legacy remote
                // gateway, where process state is not locally owned.
                satisfied = satisfied && freshReady
                    && (nativeEngine || status.apiReady) && status.collectorRunning
                    && status.browserState == QStringLiteral("running");
            }
            if (mode == QStringLiteral("force_stopped")) satisfied = satisfied && !status.collectorRunning;
            expectation = QStringLiteral("schedule_mode=%1 且采集状态与强制模式一致").arg(mode);
        } else if (action == QStringLiteral("collector_start")) {
            satisfied = freshStatus && freshReady && status.apiLive
                && (nativeEngine || status.apiReady)
                && status.collectorRunning && status.browserState == QStringLiteral("running");
            expectation = QStringLiteral("api_live/api_ready/collector_running 均为 true");
        } else if (action == QStringLiteral("collector_stop")) {
            satisfied = freshStatus && status.apiLive && !status.collectorRunning;
            expectation = QStringLiteral("api_live=true 且 collector_running=false");
        } else if (action == QStringLiteral("restart_browser")) {
            satisfied = freshStatus && freshReady && status.apiLive
                && (nativeEngine || status.apiReady) && status.collectorRunning
                && status.browserState == QStringLiteral("running")
                && tracking.value(QStringLiteral("accepted_result")).toObject()
                       .value(QStringLiteral("restart_transition_observed")).toBool(false);
            expectation = QStringLiteral("API ready、collector running 且 browser=running");
        } else if (action == QStringLiteral("open_login")) {
            // Native helpers must confirm a visible browser connected to CDP;
            // an old headless collector is not proof that login opened.
            satisfied = freshStatus && freshReady && status.apiLive
                && status.collectorRunning
                && (!nativeEngine || status.rawStatus.value(QStringLiteral("browser_visible")).toBool(false));
            expectation = QStringLiteral("API 在线且可见登录浏览器已连接；桌面呈现需另行确认");
        }
        if (!satisfied) continue;

        QJsonObject details = tracking.value(QStringLiteral("accepted_result")).toObject();
        details.insert(QStringLiteral("postcondition_verified"), true);
        details.insert(QStringLiteral("postcondition"), expectation);
        details.insert(QStringLiteral("verified_at"), iso(status.observedAt));
        details.insert(QStringLiteral("status"), webullStatusJson(status));
        webullControlRequests_.remove(requestId);
        finishPending(QStringLiteral("webull_control:") + requestId, true,
                      QStringLiteral("Webull 控制已由新鲜 v2 status/ready 确认"), details);
    }
}

QJsonObject ModuleWorker::webullStatusJson(const Machome::Webull::GatewayStatus &status) const {
    QJsonObject object = status.rawStatus;
    object.insert(QStringLiteral("api_live"), status.apiLive);
    object.insert(QStringLiteral("service"), status.service);
    object.insert(QStringLiteral("api_running"), status.apiReportedRunning);
    object.insert(QStringLiteral("ready_probe_issued_monotonic_ms"),
                  status.readyProbeIssuedMonotonicMs);
    object.insert(QStringLiteral("status_probe_issued_monotonic_ms"),
                  status.statusProbeIssuedMonotonicMs);
    object.insert(QStringLiteral("api_ready"), status.apiReady);
    object.insert(QStringLiteral("stream_connected"), status.streamConnected);
    object.insert(QStringLiteral("polling_fallback"), status.pollingFallback);
    object.insert(QStringLiteral("collector_running"), status.collectorRunning);
    object.insert(QStringLiteral("scheduled_idle"), status.scheduledIdle);
    object.insert(QStringLiteral("data_fresh"), status.dataFresh);
    object.insert(QStringLiteral("data_age_ms"), status.dataAgeMs);
    object.insert(QStringLiteral("browser_state"), status.browserState);
    object.insert(QStringLiteral("auth_state"), status.authState);
    object.insert(QStringLiteral("data_state"), status.dataState);
    object.insert(QStringLiteral("schedule_mode"), status.scheduleMode);
    object.insert(QStringLiteral("client_count"), status.clientCount);
    object.insert(QStringLiteral("valid_responses"), status.validResponses);
    object.insert(QStringLiteral("invalid_responses"), status.invalidResponses);
    object.insert(QStringLiteral("last_depth_at"), status.lastDepthAt);
    object.insert(QStringLiteral("last_error"), status.lastError);
    object.insert(QStringLiteral("observed_at"), iso(status.observedAt));
    return object;
}

QJsonObject ModuleWorker::webullBookJson(const Machome::Webull::BookSnapshot &book) const {
    QJsonArray bids;
    QJsonArray asks;
    for (const auto &level : book.bids) bids.append(priceLevelJson(level));
    for (const auto &level : book.asks) asks.append(priceLevelJson(level));
    return {{QStringLiteral("symbol"), book.symbol}, {QStringLiteral("ticker_id"), book.tickerId},
            {QStringLiteral("session_id"), book.sessionId}, {QStringLiteral("sequence"), book.sequence},
            {QStringLiteral("captured_at"), iso(book.capturedAt)}, {QStringLiteral("published_at"), iso(book.publishedAt)},
            {QStringLiteral("changed"), book.changed}, {QStringLiteral("age_ms"), book.ageMs},
            {QStringLiteral("fresh"), book.fresh}, {QStringLiteral("bids"), bids},
            {QStringLiteral("asks"), asks},
            {QStringLiteral("inside"), QJsonObject{{QStringLiteral("best_bid"), book.inside.bestBid},
                                                    {QStringLiteral("best_ask"), book.inside.bestAsk},
                                                    {QStringLiteral("spread"), book.inside.spread},
                                                    {QStringLiteral("state"), book.inside.state}}}};
}

void ModuleWorker::startRealtime() {
    realtime_ = new machome::bridge::RealtimeClient(this);
    QString error;
    if (!realtime_->setBaseUrl(QUrl(config_.settings.value(QStringLiteral("api_base_url")).toString()), &error)) {
        lastError_ = error;
        workState_ = QStringLiteral("blocked");
        headline_ = QStringLiteral("Realtime adapter 配置无效，已拒绝启动");
        publishEvent(QStringLiteral("redemption.configuration_error"),
                     {{QStringLiteral("message"), error}});
        realtime_->deleteLater();
        realtime_ = nullptr;
        return;
    }
    realtime_->setRequiredProtocolVersion(config_.settings.value(QStringLiteral("required_protocol")).toInt(1));
    realtime_->setRequestTimeoutMs(config_.settings.value(QStringLiteral("request_timeout_ms")).toInt(3000));
    realtime_->setControlTimeoutMs(config_.settings.value(QStringLiteral("control_timeout_ms")).toInt(120000));
    realtime_->setMaximumResponseBytes(config_.settings
        .value(QStringLiteral("maximum_response_bytes")).toInteger(512 * 1024));
    realtime_->setHealthPollIntervalMs(config_.settings.value(QStringLiteral("poll_interval_ms")).toInt(1000));
    connect(realtime_, &machome::bridge::RealtimeClient::healthReceived, this,
            [this](const QString &, const QJsonObject &health, qint64 latency) {
        auto object = health;
        object.insert(QStringLiteral("latency_ms"), latency);
        telemetry_.insert(QStringLiteral("health"), object);
        markTelemetryObserved(QStringLiteral("health"));
        updateRealtimeServiceIdentity(true);
        const bool monitoring = health.value(QStringLiteral("monitoring")).toBool();
        workState_ = monitoring ? QStringLiteral("active") : QStringLiteral("scheduled_idle");
        headline_ = monitoring ? QStringLiteral("实时申赎监控中") : QStringLiteral("服务在线，当前未监控");
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::healthStateChanged, this,
            [this](bool ready, const QString &reason, const QDateTime &observedAt) {
        telemetry_.insert(QStringLiteral("health_probe"), QJsonObject{
            {QStringLiteral("ready"), ready}, {QStringLiteral("reason"), reason},
            {QStringLiteral("observed_at"), iso(observedAt)}});
        markTelemetryObserved(QStringLiteral("health_probe"));
        if (!ready) {
            lastError_ = reason;
            workState_ = QStringLiteral("blocked");
        }
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::snapshotReceived, this, [this](const QJsonObject &snapshot) {
        telemetry_.insert(QStringLiteral("snapshot"), snapshot);
        markTelemetryObserved(QStringLiteral("snapshot"));
        updateRealtimeServiceIdentity(true);
        const auto items = snapshot.value(QStringLiteral("items")).toArray();
        if (!items.isEmpty()) telemetry_.insert(QStringLiteral("symbol_count"), items.size());
        publishEvent(QStringLiteral("redemption.snapshot"), snapshot);
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::changeReceived, this, [this](const QJsonObject &change) {
        publishEvent(QStringLiteral("redemption.change"), change);
    });
    connect(realtime_, &machome::bridge::RealtimeClient::statusReceived, this, [this](const QJsonObject &status) {
        telemetry_.insert(QStringLiteral("stream_status"), status);
        publishEvent(QStringLiteral("redemption.status"), status);
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::heartbeatReceived, this, [this](const QJsonObject &heartbeat) {
        telemetry_.insert(QStringLiteral("heartbeat"), heartbeat);
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::freshnessUpdated, this,
            [this](const machome::bridge::RealtimeDataFreshness &freshness) {
        telemetry_.insert(QStringLiteral("freshness"), QJsonObject{{QStringLiteral("known"), freshness.known},
                                                                    {QStringLiteral("fresh"), freshness.fresh},
                                                                    {QStringLiteral("total_items"), freshness.totalItems},
                                                                    {QStringLiteral("stale_items"), freshness.staleItems},
                                                                    {QStringLiteral("maximum_age_ms"), freshness.maximumAgeMs},
                                                                    {QStringLiteral("last_sample_at"), iso(freshness.lastSampleAt)}});
        if (freshness.known && !freshness.fresh && workState_ == QStringLiteral("active")) workState_ = QStringLiteral("degraded");
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::watchlistReceived, this,
            [this](const QString &, const QStringList &symbols) {
        const QJsonArray values = QJsonArray::fromStringList(symbols);
        telemetry_.insert(QStringLiteral("watchlist"), values);
        publishEvent(QStringLiteral("redemption.watchlist"), {{QStringLiteral("symbols"), values}});
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::historyReceived, this,
            [this](const QString &requestId, const QJsonObject &history) {
        const QJsonArray items = history.value(QStringLiteral("items")).toArray();
        QList<QJsonArray> chunks;
        QJsonArray current;
        int currentBytes = 0;
        for (const auto &item : items) {
            const int itemBytes = QJsonDocument(item.toObject()).toJson(QJsonDocument::Compact).size();
            if (!current.isEmpty() && (current.size() >= 200 || currentBytes + itemBytes > 192 * 1024)) {
                chunks.push_back(current);
                current = {};
                currentBytes = 0;
            }
            current.append(item);
            currentBytes += itemBytes;
        }
        if (!current.isEmpty() || chunks.isEmpty()) chunks.push_back(current);
        for (int index = 0; index < chunks.size(); ++index) {
            QJsonObject page = history;
            page.insert(QStringLiteral("items"), chunks.at(index));
            page.insert(QStringLiteral("chunked"), true);
            page.insert(QStringLiteral("page_index"), index);
            page.insert(QStringLiteral("page_count"), chunks.size());
            page.insert(QStringLiteral("total_items"), items.size());
            page.insert(QStringLiteral("complete"), index + 1 == chunks.size());
            publishEvent(QStringLiteral("redemption.history"), page);
        }
        finishPending(realtimeRequestToPendingKey_.take(requestId), true,
                      QStringLiteral("历史数据已分 %1 页完整返回").arg(chunks.size()),
                      {{QStringLiteral("chunked"), true},
                       {QStringLiteral("page_count"), chunks.size()},
                       {QStringLiteral("total_items"), items.size()}});
    });
    connect(realtime_, &machome::bridge::RealtimeClient::pcfDetailReceived, this,
            [this](const QString &requestId, const QString &symbol, QJsonObject detail) {
        if (!detail.contains(QStringLiteral("symbol"))) detail.insert(QStringLiteral("symbol"), symbol);
        publishEvent(QStringLiteral("redemption.pcf"), detail);
        finishPending(realtimeRequestToPendingKey_.take(requestId), true, QStringLiteral("PCF 详情已返回"), detail);
    });
    connect(realtime_, &machome::bridge::RealtimeClient::controlFinished, this,
            [this](const QString &requestId, const QString &action, const QJsonObject &result) {
        publishEvent(QStringLiteral("redemption.control"), result);
        finishPending(realtimeRequestToPendingKey_.take(requestId), true,
                      QStringLiteral("%1 已完成").arg(action), result);
        if (action == QStringLiteral("watchlist_update")) realtime_->requestWatchlist();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::requestFailed, this,
            [this](const QString &requestId, const QString &, const QString &code, const QString &message,
                   bool retryable, bool outcomeUncertain) {
        lastError_ = QStringLiteral("%1: %2").arg(code, message);
        const QJsonObject details{{QStringLiteral("code"), code},
                                  {QStringLiteral("retryable"), retryable},
                                  {QStringLiteral("outcome_uncertain"), outcomeUncertain}};
        finishPending(realtimeRequestToPendingKey_.take(requestId), false, lastError_, details);
        publishEvent(QStringLiteral("redemption.error"), {{QStringLiteral("code"), code},
                                                           {QStringLiteral("message"), message},
                                                           {QStringLiteral("retryable"), retryable},
                                                           {QStringLiteral("outcome_uncertain"), outcomeUncertain}});
        scheduleSnapshot();
    });
    connect(realtime_, &machome::bridge::RealtimeClient::protocolMismatch, this,
            [this](int expected, int actual, const QString &source) {
        lastError_ = QStringLiteral("协议不匹配 %1: expected %2 actual %3").arg(source).arg(expected).arg(actual);
        workState_ = QStringLiteral("blocked");
        scheduleSnapshot();
    });

    const auto qmtDefinitions = config_.settings.value(QStringLiteral("qmt_backends")).toArray();
    for (const auto &value : qmtDefinitions) {
        if (!value.isObject()) continue;
        const auto definition = value.toObject();
        if (!definition.value(QStringLiteral("enabled")).toBool(true)) continue;
        machome::qmt::QmtClientConfig qmtConfig;
        qmtConfig.id = definition.value(QStringLiteral("id")).toString().trimmed();
        qmtConfig.host = definition.value(QStringLiteral("host")).toString().trimmed();
        qmtConfig.port = static_cast<quint16>(definition.value(QStringLiteral("port")).toInt());
        qmtConfig.reconnectIntervalMs = definition.value(QStringLiteral("reconnect_interval_ms")).toInt(5000);
        qmtConfig.heartbeatIntervalMs = definition.value(QStringLiteral("heartbeat_interval_ms")).toInt(5000);
        qmtConfig.silenceTimeoutMs = definition.value(QStringLiteral("silence_timeout_ms")).toInt(15000);
        qmtConfig.orderThrottleMs = definition.value(QStringLiteral("order_throttle_ms")).toInt(5000);
        if (qmtConfig.id.isEmpty() || qmtConfig.host.isEmpty() || qmtConfig.port == 0
            || qmtClients_.contains(qmtConfig.id)) {
            publishEvent(QStringLiteral("redemption.qmt_config_error"),
                         {{QStringLiteral("message"), QStringLiteral("忽略无效或重复 QMT backend 配置")}});
            continue;
        }
        auto *client = new machome::qmt::QmtClient(qmtConfig, this);
        qmtClients_.insert(qmtConfig.id, client);
        connect(client, &machome::qmt::QmtClient::snapshotChanged, this,
                [this, id = qmtConfig.id](const QJsonObject &snapshot) {
            auto backends = telemetry_.value(QStringLiteral("qmt_backends")).toObject();
            backends.insert(id, snapshot);
            telemetry_.insert(QStringLiteral("qmt_backends"), backends);
            if (snapshot.value(QStringLiteral("ready")).toBool()) {
                finishPending(QStringLiteral("qmt_connect:") + id, true,
                              QStringLiteral("%1 已连接并完成全量同步").arg(id), snapshot);
                finishPending(QStringLiteral("qmt_sync:") + id, true,
                              QStringLiteral("%1 全量同步完成").arg(id), snapshot);
            }
            if (snapshot.value(QStringLiteral("connection_state")).toString()
                    == QStringLiteral("disconnected")) {
                finishPending(QStringLiteral("qmt_disconnect:") + id, true,
                              QStringLiteral("%1 已断开").arg(id), snapshot);
            }
            scheduleSnapshot();
        });
        connect(client, &machome::qmt::QmtClient::eventOccurred, this,
                [this, id = qmtConfig.id](const QString &kind, QJsonObject payload) {
            payload.insert(QStringLiteral("backend"), id);
            publishEvent(QStringLiteral("redemption.qmt.") + kind, payload);
        });
        connect(client, &machome::qmt::QmtClient::orderFinished, this,
                [this, id = qmtConfig.id](const QString &commandId, bool ok,
                                           const QString &message, QJsonObject details) {
            details.insert(QStringLiteral("backend"), id);
            finishPending(QStringLiteral("qmt_order:") + commandId, ok, message, details);
        });
        auto backends = telemetry_.value(QStringLiteral("qmt_backends")).toObject();
        backends.insert(qmtConfig.id, client->snapshot());
        telemetry_.insert(QStringLiteral("qmt_backends"), backends);
        if (config_.settings.value(QStringLiteral("qmt_auto_connect_enabled")).toBool(false)
            && definition.value(QStringLiteral("auto_connect")).toBool(false)
            && config_.controlEnabled && config_.ownership != QStringLiteral("shadow")) {
            client->connectBackend();
        }
    }
    realtime_->start();
    realtime_->requestWatchlist();
}

void ModuleWorker::startRedemption() {
    redemption_ = new machome::redemption::RedemptionEngine(this);
    connect(redemption_, &machome::redemption::RedemptionEngine::snapshotReady,
            this, [this](const QJsonObject &snapshot) {
        telemetry_.insert(QStringLiteral("engine"), snapshot);
        telemetry_.insert(QStringLiteral("snapshot"), snapshot);
        telemetry_.insert(QStringLiteral("health"), snapshot.value(QStringLiteral("health")));
        telemetry_.insert(QStringLiteral("watchlist"), snapshot.value(QStringLiteral("watchlist")));
        telemetry_.insert(QStringLiteral("qmt_backends"), snapshot.value(QStringLiteral("qmt_backends")));
        telemetry_.insert(QStringLiteral("symbol_count"),
                          snapshot.value(QStringLiteral("items")).toArray().size());
        markTelemetryObserved(QStringLiteral("engine"));
        markTelemetryObserved(QStringLiteral("snapshot"));
        markTelemetryObserved(QStringLiteral("health"));
        workState_ = snapshot.value(QStringLiteral("state")).toString();
        lastError_ = snapshot.value(QStringLiteral("last_error")).toString();
        headline_ = !snapshot.value(QStringLiteral("running")).toBool(false)
            ? QStringLiteral("原生实时申赎模块已停止")
            : snapshot.value(QStringLiteral("monitoring")).toBool()
                ? QStringLiteral("原生实时申赎监控中")
                : QStringLiteral("原生实时申赎已驻留，当前计划空闲");
        scheduleSnapshot();
    });
    connect(redemption_, &machome::redemption::RedemptionEngine::eventReady,
            this, [this](const QString &kind, const QJsonObject &payload) {
        publishEvent(kind, payload);
    });
    connect(redemption_, &machome::redemption::RedemptionEngine::commandFinished,
            this, [this](const QString &commandId, bool ok, const QString &message,
                         const QJsonObject &details) {
        finishPending(QStringLiteral("redemption:") + commandId, ok, message, details);
    });
    const QString dataRoot = AppConfig::expandPath(
        config_.settings.value(QStringLiteral("data_root"))
            .toString(QStringLiteral("~/Library/Application Support/MachomeHub/data/redemption")));
    redemption_->initialize(hub::ModuleContext{
        config_.id, dataRoot, config_.settings,
        config_.settings.value(QStringLiteral("record_only")).toBool(true)});
    redemption_->start();
}

void ModuleWorker::requestSnapshot() {
    if (lifecycle_) lifecycle_->refresh();
    // During the post-launchd settle barrier, issuing an adapter request would
    // create an untagged response that could arrive after the new-generation
    // probe and incorrectly satisfy readiness.
    if (readinessAdaptersSuspended_) {
        publishSnapshot();
        return;
    }
    if (upload_) {
        const QJsonObject snapshot = upload_->snapshot();
        telemetry_.insert(QStringLiteral("engine"), snapshot);
        telemetry_.insert(QStringLiteral("workers"), snapshot.value(QStringLiteral("jobs")));
        telemetry_.insert(QStringLiteral("worker_count"),
                          snapshot.value(QStringLiteral("jobs")).toArray().size());
        telemetry_.insert(QStringLiteral("funds"), snapshot.value(QStringLiteral("funds")));
        telemetry_.insert(QStringLiteral("upload_records"),
                          snapshot.value(QStringLiteral("upload_records")));
        telemetry_.insert(QStringLiteral("job_runs"), snapshot.value(QStringLiteral("job_runs")));
    }
    if (premium_) premium_->submitCommand(QStringLiteral("premium_status"), {},
                                           QStringLiteral("snapshot_refresh"));
    if (premiumProbe_) {
        premiumProbe_->requestStatus();
        premiumProbe_->requestL1Status();
    }
    if (webull_) webull_->refreshNow();
    if (realtime_) realtime_->refreshAll();
    if (redemption_) {
        const QJsonObject snapshot = redemption_->snapshot();
        telemetry_.insert(QStringLiteral("engine"), snapshot);
        telemetry_.insert(QStringLiteral("snapshot"), snapshot);
        telemetry_.insert(QStringLiteral("health"), snapshot.value(QStringLiteral("health")));
        telemetry_.insert(QStringLiteral("qmt_backends"), snapshot.value(QStringLiteral("qmt_backends")));
    }
    publishSnapshot();
}

void ModuleWorker::startMonitorSync() {
    sync_ = new machome::sync::MonitorSyncEngine(this);
    connect(sync_, &machome::sync::MonitorSyncEngine::snapshotReady, this, [this](const QJsonObject &s) {
        telemetry_["engine"]=s; process_["lifecycle"]=s["running"].toBool()?"running":"stopped";
        workState_=s["error"].toString().isEmpty()?(s["running"].toBool()?"active":"paused"):"blocked";
        lastError_=s["error"].toString(); headline_=lastError_.isEmpty()?QStringLiteral("文件同步 · 在线设备 %1 · 队列 %2").arg(s["clients"].toArray().size()).arg(s["pending"].toInt()):lastError_; scheduleSnapshot();
    });
    connect(sync_, &machome::sync::MonitorSyncEngine::eventReady, this, &ModuleWorker::publishEvent);
    connect(sync_, &machome::sync::MonitorSyncEngine::commandFinished, this, [this](const QString &id,bool ok,const QString &message,const QJsonObject &details){ finishPending("monitor_sync:"+id,ok,message,details); });
    sync_->initialize({config_.id,AppConfig::expandPath(config_.settings.value("data_root").toString("~/MachomeHubData/monitor-sync")),config_.settings,false});
    sync_->start();
}

void ModuleWorker::setControlRevision(quint64 revision) {
    controlRevision_ = qMax(controlRevision_, revision);
    scheduleSnapshot();
}

bool ModuleWorker::allowMutation(const QJsonObject &command) {
    const QString action = command.value(QStringLiteral("action")).toString();
    if (config_.controlEnabled && config_.ownership != QStringLiteral("shadow")
        && config_.allowedActions.contains(action)) return true;
    publishCommand(command, QStringLiteral("failed"),
                   QStringLiteral("该模块未启用逻辑控制；请先完成所有权配置与真机预检"));
    return false;
}

void ModuleWorker::submitCommand(const QJsonObject &command) {
    const QString action = command.value(QStringLiteral("action")).toString();
    const QString commandId = command.value(QStringLiteral("command_id")).toString();
    const auto arguments = command.value(QStringLiteral("arguments")).toObject();
    if (!config_.allowedActions.contains(action)) {
        publishCommand(command, QStringLiteral("failed"),
                       QStringLiteral("动作未列入模块 allowlist，未执行"),
                       {{QStringLiteral("code"), QStringLiteral("action_not_allowed")}});
        return;
    }
    if (!premiumDetailObservationAction(action)
        && !activeCommandId_.isEmpty() && activeCommandId_ != commandId) {
        publishCommand(command, QStringLiteral("failed"),
                       QStringLiteral("本模块已有命令执行中；为保证串行，本次未执行"),
                       {{QStringLiteral("code"), QStringLiteral("module_command_busy")},
                        {QStringLiteral("active_command_id"), activeCommandId_},
                        {QStringLiteral("outcome_uncertain"), false}});
        return;
    }
    if (action != QStringLiteral("acknowledge_uncertain")
        && action != QStringLiteral("refresh")
        && !premiumDetailObservationAction(action)
        && !timedOutPending_.isEmpty()) {
        publishCommand(command, QStringLiteral("failed"),
                       QStringLiteral("本模块有未知终态命令；为保证严格串行，除刷新与人工解除外不接受新命令"),
                       {{QStringLiteral("code"), QStringLiteral("previous_outcome_unresolved")},
                        {QStringLiteral("pending_count"), timedOutPending_.size()},
                        {QStringLiteral("outcome_uncertain"), false},
                        {QStringLiteral("blocked_by_unresolved"), true}});
        return;
    }
    if (!premiumDetailObservationAction(action) && activeCommandId_.isEmpty()) {
        activeCommandId_ = commandId;
    }
    if (action == QStringLiteral("acknowledge_uncertain")) {
        const QString evidence = arguments.value(QStringLiteral("evidence")).toString().trimmed();
        if (evidence.size() < 8 || evidence.size() > 512) {
            publishCommand(command, QStringLiteral("failed"),
                           QStringLiteral("解除未知结果锁必须附带 8–512 字符的权威核对证据"),
                           {{QStringLiteral("code"), QStringLiteral("reconciliation_evidence_required")}});
            return;
        }
        const qint64 now = QDateTime::currentMSecsSinceEpoch();
        qint64 waitUntil = 0;
        for (const auto &key : std::as_const(timedOutPending_)) {
            waitUntil = qMax(waitUntil,
                             pendingTransportQuiescenceUntilMs_.value(key, 0));
        }
        if (waitUntil > now) {
            publishCommand(command, QStringLiteral("failed"),
                           QStringLiteral("底层变更请求仍在最大执行窗口内；暂不允许解锁"),
                           {{QStringLiteral("code"), QStringLiteral("transport_quiescence_required")},
                            {QStringLiteral("retry_after_ms"), waitUntil - now},
                            {QStringLiteral("outcome_uncertain"), false}});
            return;
        }
        if (lifecycle_ && !lifecycle_->transportQuiescent()) {
            publishCommand(command, QStringLiteral("failed"),
                           QStringLiteral("生命周期子进程尚未回收；暂不允许解锁"),
                           {{QStringLiteral("code"), QStringLiteral("transport_quiescence_required")},
                            {QStringLiteral("outcome_uncertain"), false}});
            return;
        }
        const QSet<QString> unresolved = timedOutPending_;
        bool resetPremium = false;
        bool resetWebull = false;
        bool resetRealtime = false;
        QSet<QString> resetQmt;
        for (const auto &key : unresolved) {
            const QJsonObject pending = pendingCommands_.value(key);
            const QString pendingAction = pending.value(QStringLiteral("action")).toString();
            resetPremium = resetPremium || pendingAction.startsWith(QStringLiteral("premium_"));
            resetWebull = resetWebull || pendingAction.startsWith(QStringLiteral("webull_"));
            resetRealtime = resetRealtime
                || (pendingAction.startsWith(QStringLiteral("redemption_"))
                    && !pendingAction.startsWith(QStringLiteral("redemption_qmt_")));
            if (pendingAction.startsWith(QStringLiteral("redemption_qmt_"))) {
                resetQmt.insert(pending.value(QStringLiteral("arguments")).toObject()
                                    .value(QStringLiteral("backend")).toString());
            }
        }
        // Cancel/replace every client generation before unlocking. QMT is
        // deliberately reconnected: a new order remains impossible until a
        // fresh authoritative positions+orders full sync reaches ready.
        if (resetPremium && premium_) { premium_->stop(); premium_->start(); }
        if (resetPremium && premiumProbe_) {
            premiumProbe_->stop();
            premiumProbe_->start();
        }
        if (resetWebull && webull_) { webull_->stop(); webull_->start(); }
        if (resetRealtime && realtime_) { realtime_->stop(); realtime_->start(); }
        if (resetRealtime && redemption_) { redemption_->stop(); redemption_->start(); }
        for (const auto &backend : resetQmt) {
            if (auto *client = qmtClients_.value(backend, nullptr)) {
                // Forget the acknowledged old Hub command before disconnecting,
                // otherwise disconnectBackend() reports the same pending order
                // as a fresh uncertainty. resetSync() on disconnect then forces
                // both authoritative full streams before isReady() can recover.
                client->reconcilePendingOrders();
                client->disconnectBackend();
                client->connectBackend();
            }
        }
        for (const auto &key : unresolved) {
            pendingCommands_.remove(key);
            pendingTransportQuiescenceUntilMs_.remove(key);
        }
        timedOutPending_.clear();
        webullControlRequests_.clear();
        lifecycleReadinessTimer_.stop();
        pendingLifecycleResult_ = {};
        lifecycleReadinessTimeoutEmitted_ = false;
        lifecycleReadinessProbeIssued_ = false;
        ++lifecycleReadinessGeneration_;
        if (lifecycle_) lifecycle_->refresh();
        resumeAdaptersAfterReadinessProbe();
        publishCommand(command, QStringLiteral("succeeded"),
                       QStringLiteral("已记录人工权威核对并解除未知结果锁"),
                       {{QStringLiteral("evidence"), evidence},
                        {QStringLiteral("reconciled_by"), command.value(QStringLiteral("requested_by"))},
                        {QStringLiteral("reconciled_at"), utcNow()}});
        return;
    }
    if (config_.engine == QStringLiteral("native")
        && (action == QStringLiteral("start_service")
            || action == QStringLiteral("stop_service")
            || action == QStringLiteral("restart_service"))) {
        if (!allowMutation(command)) return;
        const auto stopNative = [this] {
            if (sync_) sync_->stop();
            if (upload_) upload_->stop(hub::StopMode::Graceful);
            else if (premium_) premium_->stop(hub::StopMode::Graceful);
            else if (webull_) webull_->stop(hub::StopMode::Graceful);
            else if (redemption_) redemption_->stop(hub::StopMode::Graceful);
        };
        const auto startNative = [this] {
            if (sync_) sync_->start();
            if (upload_) upload_->start();
            else if (premium_) premium_->start();
            else if (webull_) webull_->start();
            else if (redemption_) redemption_->start();
        };
        const bool hasNativeEngine = sync_ || upload_ || premium_ || webull_ || redemption_;
        if (!hasNativeEngine) {
            publishCommand(command, QStringLiteral("failed"),
                           QStringLiteral("原生模块引擎尚未就绪"));
            return;
        }
        if (action == QStringLiteral("stop_service")) {
            stopNative();
        } else if (action == QStringLiteral("restart_service")) {
            stopNative();
            startNative();
        } else {
            startNative();
        }
        requestSnapshot();
        bool running = false;
        QJsonObject engineStatus;
        if (upload_) {
            engineStatus = upload_->snapshot();
            running = engineStatus.value(QStringLiteral("running")).toBool(false);
        } else if (premium_) {
            engineStatus = telemetry_.value(QStringLiteral("engine")).toObject();
            running = engineStatus.value(QStringLiteral("running")).toBool(false);
        } else if (webull_) {
            engineStatus = webull_->snapshot();
            running = engineStatus.value(QStringLiteral("running")).toBool(false);
            if (running && config_.settings.value(QStringLiteral("api_enabled")).toBool(true)) {
                running = engineStatus.value(QStringLiteral("status")).toObject()
                              .value(QStringLiteral("api_running")).toBool(false);
            }
        } else if (redemption_) {
            engineStatus = redemption_->snapshot();
            running = engineStatus.value(QStringLiteral("running")).toBool(false);
        }
        if (sync_) { engineStatus=sync_->snapshot(); running=engineStatus["running"].toBool(); }
        const bool expectedRunning = action != QStringLiteral("stop_service");
        const bool lifecycleConfirmed = running == expectedRunning;
        const QString operation = action == QStringLiteral("start_service")
            ? QStringLiteral("启动")
            : action == QStringLiteral("stop_service") ? QStringLiteral("停止")
                                                       : QStringLiteral("重启");
        publishCommand(command,
                       lifecycleConfirmed ? QStringLiteral("succeeded")
                                          : QStringLiteral("failed"),
                       lifecycleConfirmed
                           ? QStringLiteral("原生模块已%1并确认终态").arg(operation)
                           : QStringLiteral("原生模块%1后未达到预期终态").arg(operation),
                       {{QStringLiteral("native_engine"), true},
                        {QStringLiteral("expected_running"), expectedRunning},
                        {QStringLiteral("actual_running"), running},
                        {QStringLiteral("engine_status"), engineStatus}});
        return;
    }
    if (sync_ && action.startsWith("monitor_sync_")) {
        if (!allowMutation(command)) return;
        if (rememberPending("monitor_sync:"+commandId,command,120000)) sync_->submitCommand(action,command.value("arguments").toObject(),commandId);
        return;
    }
    if (lifecycle_ && lifecycle_->handles(action)) {
        if (stateChangingAction(action)) {
            const QString key = QStringLiteral("lifecycle_unknown:") + commandId;
            const int deadline = std::clamp(
                command.value(QStringLiteral("deadline_ms")).toInt(45000),
                1000, 30 * 60 * 1000);
            pendingTransportQuiescenceUntilMs_.insert(
                key, QDateTime::currentMSecsSinceEpoch() + deadline + 1000);
        }
        lifecycle_->execute(command);
        return;
    }
    if (action == QStringLiteral("refresh")) {
        requestSnapshot();
        publishCommand(command, QStringLiteral("succeeded"), QStringLiteral("已触发刷新"));
        return;
    }

    if (action == QStringLiteral("set_operating_mode")) {
        if (!allowMutation(command)) return;
        hub::IModuleEngine *engine = upload_ ? static_cast<hub::IModuleEngine *>(upload_)
            : premium_ ? static_cast<hub::IModuleEngine *>(premium_)
            : webull_ ? static_cast<hub::IModuleEngine *>(webull_)
            : redemption_ ? static_cast<hub::IModuleEngine *>(redemption_)
                          : nullptr;
        if (!engine) {
            publishCommand(command, QStringLiteral("failed"),
                           QStringLiteral("仅原生模块支持运行模式切换"));
            return;
        }
        const bool activateForTest = arguments.value(QStringLiteral("mode")).toString()
            == QStringLiteral("weekend_test");
        const QString key = (upload_ ? QStringLiteral("upload:")
            : premium_ ? QStringLiteral("premium:")
            : webull_ ? QStringLiteral("webull-engine:")
                      : QStringLiteral("redemption:")) + commandId;
        if (rememberPending(key, command,
                            command.value(QStringLiteral("deadline_ms")).toInt(15000))) {
            engine->submitCommand(action, arguments, commandId);
            if (activateForTest) {
                // Apply the time-window override before start(). Otherwise a
                // stopped module could briefly execute its work-mode close or
                // shutdown edge during weekend activation.
                engine->start();
            }
        }
        return;
    }

    if (upload_ && action.startsWith(QStringLiteral("upload_"))) {
        if (!allowMutation(command)) return;
        const QString key = QStringLiteral("upload:") + commandId;
        if (rememberPending(key, command,
                            command.value(QStringLiteral("deadline_ms")).toInt(15000))) {
            upload_->submitCommand(action, arguments, commandId);
        }
        return;
    }

    if (premium_ && action.startsWith(QStringLiteral("premium_"))) {
        if ((action == QStringLiteral("premium_set_watchlist")
             || action == QStringLiteral("premium_set_l1_hotlist"))
            && !allowMutation(command)) {
            return;
        }
        const QString key = QStringLiteral("premium:") + commandId;
        if (rememberPending(key, command,
                            command.value(QStringLiteral("deadline_ms")).toInt(15000))) {
            premium_->submitCommand(action, arguments, commandId);
        }
        return;
    }
    if (premiumProbe_ && action.startsWith(QStringLiteral("premium_"))) {
        if (action == QStringLiteral("premium_sync")) {
            if (rememberPending(action, command)) premiumProbe_->requestSync();
        } else if (action == QStringLiteral("premium_raw_snapshot")) {
            if (rememberPending(action, command)) premiumProbe_->requestRawSnapshot();
        } else if (action == QStringLiteral("premium_detail_subscribe")) {
            const QString symbol = arguments.value(QStringLiteral("symbol"))
                                       .toString().trimmed().toUpper();
            if (rememberPending(action + u':' + symbol, command)) {
                premiumProbe_->subscribeDetail(symbol);
            }
        } else if (action == QStringLiteral("premium_detail_unsubscribe")) {
            const QString symbol = arguments.value(QStringLiteral("symbol"))
                                       .toString().trimmed().toUpper();
            if (rememberPending(action + u':' + symbol, command)) {
                premiumProbe_->unsubscribeDetail(symbol);
            }
        } else {
            publishCommand(command, QStringLiteral("failed"),
                           QStringLiteral("LegacyProbe 不允许修改 A 端业务"));
        }
        return;
    }
    if (webull_) {
        if (action == QStringLiteral("webull_get_book")) {
            const QString configured = config_.settings.value(QStringLiteral("default_symbol"))
                                           .toString(QStringLiteral("XOP")).trimmed().toUpper();
            const QString requested = arguments.value(QStringLiteral("symbol")).toString().trimmed().toUpper();
            if (!requested.isEmpty() && requested != configured) {
                publishCommand(command, QStringLiteral("failed"),
                               QStringLiteral("该 gateway 仅配置标的 %1；未切换盘口").arg(configured),
                               {{QStringLiteral("code"), QStringLiteral("symbol_not_configured")},
                                {QStringLiteral("configured_symbol"), configured}});
                return;
            }
            webull_->refreshNow();
            const auto currentBook = telemetry_.value(QStringLiteral("book")).toObject();
            if (!currentBook.isEmpty()) {
                publishCommand(command, QStringLiteral("succeeded"),
                               QStringLiteral("已触发刷新；当前权威盘口保留到新序列到达"), currentBook);
            } else {
                rememberPending(action, command, 8000);
            }
            return;
        }
        if (action.startsWith(QStringLiteral("webull_"))) {
            if (!allowMutation(command)) return;
            QString bridgeAction;
            QJsonObject bridgeArguments;
            if (action == QStringLiteral("webull_set_mode")) {
                bridgeAction = QStringLiteral("set_schedule_mode");
                bridgeArguments = {{QStringLiteral("mode"), arguments.value(QStringLiteral("mode"))}};
            } else if (action == QStringLiteral("webull_collector_start")) {
                bridgeAction = QStringLiteral("collector_start");
            } else if (action == QStringLiteral("webull_collector_stop")) {
                bridgeAction = QStringLiteral("collector_stop");
            } else if (action == QStringLiteral("webull_restart_browser")) {
                bridgeAction = QStringLiteral("restart_browser");
            } else if (action == QStringLiteral("webull_show_login")) {
                bridgeAction = QStringLiteral("open_login");
            } else {
                publishCommand(command, QStringLiteral("failed"),
                               QStringLiteral("不支持的 Webull 控制动作：%1").arg(action));
                return;
            }
            const QString requestId = command.value(QStringLiteral("command_id")).toString();
            const QString key = QStringLiteral("webull_control:") + requestId;
            if (rememberPending(key, command, command.value(QStringLiteral("deadline_ms")).toInt(15000))) {
                webullControlRequests_.insert(requestId,
                    {{QStringLiteral("bridge_action"), bridgeAction},
                     {QStringLiteral("arguments"), bridgeArguments},
                     {QStringLiteral("retry_count"), 0}});
                webull_->submitControl(requestId, bridgeAction, bridgeArguments);
            }
            return;
        }
    }
    if (redemption_ && action.startsWith(QStringLiteral("redemption_"))) {
        const bool readOnly = action == QStringLiteral("redemption_get_history")
            || action == QStringLiteral("redemption_get_pcf");
        if (!readOnly && !allowMutation(command)) return;
        const QString key = QStringLiteral("redemption:") + commandId;
        if (rememberPending(key, command,
                            command.value(QStringLiteral("deadline_ms")).toInt(120000))) {
            redemption_->submitCommand(action, arguments, commandId);
        }
        return;
    }
    if (realtime_) {
        QString requestId;
        if (action == QStringLiteral("redemption_get_history")) {
            requestId = realtime_->requestHistory(QDate::fromString(arguments.value(QStringLiteral("date")).toString(), Qt::ISODate),
                                                   arguments.value(QStringLiteral("symbol")).toString(),
                                                   arguments.value(QStringLiteral("limit")).toInt(500));
        } else if (action == QStringLiteral("redemption_get_pcf")) {
            requestId = realtime_->requestPcfDetail(arguments.value(QStringLiteral("symbol")).toString());
        } else if (action.startsWith(QStringLiteral("redemption_qmt_"))) {
            if (!allowMutation(command)) return;
            const QString backend = arguments.value(QStringLiteral("backend")).toString();
            auto *client = qmtClients_.value(backend, nullptr);
            if (!client) {
                publishCommand(command, QStringLiteral("failed"),
                               QStringLiteral("未知 QMT backend：%1").arg(backend));
                return;
            }
            if (action == QStringLiteral("redemption_qmt_connect")) {
                const QString key = QStringLiteral("qmt_connect:") + backend;
                if (rememberPending(key, command, 20000)) client->connectBackend();
            } else if (action == QStringLiteral("redemption_qmt_disconnect")) {
                const QString key = QStringLiteral("qmt_disconnect:") + backend;
                if (rememberPending(key, command, 5000)) client->disconnectBackend();
            } else if (action == QStringLiteral("redemption_qmt_sync")) {
                const QString key = QStringLiteral("qmt_sync:") + backend;
                if (!client->isReady()) {
                    publishCommand(command, QStringLiteral("failed"),
                                   QStringLiteral("%1 尚未就绪，先执行连接").arg(backend));
                } else if (rememberPending(key, command, 15000)) {
                    client->requestSync();
                }
            } else if (action == QStringLiteral("redemption_qmt_order")) {
                const QString key = QStringLiteral("qmt_order:") + commandId;
                if (!rememberPending(key, command, command.value(QStringLiteral("deadline_ms")).toInt(20000))) return;
                QString orderError;
                if (!client->submitEtfOrder(arguments.value(QStringLiteral("symbol")).toString(),
                                            arguments.value(QStringLiteral("side")).toString(),
                                            commandId, &orderError)) {
                    finishPending(key, false, orderError);
                }
            } else {
                publishCommand(command, QStringLiteral("failed"), QStringLiteral("不支持的 QMT 动作"));
            }
            return;
        } else {
            if (!allowMutation(command)) return;
            if (action == QStringLiteral("redemption_set_watchlist")) {
                QStringList symbols;
                for (const auto &value : arguments.value(QStringLiteral("symbols")).toArray()) {
                    symbols.append(value.toString());
                }
                requestId = realtime_->updateWatchlist(symbols);
            } else if (action == QStringLiteral("redemption_set_symbol_name")) {
                requestId = realtime_->updateSymbolName(arguments.value(QStringLiteral("symbol")).toString(),
                                                        arguments.value(QStringLiteral("name")).toString());
            } else if (action == QStringLiteral("redemption_monitor_start")) requestId = realtime_->startMonitor();
            else if (action == QStringLiteral("redemption_monitor_stop")) requestId = realtime_->stopMonitor();
            else if (action == QStringLiteral("redemption_wind_start")) requestId = realtime_->startWind();
            else if (action == QStringLiteral("redemption_wind_shutdown_cleanup")) requestId = realtime_->shutdownWindCleanly();
            else if (action == QStringLiteral("redemption_pcf_refresh")) requestId = realtime_->refreshPcf();
        }
        if (!requestId.isEmpty()) {
            const QString key = action + u':' + command.value(QStringLiteral("command_id")).toString();
            realtimeRequestToPendingKey_.insert(requestId, key);
            rememberPending(key, command, command.value(QStringLiteral("deadline_ms")).toInt(120000));
            return;
        }
    }
    publishCommand(command, QStringLiteral("failed"), QStringLiteral("模块不支持该动作：%1").arg(action));
}

bool ModuleWorker::businessReadinessSatisfied(QString *reason) const {
    if (!lifecycleReadinessProbeIssued_) {
        if (reason) *reason = QStringLiteral("等待变更后独立 readiness 探针发出");
        return false;
    }
    const QJsonObject readiness = config_.settings.value(QStringLiteral("owner_readiness")).toObject();
    const QJsonArray conditions = readiness.value(QStringLiteral("conditions")).toArray();
    if (conditions.isEmpty()) {
        if (reason) *reason = QStringLiteral("owner_readiness 未配置");
        return false;
    }
    const QJsonObject webullStatus = telemetry_.value(QStringLiteral("status")).toObject();
    const bool statusObservedAfterChange =
        telemetryObservedAt_.value(QStringLiteral("status"))
            > lifecycleReadinessObservationBaseline_
        && telemetryObservedAtEpochMs_.value(QStringLiteral("status"))
            >= lifecycleReadinessNotBeforeMs_;
    const bool scheduledIdleOverride = config_.id == QStringLiteral("webull")
        && readiness.value(QStringLiteral("allow_scheduled_idle")).toBool(false)
        && statusObservedAfterChange
        && webullStatus.value(QStringLiteral("api_live")).toBool(false)
        && webullStatus.value(QStringLiteral("api_running")).toBool(false)
        && webullStatus.value(QStringLiteral("scheduled_idle")).toBool(false)
        && webullStatus.value(QStringLiteral("service")).toString()
            == QStringLiteral("webull-lv2-gateway");
    bool redemptionPollingHealthConfigured = false;
    if (config_.id == QStringLiteral("redemption")) {
        for (const auto &candidate : conditions) {
            const QJsonObject condition = candidate.toObject();
            if (condition.value(QStringLiteral("path")).toString()
                    == QStringLiteral("health.ok")
                && condition.value(QStringLiteral("equals")) == QJsonValue(true)) {
                redemptionPollingHealthConfigured = true;
                break;
            }
        }
    }
    for (const auto &value : conditions) {
        const QJsonObject condition = value.toObject();
        const QString path = condition.value(QStringLiteral("path")).toString();
        // Rolling upgrades may temporarily carry the old edge-triggered
        // health_probe assertion so the previous Agent can still validate the
        // file. Once continuous, contract-verified health.ok is present, the
        // new Agent must not wait for a state-change signal that intentionally
        // does not repeat while the service remains healthy.
        if (redemptionPollingHealthConfigured
            && path == QStringLiteral("health_probe.ready")) {
            continue;
        }
        if (scheduledIdleOverride
            && (path == QStringLiteral("status.api_ready")
                || path == QStringLiteral("status.data_fresh")
                || path == QStringLiteral("book.fresh"))) {
            continue;
        }
        const QString root = path.section(u'.', 0, 0);
        if (telemetryObservedAt_.value(root) <= lifecycleReadinessObservationBaseline_) {
            if (reason) {
                *reason = QStringLiteral("等待生命周期变更后的新鲜观测：%1").arg(path);
            }
            return false;
        }
        if (telemetryObservedAtEpochMs_.value(root) < lifecycleReadinessNotBeforeMs_) {
            if (reason) {
                *reason = QStringLiteral("等待 settle 窗口后的新一代观测：%1").arg(path);
            }
            return false;
        }
        const QJsonValue actual = valueAt(telemetry_, path);
        bool ok = !actual.isUndefined() && !actual.isNull();
        if (condition.contains(QStringLiteral("equals"))) {
            ok = ok && actual == condition.value(QStringLiteral("equals"));
        } else if (condition.value(QStringLiteral("one_of")).isArray()) {
            ok = false;
            for (const auto &candidate : condition.value(QStringLiteral("one_of")).toArray()) {
                if (actual == candidate) { ok = true; break; }
            }
        } else if (condition.value(QStringLiteral("non_empty")).toBool(false)) {
            ok = ok && ((actual.isString() && !actual.toString().isEmpty())
                        || (actual.isArray() && !actual.toArray().isEmpty())
                        || (actual.isObject() && !actual.toObject().isEmpty()));
        } else if (actual.isBool()) {
            ok = actual.toBool();
        }
        if (!ok) {
            if (reason) {
                *reason = QStringLiteral("业务 readiness 条件未满足：%1（当前 %2）")
                              .arg(path, displayValue(actual, QStringLiteral("<missing>")));
            }
            return false;
        }
    }
    return true;
}

void ModuleWorker::markTelemetryObserved(const QString &root, qint64 sourceEpochMs) {
    telemetryObservedAt_.insert(root, ++telemetryObservationSequence_);
    telemetryObservedAtEpochMs_.insert(
        root, sourceEpochMs > 0 ? sourceEpochMs : QDateTime::currentMSecsSinceEpoch());
    if (!pendingLifecycleResult_.isEmpty()) checkLifecycleReadiness();
}

void ModuleWorker::updatePremiumBusinessReadiness() {
    if (config_.adapter != QStringLiteral("premium")) return;

    const QJsonObject summaryChannel = telemetry_
        .value(QStringLiteral("summary_channel")).toObject();
    const QJsonObject l1Channel = telemetry_
        .value(QStringLiteral("l1_channel")).toObject();
    const QJsonObject status = telemetry_.value(QStringLiteral("status")).toObject();
    const QJsonObject l1Status = telemetry_.value(QStringLiteral("l1_status")).toObject();

    const bool channelsConnected =
        summaryChannel.value(QStringLiteral("state")).toString()
                == QStringLiteral("connected")
        && l1Channel.value(QStringLiteral("state")).toString()
                == QStringLiteral("connected");
    const bool statusContract = status.value(QStringLiteral("type")).toString()
                == QStringLiteral("status")
        && !status.value(QStringLiteral("phase")).toString().trimmed().isEmpty()
        && status.value(QStringLiteral("cn_quotes_desired")).isBool()
        && status.value(QStringLiteral("hk_quotes_desired")).isBool();
    const bool l1Contract = l1Status.value(QStringLiteral("v")).toInt(-1) == 1
        && l1Status.value(QStringLiteral("t")).toString()
                == QStringLiteral("status")
        && l1Status.value(QStringLiteral("symbols")).isArray()
        && l1Status.value(QStringLiteral("ready")).isArray()
        && l1Status.value(QStringLiteral("market_online")).isBool();

    const bool quotesDesired = statusContract
        && (status.value(QStringLiteral("cn_quotes_desired")).toBool()
            || status.value(QStringLiteral("hk_quotes_desired")).toBool());
    bool upstreamReady = statusContract;
    bool subscriptionsReady = statusContract && l1Contract;
    if (quotesDesired) {
        const qint64 watchCount = status
            .value(QStringLiteral("watchlist_symbols")).toInteger(-1);
        const qint64 readyCount = status
            .value(QStringLiteral("ready_symbols")).toInteger(-1);
        const qint64 hotCount = status
            .value(QStringLiteral("l1_hot_symbols")).toInteger(-1);
        const qint64 hotReady = status
            .value(QStringLiteral("l1_hot_ready")).toInteger(-1);
        upstreamReady = status.value(QStringLiteral("adapter_connected")).toBool(false)
            && status.value(QStringLiteral("upstream_healthy")).toBool(false)
            && !status.value(QStringLiteral("upstream_status"))
                    .toString().trimmed().isEmpty();
        subscriptionsReady = subscriptionsReady
            && watchCount >= 0 && readyCount >= watchCount
            && hotCount >= 0 && hotReady >= hotCount
            && l1Status.value(QStringLiteral("market_online")).toBool(false);
    }

    const bool ready = channelsConnected && statusContract && l1Contract
        && upstreamReady && subscriptionsReady;
    QString reason;
    if (!channelsConnected) reason = QStringLiteral("等待 8421/19195 两通道连接");
    else if (!statusContract) reason = QStringLiteral("等待 8421 权威 status 合同");
    else if (!l1Contract) reason = QStringLiteral("等待 19195 v1 status 合同");
    else if (!upstreamReady) reason = QStringLiteral("等待 TGW 登录与上游健康");
    else if (!subscriptionsReady) reason = QStringLiteral("等待观察清单/L1 热清单全部 ready");
    else reason = quotesDesired ? QStringLiteral("TGW 与订阅均已就绪")
                                : QStringLiteral("盘外计划空闲，控制通道就绪");

    telemetry_.insert(QStringLiteral("premium_readiness"), QJsonObject{
        {QStringLiteral("ready"), ready},
        {QStringLiteral("quotes_desired"), quotesDesired},
        {QStringLiteral("channels_connected"), channelsConnected},
        {QStringLiteral("status_contract_verified"), statusContract},
        {QStringLiteral("l1_contract_verified"), l1Contract},
        {QStringLiteral("upstream_ready"), upstreamReady},
        {QStringLiteral("subscriptions_ready"), subscriptionsReady},
        {QStringLiteral("reason"), reason}});
    markTelemetryObserved(QStringLiteral("premium_readiness"));
}

void ModuleWorker::updateRealtimeServiceIdentity(bool adapterObservation) {
    if (config_.adapter != QStringLiteral("realtime")) return;

    const QJsonObject health = telemetry_.value(QStringLiteral("health")).toObject();
    const QJsonObject snapshot = telemetry_.value(QStringLiteral("snapshot")).toObject();
    const bool healthContract = health
        .value(QStringLiteral("_hub_health_contract_verified")).toBool(false)
        && health.value(QStringLiteral("_hub_module_identity")).toString()
                == QStringLiteral("etf-realtime-monitor");
    const bool snapshotContract = snapshot
        .value(QStringLiteral("_hub_snapshot_contract_verified")).toBool(false)
        && snapshot.value(QStringLiteral("_hub_module_identity")).toString()
                == QStringLiteral("etf-realtime-monitor");

    bool ownerLeaseHeld = config_.ownership != QStringLiteral("owner");
    bool processIdentityConfigured = config_.ownership != QStringLiteral("owner");
    bool processIdentityVerified = config_.ownership != QStringLiteral("owner");
    if (config_.ownership == QStringLiteral("owner")) {
        ownerLeaseHeld = process_.value(QStringLiteral("owner_lease"))
                             .toObject().value(QStringLiteral("held")).toBool(false);
        processIdentityConfigured = true;
        processIdentityVerified = true;
        bool sawRequired = false;
        const QJsonArray units = process_.value(QStringLiteral("units")).toArray();
        for (const auto &value : units) {
            const QJsonObject unit = value.toObject();
            if (!unit.value(QStringLiteral("required")).toBool(false)) continue;
            sawRequired = true;
            processIdentityConfigured = processIdentityConfigured
                && unit.value(QStringLiteral("identity_gate_configured")).toBool(false);
            processIdentityVerified = processIdentityVerified
                && unit.value(QStringLiteral("running")).toBool(false)
                && unit.value(QStringLiteral("identity_verified")).toBool(false);
        }
        processIdentityConfigured = processIdentityConfigured && sawRequired;
        processIdentityVerified = processIdentityVerified && sawRequired;
    }

    const bool instanceVerified = healthContract && snapshotContract
        && ownerLeaseHeld && processIdentityConfigured && processIdentityVerified;
    telemetry_.insert(QStringLiteral("service_identity"), QJsonObject{
        {QStringLiteral("module"), QStringLiteral("etf-realtime-monitor")},
        {QStringLiteral("health_contract_verified"), healthContract},
        {QStringLiteral("snapshot_contract_verified"), snapshotContract},
        {QStringLiteral("owner_lease_held"), ownerLeaseHeld},
        {QStringLiteral("process_identity_configured"), processIdentityConfigured},
        {QStringLiteral("process_identity_verified"), processIdentityVerified},
        {QStringLiteral("instance_verified"), instanceVerified},
        {QStringLiteral("last_trigger"), adapterObservation
             ? QStringLiteral("adapter") : QStringLiteral("lifecycle")}});
    markTelemetryObserved(QStringLiteral("service_identity"));
}

void ModuleWorker::beginLifecycleReadiness(const QJsonObject &result) {
    pendingLifecycleResult_ = result;
    lifecycleReadinessTimeoutEmitted_ = false;
    lifecycleReadinessProbeIssued_ = false;
    const quint64 generation = ++lifecycleReadinessGeneration_;
    lifecycleReadinessTimer_.setInterval(500);
    lifecycleReadinessObservationBaseline_ = telemetryObservationSequence_;
    const int timeout = config_.settings.value(QStringLiteral("owner_readiness")).toObject()
                            .value(QStringLiteral("timeout_ms")).toInt(30000);
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    const int settleMs = std::clamp(
        config_.settings.value(QStringLiteral("owner_readiness")).toObject()
            .value(QStringLiteral("settle_ms")).toInt(1000), 250, 10000);
    lifecycleReadinessNotBeforeMs_ = now + settleMs;
    const qint64 configuredDeadline = now + std::clamp(timeout, 1000, 300000);
    const qint64 commandDeadline = result.value(QStringLiteral("command_deadline_epoch_ms"))
                                       .toInteger(configuredDeadline);
    lifecycleReadinessDeadlineMs_ = std::min(configuredDeadline, commandDeadline);
    const QJsonObject command{{QStringLiteral("command_id"), result.value(QStringLiteral("command_id"))},
                              {QStringLiteral("action"), result.value(QStringLiteral("action"))}};
    const QString key = QStringLiteral("lifecycle_ready:")
        + result.value(QStringLiteral("command_id")).toString();
    pendingCommands_.insert(key, command);
    pendingTransportQuiescenceUntilMs_.insert(key, lifecycleReadinessDeadlineMs_ + 1000);
    publishCommand(command, QStringLiteral("running"),
                   QStringLiteral("launchd 与 artifact 已核对，正等待业务 readiness"),
                   {{QStringLiteral("launchd_result"), result}});
    lifecycleReadinessTimer_.start();
    // Tear down observer transports after the launchd transition. The fresh
    // readiness request is issued only after settle_ms, so an in-flight reply
    // from the previous service instance cannot satisfy the new generation.
    suspendAdaptersForReadinessProbe();
    QTimer::singleShot(settleMs, this, [this, generation,
                                       commandId = result.value(QStringLiteral("command_id")).toString()] {
        startFreshReadinessProbe(generation, commandId);
    });
    checkLifecycleReadiness();
}

void ModuleWorker::suspendAdaptersForReadinessProbe() {
    readinessAdaptersSuspended_ = true;
    if (upload_) upload_->stop();
    if (premium_) premium_->stop();
    if (premiumProbe_) premiumProbe_->stop();
    if (webull_) webull_->stop();
    if (realtime_) realtime_->stop();
}

void ModuleWorker::resumeAdaptersAfterReadinessProbe() {
    readinessAdaptersSuspended_ = false;
    if (upload_) upload_->start();
    if (premium_) premium_->start();
    if (premiumProbe_) premiumProbe_->start();
    if (webull_) {
        webull_->start();
        webull_->refreshNow();
    }
    if (realtime_) {
        realtime_->start();
        realtime_->refreshAll();
    }
}

void ModuleWorker::startFreshReadinessProbe(quint64 generation,
                                            const QString &commandId) {
    if (generation != lifecycleReadinessGeneration_
        || pendingLifecycleResult_.value(QStringLiteral("command_id")).toString()
               != commandId) return;
    lifecycleReadinessObservationBaseline_ = telemetryObservationSequence_;
    lifecycleReadinessNotBeforeMs_ = QDateTime::currentMSecsSinceEpoch();
    lifecycleReadinessProbeIssued_ = true;
    resumeAdaptersAfterReadinessProbe();
    checkLifecycleReadiness();
}

void ModuleWorker::checkLifecycleReadiness() {
    if (pendingLifecycleResult_.isEmpty()) {
        lifecycleReadinessTimer_.stop();
        return;
    }
    const QString commandId = pendingLifecycleResult_.value(QStringLiteral("command_id")).toString();
    const QJsonObject command{{QStringLiteral("command_id"), commandId},
                              {QStringLiteral("action"),
                               pendingLifecycleResult_.value(QStringLiteral("action"))}};
    const QString key = QStringLiteral("lifecycle_ready:") + commandId;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    QString reason;
    if (!lifecycleReadinessTimeoutEmitted_ && now >= lifecycleReadinessDeadlineMs_) {
        // A command deadline may be shorter than settle_ms. Keep the result
        // uncertain, but never leave observation transports suspended: begin a
        // fresh generation immediately and continue watching for a late,
        // authoritative confirmation.
        if (readinessAdaptersSuspended_) {
            lifecycleReadinessObservationBaseline_ = telemetryObservationSequence_;
            lifecycleReadinessNotBeforeMs_ = now;
            lifecycleReadinessProbeIssued_ = true;
            resumeAdaptersAfterReadinessProbe();
        }
        lifecycleReadinessTimeoutEmitted_ = true;
        lifecycleReadinessTimer_.setInterval(1000);
        businessReadinessSatisfied(&reason);
        timedOutPending_.insert(key);
        publishCommand(command, QStringLiteral("timed_out"),
                       QStringLiteral("launchd 已变更，但业务 readiness 未在期限内通过；持续追踪并锁定新变更"),
                       {{QStringLiteral("outcome_uncertain"), true},
                        {QStringLiteral("reconciliation_required"), true},
                        {QStringLiteral("tracking_late_result"), true},
                        {QStringLiteral("readiness_failure"), reason},
                        {QStringLiteral("launchd_result"), pendingLifecycleResult_}});
        return;
    }
    if (businessReadinessSatisfied(&reason)) {
        const bool lateConfirmation = lifecycleReadinessTimeoutEmitted_;
        QJsonObject details = pendingLifecycleResult_;
        details.insert(QStringLiteral("business_readiness_verified"), true);
        details.insert(QStringLiteral("business_readiness_verified_at"), utcNow());
        details.insert(QStringLiteral("late_confirmation"), lateConfirmation);
        pendingCommands_.remove(key);
        pendingTransportQuiescenceUntilMs_.remove(key);
        timedOutPending_.remove(key);
        pendingLifecycleResult_ = {};
        lifecycleReadinessTimer_.stop();
        publishCommand(command, QStringLiteral("succeeded"),
                       lateConfirmation
                           ? QStringLiteral("服务的迟到权威 readiness 已确认，未知结果锁已自动解除")
                           : QStringLiteral("服务已启动，artifact 身份与业务 readiness 均已验证"),
                       details);
        return;
    }
}

bool ModuleWorker::rememberPending(const QString &key, const QJsonObject &command, int timeoutMs) {
    if (key.isEmpty()) {
        publishCommand(command, QStringLiteral("failed"), QStringLiteral("内部请求标识为空"));
        return false;
    }
    if (pendingCommands_.contains(key)) {
        publishCommand(command, QStringLiteral("failed"), QStringLiteral("同类命令正在执行，请等待其完成"));
        return false;
    }
    pendingCommands_.insert(key, command);
    publishCommand(command, QStringLiteral("running"), QStringLiteral("命令正在异步执行"));
    const QString commandId = command.value(QStringLiteral("command_id")).toString();
    const int safeTimeout = std::clamp(timeoutMs, 1000, 30 * 60 * 1000);
    if (stateChangingAction(command.value(QStringLiteral("action")).toString())) {
        const QString action = command.value(QStringLiteral("action")).toString();
        int transportWindow = safeTimeout;
        if (action.startsWith(QStringLiteral("webull_"))) {
            transportWindow = qMax(
                transportWindow,
                config_.settings.value(QStringLiteral("control_timeout_ms")).toInt(45000));
        } else if (action.startsWith(QStringLiteral("redemption_qmt_"))) {
            transportWindow = qMax(transportWindow, 30000);
        } else if (action.startsWith(QStringLiteral("redemption_"))) {
            transportWindow = qMax(
                transportWindow,
                config_.settings.value(QStringLiteral("control_timeout_ms")).toInt(120000));
        }
        pendingTransportQuiescenceUntilMs_.insert(
            key, QDateTime::currentMSecsSinceEpoch()
                     + std::clamp(transportWindow, 1000, 30 * 60 * 1000) + 1000);
    }
    QTimer::singleShot(safeTimeout, this, [this, key, commandId] {
        if (!pendingCommands_.contains(key)) return;
        const auto pending = pendingCommands_.value(key);
        if (pending.value(QStringLiteral("command_id")).toString() != commandId) return;
        const bool mutation = stateChangingAction(
            pending.value(QStringLiteral("action")).toString());
        if (mutation) {
            timedOutPending_.insert(key);
            publishCommand(pending, QStringLiteral("timed_out"),
                           QStringLiteral("变更命令在截止时间内未收到权威响应；将持续锁定后续变更并追踪迟到终态"),
                           {{QStringLiteral("outcome_uncertain"), true},
                            {QStringLiteral("reconciliation_required"), true},
                            {QStringLiteral("tracking_late_result"), true}});
        } else {
            pendingCommands_.remove(key);
            publishCommand(pending, QStringLiteral("timed_out"),
                           QStringLiteral("只读查询超时；未改变业务状态，可立即重试"),
                           {{QStringLiteral("outcome_uncertain"), false},
                            {QStringLiteral("tracking_late_result"), false}});
        }
    });
    return true;
}

void ModuleWorker::finishPending(const QString &key, bool ok, const QString &message, const QJsonObject &details) {
    if (key.isEmpty() || !pendingCommands_.contains(key)) return;
    const auto command = pendingCommands_.value(key);
    const bool uncertain = stateChangingAction(command.value(QStringLiteral("action")).toString())
        && details.value(QStringLiteral("outcome_uncertain")).toBool(false);
    if (uncertain) {
        timedOutPending_.insert(key);
        QJsonObject safeDetails = details;
        safeDetails.insert(QStringLiteral("reconciliation_required"), true);
        publishCommand(command, QStringLiteral("timed_out"), message, safeDetails);
        return;
    }
    pendingCommands_.remove(key);
    pendingTransportQuiescenceUntilMs_.remove(key);
    timedOutPending_.remove(key);
    publishCommand(command, ok ? QStringLiteral("succeeded") : QStringLiteral("failed"), message, details);
}

void ModuleWorker::publishCommand(const QJsonObject &command, const QString &state,
                                  const QString &message, const QJsonObject &details) {
    if (state == QStringLiteral("succeeded")
        && stateChangingAction(command.value(QStringLiteral("action")).toString())) {
        ++controlRevision_;
        scheduleSnapshot();
    }
    QJsonObject result{{QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                       {QStringLiteral("schema_version"), 1},
                       {QStringLiteral("type"), QStringLiteral("command_result")},
                       {QStringLiteral("module_id"), config_.id},
                       {QStringLiteral("command_id"), command.value(QStringLiteral("command_id")).toString()},
                       {QStringLiteral("action"), command.value(QStringLiteral("action")).toString()},
                       {QStringLiteral("state"), state}, {QStringLiteral("message"), message},
                       {QStringLiteral("timestamp"), utcNow()},
                       {QStringLiteral("control_revision"), static_cast<qint64>(controlRevision_)},
                       {QStringLiteral("details"), details}};
    emit commandChanged(result);
    if (state == QStringLiteral("succeeded") || state == QStringLiteral("failed")
        || state == QStringLiteral("timed_out")) {
        const QString commandId = command.value(QStringLiteral("command_id")).toString();
        if (activeCommandId_ == commandId) activeCommandId_.clear();
    }
}

void ModuleWorker::publishEvent(const QString &kind, const QJsonObject &payload) {
    QJsonObject message = envelope(QStringLiteral("event"), payload);
    message.insert(QStringLiteral("event_kind"), kind);
    message.insert(QStringLiteral("sequence"), static_cast<qint64>(++sequence_));
    if (criticalEventKind(kind)) {
        if (criticalIngressTripped_) return;
        const qint64 bytes = QJsonDocument(message).toJson(QJsonDocument::Compact).size();
        constexpr qsizetype MaximumCriticalEvents = 4096;
        constexpr qint64 MaximumCriticalBytes = 32LL * 1024 * 1024;
        if (bytes <= 0 || bytes > 4LL * 1024 * 1024
            || criticalEventMailbox_.size() >= MaximumCriticalEvents - 1
            || criticalEventMailboxBytes_ + bytes > MaximumCriticalBytes) {
            criticalIngressTripped_ = true;
            lastError_ = QStringLiteral("关键事件入口超过有界 mailbox；已断开本模块观测桥防止影响其他模块");
            workState_ = QStringLiteral("blocked");
            if (upload_) upload_->stop();
            if (premium_) premium_->stop();
            if (premiumProbe_) premiumProbe_->stop();
            if (webull_) webull_->stop();
            if (realtime_) realtime_->stop();
            for (auto *client : std::as_const(qmtClients_)) client->disconnectBackend();
            QJsonObject overflow = envelope(
                QStringLiteral("event"),
                QJsonObject{{QStringLiteral("message"), lastError_},
                            {QStringLiteral("queued_events"),
                             static_cast<qint64>(criticalEventMailbox_.size())},
                            {QStringLiteral("queued_bytes"), criticalEventMailboxBytes_}});
            overflow.insert(QStringLiteral("event_kind"),
                            QStringLiteral("module.critical_ingress_error"));
            overflow.insert(QStringLiteral("sequence"), static_cast<qint64>(++sequence_));
            criticalEventMailbox_.enqueue(overflow);
            criticalEventMailboxBytes_ += QJsonDocument(overflow)
                .toJson(QJsonDocument::Compact).size();
            scheduleSnapshot();
        } else {
            criticalEventMailbox_.enqueue(message);
            criticalEventMailboxBytes_ += bytes;
        }
        // Business alerts/errors must not wait behind the telemetry cadence.
        eventDrainTimer_.start(0);
        return;
    }

    static const QSet<QString> latestOnly{
        QStringLiteral("premium.summary"), QStringLiteral("premium.detail"),
        QStringLiteral("premium.l1_status"),
        QStringLiteral("webull.book"), QStringLiteral("webull.clients"),
        QStringLiteral("redemption.snapshot"), QStringLiteral("redemption.status"),
        QStringLiteral("process.log")};
    if (latestOnly.contains(kind)) {
        const QString symbol = payload.value(QStringLiteral("symbol")).toString();
        const QString key = kind + u'|' + symbol;
        constexpr qsizetype MaximumLatestKeys = 4096;
        if (!latestEventMailbox_.contains(key)
            && latestEventMailbox_.size() >= MaximumLatestKeys) {
            latestEventMailbox_.erase(latestEventMailbox_.begin());
            ++droppedNonCriticalEvents_;
        }
        latestEventMailbox_.insert(key, message);
    } else {
        constexpr qsizetype MaximumOrdinaryEvents = 512;
        if (ordinaryEventMailbox_.size() >= MaximumOrdinaryEvents) {
            ordinaryEventMailbox_.dequeue();
            ++droppedNonCriticalEvents_;
        }
        ordinaryEventMailbox_.enqueue(message);
    }
    if (!eventDrainTimer_.isActive()) eventDrainTimer_.start();
}

void ModuleWorker::drainEventMailbox() {
    int delivered = 0;
    while (!criticalEventMailbox_.isEmpty() && delivered < 64) {
        const QJsonObject message = criticalEventMailbox_.dequeue();
        criticalEventMailboxBytes_ = qMax<qint64>(
            0, criticalEventMailboxBytes_
                   - QJsonDocument(message).toJson(QJsonDocument::Compact).size());
        emit detailEvent(message);
        ++delivered;
    }
    while (!latestEventMailbox_.isEmpty() && delivered < 256) {
        auto it = latestEventMailbox_.begin();
        const QJsonObject message = it.value();
        latestEventMailbox_.erase(it);
        emit detailEvent(message);
        ++delivered;
    }
    while (!ordinaryEventMailbox_.isEmpty() && delivered < 320) {
        emit detailEvent(ordinaryEventMailbox_.dequeue());
        ++delivered;
    }
    if (droppedNonCriticalEvents_ > 0) {
        const quint64 dropped = std::exchange(droppedNonCriticalEvents_, 0);
        QJsonObject warning = envelope(
            QStringLiteral("event"),
            QJsonObject{{QStringLiteral("message"),
                         QStringLiteral("模块普通遥测突发超过有界 mailbox，已合并/丢弃最旧普通事件")},
                        {QStringLiteral("dropped_count"), static_cast<qint64>(dropped)}});
        warning.insert(QStringLiteral("event_kind"),
                       QStringLiteral("module.telemetry_overflow"));
        warning.insert(QStringLiteral("sequence"), static_cast<qint64>(++sequence_));
        emit detailEvent(warning);
    }
    if (!criticalEventMailbox_.isEmpty() || !latestEventMailbox_.isEmpty()
        || !ordinaryEventMailbox_.isEmpty()) {
        eventDrainTimer_.start();
    }
}

void ModuleWorker::scheduleSnapshot() {
    if (!publishTimer_.isActive()) publishTimer_.start();
}

void ModuleWorker::publishSnapshot() {
    QString lifecycle = process_.value(QStringLiteral("lifecycle")).toString(QStringLiteral("unknown"));
    bool transportAlive = false;
    if (config_.adapter == QStringLiteral("upload")) {
        const auto engine = telemetry_.value(QStringLiteral("engine")).toObject();
        transportAlive = config_.engine == QStringLiteral("native")
            ? engine.value(QStringLiteral("running")).toBool(false)
            : engine.value(QStringLiteral("ready")).toBool(false);
    }
    else if (config_.adapter == QStringLiteral("premium")) {
        transportAlive = config_.engine == QStringLiteral("native")
            ? telemetry_.value(QStringLiteral("engine")).toObject()
                  .value(QStringLiteral("running")).toBool(false)
            : telemetry_.value(QStringLiteral("summary_channel")).toObject()
                  .value(QStringLiteral("state")).toString() == QStringLiteral("connected");
    }
    else if (config_.adapter == QStringLiteral("webull")) transportAlive = telemetry_.value(QStringLiteral("status")).toObject().value(QStringLiteral("api_live")).toBool();
    else if (config_.engine == QStringLiteral("native")) {
        // Engine liveness and data-source health are different concepts. A
        // missing/blocked Wind probe degrades Redemption but does not mean the
        // in-process module thread has stopped.
        transportAlive = telemetry_.value(QStringLiteral("engine")).toObject()
                             .value(QStringLiteral("running")).toBool(false);
    } else {
        const QJsonObject health = telemetry_.value(QStringLiteral("health")).toObject();
        // Legacy realtime adapters expose `ready`; the native RedemptionEngine
        // uses the common engine health contract (`ok`).  Accept both so an
        // owner-less native module is not incorrectly reported as stopped.
        transportAlive = health.value(QStringLiteral("ready"))
                             .toBool(health.value(QStringLiteral("ok")).toBool(false));
    }
    if (config_.ownership != QStringLiteral("owner") && transportAlive
        && (lifecycle == QStringLiteral("unknown") || lifecycle == QStringLiteral("stopped"))) {
        lifecycle = QStringLiteral("running");
    }

    QJsonArray commands{QStringLiteral("refresh"), QStringLiteral("open_legacy_ui"),
                        QStringLiteral("set_operating_mode")};
    if (config_.adapter == QStringLiteral("upload")) {
        commands.append(QStringLiteral("upload_run_job"));
        commands.append(QStringLiteral("upload_ingest_quote"));
        commands.append(QStringLiteral("upload_ingest_valuation"));
        commands.append(QStringLiteral("upload_ingest_dataset"));
        commands.append(QStringLiteral("upload_ack"));
        commands.append(QStringLiteral("upload_set_fund"));
        commands.append(QStringLiteral("upload_add_message"));
        commands.append(QStringLiteral("upload_ibkr_reconnect"));
        commands.append(QStringLiteral("upload_ibkr_disconnect"));
    } else if (config_.adapter == QStringLiteral("premium")) {
        commands.append(QStringLiteral("premium_sync"));
        commands.append(QStringLiteral("premium_raw_snapshot"));
        commands.append(QStringLiteral("premium_detail_subscribe"));
        commands.append(QStringLiteral("premium_detail_unsubscribe"));
        commands.append(QStringLiteral("premium_set_watchlist"));
        commands.append(QStringLiteral("premium_set_l1_hotlist"));
    } else if (config_.adapter == QStringLiteral("webull")) {
        commands.append(QStringLiteral("webull_get_book"));
        if (webull_
            || !config_.settings.value(QStringLiteral("control_base_url")).toString().isEmpty()) {
            commands.append(QStringLiteral("webull_set_mode"));
            commands.append(QStringLiteral("webull_collector_start"));
            commands.append(QStringLiteral("webull_collector_stop"));
            commands.append(QStringLiteral("webull_restart_browser"));
            commands.append(QStringLiteral("webull_show_login"));
        }
    } else if (config_.adapter == QStringLiteral("realtime")) {
        for (const auto &name : {QStringLiteral("redemption_get_history"), QStringLiteral("redemption_get_pcf"),
                                 QStringLiteral("redemption_set_watchlist"), QStringLiteral("redemption_set_symbol_name"),
                                 QStringLiteral("redemption_monitor_start"), QStringLiteral("redemption_monitor_stop"),
                                 QStringLiteral("redemption_wind_start"), QStringLiteral("redemption_wind_shutdown_cleanup"),
                                 QStringLiteral("redemption_pcf_refresh"), QStringLiteral("redemption_qmt_connect"),
                                 QStringLiteral("redemption_qmt_disconnect"), QStringLiteral("redemption_qmt_sync"),
                                 QStringLiteral("redemption_qmt_order")}) commands.append(name);
    }
    if (config_.ownership == QStringLiteral("owner")
        || (config_.engine == QStringLiteral("native")
            && config_.ownership == QStringLiteral("logic"))) {
        commands.append(QStringLiteral("start_service"));
        commands.append(QStringLiteral("stop_service"));
        commands.append(QStringLiteral("restart_service"));
    }
    if (sync_) for(const auto &a:{QStringLiteral("monitor_sync_set_port"),QStringLiteral("monitor_sync_status"),QStringLiteral("monitor_sync_set_root"),QStringLiteral("monitor_sync_backup"),QStringLiteral("monitor_sync_add_device"),QStringLiteral("monitor_sync_revoke_device")}) commands.append(a);
    QJsonArray allowedCommands;
    for (const auto &value : commands) {
        if (config_.allowedActions.contains(value.toString())) allowedCommands.append(value);
    }
    if (config_.allowedActions.contains(QStringLiteral("acknowledge_uncertain"))) {
        allowedCommands.append(QStringLiteral("acknowledge_uncertain"));
    }
    commands = allowedCommands;

    QJsonObject payload{{QStringLiteral("display_name"), config_.displayName},
                        {QStringLiteral("adapter"), config_.adapter},
                        {QStringLiteral("engine"), config_.id == QStringLiteral("upload")
                             ? QStringLiteral("native") : config_.engine},
                        {QStringLiteral("lifecycle"), lifecycle},
                        {QStringLiteral("work_state"), workState_},
                        {QStringLiteral("desired_state"), config_.ownership == QStringLiteral("shadow")
                                                                 ? QStringLiteral("observe") : QStringLiteral("managed")},
                        {QStringLiteral("ownership"), config_.ownership},
                        {QStringLiteral("control_enabled"), config_.controlEnabled},
                        {QStringLiteral("headline"), headline_},
                        {QStringLiteral("last_error"), lastError_},
                        {QStringLiteral("unresolved_outcomes"), timedOutPending_.size()},
                        {QStringLiteral("process"), process_},
                        {QStringLiteral("telemetry"), telemetry_},
                        {QStringLiteral("capabilities"), QJsonObject{{QStringLiteral("commands"), commands}}}};
    QJsonObject message = envelope(QStringLiteral("snapshot"), payload);
    message.insert(QStringLiteral("sequence"), static_cast<qint64>(++sequence_));
    message.insert(QStringLiteral("control_revision"), static_cast<qint64>(controlRevision_));
    emit snapshotChanged(message);
}

} // namespace hub
