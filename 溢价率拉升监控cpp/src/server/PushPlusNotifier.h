#pragma once

#include <QDate>
#include <QJsonObject>
#include <QList>
#include <QObject>
#include <QSet>
#include <QString>

class QNetworkAccessManager;
class QNetworkReply;
class QTimer;

namespace premium {

struct PushPlusConfig {
    bool enabled = false;
    bool startupNotification = false;
    QString token;
    QString endpoint = QStringLiteral("https://www.pushplus.plus/send");
    QString channel = QStringLiteral("wechat");
    QString stateFile;
    int batchWindowMs = 15'000;
    int maxRequestsPerMinute = 4;
    int dailyRequestLimit = 180;
    int requestTimeoutMs = 10'000;
    int maxSignalAgeSeconds = 120;
    int maxBatchSignals = 100;
};

struct PushPlusMessage {
    QString title;
    QString content;
    int signalCount = 0;
};

class PushPlusNotifier final : public QObject {
    Q_OBJECT
public:
    explicit PushPlusNotifier(PushPlusConfig config, QObject *parent = nullptr);

    static PushPlusConfig fromApplicationConfig(const QJsonObject &applicationConfig,
                                                const QString &rootDirectory,
                                                QString *error = nullptr);
    static QString eventKey(const QJsonObject &signal);
    static PushPlusMessage buildMessage(const QList<QJsonObject> &items);

public Q_SLOTS:
    void start();
    void stop();
    void enqueueSignal(const QJsonObject &signal);

Q_SIGNALS:
    void statusChanged(const QJsonObject &status);
    void operationalEvent(const QString &level, const QString &message,
                          const QJsonObject &fields = {});

private:
    void enqueueItem(QJsonObject item, const QString &key);
    void scheduleFlush(int delayMs);
    void flush();
    void dispatchCurrentBatch();
    void handleReply();
    void completeCurrentBatch();
    void dropCurrentBatch(const QString &reason);
    void resetDailyStateIfNeeded();
    void pruneExpiredItems();
    int rateLimitDelayMs();
    void loadState();
    void saveState() const;
    void publishStatus();
    QJsonObject statusObject() const;

    PushPlusConfig config_;
    QNetworkAccessManager *network_ = nullptr;
    QNetworkReply *reply_ = nullptr;
    QTimer *flushTimer_ = nullptr;
    QList<QJsonObject> pending_;
    QList<QJsonObject> currentBatch_;
    QSet<QString> queuedKeys_;
    QSet<QString> acknowledgedKeys_;
    QList<qint64> requestTimesMs_;
    QDate accountingDate_;
    int requestsToday_ = 0;
    int acceptedBatches_ = 0;
    int sentSignals_ = 0;
    int failedRequests_ = 0;
    int droppedSignals_ = 0;
    int expiredSignals_ = 0;
    int currentRetry_ = 0;
    bool started_ = false;
    QString pausedReason_;
    QString lastSuccessAt_;
    QString lastError_;
};

} // namespace premium
