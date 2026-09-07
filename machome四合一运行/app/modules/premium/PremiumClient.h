#pragma once

#include <QElapsedTimer>
#include <QHash>
#include <QJsonObject>
#include <QObject>
#include <QSet>
#include <QStringList>
#include <QtGlobal>

class QTcpSocket;
class QTimer;
class QWebSocket;

namespace machome::premium {

class PremiumClient final : public QObject
{
    Q_OBJECT

public:
    enum class ChannelState {
        Stopped,
        Connecting,
        Connected,
        Stale,
        Backoff,
    };
    Q_ENUM(ChannelState)

    struct Config {
        QString host = QStringLiteral("127.0.0.1");
        quint16 summaryPort = 8421;
        quint16 l1Port = 19195;

        int connectTimeoutMs = 5'000;
        int reconnectInitialMs = 500;
        int reconnectMaximumMs = 30'000;
        int freshnessPollMs = 1'000;
        int summaryStaleAfterMs = 15'000;
        int l1StaleAfterMs = 35'000;
        int silenceDisconnectAfterMs = 60'000;
        int l1PingIntervalMs = 15'000;
        int l1StatusIntervalMs = 10'000;

        qint64 maximumWebSocketMessageBytes = 4 * 1024 * 1024;
        qint64 maximumL1LineBytes = 65'536;
        qint64 maximumL1BufferBytes = 256 * 1024;
        qint64 maximumPendingWriteBytes = 1024 * 1024;
    };

    explicit PremiumClient(QObject *parent = nullptr);
    explicit PremiumClient(const Config &config, QObject *parent = nullptr);
    ~PremiumClient() override;

    [[nodiscard]] Config config() const;
    [[nodiscard]] bool isRunning() const;
    [[nodiscard]] ChannelState summaryState() const;
    [[nodiscard]] ChannelState detailState() const;
    [[nodiscard]] ChannelState l1State() const;
    [[nodiscard]] bool isSummaryFresh() const;
    [[nodiscard]] bool isL1Fresh() const;
    [[nodiscard]] qint64 summaryAgeMs() const;
    [[nodiscard]] qint64 l1AgeMs() const;
    [[nodiscard]] QJsonObject lastStatus() const;
    [[nodiscard]] QJsonObject lastL1Status() const;
    [[nodiscard]] QStringList desiredDetailSymbols() const;
    [[nodiscard]] QStringList acknowledgedDetailSymbols() const;

public Q_SLOTS:
    // Every operation returns immediately. Calls made from another thread are
    // queued back to this object's owner thread.
    void setConfig(const Config &config);
    void start();
    void stop();
    void reconnect();

    void requestStatus();
    void requestSync();
    void setWatchlist(const QStringList &symbols);
    void setL1Hotlist(const QStringList &symbols);
    void requestRawSnapshot();
    void subscribeDetail(const QString &symbol);
    void unsubscribeDetail(const QString &symbol);

    void requestL1Status();
    void pingL1();
    // Called only after the control plane records authoritative reconciliation
    // evidence for an unknown mutation outcome.
    void reconcilePendingMutations();

Q_SIGNALS:
    void runningChanged(bool running);
    void summaryStateChanged(machome::premium::PremiumClient::ChannelState state,
                             const QString &detail);
    void detailStateChanged(machome::premium::PremiumClient::ChannelState state,
                            const QString &detail);
    void l1StateChanged(machome::premium::PremiumClient::ChannelState state,
                        const QString &detail);
    void freshnessChanged(bool summaryFresh, bool l1Fresh,
                          qint64 summaryAgeMs, qint64 l1AgeMs);

    // All valid inbound objects are published here before their typed signal.
    void messageReceived(const QString &channel, const QString &type,
                         const QJsonObject &message);

    void statusReceived(const QJsonObject &status);
    void summaryReceived(const QString &symbol, const QJsonObject &summary);
    void signalReceived(const QString &symbol, const QJsonObject &signal);
    void syncBeginReceived(const QJsonObject &event);
    void syncCompleteReceived(const QJsonObject &event);
    void syncEventReceived(const QString &phase, const QJsonObject &event);
    void rawSnapshotReceived(const QJsonObject &snapshot);
    void watchlistAcknowledged(const QJsonObject &acknowledgement);
    void l1HotlistAcknowledged(const QJsonObject &acknowledgement);
    void symbolRemoved(const QString &symbol, const QJsonObject &event);

    void detailHelloReceived(const QJsonObject &hello);
    void detailAcknowledged(const QString &operation, const QString &symbol,
                            const QJsonObject &acknowledgement);
    void detailReceived(const QString &symbol, const QJsonObject &detail);
    void detailSubscriptionFailed(const QString &operation, const QString &symbol,
                                  const QJsonObject &error);
    void detailSubscriptionsChanged(const QStringList &desired,
                                    const QStringList &acknowledged);

