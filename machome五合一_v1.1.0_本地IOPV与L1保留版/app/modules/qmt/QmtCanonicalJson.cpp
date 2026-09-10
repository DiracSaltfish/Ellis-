#include "modules/qmt/QmtCanonicalJson.h"

#include <QCryptographicHash>
#include <QSet>
#include <QStringList>
#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <limits>
#include <string>

namespace machome::qmt::contract {
namespace {

constexpr double kMaximumExactJsonInteger = 9'007'199'254'740'991.0; // 2^53 - 1

struct RecordSchema {
    QSet<QString> required;
    QSet<QString> strings;
    QSet<QString> integers;
    QSet<QString> floats;
    QSet<QString> booleans;
};

const RecordSchema &ordersSchema() {
    static const RecordSchema schema{
        QSet<QString>{QStringLiteral("code"), QStringLiteral("name"),
                      QStringLiteral("direction"), QStringLiteral("price"),
                      QStringLiteral("qty"), QStringLiteral("traded_qty"),
                      QStringLiteral("traded_price"), QStringLiteral("trade_amount"),
                      QStringLiteral("status"), QStringLiteral("time"),
                      QStringLiteral("order_id"), QStringLiteral("status_code"),
                      QStringLiteral("remark"), QStringLiteral("client_order_id"),
                      QStringLiteral("sort_seq")},
        QSet<QString>{QStringLiteral("code"), QStringLiteral("name"),
                      QStringLiteral("direction"), QStringLiteral("status"),
                      QStringLiteral("time"), QStringLiteral("order_id"),
                      QStringLiteral("remark"), QStringLiteral("client_order_id"),
                      QStringLiteral("split_parent_id"), QStringLiteral("split_group_no"),
                      QStringLiteral("split_display_no"), QStringLiteral("split_child_index"),
                      QStringLiteral("split_child_count"),
                      QStringLiteral("split_child_client_order_id"),
                      QStringLiteral("split_source")},
        QSet<QString>{QStringLiteral("qty"), QStringLiteral("traded_qty"),
                      QStringLiteral("status_code"), QStringLiteral("sort_seq")},
        QSet<QString>{QStringLiteral("price"), QStringLiteral("traded_price"),
                      QStringLiteral("trade_amount")},
        QSet<QString>{QStringLiteral("is_split_child")}};
    return schema;
}

const RecordSchema &positionsSchema() {
    static const RecordSchema schema{
        QSet<QString>{QStringLiteral("code"), QStringLiteral("name"),
                      QStringLiteral("volume"), QStringLiteral("available"),
                      QStringLiteral("cost_price"), QStringLiteral("current_price"),
                      QStringLiteral("profit"), QStringLiteral("profit_rate"),
                      QStringLiteral("market_value")},
        QSet<QString>{QStringLiteral("code"), QStringLiteral("name")},
        QSet<QString>{QStringLiteral("volume"), QStringLiteral("available")},
        QSet<QString>{QStringLiteral("cost_price"), QStringLiteral("current_price"),
                      QStringLiteral("profit"), QStringLiteral("profit_rate"),
                      QStringLiteral("market_value")},
        {}};
    return schema;
}

void setError(QString *error, const QString &message) {
    if (error) *error = message;
}

bool pythonKeyLess(const QString &left, const QString &right) {
    const QList<uint> leftCodePoints = left.toUcs4();
    const QList<uint> rightCodePoints = right.toUcs4();
    return std::lexicographical_compare(leftCodePoints.cbegin(), leftCodePoints.cend(),
                                        rightCodePoints.cbegin(), rightCodePoints.cend());
}

bool appendPythonString(const QString &value, QByteArray *output, QString *error) {
    output->append('"');
    static constexpr char hex[] = "0123456789abcdef";
    for (qsizetype index = 0; index < value.size(); ++index) {
        const ushort unit = value.at(index).unicode();
        switch (unit) {
        case '"': output->append("\\\""); continue;
        case '\\': output->append("\\\\"); continue;
        case '\b': output->append("\\b"); continue;
        case '\f': output->append("\\f"); continue;
        case '\n': output->append("\\n"); continue;
        case '\r': output->append("\\r"); continue;
        case '\t': output->append("\\t"); continue;
        default: break;
        }
        if (unit < 0x20) {
            output->append("\\u00");
            output->append(hex[(unit >> 4) & 0x0f]);
            output->append(hex[unit & 0x0f]);
            continue;
        }
        if (QChar::isHighSurrogate(unit)) {
            if (index + 1 >= value.size()
                || !QChar::isLowSurrogate(value.at(index + 1).unicode())) {
                setError(error, QStringLiteral("字符串含孤立的 UTF-16 高代理项"));
                return false;
            }
            const QString pair(value.mid(index, 2));
            output->append(pair.toUtf8());
            ++index;
            continue;
        }
        if (QChar::isLowSurrogate(unit)) {
            setError(error, QStringLiteral("字符串含孤立的 UTF-16 低代理项"));
            return false;
        }
        output->append(QString(value.at(index)).toUtf8());
    }
    output->append('"');
    return true;
}

bool exactSignedInteger(const QJsonValue &value, qint64 *result) {
    if (!value.isDouble()) return false;
    const double number = value.toDouble();
    if (!std::isfinite(number) || std::trunc(number) != number
        || std::abs(number) > kMaximumExactJsonInteger) {
        return false;
    }
    *result = static_cast<qint64>(number);
    return true;
}

QByteArray pythonFloat(double number, bool *ok) {
    *ok = false;
    if (!std::isfinite(number)) return {};
    if (number == 0.0) {
        *ok = true;
        return std::signbit(number) ? QByteArray("-0.0") : QByteArray("0.0");
    }

    std::array<char, 128> buffer{};
    const auto converted = std::to_chars(buffer.data(), buffer.data() + buffer.size(),
                                         number, std::chars_format::general);
    if (converted.ec != std::errc{}) return {};
    std::string text(buffer.data(), converted.ptr);
    bool negative = false;
    if (!text.empty() && text.front() == '-') {
        negative = true;
        text.erase(text.begin());
    }

    int notationExponent = 0;
    const auto exponentAt = text.find_first_of("eE");
    if (exponentAt != std::string::npos) {
        std::string exponentText = text.substr(exponentAt + 1);
        bool exponentNegative = false;
        if (!exponentText.empty()
            && (exponentText.front() == '+' || exponentText.front() == '-')) {
            exponentNegative = exponentText.front() == '-';
            exponentText.erase(exponentText.begin());
        }
        if (exponentText.empty()) return {};
        int parsed = 0;
        const auto parsedExponent = std::from_chars(exponentText.data(),
                                                    exponentText.data() + exponentText.size(),
                                                    parsed);
        if (parsedExponent.ec != std::errc{}
            || parsedExponent.ptr != exponentText.data() + exponentText.size()) {
            return {};
        }
        notationExponent = exponentNegative ? -parsed : parsed;
        text.resize(exponentAt);
    }

    const auto decimalAt = text.find('.');
    int decimalPosition = decimalAt == std::string::npos
        ? static_cast<int>(text.size()) : static_cast<int>(decimalAt);
    if (decimalAt != std::string::npos) text.erase(decimalAt, 1);
    decimalPosition += notationExponent;

    const auto firstNonZero = text.find_first_not_of('0');
    if (firstNonZero == std::string::npos) return {};
    decimalPosition -= static_cast<int>(firstNonZero);
    text.erase(0, firstNonZero);
    while (text.size() > 1 && text.back() == '0') text.pop_back();

    const int scientificExponent = decimalPosition - 1;
    std::string formatted;
    if (scientificExponent < -4 || scientificExponent >= 16) {
        formatted.push_back(text.front());
        if (text.size() > 1) {
            formatted.push_back('.');
            formatted.append(text.substr(1));
        }
        formatted.push_back('e');
        formatted.push_back(scientificExponent < 0 ? '-' : '+');
        const int absoluteExponent = std::abs(scientificExponent);
        if (absoluteExponent < 10) formatted.push_back('0');
        formatted.append(std::to_string(absoluteExponent));
    } else if (decimalPosition <= 0) {
        formatted = "0.";
        formatted.append(static_cast<std::size_t>(-decimalPosition), '0');
        formatted.append(text);
    } else if (decimalPosition >= static_cast<int>(text.size())) {
        formatted = text;
        formatted.append(static_cast<std::size_t>(decimalPosition - text.size()), '0');
        formatted.append(".0");
    } else {
        formatted = text.substr(0, static_cast<std::size_t>(decimalPosition));
        formatted.push_back('.');
        formatted.append(text.substr(static_cast<std::size_t>(decimalPosition)));
    }

    if (negative) formatted.insert(formatted.begin(), '-');
    *ok = true;
    return QByteArray::fromStdString(formatted);
}

bool appendGenericValue(const QJsonValue &value, QByteArray *output, QString *error) {
    if (value.isNull()) {
        output->append("null");
        return true;
    }
    if (value.isBool()) {
        output->append(value.toBool() ? "true" : "false");
        return true;
    }
    if (value.isString()) return appendPythonString(value.toString(), output, error);
    setError(error, QStringLiteral("未知字段含无法从 QJsonValue 还原 Python 类型的值"));
    return false;
}

bool appendSchemaObject(const QJsonObject &object, const RecordSchema &schema,
                        const QString &where, QByteArray *output, QString *error) {
    for (const QString &required : schema.required) {
        if (!object.contains(required)) {
            setError(error, QStringLiteral("%1 缺少字段 %2").arg(where, required));
            return false;
        }
    }

    QStringList keys = object.keys();
    std::sort(keys.begin(), keys.end(), pythonKeyLess);
    output->append('{');
    bool first = true;
    for (const QString &key : keys) {
        if (!first) output->append(',');
        first = false;
        if (!appendPythonString(key, output, error)) return false;
        output->append(':');
        const QJsonValue value = object.value(key);
        if (schema.strings.contains(key)) {
            if (!value.isString()) {
                setError(error, QStringLiteral("%1.%2 必须是字符串").arg(where, key));
                return false;
            }
            if (!appendPythonString(value.toString(), output, error)) return false;
        } else if (schema.integers.contains(key)) {
            qint64 number = 0;
            if (!exactSignedInteger(value, &number)) {
                setError(error, QStringLiteral("%1.%2 必须是精确整数").arg(where, key));
                return false;
            }
            output->append(QByteArray::number(number));
        } else if (schema.floats.contains(key)) {
            if (!value.isDouble()) {
                setError(error, QStringLiteral("%1.%2 必须是数值").arg(where, key));
                return false;
            }
            bool ok = false;
            const QByteArray formatted = pythonFloat(value.toDouble(), &ok);
            if (!ok) {
                setError(error, QStringLiteral("%1.%2 不是有限 Python float").arg(where, key));
                return false;
            }
            output->append(formatted);
        } else if (schema.booleans.contains(key)) {
            if (!value.isBool()) {
                setError(error, QStringLiteral("%1.%2 必须是布尔值").arg(where, key));
                return false;
            }
            output->append(value.toBool() ? "true" : "false");
        } else if (!appendGenericValue(value, output, error)) {
            return false;
        }
    }
    output->append('}');
    return true;
}

} // namespace

bool strictNonNegativeInteger(const QJsonValue &value, qint64 *result) {
    qint64 number = -1;
    if (!result || !exactSignedInteger(value, &number) || number < 0) return false;
    *result = number;
    return true;
}

QByteArray canonicalSnapshotJson(SnapshotKind kind, const QJsonArray &data,
                                 const QJsonObject &extra, QString *error) {
    if (error) error->clear();
    const RecordSchema &schema = kind == SnapshotKind::Orders
        ? ordersSchema() : positionsSchema();

    if (kind == SnapshotKind::Orders && !extra.isEmpty()) {
        setError(error, QStringLiteral("订单快照 extra 必须为空"));
        return {};
    }
    if (kind == SnapshotKind::Positions) {
        if (extra.size() != 1 || !extra.contains(QStringLiteral("available_cash"))
            || !extra.value(QStringLiteral("available_cash")).isDouble()) {
            setError(error, QStringLiteral("持仓快照 extra 必须只包含数值 available_cash"));
            return {};
        }
    }

    QByteArray output("{\"data\":[");
    for (qsizetype index = 0; index < data.size(); ++index) {
        if (index > 0) output.append(',');
        if (!data.at(index).isObject()) {
            setError(error, QStringLiteral("data[%1] 必须是对象").arg(index));
            return {};
        }
        if (!appendSchemaObject(data.at(index).toObject(), schema,
                                QStringLiteral("data[%1]").arg(index),
                                &output, error)) {
            return {};
        }
    }
    output.append("],\"extra\":");
    if (kind == SnapshotKind::Orders) {
        output.append("{}");
    } else {
        output.append("{\"available_cash\":");
        bool ok = false;
        const QByteArray formatted = pythonFloat(
            extra.value(QStringLiteral("available_cash")).toDouble(), &ok);
        if (!ok) {
            setError(error, QStringLiteral("extra.available_cash 不是有限 Python float"));
            return {};
        }
        output.append(formatted);
        output.append('}');
    }
    output.append('}');
    return output;
}

QByteArray snapshotChecksum(SnapshotKind kind, const QJsonArray &data,
                            const QJsonObject &extra, QString *error) {
    const QByteArray canonical = canonicalSnapshotJson(kind, data, extra, error);
    if (canonical.isEmpty()) return {};
    return QCryptographicHash::hash(canonical, QCryptographicHash::Sha1).toHex();
}

} // namespace machome::qmt::contract
