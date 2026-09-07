#pragma once

#include <QElapsedTimer>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QPointer>

class QTcpSocket;
class QTimer;

namespace machome::qmt {

namespace contract {
enum class SnapshotKind;
}

class QmtClientTestPeer;

struct QmtClientConfig {
    QString id;
    QString host;
    quint16 port = 0;
    int reconnectIntervalMs = 5'000;
    int heartbeatIntervalMs = 5'000;
    int silenceTimeoutMs = 15'000;
    int orderThrottleMs = 5'000;
    int maximumLineBytes = 1024 * 1024;
};

/**
 * Strict event-driven client for the existing QMT newline-delimited JSON
 * backend. It requires welcome(push_sync=true) plus validated full order and
 * position snapshots before trading is enabled.
 */
class QmtClient final : public QObject {
    Q_OBJECT
public:
    explicit QmtClient(QmtClientConfig config, QObject *parent = nullptr);
    ~QmtClient() override;

    QString id() const { return config_.id; }
    bool isReady() const;
    bool wantsConnection() const { return wantsConnection_; }
    QJsonObject snapshot() const;
    qint64 throttleRemainingMs() const;

    bool submitEtfOrder(const QString &symbol, const QString &action,
                        const QString &hubCommandId, QString *error = nullptr);

public slots:
    void connectBackend();
    void disconnectBackend();
    void requestSync();
    void reconcilePendingOrders();

signals:
    void snapshotChanged(const QJsonObject &snapshot);
    void eventOccurred(const QString &kind, const QJsonObject &payload);
    void orderFinished(const QString &hubCommandId, bool ok,
                       const QString &message, const QJsonObject &details);

private:
    friend class QmtClientTestPeer;

    void openSocket();
    void scheduleReconnect();
    void sendPing();
    bool sendJson(const QJsonObject &object);
    void consumeLines();
    void handleMessage(const QJsonObject &message);
    void handlePositions(const QJsonObject &message);
    void handleOrders(const QJsonObject &message);
    void rejectSync(const QString &stream, const QString &reason);
    void failPendingOrders(const QString &reason);
    void confirmPendingOrders();
    void maybeReady();
    void resetSync();
    void publish();
    void setState(const QString &state, const QString &error = {});

    bool validateFullMeta(contract::SnapshotKind kind, const QJsonObject &message,
                          const QJsonArray &data, const QJsonObject &extra,
                          qint64 *snapshotId,
                          qint64 *sequence) const;
    static QJsonArray sortedOrders(const QJsonArray &input, bool *ok = nullptr);
    static QJsonArray sortedPositions(const QJsonArray &input, bool *ok = nullptr);
    static QString normalizeEtfCode(const QString &symbol);

    QmtClientConfig config_;
    QTcpSocket *socket_ = nullptr;
    QTimer *reconnectTimer_ = nullptr;
    QTimer *heartbeatTimer_ = nullptr;
    QTimer *throttleTimer_ = nullptr;
    QByteArray buffer_;
    bool wantsConnection_ = false;
    QString state_ = QStringLiteral("disconnected");
    QString lastError_;
    QJsonValue availableCash_;
    QJsonArray positions_;
    QHash<QString, QJsonObject> orders_;
    QJsonObject lastResult_;
    bool welcomeReceived_ = false;
    bool positionsSynced_ = false;
    bool ordersSynced_ = false;
    qint64 positionsSnapshotId_ = -1;
    qint64 ordersSnapshotId_ = -1;
    qint64 positionsSequence_ = -1;
    qint64 ordersSequence_ = -1;
    QElapsedTimer lastMessageClock_;
    QElapsedTimer orderThrottleClock_;
    struct PendingOrder {
        QString hubCommandId;
        QJsonObject acknowledgement;
        bool uncertaintyReported = false;
    };
    QHash<QString, PendingOrder> pendingOrderCommands_;
};

} // namespace machome::qmt
