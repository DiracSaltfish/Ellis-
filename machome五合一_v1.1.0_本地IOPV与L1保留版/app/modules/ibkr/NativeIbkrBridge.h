#pragma once

#include "modules/ibkr/IbkrBridgeCore.h"

#include <DefaultEWrapper.h>
#include <EReaderOSSignal.h>

#include <QHash>
#include <QMutex>
#include <QObject>
#include <QSet>

#include <atomic>
#include <memory>
#include <thread>

class EClientSocket;
class EReader;
class QLocalServer;
class QLocalSocket;
class QTimer;

namespace machome::ibkr {

class NativeIbkrBridge final : public QObject, public DefaultEWrapper {
    Q_OBJECT

public:
    explicit NativeIbkrBridge(BridgeConfig config, QObject *parent = nullptr);
    ~NativeIbkrBridge() override;

    bool start(QString *errorMessage);
    void stop();
    QJsonObject statusSnapshot() const;

    void connectAck() override;
    void nextValidId(OrderId orderId) override;
    void connectionClosed() override;
    void currentTime(long epochSeconds) override;
    void error(int id, time_t errorTime, int errorCode,
               const std::string &errorString,
               const std::string &advancedOrderRejectJson) override;
    void tickPrice(TickerId tickerId, TickType field, double price,
                   const TickAttrib &attrib) override;
    void tickSize(TickerId tickerId, TickType field, Decimal size) override;
    void tickString(TickerId tickerId, TickType field,
                    const std::string &value) override;
    void marketDataType(TickerId tickerId, int marketDataType) override;

private:
    void startLocalServer(QString *errorMessage);
    void connectTws();
    void disconnectTws();
    void scheduleReconnect(const QString &reason);
    bool scheduleAllowsConnection() const;
    void armScheduleTimer(const ConnectionScheduleDecision &decision);
    void enterScheduledIdle(const ConnectionScheduleDecision &decision);
    void reevaluateConnectionSchedule();
    void markReady(int nextValidId);
    void subscribeConfiguredContracts();
    void requestMarketData(int tickerId, const ContractSpec &contract);
    void cancelMarketData(int tickerId);
    void heartbeatTick();
    void writeHealthFile();
    void readerLoop();

    void acceptConnections();
    void readClient(QLocalSocket *socket);
    void removeClient(QLocalSocket *socket);
    void releaseSubscriptionLease(QLocalSocket *socket, const QString &subscriptionId);
    void handleRequest(QLocalSocket *socket, const QJsonObject &request);
    void sendResponse(QLocalSocket *socket, QJsonObject response);
    void failRequest(QLocalSocket *socket, const QJsonValue &requestId,
                     const QString &code, const QString &message,
                     bool closeAfter = false);

    static Contract toIbContract(const ContractSpec &spec);
    static QString safeSdkMessage(const std::string &message);
    static bool informationalError(int errorCode);

    BridgeConfig config_;
    QuoteBook quotes_;
    QLocalServer *server_ = nullptr;
    QTimer *reconnectTimer_ = nullptr;
    QTimer *scheduleTimer_ = nullptr;
    QTimer *connectTimeoutTimer_ = nullptr;
    QTimer *heartbeatTimer_ = nullptr;
    QTimer *healthTimer_ = nullptr;
    QHash<QLocalSocket *, QByteArray> inputBuffers_;
    QSet<QLocalSocket *> handshaken_;
    QHash<QLocalSocket *, QSet<QString>> clientSubscriptions_;
    QHash<QString, QSet<QLocalSocket *>> subscriptionClients_;
    QSet<QString> pinnedSubscriptionIds_;
    int nextDynamicTickerId_ = 1'000'000;

    EReaderOSSignal signal_{1000};
    std::unique_ptr<EClientSocket> client_;
    std::unique_ptr<EReader> reader_;
    std::thread readerThread_;
    std::atomic_bool readerRunning_{false};
    mutable QMutex sdkMutex_;

    mutable QMutex stateMutex_;
    QString state_ = QStringLiteral("stopped");
    QString lastError_;
    QDateTime startedAt_;
    QDateTime connectedAt_;
    QDateTime lastHeartbeatAt_;
    QDateTime lastSuccessAt_;
    QDateTime lastFailureAt_;
    int serverVersion_ = 0;
    int reconnectAttempt_ = 0;
    bool ready_ = false;
    bool started_ = false;
    bool stopping_ = false;
    bool ownsSocket_ = false;
};

} // namespace machome::ibkr
