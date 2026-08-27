#pragma once

#include "common/BridgeFrame.h"
#include "common/MarketSchedule.h"
#include "common/MarketTypes.h"
#include "common/SignalEngine.h"
#include "common/SnapshotParser.h"

#include <QFile>
#include <QHash>
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

namespace premium {

class LegacyL1Server;
class PersistenceWriter;

class QuoteWorker final : public QObject {
    Q_OBJECT
public:
    explicit QuoteWorker(QObject *parent = nullptr);
    void process(const BridgeFrame &frame, const QDateTime &now, bool allow30, bool allow300, bool replay);
    void reset(const QString &session = {});
    void resetSymbol(const QString &symbol);

Q_SIGNALS:
    void resultReady(const premium::QuoteSnapshot &snapshot, const QJsonObject &signal, bool hasSignal,
                     qint64 rise30sPpm, qint64 rise300sPpm);
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

Q_SIGNALS:
    void persistRaw(const QByteArray &line, const QDate &partition);
    void persistNormalized(const QByteArray &line, const QDate &partition);
    void persistSignal(const QByteArray &line);

private:
    struct DetailClient {
        QSet<QString> symbols;
    };

    bool loadConfiguration(QString *error);
    bool loadWatchlist(QString *error);
    void acceptAdapter();
    void readAdapter();
    void handleFrame(const BridgeFrame &frame);
    void routeMarketFrame(const BridgeFrame &frame);
    QString symbolHint(const BridgeFrame &frame) const;
    void acceptWebSocket();
    void handleSummaryMessage(QWebSocket *socket, const QString &message);
    void handleDetailMessage(QWebSocket *socket, const QString &message);
    void replaceWatchlist(QWebSocket *socket, const QJsonArray &symbols);
    void publishSnapshot(const QuoteSnapshot &snapshot, const QJsonObject &signal, bool hasSignal,
                         qint64 rise30sPpm, qint64 rise300sPpm);
    void sendSummarySync(QWebSocket *socket);
    void sendJson(QWebSocket *socket, const QJsonObject &object);
    void broadcastSummary(const QJsonObject &object);
    void sendAdapterControl(const QStringList &symbols);
    void updateSchedule();
    void writeOperational(const QString &level, const QString &component, const QString &message,
                          const QJsonObject &fields = {});
    QJsonObject statusObject() const;

    QString configPath_;
    QString rootDirectory_;
    QString dataDirectory_;
    QJsonObject config_;
    QStringList fixedSymbols_;
    QHash<QString, QString> names_;
    bool simulation_ = false;
    bool replay_ = false;
    bool forceQuotes_ = false;

    QLocalServer adapterServer_;
    QPointer<QLocalSocket> adapterSocket_;
    QByteArray adapterBuffer_;
    QString adapterSession_;
    quint64 lastAdapterSequence_ = 0;
    quint64 adapterGapCount_ = 0;
    quint64 rejectedFrameCount_ = 0;
    quint64 monitorSlowClientDrops_ = 0;
    qint64 lastCoreLatencyNs_ = 0;
    qint64 maxCoreLatencyNs_ = 0;
    quint32 lastSdkQueueDepth_ = 0;
    QJsonObject latestRawRecord_;

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
};

} // namespace premium

Q_DECLARE_METATYPE(premium::BridgeFrame)
Q_DECLARE_METATYPE(premium::QuoteSnapshot)
