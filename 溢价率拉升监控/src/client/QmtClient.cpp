#include "client/QmtClient.h"

#include "common/MarketTypes.h"

#include <QDateTime>
#include <QJsonDocument>
#include <QUuid>

namespace premium {

QmtClient::QmtClient(Profile profile, bool tradingEnabled, QObject *parent)
    : QObject(parent), profile_(std::move(profile)), tradingEnabled_(tradingEnabled)
{
    reconnectTimer_.setInterval(3000);
    reconnectTimer_.setSingleShot(true);
    connect(&reconnectTimer_, &QTimer::timeout, this, &QmtClient::connectBackend);
    connect(&socket_, &QTcpSocket::connected, this, [this] {
        reconnectTimer_.stop();
        Q_EMIT stateChanged();
        sendRequest({{"type", "sync_request"}, {"target", "all"}});
    });
    connect(&socket_, &QTcpSocket::disconnected, this, [this] {
        Q_EMIT stateChanged();
        if (!reconnectTimer_.isActive()) reconnectTimer_.start();
    });
    connect(&socket_, &QTcpSocket::readyRead, this, &QmtClient::readLines);
    connect(&socket_, &QTcpSocket::errorOccurred, this, [this](QAbstractSocket::SocketError) {
        Q_EMIT notice(QStringLiteral("%1: %2").arg(profile_.name, socket_.errorString()), true);
        Q_EMIT stateChanged();
        if (!reconnectTimer_.isActive()) reconnectTimer_.start();
    });
}

QmtClient::~QmtClient()
{
    // QTcpSocket emits disconnected while its destructor runs.  At that point
    // members declared after socket_ (notably reconnectTimer_) have already
    // been destroyed, so leaving these callbacks connected is a use-after-free.
    reconnectTimer_.stop();
    QObject::disconnect(&reconnectTimer_, nullptr, this, nullptr);
    QObject::disconnect(&socket_, nullptr, this, nullptr);
    socket_.abort();
}

void QmtClient::connectBackend()
{
    if (socket_.state() == QAbstractSocket::UnconnectedState) socket_.connectToHost(profile_.host, profile_.port);
}

bool QmtClient::isConnected() const { return socket_.state() == QAbstractSocket::ConnectedState; }
QString QmtClient::profileName() const { return profile_.name; }

qint64 QmtClient::availableQuantity(const QString &symbol) const { return available_.value(normalizeSymbol(symbol), 0); }

QJsonArray QmtClient::ordersFor(const QString &symbol) const
{
    QJsonArray result;
    const QString wanted = normalizeSymbol(symbol);
    for (const QJsonValue &value : orders_) if (normalizeSymbol(objectSymbol(value.toObject())) == wanted) result.append(value);
    return result;
}

QString QmtClient::newClientOrderId(const QString &symbol, const QString &action) const
{
    return QStringLiteral("etfmon-%1-%2-%3").arg(symbol.left(6), action, QUuid::createUuid().toString(QUuid::Id128));
}

bool QmtClient::sendEtf(const QString &symbol, const QString &action)
{
    if (!tradingEnabled_) {
        Q_EMIT notice(QStringLiteral("%1: 只读验收模式，申赎指令未发送").arg(profile_.name), true);
        return false;
    }
    const QString normalized = normalizeSymbol(symbol);
    return sendRequest({{"type", "etf_order"}, {"action", action}, {"code", normalized}, {"qty", 1},
                        {"client_order_id", newClientOrderId(normalized, action)}}, normalized + u'|' + action);
}

bool QmtClient::sendSell(const QString &symbol, qint64 quantity, qint64 priceE6)
{
    if (!tradingEnabled_) {
        Q_EMIT notice(QStringLiteral("%1: 只读验收模式，卖出指令未发送").arg(profile_.name), true);
        return false;
    }
    const QString normalized = normalizeSymbol(symbol);
    return sendRequest({{"type", "order"}, {"code", normalized}, {"side", "SELL"},
                        {"price", scaledPrice(priceE6)}, {"qty", quantity},
                        {"client_order_id", newClientOrderId(normalized, QStringLiteral("SELL"))}}, normalized + QStringLiteral("|SELL"));
}

bool QmtClient::cancelOrder(const QString &orderId)
{
    if (!tradingEnabled_) {
        Q_EMIT notice(QStringLiteral("%1: 只读验收模式，撤单指令未发送").arg(profile_.name), true);
        return false;
    }
    return sendRequest({{"type", "cancel_order"}, {"order_id", orderId}}, QStringLiteral("CANCEL|") + orderId);
}

void QmtClient::queryOrders() { sendRequest({{"type", "query_orders"}}); }
void QmtClient::queryPositions() { sendRequest({{"type", "query_positions"}}); }

bool QmtClient::sendRequest(QJsonObject request, const QString &lockKey)
{
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    if (!lockKey.isEmpty() && lockedUntilMs_.value(lockKey) > now) {
        Q_EMIT notice(QStringLiteral("%1: 相同标的/方向仍在10秒防重复窗口内").arg(profile_.name), true);
        return false;
    }
    if (!isConnected()) {
        Q_EMIT notice(QStringLiteral("%1 未连接，指令未发送").arg(profile_.name), true);
        return false;
    }
    const QByteArray payload = QJsonDocument(request).toJson(QJsonDocument::Compact) + '\n';
    if (socket_.write(payload) != payload.size()) {
        Q_EMIT notice(QStringLiteral("%1 写入失败，结果未知").arg(profile_.name), true);
        return false;
    }
    if (!lockKey.isEmpty()) {
        const qint64 deadlineMs = now + 10'000;
        lockedUntilMs_.insert(lockKey, deadlineMs);
        QString requestId = request.value(QStringLiteral("client_order_id")).toString();
        if (requestId.isEmpty()) requestId = request.value(QStringLiteral("order_id")).toVariant().toString();
        if (!requestId.isEmpty()) requestLocks_.insert(requestId, lockKey);
        QTimer::singleShot(10'000, this, [this, lockKey, requestId, deadlineMs] {
            // An earlier timer must never unlock a newer explicit retry.
            if (lockedUntilMs_.value(lockKey) == deadlineMs) lockedUntilMs_.remove(lockKey);
            if (!requestId.isEmpty() && requestLocks_.value(requestId) == lockKey) requestLocks_.remove(requestId);
            queryOrders();
            Q_EMIT stateChanged();
        });
    }
    Q_EMIT notice(QStringLiteral("%1: 指令已发送；10秒内绝不自动重发").arg(profile_.name), false);
    Q_EMIT stateChanged();
    return true;
}

