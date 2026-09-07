#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QRegularExpression>
#include <QSocketNotifier>
#include <QTextStream>
#include <QThread>
#include <QTimeZone>
#include <QTimer>
#include <QtEndian>

#include <csignal>
#include <unistd.h>
#include <utility>

namespace {

const QString kWindExecutable = QStringLiteral(
    "/Applications/WindPersonFree.app/Contents/MacOS/WindPersonFree");
const QString kProbeName = QStringLiteral("libmachome_wind_tbapi_probe.dylib");

QString escapedLldbString(QString value) {
    value.replace(u'\\', QStringLiteral("\\\\"));
    value.replace(u'"', QStringLiteral("\\\""));
    value.replace(u'\n', QStringLiteral("\\n"));
    value.replace(u'\r', QStringLiteral("\\r"));
    return value;
}

bool exactHex(const QString &value) {
    static const QRegularExpression pattern(QStringLiteral("^[0-9a-fA-F]*$"));
    return value.size() % 2 == 0 && pattern.match(value).hasMatch();
}

bool decodeRawCapture(const QJsonObject &capture, QJsonObject *payload,
                      QString *error) {
    if (capture.value(QStringLiteral("error_code")).toInt(-1) != 0) {
        if (error) *error = QStringLiteral("Wind callback 返回错误");
        return false;
    }
    const QString fieldHex = capture.value(QStringLiteral("field_info"))
                                 .toObject().value(QStringLiteral("hex")).toString();
    const QString dataHex = capture.value(QStringLiteral("buffer_58"))
                                .toObject().value(QStringLiteral("hex")).toString();
    if (!exactHex(fieldHex) || !exactHex(dataHex)) {
        if (error) *error = QStringLiteral("原始帧 hex 损坏");
        return false;
    }
    const QByteArray fields = QByteArray::fromHex(fieldHex.toLatin1());
    const QByteArray data = QByteArray::fromHex(dataHex.toLatin1());
    qsizetype position = 0;
    auto require = [error](qsizetype pos, quint64 size, qsizetype total,
                           const QString &label) {
        if (pos < 0 || size > static_cast<quint64>(total)
            || static_cast<quint64>(pos) + size > static_cast<quint64>(total)) {
            if (error) *error = QStringLiteral("原始帧截断：%1").arg(label);
            return false;
        }
        return true;
    };
    auto take16be = [&fields, &position, &require](quint16 *value,
                                                   const QString &label) {
        if (!require(position, 2, fields.size(), label)) return false;
        *value = qFromBigEndian<quint16>(reinterpret_cast<const uchar *>(fields.constData() + position));
        position += 2;
        return true;
    };
    if (!require(0, 2, fields.size(), QStringLiteral("field_count"))) return false;
    const quint16 count = qFromBigEndian<quint16>(reinterpret_cast<const uchar *>(fields.constData()));
    position = 2;
    struct Field { QString name; quint8 type = 0; quint64 width = 0; quint16 offset = 0; };
    QList<Field> descriptors;
    for (quint16 index = 0; index < count; ++index) {
        quint16 nameSize = 0;
        if (!take16be(&nameSize, QStringLiteral("field.name"))
            || !require(position, static_cast<quint64>(nameSize) + 1, fields.size(), QStringLiteral("field.name"))) return false;
        const QByteArray name = fields.mid(position, nameSize);
        position += nameSize;
        if (fields.at(position++) != '\0'
            || !require(position, 1 + 2 + 8 + 8 + 4 + 2 + 2 + 2,
                        fields.size(), QStringLiteral("field.descriptor"))) return false;
        Field field;
        field.name = QString::fromUtf8(name).toLower();
        field.type = static_cast<quint8>(fields.at(position++));
        position += 2;
        field.width = qFromBigEndian<quint64>(reinterpret_cast<const uchar *>(fields.constData() + position));
        position += 8 + 8 + 4;
        field.offset = qFromBigEndian<quint16>(reinterpret_cast<const uchar *>(fields.constData() + position));
        position += 2 + 2 + 2;
        descriptors.append(field);
    }
    if (position != fields.size() || !require(0, 12, data.size(), QStringLiteral("data.header"))) {
        if (error && error->isEmpty()) *error = QStringLiteral("字段描述存在尾随数据");
        return false;
    }
    const uchar *raw = reinterpret_cast<const uchar *>(data.constData());
    const quint32 rowCount = qFromBigEndian<quint32>(raw);
    const quint32 rowSize = qFromBigEndian<quint32>(raw + 4);
    if (rowCount != 1 || rowSize > 1024 * 1024
        || 12ULL + static_cast<quint64>(rowCount) * rowSize != static_cast<quint64>(data.size())) {
        if (error) *error = QStringLiteral("Wind 帧必须且只能包含一行");
        return false;
    }
    QJsonObject values;
    for (const Field &field : descriptors) {
        if (!require(12 + field.offset, field.width, data.size(), field.name)) return false;
        const uchar *value = raw + 12 + field.offset;
        if (field.type == 0x25 && field.width == 4) {
            values.insert(field.name, qFromLittleEndian<qint32>(value));
        } else if (field.type == 0x27 && field.width == 8) {
            values.insert(field.name, static_cast<double>(qFromLittleEndian<qint64>(value)));
        }
    }
    for (const QString &required : {QStringLiteral("etfbuynumber"),
                                    QStringLiteral("etfbuyamount"),
                                    QStringLiteral("etfsellnumber"),
                                    QStringLiteral("etfsellamount")}) {
        if (!values.value(required).isDouble()) {
            if (error) *error = QStringLiteral("Wind 帧缺少字段 %1").arg(required);
            return false;
        }
    }
    const qint64 milliseconds = capture.value(QStringLiteral("callback_epoch_ms")).toInteger();
    *payload = {{QStringLiteral("windcode"),
                 capture.value(QStringLiteral("requested_windcode"))},
                {QStringLiteral("observed_at"), QDateTime::fromMSecsSinceEpoch(
                     milliseconds, QTimeZone::UTC).toString(Qt::ISODateWithMs)},
                {QStringLiteral("sub_id"), capture.value(QStringLiteral("sub_id"))},
                {QStringLiteral("callback_seq"), capture.value(QStringLiteral("callback_seq"))},
                {QStringLiteral("values"), values}};
    return true;
}

void output(const QJsonObject &object) {
    QFile stream;
    if (!stream.open(stdout, QIODevice::WriteOnly)) return;
    QByteArray bytes = QJsonDocument(object).toJson(QJsonDocument::Compact);
    bytes.append('\n');
    stream.write(bytes);
    stream.flush();
}

QString sha256(const QString &path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    QCryptographicHash digest(QCryptographicHash::Sha256);
    if (!digest.addData(&file)) return {};
    return QString::fromLatin1(digest.result().toHex());
}

QList<qint64> windPids() {
    QProcess pgrep;
    pgrep.start(QStringLiteral("/usr/bin/pgrep"),
                {QStringLiteral("-x"), QStringLiteral("WindPersonFree")});
    if (!pgrep.waitForFinished(5000)) return {};
    QList<qint64> accepted;
    for (const QByteArray &line : pgrep.readAllStandardOutput().split('\n')) {
        bool ok = false;
        const qint64 pid = line.trimmed().toLongLong(&ok);
        if (!ok || pid <= 0) continue;
        QProcess ps;
        ps.start(QStringLiteral("/bin/ps"),
                 {QStringLiteral("-p"), QString::number(pid), QStringLiteral("-o"),
                  QStringLiteral("command=")});
        if (!ps.waitForFinished(5000)) continue;
        if (QString::fromUtf8(ps.readAllStandardOutput()).contains(kWindExecutable)) {
            accepted.append(pid);
        }
    }
    return accepted;
}

bool tbapiLoaded(qint64 pid) {
    QProcess lsof;
    lsof.start(QStringLiteral("/usr/sbin/lsof"),
               {QStringLiteral("-Fn"), QStringLiteral("-p"), QString::number(pid)});
    return lsof.waitForFinished(5000)
        && QString::fromUtf8(lsof.readAllStandardOutput())
               .contains(QStringLiteral("libWind.Cosmos.TBAPI2.dylib"));
}

class Helper final : public QObject {
public:
    Helper(QString mode, QString dataRoot, bool allowWindControl, QObject *parent = nullptr)
        : QObject(parent), mode_(std::move(mode)), dataRoot_(std::move(dataRoot)),
          allowWindControl_(allowWindControl) {
        notifier_ = new QSocketNotifier(fileno(stdin), QSocketNotifier::Read, this);
        connect(notifier_, &QSocketNotifier::activated, this, [this] { readCommand(); });
        // Match the standalone production service's
        // refresh_interval_seconds=1.0.  Both the capture-file poll and the
        // TBAPI subscription interval use the same 1000 ms cadence.
        poll_.setInterval(1000);
        connect(&poll_, &QTimer::timeout, this, [this] { pollCaptures(); });
        poll_.start();
        output({{QStringLiteral("type"), QStringLiteral("hello")},
                {QStringLiteral("protocol"), 1},
                {QStringLiteral("service"), QStringLiteral("machome-wind-probe-helper")},
                {QStringLiteral("mode"), mode_},
                {QStringLiteral("poll_interval_ms"), poll_.interval()},
                {QStringLiteral("never_sigkill_wind"), true},
                {QStringLiteral("legacy_dependency"), false}});
        publishStatus();
    }

private:
    struct Session {
        QString symbol;
        qint64 pid = -1;
        QString dylibPath;
        qint64 subId = -1;
    };

