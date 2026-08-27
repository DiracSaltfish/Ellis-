#include "client/ClientSettings.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>

namespace premium {
namespace {

bool validPort(int value) { return value >= 1 && value <= 65'535; }

} // namespace

bool loadClientSettings(const QString &path, ClientSettings *settings, QString *error)
{
    if (!settings) return false;
    if (!QFileInfo::exists(path)) return true;
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("无法读取 %1: %2").arg(path, file.errorString());
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error) *error = QStringLiteral("客户端设置 JSON 无效: %1").arg(parseError.errorString());
        return false;
    }
    const QJsonObject root = document.object();
    const QJsonObject a = root.value(QStringLiteral("a")).toObject();
    if (!a.isEmpty()) {
        const QString host = a.value(QStringLiteral("host")).toString().trimmed();
        const int port = a.value(QStringLiteral("port")).toInt();
        if (host.isEmpty() || !validPort(port)) {
            if (error) *error = QStringLiteral("A 地址或端口无效");
            return false;
        }
        settings->serverBase = QUrl(QStringLiteral("ws://%1:%2").arg(host).arg(port));
    }
    const QJsonArray profiles = root.value(QStringLiteral("qmt_profiles")).toArray();
    if (!profiles.isEmpty()) {
        QList<QmtClient::Profile> loaded;
        for (const QJsonValue &value : profiles) {
            const QJsonObject object = value.toObject();
            const QString name = object.value(QStringLiteral("name")).toString().trimmed();
            const QString host = object.value(QStringLiteral("host")).toString().trimmed();
            const int port = object.value(QStringLiteral("port")).toInt();
            if (name.isEmpty() || host.isEmpty() || !validPort(port)) {
                if (error) *error = QStringLiteral("QMT 配置无效");
                return false;
            }
            loaded.append({name, host, static_cast<quint16>(port)});
        }
        if (loaded.size() != 2) {
            if (error) *error = QStringLiteral("客户端设置必须包含 QMT1/QMT2 两个配置");
            return false;
        }
        settings->profiles = loaded;
    }
    if (root.contains(QStringLiteral("sound_enabled")))
        settings->soundEnabled = root.value(QStringLiteral("sound_enabled")).toBool(true);
    if (root.contains(QStringLiteral("sound_preset"))) {
        const QString preset = root.value(QStringLiteral("sound_preset")).toString().trimmed();
        if (preset == QStringLiteral("classic") || preset == QStringLiteral("double")
            || preset == QStringLiteral("rising")) {
            settings->alertSoundPreset = preset;
        }
    }
    settings->alertSoundRepeat = qBound(1, root.value(QStringLiteral("sound_repeat")).toInt(settings->alertSoundRepeat), 3);
    if (root.contains(QStringLiteral("popup_enabled")))
        settings->popupEnabled = root.value(QStringLiteral("popup_enabled")).toBool(true);
    settings->summaryRefreshMs = qBound(50, root.value(QStringLiteral("summary_refresh_ms")).toInt(settings->summaryRefreshMs), 3'000);
    return true;
}

bool saveClientSettings(const QString &path, const ClientSettings &settings, QString *error)
{
    const QFileInfo info(path);
    if (!QDir().mkpath(info.absolutePath())) {
        if (error) *error = QStringLiteral("无法创建设置目录 %1").arg(info.absolutePath());
        return false;
    }
    QJsonArray profiles;
    for (const QmtClient::Profile &profile : settings.profiles) {
        profiles.append(QJsonObject{{QStringLiteral("name"), profile.name},
                                    {QStringLiteral("host"), profile.host},
                                    {QStringLiteral("port"), profile.port}});
    }
    QJsonObject a{{QStringLiteral("host"), settings.serverBase.host()},
                  {QStringLiteral("port"), settings.serverBase.port(8421)}};
    const QJsonObject root{{QStringLiteral("version"), 1},
                           {QStringLiteral("a"), a},
                           {QStringLiteral("qmt_profiles"), profiles},
                           {QStringLiteral("sound_enabled"), settings.soundEnabled},
                           {QStringLiteral("sound_preset"), settings.alertSoundPreset},
                           {QStringLiteral("sound_repeat"), settings.alertSoundRepeat},
                           {QStringLiteral("popup_enabled"), settings.popupEnabled},
                           {QStringLiteral("summary_refresh_ms"), settings.summaryRefreshMs}};
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)
        || file.write(QJsonDocument(root).toJson(QJsonDocument::Indented)) < 0
        || !file.commit()) {
        if (error) *error = QStringLiteral("无法原子保存 %1: %2").arg(path, file.errorString());
        return false;
    }
    return true;
}

} // namespace premium
