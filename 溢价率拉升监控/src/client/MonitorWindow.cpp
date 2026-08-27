#include "client/MonitorWindow.h"

#include "client/DetailDialog.h"
#include "common/MarketTypes.h"

#include <QApplication>
#include <QAudioFormat>
#include <QAudioSink>
#include <QBuffer>
#include <QCheckBox>
#include <QDateTime>
#include <QDialog>
#include <QDialogButtonBox>
#include <QComboBox>
#include <QDir>
#include <QFormLayout>
#include <QFile>
#include <QFileInfo>
#include <QFrame>
#include <QGroupBox>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QMediaDevices>
#include <QPushButton>
#include <QSaveFile>
#include <QScrollArea>
#include <QSettings>
#include <QSpinBox>
#include <QStatusBar>
#include <QSystemTrayIcon>
#include <QStyle>
#include <QTableWidget>
#include <QTabWidget>
#include <QTimer>
#include <QVBoxLayout>

#include <limits>
#include <cmath>
#include <cstring>
#include <QVector>

namespace premium {
namespace {

class NumericTableItem final : public QTableWidgetItem {
public:
    using QTableWidgetItem::QTableWidgetItem;
    bool operator<(const QTableWidgetItem &other) const override
    {
        return data(Qt::UserRole + 1).toLongLong() < other.data(Qt::UserRole + 1).toLongLong();
    }
};

QTableWidgetItem *numberItem(const QString &text, qint64 sortValue)
{
    auto *item = new NumericTableItem(text);
    item->setData(Qt::UserRole + 1, sortValue);
    return item;
}

QString signalEventKey(const QJsonObject &signal)
{
    return signal.value(QStringLiteral("symbol")).toString() + u'|'
         + signal.value(QStringLiteral("occurred_at")).toString() + u'|'
         + QString::number(signal.value(QStringLiteral("signal_seq")).toInteger());
}

int compareSignalRecency(const QJsonObject &left, const QJsonObject &right)
{
    const QString leftTime = left.value(QStringLiteral("occurred_at")).toString();
    const QString rightTime = right.value(QStringLiteral("occurred_at")).toString();
    const int timeComparison = QString::compare(leftTime, rightTime, Qt::CaseSensitive);
    if (timeComparison != 0) return timeComparison;
    const qint64 leftSequence = left.value(QStringLiteral("signal_seq")).toInteger();
    const qint64 rightSequence = right.value(QStringLiteral("signal_seq")).toInteger();
    return leftSequence == rightSequence ? 0 : (leftSequence > rightSequence ? 1 : -1);
}

QString signalDescription(const QJsonObject &object)
{
    const QString model = object.value(QStringLiteral("model")).toString(QStringLiteral("premium"));
    QString metrics;
    if (model.contains(QStringLiteral("pull"))) {
        metrics = QStringLiteral("盘口150s %1% / 300s %2%")
                      .arg(ppmToPercent(object.value(QStringLiteral("bid_rise_150s_ppm")).toInteger()), 0, 'f', 3)
                      .arg(ppmToPercent(object.value(QStringLiteral("bid_rise_300s_ppm")).toInteger()), 0, 'f', 3);
        if (object.value(QStringLiteral("iopv_e6")).toInteger() > 0
            || object.value(QStringLiteral("premium_ppm")).toInteger() != 0) {
            metrics += QStringLiteral(" · 溢价 %1%")
                           .arg(ppmToPercent(object.value(QStringLiteral("premium_ppm")).toInteger()), 0, 'f', 3);
        } else {
            metrics += QStringLiteral(" · 无IOPV");
        }
    } else {
        metrics = QStringLiteral("可卖溢价 %1%")
                      .arg(ppmToPercent(object.value(QStringLiteral("premium_ppm")).toInteger()), 0, 'f', 3);
    }
    return QStringLiteral("⚡ %1 · %2").arg(object.value(QStringLiteral("reason")).toString(), metrics);
}

void configureQuoteTable(QTableWidget *table, bool signalList)
{
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->setSortingEnabled(false);
    table->horizontalHeader()->setSectionResizeMode(QHeaderView::Interactive);
    table->horizontalHeader()->setStretchLastSection(false);
    table->setColumnWidth(0, 112);
    table->setColumnWidth(1, 180);
    table->setColumnWidth(2, 96);
    table->setColumnWidth(3, 96);
    table->setColumnWidth(4, 156);
    table->setColumnWidth(5, 166);
    table->horizontalHeader()->setSectionResizeMode(6, QHeaderView::Stretch);
    table->setColumnWidth(7, 132);
    if (signalList) table->setColumnWidth(8, 104);
    table->verticalHeader()->setVisible(false);
    table->verticalHeader()->setDefaultSectionSize(40);
    table->setAlternatingRowColors(true);
}

void populateQuoteColumns(QTableWidget *table, int row, const QString &symbol,
                          const QJsonObject &object)
{
    auto *symbolItem = new QTableWidgetItem(symbol);
    symbolItem->setData(Qt::UserRole, symbol);
    table->setItem(row, 0, symbolItem);
    QString name = object.value(QStringLiteral("name")).toString().trimmed();
    if (name.isEmpty()) name = symbol;
    table->setItem(row, 1, new QTableWidgetItem(name));

    const qint64 lastPriceE6 = object.value(QStringLiteral("last_price_e6")).toInteger();
    table->setItem(row, 2, numberItem(lastPriceE6 > 0
                                         ? QString::number(scaledPrice(lastPriceE6), 'f', 3)
                                         : QStringLiteral("—"),
                                     lastPriceE6));
    const bool hasChange = object.contains(QStringLiteral("change_ppm"));
    const qint64 changePpm = object.value(QStringLiteral("change_ppm")).toInteger();
    table->setItem(row, 3, numberItem(hasChange
                                         ? QStringLiteral("%1%").arg(ppmToPercent(changePpm), 0, 'f', 2)
                                         : QStringLiteral("—"),
                                     changePpm));

    const qint64 iopvE6 = object.value(QStringLiteral("iopv_e6")).toInteger();
    const qint64 premiumPpm = object.contains(QStringLiteral("sell_premium_ppm"))
        ? object.value(QStringLiteral("sell_premium_ppm")).toInteger()
        : object.value(QStringLiteral("premium_ppm")).toInteger();
    auto *premium = numberItem(iopvE6 > 0
                                   ? QStringLiteral("%1%").arg(ppmToPercent(premiumPpm), 0, 'f', 3)
                                   : QStringLiteral("—"),
                               iopvE6 > 0 ? premiumPpm : std::numeric_limits<qint64>::min());
    if (iopvE6 > 0 && premiumPpm > 15'000) premium->setBackground(QColor(QStringLiteral("#ffdfe3")));
    table->setItem(row, 4, premium);

    QString iopvText = QStringLiteral("正常");
    QColor iopvColor;
    if (iopvE6 <= 0) {
        iopvText = QStringLiteral("无 IOPV · 盘口模型");
        iopvColor = QColor(QStringLiteral("#2f6feb"));
    } else if (object.value(QStringLiteral("iopv_static")).toBool()) {
        iopvText = QStringLiteral("⚠ 静态 IOPV");
        iopvColor = QColor(QStringLiteral("#b26a00"));
    }
    auto *iopv = new QTableWidgetItem(iopvText);
    if (iopvColor.isValid()) iopv->setForeground(iopvColor);
    table->setItem(row, 5, iopv);
}

QByteArray makeAlertSound(const QAudioFormat &format, const QString &preset, int *durationMs)
{
    if (!format.isValid() || format.sampleRate() <= 0 || format.channelCount() <= 0
        || format.bytesPerSample() <= 0) {
        return {};
    }

    constexpr double kPi = 3.14159265358979323846;
    QVector<float> samples;
    const int sampleRate = format.sampleRate();
    auto appendSilence = [&samples, sampleRate](int duration) {
        samples.resize(samples.size() + qMax(1, qRound(sampleRate * duration / 1'000.0)));
    };
    auto appendTone = [&samples, sampleRate](double startFrequency, double endFrequency,
                                             int duration, float amplitude) {
        const int count = qMax(1, qRound(sampleRate * duration / 1'000.0));
        const int fadeIn = qMax(1, qRound(sampleRate * 0.008));
        const int fadeOut = qMax(1, qRound(sampleRate * 0.025));
        double phase = 0.0;
        for (int index = 0; index < count; ++index) {
            const double ratio = count == 1 ? 0.0 : static_cast<double>(index) / (count - 1);
            const double frequency = startFrequency + (endFrequency - startFrequency) * ratio;
            const double envelopeIn = qMin(1.0, static_cast<double>(index + 1) / fadeIn);
            const double envelopeOut = qMin(1.0, static_cast<double>(count - index) / fadeOut);
            const double envelope = qMin(envelopeIn, envelopeOut);
            samples.append(static_cast<float>(std::sin(phase) * amplitude * envelope));
            phase += 2.0 * kPi * frequency / sampleRate;
        }
    };

    if (preset == QStringLiteral("double")) {
        appendTone(660.0, 660.0, 95, 0.48f);
        appendSilence(55);
        appendTone(990.0, 990.0, 145, 0.48f);
        if (durationMs) *durationMs = 295;
    } else if (preset == QStringLiteral("rising")) {
        appendTone(520.0, 1'320.0, 260, 0.46f);
        if (durationMs) *durationMs = 260;
    } else {
        appendTone(880.0, 880.0, 180, 0.48f);
        if (durationMs) *durationMs = 180;
    }

    const int channels = format.channelCount();
    const int bytesPerSample = format.bytesPerSample();
    QByteArray data(samples.size() * channels * bytesPerSample, Qt::Uninitialized);
    char *output = data.data();
    for (float sample : samples) {
        for (int channel = 0; channel < channels; ++channel) {
            char *destination = output;
            switch (format.sampleFormat()) {
            case QAudioFormat::UInt8: {
                const auto value = static_cast<quint8>(qBound(0, qRound(128.0 + sample * 127.0), 255));
                std::memcpy(destination, &value, sizeof(value));
                break;
            }
            case QAudioFormat::Int16: {
                const auto value = static_cast<qint16>(qBound(-32'767, qRound(sample * 32'767.0), 32'767));
                std::memcpy(destination, &value, sizeof(value));
                break;
            }
            case QAudioFormat::Int32: {
                const qint64 raw = qRound64(sample * 2'147'483'647.0);
                const auto value = static_cast<qint32>(qBound(qint64(-2'147'483'647), raw,
                                                               qint64(2'147'483'647)));
                std::memcpy(destination, &value, sizeof(value));
                break;
            }
            case QAudioFormat::Float:
                std::memcpy(destination, &sample, sizeof(sample));
                break;
            case QAudioFormat::Unknown:
                return {};
            default:
                return {};
            }
            output += bytesPerSample;
        }
    }
    return data;
}

} // namespace

MonitorWindow::MonitorWindow(ClientSettings settings, QString settingsPath,
                             bool tradingEnabled, QWidget *parent)
    : QMainWindow(parent), settings_(std::move(settings)), settingsPath_(std::move(settingsPath)),
      serverBase_(settings_.serverBase), profiles_(settings_.profiles),
      alertSoundPreset_(settings_.alertSoundPreset), alertSoundRepeat_(settings_.alertSoundRepeat),
      tradingEnabled_(tradingEnabled), soundEnabled_(settings_.soundEnabled),
      popupEnabled_(settings_.popupEnabled)
{
    buildUi();
    loadSignalList();
    summaryFlushTimer_.setSingleShot(true);
    summaryFlushTimer_.setInterval(settings_.summaryRefreshMs);
    connect(&summaryFlushTimer_, &QTimer::timeout, this, &MonitorWindow::flushPendingSummaries);
    soundTimer_.setSingleShot(true);
    connect(&soundTimer_, &QTimer::timeout, this, &MonitorWindow::playNextAlertSound);
    connect(&summary_, &QWebSocket::connected, this, [this] {
        lastSummaryError_.clear();
        connection_->setToolTip(QString());
        connection_->setText(QStringLiteral("A 已连接"));
        connection_->setStyleSheet(QStringLiteral("background:#008f5a;color:white;padding:8px;font-weight:700"));
        synchronized_ = false;
        summaryFlushTimer_.stop();
        pendingSummaries_.clear();
    });
    connect(&summary_, &QWebSocket::errorOccurred, this, [this](QAbstractSocket::SocketError) {
        lastSummaryError_ = summary_.errorString();
        connection_->setToolTip(lastSummaryError_);
        connection_->setText(QStringLiteral("A 连接失败 · %1").arg(lastSummaryError_));
        connection_->setStyleSheet(QStringLiteral("background:#d7263d;color:white;padding:8px;font-weight:700"));
    });
    connect(&summary_, &QWebSocket::disconnected, this, [this] {
        connection_->setText(lastSummaryError_.isEmpty()
                                 ? QStringLiteral("A 未连接 · 3秒后重试")
                                 : QStringLiteral("A 未连接 · %1 · 3秒后重试").arg(lastSummaryError_));
        connection_->setStyleSheet(QStringLiteral("background:#d7263d;color:white;padding:8px;font-weight:700"));
        synchronized_ = false;
        if (syncBatching_) {
            syncBatching_ = false;
            table_->setUpdatesEnabled(true);
            signalTable_->setUpdatesEnabled(true);
        }
        QTimer::singleShot(3000, this, &MonitorWindow::connectSummary);
    });
    connect(&summary_, &QWebSocket::textMessageReceived, this, &MonitorWindow::processMessage);
    connect(&detail_, &QWebSocket::connected, this, [this] {
        for (auto it = details_.begin(); it != details_.end(); ++it) {
            if (!it.value()) continue;
            it.value()->setAConnected(true);
            detail_.sendTextMessage(QString::fromUtf8(QJsonDocument(QJsonObject{{"op", "subscribe"}, {"symbol", it.key()}}).toJson(QJsonDocument::Compact)));
        }
    });
    connect(&detail_, &QWebSocket::disconnected, this, [this] {
        for (const QPointer<DetailDialog> &dialog : details_) {
            if (dialog) dialog->setAConnected(false);
        }
        if (!details_.isEmpty()) QTimer::singleShot(3000, this, &MonitorWindow::connectDetail);
    });
    connect(&detail_, &QWebSocket::errorOccurred, this, [this](QAbstractSocket::SocketError) {
        statusBar()->showMessage(QStringLiteral("详情行情连接失败：%1").arg(detail_.errorString()), 8'000);
    });
    connect(&detail_, &QWebSocket::textMessageReceived, this, &MonitorWindow::processDetailMessage);
    connectSummary();
}

MonitorWindow::~MonitorWindow()
{
    soundTimer_.stop();
    summaryFlushTimer_.stop();
    summary_.abort();
    detail_.abort();
    const QList<QPointer<DetailDialog>> dialogs = details_.values();
    for (const QPointer<DetailDialog> &dialog : dialogs) {
        if (!dialog) continue;
        QObject::disconnect(dialog, nullptr, this, nullptr);
        dialog->close();
    }
    details_.clear();
}

void MonitorWindow::buildUi()
{
    setWindowTitle(QStringLiteral("ETF 溢价率拉升监控 · 客户端 B%1")
                       .arg(tradingEnabled_ ? QString() : QStringLiteral(" · READ ONLY")));
    resize(1280, 820);
    auto *central = new QWidget;
    central->setObjectName(QStringLiteral("appRoot"));
    auto *layout = new QVBoxLayout(central);
    layout->setContentsMargins(20, 18, 20, 18);
    layout->setSpacing(14);
    auto *top = new QHBoxLayout;
    connection_ = new QLabel(QStringLiteral("A 连接中…"));
    serverState_ = new QLabel(QStringLiteral("等待状态"));
    auto *reconnect = new QPushButton(QStringLiteral("重新连接"));
    reconnect->setObjectName(QStringLiteral("primaryButton"));
    connect(reconnect, &QPushButton::clicked, this, [this] {
        lastSummaryError_.clear();
        summary_.abort();
        connectSummary();
        if (!details_.isEmpty()) {
            detail_.abort();
            connectDetail();
        }
    });
    auto *settings = new QPushButton(QStringLiteral("设置…"));
    connect(settings, &QPushButton::clicked, this, &MonitorWindow::showSettings);
    top->addWidget(connection_);
    top->addWidget(serverState_, 1);
    top->addWidget(settings);
    top->addWidget(reconnect);
    layout->addLayout(top);
    if (!tradingEnabled_) {
        auto *readOnly = new QLabel(QStringLiteral("READ ONLY · 生产行情验收中 · QMT只同步查询，申赎/卖出/撤单在客户端底层禁止"));
        readOnly->setAlignment(Qt::AlignCenter);
        readOnly->setStyleSheet(QStringLiteral("background:#ffcf66;color:#5a3b00;padding:8px;font-size:16px;font-weight:800"));
        layout->addWidget(readOnly);
    }
    replayBanner_ = new QLabel(QStringLiteral("REPLAY · 行情为回放数据 · 交易按钮仍连接真实 QMT"));
    replayBanner_->setAlignment(Qt::AlignCenter);
    replayBanner_->setStyleSheet(QStringLiteral("background:#d7263d;color:white;padding:8px;font-size:17px;font-weight:800"));
    replayBanner_->hide();
    layout->addWidget(replayBanner_);
    listTabs_ = new QTabWidget;
    listTabs_->setObjectName(QStringLiteral("marketListTabs"));

    auto *signalPage = new QWidget;
    auto *signalLayout = new QVBoxLayout(signalPage);
    signalLayout->setContentsMargins(0, 10, 0, 0);
    signalLayout->setSpacing(8);
    auto *signalHint = new QLabel(QStringLiteral("最近触发置顶；同一标的只保留最新一次。记录保存在本机，只有点击行内“本次移除”才删除。"));
    signalHint->setObjectName(QStringLiteral("listHint"));
    signalLayout->addWidget(signalHint);
    signalTable_ = new QTableWidget(0, 9);
    signalTable_->setObjectName(QStringLiteral("signalTable"));
    signalTable_->setHorizontalHeaderLabels({QStringLiteral("标的"), QStringLiteral("名称"), QStringLiteral("价格"), QStringLiteral("涨跌幅"),
                                             QStringLiteral("可卖IOPV溢价率"), QStringLiteral("IOPV状态"), QStringLiteral("信号"),
                                             QStringLiteral("最近触发"), QStringLiteral("操作")});
    configureQuoteTable(signalTable_, true);
    signalLayout->addWidget(signalTable_, 1);

    auto *globalPage = new QWidget;
    auto *globalLayout = new QVBoxLayout(globalPage);
    globalLayout->setContentsMargins(0, 10, 0, 0);
    globalLayout->setSpacing(8);
    auto *globalHint = new QLabel(QStringLiteral("全部观察标的；始终按证券代码固定顺序，行情刷新和信号触发都不会改变行位置。"));
    globalHint->setObjectName(QStringLiteral("listHint"));
    globalLayout->addWidget(globalHint);
    table_ = new QTableWidget(0, 8);
    table_->setObjectName(QStringLiteral("monitorTable"));
    table_->setHorizontalHeaderLabels({QStringLiteral("标的"), QStringLiteral("名称"), QStringLiteral("价格"), QStringLiteral("涨跌幅"),
                                       QStringLiteral("可卖IOPV溢价率"), QStringLiteral("IOPV状态"), QStringLiteral("信号"), QStringLiteral("更新时间")});
    configureQuoteTable(table_, false);
    globalLayout->addWidget(table_, 1);

    listTabs_->addTab(signalPage, QStringLiteral("信号列表"));
    listTabs_->addTab(globalPage, QStringLiteral("全局列表"));
    listTabs_->setCurrentIndex(0);
    connect(table_, &QTableWidget::cellDoubleClicked, this, [this](int row, int) {
        if (auto *item = table_->item(row, 0)) openDetail(item->data(Qt::UserRole).toString(), table_->item(row, 1)->text());
    });
    connect(signalTable_, &QTableWidget::cellDoubleClicked, this, [this](int row, int column) {
        if (column == 8) return;
        if (auto *item = signalTable_->item(row, 0)) {
            openDetail(item->data(Qt::UserRole).toString(), signalTable_->item(row, 1)->text());
        }
    });
    layout->addWidget(listTabs_, 1);
    setCentralWidget(central);
    setStyleSheet(QStringLiteral(R"(
        QMainWindow, QWidget#appRoot { background:#f5f7fa; color:#1f2937; }
        QLabel { background:transparent; color:#253044; }
        QPushButton { color:#283548; background:#f8fafc; border:1px solid #cbd5e1;
                      border-radius:7px; padding:8px 14px; font-weight:600; }
        QPushButton:hover { background:#eef3f9; }
        QPushButton#primaryButton { color:white; background:#2f6feb; border-color:#2f6feb; }
        QPushButton#primaryButton:hover { background:#245dcc; }
        QTabWidget#marketListTabs::pane { border:1px solid #dce3ec; border-radius:10px; background:white; }
        QTabBar::tab { background:#e8eef6; color:#526074; border:1px solid #cbd5e1;
                       border-bottom:none; padding:11px 28px; min-width:120px; font-weight:700; }
        QTabBar::tab:selected { background:#2f6feb; color:white; }
        QLabel#listHint { color:#68758a; padding:0 8px; }
        QTableWidget#monitorTable, QTableWidget#signalTable { color:#172033; background:white; alternate-background-color:#f8fafc;
                      border:1px solid #dce3ec; border-radius:10px;
                      gridline-color:#e2e8f0; font-size:13px; }
        QTableWidget#monitorTable::item, QTableWidget#signalTable::item { padding:7px; }
        QTableWidget#monitorTable::item:selected, QTableWidget#signalTable::item:selected { background:#dce9ff; color:#172033; }
        QPushButton#removeSignalButton { color:#b42318; background:#fff5f4; border-color:#f0b4ae; padding:5px 9px; }
        QPushButton#removeSignalButton:hover { background:#ffe5e1; }
        QHeaderView::section { background:#eef2f7; color:#526074; border:none;
                      border-right:1px solid #dce3ec; border-bottom:1px solid #dce3ec;
                      padding:9px; font-weight:700; }
        QStatusBar { background:#f5f7fa; color:#68758a; }
    )"));
    statusBar()->showMessage(QStringLiteral("首页默认为信号列表；双击任一列表中的证券可打开详情。"));
    tray_ = new QSystemTrayIcon(QApplication::style()->standardIcon(QStyle::SP_MessageBoxInformation), this);
    tray_->setToolTip(QStringLiteral("ETF 溢价率拉升监控"));
    tray_->show();
}

QUrl MonitorWindow::endpoint(const QString &path) const
{
    QUrl url = serverBase_;
    url.setPath(path);
    return url;
}

void MonitorWindow::connectSummary()
{
    if (summary_.state() == QAbstractSocket::ConnectedState || summary_.state() == QAbstractSocket::ConnectingState) return;
    summary_.open(endpoint(QStringLiteral("/ws/v2/summary")));
}

void MonitorWindow::connectDetail()
{
    if (details_.isEmpty()) return;
    if (detail_.state() == QAbstractSocket::ConnectedState || detail_.state() == QAbstractSocket::ConnectingState) return;
    detail_.open(endpoint(QStringLiteral("/ws/v2/detail")));
}

void MonitorWindow::processDetailMessage(const QString &message)
{
    const QJsonObject object = QJsonDocument::fromJson(message.toUtf8()).object();
    if (object.value(QStringLiteral("type")).toString() != QStringLiteral("detail")) return;
    const QString symbol = object.value(QStringLiteral("s")).toString();
    const QPointer<DetailDialog> dialog = details_.value(symbol);
    if (dialog) dialog->applyDetail(object);
}

void MonitorWindow::processMessage(const QString &message)
{
    const QJsonObject object = QJsonDocument::fromJson(message.toUtf8()).object();
    const QString type = object.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("summary")) {
        if (syncBatching_ || !synchronized_) {
            updateSummary(object);
        } else {
            const QString symbol = object.value(QStringLiteral("s")).toString();
            if (!symbol.isEmpty()) pendingSummaries_.insert(symbol, object);
            if (!summaryFlushTimer_.isActive()) summaryFlushTimer_.start();
        }
    }
    else if (type == QStringLiteral("signal")) processSignal(object);
    else if (type == QStringLiteral("symbol_removed")) {
        const QString symbol = object.value(QStringLiteral("symbol")).toString();
        snapshots_.remove(symbol);
        const int row = findRow(symbol);
        if (row >= 0) table_->removeRow(row);
        symbolItems_.remove(symbol);
        if (const QPointer<DetailDialog> dialog = details_.value(symbol); dialog) dialog->close();
    }
    else if (type == QStringLiteral("sync_begin")) {
        synchronized_ = false;
        if (!syncBatching_) {
            syncBatching_ = true;
            table_->setUpdatesEnabled(false);
            signalTable_->setUpdatesEnabled(false);
        }
        table_->setRowCount(0);
        summaryFlushTimer_.stop();
        pendingSummaries_.clear();
        snapshots_.clear();
        symbolItems_.clear();
        replay_ = object.value(QStringLiteral("replay")).toBool();
        replayBanner_->setVisible(replay_);
    } else if (type == QStringLiteral("sync_complete")) {
        synchronized_ = true;
        if (syncBatching_) {
            syncBatching_ = false;
            table_->setUpdatesEnabled(true);
            signalTable_->setUpdatesEnabled(true);
            table_->viewport()->update();
            signalTable_->viewport()->update();
        }
    }
    else if (type == QStringLiteral("status")) {
        serverState_->setText(QStringLiteral("阶段 %1 · 就绪 %2 · 隔离 %3 · A→B %4客户端 · 信号 %5")
                              .arg(object.value(QStringLiteral("phase")).toString())
                              .arg(object.value(QStringLiteral("ready_symbols")).toInt())
                              .arg(object.value(QStringLiteral("quarantined")).toInteger())
                              .arg(object.value(QStringLiteral("summary_clients")).toInt())
                              .arg(signalTable_->rowCount()));
    }
}

void MonitorWindow::flushPendingSummaries()
{
    QHash<QString, QJsonObject> pending;
    pending.swap(pendingSummaries_);
    table_->setUpdatesEnabled(false);
    signalTable_->setUpdatesEnabled(false);
    for (auto it = pending.cbegin(); it != pending.cend(); ++it) updateSummary(it.value());
    table_->setUpdatesEnabled(true);
    signalTable_->setUpdatesEnabled(true);
    table_->viewport()->update();
    signalTable_->viewport()->update();
}

void MonitorWindow::showSettings()
{
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("客户端 B 设置"));
    dialog.setMinimumSize(560, 520);
    dialog.resize(640, 700);
    auto *outerLayout = new QVBoxLayout(&dialog);
    outerLayout->setContentsMargins(12, 12, 12, 12);
    outerLayout->setSpacing(8);

    auto *scroll = new QScrollArea(&dialog);
    scroll->setObjectName(QStringLiteral("settingsScrollArea"));
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setWidgetResizable(true);
    auto *content = new QWidget;
    auto *layout = new QVBoxLayout(content);
    layout->setContentsMargins(4, 4, 4, 4);
    layout->setSpacing(8);

    auto *aGroup = new QGroupBox(QStringLiteral("服务端 A"));
    auto *aForm = new QFormLayout(aGroup);
    auto *aHost = new QLineEdit(serverBase_.host());
    auto *aPort = new QSpinBox;
    aPort->setRange(1, 65'535);
    aPort->setValue(serverBase_.port(8421));
    aForm->addRow(QStringLiteral("IP / 主机名"), aHost);
    aForm->addRow(QStringLiteral("WebSocket 端口"), aPort);
    layout->addWidget(aGroup);

    QList<QLineEdit *> qmtHosts;
    QList<QSpinBox *> qmtPorts;
    for (int index = 0; index < 2; ++index) {
        const QmtClient::Profile profile = index < profiles_.size()
            ? profiles_.at(index)
            : QmtClient::Profile{QStringLiteral("QMT%1").arg(index + 1),
                                 index == 0 ? QStringLiteral("192.168.1.112") : QStringLiteral("192.168.1.111"),
                                 9527};
        auto *group = new QGroupBox(profile.name);
        auto *form = new QFormLayout(group);
        auto *host = new QLineEdit(profile.host);
        auto *port = new QSpinBox;
        port->setRange(1, 65'535);
        port->setValue(profile.port);
        form->addRow(QStringLiteral("IP / 主机名"), host);
        form->addRow(QStringLiteral("TCP 端口"), port);
        qmtHosts.append(host);
        qmtPorts.append(port);
        layout->addWidget(group);
    }

    auto *otherGroup = new QGroupBox(QStringLiteral("提醒与显示"));
    auto *otherForm = new QFormLayout(otherGroup);
    auto *sound = new QCheckBox(QStringLiteral("实时信号声音提醒"));
    sound->setChecked(soundEnabled_);
    auto *soundPreset = new QComboBox;
    soundPreset->setObjectName(QStringLiteral("alertSoundPreset"));
    soundPreset->addItem(QStringLiteral("标准短音"), QStringLiteral("classic"));
    soundPreset->addItem(QStringLiteral("双音提醒"), QStringLiteral("double"));
    soundPreset->addItem(QStringLiteral("上扬提醒"), QStringLiteral("rising"));
    const int presetIndex = soundPreset->findData(alertSoundPreset_);
    soundPreset->setCurrentIndex(presetIndex >= 0 ? presetIndex : 0);
    auto *soundRepeat = new QSpinBox;
    soundRepeat->setObjectName(QStringLiteral("alertSoundRepeat"));
    soundRepeat->setRange(1, 3);
    soundRepeat->setSuffix(QStringLiteral(" 次"));
    soundRepeat->setValue(qBound(1, alertSoundRepeat_, 3));
    auto *soundPreview = new QPushButton(QStringLiteral("试听提示音"));
    soundPreview->setObjectName(QStringLiteral("alertSoundPreview"));
    connect(soundPreview, &QPushButton::clicked, this, [this, soundPreset, soundRepeat] {
        playAlertSound(soundPreset->currentData().toString(), soundRepeat->value());
        statusBar()->showMessage(QStringLiteral("正在试听提示音…"), 2'000);
    });
    auto *popup = new QCheckBox(QStringLiteral("实时信号系统弹窗"));
    popup->setChecked(popupEnabled_);
    auto *refresh = new QSpinBox;
    refresh->setRange(50, 3'000);
    refresh->setSuffix(QStringLiteral(" ms"));
    refresh->setValue(summaryFlushTimer_.interval());
    otherForm->addRow(sound);
    otherForm->addRow(QStringLiteral("提示音预置"), soundPreset);
    otherForm->addRow(QStringLiteral("提示音次数"), soundRepeat);
    otherForm->addRow(QString(), soundPreview);
    otherForm->addRow(popup);
    otherForm->addRow(QStringLiteral("主表显示刷新间隔"), refresh);
    layout->addWidget(otherGroup);

    auto *path = new QLabel(QStringLiteral("保存到：%1\n保存后 A/QMT 使用新地址重连；已打开详情页会关闭。")
                                .arg(settingsPath_));
    path->setWordWrap(true);
    layout->addWidget(path);
    layout->addStretch(1);
    scroll->setWidget(content);
    outerLayout->addWidget(scroll, 1);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Save | QDialogButtonBox::Cancel);
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    outerLayout->addWidget(buttons, 0, Qt::AlignRight);
    if (dialog.exec() != QDialog::Accepted) return;

    if (aHost->text().trimmed().isEmpty() || qmtHosts.at(0)->text().trimmed().isEmpty()
        || qmtHosts.at(1)->text().trimmed().isEmpty()) {
        QMessageBox::warning(this, QStringLiteral("设置无效"), QStringLiteral("A/QMT 主机名不能为空。"));
        return;
    }
    ClientSettings next;
    next.serverBase = QUrl(QStringLiteral("ws://%1:%2").arg(aHost->text().trimmed()).arg(aPort->value()));
    next.profiles = {
        {QStringLiteral("QMT1"), qmtHosts.at(0)->text().trimmed(), static_cast<quint16>(qmtPorts.at(0)->value())},
        {QStringLiteral("QMT2"), qmtHosts.at(1)->text().trimmed(), static_cast<quint16>(qmtPorts.at(1)->value())}
    };
    next.soundEnabled = sound->isChecked();
    next.alertSoundPreset = soundPreset->currentData().toString();
    next.alertSoundRepeat = soundRepeat->value();
    next.popupEnabled = popup->isChecked();
    next.summaryRefreshMs = refresh->value();
    QString error;
    if (!saveClientSettings(settingsPath_, next, &error)) {
        QMessageBox::critical(this, QStringLiteral("保存失败"), error);
        return;
    }

    soundTimer_.stop();
    soundRemaining_ = 0;
    if (audioSink_) audioSink_->stop();
    const QList<QPointer<DetailDialog>> dialogs = details_.values();
    for (const QPointer<DetailDialog> &detailDialog : dialogs) {
        if (detailDialog) detailDialog->close();
    }
    settings_ = next;
    serverBase_ = next.serverBase;
    profiles_ = next.profiles;
    soundEnabled_ = next.soundEnabled;
    alertSoundPreset_ = next.alertSoundPreset;
    alertSoundRepeat_ = next.alertSoundRepeat;
    popupEnabled_ = next.popupEnabled;
    summaryFlushTimer_.setInterval(next.summaryRefreshMs);
    summary_.abort();
    detail_.abort();
    QTimer::singleShot(0, this, &MonitorWindow::connectSummary);
    statusBar()->showMessage(QStringLiteral("设置已保存，正在重连 A/QMT。"), 8'000);
}

int MonitorWindow::findRow(const QString &symbol) const
{
    QTableWidgetItem *item = symbolItems_.value(symbol, nullptr);
    if (!item || item->tableWidget() != table_) return -1;
    return item->row();
}

int MonitorWindow::fixedInsertRow(const QString &symbol) const
{
    int row = 0;
    while (row < table_->rowCount()) {
        QTableWidgetItem *item = table_->item(row, 0);
        if (!item) break;
        const QString existing = item->data(Qt::UserRole).toString();
        if (existing > symbol) break;
        ++row;
    }
    return row;
}

int MonitorWindow::findSignalRow(const QString &symbol) const
{
    QTableWidgetItem *item = signalItems_.value(symbol, nullptr);
    if (!item || item->tableWidget() != signalTable_) return -1;
    return item->row();
}

void MonitorWindow::updateSignalRow(const QJsonObject &signal, const QString &text,
                                    const QString &eventKey, bool alreadyRead)
{
    const QString symbol = signal.value(QStringLiteral("symbol")).toString();
    if (symbol.isEmpty()) return;
    if (dismissedSignals_.contains(symbol)
        && compareSignalRecency(signal, dismissedSignals_.value(symbol)) <= 0) return;
    if (latestSignals_.contains(symbol)
        && compareSignalRecency(signal, latestSignals_.value(symbol)) <= 0) return;

    const int oldRow = findSignalRow(symbol);
    if (oldRow >= 0) signalTable_->removeRow(oldRow);
    signalItems_.remove(symbol);

    int row = 0;
    while (row < signalTable_->rowCount()) {
        const QTableWidgetItem *item = signalTable_->item(row, 0);
        if (!item) break;
        const QString existingSymbol = item->data(Qt::UserRole).toString();
        if (!latestSignals_.contains(existingSymbol)
            || compareSignalRecency(signal, latestSignals_.value(existingSymbol)) > 0) break;
        ++row;
    }
    signalTable_->insertRow(row);
    const QJsonObject quote = snapshots_.contains(symbol) ? snapshots_.value(symbol) : signal;
    populateQuoteColumns(signalTable_, row, symbol, quote);
    signalItems_.insert(symbol, signalTable_->item(row, 0));

    auto *signalItem = new NumericTableItem((alreadyRead ? QStringLiteral("✓ 已读 · ") : QString()) + text);
    signalItem->setData(Qt::UserRole + 1, signal.value(QStringLiteral("signal_seq")).toInteger());
    signalItem->setData(Qt::UserRole + 2, eventKey);
    signalItem->setBackground(QColor(QStringLiteral("#ffb3be")));
    signalTable_->setItem(row, 6, signalItem);

    QDateTime occurred = QDateTime::fromString(signal.value(QStringLiteral("occurred_at")).toString(), Qt::ISODateWithMs);
    if (!occurred.isValid()) occurred = QDateTime::fromString(signal.value(QStringLiteral("occurred_at")).toString(), Qt::ISODate);
    const QString occurredText = occurred.isValid()
        ? occurred.toString(QStringLiteral("MM-dd HH:mm:ss.zzz"))
        : signal.value(QStringLiteral("occurred_at")).toString();
    auto *timeItem = new QTableWidgetItem(occurredText.isEmpty() ? QStringLiteral("—") : occurredText);
    timeItem->setData(Qt::UserRole + 1, signal.value(QStringLiteral("occurred_at")).toString());
    signalTable_->setItem(row, 7, timeItem);

    auto *remove = new QPushButton(QStringLiteral("本次移除"));
    remove->setObjectName(QStringLiteral("removeSignalButton"));
    remove->setToolTip(QStringLiteral("仅从本机 B 的信号列表移除；不改变 A 的观察清单或订阅"));
    connect(remove, &QPushButton::clicked, this, [this, symbol] { removeSignalRow(symbol); });
    signalTable_->setCellWidget(row, 8, remove);
    latestSignals_.insert(symbol, signal);
    dismissedSignals_.remove(symbol);
}

void MonitorWindow::refreshSignalQuote(const QString &symbol)
{
    const int row = findSignalRow(symbol);
    if (row < 0 || !snapshots_.contains(symbol)) return;
    populateQuoteColumns(signalTable_, row, symbol, snapshots_.value(symbol));
    signalItems_.insert(symbol, signalTable_->item(row, 0));
}

void MonitorWindow::removeSignalRow(const QString &symbol)
{
    const int row = findSignalRow(symbol);
    if (row < 0) return;
    if (latestSignals_.contains(symbol)) dismissedSignals_.insert(symbol, latestSignals_.value(symbol));
    signalTable_->removeRow(row);
    signalItems_.remove(symbol);
    latestSignals_.remove(symbol);
    listTabs_->setTabText(0, QStringLiteral("信号列表 (%1)").arg(signalTable_->rowCount()));
    saveSignalList();
    statusBar()->showMessage(QStringLiteral("已从本机信号列表移除 %1；A 观察清单和订阅未改变。新信号再次触发时会重新出现。")
                                 .arg(symbol), 8'000);
}

void MonitorWindow::markSignalRead(const QString &symbol)
{
    const QJsonObject signal = latestSignals_.value(symbol);
    if (signal.isEmpty()) return;
    const QString eventKey = signalEventKey(signal);
    const quint64 sequence = static_cast<quint64>(signal.value(QStringLiteral("signal_seq")).toInteger());
    QSettings settings;
    settings.setValue(QStringLiteral("read/%1").arg(symbol), sequence);
    settings.setValue(QStringLiteral("read_event/%1").arg(symbol), eventKey);

    auto markItem = [](QTableWidgetItem *item) {
        if (!item || item->text().startsWith(QStringLiteral("✓ 已读 · "))) return;
        item->setText(QStringLiteral("✓ 已读 · ") + item->text());
    };
    const int globalRow = findRow(symbol);
    if (globalRow >= 0) markItem(table_->item(globalRow, 6));
    const int signalRow = findSignalRow(symbol);
    if (signalRow >= 0) markItem(signalTable_->item(signalRow, 6));
}

QString MonitorWindow::signalCachePath() const
{
    return QFileInfo(settingsPath_).dir().filePath(QStringLiteral("client-signal-list.json"));
}

void MonitorWindow::loadSignalList()
{
    QFile file(signalCachePath());
    if (!file.exists()) return;
    if (!file.open(QIODevice::ReadOnly)) {
        statusBar()->showMessage(QStringLiteral("无法读取本机信号列表：%1").arg(file.errorString()), 8'000);
        return;
    }
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        statusBar()->showMessage(QStringLiteral("本机信号列表文件无效：%1").arg(error.errorString()), 8'000);
        return;
    }
    const QJsonObject root = document.object();
    for (const QJsonValue &value : root.value(QStringLiteral("dismissed")).toArray()) {
        const QJsonObject signal = value.toObject();
        const QString symbol = signal.value(QStringLiteral("symbol")).toString();
        if (!symbol.isEmpty()) dismissedSignals_.insert(symbol, signal);
    }
    QSettings settings;
    for (const QJsonValue &value : root.value(QStringLiteral("signals")).toArray()) {
        const QJsonObject signal = value.toObject();
        const QString symbol = signal.value(QStringLiteral("symbol")).toString();
        if (symbol.isEmpty()) continue;
        const QString eventKey = signalEventKey(signal);
        const bool alreadyRead = settings.value(QStringLiteral("read_event/%1").arg(symbol)).toString() == eventKey;
        updateSignalRow(signal, signalDescription(signal), eventKey, alreadyRead);
        seenSignals_.insert(eventKey);
    }
    listTabs_->setTabText(0, QStringLiteral("信号列表 (%1)").arg(signalTable_->rowCount()));
}

void MonitorWindow::saveSignalList()
{
    QJsonArray signals;
    for (int row = 0; row < signalTable_->rowCount(); ++row) {
        const QTableWidgetItem *item = signalTable_->item(row, 0);
        if (!item) continue;
        const QString symbol = item->data(Qt::UserRole).toString();
        if (latestSignals_.contains(symbol)) signals.append(latestSignals_.value(symbol));
    }
    QJsonArray dismissed;
    for (auto it = dismissedSignals_.cbegin(); it != dismissedSignals_.cend(); ++it) dismissed.append(it.value());
    const QJsonObject root{{QStringLiteral("version"), 1},
                           {QStringLiteral("signals"), signals},
                           {QStringLiteral("dismissed"), dismissed}};
    const QString path = signalCachePath();
    if (!QDir().mkpath(QFileInfo(path).absolutePath())) {
        statusBar()->showMessage(QStringLiteral("无法创建本机信号列表目录：%1").arg(QFileInfo(path).absolutePath()), 8'000);
        return;
    }
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)
        || file.write(QJsonDocument(root).toJson(QJsonDocument::Indented)) < 0
        || !file.commit()) {
        statusBar()->showMessage(QStringLiteral("无法保存本机信号列表：%1").arg(file.errorString()), 8'000);
    }
}

void MonitorWindow::updateSummary(const QJsonObject &object)
{
    const QString symbol = object.value(QStringLiteral("s")).toString();
    if (symbol.isEmpty()) return;
    snapshots_.insert(symbol, object);
    if (replay_) {
        const qint64 replayMs = exchangeTimeToEpochMs(object.value(QStringLiteral("orig_time")).toInteger());
        if (replayMs > 0) {
            const qint64 differenceMs = QDateTime::currentMSecsSinceEpoch() - replayMs;
            replayBanner_->setText(QStringLiteral("REPLAY · 回放 %1 · 实时差 %2 秒 · 交易按钮仍连接真实 QMT")
                                       .arg(QDateTime::fromMSecsSinceEpoch(replayMs).toString(QStringLiteral("yyyy-MM-dd HH:mm:ss.zzz")))
                                       .arg(differenceMs / 1000.0, 0, 'f', 1));
        }
    }
    int row = findRow(symbol);
    if (row < 0) {
        row = fixedInsertRow(symbol);
        table_->insertRow(row);
    }
    populateQuoteColumns(table_, row, symbol, object);
    symbolItems_.insert(symbol, table_->item(row, 0));
    if (!table_->item(row, 6)) table_->setItem(row, 6, numberItem(QStringLiteral("—"), 0));
    const qint64 publishNs = object.value(QStringLiteral("publish_wall_ns")).toInteger();
    table_->setItem(row, 7, new QTableWidgetItem(QDateTime::fromMSecsSinceEpoch(publishNs / 1'000'000).toString(QStringLiteral("HH:mm:ss.zzz"))));
    refreshSignalQuote(symbol);
}

void MonitorWindow::processSignal(const QJsonObject &object)
{
    const quint64 sequence = static_cast<quint64>(object.value(QStringLiteral("signal_seq")).toInteger());
    const QString symbol = object.value(QStringLiteral("symbol")).toString();
    if (symbol.isEmpty()) return;
    const QString eventKey = signalEventKey(object);
    if (seenSignals_.contains(eventKey)) return;
    seenSignals_.insert(eventKey);
    const bool isNewer = (!latestSignals_.contains(symbol)
                          || compareSignalRecency(object, latestSignals_.value(symbol)) > 0)
                      && (!dismissedSignals_.contains(symbol)
                          || compareSignalRecency(object, dismissedSignals_.value(symbol)) > 0);
    QSettings settings;
    const bool alreadyRead = settings.value(QStringLiteral("read_event/%1").arg(symbol)).toString() == eventKey;
    const QString signalText = signalDescription(object);
    if (isNewer) {
        const int row = findRow(symbol);
        if (row >= 0) {
        auto *signal = new NumericTableItem(
            (alreadyRead ? QStringLiteral("✓ 已读 · ") : QString())
                + signalText);
            signal->setData(Qt::UserRole + 1, static_cast<qint64>(sequence));
            signal->setData(Qt::UserRole + 2, eventKey);
            signal->setBackground(QColor(QStringLiteral("#ffb3be")));
            table_->setItem(row, 6, signal);
        }
        updateSignalRow(object, signalText, eventKey, alreadyRead);
        listTabs_->setTabText(0, QStringLiteral("信号列表 (%1)").arg(signalTable_->rowCount()));
        saveSignalList();
    }
    settings.setValue(QStringLiteral("last_signal_seq"), static_cast<qulonglong>(sequence));
    if (!isNewer || !synchronized_ || object.value(QStringLiteral("backfill")).toBool()) return;
    if (soundEnabled_) playAlertSound(alertSoundPreset_, alertSoundRepeat_);
    if (popupEnabled_) {
        tray_->showMessage(QStringLiteral("ETF 盘中拉升信号"),
                           QStringLiteral("%1 · %2").arg(symbol, signalText),
                           QSystemTrayIcon::Warning, 15'000);
        show();
        raise();
    }
}

void MonitorWindow::playAlertSound(const QString &preset, int repeatCount)
{
    soundTimer_.stop();
    soundRemaining_ = qBound(1, repeatCount, 3);
    if (audioSink_) audioSink_->stop();
    soundPreset_ = preset;
    playNextAlertSound();
}

void MonitorWindow::playNextAlertSound()
{
    if (soundRemaining_ <= 0) return;
    --soundRemaining_;

    soundDurationMs_ = 180;
    bool started = false;
    const QAudioDevice device = QMediaDevices::defaultAudioOutput();
    if (!device.isNull()) {
        const QAudioFormat format = device.preferredFormat();
        audioData_ = makeAlertSound(format, soundPreset_, &soundDurationMs_);
        if (!audioData_.isEmpty()) {
            if (!audioSink_) audioSink_ = new QAudioSink(device, format, this);
            if (!audioBuffer_) audioBuffer_ = new QBuffer(this);
            audioSink_->stop();
            audioBuffer_->close();
            audioBuffer_->setData(audioData_);
            if (audioBuffer_->open(QIODevice::ReadOnly)) {
                audioSink_->start(audioBuffer_);
                started = true;
            }
        }
    }
    if (!started) QApplication::beep();
    if (soundRemaining_ > 0) soundTimer_.start(soundDurationMs_ + 100);
}

void MonitorWindow::openDetail(const QString &symbol, const QString &name)
{
    if (const QPointer<DetailDialog> dialog = details_.value(symbol); dialog) {
        dialog->show();
        dialog->raise();
        return;
    }
    details_.remove(symbol);
    if (details_.size() >= 4) {
        QMessageBox::warning(this, QStringLiteral("详情页上限"), QStringLiteral("每台客户端最多同时打开4个详情页。"));
        return;
    }
    markSignalRead(symbol);
    auto *dialog = new DetailDialog(symbol, name, profiles_, replay_, tradingEnabled_, this);
    details_.insert(symbol, dialog);
    dialog->setAConnected(detail_.state() == QAbstractSocket::ConnectedState);
    connect(dialog, &DetailDialog::detailSubscriptionRequested, this, [this](const QString &requestedSymbol, bool subscribe) {
        if (detail_.state() != QAbstractSocket::ConnectedState) return;
        detail_.sendTextMessage(QString::fromUtf8(QJsonDocument(QJsonObject{{"op", subscribe ? "subscribe" : "unsubscribe"},
                                                                            {"symbol", requestedSymbol}}).toJson(QJsonDocument::Compact)));
    });
    connect(dialog, &DetailDialog::closed, this, [this, symbol, dialog](const QString &closedSymbol) {
        const auto it = details_.find(closedSymbol);
        if (it != details_.end() && it.value().data() == dialog) details_.erase(it);
        if (details_.isEmpty()) detail_.abort();
    });
    connect(dialog, &QObject::destroyed, this, [this, symbol, dialog](QObject *) {
        const auto it = details_.find(symbol);
        if (it != details_.end() && it.value().data() == dialog) details_.erase(it);
        if (details_.isEmpty()) detail_.abort();
    });
    if (detail_.state() == QAbstractSocket::ConnectedState) {
        detail_.sendTextMessage(QString::fromUtf8(QJsonDocument(QJsonObject{{"op", "subscribe"}, {"symbol", symbol}}).toJson(QJsonDocument::Compact)));
    } else connectDetail();
    dialog->show();
}

} // namespace premium
