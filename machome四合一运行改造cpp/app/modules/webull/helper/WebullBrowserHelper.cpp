#include <QCoreApplication>
#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QDateTime>
#include <QDeadlineTimer>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QHash>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QProcess>
#include <QSet>
#include <QJsonArray>
#include <QTimer>
#include <QUrlQuery>
#include <QtWebSockets/QWebSocket>

#include <iostream>
#include <string>
#include <thread>

namespace {

constexpr qint64 kMaximumEventBytes = 2 * 1024 * 1024;
const QString kStartUrl = QStringLiteral("https://app.webull.com/watch");
const QString kDepthHost = QStringLiteral("quotes-gw.webullfintech.com");
const QString kDepthPath = QStringLiteral("/api/quote/tickerRealTime/queryOvernightDepth");

QString utcNow() { return QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs); }

void emitEvent(const QJsonObject &event)
{
    QByteArray payload = QJsonDocument(event).toJson(QJsonDocument::Compact);
    if (payload.size() > kMaximumEventBytes)
        payload = QByteArrayLiteral("{\"type\":\"error\",\"code\":\"event_too_large\"}");
    payload.append('\n');
    QFile output;
    if (!output.open(stdout, QIODevice::WriteOnly)) return;
    output.write(payload); output.flush();
}

class Helper final : public QObject {
public:
    Helper(QString profile, QString tickerId, QString symbol, QObject *parent=nullptr)
        : QObject(parent), profile_(std::move(profile)), tickerId_(std::move(tickerId)), symbol_(std::move(symbol))
    {
        std::thread([this] {
            std::string line;
            while (std::getline(std::cin, line)) {
                const QByteArray value(line.data(), static_cast<qsizetype>(line.size()));
                QMetaObject::invokeMethod(this, [this, value] { handleCommand(value); }, Qt::QueuedConnection);
            }
            QMetaObject::invokeMethod(QCoreApplication::instance(), &QCoreApplication::quit,
                                      Qt::QueuedConnection);
        }).detach();
        portPoll_.setInterval(50); portPoll_.setSingleShot(false);
        authPoll_.setInterval(10'000);
        connect(&authPoll_, &QTimer::timeout, this, [this] { checkAuth(); });
        connect(&portPoll_, &QTimer::timeout, this, [this] { pollPort(); });
        connect(&chrome_, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
            emitEvent({{"type","error"},{"code","chrome_process_error"},{"message",chrome_.errorString()}});
        });
        connect(&chrome_, qOverload<int,QProcess::ExitStatus>(&QProcess::finished), this,
                [this](int code,QProcess::ExitStatus status) {
            if (!stopping_) emitEvent({{"type","error"},{"code","chrome_exited"},
                                       {"message",QStringLiteral("code=%1 status=%2").arg(code).arg(int(status))}});
            collectorRunning_=false;
            emitEvent({{"type","browser"},{"state",stopping_?"stopped":"error"},{"collector_running",false}});
            if (!stopping_) QTimer::singleShot(0,QCoreApplication::instance(),[]{QCoreApplication::exit(71);});
        });
        connect(&websocket_, &QWebSocket::connected, this, [this] {
            sendCdp(QStringLiteral("Network.enable")); sendCdp(QStringLiteral("Page.enable"));
            // Attach the network observer before loading the business page.
            // Chrome's /json/new query is a raw URL, not a url= parameter.
            sendCdp(QStringLiteral("Page.navigate"), {{QStringLiteral("url"), kStartUrl}});
            collectorRunning_=true;
            authPoll_.start();
            if (visible_) sendCdp(QStringLiteral("Page.bringToFront"));
            emitEvent({{"type","browser"},{"state","running"},{"collector_running",true},{"visible",visible_}});
            if(authCheckPending_) { authCheckPending_=false; checkAuth(); }
        });
        connect(&websocket_, &QWebSocket::textMessageReceived, this, [this](const QString &text) { onCdp(text); });
        connect(&websocket_, &QWebSocket::disconnected, this, [this] {
            if (!stopping_ && collectorRunning_)
                emitEvent({{"type","error"},{"code","cdp_disconnected"},{"message","Chrome DevTools disconnected"}});
            collectorRunning_=false;
            if (!stopping_) QTimer::singleShot(0,QCoreApplication::instance(),[]{QCoreApplication::exit(72);});
        });
        emitEvent({{"type","hello"},{"protocol","machome.webull.raw.v1"},{"pid",QCoreApplication::applicationPid()}});
    }

private:
    void handleCommand(const QByteArray &line)
    {
            QJsonParseError parse; const auto document=QJsonDocument::fromJson(line,&parse);
            if(parse.error!=QJsonParseError::NoError||!document.isObject()) { ack({},false,QStringLiteral("invalid command JSON")); return; }
            const auto value=document.object(); const QString command=value.value(QStringLiteral("command")).toString();
            const auto arguments=value.value(QStringLiteral("arguments")).toObject();
            if(command==QStringLiteral("shutdown")) { stopChrome(); ack(command,true,{}); QCoreApplication::quit(); return; }
            if(command==QStringLiteral("crash_for_test")) {
                ack(command,true,{});
                QTimer::singleShot(0,QCoreApplication::instance(),[]{QCoreApplication::exit(86);});
                return;
            }
            if(command==QStringLiteral("start_collector")) startChrome(false);
            else if(command==QStringLiteral("stop_collector")) stopChrome();
            else if(command==QStringLiteral("restart_browser")) { stopChrome(); startChrome(false); }
            else if(command==QStringLiteral("open_login")) { stopChrome(); startChrome(true); }
            else if(command==QStringLiteral("check_auth")) checkAuth();
            else if(command==QStringLiteral("fixture_event") && arguments.value(QStringLiteral("event")).isObject())
                emitEvent(arguments.value(QStringLiteral("event")).toObject());
            else { ack(command,false,QStringLiteral("unsupported command")); return; }
            ack(command,true,{});
    }

