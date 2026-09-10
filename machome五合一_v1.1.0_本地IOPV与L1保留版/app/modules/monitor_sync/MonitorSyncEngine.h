#pragma once
#include "common/ModuleEngine.h"
#include <QThreadPool>
#include <QJsonArray>
#include <QPointer>
#include <QHash>
#include <QTimer>
class QWebSocketServer;
class QWebSocket;
class QLockFile;
namespace machome::sync {
class MonitorSyncEngine final : public hub::IModuleEngine {
    Q_OBJECT
public:
    explicit MonitorSyncEngine(QObject *parent=nullptr);
    ~MonitorSyncEngine() override;
    QJsonObject snapshot() const;
public slots:
    void initialize(const hub::ModuleContext &) override;
    void start() override;
    void stop(hub::StopMode mode=hub::StopMode::Graceful) override;
    void submitCommand(const QString &,const QJsonObject &,const QString &) override;
private:
    void receive(QWebSocket *,const QString &);
    void publish();
    void job(const QJsonObject &, QWebSocket *,const QString &);
    hub::ModuleContext context_;
    QWebSocketServer *server_=nullptr;
    QThreadPool pool_;
    QLockFile *lease_=nullptr;
    QJsonObject devices_;
    QHash<QWebSocket *,QJsonObject> clients_;
    QJsonArray history_;
    QString error_;
    QString initializationError_;
    int pending_=0;
    QTimer statusTimer_;
};
}