    void fail(const QString &code, const QString &message,
              const QJsonObject &command = {}) {
        QJsonObject result{{QStringLiteral("type"), QStringLiteral("error")},
                           {QStringLiteral("code"), code},
                           {QStringLiteral("message"), message},
                           {QStringLiteral("fail_closed"), true}};
        if (!command.value(QStringLiteral("action")).toString().isEmpty())
            result.insert(QStringLiteral("action"), command.value(QStringLiteral("action")));
        if (!command.value(QStringLiteral("request_id")).toString().isEmpty())
            result.insert(QStringLiteral("request_id"), command.value(QStringLiteral("request_id")));
        output(result);
    }

    void commandResult(const QJsonObject &command, bool ok, const QString &message,
                       const QJsonObject &details = {}) {
        QJsonObject result{{QStringLiteral("type"), QStringLiteral("command_result")},
                           {QStringLiteral("action"), command.value(QStringLiteral("action"))},
                           {QStringLiteral("request_id"), command.value(QStringLiteral("request_id"))},
                           {QStringLiteral("ok"), ok},
                           {QStringLiteral("message"), message},
                           {QStringLiteral("details"), details}};
        output(result);
    }

    bool waitForWindState(bool running, int timeoutSeconds) const {
        const qint64 deadline = QDateTime::currentMSecsSinceEpoch()
            + static_cast<qint64>(timeoutSeconds) * 1000;
        do {
            if ((!windPids().isEmpty()) == running) return true;
            QThread::msleep(250);
        } while (QDateTime::currentMSecsSinceEpoch() < deadline);
        return ((!windPids().isEmpty()) == running);
    }

