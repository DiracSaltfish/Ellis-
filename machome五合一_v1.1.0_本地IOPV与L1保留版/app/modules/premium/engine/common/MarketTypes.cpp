#include "modules/premium/engine/common/MarketTypes.h"

#include <QJsonArray>
#include <cmath>
#include <limits>

namespace machome::premium::engine {
namespace {

QJsonArray arrayToJson(const std::array<qint64, 10> &values, qint64 scale, int levels)
{
    QJsonArray result;
    for (int i = 0; i < levels; ++i) {
        if (scale == PriceScale) result.append(scaledPrice(values[static_cast<size_t>(i)]));
        else result.append(static_cast<qint64>(std::llround(scaledVolume(values[static_cast<size_t>(i)]))));
    }
    return result;
}

QJsonArray rawArray(const std::array<qint64, 10> &values)
{
    QJsonArray result;
    for (const auto value : values) result.append(value);
    return result;
}

} // namespace

QJsonObject withLocalValuation(QJsonObject object, const QJsonObject &valuation,
                               const QString &basis, const QDateTime &now)
{
    object.insert("schema_version", "local-iopv-client.v1");
    const auto type = object.value("type").toString();
    if (type == "local_iopv_signal") {
        object.insert("type", "signal");
        return object;
    }
    if (type != "summary" && type != "detail") return object;
    object.insert("valuation", valuation);
    object.insert("valuation_source", "local_pcf");
    object.insert("valuation_basis", basis);
    object.insert("exchange_iopv_e6", object.value("iopv_e6"));
    const auto at = QDateTime::fromString(valuation.value("calculated_at").toString(), Qt::ISODateWithMs);
    const bool fresh = at.isValid() && at.msecsTo(now) >= 0 && at.msecsTo(now) <= 12000
        && valuation.value("trade_date").toString() == now.date().toString(Qt::ISODate);
    const bool knownBasis = basis == "midpoint" || basis == "settlement_buy" || basis == "settlement_sell";
    const double nav = valuation.value(basis + "_iopv").toDouble();
    const qint64 scaled = fresh && knownBasis && std::isfinite(nav) && nav > 0 && nav <= 1000000
        ? std::llround(nav * PriceScale) : 0;
    object.insert("iopv_e6", scaled);
    object.insert("iopv_static", false);
    object.insert("sell_premium_ppm", scaled ? calculateRatioPpm(object.value("bid1_price_e6").toInteger(), scaled) : 0);
    object.insert("display_premium_ppm", scaled ? calculateRatioPpm(object.value("last_price_e6").toInteger(), scaled) : 0);
    if (!scaled) {
        auto quality = object.value("quality").toArray();
        quality.append("LOCAL_IOPV_UNAVAILABLE");
        object.insert("quality", quality);
    }
    return object;
}

QJsonObject QuoteSnapshot::toSummaryJson() const
{
    return {
        {QStringLiteral("s"), symbol},
        {QStringLiteral("name"), name},
        {QStringLiteral("market"), market},
        {QStringLiteral("source_session"), sourceSession},
        {QStringLiteral("orig_time"), origTime},
        {QStringLiteral("receive_wall_ns"), receiveWallNs},
        {QStringLiteral("publish_wall_ns"), publishWallNs},
        {QStringLiteral("last_price_e6"), lastPriceE6},
        {QStringLiteral("bid1_price_e6"), bidPricesE6[0]},
        {QStringLiteral("iopv_e6"), iopvE6},
        {QStringLiteral("change_ppm"), changePpm},
        {QStringLiteral("bid_rise_150s_ppm"), bidRise150sPpm},
        {QStringLiteral("bid_rise_300s_ppm"), bidRise300sPpm},
        {QStringLiteral("bid_rise_30s_ppm"), bidRise30sPpm},
        {QStringLiteral("bid_rise_60s_ppm"), bidRise60sPpm},
        {QStringLiteral("bid_rise_90s_ppm"), bidRise90sPpm},
        {QStringLiteral("momentum_3m_ppm"), momentum3mPpm},
        {QStringLiteral("momentum_5m_ppm"), momentum5mPpm},
        {QStringLiteral("adaptive_3m_ppm"), adaptive3mPpm},
        {QStringLiteral("adaptive_5m_ppm"), adaptive5mPpm},
        {QStringLiteral("minute_range_ppm"), minuteRangePpm},
        {QStringLiteral("minute_range_base_ppm"), minuteRangeBasePpm},
        {QStringLiteral("sell_premium_ppm"), sellPremiumPpm},
        {QStringLiteral("display_premium_ppm"), displayPremiumPpm},
        {QStringLiteral("iopv_static"), iopvStatic},
        {QStringLiteral("source_ready"), sourceReady},
        {QStringLiteral("replay"), replay},
        {QStringLiteral("mapping_verified"), numericMappingVerified},
        {QStringLiteral("mapping_version"), mappingVersion},
        {QStringLiteral("quality"), QJsonArray::fromStringList(qualityIssues)},
    };
}

QJsonObject QuoteSnapshot::toDetailJson() const
{
    QJsonObject object = toSummaryJson();
    object.insert(QStringLiteral("pre_close_price_e6"), preClosePriceE6);
    object.insert(QStringLiteral("nominal_price_e6"), nominalPriceE6);
    object.insert(QStringLiteral("reference_price_e6"), referencePriceE6);
    object.insert(QStringLiteral("open_price_e6"), openPriceE6);
    object.insert(QStringLiteral("high_price_e6"), highPriceE6);
    object.insert(QStringLiteral("low_price_e6"), lowPriceE6);
    object.insert(QStringLiteral("bid_prices_e6"), rawArray(bidPricesE6));
    object.insert(QStringLiteral("ask_prices_e6"), rawArray(askPricesE6));
    object.insert(QStringLiteral("bid_volumes_e2"), rawArray(bidVolumesE2));
    object.insert(QStringLiteral("ask_volumes_e2"), rawArray(askVolumesE2));
    object.insert(QStringLiteral("total_volume_e2"), totalVolumeE2);
    object.insert(QStringLiteral("total_amount_e5"), totalAmountE5);
    object.insert(QStringLiteral("num_trades"), numTrades);
    object.insert(QStringLiteral("level_count"), levelCount);
    object.insert(QStringLiteral("high_limit_price_e6"), highLimitE6);
    object.insert(QStringLiteral("low_limit_price_e6"), lowLimitE6);
    object.insert(QStringLiteral("trading_phase"), tradingPhase);
    return object;
}

QJsonObject QuoteSnapshot::toLegacyBookJson() const
{
    return {
        {QStringLiteral("s"), symbol},
        {QStringLiteral("qt"), exchangeTimeToEpochMs(origTime)},
        {QStringLiteral("rt"), receiveWallNs / 1'000'000},
        {QStringLiteral("lp"), scaledPrice(lastPriceE6)},
        {QStringLiteral("o"), scaledPrice(openPriceE6)},
        {QStringLiteral("h"), scaledPrice(highPriceE6)},
        {QStringLiteral("l"), scaledPrice(lowPriceE6)},
        {QStringLiteral("pc"), scaledPrice(preClosePriceE6)},
        {QStringLiteral("vol"), static_cast<qint64>(std::llround(scaledVolume(totalVolumeE2)))},
        {QStringLiteral("amt"), static_cast<double>(totalAmountE5) / AmountScale},
        {QStringLiteral("st"), 0},
        {QStringLiteral("ap"), arrayToJson(askPricesE6, PriceScale, 5)},
        {QStringLiteral("av"), arrayToJson(askVolumesE2, VolumeScale, 5)},
        {QStringLiteral("bp"), arrayToJson(bidPricesE6, PriceScale, 5)},
        {QStringLiteral("bv"), arrayToJson(bidVolumesE2, VolumeScale, 5)},
    };
}

QJsonObject SignalEvent::toJson(bool backfill) const
{
    return {
        {QStringLiteral("type"), QStringLiteral("signal")},
        {QStringLiteral("signal_seq"), static_cast<qint64>(sequence)},
        {QStringLiteral("symbol"), symbol},
        {QStringLiteral("occurred_at"), occurredAt.toString(Qt::ISODateWithMs)},
        {QStringLiteral("premium_ppm"), premiumPpm},
        {QStringLiteral("rise_30s_ppm"), rise30sPpm},
        {QStringLiteral("rise_300s_ppm"), rise300sPpm},
        {QStringLiteral("bid_rise_150s_ppm"), bidRise150sPpm},
        {QStringLiteral("bid_rise_300s_ppm"), bidRise300sPpm},
        {QStringLiteral("bid_rise_30s_ppm"), bidRise30sPpm},
        {QStringLiteral("bid_rise_60s_ppm"), bidRise60sPpm},
        {QStringLiteral("bid_rise_90s_ppm"), bidRise90sPpm},
        {QStringLiteral("momentum_3m_ppm"), momentum3mPpm},
        {QStringLiteral("momentum_5m_ppm"), momentum5mPpm},
        {QStringLiteral("adaptive_3m_ppm"), adaptive3mPpm},
        {QStringLiteral("adaptive_5m_ppm"), adaptive5mPpm},
        {QStringLiteral("minute_range_ppm"), minuteRangePpm},
        {QStringLiteral("minute_range_base_ppm"), minuteRangeBasePpm},
        {QStringLiteral("model"), model},
        {QStringLiteral("reason"), reason},
        {QStringLiteral("repeat"), repeat},
        {QStringLiteral("replay"), replay},
        {QStringLiteral("backfill"), backfill},
    };
}

QString normalizeSymbol(QString value)
{
    value = value.trimmed().toUpper();
    if (value.size() == 6) {
        const QString suffix = inferMarketSuffix(value);
        if (!suffix.isEmpty()) value += QStringLiteral(".") + suffix;
    }
    return value;
}

QString inferMarketSuffix(const QString &code)
{
    if (code.size() != 6) return {};
    const QChar first = code.at(0);
    if (first == u'0' || first == u'1' || first == u'2' || first == u'3') return QStringLiteral("SZ");
    if (first == u'5' || first == u'6' || first == u'9') return QStringLiteral("SH");
    return {};
}

double scaledPrice(qint64 valueE6)
{
    return static_cast<double>(valueE6) / PriceScale;
}

double scaledVolume(qint64 valueE2)
{
    return static_cast<double>(valueE2) / VolumeScale;
}

double ppmToPercent(qint64 ppm)
{
    return static_cast<double>(ppm) / 10'000.0;
}

qint64 calculateRatioPpm(qint64 numeratorE6, qint64 denominatorE6)
{
    if (denominatorE6 <= 0) return 0;
    const long double ratio = (static_cast<long double>(numeratorE6) - denominatorE6)
                            * RatioPpmScale / denominatorE6;
    if (ratio > static_cast<long double>(std::numeric_limits<qint64>::max())
        || ratio < static_cast<long double>(std::numeric_limits<qint64>::min())) return 0;
    return static_cast<qint64>(std::llround(ratio));
}

qint64 exchangeTimeToEpochMs(qint64 rawOrigTime)
{
    if (rawOrigTime >= 1'000'000'000'000LL && rawOrigTime < 10'000'000'000'000LL) return rawOrigTime;
    const QString text = QString::number(rawOrigTime);
    if (text.size() != 17) return 0;
    const QDateTime value = QDateTime::fromString(text, QStringLiteral("yyyyMMddHHmmsszzz"));
    return value.isValid() ? value.toMSecsSinceEpoch() : 0;
}

} // namespace machome::premium::engine
