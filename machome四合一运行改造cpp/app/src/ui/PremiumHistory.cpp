#include "ui/PremiumHistory.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFutureWatcher>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QSaveFile>
#include <QtConcurrent>
#include <set>

namespace hub {
namespace {

struct RetainedRecord {
    QJsonObject object;
    QString occurredAt;
    qint64 signalSequence = 0;
    quint64 arrivalSequence = 0;
    qint64 compactBytes = 0;
};

struct OlderRecord {
    bool operator()(const RetainedRecord &left, const RetainedRecord &right) const {
        if (left.occurredAt != right.occurredAt) return left.occurredAt < right.occurredAt;
        if (left.signalSequence != right.signalSequence) {
            return left.signalSequence < right.signalSequence;
        }
        return left.arrivalSequence < right.arrivalSequence;
    }
};

qint64 compactArrayBytes(qint64 objectBytes, std::size_t count) {
    // [] plus one comma between each pair of objects.
    return 2 + objectBytes + (count > 0 ? static_cast<qint64>(count - 1) : 0);
}

bool canAppendWithin(qint64 current, qint64 additional, qint64 maximum) {
    return additional >= 0 && current >= 0 && current <= maximum
        && additional <= maximum - current;
}

QString ppmText(const QJsonObject &object, const QString &key) {
    if (!object.contains(key) || object.value(key).isNull()) return QStringLiteral("—");
    return QStringLiteral("%1%").arg(object.value(key).toInteger() / 10'000.0, 0, 'f', 3);
}

QString modelText(const QString &model) {
    if (model.isEmpty()) return QStringLiteral("—");
    QStringList names;
    for (const QString &part : model.split(u'+', Qt::SkipEmptyParts)) {
        if (part == QStringLiteral("premium")) names.append(QStringLiteral("溢价率"));
        else if (part == QStringLiteral("pull")) names.append(QStringLiteral("盘口拉涨"));
        else if (part == QStringLiteral("radar")) names.append(QStringLiteral("快速拉涨雷达"));
        else names.append(part);
    }
    return names.join(QStringLiteral(" + "));
}

QString sourceText(const QJsonObject &record) {
    if (record.contains(QStringLiteral("_audit_file"))) {
        return QStringLiteral("%1:%2").arg(record.value(QStringLiteral("_audit_file")).toString())
                                      .arg(record.value(QStringLiteral("_audit_line")).toInteger());
    }
    if (record.value(QStringLiteral("replay")).toBool()) return QStringLiteral("回放");
    if (record.value(QStringLiteral("backfill")).toBool()) return QStringLiteral("30分钟补发");
    return QStringLiteral("实时");
}

QStringList recordValues(const QJsonObject &record) {
    const QDateTime occurred = QDateTime::fromString(
        record.value(QStringLiteral("occurred_at")).toString(), Qt::ISODateWithMs);
    const QString symbol = record.value(QStringLiteral("symbol")).toString();
    return {
        occurred.isValid() ? occurred.toString(QStringLiteral("MM-dd HH:mm:ss.zzz"))
                           : record.value(QStringLiteral("occurred_at")).toString(),
        symbol,
        record.value(QStringLiteral("name")).toString(symbol.left(6)),
        modelText(record.value(QStringLiteral("model")).toString()),
        ppmText(record, QStringLiteral("premium_ppm")),
        ppmText(record, QStringLiteral("rise_30s_ppm")),
        ppmText(record, QStringLiteral("rise_300s_ppm")),
        ppmText(record, QStringLiteral("bid_rise_150s_ppm")),
        ppmText(record, QStringLiteral("bid_rise_300s_ppm")),
        record.value(QStringLiteral("repeat")).toBool() ? QStringLiteral("重复提醒")
                                                         : QStringLiteral("首次触发"),
        sourceText(record),
        record.value(QStringLiteral("reason")).toString(),
    };
}

QByteArray csvCell(QString value) {
    value.replace(u'"', QStringLiteral("\"\""));
    return QByteArrayLiteral("\"") + value.toUtf8() + QByteArrayLiteral("\"");
}

} // namespace

PremiumHistoryLoader::PremiumHistoryLoader(QObject *parent) : QObject(parent) {
    loadWatcher_ = new QFutureWatcher<LoadOutcome>(this);
    exportWatcher_ = new QFutureWatcher<ExportOutcome>(this);
    connect(loadWatcher_, &QFutureWatcher<LoadOutcome>::finished, this, [this] {
        const auto result = loadWatcher_->result();
        if (!result.error.isEmpty()) {
            emit failed(result.error);
            return;
        }
        emit loadFinished(result.records,
                          {{QStringLiteral("files"), result.files},
                           {QStringLiteral("parsed_lines"), result.parsedLines},
                           {QStringLiteral("rejected_lines"), result.rejectedLines},
                           {QStringLiteral("oversized_records"), result.oversizedRecords},
                           {QStringLiteral("dropped_records"), result.droppedRecords},
                           {QStringLiteral("truncated"), result.truncated},
                           {QStringLiteral("truncated_by_record_count"),
                            result.truncatedByRecordCount},
                           {QStringLiteral("truncated_by_returned_bytes"),
                            result.truncatedByReturnedBytes},
                           {QStringLiteral("returned_bytes"), result.returnedBytes},
                           {QStringLiteral("visible_limit"), MaximumRecords},
                           {QStringLiteral("single_record_byte_limit"), MaximumRecordBytes},
                           {QStringLiteral("returned_byte_limit"), MaximumReturnedBytes}});
    });
    connect(exportWatcher_, &QFutureWatcher<ExportOutcome>::finished, this, [this] {
        const auto result = exportWatcher_->result();
        emit exportFinished(result.ok, result.message);
    });
}

PremiumHistoryLoader::~PremiumHistoryLoader() = default;

bool PremiumHistoryLoader::busy() const {
    return loadWatcher_->isRunning() || exportWatcher_->isRunning();
}

void PremiumHistoryLoader::load(const QString &directory, const QDate &from, const QDate &to,
                                const QString &symbolFilter, const QString &modelFilter) {
    if (busy()) {
        emit failed(QStringLiteral("历史记录任务正在运行，请稍候"));
        return;
    }
    if (!from.isValid() || !to.isValid() || from > to) {
        emit failed(QStringLiteral("历史日期范围无效"));
        return;
    }
    const QDir dataDirectory(directory);
    if (!dataDirectory.exists()) {
        emit failed(QStringLiteral("信号审计目录不存在：%1").arg(directory));
        return;
    }
    QStringList files;
    for (const QString &name : dataDirectory.entryList(
             {QStringLiteral("signals-*.jsonl")}, QDir::Files | QDir::Readable, QDir::Name)) {
        const QDate date = QDate::fromString(name.mid(8, 8), QStringLiteral("yyyyMMdd"));
        if (date.isValid() && date >= from && date <= to) {
            files.append(dataDirectory.absoluteFilePath(name));
        }
    }
    emit loadStarted(files.size());
    const QString normalizedSymbol = symbolFilter.trimmed().toUpper();
    const QString normalizedModel = modelFilter.trimmed();
    loadWatcher_->setFuture(QtConcurrent::run(
        [files, normalizedSymbol, normalizedModel]() -> LoadOutcome {
            LoadOutcome result;
            result.files = files.size();
            // The oldest entry is always at begin().  Enforcing both limits
            // after every insertion keeps the working set bounded even when
            // every source line is close to the 2 MiB parser limit.
            std::multiset<RetainedRecord, OlderRecord> records;
            qint64 retainedObjectBytes = 0;
            quint64 arrivalSequence = 0;
            for (const QString &path : files) {
                QFile file(path);
                if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
                    ++result.rejectedLines;
                    continue;
                }
                qint64 lineNumber = 0;
                while (!file.atEnd()) {
                    const QByteArray line = file.readLine(MaximumLineBytes + 1);
                    ++lineNumber;
                    if (line.size() > MaximumLineBytes || (!line.endsWith('\n') && !file.atEnd())) {
                        ++result.rejectedLines;
                        QByteArray discard = line;
                        while (!discard.endsWith('\n') && !file.atEnd()) {
                            discard = file.readLine(64 * 1024);
                        }
                        continue;
                    }
                    QJsonParseError parseError;
                    const auto document = QJsonDocument::fromJson(line, &parseError);
                    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
                        ++result.rejectedLines;
                        continue;
                    }
                    auto object = document.object();
                    if (object.value(QStringLiteral("type")).toString() != QStringLiteral("signal")) continue;
                    ++result.parsedLines;
                    const QString symbol = object.value(QStringLiteral("symbol")).toString().toUpper();
                    const QString model = object.value(QStringLiteral("model")).toString();
                    if (!normalizedSymbol.isEmpty() && !symbol.contains(normalizedSymbol)) continue;
                    if (normalizedModel.startsWith(QStringLiteral("contains:"))) {
                        if (!model.split(u'+', Qt::SkipEmptyParts).contains(normalizedModel.mid(9))) continue;
                    } else if (!normalizedModel.isEmpty() && model != normalizedModel) {
                        continue;
                    }
                    object.insert(QStringLiteral("_audit_file"), QFileInfo(path).fileName());
                    object.insert(QStringLiteral("_audit_line"), lineNumber);
                    const qint64 compactBytes = QJsonDocument(object)
                                                    .toJson(QJsonDocument::Compact)
                                                    .size();
                    if (compactBytes > MaximumRecordBytes) {
                        ++result.rejectedLines;
                        ++result.oversizedRecords;
                        result.truncated = true;
                        continue;
                    }
                    records.insert(RetainedRecord{
                        object,
                        object.value(QStringLiteral("occurred_at")).toString(),
                        object.value(QStringLiteral("signal_seq")).toInteger(),
                        arrivalSequence++,
                        compactBytes,
                    });
                    retainedObjectBytes += compactBytes;
                    while (!records.empty()) {
                        const bool overCount = records.size()
                            > static_cast<std::size_t>(MaximumRecords);
                        const bool overBytes = compactArrayBytes(retainedObjectBytes, records.size())
                            > MaximumReturnedBytes;
                        if (!overCount && !overBytes) break;
                        result.truncated = true;
                        result.truncatedByRecordCount = result.truncatedByRecordCount || overCount;
                        result.truncatedByReturnedBytes = result.truncatedByReturnedBytes || overBytes;
                        retainedObjectBytes -= records.begin()->compactBytes;
                        records.erase(records.begin());
                        ++result.droppedRecords;
                    }
                }
            }
            result.returnedBytes = compactArrayBytes(retainedObjectBytes, records.size());
            for (auto iterator = records.rbegin(); iterator != records.rend(); ++iterator) {
                result.records.append(iterator->object);
            }
            return result;
        }));
}

