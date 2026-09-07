#pragma once

#include "common/ModuleEngine.h"
#include "modules/webull/WebullClient.h"

#include <QByteArray>
#include <QDateTime>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QProcess>
#include <QSet>
#include <QTime>
#include <QTimeZone>
#include <QTimer>

class QTcpServer;

namespace machome::webull {

class WebullV2Server;

struct ScheduleDecision {
    bool collectorDesired = false;
    bool scheduledIdle = true;
    QDateTime localNow;
    QDateTime nextTransitionUtc;
    QString reason;
};

class WebullSchedule final {
public:
    static ScheduleDecision evaluate(const QDateTime &utcNow,
                                     const QString &mode = QStringLiteral("auto"),
                                     const QTime &start = QTime(9, 0),
                                     const QTime &stop = QTime(16, 0),
                                     const QTimeZone &zone = QTimeZone("Asia/Shanghai"),
                                     const QString &operatingMode = QStringLiteral("work"));
    static bool loginCheckDue(const QDateTime &utcNow, const QSet<QString> &completed,
                              QString *slotKey = nullptr,
                              const QTimeZone &zone = QTimeZone("Asia/Shanghai"),
                              int catchUpMinutes = 10);
};

// Native owner of Webull scheduling, normalization, sequence/session, storage,
// helper lifecycle and the external v2 compatibility surface.  It lives on the
// Webull ModuleWorker QThread; the browser helper transports raw frames only.
class WebullEngine final : public hub::IModuleEngine {
    Q_OBJECT
public:
    explicit WebullEngine(QObject *parent = nullptr);
    ~WebullEngine() override;

    QJsonObject snapshot() const;
    QString dataRoot() const { return context_.dataRoot; }
    QString apiTokenFile() const { return tokenFile_; }

    static bool normalizeHttpDepth(const QJsonObject &body, int maximumLevels,
                                   QJsonArray *bids, QJsonArray *asks,
                                   QString *error = nullptr);
    static bool decodeMqttDepth(const QByteArray &frame, const QString &tickerId,
                                int minimumSideLevels, int maximumLevels,
                                QJsonArray *bids, QJsonArray *asks,
                                QString *error = nullptr);

    void setNowForTest(const QDateTime &utcNow);
    void clearNowForTest();
    void evaluateScheduleForTest();
    bool ingestRawEventForTest(const QJsonObject &event, QString *error = nullptr);

public slots:
    void initialize(const hub::ModuleContext &context) override;
    void start() override;
    void stop(hub::StopMode mode = hub::StopMode::Graceful) override;
    void submitCommand(const QString &action, const QJsonObject &arguments,
                       const QString &commandId) override;

    // Compatibility facade used by ModuleWorker while the shared engine
    // plumbing is migrated. These calls are in-process and never use 18765.
    void refreshNow();
    void submitControl(const QString &requestId, const QString &action,
                       const QJsonObject &arguments = {});

signals:
    void statusUpdated(const Machome::Webull::GatewayStatus &status);
    void bookUpdated(const Machome::Webull::BookSnapshot &snapshot);
    void clientsUpdated(const Machome::Webull::ClientList &clients);
    void symbolsUpdated(const Machome::Webull::SymbolList &symbols);
    void logEntry(const Machome::Webull::LogEntry &entry);
    void errorOccurred(const Machome::Webull::ApiError &error);
    void controlCompleted(const QString &requestId, const QString &action,
                          const QJsonObject &result);
    void controlFailed(const QString &requestId, const QString &action,
                       const QString &code, const QString &message,
                       bool outcomeUncertain);

private slots:
    void evaluateSchedule();
    void checkFreshness();
    void helperReadyRead();
    void helperFinished(int exitCode, QProcess::ExitStatus status);
    void helperError(QProcess::ProcessError error);

private:
    friend class WebullV2Server;

    bool validateContext(QString *error);
    bool prepareStorage(QString *error);
    bool loadLoginChecks(QString *error);
    bool persistLoginChecks(QString *error);
    void finishLoginCheck(const QString &outcome);
    bool loadLatest(QString *error);
    bool persistBook(const QJsonObject &book, QString *error);
    bool processRawEvent(const QJsonObject &event, QString *error);
    bool publishDepth(const QJsonArray &bids, const QJsonArray &asks,
                      const QString &capturedAt, const QString &source,
                      QString *error);
    void publishState();
    void publishLog(Machome::Webull::LogSeverity severity, const QString &code,
                    const QString &message);
    void publishError(const QString &code, const QString &message,
                      bool retryable = false);
    void startHelper(bool visible = false);
    void stopHelper();
    void sendHelperCommand(const QString &action, const QJsonObject &arguments = {});
    void restartHelperLater();
    void startApi();
    void stopApi();
    QJsonObject publicStatus() const;
    QJsonObject publicBook() const;
    QJsonArray publicClients() const;
    QJsonObject symbolsPayload() const;
    QDateTime nowUtc() const;
    QString iso(const QDateTime &value) const;
    bool collectorDesiredNow() const;

    hub::ModuleContext context_;
    QProcess helper_;
    QByteArray helperBuffer_;
    QTimer scheduleTimer_;
    QTimer freshnessTimer_;
    QTimer helperRestartTimer_;
    WebullV2Server *api_ = nullptr;
    QDateTime testNowUtc_;
    QDateTime temporaryLoginUntilUtc_;
    QDateTime activeLoginStartedUtc_;
    QDateTime lastDepthUtc_;
    QDateTime lastHelperStartUtc_;
    QJsonObject latestBook_;
    QSet<QString> completedLoginChecks_;
    QHash<QString, QJsonObject> completedLoginResults_;
    QString activeLoginCheckKey_;
    QString symbol_ = QStringLiteral("XOP");
    QString tickerId_ = QStringLiteral("913243629");
    QString scheduleMode_ = QStringLiteral("auto");
    QString operatingMode_ = QStringLiteral("work");
    QString sessionId_;
    QString lastHash_;
    QString state_ = QStringLiteral("created");
    QString browserState_ = QStringLiteral("stopped");
    bool browserVisible_ = false;
    QString authState_ = QStringLiteral("unknown");
    QString dataState_ = QStringLiteral("no_data");
    QString lastError_;
    QString tokenFile_;
    QByteArray apiToken_;
    qint64 sequence_ = 0;
    qint64 validResponses_ = 0;
    qint64 invalidResponses_ = 0;
    qint64 staleAfterMs_ = 90'000;
    int maximumLevels_ = 50;
    int apiPort_ = 18765;
    int helperRestartAttempt_ = 0;
    bool initialized_ = false;
    bool running_ = false;
    bool collectorRunning_ = false;
    bool liveBrowserEnabled_ = false;
    bool fixtureMode_ = false;
    bool apiEnabled_ = true;
    bool stopping_ = false;
    bool intentionalHelperStop_ = false;
};

} // namespace machome::webull
