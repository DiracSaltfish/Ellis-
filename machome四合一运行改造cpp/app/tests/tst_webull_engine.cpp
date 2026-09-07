#include "modules/webull/WebullEngine.h"

#include <QFile>
#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtWebSockets/QWebSocket>
#include <QtTest>

using machome::webull::WebullEngine;
using machome::webull::WebullSchedule;

namespace {
QByteArray varint(quint64 value)
{
    QByteArray result;
    do { quint8 byte = value & 0x7f; value >>= 7; if (value) byte |= 0x80; result.append(char(byte)); } while (value);
    return result;
}
QByteArray bytesField(int number, const QByteArray &value)
{
    return varint((number << 3) | 2) + varint(value.size()) + value;
}
QByteArray level(const QByteArray &price, const QByteArray &volume)
{
    return bytesField(1, price) + bytesField(2, volume);
}
QByteArray mqttFixture(const QString &ticker)
{
    QByteArray container;
    for (int i=0;i<5;++i) container += bytesField(1, level(QByteArray::number(40.11+i*.01,'f',2), QByteArray::number(10+i)));
    for (int i=0;i<5;++i) container += bytesField(2, level(QByteArray::number(40.10-i*.01,'f',2), QByteArray::number(20+i)));
    const QByteArray protobuf = bytesField(7, container);
    const QByteArray topic = QJsonDocument(QJsonObject{{"type",109},{"tickerId",ticker}}).toJson(QJsonDocument::Compact);
    const QByteArray packet = char(topic.size() >> 8) + QByteArray(1,char(topic.size() & 0xff)) + topic + protobuf;
    return QByteArray(1,char(0x30)) + varint(packet.size()) + packet;
}
QJsonObject fixture(const QString &name)
{
    QFile file(QStringLiteral(MACHOME_TEST_SOURCE_DIR "/fixtures/webull/") + name);
    if (!file.open(QIODevice::ReadOnly)) return {};
    return QJsonDocument::fromJson(file.readAll()).object();
}
}

class WebullEngineTests final : public QObject {
    Q_OBJECT
private slots:
    void mqttControlPacketsDoNotCreateDepthOrErrors() {
        QTemporaryDir root; WebullEngine engine;
        engine.initialize({"webull", root.path(), {{"api_enabled",false},{"live_browser_enabled",false}},true});
        const auto before = engine.snapshot().value("status").toObject();
        for (const auto &hex : {"d000", "20020000", "9003000100"}) {
            QString error;
            QVERIFY2(engine.ingestRawEventForTest({{"type","raw_mqtt"},
                {"base64",QString::fromLatin1(QByteArray::fromHex(hex).toBase64())}}, &error), qPrintable(error));
            QVERIFY(error.isEmpty());
        }
        const auto after = engine.snapshot().value("status").toObject();
        QCOMPARE(after.value("valid_responses"), before.value("valid_responses"));
        QCOMPARE(after.value("invalid_responses"), before.value("invalid_responses"));
        QCOMPARE(after.value("last_depth_at"), before.value("last_depth_at"));
    }
    void legacyRoutesPingAndCidrAreCompatible() {
        QTemporaryDir root;WebullEngine engine;
        engine.initialize({"webull",root.path(),{{"api_enabled",true},{"api_port",0},
            {"api_host","127.0.0.1"},{"api_allowed_cidrs",QJsonArray{"127.0.0.0/8"}},
            {"live_browser_enabled",false}},true});engine.start();
        const auto address=engine.snapshot().value("status").toObject().value("api_address").toString();
        QFile token(engine.apiTokenFile());QVERIFY(token.open(QIODevice::ReadOnly));
        const auto auth=token.readAll().trimmed();QNetworkAccessManager net;
        for(const auto *path:{"/v1/health/live","/v1/status","/v1/symbols","/v1/clients"}){
            QNetworkRequest request{QUrl(address+QLatin1String(path))};request.setRawHeader("Authorization","bEaReR "+auth);
            auto *reply=net.get(request);QSignalSpy done(reply,&QNetworkReply::finished);
            QTRY_VERIFY_WITH_TIMEOUT(!done.isEmpty(),1500);
            QCOMPARE(reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt(),200);
            QCOMPARE(reply->rawHeader("Deprecation"),QByteArray("true"));reply->deleteLater();
        }
        QWebSocket ws;QSignalSpy connected(&ws,&QWebSocket::connected);QSignalSpy messages(&ws,&QWebSocket::textMessageReceived);
        QUrl stream(address+"/v1/stream");stream.setScheme("ws");QNetworkRequest req(stream);req.setRawHeader("Authorization","Bearer "+auth);
        ws.open(req);QTRY_VERIFY_WITH_TIMEOUT(!connected.isEmpty(),1500);
        ws.sendTextMessage("ping");
        QTRY_VERIFY_WITH_TIMEOUT(!messages.isEmpty(),1500);
        QCOMPARE(QJsonDocument::fromJson(messages.last().first().toString().toUtf8()).object().value("type").toString(),QStringLiteral("pong"));
        ws.close();engine.stop();
        WebullEngine denied;denied.initialize({"webull",root.path(),{{"api_enabled",true},{"api_port",0},
            {"api_allowed_cidrs",QJsonArray{"192.0.2.0/24"}},{"live_browser_enabled",false}},true});denied.start();
        auto *reply=net.get(QNetworkRequest(QUrl(denied.snapshot().value("status").toObject().value("api_address").toString()+"/v2/health/live")));
        QSignalSpy done(reply,&QNetworkReply::finished);QTRY_VERIFY_WITH_TIMEOUT(!done.isEmpty(),1500);
        QCOMPARE(reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt(),403);reply->deleteLater();
    }

