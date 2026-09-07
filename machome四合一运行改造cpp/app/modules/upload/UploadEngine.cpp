#include "UploadBusinessComponent.h"
#include "modules/upload/UploadEngine.h"
#include "modules/ibkr/IbkrBridgeCore.h"
#include "modules/upload/UploadCollectors.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonValue>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSqlError>
#include <QSqlQuery>
#include <QStandardPaths>
#include <QThread>
#include <QUrlQuery>
#include <QUuid>

#include <algorithm>
#include <cmath>

#ifndef MACHOME_NATIVE_IBKR_BRIDGE_AVAILABLE
#define MACHOME_NATIVE_IBKR_BRIDGE_AVAILABLE 0
#endif

namespace machome::upload {
namespace {

constexpr auto kShanghai = "Asia/Shanghai";

UploadJobDefinition recurring(QString id, QString title, QString source,
                              QString model, int seconds,
                              std::initializer_list<QPair<QTime, QTime>> windows,
                              QList<int> weekdays = {1, 2, 3, 4, 5})
{
    UploadJobDefinition job;
    job.id = std::move(id);
    job.displayName = std::move(title);
    job.kind = QStringLiteral("uploader");
    job.source = std::move(source);
    job.modelVersion = std::move(model);
    job.intervalSeconds = seconds;
    job.windows = windows;
    job.weekdays = std::move(weekdays);
    return job;
}

UploadJobDefinition daily(QString id, QString title, const char *timezone,
                          QTime at, int retryIntervalSeconds = 0,
                          QTime retryUntilExclusive = {},
                          QList<int> weekdays = {1, 2, 3, 4, 5, 6, 7})
{
    UploadJobDefinition job;
    job.id = std::move(id);
    job.displayName = std::move(title);
    job.kind = QStringLiteral("daily");
    job.timezone = QLatin1String(timezone);
    job.runAt = at;
    job.retryIntervalSeconds = retryIntervalSeconds;
    job.retryUntilExclusive = retryUntilExclusive;
    job.weekdays = std::move(weekdays);
    return job;
}

bool finiteNumber(const QJsonValue &value, double *number = nullptr)
{
    if (!value.isDouble() || !std::isfinite(value.toDouble())) return false;
    if (number) *number = value.toDouble();
    return true;
}

bool positiveJsonNumber(const QJsonValue &value, double *number = nullptr)
{
    bool ok = false;
    double parsed = 0;
    if (value.isDouble()) {
        parsed = value.toDouble();
        ok = std::isfinite(parsed);
    } else if (value.isString()) {
        parsed = value.toString().toDouble(&ok);
        ok = ok && std::isfinite(parsed);
    }
    if (!ok || parsed <= 0) return false;
    if (number) *number = parsed;
    return true;
}

QString ibkrMarketDataTypeName(int value)
{
    switch (value) {
    case 1: return QStringLiteral("Live");
    case 2: return QStringLiteral("Frozen");
    case 3: return QStringLiteral("Delayed");
    case 4: return QStringLiteral("DelayedFrozen");
    default: return QStringLiteral("Unknown");
    }
}

QDateTime parseTimestamp(const QJsonValue &value);
QJsonObject canonicalValuationInput(const QJsonObject &input);
bool positiveNumber(const QJsonObject &object, const QString &key,
                    double *number = nullptr);

QString inferredValuationKind(const QJsonObject &input)
{
    const QString explicitKind = input.value(QStringLiteral("valuation_kind"))
                                     .toString().trimmed();
    if (!explicitKind.isEmpty()) return explicitKind;
    const QString symbol = UploadEngine::normalizedSymbol(
        input.value(QStringLiteral("symbol")).toString());
    const QString model = input.value(QStringLiteral("model_version")).toString();
    if (symbol == QStringLiteral("SZ161226")) return QStringLiteral("silver_settlement");
    if (symbol == QStringLiteral("SZ162411")) return QStringLiteral("lof_weighted_anchor");
    if (symbol == QStringLiteral("SZ164824")) return QStringLiteral("india_t2_multimarket");
    if (model.contains(QStringLiteral("full-cash-substitution")))
        return QStringLiteral("full_cash_substitution_pcf");
    if (model.contains(QStringLiteral("nq-cfets"))) return QStringLiteral("nq_proxy");
    if (model.contains(QStringLiteral("es-cfets"))) return QStringLiteral("es_proxy");
    if (model.contains(QStringLiteral("n225m-cfets"))) return QStringLiteral("n225m_proxy");
    if (model.contains(QStringLiteral("fdxm-cfets"))) return QStringLiteral("dax_proxy");
    if (model.contains(QStringLiteral("xop-cfets"))) return QStringLiteral("xop_proxy");
    return {};
}

QPair<QString, QString> productionModel(const QString &symbol)
{
    static const QSet<QString> nq{QStringLiteral("SH513100"), QStringLiteral("SH513110"),
        QStringLiteral("SH513300"), QStringLiteral("SH513390"), QStringLiteral("SH513870"),
        QStringLiteral("SZ159501"), QStringLiteral("SZ159513"), QStringLiteral("SZ159632"),
        QStringLiteral("SZ159659"), QStringLiteral("SZ159660"), QStringLiteral("SZ159696"),
        QStringLiteral("SZ159941")};
    static const QSet<QString> es{QStringLiteral("SH513500"), QStringLiteral("SH513650"),
        QStringLiteral("SZ159612"), QStringLiteral("SZ159655")};
    static const QSet<QString> nikkei{QStringLiteral("SH513000"), QStringLiteral("SH513520"),
        QStringLiteral("SH513880"), QStringLiteral("SZ159866")};
    if (symbol == QStringLiteral("SZ159518"))
        return {QStringLiteral("xop_proxy"), QStringLiteral("private.total-basket.xop-cfets-pcf.v1")};
    if (symbol == QStringLiteral("SH513350"))
        return {QStringLiteral("xop_proxy"), QStringLiteral("private.total-basket.xop-cfets-pcf.sh513350.v1")};
    if (nq.contains(symbol))
        return {QStringLiteral("nq_proxy"), QStringLiteral("private.total-basket.nq-cfets-pcf.pre-scan.v1")};
    if (es.contains(symbol))
        return {QStringLiteral("es_proxy"), QStringLiteral("private.total-basket.es-cfets-pcf.pre-scan.v1")};
    if (nikkei.contains(symbol))
        return {QStringLiteral("n225m_proxy"), QStringLiteral("private.total-basket.n225m-cfets-pcf.pre-scan.v1")};
    if (symbol == QStringLiteral("SH513030") || symbol == QStringLiteral("SZ159561"))
        return {QStringLiteral("dax_proxy"), QStringLiteral("private.total-basket.fdxm-cfets-pcf.xetra-1735-anchor.pre-scan.v2")};
    if (symbol == QStringLiteral("SZ159605"))
        return {QStringLiteral("full_cash_substitution_pcf"), QStringLiteral("private.full-cash-substitution.multi-market-pcf.v1")};
    if (symbol == QStringLiteral("SZ159607"))
        return {QStringLiteral("full_cash_substitution_pcf"), QStringLiteral("private.full-cash-substitution.multi-market-pcf.159607.v1")};
    if (symbol == QStringLiteral("SH513050"))
        return {QStringLiteral("full_cash_substitution_pcf"), QStringLiteral("private.full-cash-substitution.multi-market-pcf.513050.v1")};
    if (symbol == QStringLiteral("SH513220"))
        return {QStringLiteral("full_cash_substitution_pcf"), QStringLiteral("private.full-cash-substitution.multi-market-pcf.513220.v1")};
    if (symbol == QStringLiteral("SZ164824"))
        return {QStringLiteral("india_t2_multimarket"), QStringLiteral("private.t2-multimarket.inda-safe.v1")};
    if (symbol == QStringLiteral("SZ162411"))
        return {QStringLiteral("lof_weighted_anchor"), QStringLiteral("private.weighted-nav.xop-safe.us-close.v1")};
    if (symbol == QStringLiteral("SZ161226"))
        return {QStringLiteral("silver_settlement"), QStringLiteral("private.cn-future.ag-settlement.v1")};
    return {};
}

double expectedRedemptionUnit(const QString &symbol)
{
    if (symbol == QStringLiteral("SH513300")) return 750000.0;
    if (symbol == QStringLiteral("SZ159941")) return 1300000.0;
    if (symbol == QStringLiteral("SH513000") || symbol == QStringLiteral("SH513520")
        || symbol == QStringLiteral("SH513880") || symbol == QStringLiteral("SZ159866")
        || symbol == QStringLiteral("SH513030")) return 500000.0;
    if (!productionModel(symbol).first.isEmpty()
        && symbol != QStringLiteral("SZ164824") && symbol != QStringLiteral("SZ162411")
        && symbol != QStringLiteral("SZ161226")) return 1000000.0;
    return 0.0;
}

int expectedComponentCount(const QString &symbol)
{
    if (symbol == QStringLiteral("SZ159518")) return 51;
    if (symbol == QStringLiteral("SZ159605") || symbol == QStringLiteral("SZ159607")
        || symbol == QStringLiteral("SH513220")) return 30;
    if (symbol == QStringLiteral("SH513050")) return 35;
    if (symbol == QStringLiteral("SH513000") || symbol == QStringLiteral("SH513520")
        || symbol == QStringLiteral("SH513880") || symbol == QStringLiteral("SZ159866")) return 1;
    if (symbol == QStringLiteral("SH513030") || symbol == QStringLiteral("SZ159561")) return 40;
    return 0;
}

QString referenceSymbolForKind(const QString &kind)
{
    if (kind == QStringLiteral("xop_proxy")
        || kind == QStringLiteral("lof_weighted_anchor")) return QStringLiteral("XOP");
    if (kind == QStringLiteral("nq_proxy")) return QStringLiteral("NQ");
    if (kind == QStringLiteral("es_proxy")) return QStringLiteral("ES");
    if (kind == QStringLiteral("n225m_proxy")) return QStringLiteral("N225M");
    if (kind == QStringLiteral("dax_proxy")) return QStringLiteral("DAX");
    if (kind == QStringLiteral("india_t2_multimarket")) return QStringLiteral("INDA");
    return {};
}

bool validSha256(const QString &value)
{
    static const QRegularExpression expression(QStringLiteral("^[0-9a-fA-F]{64}$"));
    return expression.match(value).hasMatch();
}

bool validDatedFx(const QJsonObject &fx, const QString &pair)
{
    const QString source = fx.value(QStringLiteral("source")).toString();
    const QString quoteTime = fx.value(QStringLiteral("quote_time")).toString();
    return fx.value(QStringLiteral("pair")).toString() == pair
        && positiveNumber(fx, QStringLiteral("rate"))
        && QDate::fromString(fx.value(QStringLiteral("trading_day")).toString(),
                             Qt::ISODate).isValid()
        && !quoteTime.isEmpty() && !source.isEmpty()
        && parseTimestamp(fx.value(QStringLiteral("fetched_at"))).isValid();
}

bool validateProductionWireMetadata(const QJsonObject &raw, QString *error)
{
    const QJsonObject normalized = canonicalValuationInput(raw);
    const QString symbol = UploadEngine::normalizedSymbol(
        normalized.value(QStringLiteral("symbol")).toString());
    const auto definition = productionModel(symbol);
    const QString kind = normalized.value(QStringLiteral("valuation_kind")).toString();
    auto fail = [error](const QString &message) {
        if (error) *error = message;
        return false;
    };
    if (definition.first.isEmpty() || kind != definition.first
        || normalized.value(QStringLiteral("model_version")).toString() != definition.second)
        return fail(QStringLiteral("标的/模型不在生产白名单"));
    if (raw.value(QStringLiteral("schema_version")).toInt(-1) != 1
        || raw.value(QStringLiteral("source")).toString().trimmed().isEmpty()
        || !parseTimestamp(raw.value(QStringLiteral("generated_at"))).isValid())
        return fail(QStringLiteral("schema_version/source/generated_at 无效"));

    const QJsonObject reference = raw.value(QStringLiteral("ib")).isObject()
        ? raw.value(QStringLiteral("ib")).toObject()
        : raw.value(QStringLiteral("reference")).toObject();
    if (kind != QStringLiteral("silver_settlement")
        && (reference.value(QStringLiteral("symbol")).toString().toUpper()
                != referenceSymbolForKind(kind)
            || !positiveNumber(reference, QStringLiteral("bid"))
            || !positiveNumber(reference, QStringLiteral("ask"))
            || reference.value(QStringLiteral("ask")).toDouble()
                < reference.value(QStringLiteral("bid")).toDouble()
            || reference.value(QStringLiteral("source")).toString().trimmed().isEmpty()
            || reference.value(QStringLiteral("market_data_type")).toString().trimmed().isEmpty()
            || !parseTimestamp(reference.value(QStringLiteral("observed_at"))).isValid()))
        return fail(QStringLiteral("IB 行情标的/买卖盘/来源/时间不符合生产合同"));

    if (kind == QStringLiteral("lof_weighted_anchor")) {
        if (raw.contains(QStringLiteral("pcf")) || raw.contains(QStringLiteral("fx"))
            || raw.contains(QStringLiteral("india")) || raw.contains(QStringLiteral("silver")))
            return fail(QStringLiteral("162411 不得夹带 PCF/FX/India/Silver 字段"));
        const QJsonObject lof = raw.value(QStringLiteral("lof")).toObject();
        if (!QDate::fromString(lof.value(QStringLiteral("base_nav_date")).toString(),
                               Qt::ISODate).isValid()
            || lof.value(QStringLiteral("base_nav_source")).toString().isEmpty()
            || !validDatedFx(lof.value(QStringLiteral("base_fx")).toObject(),
                             QStringLiteral("USD/CNY"))
            || !validDatedFx(lof.value(QStringLiteral("current_fx")).toObject(),
                             QStringLiteral("USD/CNY")))
            return fail(QStringLiteral("162411 基准日/NAV/SAFE 元数据不完整"));
        return true;
    }
    if (kind == QStringLiteral("india_t2_multimarket")) {
        if (raw.contains(QStringLiteral("pcf")) || raw.contains(QStringLiteral("fx"))
            || raw.contains(QStringLiteral("lof")) || raw.contains(QStringLiteral("silver")))
            return fail(QStringLiteral("164824 不得夹带 PCF/FX/LOF/Silver 字段"));
        const QJsonObject india = raw.value(QStringLiteral("india")).toObject();
        if (!QDate::fromString(india.value(QStringLiteral("base_nav_date")).toString(),
                               Qt::ISODate).isValid()
            || !QDate::fromString(india.value(QStringLiteral("portfolio_as_of")).toString(),
                                  Qt::ISODate).isValid()
            || india.value(QStringLiteral("portfolio_source")).toString().isEmpty()
            || !validDatedFx(india.value(QStringLiteral("base_fx")).toObject(),
                             QStringLiteral("USD/CNY"))
            || !validDatedFx(india.value(QStringLiteral("current_fx")).toObject(),
                             QStringLiteral("USD/CNY")))
            return fail(QStringLiteral("164824 T-2 NAV/组合/SAFE 元数据不完整"));
        return true;
    }
    if (kind == QStringLiteral("silver_settlement")) {
        if (raw.contains(QStringLiteral("pcf")) || raw.contains(QStringLiteral("fx"))
            || raw.contains(QStringLiteral("ib")) || raw.contains(QStringLiteral("reference"))
            || raw.contains(QStringLiteral("lof")) || raw.contains(QStringLiteral("india")))
            return fail(QStringLiteral("161226 不得夹带 PCF/FX/IB/LOF/India 字段"));
        return true;
    }

    const QJsonObject pcf = raw.value(QStringLiteral("pcf")).toObject();
    const QString securityId = symbol.mid(2);
    const int componentCount = pcf.value(QStringLiteral("component_count")).toInt();
    if (pcf.value(QStringLiteral("security_id")).toString() != securityId
        || !QDate::fromString(pcf.value(QStringLiteral("trading_day")).toString(),
                              Qt::ISODate).isValid()
        || pcf.value(QStringLiteral("redemption")).toString().size() != 1
        || !QStringList{QStringLiteral("Y"), QStringLiteral("N")}.contains(
               pcf.value(QStringLiteral("redemption")).toString())
        || !positiveNumber(pcf, QStringLiteral("creation_redemption_unit"))
        || !finiteNumber(pcf.value(QStringLiteral("estimate_cash_component_cny")) )
        || componentCount <= 0
        || pcf.value(QStringLiteral("source_url")).toString().trimmed().isEmpty()
        || !validSha256(pcf.value(QStringLiteral("sha256")).toString()))
        return fail(QStringLiteral("PCF security_id/日期/单位/cash/来源摘要不完整"));
    const double expectedUnit = expectedRedemptionUnit(symbol);
    if (expectedUnit > 0
        && qAbs(pcf.value(QStringLiteral("creation_redemption_unit")).toDouble()
                - expectedUnit) > 0.001)
        return fail(QStringLiteral("PCF 申购赎回单位与基金定义不符"));

    if (kind == QStringLiteral("full_cash_substitution_pcf")) {
        if (!QStringList{QStringLiteral("Y"), QStringLiteral("N")}.contains(
                pcf.value(QStringLiteral("creation")).toString()))
            return fail(QStringLiteral("全现金替代 PCF creation 必须为 Y/N"));
        const QJsonArray components = pcf.value(QStringLiteral("components")).toArray();
        const QJsonArray quotes = raw.value(QStringLiteral("market_quotes")).toArray();
        if (components.size() != componentCount || quotes.size() != componentCount)
            return fail(QStringLiteral("全现金替代成分/行情数与 component_count 不符"));
        QSet<QString> componentKeys;
        QSet<QString> requiredPairs;
        for (const auto &value : components) {
            const QJsonObject item = value.toObject();
            const QString market = item.value(QStringLiteral("market")).toString();
            const QString currency = item.value(QStringLiteral("currency")).toString();
            const QString key = market + u':' + item.value(QStringLiteral("symbol")).toString();
            const bool venue = (market == QStringLiteral("US") && currency == QStringLiteral("USD"))
                || (market == QStringLiteral("HK") && currency == QStringLiteral("HKD"))
                || (market == QStringLiteral("CN") && currency == QStringLiteral("CNY"));
            if (!venue || key.endsWith(u':') || componentKeys.contains(key)
                || item.value(QStringLiteral("name")).toString().isEmpty()
                || !positiveNumber(item, QStringLiteral("quantity")))
                return fail(QStringLiteral("全现金替代成分标识/市场/币种/数量无效"));
            componentKeys.insert(key);
            if (currency != QStringLiteral("CNY")) requiredPairs.insert(currency + QStringLiteral("/CNY"));
        }
        QSet<QString> quoteKeys;
        for (const auto &value : quotes) {
            const QJsonObject item = value.toObject();
            const QString key = item.value(QStringLiteral("market")).toString() + u':'
                + item.value(QStringLiteral("symbol")).toString();
            if (!componentKeys.contains(key) || quoteKeys.contains(key)
                || !positiveNumber(item, QStringLiteral("bid"))
                || !positiveNumber(item, QStringLiteral("ask"))
                || item.value(QStringLiteral("ask")).toDouble()
                    < item.value(QStringLiteral("bid")).toDouble()
                || item.value(QStringLiteral("source")).toString().isEmpty()
                || item.value(QStringLiteral("market_data_type")).toString().isEmpty()
                || !parseTimestamp(item.value(QStringLiteral("observed_at"))).isValid())
                return fail(QStringLiteral("全现金替代成分行情与 PCF 不匹配"));
            quoteKeys.insert(key);
        }
        QSet<QString> actualPairs;
        for (const auto &value : raw.value(QStringLiteral("fx_rates")).toArray()) {
            const QJsonObject fx = value.toObject();
            const QString pair = fx.value(QStringLiteral("pair")).toString();
            if (actualPairs.contains(pair) || !validDatedFx(fx, pair))
                return fail(QStringLiteral("全现金替代汇率元数据无效或重复"));
            actualPairs.insert(pair);
        }
        if (actualPairs != requiredPairs)
            return fail(QStringLiteral("全现金替代汇率集合与 PCF 币种不匹配"));
        return true;
    }

    const QString fxPair = kind == QStringLiteral("n225m_proxy")
        ? QStringLiteral("JPY/CNY") : kind == QStringLiteral("dax_proxy")
        ? QStringLiteral("EUR/CNY") : QStringLiteral("USD/CNY");
    if (!validDatedFx(raw.value(QStringLiteral("fx")).toObject(), fxPair))
        return fail(QStringLiteral("PCF 代理汇率 pair/日期/来源/时间不完整"));
    if (kind == QStringLiteral("nq_proxy") || kind == QStringLiteral("es_proxy")
        || kind == QStringLiteral("n225m_proxy") || kind == QStringLiteral("dax_proxy")) {
        const QJsonArray components = pcf.value(QStringLiteral("components")).toArray();
        if (components.size() != componentCount)
            return fail(QStringLiteral("指数期货代理 components 数与 component_count 不符"));
        const QString market = kind == QStringLiteral("n225m_proxy") ? QStringLiteral("JP")
            : kind == QStringLiteral("dax_proxy") ? QStringLiteral("DE") : QStringLiteral("US");
        const QString currency = kind == QStringLiteral("n225m_proxy") ? QStringLiteral("JPY")
            : kind == QStringLiteral("dax_proxy") ? QStringLiteral("EUR") : QStringLiteral("USD");
        QSet<QString> seen;
        for (const auto &value : components) {
            const QJsonObject item = value.toObject();
            const QString component = item.value(QStringLiteral("symbol")).toString();
            if (component.isEmpty() || seen.contains(component)
                || item.value(QStringLiteral("name")).toString().isEmpty()
                || item.value(QStringLiteral("market")).toString() != market
                || item.value(QStringLiteral("currency")).toString() != currency
                || !positiveNumber(item, QStringLiteral("quantity")))
                return fail(QStringLiteral("指数期货代理 PCF 成分不完整或重复"));
            seen.insert(component);
        }
        const int expected = expectedComponentCount(symbol);
        if (expected > 0 && componentCount != expected
            && reference.value(QStringLiteral("market_data_type")).toString()
                != QStringLiteral("HistoricalBidAsk"))
            return fail(QStringLiteral("指数期货代理 PCF 生产成分数不符"));
    }
    return true;
}

QJsonObject canonicalValuationInput(const QJsonObject &input)
{
    QJsonObject result = input;
    const QString kind = inferredValuationKind(input);
    if (!kind.isEmpty()) result.insert(QStringLiteral("valuation_kind"), kind);
    if (!result.contains(QStringLiteral("reference"))
        && result.value(QStringLiteral("ib")).isObject())
        result.insert(QStringLiteral("reference"), result.value(QStringLiteral("ib")));
    if (result.value(QStringLiteral("fx_rates")).isArray()) {
        QJsonObject rates;
        for (const auto &value : result.value(QStringLiteral("fx_rates")).toArray()) {
            const QJsonObject rate = value.toObject();
            const QString pair = rate.value(QStringLiteral("pair")).toString().toUpper();
            const QString currency = pair.section(u'/', 0, 0);
            if (!currency.isEmpty() && rate.value(QStringLiteral("rate")).isDouble())
                rates.insert(currency, rate.value(QStringLiteral("rate")));
        }
        result.insert(QStringLiteral("fx_rates"), rates);
    }
    if (result.value(QStringLiteral("market_quotes")).isArray()
        && !result.value(QStringLiteral("quotes")).isObject()) {
        QJsonObject quotes;
        for (const auto &value : result.value(QStringLiteral("market_quotes")).toArray()) {
            const QJsonObject quote = value.toObject();
            const QString symbol = quote.value(QStringLiteral("symbol"))
                                       .toString().trimmed().toUpper();
            const QString market = quote.value(QStringLiteral("market"))
                                       .toString().trimmed().toUpper();
            if (!symbol.isEmpty()) {
                quotes.insert(symbol, quote);
                if (!market.isEmpty()) quotes.insert(market + u':' + symbol, quote);
            }
        }
        result.insert(QStringLiteral("quotes"), quotes);
    }
    return result;
}

QJsonObject quoteForComponent(const QJsonObject &quotes,
                              const QJsonObject &component)
{
    const QString symbol = component.value(QStringLiteral("symbol"))
                               .toString().trimmed().toUpper();
    const QString market = component.value(QStringLiteral("market"))
                               .toString().trimmed().toUpper();
    const QJsonObject exact = quotes.value(market + u':' + symbol).toObject();
    return exact.isEmpty() ? quotes.value(symbol).toObject() : exact;
}

bool positiveNumber(const QJsonObject &object, const QString &key,
                    double *number)
{
    double value = 0;
    if (!finiteNumber(object.value(key), &value) || value <= 0) return false;
    if (number) *number = value;
    return true;
}

bool executableDomesticQuote(const QJsonObject &input, double *bid,
                             double *ask, QString *error)
{
    const QJsonObject domestic = input.value(QStringLiteral("domestic")).toObject();
    if (!positiveNumber(domestic, QStringLiteral("bid"), bid)
        || !positiveNumber(domestic, QStringLiteral("ask"), ask)
        || *ask < *bid) {
        if (error) *error = QStringLiteral("国内标的缺少有效 bid/ask");
        return false;
    }
    return true;
}

void appendPremiums(QJsonObject *result, const QJsonObject &input,
                    double navBid, double navAsk)
{
    double bid = 0;
    double ask = 0;
    QString ignored;
    if (!executableDomesticQuote(input, &bid, &ask, &ignored)) return;
    result->insert(QStringLiteral("buy_direction_premium_rate"), ask / navBid - 1.0);
    result->insert(QStringLiteral("sell_direction_premium_rate"), bid / navAsk - 1.0);
}

bool isChinaContinuousSession(const QDateTime &utc)
{
    const QDateTime local = utc.toTimeZone(QTimeZone(kShanghai));
    if (local.date().dayOfWeek() > 5) return false;
    const QTime time = local.time();
    return (time >= QTime(9, 30) && time <= QTime(11, 30))
        || (time >= QTime(13, 0) && time < QTime(15, 0));
}

QJsonArray freshnessWarnings(const QJsonObject &input, int maxAgeSeconds,
                             bool forceIndicative = false)
{
    QJsonArray warnings;
    const QDateTime generated = parseTimestamp(input.value(QStringLiteral("generated_at")));
    const QDateTime referenceTime = input.value(QStringLiteral("as_of")).isString()
        ? parseTimestamp(input.value(QStringLiteral("as_of"))) : generated;
    if (!referenceTime.isValid() || !isChinaContinuousSession(referenceTime.toUTC()))
        warnings.append(QStringLiteral("当前不在中国连续竞价时段"));
    if (!generated.isValid() || !referenceTime.isValid()
        || qAbs(generated.secsTo(referenceTime)) > maxAgeSeconds)
        warnings.append(QStringLiteral("private 采集心跳超时"));
    const QJsonObject referenceQuote = input.value(QStringLiteral("reference")).toObject();
    const QDateTime observed = parseTimestamp(
        referenceQuote.contains(QStringLiteral("stream_checked_at"))
            ? referenceQuote.value(QStringLiteral("stream_checked_at"))
            : referenceQuote.value(QStringLiteral("observed_at")));
    if (!referenceQuote.isEmpty()
        && (!observed.isValid() || !referenceTime.isValid()
            || qAbs(observed.secsTo(referenceTime)) > maxAgeSeconds))
        warnings.append(QStringLiteral("境外行情心跳超过允许时限"));
    if (!referenceQuote.isEmpty()
        && referenceQuote.value(QStringLiteral("market_data_type")).toString()
               != QStringLiteral("Live"))
        warnings.append(QStringLiteral("IB 行情不是 Live"));
    const QJsonObject domestic = input.value(QStringLiteral("domestic")).toObject();
    const QDateTime domesticAt = parseTimestamp(domestic.value(QStringLiteral("observed_at")));
    if (domestic.isEmpty())
        warnings.append(QStringLiteral("缺少 Sina 国内五档，仅可计算 NAV"));
    else if (!domesticAt.isValid() || !referenceTime.isValid()
             || qAbs(domesticAt.secsTo(referenceTime)) > maxAgeSeconds)
        warnings.append(QStringLiteral("Sina 国内盘口超时"));
    if (forceIndicative)
        warnings.append(QStringLiteral("该模型仅供指示性估值，不用于下单"));
    return warnings;
}

void appendFxWarnings(QJsonArray *warnings, const QJsonObject &input,
                      const QJsonObject &fx, int freshnessSeconds)
{
    const QDateTime asOf = input.value(QStringLiteral("as_of")).isString()
        ? parseTimestamp(input.value(QStringLiteral("as_of")))
        : parseTimestamp(input.value(QStringLiteral("generated_at")));
    if (!asOf.isValid()) return;
    const QString day = fx.value(QStringLiteral("trading_day")).toString();
    if (!day.isEmpty()
        && day != asOf.toTimeZone(QTimeZone(kShanghai)).date().toString(Qt::ISODate))
        warnings->append(QStringLiteral("汇率使用回退交易日"));
    const QDateTime observed = parseTimestamp(fx.value(QStringLiteral("observed_at")));
    if (fx.contains(QStringLiteral("observed_at"))
        && (!observed.isValid() || qAbs(observed.secsTo(asOf)) > freshnessSeconds))
        warnings->append(QStringLiteral("汇率采集心跳超时"));
}

QJsonObject resultEnvelope(const QJsonObject &input, double navBid,
                           double navAsk, const QString &formula,
                           const QJsonArray &warnings = {})
{
    QJsonObject result{{QStringLiteral("schema_version"), 1},
                       {QStringLiteral("symbol"), input.value(QStringLiteral("symbol"))},
                       {QStringLiteral("model_version"), input.value(QStringLiteral("model_version"))},
                       {QStringLiteral("valuation_kind"), input.value(QStringLiteral("valuation_kind"))},
                       {QStringLiteral("basket_bid_nav"), navBid},
                       {QStringLiteral("basket_ask_nav"), navAsk},
                       {QStringLiteral("spread"), navAsk - navBid},
                       {QStringLiteral("formula"), formula},
                       {QStringLiteral("warnings"), warnings},
                       {QStringLiteral("actionable"), warnings.isEmpty()},
                       {QStringLiteral("source"), input.value(QStringLiteral("source"))},
                       {QStringLiteral("generated_at"), input.value(QStringLiteral("generated_at"))}};
    appendPremiums(&result, input, navBid, navAsk);
    return result;
}

QString valueString(const QJsonObject &object, const QString &name)
{
    return object.value(name).toString().trimmed();
}

QDateTime parseTimestamp(const QJsonValue &value)
{
    if (!value.isString()) return {};
    QDateTime result = QDateTime::fromString(value.toString(), Qt::ISODateWithMs);
    if (!result.isValid()) result = QDateTime::fromString(value.toString(), Qt::ISODate);
    return result;
}

QString sha256(const QByteArray &bytes)
{
    return QString::fromLatin1(QCryptographicHash::hash(bytes,
                                                        QCryptographicHash::Sha256)
                                  .toHex());
}

bool isWeekday(const UploadJobDefinition &job, const QDate &date)
{
    return job.weekdays.contains(date.dayOfWeek());
}

QDateTime localOccurrence(const UploadJobDefinition &job, const QDate &date,
                          const QTime &time)
{
    const QTimeZone zone(job.timezone.toUtf8());
    return QDateTime(date, time, zone);
}

QString defaultDataRoot()
{
    const QString base = QStandardPaths::writableLocation(
        QStandardPaths::GenericDataLocation);
    return QDir(base).filePath(QStringLiteral("MachomeHub/data/upload"));
}

double cashComponent(const QJsonObject &pcf, bool *ok = nullptr)
{
    const QString key = pcf.contains(QStringLiteral("estimate_cash_component_cny"))
        ? QStringLiteral("estimate_cash_component_cny")
        : QStringLiteral("estimate_cash_component");
    double value = 0;
    const bool valid = finiteNumber(pcf.value(key), &value);
    if (ok) *ok = valid;
    return value;
}

QJsonArray proxyWarnings(const QJsonObject &input, int maxAgeSeconds,
                         bool preScan)
{
    QJsonArray warnings = freshnessWarnings(input, maxAgeSeconds, preScan);
    const QJsonObject pcf = input.value(QStringLiteral("pcf")).toObject();
    const QDateTime generated = parseTimestamp(input.value(QStringLiteral("generated_at")));
    if (generated.isValid()
        && pcf.value(QStringLiteral("trading_day")).toString()
            != generated.toTimeZone(QTimeZone(kShanghai)).date().toString(Qt::ISODate))
        warnings.append(QStringLiteral("PCF 不是当前交易日"));
    if (pcf.value(QStringLiteral("redemption")).toString(QStringLiteral("Y"))
        != QStringLiteral("Y"))
        warnings.append(QStringLiteral("当日 PCF 不允许赎回"));
    if ((input.value(QStringLiteral("valuation_kind")).toString()
             == QStringLiteral("full_cash_substitution_pcf"))
        && pcf.value(QStringLiteral("creation")).toString(QStringLiteral("Y"))
            != QStringLiteral("Y"))
        warnings.append(QStringLiteral("当日 PCF 不允许申购"));
    appendFxWarnings(&warnings, input, input.value(QStringLiteral("fx")).toObject(), 180);
    return warnings;
}

QJsonObject calculateProxy(const QJsonObject &input, QString *error)
{
    const QJsonObject pcf = input.value(QStringLiteral("pcf")).toObject();
    const QJsonObject reference = input.value(QStringLiteral("reference")).toObject();
    const QJsonObject fx = input.value(QStringLiteral("fx")).toObject();
    double unit = 0, coefficient = 0, bid = 0, ask = 0, rate = 0;
    const QString fundSymbol = UploadEngine::normalizedSymbol(
        input.value(QStringLiteral("symbol")).toString());
    if (fundSymbol == QStringLiteral("SZ159518")) coefficient = 996.0;
    else if (fundSymbol == QStringLiteral("SH513350")) coefficient = 1046.0;
    else positiveNumber(pcf, QStringLiteral("xop_equivalent_shares"), &coefficient);
    bool cashOk = false;
    const double cash = cashComponent(pcf, &cashOk);
    if (!positiveNumber(pcf, QStringLiteral("creation_redemption_unit"), &unit)
        || coefficient <= 0
        || !positiveNumber(reference, QStringLiteral("bid"), &bid)
        || !positiveNumber(reference, QStringLiteral("ask"), &ask) || ask < bid
        || !positiveNumber(fx, QStringLiteral("rate"), &rate) || !cashOk) {
        if (error) *error = QStringLiteral("PCF 代理估值的 unit/cash/coefficient/reference/fx 无效");
        return {};
    }
    const double expectedUnit = expectedRedemptionUnit(fundSymbol);
    const int expectedCount = expectedComponentCount(fundSymbol);
    if ((expectedUnit > 0 && qAbs(unit - expectedUnit) > 0.001)
        || (expectedCount > 0 && pcf.contains(QStringLiteral("component_count"))
            && pcf.value(QStringLiteral("component_count")).toInt() != expectedCount)) {
        if (error) *error = QStringLiteral("PCF 申购赎回单位或成分数与基金定义不符");
        return {};
    }
    const QString kind = input.value(QStringLiteral("valuation_kind")).toString();
    double multiplier = 1.0;
    QString formula = QStringLiteral("XOP Bid/Ask × CFETS USD/CNY + EstimateCashComponent");
    bool preScan = false;
    if (kind == QStringLiteral("nq_proxy")) {
        multiplier = 20.0;
        formula = QStringLiteral("PCF/NAV 校准 NQ 合约 × 20 USD/点 × NQ Bid/Ask × CFETS USD/CNY + EstimateCashComponent（预扫描）");
        preScan = true;
    } else if (kind == QStringLiteral("es_proxy")) {
        multiplier = 50.0;
        formula = QStringLiteral("PCF/NAV 校准 ES 合约 × 50 USD/点 × ES Bid/Ask × CFETS USD/CNY + EstimateCashComponent（预扫描）");
        preScan = true;
    } else if (kind == QStringLiteral("n225m_proxy")) {
        multiplier = 100.0;
        formula = QStringLiteral("PCF/NAV 校准 N225M 合约 × 100 JPY/点 × N225M Bid/Ask × CFETS JPY/CNY + EstimateCashComponent（预扫描）");
        preScan = true;
    } else if (kind == QStringLiteral("dax_proxy")) {
        multiplier = 5.0;
        formula = QStringLiteral("Xetra 17:35 锚定 FDXM 合约 × 5 EUR/点 × DAX Bid/Ask × CFETS EUR/CNY + EstimateCashComponent（预扫描）");
        preScan = true;
    } else if (kind != QStringLiteral("xop_proxy")) {
        if (error) *error = QStringLiteral("不支持的 PCF 代理模型");
        return {};
    }
    const double stockBid = coefficient * multiplier * bid * rate;
    const double stockAsk = coefficient * multiplier * ask * rate;
    const double navBid = (stockBid + cash) / unit;
    const double navAsk = (stockAsk + cash) / unit;
    if (!(navBid > 0) || navAsk < navBid) {
        if (error) *error = QStringLiteral("PCF 代理估值结果无效");
        return {};
    }
    QJsonObject result = resultEnvelope(input, navBid, navAsk, formula,
                                        proxyWarnings(input, 15, preScan));
    result.insert(QStringLiteral("redemption_unit"), unit);
    result.insert(QStringLiteral("reference_equivalent"), coefficient);
    result.insert(QStringLiteral("contract_multiplier"), multiplier);
    result.insert(QStringLiteral("stock_component_bid_cny"), stockBid);
    result.insert(QStringLiteral("stock_component_ask_cny"), stockAsk);
    result.insert(QStringLiteral("estimate_cash_component_cny"), cash);
    return result;
}

QJsonObject calculateWeightedAnchor(const QJsonObject &input, QString *error)
{
    const QJsonObject lof = input.value(QStringLiteral("lof")).toObject();
    const QJsonObject baseReference = lof.value(QStringLiteral("base_reference")).toObject();
    const QJsonObject baseFx = lof.value(QStringLiteral("base_fx")).toObject();
    const QJsonObject currentFx = lof.value(QStringLiteral("current_fx")).toObject();
    const QJsonObject ratioObject = lof.value(QStringLiteral("effective_ratio")).toObject();
    const QJsonObject reference = input.value(QStringLiteral("reference")).toObject();
    double baseNav = 0, anchor = 0, baseRate = 0, currentRate = 0;
    double ratio = 0, bid = 0, ask = 0;
    if (!positiveNumber(lof, QStringLiteral("base_nav"), &baseNav)
        || !positiveNumber(baseReference, QStringLiteral("price"), &anchor)
        || !positiveNumber(baseFx, QStringLiteral("rate"), &baseRate)
        || !positiveNumber(currentFx, QStringLiteral("rate"), &currentRate)
        || !finiteNumber(ratioObject.value(QStringLiteral("value")), &ratio)
        || ratio <= 0 || ratio > 1
        || !positiveNumber(reference, QStringLiteral("bid"), &bid)
        || !positiveNumber(reference, QStringLiteral("ask"), &ask) || ask < bid) {
        if (error) *error = QStringLiteral("162411 收盘锚点输入不完整");
        return {};
    }
    const double staticValue = baseNav * (1.0 - ratio);
    const double riskBid = baseNav * ratio * (bid / anchor) * (currentRate / baseRate);
    const double riskAsk = baseNav * ratio * (ask / anchor) * (currentRate / baseRate);
    QJsonArray warnings = freshnessWarnings(input, 15, true);
    appendFxWarnings(&warnings, input, currentFx, 180);
    QJsonObject result = resultEnvelope(
        input, staticValue + riskBid, staticValue + riskAsk,
        QStringLiteral("基准单位净值 × [(1−实际有效仓位) + 实际有效仓位 × (当前 XOP Bid/Ask ÷ 基准日 XOP 美东16:00常规收盘价) × (T日 SAFE USD/CNY ÷ 基准日 SAFE USD/CNY)]"),
        warnings);
    result.insert(QStringLiteral("static_value"), staticValue);
    result.insert(QStringLiteral("risk_bid"), riskBid);
    result.insert(QStringLiteral("risk_ask"), riskAsk);
    result.insert(QStringLiteral("fx_multiplier"), currentRate / baseRate);
    result.insert(QStringLiteral("effective_ratio"), ratio);
    return result;
}

QJsonObject indiaVariant(const QJsonObject &input, const QJsonObject &india,
                         double anchor, double bid, double ask,
                         const QString &formula, QString *error)
{
    const QJsonObject baseFx = india.value(QStringLiteral("base_fx")).toObject();
    const QJsonObject currentFx = india.value(QStringLiteral("current_fx")).toObject();
    double baseNav = 0, investment = 0, staticRatio = 0, baseRate = 0, currentRate = 0;
    if (!positiveNumber(india, QStringLiteral("base_nav"), &baseNav)
        || !positiveNumber(india, QStringLiteral("investment_ratio"), &investment)
        || !finiteNumber(india.value(QStringLiteral("static_ratio")), &staticRatio)
        || staticRatio < 0 || qAbs(investment + staticRatio - 1.0) > 1e-9
        || !positiveNumber(baseFx, QStringLiteral("rate"), &baseRate)
        || !positiveNumber(currentFx, QStringLiteral("rate"), &currentRate)
        || !(anchor > 0) || !(bid > 0) || ask < bid) {
        if (error) *error = QStringLiteral("164824 T-2 多市场输入无效");
        return {};
    }
    const double staticValue = baseNav * staticRatio;
    const double riskBid = baseNav * investment * (bid / anchor) * (currentRate / baseRate);
    const double riskAsk = baseNav * investment * (ask / anchor) * (currentRate / baseRate);
    QJsonArray warnings = freshnessWarnings(input, 15, true);
    appendFxWarnings(&warnings, input, currentFx, 180);
    QJsonObject result = resultEnvelope(input, staticValue + riskBid,
                                        staticValue + riskAsk, formula, warnings);
    result.insert(QStringLiteral("anchor_price"), anchor);
    result.insert(QStringLiteral("static_value"), staticValue);
    result.insert(QStringLiteral("risk_bid"), riskBid);
    result.insert(QStringLiteral("risk_ask"), riskAsk);
    return result;
}

QJsonObject calculateIndia(const QJsonObject &input, QString *error)
{
    const QJsonObject india = input.value(QStringLiteral("india")).toObject();
    const QJsonObject reference = input.value(QStringLiteral("reference")).toObject();
    double bid = 0, ask = 0;
    if (!positiveNumber(reference, QStringLiteral("bid"), &bid)
        || !positiveNumber(reference, QStringLiteral("ask"), &ask) || ask < bid) {
        if (error) *error = QStringLiteral("164824 缺少 INDA Bid/Ask");
        return {};
    }
    double anchor = 0, weight = 0;
    QSet<QString> keys;
    for (const auto &value : india.value(QStringLiteral("anchors")).toArray()) {
        const QJsonObject item = value.toObject();
        const QString key = item.value(QStringLiteral("key")).toString();
        double itemWeight = 0, price = 0;
        if (key.isEmpty() || keys.contains(key)
            || !positiveNumber(item, QStringLiteral("weight"), &itemWeight)
            || !positiveNumber(item, QStringLiteral("price"), &price)) {
            if (error) *error = QStringLiteral("164824 锚点重复或无效");
            return {};
        }
        keys.insert(key);
        anchor += itemWeight * price;
        weight += itemWeight;
    }
    if (keys.size() != 4 || qAbs(weight - 1.0) > 1e-6) {
        if (error) *error = QStringLiteral("164824 必须有四个归一化市场锚点");
        return {};
    }
    QJsonObject direct = indiaVariant(
        input, india, anchor, bid, ask,
        QStringLiteral("T-2 单位净值 × [10.63% 静态 + 89.37% × (INDA Bid/Ask ÷ T-2 四市场加权 INDA 锚点) × (T日 SAFE USD/CNY ÷ T-2日 SAFE USD/CNY)]"), error);
    if (!direct.isEmpty()) direct.insert(QStringLiteral("default_variant"), QStringLiteral("direct_inda"));
    return direct;
}

QString silverContractForDate(const QDate &date)
{
    int year = date.year();
    int month = date.month();
    if (month % 2 != 0) ++month;
    else if (date.day() >= 10) month += 2;
    if (month > 12) { month -= 12; ++year; }
    return QStringLiteral("AG%1%2").arg(year % 100, 2, 10, QLatin1Char('0'))
                                  .arg(month, 2, 10, QLatin1Char('0'));
}

QJsonObject calculateSilver(const QJsonObject &input, QString *error)
{
    const QJsonObject silver = input.value(QStringLiteral("silver")).toObject();
    double baseNav = 0, previous = 0, price = 0, average = 0;
    const QDate tradingDay = QDate::fromString(
        silver.value(QStringLiteral("trading_day")).toString(), Qt::ISODate);
    const QDateTime observedAt = parseTimestamp(silver.value(QStringLiteral("observed_at")));
    if (!positiveNumber(silver, QStringLiteral("base_nav"), &baseNav)
        || !positiveNumber(silver, QStringLiteral("previous_settlement"), &previous)
        || !positiveNumber(silver, QStringLiteral("futures_price"), &price)
        || !positiveNumber(silver, QStringLiteral("intraday_average"), &average)
        || !tradingDay.isValid()
        || silver.value(QStringLiteral("previous_settlement_date")).toString()
            != silver.value(QStringLiteral("base_nav_date")).toString()
        || silver.value(QStringLiteral("contract")).toString()
            != silverContractForDate(tradingDay)
        || silver.value(QStringLiteral("contract_selection_version")).toString()
            != QStringLiteral("shfe-ag-even-month-roll-day10.v1")
        || !observedAt.isValid()
        || silver.value(QStringLiteral("source")).toString().trimmed().isEmpty()) {
        if (error) *error = QStringLiteral("161226 基准净值/结算价/合约日期无效");
        return {};
    }
    const double settlementNav = baseNav / previous * average;
    const double tradingNav = baseNav / previous * price;
    QJsonArray warnings = freshnessWarnings(input, 30, true);
    const QDateTime asOf = input.value(QStringLiteral("as_of")).isString()
        ? parseTimestamp(input.value(QStringLiteral("as_of")))
        : parseTimestamp(input.value(QStringLiteral("generated_at")));
    if (!asOf.isValid() || qAbs(observedAt.secsTo(asOf)) > 30)
        warnings.prepend(QStringLiteral("161226 AG 采集心跳超过 30 秒"));
    QJsonObject result = resultEnvelope(
        input, tradingNav, tradingNav,
        QStringLiteral("昨日单位净值 ÷ 所选 AG 合约昨日结算价 × 今日盘中累计均价 / 当前交易价"), warnings);
    result.insert(QStringLiteral("settlement_nav"), settlementNav);
    result.insert(QStringLiteral("trading_nav"), tradingNav);
    result.insert(QStringLiteral("contract"), silver.value(QStringLiteral("contract")));
    result.insert(QStringLiteral("actionable"), false);
    return result;
}

bool valuationBelongsToJob(const QString &jobId, const QJsonObject &valuation)
{
    const QString symbol = UploadEngine::normalizedSymbol(
        valuation.value(QStringLiteral("symbol")).toString());
    const QString kind = valuation.value(QStringLiteral("valuation_kind")).toString();
    if (jobId == QStringLiteral("intraday-rebuild")) return true;
    if (jobId == QStringLiteral("xop-family"))
        return kind == QStringLiteral("xop_proxy") && symbol != QStringLiteral("SH513350");
    if (jobId == QStringLiteral("513350")) return symbol == QStringLiteral("SH513350");
    if (jobId == QStringLiteral("nasdaq")) return kind == QStringLiteral("nq_proxy");
    if (jobId == QStringLiteral("sp500")) return kind == QStringLiteral("es_proxy");
    if (jobId == QStringLiteral("nikkei225")) return kind == QStringLiteral("n225m_proxy");
    if (jobId == QStringLiteral("germany")) return kind == QStringLiteral("dax_proxy");
    if (jobId == QStringLiteral("silver")) return kind == QStringLiteral("silver_settlement");
    if (jobId == QStringLiteral("164824")) return symbol == QStringLiteral("SZ164824");
    if (jobId == QStringLiteral("159605")) return symbol == QStringLiteral("SZ159605");
    if (jobId == QStringLiteral("china-internet"))
        return kind == QStringLiteral("full_cash_substitution_pcf")
            && symbol != QStringLiteral("SZ159605");
    return false;
}

QString closeMarketForJob(const QString &jobId)
{
    if (jobId == QStringLiteral("cn-close")) return QStringLiteral("cn");
    if (jobId == QStringLiteral("hk-close")) return QStringLiteral("hk");
    if (jobId == QStringLiteral("jp-close")) return QStringLiteral("jp");
    if (jobId == QStringLiteral("eu-close")) return QStringLiteral("eu");
    if (jobId == QStringLiteral("us-close")) return QStringLiteral("us");
    if (jobId == QStringLiteral("us-commodity-close"))
        return QStringLiteral("us_commodity_futures");
    return {};
}

bool quoteBelongsToCloseMarket(const QString &market, const QString &symbol,
                               const QJsonObject &quote)
{
    const QString explicitMarket = quote.value(QStringLiteral("market"))
                                       .toString().trimmed().toLower();
    if (!explicitMarket.isEmpty()) return explicitMarket == market;
    if (market == QStringLiteral("cn"))
        return symbol.startsWith(QStringLiteral("SH"))
            || symbol.startsWith(QStringLiteral("SZ"));
    if (market == QStringLiteral("hk")) return symbol.endsWith(QStringLiteral(".HK"));
    if (market == QStringLiteral("jp"))
        return symbol == QStringLiteral("N225M") || symbol == QStringLiteral("NKY");
    if (market == QStringLiteral("eu"))
        return symbol == QStringLiteral("DAX") || symbol == QStringLiteral("FDXM");
    if (market == QStringLiteral("us_commodity_futures")) {
        static const QSet<QString> commodities{
            QStringLiteral("CL"), QStringLiteral("GC"), QStringLiteral("SI"),
            QStringLiteral("HG"), QStringLiteral("AG")};
        return commodities.contains(symbol);
    }
    if (market == QStringLiteral("us"))
        return !symbol.startsWith(QStringLiteral("SH"))
            && !symbol.startsWith(QStringLiteral("SZ"))
            && !symbol.endsWith(QStringLiteral(".HK"))
            && !quoteBelongsToCloseMarket(QStringLiteral("jp"), symbol, quote)
            && !quoteBelongsToCloseMarket(QStringLiteral("eu"), symbol, quote)
            && !quoteBelongsToCloseMarket(QStringLiteral("us_commodity_futures"), symbol, quote);
    return false;
}

QJsonArray defaultPcfDefinitions()
{
    struct Spec { const char *symbol; double unit; const char *market; const char *currency; };
    const QList<Spec> specs{
        {"SZ159518",1000000,"US","USD"},
        {"SH513100",1000000,"US","USD"},{"SH513110",1000000,"US","USD"},
        {"SH513300",750000,"US","USD"},{"SH513390",1000000,"US","USD"},
        {"SH513870",1000000,"US","USD"},{"SZ159501",1000000,"US","USD"},
        {"SZ159513",1000000,"US","USD"},{"SZ159632",1000000,"US","USD"},
        {"SZ159659",1000000,"US","USD"},{"SZ159660",1000000,"US","USD"},
        {"SZ159696",1000000,"US","USD"},{"SZ159941",1300000,"US","USD"},
        {"SH513500",1000000,"US","USD"},{"SH513650",1000000,"US","USD"},
        {"SZ159612",1000000,"US","USD"},{"SZ159655",1000000,"US","USD"},
        {"SH513000",500000,"JP","JPY"},{"SH513520",500000,"JP","JPY"},
        {"SH513880",500000,"JP","JPY"},{"SZ159866",500000,"JP","JPY"},
        {"SH513030",500000,"DE","EUR"},{"SZ159561",1000000,"DE","EUR"},
        {"SZ159605",1000000,"",""},{"SZ159607",1000000,"",""},
        {"SH513050",1000000,"",""},{"SH513220",1000000,"",""}};
    QJsonArray result;
    for (const Spec &spec : specs) {
        const QString symbol = QString::fromLatin1(spec.symbol);
        const QString security = symbol.mid(2);
        const bool szse = symbol.startsWith(QStringLiteral("SZ"));
        QJsonObject definition{{"symbol",symbol},{"security_id",security},
            {"exchange",szse?"SZSE":"SSE"},{"redemption_unit",spec.unit},
            {"market",QString::fromLatin1(spec.market)},{"currency",QString::fromLatin1(spec.currency)},
            {"url",szse
                ? QStringLiteral("https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_%1_{yyyymmdd}.xml").arg(security)
                : QStringLiteral("https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=%1").arg(security)}};
        const QSet<QString> chinaInternet{QStringLiteral("SZ159605"),QStringLiteral("SZ159607"),
            QStringLiteral("SH513050"),QStringLiteral("SH513220")};
        definition.insert("allowed_sources",chinaInternet.contains(symbol)
            ? QJsonArray{"101","102","103","9999"}:QJsonArray{"9999"});
        if(symbol=="SZ159605"||symbol=="SZ159607"){
            definition.insert("expected_component_count",30);
            definition.insert("expected_market_counts",QJsonObject{{"HK",23},{"US",7}});
        }else if(symbol=="SH513050"){
            definition.insert("expected_component_count",35);
            definition.insert("expected_market_counts",QJsonObject{{"HK",25},{"US",10}});
        }else if(symbol=="SH513220"){
            definition.insert("expected_component_count",30);
            definition.insert("expected_market_counts",QJsonObject{{"CN",9},{"HK",15},{"US",6}});
        }else if(symbol=="SH513000"||symbol=="SH513520"||symbol=="SH513880"||symbol=="SZ159866"){
            definition.insert("expected_component_count",1);
        }else if(symbol=="SH513030"||symbol=="SZ159561"){
            definition.insert("expected_component_count",40);
        }
        if(szse){
            const QSet<QString> nq{QStringLiteral("SZ159501"),QStringLiteral("SZ159513"),
                QStringLiteral("SZ159632"),QStringLiteral("SZ159659"),QStringLiteral("SZ159660"),
                QStringLiteral("SZ159696"),QStringLiteral("SZ159941")};
            const QSet<QString> sp{QStringLiteral("SZ159612"),QStringLiteral("SZ159655")};
            if(nq.contains(symbol))definition.insert("expected_underlying","NDX");
            else if(sp.contains(symbol))definition.insert("expected_underlying","SPXNTR");
            else if(symbol=="SZ159866")definition.insert("expected_underlying","N225");
            else if(symbol=="SZ159561")definition.insert("expected_underlying","DAX");
        }
        result.append(definition);
    }
    result.append(QJsonObject{{"symbol","SH513350"},{"security_id","513350"},
        {"exchange","FULLGOAL_JSON"},{"redemption_unit",1000000.0},
        {"market","US"},{"currency","USD"},
        {"url","https://wap.fullgoal.com.cn/ws-business-server/fund/getFundSg?siteno=main&merchantId=&productCode=513350&tradeDate={date}"}});
    return result;
}

} // namespace

QList<UploadJobDefinition> UploadSchedule::productionJobs()
{
    const auto morningAndAfternoon = {
        qMakePair(QTime(9, 20), QTime(11, 30)),
        qMakePair(QTime(13, 1), QTime(14, 57))};
    const auto privateDay = {qMakePair(QTime(9, 0), QTime(15, 0))};
    return {
        recurring(QStringLiteral("sina-public"), QStringLiteral("Sina 公共行情"),
                  QStringLiteral("home-mac"), QStringLiteral("sina.quote.v1"),
                  5, {}, {1, 2, 3, 4, 5, 6, 7}),
        recurring(QStringLiteral("xop-family"), QStringLiteral("XOP 三基金"),
                  QStringLiteral("mac-home-private-xop-family-uploader"),
                  QStringLiteral("private.xop-family.v1"), 3, privateDay),
        recurring(QStringLiteral("nasdaq"), QStringLiteral("Nasdaq PCF/NQ"),
                  QStringLiteral("mac-home-private-nasdaq-pcf-nq-uploader"),
                  QStringLiteral("private.total-basket.nq-cfets-pcf.pre-scan.v1"),
                  3, privateDay),
        recurring(QStringLiteral("sp500"), QStringLiteral("S&P 500 PCF/ES"),
                  QStringLiteral("mac-home-private-sp500-pcf-es-uploader"),
                  QStringLiteral("private.total-basket.es-cfets-pcf.pre-scan.v1"),
                  3, privateDay),
        recurring(QStringLiteral("nikkei225"), QStringLiteral("Nikkei 225 PCF/N225M"),
                  QStringLiteral("mac-home-private-nikkei225-pcf-n225m-uploader"),
                  QStringLiteral("private.total-basket.n225m-cfets-pcf.pre-scan.v1"),
                  3, privateDay),
        recurring(QStringLiteral("germany"), QStringLiteral("DAX PCF/FDXM"),
                  QStringLiteral("mac-home-private-germany-pcf-fdxm-xetra1735-uploader"),
                  QStringLiteral("private.total-basket.fdxm-cfets-pcf.xetra-1735-anchor.pre-scan.v2"),
                  3, privateDay),
        recurring(QStringLiteral("silver"), QStringLiteral("白银 LOF"),
                  QStringLiteral("mac-home-private-161226-silver-uploader"),
                  QStringLiteral("private.cn-future.ag-settlement.v1"), 10,
                  {qMakePair(QTime(9, 15), QTime(10, 15)),
                   qMakePair(QTime(10, 30), QTime(11, 30)),
                   qMakePair(QTime(13, 30), QTime(15, 0))}),
        recurring(QStringLiteral("china-internet"), QStringLiteral("中概互联网四基金"),
                  QStringLiteral("mac-home-private-china-internet-uploader"),
                  QStringLiteral("private.full-cash-substitution.multi-market-pcf.v1"),
                  3, privateDay),
        recurring(QStringLiteral("164824"), QStringLiteral("印度 LOF T+2"),
                  QStringLiteral("mac-home-private-164824-t2-inda-uploader"),
                  QStringLiteral("private.t2-multimarket.inda-safe.v1"),
                  3, privateDay),
        recurring(QStringLiteral("159605"), QStringLiteral("159605 多市场 PCF"),
                  QStringLiteral("mac-home-private-159605-uploader"),
                  QStringLiteral("private.full-cash-substitution.multi-market-pcf.v1"),
                  3, privateDay),
        recurring(QStringLiteral("513350"), QStringLiteral("513350 XOP PCF"),
                  QStringLiteral("mac-home-private-513350-uploader"),
                  QStringLiteral("private.total-basket.xop-cfets-pcf.sh513350.v1"),
                  3, privateDay),
        recurring(QStringLiteral("intraday-rebuild"), QStringLiteral("盘中分钟重建"),
                  QStringLiteral("machome-native-intraday-rebuild"),
                  QStringLiteral("minute-rebuild.v1"), 60, morningAndAfternoon),

        daily(QStringLiteral("purchase-status"), QStringLiteral("申购赎回状态"),
              kShanghai, QTime(8, 5)),
        daily(QStringLiteral("pcf-prefetch"), QStringLiteral("PCF 预取"),
              kShanghai, QTime(8, 30), 60, QTime(15, 0),
              {1, 2, 3, 4, 5}),
        daily(QStringLiteral("safe-central-parity"), QStringLiteral("SAFE 中间价"),
              kShanghai, QTime(9, 30), 60, QTime(10, 31)),
        daily(QStringLiteral("cn-close"), QStringLiteral("A 股收盘持久化"),
              kShanghai, QTime(15, 10)),
        daily(QStringLiteral("jp-close"), QStringLiteral("日本收盘持久化"),
              "Asia/Tokyo", QTime(15, 45)),
        daily(QStringLiteral("hk-close"), QStringLiteral("香港收盘持久化"),
              "Asia/Hong_Kong", QTime(16, 15)),
        daily(QStringLiteral("eu-close"), QStringLiteral("欧洲收盘持久化"),
              "Europe/Berlin", QTime(17, 45)),
        daily(QStringLiteral("us-commodity-close"), QStringLiteral("美国商品期货收盘"),
              "America/New_York", QTime(16, 0)),
        daily(QStringLiteral("us-close"), QStringLiteral("美国收盘持久化"),
              "America/New_York", QTime(16, 20)),
        daily(QStringLiteral("eastmoney-nav"), QStringLiteral("东方财富净值"),
              kShanghai, QTime(22, 30)),
        daily(QStringLiteral("daily-calibration"), QStringLiteral("每日校准"),
              kShanghai, QTime(22, 45)),
        daily(QStringLiteral("effective-ratio-fit"), QStringLiteral("有效仓位拟合"),
              kShanghai, QTime(23, 0)),
        daily(QStringLiteral("share-history"), QStringLiteral("上交所/深交所份额历史"),
              kShanghai, QTime(23, 50)),
    };
}

ScheduleDecision UploadSchedule::evaluate(const UploadJobDefinition &job,
                                          const QDateTime &utcNow,
                                          const QDateTime &lastStartedUtc,
                                          const QDateTime &lastSuccessUtc)
{
    ScheduleDecision result;
    if (!utcNow.isValid()) {
        result.reason = QStringLiteral("invalid_clock");
        return result;
    }
    const QTimeZone zone(job.timezone.toUtf8());
    if (!zone.isValid()) {
        result.reason = QStringLiteral("invalid_timezone");
        return result;
    }
    result.localNow = utcNow.toTimeZone(zone);
    if (job.kind == QStringLiteral("uploader")) {
        if (!isWeekday(job, result.localNow.date())) {
            result.reason = QStringLiteral("weekend");
        } else if (job.windows.isEmpty()) {
            result.active = true;
            result.reason = QStringLiteral("always_on");
        } else {
            for (const auto &[start, end] : job.windows) {
                if (result.localNow.time() >= start && result.localNow.time() < end) {
                    result.active = true;
                    break;
                }
            }
            result.reason = result.active ? QStringLiteral("active_window")
                                          : QStringLiteral("outside_window");
        }
        if (result.active) {
            const qint64 elapsed = lastStartedUtc.isValid()
                ? lastStartedUtc.msecsTo(utcNow) : std::numeric_limits<qint64>::max();
            result.due = elapsed >= qMax(1, job.intervalSeconds) * 1000LL;
            const qint64 slot = result.localNow.toSecsSinceEpoch()
                / qMax(1, job.intervalSeconds);
            result.idempotencyKey = QStringLiteral("%1:%2")
                                        .arg(result.localNow.date().toString(Qt::ISODate))
                                        .arg(slot);
            result.nextRunUtc = result.due
                ? utcNow : lastStartedUtc.addSecs(qMax(1, job.intervalSeconds));
        }
        return result;
    }

    QDate candidate = result.localNow.date();
    QDateTime occurrence = localOccurrence(job, candidate, job.runAt);
    if (!isWeekday(job, candidate) || result.localNow < occurrence) {
        if (result.localNow < occurrence && isWeekday(job, candidate)) {
            result.nextRunUtc = occurrence.toUTC();
        } else {
            do candidate = candidate.addDays(1); while (!isWeekday(job, candidate));
            result.nextRunUtc = localOccurrence(job, candidate, job.runAt).toUTC();
        }
        result.reason = isWeekday(job, result.localNow.date())
            ? QStringLiteral("before_run_time") : QStringLiteral("weekend");
        return result;
    }
    result.active = true;
    result.idempotencyKey = result.localNow.date().toString(Qt::ISODate);
    const QDateTime lastLocal = lastStartedUtc.toTimeZone(zone);
    const QDateTime lastSuccessLocal = lastSuccessUtc.toTimeZone(zone);
    const bool completedToday = lastSuccessUtc.isValid()
        && lastSuccessLocal.date() == result.localNow.date();
    const bool attemptedToday = lastStartedUtc.isValid()
        && lastLocal.date() == result.localNow.date();
    const bool retryingDaily = job.retryIntervalSeconds > 0
        && job.retryUntilExclusive.isValid();
    // The legacy SAFE daily timer fired on every calendar day, while its
    // minute-by-minute ensure loop was explicitly weekday-only.
    const bool insideRetryWindow = retryingDaily
        && result.localNow.date().dayOfWeek() <= 5
        && result.localNow.time() < job.retryUntilExclusive;
    if (completedToday) {
        result.due = false;
        result.reason = QStringLiteral("already_succeeded");
    } else if (!attemptedToday) {
        result.due = true;
        result.reason = QStringLiteral("due");
    } else if (insideRetryWindow) {
        const QDateTime retryAt = lastStartedUtc.addSecs(job.retryIntervalSeconds);
        result.due = utcNow >= retryAt;
        result.nextRunUtc = result.due ? utcNow : retryAt;
        result.reason = result.due ? QStringLiteral("retry_due")
                                   : QStringLiteral("waiting_retry_interval");
    } else {
        result.due = false;
        result.reason = retryingDaily ? QStringLiteral("retry_window_closed")
                                      : QStringLiteral("already_run");
    }
    do candidate = candidate.addDays(1); while (!isWeekday(job, candidate));
    if (!result.nextRunUtc.isValid())
        result.nextRunUtc = localOccurrence(job, candidate, job.runAt).toUTC();
    return result;
}

UploadEngine::UploadEngine(QObject *parent)
    : hub::IModuleEngine(parent)
{
    collectors_ = std::make_unique<UploadCollectors>();
    scheduleTimer_.setInterval(1000);
    scheduleTimer_.setTimerType(Qt::CoarseTimer);
    connect(&scheduleTimer_, &QTimer::timeout,
            this, &UploadEngine::evaluateSchedules);
    connect(&ibkrHelper_, qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this, &UploadEngine::helperFinished);
    connect(&ibkrHelper_, &QProcess::errorOccurred,
            this, &UploadEngine::helperError);
    connect(&ibkrHelper_, &QProcess::started, this, [this] {
        QTimer::singleShot(100, this, &UploadEngine::connectIbkrSocket);
    });
    connect(&ibkrSocket_, &QLocalSocket::connected, this, [this] {
        ibkrInputBuffer_.clear();
        ibkrHandshakeComplete_ = false;
        writeIbkrRequest(QStringLiteral("hello"));
        publishEvent(QStringLiteral("upload.ibkr_socket_connected"), {});
    });
    connect(&ibkrSocket_, &QLocalSocket::readyRead,
            this, &UploadEngine::readIbkrSocket);
    connect(&ibkrSocket_, &QLocalSocket::disconnected, this, [this] {
        ibkrHandshakeComplete_ = false;
        ibkrPollTimer_.stop();
        if (running_ && ibkrHelper_.state() != QProcess::NotRunning)
            QTimer::singleShot(1000, this, &UploadEngine::connectIbkrSocket);
    });
    connect(&ibkrSocket_, &QLocalSocket::errorOccurred, this,
            [this](QLocalSocket::LocalSocketError) {
        if (!running_ || ibkrHelper_.state() == QProcess::NotRunning) return;
        publishEvent(QStringLiteral("upload.ibkr_socket_error"),
                     {{QStringLiteral("message"), ibkrSocket_.errorString()}});
        if (ibkrSocket_.state() == QLocalSocket::UnconnectedState)
            QTimer::singleShot(1000, this, &UploadEngine::connectIbkrSocket);
    });
    ibkrPollTimer_.setInterval(1000);
    ibkrPollTimer_.setTimerType(Qt::CoarseTimer);
    connect(&ibkrPollTimer_, &QTimer::timeout,
            this, &UploadEngine::pollIbkrQuotes);
}

UploadEngine::~UploadEngine()
{
    stop(hub::StopMode::Immediate);
    if (database_.isValid()) database_.close();
    const QString name = connectionName_;
    database_ = {};
    if (!name.isEmpty()) QSqlDatabase::removeDatabase(name);
}

void UploadEngine::initialize(const hub::ModuleContext &context)
{
    if (initialized_) return;
    operatingMode_ = QStringLiteral("work");
    context_ = context;
    if (context_.dataRoot.trimmed().isEmpty()) context_.dataRoot = defaultDataRoot();
    context_.dataRoot = QDir::cleanPath(context_.dataRoot);
    const QString allowedRoot = QDir::cleanPath(defaultDataRoot());
    const bool isolatedTestRoot = context_.settings.value(QStringLiteral("test_mode"))
                                      .toBool(false)
        && context_.dataRoot.startsWith(QStringLiteral("/private/tmp/"))
        && context_.dataRoot.contains(QStringLiteral("/MachomeHub/data/upload"));
    if (!isolatedTestRoot && context_.dataRoot != allowedRoot
        && !context_.dataRoot.startsWith(allowedRoot + u'/')) {
        state_ = QStringLiteral("blocked");
        lastError_ = QStringLiteral("Upload data_root 必须位于 %1").arg(allowedRoot);
        publishSnapshot();
        return;
    }
    const QString sink = context_.settings.value(QStringLiteral("sink_mode"))
                             .toString(QStringLiteral("record_only"));
    context_.recordOnly = sink == QStringLiteral("record_only");
    if (!context_.recordOnly && sink != QStringLiteral("bundled_business")) {
        state_ = QStringLiteral("blocked");
        lastError_ = QStringLiteral("当前原生 UploadEngine 仅允许 sink_mode=record_only");
        publishSnapshot();
        return;
    }
    QString error;
    if (!openRepository(&error)) {
        state_ = QStringLiteral("blocked");
        lastError_ = error;
        publishSnapshot();
        return;
    }
    for (const auto &definition : UploadSchedule::productionJobs()) {
        JobRuntime runtime;
        runtime.definition = definition;
        QSqlQuery query(database_);
        query.prepare(QStringLiteral(
            "SELECT last_started_at,last_success_at,last_failure_at,run_count,last_error "
            "FROM upload_job_state WHERE job_id=?"));
        query.addBindValue(definition.id);
        if (query.exec() && query.next()) {
            runtime.lastStartedUtc = parseTimestamp(query.value(0).toString());
            runtime.lastSuccessUtc = parseTimestamp(query.value(1).toString());
            runtime.lastFailureUtc = parseTimestamp(query.value(2).toString());
            runtime.runCount = query.value(3).toULongLong();
            runtime.lastError = query.value(4).toString();
        }
        jobs_.insert(definition.id, runtime);
    }
    if (sink == QStringLiteral("bundled_business")) {
        business_ = new UploadBusinessComponent(this);
        QString config = context_.settings.value("business_config").toString("config/upload-business.json");
        if (!QDir::isAbsolutePath(config)) config=QDir(context_.dataRoot).filePath(config);
        if (!business_->configure(context_.dataRoot,config,&error)) {
            state_=QStringLiteral("blocked");lastError_=error;delete business_;business_=nullptr;
            publishSnapshot();return;
        }
        connect(business_,&UploadBusinessComponent::changed,this,&UploadEngine::publishSnapshot);
        connect(business_,&UploadBusinessComponent::commandFinished,this,&UploadEngine::commandFinished);
    }
    initialized_ = true;
    state_ = QStringLiteral("initialized");
    publishSnapshot();
}

void UploadEngine::setHttpTransportForTest(
    std::unique_ptr<IUploadHttpTransport> transport)
{
    if (initialized_) return;
    collectors_->setTransport(std::move(transport));
}

void UploadEngine::start()
{
    if (!initialized_ || running_) return;
    running_ = true;
    state_ = QStringLiteral("warming");
    if(business_){business_->start();publishSnapshot();return;}
    startIbkrHelper();
    runStartupCatchups();
    evaluateSchedules();
    scheduleTimer_.start();
    state_ = lastError_.isEmpty() ? QStringLiteral("running")
                                  : QStringLiteral("degraded");
    publishEvent(QStringLiteral("upload.engine_started"),
                 {{QStringLiteral("record_only"), true},
                  {QStringLiteral("data_root"), context_.dataRoot}});
    publishSnapshot();
}

void UploadEngine::stop(hub::StopMode mode)
{
    Q_UNUSED(mode)
    if (!running_ && !initialized_) return;
    scheduleTimer_.stop();
    if(business_)business_->stop();
    stopIbkrHelper();
    running_ = false;
    state_ = QStringLiteral("stopped");
    if (database_.isOpen()) {
        QSqlQuery(database_).exec(QStringLiteral("PRAGMA wal_checkpoint(PASSIVE)"));
    }
    publishSnapshot();
}

void UploadEngine::submitCommand(const QString &action,
                                 const QJsonObject &arguments,
                                 const QString &commandId)
{
    if (action == QStringLiteral("set_operating_mode")) {
        if (!initialized_) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("Upload 原生模块尚未初始化"),
                                 {{QStringLiteral("code"), QStringLiteral("module_uninitialized")}});
            return;
        }
        const QString mode = arguments.value(QStringLiteral("mode")).toString();
        if (arguments.size() != 1
            || (mode != QStringLiteral("work")
                && mode != QStringLiteral("weekend_test"))) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("运行模式只能是 work 或 weekend_test"),
                                 {{QStringLiteral("code"), QStringLiteral("invalid_operating_mode")},
                                  {QStringLiteral("operating_mode"), operatingMode_}});
            return;
        }
        if (business_) {
            operatingMode_=mode;
            emit commandFinished(commandId,true,QStringLiteral("包内 Upload 保持原业务调度；控制器模式已更新"),
                {{"operating_mode",mode},{"manual_time_window_bypass",false},{"automatic_schedule_bypass",false}});
            publishSnapshot();return;
        }
        const bool changed = operatingMode_ != mode;
        operatingMode_ = mode;
        if (changed && running_
            && context_.settings.value(QStringLiteral("ibkr")).toObject()
                   .value(QStringLiteral("enabled")).toBool(false)) {
            stopIbkrHelper();
            startIbkrHelper();
        }
        const QJsonObject details{
            {QStringLiteral("operating_mode"), operatingMode_},
            {QStringLiteral("changed"), changed},
            {QStringLiteral("engine_running"), running_},
            {QStringLiteral("manual_time_window_bypass"),
             operatingMode_ == QStringLiteral("weekend_test")},
            {QStringLiteral("automatic_schedule_bypass"), false},
            {QStringLiteral("sink_mode"), QStringLiteral("record_only")}};
        publishEvent(QStringLiteral("upload.operating_mode_changed"), details);
        emit commandFinished(commandId, true,
                             operatingMode_ == QStringLiteral("weekend_test")
                                 ? QStringLiteral("已切换到周末测试模式；仅手动只读采集和 IBKR 连接解除时间窗，上传仍为 record-only")
                                 : QStringLiteral("已切换到生产工作时间模式"),
                             details);
        publishSnapshot();
        return;
    }
    if (!running_) {
        emit commandFinished(commandId, false,
                             QStringLiteral("Upload 原生模块已停止，请先在 UI 中启动模块"),
                             {{QStringLiteral("code"), QStringLiteral("module_stopped")}});
        return;
    }
    if(business_){business_->submit(action,arguments,commandId);return;}
    if (action == QStringLiteral("upload_ibkr_reconnect")) {
        QString error;
        if (!validateHelperConfiguration(&error)
            || !context_.settings.value(QStringLiteral("ibkr")).toObject()
                    .value(QStringLiteral("enabled")).toBool(false)) {
            emit commandFinished(commandId, false,
                                 error.isEmpty() ? QStringLiteral("IBKR 原生数据源未启用")
                                                 : error,
                                 snapshot());
            return;
        }
        stopIbkrHelper();
        startIbkrHelper();
        const bool started = ibkrHelper_.state() != QProcess::NotRunning;
        emit commandFinished(commandId, started,
                             started ? QStringLiteral("IBKR 原生 helper 已重启，正在按生产时间表连接 TWS")
                                     : QStringLiteral("IBKR 原生 helper 启动失败"),
                             snapshot());
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_ibkr_disconnect")) {
        stopIbkrHelper();
        emit commandFinished(commandId, true,
                             QStringLiteral("IBKR 原生 helper 已断开；Upload 其他任务继续运行"),
                             snapshot());
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_run_job")) {
        const QString id = arguments.value(QStringLiteral("job_id")).toString();
        auto found = jobs_.find(id);
        if (found == jobs_.end()) {
            emit commandFinished(commandId, false, QStringLiteral("未知 Upload job"),
                                 {{QStringLiteral("code"), QStringLiteral("unknown_job")}});
            return;
        }
        ScheduleDecision gate;
        if (!manualRunAllowed(found.value(), &gate)) {
            emit commandFinished(
                commandId, false,
                QStringLiteral("当前不在该任务的原生生产时间窗；如需非交易时段做只读测试，请先切换 weekend_test"),
                {{QStringLiteral("code"), QStringLiteral("outside_operating_window")},
                 {QStringLiteral("operating_mode"), operatingMode_},
                 {QStringLiteral("schedule_reason"), gate.reason},
                 {QStringLiteral("local_now"), iso(gate.localNow)},
                 {QStringLiteral("next_run_at"), iso(gate.nextRunUtc)},
                 {QStringLiteral("sink_mode"), QStringLiteral("record_only")}});
            return;
        }
        const QString key = arguments.value(QStringLiteral("idempotency_key"))
                                .toString(QStringLiteral("manual-%1")
                                              .arg(nowUtc().toString(Qt::ISODateWithMs)));
        QString error;
        QJsonObject details;
        const bool ok = executeJob(found.value(), key, true, &error, &details);
        emit commandFinished(commandId, ok,
                             ok ? QStringLiteral("已执行并记录（record-only）") : error,
                             details);
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_ingest_quote")) {
        QString error;
        const bool ok = persistQuote(arguments.value(QStringLiteral("quote")).toObject(), &error);
        emit commandFinished(commandId, ok,
                             ok ? QStringLiteral("行情已写入原生 Upload 仓储") : error, {});
        if (ok) publishEvent(QStringLiteral("upload.quote"),
                             arguments.value(QStringLiteral("quote")).toObject());
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_ingest_dataset")) {
        QString error;
        const QJsonObject dataset = arguments.value(QStringLiteral("dataset")).toObject();
        const bool ok = persistDataset(dataset, &error);
        emit commandFinished(commandId, ok,
                             ok ? QStringLiteral("业务数据已写入原生 Upload 仓储") : error,
                             {{QStringLiteral("kind"), dataset.value(QStringLiteral("kind"))}});
        if (ok) publishEvent(QStringLiteral("upload.dataset"), dataset);
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_ingest_valuation")) {
        QString error;
        QJsonObject valuation;
        const bool ok = persistValuation(
            arguments.value(QStringLiteral("input")).toObject(), &error, &valuation);
        emit commandFinished(commandId, ok,
                             ok ? QStringLiteral("估值已校验并写入原生仓储") : error,
                             valuation);
        if (ok) publishEvent(QStringLiteral("upload.valuation"), valuation);
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_ack")) {
        const QString recordId = arguments.value(QStringLiteral("record_id")).toString();
        const QString digest = arguments.value(QStringLiteral("sha256")).toString();
        QSqlQuery load(database_);
        load.prepare(QStringLiteral(
            "SELECT payload_json,payload_sha256,acked_at FROM upload_records WHERE record_id=?"));
        load.addBindValue(recordId);
        if (!load.exec() || !load.next() || load.value(1).toString() != digest
            || !load.value(2).toString().isEmpty()) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("ACK 不匹配、重复或 record_id 不存在"),
                                 {{QStringLiteral("record_id"), recordId}});
            return;
        }
        const QJsonObject recorded = QJsonDocument::fromJson(load.value(0).toByteArray()).object();
        const QJsonObject expected = recorded.value(QStringLiteral("expected_ack")).toObject();
        const QJsonObject response = arguments.value(QStringLiteral("response")).toObject();
        QString ackError;
        if (!response.isEmpty() && expected.contains(QStringLiteral("batch_id"))) {
            if (response.value(QStringLiteral("batch_id")).toString()
                    != expected.value(QStringLiteral("batch_id")).toString())
                ackError = QStringLiteral("batch_id 不匹配");
            const QSet<QString> wanted = [&expected] {
                QSet<QString> values;
                for (const auto &item : expected.value(QStringLiteral("accepted_symbols")).toArray())
                    values.insert(item.toString().trimmed().toUpper());
                return values;
            }();
            QSet<QString> accepted;
            for (const auto &item : response.value(QStringLiteral("accepted")).toArray())
                accepted.insert(item.toString().trimmed().toUpper());
            if (ackError.isEmpty()
                && (accepted != wanted
                    || !response.value(QStringLiteral("rejected")).toObject().isEmpty()))
                ackError = QStringLiteral("accepted/rejected 不匹配");
        } else if (!response.isEmpty() && expected.contains(QStringLiteral("ok"))
                   && !response.value(QStringLiteral("ok")).toBool(false)) {
            ackError = QStringLiteral("单条上传未返回 ok=true");
        } else if (!response.isEmpty() && expected.contains(QStringLiteral("accepted"))) {
            if (response.value(QStringLiteral("accepted")).toInt(-1)
                    != expected.value(QStringLiteral("accepted")).toInt()
                || !response.value(QStringLiteral("enabled")).isBool())
                ackError = QStringLiteral("Sina accepted/enabled ACK 不匹配");
        }
        if (!ackError.isEmpty()) {
            emit commandFinished(commandId, false, ackError,
                                 {{QStringLiteral("record_id"), recordId}});
            return;
        }
        QSqlQuery query(database_);
        query.prepare(QStringLiteral(
            "UPDATE upload_records SET acked_at=?,ack_sha256=? "
            "WHERE record_id=? AND payload_sha256=? AND acked_at IS NULL"));
        query.addBindValue(iso(nowUtc()));
        query.addBindValue(digest);
        query.addBindValue(recordId);
        query.addBindValue(digest);
        const bool ok = query.exec() && query.numRowsAffected() == 1;
        emit commandFinished(commandId, ok,
                             ok ? QStringLiteral("ACK 与 payload 摘要匹配")
                                : QStringLiteral("ACK 不匹配、重复或 record_id 不存在"),
                             {{QStringLiteral("record_id"), recordId}});
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_set_fund")) {
        const QString symbol = normalizedSymbol(arguments.value(QStringLiteral("symbol")).toString());
        auto validOptional = [&arguments](const QString &key, double minimum,
                                          double maximum) {
            if (!arguments.contains(key)) return true;
            double number = 0;
            return finiteNumber(arguments.value(key), &number)
                && number >= minimum && number <= maximum;
        };
        if (symbol.isEmpty() || !validOptional(QStringLiteral("nav"), 0.0, 1e9)
            || !validOptional(QStringLiteral("shares"), 0.0, 1e15)
            || !validOptional(QStringLiteral("position_ratio"), 0.0, 1.0)) {
            emit commandFinished(commandId, false, QStringLiteral("基金 NAV/份额/仓位参数无效"), {});
            return;
        }
        QSqlQuery query(database_);
        query.prepare(QStringLiteral(
            "INSERT INTO funds(symbol,name,branch,nav,shares,position_ratio,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
            "name=CASE WHEN excluded.name='' THEN funds.name ELSE excluded.name END,"
            "branch=CASE WHEN excluded.branch='' THEN funds.branch ELSE excluded.branch END,"
            "nav=COALESCE(excluded.nav,funds.nav),shares=COALESCE(excluded.shares,funds.shares),"
            "position_ratio=COALESCE(excluded.position_ratio,funds.position_ratio),"
            "updated_at=excluded.updated_at"));
        query.addBindValue(symbol);
        query.addBindValue(arguments.value(QStringLiteral("name")).toString().simplified());
        query.addBindValue(arguments.value(QStringLiteral("branch")).toString().simplified());
        query.addBindValue(arguments.contains(QStringLiteral("nav"))
                               ? QVariant(arguments.value(QStringLiteral("nav")).toDouble()) : QVariant{});
        query.addBindValue(arguments.contains(QStringLiteral("shares"))
                               ? QVariant(arguments.value(QStringLiteral("shares")).toDouble()) : QVariant{});
        query.addBindValue(arguments.contains(QStringLiteral("position_ratio"))
                               ? QVariant(arguments.value(QStringLiteral("position_ratio")).toDouble()) : QVariant{});
        query.addBindValue(iso(nowUtc()));
        const bool ok = query.exec();
        emit commandFinished(commandId, ok,
                             ok ? QStringLiteral("基金设置已保存") : query.lastError().text(),
                             {{QStringLiteral("symbol"), symbol}});
        publishSnapshot();
        return;
    }
    if (action == QStringLiteral("upload_add_message")) {
        const QString message = arguments.value(QStringLiteral("message")).toString().trimmed();
        if (message.isEmpty() || message.size() > 4000) {
            emit commandFinished(commandId, false, QStringLiteral("留言必须为 1–4000 字符"), {});
            return;
        }
        QSqlQuery query(database_);
        query.prepare(QStringLiteral(
            "INSERT INTO contact_messages(message,created_at) VALUES(?,?)"));
        query.addBindValue(message);
        query.addBindValue(iso(nowUtc()));
        const bool ok = query.exec();
        emit commandFinished(commandId, ok,
                             ok ? QStringLiteral("留言已保存到原生仓储")
                                : query.lastError().text(), {});
        publishSnapshot();
        return;
    }
    emit commandFinished(commandId, false,
                         QStringLiteral("UploadEngine 不支持该动作"),
                         {{QStringLiteral("code"), QStringLiteral("unsupported_action")}});
}

