#include "modules/redemption/RedemptionCore.h"

#include <QRegularExpression>
#include <QTimeZone>
#include <QtMath>
#include <algorithm>
#include <cmath>

namespace machome::redemption {
namespace {

const QTimeZone kShanghai("Asia/Shanghai");

bool finiteNumber(const QJsonValue &value, double *result = nullptr) {
    if (!value.isDouble() || !std::isfinite(value.toDouble())) return false;
    if (result) *result = value.toDouble();
    return true;
}

QString displaySymbol(const QString &windcode) {
    return windcode.endsWith(QStringLiteral(".SZ")) ? windcode.first(6) : windcode;
}

QDateTime localBoundary(const QDate &day, const QTime &time) {
    return QDateTime(day, time, kShanghai);
}

QString shareText(const QJsonValue &value) {
    if (!value.isDouble()) return QStringLiteral("—");
    qint64 number = qRound64(value.toDouble());
    const bool negative = number < 0;
    QString digits = QString::number(qAbs(number));
    QStringList groups;
    while (!digits.isEmpty()) {
        const qsizetype take = std::min<qsizetype>(4, digits.size());
        groups.prepend(digits.right(take));
        digits.chop(take);
    }
    return (negative ? QStringLiteral("-") : QString()) + groups.join(u' ');
}

bool pcfReadyToday(const QJsonObject &pcf, const QDate &day) {
    return pcf.value(QStringLiteral("status")).toString() == QStringLiteral("ready")
        && pcf.value(QStringLiteral("trading_day")).toString() == day.toString(Qt::ISODate);
}

} // namespace

QString RedemptionCore::normalizeSymbol(const QString &value, QString *error) {
    QString symbol = value.trimmed().toUpper();
    if (symbol.endsWith(QStringLiteral(".SZ"))) symbol.chop(3);
    static const QRegularExpression pattern(QStringLiteral("^[0-9]{6}$"));
    if (!pattern.match(symbol).hasMatch()) {
        if (error) *error = QStringLiteral("申赎标的必须是 6 位深圳证券代码");
        return {};
    }
    return symbol + QStringLiteral(".SZ");
}

ScheduleDecision RedemptionCore::evaluateSchedule(const QDateTime &utcNow) {
    ScheduleDecision result;
    result.localNow = utcNow.toTimeZone(kShanghai);
    const int weekday = result.localNow.date().dayOfWeek();
    result.businessDay = weekday >= 1 && weekday <= 5;
    const QTime now = result.localNow.time();
    result.pcfWindow = result.businessDay && now >= QTime(8, 30) && now <= QTime(23, 0);
    result.resetDue = result.businessDay && now >= QTime(9, 0);
    result.windLaunchDue = result.businessDay && now >= QTime(9, 10) && now < QTime(15, 0);
    result.warmupDue = result.businessDay && now >= QTime(9, 15, 5) && now < QTime(9, 15, 30);
    result.monitoringDesired = result.businessDay && now >= QTime(9, 15, 30) && now < QTime(15, 0);
    result.shutdownDue = result.businessDay && now >= QTime(15, 0);

    if (!result.businessDay) result.phase = QStringLiteral("weekend");
    else if (now < QTime(8, 30)) result.phase = QStringLiteral("overnight");
    else if (now < QTime(9, 0)) result.phase = QStringLiteral("pcf_prefetch");
    else if (now < QTime(9, 10)) result.phase = QStringLiteral("daily_reset");
    else if (now < QTime(9, 15, 5)) result.phase = QStringLiteral("wind_start");
    else if (now < QTime(9, 15, 30)) result.phase = QStringLiteral("tbapi_warmup");
    else if (now < QTime(15, 0)) result.phase = QStringLiteral("monitoring");
    else if (now <= QTime(23, 0)) result.phase = QStringLiteral("closed_pcf_cache");
    else result.phase = QStringLiteral("closed");

    QList<QDateTime> boundaries;
    QDate day = result.localNow.date();
    for (int offset = 0; offset < 8; ++offset) {
        const QDate candidate = day.addDays(offset);
        if (candidate.dayOfWeek() > 5) continue;
        for (const QTime &time : {QTime(8, 30), QTime(9, 0), QTime(9, 10),
                                  QTime(9, 15, 5), QTime(9, 15, 30),
                                  QTime(15, 0), QTime(23, 0, 1)}) {
            const QDateTime boundary = localBoundary(candidate, time);
            if (boundary > result.localNow) boundaries.append(boundary);
        }
    }
    if (!boundaries.isEmpty()) {
        std::sort(boundaries.begin(), boundaries.end());
        result.nextTransitionUtc = boundaries.first().toUTC();
    }
    return result;
}

QJsonObject RedemptionCore::canonicalCapture(const QJsonObject &payload,
                                              QString *error) {
    const QString windcode = normalizeSymbol(
        payload.value(QStringLiteral("windcode")).toString(
            payload.value(QStringLiteral("symbol")).toString()), error);
    if (windcode.isEmpty()) return {};
    QJsonObject values = payload.value(QStringLiteral("values")).toObject();
    if (values.isEmpty()) {
        const QJsonArray rows = payload.value(QStringLiteral("rows")).toArray();
        if (rows.size() != 1 || !rows.first().isObject()) {
            if (error) *error = QStringLiteral("Wind 捕获必须包含唯一数据行或 values 对象");
            return {};
        }
        values = rows.first().toObject();
    }
    QJsonObject resultValues;
    for (const QString &field : {QStringLiteral("etfbuynumber"),
                                 QStringLiteral("etfbuyamount"),
                                 QStringLiteral("etfsellnumber"),
                                 QStringLiteral("etfsellamount")}) {
        double number = 0;
        if (!finiteNumber(values.value(field), &number) || number < 0.0) {
            if (error) *error = QStringLiteral("Wind 字段 %1 缺失、非数值或为负数").arg(field);
            return {};
        }
        resultValues.insert(field, number);
    }
    resultValues.insert(QStringLiteral("netamount"),
                        resultValues.value(QStringLiteral("etfbuyamount")).toDouble()
                            - resultValues.value(QStringLiteral("etfsellamount")).toDouble());
    QDateTime observed = QDateTime::fromString(
        payload.value(QStringLiteral("observed_at")).toString(), Qt::ISODateWithMs);
    if (!observed.isValid()) {
        observed = QDateTime::fromString(
            payload.value(QStringLiteral("observed_at")).toString(), Qt::ISODate);
    }
    if (!observed.isValid()) observed = QDateTime::currentDateTimeUtc();
    return {{QStringLiteral("symbol"), displaySymbol(windcode)},
            {QStringLiteral("windcode"), windcode},
            {QStringLiteral("values"), resultValues},
            {QStringLiteral("observed_at"), observed.toUTC().toString(Qt::ISODateWithMs)},
            {QStringLiteral("sub_id"), payload.value(QStringLiteral("sub_id"))},
            {QStringLiteral("callback_seq"), payload.value(QStringLiteral("callback_seq"))}};
}

QJsonArray RedemptionCore::changeDetails(const QJsonObject &previous,
                                         const QJsonObject &current) {
    QJsonArray changes;
    const QList<QPair<QString, QString>> fields{
        {QStringLiteral("etfbuyamount"), QStringLiteral("申购份额")},
        {QStringLiteral("etfsellamount"), QStringLiteral("赎回份额")}};
    for (const auto &[field, label] : fields) {
        if (previous.value(field) == current.value(field)) continue;
        changes.append(QJsonObject{
            {QStringLiteral("field"), field}, {QStringLiteral("label"), label},
            {QStringLiteral("old"), previous.value(field)},
            {QStringLiteral("new"), current.value(field)},
            {QStringLiteral("text"), QStringLiteral("%1 %2 → %3")
                .arg(label, shareText(previous.value(field)), shareText(current.value(field)))}});
    }
    return changes;
}

QJsonObject RedemptionCore::classifyIntradayOpportunity(
    const QJsonObject *previous, const QJsonObject &current,
    const QJsonObject &pcf, const QDate &referenceDay) {
    QJsonObject result{{QStringLiteral("kind"), QStringLiteral("baseline")},
                       {QStringLiteral("label"), QStringLiteral("等待盘中变化")},
                       {QStringLiteral("actionable"), false},
                       {QStringLiteral("net_shares"), current.value(QStringLiteral("netamount"))},
                       {QStringLiteral("basket_unit"), QJsonValue()},
                       {QStringLiteral("net_baskets"), QJsonValue()},
                       {QStringLiteral("full_baskets"), 0},
                       {QStringLiteral("buy_delta"), QJsonValue()},
                       {QStringLiteral("sell_delta"), QJsonValue()},
                       {QStringLiteral("released_capacity_shares"), QJsonValue()},
                       {QStringLiteral("reason"), QStringLiteral("当前累计申赎仅作为基准，不据此判断机会")}};
    if (!previous) return result;
    double oldBuy = 0, oldSell = 0, buy = 0, sell = 0;
    if (!finiteNumber(previous->value(QStringLiteral("etfbuyamount")), &oldBuy)
        || !finiteNumber(previous->value(QStringLiteral("etfsellamount")), &oldSell)
        || !finiteNumber(current.value(QStringLiteral("etfbuyamount")), &buy)
        || !finiteNumber(current.value(QStringLiteral("etfsellamount")), &sell)) {
        result.insert(QStringLiteral("kind"), QStringLiteral("waiting"));
        result.insert(QStringLiteral("label"), QStringLiteral("等待数据"));
        result.insert(QStringLiteral("reason"), QStringLiteral("申购或赎回份额不完整"));
        return result;
    }
    const double buyDelta = buy - oldBuy;
    const double sellDelta = sell - oldSell;
    result.insert(QStringLiteral("buy_delta"), buyDelta);
    result.insert(QStringLiteral("sell_delta"), sellDelta);
    if (buyDelta < 0 || sellDelta < 0) {
        result.insert(QStringLiteral("label"), QStringLiteral("基准已更新"));
        result.insert(QStringLiteral("reason"), QStringLiteral("累计份额发生回落，按数据重置或修正处理，不触发机会"));
        return result;
    }
    const double released = sellDelta - buyDelta;
    result.insert(QStringLiteral("released_capacity_shares"), released);
    if (qFuzzyIsNull(released)) {
        result.insert(QStringLiteral("kind"), QStringLiteral("flat"));
        result.insert(QStringLiteral("label"), QStringLiteral("盘中变化已抵消"));
        result.insert(QStringLiteral("reason"), QStringLiteral("申购与赎回份额增量相同，未形成明确的反向容量释放"));
        return result;
    }
    const bool creation = released > 0;
    const QString chinese = creation ? QStringLiteral("申购") : QStringLiteral("赎回");
    const QString source = creation ? QStringLiteral("赎回") : QStringLiteral("申购");
    const double shares = qAbs(released);
    double unit = 0;
    if (!finiteNumber(pcf.value(QStringLiteral("creation_redemption_unit")), &unit) || unit <= 0) {
        result.insert(QStringLiteral("kind"), QStringLiteral("pending"));
        result.insert(QStringLiteral("label"), QStringLiteral("盘中%1信号待确认").arg(chinese));
        result.insert(QStringLiteral("reason"), QStringLiteral("%1份额盘中净增 %2，但尚无可用 PCF")
            .arg(source, QString::number(shares, 'f', 0)));
        return result;
    }
    const double baskets = shares / unit;
    const int full = static_cast<int>(baskets);
    result.insert(QStringLiteral("basket_unit"), unit);
    result.insert(QStringLiteral("net_baskets"), creation ? baskets : -baskets);
    result.insert(QStringLiteral("full_baskets"), full);
    if (!pcfReadyToday(pcf, referenceDay)) {
        result.insert(QStringLiteral("kind"), QStringLiteral("stale"));
        result.insert(QStringLiteral("label"), QStringLiteral("盘中%1信号（PCF非当日）").arg(chinese));
        result.insert(QStringLiteral("reason"), QStringLiteral("%1份额净增 %2 篮子，可能释放%3额度，但 PCF 非当日")
            .arg(source, QString::number(baskets, 'f', 2), chinese));
        return result;
    }
    const QString allowedKey = creation ? QStringLiteral("creation_allowed")
                                        : QStringLiteral("redemption_allowed");
    if (pcf.value(allowedKey).isBool() && !pcf.value(allowedKey).toBool()) {
        result.insert(QStringLiteral("kind"), QStringLiteral("closed"));
        result.insert(QStringLiteral("label"), QStringLiteral("盘中%1信号（PCF关闭）").arg(chinese));
        result.insert(QStringLiteral("reason"), QStringLiteral("%1份额出现反向增量，但 PCF 显示%2关闭")
            .arg(source, chinese));
        return result;
    }
    if (full < 1) {
        result.insert(QStringLiteral("kind"), QStringLiteral("partial"));
        result.insert(QStringLiteral("label"), QStringLiteral("盘中%1倾向").arg(chinese));
        result.insert(QStringLiteral("reason"), QStringLiteral("%1份额净增 %2 篮子，不足一个完整篮子")
            .arg(source, QString::number(baskets, 'f', 2)));
        return result;
    }
    result.insert(QStringLiteral("kind"), creation ? QStringLiteral("creation")
                                                    : QStringLiteral("redemption"));
    result.insert(QStringLiteral("label"), QStringLiteral("盘中%1机会").arg(chinese));
    result.insert(QStringLiteral("actionable"), true);
    result.insert(QStringLiteral("reason"), QStringLiteral("%1份额净增 %2 篮子，释放%3容量（完整 %4 篮子）")
        .arg(source, QString::number(baskets, 'f', 2), chinese, QString::number(full)));
    return result;
}

QJsonObject RedemptionCore::normalizePcf(const QJsonObject &payload,
                                         const QString &symbol,
                                         const QDate &requestedDay,
                                         QString *error) {
    const QString windcode = normalizeSymbol(symbol, error);
    if (windcode.isEmpty()) return {};
    double unit = 0;
    if (!finiteNumber(payload.value(QStringLiteral("creation_redemption_unit")), &unit)
        || unit <= 0) {
        if (error) *error = QStringLiteral("PCF 缺少有效的最小申赎单位");
        return {};
    }
    const QString tradingDay = payload.value(QStringLiteral("trading_day")).toString();
    if (!QDate::fromString(tradingDay, Qt::ISODate).isValid()) {
        if (error) *error = QStringLiteral("PCF trading_day 必须是 ISO 日期");
        return {};
    }
    const QJsonArray components = payload.value(QStringLiteral("components")).toArray();
    if (components.size() > 10000) {
        if (error) *error = QStringLiteral("PCF 成分数量超过安全上限");
        return {};
    }
    QJsonObject result = payload;
    result.insert(QStringLiteral("status"),
                  tradingDay == requestedDay.toString(Qt::ISODate)
                      ? QStringLiteral("ready") : QStringLiteral("stale"));
    result.insert(QStringLiteral("symbol"), displaySymbol(windcode));
    result.insert(QStringLiteral("windcode"), windcode);
    result.insert(QStringLiteral("requested_day"), requestedDay.toString(Qt::ISODate));
    result.insert(QStringLiteral("creation_redemption_unit"), unit);
    result.insert(QStringLiteral("component_count"), components.size());
    return result;
}

} // namespace machome::redemption
