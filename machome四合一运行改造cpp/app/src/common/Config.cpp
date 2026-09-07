#include "common/Config.h"
#include "common/WorkerSchedule.h"

#include <QDir>
#include <QCryptographicHash>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QSet>
#include <QStandardPaths>
#include <QUrl>

#include <cmath>

namespace hub {
namespace {

QString jsonString(const QJsonObject &object, const QString &key,
                   const QString &fallback = {}) {
    const auto value = object.value(key);
    return value.isString() ? value.toString() : fallback;
}

QStringList stringList(const QJsonValue &value) {
    QStringList result;
    for (const auto &item : value.toArray()) {
        if (item.isString()) {
            const QString text = item.toString().trimmed();
            if (!text.isEmpty()) result.push_back(text);
        }
    }
    return result;
}

bool exactIntegerInRange(const QJsonValue &value, int minimum, int maximum) {
    if (!value.isDouble()) return false;
    const double number = value.toDouble();
    return std::isfinite(number) && std::floor(number) == number
        && number >= minimum && number <= maximum;
}

bool strictLoopbackUrl(const QJsonValue &value, const QString &scheme,
                       const QString &requiredPath, QUrl *result = nullptr) {
    if (!value.isString()) return false;
    const QString text = value.toString();
    if (text.isEmpty() || text != text.trimmed()) return false;
    const QUrl url(text, QUrl::StrictMode);
    QString path = url.path();
    while (path.size() > 1 && path.endsWith(u'/')) path.chop(1);
    if (path == QStringLiteral("/")) path.clear();
    QString expected = requiredPath;
    while (expected.size() > 1 && expected.endsWith(u'/')) expected.chop(1);
    if (expected == QStringLiteral("/")) expected.clear();
    const bool valid = url.isValid() && url.scheme() == scheme
        && url.host() == QStringLiteral("127.0.0.1")
        && url.port(-1) >= 1 && url.port(-1) <= 65535
        && url.userInfo().isEmpty() && !url.hasQuery() && url.fragment().isEmpty()
        && path == expected;
    if (valid && result) *result = url;
    return valid;
}

bool sameOrigin(const QUrl &http, const QUrl &stream) {
    return http.host() == stream.host() && http.port(-1) == stream.port(-1)
        && ((http.scheme() == QStringLiteral("http")
             && stream.scheme() == QStringLiteral("ws"))
            || (http.scheme() == QStringLiteral("https")
                && stream.scheme() == QStringLiteral("wss")));
}

bool hasReadinessEquals(const QJsonArray &conditions, const QString &path,
                        const QJsonValue &expected) {
    for (const auto &value : conditions) {
        const QJsonObject condition = value.toObject();
        if (condition.value(QStringLiteral("path")).toString() == path
            && condition.contains(QStringLiteral("equals"))
            && condition.value(QStringLiteral("equals")) == expected) {
            return true;
        }
    }
    return false;
}

} // namespace

QString AppConfig::expandPath(const QString &path) {
    if (path == QStringLiteral("~")) {
        return QDir::homePath();
    }
    if (path.startsWith(QStringLiteral("~/"))) {
        return QDir::home().filePath(path.mid(2));
    }
    return QDir::cleanPath(path);
}

QString AppConfig::defaultConfigPath() {
    const QString base = QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation);
    return QDir(base).filePath(QStringLiteral("MachomeHub/config/modules.json"));
}

