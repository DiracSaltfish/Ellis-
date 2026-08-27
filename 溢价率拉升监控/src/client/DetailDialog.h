#pragma once

#include "client/QmtClient.h"

#include <QDialog>
#include <QJsonObject>
#include <QPushButton>

class QLabel;
class QDoubleSpinBox;
class QSpinBox;
class QTableWidget;
class QTabWidget;

namespace premium {

class DoubleClickButton final : public QPushButton {
    Q_OBJECT
public:
    using QPushButton::QPushButton;
Q_SIGNALS:
    void doubleClicked();
protected:
    void mouseDoubleClickEvent(QMouseEvent *event) override;
};

class DetailDialog final : public QDialog {
    Q_OBJECT
public:
    DetailDialog(QString symbol, QString name, QList<QmtClient::Profile> profiles,
                 bool replay, bool tradingEnabled = true, QWidget *parent = nullptr);

public Q_SLOTS:
    void applyDetail(const QJsonObject &object);
    void setAConnected(bool connected);

Q_SIGNALS:
    void closed(const QString &symbol);
    void detailSubscriptionRequested(const QString &symbol, bool subscribe);

protected:
    void closeEvent(QCloseEvent *event) override;

private:
    QWidget *buildProfilePage(QmtClient *client);
    void refreshQuoteWarning();
    void refreshProfile(QmtClient *client, QWidget *page);
    void fillActiveLimitPrice(qint64 priceE6);
    qint64 selectedAvailable() const;
    QmtClient *activeClient() const;

    QString symbol_;
    QString name_;
    QLabel *headline_ = nullptr;
    QLabel *warning_ = nullptr;
    QLabel *replayWatermark_ = nullptr;
    QList<QPushButton *> askBookButtons_;
    QList<QPushButton *> bidBookButtons_;
    QTabWidget *tabs_ = nullptr;
    QList<QmtClient *> qmtClients_;
    QHash<QmtClient *, QWidget *> pages_;
    QHash<QmtClient *, QTableWidget *> orderTables_;
    QHash<QmtClient *, QSpinBox *> quantities_;
    QHash<QmtClient *, QDoubleSpinBox *> limitPrices_;
    qint64 bid1E6_ = 0;
    qint64 quoteReceivedMs_ = 0;
    qint64 quoteOriginMs_ = 0;
    bool latestBookHasNonzeroBid_ = false;
    bool replay_ = false;
    bool tradingEnabled_ = true;
    bool aConnected_ = false;
    bool closing_ = false;
};

} // namespace premium
