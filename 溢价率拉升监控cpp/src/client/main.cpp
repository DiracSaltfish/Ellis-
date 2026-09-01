#include "client/ClientSettings.h"
#include "client/MonitorWindow.h"

#include <QApplication>
#include <QCommandLineParser>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMessageBox>

namespace {

QString programDirectory()
{
    QDir directory(QCoreApplication::applicationDirPath());
    if (directory.dirName() == QStringLiteral("MacOS")
        && directory.cdUp() && directory.dirName() == QStringLiteral("Contents")
        && directory.cdUp() && directory.dirName().endsWith(QStringLiteral(".app"))
        && directory.cdUp()) {
        return directory.absolutePath();
    }
    return QCoreApplication::applicationDirPath();
}

QString defaultClientSettingsPath()
{
    return QDir::cleanPath(QDir(programDirectory()).filePath(QStringLiteral("config/client-settings.json")));
}

QString resolveExplicitPath(const QString &path)
{
    const QFileInfo info(path);
    return info.isAbsolute() ? QDir::cleanPath(path) : info.absoluteFilePath();
}

} // namespace

int main(int argc, char **argv)
{
    QApplication application(argc, argv);
    QCoreApplication::setOrganizationName(QStringLiteral("EllisTools"));
    QCoreApplication::setApplicationName(QStringLiteral("ETF溢价率拉升监控"));
    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addOption({QStringLiteral("server"), QStringLiteral("A WebSocket base URL"), QStringLiteral("url"), QStringLiteral("ws://192.168.1.113:8421")});
    parser.addOption({QStringLiteral("config"), QStringLiteral("Read QMT profiles from app JSON"), QStringLiteral("path")});
    parser.addOption({QStringLiteral("settings"), QStringLiteral("Persistent client settings JSON"), QStringLiteral("path")});
    parser.addOption({QStringLiteral("read-only"), QStringLiteral("Connect to A/QMT for production validation but block every trading/cancel request")});
    parser.process(application);
    QList<premium::QmtClient::Profile> profiles;
    QString configPath = parser.value(QStringLiteral("config"));
    if (configPath.isEmpty()) {
        const QString programConfig = QDir(programDirectory()).filePath(QStringLiteral("config/app.json"));
        const QString programExample = QDir(programDirectory()).filePath(QStringLiteral("config/app.example.json"));
        QString packagedRoot = QDir(QCoreApplication::applicationDirPath())
                                   .absoluteFilePath(QStringLiteral("../../../share/etf-premium-monitor"));
        packagedRoot = QDir::cleanPath(packagedRoot);
        const QString packagedConfig = QDir(packagedRoot).filePath(QStringLiteral("config/app.json"));
        const QString packagedExample = QDir(packagedRoot).filePath(QStringLiteral("config/app.example.json"));
        if (QFileInfo::exists(programConfig)) configPath = programConfig;
        else if (QFileInfo::exists(programExample)) configPath = programExample;
        else if (QFileInfo::exists(packagedConfig)) configPath = packagedConfig;
        else if (QFileInfo::exists(packagedExample)) configPath = packagedExample;
        else configPath = programConfig;
    }
    if (!QFileInfo::exists(configPath)) {
        const QString programExample = QDir(programDirectory()).filePath(QStringLiteral("config/app.example.json"));
        if (QFileInfo::exists(programExample)) configPath = programExample;
    }
    QFile file(configPath);
    if (file.open(QIODevice::ReadOnly)) {
        const QJsonArray values = QJsonDocument::fromJson(file.readAll()).object().value(QStringLiteral("qmt_profiles")).toArray();
        for (const QJsonValue &value : values) {
            const QJsonObject profile = value.toObject();
            profiles.append({profile.value(QStringLiteral("name")).toString(), profile.value(QStringLiteral("host")).toString(),
                             static_cast<quint16>(profile.value(QStringLiteral("port")).toInt(9527))});
        }
    }
    if (profiles.isEmpty()) profiles = {{QStringLiteral("QMT1"), QStringLiteral("192.168.1.112"), 9527},
                                        {QStringLiteral("QMT2"), QStringLiteral("192.168.1.111"), 9527}};
    premium::ClientSettings settings;
    settings.profiles = profiles;
    QString settingsPath = parser.value(QStringLiteral("settings"));
    if (settingsPath.isEmpty()) {
        settingsPath = defaultClientSettingsPath();
    } else {
        settingsPath = resolveExplicitPath(settingsPath);
    }
    QString settingsError;
    if (!premium::loadClientSettings(settingsPath, &settings, &settingsError)) {
        QMessageBox::warning(nullptr, QStringLiteral("客户端设置读取失败"),
                             settingsError + QStringLiteral("\n本次使用 app.json 默认值。"));
        settings = premium::ClientSettings{};
        settings.profiles = profiles;
    }
    if (parser.isSet(QStringLiteral("server"))) settings.serverBase = QUrl(parser.value(QStringLiteral("server")));
    if (!QFileInfo::exists(settingsPath)) {
        settingsError.clear();
        if (!premium::saveClientSettings(settingsPath, settings, &settingsError)) {
            QMessageBox::warning(nullptr, QStringLiteral("客户端设置创建失败"), settingsError);
        }
    }
    premium::MonitorWindow window(settings, QFileInfo(settingsPath).absoluteFilePath(),
                                  !parser.isSet(QStringLiteral("read-only")));
    window.show();
    return application.exec();
}
