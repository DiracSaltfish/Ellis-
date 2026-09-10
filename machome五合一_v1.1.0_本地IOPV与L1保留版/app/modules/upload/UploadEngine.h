#pragma once

#include "common/ModuleEngine.h"

#include <QDateTime>
#include <QHash>
#include <QJsonArray>
#include <QLocalSocket>
#include <QProcess>
#include <QSqlDatabase>
#include <QTimeZone>
#include <QTimer>

#include <memory>

namespace machome::upload {

class IUploadHttpTransport;
class UploadCollectors;
class UploadBusinessComponent;

struct UploadJobDefinition {
    QString id;
    QString displayName;
    QString kind;
    QString timezone = QStringLiteral("Asia/Shanghai");
    QTime runAt;
    int retryIntervalSeconds = 0;
    QTime retryUntilExclusive;
    QList<int> weekdays{1, 2, 3, 4, 5};
    int intervalSeconds = 0;
    QList<QPair<QTime, QTime>> windows;
    QString source;
    QString modelVersion;
};

struct ScheduleDecision {
    bool active = false;
    bool due = false;
    QString idempotencyKey;
    QDateTime localNow;
    QDateTime nextRunUtc;
    QString reason;
};

class UploadSchedule final {
public:
    static QList<UploadJobDefinition> productionJobs();
    static ScheduleDecision evaluate(const UploadJobDefinition &job,
                                     const QDateTime &utcNow,
                                     const QDateTime &lastStartedUtc = {},
                                     const QDateTime &lastSuccessUtc = {});
};

class UploadEngine final : public hub::IModuleEngine {
    Q_OBJECT
public:
    explicit UploadEngine(QObject *parent = nullptr);
    ~UploadEngine() override;

    QJsonObject snapshot() const;
    QString databasePath() const;
    bool isRecordOnly() const;

    static bool validateQuote(const QJsonObject &quote, QString *error = nullptr);
    static bool validateValuationInput(const QJsonObject &input,
                                       QString *error = nullptr);
    static QJsonObject calculateBasketValuation(const QJsonObject &input,
                                                QString *error = nullptr);
    static QJsonObject quoteFromIbkrItem(const QJsonObject &item,
                                         QString *error = nullptr);
    static QString normalizedSymbol(const QString &value);
    static QJsonArray productionPcfDefinitions();
    static QJsonObject ibkrConfigurationForOperatingMode(
        const QJsonObject &configuration, const QString &mode,
        QString *error = nullptr);

    void setNowForTest(const QDateTime &utcNow);
    void clearNowForTest();
    void evaluateSchedulesForTest();
    void setHttpTransportForTest(std::unique_ptr<IUploadHttpTransport> transport);

public slots:
    void initialize(const hub::ModuleContext &context) override;
    void start() override;
    void stop(hub::StopMode mode = hub::StopMode::Graceful) override;
    void submitCommand(const QString &action, const QJsonObject &arguments,
                       const QString &commandId) override;

private slots:
    void evaluateSchedules();
    void helperFinished(int exitCode, QProcess::ExitStatus status);
    void helperError(QProcess::ProcessError error);
    void connectIbkrSocket();
    void readIbkrSocket();
    void pollIbkrQuotes();

private:
    struct JobRuntime {
        UploadJobDefinition definition;
        QString state = QStringLiteral("scheduled_idle");
        QString stage = QStringLiteral("waiting_schedule");
        QDateTime lastStartedUtc;
        QDateTime lastSuccessUtc;
        QDateTime lastFailureUtc;
        QDateTime nextRunUtc;
        QString lastError;
        quint64 runCount = 0;
        quint64 acceptedCount = 0;
    };

    bool openRepository(QString *error);
    bool migrateRepository(QString *error);
    bool executeSql(const QString &sql, QString *error = nullptr);
    bool recordPayload(const QString &jobId, const QJsonObject &payload,
                       const QString &idempotencyKey, QString *error,
                       QString *sha256 = nullptr);
    bool persistQuote(const QJsonObject &quote, QString *error);
    bool persistDataset(const QJsonObject &dataset, QString *error);
    bool persistValuation(const QJsonObject &input, QString *error,
                          QJsonObject *valuation = nullptr);
    bool collectForJob(const QString &jobId, QJsonObject *details, QString *error);
    bool executeJob(JobRuntime &runtime, const QString &idempotencyKey,
                    bool manual, QString *error, QJsonObject *details = nullptr);
    bool alreadyCompleted(const QString &jobId, const QString &idempotencyKey) const;
    void recordJobRun(const JobRuntime &runtime, const QString &idempotencyKey,
                      const QString &state, const QString &error = {});
    void publishSnapshot();
    void publishEvent(const QString &kind, const QJsonObject &payload);
    void runStartupCatchups();
    bool manualRunAllowed(const JobRuntime &runtime,
                          ScheduleDecision *decision = nullptr) const;
    void persistJobState(const JobRuntime &runtime);
    void startIbkrHelper();
    void stopIbkrHelper();
    void writeIbkrRequest(const QString &type);
    void handleIbkrFrame(const QJsonObject &frame);
    bool validateHelperConfiguration(QString *error) const;
    QString ibkrConfigurationPathForOperatingMode(QString *error) const;
    QDateTime nowUtc() const;
    QJsonArray jobSnapshots() const;
    QJsonArray fundSnapshots() const;
    QJsonArray recentUploadRecords(int limit = 100) const;
    QJsonArray recentJobRuns(int limit = 100) const;
    QJsonObject historySnapshots(int limit = 200) const;
    static QString iso(const QDateTime &value);

    UploadBusinessComponent *business_ = nullptr;
    hub::ModuleContext context_;
    QString connectionName_;
    QSqlDatabase database_;
    QHash<QString, JobRuntime> jobs_;
    std::unique_ptr<UploadCollectors> collectors_;
    QTimer scheduleTimer_;
    QProcess ibkrHelper_;
    QLocalSocket ibkrSocket_;
    QTimer ibkrPollTimer_;
    QByteArray ibkrInputBuffer_;
    QString ibkrSocketPath_;
    QDateTime ibkrLastQuoteUtc_;
    bool ibkrHandshakeComplete_ = false;
    QDateTime testNowUtc_;
    QString state_ = QStringLiteral("created");
    QString lastError_;
    QString operatingMode_ = QStringLiteral("work");
    bool initialized_ = false;
    bool running_ = false;
    quint64 sequence_ = 0;
};

} // namespace machome::upload
