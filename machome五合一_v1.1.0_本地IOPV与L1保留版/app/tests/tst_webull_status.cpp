#include "modules/webull/WebullClient.h"

#include <QtTest>

class WebullStatusTests final : public QObject {
    Q_OBJECT

private slots:
    void recognizesOriginalGatewayScheduledIdle() {
        Machome::Webull::GatewayStatus status;
        status.apiReportedRunning = true;
        status.scheduleMode = QStringLiteral("auto");
        status.collectorRunning = false;
        status.browserState = QStringLiteral("stopped");
        status.dataState = QStringLiteral("stale");
        status.scheduleMessage = QStringLiteral(
            "不在计划采集时间窗；下一切换 2026-09-07 09:00 CST");

        QVERIFY(Machome::Webull::isScheduledIdle(status));
    }

    void rejectsManualStopAndUnexpectedCollectorLoss() {
        Machome::Webull::GatewayStatus status;
        status.apiReportedRunning = true;
        status.scheduleMode = QStringLiteral("force_stopped");
        status.collectorRunning = false;
        status.browserState = QStringLiteral("stopped");
        status.dataState = QStringLiteral("stale");
        status.scheduleMessage = QStringLiteral("手动强制停止；自动调度暂时被覆盖");
        QVERIFY(!Machome::Webull::isScheduledIdle(status));

        status.scheduleMode = QStringLiteral("auto");
        status.scheduleMessage = QStringLiteral("调度状态未知");
        QVERIFY(!Machome::Webull::isScheduledIdle(status));

        status.scheduleMessage = QStringLiteral("不在计划采集时间窗；临时登录检查");
        status.collectorRunning = true;
        QVERIFY(!Machome::Webull::isScheduledIdle(status));
    }
};

QTEST_GUILESS_MAIN(WebullStatusTests)
#include "tst_webull_status.moc"
