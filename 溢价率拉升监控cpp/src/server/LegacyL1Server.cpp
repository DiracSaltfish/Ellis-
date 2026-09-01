#include "server/LegacyL1Server.h"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QPointer>
#include <QTcpSocket>

#include <algorithm>

namespace premium {
namespace {

constexpr qsizetype MaximumLineBytes = 65'536;

QJsonArray stringArray(const auto &values)
{
    QJsonArray result;
    QStringList sorted;
    for (const auto &value : values) sorted.append(value);
    std::sort(sorted.begin(), sorted.end());
    for (const auto &value : sorted) result.append(value);
    return result;
}

} // namespace

LegacyL1Server::LegacyL1Server(QStringList defaults, QStringList hotSymbols, Limits limits, QObject *parent)
    : QObject(parent), defaults_(defaults.begin(), defaults.end()),
      hotSymbols_(hotSymbols.begin(), hotSymbols.end()), pinned_(defaults_), limits_(limits)
{
    pinned_.unite(hotSymbols_);
    maintained_ = pinned_;
    connect(&server_, &QTcpServer::newConnection, this, &LegacyL1Server::acceptPending);
    housekeeping_.setInterval(1000);
    connect(&housekeeping_, &QTimer::timeout, this, [this] {
        const qint64 now = QDateTime::currentMSecsSinceEpoch();
        const auto sockets = clients_.keys();
        for (QTcpSocket *socket : sockets) {
            if (now - clients_[socket].lastInboundMs > 45'000) closeClient(socket, QStringLiteral("idle_timeout"));
        }
        bool changed = false;
        for (auto it = releaseAtMs_.begin(); it != releaseAtMs_.end();) {
            if (it.value() <= now) {
                const QString symbol = it.key();
                maintained_.remove(symbol);
                ready_.remove(symbol);
                cache_.remove(symbol);
                it = releaseAtMs_.erase(it);
                changed = true;
            } else ++it;
        }
        if (changed) Q_EMIT desiredSymbolsChanged(maintainedSymbols());
    });
    housekeeping_.start();
    pendingTimer_.setSingleShot(true);
    connect(&pendingTimer_, &QTimer::timeout, this, &LegacyL1Server::flushPending);
}

LegacyL1Server::~LegacyL1Server()
{
    housekeeping_.stop();
    pendingTimer_.stop();
    const auto sockets = clients_.keys();
    for (QTcpSocket *socket : sockets) {
        disconnect(socket, nullptr, this, nullptr);
        socket->abort();
    }
    clients_.clear();
    server_.close();
}

bool LegacyL1Server::listen(const QHostAddress &address, quint16 port, QString *error)
{
    if (!server_.listen(address, port)) {
        if (error) *error = server_.errorString();
        return false;
    }
    port_ = server_.serverPort();
    Q_EMIT operationalEvent(QStringLiteral("19195 listening on %1:%2").arg(server_.serverAddress().toString()).arg(port_));
    return true;
}

void LegacyL1Server::acceptPending()
{
    while (server_.hasPendingConnections()) {
        QTcpSocket *socket = server_.nextPendingConnection();
        socket->setSocketOption(QAbstractSocket::LowDelayOption, 1);
        if (clients_.size() >= limits_.maxClients) {
            ++rejectedClients_;
            sendJson(socket, {{"v", 1}, {"t", "error"}, {"code", "server_full"}, {"message", "maximum clients reached"}});
            socket->disconnectFromHost();
            continue;
        }
        Client client;
        client.socket = socket;
        client.lastInboundMs = QDateTime::currentMSecsSinceEpoch();
        clients_.insert(socket, client);
        connect(socket, &QTcpSocket::readyRead, this, [this, socket] { readClient(socket); });
        connect(socket, &QTcpSocket::disconnected, this, [this, socket] { closeClient(socket, QStringLiteral("peer_closed")); });
        QJsonObject hello{{"v", 1}, {"t", "hello"}, {"service", "qmt_l1"}, {"port", port_},
                          {"default_interval_ms", 0}, {"min_interval_ms", 0}, {"max_interval_ms", 60'000},
                          {"publish_mode", "event_driven"}, {"coalesce_ms", 0},
                          {"client_ping_interval_ms", 15'000}, {"client_idle_timeout_ms", 45'000},
                          {"max_clients", limits_.maxClients}, {"defaults", stringArray(defaults_)}};
        sendJson(socket, hello);
    }
}

void LegacyL1Server::readClient(QTcpSocket *socket)
{
    auto it = clients_.find(socket);
    if (it == clients_.end()) return;
    Client &client = it.value();
    client.input += socket->readAll();
    if (client.input.size() > MaximumLineBytes && !client.input.contains('\n')) {
        sendError(client, {}, QStringLiteral("line_too_large"), QStringLiteral("line exceeds 65536 bytes"));
        closeClient(socket, QStringLiteral("line_too_large"));
        return;
    }
    while (true) {
        const qsizetype newline = client.input.indexOf('\n');
        if (newline < 0) break;
        const QByteArray line = client.input.left(newline);
        client.input.remove(0, newline + 1);
        if (line.size() > MaximumLineBytes) {
            sendError(client, {}, QStringLiteral("line_too_large"), QStringLiteral("line exceeds 65536 bytes"));
            closeClient(socket, QStringLiteral("line_too_large"));
            return;
        }
        if (!line.trimmed().isEmpty()) processLine(client, line);
    }
}

void LegacyL1Server::processLine(Client &client, const QByteArray &line)
{
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
    if (!document.isObject()) {
        sendError(client, {}, QStringLiteral("invalid_json"), parseError.errorString());
        return;
    }
    const QJsonObject request = document.object();
    const QJsonValue id = request.value(QStringLiteral("id"));
    if (request.value(QStringLiteral("v")).toInt(1) != 1) {
        sendError(client, id, QStringLiteral("unsupported_version"), QStringLiteral("only v1 is supported"));
        return;
    }
    client.lastInboundMs = QDateTime::currentMSecsSinceEpoch();
    const QString operation = operationOf(request);
    if (operation == QStringLiteral("subscribe")) handleSubscribe(client, request);
    else if (operation == QStringLiteral("unsubscribe")) handleUnsubscribe(client, request);
    else if (operation == QStringLiteral("status")) sendJson(client.socket, statusFor(client, id));
    else if (operation == QStringLiteral("ping")) {
        QJsonObject response{{"v", 1}, {"t", "pong"}, {"ts", QDateTime::currentMSecsSinceEpoch()}};
        if (!id.isUndefined()) response.insert(QStringLiteral("id"), id);
        sendJson(client.socket, response);
    } else sendError(client, id, QStringLiteral("unknown_operation"), QStringLiteral("use subscribe/unsubscribe/status/ping"));
}

void LegacyL1Server::handleSubscribe(Client &client, const QJsonObject &request)
{
    QStringList invalid;
    const QStringList requested = normalizedSymbols(request.value(QStringLiteral("symbols")), &invalid);
    const QJsonValue id = request.value(QStringLiteral("id"));
    if (requested.isEmpty()) {
        sendError(client, id, QStringLiteral("bad_request"), QStringLiteral("symbols must be a non-empty array"));
        return;
    }
    client.intervalMs = std::clamp(request.value(QStringLiteral("interval_ms")).toInt(0), 0, 60'000);
    QStringList accepted;
    QJsonArray rejected;
    QJsonArray temporary;
    for (const QString &symbol : requested) {
        QString reason;
        if (invalid.contains(symbol)) reason = QStringLiteral("invalid_symbol");
        else if (!client.symbols.contains(symbol) && client.symbols.size() >= limits_.maxSymbolsPerClient) reason = QStringLiteral("client_symbol_limit");
        else if (!maintained_.contains(symbol) && maintained_.size() >= limits_.maxMaintainedSymbols) reason = QStringLiteral("upstream_capacity");
        if (!reason.isEmpty()) {
            rejected.append(QJsonObject{{"s", symbol}, {"reason", reason}});
            continue;
        }
        if (!client.symbols.contains(symbol)) {
            client.symbols.insert(symbol);
            accepted.append(symbol);
        }
        if (!pinned_.contains(symbol)) temporary.append(symbol);
        releaseAtMs_.remove(symbol);
        maintained_.insert(symbol);
    }
    QJsonObject ack{{"v", 1}, {"t", "ack"}, {"op", "subscribe"}, {"symbols", stringArray(accepted)},
                    {"temporary", temporary}, {"rejected", rejected}, {"interval_ms", client.intervalMs}};
    if (!id.isUndefined()) ack.insert(QStringLiteral("id"), id);
    sendJson(client.socket, ack);
    sendInitial(client, accepted);
    schedulePendingFlush();
    refreshReferences();
}

void LegacyL1Server::handleUnsubscribe(Client &client, const QJsonObject &request)
{
    QStringList invalid;
    const QStringList requested = normalizedSymbols(request.value(QStringLiteral("symbols")), &invalid);
    QStringList removed;
    for (const QString &symbol : requested) {
        if (client.symbols.remove(symbol)) removed.append(symbol);
        client.pendingSymbols.remove(symbol);
        client.lastSentMs.remove(symbol);
    }
    QJsonObject ack{{"v", 1}, {"t", "ack"}, {"op", "unsubscribe"}, {"symbols", stringArray(removed)}};
    if (request.contains(QStringLiteral("id"))) ack.insert(QStringLiteral("id"), request.value(QStringLiteral("id")));
    sendJson(client.socket, ack);
    refreshReferences();
}

void LegacyL1Server::publish(const QuoteSnapshot &snapshot)
{
    cache_.insert(snapshot.symbol, snapshot);
    ready_.insert(snapshot.symbol);
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    const QJsonObject book = snapshot.toLegacyBookJson();
    for (auto it = clients_.begin(); it != clients_.end(); ++it) {
        Client &client = it.value();
        if (!client.symbols.contains(snapshot.symbol)) continue;
        if (now - client.lastSentMs.value(snapshot.symbol) < client.intervalMs) {
            client.pendingSymbols.insert(snapshot.symbol);
            continue;
        }
        client.pendingSymbols.remove(snapshot.symbol);
        client.lastSentMs.insert(snapshot.symbol, now);
        sendJson(client.socket, {{"v", 1}, {"t", "l1"}, {"seq", static_cast<qint64>(++sequence_)},
                                 {"ts", now}, {"books", QJsonArray{book}}});
    }
    schedulePendingFlush();
}

void LegacyL1Server::schedulePendingFlush()
{
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    qint64 earliest = -1;
    for (auto it = clients_.cbegin(); it != clients_.cend(); ++it) {
        const Client &client = it.value();
        for (const QString &symbol : client.pendingSymbols) {
            const qint64 due = client.lastSentMs.value(symbol) + client.intervalMs;
            if (earliest < 0 || due < earliest) earliest = due;
        }
    }
    if (earliest < 0) {
        pendingTimer_.stop();
        return;
    }
    pendingTimer_.start(static_cast<int>(std::clamp<qint64>(earliest - now, 0, 60'000)));
}

void LegacyL1Server::flushPending()
{
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    for (auto it = clients_.begin(); it != clients_.end(); ++it) {
        Client &client = it.value();
        const auto pending = client.pendingSymbols.values();
        for (const QString &symbol : pending) {
            if (!client.symbols.contains(symbol) || !ready_.contains(symbol) || !cache_.contains(symbol)) {
                client.pendingSymbols.remove(symbol);
                continue;
            }
            if (now - client.lastSentMs.value(symbol) < client.intervalMs) continue;
            client.pendingSymbols.remove(symbol);
            client.lastSentMs.insert(symbol, now);
            sendJson(client.socket, {{"v", 1}, {"t", "l1"}, {"seq", static_cast<qint64>(++sequence_)},
                                     {"ts", now}, {"books", QJsonArray{cache_.value(symbol).toLegacyBookJson()}}});
        }
    }
    schedulePendingFlush();
}

void LegacyL1Server::sendInitial(Client &client, const QStringList &symbols)
{
    for (const QString &symbol : symbols) {
        if (ready_.contains(symbol) && cache_.contains(symbol)) {
            sendJson(client.socket, {{"v", 1}, {"t", "l1"}, {"seq", static_cast<qint64>(++sequence_)},
                                     {"ts", QDateTime::currentMSecsSinceEpoch()},
                                     {"books", QJsonArray{cache_.value(symbol).toLegacyBookJson()}}});
        } else {
            sendJson(client.socket, {{"v", 1}, {"t", "l1"}, {"seq", static_cast<qint64>(++sequence_)},
                                     {"ts", QDateTime::currentMSecsSinceEpoch()}, {"books", QJsonArray{}},
                                     {"missing", QJsonArray{symbol}}});
        }
    }
}

void LegacyL1Server::closeClient(QTcpSocket *socket, const QString &reason)
{
    auto it = clients_.find(socket);
    if (it == clients_.end()) return;
    clients_.erase(it);
    ++dropCounts_[reason];
    socket->deleteLater();
    refreshReferences();
}

void LegacyL1Server::refreshReferences()
{
    QSet<QString> referenced = pinned_;
    for (const auto &client : clients_) referenced.unite(client.symbols);
    const qint64 releaseAt = QDateTime::currentMSecsSinceEpoch() + limits_.unsubscribeGraceSeconds * 1000LL;
    for (const QString &symbol : maintained_) {
        if (!referenced.contains(symbol) && !releaseAtMs_.contains(symbol)) releaseAtMs_.insert(symbol, releaseAt);
    }
    for (const QString &symbol : referenced) {
        releaseAtMs_.remove(symbol);
        maintained_.insert(symbol);
    }
    Q_EMIT desiredSymbolsChanged(maintainedSymbols());
}

QJsonObject LegacyL1Server::statusFor(const Client &client, const QJsonValue &id) const
{
    QSet<QString> temporary = maintained_;
    temporary.subtract(pinned_);
    QJsonObject drops;
    for (auto it = dropCounts_.begin(); it != dropCounts_.end(); ++it) drops.insert(it.key(), static_cast<qint64>(it.value()));
    QJsonObject result{{"v", 1}, {"t", "status"}, {"symbols", stringArray(client.symbols)},
                       {"defaults", stringArray(defaults_)}, {"hot", stringArray(hotSymbols_)},
                       {"pinned", stringArray(pinned_)}, {"temporary", stringArray(temporary)},
                       {"maintained", stringArray(maintained_)}, {"ready", stringArray(ready_)},
                       {"active_clients", clients_.size()}, {"max_clients", limits_.maxClients},
                       {"rejected_clients", static_cast<qint64>(rejectedClients_)}, {"client_drop_counts", drops},
                       {"market_online", marketOnline_}};
    if (!id.isUndefined()) result.insert(QStringLiteral("id"), id);
    return result;
}

void LegacyL1Server::sendJson(QTcpSocket *socket, const QJsonObject &object)
{
    if (!socket || socket->state() == QAbstractSocket::UnconnectedState) return;
    const QByteArray line = QJsonDocument(object).toJson(QJsonDocument::Compact) + '\n';
    if (socket->bytesToWrite() > 1024 * 1024) {
        QPointer<QTcpSocket> guarded(socket);
        QTimer::singleShot(0, this, [this, guarded] {
            if (guarded) closeClient(guarded, QStringLiteral("slow_client"));
        });
        return;
    }
    socket->write(line);
}

void LegacyL1Server::sendError(Client &client, const QJsonValue &id, const QString &code, const QString &message)
{
    QJsonObject object{{"v", 1}, {"t", "error"}, {"code", code}, {"message", message}};
    if (!id.isUndefined() && !id.isNull()) object.insert(QStringLiteral("id"), id);
    sendJson(client.socket, object);
}

QString LegacyL1Server::operationOf(const QJsonObject &request)
{
    for (const auto &name : {QStringLiteral("t"), QStringLiteral("op"), QStringLiteral("type")}) {
        if (request.value(name).isString()) return request.value(name).toString().toLower();
    }
    return {};
}

QStringList LegacyL1Server::normalizedSymbols(const QJsonValue &value, QStringList *invalid)
{
    QStringList result;
    if (!value.isArray()) return result;
    for (const QJsonValue &item : value.toArray()) {
        const QString raw = item.toString().trimmed().toUpper();
        const QString normalized = normalizeSymbol(raw);
        const bool domestic = normalized.size() == 9 && normalized.at(6) == u'.'
                           && (normalized.endsWith(QStringLiteral(".SH")) || normalized.endsWith(QStringLiteral(".SZ")));
        const bool hkt = raw.size() == 8 && raw.at(5) == u'.' && raw.endsWith(QStringLiteral(".HK"));
        bool hktDigits = hkt;
        for (int index = 0; hktDigits && index < 5; ++index) hktDigits = raw.at(index).isDigit();
        const bool valid = domestic || hktDigits;
        if (!valid) {
            invalid->append(raw);
            result.append(raw);
        } else {
            const QString canonical = hktDigits ? raw : normalized;
            if (!result.contains(canonical)) result.append(canonical);
        }
    }
    return result;
}

void LegacyL1Server::setMarketOnline(bool online)
{
    marketOnline_ = online;
    if (!online) {
        ready_.clear();
        cache_.clear();
        pendingTimer_.stop();
        for (auto it = clients_.begin(); it != clients_.end(); ++it) {
            it->pendingSymbols.clear();
            it->lastSentMs.clear();
        }
    }
}

bool LegacyL1Server::replaceDefaultSymbols(const QStringList &symbols, QString *error)
{
    return replacePinnedSymbols(symbols, QStringList(hotSymbols_.begin(), hotSymbols_.end()), error);
}

bool LegacyL1Server::replacePinnedSymbols(const QStringList &symbols, const QStringList &hotSymbols,
                                          QString *error)
{
    const QSet<QString> replacement(symbols.begin(), symbols.end());
    const QSet<QString> hotReplacement(hotSymbols.begin(), hotSymbols.end());
    QSet<QString> pinnedReplacement = replacement;
    pinnedReplacement.unite(hotReplacement);
    QSet<QString> clientReferences;
    for (const auto &client : clients_) clientReferences.unite(client.symbols);

    QSet<QString> prospective = maintained_;
    for (const QString &oldPinned : pinned_) {
        if (!pinnedReplacement.contains(oldPinned) && !clientReferences.contains(oldPinned)) {
            prospective.remove(oldPinned);
        }
    }
    prospective.unite(pinnedReplacement);
    prospective.unite(clientReferences);
    if (prospective.size() > limits_.maxMaintainedSymbols) {
        if (error) {
            *error = QStringLiteral("watchlist, L1 hot list and active clients would maintain %1 symbols; limit is %2")
                         .arg(prospective.size()).arg(limits_.maxMaintainedSymbols);
        }
        return false;
    }

    const QSet<QString> removed = pinned_ - pinnedReplacement;
    defaults_ = replacement;
    hotSymbols_ = hotReplacement;
    pinned_ = pinnedReplacement;
    maintained_ = prospective;
    for (const QString &symbol : removed) {
        releaseAtMs_.remove(symbol);
        if (!clientReferences.contains(symbol)) {
            ready_.remove(symbol);
            cache_.remove(symbol);
        }
    }
    for (const QString &symbol : pinned_) releaseAtMs_.remove(symbol);
    Q_EMIT desiredSymbolsChanged(maintainedSymbols());
    Q_EMIT operationalEvent(QStringLiteral("pinned lists replaced: monitor %1, hot %2, unique pinned %3, maintained %4")
                                .arg(defaults_.size()).arg(hotSymbols_.size()).arg(pinned_.size()).arg(maintained_.size()));
    return true;
}

QStringList LegacyL1Server::maintainedSymbols() const { return QStringList(maintained_.begin(), maintained_.end()); }
int LegacyL1Server::clientCount() const { return clients_.size(); }

} // namespace premium