AppConfig AppConfig::load(const QString &path, QString *error) {
    AppConfig config;
    QFile file(expandPath(path));
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) {
            *error = QStringLiteral("无法读取配置 %1：%2").arg(file.fileName(), file.errorString());
        }
        return config;
    }

    const QByteArray raw = file.readAll();
    config.sourcePath = file.fileName();
    config.configHash = QString::fromLatin1(
        QCryptographicHash::hash(raw, QCryptographicHash::Sha256).toHex());
    QJsonParseError parseError;
    const auto document = QJsonDocument::fromJson(raw, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error) {
            *error = QStringLiteral("配置 JSON 无效：%1").arg(parseError.errorString());
        }
        return config;
    }

    const auto root = document.object();
    config.schemaVersion = root.value(QStringLiteral("schema_version")).toInt(1);
    config.socketPath = expandPath(jsonString(root, QStringLiteral("agent_socket")));
    config.auditDatabase = expandPath(jsonString(root, QStringLiteral("audit_database")));
    config.frameLimitBytes = root.value(QStringLiteral("frame_limit_bytes")).toInt(1024 * 1024);

    for (const auto &entry : root.value(QStringLiteral("modules")).toArray()) {
        if (!entry.isObject()) {
            continue;
        }
        const auto object = entry.toObject();
        ModuleConfig module;
        module.id = jsonString(object, QStringLiteral("id"));
        module.displayName = jsonString(object, QStringLiteral("display_name"), module.id);
        module.adapter = jsonString(object, QStringLiteral("adapter"), module.id);
        module.engine = jsonString(object, QStringLiteral("engine"),
                                   QStringLiteral("legacy_probe"));
        module.enabled = object.value(QStringLiteral("enabled")).toBool(true);
        module.controlEnabled = object.value(QStringLiteral("control_enabled")).toBool(false);
        module.ownership = jsonString(object, QStringLiteral("ownership"), QStringLiteral("shadow"));
        module.settings = object.value(QStringLiteral("settings")).toObject();

        for (const auto &unitValue : object.value(QStringLiteral("launchd_units")).toArray()) {
            const auto unitObject = unitValue.toObject();
            LaunchdUnit unit;
            unit.id = jsonString(unitObject, QStringLiteral("id"));
            unit.label = jsonString(unitObject, QStringLiteral("label"));
            unit.plistPath = expandPath(jsonString(unitObject, QStringLiteral("plist")));
            unit.expectedProgram = expandPath(
                jsonString(unitObject, QStringLiteral("expected_program")));
            unit.expectedArguments = stringList(
                unitObject.value(QStringLiteral("expected_arguments")));
            unit.artifactSha256 = jsonString(
                unitObject, QStringLiteral("artifact_sha256")).trimmed().toLower();
            for (const auto &artifactValue : unitObject.value(
                     QStringLiteral("expected_artifacts")).toArray()) {
                const QJsonObject artifactObject = artifactValue.toObject();
                ExpectedArtifact artifact;
                artifact.path = expandPath(jsonString(
                    artifactObject, QStringLiteral("path")));
                artifact.sha256 = jsonString(
                    artifactObject, QStringLiteral("sha256")).trimmed().toLower();
                if (!artifact.path.isEmpty()) unit.expectedArtifacts.push_back(artifact);
            }
            unit.startDelayMs = unitObject.value(QStringLiteral("start_delay_ms")).toInt();
            unit.required = unitObject.value(QStringLiteral("required")).toBool(true);
            if (!unit.id.isEmpty() && !unit.label.isEmpty()) {
                module.launchdUnits.push_back(unit);
            }
        }

        module.allowedActions = stringList(object.value(QStringLiteral("allowed_actions")));
        module.approvalRequiredActions =
            stringList(object.value(QStringLiteral("approval_required_actions")));

        for (const auto &processValue : object.value(QStringLiteral("managed_processes")).toArray()) {
            const auto processObject = processValue.toObject();
            ManagedProcess process;
            process.id = jsonString(processObject, QStringLiteral("id"));
            process.program = expandPath(jsonString(processObject, QStringLiteral("program")));
            process.arguments = stringList(processObject.value(QStringLiteral("arguments")));
            process.workingDirectory = expandPath(jsonString(processObject, QStringLiteral("working_directory")));
            process.startOrder = processObject.value(QStringLiteral("start_order")).toInt();
            process.stopOrder = processObject.value(QStringLiteral("stop_order")).toInt();
            process.startDelayMs = processObject.value(QStringLiteral("start_delay_ms")).toInt();
            process.required = processObject.value(QStringLiteral("required")).toBool(true);
            if (!process.id.isEmpty() && !process.program.isEmpty()) {
                module.managedProcesses.push_back(process);
            }
        }
        config.modules.push_back(module);
    }

    QString validationError;
    if (!config.isValid(&validationError) && error) {
        *error = validationError;
    }
    return config;
}