    int cleanupRawProbeCopies() const {
        QDir raw(rawDirectory());
        int deleted = 0;
        for (const QFileInfo &info : raw.entryInfoList(
                 {QStringLiteral("libmachome_wind_*.dylib")},
                 QDir::Files | QDir::NoSymLinks, QDir::Name)) {
            if (QFile::remove(info.absoluteFilePath())) ++deleted;
        }
        return deleted;
    }

    QString packagedProbePath() const {
        return QDir(QCoreApplication::applicationDirPath()).filePath(kProbeName);
    }

    bool abiManifestValid(QString *error) const {
        const QString path = QDir(dataRoot_).filePath(QStringLiteral("abi-manifest.json"));
        QFileInfo info(path);
        if (!info.isFile() || info.isSymLink()) {
            if (error) *error = QStringLiteral("缺少非符号链接 ABI manifest");
            return false;
        }
        QFile file(path);
        if (!file.open(QIODevice::ReadOnly)) {
            if (error) *error = QStringLiteral("无法读取 ABI manifest");
            return false;
        }
        const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
        if (!document.isObject()) {
            if (error) *error = QStringLiteral("ABI manifest JSON 损坏");
            return false;
        }
        const QJsonObject manifest = document.object();
        if (manifest.value(QStringLiteral("schema_version")).toInt() != 1
            || !manifest.value(QStringLiteral("approved")).toBool(false)) {
            if (error) *error = QStringLiteral("ABI manifest 未经显式批准");
            return false;
        }
        const QString expectedWind = manifest.value(QStringLiteral("wind_executable_sha256"))
                                         .toString().toLower();
        if (expectedWind.size() != 64 || sha256(kWindExecutable) != expectedWind) {
            if (error) *error = QStringLiteral("Wind 可执行文件与 ABI manifest 不匹配");
            return false;
        }
        const QString probe = packagedProbePath();
        const QString expectedProbe = manifest.value(QStringLiteral("probe_sha256"))
                                          .toString().toLower();
        QFileInfo probeInfo(probe);
        if (!probeInfo.isAbsolute() || !probeInfo.isFile() || probeInfo.isSymLink()
            || expectedProbe.size() != 64 || sha256(probe) != expectedProbe) {
            if (error) *error = QStringLiteral("包内探针与 ABI manifest 不匹配");
            return false;
        }
        return true;
    }

