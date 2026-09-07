#pragma once

#include "modules/premium/engine/common/BridgeFrame.h"
#include "modules/premium/engine/common/MarketSchedule.h"
#include "modules/premium/engine/common/MarketTypes.h"
#include "modules/premium/engine/common/SignalEngine.h"
#include "modules/premium/engine/common/SnapshotParser.h"

#include <QFile>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QLocalServer>
#include <QPointer>
#include <QSet>
#include <QThread>
#include <QTimer>
#include <QWebSocketServer>

#include <array>
#include <atomic>

class QLocalSocket;
class QWebSocket;

namespace machome::premium::engine {

class LegacyL1Server;
class PersistenceWriter;
class PushPlusNotifier;

class QuoteWorker final : public QObject {
    Q_OBJECT
public:
    explicit QuoteWorker(QObject *parent = nullptr);
    void process(const BridgeFrame &frame, const QDateTime &now, bool allow30, bool allow300,
                 bool replay, bool signalEligible, quint64 publicationGeneration);
    void reset(const QString &session = {});
    void resetSignals();
    void resetSymbol(const QString &symbol);
    void resetSignalSymbol(const QString &symbol);

Q_SIGNALS:
    void resultReady(const machome::premium::engine::QuoteSnapshot &snapshot, const QJsonObject &signal, bool hasSignal,
                     qint64 rise30sPpm, qint64 rise300sPpm, quint64 publicationGeneration);
    void rejected(const QString &symbol, const QStringList &issues, bool waitingForFull);

private:
    SnapshotParser parser_;
    SignalEngine signalEngine_;
};

class CoreServer final : public QObject {
    Q_OBJECT
public:
    explicit CoreServer(QString configPath, bool simulationOverride, bool replayOverride,
                        bool forceQuotesOverride, QObject *parent = nullptr);
    ~CoreServer() override;
    bool start(QString *error = nullptr);
    [[nodiscard]] QJsonObject currentStatus() const;
    [[nodiscard]] QJsonArray currentWatchlist() const;
    [[nodiscard]] QJsonArray currentHotlist() const;
    [[nodiscard]] QJsonObject latestRawRecord() const { return latestRawRecord_; }
    [[nodiscard]] QList<QJsonObject> currentSummaries() const;
    [[nodiscard]] QList<QJsonObject> currentSignals() const;
    [[nodiscard]] QJsonObject currentDetail(const QString &symbol) const;
    bool replaceWatchlistNative(const QJsonArray &symbols, QJsonObject *result,
                                QString *error = nullptr);
    bool replaceHotlistNative(const QJsonArray &symbols, QJsonObject *result,
                              QString *error = nullptr);
    // Mirrors the original A-side --force-quotes switch at runtime. This only
    // changes quote subscription desire; the production signal time gates in
    // MarketSchedule remain in force.
    void setForceQuotesNative(bool enabled);

Q_SIGNALS:
    void persistRaw(const QByteArray &line, const QDate &partition);
    void persistNormalized(const QByteArray &line, const QDate &partition);
    void persistSignal(const QByteArray &line);
    void pushPlusSignal(const QJsonObject &signal);
    void nativeStatusChanged(const QJsonObject &status);
    void nativeSummaryPublished(const QJsonObject &summary);
    void nativeDetailPublished(const QJsonObject &detail);
    void nativeSignalPublished(const QJsonObject &signal);

private:
    struct DetailClient {
        QSet<QString> symbols;
    };

