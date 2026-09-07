#pragma once

#include "common/Config.h"

#include <QJsonObject>
#include <QObject>

namespace hub {

class ModuleBackend : public QObject {
    Q_OBJECT
public:
    explicit ModuleBackend(ModuleConfig config, QObject *parent = nullptr);
    ~ModuleBackend() override = default;

    QString moduleId() const { return config_.id; }
    ModuleConfig config() const { return config_; }

public slots:
    virtual void start() = 0;
    virtual void stop() = 0;
    virtual void requestSnapshot() = 0;
    virtual void submitCommand(const QJsonObject &command) = 0;

signals:
    void snapshotChanged(const QJsonObject &snapshot);
    void detailEvent(const QJsonObject &event);
    void commandChanged(const QJsonObject &result);

protected:
    QJsonObject envelope(const QString &type, const QJsonObject &payload) const;
    ModuleConfig config_;
    quint64 sequence_ = 0;
};

} // namespace hub
