#include "common/JsonUtil.h"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QUuid>

namespace hub {

QString utcNow() {
    return QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
}

QString randomId() {
    return QUuid::createUuid().toString(QUuid::WithoutBraces);
}

namespace {
QJsonValue scrub(const QString &key, const QJsonValue &value) {
    static const QRegularExpression sensitive(
        QStringLiteral("token|secret|password|passwd|cookie|authorization|account|credential"),
        QRegularExpression::CaseInsensitiveOption);
    if (sensitive.match(key).hasMatch()) {
        return value.isNull() || value.isUndefined() ? value : QJsonValue(QStringLiteral("<redacted>"));
    }
    if (value.isObject()) {
        QJsonObject result;
        const auto object = value.toObject();
        for (auto it = object.begin(); it != object.end(); ++it) {
            result.insert(it.key(), scrub(it.key(), it.value()));
        }
        return result;
    }
    if (value.isArray()) {
        QJsonArray result;
        for (const auto &entry : value.toArray()) {
            result.append(scrub({}, entry));
        }
        return result;
    }
    return value;
}
} // namespace

QJsonObject redacted(QJsonObject object) {
    QJsonObject result;
    for (auto it = object.begin(); it != object.end(); ++it) {
        result.insert(it.key(), scrub(it.key(), it.value()));
    }
    return result;
}

QString compactJson(const QJsonValue &value) {
    if (value.isObject()) return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
    if (value.isArray()) return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact));
    if (value.isString()) return value.toString();
    if (value.isBool()) return value.toBool() ? QStringLiteral("true") : QStringLiteral("false");
    if (value.isDouble()) return QString::number(value.toDouble(), 'g', 15);
    if (value.isNull()) return QStringLiteral("null");
    return {};
}

QJsonValue valueAt(const QJsonObject &object, const QString &path) {
    QJsonValue current(object);
    for (const auto &part : path.split(u'.', Qt::SkipEmptyParts)) {
        if (!current.isObject()) return {};
        current = current.toObject().value(part);
    }
    return current;
}

QString displayValue(const QJsonValue &value, const QString &fallback) {
    if (value.isUndefined() || value.isNull()) return fallback;
    const QString result = compactJson(value);
    return result.isEmpty() ? fallback : result;
}

} // namespace hub

