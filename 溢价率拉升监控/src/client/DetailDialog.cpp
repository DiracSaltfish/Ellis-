#include "client/DetailDialog.h"

#include "common/MarketTypes.h"

#include <QCloseEvent>
#include <QDateTime>
#include <QDoubleSpinBox>
#include <QFrame>
#include <QFontDatabase>
#include <QGridLayout>
#include <QHeaderView>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QMouseEvent>
#include <QPushButton>
#include <QSizePolicy>
#include <QSpinBox>
#include <QTabBar>
#include <QTabWidget>
#include <QTableWidget>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>

namespace premium {

void DoubleClickButton::mouseDoubleClickEvent(QMouseEvent *event)
{
    QPushButton::mouseDoubleClickEvent(event);
    Q_EMIT doubleClicked();
}

DetailDialog::DetailDialog(QString symbol, QString name, QList<QmtClient::Profile> profiles,
                           bool replay, bool tradingEnabled, QWidget *parent)
    : QDialog(parent), symbol_(std::move(symbol)), name_(std::move(name)), replay_(replay),
      tradingEnabled_(tradingEnabled)
{
    setAttribute(Qt::WA_DeleteOnClose);
    setWindowTitle(QStringLiteral("%1 盘口与快速交易").arg(symbol_));
    resize(1050, 760);
    setObjectName(QStringLiteral("detailDialog"));
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(16, 12, 16, 12);
    layout->setSpacing(8);
    headline_ = new QLabel(QStringLiteral("%1 %2 · 等待十档快照").arg(symbol_, name_));
    headline_->setStyleSheet(QStringLiteral("font-size:17px;font-weight:700"));
    layout->addWidget(headline_);
    replayWatermark_ = new QLabel(QStringLiteral("REPLAY · 回放模式仍允许真实交易 · 请确认时间"));
    replayWatermark_->setAlignment(Qt::AlignCenter);
    replayWatermark_->setStyleSheet(QStringLiteral("background:#d7263d;color:white;font-size:14px;font-weight:800;padding:5px"));
    replayWatermark_->setVisible(replay_);
    layout->addWidget(replayWatermark_);
    warning_ = new QLabel(QStringLiteral("A 未连接：缓存买一仍可用于卖出，但请核对风险"));
    warning_->setStyleSheet(QStringLiteral("background:#ffdfe3;color:#b00020;font-size:12px;font-weight:700;padding:4px"));
    layout->addWidget(warning_);
    auto *bookPanel = new QWidget;
    bookPanel->setObjectName(QStringLiteral("bookPanel"));
    bookPanel->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
    auto *bookLayout = new QVBoxLayout(bookPanel);
    bookLayout->setContentsMargins(0, 0, 0, 0);
    bookLayout->setSpacing(2);
    auto makeBookButton = [this, bookLayout](const QString &side, int level) {
        auto *button = new QPushButton;
        button->setObjectName(QStringLiteral("book%1%2").arg(side, QString::number(level)));
        button->setProperty("bookSide", side);
        button->setProperty("best", level == 1);
        button->setProperty("priceE6", 0LL);
        button->setFont(QFontDatabase::systemFont(QFontDatabase::FixedFont));
        button->setCursor(Qt::PointingHandCursor);
        button->setToolTip(QStringLiteral("单击只填入当前 QMT 页签的限价，不发送委托"));
        connect(button, &QPushButton::clicked, this, [this, button] {
            const qint64 priceE6 = button->property("priceE6").toLongLong();
            if (priceE6 > 0) fillActiveLimitPrice(priceE6);
        });
        bookLayout->addWidget(button);
        return button;
    };
    for (int level = 5; level >= 1; --level) askBookButtons_.append(makeBookButton(QStringLiteral("ask"), level));
    auto *spreadLine = new QFrame;
    spreadLine->setObjectName(QStringLiteral("spreadLine"));
    spreadLine->setFrameShape(QFrame::HLine);
    bookLayout->addWidget(spreadLine);
    for (int level = 1; level <= 5; ++level) bidBookButtons_.append(makeBookButton(QStringLiteral("bid"), level));
    tabs_ = new QTabWidget;
    tabs_->setObjectName(QStringLiteral("tradeTabs"));
    for (const auto &profile : profiles) {
        auto *client = new QmtClient(profile, tradingEnabled_, this);
        qmtClients_.append(client);
        QWidget *page = buildProfilePage(client);
        pages_.insert(client, page);
        tabs_->addTab(page, profile.name);
        client->connectBackend();
    }
    tabs_->tabBar()->hide();
    auto *profileSelector = new QWidget;
    profileSelector->setObjectName(QStringLiteral("qmtProfileSelector"));
    auto *profileLayout = new QHBoxLayout(profileSelector);
    profileLayout->setContentsMargins(0, 0, 0, 0);
    profileLayout->setSpacing(6);
    profileLayout->addStretch(1);
    QList<QPushButton *> profileButtons;
    for (int index = 0; index < tabs_->count(); ++index) {
        auto *button = new QPushButton(tabs_->tabText(index));
        button->setObjectName(QStringLiteral("qmtProfileButton"));
        button->setCheckable(true);
        button->setMinimumWidth(84);
        button->setProperty("profileIndex", index);
        profileLayout->addWidget(button);
        profileButtons.append(button);
        connect(button, &QPushButton::clicked, this, [this, index] { tabs_->setCurrentIndex(index); });
    }
    if (!profileButtons.isEmpty()) profileButtons.first()->setChecked(true);
    connect(tabs_, &QTabWidget::currentChanged, this, [profileButtons](int index) {
        for (int buttonIndex = 0; buttonIndex < profileButtons.size(); ++buttonIndex)
            profileButtons.at(buttonIndex)->setChecked(buttonIndex == index);
    });

    auto *tradePanel = new QWidget;
    tradePanel->setObjectName(QStringLiteral("tradePanel"));
    auto *tradeLayout = new QVBoxLayout(tradePanel);
    tradeLayout->setContentsMargins(0, 0, 0, 0);
    tradeLayout->setSpacing(5);
    tradeLayout->addWidget(profileSelector, 0, Qt::AlignRight);
    tradeLayout->addWidget(tabs_, 1);

    auto *mainPanels = new QHBoxLayout;
    mainPanels->setContentsMargins(0, 0, 0, 0);
    mainPanels->setSpacing(10);
    mainPanels->addWidget(bookPanel, 1, Qt::AlignTop);
    mainPanels->addWidget(tradePanel, 1);
    layout->addLayout(mainPanels, 1);
    setStyleSheet(QStringLiteral(R"(
        QDialog#detailDialog { background:#f5f7fa; color:#1f2937; }
        QLabel { color:#253044; background:transparent; font-size:13px; }
        QTabWidget#tradeTabs::pane { background:white; border:1px solid #dce3ec;
                    border-radius:8px; top:-1px; }
        QSpinBox, QDoubleSpinBox { color:#172033; background:white; border:1px solid #cbd5e1;
                   border-radius:6px; padding:4px 6px; min-height:18px; }
        QPushButton { color:#283548; background:#f8fafc; border:1px solid #cbd5e1;
                      border-radius:7px; padding:7px 10px; font-weight:700; }
        QPushButton:hover { background:#eef3f9; }
        QPushButton#qmtProfileButton { color:#526074; background:#edf2f7; border-color:#dce3ec;
                      min-height:26px; padding:5px 12px; }
        QPushButton#qmtProfileButton:checked { color:white; background:#2f6feb; border-color:#2f6feb; }
        QPushButton#qmtProfileButton:hover:!checked { color:#2f6feb; background:#e0e9f5; }
        QPushButton#qmtPurchaseButton { color:white; background:#168553; border-color:#168553; font-size:19px; min-height:82px; }
        QPushButton#qmtPurchaseButton:hover { background:#0f6c42; }
        QPushButton#qmtRedeemButton { color:white; background:#c53b45; border-color:#c53b45; font-size:15px; min-height:46px; }
        QPushButton#qmtRedeemButton:hover { background:#aa2733; }
        QPushButton#qmtSellButton { color:white; background:#2f6feb; border-color:#2f6feb; font-size:14px; min-height:42px; }
        QPushButton#qmtSellButton:hover { background:#245dcc; }
        QPushButton#qmtLimitSellButton { color:white; background:#6f42c1; border-color:#6f42c1; font-size:14px; min-height:42px; }
        QPushButton#qmtLimitSellButton:hover { background:#59359d; }
        QPushButton[bookSide="ask"] { color:#d7263d; background:#fff; border:1px solid #d9dee7;
                   border-radius:5px; padding:3px 8px; min-height:18px; text-align:left; font-size:12px; }
        QPushButton[bookSide="bid"] { color:#008f4c; background:#fff; border:1px solid #d9dee7;
                   border-radius:5px; padding:3px 8px; min-height:18px; text-align:left; font-size:12px; }
        QPushButton[bookSide="ask"][best="true"] { background:#ffd8dd; }
        QPushButton[bookSide="bid"][best="true"] { background:#d7f8e2; }
        QPushButton[bookSide]:hover { border:2px solid #2f6feb; padding:2px 7px; }
        QFrame#spreadLine { color:#2f6feb; background:#2f6feb; min-height:2px; max-height:2px; margin:2px 8px; }
        QTableWidget { color:#172033; background:white; alternate-background-color:#f8fafc;
                       border:1px solid #dce3ec; border-radius:8px; gridline-color:#e2e8f0; }
        QTableWidget::item { padding:4px; font-size:12px; }
        QTableWidget::item:selected { background:#dce9ff; color:#172033; }
        QHeaderView::section { background:#eef2f7; color:#526074; border:none;
                       border-right:1px solid #dce3ec; border-bottom:1px solid #dce3ec;
                       padding:4px; font-size:12px; font-weight:700; }
    )"));

    auto *timer = new QTimer(this);
    timer->setInterval(500);
    connect(timer, &QTimer::timeout, this, &DetailDialog::refreshQuoteWarning);
    timer->start();
}

QWidget *DetailDialog::buildProfilePage(QmtClient *client)
{
    auto *page = new QWidget;
    page->setObjectName(QStringLiteral("qmtTradePage"));
    auto *layout = new QVBoxLayout(page);
    layout->setContentsMargins(10, 8, 10, 8);
    layout->setSpacing(6);
    auto *state = new QLabel(QStringLiteral("连接中…"));
    layout->addWidget(state);
    auto *purchase = new DoubleClickButton(QStringLiteral("双击申购 1 篮子"));
    auto *redeem = new DoubleClickButton(QStringLiteral("双击赎回 1 篮子"));
    auto *sell = new DoubleClickButton(QStringLiteral("双击买一价快速卖出"));
    auto *limitSell = new DoubleClickButton(QStringLiteral("双击限价卖出"));
    auto *quantity = new QSpinBox;
    quantity->setRange(0, 2'000'000'000);
    quantity->setSingleStep(100);
    quantity->setValue(100'000);
    quantity->setEnabled(tradingEnabled_);
    quantity->setObjectName(QStringLiteral("qmtSellQuantity"));
    quantities_.insert(client, quantity);
    auto *limitPrice = new QDoubleSpinBox;
    limitPrice->setObjectName(QStringLiteral("qmtLimitPrice"));
    limitPrice->setDecimals(3);
    limitPrice->setRange(0.000, 1'000'000.000);
    limitPrice->setSingleStep(0.001);
    limitPrice->setEnabled(tradingEnabled_);
    limitPrices_.insert(client, limitPrice);
    purchase->setObjectName(QStringLiteral("qmtPurchaseButton"));
    redeem->setObjectName(QStringLiteral("qmtRedeemButton"));
    sell->setObjectName(QStringLiteral("qmtSellButton"));
    limitSell->setObjectName(QStringLiteral("qmtLimitSellButton"));
    purchase->setEnabled(tradingEnabled_);
    redeem->setEnabled(tradingEnabled_);
    sell->setEnabled(tradingEnabled_);
    limitSell->setEnabled(tradingEnabled_);

    layout->addWidget(purchase);
    layout->addWidget(redeem);

    auto *inputs = new QHBoxLayout;
    inputs->setSpacing(6);
    auto *quantityPanel = new QWidget;
    auto *quantityLayout = new QVBoxLayout(quantityPanel);
    quantityLayout->setContentsMargins(0, 0, 0, 0);
    quantityLayout->setSpacing(2);
    quantityLayout->addWidget(new QLabel(QStringLiteral("卖出数量")));
    quantityLayout->addWidget(quantity);
    auto *limitPanel = new QWidget;
    auto *limitLayout = new QVBoxLayout(limitPanel);
    limitLayout->setContentsMargins(0, 0, 0, 0);
    limitLayout->setSpacing(2);
    limitLayout->addWidget(new QLabel(QStringLiteral("限价")));
    limitLayout->addWidget(limitPrice);
    inputs->addWidget(quantityPanel, 1);
    inputs->addWidget(limitPanel, 1);
    layout->addLayout(inputs);

    auto *sellButtons = new QHBoxLayout;
    sellButtons->setSpacing(6);
    sellButtons->addWidget(sell, 1);
    sellButtons->addWidget(limitSell, 1);
    layout->addLayout(sellButtons);
    auto *notice = new QLabel(tradingEnabled_
                                  ? QStringLiteral("数量与限价并排；盘口单击填入限价；只有双击按钮才发送。")
                                  : QStringLiteral("READ ONLY：QMT持仓/委托只读同步，所有交易与撤单调用在 QmtClient 底层禁止。"));
    notice->setWordWrap(true);
    layout->addWidget(notice);
    auto *orders = new QTableWidget(0, 5);
    orders->setObjectName(QStringLiteral("ordersTable"));
    orders->setHorizontalHeaderLabels({QStringLiteral("时间"), QStringLiteral("方向"), QStringLiteral("状态"), QStringLiteral("委托量"), QStringLiteral("委托号（双击撤单）")});
    orders->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    orders->verticalHeader()->setVisible(false);
    orders->verticalHeader()->setDefaultSectionSize(24);
    orders->setAlternatingRowColors(true);
    orders->setEditTriggers(QAbstractItemView::NoEditTriggers);
    orderTables_.insert(client, orders);
    layout->addWidget(orders, 1);
    connect(purchase, &DoubleClickButton::doubleClicked, this, [this, client] { client->sendEtf(symbol_, QStringLiteral("PURCHASE")); });
    connect(redeem, &DoubleClickButton::doubleClicked, this, [this, client] { client->sendEtf(symbol_, QStringLiteral("REDEEM")); });
    connect(sell, &DoubleClickButton::doubleClicked, this, [this, client, quantity] {
        if (bid1E6_ <= 0) {
            warning_->setText(QStringLiteral("尚无非零买一，快速卖出指令未发送。"));
            warning_->show();
            return;
        }
        client->sendSell(symbol_, quantity->value(), bid1E6_);
    });
    connect(limitSell, &DoubleClickButton::doubleClicked, this, [this, client, quantity, limitPrice] {
        const qint64 priceE6 = qRound64(limitPrice->value() * 1'000'000.0);
        if (priceE6 <= 0) {
            warning_->setText(QStringLiteral("请先单击上方盘口档位或手工输入有效限价，限价卖出指令未发送。"));
            warning_->show();
            return;
        }
        client->sendSell(symbol_, quantity->value(), priceE6);
    });
    connect(client, &QmtClient::stateChanged, this, [client, state] {
        state->setText(client->isConnected() ? QStringLiteral("%1 · 已连接").arg(client->profileName())
                                             : QStringLiteral("%1 · 未连接").arg(client->profileName()));
        state->setStyleSheet(client->isConnected() ? QStringLiteral("color:#008f5a;font-weight:700") : QStringLiteral("color:#d7263d;font-weight:700"));
    });
    connect(client, &QmtClient::dataChanged, this, [this, client, page] { refreshProfile(client, page); });
    connect(client, &QmtClient::notice, this, [notice](const QString &text, bool error) {
        notice->setText(text);
        notice->setStyleSheet(error ? QStringLiteral("color:#d7263d;font-weight:700") : QStringLiteral("color:#008f5a"));
    });
    if (tradingEnabled_) {
        connect(orders, &QTableWidget::cellDoubleClicked, this, [client, orders](int row, int) {
            if (auto *item = orders->item(row, 4)) client->cancelOrder(item->data(Qt::UserRole).toString());
        });
    }
    return page;
}

void DetailDialog::applyDetail(const QJsonObject &object)
{
    if (closing_) return;
    if (object.value(QStringLiteral("type")).toString() != QStringLiteral("detail")) return;
    const qint64 incomingBid1E6 = object.value(QStringLiteral("bid1_price_e6")).toInteger();
    latestBookHasNonzeroBid_ = incomingBid1E6 > 0;
    if (incomingBid1E6 > 0) {
        bid1E6_ = incomingBid1E6;
        quoteReceivedMs_ = QDateTime::currentMSecsSinceEpoch();
        const qint64 originMs = exchangeTimeToEpochMs(object.value(QStringLiteral("orig_time")).toInteger());
        if (originMs > 0) quoteOriginMs_ = originMs;
        for (QDoubleSpinBox *price : limitPrices_) {
            if (price->value() <= 0.0) price->setValue(scaledPrice(incomingBid1E6));
        }
    }
    const qint64 premium = object.value(QStringLiteral("sell_premium_ppm")).toInteger();
    if (replay_) {
        const qint64 replayMs = exchangeTimeToEpochMs(object.value(QStringLiteral("orig_time")).toInteger());
        if (replayMs > 0) {
            const qint64 differenceMs = QDateTime::currentMSecsSinceEpoch() - replayMs;
            replayWatermark_->setText(QStringLiteral("REPLAY · 回放 %1 · 实时差 %2 秒 · 仍允许真实交易")
                                          .arg(QDateTime::fromMSecsSinceEpoch(replayMs).toString(QStringLiteral("yyyy-MM-dd HH:mm:ss.zzz")))
                                          .arg(differenceMs / 1000.0, 0, 'f', 1));
        }
    }
    const qint64 iopvE6 = object.value(QStringLiteral("iopv_e6")).toInteger();
    if (iopvE6 > 0) {
        headline_->setText(QStringLiteral("%1 %2 · 买一 %3 · IOPV %4 · 可卖溢价 %5%")
                           .arg(symbol_, name_).arg(scaledPrice(bid1E6_), 0, 'f', 3)
                           .arg(scaledPrice(iopvE6), 0, 'f', 4)
                           .arg(ppmToPercent(premium), 0, 'f', 3));
    } else {
        headline_->setText(QStringLiteral("%1 %2 · 买一 %3 · 无 IOPV · 盘口150s %4% / 300s %5%")
                           .arg(symbol_, name_).arg(scaledPrice(bid1E6_), 0, 'f', 3)
                           .arg(ppmToPercent(object.value(QStringLiteral("bid_rise_150s_ppm")).toInteger()), 0, 'f', 3)
                           .arg(ppmToPercent(object.value(QStringLiteral("bid_rise_300s_ppm")).toInteger()), 0, 'f', 3));
    }
    const QJsonArray bp = object.value(QStringLiteral("bid_prices_e6")).toArray();
    const QJsonArray ap = object.value(QStringLiteral("ask_prices_e6")).toArray();
    const QJsonArray bv = object.value(QStringLiteral("bid_volumes_e2")).toArray();
    const QJsonArray av = object.value(QStringLiteral("ask_volumes_e2")).toArray();
    auto updateBookButton = [](QPushButton *button, const QString &label, qint64 priceE6, qint64 volumeE2) {
        button->setProperty("priceE6", priceE6);
        button->setEnabled(priceE6 > 0);
        const qint64 volumeInLots = static_cast<qint64>(scaledVolume(volumeE2)) / 100;
        button->setText(QStringLiteral("%1    %2    %3")
                            .arg(label, -4)
                            .arg(priceE6 > 0 ? QString::number(scaledPrice(priceE6), 'f', 3) : QStringLiteral("—"), 10)
                            .arg(QString::number(volumeInLots), 14));
    };
    for (int row = 0; row < askBookButtons_.size(); ++row) {
        const int index = 4 - row;
        updateBookButton(askBookButtons_.at(row), QStringLiteral("卖%1").arg(index + 1),
                         index < ap.size() ? ap.at(index).toInteger() : 0,
                         index < av.size() ? av.at(index).toInteger() : 0);
    }
    for (int row = 0; row < bidBookButtons_.size(); ++row) {
        updateBookButton(bidBookButtons_.at(row), QStringLiteral("买%1").arg(row + 1),
                         row < bp.size() ? bp.at(row).toInteger() : 0,
                         row < bv.size() ? bv.at(row).toInteger() : 0);
    }
    if (iopvE6 <= 0) headline_->setStyleSheet(QStringLiteral("font-size:20px;font-weight:700;color:#2f6feb"));
    else if (object.value(QStringLiteral("iopv_static")).toBool()) headline_->setStyleSheet(QStringLiteral("font-size:20px;font-weight:700;color:#b26a00"));
    else headline_->setStyleSheet(QStringLiteral("font-size:20px;font-weight:700"));
    for (QmtClient *client : qmtClients_) refreshProfile(client, pages_.value(client));
    refreshQuoteWarning();
}

void DetailDialog::setAConnected(bool connected)
{
    if (closing_) return;
    aConnected_ = connected;
    refreshQuoteWarning();
}

void DetailDialog::refreshProfile(QmtClient *client, QWidget *)
{
    if (closing_) return;
    const qint64 available = client->availableQuantity(symbol_);
    QSpinBox *quantity = quantities_.value(client);
    constexpr int DefaultQuantity = 100'000;
    if (available > 0) {
        const int defaultQuantity = static_cast<int>(std::min<qint64>(DefaultQuantity, available)) / 100 * 100;
        quantity->setMaximum(defaultQuantity);
        if (!quantity->hasFocus()) quantity->setValue(defaultQuantity);
    } else {
        // QMT 未连接或尚未返回持仓时，不要把默认卖出数量误重置为 0。
        quantity->setMaximum(2'000'000'000);
        if (!quantity->hasFocus() && quantity->value() <= 0) quantity->setValue(DefaultQuantity);
    }
    QTableWidget *table = orderTables_.value(client);
    const QJsonArray orders = client->ordersFor(symbol_);
    table->setRowCount(orders.size());
    for (int row = 0; row < orders.size(); ++row) {
        const QJsonObject order = orders.at(row).toObject();
        const QString id = order.value(QStringLiteral("order_id")).toVariant().toString();
        QString direction = order.value(QStringLiteral("direction")).toString();
        if (direction.isEmpty()) direction = order.value(QStringLiteral("side")).toString();
        const QStringList values{order.value(QStringLiteral("time")).toString(), direction,
                                 order.value(QStringLiteral("status")).toString(), order.value(QStringLiteral("qty")).toVariant().toString(), id};
        for (int column = 0; column < values.size(); ++column) {
            auto *item = new QTableWidgetItem(values.at(column));
            if (column == 4) item->setData(Qt::UserRole, id);
            table->setItem(row, column, item);
        }
    }
}

void DetailDialog::fillActiveLimitPrice(qint64 priceE6)
{
    QDoubleSpinBox *editor = limitPrices_.value(activeClient(), nullptr);
    if (!editor || priceE6 <= 0) return;
    editor->setValue(scaledPrice(priceE6));
    editor->setFocus(Qt::MouseFocusReason);
    editor->selectAll();
}

void DetailDialog::refreshQuoteWarning()
{
    const QDateTime current = QDateTime::currentDateTime();
    const qint64 nowMs = current.toMSecsSinceEpoch();
    const qint64 receiveAge = quoteReceivedMs_ ? nowMs - quoteReceivedMs_ : -1;
    const qint64 sourceAge = (!replay_ && quoteOriginMs_) ? nowMs - quoteOriginMs_ : -1;
    constexpr qint64 staleAfterMs = 30'000;
    qint64 age = receiveAge;
    if (sourceAge >= 0) age = std::max(age, sourceAge);
    if (!aConnected_) {
        warning_->setText(QStringLiteral("A 已断线：允许继续使用缓存买一 %1 卖出；风险由操作者判断。")
                          .arg(bid1E6_ ? QString::number(scaledPrice(bid1E6_), 'f', 3) : QStringLiteral("（无）")));
        warning_->show();
    } else if (!latestBookHasNonzeroBid_) {
        warning_->setText(QStringLiteral("最新盘口买一为 0：仍保留最后非零缓存价 %1，但不会把 0 当卖出限价。")
                              .arg(bid1E6_ ? QString::number(scaledPrice(bid1E6_), 'f', 3) : QStringLiteral("（无）")));
        warning_->show();
    } else if (age < 0 || age > staleAfterMs) {
        warning_->setText(QStringLiteral("行情已过旧（接收龄 %1 ms，交易所龄 %2 ms，当前阈值 %3 ms）："
                                         "不会禁止缓存价卖出。")
                              .arg(receiveAge).arg(sourceAge).arg(staleAfterMs));
        warning_->show();
    } else warning_->hide();
}

QmtClient *DetailDialog::activeClient() const { return qmtClients_.value(tabs_->currentIndex()); }
qint64 DetailDialog::selectedAvailable() const { auto *client = activeClient(); return client ? client->availableQuantity(symbol_) : 0; }

void DetailDialog::closeEvent(QCloseEvent *event)
{
    if (!closing_) {
        closing_ = true;
        Q_EMIT detailSubscriptionRequested(symbol_, false);
        Q_EMIT closed(symbol_);
    }
    QDialog::closeEvent(event);
}

} // namespace premium
