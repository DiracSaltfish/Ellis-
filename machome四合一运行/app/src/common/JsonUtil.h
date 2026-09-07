#pragma once

#include <QJsonObject>
#include <QString>

namespace hub {

QString utcNow();
QString randomId();
QJsonObject redacted(QJsonObject object);
QString compactJson(const QJsonValue &value);
QJsonValue valueAt(const QJsonObject &object, const QString &path);
QString displayValue(const QJsonValue &value, const QString &fallback = QStringLiteral("—"));

} // namespace hub

