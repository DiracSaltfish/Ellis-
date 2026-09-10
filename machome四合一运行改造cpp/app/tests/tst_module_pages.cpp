#include "ui/ModulePages.h"
#include "ui/UiWidgets.h"
#include "ui/MonitorSyncPage.h"
#include "ui/MainWindow.h"
#include <QTableView>
#include <QSortFilterProxyModel>
#include <QComboBox>
#include <QScrollArea>
#include <QHeaderView>
#include <QTabWidget>
#include "ui/PremiumHistory.h"
#include "ui/UiText.h"
#include "ui/ServiceOverview.h"
#include <QTreeWidget>
#include <QToolButton>

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSignalSpy>
#include <QTableWidget>
#include <QTemporaryDir>
#include <QtTest>

class ModulePagesTests final : public QObject {
    Q_OBJECT

private slots:
    void viewSortingPreservesSourceRowsAndDetailIdentity() {
        auto *source=hub::ui::jsonTable({"代码","价格"});source->setRowCount(2);
        source->setItem(0,0,new QTableWidgetItem("AAA"));source->setItem(0,1,new QTableWidgetItem("10.5"));
        source->setItem(1,0,new QTableWidgetItem("BBB"));source->setItem(1,1,new QTableWidgetItem("2.5"));
        QScopedPointer<QWidget> panel(hub::ui::tablePanel(source,"sortRegression",QStringLiteral("查看盘口")));
        auto *view=panel->findChild<QTableView *>("sortRegressionView");QVERIFY(view);
        view->sortByColumn(1,Qt::AscendingOrder);QCOMPARE(view->model()->index(0,0).data().toString(),QString("BBB"));
        QCOMPARE(source->item(0,0)->text(),QString("AAA"));
        // Incoming source-row update must not land on the sorted first row.
        source->item(0,1)->setText("1.5");QCOMPARE(view->model()->index(0,0).data().toString(),QString("AAA"));
        panel->findChild<QLineEdit *>("sortRegressionSearch")->setText("BBB");QCOMPARE(view->model()->rowCount(),1);
        QSignalSpy detail(source,&QTableWidget::cellDoubleClicked);
        view->setCurrentIndex(view->model()->index(0,0));
        for(auto *button:panel->findChildren<QPushButton *>())if(button->text()==QStringLiteral("查看盘口"))button->click();
        QCOMPARE(detail.size(),1);QCOMPARE(detail.first().first().toInt(),1);
    }
    void syncStructuredViewUsesUnknownForMissingAcknowledgementAndDisablesWrites() {
        hub::ModuleConfig config;config.id="monitor_sync";config.adapter="monitor_sync";config.engine="native";config.controlEnabled=false;config.ownership="shadow";
        hub::MonitorSyncPage page(config);QSignalSpy commands(&page,&hub::ModulePage::commandRequested);
        page.applySnapshot({{"payload",QJsonObject{{"control_enabled",false},{"ownership","shadow"},{"telemetry",QJsonObject{{"engine",QJsonObject{{"running",true},{"clients",QJsonArray{QJsonObject{{"name","test"},{"device_id","test-id"}}}}}}}}}}});
        auto *devices=page.findChild<QTableWidget *>("syncDevices");QVERIFY(devices);QCOMPARE(devices->rowCount(),1);
        QCOMPARE(devices->item(0,4)->text(),QStringLiteral("未报告"));QCOMPARE(devices->item(0,5)->text(),QStringLiteral("未报告"));
        for(auto *button:page.findChildren<QPushButton *>())if(button->text()==QStringLiteral("备份全部文件")||button->text()==QStringLiteral("设置监听端口"))QVERIFY(!button->isEnabled());
        QVERIFY(commands.isEmpty());
    }
    void fiveModuleOverviewDoesNotWaitForSyncModeOrUseWindMetrics() {
        hub::AppConfig config;
        for(const auto &id:QStringList{"upload","premium","webull","redemption","monitor_sync"}){hub::ModuleConfig m;m.id=id;m.adapter=id=="redemption"?"realtime":id;m.engine="native";m.enabled=true;if(id!="monitor_sync")m.allowedActions<<"set_operating_mode";config.modules<<m;}
        hub::MainWindow window(config,{}, {},nullptr,hub::MainWindow::StartMode::OfflinePreview);
        for(const auto &id:QStringList{"upload","premium","webull","redemption"}){
            QJsonObject message{{"module_id",id},{"payload",QJsonObject{{"telemetry",QJsonObject{{"engine",QJsonObject{{"operating_mode","work"}}}}}}}};
            QVERIFY(QMetaObject::invokeMethod(&window,"onSnapshot",Q_ARG(QJsonObject,message)));
        }
        QCOMPARE(window.findChild<QLabel *>("globalOperatingModeState")->text(),QStringLiteral("当前：工作模式"));
        QJsonObject message{{"module_id","monitor_sync"},{"payload",QJsonObject{{"telemetry",QJsonObject{{"engine",QJsonObject{{"clients",QJsonArray{}},{"pending",0},{"free_bytes",1073741824.0}}}}}}}};
        QVERIFY(QMetaObject::invokeMethod(&window,"onSnapshot",Q_ARG(QJsonObject,message)));
        bool sync=false;for(auto *label:window.findChildren<QLabel *>("metric"))sync|=label->text().contains(QStringLiteral("在线设备 0 · 处理中 0"));QVERIFY(sync);
        window.resize(1120,720);window.show();QTest::qWait(50);
        auto *scroll=window.findChild<QScrollArea *>("overviewCardsScroll");QVERIFY(scroll);
        QCOMPARE(scroll->widget()->findChildren<QFrame *>("moduleCard").size(),5);
        for(auto *card:scroll->widget()->findChildren<QFrame *>("moduleCard")){QVERIFY(card->height()>=180);for(auto *button:card->findChildren<QPushButton *>())QVERIFY(card->rect().contains(QRect(button->mapTo(card,QPoint()),button->size())));}
    }
    void pcfProvidesStructuredComponentsWithoutCommands() {
        hub::PcfDetailWindow page("159518");QSignalSpy commands(&page,&hub::PcfDetailWindow::qmtCommandRequested);
        page.applyData({{"status","ready"},{"summary",QJsonObject{{"TradingDay","20260908"}}},{"components",QJsonArray{QJsonObject{{"SecurityID","000001"},{"Quantity",100}}}}});
        auto *table=page.findChild<QTableWidget *>("pcfComponentsTable");QVERIFY(table);QCOMPARE(table->rowCount(),1);
        bool quantity=false,security=false;for(int c=0;c<table->columnCount();++c){quantity|=table->item(0,c)->text()=="100";security|=table->item(0,c)->text()=="000001";}QVERIFY(quantity&&security);QVERIFY(commands.isEmpty());
    }
    void diagnosticRefreshRetainsUserExpansion() {
        hub::ModuleConfig config;config.id="upload";config.adapter="upload";hub::UploadPage page(config);
        auto snapshot=[](int n){return QJsonObject{{"payload",QJsonObject{{"telemetry",QJsonObject{{"engine",QJsonObject{{"nested",QJsonObject{{"value",n}}}}}}}}}};};
        page.applySnapshot(snapshot(1));auto *tree=page.findChild<QTreeWidget *>();QVERIFY(tree);
        tree->collapseAll();page.applySnapshot(snapshot(2));for(int i=0;i<tree->topLevelItemCount();++i)QVERIFY(!tree->topLevelItem(i)->isExpanded());
    }
    void windShutdownIsAvailableOnOverviewAndRespectsControlGate() {
        hub::ModuleConfig config;config.id="redemption";config.adapter="realtime";
        config.controlEnabled=true;config.ownership="logic";
        hub::RealtimePage page(config);QSignalSpy commands(&page,&hub::ModulePage::commandRequested);
        auto *button=page.findChild<QPushButton *>("overviewWindShutdown");QVERIFY(button);
        page.applySnapshot({{"payload",QJsonObject{{"control_enabled",true},{"ownership","logic"}}}});
        QVERIFY(button->isEnabled());button->click();QCOMPARE(commands.size(),1);
        QCOMPARE(commands.first().at(1).toString(),QString("redemption_wind_shutdown_cleanup"));
        page.applySnapshot({{"payload",QJsonObject{{"control_enabled",false},{"ownership","shadow"}}}});
        QVERIFY(!button->isEnabled());button->click();QCOMPARE(commands.size(),1);
    }

