#include "modules/ibkr/IbkrBridgeCore.h"
#include "modules/ibkr/NativeIbkrBridge.h"

#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QCoreApplication>
#include <QJsonDocument>
#include <QTextStream>
#include <QTimer>

#include <atomic>
#include <csignal>

namespace {
std::atomic_bool stopRequested = false;

void requestStop(int)
{
    stopRequested.store(true);
}
}

int main(int argc, char **argv)
{
    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("machome-ibkr-bridge"));
    QCoreApplication::setApplicationVersion(QStringLiteral("1.0.0"));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Machome 原生 IBKR TWS 行情扇出桥"));
    parser.addHelpOption();
    parser.addVersionOption();
    QCommandLineOption configOption({QStringLiteral("c"), QStringLiteral("config")},
                                    QStringLiteral("JSON 配置文件"),
                                    QStringLiteral("path"));
    QCommandLineOption validateOption(QStringLiteral("validate-config"),
                                      QStringLiteral("仅校验配置并输出脱敏摘要"));
    parser.addOption(configOption);
    parser.addOption(validateOption);
    parser.process(application);

    const QString configPath = parser.value(configOption).trimmed();
    if (configPath.isEmpty()) {
        QTextStream(stderr) << "--config is required\n";
        return 2;
    }
    machome::ibkr::BridgeConfig config;
    QString error;
    if (!machome::ibkr::BridgeConfig::loadFile(configPath, &config, &error)) {
        QTextStream(stderr) << error << '\n';
        return 2;
    }
    if (parser.isSet(validateOption)) {
        QTextStream(stdout) << QJsonDocument(config.safeSummary()).toJson(QJsonDocument::Indented);
        return 0;
    }

    machome::ibkr::NativeIbkrBridge bridge(config);
    if (!bridge.start(&error)) {
        QTextStream(stderr) << error << '\n';
        return 1;
    }

    std::signal(SIGTERM, requestStop);
    std::signal(SIGINT, requestStop);
    QTimer signalTimer;
    signalTimer.setInterval(200);
    QObject::connect(&signalTimer, &QTimer::timeout, &application, [&application] {
        if (stopRequested.load()) application.quit();
    });
    signalTimer.start();
    QObject::connect(&application, &QCoreApplication::aboutToQuit,
                     &bridge, &machome::ibkr::NativeIbkrBridge::stop);
    return application.exec();
}