QString UploadEngine::normalizedSymbol(const QString &value)
{
    QString result = value.trimmed().toUpper();
    if (result.endsWith(QStringLiteral(".SH"))) result = QStringLiteral("SH") + result.left(6);
    else if (result.endsWith(QStringLiteral(".SZ"))) result = QStringLiteral("SZ") + result.left(6);
    static const QRegularExpression six(QStringLiteral("^[0-9]{6}$"));
    if (six.match(result).hasMatch()) {
        result.prepend(result.startsWith(u'5') || result.startsWith(u'6')
                           ? QStringLiteral("SH") : QStringLiteral("SZ"));
    }
    static const QRegularExpression canonical(
        QStringLiteral("^(?:SH|SZ)[0-9]{6}$|^[A-Z][A-Z0-9./-]{0,14}$|^[0-9]{4,5}\\.HK$"));
    return canonical.match(result).hasMatch() ? result : QString{};
}

QJsonArray UploadEngine::productionPcfDefinitions()
{
    return defaultPcfDefinitions();
}

QJsonObject UploadEngine::quoteFromIbkrItem(const QJsonObject &item,
                                            QString *error)
{
    const QJsonObject contract = item.value(QStringLiteral("contract")).toObject();
    const QString symbol = normalizedSymbol(
        contract.value(QStringLiteral("symbol")).toString());
    if (symbol.isEmpty()) {
        if (error) *error = QStringLiteral("IBKR item contract.symbol 无效");
        return {};
    }
    const QDateTime received = parseTimestamp(item.value(QStringLiteral("received_at")));
    if (!received.isValid()) {
        if (error) *error = QStringLiteral("IBKR item received_at 无效");
        return {};
    }
    double bid = 0, ask = 0, last = 0, close = 0;
    const bool hasBid = positiveJsonNumber(item.value(QStringLiteral("bid")), &bid);
    const bool hasAsk = positiveJsonNumber(item.value(QStringLiteral("ask")), &ask);
    const bool hasLast = positiveJsonNumber(item.value(QStringLiteral("last")), &last);
    const bool hasClose = positiveJsonNumber(item.value(QStringLiteral("close")), &close);
    if (hasBid != hasAsk || (hasBid && ask < bid)) {
        if (error) *error = QStringLiteral("IBKR item bid/ask 必须成对且 ask>=bid");
        return {};
    }
    const double price = hasLast ? last : hasBid ? (bid + ask) / 2.0
                                      : hasClose ? close : 0.0;
    if (!(price > 0) || !std::isfinite(price)) {
        if (error) *error = QStringLiteral("IBKR item 没有可用正价");
        return {};
    }
    QJsonObject quote{{QStringLiteral("symbol"), symbol},
                      {QStringLiteral("price"), price},
                      {QStringLiteral("source"), QStringLiteral("machome-native-ibkr-bridge")},
                      {QStringLiteral("observed_at"), received.toUTC().toString(Qt::ISODateWithMs)},
                      {QStringLiteral("stream_checked_at"), received.toUTC().toString(Qt::ISODateWithMs)},
                      {QStringLiteral("market_data_type"),
                       ibkrMarketDataTypeName(item.value(QStringLiteral("market_data_type")).toInt())},
                      {QStringLiteral("fresh"), item.value(QStringLiteral("fresh")).toBool(false)},
                      {QStringLiteral("subscription_id"),
                       contract.value(QStringLiteral("id")).toString()},
                      {QStringLiteral("sequence"), item.value(QStringLiteral("sequence"))}};
    if (hasBid) {
        quote.insert(QStringLiteral("bid"), bid);
        quote.insert(QStringLiteral("ask"), ask);
    }
    return quote;
}