    QString runLldb(qint64 pid, const QStringList &commands, QString *error) const {
        QStringList arguments{QStringLiteral("--batch"), QStringLiteral("-p"),
                              QString::number(pid)};
        for (const QString &command : commands)
            arguments.append({QStringLiteral("-o"), command});
        QProcess process;
        process.setProcessChannelMode(QProcess::MergedChannels);
        process.start(QStringLiteral("/usr/bin/lldb"), arguments);
        if (!process.waitForStarted(5000)) {
            if (error) *error = QStringLiteral("无法启动 lldb");
            return {};
        }
        if (!process.waitForFinished(qMin(45'000, 15'000 + commands.size() * 1500))) {
            process.kill(); // Only terminate the disposable lldb child, never Wind.
            process.waitForFinished(3000);
            ::kill(static_cast<pid_t>(pid), SIGCONT);
            if (error) *error = QStringLiteral("lldb 执行超时，已恢复 Wind 运行：%1")
                                    .arg(QString::fromUtf8(process.readAll()).right(2000));
            return {};
        }
        const QString result = QString::fromUtf8(process.readAll());
        if (process.exitCode() != 0
            || !result.contains(QStringLiteral("Process %1 detached").arg(pid))) {
            ::kill(static_cast<pid_t>(pid), SIGCONT);
            if (error) *error = QStringLiteral("lldb 失败或未确认 detach：%1")
                                    .arg(result.right(2000));
            return {};
        }
        return result;
    }

    static qint64 lldbInteger(const QString &outputText, const QString &name,
                              bool *ok = nullptr) {
        const QRegularExpression matchExpression(
            QStringLiteral("%1\\s*=\\s*(-?\\d+)")
                .arg(QRegularExpression::escape(name)));
        const QRegularExpressionMatch match = matchExpression.match(outputText);
        bool converted = false;
        const qint64 value = match.hasMatch() ? match.captured(1).toLongLong(&converted) : -1;
        if (ok) *ok = converted;
        return value;
    }

    QString rawDirectory() const {
        // Wind is sandboxed. Both dlopen and callback output must stay inside
        // its own container, as in the original monitor. Use a Hub-specific
        // directory so cleanup cannot touch the original monitor's probes.
        if (mode_ == QStringLiteral("live"))
            return QDir::home().filePath(QStringLiteral(
                "Library/Containers/com.windin.mac.free/Data/tmp/machome-hub-probe"));
        return QDir(dataRoot_).filePath(QStringLiteral("raw-captures"));
    }

    bool liveBoundaryReady(QString *error) const {
        if (!abiManifestValid(error)) return false;
        const QList<qint64> pids = windPids();
        if (pids.isEmpty()) {
            if (error) *error = QStringLiteral("未找到经过路径校验的 WindPersonFree");
            return false;
        }
        if (!tbapiLoaded(pids.first())) {
            if (error) *error = QStringLiteral("Wind TBAPI2 尚未加载");
            return false;
        }
        QFileInfo rootInfo(dataRoot_);
        if (!rootInfo.isAbsolute() || rootInfo.isSymLink()
            || !QDir().mkpath(rawDirectory())) {
            if (error) *error = QStringLiteral("无法创建包内原始采集目录");
            return false;
        }
        QFileInfo rawInfo(rawDirectory());
        if (!rawInfo.isDir() || rawInfo.isSymLink()) {
            if (error) *error = QStringLiteral("原始采集目录不安全");
            return false;
        }
        if (!QFile::setPermissions(rawDirectory(), QFileDevice::ReadOwner
                | QFileDevice::WriteOwner | QFileDevice::ExeOwner)) {
            if (error) *error = QStringLiteral("无法保护 Wind 容器内的采集目录");
            return false;
        }
        return true;
    }

