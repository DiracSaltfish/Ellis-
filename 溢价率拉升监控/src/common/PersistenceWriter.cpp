#include "common/PersistenceWriter.h"

#include <QDateTime>
#include <QJsonDocument>
#include <QStorageInfo>

#include <algorithm>
#include <functional>

#ifdef PREMIUM_HAS_ZSTD
#include <zstd.h>
#endif

namespace premium {

PersistenceWriter::PersistenceWriter(QString dataDirectory, QObject *parent)
    : QObject(parent), directory_(std::move(dataDirectory))
{
    if (!directory_.exists()) directory_.mkpath(QStringLiteral("."));
}

void PersistenceWriter::appendRaw(const QByteArray &jsonLine, const QDate &partitionDate)
{
    append(rawFile_, QStringLiteral("raw"), jsonLine, partitionDate, true);
    Q_EMIT writeCompleted();
}

void PersistenceWriter::appendNormalized(const QByteArray &jsonLine, const QDate &partitionDate)
{
    append(normalizedFile_, QStringLiteral("normalized"), jsonLine, partitionDate, true);
    Q_EMIT writeCompleted();
}

void PersistenceWriter::appendSignal(const QByteArray &jsonLine)
{
    const QDate today = QDate::currentDate();
    append(signalFile_, QStringLiteral("signals"), jsonLine, today, false);
    Q_EMIT writeCompleted();
}

void PersistenceWriter::prune()
{
    struct Partition { QDate date; QString path; };
    QList<Partition> marketFiles;
    const QStringList files = directory_.entryList({QStringLiteral("raw-*.jsonl*"), QStringLiteral("normalized-*.jsonl*")}, QDir::Files);
    for (const QString &name : files) {
        const int dash = name.indexOf(u'-');
        const int dot = name.indexOf(u'.', dash);
        const QDate date = QDate::fromString(name.mid(dash + 1, dot - dash - 1), QStringLiteral("yyyyMMdd"));
        if (date.isValid()) marketFiles.append({date, directory_.absoluteFilePath(name)});
    }
    QList<QDate> dates;
    for (const auto &item : marketFiles) if (!dates.contains(item.date)) dates.append(item.date);
    std::sort(dates.begin(), dates.end(), std::greater<>());
    while (dates.size() > 5) {
        const QDate removeDate = dates.takeLast();
        for (const auto &item : marketFiles) if (item.date == removeDate) QFile::remove(item.path);
    }
    const QDate logCutoff = QDate::currentDate().addDays(-30);
    for (const QString &name : directory_.entryList({QStringLiteral("signals-*.jsonl")}, QDir::Files)) {
        const QDate date = QDate::fromString(name.mid(8, 8), QStringLiteral("yyyyMMdd"));
        if (date.isValid() && date < logCutoff) QFile::remove(directory_.absoluteFilePath(name));
    }
}

void PersistenceWriter::close()
{
    rawFile_.close();
    normalizedFile_.close();
    signalFile_.close();
}

bool PersistenceWriter::append(QFile &file, const QString &prefix, const QByteArray &line,
                               const QDate &date, bool compressed)
{
    const qint64 free = availableBytes();
    if (free >= 0 && free < 5LL * 1024 * 1024 * 1024) {
        if (storageEnabled_) {
            storageEnabled_ = false;
            Q_EMIT storageStateChanged(false, free);
        }
        return false;
    }
    if (!storageEnabled_) {
        storageEnabled_ = true;
        Q_EMIT storageStateChanged(true, free);
    }
    if (!ensureFile(file, prefix, date, compressed)) return false;
    QByteArray record = line;
    if (!record.endsWith('\n')) record.append('\n');
    if (compressed) record = compressFrame(record);
    if (file.write(record) != record.size()) {
        Q_EMIT writeError(QStringLiteral("写入失败: %1").arg(file.fileName()));
        return false;
    }
    file.flush();
    return true;
}

bool PersistenceWriter::ensureFile(QFile &file, const QString &prefix, const QDate &date, bool compressed)
{
    const QString extension = compressed
#ifdef PREMIUM_HAS_ZSTD
        ? QStringLiteral(".jsonl.zst")
#else
        ? QStringLiteral(".jsonl")
#endif
        : QStringLiteral(".jsonl");
    const QString path = directory_.absoluteFilePath(prefix + u'-' + date.toString(QStringLiteral("yyyyMMdd")) + extension);
    if (file.fileName() == path && file.isOpen()) return true;
    file.close();
    file.setFileName(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Append)) {
        Q_EMIT writeError(QStringLiteral("无法打开数据文件: %1 (%2)").arg(path, file.errorString()));
        return false;
    }
    return true;
}

QByteArray PersistenceWriter::compressFrame(const QByteArray &line) const
{
#ifdef PREMIUM_HAS_ZSTD
    QByteArray compressed(static_cast<qsizetype>(ZSTD_compressBound(static_cast<size_t>(line.size()))), Qt::Uninitialized);
    const size_t written = ZSTD_compress(compressed.data(), static_cast<size_t>(compressed.size()),
                                         line.constData(), static_cast<size_t>(line.size()), 1);
    if (!ZSTD_isError(written)) {
        compressed.resize(static_cast<qsizetype>(written));
        return compressed;
    }
#endif
    return line;
}

qint64 PersistenceWriter::availableBytes() const
{
    const QStorageInfo storage(directory_.absolutePath());
    return storage.isValid() && storage.isReady() ? storage.bytesAvailable() : -1;
}

} // namespace premium