bool AppConfig::isValid(QString *error) const {
    if (schemaVersion != 1) {
        if (error) *error = QStringLiteral("仅支持 schema_version=1");
        return false;
    }
    if (socketPath.isEmpty() || !QDir::isAbsolutePath(socketPath)) {
        if (error) *error = QStringLiteral("agent_socket 必须是绝对路径");
        return false;
    }
    if (auditDatabase.isEmpty() || !QDir::isAbsolutePath(auditDatabase)) {
        if (error) *error = QStringLiteral("audit_database 必须是绝对路径");
        return false;
    }
    if (frameLimitBytes < 64 * 1024 || frameLimitBytes > 16 * 1024 * 1024) {
        if (error) *error = QStringLiteral("frame_limit_bytes 必须在 64 KiB 到 16 MiB 之间");
        return false;
    }

    const QHash<QString, QString> requiredModules{
        {QStringLiteral("upload"), QStringLiteral("upload")},
        {QStringLiteral("premium"), QStringLiteral("premium")},
        {QStringLiteral("webull"), QStringLiteral("webull")},
        {QStringLiteral("redemption"), QStringLiteral("realtime")}};
    const QSet<QString> supportedActions{
        QStringLiteral("refresh"), QStringLiteral("open_legacy_ui"),
        QStringLiteral("set_operating_mode"),
        QStringLiteral("start_service"), QStringLiteral("stop_service"),
        QStringLiteral("restart_service"), QStringLiteral("acknowledge_uncertain"),
        QStringLiteral("premium_sync"), QStringLiteral("premium_raw_snapshot"),
        QStringLiteral("premium_detail_subscribe"),
        QStringLiteral("premium_detail_unsubscribe"),
        QStringLiteral("premium_set_watchlist"), QStringLiteral("premium_set_l1_hotlist"),
        QStringLiteral("webull_get_book"), QStringLiteral("webull_set_mode"),
        QStringLiteral("webull_collector_start"), QStringLiteral("webull_collector_stop"),
        QStringLiteral("webull_restart_browser"), QStringLiteral("webull_show_login"),
        QStringLiteral("redemption_get_history"), QStringLiteral("redemption_get_pcf"),
        QStringLiteral("redemption_set_watchlist"), QStringLiteral("redemption_set_symbol_name"),
        QStringLiteral("redemption_monitor_start"), QStringLiteral("redemption_monitor_stop"),
        QStringLiteral("redemption_wind_start"),
        QStringLiteral("redemption_wind_shutdown_cleanup"),
        QStringLiteral("redemption_pcf_refresh"), QStringLiteral("redemption_qmt_connect"),
        QStringLiteral("redemption_qmt_disconnect"), QStringLiteral("redemption_qmt_sync"),
        QStringLiteral("redemption_qmt_order"),
        QStringLiteral("upload_run_job"), QStringLiteral("upload_ingest_quote"),
        QStringLiteral("upload_ingest_valuation"), QStringLiteral("upload_ingest_dataset"),
        QStringLiteral("upload_ack"),
        QStringLiteral("upload_set_fund"), QStringLiteral("upload_add_message"),
        QStringLiteral("upload_ibkr_reconnect"),
        QStringLiteral("upload_ibkr_disconnect")};
    const QSet<QString> highRisk{
        QStringLiteral("open_legacy_ui"),
        QStringLiteral("start_service"), QStringLiteral("stop_service"),
        QStringLiteral("restart_service"), QStringLiteral("acknowledge_uncertain"),
        QStringLiteral("webull_set_mode"), QStringLiteral("webull_collector_start"),
        QStringLiteral("webull_collector_stop"), QStringLiteral("webull_restart_browser"),
        QStringLiteral("webull_show_login"),
        QStringLiteral("redemption_monitor_stop"),
        QStringLiteral("redemption_wind_shutdown_cleanup"),
        QStringLiteral("redemption_qmt_disconnect"), QStringLiteral("redemption_qmt_order")};
    QSet<QString> ids;
    for (const auto &module : modules) {
        if (module.id.isEmpty() || ids.contains(module.id)) {
            if (error) *error = QStringLiteral("模块 id 为空或重复：%1").arg(module.id);
            return false;
        }
        if (!requiredModules.contains(module.id) || requiredModules.value(module.id) != module.adapter) {
            if (error) *error = QStringLiteral("模块 %1 必须使用固定 adapter（当前 %2）")
                                    .arg(module.id, module.adapter);
            return false;
        }
        if (module.engine != QStringLiteral("native")
            && module.engine != QStringLiteral("legacy_probe")
            && module.engine != QStringLiteral("disabled")) {
            if (error) *error = QStringLiteral("模块 %1 engine 必须是 native/legacy_probe/disabled")
                                    .arg(module.id);
            return false;
        }
        if (module.ownership != QStringLiteral("shadow") &&
            module.ownership != QStringLiteral("logic") &&
            module.ownership != QStringLiteral("owner")) {
            if (error) *error = QStringLiteral("模块 %1 ownership 无效").arg(module.id);
            return false;
        }
        if (module.controlEnabled && module.ownership == QStringLiteral("shadow")) {
            if (error) *error = QStringLiteral("模块 %1 在 shadow 模式不能启用控制").arg(module.id);
            return false;
        }

        const QJsonObject settings = module.settings;
        const QString testRoot = QDir::cleanPath(expandPath(jsonString(settings, QStringLiteral("data_root"))));
        const bool isolatedTestRoot = settings.value(QStringLiteral("test_mode")).toBool(false)
            && testRoot.startsWith(QStringLiteral("/private/tmp/"))
            && testRoot.endsWith(QStringLiteral("/MachomeHub/data/") + module.id);
        if (module.id == QStringLiteral("upload")) {
            if (module.engine == QStringLiteral("native")) {
                const QString dataRoot = expandPath(jsonString(
                    settings, QStringLiteral("data_root"),
                    QStringLiteral("~/Library/Application Support/MachomeHub/data/upload")));
                const QString allowed = QDir(QStandardPaths::writableLocation(
                    QStandardPaths::GenericDataLocation))
                                            .filePath(QStringLiteral("MachomeHub/data/upload"));
                if ((!isolatedTestRoot && QDir::cleanPath(dataRoot) != QDir::cleanPath(allowed))
                    || !QStringList{QStringLiteral("record_only"),QStringLiteral("bundled_business")}.contains(
                        jsonString(settings,QStringLiteral("sink_mode"),QStringLiteral("record_only")))
                    || !module.launchdUnits.isEmpty()
                    || !module.managedProcesses.isEmpty()) {
                    if (error) {
                        *error = QStringLiteral("原生 Upload 必须使用 MachomeHub/data/upload、"
                                                "record_only 或 bundled_business，且不得配置旧进程/LaunchAgent");
                    }
                    return false;
                }
            } else if (!strictLoopbackUrl(settings.value(QStringLiteral("web_base_url")),
                                   QStringLiteral("http"), QString{})) {
                if (error) *error = QStringLiteral("Upload web_base_url 必须是含显式端口的 http://127.0.0.1 origin");
                return false;
            }
            if (module.engine != QStringLiteral("native")
                && (jsonString(settings, QStringLiteral("health_endpoint"))
                    != QStringLiteral("/api/v1/health")
                || jsonString(settings, QStringLiteral("api_contract"))
                    != QStringLiteral("newnavnav-web-v1"))) {
                if (error) *error = QStringLiteral("Upload 必须显式锁定 /api/v1/health 与 newnavnav-web-v1 合同");
                return false;
            }
            if (module.engine != QStringLiteral("native")
                && settings.value(QStringLiteral("workers_expected")).toBool(true)) {
                WorkerSchedule schedule;
                QString scheduleError;
                if (!schedule.configure(
                        settings.value(QStringLiteral("worker_monitor_schedule")).toObject(),
                        &scheduleError)) {
                    if (error) *error = QStringLiteral("Upload worker 排程无效：%1").arg(scheduleError);
                    return false;
                }
            }
        } else if (module.id == QStringLiteral("premium")) {
            const QString host = jsonString(settings, QStringLiteral("host"));
            const QJsonValue summaryPort = settings.value(QStringLiteral("summary_port"));
            const QJsonValue l1Port = settings.value(QStringLiteral("l1_port"));
            if (host != QStringLiteral("127.0.0.1")
                || !exactIntegerInRange(summaryPort, 1, 65535)
                || !exactIntegerInRange(l1Port, 1, 65535)
                || summaryPort.toInt() == l1Port.toInt()) {
                if (error) *error = QStringLiteral("Premium host 必须是 127.0.0.1，summary/l1 必须是不同的 1–65535 显式端口");
                return false;
            }
            if (jsonString(settings, QStringLiteral("summary_protocol"))
                    != QStringLiteral("ws-json-v1")
                || jsonString(settings, QStringLiteral("l1_protocol"))
                    != QStringLiteral("qmt-l1-ndjson-v1")) {
                if (error) *error = QStringLiteral("Premium 必须显式锁定 ws-json-v1 与 qmt-l1-ndjson-v1 协议");
                return false;
            }
            if (module.engine == QStringLiteral("native")) {
                const QString dataRoot = QDir::cleanPath(expandPath(jsonString(
                    settings, QStringLiteral("data_root"),
                    QStringLiteral("~/Library/Application Support/MachomeHub/data/premium"))));
                const QString allowed = QDir::cleanPath(QDir(
                    QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation))
                    .filePath(QStringLiteral("MachomeHub/data/premium")));
                if ((!isolatedTestRoot && dataRoot != allowed) || !module.launchdUnits.isEmpty()
                    || !module.managedProcesses.isEmpty()
                    || module.ownership != QStringLiteral("logic")) {
                    if (error) {
                        *error = QStringLiteral("原生 Premium A 必须使用 MachomeHub/data/premium、"
                                                "logic ownership，且不得配置旧进程/LaunchAgent");
                    }
                    return false;
                }
            }
        } else if (module.id == QStringLiteral("webull")) {
            QUrl api;
            QUrl stream;
            if (!strictLoopbackUrl(settings.value(QStringLiteral("api_base_url")),
                                   QStringLiteral("http"), QStringLiteral("/v2"), &api)
                || !strictLoopbackUrl(settings.value(QStringLiteral("stream_url")),
                                      QStringLiteral("ws"), QStringLiteral("/v2/stream"), &stream)
                || !sameOrigin(api, stream)
                || !exactIntegerInRange(settings.value(QStringLiteral("api_protocol")), 2, 2)) {
                if (error) *error = QStringLiteral("Webull API/stream 必须是同源显式端口的 127.0.0.1 v2 HTTP/WS 合同");
                return false;
            }
            if (module.engine == QStringLiteral("native")) {
                const QString dataRoot = QDir::cleanPath(expandPath(jsonString(
                    settings, QStringLiteral("data_root"),
                    QStringLiteral("~/Library/Application Support/MachomeHub/data/webull"))));
                const QString allowed = QDir::cleanPath(QDir(QStandardPaths::writableLocation(
                    QStandardPaths::GenericDataLocation)).filePath(QStringLiteral("MachomeHub/data/webull")));
                if ((!isolatedTestRoot && dataRoot != allowed) || !module.launchdUnits.isEmpty()
                    || !module.managedProcesses.isEmpty()
                    || module.ownership != QStringLiteral("logic")) {
                    if (error) *error = QStringLiteral("原生 Webull 必须使用 MachomeHub/data/webull、logic ownership，且不得配置旧进程/LaunchAgent");
                    return false;
                }
            }
        } else if (module.id == QStringLiteral("redemption")) {
            QUrl api;
            QUrl stream;
            if (!strictLoopbackUrl(settings.value(QStringLiteral("api_base_url")),
                                   QStringLiteral("http"), QString{}, &api)
                || !strictLoopbackUrl(settings.value(QStringLiteral("stream_url")),
                                      QStringLiteral("ws"), QStringLiteral("/ws/v1/changes"), &stream)
                || !sameOrigin(api, stream)
                || !exactIntegerInRange(settings.value(QStringLiteral("required_protocol")), 1, 1)) {
                if (error) *error = QStringLiteral("Realtime API/stream 必须是同源显式端口的 127.0.0.1 protocol v1 合同");
                return false;
            }
            if (module.engine == QStringLiteral("native")) {
                const QString dataRoot = QDir::cleanPath(expandPath(jsonString(
                    settings, QStringLiteral("data_root"),
                    QStringLiteral("~/Library/Application Support/MachomeHub/data/redemption"))));
                const QString allowed = QDir::cleanPath(QDir(
                    QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation))
                    .filePath(QStringLiteral("MachomeHub/data/redemption")));
                const QString helperMode = jsonString(
                    settings, QStringLiteral("wind_helper_mode"), QStringLiteral("disabled"));
                if ((!isolatedTestRoot && dataRoot != allowed) || !module.launchdUnits.isEmpty()
                    || !module.managedProcesses.isEmpty()
                    || module.ownership != QStringLiteral("logic")
                    || !QStringList{QStringLiteral("disabled"), QStringLiteral("fixture"),
                                    QStringLiteral("live")}.contains(helperMode)
                    || (settings.value(QStringLiteral("live_qmt_orders_enabled")).toBool(false)
                        && settings.value(QStringLiteral("record_only")).toBool(true))) {
                    if (error) {
                        *error = QStringLiteral("原生实时申赎必须使用 MachomeHub/data/redemption、"
                                                "logic ownership、记录模式不得启用真实 QMT 下单，"
                                                "且不得配置旧进程/LaunchAgent");
                    }
                    return false;
                }
            }
        }
        if (module.ownership == QStringLiteral("owner")) {
            if (!module.managedProcesses.isEmpty() || module.launchdUnits.isEmpty()) {
                if (error) {
                    *error = QStringLiteral("模块 %1 的 owner 模式只允许由明确的 launchd_units 接管；"
                                            "managed_processes 仅用于 shadow 观测")
                                 .arg(module.id);
                }
                return false;
            }
            static const QRegularExpression sha256Pattern(QStringLiteral("^[0-9a-f]{64}$"));
            for (const auto &unit : module.launchdUnits) {
                if (unit.expectedProgram.isEmpty()
                    || !QDir::isAbsolutePath(unit.expectedProgram)
                    || !sha256Pattern.match(unit.artifactSha256).hasMatch()
                    || unit.expectedArtifacts.isEmpty()) {
                    if (error) {
                        *error = QStringLiteral("owner 模块 %1 的每个受控 launchd unit %2 必须配置"
                                                "绝对 expected_program、64 位 artifact_sha256 与 expected_artifacts")
                                     .arg(module.id, unit.id);
                    }
                    return false;
                }
                bool programArtifactPresent = false;
                QSet<QString> artifactPaths;
                for (const auto &artifact : unit.expectedArtifacts) {
                    if (!QDir::isAbsolutePath(artifact.path)
                        || artifactPaths.contains(artifact.path)
                        || !sha256Pattern.match(artifact.sha256).hasMatch()) {
                        if (error) {
                            *error = QStringLiteral("owner 单元 %1 的 expected_artifacts 必须是唯一绝对路径 + SHA-256")
                                         .arg(unit.id);
                        }
                        return false;
                    }
                    artifactPaths.insert(artifact.path);
                    if (artifact.path == unit.expectedProgram
                        && artifact.sha256 == unit.artifactSha256) {
                        programArtifactPresent = true;
                    }
                }
                if (!programArtifactPresent) {
                    if (error) {
                        *error = QStringLiteral("owner 单元 %1 的 expected_artifacts 必须包含 expected_program 及相同 hash")
                                     .arg(unit.id);
                    }
                    return false;
                }
                const bool pythonLauncher = unit.expectedProgram.contains(
                    QStringLiteral("python"), Qt::CaseInsensitive);
                if (pythonLauncher) {
                    QStringList pythonEntries;
                    for (const auto &argument : unit.expectedArguments) {
                        if (argument.endsWith(QStringLiteral(".py"), Qt::CaseInsensitive)
                            || argument.endsWith(QStringLiteral(".pyw"), Qt::CaseInsensitive)) {
                            pythonEntries.append(expandPath(argument));
                        }
                    }
                    if (pythonEntries.size() != 1
                        || !QDir::isAbsolutePath(pythonEntries.first())
                        || !artifactPaths.contains(pythonEntries.first())
                        || unit.expectedArtifacts.size() < 3) {
                        if (error) {
                            *error = QStringLiteral("Python owner 单元 %1 必须将 ProgramArguments 中唯一真实入口"
                                                    "及部署 manifest 展开文件全部纳入 expected_artifacts")
                                         .arg(unit.id);
                        }
                        return false;
                    }
                }
            }
            const QJsonObject lease = module.settings.value(QStringLiteral("owner_lease")).toObject();
            const QString lockPath = expandPath(jsonString(lease, QStringLiteral("lock_path")));
            const QString markerPath = expandPath(jsonString(lease, QStringLiteral("handoff_marker")));
            const QString tokenHash = jsonString(lease, QStringLiteral("handoff_token_sha256"))
                                          .trimmed().toLower();
            const QStringList previousProcesses =
                stringList(lease.value(QStringLiteral("previous_owner_processes")));
            const QStringList previousLabels =
                stringList(lease.value(QStringLiteral("previous_owner_labels")));
            if (!QDir::isAbsolutePath(lockPath) || !QDir::isAbsolutePath(markerPath)
                || jsonString(lease, QStringLiteral("owner_id")).size() < 8
                || lease.value(QStringLiteral("generation")).toInteger() < 1
                || !sha256Pattern.match(tokenHash).hasMatch()
                || (previousProcesses.isEmpty() && previousLabels.isEmpty())) {
                if (error) {
                    *error = QStringLiteral("owner 模块 %1 必须配置完整 owner_lease "
                                            "(lock/marker/owner/generation/token hash/旧监管器身份)")
                                 .arg(module.id);
                }
                return false;
            }
            const QJsonObject readiness = module.settings.value(QStringLiteral("owner_readiness")).toObject();
            const QJsonArray conditions = readiness.value(QStringLiteral("conditions")).toArray();
            const int readinessTimeout = readiness.value(QStringLiteral("timeout_ms")).toInt();
            const QJsonValue allowScheduledIdle = readiness.value(
                QStringLiteral("allow_scheduled_idle"));
            if (conditions.isEmpty() || readinessTimeout < 1000 || readinessTimeout > 300000) {
                if (error) {
                    *error = QStringLiteral("owner 模块 %1 必须配置 1–300 秒 owner_readiness 及至少一个条件")
                                 .arg(module.id);
                }
                return false;
            }
            if ((!allowScheduledIdle.isUndefined() && !allowScheduledIdle.isBool())
                || (allowScheduledIdle.toBool(false)
                    && module.id != QStringLiteral("webull"))) {
                if (error) {
                    *error = QStringLiteral("owner_readiness.allow_scheduled_idle 仅允许 Webull 使用布尔值");
                }
                return false;
            }
            for (const auto &value : conditions) {
                const QJsonObject condition = value.toObject();
                const QString path = jsonString(condition, QStringLiteral("path")).trimmed();
                const int matcherCount = (condition.contains(QStringLiteral("equals")) ? 1 : 0)
                    + (condition.value(QStringLiteral("one_of")).isArray() ? 1 : 0)
                    + (condition.value(QStringLiteral("non_empty")).toBool(false) ? 1 : 0);
                if (!value.isObject() || path.isEmpty() || path.startsWith(u'.')
                    || path.endsWith(u'.') || path.contains(QStringLiteral(".."))
                    || matcherCount != 1) {
                    if (error) *error = QStringLiteral("owner_readiness.conditions 必须含安全 path 且恰好一个 equals/one_of/non_empty 断言");
                    return false;
                }
            }
            QStringList missingReadiness;
            const auto requireTrue = [&](const QString &path) {
                if (!hasReadinessEquals(conditions, path, QJsonValue(true))) {
                    missingReadiness.append(path + QStringLiteral("=true"));
                }
            };
            if (module.id == QStringLiteral("upload")) {
                requireTrue(QStringLiteral("site.healthy"));
                requireTrue(QStringLiteral("site.identity_verified"));
                requireTrue(QStringLiteral("worker_health.ack_fresh"));
            } else if (module.id == QStringLiteral("premium")) {
                requireTrue(QStringLiteral("premium_readiness.ready"));
                requireTrue(QStringLiteral("freshness.summary_fresh"));
                requireTrue(QStringLiteral("freshness.l1_fresh"));
            } else if (module.id == QStringLiteral("webull")) {
                requireTrue(QStringLiteral("status.api_live"));
                requireTrue(QStringLiteral("status.api_ready"));
                requireTrue(QStringLiteral("status.data_fresh"));
                requireTrue(QStringLiteral("book.fresh"));
                if (!hasReadinessEquals(conditions, QStringLiteral("status.service"),
                                        QJsonValue(QStringLiteral("webull-lv2-gateway")))) {
                    missingReadiness.append(QStringLiteral("status.service=webull-lv2-gateway"));
                }
            } else {
                requireTrue(QStringLiteral("health.ok"));
                requireTrue(QStringLiteral("health._hub_health_contract_verified"));
                requireTrue(QStringLiteral("snapshot._hub_snapshot_contract_verified"));
                requireTrue(QStringLiteral("service_identity.instance_verified"));
            }
            if (!missingReadiness.isEmpty()) {
                if (error) {
                    *error = QStringLiteral("owner 模块 %1 readiness 缺少强制业务断言：%2")
                                 .arg(module.id, missingReadiness.join(QStringLiteral(", ")));
                }
                return false;
            }
        }
        QSet<QString> allowed;
        for (const auto &action : module.allowedActions) {
            if (!supportedActions.contains(action) || allowed.contains(action)) {
                if (error) *error = QStringLiteral("模块 %1 的 allowed_actions 含未知或重复动作：%2")
                                        .arg(module.id, action);
                return false;
            }
            const bool prefixMatches = action == QStringLiteral("refresh")
                || action == QStringLiteral("set_operating_mode")
                || action == QStringLiteral("open_legacy_ui")
                || action == QStringLiteral("acknowledge_uncertain")
                || action.endsWith(QStringLiteral("_service"))
                || (module.id == QStringLiteral("upload") && action.startsWith(QStringLiteral("upload_")))
                || (module.id == QStringLiteral("premium") && action.startsWith(QStringLiteral("premium_")))
                || (module.id == QStringLiteral("webull") && action.startsWith(QStringLiteral("webull_")))
                || (module.id == QStringLiteral("redemption") && action.startsWith(QStringLiteral("redemption_")));
            if (!prefixMatches) {
                if (error) *error = QStringLiteral("动作 %1 不属于模块 %2").arg(action, module.id);
                return false;
            }
            if (action == QStringLiteral("set_operating_mode")
                && module.engine != QStringLiteral("native")) {
                if (error) {
                    *error = QStringLiteral("模块 %1 非原生引擎，不得开放运行模式切换")
                                 .arg(module.id);
                }
                return false;
            }
            const bool nativeLogicLifecycle = action.endsWith(QStringLiteral("_service"))
                && module.engine == QStringLiteral("native")
                && module.ownership == QStringLiteral("logic");
            if (action.endsWith(QStringLiteral("_service"))
                && module.ownership != QStringLiteral("owner")
                && !nativeLogicLifecycle) {
                if (error) *error = QStringLiteral("模块 %1 非 owner，不得开放 %2").arg(module.id, action);
                return false;
            }
            if (action == QStringLiteral("open_legacy_ui")) {
                if (module.ownership == QStringLiteral("owner")) {
                    if (error) {
                        *error = QStringLiteral("owner 模块 %1 禁止打开会自启旧监管器的旧 UI")
                                     .arg(module.id);
                    }
                    return false;
                }
                const QString legacyPath = expandPath(
                    jsonString(module.settings, QStringLiteral("legacy_app")));
                if (!module.controlEnabled
                    || !module.settings.value(QStringLiteral("legacy_ui_view_only")).toBool(false)
                    || !QDir::isAbsolutePath(legacyPath)) {
                    if (error) {
                        *error = QStringLiteral("模块 %1 仅可在非 owner 逻辑控制模式中开放已证明纯只读的 legacy UI")
                                     .arg(module.id);
                    }
                    return false;
                }
            }
            allowed.insert(action);
        }
        QSet<QString> approvals;
        for (const auto &action : module.approvalRequiredActions) {
            if (!allowed.contains(action) || approvals.contains(action)) {
                if (error) {
                    *error = QStringLiteral("模块 %1 的 approval_required_actions 必须是 allowed_actions 子集：%2")
                                 .arg(module.id, action);
                }
                return false;
            }
            approvals.insert(action);
        }
        for (const auto &action : highRisk) {
            const bool nativeOperatorControl = module.engine == QStringLiteral("native")
                && module.ownership == QStringLiteral("logic")
                && (action.endsWith(QStringLiteral("_service"))
                    || action == QStringLiteral("webull_set_mode")
                    || action == QStringLiteral("webull_collector_start")
                    || action == QStringLiteral("webull_collector_stop")
                    || action == QStringLiteral("webull_restart_browser")
                    || action == QStringLiteral("webull_show_login")
                    || action == QStringLiteral("redemption_monitor_stop")
                    || action == QStringLiteral("redemption_wind_shutdown_cleanup")
                    || action == QStringLiteral("redemption_qmt_disconnect"));
            if (allowed.contains(action) && !approvals.contains(action)
                && !nativeOperatorControl) {
                if (error) *error = QStringLiteral("高风险动作 %1 必须配置代理端二阶段审批").arg(action);
                return false;
            }
        }
        if (module.ownership == QStringLiteral("owner") && module.controlEnabled
            && (!allowed.contains(QStringLiteral("acknowledge_uncertain"))
                || !approvals.contains(QStringLiteral("acknowledge_uncertain")))) {
            if (error) {
                *error = QStringLiteral("可控制的 owner 模块 %1 必须开放 acknowledge_uncertain 并要求二阶段审批")
                             .arg(module.id);
            }
            return false;
        }
        if (module.id == QStringLiteral("webull")) {
            bool hasControlAction = false;
            for (const auto &action : module.allowedActions) {
                if (action.startsWith(QStringLiteral("webull_"))
                    && action != QStringLiteral("webull_get_book")) {
                    hasControlAction = true;
                    break;
                }
            }
            if (hasControlAction && module.engine != QStringLiteral("native")) {
                const QString dataToken = QDir::cleanPath(expandPath(
                    jsonString(module.settings, QStringLiteral("token_file"))));
                const QString controlToken = QDir::cleanPath(expandPath(
                    jsonString(module.settings, QStringLiteral("control_token_file"))));
                QUrl control;
                if (!strictLoopbackUrl(module.settings.value(QStringLiteral("control_base_url")),
                                       QStringLiteral("http"), QStringLiteral("/v1"), &control)
                    || !exactIntegerInRange(module.settings.value(QStringLiteral("control_protocol")), 1, 1)
                    || !QDir::isAbsolutePath(dataToken)
                    || !QDir::isAbsolutePath(controlToken) || controlToken == dataToken) {
                    if (error) {
                        *error = QStringLiteral("Webull 变更动作必须锁定 loopback v1 control 及互异的绝对 data/control token 路径");
                    }
                    return false;
                }
            }
        }
        ids.insert(module.id);
    }
    if (ids != QSet<QString>(requiredModules.keyBegin(), requiredModules.keyEnd())) {
        if (error) *error = QStringLiteral("配置必须恰好包含 upload、premium、webull、redemption 四个模块");
        return false;
    }
    return true;
}

const ModuleConfig *AppConfig::module(const QString &id) const {
    for (const auto &candidate : modules) {
        if (candidate.id == id) return &candidate;
    }
    return nullptr;
}

} // namespace hub
