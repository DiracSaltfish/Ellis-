#include "agent/LifecycleController.h"

#include "common/JsonUtil.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLockFile>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSet>
#include <QSettings>
#include <algorithm>
#include <utility>
#include <unistd.h>

namespace hub {
namespace {

QString unquoted(QString value) {
    value = value.trimmed();
    if (value.size() >= 2 && value.front() == u'"' && value.back() == u'"') {
        value = value.mid(1, value.size() - 2);
    }
    return value;
}

QString launchdProgram(const QString &output) {
    static const QRegularExpression pattern(
        QStringLiteral("(?m)^\\s*program = (.+?)\\s*$"));
    const auto match = pattern.match(output);
    return match.hasMatch() ? unquoted(match.captured(1)) : QString{};
}

QStringList launchdArguments(const QString &output) {
    QStringList result;
    const QStringList lines = output.split(u'\n');
    bool inside = false;
    for (QString line : lines) {
        const QString trimmed = line.trimmed();
        if (!inside) {
            if (trimmed == QStringLiteral("arguments = {")) inside = true;
            continue;
        }
        if (trimmed == QStringLiteral("}")) break;
        if (trimmed.isEmpty()) continue;
        static const QRegularExpression indexed(QStringLiteral("^\\d+\\s*=\\s*(.*)$"));
        const auto match = indexed.match(trimmed);
        result.append(unquoted(match.hasMatch() ? match.captured(1) : trimmed));
    }
    return result;
}

QString sha256File(const QString &path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    QCryptographicHash hash(QCryptographicHash::Sha256);
    if (!hash.addData(&file)) return {};
    return QString::fromLatin1(hash.result().toHex());
}

bool expectedArtifactsMatch(const LaunchdUnit &unit, QJsonArray *observed = nullptr) {
    bool allMatch = !unit.expectedArtifacts.isEmpty();
    QJsonArray values;
    for (const auto &artifact : unit.expectedArtifacts) {
        const QString actual = sha256File(artifact.path);
        const bool matches = !actual.isEmpty() && actual == artifact.sha256;
        allMatch = allMatch && matches;
        values.append(QJsonObject{{QStringLiteral("path"), artifact.path},
                                  {QStringLiteral("expected_sha256"), artifact.sha256},
                                  {QStringLiteral("actual_sha256"), actual},
                                  {QStringLiteral("matches"), matches}});
    }
    if (observed) *observed = values;
    return allMatch;
}

} // namespace

LifecycleController::LifecycleController(ModuleConfig config, QObject *parent)
    : QObject(parent), config_(std::move(config)) {
    probeTimer_.setInterval(config_.settings.value(QStringLiteral("process_probe_ms")).toInt(5000));
    probeTimer_.setTimerType(Qt::CoarseTimer);
    connect(&probeTimer_, &QTimer::timeout, this, &LifecycleController::refresh);

    commandDeadline_.setSingleShot(true);
    connect(&commandDeadline_, &QTimer::timeout, this, [this] {
        ++commandGeneration_;
        steps_.clear();
        managedQueue_.clear();
        restartAfterStop_ = false;
        verifyingLaunchd_ = false;
        preflighting_ = false;
        retireProcess(probeProcess_);
        retireProcess(stepProcess_);
        QJsonObject details;
        if (externalMutationAttempted_) {
            details.insert(QStringLiteral("outcome_uncertain"), true);
            details.insert(QStringLiteral("reconciliation_required"), true);
            details.insert(QStringLiteral("tracking_late_result"), false);
        }
        finishCommand(false, QStringLiteral("生命周期命令超过截止时间，已停止继续执行；请先刷新状态"), details);
    });
}

LifecycleController::~LifecycleController() {
    for (auto *process : std::as_const(managed_)) {
        if (process && process->state() != QProcess::NotRunning) {
            process->disconnect(this);
            process->setParent(nullptr);
        }
    }
}

bool LifecycleController::handles(const QString &action) const {
    static const QSet<QString> actions{
        QStringLiteral("start_service"), QStringLiteral("stop_service"),
        QStringLiteral("restart_service"), QStringLiteral("open_legacy_ui")};
    return actions.contains(action);
}

bool LifecycleController::transportQuiescent() const {
    if (!currentCommandId_.isEmpty()) return false;
    const auto running = [](const QPointer<QProcess> &process) {
        return process && process->state() != QProcess::NotRunning;
    };
    if (running(probeProcess_) || running(managedProbeProcess_)
        || running(stepProcess_)) return false;
    for (const auto &process : retiringProcesses_) {
        if (running(process)) return false;
    }
    return true;
}

void LifecycleController::retireProcess(QPointer<QProcess> &slot) {
    QProcess *process = slot.data();
    slot.clear();
    if (!process) return;
    process->disconnect(this);
    if (process->state() == QProcess::NotRunning) {
        process->deleteLater();
        return;
    }
    retiringProcesses_.append(process);
    connect(process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this, [this, process] {
        retiringProcesses_.removeAll(process);
        process->deleteLater();
    });
    process->terminate();
    QTimer::singleShot(250, process, [process] {
        if (process->state() != QProcess::NotRunning) process->kill();
    });
}

QString LifecycleController::domainTarget(const QString &label) const {
    const QString base = QStringLiteral("gui/%1").arg(static_cast<qulonglong>(::getuid()));
    return label.isEmpty() ? base : base + u'/' + label;
}

void LifecycleController::start() {
    if (config_.ownership == QStringLiteral("owner")) acquireOwnerLease();
    snapshot_ = {{QStringLiteral("ownership"), config_.ownership},
                 {QStringLiteral("control_enabled"), config_.controlEnabled},
                 {QStringLiteral("lifecycle"), QStringLiteral("unknown")},
                 {QStringLiteral("owner_lease"),
                  QJsonObject{{QStringLiteral("required"),
                               config_.ownership == QStringLiteral("owner")},
                              {QStringLiteral("held"), ownerLeaseHeld_},
                              {QStringLiteral("error"), ownerLeaseError_}}}};
    refresh();
    probeTimer_.start();
}

void LifecycleController::stop() {
    probeTimer_.stop();
    cancelCommandExecution();
    QList<QProcess *> processes;
    const auto collect = [&processes](QPointer<QProcess> &slot) {
        if (slot && !processes.contains(slot.data())) processes.append(slot.data());
        slot.clear();
    };
    collect(probeProcess_);
    collect(managedProbeProcess_);
    collect(stepProcess_);
    for (const auto &process : std::as_const(retiringProcesses_)) {
        if (process && !processes.contains(process.data())) processes.append(process.data());
    }
    retiringProcesses_.clear();
    for (QProcess *process : std::as_const(processes)) {
        process->disconnect(this);
        if (process->state() != QProcess::NotRunning) {
            process->terminate();
            if (!process->waitForFinished(250)) {
                process->kill();
                process->waitForFinished(1000);
            }
        }
        if (process->state() == QProcess::NotRunning) {
            delete process;
        } else {
            // A QProcess destructor can synchronously kill a still-running
            // child.  Shutdown must never block indefinitely or destroy an
            // active process wrapper after the bounded terminate/kill waits.
            // These are Hub-owned helper probes (never business processes),
            // so detach the rare stubborn wrapper and let process exit reclaim
            // it instead.  The OS reaps any remaining helper when Agent exits.
            qCritical().noquote()
                << "辅助探针拒绝在关停期限内退出，已从控制器解绑："
                << process->program();
            process->setParent(nullptr);
            QObject::connect(process,
                             qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
                             process,
                             &QObject::deleteLater);
        }
    }
    ownerLease_.reset();
    ownerLeaseHeld_ = false;
}

bool LifecycleController::acquireOwnerLease() {
    ownerLeaseHeld_ = false;
    ownerLeaseError_.clear();
    const QJsonObject lease = config_.settings.value(QStringLiteral("owner_lease")).toObject();
    const QString lockPath = AppConfig::expandPath(
        lease.value(QStringLiteral("lock_path")).toString());
    const QString markerPath = AppConfig::expandPath(
        lease.value(QStringLiteral("handoff_marker")).toString());
    const auto insecure = QFileDevice::ReadGroup | QFileDevice::WriteGroup
        | QFileDevice::ReadOther | QFileDevice::WriteOther;
    QDir lockDirectory(QFileInfo(lockPath).absolutePath());
    if ((!lockDirectory.exists() && !lockDirectory.mkpath(QStringLiteral(".")))) {
        ownerLeaseError_ = QStringLiteral("无法创建 owner lease 目录");
        return false;
    }
    const QString statePath = lockPath + QStringLiteral(".state.json");
    QJsonObject state;
    QFile stateFile(statePath);
    if (QFileInfo(statePath).exists() && QFileInfo(statePath).isFile()
        && !(QFileInfo(statePath).permissions() & insecure)
        && stateFile.open(QIODevice::ReadOnly)) {
        const QJsonDocument stateDocument = QJsonDocument::fromJson(stateFile.readAll());
        state = stateDocument.object();
    }
    const bool recoveredGeneration = state.value(QStringLiteral("schema_version")).toInt() == 1
        && state.value(QStringLiteral("module_id")).toString() == config_.id
        && state.value(QStringLiteral("owner_id")).toString()
               == lease.value(QStringLiteral("owner_id")).toString()
        && state.value(QStringLiteral("generation")).toInteger()
               == lease.value(QStringLiteral("generation")).toInteger()
        && state.value(QStringLiteral("active")).toBool(false);

    QJsonObject marker;
    QString markerNonceHash;
    if (!recoveredGeneration) {
        QFileInfo markerInfo(markerPath);
        if (!markerInfo.exists() || (markerInfo.permissions() & insecure)) {
            ownerLeaseError_ = QStringLiteral("缺少仅当前用户可读的新世代 owner 交接标记：%1")
                                   .arg(markerPath);
            return false;
        }
        QFile markerFile(markerPath);
        if (!markerFile.open(QIODevice::ReadOnly)) {
            ownerLeaseError_ = QStringLiteral("无法读取 owner 交接标记：%1").arg(markerFile.errorString());
            return false;
        }
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(markerFile.readAll(), &parseError);
        marker = document.object();
        const QString token = marker.value(QStringLiteral("handoff_token")).toString();
        const QString nonce = marker.value(QStringLiteral("nonce")).toString();
        const QString tokenHash = QString::fromLatin1(
            QCryptographicHash::hash(token.toUtf8(), QCryptographicHash::Sha256).toHex());
        markerNonceHash = QString::fromLatin1(
            QCryptographicHash::hash(nonce.toUtf8(), QCryptographicHash::Sha256).toHex());
        const QDateTime issuedAt = QDateTime::fromString(
            marker.value(QStringLiteral("issued_at")).toString(), Qt::ISODateWithMs);
        const QDateTime expiresAt = QDateTime::fromString(
            marker.value(QStringLiteral("expires_at")).toString(), Qt::ISODateWithMs);
        const QDateTime now = QDateTime::currentDateTimeUtc();
        const bool timeValid = issuedAt.isValid() && expiresAt.isValid()
            && issuedAt.toUTC() <= now.addSecs(30) && expiresAt.toUTC() > now
            && issuedAt.secsTo(expiresAt) > 0 && issuedAt.secsTo(expiresAt) <= 15 * 60;
        const bool markerValid = parseError.error == QJsonParseError::NoError && document.isObject()
            && marker.value(QStringLiteral("schema_version")).toInt() == 1
            && marker.value(QStringLiteral("module_id")).toString() == config_.id
            && marker.value(QStringLiteral("owner_id")).toString()
                   == lease.value(QStringLiteral("owner_id")).toString()
            && marker.value(QStringLiteral("generation")).toInteger()
                   == lease.value(QStringLiteral("generation")).toInteger()
            && marker.value(QStringLiteral("previous_owner_stopped")).toBool(false)
            && nonce.size() >= 16 && !token.isEmpty() && timeValid
            && tokenHash == lease.value(QStringLiteral("handoff_token_sha256")).toString().toLower();
        if (!markerValid) {
            ownerLeaseError_ = QStringLiteral("owner 交接标记的模块/世代/令牌/nonce/有效期不匹配，拒绝接管");
            return false;
        }
    }
    QString supervisorError;
    if (!legacySupervisorAbsent(&supervisorError)) {
        ownerLeaseError_ = supervisorError;
        return false;
    }
    ownerLease_ = std::make_unique<QLockFile>(lockPath);
    ownerLease_->setStaleLockTime(30'000);
    if (!ownerLease_->tryLock(0)) {
        ownerLeaseError_ = QStringLiteral("owner lease 已被其他监管器持有，拒绝双重接管");
        ownerLease_.reset();
        return false;
    }
    if (!recoveredGeneration) {
        const QJsonObject newState{{QStringLiteral("schema_version"), 1},
                                   {QStringLiteral("module_id"), config_.id},
                                   {QStringLiteral("owner_id"), lease.value(QStringLiteral("owner_id"))},
                                   {QStringLiteral("generation"), lease.value(QStringLiteral("generation"))},
                                   {QStringLiteral("marker_nonce_sha256"), markerNonceHash},
                                   {QStringLiteral("active"), true},
                                   {QStringLiteral("activated_at"), utcNow()}};
        QSaveFile output(statePath);
        const QByteArray bytes = QJsonDocument(newState).toJson(QJsonDocument::Indented);
        if (!output.open(QIODevice::WriteOnly) || output.write(bytes) != bytes.size()
            || !output.commit() || !QFile::setPermissions(
                   statePath, QFileDevice::ReadOwner | QFileDevice::WriteOwner)
            || !QFile::remove(markerPath)) {
            ownerLeaseError_ = QStringLiteral("无法原子记录/消费 owner 交接标记，已拒绝接管");
            ownerLease_.reset();
            QFile::remove(statePath);
            return false;
        }
    }
    ownerLeaseHeld_ = true;
    QFile::setPermissions(lockPath, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    return true;
}

bool LifecycleController::legacySupervisorAbsent(QString *error, qint64 deadlineEpochMs) const {
    const QJsonObject lease = config_.settings.value(QStringLiteral("owner_lease")).toObject();
    const auto remaining = [deadlineEpochMs](int maximum) {
        if (deadlineEpochMs <= 0) return maximum;
        const qint64 value = deadlineEpochMs - QDateTime::currentMSecsSinceEpoch();
        return static_cast<int>(qBound<qint64>(0LL, value,
                                               static_cast<qint64>(maximum)));
    };
    QStringList processPrefixes;
    for (const auto &value : lease.value(QStringLiteral("previous_owner_processes")).toArray()) {
        if (value.isString()) processPrefixes.append(value.toString().trimmed());
    }
    if (!processPrefixes.isEmpty()) {
        QProcess ps;
        ps.start(QStringLiteral("/bin/ps"),
                 {QStringLiteral("-axo"), QStringLiteral("pid=,command=")});
        const int startBudget = remaining(500);
        const bool started = startBudget > 0 && ps.waitForStarted(startBudget);
        const int finishBudget = remaining(2000);
        if (!started || finishBudget <= 0 || !ps.waitForFinished(finishBudget)) {
            ps.kill();
            ps.waitForFinished(250);
            if (error) *error = QStringLiteral("无法核对旧监管器进程，按不安全处理");
            return false;
        }
        if (ps.exitStatus() != QProcess::NormalExit || ps.exitCode() != 0) {
            if (error) *error = QStringLiteral("无法权威核对旧监管器进程，ps 返回异常");
            return false;
        }
        const QStringList rows = QString::fromUtf8(ps.readAllStandardOutput())
                                     .split(u'\n', Qt::SkipEmptyParts);
        const QRegularExpression rowPattern(QStringLiteral("^\\s*(\\d+)\\s+(.+)$"));
        for (const auto &row : rows) {
            const auto match = rowPattern.match(row);
            if (!match.hasMatch() || match.captured(1).toLongLong() == QCoreApplication::applicationPid()) continue;
            const QString command = match.captured(2).trimmed();
            for (const auto &prefix : processPrefixes) {
                if (!prefix.isEmpty() && (command == prefix || command.startsWith(prefix + u' '))) {
                    if (error) *error = QStringLiteral("旧监管器仍在运行（PID %1）：%2")
                                            .arg(match.captured(1), prefix);
                    return false;
                }
            }
        }
    }
    for (const auto &value : lease.value(QStringLiteral("previous_owner_labels")).toArray()) {
        const QString label = value.toString().trimmed();
        if (label.isEmpty()) continue;
        QProcess launchctl;
        launchctl.start(QStringLiteral("/bin/launchctl"),
                        {QStringLiteral("print"), domainTarget(label)});
        const int startBudget = remaining(500);
        const bool started = startBudget > 0 && launchctl.waitForStarted(startBudget);
        const int finishBudget = remaining(1500);
        if (!started || finishBudget <= 0 || !launchctl.waitForFinished(finishBudget)) {
            launchctl.kill();
            launchctl.waitForFinished(250);
            if (error) *error = QStringLiteral("无法核对旧监管 label %1，按不安全处理").arg(label);
            return false;
        }
        const QString output = QString::fromUtf8(launchctl.readAllStandardOutput());
        const QString standardError = QString::fromUtf8(launchctl.readAllStandardError());
        if (launchctl.exitStatus() == QProcess::NormalExit && launchctl.exitCode() == 0) {
            if (error) *error = QStringLiteral("旧监管 launchd label 仍已加载（无论当前运行态）：%1").arg(label);
            return false;
        }
        const QString combined = output + u'\n' + standardError;
        const bool authoritativelyAbsent = launchctl.exitStatus() == QProcess::NormalExit
            && (combined.contains(QStringLiteral("Could not find service"), Qt::CaseInsensitive)
                || combined.contains(QStringLiteral("Could not find specified service"),
                                     Qt::CaseInsensitive));
        if (!authoritativelyAbsent) {
            if (error) {
                *error = QStringLiteral("旧监管 launchd label %1 无法得到权威 not-found，按不安全处理")
                             .arg(label);
            }
            return false;
        }
    }
    return true;
}

void LifecycleController::refresh() {
    if (config_.launchdUnits.isEmpty()) {
        probeManagedProcesses();
        return;
    }
    beginLaunchdProbe(false);
}

quint64 LifecycleController::beginLaunchdProbe(bool replaceExisting) {
    if (config_.launchdUnits.isEmpty()) return 0;
    if (probeProcess_) {
        if (!replaceExisting) return 0;
        retireProcess(probeProcess_);
    }
    activeProbeGeneration_ = ++probeGeneration_;
    probeIndex_ = 0;
    probeUnits_ = {};
    probeNext();
    return activeProbeGeneration_;
}

void LifecycleController::probeNext() {
    const quint64 generation = activeProbeGeneration_;
    if (probeIndex_ >= config_.launchdUnits.size()) {
        if (generation != activeProbeGeneration_) return;
        int required = 0;
        int requiredRunning = 0;
        int running = 0;
        bool identityConflict = false;
        for (const auto &value : probeUnits_) {
            const auto object = value.toObject();
            const bool active = object.value(QStringLiteral("running")).toBool();
            if (object.value(QStringLiteral("loaded")).toBool(false)
                && !object.value(QStringLiteral("identity_verified")).toBool(true)) {
                identityConflict = true;
            }
            if (active) ++running;
            if (object.value(QStringLiteral("required")).toBool()) {
                ++required;
                if (active) ++requiredRunning;
            }
        }
        snapshot_.insert(QStringLiteral("units"), probeUnits_);
        snapshot_.insert(QStringLiteral("running_count"), running);
        snapshot_.insert(QStringLiteral("total_count"), config_.launchdUnits.size());
        snapshot_.insert(QStringLiteral("lifecycle"),
                         identityConflict ? QStringLiteral("failed")
                         : required > 0 && requiredRunning == required ? QStringLiteral("running")
                         : running > 0 ? QStringLiteral("degraded") : QStringLiteral("stopped"));
        snapshot_.insert(QStringLiteral("identity_conflict"), identityConflict);
        completedProbeGeneration_ = generation;
        publishSnapshot();
        continueAfterPreflight();
        verifyLaunchdOutcome();
        return;
    }

    const auto unit = config_.launchdUnits.at(probeIndex_++);
    auto *process = new QProcess(this);
    probeProcess_ = process;
    process->setProgram(QStringLiteral("/bin/launchctl"));
    process->setArguments({QStringLiteral("print"), domainTarget(unit.label)});
    connect(process, &QProcess::finished, this,
            [this, process, unit, generation](int exitCode, QProcess::ExitStatus exitStatus) {
        if (generation != activeProbeGeneration_ || probeProcess_ != process) {
            process->deleteLater();
            return;
        }
        const QString output = QString::fromUtf8(process->readAllStandardOutput());
        const QString standardError = QString::fromUtf8(process->readAllStandardError()).trimmed();
        const bool loaded = exitStatus == QProcess::NormalExit && exitCode == 0;
        const bool authoritativeAbsent = !loaded && exitStatus == QProcess::NormalExit
            && (standardError.contains(QStringLiteral("Could not find service"), Qt::CaseInsensitive)
                || standardError.contains(QStringLiteral("Could not find specified service"), Qt::CaseInsensitive)
                || output.contains(QStringLiteral("Could not find service"), Qt::CaseInsensitive));
        const bool processRunning = loaded && output.contains(QStringLiteral("state = running"));
        qint64 pid = 0;
        const QRegularExpression expression(QStringLiteral("\\bpid = (\\d+)"));
        const auto match = expression.match(output);
        if (match.hasMatch()) pid = match.captured(1).toLongLong();
        const bool identityConfigured = !unit.expectedProgram.isEmpty();
        const QString actualProgram = launchdProgram(output);
        const QStringList actualArguments = launchdArguments(output);
        QStringList expectedArguments{unit.expectedProgram};
        expectedArguments.append(unit.expectedArguments);
        const bool programMatches = !identityConfigured || actualProgram == unit.expectedProgram;
        const bool argumentsMatch = !identityConfigured || actualArguments == expectedArguments;
        const QString actualHash = identityConfigured ? sha256File(unit.expectedProgram) : QString{};
        QJsonArray observedArtifacts;
        const bool artifactMatches = !identityConfigured
            || expectedArtifactsMatch(unit, &observedArtifacts);
        const bool identityVerified = programMatches && argumentsMatch && artifactMatches;
        const bool running = processRunning && identityVerified;
        probeUnits_.append(QJsonObject{{QStringLiteral("id"), unit.id},
                                       {QStringLiteral("label"), unit.label},
                                       {QStringLiteral("loaded"), loaded},
                                       {QStringLiteral("probe_authoritative"), loaded || authoritativeAbsent},
                                       {QStringLiteral("probe_error"),
                                        loaded || authoritativeAbsent ? QString{} : standardError.left(300)},
                                       {QStringLiteral("process_running"), processRunning},
                                       {QStringLiteral("running"), running},
                                       {QStringLiteral("pid"), pid},
                                       {QStringLiteral("required"), unit.required},
                                       {QStringLiteral("identity_gate_configured"), identityConfigured},
                                       {QStringLiteral("identity_verified"), identityVerified},
                                       {QStringLiteral("actual_program"), actualProgram},
                                       {QStringLiteral("artifact_sha256"), actualHash},
                                       {QStringLiteral("artifacts"), observedArtifacts},
                                       {QStringLiteral("program_matches"), programMatches},
                                       {QStringLiteral("arguments_match"), argumentsMatch},
                                       {QStringLiteral("artifact_matches"), artifactMatches}});
        probeProcess_.clear();
        process->deleteLater();
        probeNext();
    });
    process->start();
    QTimer::singleShot(2000, this, [this, process, unit, generation] {
        if (generation != activeProbeGeneration_ || probeProcess_ != process) return;
        process->disconnect(this);
        probeUnits_.append(QJsonObject{{QStringLiteral("id"), unit.id},
                                       {QStringLiteral("label"), unit.label},
                                       {QStringLiteral("loaded"), false},
                                       {QStringLiteral("probe_authoritative"), false},
                                       {QStringLiteral("process_running"), false},
                                       {QStringLiteral("running"), false},
                                       {QStringLiteral("pid"), 0},
                                       {QStringLiteral("required"), unit.required},
                                       {QStringLiteral("identity_gate_configured"),
                                        !unit.expectedProgram.isEmpty()},
                                       {QStringLiteral("identity_verified"), false},
                                       {QStringLiteral("probe_error"),
                                        QStringLiteral("launchctl print 超时或无法启动")}});
        retireProcess(probeProcess_);
        probeNext();
    });
}

void LifecycleController::publishSnapshot() {
    snapshot_.insert(QStringLiteral("observed_at"), utcNow());
    snapshot_.insert(QStringLiteral("ownership"), config_.ownership);
    snapshot_.insert(QStringLiteral("control_enabled"), config_.controlEnabled);
    emit snapshotChanged(snapshot_);
}

void LifecycleController::probeManagedProcesses() {
    if (managedProbeProcess_ || config_.managedProcesses.isEmpty()) {
        if (config_.managedProcesses.isEmpty()) {
            snapshot_.insert(QStringLiteral("processes"), QJsonArray{});
            snapshot_.insert(QStringLiteral("running_count"), 0);
            snapshot_.insert(QStringLiteral("total_count"), 0);
            snapshot_.insert(QStringLiteral("lifecycle"), QStringLiteral("unknown"));
            publishSnapshot();
        }
        return;
    }
    const quint64 generation = ++managedProbeGeneration_;
    auto *process = new QProcess(this);
    managedProbeProcess_ = process;
    process->setProgram(QStringLiteral("/bin/ps"));
    process->setArguments({QStringLiteral("-axo"), QStringLiteral("pid=,ppid=,command=")});
    connect(process, &QProcess::finished, this,
            [this, process, generation](int exitCode, QProcess::ExitStatus exitStatus) {
        if (generation != managedProbeGeneration_) {
            process->deleteLater();
            return;
        }
        const QString output = QString::fromUtf8(process->readAllStandardOutput());
        const bool probeOk = exitStatus == QProcess::NormalExit && exitCode == 0;
        QJsonArray processes;
        int running = 0;
        int required = 0;
        int requiredRunning = 0;
        const QStringList lines = output.split(u'\n', Qt::SkipEmptyParts);
        const QRegularExpression rowPattern(QStringLiteral("^\\s*(\\d+)\\s+(\\d+)\\s+(.+)$"));
        for (const auto &definition : config_.managedProcesses) {
            if (definition.required) ++required;
            qint64 pid = 0;
            qint64 parentPid = 0;
            QString matchedCommand;
            if (probeOk) {
                for (const auto &line : lines) {
                    const auto match = rowPattern.match(line);
                    if (!match.hasMatch()) continue;
                    const QString command = match.captured(3).trimmed();
                    const bool programMatches = command == definition.program
                        || command.startsWith(definition.program + u' ');
                    if (!programMatches) continue;
                    int position = definition.program.size();
                    bool argumentsMatch = true;
                    for (const auto &argument : definition.arguments) {
                        position = command.indexOf(argument, position, Qt::CaseSensitive);
                        if (position < 0) {
                            argumentsMatch = false;
                            break;
                        }
                        position += argument.size();
                    }
                    if (!argumentsMatch) continue;
                    pid = match.captured(1).toLongLong();
                    parentPid = match.captured(2).toLongLong();
                    matchedCommand = command.left(512);
                    break;
                }
            }
            const bool active = pid > 0;
            if (active) ++running;
            if (active && definition.required) ++requiredRunning;
            processes.append(QJsonObject{
                {QStringLiteral("id"), definition.id},
                {QStringLiteral("running"), active},
                {QStringLiteral("pid"), pid},
                {QStringLiteral("parent_pid"), parentPid},
                {QStringLiteral("required"), definition.required},
                {QStringLiteral("identity_verified"), active},
                {QStringLiteral("observation_source"), QStringLiteral("system_ps")},
                {QStringLiteral("command"), matchedCommand}});
        }
        snapshot_.insert(QStringLiteral("processes"), processes);
        snapshot_.insert(QStringLiteral("running_count"), running);
        snapshot_.insert(QStringLiteral("total_count"), config_.managedProcesses.size());
        snapshot_.insert(QStringLiteral("probe_ok"), probeOk);
        if (!probeOk) {
            snapshot_.insert(QStringLiteral("lifecycle"), QStringLiteral("unknown"));
            snapshot_.insert(QStringLiteral("probe_error"),
                             QString::fromUtf8(process->readAllStandardError()).left(300));
        } else {
            snapshot_.insert(QStringLiteral("lifecycle"),
                             required > 0 && requiredRunning == required ? QStringLiteral("running")
                             : running > 0 ? QStringLiteral("degraded") : QStringLiteral("stopped"));
        }
        managedProbeProcess_.clear();
        process->deleteLater();
        publishSnapshot();
    });
    process->start();
    QTimer::singleShot(2000, this, [this, process, generation] {
        if (generation == managedProbeGeneration_ && managedProbeProcess_ == process
            && process->state() != QProcess::NotRunning) {
            process->terminate();
        }
    });
}

void LifecycleController::execute(const QJsonObject &command) {
    const QString action = command.value(QStringLiteral("action")).toString();
    const QString commandId = command.value(QStringLiteral("command_id")).toString();
    const QString targetUnit = command.value(QStringLiteral("arguments")).toObject()
                                   .value(QStringLiteral("target_unit")).toString();
    if (!handles(action)) {
        return;
    }
    if (!currentCommandId_.isEmpty()) {
        emit commandFinished(QJsonObject{{QStringLiteral("command_id"), commandId},
                                         {QStringLiteral("module_id"), config_.id},
                                         {QStringLiteral("action"), action},
                                         {QStringLiteral("state"), QStringLiteral("failed")},
                                         {QStringLiteral("message"), QStringLiteral("本模块已有生命周期命令执行中")},
                                         {QStringLiteral("finished_at"), utcNow()}});
        return;
    }
    if (action == QStringLiteral("open_legacy_ui")) {
        if (config_.ownership == QStringLiteral("owner")
            || !config_.controlEnabled
            || !config_.settings.value(QStringLiteral("legacy_ui_view_only")).toBool(false)) {
            emit commandFinished(QJsonObject{
                {QStringLiteral("command_id"), commandId},
                {QStringLiteral("module_id"), config_.id},
                {QStringLiteral("action"), action},
                {QStringLiteral("state"), QStringLiteral("failed")},
                {QStringLiteral("message"),
                 QStringLiteral("旧 UI 会自启原有监管器；owner 阶段已硬禁用，请使用四合一内建二级页")},
                {QStringLiteral("finished_at"), utcNow()}});
            return;
        }
        const QString path = AppConfig::expandPath(config_.settings.value(QStringLiteral("legacy_app")).toString());
        const bool ok = !path.isEmpty() && QFileInfo::exists(path) &&
                        QProcess::startDetached(QStringLiteral("/usr/bin/open"), {path});
        emit commandFinished(QJsonObject{{QStringLiteral("command_id"), commandId},
                                         {QStringLiteral("module_id"), config_.id},
                                         {QStringLiteral("action"), action},
                                         {QStringLiteral("state"), ok ? QStringLiteral("succeeded") : QStringLiteral("failed")},
                                         {QStringLiteral("message"), ok ? QStringLiteral("旧界面已打开") : QStringLiteral("旧界面路径不存在或无法打开")},
                                         {QStringLiteral("finished_at"), utcNow()}});
        return;
    }
    if (!config_.controlEnabled || config_.ownership != QStringLiteral("owner")) {
        emit commandFinished(QJsonObject{{QStringLiteral("command_id"), commandId},
                                         {QStringLiteral("module_id"), config_.id},
                                         {QStringLiteral("action"), action},
                                         {QStringLiteral("state"), QStringLiteral("failed")},
                                         {QStringLiteral("message"), QStringLiteral("服务级控制仅在 control_enabled=true 且 ownership=owner 时允许")},
                                         {QStringLiteral("finished_at"), utcNow()}});
        return;
    }
    if (!ownerLeaseHeld_) {
        emit commandFinished(QJsonObject{{QStringLiteral("command_id"), commandId},
                                         {QStringLiteral("module_id"), config_.id},
                                         {QStringLiteral("action"), action},
                                         {QStringLiteral("state"), QStringLiteral("failed")},
                                         {QStringLiteral("message"),
                                          QStringLiteral("owner lease/显式交接未通过，未执行任何生命周期变更：%1")
                                              .arg(ownerLeaseError_)},
                                         {QStringLiteral("finished_at"), utcNow()}});
        return;
    }
    if (!targetUnit.isEmpty()) {
        bool configuredUnit = false;
        for (const auto &unit : config_.launchdUnits) {
            if (unit.id == targetUnit) {
                configuredUnit = true;
                break;
            }
        }
        if (config_.id != QStringLiteral("upload") || targetUnit != targetUnit.trimmed()
            || !configuredUnit) {
            emit commandFinished(QJsonObject{
                {QStringLiteral("command_id"), commandId},
                {QStringLiteral("module_id"), config_.id},
                {QStringLiteral("action"), action},
                {QStringLiteral("state"), QStringLiteral("failed")},
                {QStringLiteral("message"),
                 QStringLiteral("未知 target_unit；未执行任何生命周期变更")},
                {QStringLiteral("finished_at"), utcNow()}});
            return;
        }
    }
    currentCommandId_ = commandId;
    ++commandGeneration_;
    currentAction_ = action;
    currentTargetUnit_ = targetUnit;
    commandNotes_.clear();
    preCommandPids_.clear();
    commandFailed_ = false;
    externalMutationAttempted_ = false;
    verifyingLaunchd_ = false;
    preflighting_ = false;
    verificationAttempts_ = 0;
    const int deadlineMs = command.value(QStringLiteral("deadline_ms")).toInt(45000);
    commandDeadlineAtMs_ = QDateTime::currentMSecsSinceEpoch() + deadlineMs;
    commandDeadline_.start(deadlineMs);

    QString supervisorError;
    if (!legacySupervisorAbsent(&supervisorError, commandDeadlineAtMs_)) {
        ownerLeaseError_ = supervisorError;
        ownerLeaseHeld_ = false;
        ownerLease_.reset();
        snapshot_.insert(QStringLiteral("owner_lease"),
                         QJsonObject{{QStringLiteral("required"), true},
                                     {QStringLiteral("held"), false},
                                     {QStringLiteral("error"), ownerLeaseError_}});
        publishSnapshot();
        finishCommand(false,
                      QStringLiteral("旧监管器未权威卸载，已释放 owner lease 并拒绝变更：%1")
                          .arg(ownerLeaseError_),
                      {{QStringLiteral("outcome_uncertain"), false},
                       {QStringLiteral("preflight_failed"), true}});
        return;
    }
    if (QDateTime::currentMSecsSinceEpoch() >= commandDeadlineAtMs_) {
        finishCommand(false, QStringLiteral("命令在旧监管器核对阶段已超时，未执行任何变更"),
                      {{QStringLiteral("outcome_uncertain"), false},
                       {QStringLiteral("deadline_expired_before_mutation"), true}});
        return;
    }
    preflighting_ = true;
    snapshot_.insert(QStringLiteral("lifecycle"),
                     action == QStringLiteral("stop_service") ? QStringLiteral("stopping")
                                                               : QStringLiteral("starting"));
    publishSnapshot();
    preflightProbeGeneration_ = beginLaunchdProbe(true);
}

bool LifecycleController::validatePlistIdentity(const LaunchdUnit &unit, QString *error) const {
    const QFileInfo info(unit.plistPath);
    const auto insecure = QFileDevice::WriteGroup | QFileDevice::WriteOther;
    if (!info.exists() || !info.isFile() || (info.permissions() & insecure)) {
        if (error) *error = QStringLiteral("%1 plist 不存在或可被其他用户篡改").arg(unit.id);
        return false;
    }
    QSettings plist(unit.plistPath, QSettings::NativeFormat);
    const QString label = plist.value(QStringLiteral("Label")).toString();
    QString program = plist.value(QStringLiteral("Program")).toString();
    QStringList arguments = plist.value(QStringLiteral("ProgramArguments")).toStringList();
    if (program.isEmpty() && !arguments.isEmpty()) program = arguments.first();
    if (!arguments.isEmpty() && arguments.first() == program) arguments.removeFirst();
    const bool ok = plist.status() == QSettings::NoError && label == unit.label
        && program == unit.expectedProgram && arguments == unit.expectedArguments
        && expectedArtifactsMatch(unit);
    if (!ok && error) {
        *error = QStringLiteral("%1 plist Label/ProgramArguments/artifact SHA-256 与 owner 配置不一致")
                     .arg(unit.id);
    }
    return ok;
}

QList<LaunchdUnit> LifecycleController::selectedLaunchdUnits() const {
    if (currentTargetUnit_.isEmpty()) return config_.launchdUnits;
    for (const auto &unit : config_.launchdUnits) {
        if (unit.id == currentTargetUnit_) return {unit};
    }
    return {};
}

bool LifecycleController::validateMutationPreflight(QString *error) const {
    QHash<QString, QJsonObject> observed;
    for (const auto &value : probeUnits_) {
        const auto object = value.toObject();
        observed.insert(object.value(QStringLiteral("id")).toString(), object);
    }
    for (const auto &unit : selectedLaunchdUnits()) {
        const QJsonObject state = observed.value(unit.id);
        if (!state.value(QStringLiteral("probe_authoritative")).toBool(false)) {
            if (error) *error = QStringLiteral("%1 的 launchd 身份探针无法得到权威结论，已拒绝所有变更").arg(unit.id);
            return false;
        }
        const bool loaded = state.value(QStringLiteral("loaded")).toBool(false);
        if (loaded) {
            if (!state.value(QStringLiteral("identity_verified")).toBool(false)) {
                if (error) {
                    *error = QStringLiteral("%1 当前 label 指向非预期 program/arguments/artifact，为避免错杀已拒绝")
                                 .arg(unit.id);
                }
                return false;
            }
            continue;
        }
        if (currentAction_ == QStringLiteral("stop_service")) continue;
        if (currentAction_ == QStringLiteral("restart_service")
            && (unit.required || !currentTargetUnit_.isEmpty())) {
            if (error) *error = QStringLiteral("必要 unit %1 未加载，不能执行语义不明的 restart").arg(unit.id);
            return false;
        }
        if (currentAction_ == QStringLiteral("start_service")
            && !validatePlistIdentity(unit, error)) return false;
    }
    return true;
}

void LifecycleController::continueAfterPreflight() {
    if (!preflighting_ || currentCommandId_.isEmpty() || probeProcess_
        || completedProbeGeneration_ != preflightProbeGeneration_) return;
    preflighting_ = false;
    if (QDateTime::currentMSecsSinceEpoch() >= commandDeadlineAtMs_) {
        finishCommand(false, QStringLiteral("命令在 launchd 身份预检阶段已超时，未执行任何变更"),
                      {{QStringLiteral("outcome_uncertain"), false},
                       {QStringLiteral("deadline_expired_before_mutation"), true},
                       {QStringLiteral("verified_snapshot"), snapshot_}});
        return;
    }
    QString supervisorError;
    if (!legacySupervisorAbsent(&supervisorError, commandDeadlineAtMs_)) {
        ownerLeaseError_ = supervisorError;
        ownerLeaseHeld_ = false;
        ownerLease_.reset();
        snapshot_.insert(QStringLiteral("owner_lease"),
                         QJsonObject{{QStringLiteral("required"), true},
                                     {QStringLiteral("held"), false},
                                     {QStringLiteral("error"), ownerLeaseError_}});
        finishCommand(false,
                      QStringLiteral("变更前二次核对发现旧监管器已加载，未执行任何变更：%1")
                          .arg(ownerLeaseError_),
                      {{QStringLiteral("outcome_uncertain"), false},
                       {QStringLiteral("preflight_failed"), true},
                       {QStringLiteral("verified_snapshot"), snapshot_}});
        return;
    }
    QString error;
    if (!validateMutationPreflight(&error)) {
        finishCommand(false, QStringLiteral("生命周期预检失败，未执行任何变更：%1").arg(error),
                      {{QStringLiteral("outcome_uncertain"), false},
                       {QStringLiteral("preflight_failed"), true},
                       {QStringLiteral("verified_snapshot"), snapshot_}});
        return;
    }
    commandNotes_.push_back(QStringLiteral("预检通过：label/program/arguments/artifact 身份一致"));
    preCommandPids_.clear();
    for (const auto &value : probeUnits_) {
        const auto unit = value.toObject();
        preCommandPids_.insert(unit.value(QStringLiteral("id")).toString(),
                               unit.value(QStringLiteral("pid")).toInteger());
    }
    beginMutationAfterPreflight();
}

void LifecycleController::beginMutationAfterPreflight() {
    if (!config_.launchdUnits.isEmpty()) {
        prepareLaunchdSteps(currentAction_);
        startStep();
    } else if (currentAction_ == QStringLiteral("start_service")) {
        startManagedProcesses();
    } else if (currentAction_ == QStringLiteral("stop_service")) {
        stopManagedProcesses(false);
    } else {
        stopManagedProcesses(true);
    }
}

void LifecycleController::prepareLaunchdSteps(const QString &action) {
    steps_.clear();
    QList<LaunchdUnit> units = selectedLaunchdUnits();
    if (action == QStringLiteral("stop_service")) std::reverse(units.begin(), units.end());
    for (const auto &unit : units) {
        bool loaded = false;
        for (const auto &value : probeUnits_) {
            const auto observed = value.toObject();
            if (observed.value(QStringLiteral("id")).toString() == unit.id) {
                loaded = observed.value(QStringLiteral("loaded")).toBool(false);
                break;
            }
        }
        if (action == QStringLiteral("stop_service")) {
            if (!loaded) {
                commandNotes_.push_back(QStringLiteral("停止 %1: authoritative not loaded，跳过").arg(unit.id));
                continue;
            }
            steps_.enqueue({QStringLiteral("/bin/launchctl"),
                            {QStringLiteral("bootout"), domainTarget(unit.label)},
                            QStringLiteral("停止 %1").arg(unit.id), true});
        } else if (action == QStringLiteral("restart_service")) {
            if (!loaded && !unit.required) {
                commandNotes_.push_back(QStringLiteral("重启 %1: optional and not loaded，跳过").arg(unit.id));
                continue;
            }
            steps_.enqueue({QStringLiteral("/bin/launchctl"),
                            {QStringLiteral("kickstart"), QStringLiteral("-k"), domainTarget(unit.label)},
                            QStringLiteral("重启 %1").arg(unit.id), !unit.required,
                            unit.startDelayMs});
        } else {
            if (!unit.plistPath.isEmpty()) {
                steps_.enqueue({QStringLiteral("/bin/launchctl"),
                                {QStringLiteral("bootstrap"), domainTarget(), unit.plistPath},
                                QStringLiteral("加载 %1").arg(unit.id), true});
            }
            steps_.enqueue({QStringLiteral("/bin/launchctl"),
                            {QStringLiteral("enable"), domainTarget(unit.label)},
                            QStringLiteral("启用 %1").arg(unit.id), true});
            steps_.enqueue({QStringLiteral("/bin/launchctl"),
                            {QStringLiteral("kickstart"), domainTarget(unit.label)},
                            QStringLiteral("启动 %1").arg(unit.id), !unit.required,
                            unit.startDelayMs});
        }
    }
}

void LifecycleController::startStep() {
    if (currentCommandId_.isEmpty()) return;
    if (steps_.isEmpty()) {
        if (commandFailed_) {
            commandNotes_.push_back(QStringLiteral("必要步骤失败，转入最终状态核对"));
            beginLaunchdVerification();
        } else {
            beginLaunchdVerification();
        }
        return;
    }
    const Step step = steps_.dequeue();
    const quint64 generation = commandGeneration_;
    auto *process = new QProcess(this);
    stepProcess_ = process;
    process->setProgram(step.program);
    process->setArguments(step.arguments);
    connect(process, &QProcess::finished, this,
            [this, process, step, generation](int exitCode, QProcess::ExitStatus exitStatus) {
        if (generation != commandGeneration_ || currentCommandId_.isEmpty()) {
            process->deleteLater();
            return;
        }
        const bool ok = exitStatus == QProcess::NormalExit && exitCode == 0;
        QString stderrText = QString::fromUtf8(process->readAllStandardError()).trimmed();
        if (stderrText.size() > 300) stderrText = stderrText.left(300) + QStringLiteral("…");
        commandNotes_.push_back(QStringLiteral("%1: %2%3")
                                    .arg(step.description, ok ? QStringLiteral("ok") : QStringLiteral("failed"),
                                         stderrText.isEmpty() ? QString() : QStringLiteral(" (%1)").arg(stderrText)));
        if (!ok && !step.tolerateFailure) {
            commandFailed_ = true;
            steps_.clear();
        }
        stepProcess_.clear();
        process->deleteLater();
        if (step.delayAfterMs > 0) {
            QTimer::singleShot(step.delayAfterMs, this, [this, generation] {
                if (generation == commandGeneration_ && !currentCommandId_.isEmpty()) startStep();
            });
        } else {
            startStep();
        }
    });
    connect(process, &QProcess::started, this, [this, generation] {
        if (generation == commandGeneration_ && !currentCommandId_.isEmpty()) {
            externalMutationAttempted_ = true;
        }
    });
    process->start();
}

void LifecycleController::beginLaunchdVerification() {
    verifyingLaunchd_ = true;
    verificationAttempts_ = 0;
    commandNotes_.push_back(QStringLiteral("开始核对 launchd 最终状态"));
    verificationProbeGeneration_ = beginLaunchdProbe(true);
}

bool LifecycleController::launchdOutcomeSatisfied() const {
    const QList<LaunchdUnit> selected = selectedLaunchdUnits();
    QSet<QString> selectedIds;
    for (const auto &unit : selected) selectedIds.insert(unit.id);
    const bool targeted = !currentTargetUnit_.isEmpty();
    int observedSelected = 0;
    int selectedRunning = 0;
    int required = 0;
    int requiredRunning = 0;
    int totalLoaded = 0;
    bool allAuthoritative = true;
    bool requiredRestarted = true;
    for (const auto &value : probeUnits_) {
        const auto unit = value.toObject();
        const QString id = unit.value(QStringLiteral("id")).toString();
        if (!selectedIds.contains(id)) continue;
        ++observedSelected;
        if (unit.value(QStringLiteral("loaded")).toBool(false)) ++totalLoaded;
        if (unit.value(QStringLiteral("running")).toBool(false)) ++selectedRunning;
        if (!unit.value(QStringLiteral("probe_authoritative")).toBool(false)) {
            allAuthoritative = false;
        }
        if (unit.value(QStringLiteral("required")).toBool()) {
            ++required;
            if (unit.value(QStringLiteral("running")).toBool()) ++requiredRunning;
            if (currentAction_ == QStringLiteral("restart_service")) {
                const qint64 pid = unit.value(QStringLiteral("pid")).toInteger();
                const qint64 previousPid = preCommandPids_.value(id);
                if (pid <= 0 || (previousPid > 0 && pid == previousPid)) requiredRestarted = false;
            }
        } else if (targeted && currentAction_ == QStringLiteral("restart_service")) {
            const qint64 pid = unit.value(QStringLiteral("pid")).toInteger();
            const qint64 previousPid = preCommandPids_.value(id);
            if (pid <= 0 || (previousPid > 0 && pid == previousPid)) requiredRestarted = false;
        }
    }
    allAuthoritative = allAuthoritative && !selected.isEmpty()
        && observedSelected == selected.size();
    const bool stopping = currentAction_ == QStringLiteral("stop_service");
    const bool restarting = currentAction_ == QStringLiteral("restart_service");
    if (targeted) {
        return stopping ? allAuthoritative && totalLoaded == 0
                        : allAuthoritative && selectedRunning == selected.size()
                            && (!restarting || (!commandFailed_ && requiredRestarted));
    }
    return stopping ? allAuthoritative && totalLoaded == 0
                    : allAuthoritative && required > 0 && requiredRunning == required
                        && (!restarting || (!commandFailed_ && requiredRestarted));
}

void LifecycleController::verifyLaunchdOutcome() {
    if (!verifyingLaunchd_ || currentCommandId_.isEmpty() || probeProcess_
        || completedProbeGeneration_ != verificationProbeGeneration_) return;
    if (QDateTime::currentMSecsSinceEpoch() >= commandDeadlineAtMs_) {
        verifyingLaunchd_ = false;
        QJsonObject details{{QStringLiteral("verified_snapshot"), snapshot_},
                            {QStringLiteral("deadline_expired_after_mutation"),
                             externalMutationAttempted_}};
        if (externalMutationAttempted_) {
            details.insert(QStringLiteral("outcome_uncertain"), true);
            details.insert(QStringLiteral("reconciliation_required"), true);
        }
        finishCommand(false, QStringLiteral("launchd 最终状态核对超过命令截止时间"), details);
        return;
    }
    const bool targeted = !currentTargetUnit_.isEmpty();
    const bool stopping = currentAction_ == QStringLiteral("stop_service");
    const bool satisfied = launchdOutcomeSatisfied();
    if (satisfied) {
        verifyingLaunchd_ = false;
        commandNotes_.push_back(QStringLiteral("最终状态核对通过"));
        QJsonObject details{{QStringLiteral("verified_snapshot"), snapshot_}};
        QString successMessage = stopping ? QStringLiteral("launchd 服务组已停止并验证")
                                          : QStringLiteral("launchd 必要服务已运行并验证");
        if (targeted) {
            details.insert(QStringLiteral("target_unit"), currentTargetUnit_);
            details.insert(QStringLiteral("target_lifecycle_success"), true);
            details.insert(QStringLiteral("module_lifecycle"),
                           snapshot_.value(QStringLiteral("lifecycle")));
            details.insert(QStringLiteral("module_ready"),
                           snapshot_.value(QStringLiteral("lifecycle")).toString()
                               == QStringLiteral("running"));
            successMessage = stopping
                ? QStringLiteral("目标 unit %1 已停止并经 launchd 验证").arg(currentTargetUnit_)
                : QStringLiteral("目标 unit %1 已运行并经 launchd 验证").arg(currentTargetUnit_);
        }
        finishCommand(true, successMessage, details);
        return;
    }
    if (++verificationAttempts_ >= 10) {
        verifyingLaunchd_ = false;
        QJsonObject details{{QStringLiteral("verified_snapshot"), snapshot_}};
        if (externalMutationAttempted_) {
            details.insert(QStringLiteral("outcome_uncertain"), true);
            details.insert(QStringLiteral("reconciliation_required"), true);
            details.insert(QStringLiteral("tracking_late_result"), false);
        }
        finishCommand(false, QStringLiteral("launchd 命令已执行，但最终状态在期限内未达到预期"),
                      details);
        return;
    }
    const quint64 generation = commandGeneration_;
    QTimer::singleShot(500, this, [this, generation] {
        if (generation == commandGeneration_ && verifyingLaunchd_ && !currentCommandId_.isEmpty()) {
            verificationProbeGeneration_ = beginLaunchdProbe(true);
        }
    });
}

void LifecycleController::startManagedProcesses() {
    managedQueue_ = config_.managedProcesses;
    std::sort(managedQueue_.begin(), managedQueue_.end(),
              [](const ManagedProcess &a, const ManagedProcess &b) { return a.startOrder < b.startOrder; });
    startNextManaged();
}

void LifecycleController::startNextManaged() {
    if (currentCommandId_.isEmpty()) return;
    if (managedQueue_.isEmpty()) {
        finishCommand(true, QStringLiteral("托管进程组已启动"));
        return;
    }
    const ManagedProcess definition = managedQueue_.takeFirst();
    const quint64 generation = commandGeneration_;
    QProcess *process = managed_.value(definition.id, nullptr);
    if (process && process->state() != QProcess::NotRunning) {
        commandNotes_.push_back(QStringLiteral("%1: already running").arg(definition.id));
        QTimer::singleShot(definition.startDelayMs, this, [this, generation] {
            if (generation == commandGeneration_ && !currentCommandId_.isEmpty()) startNextManaged();
        });
        return;
    }
    if (!QFileInfo::exists(definition.program)) {
        finishCommand(false, QStringLiteral("程序不存在：%1").arg(definition.program));
        return;
    }
    if (!process) {
        process = new QProcess(this);
        managed_.insert(definition.id, process);
        connect(process, &QProcess::errorOccurred, this, [this, definition](QProcess::ProcessError error) {
            emit logEvent(QJsonObject{{QStringLiteral("level"), QStringLiteral("error")},
                                      {QStringLiteral("message"), QStringLiteral("%1 process error %2").arg(definition.id).arg(error)},
                                      {QStringLiteral("timestamp"), utcNow()}});
        });
    }
    process->setProgram(definition.program);
    process->setArguments(definition.arguments);
    if (!definition.workingDirectory.isEmpty()) process->setWorkingDirectory(definition.workingDirectory);
    process->setProcessChannelMode(QProcess::ForwardedChannels);
    connect(process, &QProcess::started, this, [this, process, definition, generation] {
        if (generation != commandGeneration_ || currentCommandId_.isEmpty()) return;
        externalMutationAttempted_ = true;
        commandNotes_.push_back(QStringLiteral("%1: started pid=%2").arg(definition.id).arg(process->processId()));
        QTimer::singleShot(definition.startDelayMs, this, [this, generation] {
            if (generation == commandGeneration_ && !currentCommandId_.isEmpty()) startNextManaged();
        });
    }, Qt::SingleShotConnection);
    connect(process, &QProcess::errorOccurred, this, [this, definition, generation](QProcess::ProcessError) {
        if (generation == commandGeneration_ && !currentCommandId_.isEmpty()) {
            finishCommand(false, QStringLiteral("无法启动 %1").arg(definition.id));
        }
    }, Qt::SingleShotConnection);
    process->start();
}

void LifecycleController::stopManagedProcesses(bool restarting) {
    restartAfterStop_ = restarting;
    managedQueue_ = config_.managedProcesses;
    std::sort(managedQueue_.begin(), managedQueue_.end(),
              [](const ManagedProcess &a, const ManagedProcess &b) { return a.stopOrder < b.stopOrder; });
    stopNextManaged();
}

void LifecycleController::stopNextManaged() {
    if (currentCommandId_.isEmpty()) return;
    const quint64 generation = commandGeneration_;
    while (!managedQueue_.isEmpty()) {
        const ManagedProcess definition = managedQueue_.takeFirst();
        auto *process = managed_.value(definition.id, nullptr);
        if (!process || process->state() == QProcess::NotRunning) {
            commandNotes_.push_back(QStringLiteral("%1: already stopped").arg(definition.id));
            continue;
        }
        connect(process, &QProcess::finished, this,
                [this, definition, generation](int, QProcess::ExitStatus) {
            if (generation != commandGeneration_ || currentCommandId_.isEmpty()) return;
            commandNotes_.push_back(QStringLiteral("%1: stopped").arg(definition.id));
            stopNextManaged();
        }, Qt::SingleShotConnection);
        externalMutationAttempted_ = true;
        process->terminate();
        return;
    }
    if (restartAfterStop_) {
        restartAfterStop_ = false;
        startManagedProcesses();
    } else {
        finishCommand(true, QStringLiteral("托管进程组已温和停止"));
    }
}

void LifecycleController::finishCommand(bool ok, const QString &message, const QJsonObject &details) {
    if (currentCommandId_.isEmpty()) return;
    commandDeadline_.stop();
    QJsonObject payload = details;
    payload.insert(QStringLiteral("command_id"), currentCommandId_);
    payload.insert(QStringLiteral("module_id"), config_.id);
    payload.insert(QStringLiteral("action"), currentAction_);
    payload.insert(QStringLiteral("state"), ok ? QStringLiteral("succeeded") : QStringLiteral("failed"));
    payload.insert(QStringLiteral("message"), message);
    payload.insert(QStringLiteral("command_deadline_epoch_ms"), commandDeadlineAtMs_);
    payload.insert(QStringLiteral("notes"), QJsonArray::fromStringList(commandNotes_));
    if (!currentTargetUnit_.isEmpty()) {
        payload.insert(QStringLiteral("target_unit"), currentTargetUnit_);
    }
    payload.insert(QStringLiteral("finished_at"), utcNow());
    currentCommandId_.clear();
    currentAction_.clear();
    currentTargetUnit_.clear();
    commandDeadlineAtMs_ = 0;
    preCommandPids_.clear();
    preflightProbeGeneration_ = 0;
    verificationProbeGeneration_ = 0;
    verifyingLaunchd_ = false;
    preflighting_ = false;
    steps_.clear();
    managedQueue_.clear();
    restartAfterStop_ = false;
    externalMutationAttempted_ = false;
    ++commandGeneration_;
    emit commandFinished(payload);
    refresh();
}

void LifecycleController::cancelCommandExecution() {
    commandDeadline_.stop();
    ++commandGeneration_;
    steps_.clear();
    managedQueue_.clear();
    restartAfterStop_ = false;
    externalMutationAttempted_ = false;
    verifyingLaunchd_ = false;
    preflighting_ = false;
    if (stepProcess_) {
        retireProcess(stepProcess_);
    }
    currentCommandId_.clear();
    currentAction_.clear();
    currentTargetUnit_.clear();
    commandDeadlineAtMs_ = 0;
    preCommandPids_.clear();
    preflightProbeGeneration_ = 0;
    verificationProbeGeneration_ = 0;
}

} // namespace hub