    void ack(const QString &command, bool ok, const QString &message)
    {
        QJsonObject value{{"type","ack"},{"command",command},{"ok",ok}};
        if(!message.isEmpty())value.insert(QStringLiteral("message"),message);
        emitEvent(value);
    }

    QString chromePath() const
    {
        for(const QString &path:{QStringLiteral("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                                 QStringLiteral("/Applications/Chromium.app/Contents/MacOS/Chromium")})
            if(QFileInfo::exists(path))return path;
        return {};
    }

    void startChrome(bool visible)
    {
        if(chrome_.state()!=QProcess::NotRunning)return;
        const QString program=chromePath();
        if(program.isEmpty()){
            emitEvent({{"type","error"},{"code","chrome_missing"},{"message","Chrome/Chromium not installed"}});
            emitEvent({{"type","browser"},{"state","error"},{"collector_running",false}});
            QTimer::singleShot(0,QCoreApplication::instance(),[]{QCoreApplication::exit(70);}); return;
        }
        QDir().mkpath(profile_); QFile::remove(profile_+QStringLiteral("/DevToolsActivePort"));
        QStringList arguments{QStringLiteral("--user-data-dir=")+profile_,QStringLiteral("--remote-debugging-port=0"),
            QStringLiteral("--no-first-run"),QStringLiteral("--no-default-browser-check"),QStringLiteral("--disable-background-networking")};
        if(!visible)arguments.append(QStringLiteral("--headless=new"));
        stopping_=false; visible_=visible; portDeadline_=QDeadlineTimer(10'000);
        chrome_.setProgram(program); chrome_.setArguments(arguments);
        chrome_.setStandardOutputFile(QProcess::nullDevice());
        chrome_.setStandardErrorFile(QProcess::nullDevice());
        chrome_.start();
        emitEvent({{"type","browser"},{"state","starting"},{"collector_running",false}}); portPoll_.start();
    }

    void pollPort()
    {
        QFile file(profile_+QStringLiteral("/DevToolsActivePort"));
        if(file.open(QIODevice::ReadOnly)) {
            bool ok=false; const int port=file.readLine().trimmed().toInt(&ok);
            if(ok&&port>0&&port<=65535){portPoll_.stop();createTarget(port);return;}
        }
        if(portDeadline_.hasExpired()){
            portPoll_.stop(); emitEvent({{"type","error"},{"code","devtools_timeout"},{"message","DevToolsActivePort timeout"}});
            stopChrome(); QTimer::singleShot(0,QCoreApplication::instance(),[]{QCoreApplication::exit(73);});
        }
    }

    void createTarget(int port)
    {
        QUrl url(QStringLiteral("http://127.0.0.1:%1/json/new").arg(port));
        url.setQuery(QStringLiteral("about:blank"));
        auto *reply=network_.sendCustomRequest(QNetworkRequest(url),QByteArrayLiteral("PUT"));
        connect(reply,&QNetworkReply::finished,this,[this,reply]{
            const QByteArray body=reply->readAll(); const auto error=reply->errorString(); const bool ok=reply->error()==QNetworkReply::NoError; reply->deleteLater();
            const QUrl ws=QUrl(QJsonDocument::fromJson(body).object().value(QStringLiteral("webSocketDebuggerUrl")).toString());
            const bool loopback = ws.host()==QStringLiteral("127.0.0.1") || ws.host()==QStringLiteral("localhost")
                || ws.host()==QStringLiteral("::1");
            if(!ok||!ws.isValid()||!loopback){
                emitEvent({{"type","error"},{"code","target_create_failed"},{"message",error}}); stopChrome();
                QTimer::singleShot(0,QCoreApplication::instance(),[]{QCoreApplication::exit(74);}); return;
            }
            websocket_.open(ws);
        });
    }

    int sendCdp(const QString &method,const QJsonObject &params={},const QString &purpose={})
    {
        const int id=++nextId_; if(!purpose.isEmpty())pending_.insert(id,purpose);
        websocket_.sendTextMessage(QString::fromUtf8(QJsonDocument(QJsonObject{{"id",id},{"method",method},{"params",params}}).toJson(QJsonDocument::Compact)));
        return id;
    }

    void onCdp(const QString &text)
    {
        if(text.toUtf8().size()>kMaximumEventBytes)return;
        const auto value=QJsonDocument::fromJson(text.toUtf8()).object();
        const int id=value.value(QStringLiteral("id")).toInt();
        if(id&&pending_.contains(id)) {
            const QString purpose=pending_.take(id); const auto result=value.value(QStringLiteral("result")).toObject();
            if(purpose==QStringLiteral("body")) {
                QByteArray body=result.value(QStringLiteral("body")).toString().toUtf8();
                if(result.value(QStringLiteral("base64Encoded")).toBool())body=QByteArray::fromBase64(body);
                const auto document=QJsonDocument::fromJson(body); if(document.isObject())emitEvent({{"type","raw_http"},{"captured_at",utcNow()},{"body",document.object()}});
            } else if(purpose==QStringLiteral("auth")) {
                const auto page=QJsonDocument::fromJson(result.value(QStringLiteral("result")).toObject()
                    .value(QStringLiteral("value")).toString().toUtf8()).object();
                const QString textValue=page.value(QStringLiteral("text")).toString().toLower();
                const QUrl pageUrl(page.value(QStringLiteral("url")).toString());
                const bool loaded=pageUrl.scheme()==QStringLiteral("https")
                    && pageUrl.host()==QStringLiteral("app.webull.com") && !textValue.trimmed().isEmpty();
                const QString state=!loaded?QStringLiteral("unknown"):
                    textValue.contains(QStringLiteral("captcha"))||textValue.contains(QStringLiteral("滑块"))?QStringLiteral("captcha_required"):
                    page.value(QStringLiteral("login")).toBool()?QStringLiteral("login_required"):QStringLiteral("authenticated");
                emitEvent({{"type","auth"},{"state",state},{"checked_at",utcNow()}});
                if(state==QStringLiteral("authenticated") && QDateTime::currentMSecsSinceEpoch()-lastSelectMs_>=60'000) {
                    lastSelectMs_=QDateTime::currentMSecsSinceEpoch();
                    const QString quoted=QString::fromUtf8(QJsonDocument(QJsonArray{symbol_}).toJson(QJsonDocument::Compact));
                    const QString expression=QStringLiteral("(()=>{const s=%1[0];const r=[...document.querySelectorAll('[role=row],tr')].find(x=>x.getClientRects().length&&x.innerText.split(/\\s+/).includes(s));if(r){r.click();return true;}return false;})()").arg(quoted);
                    sendCdp(QStringLiteral("Runtime.evaluate"),{{"expression",expression},{"returnByValue",true}},QStringLiteral("select_symbol"));
                }
            }
            return;
        }
        const QString method=value.value(QStringLiteral("method")).toString(); const auto params=value.value(QStringLiteral("params")).toObject();
        if(method==QStringLiteral("Network.responseReceived")) {
            const auto response=params.value(QStringLiteral("response")).toObject(); const QUrl url(response.value(QStringLiteral("url")).toString());
            if(url.scheme()==QStringLiteral("https")&&url.host()==kDepthHost&&url.path()==kDepthPath)
                depthRequests_.insert(params.value(QStringLiteral("requestId")).toString());
        } else if(method==QStringLiteral("Network.loadingFinished")) {
            const QString request=params.value(QStringLiteral("requestId")).toString();
            if(depthRequests_.remove(request))
                sendCdp(QStringLiteral("Network.getResponseBody"),{{"requestId",request}},QStringLiteral("body"));
        } else if(method==QStringLiteral("Network.loadingFailed")) {
            depthRequests_.remove(params.value(QStringLiteral("requestId")).toString());
        } else if(method==QStringLiteral("Page.loadEventFired")) {
            checkAuth();
        } else if(method==QStringLiteral("Network.webSocketFrameReceived")) {
            const auto response=params.value(QStringLiteral("response")).toObject();
            if(response.value(QStringLiteral("opcode")).toInt()==2)
                emitEvent({{"type","raw_mqtt"},{"captured_at",utcNow()},{"base64",response.value(QStringLiteral("payloadData"))}});
        }
    }

    void checkAuth()
    {
        if(!collectorRunning_){authCheckPending_=true;return;}
        sendCdp(QStringLiteral("Runtime.evaluate"),{{"expression",QStringLiteral("JSON.stringify({url:location.href,text:(document.body&&document.body.innerText||'').slice(0,4000),login:[...document.querySelectorAll('header button,header a,header span')].some(x=>x.getClientRects().length&&/^(login|log in|sign in|登录)$/i.test(x.innerText.trim()))})")},
            {"returnByValue",true}},QStringLiteral("auth"));
    }

    void stopChrome()
    {
        portPoll_.stop(); authPoll_.stop(); stopping_=true; collectorRunning_=false; pending_.clear(); depthRequests_.clear(); websocket_.abort();
        if(chrome_.state()!=QProcess::NotRunning){chrome_.terminate();if(!chrome_.waitForFinished(3'000)){chrome_.kill();chrome_.waitForFinished(1'000);}}
        emitEvent({{"type","browser"},{"state","stopped"},{"collector_running",false}});
    }

    QString profile_,tickerId_,symbol_;
    QTimer authPoll_;
    QSet<QString> depthRequests_;
    qint64 lastSelectMs_=0;
    QProcess chrome_;
    QNetworkAccessManager network_;
    QWebSocket websocket_;
    QTimer portPoll_;
    QDeadlineTimer portDeadline_;
    QHash<int,QString> pending_;
    int nextId_=0;
    bool stopping_=false,collectorRunning_=false,visible_=false,authCheckPending_=false;
};

}

int main(int argc,char **argv)
{
    QCoreApplication app(argc,argv);
    QCommandLineParser parser; parser.addHelpOption();
    QCommandLineOption profile("profile","MachomeHub Chrome profile","path");
    QCommandLineOption ticker("ticker-id","Webull ticker id","id");
    QCommandLineOption symbol("symbol","display symbol","symbol");
    parser.addOptions({profile,ticker,symbol}); parser.process(app);
    if(parser.value(profile).isEmpty()||parser.value(ticker).isEmpty()||parser.value(symbol).isEmpty())return 2;
    Helper helper(parser.value(profile),parser.value(ticker),parser.value(symbol));
    return app.exec();
}
