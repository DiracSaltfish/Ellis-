#include <QCoreApplication>
#include <QSaveFile>
#include <QLockFile>
#include <QUuid>
#include <QElapsedTimer>
#include <memory>
#include <cstring>
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
    const quint32 dataSize = qFromBigEndian<quint32>(raw + 4);
    if (rowCount < 1 || rowCount > 256 || dataSize > 1024 * 1024
        || dataSize % rowCount || 12ULL + dataSize != static_cast<quint64>(data.size())) {
        if (error) *error = QStringLiteral("Wind 数据行数或长度无效");
        return false;
    }
    const quint32 rowSize = dataSize / rowCount;
    // Some subscriptions return duplicate rows. Never choose between conflicting rows.
    for (quint32 row = 1; row < rowCount; ++row) {
        if (memcmp(raw + 12, raw + 12 + row * rowSize, rowSize) != 0) {
            if (error) *error = QStringLiteral("Wind 多行数据冲突，拒绝覆盖");
            return false;
        }
    }
    QJsonObject values;
    for (const Field &field : descriptors) {
        if (!require(field.offset, field.width, rowSize, field.name)) return false;
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
        // File delivery cadence is independent of the native subscription latency.
        poll_.setInterval(1000);
        connect(&poll_, &QTimer::timeout, this, [this] {
            if(mode_=="live" && batchPid_>0 && !renewBatchLease()) fail("lease_write_failed","无法续约 Wind 订阅");
            pollCaptures();
        });
        poll_.start();
        addTimer_.setInterval(500);
        connect(&addTimer_, &QTimer::timeout, this, [this] { addNextSymbol(); });
        addTimer_.start();
        output({{QStringLiteral("type"), QStringLiteral("hello")},
                {QStringLiteral("protocol"), 1},
                {QStringLiteral("service"), QStringLiteral("machome-wind-probe-helper")},
                {QStringLiteral("mode"), mode_},
                {QStringLiteral("poll_interval_ms"), poll_.interval()},
                {QStringLiteral("subscription_mode"), QStringLiteral("in_process_batch_v1")},
                {QStringLiteral("legacy_latency_ms"), 500},
                {QStringLiteral("hk_latency_ms"), 5000},
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
        const QString batch=QDir(QCoreApplication::applicationDirPath()).filePath("libmachome_wind_batch_probe.dylib");
        const QString tbapi="/Applications/WindPersonFree.app/Contents/Frameworks/libWind.Cosmos.TBAPI2.dylib";
        if(QFileInfo(batch).isSymLink() || manifest.value("batch_probe_sha256").toString().size()!=64
           || sha256(batch)!=manifest.value("batch_probe_sha256").toString()
           || sha256(tbapi)!=manifest.value("tbapi_sha256").toString()) {
            if(error)*error="批量探针或 TBAPI 与 ABI manifest 不匹配";return false;
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

    QString batchDirectory() const {
        return QDir(rawDirectory()).filePath(QStringLiteral("batch-v1-%1").arg(batchPid_));
    }
    QJsonObject batchState() const {
        QFile f(QDir(batchDirectory()).filePath("state.json"));
        if (!f.open(QIODevice::ReadOnly)) return {};
        auto s=QJsonDocument::fromJson(f.readAll()).object();
        if (s.value("pid").toInteger()!=batchPid_ || s.value("protocol").toInt()!=1
            || QDateTime::currentMSecsSinceEpoch()/1000.0-s.value("time").toDouble()>5) return {};
        return s;
    }
    bool renewBatchLease() const {
        if (batchPid_<0) return true;
        QSaveFile f(QDir(batchDirectory()).filePath("lease"));
        return f.open(QIODevice::WriteOnly) && f.write("alive")==5 && f.commit();
    }
    bool batchCommand(const QStringList &symbols, QString *id, QString *error) {
        *id=QUuid::createUuid().toString(QUuid::WithoutBraces);
        QJsonObject intervals;
        for (const auto &s:symbols) intervals.insert(s,poolSymbols_.contains(s)?5000:500);
        QSaveFile f(QDir(batchDirectory()).filePath("command.json"));
        auto bytes=QJsonDocument(QJsonObject{{"id",*id},{"symbols",QJsonArray::fromStringList(symbols)}, {"intervals",intervals}}).toJson(QJsonDocument::Compact);
        if (!renewBatchLease() || !f.open(QIODevice::WriteOnly) || f.write(bytes)!=bytes.size() || !f.commit()) {
            if(error)*error="无法提交批量订阅命令"; return false;
        }
        return true;
    }
    bool waitBatch(const QStringList &symbols,const QString &id,QString *error) {
        QElapsedTimer timer;timer.start();
        while(timer.elapsed()<45000) {
            if(!renewBatchLease()){if(error)*error="订阅租约写入失败";return false;}
            const auto s=batchState();
            if(s.value("command").toString()==id) {
                if(!s.value("error").toString().isEmpty()){if(error)*error=s.value("error").toString();return false;}
                QHash<QString,Session> found;
                for(const auto &v:s.value("items").toArray()) {
                    const auto row=v.toObject();const auto code=row.value("symbol").toString();
                    if(symbols.contains(code) && row.value("sub_id").toInteger(-1)>=0)
                        found.insert(code,Session{code,batchPid_,{},row.value("sub_id").toInteger()});
                }
                if(found.size()==symbols.size() && s.value("items").toArray().size()==symbols.size()) {
                    sessions_=found;batchInstance_=s.value("instance").toString();return true;
                }
            }
            QThread::msleep(50);
        }
        if(error)*error="进程内订阅命令 45 秒未确认；保留状态以便退订";
        return false;
    }
    bool ensureBatch(QString *error) {
        if(batchPid_>0 && !batchInstance_.isEmpty() && !batchState().isEmpty())return true;
        if(!liveBoundaryReady(error))return false;
        const auto pids=windPids();if(pids.size()!=1){if(error)*error="Wind 进程数必须为一";return false;}
        if(!batchLock_) {
            batchLock_=std::make_unique<QLockFile>(QDir(rawDirectory()).filePath("batch-controller.lock"));
            batchLock_->setStaleLockTime(0);
            if(!batchLock_->tryLock()){batchLock_.reset();if(error)*error="已有批量订阅控制器运行";return false;}
        }
        if(batchPid_!=pids.first()){batchPid_=pids.first();sessions_.clear();batchInstance_.clear();}
        const QString directory=batchDirectory();
        if(QFileInfo(directory).isSymLink() || !QDir().mkpath(directory)){if(error)*error="批量订阅目录不可用";return false;}
        QFile::setPermissions(directory,QFileDevice::ReadOwner|QFileDevice::WriteOwner|QFileDevice::ExeOwner);
        const QString packaged=QDir(QCoreApplication::applicationDirPath()).filePath("libmachome_wind_batch_probe.dylib");
        const QString copy=QDir(directory).filePath("probe.dylib");
        QFile metadata(QDir(directory).filePath("probe.sha256"));QString recorded;
        if(metadata.open(QIODevice::ReadOnly))recorded=QString::fromUtf8(metadata.readAll()).trimmed();
        auto state=batchState();
        if(!state.isEmpty()) {
            if(recorded!=sha256(packaged)){if(error)*error="Wind 已载入其他版本，请先正常重启 Wind";return false;}
            batchInstance_=state.value("instance").toString();return true;
        }
        // Never load a second actor into a process after an uncertain initialization.
        if(QFileInfo::exists(copy)){if(error)*error="已有批量探针但心跳不可用，请正常重启 Wind 后重试";return false;}
        if(!QFile::copy(packaged,copy)){if(error)*error="批量探针复制失败";return false;}
        QSaveFile m(metadata.fileName());if(!m.open(QIODevice::WriteOnly)||m.write(sha256(packaged).toUtf8())!=64||!m.commit()){if(error)*error="探针校验记录失败";return false;}
        QString commandId;
        if(!batchCommand({},&commandId,error))return false;
        const auto result=runLldb(batchPid_,{
            "thread select 1",
            QStringLiteral("expr -- void *$mb = (void *)dlopen(\"%1\", 0x6)").arg(escapedLldbString(copy)),
            "expr -- void *$mbf = $mb ? (void *)dlsym($mb, \"machome_batch_start\") : (void *)0",
            QStringLiteral("expr -- int $mbr = $mbf ? ((int (*)(const char *))$mbf)(\"%1\") : -9002").arg(escapedLldbString(directory)),
            "expr -- $mbr","process detach"},error);
        bool ok=false;if(lldbInteger(result,"$mbr",&ok)!=0||!ok){if(error && error->isEmpty())*error="批量探针初始化失败";return false;}
        return waitBatch({},commandId,error);
    }
    bool subscribeLive(const QStringList &symbols,QString *error,bool additive=false) {
        static const QRegularExpression safe("^[0-9]{6}\\.S[ZH]$");
        for(const auto &s:symbols)if(!safe.match(s).hasMatch()){if(error)*error="不安全的 Wind 代码";return false;}
        if(!ensureBatch(error))return false;
        QStringList target=additive?sessions_.keys():QStringList{};
        for(const auto &s:symbols)if(!target.contains(s))target.append(s);
        if(target.size()>256){if(error)*error="订阅数超过上限";return false;}
        QString id;
        if(!batchCommand(target,&id,error)||!waitBatch(target,id,error))return false;
        subscribed_=true;return true;
    }
    bool unsubscribeLive(QString *error) {
        pendingAdds_.clear();
        if(batchPid_<0 || !windPids().contains(batchPid_)){sessions_.clear();subscribed_=false;return true;}
        QString id;
        if(!batchCommand({},&id,error)||!waitBatch({},id,error))return false;
        subscribed_=false;seenMtime_.clear();return true;
    }

    void publishStatus() {
        const QList<qint64> pids = windPids();
        bool tbapi = false;
        QJsonArray values;
        for (qint64 pid : pids) { values.append(pid); tbapi = tbapi || tbapiLoaded(pid); }
        QString manifestError;
        const bool abi = mode_ == QStringLiteral("fixture") || abiManifestValid(&manifestError);
        output({{QStringLiteral("type"), QStringLiteral("status")},
                {QStringLiteral("state"), abi ? (subscribed_ ? QStringLiteral("subscribed") : QStringLiteral("ready")) : QStringLiteral("blocked")},
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
        } else if (action == QStringLiteral("subscribe_add")) {
            const auto values=command.value("symbols").toArray();
            if (!subscribed_ || values.size()>150) return;
            for (const auto &v:values) {
                const QString s=v.toString().trimmed().toUpper();
                static const QRegularExpression safe(QStringLiteral("^1[0-9]{5}\\.SZ$"));
                if (!safe.match(s).hasMatch()) { output({{"type","pool_subscription"},{"symbol",s},{"ok",false},{"error","Invalid SZ ETF code"}}); continue; }
                if (!poolSymbols_.contains(s)) poolSymbols_.append(s);
                if (!pendingAdds_.contains(s)) pendingAdds_.append(s);
            }
        } else if (action == QStringLiteral("subscribe")) {
            pendingAdds_.clear();
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
            pendingAdds_.clear();
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

    void addNextSymbol() {
        if(!subscribed_ || pendingAdds_.isEmpty())return;
        const QStringList added=pendingAdds_;pendingAdds_.clear();
        QString error;const bool ok=mode_=="fixture" || subscribeLive(added,&error,true);
        for(const auto &symbol:added)output({{"type","pool_subscription"},{"symbol",symbol},{"ok",ok},
            {"sub_id",ok?(mode_=="fixture"?qint64(1):sessions_.value(symbol).subId):qint64(-1)}, {"error",error.left(2000)}});
        pollCaptures();
    }

    void pollCaptures() {
        if (!subscribed_) return;
        const bool live = mode_ == QStringLiteral("live");
        const QDir directory(live ? batchDirectory()
                                  : QDir(dataRoot_).filePath(QStringLiteral("captures")));
        for (const QFileInfo &info : directory.entryInfoList(
                 {live ? QStringLiteral("capture_*.json")
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
                const QString code=payload.value("requested_windcode").toString();
                if (payload.value("instance").toString()!=batchInstance_ || !sessions_.contains(code) || sessions_[code].pid!=payload.value("source_pid").toInteger()
                    || sessions_[code].subId!=payload.value("sub_id").toInteger(-1)) continue;
                // decodeRawCapture provides the canonical symbol; source checks also happen there.
            }
            if (live) {
                QString error;
                QJsonObject decoded;
                if (!decodeRawCapture(payload, &decoded, &error)) {
                    const QString code=payload.value("requested_windcode").toString();
                    if (poolSymbols_.contains(code)) output({{"type","pool_capture_error"},{"symbol",code},{"error",error}});
                    else fail(QStringLiteral("capture_decode_failed"), error);
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

    qint64 batchPid_=-1;
    QString batchInstance_;
    std::unique_ptr<QLockFile> batchLock_;
    QString mode_;
    QString dataRoot_;
    bool allowWindControl_ = false;
    bool subscribed_ = false;
    QHash<QString, qint64> seenMtime_;
    QHash<QString, Session> sessions_;
    QByteArray commandBuffer_;
    QSocketNotifier *notifier_ = nullptr;
    QTimer poll_;
    QTimer addTimer_;
    QStringList pendingAdds_;
    QStringList poolSymbols_;
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
