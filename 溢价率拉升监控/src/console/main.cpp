#include "console/ConsoleWindow.h"

#include <QApplication>
#include <QCommandLineParser>
#include <QDir>
#include <QFileInfo>

int main(int argc, char **argv)
{
    QApplication application(argc, argv);
    QApplication::setQuitOnLastWindowClosed(false);
    QApplication::setApplicationName(QStringLiteral("ETF溢价监控服务端"));
    QString defaultRoot = QDir(QCoreApplication::applicationDirPath())
                              .absoluteFilePath(QStringLiteral("../../../share/etf-premium-monitor"));
    defaultRoot = QDir::cleanPath(defaultRoot);
    if (!QFileInfo::exists(QDir(defaultRoot).filePath(QStringLiteral("config/app.example.json")))) {
        defaultRoot = QDir::currentPath();
    }
    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addOption({QStringLiteral("root"), QStringLiteral("Project deployment root"), QStringLiteral("path"), defaultRoot});
    parser.addOption({QStringLiteral("no-autostart"), QStringLiteral("Open console without starting services")});
    parser.process(application);
    premium::ConsoleWindow window(parser.value(QStringLiteral("root")), !parser.isSet(QStringLiteral("no-autostart")));
    window.show();
    return application.exec();
}
