#pragma once

#include "common/BridgeFrame.h"
#include "tgw/RawEventValidator.h"

#include <QByteArray>
#include <QFile>
#include <QJsonObject>
#include <QLocalSocket>
#include <QObject>
#include <QTimer>
#include <QString>
#include <tgw/session.hpp>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <thread>
#include <vector>

namespace premium::native_tgw {

struct AdapterOptions {
    QString socketPath;
    QString watchlistPath;
    QString logPath;
    bool simulation = false;
    std::optional<tgw::Config> liveConfig;
};

class NativeTgwAdapter final : public QObject {
public:
    explicit NativeTgwAdapter(AdapterOptions options, QObject *parent = nullptr);
    ~NativeTgwAdapter() override;

    bool start(QString *error = nullptr);
    void stop();

private:
    struct StateSnapshot {
        std::set<QString> desired;
        bool quotesDesired = false;
        bool bridgeConnected = false;
        quint64 resetGeneration = 0;
    };

    struct PendingEvent {
        QByteArray payload;
        QString tag;
        QString symbol;
        QString sessionId;
        qint64 receiveWallNs = 0;
        qint64 receiveMonotonicNs = 0;
        quint64 bridgeEpoch = 0;
        bool isDelta = false;
    };

    struct RetryState {
        std::chrono::steady_clock::time_point retryAt;
        std::chrono::seconds delay{1};
        int failures = 0;
    };

    bool loadWatchlist(QString *error);
    void connectCore();
    void coreConnected();
    void coreDisconnected();
    void readControl();
    void applyControl(const QJsonObject &request);
    bool sendFrame(BridgeFrame frame, const QString &sessionId = {});
    void sendStatus(const QString &message, const QJsonObject &detail = {});
    void queueStatus(const QString &message, const QJsonObject &detail = {});
    void writeLog(const QString &level, const QString &message, const QJsonObject &detail = {});
    QString currentSessionId() const;
    void setCurrentSessionId(QString sessionId);
    QString redact(QString value) const;

    StateSnapshot stateSnapshot() const;
    void requestSessionReset();
    void enqueueEvent(PendingEvent event);
    void drainEvents();
    void requestBridgeReset(const QString &reason);
    void resetBridge(const QString &reason);
    void clearEventQueue();

    void workerMain();
    void liveLoop();
    void simulationLoop();
    void waitForState(std::chrono::milliseconds duration);
    void unsubscribeAll(tgw::Session &session,
                        std::map<QString, tgw::SubscribeItem> &active,
                        bool report);
    void reconcileSubscriptions(tgw::Session &session,
                                const std::set<QString> &desired,
                                std::map<QString, tgw::SubscribeItem> &active,
                                std::map<QString, RetryState> &retries);
    void subscribeBatch(tgw::Session &session,
                        const std::vector<std::pair<QString, tgw::SubscribeItem>> &batch,
                        std::map<QString, tgw::SubscribeItem> &active,
                        std::map<QString, RetryState> &retries,
                        int &callBudget);

    AdapterOptions options_;
    QLocalSocket coreSocket_;
    QTimer reconnectTimer_;
    QByteArray controlBuffer_;
    QFile logFile_;
    quint64 sequence_ = 0;
    std::atomic<quint64> bridgeEpoch_{0};

    mutable std::mutex stateMutex_;
    std::condition_variable stateChanged_;
    std::set<QString> desired_;
    bool quotesDesired_ = false;
    bool bridgeConnected_ = false;
    quint64 resetGeneration_ = 0;

    mutable std::mutex sessionMutex_;
    QString sessionId_;

    std::mutex eventMutex_;
    std::deque<PendingEvent> events_;
    std::atomic<bool> drainScheduled_{false};
    std::atomic<bool> bridgeResetScheduled_{false};
    std::atomic<quint64> queueDrops_{0};
    std::atomic<quint64> invalidEvents_{0};

    std::atomic<bool> stopRequested_{false};
    bool started_ = false;
    std::thread worker_;

    static constexpr std::size_t EventLimit = 10'000;
    static constexpr qint64 BridgeWriteLimit = 16 * 1024 * 1024;
    static constexpr std::size_t SubscriptionBatchSize = 20;
    static constexpr int SubscribeCallBudget = 64;
};

} // namespace premium::native_tgw