    bool subscribeLive(const QStringList &symbols, QString *error) {
        if (!liveBoundaryReady(error)) return false;
        if (!sessions_.isEmpty() && !unsubscribeLive(error)) return false;
        const qint64 pid = windPids().first();
        QList<Session> stale;
        const QDir raw(rawDirectory());
        for (const QFileInfo &info : raw.entryInfoList(
                 {QStringLiteral("libmachome_wind_*_%1_*.dylib").arg(pid)},
                 QDir::Files | QDir::NoSymLinks, QDir::Name)) {
            stale.append(Session{{}, pid, info.absoluteFilePath(), -1});
        }
        if (!stale.isEmpty()) {
            if (!unsubscribePaths(pid, stale, error)) return false;
            for (const Session &session : std::as_const(stale)) QFile::remove(session.dylibPath);
        }
        QStringList commands;
        QList<Session> planned;
        const QString outputDir = escapedLldbString(rawDirectory());
        int index = 0;
        for (const QString &symbol : symbols) {
            static const QRegularExpression symbolPattern(QStringLiteral("^[0-9]{6}\\.S[ZH]$"));
            if (!symbolPattern.match(symbol).hasMatch()) {
                if (error) *error = QStringLiteral("不安全的 Wind 代码：%1").arg(symbol);
                return false;
            }
            const QString safe = QString(symbol).replace(u'.', u'_');
            QFile::remove(QDir(rawDirectory()).filePath(
                QStringLiteral("wind_tbapi_live_%1_status.json").arg(safe)));
            const QString copy = QDir(rawDirectory()).filePath(
                QStringLiteral("libmachome_wind_%1_%2_%3.dylib")
                    .arg(safe).arg(pid).arg(QDateTime::currentMSecsSinceEpoch() + index));
            if (!QFile::copy(packagedProbePath(), copy)) {
                if (error) *error = QStringLiteral("无法创建唯一的包内探针副本");
                return false;
            }
            QFile::setPermissions(copy, QFileDevice::ReadOwner | QFileDevice::WriteOwner
                                         | QFileDevice::ExeOwner);
            const QString escapedPath = escapedLldbString(copy);
            const QString escapedSymbol = escapedLldbString(symbol);
            commands.append({
                QStringLiteral("expr -- void *$mh%1 = (void *)dlopen(\"%2\", 0x6)").arg(index).arg(escapedPath),
                QStringLiteral("expr -- void *$mo%1 = $mh%1 ? (void *)dlsym($mh%1, \"wind_tbapi_set_output_dir\") : (void *)0").arg(index),
                QStringLiteral("expr -- long long $mov%1 = $mo%1 ? ((long long (*)(const char *))$mo%1)(\"%2\") : -9002").arg(index).arg(outputDir),
                QStringLiteral("expr -- void *$mf%1 = $mh%1 ? (void *)dlsym($mh%1, \"wind_tbapi_subscribe\") : (void *)0").arg(index),
                QStringLiteral("expr -- long long $mr%1 = $mf%1 ? ((long long (*)(const char *, int))$mf%1)(\"%2\", 1000) : -9002").arg(index).arg(escapedSymbol),
                QStringLiteral("expr -- void *$mi%1 = $mh%1 ? (void *)dlsym($mh%1, \"wind_tbapi_subscription_id\") : (void *)0").arg(index),
                QStringLiteral("expr -- long long $mid%1 = $mi%1 ? ((long long (*)(void))$mi%1)() : -9002").arg(index),
                QStringLiteral("expr -- $mov%1").arg(index),
                QStringLiteral("expr -- $mr%1").arg(index),
                QStringLiteral("expr -- $mid%1").arg(index)});
            planned.append(Session{symbol, pid, copy, -1});
            ++index;
        }
        commands.append(QStringLiteral("process detach"));
        const QString lldb = runLldb(pid, commands, error);
        if (lldb.isEmpty()) return false;
        bool allOk = true;
        for (int i = 0; i < planned.size(); ++i) {
            bool outputOk = false, callOk = false, idOk = false;
            const qint64 outputResult = lldbInteger(lldb, QStringLiteral("$mov%1").arg(i), &outputOk);
            const qint64 callResult = lldbInteger(lldb, QStringLiteral("$mr%1").arg(i), &callOk);
            const qint64 id = lldbInteger(lldb, QStringLiteral("$mid%1").arg(i), &idOk);
            const QString statusPath = QDir(rawDirectory()).filePath(
                QStringLiteral("wind_tbapi_live_%1_status.json")
                    .arg(QString(planned[i].symbol).replace(u'.', u'_')));
            QFile statusFile(statusPath);
            QJsonObject status;
            if (statusFile.open(QIODevice::ReadOnly))
                status = QJsonDocument::fromJson(statusFile.readAll()).object();
            if (!outputOk || outputResult < 0 || !callOk || callResult < 0
                || !idOk || id < 0
                || !QStringList{QStringLiteral("modify_target"), QStringLiteral("subscribed")}
                        .contains(status.value(QStringLiteral("status")).toString())
                || status.value(QStringLiteral("code")).toInteger(-1) < 0) {
                allOk = false;
                continue;
            }
            planned[i].subId = id;
            sessions_.insert(planned[i].symbol, planned[i]);
        }
        if (!allOk) {
            QString stopError;
            if (unsubscribePaths(pid, planned, &stopError)) {
                for (const Session &session : std::as_const(planned))
                    QFile::remove(session.dylibPath);
            }
            sessions_.clear();
            if (error) *error = QStringLiteral("至少一个 TBAPI2 订阅未通过原生状态校验");
            return false;
        }
        subscribed_ = true;
        return true;
    }

