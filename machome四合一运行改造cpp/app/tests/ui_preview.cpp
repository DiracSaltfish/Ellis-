// Offline visual QA: renders saved snapshots through the production page widgets.
// No HubClient, Agent, QMT client, or network connection is created here.
#include "ui/ModulePages.h"
#include "ui/MainWindow.h"
#include "ui/UiText.h"
#include <QApplication>
#include <QDir>
#include <QFile>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QListWidget>
#include <QMainWindow>
#include <QSettings>
#include <QStackedWidget>
#include <QTemporaryDir>
#include <QTextStream>
#include <QTabWidget>

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    if (argc != 3 && !(argc == 4 && QString::fromLocal8Bit(argv[3]) == "--all-tabs")) return 2;
    QTemporaryDir settings;
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
    app.setOrganizationName("MachomeOfflinePreview");
    app.setApplicationName("UiPreview");
    QFile input(QString::fromLocal8Bit(argv[1]));
    if (!input.open(QIODevice::ReadOnly)) return 3;
    const auto records = QJsonDocument::fromJson(input.readAll()).array();
    QHash<QString, QJsonObject> snapshots;
    for (const auto &v : records) {
        const auto message = v.toObject();
        if (message.value("payload").toObject().value("telemetry").isObject())
            snapshots.insert(message.value("module_id").toString(), message);
    }
    hub::AppConfig appConfig;
    const QStringList ids{"upload", "premium", "webull", "redemption"};
    for (const auto &id : ids) {
        hub::ModuleConfig config;
        config.id = id;
        config.adapter = id == "redemption" ? "realtime" : id;
        config.engine = "native";
        config.displayName = hub::ui::serviceTitle(config.adapter);
        config.controlEnabled = false;
        config.ownership = "shadow";
        appConfig.modules.append(config);
    }
    hub::MainWindow window(appConfig, {}, {}, nullptr, hub::MainWindow::StartMode::OfflinePreview);
    window.setWindowTitle(QStringLiteral("四合一界面预览 · 离线快照"));
    for (const auto &id : ids) QMetaObject::invokeMethod(&window, "onSnapshot", Qt::DirectConnection,
                                                       Q_ARG(QJsonObject, snapshots.value(id)));
    auto *nav = window.findChild<QListWidget *>("navigation");
    if (!nav) return 5;
    QStringList pages{"overview"};
    pages.append(ids);
    QDir output(QString::fromLocal8Bit(argv[2]));
    output.mkpath(".");
    for (const int width : {1440, 1120}) {
        window.resize(width, width == 1440 ? 940 : 760);
        window.show();
        for (int i = 0; i < pages.size(); ++i) {
            nav->setCurrentRow(i);
            app.processEvents();
            if (!window.grab().save(output.filePath(pages[i] + "-" + QString::number(width) + ".png"))) return 4;
            QTextStream(stdout) << pages[i] << " " << window.size().width() << "x" << window.size().height() << '\n';
            if (argc == 4 && i > 0) {
                for (auto *page : window.findChildren<hub::ModulePage *>()) {
                    if (page->moduleId() != pages[i]) continue;
                    auto *tabs = page->findChild<QTabWidget *>();
                    if (!tabs) continue;
                    for (int tab = 0; tab < tabs->count(); ++tab) {
                        tabs->setCurrentIndex(tab);
                        app.processEvents();
                        if (!window.grab().save(output.filePath(pages[i] + "-tab-" + QString::number(tab)
                            + "-" + QString::number(width) + ".png"))) return 4;
                    }
                    tabs->setCurrentIndex(0);
                }
            }
        }
    }
    return 0;
}
