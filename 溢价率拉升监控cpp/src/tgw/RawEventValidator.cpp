#include "tgw/RawEventValidator.h"

#include <QRegularExpression>
#include <simdjson.h>

#include <cstdint>
#include <initializer_list>
#include <string>
#include <string_view>

namespace premium::native_tgw {
namespace {

using simdjson::dom::array;
using simdjson::dom::element;
using simdjson::dom::object;

QString jsonError(simdjson::error_code code)
{
    return QString::fromLatin1(simdjson::error_message(code));
}

bool optional(object source, std::string_view key, element *value, QString *error)
{
    const simdjson::error_code code = source.at_key(key).get(*value);
    if (code == simdjson::NO_SUCH_FIELD) return false;
    if (code) {
        if (error) *error = QStringLiteral("cannot inspect JSON field %1: %2")
                                .arg(QString::fromUtf8(key.data(), static_cast<qsizetype>(key.size())),
                                     jsonError(code));
        return false;
    }
    return true;
}

bool required(object source, std::string_view key, element *value, QString *error)
{
    const simdjson::error_code code = source.at_key(key).get(*value);
    if (code) {
        if (error) *error = QStringLiteral("required JSON field %1 is unavailable: %2")
                                .arg(QString::fromUtf8(key.data(), static_cast<qsizetype>(key.size())),
                                     jsonError(code));
        return false;
    }
    return true;
}

bool asObject(element value, object *result, const QString &context, QString *error)
{
    const simdjson::error_code code = value.get_object().get(*result);
    if (!code) return true;
    if (error) *error = context + QStringLiteral(" must be a JSON object");
    return false;
}

bool exactInt64(element value, qint64 *result = nullptr)
{
    std::int64_t number = 0;
    if (value.get_int64().get(number)) return false;
    if (result) *result = static_cast<qint64>(number);
    return true;
}

bool exactString(element value, QString *result = nullptr)
{
    std::string_view text;
    if (value.get_string().get(text)) return false;
    if (result) *result = QString::fromUtf8(text.data(), static_cast<qsizetype>(text.size()));
    return true;
}

bool requireIntegerFields(object data, const std::initializer_list<std::string_view> &keys,
                          QString *error)
{
    for (const std::string_view key : keys) {
        element value;
        QString fieldError;
        if (!optional(data, key, &value, &fieldError)) {
            if (!fieldError.isEmpty()) {
                if (error) *error = fieldError;
                return false;
            }
            continue;
        }
        if (!exactInt64(value)) {
            if (error) *error = QStringLiteral("data.%1 must be an exact int64 JSON integer token")
                                    .arg(QString::fromUtf8(key.data(), static_cast<qsizetype>(key.size())));
            return false;
        }
    }
    return true;
}

bool requireStringFields(object data, const std::initializer_list<std::string_view> &keys,
                         QString *error)
{
    for (const std::string_view key : keys) {
        element value;
        QString fieldError;
        if (!optional(data, key, &value, &fieldError)) {
            if (!fieldError.isEmpty()) {
                if (error) *error = fieldError;
                return false;
            }
            continue;
        }
        if (!exactString(value)) {
            if (error) *error = QStringLiteral("data.%1 must be a JSON string")
                                    .arg(QString::fromUtf8(key.data(), static_cast<qsizetype>(key.size())));
            return false;
        }
    }
    return true;
}

bool requireIntegerArrays(object data, const std::initializer_list<std::string_view> &keys,
                          QString *error)
{
    for (const std::string_view key : keys) {
        element value;
        QString fieldError;
        if (!optional(data, key, &value, &fieldError)) {
            if (!fieldError.isEmpty()) {
                if (error) *error = fieldError;
                return false;
            }
            continue;
        }
        array values;
        if (value.get_array().get(values)) {
            if (error) *error = QStringLiteral("data.%1 must be an integer array")
                                    .arg(QString::fromUtf8(key.data(), static_cast<qsizetype>(key.size())));
            return false;
        }
        for (element entry : values) {
            if (!exactInt64(entry)) {
                if (error) *error = QStringLiteral("data.%1 contains a non-int64 token")
                                        .arg(QString::fromUtf8(key.data(), static_cast<qsizetype>(key.size())));
                return false;
            }
        }
    }
    return true;
}

bool requireFields(object data, const std::initializer_list<std::string_view> &keys,
                   QString *error)
{
    for (const std::string_view key : keys) {
        element unused;
        if (!required(data, key, &unused, error)) return false;
    }
    return true;
}

bool allowOnlyFields(object data, const std::initializer_list<std::string_view> &keys,
                     QString *error)
{
    for (auto entry : data) {
        bool allowed = false;
        for (const std::string_view key : keys) {
            if (entry.key == key) {
                allowed = true;
                break;
            }
        }
        if (!allowed) {
            if (error) *error = QStringLiteral("unsupported data field: %1")
                                    .arg(QString::fromUtf8(entry.key.data(),
                                                          static_cast<qsizetype>(entry.key.size())));
            return false;
        }
    }
    return true;
}

} // namespace

bool inspectRawEvent(QByteArrayView payload, RawEventMetadata *metadata, QString *error)
{
    if (error) error->clear();
    if (!metadata) {
        if (error) *error = QStringLiteral("metadata output is null");
        return false;
    }
    simdjson::padded_string input(std::string(payload.data(),
                                              static_cast<std::size_t>(payload.size())));
    simdjson::dom::parser parser;
    element rootElement;
    simdjson::error_code code = parser.parse(input).get(rootElement);
    if (code) {
        if (error) *error = QStringLiteral("raw event is invalid JSON: %1").arg(jsonError(code));
        return false;
    }
    object root;
    if (!asObject(rootElement, &root, QStringLiteral("raw event root"), error)) return false;

    element headersElement;
    object headers;
    if (!required(root, "headers", &headersElement, error)
        || !asObject(headersElement, &headers, QStringLiteral("raw event headers"), error)) return false;
    element tagElement;
    if (!required(headers, "tag", &tagElement, error)) return false;
    QString tag;
    if (!exactString(tagElement, &tag)) {
        if (error) *error = QStringLiteral("headers.tag must be a JSON string");
        return false;
    }
    if (tag != QStringLiteral("14") && tag != QStringLiteral("16")) {
        if (error) *error = QStringLiteral("unsupported push tag: %1").arg(tag);
        return false;
    }

    element statusElement;
    qint64 status = 0;
    if (!required(root, "status", &statusElement, error)
        || !exactInt64(statusElement, &status) || status != 0) {
        if (error && error->isEmpty())
            *error = QStringLiteral("raw event status must be the exact integer token 0");
        return false;
    }

    element deltaElement;
    if (!required(root, "is_delta", &deltaElement, error)) return false;
    qint64 delta = -1;
    if (!exactInt64(deltaElement, &delta) || (delta != 0 && delta != 1)) {
        if (error) *error = QStringLiteral("raw event is_delta must be the exact integer token 0 or 1");
        return false;
    }
    const bool isDelta = delta == 1;

    element dataElement;
    object data;
    if (!required(root, "data", &dataElement, error)
        || !asObject(dataElement, &data, QStringLiteral("raw event data"), error)) return false;

    element codeElement;
    QString fieldError;
    const bool numericSchema = optional(data, "2", &codeElement, &fieldError);
    if (!fieldError.isEmpty()) {
        if (error) *error = fieldError;
        return false;
    }
    QString securityCode;
    if (numericSchema) {
        if (!exactString(codeElement, &securityCode)) {
            if (error) *error = QStringLiteral("numeric data.2 security code must be a string");
            return false;
        }
        static constexpr std::initializer_list<std::string_view> DomesticKeys{
            "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
            "13", "14", "15", "16", "17", "18", "19", "20", "21"};
        static constexpr std::initializer_list<std::string_view> HktKeys{
            "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
            "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23"};
        const bool hkt = tag == QStringLiteral("16");
        const bool typesValid = hkt
            ? requireIntegerFields(data,
                {"1", "3", "5", "6", "7", "8", "9", "10", "11", "16", "17", "18", "19", "20", "21", "22", "23"}, error)
              && requireStringFields(data, {"2", "4", "12", "13", "14", "15"}, error)
            : requireIntegerFields(data,
                {"1", "3", "4", "6", "7", "8", "9", "10", "11", "16", "17", "18", "19", "20", "21"}, error)
              && requireStringFields(data, {"2", "5", "12", "13", "14", "15"}, error);
        if (!typesValid) return false;
        if (!(hkt ? allowOnlyFields(data, HktKeys, error)
                  : allowOnlyFields(data, DomesticKeys, error))) return false;
        if (!(hkt ? requireFields(data, {"1", "2", "3"}, error)
                  : requireFields(data, {"1", "2", "4"}, error))) return false;
        if (!isDelta && !(hkt ? requireFields(data, HktKeys, error)
                              : requireFields(data, DomesticKeys, error))) return false;
    } else {
        static constexpr std::initializer_list<std::string_view> NamedKeys{
            "security_code", "market_type", "variety_category", "orig_time", "last_price",
            "open_price", "high_price", "low_price", "close_price", "pre_close_price",
            "bid_price", "offer_price", "bid_volume", "offer_volume", "total_volume_trade",
            "total_value_trade", "num_trades", "trading_phase_code", "IOPV", "high_limited",
            "low_limited"};
        element namedCode;
        if (!required(data, "security_code", &namedCode, error)
            || !exactString(namedCode, &securityCode)) {
            if (error && error->isEmpty())
                *error = QStringLiteral("named data.security_code must be a string");
            return false;
        }
        if (!requireIntegerFields(data,
                {"market_type", "variety_category", "orig_time", "last_price", "open_price",
                 "high_price", "low_price", "close_price", "pre_close_price", "total_volume_trade",
                 "total_value_trade", "num_trades", "IOPV", "high_limited", "low_limited"}, error)
            || !requireIntegerArrays(data,
                {"bid_price", "offer_price", "bid_volume", "offer_volume"}, error)
            || !requireStringFields(data, {"trading_phase_code"}, error)) return false;
        if (!allowOnlyFields(data, NamedKeys, error)
            || !requireFields(data, {"security_code", "orig_time"}, error)
            || (!isDelta && !requireFields(data, NamedKeys, error))) return false;
    }

    securityCode = securityCode.trimmed().toUpper();
    static const QRegularExpression Domestic(QStringLiteral("^[0-9]{6}(\\.(SH|SZ))?$"));
    static const QRegularExpression Hkt(QStringLiteral("^[0-9]{5}(\\.HK)?$"));
    if (!(tag == QStringLiteral("16") ? Hkt.match(securityCode).hasMatch()
                                       : Domestic.match(securityCode).hasMatch())) {
        if (error) *error = QStringLiteral("security code does not match tag %1: %2")
                                .arg(tag, securityCode);
        return false;
    }

    if (tag == QStringLiteral("16")) {
        element marketElement;
        QString marketError;
        const bool hasMarket = optional(data, "1", &marketElement, &marketError);
        qint64 market = 0;
        if (!marketError.isEmpty()
            || (hasMarket && (!exactInt64(marketElement, &market) || market != 102))
            || (!hasMarket && numericSchema)) {
            if (error) *error = QStringLiteral("numeric HKT market must be integer 102");
            return false;
        }
        securityCode = securityCode.left(5) + QStringLiteral(".HK");
    } else if (!securityCode.contains(u'.')) {
        element marketElement;
        qint64 market = 0;
        if (!required(data, "1", &marketElement, error)
            || !exactInt64(marketElement, &market) || (market != 101 && market != 102)) {
            if (error && error->isEmpty())
                *error = QStringLiteral("numeric domestic market must be exact integer 101 or 102");
            return false;
        }
        securityCode += market == 101 ? QStringLiteral(".SH") : QStringLiteral(".SZ");
    }

    metadata->tag = tag;
    metadata->symbol = securityCode;
    metadata->isDelta = isDelta;
    metadata->numericSchema = numericSchema;
    return true;
}

} // namespace premium::native_tgw
