#include "client/DetailDialog.h"
#include "client/MonitorWindow.h"

#include <QDoubleSpinBox>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QPointer>
#include <QSpinBox>
#include <QTabBar>
#include <QTabWidget>
#include <QTableWidget>
#include <QTcpServer>
#include <QTemporaryDir>
#include <QtTest>

namespace premium {

class ClientUiTests final : public QObject {
    Q_OBJECT

private Q_SLOTS:
    void signalAndGlobalListsAreIndependentAndPersistent()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        ClientSettings settings;
        settings.serverBase = QUrl(QStringLiteral("ws://127.0.0.1:9"));
        settings.profiles = {
            {QStringLiteral("QMT1"), QStringLiteral("127.0.0.1"), 9},
            {QStringLiteral("QMT2"), QStringLiteral("127.0.0.1"), 9}
        };
        settings.soundEnabled = false;
        settings.popupEnabled = false;
        const QString settingsPath = directory.filePath(QStringLiteral("config/client-settings.json"));

        auto summary = [](const QString &symbol, qint64 lastPriceE6) {
            return QJsonObject{{"type", "summary"}, {"s", symbol}, {"name", symbol + QStringLiteral(" 测试")},
                               {"last_price_e6", lastPriceE6}, {"change_ppm", 1'000},
                               {"iopv_e6", lastPriceE6 - 1'000}, {"sell_premium_ppm", 2'000},
                               {"publish_wall_ns", 1'787'805'600'000'000'000LL}};
        };
        auto signal = [](const QString &symbol, qint64 sequence, const QString &occurredAt) {
            return QJsonObject{{"type", "signal"}, {"symbol", symbol}, {"name", symbol + QStringLiteral(" 测试")},
                               {"signal_seq", sequence}, {"occurred_at", occurredAt},
                               {"model", "premium"}, {"premium_ppm", 18'000},
                               {"reason", "自动化测试信号"}, {"backfill", true},
                               {"last_price_e6", 1'200'000}, {"iopv_e6", 1'180'000}};
        };
        auto send = [](MonitorWindow &window, const QJsonObject &message) {
            window.processMessage(QString::fromUtf8(QJsonDocument(message).toJson(QJsonDocument::Compact)));
        };
        auto rowFor = [](QTableWidget *table, const QString &symbol) {
            for (int row = 0; row < table->rowCount(); ++row) {
                if (table->item(row, 0) && table->item(row, 0)->data(Qt::UserRole).toString() == symbol) return row;
            }
            return -1;
        };

        {
            MonitorWindow window(settings, settingsPath, false);
            auto *tabs = window.findChild<QTabWidget *>(QStringLiteral("marketListTabs"));
            auto *global = window.findChild<QTableWidget *>(QStringLiteral("monitorTable"));
            auto *signals = window.findChild<QTableWidget *>(QStringLiteral("signalTable"));
            QVERIFY(tabs);
            QVERIFY(global);
            QVERIFY(signals);
            QCOMPARE(tabs->currentIndex(), 0);
            QCOMPARE(tabs->tabText(1), QStringLiteral("全局列表"));

            send(window, QJsonObject{{"type", "sync_begin"}, {"replay", false}});
            send(window, summary(QStringLiteral("159866.SZ"), 1'684'000));
            send(window, summary(QStringLiteral("159010.SZ"), 909'000));
            send(window, QJsonObject{{"type", "sync_complete"}});
            QCOMPARE(global->rowCount(), 2);
            QCOMPARE(global->item(0, 0)->data(Qt::UserRole).toString(), QStringLiteral("159010.SZ"));
            QCOMPARE(global->item(1, 0)->data(Qt::UserRole).toString(), QStringLiteral("159866.SZ"));

            const QJsonObject first = signal(QStringLiteral("159866.SZ"), 1, QStringLiteral("2026-08-27T10:00:00.000+08:00"));
            const QJsonObject second = signal(QStringLiteral("159010.SZ"), 2, QStringLiteral("2026-08-27T10:01:00.000+08:00"));
            const QJsonObject repeated = signal(QStringLiteral("159866.SZ"), 3, QStringLiteral("2026-08-27T10:02:00.000+08:00"));
            send(window, second);
            send(window, first); // 故意乱序补发，仍必须按 occurred_at 从新到旧。
            QCOMPARE(signals->rowCount(), 2);
            QCOMPARE(signals->item(0, 0)->data(Qt::UserRole).toString(), QStringLiteral("159010.SZ"));
            send(window, repeated);
            QCOMPARE(signals->rowCount(), 2);
            QCOMPARE(signals->item(0, 0)->data(Qt::UserRole).toString(), QStringLiteral("159866.SZ"));
            QCOMPARE(global->item(0, 0)->data(Qt::UserRole).toString(), QStringLiteral("159010.SZ"));
            QCOMPARE(global->item(1, 0)->data(Qt::UserRole).toString(), QStringLiteral("159866.SZ"));

            send(window, summary(QStringLiteral("159010.SZ"), 910'000));
            const int signal010 = rowFor(signals, QStringLiteral("159010.SZ"));
            QVERIFY(signal010 >= 0);
            QTRY_COMPARE_WITH_TIMEOUT(signals->item(signal010, 2)->text(), QStringLiteral("0.910"), 1'000);

            send(window, QJsonObject{{"type", "sync_begin"}, {"replay", false}});
            QCOMPARE(global->rowCount(), 0);
            QCOMPARE(signals->rowCount(), 2);

            const int signal866 = rowFor(signals, QStringLiteral("159866.SZ"));
            QVERIFY(signal866 >= 0);
            auto *remove = qobject_cast<QPushButton *>(signals->cellWidget(signal866, 8));
            QVERIFY(remove);
            QCOMPARE(remove->text(), QStringLiteral("本次移除"));
            QTest::mouseClick(remove, Qt::LeftButton);
            QCOMPARE(signals->rowCount(), 1);
            QCOMPARE(rowFor(signals, QStringLiteral("159866.SZ")), -1);
            send(window, repeated);
            QCOMPARE(signals->rowCount(), 1);
            QVERIFY(QFileInfo::exists(directory.filePath(QStringLiteral("config/client-signal-list.json"))));
        }

        {
            MonitorWindow window(settings, settingsPath, false);
            auto *tabs = window.findChild<QTabWidget *>(QStringLiteral("marketListTabs"));
            auto *signals = window.findChild<QTableWidget *>(QStringLiteral("signalTable"));
            QVERIFY(tabs);
            QVERIFY(signals);
            QCOMPARE(tabs->currentIndex(), 0);
            QCOMPARE(signals->rowCount(), 1);
            QCOMPARE(signals->item(0, 0)->data(Qt::UserRole).toString(), QStringLiteral("159010.SZ"));

            send(window, signal(QStringLiteral("159866.SZ"), 3, QStringLiteral("2026-08-27T10:02:00.000+08:00")));
            QCOMPARE(signals->rowCount(), 1);
            send(window, signal(QStringLiteral("159866.SZ"), 4, QStringLiteral("2026-08-27T10:03:00.000+08:00")));
            QCOMPARE(signals->rowCount(), 2);
            QCOMPARE(signals->item(0, 0)->data(Qt::UserRole).toString(), QStringLiteral("159866.SZ"));

            QVERIFY(QMetaObject::invokeMethod(signals, "cellDoubleClicked", Qt::DirectConnection,
                                              Q_ARG(int, 0), Q_ARG(int, 0)));
            QTRY_COMPARE_WITH_TIMEOUT(window.findChildren<DetailDialog *>().size(), 1, 1'000);
            window.findChildren<DetailDialog *>().constFirst()->close();
            QTRY_COMPARE_WITH_TIMEOUT(window.findChildren<DetailDialog *>().size(), 0, 1'000);
        }
    }

    void closeAndReopenDetailWithoutCrash()
    {
        QTcpServer qmt;
        QVERIFY(qmt.listen(QHostAddress::LocalHost, 0));
        const QList<QmtClient::Profile> profiles{
            {QStringLiteral("QMT1"), QStringLiteral("127.0.0.1"), qmt.serverPort()},
            {QStringLiteral("QMT2"), QStringLiteral("127.0.0.1"), qmt.serverPort()}
        };

        QJsonArray bidPrices, askPrices, bidVolumes, askVolumes;
        for (int level = 0; level < 10; ++level) {
            bidPrices.append(1'234'000 - level * 1'000);
            askPrices.append(1'235'000 + level * 1'000);
            bidVolumes.append(level == 0 ? 192'700 * 100 : (8'000 + level * 100) * 100);
            askVolumes.append((16'000 + level * 100) * 100);
        }
        const QJsonObject detail{{"type", "detail"}, {"s", "159028.SZ"},
                                 {"bid1_price_e6", 1'234'000}, {"iopv_e6", 1'233'000},
                                 {"orig_time", 20260827130500000LL},
                                 {"bid_prices_e6", bidPrices}, {"ask_prices_e6", askPrices},
                                 {"bid_volumes_e2", bidVolumes}, {"ask_volumes_e2", askVolumes}};

        for (int iteration = 0; iteration < 5; ++iteration) {
            QPointer<DetailDialog> dialog = new DetailDialog(QStringLiteral("159028.SZ"),
                                                              QStringLiteral("测试ETF"), profiles,
                                                              false, false);
            dialog->setAConnected(true);
            dialog->applyDetail(detail);
            dialog->show();
            QVERIFY(QTest::qWaitForWindowExposed(dialog));

            const auto askButtons = dialog->findChildren<QPushButton *>(QRegularExpression(QStringLiteral("bookask[1-5]")));
            const auto bidButtons = dialog->findChildren<QPushButton *>(QRegularExpression(QStringLiteral("bookbid[1-5]")));
            QCOMPARE(askButtons.size(), 5);
            QCOMPARE(bidButtons.size(), 5);
            QCOMPARE(dialog->findChildren<DoubleClickButton *>(QStringLiteral("qmtSellButton")).size(), 2);
            QCOMPARE(dialog->findChildren<DoubleClickButton *>(QStringLiteral("qmtLimitSellButton")).size(), 2);

            auto *tabs = dialog->findChild<QTabWidget *>(QStringLiteral("tradeTabs"));
            QVERIFY(tabs);
            QVERIFY(!tabs->tabBar()->isVisible());
            auto *profileSelector = dialog->findChild<QWidget *>(QStringLiteral("qmtProfileSelector"));
            QVERIFY(profileSelector);
            const auto profileButtons = profileSelector->findChildren<QPushButton *>(QStringLiteral("qmtProfileButton"));
            QCOMPARE(profileButtons.size(), 2);
            QVERIFY(profileButtons.at(0)->isChecked());
            QTest::mouseClick(profileButtons.at(1), Qt::LeftButton);
            QCOMPARE(tabs->currentIndex(), 1);
            QVERIFY(profileButtons.at(1)->isChecked());
            auto *purchase = dialog->findChildren<DoubleClickButton *>(QStringLiteral("qmtPurchaseButton")).at(0);
            auto *redeem = dialog->findChildren<DoubleClickButton *>(QStringLiteral("qmtRedeemButton")).at(0);
            QVERIFY(purchase->minimumHeight() > redeem->minimumHeight());
            auto *price = tabs->currentWidget()->findChild<QDoubleSpinBox *>(QStringLiteral("qmtLimitPrice"));
            QVERIFY(price);
            auto *quantity = tabs->currentWidget()->findChild<QSpinBox *>(QStringLiteral("qmtSellQuantity"));
            QVERIFY(quantity);
            QCOMPARE(quantity->value(), 100'000);
            auto *bid3 = dialog->findChild<QPushButton *>(QStringLiteral("bookbid3"));
            QVERIFY(bid3);
            auto *bid1 = dialog->findChild<QPushButton *>(QStringLiteral("bookbid1"));
            QVERIFY(bid1);
            QVERIFY(bid1->text().contains(QStringLiteral("1927")));
            QVERIFY(!bid1->text().contains(u','));
            QTest::mouseClick(bid3, Qt::LeftButton);
            QCOMPARE(price->value(), 1.232);

            dialog->close();
            QTRY_VERIFY_WITH_TIMEOUT(dialog.isNull(), 1'000);
        }
    }
};

} // namespace premium

QTEST_MAIN(premium::ClientUiTests)

#include "test_client_ui.moc"
