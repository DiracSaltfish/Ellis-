#include "modules/upload/UploadEngine.h"
#include "modules/premium/engine/PremiumAEngine.h"
#include "modules/webull/WebullEngine.h"
#include "modules/redemption/RedemptionEngine.h"
#include <QCoreApplication>
#include <QTemporaryDir>
#include <QJsonDocument>
#include <QJsonArray>
#include <QFile>
#include <QTest>
#include <QTcpServer>
#include <QTcpSocket>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QtWebSockets/QWebSocket>
#include <cstdio>

static QJsonArray observations;
static void result(const char *id, bool reproduced, const QJsonObject &detail) {
    observations.append(QJsonObject{{"id",id},{"gap_reproduced",reproduced},{"detail",detail}});
}
static QJsonObject readJson(const QString &path) {
    QFile f(path); if (!f.open(QIODevice::ReadOnly)) return {};
    return QJsonDocument::fromJson(f.readAll()).object();
}
int main(int argc,char **argv) {
    QCoreApplication app(argc,argv);
    // Every data root is disposable. All listeners use ephemeral loopback ports.
    // No broker, browser, Wind, TGW, production endpoint, or old service is started.
    {
        QTemporaryDir dir("/private/tmp/machome-acceptance-XXXXXX");
        machome::upload::UploadEngine engine;
        engine.initialize({"upload",dir.path()+"/MachomeHub/data/upload",
            {{"test_mode",true},{"sink_mode","live"},{"ibkr_enabled",false}},false});
        const auto s=engine.snapshot();
        result("U01-live-sink-blocked",s.value("state")=="blocked",
            {{"state",s.value("state")},{"error",s.value("last_error")}});
    }
    {
        QTemporaryDir dir;
        QJsonObject snapshot; bool changed=false;
        machome::premium::engine::PremiumAEngine engine;
        QObject::connect(&engine,&hub::IModuleEngine::snapshotReady,[&](const QJsonObject &s){snapshot=s;});
        QObject::connect(&engine,&hub::IModuleEngine::commandFinished,
            [&](const QString &id,bool ok,const QString&,const QJsonObject&){if(id=="change")changed=ok;});
        engine.initialize({"premium",dir.path(),{{"summary_port",0},{"l1_port",0},
            {"replay",true},{"tgw_helper_enabled",false},{"watchlist",QJsonArray{"159217.SZ"}}},true});
        engine.start();
        engine.submitCommand("premium_set_watchlist",{{"symbols",QJsonArray{"510300.SH"}}},"change");
        const auto before=snapshot.value("watchlist");
        engine.stop();engine.start();
        const auto after=snapshot.value("watchlist");
        result("P01-watchlist-reverted-after-restart",changed && before!=after,
            {{"command_succeeded",changed},{"before_restart",before},{"after_restart",after}});
        engine.stop();
        QObject::disconnect(&engine,nullptr,nullptr,nullptr);
    }
    {
        QTemporaryDir dir;
        machome::webull::WebullEngine engine;
        engine.initialize({"webull",dir.path(),{{"api_enabled",true},{"api_port",0},
            {"live_browser_enabled",false},{"initial_schedule_mode","force_stopped"}},true});
        engine.start();
        const int port=QUrl(engine.snapshot().value("status").toObject().value("api_address").toString()).port();
        QFile token(dir.path()+"/runtime/api.token");if(!token.open(QIODevice::ReadOnly))return 2;
        const auto auth="Bearer "+token.readAll().trimmed();
        QNetworkAccessManager network;
        auto get=[&](const QString &path){
            QNetworkRequest req(QUrl(QString("http://127.0.0.1:%1%2").arg(port).arg(path)));
            req.setRawHeader("Authorization",auth);
            auto *reply=network.get(req);
            for(int i=0;i<200 && !reply->isFinished();++i)QTest::qWait(5);
            int status=reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            reply->abort();reply->deleteLater();return status;
        };
        const int v2=get("/v2/status"),v1=get("/v1/status");
        result("W01-v1-route-missing",v2==200 && v1==404,{{"v2_status",v2},{"v1_status",v1}});
        QWebSocket ws;bool hello=false,pong=false;
        QObject::connect(&ws,&QWebSocket::textMessageReceived,[&](const QString &text){
            auto obj=QJsonDocument::fromJson(text.toUtf8()).object();
            if(obj.value("type")=="hello")hello=true;
            if(obj.value("type")=="pong")pong=true;
        });
        QNetworkRequest req(QUrl(QString("ws://127.0.0.1:%1/v2/stream").arg(port)));
        req.setRawHeader("Authorization",auth);ws.open(req);
        for(int i=0;i<200&&!hello;++i)QTest::qWait(5);
        ws.sendTextMessage("ping");QTest::qWait(300);
        result("W02-application-ping-ignored",hello&&!pong,{{"hello_received",hello},{"pong_received",pong},{"wait_ms",300}});
        ws.close();QTest::qWait(20);engine.stop();
    }
    {
        QTemporaryDir dir;
        QTcpServer mock;mock.listen(QHostAddress::LocalHost,0);
        QList<QTcpSocket*> peers;int requests=0;
        QObject::connect(&mock,&QTcpServer::newConnection,[&]{
            while(auto *p=mock.nextPendingConnection()){
                peers.append(p);
                QObject::connect(p,&QTcpSocket::readyRead,[&,p]{
                    const auto bytes=p->readAll();if(bytes.startsWith("GET "))++requests;
                    // Hold replies, so any second request demonstrates overlapping in-flight work.
                });
            }
        });
        machome::redemption::RedemptionEngine engine;
        QJsonObject settings{{"test_mode",true},{"wind_helper_mode","disabled"},
            {"pcf_network_enabled",true},{"compatibility_api_enabled",false},
            {"live_qmt_orders_enabled",false},{"qmt_backends",QJsonArray{}},
            {"pcf_url_template",QString("http://127.0.0.1:%1/{symbol}/{date}").arg(mock.serverPort())},
            {"notifications_enabled",true}};
        engine.initialize({"redemption",dir.path(),settings,false});
        engine.setNowForTest(QDateTime::fromString("2026-09-04T09:31:01+08:00",Qt::ISODate).toUTC());
        engine.submitCommand("set_operating_mode",{{"mode","weekend_test"}},"mode");
        engine.start();
        engine.submitCommand("redemption_pcf_refresh",{},"first");
        engine.submitCommand("redemption_pcf_refresh",{},"second");
        QTest::qWait(200);
        result("R01-pcf-global-serialization-missing",requests>=2,
            {{"requests_before_any_response",requests},{"observation_ms",200}});
        // Replay only sanitized fixtures; record backend decision without delivering a notification.
        const auto fixture=readJson(QString(HUB_SOURCE)+"/fixtures/redemption/golden_intraday_creation.json");
        QJsonObject decision;QString error;
        QObject::connect(&engine,&hub::IModuleEngine::eventReady,[&](const QString &kind,const QJsonObject &event){
            if(kind=="redemption.notification_decision")decision=event;
        });
        engine.installPcfForTest("159518",fixture.value("pcf").toObject(),&error);
        engine.ingestCaptureForTest(fixture.value("baseline").toObject(),&error);
        engine.ingestCaptureForTest(fixture.value("change").toObject(),&error);
        result("R02-backend-notification-record-only",decision.value("enabled").toBool()
            &&!decision.value("sent").toBool()&&decision.value("record_only").toBool(),decision);
        engine.stop();
        QObject::disconnect(&engine,nullptr,nullptr,nullptr);
        for(auto *p:peers)p->abort();
    }
    std::puts(QJsonDocument(observations).toJson(QJsonDocument::Indented).constData());
    for(const auto &v:observations)if(!v.toObject().value("gap_reproduced").toBool())return 1;
    return 0; // Gaps reproduced, NOT production acceptance passed.
}
