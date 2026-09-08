#include "modules/monitor_sync/MonitorSyncEngine.h"
#include "modules/monitor_sync/FileStore.h"
#include <QtTest>
#include <QTemporaryDir>
#include <QDir>
#include <QTcpServer>
using namespace machome::sync;
class SyncTests : public QObject {
    Q_OBJECT
private slots:
    void lifecycleAndMaintenance() {
        QTemporaryDir t; const QString original=t.path()+"/original",migrated=t.path()+"/migrated",backup=t.path()+"/backup",state=t.path()+"/state.json";
        MonitorSyncEngine e; QJsonObject settings{{"listen_host","127.0.0.1"},{"listen_port",0},{"auth_enabled",false},{"tls_enabled",false},{"test_mode",true},{"state_file",state}};
        e.initialize({"monitor_sync",original,settings,false});e.start();QVERIFY(e.snapshot()["running"].toBool());
        FileStore::writeJson(original+"/sample.json",{{"original","unchanged"}});
        QSignalSpy commands(&e,&MonitorSyncEngine::commandFinished);
        e.submitCommand("monitor_sync_backup",{{"path",backup}},"backup");QVERIFY(commands.takeLast()[1].toBool());QCOMPARE(FileStore::readJson(backup+"/sample.json")["original"].toString(),"unchanged");
        e.submitCommand("monitor_sync_set_root",{{"path",migrated}},"move");QVERIFY(commands.takeLast()[1].toBool());QCOMPARE(e.snapshot()["data_root"].toString(),migrated);QVERIFY(QFileInfo::exists(original+"/sample.json"));QCOMPARE(FileStore::readJson(state)["data_root"].toString(),migrated);
        QTcpServer occupied;QVERIFY(occupied.listen(QHostAddress::LocalHost,0));
        e.submitCommand("monitor_sync_set_port",{{"port",int(occupied.serverPort())}},"port");QVERIFY(!commands.takeLast()[1].toBool());QVERIFY(e.snapshot()["running"].toBool());
        e.stop();QVERIFY(!e.snapshot()["running"].toBool());
        MonitorSyncEngine second;second.initialize({"monitor_sync",original,settings,false});second.start();QVERIFY(second.snapshot()["running"].toBool());QCOMPARE(second.snapshot()["data_root"].toString(),migrated);second.stop();
    }
    void corruptStateIsModuleFailure() {
        QTemporaryDir t;QString p=t.path()+"/state.json";QFile bad(p);QVERIFY(bad.open(QIODevice::WriteOnly));bad.write("{");bad.close();MonitorSyncEngine e;e.initialize({"monitor_sync",t.path()+"/root",{{"state_file",p}},false});e.start();QVERIFY(!e.snapshot()["running"].toBool());QVERIFY(!e.snapshot()["error"].toString().isEmpty());
    }
    void singleServerOwnsRoot() {
        QTemporaryDir t;QJsonObject s{{"listen_host","127.0.0.1"},{"listen_port",0},{"test_mode",true}};MonitorSyncEngine first,second;first.initialize({"monitor_sync",t.path(),s,false});second.initialize({"monitor_sync",t.path(),s,false});first.start();second.start();QVERIFY(first.snapshot()["running"].toBool());QVERIFY(!second.snapshot()["running"].toBool());first.stop();second.start();QVERIFY(second.snapshot()["running"].toBool());second.stop();
    }
};
QTEST_GUILESS_MAIN(SyncTests)
#include "tst_monitor_sync.moc"
