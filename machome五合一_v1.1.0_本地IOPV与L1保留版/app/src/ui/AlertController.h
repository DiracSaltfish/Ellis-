#pragma once
#include <QObject>
#include <QElapsedTimer>
#include <QHash>
#include <QProcess>
#include <QTimer>
#include <QPointer>
class QWidget;
class QMessageBox;
namespace hub {
class AlertController final : public QObject {
    Q_OBJECT
public:
    explicit AlertController(QObject *parent=nullptr);
    ~AlertController() override;
    static void configure(QWidget *parent);
    void notify(const QString &module,const QString &title,const QString &message,bool sound,bool popup);
    static QString soundPath(const QString &id,const QString &external);
signals:
    void presented(const QString &message, bool sound, bool popup);
    void playbackRequested(const QString &path,int volume,int repeats);
private:
    void flush();
    void play();
    QElapsedTimer clock_;
    QHash<QString,qint64> last_;
    QTimer batch_;
    QStringList pending_;
    QString title_;
    bool sound_=false,popup_=false;
    QProcess audio_;
    int repeats_=0;
    QString path_;
    int volume_=75;
    QPointer<QMessageBox> popupBox_;
};
}
