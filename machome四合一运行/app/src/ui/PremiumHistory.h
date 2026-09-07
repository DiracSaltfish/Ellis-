#pragma once

#include <QDate>
#include <QJsonArray>
#include <QJsonObject>
#include <QObject>

template <typename T> class QFutureWatcher;

namespace hub {

/**
 * Read-only, bounded background reader for the A-core signal JSONL audit.
 * No work is performed on the GUI thread and no source record is modified.
 */
class PremiumHistoryLoader final : public QObject {
    Q_OBJECT
public:
    // These limits are part of the loader contract.  They bound the JSONL
    // parser, the retained/result set and the CSV writer independently.
    static constexpr int MaximumRecords = 50'000;
    static constexpr qint64 MaximumLineBytes = 2LL * 1024 * 1024;
    static constexpr qint64 MaximumRecordBytes = 2LL * 1024 * 1024;
    static constexpr qint64 MaximumReturnedBytes = 32LL * 1024 * 1024;
    static constexpr qint64 MaximumExportRowBytes = 8LL * 1024 * 1024;
    static constexpr qint64 MaximumExportBytes = 128LL * 1024 * 1024;

    explicit PremiumHistoryLoader(QObject *parent = nullptr);
    ~PremiumHistoryLoader() override;

    bool busy() const;

public slots:
    void load(const QString &directory, const QDate &from, const QDate &to,
              const QString &symbolFilter, const QString &modelFilter);
    void exportCsv(const QString &path, const QJsonArray &records);

signals:
    void loadStarted(int fileCount);
    void loadFinished(const QJsonArray &records, const QJsonObject &statistics);
    void exportFinished(bool ok, const QString &message);
    void failed(const QString &message);

private:
    struct LoadOutcome {
        QJsonArray records;
        int files = 0;
        int parsedLines = 0;
        int rejectedLines = 0;
        int oversizedRecords = 0;
        int droppedRecords = 0;
        qint64 returnedBytes = 2; // Compact JSON representation of an empty array: [].
        bool truncated = false;
        bool truncatedByRecordCount = false;
        bool truncatedByReturnedBytes = false;
        QString error;
    };
    struct ExportOutcome {
        bool ok = false;
        QString message;
    };

    QFutureWatcher<LoadOutcome> *loadWatcher_ = nullptr;
    QFutureWatcher<ExportOutcome> *exportWatcher_ = nullptr;
};

} // namespace hub