    bool unsubscribePaths(qint64 pid, const QList<Session> &sessions, QString *error) {
        if (sessions.isEmpty()) return true;
        if (!windPids().contains(pid)) return true; // A restarted Wind cannot retain old images.
        QStringList commands;
        for (int i = 0; i < sessions.size(); ++i) {
            const QString path = escapedLldbString(sessions[i].dylibPath);
            commands.append({
                QStringLiteral("expr -- void *$sh%1 = (void *)dlopen(\"%2\", 0x6)").arg(i).arg(path),
                QStringLiteral("expr -- void *$sf%1 = $sh%1 ? (void *)dlsym($sh%1, \"wind_tbapi_stop\") : (void *)0").arg(i),
                QStringLiteral("expr -- long long $sr%1 = $sf%1 ? ((long long (*)(void))$sf%1)() : -9002").arg(i),
                QStringLiteral("expr -- $sr%1").arg(i)});
        }
        commands.append(QStringLiteral("process detach"));
        const QString result = runLldb(pid, commands, error);
        if (result.isEmpty()) return false;
        for (int i = 0; i < sessions.size(); ++i) {
            bool ok = false;
            if (lldbInteger(result, QStringLiteral("$sr%1").arg(i), &ok) < 0 || !ok) {
                if (error) *error = QStringLiteral("TBAPI2 停订未确认");
                return false;
            }
        }
        return true;
    }

    bool unsubscribeLive(QString *error) {
        if (sessions_.isEmpty()) { subscribed_ = false; return true; }
        QHash<qint64, QList<Session>> grouped;
        for (const Session &session : std::as_const(sessions_))
            grouped[session.pid].append(session);
        for (auto it = grouped.constBegin(); it != grouped.constEnd(); ++it) {
            if (!unsubscribePaths(it.key(), it.value(), error)) return false;
        }
        for (const Session &session : std::as_const(sessions_)) QFile::remove(session.dylibPath);
        sessions_.clear();
        subscribed_ = false;
        return true;
    }

    void publishStatus() {
        const QList<qint64> pids = windPids();
        bool tbapi = false;
        QJsonArray values;
        for (qint64 pid : pids) { values.append(pid); tbapi = tbapi || tbapiLoaded(pid); }
        QString manifestError;
        const bool abi = mode_ == QStringLiteral("fixture") || abiManifestValid(&manifestError);
        output({{QStringLiteral("type"), QStringLiteral("status")},
                {QStringLiteral("state"), abi ? QStringLiteral("ready") : QStringLiteral("blocked")},
                {QStringLiteral("wind_running"), !pids.isEmpty()},
                {QStringLiteral("wind_pids"), values},
                {QStringLiteral("tbapi_loaded"), tbapi},
                {QStringLiteral("abi_verified"), abi},
                {QStringLiteral("abi_error"), manifestError}});
    }

    void readCommand() {
        char bytes[4096];
        const ssize_t count = ::read(fileno(stdin), bytes, sizeof(bytes));
        if (count == 0) {
            QString error;
            if (mode_ != QStringLiteral("live") || unsubscribeLive(&error))
                QCoreApplication::quit();
            else fail(QStringLiteral("eof_unsubscribe_unconfirmed"), error);
            return;
        }
        if (count < 0) return;
        commandBuffer_.append(bytes, count);
        while (true) {
            const qsizetype newline = commandBuffer_.indexOf('\n');
            if (newline < 0) break;
            const QByteArray line = commandBuffer_.left(newline).trimmed();
            commandBuffer_.remove(0, newline + 1);
            if (!line.isEmpty()) handleCommandLine(line);
        }
    }

