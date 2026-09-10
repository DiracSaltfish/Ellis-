#include "modules/monitor_sync/MonitorSyncEngine.h"
#include "modules/monitor_sync/FileStore.h"
#include <QCoreApplication>
#include <QJsonDocument>
#include <QTimer>
#include <cstdio>
int main(int argc,char **argv){QCoreApplication app(argc,argv);if(argc!=2)return 2;auto c=machome::sync::FileStore::readJson(QString::fromLocal8Bit(argv[1]));machome::sync::MonitorSyncEngine engine;engine.initialize({"monitor_sync",c["data_root"].toString(),c,false});engine.start();auto s=engine.snapshot();puts(QJsonDocument(s).toJson(QJsonDocument::Compact).constData());fflush(stdout);if(!s["running"].toBool())return 1;return app.exec();}
