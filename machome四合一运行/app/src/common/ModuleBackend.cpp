#include "common/ModuleBackend.h"

#include "common/JsonUtil.h"

namespace hub {

ModuleBackend::ModuleBackend(ModuleConfig config, QObject *parent)
    : QObject(parent), config_(std::move(config)) {}

QJsonObject ModuleBackend::envelope(const QString &type, const QJsonObject &payload) const {
    const QString protocol = type == QStringLiteral("snapshot")
                                 ? QStringLiteral("module.status.v1")
                             : type == QStringLiteral("event")
                                 ? QStringLiteral("module.event.v1")
                                 : QStringLiteral("module.control.v1");
    return {{QStringLiteral("schema_version"), 1},
            {QStringLiteral("protocol"), protocol},
            {QStringLiteral("type"), type},
            {QStringLiteral("module_id"), config_.id},
            {QStringLiteral("timestamp"), utcNow()},
            {QStringLiteral("payload"), payload}};
}

} // namespace hub
