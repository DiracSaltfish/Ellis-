#include "ui/MonitorSyncPage.h"
#include "ui/UiWidgets.h"
#include "ui/UiText.h"
#include <QTabWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QLineEdit>
#include <QPushButton>
#include <QFileDialog>
#include <QInputDialog>
#include <QJsonDocument>
#include <QJsonArray>
#include <QLabel>
#include <QDateTime>
#include <QDesktopServices>
#include <QUrl>
#include <QTableWidget>
#include <QFrame>
#include <QApplication>
#include <QClipboard>
#include <QScrollArea>
namespace hub {
namespace {
QLabel *label(const QString &text,const QString &name={}){auto *l=new QLabel(text);l->setObjectName(name);l->setWordWrap(true);l->setTextFormat(Qt::PlainText);l->setTextInteractionFlags(Qt::TextSelectableByMouse);return l;}
void cell(QTableWidget *t,int r,int c,const QString &text,const QJsonObject &raw={}){
 auto *i=t->item(r,c);if(!i){i=new QTableWidgetItem;t->setItem(r,c,i);}i->setText(text);i->setToolTip(text);
 if(!raw.isEmpty())i->setData(Qt::UserRole,QString::fromUtf8(QJsonDocument(raw).toJson(QJsonDocument::Compact)));
}
QString value(const QJsonObject &o,const QString &k){return o.contains(k)?ui::valueText(o[k]):QStringLiteral("未报告");}
}
MonitorSyncPage::MonitorSyncPage(ModuleConfig c,QWidget *p):ModulePage(std::move(c),p){
 auto *tabs=new QTabWidget;
 auto *status=new QWidget;auto *main=new QVBoxLayout(status);auto *cards=new QGridLayout;
 const QStringList names{QStringLiteral("服务监听"),QStringLiteral("在线设备"),QStringLiteral("处理中提交"),QStringLiteral("磁盘可用")};
 for(int i=0;i<4;++i){auto *card=new QFrame;card->setObjectName("metricCard");auto *l=new QVBoxLayout(card);l->addWidget(label(names[i],"metricCaption"));metrics_[i]=label(QStringLiteral("等待状态"),"metricValue");l->addWidget(metrics_[i]);cards->addWidget(card,0,i);cards->setColumnStretch(i,1);}
 main->addLayout(cards);connection_=label(QStringLiteral("等待监听状态"));main->addWidget(connection_);
 notice_=label(QStringLiteral("尚未收到同步服务状态。"),"syncNotice");main->addWidget(notice_);
 main->addWidget(label(QStringLiteral("服务保持常驻；设备确认应用的版本与服务器保存的版本分别记录。"),"secondaryText"));
 auto *shortcuts=new QHBoxLayout;
 auto *toDevices=new QPushButton(QStringLiteral("查看设备状态"));auto *toHistory=new QPushButton(QStringLiteral("查看最近提交"));
 shortcuts->addWidget(toDevices);shortcuts->addWidget(toHistory);shortcuts->addStretch();main->addLayout(shortcuts);main->addStretch();
 tabs->addTab(status,QStringLiteral("运行概览"));
 devices_=ui::jsonTable({QStringLiteral("设备名称"),QStringLiteral("地址"),QStringLiteral("最后活动"),QStringLiteral("已应用日期"),QStringLiteral("版本"),QStringLiteral("待上传"),QStringLiteral("设备 ID")});devices_->setObjectName("syncDevices");
 tabs->addTab(ui::tablePanel(devices_,"syncDevices",{}, {6}),QStringLiteral("设备与应用状态"));
 auto *historyPage=new QWidget;auto *hl=new QVBoxLayout(historyPage);hl->setContentsMargins(0,0,0,0);
 hl->addWidget(label(QStringLiteral("显示服务提供的最近提交；重启后从各日期最新记录恢复，不代表完整审计历史。"),"secondaryText"));
 history_=ui::jsonTable({QStringLiteral("提交时间"),QStringLiteral("数据日期"),QStringLiteral("版本"),QStringLiteral("来源设备")});history_->setObjectName("syncHistory");hl->addWidget(ui::tablePanel(history_,"syncHistory"),1);
 tabs->addTab(historyPage,QStringLiteral("最近提交"));
 connect(toDevices,&QPushButton::clicked,tabs,[tabs]{tabs->setCurrentIndex(1);});connect(toHistory,&QPushButton::clicked,tabs,[tabs]{tabs->setCurrentIndex(2);});
 auto *settings=new QWidget;auto *layout=new QVBoxLayout(settings);layout->setAlignment(Qt::AlignTop);
 layout->addWidget(label(QStringLiteral("网络与存储"),"sectionTitle"));
 root_=new QLineEdit;root_->setReadOnly(true);root_->setPlaceholderText(QStringLiteral("等待服务报告数据目录"));layout->addWidget(root_);
 auto *buttons=new QGridLayout;int index=0;
 auto button=[&](const QString &text,bool mutation,auto fn){auto *b=new QPushButton(text);buttons->addWidget(b,index/2,index%2);++index;if(mutation)controls_<<b;connect(b,&QPushButton::clicked,this,fn);return b;};
 button(QStringLiteral("设置监听端口"),true,[this]{bool ok=false;int port=QInputDialog::getInt(this,QStringLiteral("监听端口"),QStringLiteral("新端口（切换时设备会重新连接）"),port_,1,65535,1,&ok);if(ok&&port!=port_)send("monitor_sync_set_port",{{"port",port}});});
 button(QStringLiteral("打开数据目录"),false,[this]{if(!root_->text().isEmpty())QDesktopServices::openUrl(QUrl::fromLocalFile(root_->text()));});
 button(QStringLiteral("复制数据目录"),false,[this]{QApplication::clipboard()->setText(root_->text());});
 button(QStringLiteral("迁移数据目录"),true,[this]{QString parent=QFileDialog::getExistingDirectory(this,QStringLiteral("选择目标父目录"));if(parent.isEmpty())return;bool ok=false;QString name=QInputDialog::getText(this,QStringLiteral("迁移到新目录"),QStringLiteral("新目录名称"),QLineEdit::Normal,"monitor-sync",&ok);if(ok&&!name.isEmpty()&&name!="."&&name!=".."&&!name.contains('/'))send("monitor_sync_set_root",{{"path",parent+"/"+name}},120000);});
 button(QStringLiteral("备份全部文件"),true,[this]{QString parent=QFileDialog::getExistingDirectory(this,QStringLiteral("选择备份父目录"));if(parent.isEmpty())return;send("monitor_sync_backup",{{"path",parent+"/monitor-sync-"+QDateTime::currentDateTime().toString("yyyyMMdd-HHmmss-zzz")}},120000);});
 if(config_.settings.value("auth_enabled").toBool(false)){
 button(QStringLiteral("添加设备"),true,[this]{bool ok=false;QString name=QInputDialog::getText(this,QStringLiteral("添加同步设备"),QStringLiteral("设备名称"),QLineEdit::Normal,{},&ok);if(ok&&!name.trimmed().isEmpty())send("monitor_sync_add_device",{{"name",name},{"read_only",false}});});
 button(QStringLiteral("撤销设备权限"),true,[this]{bool ok=false;QString id=QInputDialog::getText(this,QStringLiteral("撤销设备"),QStringLiteral("设备 ID"),QLineEdit::Normal,{},&ok);if(ok&&!id.isEmpty())send("monitor_sync_revoke_device",{{"device_id",id}});});}
 layout->addLayout(buttons);layout->addWidget(label(QStringLiteral("迁移和备份请选择不存在的新目录。原始 CSV / JSON 按日期保留；此页不执行清理删除。"),"secondaryText"));
 operation_=label(QStringLiteral("尚无维护操作。"),"syncOperationResult");layout->addWidget(operation_);layout->addStretch();tabs->addTab(settings,QStringLiteral("设置与维护"));setContent(tabs);updateBusyControls();
}
void MonitorSyncPage::applySnapshot(const QJsonObject &m){
 ModulePage::applySnapshot(m);auto e=m["payload"].toObject()["telemetry"].toObject()["engine"].toObject();
 metrics_[0]->setText(e.contains("running")?(e["running"].toBool()?QStringLiteral("运行中"):QStringLiteral("已停止")):QStringLiteral("未报告"));
 metrics_[1]->setText(e["clients"].isArray()?QString::number(e["clients"].toArray().size()):QStringLiteral("—"));metrics_[2]->setText(value(e,"pending"));
 metrics_[3]->setText(e.contains("free_bytes")?QString::number(e["free_bytes"].toDouble()/1073741824.0,'f',1)+" GiB":QStringLiteral("—"));
 if(e["listen_port"].toInt()>0)port_=e["listen_port"].toInt();
 connection_->setText(QStringLiteral("监听：%1:%2 · 客户端使用这台主机的内网 IP 与端口连接").arg(e["listen_host"].toString(QStringLiteral("未报告"))).arg(port_));
 const auto error=e["error"].toString();notice_->setText(error.isEmpty()?QStringLiteral("暂无服务端错误。应用进度请查看设备确认；在线不等于已应用最新版本。"):ui::problemText(error));
 root_->setText(e["data_root"].toString());const auto clients=e["clients"].toArray();devices_->setRowCount(clients.size());
 for(int r=0;r<clients.size();++r){auto c=clients[r].toObject(),a=c["applied"].toObject();cell(devices_,r,0,value(c,"name"),c);cell(devices_,r,1,value(c,"address"));cell(devices_,r,2,value(c,"last_seen"));cell(devices_,r,3,value(a,"trade_day"));cell(devices_,r,4,value(a,"sequence"));cell(devices_,r,5,value(a,"local_changes_pending"));cell(devices_,r,6,value(c,"device_id"));}
 const auto history=e["history"].toArray();history_->setRowCount(history.size());for(int r=0;r<history.size();++r){auto c=history[r].toObject(),result=c["result"].toObject();cell(history_,r,0,value(result,"committed_at"),c);cell(history_,r,1,value(c,"date"));cell(history_,r,2,value(result,"sequence"));cell(history_,r,3,value(c,"device"));}
 updateBusyControls();
}
void MonitorSyncPage::updateBusyControls(){ModulePage::updateBusyControls();for(auto *b:controls_)b->setEnabled(logicalControlAllowed(lastSnapshot_));}
void MonitorSyncPage::applyCommand(const QJsonObject &m){ModulePage::applyCommand(m);operation_->setText(QStringLiteral("%1 · %2：%3").arg(ui::localTimeText(m["timestamp"].toString()),ui::stateText(m["state"].toString()),m["message"].toString()));auto d=m["details"].toObject();if(d.contains("pairing_file")&&m["state"].toString()=="succeeded")QDesktopServices::openUrl(QUrl::fromLocalFile(d["pairing_file"].toString()));}
}
