#pragma once

#include <QDateTime>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QMutex>
#include <QSet>
#include <QString>
#include <QTime>
#include <QTimeZone>

namespace machome::ibkr {

inline constexpr auto kBridgeProtocol = "machome.ibkr.quote.v1";

struct ConnectionScheduleDecision {
    bool active = true;
    QDateTime localTime;
    QDateTime nextTransition;
};

struct ConnectionSchedule {
    // Missing/disabled schedule preserves the legacy always-connected bridge
    // behaviour. Production enables this explicitly to match the standalone
    // upload programs' TWS lifecycle.
    bool enabled = false;
    QTimeZone timezone{QByteArrayLiteral("Asia/Shanghai")};
    QSet<int> weekdays{1, 2, 3, 4, 5}; // Qt ISO weekday: Monday=1.
    QTime start{9, 0};
    QTime stop{15, 6};

    ConnectionScheduleDecision evaluate(const QDateTime &observedAt) const;
    QJsonObject toJson() const;
};

struct ContractSpec {
    QString id;
    qint64 conId = 0;
    QString symbol;
    QString securityType;
    QString exchange;
    QString primaryExchange;
    QString currency;
    QString expiry;
    QString multiplier;
    QString tradingClass;
    QString genericTicks;

    static bool fromJson(const QJsonObject &object, const QString &subscriptionId,
                         ContractSpec *out, QString *errorMessage);
    bool marketDataEquivalent(const ContractSpec &other) const;
};

struct BridgeConfig {
    QString socketPath;
    QString healthFile;
    QString twsHost = QStringLiteral("127.0.0.1");
    int twsPort = 7496;
    int clientId = 41;
    int marketDataType = 1;
    int reconnectMinimumMs = 1'000;
    int reconnectMaximumMs = 30'000;
    int connectTimeoutMs = 10'000;
    int heartbeatIntervalMs = 5'000;
    int heartbeatStaleMs = 20'000;
    int maximumClients = 32;
    int maximumRequestBytes = 64 * 1024;
    int maximumSubscriptions = 512;
    ConnectionSchedule connectionSchedule;
    QList<ContractSpec> subscriptions;

    static bool loadFile(const QString &path, BridgeConfig *out, QString *errorMessage);
    QJsonObject safeSummary() const;
};

class QuoteBook final {
public:
    enum class PriceField { Bid, Ask, Last, Close };
    enum class SizeField { Bid, Ask, Last };
    enum class RegisterResult { Added, Existing, Conflict };

    void reset(const QList<ContractSpec> &subscriptions, int firstTickerId = 10'000);
    RegisterResult registerSubscription(const ContractSpec &subscription, int tickerId);
    bool removeSubscription(const QString &subscriptionId, int *tickerId = nullptr,
                            ContractSpec *contract = nullptr);
    bool updatePrice(int tickerId, PriceField field, double value,
                     const QDateTime &receivedAt = QDateTime::currentDateTimeUtc());
    bool updateSize(int tickerId, SizeField field, const QString &decimalValue,
                    const QDateTime &receivedAt = QDateTime::currentDateTimeUtc());
    bool updateExchangeTimestamp(int tickerId, qint64 epochSeconds);
    bool updateMarketDataType(int tickerId, int marketDataType);

    bool containsId(const QString &subscriptionId) const;
    int tickerIdFor(const QString &subscriptionId) const;
    QList<QPair<int, ContractSpec>> tickerContracts() const;
    int subscriptionCount() const;
    QJsonObject quote(const QString &subscriptionId, qint64 staleAfterMs) const;
    QJsonArray quotes(qint64 staleAfterMs) const;
    qint64 updateCount() const;

private:
    struct Record {
        ContractSpec contract;
        int tickerId = 0;
        double bid = 0.0;
        double ask = 0.0;
        double last = 0.0;
        double close = 0.0;
        QString bidSize;
        QString askSize;
        QString lastSize;
        bool hasBid = false;
        bool hasAsk = false;
        bool hasLast = false;
        bool hasClose = false;
        int marketDataType = 0;
        qint64 exchangeEpochSeconds = 0;
        QDateTime receivedAt;
        quint64 sequence = 0;
    };

    static QJsonObject contractJson(const ContractSpec &contract);
    static QJsonObject recordJson(const Record &record, qint64 staleAfterMs,
                                  const QDateTime &now);
    static QString normalizedDecimal(const QString &value);

    mutable QMutex mutex_;
    QHash<int, Record> byTicker_;
    QHash<QString, int> tickerById_;
    qint64 updateCount_ = 0;
};

} // namespace machome::ibkr
