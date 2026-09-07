#include "IbkrBridgeCore.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QMutexLocker>
#include <QRegularExpression>
#include <QSet>
#include <QTimeZone>

#include <algorithm>
#include <cmath>
#include <initializer_list>

namespace machome::ibkr {
namespace {

bool onlyKnownKeys(const QJsonObject &object,
                   std::initializer_list<const char *> allowed,
                   const QString &context, QString *error)
{
    QSet<QString> known;
    for (const char *name : allowed) known.insert(QString::fromLatin1(name));
    for (auto it = object.constBegin(); it != object.constEnd(); ++it) {
        if (!known.contains(it.key())) {
            *error = QStringLiteral("%1 含未知字段: %2").arg(context, it.key());
            return false;
        }
    }
    return true;
}

QString expandPath(QString value)
{
    value = value.trimmed();
    if (value == QStringLiteral("~")) {
        return QDir::homePath();
    }
    if (value.startsWith(QStringLiteral("~/"))) {
        return QDir::home().filePath(value.mid(2));
    }
    return value;
}

bool boundedString(const QJsonObject &object, const char *name, int maximum,
                   QString *out, QString *error, bool required = false)
{
    const QJsonValue value = object.value(QLatin1String(name));
    if (value.isUndefined() && !required) {
        out->clear();
        return true;
    }
    if (!value.isString()) {
        *error = QStringLiteral("%1 必须是字符串").arg(QString::fromLatin1(name));
        return false;
    }
    const QString text = value.toString().trimmed();
    if ((required && text.isEmpty()) || text.size() > maximum
        || text.contains(QLatin1Char('\n')) || text.contains(QLatin1Char('\r'))
        || text.contains(QChar::Null)) {
        *error = QStringLiteral("%1 为空、过长或含非法字符").arg(QString::fromLatin1(name));
        return false;
    }
    *out = text;
    return true;
}

bool integerInRange(const QJsonObject &object, const char *name, int fallback,
                    int minimum, int maximum, int *out, QString *error)
{
    const QJsonValue value = object.value(QLatin1String(name));
    if (value.isUndefined()) {
        *out = fallback;
        return true;
    }
    if (!value.isDouble()) {
        *error = QStringLiteral("%1 必须是整数").arg(QString::fromLatin1(name));
        return false;
    }
    const double numeric = value.toDouble();
    if (!std::isfinite(numeric) || std::floor(numeric) != numeric
        || numeric < minimum || numeric > maximum) {
        *error = QStringLiteral("%1 超出允许范围 [%2, %3]")
                     .arg(QString::fromLatin1(name)).arg(minimum).arg(maximum);
        return false;
    }
    *out = static_cast<int>(numeric);
    return true;
}

bool validCode(const QString &value, int maximum)
{
    if (value.isEmpty() || value.size() > maximum) return false;
    static const QRegularExpression allowed(QStringLiteral("^[A-Za-z0-9._:/ -]+$"));
    return allowed.match(value).hasMatch();
}

bool isPrivateIpv4Literal(const QString &value)
{
    const QStringList parts = value.split(QLatin1Char('.'));
    if (parts.size() != 4) return false;
    int octets[4] = {};
    for (int index = 0; index < 4; ++index) {
        const QString &part = parts.at(index);
        if (part.isEmpty() || (part.size() > 1 && part.startsWith(QLatin1Char('0')))) {
            return false;
        }
        bool ok = false;
        const int octet = part.toInt(&ok);
        if (!ok || octet < 0 || octet > 255) return false;
        octets[index] = octet;
    }
    return octets[0] == 10
        || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
        || (octets[0] == 192 && octets[1] == 168);
}

QJsonValue finiteValue(bool present, double value)
{
    return present && std::isfinite(value)
        ? QJsonValue(QString::number(value, 'g', 15))
        : QJsonValue(QJsonValue::Null);
}

} // namespace

ConnectionScheduleDecision ConnectionSchedule::evaluate(const QDateTime &observedAt) const
{
    ConnectionScheduleDecision decision;
    decision.localTime = observedAt.toTimeZone(timezone);
    if (!enabled) {
        decision.active = true;
        return decision;
    }

    const QDate date = decision.localTime.date();
    const QTime time = decision.localTime.time();
    const bool sameDay = start < stop;
    if (sameDay) {
        decision.active = weekdays.contains(date.dayOfWeek()) && time >= start && time < stop;
    } else {
        decision.active = (weekdays.contains(date.dayOfWeek()) && time >= start)
            || (weekdays.contains(date.addDays(-1).dayOfWeek()) && time < stop);
    }

    for (int offset = -1; offset <= 8; ++offset) {
        const QDate sessionDate = date.addDays(offset);
        if (!weekdays.contains(sessionDate.dayOfWeek())) continue;
        const QDateTime startAt(sessionDate, start, timezone);
        const QDateTime stopAt(sessionDate.addDays(sameDay ? 0 : 1), stop, timezone);
        for (const QDateTime &candidate : {startAt, stopAt}) {
            if (candidate > decision.localTime
                && (!decision.nextTransition.isValid() || candidate < decision.nextTransition)) {
                decision.nextTransition = candidate;
            }
        }
    }
    return decision;
}

QJsonObject ConnectionSchedule::toJson() const
{
    QList<int> days = weekdays.values();
    std::sort(days.begin(), days.end());
    QJsonArray weekdayValues;
    for (int day : days) weekdayValues.append(day);
    return {
        {QStringLiteral("enabled"), enabled},
        {QStringLiteral("timezone"), QString::fromUtf8(timezone.id())},
        {QStringLiteral("weekdays"), weekdayValues},
        {QStringLiteral("start_time"), start.toString(QStringLiteral("HH:mm:ss"))},
        {QStringLiteral("stop_time"), stop.toString(QStringLiteral("HH:mm:ss"))},
    };
}

bool ContractSpec::fromJson(const QJsonObject &object, const QString &subscriptionId,
                            ContractSpec *out, QString *errorMessage)
{
    if (out == nullptr || errorMessage == nullptr) return false;
    *errorMessage = {};
    if (!validCode(subscriptionId.trimmed(), 128)) {
        *errorMessage = QStringLiteral("subscription_id 为空、过长或含非法字符");
        return false;
    }
    if (!onlyKnownKeys(object,
                       {"con_id", "symbol", "security_type", "exchange",
                        "primary_exchange", "currency", "expiry", "multiplier",
                        "trading_class", "generic_ticks"},
                       QStringLiteral("contract"), errorMessage)) {
        return false;
    }
    ContractSpec spec;
    spec.id = subscriptionId.trimmed();
    if (!boundedString(object, "symbol", 32, &spec.symbol, errorMessage, true)
        || !boundedString(object, "security_type", 16, &spec.securityType, errorMessage, true)
        || !boundedString(object, "exchange", 32, &spec.exchange, errorMessage, true)
        || !boundedString(object, "primary_exchange", 32, &spec.primaryExchange, errorMessage)
        || !boundedString(object, "currency", 8, &spec.currency, errorMessage, true)
        || !boundedString(object, "expiry", 16, &spec.expiry, errorMessage)
        || !boundedString(object, "multiplier", 16, &spec.multiplier, errorMessage)
        || !boundedString(object, "trading_class", 32, &spec.tradingClass, errorMessage)
        || !boundedString(object, "generic_ticks", 128, &spec.genericTicks, errorMessage)) {
        return false;
    }
    if (!validCode(spec.symbol, 32) || !validCode(spec.securityType, 16)
        || !validCode(spec.exchange, 32) || !validCode(spec.currency, 8)
        || (!spec.primaryExchange.isEmpty() && !validCode(spec.primaryExchange, 32))
        || (!spec.expiry.isEmpty() && !validCode(spec.expiry, 16))
        || (!spec.multiplier.isEmpty() && !validCode(spec.multiplier, 16))
        || (!spec.tradingClass.isEmpty() && !validCode(spec.tradingClass, 32))) {
        *errorMessage = QStringLiteral("contract 含非法合约字段");
        return false;
    }
    static const QRegularExpression ticks(QStringLiteral("^[0-9,]*$"));
    if (!ticks.match(spec.genericTicks).hasMatch()) {
        *errorMessage = QStringLiteral("contract.generic_ticks 只允许数字和逗号");
        return false;
    }
    const QJsonValue conIdValue = object.value(QStringLiteral("con_id"));
    if (!conIdValue.isUndefined()) {
        if (!conIdValue.isDouble() || conIdValue.toDouble() < 0
            || conIdValue.toDouble() > 2'000'000'000
            || std::floor(conIdValue.toDouble()) != conIdValue.toDouble()) {
            *errorMessage = QStringLiteral("contract.con_id 必须是 0..2000000000 的整数");
            return false;
        }
        spec.conId = static_cast<qint64>(conIdValue.toDouble());
    }
    spec.symbol = spec.symbol.toUpper();
    spec.securityType = spec.securityType.toUpper();
    spec.exchange = spec.exchange.toUpper();
    spec.primaryExchange = spec.primaryExchange.toUpper();
    spec.currency = spec.currency.toUpper();
    *out = spec;
    return true;
}

bool ContractSpec::marketDataEquivalent(const ContractSpec &other) const
{
    return conId == other.conId && symbol == other.symbol
        && securityType == other.securityType && exchange == other.exchange
        && primaryExchange == other.primaryExchange && currency == other.currency
        && expiry == other.expiry && multiplier == other.multiplier
        && tradingClass == other.tradingClass && genericTicks == other.genericTicks;
}

bool BridgeConfig::loadFile(const QString &path, BridgeConfig *out, QString *errorMessage)
{
    if (out == nullptr || errorMessage == nullptr) return false;
    *errorMessage = {};
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        *errorMessage = QStringLiteral("无法读取 IBKR bridge 配置: %1").arg(file.errorString());
        return false;
    }
    constexpr qint64 maximumConfigBytes = 1024 * 1024;
    const QByteArray raw = file.read(maximumConfigBytes + 1);
    if (raw.size() > maximumConfigBytes) {
        *errorMessage = QStringLiteral("IBKR bridge 配置超过 1 MiB");
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(raw, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        *errorMessage = QStringLiteral("IBKR bridge 配置不是有效 JSON 对象: %1")
                            .arg(parseError.errorString());
        return false;
    }

    const QJsonObject root = document.object();
    if (!onlyKnownKeys(root,
                       {"schema_version", "socket_path", "health_file", "tws",
                        "connection_schedule", "limits", "subscriptions"},
                       QStringLiteral("IBKR bridge 配置"), errorMessage)) {
        return false;
    }
    if (root.value(QStringLiteral("schema_version")).toInt(-1) != 1) {
        *errorMessage = QStringLiteral("IBKR bridge 配置 schema_version 必须为 1");
        return false;
    }
    BridgeConfig result;
    QString socketPath;
    QString healthFile;
    if (!boundedString(root, "socket_path", 1024, &socketPath, errorMessage, true)
        || !boundedString(root, "health_file", 1024, &healthFile, errorMessage, true)) {
        return false;
    }
    socketPath = QDir::cleanPath(expandPath(socketPath));
    healthFile = QDir::cleanPath(expandPath(healthFile));
    if (!QFileInfo(socketPath).isAbsolute() || !QFileInfo(healthFile).isAbsolute()) {
        *errorMessage = QStringLiteral("socket_path 和 health_file 必须是绝对路径或 ~/ 路径");
        return false;
    }
    if (socketPath == QDir::rootPath() || healthFile == QDir::rootPath()) {
        *errorMessage = QStringLiteral("拒绝使用根目录作为运行时路径");
        return false;
    }
    result.socketPath = socketPath;
    result.healthFile = healthFile;

    const QJsonValue twsValue = root.value(QStringLiteral("tws"));
    const QJsonObject tws = twsValue.toObject();
    if (!twsValue.isObject() || tws.isEmpty()) {
        *errorMessage = QStringLiteral("tws 必须是非空 JSON 对象");
        return false;
    }
    if (!onlyKnownKeys(tws,
                       {"host", "port", "client_id", "market_data_type",
                        "allowed_private_hosts",
                        "reconnect_minimum_ms", "reconnect_maximum_ms",
                        "connect_timeout_ms", "heartbeat_interval_ms",
                        "heartbeat_stale_ms"},
                       QStringLiteral("tws"), errorMessage)) {
        return false;
    }
    QString host;
    if (!boundedString(tws, "host", 255, &host, errorMessage, true)) return false;
    host = host.toLower();
    QSet<QString> allowedPrivateHosts;
    const QJsonValue allowedHostsValue = tws.value(QStringLiteral("allowed_private_hosts"));
    if (!allowedHostsValue.isUndefined()) {
        if (!allowedHostsValue.isArray() || allowedHostsValue.toArray().size() > 16) {
            *errorMessage = QStringLiteral("tws.allowed_private_hosts 必须是最多 16 项的数组");
            return false;
        }
        for (const QJsonValue &entry : allowedHostsValue.toArray()) {
            if (!entry.isString()) {
                *errorMessage = QStringLiteral("tws.allowed_private_hosts 只允许私网 IPv4 字符串");
                return false;
            }
            const QString candidate = entry.toString().trimmed().toLower();
            if (!isPrivateIpv4Literal(candidate) || allowedPrivateHosts.contains(candidate)) {
                *errorMessage = QStringLiteral("tws.allowed_private_hosts 含非私网 IPv4 或重复项");
                return false;
            }
            allowedPrivateHosts.insert(candidate);
        }
    }
    const bool loopback = host == QStringLiteral("127.0.0.1")
        || host == QStringLiteral("localhost");
    if (!loopback && (!isPrivateIpv4Literal(host) || !allowedPrivateHosts.contains(host))) {
        *errorMessage = QStringLiteral(
            "远端 TWS 必须是显式列入 tws.allowed_private_hosts 的 RFC1918 IPv4");
        return false;
    }
    result.twsHost = host;
    if (!integerInRange(tws, "port", result.twsPort, 1, 65535,
                        &result.twsPort, errorMessage)
        || !integerInRange(tws, "client_id", result.clientId, 0, 2'000'000'000,
                           &result.clientId, errorMessage)
        || !integerInRange(tws, "market_data_type", result.marketDataType, 1, 4,
                           &result.marketDataType, errorMessage)
        || !integerInRange(tws, "reconnect_minimum_ms", result.reconnectMinimumMs, 250, 60'000,
                           &result.reconnectMinimumMs, errorMessage)
        || !integerInRange(tws, "reconnect_maximum_ms", result.reconnectMaximumMs, 1'000, 300'000,
                           &result.reconnectMaximumMs, errorMessage)
        || !integerInRange(tws, "connect_timeout_ms", result.connectTimeoutMs, 1'000, 120'000,
                           &result.connectTimeoutMs, errorMessage)
        || !integerInRange(tws, "heartbeat_interval_ms", result.heartbeatIntervalMs, 1'000, 60'000,
                           &result.heartbeatIntervalMs, errorMessage)
        || !integerInRange(tws, "heartbeat_stale_ms", result.heartbeatStaleMs, 3'000, 300'000,
                           &result.heartbeatStaleMs, errorMessage)) {
        return false;
    }
    if (result.reconnectMaximumMs < result.reconnectMinimumMs
        || result.heartbeatStaleMs <= result.heartbeatIntervalMs) {
        *errorMessage = QStringLiteral("重连上限不得小于下限，心跳 stale 必须大于心跳周期");
        return false;
    }

    const QJsonValue scheduleValue = root.value(QStringLiteral("connection_schedule"));
    {
        if (!scheduleValue.isObject()) {
            *errorMessage = QStringLiteral("connection_schedule 必须是 JSON 对象");
            return false;
        }
        const QJsonObject schedule = scheduleValue.toObject();
        if (!onlyKnownKeys(schedule,
                           {"enabled", "timezone", "weekdays", "start_time", "stop_time"},
                           QStringLiteral("connection_schedule"), errorMessage)) {
            return false;
        }
        const QJsonValue enabledValue = schedule.value(QStringLiteral("enabled"));
        if (!enabledValue.isBool()) {
            *errorMessage = QStringLiteral("connection_schedule.enabled 必须是布尔值");
            return false;
        }
        result.connectionSchedule.enabled = enabledValue.toBool();

        QString timezoneName;
        QString startText;
        QString stopText;
        if (!boundedString(schedule, "timezone", 128, &timezoneName, errorMessage, true)
            || !boundedString(schedule, "start_time", 8, &startText, errorMessage, true)
            || !boundedString(schedule, "stop_time", 8, &stopText, errorMessage, true)) {
            return false;
        }
        const QTimeZone timezone(timezoneName.toUtf8());
        if (!timezone.isValid()) {
            *errorMessage = QStringLiteral("connection_schedule.timezone 无效");
            return false;
        }
        static const QRegularExpression clockPattern(
            QStringLiteral("^(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?$"));
        if (!clockPattern.match(startText).hasMatch()
            || !clockPattern.match(stopText).hasMatch()) {
            *errorMessage = QStringLiteral("connection_schedule 时间必须是 HH:mm 或 HH:mm:ss");
            return false;
        }
        const auto parseClock = [](const QString &value) {
            return QTime::fromString(value,
                                     value.size() == 5 ? QStringLiteral("HH:mm")
                                                       : QStringLiteral("HH:mm:ss"));
        };
        const QTime start = parseClock(startText);
        const QTime stop = parseClock(stopText);
        if (!start.isValid() || !stop.isValid() || start == stop) {
            *errorMessage = QStringLiteral("connection_schedule start_time/stop_time 无效或相同");
            return false;
        }
        const QJsonValue weekdaysValue = schedule.value(QStringLiteral("weekdays"));
        if (!weekdaysValue.isArray() || weekdaysValue.toArray().isEmpty()
            || weekdaysValue.toArray().size() > 7) {
            *errorMessage = QStringLiteral("connection_schedule.weekdays 必须是非空数组");
            return false;
        }
        QSet<int> weekdays;
        for (const QJsonValue &entry : weekdaysValue.toArray()) {
            if (!entry.isDouble() || std::floor(entry.toDouble()) != entry.toDouble()) {
                *errorMessage = QStringLiteral("connection_schedule.weekdays 必须是 1..7 整数");
                return false;
            }
            const int day = entry.toInt();
            if (day < 1 || day > 7 || weekdays.contains(day)) {
                *errorMessage = QStringLiteral("connection_schedule.weekdays 含越界或重复项");
                return false;
            }
            weekdays.insert(day);
        }
        result.connectionSchedule.timezone = timezone;
        result.connectionSchedule.weekdays = weekdays;
        result.connectionSchedule.start = start;
        result.connectionSchedule.stop = stop;
    }

    const QJsonValue limitsValue = root.value(QStringLiteral("limits"));
    if (!limitsValue.isUndefined() && !limitsValue.isObject()) {
        *errorMessage = QStringLiteral("limits 必须是 JSON 对象");
        return false;
    }
    const QJsonObject limits = limitsValue.toObject();
    if (!limits.isEmpty()) {
        if (!onlyKnownKeys(limits,
                           {"maximum_clients", "maximum_request_bytes",
                            "maximum_subscriptions"},
                           QStringLiteral("limits"), errorMessage)) {
            return false;
        }
        if (!integerInRange(limits, "maximum_clients", result.maximumClients, 1, 128,
                            &result.maximumClients, errorMessage)
            || !integerInRange(limits, "maximum_request_bytes", result.maximumRequestBytes,
                               1024, 1024 * 1024, &result.maximumRequestBytes, errorMessage)
            || !integerInRange(limits, "maximum_subscriptions", result.maximumSubscriptions,
                               1, 4096, &result.maximumSubscriptions, errorMessage)) {
            return false;
        }
    }

    const QJsonValue subscriptionsValue = root.value(QStringLiteral("subscriptions"));
    if (!subscriptionsValue.isArray()) {
        *errorMessage = QStringLiteral("subscriptions 必须是数组");
        return false;
    }
    const QJsonArray subscriptions = subscriptionsValue.toArray();
    if (subscriptions.isEmpty() || subscriptions.size() > 512) {
        *errorMessage = QStringLiteral("subscriptions 必须包含 1..512 个合约");
        return false;
    }
    if (subscriptions.size() > result.maximumSubscriptions) {
        *errorMessage = QStringLiteral("subscriptions 超过 limits.maximum_subscriptions");
        return false;
    }
    QHash<QString, bool> seenIds;
    for (qsizetype index = 0; index < subscriptions.size(); ++index) {
        if (!subscriptions.at(index).isObject()) {
            *errorMessage = QStringLiteral("subscriptions[%1] 必须是对象").arg(index);
            return false;
        }
        const QJsonObject object = subscriptions.at(index).toObject();
        if (!onlyKnownKeys(object,
                           {"id", "con_id", "symbol", "security_type", "exchange",
                            "primary_exchange", "currency", "expiry", "multiplier",
                            "trading_class", "generic_ticks"},
                           QStringLiteral("subscriptions[%1]").arg(index), errorMessage)) {
            return false;
        }
        QString subscriptionId;
        if (!boundedString(object, "id", 128, &subscriptionId, errorMessage, true)) return false;
        ContractSpec spec;
        QJsonObject contractObject = object;
        contractObject.remove(QStringLiteral("id"));
        if (!ContractSpec::fromJson(contractObject, subscriptionId, &spec, errorMessage)) {
            *errorMessage = QStringLiteral("subscriptions[%1]: %2").arg(index).arg(*errorMessage);
            return false;
        }
        if (seenIds.contains(spec.id)) {
            *errorMessage = QStringLiteral("subscriptions id 重复: %1").arg(spec.id);
            return false;
        }
        seenIds.insert(spec.id, true);
        result.subscriptions.append(spec);
    }
    *out = result;
    return true;
}

QJsonObject BridgeConfig::safeSummary() const
{
    QJsonArray ids;
    for (const ContractSpec &spec : subscriptions) ids.append(spec.id);
    return {
        {QStringLiteral("protocol"), QString::fromLatin1(kBridgeProtocol)},
        {QStringLiteral("socket_path"), socketPath},
        {QStringLiteral("health_file"), healthFile},
        {QStringLiteral("tws_host"), twsHost},
        {QStringLiteral("tws_port"), twsPort},
        {QStringLiteral("client_id"), clientId},
        {QStringLiteral("market_data_type"), marketDataType},
        {QStringLiteral("connection_schedule"), connectionSchedule.toJson()},
        {QStringLiteral("subscription_count"), subscriptions.size()},
        {QStringLiteral("subscription_ids"), ids},
    };
}

void QuoteBook::reset(const QList<ContractSpec> &subscriptions, int firstTickerId)
{
    QMutexLocker locker(&mutex_);
    byTicker_.clear();
    tickerById_.clear();
    updateCount_ = 0;
    int tickerId = firstTickerId;
    for (const ContractSpec &spec : subscriptions) {
        Record record;
        record.contract = spec;
        record.tickerId = tickerId;
        byTicker_.insert(tickerId, record);
        tickerById_.insert(spec.id, tickerId);
        ++tickerId;
    }
}

QuoteBook::RegisterResult QuoteBook::registerSubscription(
    const ContractSpec &subscription, int tickerId)
{
    QMutexLocker locker(&mutex_);
    const auto existingTicker = tickerById_.constFind(subscription.id);
    if (existingTicker != tickerById_.cend()) {
        const auto record = byTicker_.constFind(*existingTicker);
        return record != byTicker_.cend()
                && record->contract.marketDataEquivalent(subscription)
            ? RegisterResult::Existing : RegisterResult::Conflict;
    }
    if (tickerId <= 0 || byTicker_.contains(tickerId)) return RegisterResult::Conflict;
    Record record;
    record.contract = subscription;
    record.tickerId = tickerId;
    byTicker_.insert(tickerId, record);
    tickerById_.insert(subscription.id, tickerId);
    return RegisterResult::Added;
}

bool QuoteBook::removeSubscription(const QString &subscriptionId, int *tickerId,
                                   ContractSpec *contract)
{
    QMutexLocker locker(&mutex_);
    const auto idIt = tickerById_.find(subscriptionId);
    if (idIt == tickerById_.end()) return false;
    const int value = idIt.value();
    const auto recordIt = byTicker_.find(value);
    if (recordIt == byTicker_.end()) return false;
    if (tickerId != nullptr) *tickerId = value;
    if (contract != nullptr) *contract = recordIt->contract;
    byTicker_.erase(recordIt);
    tickerById_.erase(idIt);
    return true;
}

bool QuoteBook::updatePrice(int tickerId, PriceField field, double value,
                            const QDateTime &receivedAt)
{
    if (!std::isfinite(value) || value <= 0.0 || !receivedAt.isValid()) return false;
    QMutexLocker locker(&mutex_);
    auto it = byTicker_.find(tickerId);
    if (it == byTicker_.end()) return false;
    switch (field) {
    case PriceField::Bid: it->bid = value; it->hasBid = true; break;
    case PriceField::Ask: it->ask = value; it->hasAsk = true; break;
    case PriceField::Last: it->last = value; it->hasLast = true; break;
    case PriceField::Close: it->close = value; it->hasClose = true; break;
    }
    it->receivedAt = receivedAt.toUTC();
    ++it->sequence;
    ++updateCount_;
    return true;
}

bool QuoteBook::updateSize(int tickerId, SizeField field, const QString &decimalValue,
                           const QDateTime &receivedAt)
{
    const QString normalized = normalizedDecimal(decimalValue);
    if (normalized.isEmpty() || !receivedAt.isValid()) return false;
    QMutexLocker locker(&mutex_);
    auto it = byTicker_.find(tickerId);
    if (it == byTicker_.end()) return false;
    switch (field) {
    case SizeField::Bid: it->bidSize = normalized; break;
    case SizeField::Ask: it->askSize = normalized; break;
    case SizeField::Last: it->lastSize = normalized; break;
    }
    it->receivedAt = receivedAt.toUTC();
    ++it->sequence;
    ++updateCount_;
    return true;
}

bool QuoteBook::updateExchangeTimestamp(int tickerId, qint64 epochSeconds)
{
    if (epochSeconds <= 0) return false;
    QMutexLocker locker(&mutex_);
    auto it = byTicker_.find(tickerId);
    if (it == byTicker_.end()) return false;
    it->exchangeEpochSeconds = epochSeconds;
    ++it->sequence;
    ++updateCount_;
    return true;
}

bool QuoteBook::updateMarketDataType(int tickerId, int marketDataType)
{
    if (marketDataType < 1 || marketDataType > 4) return false;
    QMutexLocker locker(&mutex_);
    auto it = byTicker_.find(tickerId);
    if (it == byTicker_.end()) return false;
    it->marketDataType = marketDataType;
    ++it->sequence;
    return true;
}

bool QuoteBook::containsId(const QString &subscriptionId) const
{
    QMutexLocker locker(&mutex_);
    return tickerById_.contains(subscriptionId);
}

int QuoteBook::tickerIdFor(const QString &subscriptionId) const
{
    QMutexLocker locker(&mutex_);
    return tickerById_.value(subscriptionId, -1);
}

QList<QPair<int, ContractSpec>> QuoteBook::tickerContracts() const
{
    QMutexLocker locker(&mutex_);
    QList<QPair<int, ContractSpec>> result;
    result.reserve(byTicker_.size());
    for (auto it = byTicker_.cbegin(); it != byTicker_.cend(); ++it) {
        result.append(qMakePair(it.key(), it->contract));
    }
    std::sort(result.begin(), result.end(), [](const auto &left, const auto &right) {
        return left.first < right.first;
    });
    return result;
}

int QuoteBook::subscriptionCount() const
{
    QMutexLocker locker(&mutex_);
    return tickerById_.size();
}

QJsonObject QuoteBook::quote(const QString &subscriptionId, qint64 staleAfterMs) const
{
    QMutexLocker locker(&mutex_);
    const int tickerId = tickerById_.value(subscriptionId, -1);
    const auto it = byTicker_.constFind(tickerId);
    return it == byTicker_.cend()
        ? QJsonObject{}
        : recordJson(*it, staleAfterMs, QDateTime::currentDateTimeUtc());
}

QJsonArray QuoteBook::quotes(qint64 staleAfterMs) const
{
    QMutexLocker locker(&mutex_);
    QList<Record> records = byTicker_.values();
    std::sort(records.begin(), records.end(), [](const Record &left, const Record &right) {
        return left.contract.id < right.contract.id;
    });
    const QDateTime now = QDateTime::currentDateTimeUtc();
    QJsonArray result;
    for (const Record &record : records) result.append(recordJson(record, staleAfterMs, now));
    return result;
}

qint64 QuoteBook::updateCount() const
{
    QMutexLocker locker(&mutex_);
    return updateCount_;
}

QJsonObject QuoteBook::contractJson(const ContractSpec &contract)
{
    return {
        {QStringLiteral("id"), contract.id},
        {QStringLiteral("con_id"), contract.conId},
        {QStringLiteral("symbol"), contract.symbol},
        {QStringLiteral("security_type"), contract.securityType},
        {QStringLiteral("exchange"), contract.exchange},
        {QStringLiteral("primary_exchange"), contract.primaryExchange},
        {QStringLiteral("currency"), contract.currency},
        {QStringLiteral("expiry"), contract.expiry},
        {QStringLiteral("multiplier"), contract.multiplier},
        {QStringLiteral("trading_class"), contract.tradingClass},
        {QStringLiteral("generic_ticks"), contract.genericTicks},
    };
}

QJsonObject QuoteBook::recordJson(const Record &record, qint64 staleAfterMs,
                                  const QDateTime &now)
{
    const qint64 ageMs = record.receivedAt.isValid()
        ? std::max<qint64>(0, record.receivedAt.msecsTo(now)) : -1;
    const bool fresh = ageMs >= 0 && ageMs <= std::max<qint64>(1, staleAfterMs);
    QJsonObject object{
        {QStringLiteral("contract"), contractJson(record.contract)},
        {QStringLiteral("ticker_id"), record.tickerId},
        {QStringLiteral("sequence"), static_cast<qint64>(record.sequence)},
        {QStringLiteral("market_data_type"), record.marketDataType},
        {QStringLiteral("bid"), finiteValue(record.hasBid, record.bid)},
        {QStringLiteral("ask"), finiteValue(record.hasAsk, record.ask)},
        {QStringLiteral("last"), finiteValue(record.hasLast, record.last)},
        {QStringLiteral("close"), finiteValue(record.hasClose, record.close)},
        {QStringLiteral("bid_size"), record.bidSize.isEmpty() ? QJsonValue(QJsonValue::Null) : QJsonValue(record.bidSize)},
        {QStringLiteral("ask_size"), record.askSize.isEmpty() ? QJsonValue(QJsonValue::Null) : QJsonValue(record.askSize)},
        {QStringLiteral("last_size"), record.lastSize.isEmpty() ? QJsonValue(QJsonValue::Null) : QJsonValue(record.lastSize)},
        {QStringLiteral("received_at"), record.receivedAt.isValid()
             ? QJsonValue(record.receivedAt.toUTC().toString(Qt::ISODateWithMs))
             : QJsonValue(QJsonValue::Null)},
        {QStringLiteral("age_ms"), ageMs},
        {QStringLiteral("fresh"), fresh},
    };
    object.insert(QStringLiteral("exchange_timestamp"), record.exchangeEpochSeconds > 0
        ? QJsonValue(QDateTime::fromSecsSinceEpoch(record.exchangeEpochSeconds, QTimeZone::UTC)
                         .toString(Qt::ISODateWithMs))
        : QJsonValue(QJsonValue::Null));
    return object;
}

QString QuoteBook::normalizedDecimal(const QString &value)
{
    QString text = value.trimmed();
    if (text.startsWith(QLatin1Char('+'))) text.remove(0, 1);
    static const QRegularExpression decimal(
        QStringLiteral("^(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"));
    if (text.size() > 64 || !decimal.match(text).hasMatch()) return {};
    bool ok = false;
    const double numeric = text.toDouble(&ok);
    return ok && std::isfinite(numeric) && numeric >= 0.0 ? text : QString{};
}

} // namespace machome::ibkr