void PremiumHistoryLoader::exportCsv(const QString &path, const QJsonArray &records) {
    if (busy()) {
        emit failed(QStringLiteral("历史记录任务正在运行，请稍候"));
        return;
    }
    if (path.isEmpty() || records.isEmpty()) {
        emit failed(QStringLiteral("没有可导出的审计记录"));
        return;
    }
    exportWatcher_->setFuture(QtConcurrent::run([path, records]() -> ExportOutcome {
        static const QStringList headers{
            QStringLiteral("触发时间"), QStringLiteral("标的"), QStringLiteral("名称"),
            QStringLiteral("模型"), QStringLiteral("可卖溢价率"), QStringLiteral("拉升30秒"),
            QStringLiteral("拉升5分钟"), QStringLiteral("买一150秒"), QStringLiteral("买一300秒"),
            QStringLiteral("提醒类型"), QStringLiteral("来源"), QStringLiteral("触发原因"),
            QStringLiteral("原始JSON")};
        if (records.size() > MaximumRecords) {
            return {false, QStringLiteral("导出失败：记录数超过 %1 条安全上限")
                               .arg(MaximumRecords)};
        }
        // Validate the complete input before opening QSaveFile.  This keeps an
        // out-of-contract caller from making us write a large temporary CSV
        // only to discover the aggregate limit near the end.
        qint64 inputObjectBytes = 0;
        std::size_t inputRecordCount = 0;
        for (const auto &value : records) {
            if (!value.isObject()) continue;
            const qint64 compactBytes = QJsonDocument(value.toObject())
                                            .toJson(QJsonDocument::Compact)
                                            .size();
            if (compactBytes > MaximumRecordBytes) {
                return {false, QStringLiteral("导出失败：单条记录超过 %1 字节安全上限")
                                   .arg(MaximumRecordBytes)};
            }
            if (!canAppendWithin(inputObjectBytes, compactBytes, MaximumReturnedBytes)
                || compactArrayBytes(inputObjectBytes + compactBytes, inputRecordCount + 1)
                       > MaximumReturnedBytes) {
                return {false, QStringLiteral("导出失败：输入记录超过 %1 字节安全上限")
                                   .arg(MaximumReturnedBytes)};
            }
            inputObjectBytes += compactBytes;
            ++inputRecordCount;
        }
        QSaveFile file(path);
        if (!file.open(QIODevice::WriteOnly)) {
            return {false, QStringLiteral("导出失败：%1").arg(file.errorString())};
        }
        qint64 writtenBytes = 0;
        const auto writeAll = [&file, &writtenBytes](const QByteArray &data) {
            if (!canAppendWithin(writtenBytes, data.size(), MaximumExportBytes)) return false;
            qint64 offset = 0;
            while (offset < data.size()) {
                const qint64 written = file.write(data.constData() + offset, data.size() - offset);
                if (written <= 0) return false;
                offset += written;
            }
            writtenBytes += data.size();
            return true;
        };
        QByteArray output("\xEF\xBB\xBF");
        for (int index = 0; index < headers.size(); ++index) {
            if (index) output.append(',');
            output.append(csvCell(headers.at(index)));
        }
        output.append('\n');
        if (!writeAll(output)) {
            file.cancelWriting();
            return {false, QStringLiteral("导出失败：%1").arg(file.errorString())};
        }
        qsizetype exportedRecords = 0;
        for (const auto &value : records) {
            if (!value.isObject()) continue;
            const auto object = value.toObject();
            const QByteArray compactJson = QJsonDocument(object).toJson(QJsonDocument::Compact);
            const auto values = recordValues(object);
            QByteArray row;
            for (int index = 0; index < values.size(); ++index) {
                if (index) row.append(',');
                row.append(csvCell(values.at(index)));
            }
            row.append(',');
            row.append(csvCell(QString::fromUtf8(compactJson)));
            row.append('\n');
            if (row.size() > MaximumExportRowBytes) {
                file.cancelWriting();
                return {false, QStringLiteral("导出失败：单行超过 %1 字节安全上限")
                                   .arg(MaximumExportRowBytes)};
            }
            if (!writeAll(row)) {
                file.cancelWriting();
                const QString detail = file.error() == QFileDevice::NoError
                    ? QStringLiteral("CSV 超过 %1 字节安全上限").arg(MaximumExportBytes)
                    : file.errorString();
                return {false, QStringLiteral("导出失败：%1").arg(detail)};
            }
            ++exportedRecords;
        }
        if (!file.commit()) {
            return {false, QStringLiteral("导出失败：%1").arg(file.errorString())};
        }
        return {true, QStringLiteral("已导出 %1 条到 %2").arg(exportedRecords).arg(path)};
    }));
}

} // namespace hub
