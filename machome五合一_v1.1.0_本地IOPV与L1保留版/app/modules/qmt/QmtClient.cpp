#include "modules/qmt/QmtClient.h"
#include "modules/qmt/QmtCanonicalJson.h"

#include <QCryptographicHash>
#include <QDateTime>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QTcpSocket>
#include <QTimer>
#include <algorithm>
#include <cmath>
#include <limits>

namespace machome::qmt {
namespace {

bool protocolInteger(const QJsonValue &value, qint64 *result) {
    constexpr double maximumExactJsonInteger = 9'007'199'254'740'991.0;
    if (value.isDouble()) {
        const double number = value.toDouble();
        if (std::isfinite(number) && std::floor(number) == number
            && number >= 0 && number <= maximumExactJsonInteger) {
            *result = static_cast<qint64>(number);
            return true;
        }
    }
    if (value.isString()) {
        bool ok = false;
        const qint64 number = value.toString().trimmed().toLongLong(&ok);
        if (ok && number >= 0) {
            *result = number;
            return true;
        }
    }
    return false;
}

struct TimeKey {
    bool valid = false;
    int seconds = 0;
    int fraction = 0;
};

TimeKey timeKey(const QJsonValue &value) {
    const QString text = value.toVariant().toString().trimmed();
    static const QRegularExpression clock(
        QStringLiteral("(\\d{1,2}):(\\d{2}):(\\d{2})(?:[.,](\\d+))?"));
    const auto match = clock.match(text);
    QString hour;
    QString minute;
    QString second;
    QString fraction;
    if (match.hasMatch()) {
        hour = match.captured(1);
        minute = match.captured(2);
        second = match.captured(3);
        fraction = match.captured(4);
    } else {
        QString digits;
        for (const QChar character : text) if (character.isDigit()) digits.append(character);
        if (digits.size() >= 14) {
            hour = digits.mid(digits.size() - 6, 2);
            minute = digits.mid(digits.size() - 4, 2);
            second = digits.right(2);
        } else if (digits.size() >= 6) {
            hour = digits.mid(0, 2);
            minute = digits.mid(2, 2);
            second = digits.mid(4, 2);
            fraction = digits.mid(6);
        } else {
            return {};
        }
    }
    bool okHour = false;
    bool okMinute = false;
    bool okSecond = false;
    const int h = hour.toInt(&okHour);
    const int m = minute.toInt(&okMinute);
    const int s = second.toInt(&okSecond);
    if (!okHour || !okMinute || !okSecond || h > 23 || m > 59 || s > 59) return {};
    QString fractionDigits;
    for (const QChar character : fraction) if (character.isDigit()) fractionDigits.append(character);
    fractionDigits = fractionDigits.left(6).leftJustified(6, QLatin1Char('0'));
    return {true, h * 3600 + m * 60 + s, fractionDigits.isEmpty() ? 0 : fractionDigits.toInt()};
}

bool orderLess(const QJsonObject &left, const QJsonObject &right) {
    const auto lt = timeKey(left.value(QStringLiteral("time")));
    const auto rt = timeKey(right.value(QStringLiteral("time")));
    if (lt.valid != rt.valid) return lt.valid < rt.valid;
    if (lt.seconds != rt.seconds) return lt.seconds < rt.seconds;
    if (lt.fraction != rt.fraction) return lt.fraction < rt.fraction;
    const QString leftId = left.value(QStringLiteral("order_id")).toVariant().toString().trimmed();
    const QString rightId = right.value(QStringLiteral("order_id")).toVariant().toString().trimmed();
    static const QRegularExpression digits(QStringLiteral("^[0-9]+$"));
    const bool leftNumeric = digits.match(leftId).hasMatch();
    const bool rightNumeric = digits.match(rightId).hasMatch();
    if (leftNumeric != rightNumeric) return leftNumeric < rightNumeric;
    if (leftNumeric) {
        bool leftOk = false;
        bool rightOk = false;
        const qulonglong leftNumber = leftId.toULongLong(&leftOk);
        const qulonglong rightNumber = rightId.toULongLong(&rightOk);
        if (leftOk && rightOk && leftNumber != rightNumber) return leftNumber < rightNumber;
    }
    if (leftId != rightId) return leftId < rightId;
    return left.value(QStringLiteral("sort_seq")).toVariant().toLongLong()
        < right.value(QStringLiteral("sort_seq")).toVariant().toLongLong();
}

} // namespace

QmtClient::QmtClient(QmtClientConfig config, QObject *parent)
    : QObject(parent), config_(std::move(config)) {
    socket_ = new QTcpSocket(this);
    reconnectTimer_ = new QTimer(this);
    heartbeatTimer_ = new QTimer(this);
    throttleTimer_ = new QTimer(this);
    reconnectTimer_->setSingleShot(true);
    reconnectTimer_->setInterval(std::clamp(config_.reconnectIntervalMs, 500, 60'000));
    heartbeatTimer_->setInterval(std::clamp(config_.heartbeatIntervalMs, 1000, 60'000));
    throttleTimer_->setSingleShot(true);

    connect(reconnectTimer_, &QTimer::timeout, this, &QmtClient::openSocket);
    connect(heartbeatTimer_, &QTimer::timeout, this, [this] {
        if (socket_->state() != QAbstractSocket::ConnectedState) return;
        if (lastMessageClock_.isValid() && lastMessageClock_.elapsed() > config_.silenceTimeoutMs) {
            lastError_ = QStringLiteral("%1 后端 %2 ms 无响应，正在重连")
                             .arg(config_.id).arg(lastMessageClock_.elapsed());
            state_ = QStringLiteral("reconnecting");
            socket_->abort();
            publish();
            scheduleReconnect();
            return;
        }
        sendPing();
    });
    connect(throttleTimer_, &QTimer::timeout, this, &QmtClient::publish);
    connect(socket_, &QTcpSocket::connected, this, [this] {
        buffer_.clear();
        resetSync();
        state_ = QStringLiteral("syncing");
        lastError_.clear();
        lastMessageClock_.restart();
        heartbeatTimer_->start();
        sendPing();
        requestSync();
        publish();
    });
    connect(socket_, &QTcpSocket::disconnected, this, [this] {
        heartbeatTimer_->stop();
        buffer_.clear();
        resetSync();
        failPendingOrders(wantsConnection_
                              ? QStringLiteral("QMT 连接中断，无法确认指令结果")
                              : QStringLiteral("QMT 连接已断开，指令未完成"));
        state_ = wantsConnection_ ? QStringLiteral("reconnecting") : QStringLiteral("disconnected");
        publish();
        scheduleReconnect();
    });
    connect(socket_, &QTcpSocket::readyRead, this, [this] {
        buffer_.append(socket_->readAll());
        if (buffer_.size() > config_.maximumLineBytes * 2) {
            lastError_ = QStringLiteral("QMT 接收缓冲区超过安全上限");
            socket_->abort();
            publish();
            return;
        }
        consumeLines();
    });
    connect(socket_, &QTcpSocket::errorOccurred, this, [this](QAbstractSocket::SocketError) {
        lastError_ = socket_->errorString().left(500);
        if (wantsConnection_) state_ = QStringLiteral("reconnecting");
        publish();
    });
}

QmtClient::~QmtClient() {
    wantsConnection_ = false;
    if (socket_) socket_->abort();
}

bool QmtClient::isReady() const {
    return socket_->state() == QAbstractSocket::ConnectedState && state_ == QStringLiteral("ready");
}

qint64 QmtClient::throttleRemainingMs() const {
    if (!orderThrottleClock_.isValid()) return 0;
    return std::max<qint64>(0, config_.orderThrottleMs - orderThrottleClock_.elapsed());
}

QJsonObject QmtClient::snapshot() const {
    QJsonArray orders = sortedOrders(QJsonArray::fromVariantList([this] {
        QVariantList result;
        result.reserve(orders_.size());
        for (const auto &order : orders_) result.append(order.toVariantMap());
        return result;
    }()));
    return {{QStringLiteral("id"), config_.id},
            {QStringLiteral("host"), config_.host},
            {QStringLiteral("port"), config_.port},
            {QStringLiteral("endpoint"), QStringLiteral("%1:%2").arg(config_.host).arg(config_.port)},
            {QStringLiteral("want_connection"), wantsConnection_},
            {QStringLiteral("connection_state"), state_},
            {QStringLiteral("ready"), isReady()},
            {QStringLiteral("welcome_received"), welcomeReceived_},
            {QStringLiteral("orders_synced"), ordersSynced_},
            {QStringLiteral("positions_synced"), positionsSynced_},
            {QStringLiteral("available_cash"), availableCash_},
            {QStringLiteral("positions"), positions_},
            {QStringLiteral("orders"), orders},
            {QStringLiteral("last_result"), lastResult_},
            {QStringLiteral("last_error"), lastError_},
            {QStringLiteral("throttle_remaining_ms"), throttleRemainingMs()}};
}

void QmtClient::publish() {
    emit snapshotChanged(snapshot());
}

void QmtClient::setState(const QString &state, const QString &error) {
    state_ = state;
    if (!error.isNull()) lastError_ = error.left(500);
    publish();
}

void QmtClient::connectBackend() {
    wantsConnection_ = true;
    reconnectTimer_->stop();
    if (socket_->state() == QAbstractSocket::ConnectedState) {
        requestSync();
        return;
    }
    openSocket();
}

void QmtClient::openSocket() {
    if (!wantsConnection_ || socket_->state() != QAbstractSocket::UnconnectedState) return;
    if (config_.host.trimmed().isEmpty() || config_.port == 0) {
        setState(QStringLiteral("disconnected"), QStringLiteral("QMT endpoint 配置无效"));
        return;
    }
    state_ = QStringLiteral("connecting");
    publish();
    socket_->connectToHost(config_.host, config_.port);
}

void QmtClient::disconnectBackend() {
    wantsConnection_ = false;
    reconnectTimer_->stop();
    heartbeatTimer_->stop();
    buffer_.clear();
    resetSync();
    failPendingOrders(QStringLiteral("QMT 连接由用户断开，指令未完成"));
    socket_->abort();
    lastError_.clear();
    setState(QStringLiteral("disconnected"), QString());
}

void QmtClient::scheduleReconnect() {
    if (wantsConnection_ && !reconnectTimer_->isActive()
        && socket_->state() == QAbstractSocket::UnconnectedState) {
        reconnectTimer_->start();
    }
}

bool QmtClient::sendJson(const QJsonObject &object) {
    if (socket_->state() != QAbstractSocket::ConnectedState) return false;
    QByteArray wire = QJsonDocument(object).toJson(QJsonDocument::Compact);
    wire.append('\n');
    return socket_->write(wire) == wire.size();
}

void QmtClient::sendPing() {
    sendJson({{QStringLiteral("type"), QStringLiteral("ping")}});
}

void QmtClient::requestSync() {
    if (socket_->state() != QAbstractSocket::ConnectedState) return;
    ordersSynced_ = false;
    positionsSynced_ = false;
    state_ = QStringLiteral("syncing");
    sendJson({{QStringLiteral("type"), QStringLiteral("query_status")}});
    sendJson({{QStringLiteral("type"), QStringLiteral("sync_request")},
              {QStringLiteral("target"), QStringLiteral("all")}});
    publish();
}

void QmtClient::reconcilePendingOrders() {
    pendingOrderCommands_.clear();
    // Human reconciliation closes only the old Hub command. Never retain the
    // previous connection's authoritative-generation flags: another order is
    // allowed only after fresh full positions + orders snapshots.
    resetSync();
    if (socket_->state() == QAbstractSocket::ConnectedState) {
        state_ = QStringLiteral("syncing");
        sendJson({{QStringLiteral("type"), QStringLiteral("query_status")}});
        sendJson({{QStringLiteral("type"), QStringLiteral("sync_request")},
                  {QStringLiteral("target"), QStringLiteral("all")}});
    }
    lastResult_.insert(QStringLiteral("pending"), false);
    lastResult_.insert(QStringLiteral("reconciled_at"),
                       QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    publish();
}

void QmtClient::consumeLines() {
    while (true) {
        const qsizetype newline = buffer_.indexOf('\n');
        if (newline < 0) return;
        QByteArray line = buffer_.left(newline).trimmed();
        buffer_.remove(0, newline + 1);
        if (line.isEmpty()) continue;
        if (line.size() > config_.maximumLineBytes) {
            lastError_ = QStringLiteral("QMT 单行消息超过安全上限");
            socket_->abort();
            publish();
            return;
        }
        QJsonParseError parseError;
        const auto document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            lastError_ = QStringLiteral("QMT 后端返回了无效 JSON");
            publish();
            continue;
        }
        lastMessageClock_.restart();
        handleMessage(document.object());
    }
}

void QmtClient::resetSync() {
    welcomeReceived_ = false;
    positionsSynced_ = false;
    ordersSynced_ = false;
    positionsSnapshotId_ = -1;
    ordersSnapshotId_ = -1;
    positionsSequence_ = -1;
    ordersSequence_ = -1;
}

void QmtClient::maybeReady() {
    if (socket_->state() == QAbstractSocket::ConnectedState && welcomeReceived_
        && positionsSynced_ && ordersSynced_) {
        state_ = QStringLiteral("ready");
        lastError_.clear();
    }
}

void QmtClient::rejectSync(const QString &stream, const QString &reason) {
    lastError_ = reason.left(500);
    state_ = QStringLiteral("syncing");
    if (stream == QStringLiteral("orders")) ordersSynced_ = false;
    else positionsSynced_ = false;
    sendJson({{QStringLiteral("type"), QStringLiteral("sync_request")},
              {QStringLiteral("target"), stream}});
    emit eventOccurred(QStringLiteral("sync_rejected"),
                       {{QStringLiteral("backend"), config_.id},
                        {QStringLiteral("stream"), stream},
                        {QStringLiteral("reason"), lastError_}});
    publish();
}

void QmtClient::failPendingOrders(const QString &reason) {
    for (auto it = pendingOrderCommands_.begin(); it != pendingOrderCommands_.end(); ++it) {
        if (it.value().uncertaintyReported) continue;
        it.value().uncertaintyReported = true;
        emit orderFinished(it.value().hubCommandId, false, reason,
                           {{QStringLiteral("backend"), config_.id},
                            {QStringLiteral("client_order_id"), it.key()},
                            {QStringLiteral("delivery_state"), QStringLiteral("unknown")},
                            {QStringLiteral("outcome_uncertain"), true},
                            {QStringLiteral("reconciliation_required"), true},
                            {QStringLiteral("tracking_late_result"), true}});
    }
}

void QmtClient::confirmPendingOrders() {
    if (pendingOrderCommands_.isEmpty()) return;
    QHash<QString, QJsonObject> authoritative;
    for (auto it = orders_.cbegin(); it != orders_.cend(); ++it) {
        const QString clientOrderId = it.value().value(QStringLiteral("client_order_id"))
                                          .toString().trimmed();
        if (!clientOrderId.isEmpty()) authoritative.insert(clientOrderId, it.value());
    }
    const auto clientOrderIds = pendingOrderCommands_.keys();
    for (const QString &clientOrderId : clientOrderIds) {
        if (!authoritative.contains(clientOrderId)) continue;
        const PendingOrder pending = pendingOrderCommands_.take(clientOrderId);
        QJsonObject details = pending.acknowledgement;
        details.insert(QStringLiteral("backend"), config_.id);
        details.insert(QStringLiteral("client_order_id"), clientOrderId);
        details.insert(QStringLiteral("delivery_state"), QStringLiteral("confirmed_by_orders_sync"));
        details.insert(QStringLiteral("authoritative_order"), authoritative.value(clientOrderId));
        emit orderFinished(pending.hubCommandId, true,
                           QStringLiteral("ETF 指令已在权威委托同步中确认"), details);
    }
}

bool QmtClient::validateFullMeta(contract::SnapshotKind kind,
                                 const QJsonObject &message, const QJsonArray &data,
                                 const QJsonObject &extra, qint64 *snapshotId,
                                 qint64 *sequence) const {
    qint64 id = -1;
    qint64 seq = -1;
    qint64 count = -1;
    const QByteArray checksum = message.value(QStringLiteral("checksum")).toString().trimmed().toLower().toUtf8();
    QString checksumError;
    const QByteArray actualChecksum = contract::snapshotChecksum(kind, data, extra, &checksumError);
    if (!contract::strictNonNegativeInteger(message.value(QStringLiteral("snapshot_id")), &id)
        || !protocolInteger(message.value(QStringLiteral("seq")), &seq)
        || !protocolInteger(message.value(QStringLiteral("count")), &count)
        || count != data.size() || checksum.isEmpty()
        || actualChecksum.isEmpty() || checksum != actualChecksum) {
        return false;
    }
    *snapshotId = id;
    *sequence = seq;
    return true;
}

QJsonArray QmtClient::sortedPositions(const QJsonArray &input, bool *ok) {
    QList<QJsonObject> values;
    for (const auto &value : input) {
        if (!value.isObject()) {
            if (ok) *ok = false;
            return {};
        }
        values.append(value.toObject());
    }
    std::sort(values.begin(), values.end(), [](const auto &left, const auto &right) {
        return left.value(QStringLiteral("code")).toVariant().toString().trimmed()
            < right.value(QStringLiteral("code")).toVariant().toString().trimmed();
    });
    QJsonArray result;
    for (const auto &value : values) result.append(value);
    if (ok) *ok = true;
    return result;
}

QJsonArray QmtClient::sortedOrders(const QJsonArray &input, bool *ok) {
    QList<QJsonObject> values;
    for (const auto &value : input) {
        if (!value.isObject()) {
            if (ok) *ok = false;
            return {};
        }
        values.append(value.toObject());
    }
    std::sort(values.begin(), values.end(), orderLess);
    std::reverse(values.begin(), values.end());
    QJsonArray result;
    for (const auto &value : values) result.append(value);
    if (ok) *ok = true;
    return result;
}

void QmtClient::handlePositions(const QJsonObject &message) {
    if (message.value(QStringLiteral("sync_mode")).toString() != QStringLiteral("full")) {
        rejectSync(QStringLiteral("positions"),
                   message.value(QStringLiteral("error")).toString(QStringLiteral("QMT 持仓同步模式无效")));
        return;
    }
    if (!message.value(QStringLiteral("data")).isArray()
        || !message.value(QStringLiteral("available_cash")).isDouble()) {
        rejectSync(QStringLiteral("positions"), QStringLiteral("QMT 持仓或可用资金格式无效"));
        return;
    }
    bool sortedOk = false;
    const QJsonArray data = sortedPositions(message.value(QStringLiteral("data")).toArray(), &sortedOk);
    const QJsonObject extra{{QStringLiteral("available_cash"), message.value(QStringLiteral("available_cash"))}};
    qint64 snapshotId = -1;
    qint64 sequence = -1;
    if (!sortedOk || !validateFullMeta(contract::SnapshotKind::Positions,
                                       message, data, extra, &snapshotId, &sequence)) {
        rejectSync(QStringLiteral("positions"), QStringLiteral("QMT 持仓全量校验失败"));
        return;
    }
    positions_ = data;
    availableCash_ = message.value(QStringLiteral("available_cash"));
    positionsSnapshotId_ = snapshotId;
    positionsSequence_ = sequence;
    positionsSynced_ = true;
    maybeReady();
    publish();
}

void QmtClient::handleOrders(const QJsonObject &message) {
    const QString mode = message.value(QStringLiteral("sync_mode")).toString().toLower();
    if (mode == QStringLiteral("error")) {
        rejectSync(QStringLiteral("orders"),
                   message.value(QStringLiteral("error")).toString(QStringLiteral("QMT 委托同步失败")));
        return;
    }
    if (mode == QStringLiteral("full")) {
        if (!message.value(QStringLiteral("data")).isArray()) {
            rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托数据格式无效"));
            return;
        }
        bool sortedOk = false;
        const QJsonArray data = sortedOrders(message.value(QStringLiteral("data")).toArray(), &sortedOk);
        qint64 snapshotId = -1;
        qint64 sequence = -1;
        if (!sortedOk || !validateFullMeta(contract::SnapshotKind::Orders,
                                           message, data, {}, &snapshotId, &sequence)) {
            rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托全量校验失败"));
            return;
        }
        QHash<QString, QJsonObject> next;
        for (const auto &value : data) {
            const auto order = value.toObject();
            const QString id = order.value(QStringLiteral("order_id")).toVariant().toString().trimmed();
            if (id.isEmpty() || next.contains(id)) {
                rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托号缺失或重复"));
                return;
            }
            next.insert(id, order);
        }
        orders_ = next;
        ordersSnapshotId_ = snapshotId;
        ordersSequence_ = sequence;
        ordersSynced_ = true;
        maybeReady();
        confirmPendingOrders();
        publish();
        return;
    }
    if (mode != QStringLiteral("delta")) {
        rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托同步模式无效"));
        return;
    }
    qint64 sequence = -1;
    qint64 snapshotId = -1;
    if (!contract::strictNonNegativeInteger(message.value(QStringLiteral("snapshot_id")),
                                            &snapshotId)
        || !protocolInteger(message.value(QStringLiteral("seq")), &sequence) || !ordersSynced_
        || ordersSnapshotId_ < 0
        || snapshotId != ordersSnapshotId_ || sequence != ordersSequence_ + 1
        || !message.value(QStringLiteral("upserts")).isArray()
        || !message.value(QStringLiteral("remove_ids")).isArray()) {
        rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托增量缺少基础快照或序号不连续"));
        return;
    }
    QHash<QString, QJsonObject> next = orders_;
    for (const auto &value : message.value(QStringLiteral("upserts")).toArray()) {
        if (!value.isObject()) {
            rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托增量格式无效"));
            return;
        }
        const auto order = value.toObject();
        const QString id = order.value(QStringLiteral("order_id")).toVariant().toString().trimmed();
        if (id.isEmpty()) {
            rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托增量缺少委托号"));
            return;
        }
        next.insert(id, order);
    }
    for (const auto &value : message.value(QStringLiteral("remove_ids")).toArray())
        next.remove(value.toVariant().toString());
    QJsonArray candidate;
    for (const auto &order : next) candidate.append(order);
    const QJsonArray sorted = sortedOrders(candidate);
    qint64 count = -1;
    const QByteArray checksum = message.value(QStringLiteral("checksum")).toString().trimmed().toLower().toUtf8();
    QString checksumError;
    const QByteArray actualChecksum = contract::snapshotChecksum(
        contract::SnapshotKind::Orders, sorted, {}, &checksumError);
    if (!protocolInteger(message.value(QStringLiteral("count")), &count)
        || count != sorted.size() || actualChecksum.isEmpty() || checksum != actualChecksum) {
        rejectSync(QStringLiteral("orders"), QStringLiteral("QMT 委托增量数量或校验和不一致"));
        return;
    }
    orders_ = next;
    ordersSequence_ = sequence;
    confirmPendingOrders();
    publish();
}

void QmtClient::handleMessage(const QJsonObject &message) {
    const QString type = message.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("welcome")) {
        if (message.value(QStringLiteral("push_sync")).toBool(false)) {
            welcomeReceived_ = true;
            maybeReady();
        } else {
            lastError_ = QStringLiteral("QMT 后端不支持 push_sync 协议");
        }
    } else if (type == QStringLiteral("positions_data")) {
        handlePositions(message);
        return;
    } else if (type == QStringLiteral("orders_data")) {
        handleOrders(message);
        return;
    } else if (type == QStringLiteral("etf_order_result")) {
        lastResult_ = message;
        const QString clientOrderId = message.value(QStringLiteral("client_order_id")).toString();
        auto pending = pendingOrderCommands_.find(clientOrderId);
        if (clientOrderId.isEmpty() || !message.value(QStringLiteral("success")).isBool()) {
            lastError_ = QStringLiteral("QMT 返回了缺少布尔 success/client_order_id 的非法指令回执");
            QJsonObject malformed = message;
            malformed.insert(QStringLiteral("reason"), lastError_);
            emit eventOccurred(QStringLiteral("protocol_error"), malformed);
            if (pending != pendingOrderCommands_.end() && !pending.value().uncertaintyReported) {
                pending.value().uncertaintyReported = true;
                emit orderFinished(
                    pending.value().hubCommandId, false, lastError_,
                    {{QStringLiteral("backend"), config_.id},
                     {QStringLiteral("client_order_id"), clientOrderId},
                     {QStringLiteral("delivery_state"), QStringLiteral("malformed_ack_unknown")},
                     {QStringLiteral("outcome_uncertain"), true},
                     {QStringLiteral("reconciliation_required"), true},
                     {QStringLiteral("tracking_late_result"), true}});
            }
            publish();
            return;
        }
        const bool ok = message.value(QStringLiteral("success")).toBool(false);
        const QString text = ok
            ? message.value(QStringLiteral("message")).toString(QStringLiteral("ETF 指令已确认"))
            : message.value(QStringLiteral("error")).toString(QStringLiteral("ETF 指令被后端拒绝"));
        lastError_ = ok ? QString{} : text;
        emit eventOccurred(QStringLiteral("order_result"), message);
        if (pending != pendingOrderCommands_.end()) {
            if (!ok) {
                const QString hubCommandId = pending.value().hubCommandId;
                pendingOrderCommands_.erase(pending);
                QJsonObject details = message;
                details.insert(QStringLiteral("delivery_state"), QStringLiteral("rejected_by_backend"));
                emit orderFinished(hubCommandId, false, text, details);
            } else {
                pending.value().acknowledgement = message;
                emit eventOccurred(QStringLiteral("order_acknowledged"), message);
                // The production backend writes client_order_id into the QMT
                // userOrderId/remark and echoes it in orders_data. Keep the
                // command non-terminal until that authoritative stream confirms it.
                requestSync();
            }
        }
    } else if (type == QStringLiteral("error")) {
        lastError_ = message.value(QStringLiteral("detail")).toString(
            message.value(QStringLiteral("error")).toString(QStringLiteral("QMT 后端错误")));
        emit eventOccurred(QStringLiteral("error"), message);
    } else if (type != QStringLiteral("pong") && type != QStringLiteral("status")) {
        emit eventOccurred(QStringLiteral("unknown_message"), message);
    }
    publish();
}

QString QmtClient::normalizeEtfCode(const QString &symbol) {
    QString clean = symbol.trimmed().toUpper();
    static const QRegularExpression full(QStringLiteral("^([0-9]{6})\\.(SH|SZ)$"));
    const auto match = full.match(clean);
    if (match.hasMatch()) return match.captured(1) + u'.' + match.captured(2);
    static const QRegularExpression digits(QStringLiteral("^[0-9]{6}$"));
    if (!digits.match(clean).hasMatch()) return {};
    const QString exchange = clean.startsWith(u'5') || clean.startsWith(u'6') || clean.startsWith(u'9')
        ? QStringLiteral("SH") : QStringLiteral("SZ");
    return clean + u'.' + exchange;
}

bool QmtClient::submitEtfOrder(const QString &symbol, const QString &action,
                               const QString &hubCommandId, QString *error) {
    if (!isReady()) {
        if (error) *error = QStringLiteral("%1 尚未完成 welcome、委托和持仓全量同步").arg(config_.id);
        return false;
    }
    const qint64 remaining = throttleRemainingMs();
    if (remaining > 0) {
        if (error) *error = QStringLiteral("%1 操作过快，请等待 %2 秒")
                                .arg(config_.id).arg((remaining + 999) / 1000);
        return false;
    }
    const QString code = normalizeEtfCode(symbol);
    const QString normalizedAction = action.trimmed().toUpper();
    if (code.isEmpty() || (normalizedAction != QStringLiteral("PURCHASE")
                           && normalizedAction != QStringLiteral("REDEEM"))) {
        if (error) *error = QStringLiteral("ETF 标的或申赎动作无效");
        return false;
    }
    const QString direction = normalizedAction == QStringLiteral("PURCHASE")
                                  ? QStringLiteral("P") : QStringLiteral("R");
    const QByteArray idDigest = QCryptographicHash::hash(
        QStringLiteral("%1|%2|%3|%4").arg(config_.id, direction, code, hubCommandId).toUtf8(),
        QCryptographicHash::Sha256).toHex().left(24);
    const QString clientOrderId = QStringLiteral("ETF-%1-%2-%3-%4")
        .arg(config_.id, direction, code.left(6), QString::fromLatin1(idDigest));
    for (auto it = orders_.cbegin(); it != orders_.cend(); ++it) {
        if (it.value().value(QStringLiteral("client_order_id")).toString() == clientOrderId) {
            emit orderFinished(hubCommandId, true,
                               QStringLiteral("相同 client_order_id 已存在于权威委托同步，未重复发送"),
                               {{QStringLiteral("backend"), config_.id},
                                {QStringLiteral("client_order_id"), clientOrderId},
                                {QStringLiteral("delivery_state"), QStringLiteral("already_confirmed")},
                                {QStringLiteral("authoritative_order"), it.value()}});
            return true;
        }
    }
    const QJsonObject order{{QStringLiteral("type"), QStringLiteral("etf_order")},
                            {QStringLiteral("action"), normalizedAction},
                            {QStringLiteral("code"), code},
                            {QStringLiteral("qty"), 1},
                            {QStringLiteral("client_order_id"), clientOrderId}};
    if (!sendJson(order)) {
        if (error) *error = QStringLiteral("QMT 指令写入失败：%1").arg(socket_->errorString());
        return false;
    }
    pendingOrderCommands_.insert(clientOrderId, PendingOrder{hubCommandId, {}, false});
    orderThrottleClock_.restart();
    throttleTimer_->start(config_.orderThrottleMs + 25);
    lastResult_ = {{QStringLiteral("pending"), true},
                   {QStringLiteral("action"), normalizedAction},
                   {QStringLiteral("code"), code},
                   {QStringLiteral("qty"), 1},
                   {QStringLiteral("client_order_id"), clientOrderId},
                   {QStringLiteral("message"), QStringLiteral("指令已发送，等待 QMT 后端确认")}};
    emit eventOccurred(QStringLiteral("order_sent"), lastResult_);
    publish();
    return true;
}

} // namespace machome::qmt
