#include "common/SnapshotParser.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QSet>
#include <cmath>
#include <limits>

namespace premium {
namespace {

// Numeric-key map observed from a live 159866.SZ full snapshot on 2026-08-27.
// The semantic mapping remains conservatively marked unverified until it has
// been confirmed across markets, symbols, reconnects, and full/delta frames.
constexpr auto KMarket = "1";
constexpr auto KCode = "2";
constexpr auto KOrigTime = "4";
constexpr auto KPhase = "5";
constexpr auto KPreClose = "6";
constexpr auto KOpen = "7";
constexpr auto KHigh = "8";
constexpr auto KLow = "9";
constexpr auto KLast = "10";
constexpr auto KClose = "11";
constexpr auto KBidPrice = "12";
constexpr auto KBidVolume = "13";
constexpr auto KAskPrice = "14";
constexpr auto KAskVolume = "15";
constexpr auto KNumTrades = "16";
constexpr auto KTotalVolume = "17";
constexpr auto KTotalAmount = "18";
constexpr auto KIopv = "19";
constexpr auto KHighLimit = "20";
constexpr auto KLowLimit = "21";

// MDHKTSnapshot uses its own wire/tag 16 layout.  It is intentionally kept
// separate from the domestic L1 map: several numeric keys have different
// meanings even though both event kinds use compact numeric JSON keys.
constexpr auto HMarket = "1";
constexpr auto HOrigTime = "3";
constexpr auto HPhase = "4";
constexpr auto HTotalVolume = "5";
constexpr auto HTotalAmount = "6";
constexpr auto HPreClose = "7";
constexpr auto HNominal = "8";
constexpr auto HHigh = "9";
constexpr auto HLow = "10";
constexpr auto HLast = "11";
constexpr auto HBidPrice = "12";
constexpr auto HBidVolume = "13";
constexpr auto HAskPrice = "14";
constexpr auto HAskVolume = "15";
constexpr auto HReference = "16";
constexpr auto HHighLimit = "17";
constexpr auto HLowLimit = "18";

bool isIntegerJson(const QJsonValue &value, qint64 *out)
{
    if (!value.isDouble()) return false;
    // Qt 6's JSON backend preserves parsed 64-bit integers internally even
    // though the public type is still `Double`.  Going through toDouble()
    // first loses the last digits of TGW's 17-digit orig_time.
    constexpr qint64 Invalid = std::numeric_limits<qint64>::min();
    const qint64 number = value.toInteger(Invalid);
    if (number == Invalid) return false;
    *out = number;
    return true;
}

QString identityKey(const QString &session, const QString &symbol, const QString &tag)
{
    return session + u'|' + symbol + u'|' + tag;
}

} // namespace

ParseResult SnapshotParser::consume(const BridgeFrame &frame, bool replay)
{
    ParseResult result;
    if (frame.kind != BridgeFrame::Kind::MarketEvent) {
        result.issues.append(QStringLiteral("not_market_event"));
        return result;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(frame.payloadJson, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        result.issues.append(QStringLiteral("event_json_invalid:%1").arg(parseError.errorString()));
        return result;
    }
    const QJsonObject envelope = document.object();
    const QJsonValue dataValue = envelope.value(QStringLiteral("data"));
    if (!dataValue.isObject()) {
        result.issues.append(QStringLiteral("data_not_object"));
        return result;
    }
    const QJsonObject incoming = dataValue.toObject();
    QString symbol = canonicalSymbol(incoming, frame.tag);
    if (symbol.isEmpty()) {
        const QString headerSymbol = envelope.value(QStringLiteral("symbol")).toString();
        if (frame.tag == QStringLiteral("16")) {
            const QString normalizedHeader = headerSymbol.trimmed().toUpper();
            if (normalizedHeader.size() == 8 && normalizedHeader.endsWith(QStringLiteral(".HK"))) {
                symbol = normalizedHeader;
            }
        } else {
            symbol = normalizeSymbol(headerSymbol);
        }
    }
    result.symbol = symbol;
    if (symbol.isEmpty()) {
        result.issues.append(frame.tag == QStringLiteral("16")
                                 ? QStringLiteral("hkt_code_not_five_digits")
                                 : QStringLiteral("symbol_missing_delta_unroutable"));
        return result;
    }

    if (!activeSession_.isEmpty() && activeSession_ != frame.sessionId) resetSession(frame.sessionId);
    if (activeSession_.isEmpty()) activeSession_ = frame.sessionId;

    const QString key = identityKey(frame.sessionId, symbol, frame.tag);
    RawState &state = states_[key];
    if (!frame.isDelta) {
        state.data = incoming;
        state.sessionId = frame.sessionId;
        state.symbol = symbol;
        state.hasFull = true;
    } else {
        if (!state.hasFull || state.sessionId != frame.sessionId) {
            result.waitingForFull = true;
            result.issues.append(QStringLiteral("delta_before_full"));
            return result;
        }
        state.data = mergedObject(state.data, incoming);
    }
    return mapSnapshot(frame, state.data, replay);
}

void SnapshotParser::resetSession(const QString &sessionId)
{
    states_.clear();
    activeSession_ = sessionId;
}

void SnapshotParser::resetSymbol(const QString &symbol)
{
    const QString normalized = symbol.trimmed().toUpper();
    for (auto it = states_.begin(); it != states_.end();) {
        if (it->symbol == normalized) it = states_.erase(it);
        else ++it;
    }
}

int SnapshotParser::readySymbolCount() const
{
    int result = 0;
    for (const auto &state : states_) if (state.hasFull) ++result;
    return result;
}

QString SnapshotParser::stringValue(const QJsonObject &object, const QString &named, const QString &numeric)
{
    QJsonValue value = object.value(named);
    if (value.isUndefined()) value = object.value(numeric);
    if (value.isString()) {
        QString result = value.toString();
        const qsizetype nul = result.indexOf(QChar::Null);
        if (nul >= 0) result.truncate(nul);
        return result.trimmed();
    }
    if (value.isDouble()) return QString::number(value.toInteger());
    return {};
}

bool SnapshotParser::integerValue(const QJsonObject &object, const QString &named, const QString &numeric, qint64 *out)
{
    QJsonValue value = object.value(named);
    if (value.isUndefined()) value = object.value(numeric);
    return isIntegerJson(value, out);
}

bool SnapshotParser::integerArray(const QJsonObject &object, const QString &named, const QString &numeric,
                                  std::array<qint64, 10> *out, QStringList *issues, int expectedLevels)
{
    out->fill(0);
    QJsonValue value = object.value(named);
    if (value.isUndefined()) value = object.value(numeric);
    if (value.isArray()) {
        const QJsonArray array = value.toArray();
        if (array.size() != expectedLevels) {
            issues->append(named + QStringLiteral("_length_%1").arg(array.size()));
            return false;
        }
        for (int i = 0; i < expectedLevels; ++i) {
            qint64 number = 0;
            if (!isIntegerJson(array.at(i), &number)) {
                issues->append(named + QStringLiteral("_non_integer_%1").arg(i));
                return false;
            }
            (*out)[static_cast<size_t>(i)] = number;
        }
        return true;
    }
    if (value.isString()) {
        const QStringList parts = value.toString().split(u'|', Qt::KeepEmptyParts);
        if (parts.size() != expectedLevels) {
            issues->append(named + QStringLiteral("_length_%1").arg(parts.size()));
            return false;
        }
        for (int i = 0; i < expectedLevels; ++i) {
            bool ok = false;
            const qint64 number = parts.at(i).toLongLong(&ok);
            if (!ok) {
                issues->append(named + QStringLiteral("_non_integer_%1").arg(i));
                return false;
            }
            (*out)[static_cast<size_t>(i)] = number;
        }
        return true;
    }
    issues->append(named + QStringLiteral("_not_array_or_pipe_string"));
    return false;
}

QString SnapshotParser::extractSymbol(const QJsonObject &data)
{
    return stringValue(data, QStringLiteral("security_code"), QString::fromLatin1(KCode));
}

QString SnapshotParser::canonicalSymbol(const QJsonObject &data, const QString &tag)
{
    const QString code = extractSymbol(data).trimmed().toUpper();
    if (tag == QStringLiteral("16")) {
        if (code.size() != 5) return {};
        for (const QChar character : code) if (!character.isDigit()) return {};
        return code + QStringLiteral(".HK");
    }
    return normalizeSymbol(code);
}

QString SnapshotParser::marketFromRaw(const QJsonObject &data, const QString &symbol)
{
    qint64 market = 0;
    if (integerValue(data, QStringLiteral("market_type"), QString::fromLatin1(KMarket), &market)) {
        if (market == 101) return QStringLiteral("SH");
        if (market == 102) return QStringLiteral("SZ");
    }
    const int dot = symbol.indexOf(u'.');
    return dot > 0 ? symbol.mid(dot + 1) : inferMarketSuffix(symbol.left(6));
}

QJsonObject SnapshotParser::mergedObject(QJsonObject base, const QJsonObject &delta)
{
    for (auto it = delta.begin(); it != delta.end(); ++it) base.insert(it.key(), it.value());
    return base;
}

ParseResult SnapshotParser::mapSnapshot(const BridgeFrame &frame, const QJsonObject &data, bool replay) const
{
    if (frame.tag == QStringLiteral("16")) return mapHktSnapshot(frame, data, replay);

    ParseResult result;
    QuoteSnapshot quote;
    quote.code = extractSymbol(data).left(6);
    quote.symbol = normalizeSymbol(extractSymbol(data));
    quote.market = marketFromRaw(data, quote.symbol);
    quote.sourceSession = frame.sessionId;
    quote.receiveWallNs = frame.receiveWallNs;
    quote.replay = replay;
    quote.sourceReady = true;
    quote.numericMappingVerified = data.contains(QStringLiteral("security_code"));
    quote.mappingVersion = quote.numericMappingVerified ? QStringLiteral("named-v1")
                                                        : QStringLiteral("numeric-live-20260827-UNVERIFIED");
    quote.tradingPhase = stringValue(data, QStringLiteral("trading_phase_code"), QString::fromLatin1(KPhase));

    static const QSet<QString> namedKeys{
        QStringLiteral("security_code"), QStringLiteral("market_type"), QStringLiteral("variety_category"),
        QStringLiteral("orig_time"), QStringLiteral("last_price"), QStringLiteral("open_price"),
        QStringLiteral("high_price"), QStringLiteral("low_price"), QStringLiteral("close_price"),
        QStringLiteral("pre_close_price"), QStringLiteral("bid_price"), QStringLiteral("offer_price"),
        QStringLiteral("bid_volume"), QStringLiteral("offer_volume"), QStringLiteral("total_volume_trade"),
        QStringLiteral("total_value_trade"), QStringLiteral("num_trades"), QStringLiteral("trading_phase_code"),
        QStringLiteral("high_limited"), QStringLiteral("low_limited"), QStringLiteral("IOPV")};
    for (auto it = data.begin(); it != data.end(); ++it) {
        bool numeric = false;
        const int number = it.key().toInt(&numeric);
        if ((!numeric && !namedKeys.contains(it.key())) || (numeric && (number < 1 || number > 21))) {
            quote.qualityIssues.append(QStringLiteral("unknown_field:%1").arg(it.key()));
        }
    }

    auto required = [&](const QString &name, const char *numeric, qint64 *target) {
        if (!integerValue(data, name, QString::fromLatin1(numeric), target)) {
            quote.qualityIssues.append(name + QStringLiteral("_not_int64"));
        }
    };
    required(QStringLiteral("orig_time"), KOrigTime, &quote.origTime);
    required(QStringLiteral("last_price"), KLast, &quote.lastPriceE6);
    required(QStringLiteral("open_price"), KOpen, &quote.openPriceE6);
    required(QStringLiteral("high_price"), KHigh, &quote.highPriceE6);
    required(QStringLiteral("low_price"), KLow, &quote.lowPriceE6);
    qint64 ignoredClose = 0;
    required(QStringLiteral("close_price"), KClose, &ignoredClose);
    required(QStringLiteral("pre_close_price"), KPreClose, &quote.preClosePriceE6);
    required(QStringLiteral("total_volume_trade"), KTotalVolume, &quote.totalVolumeE2);
    required(QStringLiteral("total_value_trade"), KTotalAmount, &quote.totalAmountE5);
    required(QStringLiteral("num_trades"), KNumTrades, &quote.numTrades);
    required(QStringLiteral("IOPV"), KIopv, &quote.iopvE6);
    required(QStringLiteral("high_limited"), KHighLimit, &quote.highLimitE6);
    required(QStringLiteral("low_limited"), KLowLimit, &quote.lowLimitE6);
    integerArray(data, QStringLiteral("bid_price"), QString::fromLatin1(KBidPrice), &quote.bidPricesE6, &quote.qualityIssues);
    integerArray(data, QStringLiteral("offer_price"), QString::fromLatin1(KAskPrice), &quote.askPricesE6, &quote.qualityIssues);
    integerArray(data, QStringLiteral("bid_volume"), QString::fromLatin1(KBidVolume), &quote.bidVolumesE2, &quote.qualityIssues);
    integerArray(data, QStringLiteral("offer_volume"), QString::fromLatin1(KAskVolume), &quote.askVolumesE2, &quote.qualityIssues);

    if (quote.symbol.isEmpty()) quote.qualityIssues.append(QStringLiteral("symbol_missing"));
    if (quote.iopvE6 <= 0) quote.qualityIssues.append(QStringLiteral("iopv_non_positive"));
    if (quote.iopvE6 > 0 && quote.iopvE6 < 1'000) quote.qualityIssues.append(QStringLiteral("iopv_scale_suspect"));
    if (quote.lastPriceE6 < 0 || quote.preClosePriceE6 < 0) quote.qualityIssues.append(QStringLiteral("negative_price"));
    auto invalidQuoteTick = [](qint64 priceE6) { return priceE6 > 0 && priceE6 % 1'000 != 0; };
    bool quoteTickInvalid = invalidQuoteTick(quote.lastPriceE6) || invalidQuoteTick(quote.openPriceE6)
                         || invalidQuoteTick(quote.highPriceE6) || invalidQuoteTick(quote.lowPriceE6)
                         || invalidQuoteTick(quote.preClosePriceE6) || invalidQuoteTick(quote.highLimitE6)
                         || invalidQuoteTick(quote.lowLimitE6);
    for (int i = 0; i < 10 && !quoteTickInvalid; ++i) {
        quoteTickInvalid = invalidQuoteTick(quote.bidPricesE6[static_cast<size_t>(i)])
                        || invalidQuoteTick(quote.askPricesE6[static_cast<size_t>(i)]);
    }
    if (quoteTickInvalid) quote.qualityIssues.append(QStringLiteral("price_tick_0_001_invalid"));
    if (quote.iopvE6 > 0 && quote.lastPriceE6 > 0) {
        const long double ratio = static_cast<long double>(quote.lastPriceE6) / quote.iopvE6;
        if (ratio < 0.2L || ratio > 5.0L) quote.qualityIssues.append(QStringLiteral("price_iopv_scale_suspect"));
    }
    if (quote.lastPriceE6 > 0
        && ((quote.highPriceE6 > 0 && quote.lastPriceE6 > quote.highPriceE6)
            || (quote.lowPriceE6 > 0 && quote.lastPriceE6 < quote.lowPriceE6))) {
        quote.qualityIssues.append(QStringLiteral("last_outside_high_low"));
    }
    for (int i = 1; i < 10; ++i) {
        if (quote.bidPricesE6[static_cast<size_t>(i)] > 0
            && quote.bidPricesE6[static_cast<size_t>(i - 1)] > 0
            && quote.bidPricesE6[static_cast<size_t>(i)] > quote.bidPricesE6[static_cast<size_t>(i - 1)]) {
            quote.qualityIssues.append(QStringLiteral("bid_order_invalid"));
            break;
        }
        if (quote.askPricesE6[static_cast<size_t>(i)] > 0
            && quote.askPricesE6[static_cast<size_t>(i - 1)] > 0
            && quote.askPricesE6[static_cast<size_t>(i)] < quote.askPricesE6[static_cast<size_t>(i - 1)]) {
            quote.qualityIssues.append(QStringLiteral("ask_order_invalid"));
            break;
        }
    }
    result.symbol = quote.symbol;
    result.issues = quote.qualityIssues;
    result.snapshot = std::move(quote);
    return result;
}

ParseResult SnapshotParser::mapHktSnapshot(const BridgeFrame &frame, const QJsonObject &data, bool replay) const
{
    ParseResult result;
    QuoteSnapshot quote;
    quote.code = extractSymbol(data);
    quote.symbol = canonicalSymbol(data, frame.tag);
    quote.market = QStringLiteral("HK");
    quote.sourceSession = frame.sessionId;
    quote.receiveWallNs = frame.receiveWallNs;
    quote.replay = replay;
    quote.sourceReady = true;
    quote.numericMappingVerified = true;
    quote.mappingVersion = QStringLiteral("numeric-hkt-deep-connect-live-20260827");
    quote.levelCount = 5;
    quote.tradingPhase = stringValue(data, QStringLiteral("trading_phase_code"), QString::fromLatin1(HPhase));

    bool structureValid = true;
    auto required = [&](const QString &name, const char *numeric, qint64 *target) {
        if (!integerValue(data, name, QString::fromLatin1(numeric), target)) {
            quote.qualityIssues.append(name + QStringLiteral("_not_int64"));
            structureValid = false;
        }
    };

    qint64 routeMarket = 0;
    required(QStringLiteral("market_type"), HMarket, &routeMarket);
    if (routeMarket != 102) {
        quote.qualityIssues.append(QStringLiteral("hkt_route_market_not_szse"));
        structureValid = false;
    }
    if (quote.symbol.isEmpty()) {
        quote.qualityIssues.append(QStringLiteral("hkt_code_not_five_digits"));
        structureValid = false;
    }
    required(QStringLiteral("orig_time"), HOrigTime, &quote.origTime);
    required(QStringLiteral("total_volume_trade"), HTotalVolume, &quote.totalVolumeE2);
    required(QStringLiteral("total_value_trade"), HTotalAmount, &quote.totalAmountE5);
    required(QStringLiteral("pre_close_price"), HPreClose, &quote.preClosePriceE6);
    required(QStringLiteral("nominal_price"), HNominal, &quote.nominalPriceE6);
    required(QStringLiteral("high_price"), HHigh, &quote.highPriceE6);
    required(QStringLiteral("low_price"), HLow, &quote.lowPriceE6);
    required(QStringLiteral("last_price"), HLast, &quote.lastPriceE6);
    required(QStringLiteral("ref_price"), HReference, &quote.referencePriceE6);
    required(QStringLiteral("high_limited"), HHighLimit, &quote.highLimitE6);
    required(QStringLiteral("low_limited"), HLowLimit, &quote.lowLimitE6);
    structureValid = integerArray(data, QStringLiteral("bid_price"), QString::fromLatin1(HBidPrice),
                                  &quote.bidPricesE6, &quote.qualityIssues, 5) && structureValid;
    structureValid = integerArray(data, QStringLiteral("offer_price"), QString::fromLatin1(HAskPrice),
                                  &quote.askPricesE6, &quote.qualityIssues, 5) && structureValid;
    structureValid = integerArray(data, QStringLiteral("bid_volume"), QString::fromLatin1(HBidVolume),
                                  &quote.bidVolumesE2, &quote.qualityIssues, 5) && structureValid;
    structureValid = integerArray(data, QStringLiteral("offer_volume"), QString::fromLatin1(HAskVolume),
                                  &quote.askVolumesE2, &quote.qualityIssues, 5) && structureValid;

    for (auto it = data.begin(); it != data.end(); ++it) {
        bool numeric = false;
        const int field = it.key().toInt(&numeric);
        if (!numeric || field < 1 || field > 23) {
            quote.qualityIssues.append(QStringLiteral("unknown_hkt_field:%1").arg(it.key()));
        }
    }
    if (quote.totalVolumeE2 < 0 || quote.totalAmountE5 < 0) {
        quote.qualityIssues.append(QStringLiteral("negative_turnover"));
        structureValid = false;
    }
    auto invalidPrice = [](qint64 price) { return price < 0 || (price > 0 && price % 1'000 != 0); };
    bool priceInvalid = invalidPrice(quote.lastPriceE6) || invalidPrice(quote.nominalPriceE6)
                     || invalidPrice(quote.preClosePriceE6) || invalidPrice(quote.highPriceE6)
                     || invalidPrice(quote.lowPriceE6);
    for (int i = 0; i < 5; ++i) {
        priceInvalid = priceInvalid || invalidPrice(quote.bidPricesE6[static_cast<size_t>(i)])
                                    || invalidPrice(quote.askPricesE6[static_cast<size_t>(i)]);
        if (quote.bidVolumesE2[static_cast<size_t>(i)] < 0 || quote.askVolumesE2[static_cast<size_t>(i)] < 0) {
            quote.qualityIssues.append(QStringLiteral("negative_book_volume"));
            structureValid = false;
        }
    }
    if (priceInvalid) {
        quote.qualityIssues.append(QStringLiteral("price_tick_0_001_invalid"));
        structureValid = false;
    }
    for (int i = 1; i < 5; ++i) {
        const auto previousBid = quote.bidPricesE6[static_cast<size_t>(i - 1)];
        const auto currentBid = quote.bidPricesE6[static_cast<size_t>(i)];
        const auto previousAsk = quote.askPricesE6[static_cast<size_t>(i - 1)];
        const auto currentAsk = quote.askPricesE6[static_cast<size_t>(i)];
        if (currentBid > 0 && previousBid > 0 && currentBid > previousBid) {
            quote.qualityIssues.append(QStringLiteral("bid_order_invalid"));
            structureValid = false;
        }
        if (currentAsk > 0 && previousAsk > 0 && currentAsk < previousAsk) {
            quote.qualityIssues.append(QStringLiteral("ask_order_invalid"));
            structureValid = false;
        }
    }
    if (quote.bidPricesE6[0] > 0 && quote.askPricesE6[0] > 0
        && quote.bidPricesE6[0] > quote.askPricesE6[0]) {
        quote.qualityIssues.append(QStringLiteral("crossed_book"));
        structureValid = false;
    }
    quote.qualityIssues.append(QStringLiteral("iopv_unavailable_hkt"));

    result.symbol = quote.symbol;
    result.issues = quote.qualityIssues;
    if (structureValid) result.snapshot = std::move(quote);
    return result;
}

} // namespace premium