bool UploadEngine::validateQuote(const QJsonObject &quote, QString *error)
{
    const QString symbol = normalizedSymbol(valueString(quote, QStringLiteral("symbol")));
    double price = 0;
    if (symbol.isEmpty()) {
        if (error) *error = QStringLiteral("行情 symbol 无效");
        return false;
    }
    if (!finiteNumber(quote.value(QStringLiteral("price")), &price) || price <= 0) {
        if (error) *error = QStringLiteral("行情 price 必须是有限正数");
        return false;
    }
    double bid = price;
    double ask = price;
    if (quote.contains(QStringLiteral("bid"))
        && (!finiteNumber(quote.value(QStringLiteral("bid")), &bid) || bid <= 0)) {
        if (error) *error = QStringLiteral("bid 无效");
        return false;
    }
    if (quote.contains(QStringLiteral("ask"))
        && (!finiteNumber(quote.value(QStringLiteral("ask")), &ask) || ask <= 0)) {
        if (error) *error = QStringLiteral("ask 无效");
        return false;
    }
    if (ask < bid) {
        if (error) *error = QStringLiteral("ask 不得小于 bid");
        return false;
    }
    const QDateTime observed = parseTimestamp(quote.value(QStringLiteral("observed_at")));
    if (!observed.isValid()) {
        if (error) *error = QStringLiteral("observed_at 必须是 ISO-8601 时间");
        return false;
    }
    if (valueString(quote, QStringLiteral("source")).isEmpty()) {
        if (error) *error = QStringLiteral("source 不得为空");
        return false;
    }
    return true;
}

