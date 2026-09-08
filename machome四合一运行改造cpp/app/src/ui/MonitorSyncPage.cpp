#include "ui/MonitorSyncPage.h"
#include <QTabWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QPlainTextEdit>
#include <QLineEdit>
#include <QPushButton>
#include <QFileDialog>
#include <QInputDialog>
#include <QJsonDocument>
#include <QLabel>
#include <QDateTime>
#include <QDesktopServices>
#include <QUrl>
namespace hub {
MonitorSyncPage::MonitorSyncPage(ModuleConfig c,QWidget *p):ModulePage(std::move(c),p){
    auto *tabs=new QTabWidget;auto add=[&](const QString &title){auto *w=new QPlainTextEdit;w->setReadOnly(true);tabs->addTab(w,title);return w;};
    status_=add("运行概览");devices_=add("设备与应用状态");history_=add("数据与提交历史");
    auto *settings=new QWidget;auto *layout=new QVBoxLayout(settings);layout->addWidget(new QLabel("数据按日期保留原始 CSV / JSON。迁移和备份请选择不存在的新目录。"));root_=new QLineEdit;root_->setReadOnly(true);layout->addWidget(root_);
    auto button=[&](const QString &text,auto fn){auto *b=new QPushButton(text);layout->addWidget(b);connect(b,&QPushButton::clicked,this,fn);};
    button("设置监听端口",[this]{bool ok=false;int port=QInputDialog::getInt(this,"监听端口","新端口",18976,1,65535,1,&ok);if(ok)send("monitor_sync_set_port",{{"port",port}});});
    button("打开数据目录",[this]{QDesktopServices::openUrl(QUrl::fromLocalFile(root_->text()));});
    button("迁移数据目录",[this]{QString parent=QFileDialog::getExistingDirectory(this,"选择目标父目录");if(parent.isEmpty())return;bool ok=false;QString name=QInputDialog::getText(this,"新目录","目录名称",QLineEdit::Normal,"monitor-sync",&ok);if(ok&&!name.isEmpty()&&!name.contains('/'))send("monitor_sync_set_root",{{"path",parent+"/"+name}},120000);});
    button("备份全部文件",[this]{QString parent=QFileDialog::getExistingDirectory(this,"选择备份父目录");if(parent.isEmpty())return;send("monitor_sync_backup",{{"path",parent+"/monitor-sync-"+QString::number(QDateTime::currentMSecsSinceEpoch())}},120000);});
    if(config_.settings.value("auth_enabled").toBool(false)) button("添加设备",[this]{bool ok=false;QString name=QInputDialog::getText(this,"添加同步设备","设备名称",QLineEdit::Normal,{},&ok);if(ok&&!name.trimmed().isEmpty())send("monitor_sync_add_device",{{"name",name},{"read_only",false}});});
    if(config_.settings.value("auth_enabled").toBool(false)) button("撤销设备权限",[this]{bool ok=false;QString id=QInputDialog::getText(this,"撤销设备","从设备页复制设备 ID",QLineEdit::Normal,{},&ok);if(ok&&!id.isEmpty())send("monitor_sync_revoke_device",{{"device_id",id}});});
    layout->addStretch();tabs->addTab(settings,"设置与维护");setContent(tabs);
}
void MonitorSyncPage::applySnapshot(const QJsonObject &m){ModulePage::applySnapshot(m);auto e=m["payload"].toObject()["telemetry"].toObject()["engine"].toObject();
    auto pretty=[](const QJsonValue &v){return QString::fromUtf8(v.isArray()?QJsonDocument(v.toArray()).toJson(QJsonDocument::Indented):QJsonDocument(v.toObject()).toJson(QJsonDocument::Indented));};
    status_->setPlainText(QStringLiteral("服务状态：%1\n监听地址：%2:%3\n连接方式：内网 IP + 端口\n数据目录：%4\n磁盘可用：%5 GiB\n在线设备：%6\n处理中：%7\n\n最近错误：%8\n\n数据按日期保存为 CSV / JSON，原始 QMT、IB 成交文件不参与覆盖同步。")
        .arg(e["running"].toBool()?QStringLiteral("运行中"):QStringLiteral("已停止"))
        .arg(e["listen_host"].toString()).arg(e["listen_port"].toInt()).arg(e["data_root"].toString())
        .arg(e["free_bytes"].toDouble()/1073741824.0,0,'f',2).arg(e["clients"].toArray().size()).arg(e["pending"].toInt())
        .arg(e["error"].toString().isEmpty()?QStringLiteral("无"):e["error"].toString()));
    root_->setText(e["data_root"].toString());
    QString clients;
    for(const auto &v:e["clients"].toArray()){auto c=v.toObject(),a=c["applied"].toObject();clients+=QStringLiteral("%1  ·  %2\n设备：%3\n最后活动：%4\n已安装日期：%5  版本：%6  本机待上传：%7\n\n").arg(c["name"].toString(),c["address"].toString(),c["device_id"].toString(),c["last_seen"].toString(),a["trade_day"].toString()).arg(a["sequence"].toInt()).arg(a["local_changes_pending"].toBool()?QStringLiteral("是"):QStringLiteral("否"));}
    devices_->setPlainText(clients.isEmpty()?QStringLiteral("当前没有已连接的监控设备。服务仍正常等待连接。"):clients);
    QString history;
    for(const auto &v:e["history"].toArray()){auto c=v.toObject(),r=c["result"].toObject();history+=QStringLiteral("%1  日期 %2  版本 %3\n来源设备：%4\n\n").arg(r["committed_at"].toString(),c["date"].toString()).arg(r["sequence"].toInt()).arg(c["device"].toString());}
    history_->setPlainText(history.isEmpty()?QStringLiteral("尚无提交。全部历史文件保存在数据目录的 datasets 文件夹，可在设置页打开。"):history);
}
void MonitorSyncPage::applyCommand(const QJsonObject &m){ModulePage::applyCommand(m);auto d=m["details"].toObject();if(d.contains("pairing_file"))QDesktopServices::openUrl(QUrl::fromLocalFile(d["pairing_file"].toString()));}
}
