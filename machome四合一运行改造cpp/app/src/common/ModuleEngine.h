#pragma once

#include <QJsonObject>
#include <QObject>
#include <QString>

namespace hub {

enum class StopMode {
    Graceful,
    Immediate,
};

struct ModuleContext {
    QString moduleId;
    QString dataRoot;
    QJsonObject settings;
    bool recordOnly = true;
};

// Stable in-process boundary between ModuleWorker and native business engines.
// Engines live on their module QThread and must never retain a UI object.
class IModuleEngine : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    ~IModuleEngine() override = default;

public slots:
    virtual void initialize(const hub::ModuleContext &context) = 0;
    virtual void start() = 0;
    virtual void stop(hub::StopMode mode = hub::StopMode::Graceful) = 0;
    virtual void submitCommand(const QString &action, const QJsonObject &arguments,
                               const QString &commandId) = 0;

signals:
    void snapshotReady(const QJsonObject &snapshot);
    void eventReady(const QString &kind, const QJsonObject &event);
    void commandFinished(const QString &commandId, bool ok,
                         const QString &message, const QJsonObject &details);
};

} // namespace hub

Q_DECLARE_METATYPE(hub::ModuleContext)
Q_DECLARE_METATYPE(hub::StopMode)