bool UploadEngine::validateValuationInput(const QJsonObject &input, QString *error)
{
    const QJsonObject normalized = canonicalValuationInput(input);
    if (normalizedSymbol(valueString(normalized, QStringLiteral("symbol"))).isEmpty()) {
        if (error) *error = QStringLiteral("估值 symbol 无效");
        return false;
    }
    if (valueString(normalized, QStringLiteral("model_version")).isEmpty()) {
        if (error) *error = QStringLiteral("model_version 不得为空");
        return false;
    }
    if (valueString(normalized, QStringLiteral("source")).isEmpty()
        || !parseTimestamp(normalized.value(QStringLiteral("generated_at"))).isValid()) {
        if (error) *error = QStringLiteral("source/generated_at 无效");
        return false;
    }
    const QString kind = valueString(normalized, QStringLiteral("valuation_kind"));
    if (!kind.isEmpty()) {
        const auto definition = productionModel(normalizedSymbol(
            normalized.value(QStringLiteral("symbol")).toString()));
        if (definition.first != kind
            || definition.second != normalized.value(QStringLiteral("model_version")).toString()) {
            if (error) *error = QStringLiteral("标的、valuation_kind 与 model_version 不符合生产定义");
            return false;
        }
    }
    if (!kind.isEmpty()) {
        double domesticBid = 0, domesticAsk = 0;
        const QJsonObject domestic = normalized.value(QStringLiteral("domestic")).toObject();
        if (!domestic.isEmpty()
            && (!executableDomesticQuote(normalized, &domesticBid, &domesticAsk, error)
                || !parseTimestamp(domestic.value(QStringLiteral("observed_at"))).isValid()))
            return false;
    }
    if (kind == QStringLiteral("xop_proxy") || kind == QStringLiteral("nq_proxy")
        || kind == QStringLiteral("es_proxy") || kind == QStringLiteral("n225m_proxy")
        || kind == QStringLiteral("dax_proxy"))
        return !calculateProxy(normalized, error).isEmpty();
    if (kind == QStringLiteral("lof_weighted_anchor"))
        return !calculateWeightedAnchor(normalized, error).isEmpty();
    if (kind == QStringLiteral("india_t2_multimarket"))
        return !calculateIndia(normalized, error).isEmpty();
    if (kind == QStringLiteral("silver_settlement"))
        return !calculateSilver(normalized, error).isEmpty();
    if (!kind.isEmpty() && kind != QStringLiteral("full_cash_substitution_pcf")) {
        if (error) *error = QStringLiteral("不支持的 valuation_kind");
        return false;
    }
    const QJsonObject pcf = normalized.value(QStringLiteral("pcf")).toObject();
    const QDate tradingDay = QDate::fromString(
        pcf.value(QStringLiteral("trading_day")).toString(), Qt::ISODate);
    double unit = 0;
    bool cashOk = false;
    cashComponent(pcf, &cashOk);
    if (!tradingDay.isValid()
        || !finiteNumber(pcf.value(QStringLiteral("creation_redemption_unit")), &unit)
        || unit <= 0
        || !cashOk) {
        if (error) *error = QStringLiteral("PCF trading_day/unit/cash 无效");
        return false;
    }
    if (!kind.isEmpty()) {
        const QString fundSymbol = normalizedSymbol(
            normalized.value(QStringLiteral("symbol")).toString());
        const double expectedUnit = expectedRedemptionUnit(fundSymbol);
        const int expectedCount = expectedComponentCount(fundSymbol);
        if ((expectedUnit > 0 && qAbs(unit - expectedUnit) > 0.001)
            || (expectedCount > 0 && pcf.contains(QStringLiteral("component_count"))
                && pcf.value(QStringLiteral("component_count")).toInt() != expectedCount)) {
            if (error) *error = QStringLiteral("PCF 申购赎回单位或成分数与基金定义不符");
            return false;
        }
    }
    const QJsonArray components = pcf.value(QStringLiteral("components")).toArray();
    if (components.isEmpty() || components.size() > 2000) {
        if (error) *error = QStringLiteral("PCF components 必须为 1–2000 项");
        return false;
    }
    const QJsonObject quotes = normalized.value(QStringLiteral("quotes")).toObject();
    const QJsonObject fx = normalized.value(QStringLiteral("fx_rates")).toObject();
    QSet<QString> seen;
    for (const auto &value : components) {
        const QJsonObject component = value.toObject();
        const QString symbol = valueString(component, QStringLiteral("symbol")).toUpper();
        const QString currency = valueString(component, QStringLiteral("currency")).toUpper();
        double quantity = 0;
        if (symbol.isEmpty() || currency.isEmpty() || seen.contains(symbol)
            || !finiteNumber(component.value(QStringLiteral("quantity")), &quantity)
            || quantity < 0) {
            if (error) *error = QStringLiteral("成分证券标识/币种/数量无效或重复");
            return false;
        }
        seen.insert(symbol);
        const QJsonObject quote = quoteForComponent(quotes, component);
        double bid = 0;
        double ask = 0;
        if (!finiteNumber(quote.value(QStringLiteral("bid")), &bid) || bid <= 0
            || !finiteNumber(quote.value(QStringLiteral("ask")), &ask)
            || ask < bid) {
            if (error) *error = QStringLiteral("成分 %1 缺失有效 bid/ask").arg(symbol);
            return false;
        }
        if (currency != QStringLiteral("CNY")) {
            double rate = 0;
            if (!finiteNumber(fx.value(currency), &rate) || rate <= 0) {
                if (error) *error = QStringLiteral("缺失 %1/CNY 正数汇率").arg(currency);
                return false;
            }
        }
    }
    if (!parseTimestamp(normalized.value(QStringLiteral("generated_at"))).isValid()) {
        if (error) *error = QStringLiteral("generated_at 必须是 ISO-8601 时间");
        return false;
    }
    return true;
}

