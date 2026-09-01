#include "tgw/NativeTgwAdapter.h"

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSettings>
#include <QTimer>
#include <tgw/session.hpp>

#include <atomic>
#include <csignal>
#include <exception>
#include <optional>

namespace {
std::atomic<bool> StopRequested{false};

void requestStop(int)
{
    StopRequested.store(true);
}

std::optional<bool> iniBoolean(const QVariant &value)
{
    if (!value.isValid()) return false;
    const QString text = value.toString().trimmed().toLower();
    if (text == QStringLiteral("true") || text == QStringLiteral("yes")
        || text == QStringLiteral("on") || text == QStringLiteral("1")) return true;
    if (text == QStringLiteral("false") || text == QStringLiteral("no")
        || text == QStringLiteral("off") || text == QStringLiteral("0")) return false;
    return std::nullopt;
}
} // namespace

int main(int argc, char **argv)
{
    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("etf-premium-tgw"));
    QCoreApplication::setApplicationVersion(QStringLiteral("0.4.0"));

    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addVersionOption();
    parser.addOption({QStringLiteral("socket"), QStringLiteral("A-core local socket path"),
                      QStringLiteral("path"), QStringLiteral("runtime/tgw.sock")});
    parser.addOption({QStringLiteral("watchlist"), QStringLiteral("Default symbol list"),
                      QStringLiteral("path"), QStringLiteral("config/watchlist.json")});
    parser.addOption({QStringLiteral("account"), QStringLiteral("TGW account INI for live mode"),
                      QStringLiteral("path")});
    parser.addOption({QStringLiteral("username-file"), QStringLiteral("Optional username override file"),
                      QStringLiteral("path")});
    parser.addOption({QStringLiteral("ca-file"), QStringLiteral("TGW TLS CA certificate"),
                      QStringLiteral("path")});
    parser.addOption({QStringLiteral("log"), QStringLiteral("Credential-safe adapter JSONL log"),
                      QStringLiteral("path"), QStringLiteral("logs/tgw-native.log")});
    parser.addOption({QStringLiteral("simulate"), QStringLiteral("Use deterministic native simulation; never connect TGW")});
    parser.process(application);

    premium::native_tgw::AdapterOptions options;
    options.socketPath = QFileInfo(parser.value(QStringLiteral("socket"))).absoluteFilePath();
    options.watchlistPath = QFileInfo(parser.value(QStringLiteral("watchlist"))).absoluteFilePath();
    options.logPath = QFileInfo(parser.value(QStringLiteral("log"))).absoluteFilePath();
    options.simulation = parser.isSet(QStringLiteral("simulate"));

    if (!options.simulation) {
        const QString accountPath = parser.value(QStringLiteral("account"));
        if (accountPath.isEmpty() || !QFileInfo::exists(accountPath)) {
            qCritical().noquote() << QStringLiteral("live mode requires an existing --account INI");
            return 2;
        }
        try {
            const QString absoluteAccountPath = QFileInfo(accountPath).absoluteFilePath();
            tgw::Config config = tgw::load_ini_config(absoluteAccountPath.toStdString());
            const QSettings accountSettings(absoluteAccountPath, QSettings::IniFormat);
            const auto forceLogout = iniBoolean(accountSettings.value(QStringLiteral("galaxy/force_logout")));
            if (!forceLogout.has_value()) {
                qCritical().noquote() << QStringLiteral("[galaxy].force_logout must be a boolean");
                return 2;
            }
            config.force_logout = *forceLogout;
            const QString usernamePath = parser.value(QStringLiteral("username-file"));
            if (!usernamePath.isEmpty()) {
                QFile usernameFile(usernamePath);
                if (!usernameFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
                    qCritical().noquote() << QStringLiteral("cannot open --username-file");
                    return 2;
                }
                const QString username = QString::fromUtf8(usernameFile.readAll()).trimmed();
                if (username.isEmpty()) {
                    qCritical().noquote() << QStringLiteral("--username-file is empty");
                    return 2;
                }
                config.username = username.toStdString();
            }
            QString caPath = parser.value(QStringLiteral("ca-file"));
            if (caPath.isEmpty() && !config.ca_file.empty()
                && QFileInfo::exists(QString::fromStdString(config.ca_file))) {
                caPath = QString::fromStdString(config.ca_file);
            }
            if (caPath.isEmpty()) {
                const QDir root = QFileInfo(accountPath).absoluteDir().absoluteFilePath(QStringLiteral(".."));
                const QString bundled = QDir(root.absolutePath()).filePath(QStringLiteral("certs/vendor-dgw-ca.crt"));
                if (QFileInfo::exists(bundled)) caPath = bundled;
            }
            if (!caPath.isEmpty()) config.ca_file = QFileInfo(caPath).absoluteFilePath().toStdString();
            if (config.ca_file.empty() || !QFileInfo::exists(QString::fromStdString(config.ca_file))) {
                qCritical().noquote() << QStringLiteral("TGW CA file is unavailable; pass --ca-file or install certs/vendor-dgw-ca.crt");
                return 2;
            }
            options.liveConfig = std::move(config);
        } catch (const std::exception &exception) {
            qCritical().noquote() << QStringLiteral("cannot load native TGW configuration: %1")
                                         .arg(QString::fromUtf8(exception.what()));
            return 2;
        }
    }

    premium::native_tgw::NativeTgwAdapter adapter(std::move(options));
    QString error;
    if (!adapter.start(&error)) {
        qCritical().noquote() << error;
        return 2;
    }
    QObject::connect(&application, &QCoreApplication::aboutToQuit,
                     &adapter, [&adapter] { adapter.stop(); });
    std::signal(SIGINT, requestStop);
    std::signal(SIGTERM, requestStop);
    QTimer signalTimer;
    signalTimer.setInterval(100);
    QObject::connect(&signalTimer, &QTimer::timeout, &application, [&application] {
        if (StopRequested.load()) application.quit();
    });
    signalTimer.start();
    return application.exec();
}