    bool loadConfiguration(QString *error);
    bool loadWatchlist(QString *error);
    bool loadHotlist(QString *error);
    void acceptAdapter();
    void readAdapter();
    void handleFrame(const BridgeFrame &frame);
    void routeMarketFrame(const BridgeFrame &frame);
    QString symbolHint(const BridgeFrame &frame) const;
    void acceptWebSocket();
    void handleSummaryMessage(QWebSocket *socket, const QString &message);
    void handleDetailMessage(QWebSocket *socket, const QString &message);
    void replaceWatchlist(QWebSocket *socket, const QJsonArray &symbols);
    void replaceHotlist(QWebSocket *socket, const QJsonArray &symbols);
    bool persistSymbolList(const QString &configKey, const QString &fallbackPath,
                           const QStringList &symbols, QString *error) const;
    [[nodiscard]] bool shouldPersistMarketEvent(const QString &symbol) const;
    void publishSnapshot(const QuoteSnapshot &snapshot, const QJsonObject &signal, bool hasSignal,
                         qint64 rise30sPpm, qint64 rise300sPpm, quint64 publicationGeneration);
    void sendSummarySync(QWebSocket *socket);
    void sendJson(QWebSocket *socket, const QJsonObject &object);
    void broadcastSummary(const QJsonObject &object);
    void sendAdapterControl(const QStringList &symbols);
    void updateSchedule();
    void invalidateMarketState(const QString &reason, const QString &session = {});
    void writeOperational(const QString &level, const QString &component, const QString &message,
                          const QJsonObject &fields = {});
    QJsonObject statusObject() const;

    QString configPath_;
    QString rootDirectory_;
    QString dataDirectory_;
    QJsonObject config_;
    QStringList fixedSymbols_;
    QStringList hotSymbols_;
    QSet<QString> lastAdapterSymbols_;
    bool lastAdapterQuotesDesired_ = false;
    QHash<QString, QString> names_;
    bool hktEnabled_ = true;
    bool simulation_ = false;
    bool replay_ = false;
    bool forceQuotes_ = false;

    QLocalServer adapterServer_;
    QPointer<QLocalSocket> adapterSocket_;
    QByteArray adapterBuffer_;
    QString adapterSession_;
    quint64 lastAdapterSequence_ = 0;
    quint64 publicationGeneration_ = 1;
    quint64 adapterGapCount_ = 0;
    quint64 rejectedFrameCount_ = 0;
    quint64 monitorSlowClientDrops_ = 0;
    qint64 lastCoreLatencyNs_ = 0;
    qint64 maxCoreLatencyNs_ = 0;
    quint32 lastSdkQueueDepth_ = 0;
    QJsonObject latestRawRecord_;
    bool upstreamHealthy_ = false;
    QString upstreamStatus_ = QStringLiteral("adapter_unavailable");

    QWebSocketServer monitorServer_;
    QSet<QWebSocket *> summaryClients_;
    QHash<QWebSocket *, DetailClient> detailClients_;
    LegacyL1Server *legacy_ = nullptr;

    static constexpr int WorkerCount = 4;
    QList<QThread *> workerThreads_;
    QList<QuoteWorker *> workers_;
    std::array<std::atomic<int>, WorkerCount> workerPending_{};
    std::array<std::atomic<int>, WorkerCount> workerPeak_{};
    std::atomic<quint64> workerDropCount_{0};
    QHash<QString, QuoteSnapshot> cache_;
    QList<QJsonObject> signalHistory_;

    MarketSchedule schedule_;
    ScheduleState scheduleState_;
    QTimer scheduleTimer_;
    QTimer statusTimer_;

    QThread persistenceThread_;
    PersistenceWriter *persistence_ = nullptr;
    std::atomic<int> persistencePending_{0};
    std::atomic<int> persistencePeak_{0};
    bool historicalWritesStopped_ = false;
    QFile operationsLog_;

    QThread pushPlusThread_;
    PushPlusNotifier *pushPlus_ = nullptr;
    QJsonObject pushPlusStatus_{{QStringLiteral("enabled"), false},
                                {QStringLiteral("started"), false}};
};

} // namespace machome::premium::engine

Q_DECLARE_METATYPE(machome::premium::engine::BridgeFrame)
Q_DECLARE_METATYPE(machome::premium::engine::QuoteSnapshot)