    void handleCommandLine(const QByteArray &line) {
        const QJsonDocument document = QJsonDocument::fromJson(line);
        if (!document.isObject()) { fail(QStringLiteral("invalid_json"), QStringLiteral("命令不是 JSON 对象")); return; }
        const QJsonObject command = document.object();
        const QString action = command.value(QStringLiteral("action")).toString();
        if (action == QStringLiteral("quit")) {
            QString error;
            if (mode_ == QStringLiteral("live") && !unsubscribeLive(&error)) {
                fail(QStringLiteral("quit_unsubscribe_unconfirmed"), error);
                return;
            }
            QCoreApplication::quit();
        } else if (action == QStringLiteral("status")) {
            publishStatus();
        } else if (action == QStringLiteral("start_wind")) {
            if (!allowWindControl_) {
                fail(QStringLiteral("wind_control_disabled"), QStringLiteral("未启用 Wind 外部控制门禁"), command);
            } else {
                if (windPids().isEmpty()) {
                    QProcess process;
                    process.start(QStringLiteral("/usr/bin/open"),
                                  {QStringLiteral("-b"), QStringLiteral("com.windin.mac.free")});
                    if (!process.waitForFinished(15000) || process.exitCode() != 0) {
                        fail(QStringLiteral("wind_start_failed"), QStringLiteral("Wind 温和启动失败"), command);
                        return;
                    }
                }
                if (!waitForWindState(true, 30)) {
                    fail(QStringLiteral("wind_start_timeout"),
                         QStringLiteral("30 秒内未确认 Wind 启动"), command);
                    return;
                }
                publishStatus();
                commandResult(command, true, QStringLiteral("已确认 Wind 进程启动"),
                              {{QStringLiteral("wind_running"), true}});
            }
        } else if (action == QStringLiteral("shutdown_wind")) {
            if (!allowWindControl_) {
                fail(QStringLiteral("wind_control_disabled"), QStringLiteral("未启用 Wind 外部控制门禁"), command);
            } else {
                QString unsubscribeError;
                if (mode_ == QStringLiteral("live") && !unsubscribeLive(&unsubscribeError)) {
                    fail(QStringLiteral("shutdown_unsubscribe_unconfirmed"),
                         unsubscribeError, command);
                    return;
                }
                const QList<qint64> pids = windPids();
                for (qint64 pid : pids) ::kill(static_cast<pid_t>(pid), SIGTERM);
                // Deliberately no SIGKILL. Cleanup is permitted only after an
                // observed graceful exit, exactly as in the original host.
                output({{QStringLiteral("type"), QStringLiteral("status")},
                        {QStringLiteral("state"), QStringLiteral("quitting")},
                        {QStringLiteral("wind_running"), !pids.isEmpty()},
                        {QStringLiteral("never_sigkill_wind"), true}});
                if (!waitForWindState(false, 30)) {
                    fail(QStringLiteral("wind_shutdown_timeout"),
                         QStringLiteral("30 秒内未确认 Wind 完全退出；未执行清理且绝不 SIGKILL"),
                         command);
                    return;
                }
                const int deleted = cleanupRawProbeCopies();
                output({{QStringLiteral("type"), QStringLiteral("status")},
                        {QStringLiteral("state"), QStringLiteral("cleaned")},
                        {QStringLiteral("wind_running"), false},
                        {QStringLiteral("tbapi_loaded"), false},
                        {QStringLiteral("cleanup_deleted_count"), deleted},
                        {QStringLiteral("never_sigkill_wind"), true}});
                commandResult(command, true, QStringLiteral("已确认 Wind 完全退出并完成安全清理"),
                              {{QStringLiteral("wind_running"), false},
                               {QStringLiteral("cleanup_deleted_count"), deleted},
                               {QStringLiteral("never_sigkill_wind"), true}});
            }
        } else if (action == QStringLiteral("warmup")) {
            if (mode_ == QStringLiteral("fixture")) {
                commandResult(command, true, QStringLiteral("fixture 预热与停订已确认"),
                              {{QStringLiteral("warmup_completed"), true},
                               {QStringLiteral("unsubscribe_confirmed"), true}});
            } else if (mode_ == QStringLiteral("live")) {
                QString error;
                QStringList symbols;
                for (const QJsonValue &value : command.value(QStringLiteral("symbols")).toArray())
                    symbols.append(value.toString().trimmed().toUpper());
                if (symbols.isEmpty()) {
                    fail(QStringLiteral("empty_warmup_watchlist"),
                         QStringLiteral("预热必须指定至少一个标的"), command);
                } else if (!liveBoundaryReady(&error)) {
                    fail(QStringLiteral("abi_or_runtime_not_ready"), error, command);
                } else if (!subscribeLive({symbols.first()}, &error)) {
                    fail(QStringLiteral("warmup_subscribe_failed"), error, command);
                } else if (!unsubscribeLive(&error)) {
                    fail(QStringLiteral("warmup_unsubscribe_unconfirmed"), error, command);
                } else {
                    publishStatus();
                    commandResult(command, true, QStringLiteral("TBAPI 临时订阅预热与停订已确认"),
                                  {{QStringLiteral("warmup_completed"), true},
                                   {QStringLiteral("unsubscribe_confirmed"), true},
                                   {QStringLiteral("symbol"), symbols.first()}});
                }
            } else fail(QStringLiteral("helper_disabled"), QStringLiteral("Wind helper 未启用"), command);
        } else if (action == QStringLiteral("subscribe")) {
            if (mode_ == QStringLiteral("fixture")) {
                subscribed_ = true;
                output({{QStringLiteral("type"), QStringLiteral("status")},
                        {QStringLiteral("state"), QStringLiteral("subscribed")},
                        {QStringLiteral("subscription_count"),
                         command.value(QStringLiteral("symbols")).toArray().size()},
                        {QStringLiteral("abi_verified"), true}});
                pollCaptures();
            } else if (mode_ == QStringLiteral("live")) {
                QString error;
                QStringList symbols;
                for (const QJsonValue &value : command.value(QStringLiteral("symbols")).toArray())
                    symbols.append(value.toString().trimmed().toUpper());
                if (symbols.isEmpty()) fail(QStringLiteral("empty_watchlist"), QStringLiteral("观察列表为空"));
                else if (!subscribeLive(symbols, &error)) fail(QStringLiteral("subscribe_failed"), error);
                else output({{QStringLiteral("type"), QStringLiteral("status")},
                             {QStringLiteral("state"), QStringLiteral("subscribed")},
                             {QStringLiteral("subscription_count"), sessions_.size()},
                             {QStringLiteral("wind_running"), true},
                             {QStringLiteral("tbapi_loaded"), true},
                             {QStringLiteral("abi_verified"), true}});
            } else fail(QStringLiteral("helper_disabled"), QStringLiteral("Wind helper 未启用"));
        } else if (action == QStringLiteral("unsubscribe")) {
            QString error;
            if (mode_ == QStringLiteral("live") && !unsubscribeLive(&error)) {
                fail(QStringLiteral("unsubscribe_unconfirmed"), error, command);
            } else {
                subscribed_ = false;
                output({{QStringLiteral("type"), QStringLiteral("status")},
                        {QStringLiteral("state"), QStringLiteral("ready")},
                        {QStringLiteral("unsubscribed"), true},
                        {QStringLiteral("request_id"), command.value(QStringLiteral("request_id"))}});
                commandResult(command, true, QStringLiteral("Wind TBAPI2 停订已确认"),
                              {{QStringLiteral("unsubscribe_confirmed"), true}});
            }
        } else {
            fail(QStringLiteral("unsupported_action"), QStringLiteral("不支持的 helper 动作"));
        }
    }

