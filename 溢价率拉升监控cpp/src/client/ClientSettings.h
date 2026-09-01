#pragma once

#include "client/QmtClient.h"

#include <QList>
#include <QString>
#include <QUrl>

namespace premium {

struct ClientSettings {
    QUrl serverBase{QStringLiteral("ws://192.168.1.113:8421")};
    QList<QmtClient::Profile> profiles;
    bool soundEnabled = true;
    QString alertSoundPreset = QStringLiteral("classic");
    int alertSoundRepeat = 1;
    bool popupEnabled = true;
    int summaryRefreshMs = 100;
};

bool loadClientSettings(const QString &path, ClientSettings *settings, QString *error);
bool saveClientSettings(const QString &path, const ClientSettings &settings, QString *error);

} // namespace premium
