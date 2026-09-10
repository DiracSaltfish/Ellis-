#include "ui/HubClient.h"

#include "common/FrameCodec.h"
#include "common/JsonUtil.h"

#include <QCoreApplication>
#include <QFileInfo>
#include <QJsonArray>
#include <QProcess>
#include <QSettings>
#include <algorithm>
#include <utility>

namespace hub {

HubClient::HubClient(QString socketPath, QString configPath, QString agentProgram,
                     int frameLimitBytes, QString expectedConfigHash, QObject *parent)
    : QObject(parent), socketPath_(std::move(socketPath)), configPath_(std::move(configPath)),
      agentProgram_(std::move(agentProgram)), frameLimitBytes_(frameLimitBytes),
      expectedConfigHash_(std::move(expectedConfigHash)), clientInstanceId_(randomId()) {
    // MainWindow constructs this object before moving it to the dedicated IPC
    // thread. Give the value-member QObjects an owner so their affinity follows
    // HubClient during moveToThread().
    socket_.setParent(this);
    reconnectTimer_.setParent(this);
    deliveryTimer_.setParent(this);
    reconnectTimer_.setSingleShot(true);
    connect(&reconnectTimer_, &QTimer::timeout, this, &HubClient::connectNow);
    deliveryTimer_.setSingleShot(true);
    // UI telemetry is observational and may arrive at market-data frequency.
    // Coalesce it to a human-readable cadence so the GUI thread remains free
    // for navigation and control actions. Critical events can still expedite
    // this timer in queueEvent().
    deliveryTimer_.setInterval(250);
    connect(&deliveryTimer_, &QTimer::timeout, this, &HubClient::flushDelivery);
    connect(&socket_, &QLocalSocket::connected, this, &HubClient::onConnected);
    connect(&socket_, &QLocalSocket::disconnected, this, &HubClient::onDisconnected);
    connect(&socket_, &QLocalSocket::readyRead, this, &HubClient::onReadyRead);
    connect(&socket_, &QLocalSocket::errorOccurred, this, &HubClient::onError);
}

void HubClient::start() {
    stopping_ = false;
    QSettings settings;
    const QString cursorKey = QStringLiteral("ipc/critical_cursor/%1").arg(expectedConfigHash_);
    const QString epochKey = QStringLiteral("ipc/critical_epoch/%1").arg(expectedConfigHash_);
    lastCriticalEventId_ = qMax<qint64>(0, settings.value(cursorKey, 0).toLongLong());
    lastCriticalEventEpoch_ = settings.value(epochKey).toString();
    highestCriticalQueuedId_ = lastCriticalEventId_;
    connectNow();
}

void HubClient::stop() {
    stopping_ = true;
    reconnectTimer_.stop();
    deliveryTimer_.stop();
    socket_.disconnectFromServer();
}

void HubClient::connectNow() {
    if (stopping_ || socket_.state() != QLocalSocket::UnconnectedState) return;
    emit connectionChanged(false, QStringLiteral("正在连接 Agent…"));
    socket_.connectToServer(socketPath_, QIODevice::ReadWrite);
}

void HubClient::maybeStartAgent() {
    if (agentStartAttempted_ || agentProgram_.isEmpty() || !QFileInfo::exists(agentProgram_)) return;
    agentStartAttempted_ = true;
    if (!QProcess::startDetached(agentProgram_, {QStringLiteral("--config"), configPath_})) {
        emit protocolError(QStringLiteral("无法启动 Hub Agent：%1").arg(agentProgram_));
    }
}

void HubClient::scheduleReconnect() {
    if (stopping_) return;
    const int delay = std::min(10000, 250 * (1 << std::min(reconnectAttempt_, 5)));
    ++reconnectAttempt_;
    reconnectTimer_.start(delay);
}

void HubClient::onConnected() {
    ++connectionGeneration_;
    reconnectAttempt_ = 0;
    readBuffer_.clear();
    criticalEvents_.clear();
    pendingCriticalEventIds_.clear();
    highestCriticalQueuedId_ = lastCriticalEventId_;
    helloComplete_ = false;
    emit connectionChanged(true, QStringLiteral("Agent 已连接"));
    write(QJsonObject{{QStringLiteral("schema_version"), 1},
                      {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                      {QStringLiteral("type"), QStringLiteral("hello")},
                      {QStringLiteral("client"), QStringLiteral("machome-hub-ui")},
                      {QStringLiteral("client_instance_id"), clientInstanceId_},
                      {QStringLiteral("config_sha256"), expectedConfigHash_},
                      {QStringLiteral("last_critical_event_id"), lastCriticalEventId_},
                      {QStringLiteral("last_critical_event_epoch"), lastCriticalEventEpoch_},
                      {QStringLiteral("version"), QCoreApplication::applicationVersion()},
                      {QStringLiteral("timestamp"), utcNow()}});
}

void HubClient::onDisconnected() {
    agentInstanceId_.clear();
    configBound_ = false;
    controlReady_ = false;
    helloComplete_ = false;
    approvalActions_.clear();
    pendingApprovals_.clear();
    emit bindingChanged(false, QStringLiteral("Agent 连接已中断；控制已锁定"));
    emit connectionChanged(false, QStringLiteral("Agent 已断开，正在重连"));
    scheduleReconnect();
}

void HubClient::onError(QLocalSocket::LocalSocketError error) {
    if (error == QLocalSocket::ServerNotFoundError || error == QLocalSocket::ConnectionRefusedError) {
        maybeStartAgent();
    }
    // QLocalSocket can emit errorOccurred before its state becomes Unconnected.
    // Queue the retry check so starting the Agent on the first failure cannot
    // leave the desktop permanently stuck in Connecting.
    QTimer::singleShot(0, this, [this] {
        if (!stopping_ && socket_.state() == QLocalSocket::UnconnectedState) scheduleReconnect();
    });
}

void HubClient::onReadyRead() {
    QList<QJsonObject> messages;
    QString error;
    if (!FrameCodec::consume(readBuffer_, socket_.readAll(), &messages, &error, frameLimitBytes_)) {
        emit protocolError(error);
        socket_.abort();
        return;
    }
    for (const auto &message : messages) {
        const QString type = message.value(QStringLiteral("type")).toString();
        const QString expectedProtocol = type == QStringLiteral("snapshot")
                                             ? QStringLiteral("module.status.v1")
                                         : type == QStringLiteral("event")
                                             ? QStringLiteral("module.event.v1")
                                             : QStringLiteral("module.control.v1");
        if (message.value(QStringLiteral("schema_version")).toInt() != 1 ||
            message.value(QStringLiteral("protocol")).toString() != expectedProtocol) {
            emit protocolError(QStringLiteral("Agent 返回了不兼容的协议消息：%1").arg(type));
            continue;
        }
        if (type == QStringLiteral("hello")) {
            helloComplete_ = true;
            agentInstanceId_ = message.value(QStringLiteral("instance_id")).toString();
            const QString serverEpoch = message.value(QStringLiteral("audit_epoch")).toString();
            const qint64 replayFloor = message.value(QStringLiteral("critical_event_floor"))
                                           .toInteger(0);
            const qint64 replayHighWater = message.value(QStringLiteral("critical_event_high_water"))
                                               .toInteger(0);
            const bool epochChanged = serverEpoch.isEmpty()
                || serverEpoch != lastCriticalEventEpoch_;
            const bool cursorRolledBack = lastCriticalEventId_ > replayHighWater;
            const bool retentionGap = replayFloor > 0
                && lastCriticalEventId_ < replayFloor - 1;
            auditEpoch_ = serverEpoch;
            if (epochChanged || cursorRolledBack) {
                ++connectionGeneration_;
                lastCriticalEventId_ = 0;
                highestCriticalQueuedId_ = 0;
                pendingCriticalEventIds_.clear();
                criticalEvents_.clear();
            }
            if (retentionGap && !epochChanged && !cursorRolledBack) {
                lastCriticalEventId_ = replayFloor - 1;
                highestCriticalQueuedId_ = lastCriticalEventId_;
                emit protocolError(QStringLiteral(
                    "关键事件游标早于 Agent 保留窗口；较早事件请从审计数据库归档核对"));
            }
            lastCriticalEventEpoch_ = auditEpoch_;
            persistCriticalCursor();
            configBound_ = message.value(QStringLiteral("client_config_bound")).toBool(false)
                && message.value(QStringLiteral("config_sha256")).toString() == expectedConfigHash_;
            controlReady_ = configBound_ && message.value(QStringLiteral("control_ready")).toBool(false);
            approvalActions_.clear();
            for (const auto &moduleValue : message.value(QStringLiteral("modules")).toArray()) {
                const auto module = moduleValue.toObject();
                QSet<QString> actions;
                for (const auto &value : module.value(QStringLiteral("approval_required_actions")).toArray()) {
                    if (value.isString()) actions.insert(value.toString());
                }
                approvalActions_.insert(module.value(QStringLiteral("id")).toString(), actions);
            }
            emit bindingChanged(controlReady_, !configBound_
                ? QStringLiteral("UI 配置与 Agent 不一致；控制已锁定")
                : controlReady_ ? QStringLiteral("UI 已绑定当前 Agent 与配置；控制面就绪")
                                : QStringLiteral("Agent 审计/命令账本未就绪；控制已锁定"));
            emit helloReceived(message);
        }
        else if (type == QStringLiteral("snapshot")) queueSnapshot(message);
        else if (type == QStringLiteral("event")) queueEvent(message);
        else if (type == QStringLiteral("command_result")) emit commandReceived(message);
        else if (type == QStringLiteral("approval_ticket")) {
            const QString requestId = message.value(QStringLiteral("request_id")).toString();
            if (!pendingApprovals_.contains(requestId)) {
                emit protocolError(QStringLiteral("收到未知审批票据，已忽略"));
                continue;
            }
            QJsonObject command = pendingApprovals_.take(requestId);
            command.insert(QStringLiteral("approval_token"),
                           message.value(QStringLiteral("approval_token")));
            write(command);
        }
        else if (type == QStringLiteral("error")) emit protocolError(message.value(QStringLiteral("message")).toString());
    }
}

void HubClient::acknowledgeCriticalEvent(qint64 auditEventId,
                                         const QString &auditEpoch,
                                         quint64 deliveryGeneration) {
    if (auditEpoch.isEmpty() || auditEpoch != auditEpoch_
        || deliveryGeneration != connectionGeneration_
        || !pendingCriticalEventIds_.contains(auditEventId)) return;
    if (auditEventId <= lastCriticalEventId_) return;
    lastCriticalEventId_ = auditEventId;
    for (auto it = pendingCriticalEventIds_.begin(); it != pendingCriticalEventIds_.end();) {
        if (*it <= lastCriticalEventId_) it = pendingCriticalEventIds_.erase(it);
        else ++it;
    }
    persistCriticalCursor();
    if (!helloComplete_ || socket_.state() != QLocalSocket::ConnectedState) return;
    write(QJsonObject{{QStringLiteral("schema_version"), 1},
                      {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                      {QStringLiteral("type"), QStringLiteral("critical_ack")},
                      {QStringLiteral("audit_epoch"), auditEpoch_},
                      {QStringLiteral("audit_event_id"), lastCriticalEventId_},
                      {QStringLiteral("timestamp"), utcNow()}});
}

void HubClient::persistCriticalCursor() {
    QSettings settings;
    settings.setValue(QStringLiteral("ipc/critical_cursor/%1").arg(expectedConfigHash_),
                      lastCriticalEventId_);
    settings.setValue(QStringLiteral("ipc/critical_epoch/%1").arg(expectedConfigHash_),
                      lastCriticalEventEpoch_);
}

void HubClient::write(const QJsonObject &message) {
    if (socket_.state() != QLocalSocket::ConnectedState) {
        emit protocolError(QStringLiteral("Agent 未连接，命令未发送"));
        return;
    }
    socket_.write(FrameCodec::encode(message));
}

void HubClient::refresh(const QString &moduleId) {
    write(QJsonObject{{QStringLiteral("schema_version"), 1},
                      {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
                      {QStringLiteral("type"), QStringLiteral("refresh")},
                      {QStringLiteral("module_id"), moduleId},
                      {QStringLiteral("timestamp"), utcNow()}});
}

void HubClient::sendCommand(const QString &moduleId, const QString &action,
                            const QJsonObject &arguments, int deadlineMs,
                            qint64 expectedRevision, const QString &reason) {
    if (!controlReady_) {
        emit protocolError(QStringLiteral("UI 未绑定就绪的 Agent/配置，命令未发送"));
        return;
    }
    QJsonObject command = commandEnvelope(moduleId, action, arguments, deadlineMs,
                                          expectedRevision, reason);
    if (!approvalActions_.value(moduleId).contains(action)) {
        write(command);
        return;
    }
    const QString requestId = randomId();
    pendingApprovals_.insert(requestId, command);
    QJsonObject approval = command;
    approval.remove(QStringLiteral("command_id"));
    approval.insert(QStringLiteral("type"), QStringLiteral("approval_request"));
    approval.insert(QStringLiteral("request_id"), requestId);
    write(approval);
}

QJsonObject HubClient::commandEnvelope(const QString &moduleId, const QString &action,
                                       const QJsonObject &arguments, int deadlineMs,
                                       qint64 expectedRevision, const QString &reason) const {
    return {{QStringLiteral("schema_version"), 1},
            {QStringLiteral("protocol"), QStringLiteral("module.control.v1")},
            {QStringLiteral("type"), QStringLiteral("command")},
            {QStringLiteral("command_id"), randomId()},
            {QStringLiteral("module_id"), moduleId},
            {QStringLiteral("action"), action},
            {QStringLiteral("arguments"), arguments},
            {QStringLiteral("expected_revision"), expectedRevision},
            {QStringLiteral("requested_by"), qEnvironmentVariable("USER", QStringLiteral("local-user"))},
            {QStringLiteral("reason"), reason.left(240)},
            {QStringLiteral("deadline_ms"), deadlineMs},
            {QStringLiteral("requested_at"), utcNow()},
            {QStringLiteral("agent_instance_id"), agentInstanceId_},
            {QStringLiteral("config_sha256"), expectedConfigHash_}};
}

void HubClient::queueSnapshot(const QJsonObject &message) {
    pendingSnapshots_.insert(message.value(QStringLiteral("module_id")).toString(), message);
    if (!deliveryTimer_.isActive()) deliveryTimer_.start();
}

void HubClient::queueEvent(const QJsonObject &source) {
    QJsonObject message = source;
    const QString kind = message.value(QStringLiteral("event_kind")).toString();
    static const QSet<QString> latestOnly{
        QStringLiteral("premium.summary"), QStringLiteral("premium.detail"),
        QStringLiteral("premium.l1_status"), QStringLiteral("premium.raw_snapshot"),
        QStringLiteral("webull.book"),
        QStringLiteral("webull.clients"), QStringLiteral("redemption.snapshot"),
        QStringLiteral("redemption.status")};
    bool urgent = false;
    if (latestOnly.contains(kind)) {
        const QString symbol = message.value(QStringLiteral("payload")).toObject()
                                   .value(QStringLiteral("symbol")).toString();
        coalescedEvents_.insert(kind + u'|' + symbol, message);
    } else if (kind == QStringLiteral("redemption.change")
               || kind.contains(QStringLiteral("signal"), Qt::CaseInsensitive)
               || kind.contains(QStringLiteral("alert"), Qt::CaseInsensitive)
               || kind.contains(QStringLiteral("error"), Qt::CaseInsensitive)
               || kind.contains(QStringLiteral("order"), Qt::CaseInsensitive)) {
        urgent = true;
        // Critical events are already durable in the Agent. Keep this GUI queue
        // bounded; if the event loop cannot keep up, reconnect from the last
        // rendered cursor instead of doing synchronous file I/O on the IPC thread.
        const qint64 auditEventId = message.value(QStringLiteral("audit_event_id")).toInteger();
        if (auditEventId > 0
            && (auditEventId <= lastCriticalEventId_
                || pendingCriticalEventIds_.contains(auditEventId))) {
            return;
        }
        message.insert(QStringLiteral("audit_epoch"), auditEpoch_);
        message.insert(QStringLiteral("client_delivery_generation"),
                       static_cast<qint64>(connectionGeneration_));
        constexpr int MaximumCriticalInMemory = 2000;
        if (criticalEvents_.size() >= MaximumCriticalInMemory) {
            emit protocolError(QStringLiteral(
                "关键事件投递队列达到 2000 条，已断开并将从 Agent 持久游标重放"));
            socket_.abort();
            return;
        }
        criticalEvents_.enqueue(message);
        if (auditEventId > 0) {
            pendingCriticalEventIds_.insert(auditEventId);
            highestCriticalQueuedId_ = qMax(highestCriticalQueuedId_, auditEventId);
        }
    } else {
        if (queuedEvents_.size() >= 1000) {
            queuedEvents_.dequeue();
            overflowWarningQueued_ = true;
        }
        queuedEvents_.enqueue(message);
    }
    if (urgent) deliveryTimer_.start(0);
    else if (!deliveryTimer_.isActive()) deliveryTimer_.start();
}

void HubClient::flushDelivery() {
    const auto snapshots = std::exchange(pendingSnapshots_, {});
    for (const auto &message : snapshots) emit snapshotReceived(message);
    const auto latestEvents = std::exchange(coalescedEvents_, {});
    for (const auto &message : latestEvents) emit eventReceived(message);
    int criticalDelivered = 0;
    while (!criticalEvents_.isEmpty() && criticalDelivered < 500) {
        emit eventReceived(criticalEvents_.dequeue());
        ++criticalDelivered;
    }
    int delivered = 0;
    while (!queuedEvents_.isEmpty() && delivered < 100) {
        emit eventReceived(queuedEvents_.dequeue());
        ++delivered;
    }
    if (overflowWarningQueued_) {
        overflowWarningQueued_ = false;
        emit protocolError(QStringLiteral("普通日志突发超过 1000 条：已丢弃最旧普通日志；命令结果不入此队列，关键业务事件由 Agent 持久化重放"));
    }
    if (!criticalEvents_.isEmpty() || !queuedEvents_.isEmpty()
        || !pendingSnapshots_.isEmpty() || !coalescedEvents_.isEmpty()) {
        deliveryTimer_.start(25);
    }
}

} // namespace hub
