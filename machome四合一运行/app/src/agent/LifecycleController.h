#pragma once

#include "common/Config.h"

#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QPointer>
#include <QProcess>
#include <QQueue>
#include <QTimer>

#include <memory>

class QLockFile;

namespace hub {

class LifecycleControllerTestPeer;

class LifecycleController final : public QObject {
    Q_OBJECT
public:
    explicit LifecycleController(ModuleConfig config, QObject *parent = nullptr);
    ~LifecycleController() override;

    QJsonObject snapshot() const { return snapshot_; }
    bool handles(const QString &action) const;
    bool transportQuiescent() const;

public slots:
    void start();
    void stop();
    void refresh();
    void execute(const QJsonObject &command);

signals:
    void snapshotChanged(const QJsonObject &snapshot);
    void commandFinished(const QJsonObject &result);
    void logEvent(const QJsonObject &event);

private:
    friend class LifecycleControllerTestPeer;
    struct Step {
        QString program;
        QStringList arguments;
        QString description;
        bool tolerateFailure = false;
        int delayAfterMs = 0;
    };

    void probeNext();
    quint64 beginLaunchdProbe(bool replaceExisting);
    void publishSnapshot();
    void finishCommand(bool ok, const QString &message, const QJsonObject &details = {});
    void startStep();
    void prepareLaunchdSteps(const QString &action);
    void startManagedProcesses();
    void startNextManaged();
    void stopManagedProcesses(bool restarting);
    void stopNextManaged();
    QString domainTarget(const QString &label = {}) const;
    void beginLaunchdVerification();
    void verifyLaunchdOutcome();
    bool launchdOutcomeSatisfied() const;
    void probeManagedProcesses();
    void cancelCommandExecution();
    void retireProcess(QPointer<QProcess> &slot);
    bool acquireOwnerLease();
    bool legacySupervisorAbsent(QString *error, qint64 deadlineEpochMs = 0) const;
    bool validateMutationPreflight(QString *error) const;
    bool validatePlistIdentity(const LaunchdUnit &unit, QString *error) const;
    QList<LaunchdUnit> selectedLaunchdUnits() const;
    void continueAfterPreflight();
    void beginMutationAfterPreflight();

    ModuleConfig config_;
    QTimer probeTimer_;
    QPointer<QProcess> probeProcess_;
    QPointer<QProcess> managedProbeProcess_;
    int probeIndex_ = 0;
    QJsonArray probeUnits_;
    QJsonObject snapshot_;
    quint64 probeGeneration_ = 0;
    quint64 activeProbeGeneration_ = 0;
    quint64 completedProbeGeneration_ = 0;
    quint64 preflightProbeGeneration_ = 0;
    quint64 verificationProbeGeneration_ = 0;

    QQueue<Step> steps_;
    QPointer<QProcess> stepProcess_;
    QList<QPointer<QProcess>> retiringProcesses_;
    QString currentCommandId_;
    QString currentAction_;
    QString currentTargetUnit_;
    QStringList commandNotes_;
    QHash<QString, qint64> preCommandPids_;
    bool commandFailed_ = false;
    bool externalMutationAttempted_ = false;
    bool verifyingLaunchd_ = false;
    bool preflighting_ = false;
    int verificationAttempts_ = 0;

    QList<ManagedProcess> managedQueue_;
    QHash<QString, QProcess *> managed_;
    bool restartAfterStop_ = false;
    QTimer commandDeadline_;
    qint64 commandDeadlineAtMs_ = 0;
    quint64 commandGeneration_ = 0;
    quint64 managedProbeGeneration_ = 0;
    std::unique_ptr<QLockFile> ownerLease_;
    bool ownerLeaseHeld_ = false;
    QString ownerLeaseError_;
};

} // namespace hub