    void closedOverviewKeepsServicesOnlineWithoutFalseCollectionAlarm() {
        hub::ServiceOverview page("redemption");
        QJsonObject snapshot{{"operating_mode", "work"}, {"monitoring", false},
            {"schedule", QJsonObject{{"phase", "closed_pcf_cache"}, {"monitoring_desired", false}}},
            {"items", QJsonArray{}}, {"wind", QJsonObject{{"state", "cleaned"}, {"tbapi_loaded", false}}}};
        QJsonObject telemetry{{"snapshot", snapshot},
            {"health", QJsonObject{{"wind_helper_ok", false}}},
            {"qmt_backends", QJsonObject{{"QMT1", QJsonObject{{"connection_state", "disconnected"}, {"connection_policy", "on_demand"}}}}}};
        page.applySnapshot({{"lifecycle", "running"}, {"work_state", "scheduled_idle"}, {"telemetry", telemetry}});
        QVERIFY(!page.findChild<QLabel *>("noticeText")->text().contains(QString("健康检查")));
        QVERIFY(page.findChild<QLabel *>("noticeText")->text().contains(QString("清单仍可查询")));
        const auto metrics = page.findChildren<QLabel *>("metricValue");
        QCOMPARE(metrics.first()->text(), QString("按计划休眠"));
        bool policy = false, phase = false;
        for (auto *label : page.findChildren<QLabel *>()) {
            policy |= label->text() == QString("按需连接（默认不连接）");
            phase |= label->text() == QString("收盘休眠 · 申赎清单缓存服务中");
        }
        QVERIFY(policy); QVERIFY(phase);
        // A subscription that actually remains active must not be hidden.
        snapshot.insert("monitoring", true); telemetry.insert("snapshot", snapshot);
        page.applySnapshot({{"lifecycle", "running"}, {"work_state", "active"}, {"telemetry", telemetry}});
        QCOMPARE(metrics.first()->text(), QString("监控中"));
        QVERIFY(page.findChild<QLabel *>("noticeText")->text().contains(QString("Wind 采集尚未就绪")));
    }
    void premiumPlannedDisconnectionIsNotUpstreamFailure() {
        hub::ServiceOverview page("premium");
        QJsonObject status{{"cn_quotes_desired", false}, {"hk_quotes_desired", false}, {"upstream_healthy", false}};
        page.applySnapshot({{"lifecycle", "running"}, {"work_state", "scheduled_idle"}, {"telemetry", QJsonObject{{"status", status}}}});
        QVERIFY(!page.findChild<QLabel *>("noticeText")->text().contains(QString("健康检查")));
        status.insert("hk_quotes_desired", true);
        page.applySnapshot({{"lifecycle", "running"}, {"work_state", "degraded"}, {"telemetry", QJsonObject{{"status", status}}}});
        QVERIFY(page.findChild<QLabel *>("noticeText")->text().contains(QString("健康检查")));
    }

