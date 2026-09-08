#include "MonitorSyncEngine.h"
#include "FileStore.h"
#include "common/Config.h"
#include <QtConcurrent>
#include <QFutureWatcher>
#include <QWebSocketServer>
#include <QWebSocket>
#include <QSslCertificate>
#include <QSslKey>
#include <QSslConfiguration>
#include <QJsonDocument>
#include <QDirIterator>
#include <QFile>
#include <QStorageInfo>
#include <QFileInfo>
#include <QLockFile>
#include <QSslSocket>
#include <QUuid>
#include <QDateTime>
namespace machome::sync {
MonitorSyncEngine::MonitorSyncEngine(QObject *p):IModuleEngine(p){pool_.setMaxThreadCount(1);statusTimer_.setParent(this);statusTimer_.setInterval(2000);connect(&statusTimer_,&QTimer::timeout,this,&MonitorSyncEngine::publish);}
MonitorSyncEngine::~MonitorSyncEngine(){stop();pool_.waitForDone();}
void MonitorSyncEngine::initialize(const hub::ModuleContext &c){try{context_=c;context_.dataRoot=hub::AppConfig::expandPath(c.dataRoot);QString state=hub::AppConfig::expandPath(context_.settings.value("state_file").toString());if(!state.isEmpty()&&QFileInfo::exists(state)){auto saved=FileStore::readJson(state);context_.dataRoot=saved.value("data_root").toString(context_.dataRoot);if(saved.contains("listen_port"))context_.settings["listen_port"]=saved["listen_port"];}}catch(const std::exception &e){initializationError_=QString::fromUtf8(e.what());}}
void MonitorSyncEngine::start(){
    if(server_&&server_->isListening())return;
    error_.clear();try{
        if(!initializationError_.isEmpty())throw std::runtime_error(initializationError_.toStdString());
        auto s=context_.settings; bool test=s["test_mode"].toBool();QString host=s["listen_host"].toString(test?"127.0.0.1":"0.0.0.0");
        bool tls=s["tls_enabled"].toBool(false);
        if(server_){delete server_;server_=nullptr;}
        FileStore store(context_.dataRoot);
        if(lease_){delete lease_;lease_=nullptr;}lease_=new QLockFile(context_.dataRoot+"/.server.lock");lease_->setStaleLockTime(0);if(!lease_->tryLock(0))throw std::runtime_error("另一个服务正在使用此数据目录");
        history_={};QDirIterator committed(context_.dataRoot+"/datasets",QStringList{"HEAD.json"},QDir::Files,QDirIterator::Subdirectories);
        while(committed.hasNext()){auto h=FileStore::readJson(committed.next());auto receipt=h["last_commit"].toObject();history_.append(QJsonObject{{"date",receipt["trade_day"]},{"device",h["last_device"]},{"result",receipt}});}

        if(QFileInfo::exists(context_.dataRoot+"/devices.json"))devices_=FileStore::readJson(context_.dataRoot+"/devices.json");else devices_={};
        server_=new QWebSocketServer("Machome Monitor Sync",tls?QWebSocketServer::SecureMode:QWebSocketServer::NonSecureMode,this);
        if(tls){QFile cert(s["certificate_file"].toString()),key(s["private_key_file"].toString());if(!cert.open(QIODevice::ReadOnly)||!key.open(QIODevice::ReadOnly))throw std::runtime_error("TLS 证书或私钥不可读");
            QSslConfiguration ssl=QSslConfiguration::defaultConfiguration();QSslCertificate c(cert.readAll());QSslKey k(key.readAll(),QSsl::Rsa);if(c.isNull()||k.isNull())throw std::runtime_error("TLS 证书或私钥无效");ssl.setLocalCertificate(c);ssl.setPrivateKey(k);ssl.setPeerVerifyMode(QSslSocket::VerifyNone);ssl.setProtocol(QSsl::TlsV1_2OrLater);server_->setSslConfiguration(ssl);}
        connect(server_,&QWebSocketServer::newConnection,this,[this]{while(server_->hasPendingConnections()){auto *socket=server_->nextPendingConnection();
            if(clients_.size()>=32){socket->close();socket->deleteLater();continue;}socket->setMaxAllowedIncomingMessageSize(24*1024*1024);socket->setMaxAllowedIncomingFrameSize(24*1024*1024);clients_.insert(socket,{});
            connect(socket,&QWebSocket::textMessageReceived,this,[this,socket](const QString &m){receive(socket,m);});
            connect(socket,&QWebSocket::disconnected,this,[this,socket]{clients_.remove(socket);socket->deleteLater();publish();});
            QTimer::singleShot(5000,socket,[this,socket]{if(clients_.value(socket).isEmpty())socket->close(QWebSocketProtocol::CloseCodePolicyViolated,"authentication timeout");});}}
        );
        if(!server_->listen(QHostAddress(host),quint16(s["listen_port"].toInt(18976))))throw std::runtime_error(server_->errorString().toStdString());
        statusTimer_.start();
    }catch(const std::exception &e){error_=QString::fromUtf8(e.what());if(server_)server_->close();if(lease_){delete lease_;lease_=nullptr;}}publish();
}
void MonitorSyncEngine::stop(hub::StopMode){statusTimer_.stop();if(server_)server_->close();for(auto *s:clients_.keys())s->close();pool_.waitForDone();if(lease_){delete lease_;lease_=nullptr;}publish();}
QJsonObject MonitorSyncEngine::snapshot()const{QJsonObject publicDevices=devices_;for(auto it=publicDevices.begin();it!=publicDevices.end();++it){auto d=it.value().toObject();d.remove("token_hash");it.value()=d;}QJsonArray clients;for(auto c:clients_)if(!c.isEmpty())clients.append(c);QStorageInfo disk(context_.dataRoot);return {{"running",server_&&server_->isListening()},{"data_root",context_.dataRoot},{"listen_port",server_?int(server_->serverPort()):0},{"listen_host",context_.settings["listen_host"]},{"error",error_},{"authentication_required",context_.settings["auth_enabled"].toBool(false)},{"tls",context_.settings["tls_enabled"].toBool(false)},{"pending",pending_},{"free_bytes",double(disk.bytesAvailable())},{"clients",clients},{"history",history_},{"devices",publicDevices}};}
void MonitorSyncEngine::publish(){emit snapshotReady(snapshot());}
void MonitorSyncEngine::receive(QWebSocket *socket,const QString &message){
    QJsonParseError parse;auto doc=QJsonDocument::fromJson(message.toUtf8(),&parse);auto r=doc.object();QString id=r["request_id"].toString();
    auto fail=[&](const QString &m){socket->sendTextMessage(QString::fromUtf8(QJsonDocument(QJsonObject{{"request_id",id},{"ok",false},{"error",m}}).toJson(QJsonDocument::Compact)));};
    if(parse.error!=QJsonParseError::NoError||!doc.isObject()){fail("请求 JSON 无效");return;}
    QString action=r["action"].toString();auto client=clients_.value(socket);
    if(action=="hello"){
        QString device=r["device_id"].toString();auto known=devices_[device].toObject();QString hash=FileStore::digest(r["token"].toString().toUtf8());
        if(context_.settings["auth_enabled"].toBool(false) && (known.isEmpty()||known["revoked"].toBool()||known["token_hash"].toString()!=hash)){fail("设备认证失败");socket->close();return;}
        if(!context_.settings["auth_enabled"].toBool(false))known=QJsonObject{{"name",r.value("name").toString("内网设备")},{"read_only",false},{"address",socket->peerAddress().toString()}};known.remove("token_hash");known["device_id"]=device;known["last_seen"]=QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);clients_[socket]=known;
        socket->sendTextMessage(QString::fromUtf8(QJsonDocument(QJsonObject{{"request_id",id},{"ok",true},{"result",QJsonObject{{"protocol",1},{"device_id",device}}}}).toJson(QJsonDocument::Compact)));publish();return;
    }
    if(client.isEmpty()){fail("请先认证");return;}
    if(action=="upload_day"&&client["read_only"].toBool()){fail("设备只读");return;}
    if(action=="ack_applied"){client["applied"]=r["applied"];client["last_seen"]=QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);clients_[socket]=client;socket->sendTextMessage(QString::fromUtf8(QJsonDocument(QJsonObject{{"request_id",id},{"ok",true},{"result",QJsonObject{}}}).toJson(QJsonDocument::Compact)));publish();return;}
    if(pending_>=32){fail("服务繁忙，请稍后重试");return;}r["device_id"]=client["device_id"];job(r,socket,id);
}
void MonitorSyncEngine::job(const QJsonObject &r,QWebSocket *socket,const QString &id){
    ++pending_;auto *watcher=new QFutureWatcher<QJsonObject>(this);QPointer<QWebSocket> target(socket);QString root=context_.dataRoot;
    connect(watcher,&QFutureWatcher<QJsonObject>::finished,this,[this,watcher,target,id,r]{--pending_;auto response=watcher->result();response["request_id"]=id;
        if(target)target->sendTextMessage(QString::fromUtf8(QJsonDocument(response).toJson(QJsonDocument::Compact)));
        if(response["ok"].toBool()&&r["action"]=="upload_day"){
            QJsonObject event{{"device",r["device_id"]},{"date",r["snapshot"].toObject()["trade_day"]},{"result",response["result"]}};history_.prepend(event);while(history_.size()>50)history_.removeLast();
            QJsonObject notification{{"event","changed"},{"dataset_id",r["dataset_id"]},{"source_id",r["source_id"]},{"receipt",response["result"]}};
            for(auto *s:clients_.keys())if(!clients_[s].isEmpty())s->sendTextMessage(QString::fromUtf8(QJsonDocument(notification).toJson(QJsonDocument::Compact)));
            emit eventReady("monitor_sync.commit",event);
        }watcher->deleteLater();publish();});
    watcher->setFuture(QtConcurrent::run(&pool_,[root,r]{try{return QJsonObject{{"ok",true},{"result",FileStore(root).dispatch(r)}};}catch(const std::exception &e){return QJsonObject{{"ok",false},{"error",QString::fromUtf8(e.what())}};}}));
}
void MonitorSyncEngine::submitCommand(const QString &action,const QJsonObject &args,const QString &id){
    bool ok=true;QString message="完成";QJsonObject details;
    try{
        if(action=="monitor_sync_status")details=snapshot();
        else if(action=="monitor_sync_set_port"){
            if(pending_)throw std::runtime_error("有提交处理中，请稍后修改端口");int port=args["port"].toInt();if(port<1||port>65535)throw std::runtime_error("端口必须在 1 至 65535 之间");
            QString state=hub::AppConfig::expandPath(context_.settings.value("state_file").toString());if(state.isEmpty())throw std::runtime_error("未配置状态文件");int old=context_.settings["listen_port"].toInt(18976);stop();context_.settings["listen_port"]=port;start();
            if(!snapshot()["running"].toBool()){QString failure=error_;context_.settings["listen_port"]=old;start();throw std::runtime_error(failure.toStdString());}
            try{FileStore::writeJson(state,{{"data_root",context_.dataRoot},{"listen_port",port}});}catch(...){stop();context_.settings["listen_port"]=old;start();throw;}
            message="端口已更新，监控端需使用新端口重连";
        }else if(action=="monitor_sync_add_device"){
            QString device=QUuid::createUuid().toString(QUuid::Id128),token=QUuid::createUuid().toString(QUuid::Id128)+QUuid::createUuid().toString(QUuid::Id128);
            devices_[device]=QJsonObject{{"name",args["name"]},{"read_only",args["read_only"].toBool()},{"token_hash",FileStore::digest(token.toUtf8())}};FileStore::writeJson(context_.dataRoot+"/devices.json",devices_);
            QString path=context_.dataRoot+"/pairing/"+device+".json";FileStore::writeJson(path,{{"device_id",device},{"token",token},{"name",args["name"]},{"port",context_.settings["listen_port"]}});QFile::setPermissions(path,QFile::ReadOwner|QFile::WriteOwner);details["pairing_file"]=path;message="设备已创建；凭据保存在本机配对文件";
        }else if(action=="monitor_sync_revoke_device"){
            QString device=args["device_id"].toString();if(!devices_.contains(device))throw std::runtime_error("设备不存在");auto d=devices_[device].toObject();d["revoked"]=true;devices_[device]=d;FileStore::writeJson(context_.dataRoot+"/devices.json",devices_);for(auto *s:clients_.keys())if(clients_[s]["device_id"]==device)s->close();
        }else if(action=="monitor_sync_set_root"||action=="monitor_sync_backup"){
            if(pending_)throw std::runtime_error("有提交处理中，请完成后重试");QString state=hub::AppConfig::expandPath(context_.settings.value("state_file").toString());if(action=="monitor_sync_set_root"&&state.isEmpty())throw std::runtime_error("未配置目录状态文件");QString to=hub::AppConfig::expandPath(args["path"].toString());QString from=QDir(context_.dataRoot).canonicalPath();
            to=QDir::cleanPath(to);if(!QDir::isAbsolutePath(to)||to==from||to.startsWith(from+"/")||from.startsWith(to+"/")||QFileInfo::exists(to))throw std::runtime_error("请选择不存在且不嵌套的新目录");
            FileStore target(to);QDirIterator it(from,QDir::Files|QDir::Dirs|QDir::NoDotAndDotDot,QDirIterator::Subdirectories);
            while(it.hasNext()){QString p=it.next(),rel=QDir(from).relativeFilePath(p);if(it.fileInfo().isSymLink())throw std::runtime_error("存储内存在符号链接");if(it.fileInfo().isDir()){QDir().mkpath(to+"/"+rel);continue;}if(rel.endsWith(".lock"))continue;QFile in(p);if(!in.open(QIODevice::ReadOnly))throw std::runtime_error("备份源不可读");auto b=in.readAll();QFile out(to+"/"+rel);if(!out.open(QIODevice::WriteOnly)||out.write(b)!=b.size())throw std::runtime_error("复制失败");out.close();QFile verify(to+"/"+rel);verify.open(QIODevice::ReadOnly);if(FileStore::digest(verify.readAll())!=FileStore::digest(b))throw std::runtime_error("复制校验失败");}
            if(action=="monitor_sync_set_root"){
                stop();context_.dataRoot=to;
                try {start();if(!snapshot()["running"].toBool())throw std::runtime_error(error_.toStdString());FileStore::writeJson(state,{{"data_root",to},{"listen_port",context_.settings["listen_port"]}});}
                catch(...){stop();context_.dataRoot=from;start();throw;}
                details["data_root"]=to;message="目录迁移完成，旧目录保留";
            }else{message="备份完成";details["backup_path"]=to;}
        }else throw std::runtime_error("未知同步控制操作");
    }catch(const std::exception &e){ok=false;message=QString::fromUtf8(e.what());}
    emit commandFinished(id,ok,message,details);publish();
}
}
