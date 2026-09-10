#pragma once
#include <QObject>
#include <QProcess>
#include <QTimer>
#include <QJsonObject>
#include <QElapsedTimer>
namespace machome::upload {
class UploadBusinessComponent final : public QObject {
    Q_OBJECT
public:
    explicit UploadBusinessComponent(QObject *parent=nullptr);
    ~UploadBusinessComponent() override;
    bool configure(const QString &root,const QString &config,QString *error);
    void start();
    void stop();
    void submit(const QString &action,const QJsonObject &arguments,const QString &id);
    QJsonObject snapshot() const {return status_;}
signals:
    void changed();
    void commandFinished(const QString &,bool,const QString &,const QJsonObject &);
private:
    void consume();
    void recover();
    QProcess process_;
    QTimer restart_;
    QElapsedTimer clock_;
    QList<qint64> failures_;
    QByteArray buffer_;
    QString root_,config_,program_;
    QJsonObject status_;
    bool requested_=false;
};
}
