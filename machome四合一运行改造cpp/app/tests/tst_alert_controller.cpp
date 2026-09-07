#include "ui/AlertController.h"
#include <QApplication>
#include <QSettings>
#include <QTemporaryDir>
#include <QSignalSpy>
#include <QtTest>
class AlertTests : public QObject {
    Q_OBJECT
private slots:
    void batchesSymbolsAndCooldownDoesNotReplay() {
        QTemporaryDir root;QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat,QSettings::UserScope,root.path());
        qApp->setProperty("test_no_audio",true);
        QSettings settings;settings.setValue("alerts/redemption/alert_cooldown_seconds",1);
        settings.setValue("alerts/redemption/volume",42);settings.setValue("alerts/redemption/sound_repeat_count",4);
        hub::AlertController alerts;QSignalSpy messages(&alerts,&hub::AlertController::presented);
        QSignalSpy sounds(&alerts,&hub::AlertController::playbackRequested);
        alerts.notify("redemption","变化","159217",true,false);
        alerts.notify("redemption","变化","159605",true,false);
        QTRY_COMPARE_WITH_TIMEOUT(messages.size(),1,1000);
        QVERIFY(messages.first().first().toString().contains("159217"));QVERIFY(messages.first().first().toString().contains("159605"));
        QCOMPARE(sounds.size(),1);QCOMPARE(sounds.first().at(1).toInt(),42);QCOMPARE(sounds.first().at(2).toInt(),4);
        alerts.notify("redemption","变化","cooldown",true,false);QTest::qWait(200);QCOMPARE(messages.size(),1);
        QTest::qWait(900);alerts.notify("redemption","变化","new",false,false);
        QTRY_COMPARE_WITH_TIMEOUT(messages.size(),2,1000);QCOMPARE(messages.last().first().toString(),QStringLiteral("new"));
    }
};
QTEST_MAIN(AlertTests)
#include "tst_alert_controller.moc"
