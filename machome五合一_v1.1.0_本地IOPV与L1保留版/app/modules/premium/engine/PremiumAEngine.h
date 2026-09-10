#pragma once

#include "common/ModuleEngine.h"

#include <QJsonArray>
#include <QElapsedTimer>
#include <QSet>
#include <QTimer>
#include <memory>

class QProcess;

namespace machome::premium::engine {
class CoreServer;

// Owns the complete A-side premium monitor in the premium module thread.
// The UI consumes these signals directly; ports 8421/19195 are compatibility
// outputs for existing B clients only.
class PremiumAEngine final : public hub::IModuleEngine {
    Q_OBJECT
public:
    explicit PremiumAEngine(QObject *parent = nullptr);
    ~PremiumAEngine() override;
    QJsonObject snapshot() const;

public Q_SLOTS:
    void initialize(const hub::ModuleContext &context) override;
    void start() override;
    void stop(hub::StopMode mode = hub::StopMode::Graceful) override;
    void submitCommand(const QString &action, const QJsonObject &arguments,
                       const QString &commandId) override;

private Q_SLOTS:
    void forwardNativeDetail(const QJsonObject &detail);

private:
    bool writeRuntimeConfiguration(QString *error);
    void publishSnapshot();
    void complete(const QString &commandId, bool ok, const QString &message,
                  const QJsonObject &details = {});
    void publishSync();
    void startIopv();
    void stopIopv();
    void startTgwHelper();
    void stopTgwHelper();
    void scheduleTgwRecovery();

    hub::ModuleContext context_;
    std::unique_ptr<CoreServer> core_;
    QProcess *iopv_ = nullptr;
    QTimer iopvRestart_;
    QString iopvState_ = QStringLiteral("disabled");
    QProcess *tgwHelper_ = nullptr;
    QTimer snapshotTimer_;
    QTimer helperRestartTimer_;
    QElapsedTimer helperRestartClock_;
    QList<qint64> helperRestartTimes_;
    bool helperFault_ = false;
    QSet<QString> detailSubscriptions_;
    QString configPath_;
    QString lastError_;
    bool initialized_ = false;
    bool running_ = false;
    QString operatingMode_ = QStringLiteral("work");
    QString tgwHelperState_ = QStringLiteral("disabled");
};
} // namespace machome::premium::engine
