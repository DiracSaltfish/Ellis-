#include "console/ConsoleWindow.h"

#include <QCheckBox>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QPushButton>
#include <QTemporaryDir>
#include <QTableWidget>
#include <QtTest>

namespace premium {

class ConsoleUiTests final : public QObject {
    Q_OBJECT

private:
    static void writeFile(const QString &path, const QByteArray &contents)
    {
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly));
        QCOMPARE(file.write(contents), contents.size());
    }

    static QString wire(const QJsonObject &object)
    {
        return QString::fromUtf8(QJsonDocument(object).toJson(QJsonDocument::Compact));
    }

private Q_SLOTS:
    void realtimeSignalsAndDiskAuditRemainDistinct()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        QVERIFY(QDir().mkpath(directory.filePath(QStringLiteral("config"))));
        QVERIFY(QDir().mkpath(directory.filePath(QStringLiteral("data"))));
        writeFile(directory.filePath(QStringLiteral("config/app.json")),
                  QByteArrayLiteral("{\"mode\":\"simulation\",\"data_dir\":\"data\"}"));
        writeFile(directory.filePath(QStringLiteral("config/watchlist.json")),
                  QByteArrayLiteral("{\"version\":1,\"symbols\":[\"159866.SZ\"]}"));
        writeFile(directory.filePath(QStringLiteral("config/security_names.tsv")),
                  QByteArrayLiteral("159866.SZ\t日经ETF工银\n"));

        const QString occurred = QDateTime::currentDateTime().addSecs(-60).toString(Qt::ISODateWithMs);
        QJsonObject historical{{"type", "signal"}, {"signal_seq", 7}, {"symbol", "159866.SZ"},
                               {"occurred_at", occurred}, {"premium_ppm", 16100},
                               {"rise_30s_ppm", 2200}, {"rise_300s_ppm", 10100},
                               {"bid_rise_150s_ppm", 4100}, {"bid_rise_300s_ppm", 8200},
                               {"model", "premium+pull"}, {"reason", "30s+盘口150s"},
                               {"repeat", false}, {"replay", false},
                               {"last_price_e6", 1684000}, {"bid1_price_e6", 1683000},
                               {"iopv_e6", 1633700}, {"orig_time", 20260827133000000LL}};
        writeFile(directory.filePath(QStringLiteral("data/signals-%1.jsonl")
                                         .arg(QDate::currentDate().toString(QStringLiteral("yyyyMMdd")))),
                  QJsonDocument(historical).toJson(QJsonDocument::Compact) + '\n');

        ConsoleWindow window(directory.path(), false);
        QVERIFY(window.windowTitle().contains(QStringLiteral("旁路监控")));
        const QSet<QString> disabledProcessActions{
            QStringLiteral("启动行情服务"), QStringLiteral("停止"), QStringLiteral("重启"),
            QStringLiteral("盘外人工启动真实行情"), QStringLiteral("历史回放…")};
        QSet<QString> observedDisabledActions;
        for (QPushButton *button : window.findChildren<QPushButton *>()) {
            if (disabledProcessActions.contains(button->text()) && !button->isEnabled()) {
                observedDisabledActions.insert(button->text());
                QVERIFY(button->toolTip().contains(QStringLiteral("不允许")));
            }
        }
        QCOMPARE(observedDisabledActions, disabledProcessActions);
        auto *sound = window.findChild<QCheckBox *>(QStringLiteral("consoleSoundEnabled"));
        auto *popup = window.findChild<QCheckBox *>(QStringLiteral("consolePopupEnabled"));
        QVERIFY(sound);
        QVERIFY(popup);
        sound->setChecked(false);
        popup->setChecked(false);

        QJsonObject summary{{"type", "summary"}, {"s", "159866.SZ"}, {"name", "日经ETF工银"},
                            {"last_price_e6", 1684000}, {"bid1_price_e6", 1683000},
                            {"iopv_e6", 1633700}, {"sell_premium_ppm", 30180}};
        QVERIFY(QMetaObject::invokeMethod(&window, "handleMetricsMessage", Qt::DirectConnection,
                                          Q_ARG(QString, wire(summary))));

        QJsonObject backfill = historical;
        backfill.insert(QStringLiteral("backfill"), true);
        QVERIFY(QMetaObject::invokeMethod(&window, "handleMetricsMessage", Qt::DirectConnection,
                                          Q_ARG(QString, wire(backfill))));
        QJsonObject realtime = historical;
        realtime.insert(QStringLiteral("signal_seq"), 8);
        realtime.insert(QStringLiteral("occurred_at"), QDateTime::currentDateTime().toString(Qt::ISODateWithMs));
        realtime.insert(QStringLiteral("backfill"), false);
        QVERIFY(QMetaObject::invokeMethod(&window, "handleMetricsMessage", Qt::DirectConnection,
                                          Q_ARG(QString, wire(realtime))));
        QVERIFY(QMetaObject::invokeMethod(&window, "handleMetricsMessage", Qt::DirectConnection,
                                          Q_ARG(QString, wire(realtime))));

        auto *liveTable = window.findChild<QTableWidget *>(QStringLiteral("signalTable"));
        auto *historyTable = window.findChild<QTableWidget *>(QStringLiteral("signalHistoryTable"));
        QVERIFY(liveTable);
        QVERIFY(historyTable);
        QCOMPARE(liveTable->rowCount(), 2);
        QCOMPARE(liveTable->item(0, 10)->text(), QStringLiteral("实时"));
        QCOMPARE(liveTable->item(1, 10)->text(), QStringLiteral("30分钟补发"));
        QCOMPARE(liveTable->item(0, 4)->text(), QStringLiteral("1.610%"));
        QCOMPARE(liveTable->item(0, 3)->text(), QStringLiteral("溢价率 + 盘口拉涨"));

        QTRY_COMPARE_WITH_TIMEOUT(historyTable->rowCount(), 1, 3'000);
        QVERIFY(historyTable->item(0, 10)->text().contains(QStringLiteral("signals-")));
        const QJsonObject audited = QJsonDocument::fromJson(
            historyTable->item(0, 0)->data(Qt::UserRole).toByteArray()).object();
        QCOMPARE(audited.value(QStringLiteral("_audit_line")).toInteger(), 1);
        QCOMPARE(audited.value(QStringLiteral("bid1_price_e6")).toInteger(), 1'683'000);
    }
};

} // namespace premium

QTEST_MAIN(premium::ConsoleUiTests)

#include "test_console_ui.moc"
