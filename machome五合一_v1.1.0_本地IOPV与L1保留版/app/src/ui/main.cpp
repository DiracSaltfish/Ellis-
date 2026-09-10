#include "common/Config.h"
#include "ui/MainWindow.h"

#include <QApplication>
#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QDir>
#include <QFileInfo>
#include <QMessageBox>
#include <QStyleFactory>
#include <QTimer>

namespace {

QString findAgentProgram(const QString &explicitPath) {
    if (!explicitPath.isEmpty()) return hub::AppConfig::expandPath(explicitPath);
    const QDir directory(QCoreApplication::applicationDirPath());
    const QString bundled = directory.filePath(QStringLiteral("machome-hub-agent"));
    if (QFileInfo::exists(bundled)) return bundled;
    const QString buildSibling = QDir::cleanPath(directory.filePath(QStringLiteral("../../../machome-hub-agent")));
    if (QFileInfo::exists(buildSibling)) return buildSibling;
    return {};
}

} // namespace

int main(int argc, char *argv[]) {
    QApplication application(argc, argv);
    application.setQuitOnLastWindowClosed(false);
    QCoreApplication::setApplicationName(QStringLiteral("Machome 四合一运行中心"));
    QCoreApplication::setApplicationVersion(QStringLiteral(MACHOME_HUB_VERSION));
    QCoreApplication::setOrganizationName(QStringLiteral("Ellis"));
    QCoreApplication::setOrganizationDomain(QStringLiteral("ellis.local"));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Machome 四组业务的统一 Qt 运维界面"));
    parser.addHelpOption();
    parser.addVersionOption();
    QCommandLineOption configOption({QStringLiteral("c"), QStringLiteral("config")},
                                    QStringLiteral("模块配置 JSON"), QStringLiteral("path"),
                                    hub::AppConfig::defaultConfigPath());
    QCommandLineOption agentOption(QStringLiteral("agent"), QStringLiteral("Hub Agent 可执行文件"),
                                   QStringLiteral("path"));
    QCommandLineOption exitAfterAgentOption(
        QStringLiteral("exit-after-agent"),
        QStringLiteral("Agent 握手成功后以 0 退出；5 秒未成功则以 5 退出（自动验收用）"));
    parser.addOption(configOption);
    parser.addOption(agentOption);
    parser.addOption(exitAfterAgentOption);
    parser.process(application);

    QString error;
    const QString configPath = parser.value(configOption);
    const auto config = hub::AppConfig::load(configPath, &error);
    if (!config.isValid(&error)) {
        QMessageBox::critical(nullptr, QStringLiteral("配置错误"), error);
        return 2;
    }
    const QString agentProgram = findAgentProgram(parser.value(agentOption));
    hub::MainWindow window(config, configPath, agentProgram);
    if (parser.isSet(exitAfterAgentOption)) {
        QObject::connect(&window, &hub::MainWindow::agentReady,
                         &application, [&application] { application.exit(0); });
        QTimer::singleShot(5000, &application, [&application] { application.exit(5); });
    }
    window.show();
    return application.exec();
}