    void pollCaptures() {
        if (!subscribed_) return;
        const bool live = mode_ == QStringLiteral("live");
        const QDir directory(live ? rawDirectory()
                                  : QDir(dataRoot_).filePath(QStringLiteral("captures")));
        for (const QFileInfo &info : directory.entryInfoList(
                 {live ? QStringLiteral("wind_tbapi_live_[0-9]*_S?.json")
                       : QStringLiteral("*.json")}, QDir::Files | QDir::NoSymLinks,
                 QDir::Name)) {
            if (seenMtime_.value(info.absoluteFilePath()) >= info.lastModified().toMSecsSinceEpoch()) continue;
            QFile file(info.absoluteFilePath());
            if (!file.open(QIODevice::ReadOnly)) continue;
            const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
            seenMtime_.insert(info.absoluteFilePath(), info.lastModified().toMSecsSinceEpoch());
            if (!document.isObject()) {
                fail(QStringLiteral("capture_invalid"), QStringLiteral("捕获 JSON 损坏"));
                continue;
            }
            QJsonObject payload = document.object();
            if (live) {
                QString error;
                QJsonObject decoded;
                if (!decodeRawCapture(payload, &decoded, &error)) {
                    fail(QStringLiteral("capture_decode_failed"), error);
                    continue;
                }
                payload = decoded;
            } else if (payload.contains(QStringLiteral("field_info"))) {
                QString error;
                QJsonObject decoded;
                if (!decodeRawCapture(payload, &decoded, &error)) {
                    fail(QStringLiteral("capture_decode_failed"), error);
                    continue;
                }
                payload = decoded;
            }
            output({{QStringLiteral("type"), QStringLiteral("capture")},
                    {QStringLiteral("payload"), payload}});
        }
    }

    QString mode_;
    QString dataRoot_;
    bool allowWindControl_ = false;
    bool subscribed_ = false;
    QHash<QString, qint64> seenMtime_;
    QHash<QString, Session> sessions_;
    QByteArray commandBuffer_;
    QSocketNotifier *notifier_ = nullptr;
    QTimer poll_;
};

} // namespace

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    const QStringList args = app.arguments();
    auto value = [&args](const QString &flag, const QString &fallback = {}) {
        const int index = args.indexOf(flag);
        return index >= 0 && index + 1 < args.size() ? args.at(index + 1) : fallback;
    };
    if (!args.contains(QStringLiteral("--stdio"))) return 64;
    const QString mode = value(QStringLiteral("--mode"), QStringLiteral("disabled"));
    if (!QStringList{QStringLiteral("disabled"), QStringLiteral("fixture"),
                     QStringLiteral("live")}.contains(mode)) return 64;
    Helper helper(mode, value(QStringLiteral("--data-root")),
                  args.contains(QStringLiteral("--allow-wind-control")));
    return app.exec();
}
