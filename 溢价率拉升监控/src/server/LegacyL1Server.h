#pragma once

#include "common/MarketTypes.h"

#include <QHash>
#include <QJsonValue>
#include <QSet>
#include <QTcpServer>
#include <QTimer>

class QTcpSocket;

namespace premium {

class LegacyL1Server final : public QObject {
    Q_OBJECT
public:
    struct Limits {
        int maxClients = 256;
        int maxSymbolsPerClient = 256;
        int maxMaintainedSymbols = 1000;
        int unsubscribeGraceSeconds = 60;
    };

    explicit LegacyL1Server(QStringList defaults, Limits limits, QObject *parent = nullptr);
    bool listen(const QHostAddress &address, quint16 port, QString *error = nullptr);
    void publish(const QuoteSnapshot &snapshot);
    void setMarketOnline(bool online);
    bool replaceDefaultSymbols(const QStringList &symbols, QString *error = nullptr);
    [[nodiscard]] QStringList maintainedSymbols() const;
    [[nodiscard]] int clientCount() const;

Q_SIGNALS:
    void desiredSymbolsChanged(const QStringList &symbols);
    void operationalEvent(const QString &message);

private:
    struct Client {
        QTcpSocket *socket = nullptr;
        QByteArray input;
        QSet<QString> symbols;
        int intervalMs = 0;
        qint64 lastInboundMs = 0;
        QHash<QString, qint64> lastSentMs;
        QSet<QString> pendingSymbols;
    };

    void acceptPending();
    void readClient(QTcpSocket *socket);
    void closeClient(QTcpSocket *socket, const QString &reason);
    void processLine(Client &client, const QByteArray &line);
    void handleSubscribe(Client &client, const QJsonObject &request);
    void handleUnsubscribe(Client &client, const QJsonObject &request);
    void sendJson(QTcpSocket *socket, const QJsonObject &object);
    void sendError(Client &client, const QJsonValue &id, const QString &code, const QString &message);
    void sendInitial(Client &client, const QStringList &symbols);
    void schedulePendingFlush();
    void flushPending();
    void refreshReferences();
    QJsonObject statusFor(const Client &client, const QJsonValue &id) const;
    static QString operationOf(const QJsonObject &request);
    static QStringList normalizedSymbols(const QJsonValue &value, QStringList *invalid);

    QTcpServer server_;
    QHash<QTcpSocket *, Client> clients_;
    QHash<QString, QuoteSnapshot> cache_;
    QSet<QString> ready_;
    QSet<QString> defaults_;
    QSet<QString> maintained_;
    QHash<QString, qint64> releaseAtMs_;
    Limits limits_;
    QTimer housekeeping_;
    QTimer pendingTimer_;
    quint64 sequence_ = 0;
    quint64 rejectedClients_ = 0;
    QHash<QString, quint64> dropCounts_;
    quint16 port_ = 0;
    bool marketOnline_ = false;
};

} // namespace premium
