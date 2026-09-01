#include "server/CoreServer.h"

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QFileInfo>

int main(int argc, char **argv)
{
    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("etf-premium-core"));
    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addOption({QStringLiteral("config"), QStringLiteral("Configuration JSON path"), QStringLiteral("path"), QStringLiteral("config/app.json")});
    parser.addOption({QStringLiteral("simulation"), QStringLiteral("Enable off-hours simulation scheduling")});
    parser.addOption({QStringLiteral("replay"), QStringLiteral("Mark all published data as replay and enable replay signals")});
    parser.addOption({QStringLiteral("force-quotes"), QStringLiteral("Start live quotes outside schedule; signal hard lock remains")});
    parser.process(application);

    QString config = parser.value(QStringLiteral("config"));
    if (!QFileInfo::exists(config) && config == QStringLiteral("config/app.json")) config = QStringLiteral("config/app.example.json");
    premium::CoreServer server(QFileInfo(config).absoluteFilePath(), parser.isSet(QStringLiteral("simulation")),
                               parser.isSet(QStringLiteral("replay")), parser.isSet(QStringLiteral("force-quotes")));
    QString error;
    if (!server.start(&error)) {
        qCritical().noquote() << error;
        return 2;
    }
    return application.exec();
}