QJsonObject UploadEngine::calculateBasketValuation(const QJsonObject &input,
                                                   QString *error)
{
    if (!validateValuationInput(input, error)) return {};
    const QJsonObject normalized = canonicalValuationInput(input);
    const QString kind = valueString(normalized, QStringLiteral("valuation_kind"));
    QJsonObject result;
    if (kind == QStringLiteral("xop_proxy") || kind == QStringLiteral("nq_proxy")
        || kind == QStringLiteral("es_proxy") || kind == QStringLiteral("n225m_proxy")
        || kind == QStringLiteral("dax_proxy"))
        result = calculateProxy(normalized, error);
    else if (kind == QStringLiteral("lof_weighted_anchor"))
        result = calculateWeightedAnchor(normalized, error);
    else if (kind == QStringLiteral("india_t2_multimarket"))
        result = calculateIndia(normalized, error);
    else if (kind == QStringLiteral("silver_settlement"))
        result = calculateSilver(normalized, error);
    if (!result.isEmpty()) {
        result.insert(QStringLiteral("symbol"), normalizedSymbol(
                          normalized.value(QStringLiteral("symbol")).toString()));
        return result;
    }
    const QJsonObject pcf = normalized.value(QStringLiteral("pcf")).toObject();
    const QJsonObject quotes = normalized.value(QStringLiteral("quotes")).toObject();
    const QJsonObject fx = normalized.value(QStringLiteral("fx_rates")).toObject();
    const double unit = pcf.value(QStringLiteral("creation_redemption_unit")).toDouble();
    double basketBid = cashComponent(pcf);
    double basketAsk = basketBid;
    for (const auto &value : pcf.value(QStringLiteral("components")).toArray()) {
        const QJsonObject component = value.toObject();
        const QString symbol = valueString(component, QStringLiteral("symbol")).toUpper();
        const QString currency = valueString(component, QStringLiteral("currency")).toUpper();
        const double quantity = component.value(QStringLiteral("quantity")).toDouble();
        const double rate = currency == QStringLiteral("CNY") ? 1.0
                                                                : fx.value(currency).toDouble();
        const QJsonObject quote = quoteForComponent(quotes, component);
        basketBid += quantity * quote.value(QStringLiteral("bid")).toDouble() * rate;
        basketAsk += quantity * quote.value(QStringLiteral("ask")).toDouble() * rate;
    }
    const double bidNav = basketBid / unit;
    const double askNav = basketAsk / unit;
    result = {{QStringLiteral("schema_version"), 1},
            {QStringLiteral("symbol"), normalizedSymbol(normalized.value(QStringLiteral("symbol")).toString())},
            {QStringLiteral("model_version"), normalized.value(QStringLiteral("model_version"))},
            {QStringLiteral("valuation_kind"), QStringLiteral("full_cash_substitution_pcf")},
            {QStringLiteral("trading_day"), pcf.value(QStringLiteral("trading_day"))},
            {QStringLiteral("basket_bid_nav"), bidNav},
            {QStringLiteral("basket_ask_nav"), askNav},
            {QStringLiteral("spread"), askNav - bidNav},
            {QStringLiteral("actionable"), bidNav > 0 && askNav >= bidNav},
            {QStringLiteral("source"), normalized.value(QStringLiteral("source"))},
            {QStringLiteral("generated_at"), normalized.value(QStringLiteral("generated_at"))}};
    if (kind == QStringLiteral("full_cash_substitution_pcf")
        || normalized.contains(QStringLiteral("domestic"))) {
        QJsonArray warnings = proxyWarnings(normalized, 15, false);
        warnings.append(QStringLiteral("全现金替代 PCF 仅为盘中指示性篮子估值，禁止自动执行"));
        result.insert(QStringLiteral("warnings"), warnings);
        result.insert(QStringLiteral("actionable"), warnings.isEmpty());
        appendPremiums(&result, normalized, bidNav, askNav);
    }
    return result;
}

void UploadEngine::evaluateSchedules()
{
    if (!running_) return;
    const QDateTime now = nowUtc();
    for (auto it = jobs_.begin(); it != jobs_.end(); ++it) {
        JobRuntime &runtime = it.value();
        const ScheduleDecision decision = UploadSchedule::evaluate(
            runtime.definition, now, runtime.lastStartedUtc,
            runtime.lastSuccessUtc);
        runtime.nextRunUtc = decision.nextRunUtc;
        if (!decision.active) {
            runtime.state = QStringLiteral("scheduled_idle");
            runtime.stage = decision.reason;
            continue;
        }
        runtime.state = QStringLiteral("active");
        runtime.stage = decision.due ? QStringLiteral("due") : QStringLiteral("waiting_interval");
        const bool completed = alreadyCompleted(runtime.definition.id,
                                                decision.idempotencyKey);
        if (!decision.due || completed) {
            if (completed) runtime.stage = QStringLiteral("already_satisfied");
            continue;
        }
        QString error;
        QJsonObject details;
        executeJob(runtime, decision.idempotencyKey, false, &error, &details);
    }
    publishSnapshot();
}

bool UploadEngine::manualRunAllowed(const JobRuntime &runtime,
                                    ScheduleDecision *decision) const
{
    if (operatingMode_ == QStringLiteral("weekend_test")) {
        if (decision) {
            *decision = UploadSchedule::evaluate(
                runtime.definition, nowUtc(), runtime.lastStartedUtc,
                runtime.lastSuccessUtc);
            decision->active = true;
            decision->reason = QStringLiteral("weekend_test_manual_bypass");
        }
        return true;
    }
    const ScheduleDecision evaluated = UploadSchedule::evaluate(
        runtime.definition, nowUtc(), runtime.lastStartedUtc,
        runtime.lastSuccessUtc);
    if (decision) *decision = evaluated;
    return evaluated.active;
}

void UploadEngine::runStartupCatchups()
{
    if (operatingMode_ != QStringLiteral("work")) return;
    const bool testMode = context_.settings.value(QStringLiteral("test_mode"))
                              .toBool(false);
    if (testMode
        && !context_.settings.value(QStringLiteral("startup_catchup_enabled"))
                .toBool(false))
        return;

    const bool liveReads = context_.settings
                               .value(QStringLiteral("live_reads_enabled"))
                               .toBool(false);
    const QJsonObject collectorConfigs = context_.settings
                                             .value(QStringLiteral("collectors"))
                                             .toObject();
    const QSet<QString> collectorJobs{
        QStringLiteral("safe-central-parity"),
        QStringLiteral("eastmoney-nav"),
        QStringLiteral("share-history")};
    const QStringList legacyStartupJobs{
        QStringLiteral("safe-central-parity"),
        QStringLiteral("eastmoney-nav"),
        QStringLiteral("effective-ratio-fit"),
        QStringLiteral("share-history")};

    for (const QString &jobId : legacyStartupJobs) {
        auto found = jobs_.find(jobId);
        if (found == jobs_.end()) continue;
        if (collectorJobs.contains(jobId)
            && (!liveReads
                || !collectorConfigs.value(jobId).toObject()
                        .value(QStringLiteral("enabled")).toBool(false)))
            continue;

        JobRuntime &runtime = found.value();
        const QTimeZone zone(runtime.definition.timezone.toUtf8());
        const QDateTime localNow = nowUtc().toTimeZone(zone);
        const QString key = QStringLiteral("startup:%1")
                                .arg(localNow.date().toString(Qt::ISODate));
        if (alreadyCompleted(jobId, key)) continue;

        const QDateTime previousStarted = runtime.lastStartedUtc;
        const QDateTime previousSuccess = runtime.lastSuccessUtc;
        QString error;
        QJsonObject details;
        const bool ok = executeJob(runtime, key, false, &error, &details);

        // In the legacy server, a startup SyncMissing before the daily clock did
        // not consume that day's scheduled run. Preserve that distinction while
        // keeping the startup attempt in upload_job_runs for audit/idempotency.
        const QDateTime occurrence = localOccurrence(
            runtime.definition, localNow.date(), runtime.definition.runAt);
        if (localNow < occurrence) {
            runtime.lastStartedUtc = previousStarted;
            runtime.lastSuccessUtc = previousSuccess;
            persistJobState(runtime);
        }
        publishEvent(QStringLiteral("upload.startup_catchup"),
                     {{QStringLiteral("job_id"), jobId},
                      {QStringLiteral("ok"), ok},
                      {QStringLiteral("idempotency_key"), key},
                      {QStringLiteral("scheduled_run_preserved"),
                       localNow < occurrence},
                      {QStringLiteral("error"), error}});
    }
}

bool UploadEngine::openRepository(QString *error)
{
    QDir root(context_.dataRoot);
    if (!root.exists() && !root.mkpath(QStringLiteral("."))) {
        if (error) *error = QStringLiteral("无法创建 Upload 数据目录 %1").arg(context_.dataRoot);
        return false;
    }
    QFile::setPermissions(context_.dataRoot, QFileDevice::ReadOwner |
                                               QFileDevice::WriteOwner |
                                               QFileDevice::ExeOwner);
    connectionName_ = QStringLiteral("machome-upload-%1")
                          .arg(QUuid::createUuid().toString(QUuid::WithoutBraces));
    database_ = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connectionName_);
    database_.setDatabaseName(root.filePath(QStringLiteral("upload.sqlite3")));
    if (!database_.open()) {
        if (error) *error = database_.lastError().text();
        return false;
    }
    executeSql(QStringLiteral("PRAGMA journal_mode=WAL"));
    executeSql(QStringLiteral("PRAGMA synchronous=FULL"));
    executeSql(QStringLiteral("PRAGMA foreign_keys=ON"));
    executeSql(QStringLiteral("PRAGMA busy_timeout=3000"));
    return migrateRepository(error);
}

bool UploadEngine::migrateRepository(QString *error)
{
    const QStringList statements{
        QStringLiteral("CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL)"),
        QStringLiteral("INSERT INTO schema_meta(version) SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_meta)"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS funds(symbol TEXT PRIMARY KEY,name TEXT NOT NULL DEFAULT '',branch TEXT NOT NULL DEFAULT '',nav REAL,shares REAL,position_ratio REAL,updated_at TEXT NOT NULL)"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS latest_quotes(symbol TEXT PRIMARY KEY,price REAL NOT NULL,bid REAL,ask REAL,source TEXT NOT NULL,observed_at TEXT NOT NULL,payload_json TEXT NOT NULL)"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS valuation_inputs(symbol TEXT PRIMARY KEY,model_version TEXT NOT NULL,trading_day TEXT NOT NULL,input_json TEXT NOT NULL,valuation_json TEXT NOT NULL,updated_at TEXT NOT NULL)"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS daily_prices(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,close REAL NOT NULL,source TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(symbol,trade_date))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS net_values(symbol TEXT NOT NULL,nav_date TEXT NOT NULL,nav REAL NOT NULL,source TEXT NOT NULL,PRIMARY KEY(symbol,nav_date,source))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS share_history(symbol TEXT NOT NULL,share_date TEXT NOT NULL,shares_10k REAL NOT NULL,source TEXT NOT NULL,PRIMARY KEY(symbol,share_date))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS purchase_status(symbol TEXT NOT NULL,status_date TEXT NOT NULL,status TEXT NOT NULL,daily_limit_yuan REAL,raw_limit TEXT,source TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(symbol,status_date))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS fx_rates(pair TEXT NOT NULL,rate_date TEXT NOT NULL,rate REAL NOT NULL,source TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(pair,rate_date,source))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS pcf_cache(symbol TEXT NOT NULL,trading_day TEXT NOT NULL,sha256 TEXT NOT NULL,source_url TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(symbol,trading_day))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS holdings(fund_symbol TEXT NOT NULL,holding_day TEXT NOT NULL,holding_symbol TEXT NOT NULL,quantity REAL,ratio REAL,currency TEXT,source TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(fund_symbol,holding_day,holding_symbol))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS upload_records(record_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,payload_sha256 TEXT NOT NULL,payload_json TEXT NOT NULL,sink_mode TEXT NOT NULL,created_at TEXT NOT NULL,acked_at TEXT,ack_sha256 TEXT,UNIQUE(job_id,idempotency_key))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS upload_job_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,state TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,error TEXT,UNIQUE(job_id,idempotency_key))"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS upload_job_state(job_id TEXT PRIMARY KEY,last_started_at TEXT,last_success_at TEXT,last_failure_at TEXT,run_count INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '')"),
        QStringLiteral("CREATE TABLE IF NOT EXISTS contact_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,message TEXT NOT NULL,created_at TEXT NOT NULL)")};
    if (!database_.transaction()) {
        if (error) *error = database_.lastError().text();
        return false;
    }
    for (const auto &statement : statements) {
        if (!executeSql(statement, error)) {
            database_.rollback();
            return false;
        }
    }
    if (!database_.commit()) {
        if (error) *error = database_.lastError().text();
        return false;
    }
    return true;
}

bool UploadEngine::executeSql(const QString &sql, QString *error)
{
    QSqlQuery query(database_);
    if (query.exec(sql)) return true;
    if (error) *error = query.lastError().text();
    return false;
}

bool UploadEngine::persistQuote(const QJsonObject &quote, QString *error)
{
    if (!database_.isOpen()) {
        if (error) *error = QStringLiteral("仓储未就绪");
        return false;
    }
    if (!validateQuote(quote, error)) return false;
    const QString symbol = normalizedSymbol(quote.value(QStringLiteral("symbol")).toString());
    QJsonObject canonical = quote;
    canonical.insert(QStringLiteral("symbol"), symbol);
    const double price = canonical.value(QStringLiteral("price")).toDouble();
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "INSERT INTO latest_quotes(symbol,price,bid,ask,source,observed_at,payload_json) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
        "price=excluded.price,bid=excluded.bid,ask=excluded.ask,source=excluded.source,"
        "observed_at=excluded.observed_at,payload_json=excluded.payload_json"));
    query.addBindValue(symbol);
    query.addBindValue(price);
    query.addBindValue(canonical.value(QStringLiteral("bid")).toDouble(price));
    query.addBindValue(canonical.value(QStringLiteral("ask")).toDouble(price));
    query.addBindValue(canonical.value(QStringLiteral("source")).toString());
    query.addBindValue(canonical.value(QStringLiteral("observed_at")).toString());
    query.addBindValue(QString::fromUtf8(QJsonDocument(canonical).toJson(QJsonDocument::Compact)));
    if (!query.exec()) {
        if (error) *error = query.lastError().text();
        return false;
    }
    QSqlQuery valuationQuery(database_);
    valuationQuery.prepare(QStringLiteral(
        "SELECT input_json FROM valuation_inputs WHERE symbol=?"));
    valuationQuery.addBindValue(symbol);
    if (valuationQuery.exec() && valuationQuery.next()) {
        QJsonObject input = QJsonDocument::fromJson(
            valuationQuery.value(0).toByteArray()).object();
        input.insert(QStringLiteral("domestic"), QJsonObject{
            {QStringLiteral("bid"), canonical.value(QStringLiteral("bid")).toDouble(price)},
            {QStringLiteral("ask"), canonical.value(QStringLiteral("ask")).toDouble(price)},
            {QStringLiteral("price"), price},
            {QStringLiteral("observed_at"), canonical.value(QStringLiteral("observed_at"))},
            {QStringLiteral("source"), canonical.value(QStringLiteral("source"))}});
        QString valuationError;
        const QJsonObject recalculated = calculateBasketValuation(input, &valuationError);
        if (!recalculated.isEmpty()) {
            QSqlQuery update(database_);
            update.prepare(QStringLiteral(
                "UPDATE valuation_inputs SET valuation_json=?,updated_at=? WHERE symbol=?"));
            update.addBindValue(QString::fromUtf8(
                QJsonDocument(recalculated).toJson(QJsonDocument::Compact)));
            update.addBindValue(iso(nowUtc()));
            update.addBindValue(symbol);
            update.exec();
        }
    }
    QSqlQuery fund(database_);
    fund.prepare(QStringLiteral(
        "INSERT INTO funds(symbol,updated_at) VALUES(?,?) "
        "ON CONFLICT(symbol) DO UPDATE SET updated_at=excluded.updated_at"));
    fund.addBindValue(symbol);
    fund.addBindValue(iso(nowUtc()));
    return fund.exec();
}

