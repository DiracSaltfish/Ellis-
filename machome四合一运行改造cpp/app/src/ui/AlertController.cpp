#include "AlertController.h"
#include <QApplication>
#include <QCoreApplication>
#include <QDialog>
#include <QDialogButtonBox>
#include <QComboBox>
#include <QFileDialog>
#include <QFileInfo>
#include <QDir>
#include <QFormLayout>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QSettings>
#include <QSpinBox>
#include <QVBoxLayout>
namespace hub {
AlertController::AlertController(QObject *parent):QObject(parent) {
    clock_.start();batch_.setSingleShot(true);batch_.setInterval(100);
    connect(&batch_,&QTimer::timeout,this,&AlertController::flush);
    connect(&audio_,&QProcess::finished,this,[this](int exit,QProcess::ExitStatus status){
        if(exit==0&&status==QProcess::NormalExit&&--repeats_>0)play();
    });
}
AlertController::~AlertController(){repeats_=0;if(audio_.state()!=QProcess::NotRunning){audio_.terminate();audio_.waitForFinished(500);}}
QString AlertController::soundPath(const QString &id,const QString &external){
    if(id==QStringLiteral("external"))return QFileInfo(external).isFile()?external:QString{};
    if(!QStringList{"bright","radar","bell","urgent","soft"}.contains(id))return {};
    return QDir(QCoreApplication::applicationDirPath()).absoluteFilePath("../Resources/sounds/"+id+".wav");
}
void AlertController::configure(QWidget *parent){
    QDialog dialog(parent);dialog.setWindowTitle(QStringLiteral("申赎提醒设置"));
    auto *layout=new QVBoxLayout(&dialog);auto *form=new QFormLayout;layout->addLayout(form);
    QSettings settings;const QString prefix="alerts/redemption/";
    auto *preset=new QComboBox(&dialog);
    const QStringList labels{QStringLiteral("轻亮三音"),QStringLiteral("雷达脉冲"),QStringLiteral("双音铃声"),QStringLiteral("紧急三连音"),QStringLiteral("柔和提示"),QStringLiteral("外部音频文件")};
    const QStringList ids{"bright","radar","bell","urgent","soft","external"};
    for(int i=0;i<ids.size();++i)preset->addItem(labels[i],ids[i]);
    preset->setCurrentIndex(qMax(0,preset->findData(settings.value(prefix+"sound_id","bright"))));form->addRow(QStringLiteral("提示音"),preset);
    auto *path=new QLineEdit(settings.value(prefix+"external_sound_path").toString(),&dialog);
    form->addRow(QStringLiteral("外部音频"),path);auto *browse=new QPushButton(QStringLiteral("选择音频文件"),&dialog);form->addRow(browse);
    connect(browse,&QPushButton::clicked,&dialog,[&]{const auto selected=QFileDialog::getOpenFileName(&dialog,QStringLiteral("选择提示音"));if(!selected.isEmpty()){path->setText(selected);preset->setCurrentIndex(5);}});
    auto spin=[&](const QString &key,const QString &label,int min,int max,int fallback){
        auto *value=new QSpinBox(&dialog);value->setRange(min,max);value->setValue(settings.value(prefix+key,fallback).toInt());form->addRow(label,value);return value;
    };
    auto *volume=spin("volume",QStringLiteral("音量（%）"),0,100,75);
    auto *repeat=spin("sound_repeat_count",QStringLiteral("重复次数"),1,10,3);
    auto *duration=spin("popup_duration_seconds",QStringLiteral("弹窗秒数（0 不自动关闭）"),0,120,12);
    auto *cooldown=spin("alert_cooldown_seconds",QStringLiteral("冷却秒数（0 不限制）"),0,300,0);
    auto *buttons=new QDialogButtonBox(QDialogButtonBox::Save|QDialogButtonBox::Cancel,&dialog);layout->addWidget(buttons);
    connect(buttons,&QDialogButtonBox::accepted,&dialog,&QDialog::accept);connect(buttons,&QDialogButtonBox::rejected,&dialog,&QDialog::reject);
    if(dialog.exec()!=QDialog::Accepted)return;
    settings.setValue(prefix+"sound_id",preset->currentData());settings.setValue(prefix+"external_sound_path",path->text());
    settings.setValue(prefix+"volume",volume->value());settings.setValue(prefix+"sound_repeat_count",repeat->value());
    settings.setValue(prefix+"popup_duration_seconds",duration->value());settings.setValue(prefix+"alert_cooldown_seconds",cooldown->value());
}
void AlertController::notify(const QString &module,const QString &title,const QString &message,bool sound,bool popup){
    if(module!=QStringLiteral("redemption"))return;
    // Group all symbols from one delivery cycle before applying cooldown.
    if(pending_.size()<100)pending_.append(message);title_=title;sound_|=sound;popup_|=popup;
    if(!batch_.isActive())batch_.start();
}
void AlertController::flush(){
    QSettings settings;const QString prefix="alerts/redemption/";const auto now=clock_.elapsed();
    const int cooldown=qBound(0,settings.value(prefix+"alert_cooldown_seconds",0).toInt(),300);
    const auto text=pending_.join(u'\n');pending_.clear();
    const bool sound=sound_,popup=popup_;sound_=popup_=false;
    if(last_.contains("redemption")&&now-last_.value("redemption")<cooldown*1000)return;
    last_.insert("redemption",now);
    emit presented(text,sound,popup);
    if(sound){
        repeats_=0;if(audio_.state()!=QProcess::NotRunning){audio_.terminate();audio_.waitForFinished(100);}
        path_=soundPath(settings.value(prefix+"sound_id","bright").toString(),settings.value(prefix+"external_sound_path").toString());
        repeats_=qBound(1,settings.value(prefix+"sound_repeat_count",3).toInt(),10);
        volume_=qBound(0,settings.value(prefix+"volume",75).toInt(),100);emit playbackRequested(path_,volume_,repeats_);play();
    }
    if(popup){
        if(popupBox_)popupBox_->close();
        auto *box=new QMessageBox(QMessageBox::Information,title_,text,QMessageBox::Ok);
        popupBox_=box;box->setAttribute(Qt::WA_DeleteOnClose);box->setWindowModality(Qt::NonModal);box->show();
        const int duration=qBound(0,settings.value(prefix+"popup_duration_seconds",12).toInt(),120);
        if(duration)QTimer::singleShot(duration*1000,box,&QWidget::close);
    }
}
void AlertController::play(){
    if(qApp->property("test_no_audio").toBool()||volume_==0||!QFileInfo(path_).isFile())return;
    audio_.start(QStringLiteral("/usr/bin/afplay"),{QStringLiteral("-v"),QString::number(volume_/100.0),path_});
}
}