    void l1HelloReceived(const QJsonObject &hello);
    void l1StatusReceived(const QJsonObject &status);
    void l1PongReceived(const QJsonObject &pong);

    void serverErrorReceived(const QString &channel, const QJsonObject &error);
    void commandSent(const QString &channel, const QString &operation,
                     const QJsonObject &command);
    void commandRejectedLocally(const QString &channel, const QString &operation,
                                const QString &reason);
    void protocolError(const QString &channel, const QString &reason);

private:
    static Config normalizedConfig(Config config);

    void initializeObjects();
    void applyLimits();
    void connectSummary();
    void connectDetail();
    void connectL1();
    void scheduleSummaryReconnect(const QString &reason);
    void scheduleDetailReconnect(const QString &reason);
    void scheduleL1Reconnect(const QString &reason);
    [[nodiscard]] int nextReconnectDelay(int &attempt) const;

    void handleSummaryConnected();
    void handleSummaryDisconnected();
    void handleSummaryText(const QString &message);
    void handleSummaryBinary(const QByteArray &message);
    void handleSummarySocketError();
    bool dispatchSummaryObject(const QJsonObject &object);
    bool sendSummaryCommand(const QString &operation, QJsonObject command);

    void handleDetailConnected();
    void handleDetailDisconnected();
    void handleDetailText(const QString &message);
    void handleDetailBinary(const QByteArray &message);
    void handleDetailSocketError();
    bool dispatchDetailObject(const QJsonObject &object);
    bool sendDetailCommand(const QString &operation, const QString &symbol);
    void reconcileDetailSubscriptions();
    void closeIdleDetailChannel();
    void resetDetailGeneration();
    void clearPendingSummaryMutations();

    void handleL1Connected();
    void handleL1Disconnected();
    void handleL1ReadyRead();
    void handleL1SocketError();
    void dispatchL1Object(const QJsonObject &object);
    bool sendL1Command(const QString &operation);
    void failL1Protocol(const QString &reason);

    void markSummaryActivity();
    void markL1Activity();
    void checkFreshness();
    void publishFreshness();
    void setSummaryState(ChannelState state, const QString &detail);
    void setDetailState(ChannelState state, const QString &detail);
    void setL1State(ChannelState state, const QString &detail);
    [[nodiscard]] qint64 ageSince(qint64 timestampMs) const;
    [[nodiscard]] bool onOwnerThread() const;

    Config config_;
    bool running_ = false;

    QWebSocket *summarySocket_ = nullptr;
    QWebSocket *detailSocket_ = nullptr;
    QTcpSocket *l1Socket_ = nullptr;
    QTimer *summaryReconnectTimer_ = nullptr;
    QTimer *detailReconnectTimer_ = nullptr;
    QTimer *l1ReconnectTimer_ = nullptr;
    QTimer *summaryConnectTimeoutTimer_ = nullptr;
    QTimer *detailConnectTimeoutTimer_ = nullptr;
    QTimer *detailHelloTimeoutTimer_ = nullptr;
    QTimer *l1ConnectTimeoutTimer_ = nullptr;
    QTimer *l1HelloTimeoutTimer_ = nullptr;
    QTimer *freshnessTimer_ = nullptr;
    QTimer *l1PingTimer_ = nullptr;
    QTimer *l1StatusTimer_ = nullptr;

    ChannelState summaryState_ = ChannelState::Stopped;
    ChannelState detailState_ = ChannelState::Stopped;
    ChannelState l1State_ = ChannelState::Stopped;
    QString summaryStateDetail_;
    QString detailStateDetail_;
    QString l1StateDetail_;
    int summaryReconnectAttempt_ = 0;
    int detailReconnectAttempt_ = 0;
    int l1ReconnectAttempt_ = 0;
    int consecutiveSummaryProtocolErrors_ = 0;
    int consecutiveDetailProtocolErrors_ = 0;
    bool detailHelloSeen_ = false;
    int detailServerMaximumSymbols_ = 4;
    QSet<QString> desiredDetailSymbols_;
    QSet<QString> acknowledgedDetailSymbols_;
    QHash<QString, QString> pendingDetailOperations_;
    int consecutiveL1ProtocolErrors_ = 0;
    bool l1HelloSeen_ = false;
    QByteArray l1InputBuffer_;
    quint64 nextL1RequestId_ = 1;

    QElapsedTimer monotonicClock_;
    qint64 lastSummaryActivityMs_ = -1;
    qint64 lastL1ActivityMs_ = -1;
    QJsonObject lastStatus_;
    QJsonObject lastL1Status_;
    QStringList pendingWatchlist_;
    QStringList pendingL1Hotlist_;
    bool watchlistAckPending_ = false;
    bool l1HotlistAckPending_ = false;
};

} // namespace machome::premium
