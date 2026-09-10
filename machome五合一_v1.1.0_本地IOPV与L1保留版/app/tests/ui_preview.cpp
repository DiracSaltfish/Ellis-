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
#include <QThread>

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
    const QStringList ids{"upload", "premium", "webull", "redemption", "monitor_sync"};
    for (const auto &id : ids) {
        hub::ModuleConfig config;
        config.id = id;
        config.adapter = id == "redemption" ? "realtime" : id;
        config.engine = "native";
        config.displayName = hub::ui::serviceTitle(config.adapter);
        config.controlEnabled = false;
        if(id=="upload")config.settings.insert("sink_mode",snapshots.value(id).value("payload").toObject().value("telemetry").toObject().value("engine").toObject().value("business_engine"));
        if(id!="monitor_sync")config.allowedActions.append("set_operating_mode");
        config.ownership = "shadow";
        appConfig.modules.append(config);
    }
    hub::MainWindow window(appConfig, {}, {}, nullptr, hub::MainWindow::StartMode::OfflinePreview);
    window.setWindowTitle(QStringLiteral("五合一界面预览 · 离线快照"));
    for (const auto &id : ids) QMetaObject::invokeMethod(&window, "onSnapshot", Qt::DirectConnection,
                                                       Q_ARG(QJsonObject, snapshots.value(id)));
    auto *nav = window.findChild<QListWidget *>("navigation");
    if (!nav) return 5;
    QStringList pages{"overview"};
    pages.append(ids);
    QDir output(QString::fromLocal8Bit(argv[2]));
    output.mkpath(".");
    for (const auto size : {QSize(1440,940), QSize(1120,760), QSize(1120,720)}) {
        const int width=size.width();
        window.resize(size);
        window.show();
        for (int i = 0; i < pages.size(); ++i) {
            nav->setCurrentRow(i);
            app.processEvents();
            if (!window.grab().save(output.filePath(pages[i] + "-" + QString::number(width) + (size.height()==720?"-h720":"") + ".png"))) return 4;
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
                            + "-" + QString::number(width) + (size.height()==720?"-h720":"") + ".png"))) return 4;
                    }
                    tabs->setCurrentIndex(0);
                }
            }
        }
    }
    if(argc==4){
        hub::PcfDetailWindow pcf("159518", &window);pcf.setWindowTitle(QStringLiteral("PCF 界面验收 · 离线示例"));
        pcf.applyData({{"status","ready"},{"cached_at","2026-09-08T00:40:00Z"},
            {"summary",QJsonObject{{"TradingDay","20260908"},{"CreationRedemptionUnit",50000},{"EstimateCashComponent",125.5}}},
            {"components",QJsonArray{QJsonObject{{"SecurityID","000001"},{"SecurityName","示例成分 A"},{"Quantity",100},{"CashSubstitute","允许"}},QJsonObject{{"SecurityID","000002"},{"SecurityName","示例成分 B"},{"Quantity",250},{"CashSubstitute","禁止"}}}}});
        pcf.show();auto *pcfTabs=pcf.findChild<QTabWidget *>();
        for(int i=0;i<2;++i){pcfTabs->setCurrentIndex(i);for(int wait=0;wait<7;++wait){app.processEvents();QThread::msleep(20);}if(!pcf.grab().save(output.filePath(QStringLiteral("pcf-tab-%1.png").arg(i))))return 4;}
        hub::PremiumDetailWindow detail("510300.SH","离线示例 ETF", &window);
        QJsonArray bids,asks,volumes;for(int i=0;i<10;++i){bids.append(3910000-i*1000);asks.append(3911000+i*1000);volumes.append(120000+i*10000);}
        detail.applyDetail({{"s","510300.SH"},{"last_price_e6",3910000},{"bid1_price_e6",3910000},{"iopv_e6",3900000},{"bid_prices_e6",bids},{"ask_prices_e6",asks},{"bid_volumes_e2",volumes},{"ask_volumes_e2",volumes}});
        detail.show();for(int i=0;i<10;++i){app.processEvents();QThread::msleep(20);}if(!detail.grab().save(output.filePath("premium-depth.png")))return 4;
    }
    return 0;
}
