#include "UploadBusinessComponent.h"
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
namespace machome::upload {
UploadBusinessComponent::UploadBusinessComponent(QObject *parent):QObject(parent) {
    process_.setParent(this);restart_.setParent(this);restart_.setSingleShot(true);clock_.start();
    connect(&restart_,&QTimer::timeout,this,[this]{if(requested_)start();});
    connect(&process_,&QProcess::readyReadStandardOutput,this,&UploadBusinessComponent::consume);
    connect(&process_,&QProcess::readyReadStandardError,this,[this]{process_.readAllStandardError();});
    connect(&process_,&QProcess::finished,this,[this](int,QProcess::ExitStatus){recover();});
    connect(&process_,&QProcess::errorOccurred,this,[this](QProcess::ProcessError){recover();});
}
UploadBusinessComponent::~UploadBusinessComponent(){stop();}
bool UploadBusinessComponent::configure(const QString &root,const QString &config,QString *error) {
    root_=QFileInfo(root).canonicalFilePath();config_=QFileInfo(config).canonicalFilePath();
    program_=QDir(QCoreApplication::applicationDirPath()).absoluteFilePath(
        QStringLiteral("../Resources/upload/machome-upload-component/machome-upload-component"));
    if(root_.isEmpty()||config_.isEmpty()||!config_.startsWith(root_+u'/')
        ||!QFileInfo(program_).isExecutable()) {
        if(error)*error=QStringLiteral("Upload 包内组件或专属 0600 配置缺失");return false;
    }
    status_={{"engine","bundled_business"},{"state","initialized"},{"record_only",false},{"ready",false}};
    return true;
}
void UploadBusinessComponent::start() {
    if(process_.state()!=QProcess::NotRunning)return;
    requested_=true;buffer_.clear();
    status_.insert("state","starting");status_.insert("ready",false);status_.insert("process_running",false);emit changed();
    process_.setWorkingDirectory(root_);
    process_.start(program_,{QStringLiteral("--data-root"),root_,QStringLiteral("--config"),config_});
    if(process_.waitForStarted(2000)){
        if(process_.bytesAvailable()>0 || process_.waitForReadyRead(5000))consume();
    }
    if(!status_.value("process_running").toBool()){
        status_.insert("state","blocked");status_.insert("ready",false);emit changed();
    }
}
void UploadBusinessComponent::stop() {
    requested_=false;restart_.stop();failures_.clear();
    if(process_.state()!=QProcess::NotRunning){
        process_.write("{\"action\":\"stop\"}\n");process_.closeWriteChannel();
        if(!process_.waitForFinished(12000)){process_.terminate();process_.waitForFinished(12000);}
    }
    status_.insert("state","stopped");status_.insert("ready",false);status_.insert("process_running",false);emit changed();
}
void UploadBusinessComponent::recover() {
    if(!requested_||restart_.isActive())return;
    const auto now=clock_.elapsed();
    while(!failures_.isEmpty()&&now-failures_.first()>=300000)failures_.removeFirst();
    status_.insert("ready",false);status_.insert("process_running",false);
    if(failures_.size()>=3)status_.insert("state","fault");
    else {failures_.append(now);status_.insert("state","recovering");restart_.start(2000*failures_.size());}
    emit changed();
}
void UploadBusinessComponent::consume() {
    buffer_.append(process_.readAllStandardOutput());
    if(buffer_.size()>1024*1024){buffer_.clear();process_.terminate();return;}
    while(true){
        const auto n=buffer_.indexOf('\n');if(n<0)break;
        const auto doc=QJsonDocument::fromJson(buffer_.left(n));buffer_.remove(0,n+1);
        if(!doc.isObject())continue;
        const auto frame=doc.object();const auto type=frame.value("type").toString();
        if(type==QStringLiteral("hello")){status_.insert("process_running",true);status_.insert("state","warming");emit changed();}
        else if(type==QStringLiteral("status")){status_=frame;status_.insert("process_running",true);emit changed();}
        else if(type==QStringLiteral("fatal")){status_=frame;status_.insert("state","fault");status_.insert("ready",false);emit changed();}
        else if(type==QStringLiteral("command_result"))emit commandFinished(frame.value("command_id").toString(),frame.value("ok").toBool(),frame.value("message").toString(),frame);
    }
}
void UploadBusinessComponent::submit(const QString &action,const QJsonObject &args,const QString &id) {
    if(process_.state()!=QProcess::Running){emit commandFinished(id,false,QStringLiteral("Upload 业务组件未运行"),{});return;}
    if(action!=QStringLiteral("upload_run_job")&&action!=QStringLiteral("upload_component_restart")){
        emit commandFinished(id,false,QStringLiteral("此动作不适用于包内业务模式；请使用原网站业务 API"),{});return;
    }
    QJsonObject command{{"action","restart"},{"job_id",args.value("job_id")},{"command_id",id}};
    process_.write(QJsonDocument(command).toJson(QJsonDocument::Compact)+'\n');
}
}