    void productionScheduleBoundaries()
    {
        const QTimeZone zone("Asia/Shanghai");
        auto utc=[](const char *v){return QDateTime::fromString(QLatin1String(v),Qt::ISODate).toUTC();};
        QVERIFY(!WebullSchedule::evaluate(utc("2026-09-04T08:59:59+08:00")).collectorDesired);
        QVERIFY(WebullSchedule::evaluate(utc("2026-09-04T09:00:00+08:00")).collectorDesired);
        QVERIFY(WebullSchedule::evaluate(utc("2026-09-04T15:59:59+08:00")).collectorDesired);
        QVERIFY(!WebullSchedule::evaluate(utc("2026-09-04T16:00:00+08:00")).collectorDesired);
        QVERIFY(!WebullSchedule::evaluate(utc("2026-09-05T10:00:00+08:00")).collectorDesired);
        QVERIFY(WebullSchedule::evaluate(utc("2026-09-05T10:00:00+08:00"),"force_running").collectorDesired);
        QVERIFY(!WebullSchedule::evaluate(utc("2026-09-04T10:00:00+08:00"),"force_stopped").collectorDesired);
        QCOMPARE(WebullSchedule::evaluate(utc("2026-09-04T16:00:00+08:00")).nextTransitionUtc.toTimeZone(zone).date(), QDate(2026,9,7));

        const auto weekendTestAuto = WebullSchedule::evaluate(
            utc("2026-09-04T10:00:00+08:00"), "auto", QTime(9, 0), QTime(16, 0),
            zone, "weekend_test");
        QVERIFY(!weekendTestAuto.collectorDesired);
        QVERIFY(weekendTestAuto.scheduledIdle);
        QVERIFY(!weekendTestAuto.nextTransitionUtc.isValid());
        QVERIFY(WebullSchedule::evaluate(
            utc("2026-09-05T20:00:00+08:00"), "force_running", QTime(9, 0),
            QTime(16, 0), zone, "weekend_test").collectorDesired);
    }

    void loginChecksAtMidnightAndNineWithCatchup()
    {
        QSet<QString> done; QString slot;
        QVERIFY(WebullSchedule::loginCheckDue(QDateTime::fromString("2026-09-04T00:10:00+08:00",Qt::ISODate).toUTC(),done,&slot));
        QCOMPARE(slot,QStringLiteral("2026-09-04T00:00")); done.insert(slot);
        QVERIFY(WebullSchedule::loginCheckDue(QDateTime::fromString("2026-09-04T09:05:00+08:00",Qt::ISODate).toUTC(),done,&slot));
        QCOMPARE(slot,QStringLiteral("2026-09-04T09:00")); done.insert(slot);
        QVERIFY(!WebullSchedule::loginCheckDue(QDateTime::fromString("2026-09-04T09:05:00+08:00",Qt::ISODate).toUTC(),done));
        QVERIFY(!WebullSchedule::loginCheckDue(QDateTime::fromString("2026-09-04T09:11:00+08:00",Qt::ISODate).toUTC(),{}));
    }