bool UploadEngine::persistDataset(const QJsonObject &dataset, QString *error)
{
    if (!database_.isOpen()) {
        if (error) *error = QStringLiteral("仓储未就绪");
        return false;
    }
    const QString kind = dataset.value(QStringLiteral("kind")).toString().trimmed();
    const QString source = dataset.value(QStringLiteral("source")).toString().trimmed();
    const QString symbol = normalizedSymbol(dataset.value(QStringLiteral("symbol")).toString());
    const QDate date = QDate::fromString(dataset.value(QStringLiteral("date")).toString(),
                                         Qt::ISODate);
    if (source.isEmpty() || !date.isValid()) {
        if (error) *error = QStringLiteral("数据集 source/date 无效");
        return false;
    }
    QSqlQuery query(database_);
    double numeric = 0;
    if (kind == QStringLiteral("daily_price")) {
        if (symbol.isEmpty() || !positiveNumber(dataset, QStringLiteral("close"), &numeric)) {
            if (error) *error = QStringLiteral("daily_price symbol/close 无效");
            return false;
        }
        query.prepare(QStringLiteral(
            "INSERT INTO daily_prices(symbol,trade_date,close,source,payload_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(symbol,trade_date) DO UPDATE SET close=excluded.close,source=excluded.source,payload_json=excluded.payload_json"));
        query.addBindValue(symbol); query.addBindValue(date.toString(Qt::ISODate));
        query.addBindValue(numeric); query.addBindValue(source);
        query.addBindValue(QString::fromUtf8(QJsonDocument(dataset).toJson(QJsonDocument::Compact)));
    } else if (kind == QStringLiteral("net_value")) {
        if (symbol.isEmpty() || !positiveNumber(dataset, QStringLiteral("nav"), &numeric)) {
            if (error) *error = QStringLiteral("net_value symbol/nav 无效");
            return false;
        }
        query.prepare(QStringLiteral(
            "INSERT INTO net_values(symbol,nav_date,nav,source) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol,nav_date,source) DO UPDATE SET nav=excluded.nav"));
        query.addBindValue(symbol); query.addBindValue(date.toString(Qt::ISODate));
        query.addBindValue(numeric); query.addBindValue(source);
    } else if (kind == QStringLiteral("share_history")) {
        if (symbol.isEmpty() || !positiveNumber(dataset, QStringLiteral("shares_10k"), &numeric)) {
            if (error) *error = QStringLiteral("share_history symbol/shares_10k 无效");
            return false;
        }
        query.prepare(QStringLiteral(
            "INSERT INTO share_history(symbol,share_date,shares_10k,source) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol,share_date) DO UPDATE SET shares_10k=excluded.shares_10k,source=excluded.source"));
        query.addBindValue(symbol); query.addBindValue(date.toString(Qt::ISODate));
        query.addBindValue(numeric); query.addBindValue(source);
    } else if (kind == QStringLiteral("purchase_status")) {
        const QString status = dataset.value(QStringLiteral("status")).toString().trimmed();
        if (symbol.isEmpty() || status.isEmpty()) {
            if (error) *error = QStringLiteral("purchase_status symbol/status 无效");
            return false;
        }
        query.prepare(QStringLiteral(
            "INSERT INTO purchase_status(symbol,status_date,status,daily_limit_yuan,raw_limit,source,payload_json) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol,status_date) DO UPDATE SET status=excluded.status,"
            "daily_limit_yuan=excluded.daily_limit_yuan,raw_limit=excluded.raw_limit,source=excluded.source,payload_json=excluded.payload_json"));
        query.addBindValue(symbol); query.addBindValue(date.toString(Qt::ISODate));
        query.addBindValue(status); query.addBindValue(dataset.value(QStringLiteral("daily_limit_yuan")).toDouble());
        query.addBindValue(dataset.value(QStringLiteral("raw_limit")).toString()); query.addBindValue(source);
        query.addBindValue(QString::fromUtf8(QJsonDocument(dataset).toJson(QJsonDocument::Compact)));
    } else if (kind == QStringLiteral("fx_rate")) {
        if (symbol.isEmpty() || !positiveNumber(dataset, QStringLiteral("rate"), &numeric)) {
            if (error) *error = QStringLiteral("fx_rate pair/rate 无效"); return false;
        }
        query.prepare(QStringLiteral(
            "INSERT INTO fx_rates(pair,rate_date,rate,source,payload_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(pair,rate_date,source) DO UPDATE SET rate=excluded.rate,payload_json=excluded.payload_json"));
        query.addBindValue(symbol); query.addBindValue(date.toString(Qt::ISODate)); query.addBindValue(numeric); query.addBindValue(source);
        query.addBindValue(QString::fromUtf8(QJsonDocument(dataset).toJson(QJsonDocument::Compact)));
    } else if (kind == QStringLiteral("pcf_cache")) {
        const QJsonObject pcf = dataset.value(QStringLiteral("pcf")).toObject();
        const QString digest = pcf.value(QStringLiteral("sha256")).toString();
        const QString url = pcf.value(QStringLiteral("source_url")).toString();
        if (symbol.isEmpty() || !validSha256(digest) || url.isEmpty()
            || pcf.value(QStringLiteral("trading_day")).toString() != date.toString(Qt::ISODate)) {
            if (error) *error = QStringLiteral("pcf_cache identity/day/source/sha256 无效"); return false;
        }
        query.prepare(QStringLiteral(
            "INSERT INTO pcf_cache(symbol,trading_day,sha256,source_url,payload_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(symbol,trading_day) DO UPDATE SET sha256=excluded.sha256,source_url=excluded.source_url,payload_json=excluded.payload_json"));
        query.addBindValue(symbol); query.addBindValue(date.toString(Qt::ISODate)); query.addBindValue(digest); query.addBindValue(url);
        query.addBindValue(QString::fromUtf8(QJsonDocument(pcf).toJson(QJsonDocument::Compact)));
    } else if (kind == QStringLiteral("holding")) {
        const QString holding = normalizedSymbol(
            dataset.value(QStringLiteral("holding_symbol")).toString());
        double quantity = 0, ratio = 0;
        if (symbol.isEmpty() || holding.isEmpty()
            || !finiteNumber(dataset.value(QStringLiteral("quantity")), &quantity)
            || quantity < 0
            || !finiteNumber(dataset.value(QStringLiteral("ratio")), &ratio)
            || ratio < 0 || ratio > 1) {
            if (error) *error = QStringLiteral("holding 基金/成分/数量/比例无效");
            return false;
        }
        query.prepare(QStringLiteral(
            "INSERT INTO holdings(fund_symbol,holding_day,holding_symbol,quantity,ratio,currency,source,payload_json) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(fund_symbol,holding_day,holding_symbol) DO UPDATE SET "
            "quantity=excluded.quantity,ratio=excluded.ratio,currency=excluded.currency,source=excluded.source,payload_json=excluded.payload_json"));
        query.addBindValue(symbol); query.addBindValue(date.toString(Qt::ISODate));
        query.addBindValue(holding); query.addBindValue(quantity); query.addBindValue(ratio);
        query.addBindValue(dataset.value(QStringLiteral("currency")).toString().toUpper());
        query.addBindValue(source);
        query.addBindValue(QString::fromUtf8(QJsonDocument(dataset).toJson(QJsonDocument::Compact)));
    } else {
        if (error) *error = QStringLiteral("不支持的数据集 kind");
        return false;
    }
    if (!query.exec()) {
        if (error) *error = query.lastError().text();
        return false;
    }
    if (symbol.isEmpty()) return true;
    QSqlQuery fund(database_);
    fund.prepare(QStringLiteral(
        "INSERT INTO funds(symbol,updated_at) VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET updated_at=excluded.updated_at"));
    fund.addBindValue(symbol);
    fund.addBindValue(iso(nowUtc()));
    return fund.exec();
}

bool UploadEngine::persistValuation(const QJsonObject &input, QString *error,
                                    QJsonObject *valuation)
{
    const QString productionKind = inferredValuationKind(input);
    if (!productionKind.isEmpty()
        && !validateProductionWireMetadata(input, error))
        return false;
    QJsonObject calculationInput = input;
    const QString requestedSymbol = normalizedSymbol(
        input.value(QStringLiteral("symbol")).toString());
    if (!calculationInput.contains(QStringLiteral("domestic"))) {
        QSqlQuery quoteQuery(database_);
        quoteQuery.prepare(QStringLiteral("SELECT payload_json FROM latest_quotes WHERE symbol=?"));
        quoteQuery.addBindValue(requestedSymbol);
        if (quoteQuery.exec() && quoteQuery.next()) {
            const QJsonObject quote = QJsonDocument::fromJson(
                quoteQuery.value(0).toByteArray()).object();
            const double price = quote.value(QStringLiteral("price")).toDouble();
            calculationInput.insert(QStringLiteral("domestic"), QJsonObject{
                {QStringLiteral("bid"), quote.value(QStringLiteral("bid")).toDouble(price)},
                {QStringLiteral("ask"), quote.value(QStringLiteral("ask")).toDouble(price)},
                {QStringLiteral("price"), price},
                {QStringLiteral("observed_at"), quote.value(QStringLiteral("observed_at"))},
                {QStringLiteral("source"), quote.value(QStringLiteral("source"))}});
        }
    }
    const QJsonObject calculated = calculateBasketValuation(calculationInput, error);
    if (calculated.isEmpty()) return false;
    const QString symbol = calculated.value(QStringLiteral("symbol")).toString();
    QString tradingDay = input.value(QStringLiteral("pcf")).toObject()
                             .value(QStringLiteral("trading_day")).toString();
    if (tradingDay.isEmpty())
        tradingDay = input.value(QStringLiteral("silver")).toObject()
                         .value(QStringLiteral("trading_day")).toString();
    if (tradingDay.isEmpty())
        tradingDay = parseTimestamp(input.value(QStringLiteral("generated_at")))
                         .toTimeZone(QTimeZone(kShanghai)).date().toString(Qt::ISODate);
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "INSERT INTO valuation_inputs(symbol,model_version,trading_day,input_json,valuation_json,updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
        "model_version=excluded.model_version,trading_day=excluded.trading_day,"
        "input_json=excluded.input_json,valuation_json=excluded.valuation_json,updated_at=excluded.updated_at"));
    query.addBindValue(symbol);
    query.addBindValue(input.value(QStringLiteral("model_version")).toString());
    query.addBindValue(tradingDay);
    query.addBindValue(QString::fromUtf8(QJsonDocument(input).toJson(QJsonDocument::Compact)));
    query.addBindValue(QString::fromUtf8(QJsonDocument(calculated).toJson(QJsonDocument::Compact)));
    query.addBindValue(iso(nowUtc()));
    if (!query.exec()) {
        if (error) *error = query.lastError().text();
        return false;
    }
    QSqlQuery fund(database_);
    fund.prepare(QStringLiteral(
        "INSERT INTO funds(symbol,updated_at) VALUES(?,?) "
        "ON CONFLICT(symbol) DO UPDATE SET updated_at=excluded.updated_at"));
    fund.addBindValue(symbol);
    fund.addBindValue(iso(nowUtc()));
    if (!fund.exec()) {
        if (error) *error = fund.lastError().text();
        return false;
    }
    if (valuation) *valuation = calculated;
    return true;
}

bool UploadEngine::recordPayload(const QString &jobId, const QJsonObject &payload,
                                 const QString &idempotencyKey, QString *error,
                                 QString *payloadSha256)
{
    const QByteArray bytes = QJsonDocument(payload).toJson(QJsonDocument::Compact);
    const QString digest = sha256(bytes);
    const QString recordId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "INSERT INTO upload_records(record_id,job_id,idempotency_key,payload_sha256,payload_json,sink_mode,created_at) "
        "VALUES(?,?,?,?,?,'record_only',?)"));
    query.addBindValue(recordId);
    query.addBindValue(jobId);
    query.addBindValue(idempotencyKey);
    query.addBindValue(digest);
    query.addBindValue(QString::fromUtf8(bytes));
    query.addBindValue(iso(nowUtc()));
    if (!query.exec()) {
        if (error) *error = query.lastError().text();
        return false;
    }
    if (payloadSha256) *payloadSha256 = digest;
    publishEvent(QStringLiteral("upload.recorded"),
                 {{QStringLiteral("record_id"), recordId},
                  {QStringLiteral("job_id"), jobId},
                  {QStringLiteral("idempotency_key"), idempotencyKey},
                  {QStringLiteral("sha256"), digest}});
    return true;
}

bool UploadEngine::collectForJob(const QString &jobId, QJsonObject *details,
                                 QString *error)
{
    if (!context_.settings.value(QStringLiteral("live_reads_enabled")).toBool(false)) {
        if (error) *error = QStringLiteral("外部只读采集默认关闭（live_reads_enabled=false）");
        return false;
    }
    const QJsonObject configs = context_.settings.value(QStringLiteral("collectors")).toObject();
    const QJsonObject config = configs.value(jobId).toObject();
    if (!config.value(QStringLiteral("enabled")).toBool(false)) {
        if (error) *error = QStringLiteral("采集器未启用: %1").arg(jobId);
        return false;
    }
    const int timeout = config.value(QStringLiteral("timeout_ms")).toInt(15000);
    const int attempts = config.value(QStringLiteral("attempts")).toInt(3);
    const QDate localDay = nowUtc().toTimeZone(QTimeZone(kShanghai)).date();
    const QString today = localDay.toString(Qt::ISODate);
    QJsonArray rows;
    int fallbackRows = 0;
    auto fetch = [&](QUrl url, const QByteArray &method = "GET", const QByteArray &body = QByteArray{},
                     const QHash<QByteArray,QByteArray> &headers = {}) -> QByteArray {
        QString fetchError;
        const auto response = collectors_->fetch(url, method, body, headers, timeout, attempts, &fetchError);
        if (!fetchError.isEmpty()) { if (error) *error = fetchError; return {}; }
        return response.body;
    };
    if (jobId == QStringLiteral("purchase-status")) {
        QHash<QString,QString> wanted; for (const auto &v:config.value("symbols").toArray()) { QString s=normalizedSymbol(v.toString()); if(!s.isEmpty()) wanted.insert(s.mid(2),s); }
        if(wanted.isEmpty()){if(error)*error=QStringLiteral("申购状态跟踪标的为空");return false;}
        QUrl url(config.value("url").toString()); QUrlQuery query(url); query.addQueryItem("t","1"); query.addQueryItem("lx","1"); query.addQueryItem("letter",""); query.addQueryItem("gsid",""); query.addQueryItem("text",""); query.addQueryItem("sort","zdf,desc"); query.addQueryItem("page","1,30000"); query.addQueryItem("dt",QString::number(nowUtc().toMSecsSinceEpoch())); url.setQuery(query);
        const QByteArray body=fetch(url); if(body.isEmpty()) return false;
        rows=UploadCollectors::parseEastmoneyPurchaseStatus(body,wanted,today,error);
    } else if (jobId == QStringLiteral("eastmoney-nav")) {
        const QString endpoint=config.value("url").toString();
        const QJsonArray symbols=config.value("symbols").toArray();if(symbols.isEmpty()){if(error)*error=QStringLiteral("净值跟踪标的为空");return false;}
        for(const auto &v:symbols){QString symbol=normalizedSymbol(v.toString());if(symbol.isEmpty())continue;QUrl url(endpoint);QUrlQuery q(url);q.addQueryItem("fundCode",symbol.mid(2));q.addQueryItem("pageIndex","1");q.addQueryItem("pageSize","200");q.addQueryItem("startDate",localDay.addDays(-std::clamp(config.value("lookback_days").toInt(30),1,3660)).toString(Qt::ISODate));q.addQueryItem("endDate",today);url.setQuery(q);const QByteArray body=fetch(url);if(body.isEmpty())return false;QString parseError;const auto parsed=UploadCollectors::parseEastmoneyNetValues(body,symbol,&parseError);if(!parseError.isEmpty()||parsed.isEmpty()){if(error)*error=parseError.isEmpty()?QStringLiteral("Eastmoney NAV 空响应: ")+symbol:parseError;return false;}for(const auto&r:parsed)rows.append(r);}
    } else if (jobId == QStringLiteral("safe-central-parity")) {
        QUrlQuery formQuery; formQuery.addQueryItem("startDate",localDay.addDays(-std::clamp(config.value("lookback_days").toInt(10),1,60)).toString(Qt::ISODate)); formQuery.addQueryItem("endDate",today); formQuery.addQueryItem("queryYN","true");
        const QByteArray form=formQuery.toString(QUrl::FullyEncoded).toUtf8();
        QString fetchError;const auto response=collectors_->fetch(QUrl(config.value("url").toString()),"POST",form,{{"Content-Type","application/x-www-form-urlencoded"},{"Referer","https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do"}},timeout,attempts,&fetchError);if(!fetchError.isEmpty()||response.body.isEmpty()){if(error)*error=fetchError.isEmpty()?QStringLiteral("SAFE 空响应"):fetchError;return false;}const QByteArray contentType=response.contentType.toLower();if(!contentType.contains("application/x-download")){if(error)*error=QStringLiteral("SAFE content-type 无效: ")+QString::fromLatin1(response.contentType);return false;}
        rows=UploadCollectors::parseSafeXls(response.body,error);
    } else if (jobId == QStringLiteral("share-history")) {
        const int maxPages=std::clamp(config.value("max_pages").toInt(100),1,500);
        const int intervalMs=std::clamp(config.value("request_interval_ms").toInt(0),0,10000);
        const int lookback=std::clamp(config.value("lookback_days").toInt(1),1,60);
        QJsonArray symbols=config.value("symbols").toArray();
        for(int offset=lookback-1;offset>=0;--offset){const QDate day=localDay.addDays(-offset);if(day.dayOfWeek()>5)continue;const QString dayText=day.toString(Qt::ISODate);
            QUrl szseBase(config.value("szse_url").toString());
            for(const auto &symbolValue:symbols){const QString symbol=normalizedSymbol(symbolValue.toString());if(!symbol.startsWith("SZ"))continue;bool matched=false;
                for(const QString &fundType:{QStringLiteral("ETF"),QStringLiteral("LOF")}){for(int page=1;page<=maxPages;++page){QUrl url=szseBase;QUrlQuery q(url);q.addQueryItem("SHOWTYPE","JSON");q.addQueryItem("CATALOGID","scsj_fund_jjgm");q.addQueryItem("jjlb",fundType);q.addQueryItem("txtStart",dayText);q.addQueryItem("txtEnd",dayText);q.addQueryItem("txtDm",symbol.mid(2));q.addQueryItem("PAGENO",QString::number(page));url.setQuery(q);const QByteArray body=fetch(url,"GET",{},{{"Referer","https://www.szse.cn/market/fund/volume/etf/index.html"},{"Accept","application/json,text/javascript,*/*;q=0.01"}});if(body.isEmpty())return false;QString parseError;QJsonArray parsed=UploadCollectors::parseSzseShares(body,&parseError);if(!parseError.isEmpty()){if(error)*error=parseError;return false;}for(const auto&r:parsed)rows.append(r);matched=matched||!parsed.isEmpty();QJsonDocument doc=QJsonDocument::fromJson(body);const QJsonObject report=doc.array().isEmpty()?QJsonObject{}:doc.array().first().toObject();const int pageCount=std::max(1,report.value("metadata").toObject().value("pagecount").toInt(1));if(page>=pageCount)break;if(intervalMs>0)QThread::msleep(unsigned(intervalMs));}if(matched)break;}
                if(intervalMs>0)QThread::msleep(unsigned(intervalMs));}
            for(const QString &fundType:{QStringLiteral("ETF"),QStringLiteral("LOF")}){QUrl base(config.value(fundType=="ETF"?"sse_etf_url":"sse_lof_url").toString());for(int page=1;page<=maxPages;++page){QUrl url=base;QUrlQuery q(url);q.addQueryItem("isPagination","true");q.addQueryItem("pageHelp.pageSize","2000");q.addQueryItem("pageHelp.pageNo",QString::number(page));q.addQueryItem("pageHelp.beginPage",QString::number(page));q.addQueryItem("pageHelp.cacheSize","1");q.addQueryItem("pageHelp.endPage",QString::number(page));if(fundType=="ETF"){q.addQueryItem("sqlId","COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L");q.addQueryItem("STAT_DATE",dayText);}else{q.addQueryItem("sqlId","COMMON_SSE_SJ_JJSJ_JJGM_LOFGMTJ_L");q.addQueryItem("PRODUCT_TYPE","11,14,15");q.addQueryItem("SEARCH_DATE",QString(dayText).remove('-'));q.addQueryItem("type","inParams");}url.setQuery(q);const QByteArray body=fetch(url,"GET",{},{{"Referer",fundType=="ETF"?"https://www.sse.com.cn/market/funddata/volumn/etfvolumn/":"https://www.sse.com.cn/market/funddata/volumn/lofvolumn/"},{"Accept","application/json,text/javascript,*/*;q=0.01"}});if(body.isEmpty())return false;QString parseError;QJsonArray parsed=UploadCollectors::parseSseShares(body,fundType,&parseError);if(!parseError.isEmpty()){if(error)*error=parseError;return false;}for(const auto&r:parsed)rows.append(r);const QJsonObject root=QJsonDocument::fromJson(body).object();const int pageCount=std::max(1,root.value("pageHelp").toObject().value("pageCount").toInt(1));if(page>=pageCount)break;if(intervalMs>0)QThread::msleep(unsigned(intervalMs));}}
        }
    } else if (jobId == QStringLiteral("pcf-prefetch")) {
        QJsonArray funds=config.value("funds").toArray();if(funds.isEmpty())funds=defaultPcfDefinitions();
        const int lookback=std::clamp(config.value("lookback_days").toInt(10),0,30);
        for(const auto &v:funds){const QJsonObject definition=v.toObject();QJsonObject parsed;QString lastFailure;const QString templateUrl=definition.value("url").toString();const bool dated=templateUrl.contains("{date}")||templateUrl.contains("{yyyymmdd}");
            for(int offset=0;offset<=lookback;++offset){const QDate candidate=localDay.addDays(-offset);if(candidate.dayOfWeek()>5)continue;if(offset>0&&!dated)break;const QString candidateDay=candidate.toString(Qt::ISODate);QString urlText=templateUrl;urlText.replace("{date}",candidateDay).replace("{yyyymmdd}",QString(candidateDay).remove('-'));QString fetchError;QHash<QByteArray,QByteArray> headers;if(definition.value("exchange").toString()=="SSE")headers.insert("Referer","https://www.sse.com.cn/");const auto response=collectors_->fetch(QUrl(urlText),"GET",{},headers,timeout,attempts,&fetchError);if(!fetchError.isEmpty()||response.body.isEmpty()){lastFailure=fetchError.isEmpty()?QStringLiteral("PCF 空响应"):fetchError;continue;}QString parseError;parsed=UploadCollectors::parsePcf(response.body,definition,urlText,candidateDay,&parseError);if(!parsed.isEmpty()){if(offset>0)++fallbackRows;break;}lastFailure=parseError;}
            if(parsed.isEmpty()){QSqlQuery cached(database_);cached.prepare("SELECT payload_json FROM pcf_cache WHERE symbol=? AND trading_day>=? ORDER BY trading_day DESC LIMIT 1");cached.addBindValue(normalizedSymbol(definition.value("symbol").toString()));cached.addBindValue(localDay.addDays(-lookback).toString(Qt::ISODate));if(cached.exec()&&cached.next()){parsed=QJsonDocument::fromJson(cached.value(0).toByteArray()).object();++fallbackRows;}}
            if(parsed.isEmpty()){if(error)*error=QStringLiteral("%1 PCF 当日与缓存 fallback 均不可用: %2").arg(definition.value("symbol").toString(),lastFailure);return false;}rows.append(parsed);}
    } else {
        if (error) *error = QStringLiteral("任务不是 live collector"); return false;
    }
    if (rows.isEmpty()) { if (error && error->isEmpty()) *error=QStringLiteral("采集成功但无有可持久化行"); return false; }
    int written=0; for(const auto &v:rows){QString persistError;if(!persistDataset(v.toObject(),&persistError)){if(error)*error=persistError;return false;}++written;}
    const QJsonObject completed{{"collector",jobId},{"fetched_rows",rows.size()},{"written_rows",written},{"fallback_rows",fallbackRows},{"live_read",true}};
    if(details)*details=completed;
    publishEvent(QStringLiteral("upload.collector_completed"),completed);
    return true;
}

