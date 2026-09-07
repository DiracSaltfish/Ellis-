#include "ui/ModulePages.h"
#include "ui/PremiumHistory.h"

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
        QVERIFY(label->text().contains(QStringLiteral("fail-closed")));
    }

    void uploadMatrixControlsExactlyOneConfiguredUnit() {
        hub::ModuleConfig config;
        config.id = QStringLiteral("upload");
        config.displayName = QStringLiteral("Upload 网站");
        config.adapter = QStringLiteral("upload");
        config.controlEnabled = true;
        config.ownership = QStringLiteral("owner");
        config.allowedActions = {QStringLiteral("start_service"),
                                 QStringLiteral("stop_service"),
                                 QStringLiteral("restart_service")};
        hub::UploadPage page(config);
        QSignalSpy commands(&page, &hub::ModulePage::commandRequested);
        const QJsonArray units{
            QJsonObject{{QStringLiteral("id"), QStringLiteral("web")},
                        {QStringLiteral("loaded"), true},
                        {QStringLiteral("running"), true}},
            QJsonObject{{QStringLiteral("id"), QStringLiteral("sina")},
                        {QStringLiteral("loaded"), true},
                        {QStringLiteral("running"), true}}};
        // Website has no uploader-health row; it must still remain visible and
        // independently controllable through launchd state.
        const QJsonArray workers{
            QJsonObject{{QStringLiteral("source"), QStringLiteral("sina")},
                        {QStringLiteral("stage"), QStringLiteral("uploading")}}};
        const QJsonObject payload{
            {QStringLiteral("control_enabled"), true},
            {QStringLiteral("ownership"), QStringLiteral("owner")},
            {QStringLiteral("process"), QJsonObject{{QStringLiteral("units"), units}}},
            {QStringLiteral("telemetry"), QJsonObject{{QStringLiteral("workers"), workers}}}};
        page.applySnapshot(QJsonObject{
            {QStringLiteral("timestamp"), QStringLiteral("2026-09-04T01:31:05Z")},
            {QStringLiteral("payload"), payload}});

        auto *table = page.findChild<QTableWidget *>();
        QVERIFY(table);
        QCOMPARE(table->rowCount(), 2);
        auto *stopWeb = page.findChild<QPushButton *>(
            QStringLiteral("uploadUnit_stop_service_web"));
        auto *restartSina = page.findChild<QPushButton *>(
            QStringLiteral("uploadUnit_restart_service_sina"));
        QVERIFY(stopWeb);
        QVERIFY(restartSina);
        QVERIFY(stopWeb->isEnabled());
        stopWeb->click();
        QCOMPARE(commands.size(), 1);
        const auto command = commands.takeFirst();
        QCOMPARE(command.at(1).toString(), QStringLiteral("stop_service"));
        const QJsonObject expectedArguments{
            {QStringLiteral("target_unit"), QStringLiteral("web")}};
        QCOMPARE(command.at(2).toJsonObject(), expectedArguments);
    }

    void qmtDetailKeepsBackendsIndependentAndRequiresDoubleClick() {
        hub::PcfDetailWindow detail(QStringLiteral("159513"));
        detail.setMutationEnabled(true);
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
        QCOMPARE(table->rowCount(), 1);
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