    void weekendTestNeverAutoStartsButAllowsExplicitUiControls()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        engine.setNowForTest(
            QDateTime::fromString("2026-09-05T08:50:00+08:00", Qt::ISODate).toUTC());
        engine.initialize({"webull", directory.path(),
            QJsonObject{{"api_enabled", false}, {"live_browser_enabled", false},
                        {"fixture_event", QJsonObject{}}}, true});
        engine.start();
        QCOMPARE(engine.snapshot().value("operating_mode").toString(),
                 QStringLiteral("work"));
        QVERIFY(!engine.snapshot().value("status").toObject()
                     .value("collector_running").toBool());

        engine.submitControl(QStringLiteral("operating-mode-test"),
                             QStringLiteral("set_operating_mode"),
                             QJsonObject{{"mode", "weekend_test"}});
        auto snapshot = engine.snapshot();
        QCOMPARE(snapshot.value("operating_mode").toString(),
                 QStringLiteral("weekend_test"));
        QVERIFY(!snapshot.value("status").toObject()
                     .value("collector_running").toBool());
        QVERIFY(snapshot.value("status").toObject().value("record_only").toBool());

        engine.setNowForTest(
            QDateTime::fromString("2026-09-05T09:00:00+08:00", Qt::ISODate).toUTC());
        engine.evaluateScheduleForTest();
        QVERIFY(!engine.snapshot().value("status").toObject()
                     .value("collector_running").toBool());
        QVERIFY(!QFileInfo::exists(directory.path()
            + QStringLiteral("/runtime/login_checks.json")));