bool UploadEngine::executeJob(JobRuntime &runtime,
                              const QString &idempotencyKey,
                              bool manual, QString *error,
                              QJsonObject *details)
{
    const QDateTime now = nowUtc();
    runtime.lastStartedUtc = now;
    ++runtime.runCount;
    runtime.state = QStringLiteral("active");
    runtime.stage = QStringLiteral("recording");
    QJsonObject payload{{QStringLiteral("schema_version"), 1},
                        {QStringLiteral("job_id"), runtime.definition.id},
                        {QStringLiteral("job_kind"), runtime.definition.kind},
                        {QStringLiteral("source"), runtime.definition.source},
                        {QStringLiteral("model_version"), runtime.definition.modelVersion},
                        {QStringLiteral("generated_at"), iso(now)},
                        {QStringLiteral("manual"), manual},
                        {QStringLiteral("sink_mode"), QStringLiteral("record_only")}};
    static const QSet<QString> collectorJobs{
        QStringLiteral("purchase-status"), QStringLiteral("pcf-prefetch"),
        QStringLiteral("safe-central-parity"), QStringLiteral("eastmoney-nav"),
        QStringLiteral("share-history")};
    if (collectorJobs.contains(runtime.definition.id)) {
        QJsonObject collectorDetails;
        if (!collectForJob(runtime.definition.id, &collectorDetails, error)) {
            runtime.lastFailureUtc = now;
            runtime.lastError = error ? *error : QStringLiteral("采集失败");
            runtime.state = QStringLiteral("degraded"); runtime.stage = QStringLiteral("collector_failed");
            recordJobRun(runtime,idempotencyKey,QStringLiteral("failed"),runtime.lastError);
            return false;
        }
        payload.insert(QStringLiteral("collector"),collectorDetails);
    }
    if (runtime.definition.id == QStringLiteral("sina-public")) {
        QJsonArray quotes;
        QSqlQuery query(database_);
        query.exec(QStringLiteral("SELECT payload_json FROM latest_quotes ORDER BY symbol"));
        while (query.next()) {
            const QJsonDocument document = QJsonDocument::fromJson(query.value(0).toByteArray());
            if (document.isObject()) quotes.append(document.object());
        }
        if (quotes.isEmpty()) {
            if (error) *error = QStringLiteral("尚无通过校验的 Sina 行情");
            runtime.lastFailureUtc = now;
            runtime.lastError = *error;
            runtime.state = QStringLiteral("degraded");
            runtime.stage = QStringLiteral("waiting_input");
            recordJobRun(runtime, idempotencyKey, QStringLiteral("failed"), runtime.lastError);
            return false;
        }
        payload.insert(QStringLiteral("quotes"), quotes);
        payload.insert(QStringLiteral("input_state"), QStringLiteral("ready"));
        payload.insert(QStringLiteral("request"), QJsonObject{
            {QStringLiteral("method"), QStringLiteral("POST")},
            {QStringLiteral("path"), QStringLiteral("/api/v1/uploads/quotes")},
            {QStringLiteral("content_encoding"), QStringLiteral("identity")},
            {QStringLiteral("body"), QJsonObject{
                 {QStringLiteral("source"), runtime.definition.source},
                 {QStringLiteral("quotes"), quotes}}}});
        payload.insert(QStringLiteral("expected_ack"), QJsonObject{
            {QStringLiteral("accepted"), quotes.size()},
            {QStringLiteral("enabled_type"), QStringLiteral("boolean")}});
    } else if (runtime.definition.kind == QStringLiteral("uploader")) {
        QJsonArray valuations;
        QJsonArray inputs;
        QSqlQuery query(database_);
        query.exec(QStringLiteral("SELECT input_json,valuation_json FROM valuation_inputs ORDER BY symbol"));
        while (query.next()) {
            const QJsonDocument inputDocument = QJsonDocument::fromJson(query.value(0).toByteArray());
            const QJsonDocument valuationDocument = QJsonDocument::fromJson(query.value(1).toByteArray());
            if (inputDocument.isObject() && valuationDocument.isObject()
                && valuationBelongsToJob(runtime.definition.id, valuationDocument.object())) {
                inputs.append(inputDocument.object());
                valuations.append(valuationDocument.object());
            }
        }
        if (valuations.isEmpty()) {
            if (error) *error = QStringLiteral("尚无属于该上传器且通过校验的估值");
            runtime.lastFailureUtc = now;
            runtime.lastError = *error;
            runtime.state = QStringLiteral("degraded");
            runtime.stage = QStringLiteral("waiting_input");
            recordJobRun(runtime, idempotencyKey, QStringLiteral("failed"), runtime.lastError);
            return false;
        }
        payload.insert(QStringLiteral("valuations"), valuations);
        payload.insert(QStringLiteral("inputs"), inputs);
        payload.insert(QStringLiteral("input_state"), QStringLiteral("ready"));
        const bool batch = runtime.definition.id == QStringLiteral("xop-family")
            || runtime.definition.id == QStringLiteral("nasdaq")
            || runtime.definition.id == QStringLiteral("sp500")
            || runtime.definition.id == QStringLiteral("nikkei225")
            || runtime.definition.id == QStringLiteral("germany")
            || runtime.definition.id == QStringLiteral("china-internet");
        if (batch) {
            QJsonArray symbols;
            for (const auto &item : inputs)
                symbols.append(item.toObject().value(QStringLiteral("symbol")));
            const QJsonObject body{{QStringLiteral("schema_version"), 1},
                                   {QStringLiteral("batch_id"), idempotencyKey},
                                   {QStringLiteral("source"), runtime.definition.source},
                                   {QStringLiteral("generated_at"), iso(now)},
                                   {QStringLiteral("inputs"), inputs}};
            payload.insert(QStringLiteral("request"), QJsonObject{
                {QStringLiteral("method"), QStringLiteral("POST")},
                {QStringLiteral("path"), QStringLiteral("/api/v1/private/inputs/batch")},
                {QStringLiteral("content_encoding"), QStringLiteral("gzip")},
                {QStringLiteral("body"), body}});
            payload.insert(QStringLiteral("expected_ack"), QJsonObject{
                {QStringLiteral("batch_id"), idempotencyKey},
                {QStringLiteral("accepted_symbols"), symbols},
                {QStringLiteral("rejected_count"), 0}});
        } else {
            const QJsonObject body = inputs.first().toObject();
            const QString symbol = body.value(QStringLiteral("symbol")).toString();
            payload.insert(QStringLiteral("request"), QJsonObject{
                {QStringLiteral("method"), QStringLiteral("POST")},
                {QStringLiteral("path"), QStringLiteral("/api/v1/private/inputs/%1").arg(symbol)},
                {QStringLiteral("content_encoding"), QStringLiteral("gzip")},
                {QStringLiteral("body"), body}});
            payload.insert(QStringLiteral("expected_ack"), QJsonObject{
                {QStringLiteral("ok"), true}, {QStringLiteral("symbol"), symbol}});
        }
    } else {
        payload.insert(QStringLiteral("operation"), runtime.definition.id);
        payload.insert(QStringLiteral("network_mutation"), false);
        const QString closeMarket = closeMarketForJob(runtime.definition.id);
        if (!closeMarket.isEmpty()) {
            const QDateTime marketNow = now.toTimeZone(
                QTimeZone(runtime.definition.timezone.toUtf8()));
            const QDate closeDate = marketNow.date();
            int persisted = 0;
            const bool marketWeekend = marketNow.date().dayOfWeek() > 5;
            if (!marketWeekend) {
                QSqlQuery load(database_);
                load.exec(QStringLiteral("SELECT symbol,price,source,payload_json FROM latest_quotes"));
                while (load.next()) {
                    const QString symbol = load.value(0).toString();
                    const QJsonObject quote = QJsonDocument::fromJson(
                        load.value(3).toByteArray()).object();
                    if (!quoteBelongsToCloseMarket(closeMarket, symbol, quote)) continue;
                    const double price = load.value(1).toDouble();
                    if (!(price > 0) || !std::isfinite(price)
                        || quote.value(QStringLiteral("source")).toString() == QStringLiteral("demo"))
                        continue;
                    QSqlQuery store(database_);
                    store.prepare(QStringLiteral(
                        "INSERT INTO daily_prices(symbol,trade_date,close,source,payload_json) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET "
                        "close=excluded.close,source=excluded.source,payload_json=excluded.payload_json"));
                    store.addBindValue(symbol);
                    store.addBindValue(closeDate.toString(Qt::ISODate));
                    store.addBindValue(price);
                    store.addBindValue(QStringLiteral("native_daily_close:") + closeMarket);
                    store.addBindValue(QString::fromUtf8(
                        QJsonDocument(quote).toJson(QJsonDocument::Compact)));
                    if (store.exec()) ++persisted;
                }
            }
            payload.insert(QStringLiteral("market"), closeMarket);
            payload.insert(QStringLiteral("trade_date"), closeDate.toString(Qt::ISODate));
            payload.insert(QStringLiteral("persisted_rows"), persisted);
            payload.insert(QStringLiteral("market_weekend_skipped"), marketWeekend);
        }
        QSqlQuery counts(database_);
        counts.exec(QStringLiteral(
            "SELECT (SELECT count(*) FROM funds),(SELECT count(*) FROM valuation_inputs),"
            "(SELECT count(*) FROM latest_quotes),(SELECT count(*) FROM daily_prices),"
            "(SELECT count(*) FROM net_values),(SELECT count(*) FROM share_history)"));
        if (counts.next()) {
            payload.insert(QStringLiteral("repository_counts"), QJsonObject{
                {QStringLiteral("funds"), counts.value(0).toInt()},
                {QStringLiteral("valuations"), counts.value(1).toInt()},
                {QStringLiteral("quotes"), counts.value(2).toInt()},
                {QStringLiteral("daily_prices"), counts.value(3).toInt()},
                {QStringLiteral("net_values"), counts.value(4).toInt()},
                {QStringLiteral("share_history"), counts.value(5).toInt()}});
        }
    }
    QString digest;
    if (!recordPayload(runtime.definition.id, payload, idempotencyKey,
                       error, &digest)) {
        runtime.lastFailureUtc = now;
        runtime.lastError = error ? *error : QStringLiteral("记录失败");
        runtime.state = QStringLiteral("degraded");
        runtime.stage = QStringLiteral("failed");
        recordJobRun(runtime, idempotencyKey, QStringLiteral("failed"), runtime.lastError);
        return false;
    }
    runtime.lastSuccessUtc = now;
    runtime.lastError.clear();
    runtime.acceptedCount++;
    runtime.stage = QStringLiteral("recorded");
    recordJobRun(runtime, idempotencyKey, QStringLiteral("succeeded"));
    if (details) {
        *details = {{QStringLiteral("job_id"), runtime.definition.id},
                    {QStringLiteral("idempotency_key"), idempotencyKey},
                    {QStringLiteral("payload_sha256"), digest},
                    {QStringLiteral("sink_mode"), QStringLiteral("record_only")}};
        if (payload.contains(QStringLiteral("collector")))
            details->insert(QStringLiteral("collector"),
                            payload.value(QStringLiteral("collector")));
    }
    return true;
}

bool UploadEngine::alreadyCompleted(const QString &jobId,
                                    const QString &idempotencyKey) const
{
    if (jobId == QStringLiteral("safe-central-parity")) {
        QSqlQuery parity(database_);
        parity.prepare(QStringLiteral(
            "SELECT 1 FROM fx_rates WHERE pair='USDCNY' AND rate_date=? LIMIT 1"));
        parity.addBindValue(idempotencyKey);
        if (parity.exec() && parity.next()) return true;
    }
    if (jobId == QStringLiteral("purchase-status")
        && QDate::fromString(idempotencyKey, Qt::ISODate).isValid()) {
        QSqlQuery fresh(database_);
        fresh.prepare(QStringLiteral(
            "SELECT 1 FROM purchase_status WHERE status_date=? LIMIT 1"));
        fresh.addBindValue(idempotencyKey);
        if (fresh.exec() && fresh.next()) return true;
    }
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "SELECT 1 FROM upload_job_runs WHERE job_id=? AND idempotency_key=? AND state='succeeded'"));
    query.addBindValue(jobId);
    query.addBindValue(idempotencyKey);
    return query.exec() && query.next();
}

void UploadEngine::recordJobRun(const JobRuntime &runtime,
                                const QString &idempotencyKey,
                                const QString &state, const QString &error)
{
    QSqlQuery run(database_);
    run.prepare(QStringLiteral(
        "INSERT INTO upload_job_runs(job_id,idempotency_key,state,started_at,finished_at,error) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,idempotency_key) DO UPDATE SET "
        "state=excluded.state,finished_at=excluded.finished_at,error=excluded.error"));
    run.addBindValue(runtime.definition.id);
    run.addBindValue(idempotencyKey);
    run.addBindValue(state);
    run.addBindValue(iso(runtime.lastStartedUtc));
    run.addBindValue(iso(nowUtc()));
    run.addBindValue(error);
    run.exec();
    persistJobState(runtime);
}

void UploadEngine::persistJobState(const JobRuntime &runtime)
{
    QSqlQuery job(database_);
    job.prepare(QStringLiteral(
        "INSERT INTO upload_job_state(job_id,last_started_at,last_success_at,last_failure_at,run_count,last_error) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
        "last_started_at=excluded.last_started_at,last_success_at=excluded.last_success_at,"
        "last_failure_at=excluded.last_failure_at,run_count=excluded.run_count,last_error=excluded.last_error"));
    job.addBindValue(runtime.definition.id);
    job.addBindValue(iso(runtime.lastStartedUtc));
    job.addBindValue(iso(runtime.lastSuccessUtc));
    job.addBindValue(iso(runtime.lastFailureUtc));
    job.addBindValue(static_cast<qulonglong>(runtime.runCount));
    job.addBindValue(runtime.lastError);
    job.exec();
}

QJsonObject UploadEngine::snapshot() const
{
    if(business_){
        auto result=business_->snapshot();
        result.insert("engine","native");result.insert("business_engine","bundled_business");
        result.insert("sink_mode","bundled_business");result.insert("running",running_ && result.value("process_running").toBool());
        result.insert("operating_mode",operatingMode_);result.insert("data_root",context_.dataRoot);
        return result;
    }
    const bool helperEnabled = context_.settings.value(QStringLiteral("ibkr"))
                                   .toObject().value(QStringLiteral("enabled")).toBool(false);
    return {{QStringLiteral("engine"), QStringLiteral("native")},
            {QStringLiteral("state"), state_},
            {QStringLiteral("running"), running_},
            {QStringLiteral("operating_mode"), operatingMode_},
            {QStringLiteral("ready"), initialized_ && running_ && lastError_.isEmpty()},
            {QStringLiteral("record_only"), context_.recordOnly},
            {QStringLiteral("sink_mode"), QStringLiteral("record_only")},
            {QStringLiteral("manual_time_window_bypass"),
             operatingMode_ == QStringLiteral("weekend_test")},
            {QStringLiteral("automatic_schedule_bypass"), false},
            {QStringLiteral("data_root"), context_.dataRoot},
            {QStringLiteral("database"), databasePath()},
            {QStringLiteral("last_error"), lastError_},
            {QStringLiteral("jobs"), jobSnapshots()},
            {QStringLiteral("funds"), fundSnapshots()},
            {QStringLiteral("upload_records"), recentUploadRecords()},
            {QStringLiteral("job_runs"), recentJobRuns()},
            {QStringLiteral("history"), historySnapshots()},
            {QStringLiteral("ibkr"), QJsonObject{
                {QStringLiteral("enabled"), helperEnabled},
                {QStringLiteral("compiled"), bool(MACHOME_NATIVE_IBKR_BRIDGE_AVAILABLE)},
                {QStringLiteral("owned_by_engine"), true},
                {QStringLiteral("running"), ibkrHelper_.state() != QProcess::NotRunning},
                {QStringLiteral("socket_connected"),
                 ibkrSocket_.state() == QLocalSocket::ConnectedState},
                {QStringLiteral("handshake_complete"), ibkrHandshakeComplete_},
                {QStringLiteral("last_quote_at"), iso(ibkrLastQuoteUtc_)},
                {QStringLiteral("pid"), static_cast<qint64>(ibkrHelper_.processId())}}}};
}

QJsonArray UploadEngine::jobSnapshots() const
{
    QJsonArray result;
    QStringList ids = jobs_.keys();
    std::sort(ids.begin(), ids.end());
    for (const QString &id : ids) {
        const JobRuntime &runtime = jobs_.value(id);
        QJsonArray windows;
        for (const auto &[start, end] : runtime.definition.windows) {
            windows.append(QJsonObject{{QStringLiteral("start"), start.toString("HH:mm")},
                                       {QStringLiteral("end"), end.toString("HH:mm")}});
        }
        result.append(QJsonObject{
            {QStringLiteral("id"), id},
            {QStringLiteral("source"), runtime.definition.source},
            {QStringLiteral("display_name"), runtime.definition.displayName},
            {QStringLiteral("kind"), runtime.definition.kind},
            {QStringLiteral("state"), runtime.state},
            {QStringLiteral("stage"), runtime.stage},
            {QStringLiteral("timezone"), runtime.definition.timezone},
            {QStringLiteral("run_at"), runtime.definition.runAt.isValid()
                 ? runtime.definition.runAt.toString("HH:mm") : QString{}},
            {QStringLiteral("windows"), windows},
            {QStringLiteral("interval_seconds"), runtime.definition.intervalSeconds},
            {QStringLiteral("model_version"), runtime.definition.modelVersion},
            {QStringLiteral("last_started_at"), iso(runtime.lastStartedUtc)},
            {QStringLiteral("last_success_at"), iso(runtime.lastSuccessUtc)},
            {QStringLiteral("last_failure_at"), iso(runtime.lastFailureUtc)},
            {QStringLiteral("next_run_at"), iso(runtime.nextRunUtc)},
            {QStringLiteral("last_error"), runtime.lastError},
            {QStringLiteral("run_count"), static_cast<qint64>(runtime.runCount)},
            {QStringLiteral("accepted_count"), static_cast<qint64>(runtime.acceptedCount)}});
    }
    return result;
}

