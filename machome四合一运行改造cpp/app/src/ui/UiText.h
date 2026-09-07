#pragma once
#include <QJsonObject>
#include <QString>

namespace hub::ui {
// Display-only translations. Never use these strings as protocol keys or commands.
QString stateText(const QString &raw);
QString fieldText(const QString &raw);
QString actionText(const QString &raw);
QString taskText(const QString &raw);
QString problemText(const QString &raw);
QString localTimeText(const QString &raw);
QString valueText(const QJsonValue &value);
QString serviceTitle(const QString &adapter, const QString &fallback = {});
QString serviceDescription(const QString &adapter);
QString stateStyle(const QJsonObject &payload);
QString styleSheet();
}
