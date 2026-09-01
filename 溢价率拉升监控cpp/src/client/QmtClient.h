#pragma once

#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QTcpSocket>
#include <QTimer>

namespace premium {

class QmtClientTests;

class QmtClient final : public QObject {
    Q_OBJECT
public:
    struct Profile { QString name; QString host; quint16 port = 9527; };

    explicit QmtClient(Profile profile, bool tradingEnabled = true, QObject *parent = nullptr);
    ~QmtClient() override;
    void connectBackend();
    [[nodiscard]] bool isConnected() const;
    [[nodiscard]] qint64 availableQuantity(const QString &symbol) const;
    [[nodiscard]] QJsonArray ordersFor(const QString &symbol) const;
    [[nodiscard]] QString profileName() const;
    bool sendEtf(const QString &symbol, const QString &action);
    bool sendSell(const QString &symbol, qint64 quantity, qint64 priceE6);
    bool cancelOrder(const QString &orderId);
    void queryOrders();
    void queryPositions();

Q_SIGNALS:
    void stateChanged();
    void dataChanged();
    void notice(const QString &text, bool error);

private:
    friend class QmtClientTests;
    bool sendRequest(QJsonObject request, const QString &lockKey = {});
    QString newClientOrderId(const QString &symbol, const QString &action) const;
    void readLines();
    void handleMessage(const QJsonObject &message);
    static QString objectSymbol(const QJsonObject &object);

    Profile profile_;
    bool tradingEnabled_ = true;
    QTcpSocket socket_;
    QTimer reconnectTimer_;
    QByteArray input_;
    QHash<QString, qint64> lockedUntilMs_;
    QHash<QString, QString> requestLocks_;
    QHash<QString, qint64> available_;
    QJsonArray orders_;
};

} // namespace premium