QJsonArray UploadEngine::fundSnapshots() const
{
    QJsonArray result;
    if (!database_.isOpen()) return result;
    QSqlQuery query(database_);
    query.exec(QStringLiteral(
        "SELECT f.symbol,f.name,f.branch,f.nav,f.shares,f.position_ratio,f.updated_at,"
        "q.price,v.valuation_json FROM funds f "
        "LEFT JOIN latest_quotes q ON q.symbol=f.symbol "
        "LEFT JOIN valuation_inputs v ON v.symbol=f.symbol ORDER BY f.symbol LIMIT 2000"));
    while (query.next()) {
        QJsonObject value{{QStringLiteral("symbol"), query.value(0).toString()},
                          {QStringLiteral("name"), query.value(1).toString()},
                          {QStringLiteral("branch"), query.value(2).toString()},
                          {QStringLiteral("nav"), QJsonValue::fromVariant(query.value(3))},
                          {QStringLiteral("shares"), QJsonValue::fromVariant(query.value(4))},
                          {QStringLiteral("position_ratio"), QJsonValue::fromVariant(query.value(5))},
                          {QStringLiteral("updated_at"), query.value(6).toString()},
                          {QStringLiteral("price"), QJsonValue::fromVariant(query.value(7))}};
        const QJsonDocument valuation = QJsonDocument::fromJson(query.value(8).toByteArray());
        if (valuation.isObject()) value.insert(QStringLiteral("valuation"), valuation.object());
        result.append(value);
    }
    return result;
}

QJsonArray UploadEngine::recentUploadRecords(int limit) const
{
    QJsonArray result;
    if (!database_.isOpen()) return result;
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "SELECT record_id,job_id,idempotency_key,payload_sha256,sink_mode,created_at,acked_at "
        "FROM upload_records ORDER BY created_at DESC LIMIT ?"));
    query.addBindValue(limit);
    if (!query.exec()) return result;
    while (query.next()) {
        result.append(QJsonObject{{QStringLiteral("record_id"), query.value(0).toString()},
                                  {QStringLiteral("job_id"), query.value(1).toString()},
                                  {QStringLiteral("idempotency_key"), query.value(2).toString()},
                                  {QStringLiteral("sha256"), query.value(3).toString()},
                                  {QStringLiteral("sink_mode"), query.value(4).toString()},
                                  {QStringLiteral("created_at"), query.value(5).toString()},
                                  {QStringLiteral("acked_at"), query.value(6).toString()}});
    }
    return result;
}

QJsonArray UploadEngine::recentJobRuns(int limit) const
{
    QJsonArray result;
    if (!database_.isOpen()) return result;
    QSqlQuery query(database_);
    query.prepare(QStringLiteral(
        "SELECT job_id,idempotency_key,state,started_at,finished_at,error "
        "FROM upload_job_runs ORDER BY id DESC LIMIT ?"));
    query.addBindValue(limit);
    if (!query.exec()) return result;
    while (query.next()) {
        result.append(QJsonObject{{QStringLiteral("job_id"), query.value(0).toString()},
                                  {QStringLiteral("idempotency_key"), query.value(1).toString()},
                                  {QStringLiteral("state"), query.value(2).toString()},
                                  {QStringLiteral("started_at"), query.value(3).toString()},
                                  {QStringLiteral("finished_at"), query.value(4).toString()},
                                  {QStringLiteral("error"), query.value(5).toString()}});
    }
    return result;
}

QJsonObject UploadEngine::historySnapshots(int limit) const
{
    QJsonArray navs, shares, prices, holdings, purchases, rates, pcfs;
    if (!database_.isOpen()) return {};
    auto collect = [this, limit](const QString &sql, auto row) {
        QJsonArray result;
        QSqlQuery query(database_);
        query.prepare(sql);
        query.addBindValue(limit);
        if (query.exec()) while (query.next()) result.append(row(query));
        return result;
    };
    navs = collect(QStringLiteral(
        "SELECT symbol,nav_date,nav,source FROM net_values ORDER BY nav_date DESC,symbol LIMIT ?"),
        [](const QSqlQuery &q) { return QJsonObject{{QStringLiteral("symbol"), q.value(0).toString()},
            {QStringLiteral("date"), q.value(1).toString()}, {QStringLiteral("nav"), q.value(2).toDouble()},
            {QStringLiteral("source"), q.value(3).toString()}}; });
    shares = collect(QStringLiteral(
        "SELECT symbol,share_date,shares_10k,source FROM share_history ORDER BY share_date DESC,symbol LIMIT ?"),
        [](const QSqlQuery &q) { return QJsonObject{{QStringLiteral("symbol"), q.value(0).toString()},
            {QStringLiteral("date"), q.value(1).toString()}, {QStringLiteral("shares_10k"), q.value(2).toDouble()},
            {QStringLiteral("source"), q.value(3).toString()}}; });
    prices = collect(QStringLiteral(
        "SELECT symbol,trade_date,close,source FROM daily_prices ORDER BY trade_date DESC,symbol LIMIT ?"),
        [](const QSqlQuery &q) { return QJsonObject{{QStringLiteral("symbol"), q.value(0).toString()},
            {QStringLiteral("date"), q.value(1).toString()}, {QStringLiteral("close"), q.value(2).toDouble()},
            {QStringLiteral("source"), q.value(3).toString()}}; });
    holdings = collect(QStringLiteral(
        "SELECT fund_symbol,holding_day,holding_symbol,quantity,ratio,currency,source "
        "FROM holdings ORDER BY holding_day DESC,fund_symbol,holding_symbol LIMIT ?"),
        [](const QSqlQuery &q) { return QJsonObject{{QStringLiteral("symbol"), q.value(0).toString()},
            {QStringLiteral("date"), q.value(1).toString()}, {QStringLiteral("holding_symbol"), q.value(2).toString()},
            {QStringLiteral("quantity"), q.value(3).toDouble()}, {QStringLiteral("ratio"), q.value(4).toDouble()},
            {QStringLiteral("currency"), q.value(5).toString()}, {QStringLiteral("source"), q.value(6).toString()}}; });
    purchases = collect(QStringLiteral(
        "SELECT symbol,status_date,status,daily_limit_yuan,source FROM purchase_status "
        "ORDER BY status_date DESC,symbol LIMIT ?"),
        [](const QSqlQuery &q) { return QJsonObject{{QStringLiteral("symbol"), q.value(0).toString()},
            {QStringLiteral("date"), q.value(1).toString()}, {QStringLiteral("status"), q.value(2).toString()},
            {QStringLiteral("daily_limit_yuan"), q.value(3).toDouble()}, {QStringLiteral("source"), q.value(4).toString()}}; });
    rates = collect(QStringLiteral(
        "SELECT pair,rate_date,rate,source FROM fx_rates ORDER BY rate_date DESC,pair LIMIT ?"),
        [](const QSqlQuery &q) { return QJsonObject{{QStringLiteral("symbol"), q.value(0).toString()},
            {QStringLiteral("date"), q.value(1).toString()}, {QStringLiteral("rate"), q.value(2).toDouble()},
            {QStringLiteral("source"), q.value(3).toString()}}; });
    pcfs = collect(QStringLiteral(
        "SELECT symbol,trading_day,sha256,source_url FROM pcf_cache "
        "ORDER BY trading_day DESC,symbol LIMIT ?"),
        [](const QSqlQuery &q) { return QJsonObject{{QStringLiteral("symbol"), q.value(0).toString()},
            {QStringLiteral("date"), q.value(1).toString()}, {QStringLiteral("sha256"), q.value(2).toString()},
            {QStringLiteral("source_url"), q.value(3).toString()}}; });
    return {{QStringLiteral("net_values"), navs},
            {QStringLiteral("share_history"), shares},
            {QStringLiteral("daily_prices"), prices},
            {QStringLiteral("holdings"), holdings},
            {QStringLiteral("purchase_status"), purchases},
            {QStringLiteral("fx_rates"), rates},
            {QStringLiteral("pcf_cache"), pcfs}};
}

void UploadEngine::publishSnapshot()
{
    QJsonObject value = snapshot();
    value.insert(QStringLiteral("sequence"), static_cast<qint64>(++sequence_));
    value.insert(QStringLiteral("observed_at"), iso(nowUtc()));
    emit snapshotReady(value);
}

void UploadEngine::publishEvent(const QString &kind, const QJsonObject &payload)
{
    QJsonObject event = payload;
    event.insert(QStringLiteral("observed_at"), iso(nowUtc()));
    emit eventReady(kind, event);
}

bool UploadEngine::validateHelperConfiguration(QString *error) const
{
    const QJsonObject options = context_.settings.value(QStringLiteral("ibkr")).toObject();
    if (!options.value(QStringLiteral("enabled")).toBool(false)) return true;
#if !MACHOME_NATIVE_IBKR_BRIDGE_AVAILABLE
    if (error) *error = QStringLiteral(
        "本构建未包含 IBKR C++ SDK helper；需使用 MACHOME_BUILD_NATIVE_IBKR_BRIDGE=ON "
        "并配置 MACHOME_IBKR_CPP_API_ROOT 重新构建");
    return false;
#endif
    const QString program = QDir::cleanPath(options.value(QStringLiteral("program")).toString());
    const QString config = QDir::cleanPath(options.value(QStringLiteral("config")).toString());
    const QString bundle = QDir::cleanPath(QCoreApplication::applicationDirPath());
    if (program.isEmpty() || !QFileInfo(program).isExecutable()
        || (program != bundle + QStringLiteral("/machome-ibkr-bridge"))) {
        if (error) *error = QStringLiteral("IBKR helper 只允许四合一 bundle 内 machome-ibkr-bridge");
        return false;
    }
    const QString allowedConfig = QDir(context_.dataRoot).filePath(QStringLiteral("ibkr-bridge.json"));
    if (config != allowedConfig || !QFileInfo::exists(config)) {
        if (error) *error = QStringLiteral("IBKR helper config 必须位于 Upload 数据目录");
        return false;
    }
    machome::ibkr::BridgeConfig bridge;
    QString bridgeError;
    if (!machome::ibkr::BridgeConfig::loadFile(config, &bridge, &bridgeError)) {
        if (error) *error = QStringLiteral("IBKR helper config 无效: %1").arg(bridgeError);
        return false;
    }
    const QString root = QDir::cleanPath(context_.dataRoot) + u'/';
    if (!QDir::cleanPath(bridge.socketPath).startsWith(root)
        || !QDir::cleanPath(bridge.healthFile).startsWith(root)) {
        if (error) *error = QStringLiteral("IBKR socket/health 必须位于 Upload 数据目录");
        return false;
    }
    const auto &schedule = bridge.connectionSchedule;
    if (!schedule.enabled || schedule.timezone.id() != QByteArray("Asia/Shanghai")
        || schedule.start != QTime(9, 0) || schedule.stop != QTime(15, 6)
        || schedule.weekdays != QSet<int>({1, 2, 3, 4, 5})) {
        if (error) *error = QStringLiteral("IBKR TWS 连接调度必须为工作日 09:00–15:06 Asia/Shanghai");
        return false;
    }
    return true;
}

QJsonObject UploadEngine::ibkrConfigurationForOperatingMode(
    const QJsonObject &configuration, const QString &mode, QString *error)
{
    if (mode != QStringLiteral("work")
        && mode != QStringLiteral("weekend_test")) {
        if (error) *error = QStringLiteral("IBKR 运行模式无效");
        return {};
    }
    if (mode == QStringLiteral("work")) return configuration;
    if (!configuration.value(QStringLiteral("connection_schedule")).isObject()) {
        if (error) *error = QStringLiteral("IBKR config 缺少 connection_schedule");
        return {};
    }
    QJsonObject result = configuration;
    QJsonObject schedule = result.value(QStringLiteral("connection_schedule")).toObject();
    schedule.insert(QStringLiteral("enabled"), false);
    result.insert(QStringLiteral("connection_schedule"), schedule);
    return result;
}

QString UploadEngine::ibkrConfigurationPathForOperatingMode(QString *error) const
{
    const QJsonObject options = context_.settings.value(QStringLiteral("ibkr")).toObject();
    const QString configuredPath = QDir::cleanPath(
        options.value(QStringLiteral("config")).toString());
    if (operatingMode_ == QStringLiteral("work")) return configuredPath;

    QFile source(configuredPath);
    if (!source.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("IBKR config 无法读取: %1")
                                .arg(source.errorString());
        return {};
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(source.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error) *error = QStringLiteral("IBKR config JSON 无效");
        return {};
    }
    QString transformError;
    const QJsonObject transformed = ibkrConfigurationForOperatingMode(
        document.object(), operatingMode_, &transformError);
    if (transformed.isEmpty()) {
        if (error) *error = transformError;
        return {};
    }

    QDir dataRoot(context_.dataRoot);
    if (!dataRoot.mkpath(QStringLiteral("runtime"))) {
        if (error) *error = QStringLiteral("无法创建 Upload IBKR 运行时目录");
        return {};
    }
    const QString runtimePath = dataRoot.filePath(
        QStringLiteral("runtime/ibkr-bridge-weekend-test.json"));
    QSaveFile output(runtimePath);
    if (!output.open(QIODevice::WriteOnly)
        || output.write(QJsonDocument(transformed).toJson(QJsonDocument::Indented)) < 0
        || !output.commit()) {
        if (error) *error = QStringLiteral("无法写入 Upload IBKR 周末测试配置: %1")
                                .arg(output.errorString());
        return {};
    }
    QFile::setPermissions(runtimePath, QFileDevice::ReadOwner
                                           | QFileDevice::WriteOwner);

    machome::ibkr::BridgeConfig verification;
    QString verificationError;
    if (!machome::ibkr::BridgeConfig::loadFile(
            runtimePath, &verification, &verificationError)
        || verification.connectionSchedule.enabled) {
        if (error) *error = QStringLiteral("IBKR 周末测试配置验证失败: %1")
                                .arg(verificationError);
        return {};
    }
    return runtimePath;
}

void UploadEngine::startIbkrHelper()
{
    const QJsonObject options = context_.settings.value(QStringLiteral("ibkr")).toObject();
    if (!options.value(QStringLiteral("enabled")).toBool(false)) return;
    QString error;
    if (!validateHelperConfiguration(&error)) {
        lastError_ = error;
        state_ = QStringLiteral("degraded");
        publishEvent(QStringLiteral("upload.ibkr_error"),
                     {{QStringLiteral("message"), error}});
        return;
    }
    const QString helperConfig = ibkrConfigurationPathForOperatingMode(&error);
    if (helperConfig.isEmpty()) {
        lastError_ = error;
        state_ = QStringLiteral("degraded");
        publishEvent(QStringLiteral("upload.ibkr_error"),
                     {{QStringLiteral("message"), error}});
        return;
    }
    machome::ibkr::BridgeConfig bridge;
    QString bridgeError;
    if (!machome::ibkr::BridgeConfig::loadFile(
            helperConfig, &bridge, &bridgeError)) {
        lastError_ = QStringLiteral("IBKR helper config 无法加载: %1").arg(bridgeError);
        state_ = QStringLiteral("degraded");
        publishEvent(QStringLiteral("upload.ibkr_error"),
                     {{QStringLiteral("message"), lastError_}});
        return;
    }
    ibkrSocketPath_ = bridge.socketPath;
    ibkrHelper_.setProgram(options.value(QStringLiteral("program")).toString());
    ibkrHelper_.setArguments({QStringLiteral("--config"), helperConfig});
    ibkrHelper_.setWorkingDirectory(context_.dataRoot);
    ibkrHelper_.setProcessChannelMode(QProcess::SeparateChannels);
    ibkrHelper_.start();
}

void UploadEngine::stopIbkrHelper()
{
    ibkrPollTimer_.stop();
    ibkrHandshakeComplete_ = false;
    ibkrInputBuffer_.clear();
    ibkrSocket_.abort();
    if (ibkrHelper_.state() == QProcess::NotRunning) return;
    ibkrHelper_.terminate();
    if (!ibkrHelper_.waitForFinished(3000)) {
        ibkrHelper_.kill();
        ibkrHelper_.waitForFinished(1000);
    }
}

void UploadEngine::connectIbkrSocket()
{
    if (!running_ || ibkrSocketPath_.isEmpty()
        || ibkrHelper_.state() == QProcess::NotRunning
        || ibkrSocket_.state() != QLocalSocket::UnconnectedState)
        return;
    ibkrSocket_.connectToServer(ibkrSocketPath_, QIODevice::ReadWrite);
}

void UploadEngine::writeIbkrRequest(const QString &type)
{
    if (ibkrSocket_.state() != QLocalSocket::ConnectedState) return;
    QJsonObject request{{QStringLiteral("request_id"),
                         QUuid::createUuid().toString(QUuid::WithoutBraces)},
                        {QStringLiteral("type"), type}};
    if (type == QStringLiteral("hello"))
        request.insert(QStringLiteral("protocol"),
                       QString::fromLatin1(machome::ibkr::kBridgeProtocol));
    ibkrSocket_.write(QJsonDocument(request).toJson(QJsonDocument::Compact) + '\n');
}

void UploadEngine::pollIbkrQuotes()
{
    if (ibkrHandshakeComplete_) writeIbkrRequest(QStringLiteral("quotes"));
}

void UploadEngine::readIbkrSocket()
{
    ibkrInputBuffer_.append(ibkrSocket_.readAll());
    constexpr qsizetype maximumBytes = 1024 * 1024;
    if (ibkrInputBuffer_.size() > maximumBytes) {
        publishEvent(QStringLiteral("upload.ibkr_protocol_error"),
                     {{QStringLiteral("message"), QStringLiteral("IBKR 帧缓冲超过 1 MiB")}});
        ibkrSocket_.abort();
        return;
    }
    while (true) {
        const qsizetype newline = ibkrInputBuffer_.indexOf('\n');
        if (newline < 0) break;
        const QByteArray line = ibkrInputBuffer_.left(newline).trimmed();
        ibkrInputBuffer_.remove(0, newline + 1);
        if (line.isEmpty()) continue;
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            publishEvent(QStringLiteral("upload.ibkr_protocol_error"),
                         {{QStringLiteral("message"), QStringLiteral("IBKR 返回了非法 JSON 帧")}});
            continue;
        }
        handleIbkrFrame(document.object());
    }
}

void UploadEngine::handleIbkrFrame(const QJsonObject &frame)
{
    const QString type = frame.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("hello")) {
        if (!frame.value(QStringLiteral("ok")).toBool(false)) {
            publishEvent(QStringLiteral("upload.ibkr_protocol_error"),
                         {{QStringLiteral("message"), QStringLiteral("IBKR hello 被拒绝")}});
            ibkrSocket_.abort();
            return;
        }
        ibkrHandshakeComplete_ = true;
        ibkrPollTimer_.start();
        pollIbkrQuotes();
        return;
    }
    if (type == QStringLiteral("error")) {
        publishEvent(QStringLiteral("upload.ibkr_protocol_error"), frame);
        return;
    }
    if (type != QStringLiteral("quotes")
        || !frame.value(QStringLiteral("ok")).toBool(false)
        || !frame.value(QStringLiteral("items")).isArray())
        return;
    int accepted = 0;
    int rejected = 0;
    for (const auto &value : frame.value(QStringLiteral("items")).toArray()) {
        if (!value.isObject()) {
            ++rejected;
            continue;
        }
        QString error;
        const QJsonObject quote = quoteFromIbkrItem(value.toObject(), &error);
        if (quote.isEmpty() || !persistQuote(quote, &error)) {
            ++rejected;
            publishEvent(QStringLiteral("upload.ibkr_quote_rejected"),
                         {{QStringLiteral("message"), error}});
            continue;
        }
        ++accepted;
        ibkrLastQuoteUtc_ = parseTimestamp(quote.value(QStringLiteral("observed_at"))).toUTC();
        publishEvent(QStringLiteral("upload.ibkr_quote"), quote);
    }
    publishEvent(QStringLiteral("upload.ibkr_batch"),
                 {{QStringLiteral("bridge_ready"),
                   frame.value(QStringLiteral("bridge_ready")).toBool(false)},
                  {QStringLiteral("accepted"), accepted},
                  {QStringLiteral("rejected"), rejected}});
    publishSnapshot();
}

void UploadEngine::helperFinished(int exitCode, QProcess::ExitStatus status)
{
    if (!running_) return;
    lastError_ = QStringLiteral("IBKR helper 退出 code=%1 status=%2")
                     .arg(exitCode).arg(static_cast<int>(status));
    state_ = QStringLiteral("degraded");
    publishEvent(QStringLiteral("upload.ibkr_exit"),
                 {{QStringLiteral("exit_code"), exitCode},
                  {QStringLiteral("exit_status"), static_cast<int>(status)}});
    publishSnapshot();
}

void UploadEngine::helperError(QProcess::ProcessError error)
{
    if (!running_) return;
    lastError_ = QStringLiteral("IBKR helper 错误 %1: %2")
                     .arg(static_cast<int>(error)).arg(ibkrHelper_.errorString());
    state_ = QStringLiteral("degraded");
    publishEvent(QStringLiteral("upload.ibkr_error"),
                 {{QStringLiteral("message"), lastError_}});
    publishSnapshot();
}

void UploadEngine::setNowForTest(const QDateTime &utcNow)
{
    testNowUtc_ = utcNow.toUTC();
}

void UploadEngine::clearNowForTest()
{
    testNowUtc_ = {};
}

void UploadEngine::evaluateSchedulesForTest()
{
    evaluateSchedules();
}

QDateTime UploadEngine::nowUtc() const
{
    return testNowUtc_.isValid() ? testNowUtc_ : QDateTime::currentDateTimeUtc();
}

QString UploadEngine::databasePath() const
{
    return database_.isValid() ? database_.databaseName()
                               : QDir(context_.dataRoot).filePath(QStringLiteral("upload.sqlite3"));
}

bool UploadEngine::isRecordOnly() const
{
    return context_.recordOnly;
}

QString UploadEngine::iso(const QDateTime &value)
{
    return value.isValid() ? value.toUTC().toString(Qt::ISODateWithMs) : QString{};
}

} // namespace machome::upload