        engine.submitControl(QStringLiteral("manual-collector-test"),
                             QStringLiteral("collector_start"));
        QVERIFY(engine.snapshot().value("status").toObject()
                    .value("collector_running").toBool());
        engine.submitControl(QStringLiteral("manual-stop-test"),
                             QStringLiteral("collector_stop"));
        QVERIFY(!engine.snapshot().value("status").toObject()
                     .value("collector_running").toBool());
        engine.submitControl(QStringLiteral("manual-login-test"),
                             QStringLiteral("open_login"));
        QVERIFY(engine.snapshot().value("status").toObject()
                    .value("collector_running").toBool());
        engine.setNowForTest(
            QDateTime::fromString("2026-09-05T20:00:00+08:00", Qt::ISODate).toUTC());
        engine.submitControl(QStringLiteral("restore-work-test"),
                             QStringLiteral("set_operating_mode"),
                             QJsonObject{{"mode", "work"}});
        QCOMPARE(engine.snapshot().value("status").toObject()
                     .value("schedule_mode").toString(), QStringLiteral("auto"));
        QVERIFY(!engine.snapshot().value("status").toObject()
                     .value("collector_running").toBool());
        engine.stop();
        QCOMPARE(engine.snapshot().value("operating_mode").toString(),
                 QStringLiteral("work"));
        QCOMPARE(engine.snapshot().value("status").toObject()
                     .value("schedule_mode").toString(), QStringLiteral("auto"));
    }

    void stoppedEngineAcceptsOperatingModeAndInitializationResetsIt()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        engine.setNowForTest(
            QDateTime::fromString("2026-09-05T20:00:00+08:00", Qt::ISODate).toUTC());
        const hub::ModuleContext context{"webull", directory.path(),
            QJsonObject{{"api_enabled", false}, {"live_browser_enabled", false}}, true};
        engine.initialize(context);
        QSignalSpy completed(&engine, &WebullEngine::controlCompleted);
        engine.submitControl(QStringLiteral("stopped-mode-switch"),
                             QStringLiteral("set_operating_mode"),
                             QJsonObject{{"mode", "weekend_test"}});
        QCOMPARE(completed.size(), 1);
        QCOMPARE(engine.snapshot().value("operating_mode").toString(),
                 QStringLiteral("weekend_test"));
        QVERIFY(!engine.snapshot().value("running").toBool());

        engine.submitControl(QStringLiteral("stopped-work-switch"),
                             QStringLiteral("set_operating_mode"),
                             QJsonObject{{"mode", "work"}});
        QCOMPARE(completed.size(), 2);
        QCOMPARE(engine.snapshot().value("operating_mode").toString(),
                 QStringLiteral("work"));
        QVERIFY(!engine.snapshot().value("running").toBool());
        engine.submitControl(QStringLiteral("stopped-test-switch-again"),
                             QStringLiteral("set_operating_mode"),
                             QJsonObject{{"mode", "weekend_test"}});

        engine.start();
        QCOMPARE(engine.snapshot().value("operating_mode").toString(),
                 QStringLiteral("weekend_test"));
        QVERIFY(!engine.snapshot().value("status").toObject()
                     .value("collector_running").toBool());
        engine.stop(); engine.initialize(context);
        QCOMPARE(engine.snapshot().value("operating_mode").toString(),
                 QStringLiteral("work"));
        QCOMPARE(engine.snapshot().value("status").toObject()
                     .value("schedule_mode").toString(), QStringLiteral("auto"));
    }

    void httpGoldenAggregationAndOrdering()
    {
        const auto value=fixture("http_overnight_depth.json"); QVERIFY(!value.isEmpty());
        QJsonArray bids,asks; QString error;
        QVERIFY2(WebullEngine::normalizeHttpDepth(value.value("body").toObject(),50,&bids,&asks,&error),qPrintable(error));
        const auto expected=value.value("expected").toObject();
        QCOMPARE(bids.first().toObject().value("price").toString(),expected.value("best_bid").toString());
        QCOMPARE(asks.first().toObject().value("price").toString(),expected.value("best_ask").toString());
        QCOMPARE(bids.first().toObject().value("volume").toString(),expected.value("bid_volume").toString());
        QCOMPARE(asks.first().toObject().value("volume").toString(),expected.value("ask_volume").toString());

        const QJsonObject precise{{"ntvAggBidList",QJsonArray{
            QJsonObject{{"price","1.000"},{"volume","9007199254740993"}},
            QJsonObject{{"price","1"},{"volume","0.000000000000000001"}}}},
            {"ntvAggAskList",QJsonArray{QJsonObject{{"price","2"},{"volume","1"}}}}};
        QVERIFY2(WebullEngine::normalizeHttpDepth(precise,50,&bids,&asks,&error),qPrintable(error));
        QCOMPARE(bids.first().toObject().value("volume").toString(),QStringLiteral("9007199254740993.000000000000000001"));
    }

    void mqttProtobufGoldenAndShallowRejection()
    {
        QJsonArray bids,asks; QString error;
        QVERIFY2(WebullEngine::decodeMqttDepth(mqttFixture("913243629"),"913243629",5,50,&bids,&asks,&error),qPrintable(error));
        QCOMPARE(bids.size(),5); QCOMPARE(asks.size(),5);
        QVERIFY(!WebullEngine::decodeMqttDepth(mqttFixture("913243629"),"other",5,50,&bids,&asks,&error));
        QVERIFY(!WebullEngine::decodeMqttDepth(QByteArray("bad"),"913243629",5,50,&bids,&asks,&error));
    }

    void nativeStorageSessionSequenceAndMalformedIsolation()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        hub::ModuleContext context{"webull",directory.path(),QJsonObject{{"api_enabled",false},{"live_browser_enabled",false},{"default_symbol","XOP"},{"ticker_id","913243629"}},true};
        QSignalSpy books(&engine,&WebullEngine::bookUpdated);
        engine.initialize(context); engine.start();
        QString error;
        const auto golden=fixture("http_overnight_depth.json");
        QVERIFY2(engine.ingestRawEventForTest(QJsonObject{{"type","raw_http"},{"captured_at",golden.value("captured_at")},{"body",golden.value("body")}},&error),qPrintable(error));
        QVERIFY2(engine.ingestRawEventForTest(QJsonObject{{"type","raw_http"},{"captured_at",golden.value("captured_at")},{"body",golden.value("body")}},&error),qPrintable(error));
        QCOMPARE(books.size(),2);
        const auto snapshot=engine.snapshot(); const auto book=snapshot.value("book").toObject();
        QCOMPARE(book.value("sequence").toInteger(),2); QVERIFY(!book.value("changed").toBool());
        QVERIFY(QFileInfo::exists(directory.path()+"/depth/latest.json"));
        QVERIFY(!engine.ingestRawEventForTest(QJsonObject{{"type","raw_http"},{"body",fixture("malformed_depth.json").value("body")}},&error));
        QCOMPARE(engine.snapshot().value("book").toObject().value("sequence").toInteger(),2);
        engine.stop();
    }

    void dailyLoginCheckIsPersistedAndAuthNeedsOnlyEmitLocalEvent()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        const auto testTime=QDateTime::fromString("2026-09-04T00:10:00+08:00",Qt::ISODate).toUTC();
        {
            WebullEngine engine;
            engine.setNowForTest(testTime);
            engine.initialize({"webull",directory.path(),QJsonObject{{"api_enabled",false},{"live_browser_enabled",false}},true});
            QSignalSpy events(&engine,&hub::IModuleEngine::eventReady);
            engine.start();
            QString error;
            QVERIFY2(engine.ingestRawEventForTest(QJsonObject{{"type","auth"},{"state","login_required"},
                {"checked_at","2026-09-03T16:10:00Z"}},&error),qPrintable(error));
            bool authEvent=false;
            for(const auto &arguments:events) if(arguments.value(0).toString()==QStringLiteral("webull.auth_required")) authEvent=true;
            QVERIFY(authEvent);
            engine.stop();
        }
        QFile file(directory.path()+QStringLiteral("/runtime/login_checks.json")); QVERIFY(file.open(QIODevice::ReadOnly));
        QCOMPARE(QJsonDocument::fromJson(file.readAll()).object().value("completed").toObject().size(),1);
        file.close();
        {
            WebullEngine engine;
            engine.setNowForTest(testTime);
            engine.initialize({"webull",directory.path(),QJsonObject{{"api_enabled",false},{"live_browser_enabled",false}},true});
            engine.start(); engine.evaluateScheduleForTest(); engine.stop();
        }
        QVERIFY(file.open(QIODevice::ReadOnly));
        QCOMPARE(QJsonDocument::fromJson(file.readAll()).object().value("completed").toObject().size(),1);
    }

    void dailyLoginCheckCompletesOnlyAfterFreshAuthOrTimeout()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        const auto started=QDateTime::fromString("2026-09-04T00:05:00+08:00",Qt::ISODate).toUTC();
        engine.setNowForTest(started);
        engine.initialize({"webull",directory.path(),QJsonObject{{"api_enabled",false},
            {"live_browser_enabled",false},{"fixture_event",QJsonObject{}}},true});
        engine.start();
        QVERIFY(!QFileInfo::exists(directory.path()+QStringLiteral("/runtime/login_checks.json")));
        engine.setNowForTest(started.addSecs(119)); engine.evaluateScheduleForTest();
        QVERIFY(!QFileInfo::exists(directory.path()+QStringLiteral("/runtime/login_checks.json")));
        engine.setNowForTest(started.addSecs(120)); engine.evaluateScheduleForTest();
        QFile file(directory.path()+QStringLiteral("/runtime/login_checks.json")); QVERIFY(file.open(QIODevice::ReadOnly));
        const auto completed=QJsonDocument::fromJson(file.readAll()).object().value("completed").toObject();
        QCOMPARE(completed.size(),1);
        QCOMPARE(completed.value("2026-09-04T00:00").toObject().value("outcome").toString(),QStringLiteral("timeout"));
        engine.stop();
    }

    void helperCrashDegradesOnlyWebullEngine()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        engine.setNowForTest(QDateTime::fromString("2026-09-04T10:00:00+08:00",Qt::ISODate).toUTC());
        engine.initialize({"webull",directory.path(),QJsonObject{{"api_enabled",false},{"live_browser_enabled",false}},true});
        QSignalSpy errors(&engine,&WebullEngine::errorOccurred);
        engine.start();
        QVERIFY(QMetaObject::invokeMethod(&engine,"helperFinished",Qt::DirectConnection,
            Q_ARG(int,86),Q_ARG(QProcess::ExitStatus,QProcess::CrashExit)));
        QCOMPARE(errors.size(),1);
        QCOMPARE(errors.first().first().value<Machome::Webull::ApiError>().code,QStringLiteral("helper_crashed"));
        QCOMPARE(engine.snapshot().value("status").toObject().value("state").toString(),QStringLiteral("degraded"));
        QVERIFY(engine.snapshot().value("ready").toBool());
        engine.stop();
    }

    void openingLoginWindowKeepsBaselineForceRunningSemantics()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        engine.setNowForTest(QDateTime::fromString("2026-09-05T20:00:00+08:00",Qt::ISODate).toUTC());
        engine.initialize({"webull",directory.path(),QJsonObject{{"api_enabled",false},
            {"live_browser_enabled",false},{"fixture_event",QJsonObject{}}},true});
        engine.start();
        engine.submitControl(QStringLiteral("login-test"),QStringLiteral("open_login"));
        QCOMPARE(engine.snapshot().value("status").toObject().value("schedule_mode").toString(),QStringLiteral("force_running"));
        engine.stop();
    }

    void forceStoppedInitialModeSuppressesScheduledLoginChecks()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        engine.setNowForTest(
            QDateTime::fromString("2026-09-07T09:00:00+08:00", Qt::ISODate).toUTC());
        engine.initialize({"webull", directory.path(),
            QJsonObject{{"api_enabled", false}, {"live_browser_enabled", false},
                        {"initial_schedule_mode", "force_stopped"}}, true});
        engine.start();
        QCOMPARE(engine.snapshot().value("status").toObject()
                     .value("schedule_mode").toString(), QStringLiteral("force_stopped"));
        QVERIFY(!QFileInfo::exists(directory.path()
            + QStringLiteral("/runtime/login_checks.json")));
        engine.stop();
    }

    void v2HttpCompatibilityAndBearerBoundary()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        engine.initialize({"webull",directory.path(),QJsonObject{{"api_enabled",true},{"api_port",0},
            {"live_browser_enabled",false},{"default_symbol","XOP"},{"ticker_id","913243629"}},true});
        engine.start();
        const QUrl base(engine.snapshot().value("status").toObject().value("api_address").toString());
        QVERIFY(base.isValid()); QVERIFY(base.port()>0);
        QNetworkAccessManager network;
        auto get=[&](QString path,QByteArray token={}) {
            QNetworkRequest request(base.resolved(QUrl(path)));
            if(!token.isEmpty()) request.setRawHeader("Authorization","Bearer "+token);
            QNetworkReply *reply=network.get(request); QSignalSpy finished(reply,&QNetworkReply::finished);
            if (!finished.wait(2000)) { reply->abort(); reply->deleteLater(); return qMakePair(-1,QByteArray{}); }
            const int status=reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            const QByteArray body=reply->readAll(); reply->deleteLater();
            return qMakePair(status,body);
        };
        QCOMPARE(get("/v2/health/live").first,200);
        QCOMPARE(get("/v2/status").first,401);
        QFile token(engine.apiTokenFile()); QVERIFY(token.open(QIODevice::ReadOnly));
        const auto authorized=get("/v2/status",token.readAll().trimmed());
        QCOMPARE(authorized.first,200);
        QCOMPARE(QJsonDocument::fromJson(authorized.second).object().value("engine").toString(),QStringLiteral("native"));
        engine.stop();
    }

    void v2WebSocketHelloCompatibility()
    {
        QTemporaryDir directory; QVERIFY(directory.isValid());
        WebullEngine engine;
        engine.initialize({"webull",directory.path(),QJsonObject{{"api_enabled",true},{"api_port",0},
            {"live_browser_enabled",false},{"default_symbol","XOP"},{"ticker_id","913243629"}},true});
        engine.start();
        QUrl url(engine.snapshot().value("status").toObject().value("api_address").toString());
        url.setScheme(QStringLiteral("ws")); url.setPath(QStringLiteral("/v2/stream")); url.setQuery(QStringLiteral("symbol=XOP"));
        QFile token(engine.apiTokenFile()); QVERIFY(token.open(QIODevice::ReadOnly));
        QNetworkRequest request(url); request.setRawHeader("Authorization","Bearer "+token.readAll().trimmed());
        QWebSocket socket;
        QSignalSpy connected(&socket,&QWebSocket::connected);
        QSignalSpy messages(&socket,&QWebSocket::textMessageReceived);
        socket.open(request);
        QVERIFY2(connected.wait(2000),qPrintable(socket.errorString()));
        QTRY_VERIFY_WITH_TIMEOUT(messages.size()>0,2000);
        const auto hello=QJsonDocument::fromJson(messages.first().first().toString().toUtf8()).object();
        QCOMPARE(hello.value("type").toString(),QStringLiteral("hello"));
        QCOMPARE(hello.value("schema_version").toInt(),2);
        QCOMPARE(hello.value("service").toString(),QStringLiteral("webull-lv2-gateway"));
        QCOMPARE(hello.value("snapshot_semantics").toString(),QStringLiteral("full_replace"));
        socket.close(); engine.stop();
    }
};

QTEST_GUILESS_MAIN(WebullEngineTests)
#include "tst_webull_engine.moc"