void QmtClient::readLines()
{
    input_ += socket_.readAll();
    while (true) {
        const qsizetype newline = input_.indexOf('\n');
        if (newline < 0) break;
        const QByteArray line = input_.left(newline);
        input_.remove(0, newline + 1);
        const QJsonDocument document = QJsonDocument::fromJson(line);
        if (document.isObject()) handleMessage(document.object());
    }
}

void QmtClient::handleMessage(const QJsonObject &message)
{
    const QString type = message.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("order_result") || type == QStringLiteral("etf_order_result")
        || type == QStringLiteral("cancel_result") || type == QStringLiteral("order_ack")
        || type == QStringLiteral("cancel_ack")) {
        QString requestId = message.value(QStringLiteral("client_order_id")).toString();
        if (requestId.isEmpty()) requestId = message.value(QStringLiteral("order_id")).toVariant().toString();
        const QString lockKey = requestLocks_.take(requestId);
        if (!lockKey.isEmpty()) lockedUntilMs_.remove(lockKey);
        const bool success = message.value(QStringLiteral("success")).toBool(
            message.value(QStringLiteral("accepted")).toBool(false));
        const QString description = message.value(QStringLiteral("message")).toString(
            message.value(QStringLiteral("error")).toString());
        Q_EMIT notice(QStringLiteral("%1: %2").arg(
                          profile_.name,
                          description.isEmpty()
                              ? (success ? QStringLiteral("指令已明确接受") : QStringLiteral("指令明确失败"))
                              : description),
                      !success);
    } else if (type == QStringLiteral("orders_data") || type == QStringLiteral("orders_full")
               || type == QStringLiteral("orders") || type == QStringLiteral("orders_delta")) {
        const QString mode = message.value(QStringLiteral("sync_mode")).toString(
            type == QStringLiteral("orders_delta") ? QStringLiteral("delta") : QStringLiteral("full"));
        if (mode == QStringLiteral("full")) {
            QJsonArray values = message.value(QStringLiteral("data")).toArray();
            if (values.isEmpty() && message.contains(QStringLiteral("orders"))) {
                values = message.value(QStringLiteral("orders")).toArray();
            }
            orders_ = values;
        } else {
            const QJsonArray upserts = message.value(QStringLiteral("upserts")).toArray();
            for (const QJsonValue &entry : upserts) {
                const QString id = entry.toObject().value(QStringLiteral("order_id")).toVariant().toString();
                bool replaced = false;
                for (qsizetype index = 0; index < orders_.size(); ++index) {
                    if (orders_.at(index).toObject().value(QStringLiteral("order_id")).toVariant().toString() == id) {
                        orders_[index] = entry;
                        replaced = true;
                        break;
                    }
                }
                if (!replaced) orders_.append(entry);
            }
            const QJsonArray removed = message.value(QStringLiteral("remove_ids")).toArray();
            for (const QJsonValue &remove : removed) {
                const QString id = remove.toVariant().toString();
                for (qsizetype index = orders_.size() - 1; index >= 0 && !orders_.isEmpty(); --index) {
                    if (orders_.at(index).toObject().value(QStringLiteral("order_id")).toVariant().toString() == id) {
                        orders_.removeAt(index);
                    }
                    if (index == 0) break;
                }
            }
        }
    } else if (type == QStringLiteral("positions_data") || type == QStringLiteral("positions_full")
               || type == QStringLiteral("positions")) {
        available_.clear();
        QJsonArray values = message.value(QStringLiteral("data")).toArray();
        if (values.isEmpty() && message.contains(QStringLiteral("positions"))) {
            values = message.value(QStringLiteral("positions")).toArray();
        }
        for (const QJsonValue &value : values) {
            const QJsonObject position = value.toObject();
            const QString symbol = normalizeSymbol(objectSymbol(position));
            qint64 quantity = position.value(QStringLiteral("available_qty")).toInteger();
            if (!quantity) quantity = position.value(QStringLiteral("can_use_volume")).toInteger();
            if (!quantity) quantity = position.value(QStringLiteral("available")).toInteger();
            available_.insert(symbol, quantity);
        }
    } else if (type.contains(QStringLiteral("error"), Qt::CaseInsensitive)) {
        Q_EMIT notice(QStringLiteral("%1 后端错误: %2").arg(profile_.name, QString::fromUtf8(QJsonDocument(message).toJson(QJsonDocument::Compact))), true);
    }
    Q_EMIT dataChanged();
}

QString QmtClient::objectSymbol(const QJsonObject &object)
{
    for (const auto &key : {QStringLiteral("code"), QStringLiteral("symbol"), QStringLiteral("stock_code")}) {
        if (object.value(key).isString()) return object.value(key).toString();
    }
    return {};
}

} // namespace premium