    void premiumNativeSignalsShowFieldsAndDeduplicateReplay() {
        hub::ModuleConfig config; config.id = "premium"; config.adapter = "premium";
        hub::PremiumPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);
        QSignalSpy alerts(&page, &hub::ModulePage::alertRequested);
        page.applyEvent({{"event_kind", "premium.summary"}, {"payload", QJsonObject{{"s", "510300.SH"}, {"name", "测试ETF"}}}});
        QJsonObject signal{{"type", "signal"}, {"symbol", "510300.SH"}, {"signal_seq", 7},
            {"occurred_at", "2026-09-07T13:45:00+08:00"}, {"model", "premium+radar"},
            {"premium_ppm", 12345}, {"rise_30s_ppm", 2345}, {"repeat", false}, {"backfill", false}};
        page.applyEvent({{"event_kind", "premium.signal"}, {"payload", signal}});
        auto *table = page.findChild<QTableWidget *>("premiumSignalsTable");
        QCOMPARE(table->rowCount(), 1);
        QCOMPARE(table->item(0, 0)->text(), hub::ui::localTimeText(signal.value("occurred_at").toString()));
        QCOMPARE(table->item(0, 2)->text(), QStringLiteral("测试ETF"));
        QCOMPARE(table->item(0, 3)->text(), QStringLiteral("溢价率 + 快速拉涨雷达"));
        QCOMPARE(table->item(0, 4)->text(), QStringLiteral("1.234%"));
        QCOMPARE(table->item(0, 5)->text(), QStringLiteral("0.234%"));
        QCOMPARE(table->item(0, 6)->text(), QStringLiteral("首次触发"));
        QCOMPARE(alerts.count(), 1);
        signal.insert("backfill", true);
        page.applyEvent({{"event_kind", "premium.signal"}, {"payload", signal}});
        signal.insert("backfill", false);
        page.applyEvent({{"event_kind", "premium.signal"}, {"payload", signal}});
        QCOMPARE(table->rowCount(), 1);
        QCOMPARE(alerts.count(), 1);
        signal.insert("signal_seq", 8); signal.insert("repeat", true);
        page.applyEvent({{"event_kind", "premium.signal"}, {"payload", signal}});
        QCOMPARE(table->rowCount(), 2);
        QCOMPARE(table->item(0, 6)->text(), QStringLiteral("重复提醒"));
        page.applyEvent({{"event_kind", "premium.sync_complete"}, {"payload", QJsonObject{{"type", "sync_complete"}}}});
        QCOMPARE(page.findChild<QLabel *>("premiumSyncState")->text(), QStringLiteral("同步完成"));
        QVERIFY(commands.isEmpty());
    }

    void webullStatusOverridesOldBookFreshnessAndShowsWireFormats() {
        hub::ModuleConfig config; config.id = "webull"; config.adapter = "webull";
        hub::WebullPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);
        const QJsonObject book{{"symbol", "TEST"},
            {"bids", QJsonArray{QJsonObject{{"level", 3}, {"price", "100.125"}, {"size", "123456"}}}},
            {"asks", QJsonArray{QJsonObject{{"price", "100.250"}, {"size", "456"}}}}};
        const QJsonObject telemetry{
            {"book", book}, {"freshness", "fresh"}, {"status", QJsonObject{{"data_state", "stale"}}},
            {"clients", QJsonArray{QJsonObject{{"client_id", "test-client"}, {"remote", "127.0.0.1"}, {"message_count", 9}}}}};
        page.applySnapshot({{"payload", QJsonObject{{"telemetry", telemetry}}}});
        auto *table = page.findChild<QTableWidget *>("webullBookTable");
        QCOMPARE(table->rowCount(), 2);
        QCOMPARE(table->item(1, 0)->text(), QStringLiteral("买盘"));
        QCOMPARE(table->item(1, 1)->text(), QStringLiteral("3"));
        QCOMPARE(table->item(1, 2)->text(), QStringLiteral("100.125"));
        QCOMPARE(table->item(1, 3)->text(), QStringLiteral("123456"));
        QCOMPARE(page.findChild<QLineEdit *>("webullSymbol")->text(), QStringLiteral("TEST"));
        bool stale = false;
        for (auto *label : page.findChildren<QLabel *>())
            if (label->property("role") == "webullFreshness") stale = label->text() == hub::ui::stateText("stale");
        QVERIFY(stale);
        QCOMPARE(page.findChild<QTableWidget *>("webullClientsTable")->item(0, 4)->text(), QStringLiteral("9"));
        page.applyEvent({{"event_kind", "webull.book"}, {"payload", QJsonObject{
            {"symbol", "TEST"}, {"book", QJsonObject{{"bids", QJsonArray{QJsonObject{{"price", "101.5"}, {"volume", "20"}}}}, {"asks", QJsonArray{}}}}}}});
        QCOMPARE(table->rowCount(), 1);
        QCOMPARE(table->item(0, 3)->text(), QStringLiteral("20"));
        page.applySnapshot({{"payload", QJsonObject{{"telemetry", QJsonObject{{"status", QJsonObject{{"data_state", "no_data"}}}}}}}});
        QCOMPARE(table->rowCount(), 0);
        QVERIFY(commands.isEmpty());
    }

    void bundledUploadDistinguishesMissingDetailsAndShowsReceipts() {
        hub::ModuleConfig config; config.id = "upload"; config.adapter = "upload";
        config.settings.insert("sink_mode", "bundled_business");
        hub::UploadPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);
        const QJsonObject telemetry{{"workers", QJsonArray{QJsonObject{{"id", "sina"}, {"state", "running"}}}},
            {"engine", QJsonObject{{"business_engine", "bundled_business"}, {"upload_health", QJsonArray{
                QJsonObject{{"source", "test-source"}, {"pid", 123}, {"stage", "uploaded"}, {"state", "running"},
                    {"accepted", 15}, {"last_success_at", "2026-09-07T13:00:00+08:00"}}}}}}};
        page.applySnapshot({{"payload", QJsonObject{{"telemetry", telemetry}}}});
        QVERIFY(page.findChild<QLabel *>("uploadFundsTableStatus")->text().contains(QStringLiteral("尚未接入")));
        QVERIFY(page.findChild<QLabel *>("uploadHistoryTableStatus")->text().contains(QStringLiteral("尚未接入")));
        auto *receipts = page.findChild<QTableWidget *>("uploadRecordsTable");
        QCOMPARE(receipts->rowCount(), 1);
        QCOMPARE(receipts->item(0, 0)->text(), QStringLiteral("test-source"));
        QCOMPARE(receipts->item(0, 4)->text(), QStringLiteral("15"));
        QVERIFY(receipts->item(0, 6)->text() != QStringLiteral("—"));
        QCOMPARE(page.findChild<QTableWidget *>("uploadJobsTable")->item(0, 5)->text(), QStringLiteral("未报告"));
        QVERIFY(commands.isEmpty());
    }

    void uploadRedemptionSummaryUsesOnlyLatestTwoDates() {
        hub::ModuleConfig config; config.id = "upload"; config.adapter = "upload";
        hub::UploadPage page(config);
        QJsonArray shares;
        for (int day : {3, 1, 4, 2}) shares.append(QJsonObject{{"symbol", "SZ159518"},
            {"date", QStringLiteral("2026-09-%1").arg(day, 2, 10, QChar('0'))}, {"shares_10k", 100 - day}});
        page.applySnapshot({{"payload", QJsonObject{{"telemetry", QJsonObject{
            {"history", QJsonObject{{"share_history", shares}}}, {"funds", QJsonArray{}}, {"upload_records", QJsonArray{}}}}}}});
        auto *history = page.findChild<QTableWidget *>("uploadHistoryTable");
        QCOMPARE(history->rowCount(), 5);
        QCOMPARE(history->item(0, 0)->text(), QStringLiteral("昨日赎回"));
        QCOMPARE(history->item(0, 2)->text(), QStringLiteral("2026-09-04"));
        QCOMPARE(history->item(0, 3)->text(), QStringLiteral("1"));
        QVERIFY(page.findChild<QLabel *>("uploadFundsTableStatus")->text().contains(QStringLiteral("暂无基金记录")));
    }

    void pcfMissingComponentsDoesNotLookLikeSuccessfulData() {
        hub::PcfDetailWindow page(QStringLiteral("159518"));
        QSignalSpy commands(&page, &hub::PcfDetailWindow::qmtCommandRequested);
        page.applyData({{"symbol", "159518"}, {"status", "error"}, {"error", "PCF pending"}});
        QVERIFY(page.findChild<QPlainTextEdit *>("pcfComponents")->toPlainText().contains(QStringLiteral("尚未收到")));
        bool hasError = false;
        for (auto *label : page.findChildren<QLabel *>())
            if (label->property("role") == "pcfDataStatus") hasError = label->text().contains("PCF pending");
        QVERIFY(hasError);
        page.applyData({{"symbol", "159518"}, {"status", "ready"},
            {"components", QJsonArray{QJsonObject{{"SecurityID", "000001"}, {"Quantity", 100}}}}});
        QVERIFY(page.findChild<QPlainTextEdit *>("pcfComponents")->toPlainText().contains("000001"));
        QVERIFY(commands.isEmpty());
    }

    void overviewDoesNotReportMissingTelemetryAsHealthy() {
        hub::ModuleConfig config;
        config.id = "upload"; config.adapter = "upload"; config.engine = "native";
        hub::UploadPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);
        page.applySnapshot({{"payload", QJsonObject{{"lifecycle", "running"}, {"work_state", "degraded"}}}});
        QVERIFY(page.findChild<QLabel *>("stateWarn"));
        auto *overview = page.findChild<QWidget *>("serviceOverview");
        QVERIFY(overview);
        const auto metrics = overview->findChildren<QLabel *>("metricValue");
        QCOMPARE(metrics.size(), 4);
        for (const auto *metric : metrics) QCOMPARE(metric->text(), QStringLiteral("—"));
        QVERIFY(overview->findChild<QLabel *>("noticeText")->text().contains(QStringLiteral("尚未收到")));
        auto *toggle = overview->findChild<QToolButton *>("diagnosticsToggle");
        auto *tree = overview->findChild<QTreeWidget *>();
        QVERIFY(toggle && tree);
        QVERIFY(tree->isHidden());
        toggle->setChecked(true);
        QVERIFY(!tree->isHidden());
        QCOMPARE(commands.count(), 0);
    }

    void webullNoDataIsNotLabeledExpired() {
        hub::ModuleConfig config;
        config.id = "webull"; config.adapter = "webull"; config.engine = "native";
        hub::WebullPage page(config);
        page.applySnapshot({{"payload", QJsonObject{{"lifecycle", "running"}, {"work_state", "degraded"},
            {"telemetry", QJsonObject{{"status", QJsonObject{{"auth_state", "authenticated"}, {"data_state", "no_data"}}}}}}}});
        const auto labels = page.findChildren<QLabel *>();
        bool noData = false;
        for (const auto *label : labels) {
            if (label->text() == QStringLiteral("暂无数据")) noData = true;
            QVERIFY(!label->text().contains(QStringLiteral("数据过期 /")));
        }
        QVERIFY(noData);
    }

    void displayTranslationPreservesProtocolValues() {
        QCOMPARE(hub::ui::fieldText("capabilities"), QStringLiteral("支持的操作"));
        QCOMPARE(hub::ui::actionText("upload_run_job"), QStringLiteral("重新执行上传任务"));
        QCOMPARE(hub::ui::stateText("managed"), QStringLiteral("统一管理"));
        QCOMPARE(hub::ui::stateText("unrecognized_state_42"), QStringLiteral("unrecognized_state_42"));
        QCOMPARE(hub::ui::stateStyle({{"lifecycle", "running"}, {"work_state", "degraded"}}), QStringLiteral("stateWarn"));
        QCOMPARE(hub::ui::stateStyle({{"lifecycle", "running"}, {"work_state", "scheduled_idle"}}), QStringLiteral("stateIdle"));
        QCOMPARE(hub::ui::localTimeText("2026-09-07T03:55:09.847Z"),
                 QDateTime::fromString("2026-09-07T03:55:09.847Z", Qt::ISODateWithMs).toLocalTime().toString("MM-dd HH:mm:ss"));
    }
    void mapsAuthoritativeRealtimeNestedSchema() {
        hub::ModuleConfig config;
        config.id = QStringLiteral("redemption");
        config.displayName = QStringLiteral("实时申购赎回数据监控");
        config.adapter = QStringLiteral("realtime");
        config.controlEnabled = true;
        config.ownership = QStringLiteral("logic");
        hub::RealtimePage page(config);

        const QJsonObject item{
            {QStringLiteral("symbol"), QStringLiteral("159513")},
            {QStringLiteral("windcode"), QStringLiteral("159513.SZ")},
            {QStringLiteral("name"), QStringLiteral("纳指科技ETF")},
            {QStringLiteral("status"), QStringLiteral("monitoring")},
            {QStringLiteral("values"), QJsonObject{
                {QStringLiteral("etfbuyamount"), 1'200'000},
                {QStringLiteral("etfsellamount"), 600'000},
                {QStringLiteral("netamount"), 600'000}}},
            {QStringLiteral("pcf"), QJsonObject{
                {QStringLiteral("status"), QStringLiteral("ready")},
                {QStringLiteral("creation_redemption_unit"), 600'000},
                {QStringLiteral("creation_allowed"), true},
                {QStringLiteral("creation_limit"), 3'600'000},
                {QStringLiteral("net_creation_limit"), 2'400'000}}},
            {QStringLiteral("opportunity"), QJsonObject{
                {QStringLiteral("kind"), QStringLiteral("creation")},
                {QStringLiteral("label"), QStringLiteral("盘中申购机会")}}},
            {QStringLiteral("updated_at"), QStringLiteral("09:31:05")},
            {QStringLiteral("last_change"), QJsonArray{
                QJsonObject{{QStringLiteral("field"), QStringLiteral("etfsellamount")},
                            {QStringLiteral("text"), QStringLiteral("赎回份额增加 60 0000")}}}}
        };
        page.applySnapshot(QJsonObject{
            {QStringLiteral("timestamp"), QStringLiteral("2026-09-04T01:31:05Z")},
            {QStringLiteral("payload"), QJsonObject{
                {QStringLiteral("control_enabled"), true},
                {QStringLiteral("ownership"), QStringLiteral("logic")},
                {QStringLiteral("telemetry"), QJsonObject{
                    {QStringLiteral("snapshot"), QJsonObject{
                        {QStringLiteral("protocol"), 1},
                        {QStringLiteral("type"), QStringLiteral("snapshot")},
                        {QStringLiteral("items"), QJsonArray{item}}}}}}}}});

        auto *table = page.findChild<QTableWidget *>(QStringLiteral("realtimeSnapshotTable"));
        QVERIFY(table);
        QCOMPARE(table->rowCount(), 1);
        const QStringList expected{
            QStringLiteral("159513"), QStringLiteral("纳指科技ETF"), QStringLiteral("监控中"),
            QStringLiteral("120 0000"), QStringLiteral("60 0000"), QStringLiteral("+60 0000"),
            QStringLiteral("2"), QStringLiteral("1"), QStringLiteral("+1"), QStringLiteral("3"),
            QStringLiteral("盘中申购机会"), QStringLiteral("09:31:05"),
            QStringLiteral("赎回份额增加 60 0000")};
        QCOMPARE(table->columnCount(), expected.size());
        for (int column = 0; column < expected.size(); ++column) {
            QVERIFY2(table->item(0, column), qPrintable(QStringLiteral("missing cell %1").arg(column)));
            QCOMPARE(table->item(0, column)->text(), expected[column]);
        }

        QJsonObject changed = item;
        changed.insert(QStringLiteral("values"), QJsonObject{
            {QStringLiteral("etfbuyamount"), 1'200'000},
            {QStringLiteral("etfsellamount"), 1'200'000},
            {QStringLiteral("netamount"), 0}});
        page.applyEvent(QJsonObject{
            {QStringLiteral("event_kind"), QStringLiteral("redemption.change")},
            {QStringLiteral("payload"), QJsonObject{
                {QStringLiteral("protocol"), 1},
                {QStringLiteral("type"), QStringLiteral("change")},
                {QStringLiteral("items"), QJsonArray{QJsonObject{
                    {QStringLiteral("symbol"), QStringLiteral("159513")},
                    {QStringLiteral("current"), changed}}}}}}});
        QCOMPARE(table->rowCount(), 1);
        QCOMPARE(table->item(0, 4)->text(), QStringLiteral("120 0000"));
        QCOMPARE(table->item(0, 5)->text(), QStringLiteral("0"));
    }

    void premiumTelemetryUpdatesStateWithoutSpammingTheLog()
    {
        hub::ModuleConfig config;
        config.id = QStringLiteral("premium");
        config.displayName = QStringLiteral("溢价率上升监控 A 端");
        config.adapter = QStringLiteral("premium");
        hub::PremiumPage page(config);

        auto *log = page.findChild<QPlainTextEdit *>(
            QStringLiteral("moduleEventLog"));
        auto *table = page.findChild<QTableWidget *>(
            QStringLiteral("premiumSummariesTable"));
        QVERIFY(log);
        QVERIFY(table);

        const QJsonObject summary{{QStringLiteral("symbol"),
                                   QStringLiteral("510300.SH")},
                                  {QStringLiteral("last_price_e6"), 3'910'000}};
        for (int update = 0; update < 20; ++update) {
            QJsonObject next = summary;
            next.insert(QStringLiteral("sequence"), update);
            page.applyEvent({{QStringLiteral("event_kind"),
                              QStringLiteral("premium.summary")},
                             {QStringLiteral("payload"), next}});
        }
        QTRY_COMPARE_WITH_TIMEOUT(table->rowCount(), 1, 1'500);
        QVERIFY(log->toPlainText().isEmpty());

        page.applyEvent({{QStringLiteral("event_kind"),
                          QStringLiteral("premium.symbol_removed")},
                         {QStringLiteral("payload"),
                          QJsonObject{{QStringLiteral("symbol"),
                                       QStringLiteral("510300.SH")}}}});
        QCOMPARE(table->rowCount(), 0);

        page.applyEvent({{QStringLiteral("timestamp"),
                          QStringLiteral("2026-09-04T07:00:00Z")},
                         {QStringLiteral("event_kind"),
                          QStringLiteral("premium.error")},
                         {QStringLiteral("payload"),
                          QJsonObject{{QStringLiteral("message"),
                                       QStringLiteral("test failure")}}}});
        QVERIFY(log->toPlainText().contains(QStringLiteral("premium.error")));
        QCOMPARE(log->document()->maximumBlockCount(), 500);
    }

    void premiumPanoramaDisplaysNativeWireSummary() {
        hub::ModuleConfig config;
        config.id = QStringLiteral("premium");
        config.adapter = QStringLiteral("premium");
        hub::PremiumPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);
        auto *table = page.findChild<QTableWidget *>(QStringLiteral("premiumSummariesTable"));
        QVERIFY(table);
        // Same schema as the native A engine: s, e6 prices, ppm, orig_time.
        QJsonObject payload{{"s", "159506.SZ"}, {"name", "测试ETF"},
                            {"last_price_e6", 1297000}, {"iopv_e6", 1301400},
                            {"display_premium_ppm", -3381},
                            {"orig_time", qint64(20260907134500000LL)},
                            {"source_ready", true}, {"mapping_verified", false}};
        page.applyEvent({{"event_kind", "premium.summary"}, {"payload", payload}});
        QTRY_COMPARE_WITH_TIMEOUT(table->rowCount(), 1, 1500);
        QCOMPARE(table->item(0, 0)->text(), QStringLiteral("159506.SZ"));
        QCOMPARE(table->item(0, 1)->text(), QStringLiteral("测试ETF"));
        QCOMPARE(table->item(0, 2)->text(), QStringLiteral("1.297"));
        QCOMPARE(table->item(0, 3)->text(), QStringLiteral("1.3014"));
        QCOMPARE(table->item(0, 4)->text(), QStringLiteral("-0.338%"));
        QCOMPARE(table->item(0, 5)->text(), QStringLiteral("09-07 13:45:00.000"));
        QVERIFY(table->item(0, 6)->text().contains(QStringLiteral("映射未验证")));
        payload.insert(QStringLiteral("symbol"), QStringLiteral("159506.SZ"));
        payload.insert(QStringLiteral("last_price_e6"), 1298000);
        payload.insert(QStringLiteral("iopv_e6"), QJsonValue::Null);
        payload.insert(QStringLiteral("source_ready"), false);
        page.applyEvent({{"event_kind", "premium.summary"}, {"payload", payload}});
        QTRY_COMPARE_WITH_TIMEOUT(table->item(0, 2)->text(), QStringLiteral("1.298"), 1500);
        QCOMPARE(table->rowCount(), 1);
        QCOMPARE(table->item(0, 3)->text(), QStringLiteral("—"));
        QVERIFY(table->item(0, 6)->text().contains(QStringLiteral("等待数据")));
        QVERIFY(commands.isEmpty());
    }

    void emitsWatchlistAndNameCommands() {
        hub::ModuleConfig config;
        config.id = QStringLiteral("redemption");
        config.displayName = QStringLiteral("实时申购赎回数据监控");
        config.adapter = QStringLiteral("realtime");
        config.controlEnabled = true;
        config.ownership = QStringLiteral("logic");
        hub::RealtimePage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);

        auto *watchlist = page.findChild<QPlainTextEdit *>(QStringLiteral("realtimeWatchlistEdit"));
        auto *saveWatchlist = page.findChild<QPushButton *>(QStringLiteral("realtimeSaveWatchlist"));
        QVERIFY(watchlist);
        QVERIFY(saveWatchlist);
        QVERIFY(saveWatchlist->isEnabled());
        watchlist->setPlainText(QStringLiteral("159518\n159393\n"));
        saveWatchlist->click();
        QCOMPARE(commands.size(), 1);
        auto arguments = commands.takeFirst();
        QCOMPARE(arguments.at(0).toString(), QStringLiteral("redemption"));
        QCOMPARE(arguments.at(1).toString(), QStringLiteral("redemption_set_watchlist"));
        const QJsonArray expectedSymbols{QStringLiteral("159518"), QStringLiteral("159393")};
        QCOMPARE(arguments.at(2).toJsonObject().value(QStringLiteral("symbols")).toArray(),
                 expectedSymbols);

        auto *symbol = page.findChild<QLineEdit *>(QStringLiteral("realtimeNameSymbol"));
        auto *name = page.findChild<QLineEdit *>(QStringLiteral("realtimeSymbolName"));
        auto *saveName = page.findChild<QPushButton *>(QStringLiteral("realtimeSaveSymbolName"));
        QVERIFY(symbol);
        QVERIFY(name);
        QVERIFY(saveName);
        symbol->setText(QStringLiteral("159518"));
        name->setText(QStringLiteral("测试标的"));
        saveName->click();
        QCOMPARE(commands.size(), 1);
        arguments = commands.takeFirst();
        QCOMPARE(arguments.at(1).toString(), QStringLiteral("redemption_set_symbol_name"));
        QCOMPARE(arguments.at(2).toJsonObject().value(QStringLiteral("symbol")).toString(),
                 QStringLiteral("159518"));
        QCOMPARE(arguments.at(2).toJsonObject().value(QStringLiteral("name")).toString(),
                 QStringLiteral("测试标的"));
    }

    void displaysExplicitUploadScheduleExpectation() {
        hub::ModuleConfig config;
        config.id = QStringLiteral("upload");
        config.displayName = QStringLiteral("Upload 网站");
        config.adapter = QStringLiteral("upload");
        config.settings.insert(QStringLiteral("workers_expected"), true);
        hub::UploadPage page(config);
        auto *label = page.findChild<QLabel *>(QStringLiteral("uploadWorkersExpectedState"));
        QVERIFY(label);

        page.applySnapshot(QJsonObject{{QStringLiteral("payload"), QJsonObject{
            {QStringLiteral("telemetry"), QJsonObject{{QStringLiteral("workers_expected"), false}}}}}});
        QVERIFY(label->text().contains(QStringLiteral("允许排程休眠")));
        page.applySnapshot(QJsonObject{{QStringLiteral("payload"), QJsonObject{
            {QStringLiteral("telemetry"), QJsonObject{{QStringLiteral("workers_expected"), true}}}}}});
        QVERIFY(label->text().contains(QStringLiteral("确认超时或缺失时提示异常")));
    }

    void uploadMatrixRunsExactlyOneNativeJob() {
        hub::ModuleConfig config;
        config.id = QStringLiteral("upload");
        config.displayName = QStringLiteral("Upload 网站");
        config.adapter = QStringLiteral("upload");
        config.controlEnabled = true;
        config.ownership = QStringLiteral("logic");
        config.allowedActions = {QStringLiteral("upload_run_job")};
        hub::UploadPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);
        const QJsonArray workers{
            QJsonObject{{QStringLiteral("id"), QStringLiteral("sina-public")},
                        {QStringLiteral("kind"), QStringLiteral("uploader")},
                        {QStringLiteral("state"), QStringLiteral("active")},
                        {QStringLiteral("stage"), QStringLiteral("recorded")}},
            QJsonObject{{QStringLiteral("id"), QStringLiteral("eastmoney-nav")},
                        {QStringLiteral("kind"), QStringLiteral("daily")},
                        {QStringLiteral("state"), QStringLiteral("scheduled_idle")},
                        {QStringLiteral("stage"), QStringLiteral("before_run_time")}}};
        const QJsonObject payload{
            {QStringLiteral("control_enabled"), true},
            {QStringLiteral("ownership"), QStringLiteral("logic")},
            {QStringLiteral("telemetry"), QJsonObject{{QStringLiteral("workers"), workers}}}};
        page.applySnapshot(QJsonObject{
            {QStringLiteral("timestamp"), QStringLiteral("2026-09-04T01:31:05Z")},
            {QStringLiteral("payload"), payload}});

        auto *table = page.findChild<QTableWidget *>(QStringLiteral("uploadJobsTable"));
        QVERIFY(table);
        QCOMPARE(table->rowCount(), 2);
        auto *runSina = page.findChild<QPushButton *>(
            QStringLiteral("uploadRunJob_sina-public"));
        auto *runNav = page.findChild<QPushButton *>(
            QStringLiteral("uploadRunJob_eastmoney-nav"));
        QVERIFY(runSina);
        QVERIFY(runNav);
        QVERIFY(runSina->isEnabled());
        runSina->click();
        QCOMPARE(commands.size(), 1);
        const auto command = commands.takeFirst();
        QCOMPARE(command.at(1).toString(), QStringLiteral("upload_run_job"));
        const QJsonObject expectedArguments{
            {QStringLiteral("job_id"), QStringLiteral("sina-public")}};
        QCOMPARE(command.at(2).toJsonObject(), expectedArguments);
    }

    void qmtDetailKeepsBackendsIndependentAndRequiresDoubleClick() {
        hub::PcfDetailWindow detail(QStringLiteral("159513"));
        detail.setMutationEnabled(true);
        auto *initialPurchase = detail.findChild<QPushButton *>(
            QStringLiteral("qmtPurchase_QMT1"));
        QVERIFY(initialPurchase);
        QVERIFY(!initialPurchase->isEnabled());
        detail.setLiveOrdersEnabled(true);
        QSignalSpy commands(&detail, &hub::PcfDetailWindow::qmtCommandRequested);
        QVERIFY(commands.isValid());

        const QJsonObject qmt1{
            {QStringLiteral("connection_state"), QStringLiteral("ready")},
            {QStringLiteral("ready"), true},
            {QStringLiteral("available_cash"), 1'000'000},
            {QStringLiteral("positions"), QJsonArray{
                QJsonObject{{QStringLiteral("code"), QStringLiteral("159513.SZ")},
                            {QStringLiteral("name"), QStringLiteral("纳指科技ETF")},
                            {QStringLiteral("volume"), 600'000}},
                QJsonObject{{QStringLiteral("code"), QStringLiteral("159518.SZ")},
                            {QStringLiteral("volume"), 1}}}},
            {QStringLiteral("orders"), QJsonArray{
                QJsonObject{{QStringLiteral("order_id"), QStringLiteral("10001")},
                            {QStringLiteral("code"), QStringLiteral("159513.SZ")},
                            {QStringLiteral("direction"), QStringLiteral("申购")},
                            {QStringLiteral("status"), QStringLiteral("已报")},
                            {QStringLiteral("qty"), 1}}}},
            {QStringLiteral("last_result"), QJsonObject{{QStringLiteral("success"), true}}},
            {QStringLiteral("throttle_remaining_ms"), 0}};
        const QJsonObject qmt2{
            {QStringLiteral("connection_state"), QStringLiteral("reconnecting")},
            {QStringLiteral("ready"), false},
            {QStringLiteral("last_error"), QStringLiteral("mock QMT2 unavailable")},
            {QStringLiteral("positions"), QJsonArray{}},
            {QStringLiteral("orders"), QJsonArray{}}};
        detail.applyQmtTelemetry({{QStringLiteral("QMT1"), qmt1},
                                  {QStringLiteral("QMT2"), qmt2}});

        auto *positions = detail.findChild<QTableWidget *>(QStringLiteral("qmtPositions_QMT1"));
        auto *orders = detail.findChild<QTableWidget *>(QStringLiteral("qmtOrders_QMT1"));
        auto *connectQmt2 = detail.findChild<QPushButton *>(QStringLiteral("qmtConnect_QMT2"));
        auto *purchase = detail.findChild<QPushButton *>(QStringLiteral("qmtPurchase_QMT1"));
        QVERIFY(positions);
        QVERIFY(orders);
        QVERIFY(connectQmt2);
        QVERIFY(purchase);
        QCOMPARE(positions->rowCount(), 1);
        QCOMPARE(orders->rowCount(), 1);

        connectQmt2->click();
        QCOMPARE(commands.size(), 1);
        auto arguments = commands.takeFirst();
        QCOMPARE(arguments.at(0).toString(), QStringLiteral("redemption_qmt_connect"));
        QCOMPARE(arguments.at(1).toJsonObject().value(QStringLiteral("backend")).toString(),
                 QStringLiteral("QMT2"));

        purchase->click();
        QCOMPARE(commands.size(), 0);
        QTest::mouseDClick(purchase, Qt::LeftButton);
        QCOMPARE(commands.size(), 1);
        arguments = commands.takeFirst();
        QCOMPARE(arguments.at(0).toString(), QStringLiteral("redemption_qmt_order"));
        QCOMPARE(arguments.at(1).toJsonObject().value(QStringLiteral("backend")).toString(),
                 QStringLiteral("QMT1"));
        QCOMPARE(arguments.at(1).toJsonObject().value(QStringLiteral("symbol")).toString(),
                 QStringLiteral("159513"));
        QCOMPARE(arguments.at(1).toJsonObject().value(QStringLiteral("side")).toString(),
                 QStringLiteral("PURCHASE"));

        detail.setMutationEnabled(false);
        QVERIFY(!purchase->isEnabled());
        QVERIFY(!connectQmt2->isEnabled());
    }

    void loadsAndExportsPremiumHistoryOffTheUiThread() {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        QFile source(directory.filePath(QStringLiteral("signals-20260904.jsonl")));
        QVERIFY(source.open(QIODevice::WriteOnly));
        source.write("{\"type\":\"signal\",\"symbol\":\"510300.SH\",\"name\":\"沪深300ETF\","
                     "\"model\":\"premium+radar\",\"occurred_at\":\"2026-09-04T01:31:05.123Z\","
                     "\"signal_seq\":7,\"premium_ppm\":12345,\"reason\":\"mock\"}\n");
        source.write("not-json\n");
        source.write("{\"type\":\"status\"}\n");
        source.close();

        hub::PremiumHistoryLoader loader;
        QSignalSpy loaded(&loader, &hub::PremiumHistoryLoader::loadFinished);
        QSignalSpy failures(&loader, &hub::PremiumHistoryLoader::failed);
        loader.load(directory.path(), QDate(2026, 9, 4), QDate(2026, 9, 4),
                    QStringLiteral("510300"), QStringLiteral("contains:radar"));
        QVERIFY(loaded.wait(3000));
        QCOMPARE(failures.size(), 0);
        const auto loadedArguments = loaded.takeFirst();
        const QJsonArray records = loadedArguments.at(0).toJsonArray();
        const QJsonObject statistics = loadedArguments.at(1).toJsonObject();
        QCOMPARE(records.size(), 1);
        QCOMPARE(records.at(0).toObject().value(QStringLiteral("_audit_line")).toInt(), 1);
        QCOMPARE(statistics.value(QStringLiteral("files")).toInt(), 1);
        QCOMPARE(statistics.value(QStringLiteral("rejected_lines")).toInt(), 1);

        QSignalSpy exported(&loader, &hub::PremiumHistoryLoader::exportFinished);
        const QString destination = directory.filePath(QStringLiteral("audit.csv"));
        loader.exportCsv(destination, records);
        QVERIFY(exported.wait(3000));
        QVERIFY(exported.takeFirst().at(0).toBool());
        QFile csv(destination);
        QVERIFY(csv.open(QIODevice::ReadOnly));
        const QByteArray content = csv.readAll();
        QVERIFY(content.startsWith("\xEF\xBB\xBF"));
        QVERIFY(content.contains("510300.SH"));
        QVERIFY(content.contains("premium+radar"));
    }

    void premiumDetailWindowUsesLiveTenLevelEventsAndUnsubscribes()
    {
        hub::ModuleConfig config;
        config.id = QStringLiteral("premium");
        config.displayName = QStringLiteral("溢价率上升监控 A 端");
        config.adapter = QStringLiteral("premium");
        config.allowedActions = {QStringLiteral("refresh"),
                                 QStringLiteral("premium_detail_subscribe"),
                                 QStringLiteral("premium_detail_unsubscribe")};
        hub::PremiumPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);

        const QJsonObject summary{{QStringLiteral("symbol"),
                                   QStringLiteral("510300.SH")},
                                  {QStringLiteral("name"),
                                   QStringLiteral("沪深300ETF")},
                                  {QStringLiteral("last_price_e6"), 3'910'000}};
        page.applyEvent({{QStringLiteral("event_kind"),
                          QStringLiteral("premium.summary")},
                         {QStringLiteral("payload"), summary}});
        auto *table = page.findChild<QTableWidget *>(
            QStringLiteral("premiumSummariesTable"));
        QVERIFY(table);
        QTRY_COMPARE_WITH_TIMEOUT(table->rowCount(), 1, 1'500);
        QVERIFY(QMetaObject::invokeMethod(table, "cellDoubleClicked",
                                          Qt::DirectConnection, Q_ARG(int, 0),
                                          Q_ARG(int, 0)));
        QCOMPARE(commands.size(), 1);
        QCOMPARE(commands.at(0).at(1).toString(),
                 QStringLiteral("premium_detail_subscribe"));
        QCOMPARE(commands.at(0).at(2).toJsonObject()
                     .value(QStringLiteral("symbol")).toString(),
                 QStringLiteral("510300.SH"));

        auto *window = page.findChild<hub::PremiumDetailWindow *>();
        QVERIFY(window);
        QCOMPARE(window->symbol(), QStringLiteral("510300.SH"));
        page.applyEvent({{QStringLiteral("event_kind"),
                          QStringLiteral("premium.detail_ack")},
                         {QStringLiteral("payload"),
                          QJsonObject{{QStringLiteral("type"),
                                       QStringLiteral("detail_ack")},
                                      {QStringLiteral("op"),
                                       QStringLiteral("subscribe")},
                                      {QStringLiteral("symbol"),
                                       QStringLiteral("510300.SH")}}}});

        QJsonArray bids;
        QJsonArray asks;
        QJsonArray bidVolumes;
        QJsonArray askVolumes;
        for (int index = 0; index < 10; ++index) {
            bids.append(3'910'000 - index * 1'000);
            asks.append(3'911'000 + index * 1'000);
            bidVolumes.append(10'000 + index * 100);
            askVolumes.append(20'000 + index * 100);
        }
        const QJsonObject detail{{QStringLiteral("type"), QStringLiteral("detail")},
                                 {QStringLiteral("symbol"), QStringLiteral("510300.SH")},
                                 {QStringLiteral("name"), QStringLiteral("沪深300ETF")},
                                 {QStringLiteral("cached"), true},
                                 {QStringLiteral("last_price_e6"), 3'910'000},
                                 {QStringLiteral("bid1_price_e6"), 3'910'000},
                                 {QStringLiteral("iopv_e6"), 3'900'000},
                                 {QStringLiteral("sell_premium_ppm"), 2'564},
                                 {QStringLiteral("bid_prices_e6"), bids},
                                 {QStringLiteral("ask_prices_e6"), asks},
                                 {QStringLiteral("bid_volumes_e2"), bidVolumes},
                                 {QStringLiteral("ask_volumes_e2"), askVolumes},
                                 {QStringLiteral("level_count"), 10},
                                 {QStringLiteral("trading_phase"), QStringLiteral("T")}};
        page.applyEvent({{QStringLiteral("event_kind"),
                          QStringLiteral("premium.detail")},
                         {QStringLiteral("payload"), detail}});
        auto *book = window->findChild<QTableWidget *>(
            QStringLiteral("premiumDetailBook"));
        QVERIFY(book);
        QCOMPARE(book->rowCount(), 20);
        QCOMPARE(book->item(9, 0)->text(), QStringLiteral("卖1"));
        QCOMPARE(book->item(9, 1)->text(), QStringLiteral("3.911"));
        QCOMPARE(book->item(10, 0)->text(), QStringLiteral("买1"));
        QCOMPARE(book->item(10, 1)->text(), QStringLiteral("3.910"));
        QVERIFY(window->findChild<QPlainTextEdit *>(
                    QStringLiteral("premiumDetailRaw"))
                    ->toPlainText().contains(QStringLiteral("bid_prices_e6")));

        window->close();
        QTRY_COMPARE_WITH_TIMEOUT(commands.size(), 2, 1'000);
        QCOMPARE(commands.at(1).at(1).toString(),
                 QStringLiteral("premium_detail_unsubscribe"));
        QCOMPARE(commands.at(1).at(2).toJsonObject()
                     .value(QStringLiteral("symbol")).toString(),
                 QStringLiteral("510300.SH"));

        // Reopen and simulate MainWindow shutdown. The Agent-side detail lease
        // must be released even though QObject child destruction itself does
        // not deliver a close event safely after page members are gone.
        QVERIFY(QMetaObject::invokeMethod(table, "cellDoubleClicked",
                                          Qt::DirectConnection, Q_ARG(int, 0),
                                          Q_ARG(int, 0)));
        QCOMPARE(commands.size(), 3);
        page.prepareForShutdown();
        QCOMPARE(commands.size(), 4);
        QCOMPARE(commands.at(3).at(1).toString(),
                 QStringLiteral("premium_detail_unsubscribe"));
    }

    void boundsPremiumHistoryLargeRecordsAndReturnedBytes() {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        QFile source(directory.filePath(QStringLiteral("signals-20260904.jsonl")));
        QVERIFY(source.open(QIODevice::WriteOnly));

        // This line is below the parser's line limit, but adding provenance
        // fields makes the returned object exceed the per-record limit.
        QJsonObject oversized{
            {QStringLiteral("type"), QStringLiteral("signal")},
            {QStringLiteral("symbol"), QStringLiteral("OVERSIZED")},
            {QStringLiteral("model"), QStringLiteral("premium")},
            {QStringLiteral("occurred_at"), QStringLiteral("2026-09-04T01:31:05.123Z")},
            {QStringLiteral("signal_seq"), -1},
            {QStringLiteral("payload"), QString()},
        };
        QByteArray encoded = QJsonDocument(oversized).toJson(QJsonDocument::Compact);
        const qsizetype targetBytes = hub::PremiumHistoryLoader::MaximumRecordBytes - 16;
        oversized.insert(QStringLiteral("payload"),
                         QString(targetBytes - encoded.size(), u'x'));
        encoded = QJsonDocument(oversized).toJson(QJsonDocument::Compact);
        QVERIFY(encoded.size() < hub::PremiumHistoryLoader::MaximumLineBytes);
        QVERIFY(source.write(encoded) == encoded.size());
        QVERIFY(source.write("\n") == 1);

        // Eighteen individually valid records would occupy roughly 36 MiB.
        // The retained and returned set must stay below the 32 MiB aggregate
        // budget at every insertion rather than collecting the whole input.
        const QString payload(hub::PremiumHistoryLoader::MaximumRecordBytes - 4096, u'y');
        for (int index = 0; index < 18; ++index) {
            const QJsonObject record{
                {QStringLiteral("type"), QStringLiteral("signal")},
                {QStringLiteral("symbol"), QStringLiteral("510300.SH")},
                {QStringLiteral("model"), QStringLiteral("premium")},
                {QStringLiteral("occurred_at"), QStringLiteral("2026-09-04T01:31:05.123Z")},
                {QStringLiteral("signal_seq"), index},
                {QStringLiteral("payload"), payload},
            };
            const QByteArray line = QJsonDocument(record).toJson(QJsonDocument::Compact);
            QVERIFY(line.size() < hub::PremiumHistoryLoader::MaximumRecordBytes);
            QVERIFY(source.write(line) == line.size());
            QVERIFY(source.write("\n") == 1);
        }
        source.close();

        hub::PremiumHistoryLoader loader;
        QSignalSpy loaded(&loader, &hub::PremiumHistoryLoader::loadFinished);
        QSignalSpy failures(&loader, &hub::PremiumHistoryLoader::failed);
        loader.load(directory.path(), QDate(2026, 9, 4), QDate(2026, 9, 4), {}, {});
        QVERIFY(loaded.wait(20'000));
        QCOMPARE(failures.size(), 0);
        const auto arguments = loaded.takeFirst();
        const QJsonArray records = arguments.at(0).toJsonArray();
        const QJsonObject statistics = arguments.at(1).toJsonObject();

        QVERIFY(records.size() < 18);
        QVERIFY(!records.isEmpty());
        QCOMPARE(records.first().toObject().value(QStringLiteral("signal_seq")).toInt(), 17);
        QCOMPARE(statistics.value(QStringLiteral("oversized_records")).toInt(), 1);
        QVERIFY(statistics.value(QStringLiteral("truncated")).toBool());
        QVERIFY(statistics.value(QStringLiteral("truncated_by_returned_bytes")).toBool());
        QVERIFY(statistics.value(QStringLiteral("dropped_records")).toInt() > 0);
        QVERIFY(statistics.value(QStringLiteral("returned_bytes")).toInteger()
                <= hub::PremiumHistoryLoader::MaximumReturnedBytes);
        QCOMPARE(statistics.value(QStringLiteral("returned_byte_limit")).toInteger(),
                 hub::PremiumHistoryLoader::MaximumReturnedBytes);
        QCOMPARE(statistics.value(QStringLiteral("single_record_byte_limit")).toInteger(),
                 hub::PremiumHistoryLoader::MaximumRecordBytes);
        const QByteArray returnedJson = QJsonDocument(records).toJson(QJsonDocument::Compact);
        QVERIFY(returnedJson.size() <= hub::PremiumHistoryLoader::MaximumReturnedBytes);
        QCOMPARE(statistics.value(QStringLiteral("returned_bytes")).toInteger(),
                 returnedJson.size());
    }

    void boundsPremiumHistoryRecordCount() {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        QFile source(directory.filePath(QStringLiteral("signals-20260904.jsonl")));
        QVERIFY(source.open(QIODevice::WriteOnly));
        for (int index = 0; index <= hub::PremiumHistoryLoader::MaximumRecords; ++index) {
            const QJsonObject record{
                {QStringLiteral("type"), QStringLiteral("signal")},
                {QStringLiteral("symbol"), QStringLiteral("510300.SH")},
                {QStringLiteral("model"), QStringLiteral("premium")},
                {QStringLiteral("occurred_at"), QStringLiteral("2026-09-04T01:31:05.123Z")},
                {QStringLiteral("signal_seq"), index},
            };
            const QByteArray line = QJsonDocument(record).toJson(QJsonDocument::Compact) + '\n';
            QVERIFY(source.write(line) == line.size());
        }
        source.close();

        hub::PremiumHistoryLoader loader;
        QSignalSpy loaded(&loader, &hub::PremiumHistoryLoader::loadFinished);
        loader.load(directory.path(), QDate(2026, 9, 4), QDate(2026, 9, 4), {}, {});
        QVERIFY(loaded.wait(20'000));
        const auto arguments = loaded.takeFirst();
        const QJsonArray records = arguments.at(0).toJsonArray();
        const QJsonObject statistics = arguments.at(1).toJsonObject();
        QCOMPARE(records.size(), hub::PremiumHistoryLoader::MaximumRecords);
        QCOMPARE(records.first().toObject().value(QStringLiteral("signal_seq")).toInt(),
                 hub::PremiumHistoryLoader::MaximumRecords);
        QCOMPARE(records.last().toObject().value(QStringLiteral("signal_seq")).toInt(), 1);
        QVERIFY(statistics.value(QStringLiteral("truncated_by_record_count")).toBool());
        QCOMPARE(statistics.value(QStringLiteral("dropped_records")).toInt(), 1);
    }

    void rejectsOversizedPremiumHistoryExport() {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QJsonObject oversized{
            {QStringLiteral("type"), QStringLiteral("signal")},
            {QStringLiteral("payload"),
             QString(hub::PremiumHistoryLoader::MaximumRecordBytes, u'z')},
        };
        hub::PremiumHistoryLoader loader;
        QSignalSpy exported(&loader, &hub::PremiumHistoryLoader::exportFinished);
        const QString destination = directory.filePath(QStringLiteral("too-large.csv"));
        loader.exportCsv(destination, QJsonArray{oversized});
        QVERIFY(exported.wait(5'000));
        const auto arguments = exported.takeFirst();
        QVERIFY(!arguments.at(0).toBool());
        QVERIFY(arguments.at(1).toString().contains(QStringLiteral("单条记录")));
        QVERIFY(!QFile::exists(destination));
    }

    void rejectsPremiumHistoryExportOverAggregateBudget() {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QString payload(hub::PremiumHistoryLoader::MaximumRecordBytes - 4096, u'z');
        QJsonArray records;
        for (int index = 0; index < 18; ++index) {
            records.append(QJsonObject{
                {QStringLiteral("type"), QStringLiteral("signal")},
                {QStringLiteral("signal_seq"), index},
                {QStringLiteral("payload"), payload},
            });
        }
        hub::PremiumHistoryLoader loader;
        QSignalSpy exported(&loader, &hub::PremiumHistoryLoader::exportFinished);
        const QString destination = directory.filePath(QStringLiteral("aggregate-too-large.csv"));
        loader.exportCsv(destination, records);
        QVERIFY(exported.wait(10'000));
        const auto arguments = exported.takeFirst();
        QVERIFY(!arguments.at(0).toBool());
        QVERIFY(arguments.at(1).toString().contains(QStringLiteral("输入记录")));
        QVERIFY(!QFile::exists(destination));
    }
};

QTEST_MAIN(ModulePagesTests)
#include "tst_module_pages.moc"
