#include "console/ConsoleWindow.h"

#include <QApplication>
#include <QCheckBox>
#include <QCloseEvent>
#include <QComboBox>
#include <QCoreApplication>
#include <QDateEdit>
#include <QDesktopServices>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFileDialog>
#include <QFormLayout>
#include <QFutureWatcher>
#include <QGridLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMenu>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QRegularExpression>
#include <QRegularExpressionValidator>
#include <QSaveFile>
#include <QSettings>
#include <QSystemTrayIcon>
#include <QStyle>
#include <QTabWidget>
#include <QTableWidget>
#include <QHeaderView>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>
#include <QtConcurrent/QtConcurrentRun>

#include <algorithm>
#include <functional>

namespace premium {

namespace {

QString ppmText(const QJsonObject &object, const QString &key)
{
    if (!object.contains(key) || object.value(key).isNull()) return QStringLiteral("—");
    return QStringLiteral("%1%").arg(object.value(key).toInteger() / 10'000.0, 0, 'f', 3);
}

QString priceText(const QJsonObject &object, const QString &key)
{
    if (!object.contains(key) || object.value(key).toInteger() <= 0) return QStringLiteral("旧记录未保存");
    return QString::number(object.value(key).toInteger() / 1'000'000.0, 'f', 3);
}

QString modelText(const QString &model)
{
    if (model.isEmpty()) return QStringLiteral("—");
    QStringList names;
    for (const QString &part : model.split(u'+', Qt::SkipEmptyParts)) {
        if (part == QStringLiteral("premium")) names.append(QStringLiteral("溢价率"));
        else if (part == QStringLiteral("pull")) names.append(QStringLiteral("盘口拉涨"));
        else if (part == QStringLiteral("radar")) names.append(QStringLiteral("快速拉涨雷达"));
        else names.append(part);
    }
    return names.join(QStringLiteral(" + "));
}

QJsonObject signalRecord(QTableWidget *table, int row)
{
    if (!table || row < 0 || row >= table->rowCount() || !table->item(row, 0)) return {};
    return QJsonDocument::fromJson(table->item(row, 0)->data(Qt::UserRole).toByteArray()).object();
}

QByteArray csvCell(QString value)
{
    value.replace(u'"', QStringLiteral("\"\""));
    return QByteArrayLiteral("\"") + value.toUtf8() + QByteArrayLiteral("\"");
}

} // namespace

ConsoleWindow::ConsoleWindow(QString projectRoot, bool autoStart, QWidget *parent)
    : QMainWindow(parent), root_(QDir(std::move(projectRoot)).absolutePath()), attachOnly_(!autoStart)
{
    buildUi();
    loadWatchlistEditor();
    loadHotlistEditor();
    auto bind = [this](QProcess &process, const QString &name) {
        connect(&process, &QProcess::readyReadStandardOutput, this, [&process, this, name] { appendLog(name, process.readAllStandardOutput()); });
        connect(&process, &QProcess::readyReadStandardError, this, [&process, this, name] { appendLog(name, process.readAllStandardError()); });
        connect(&process, &QProcess::stateChanged, this, [this] { updateState(); });
        connect(&process, &QProcess::finished, this, [this, name](int code, QProcess::ExitStatus status) { handleFinished(name, code, status); });
    };
    bind(core_, QStringLiteral("A-core"));
    bind(adapter_, QStringLiteral("TGW"));
    connect(&replayProcess_, &QProcess::readyReadStandardOutput, this, [this] { appendLog(QStringLiteral("REPLAY"), replayProcess_.readAllStandardOutput()); });
    connect(&replayProcess_, &QProcess::readyReadStandardError, this, [this] { appendLog(QStringLiteral("REPLAY"), replayProcess_.readAllStandardError()); });
    connect(&replayProcess_, &QProcess::finished, this, [this](int code) {
        appendLog(QStringLiteral("REPLAY"), QStringLiteral("回放完成 code=%1\n").arg(code).toUtf8());
    });
    connect(&metrics_, &QWebSocket::textMessageReceived, this, &ConsoleWindow::handleMetricsMessage);
    connect(&metrics_, &QWebSocket::disconnected, this, [this] {
        metricsState_->setText(QStringLiteral("8421 状态流未连接"));
        QTimer::singleShot(2000, this, &ConsoleWindow::connectMetrics);
    });
    connect(&metrics_, &QWebSocket::connected, this, [this] {
        metrics_.sendTextMessage(QStringLiteral("{\"op\":\"status\"}"));
        sendWatchlistToCore();
        sendHotlistToCore();
    });
    signalSoundTimer_.setInterval(350);
    connect(&signalSoundTimer_, &QTimer::timeout, this, [this] {
        if (signalSoundRemaining_ <= 0) {
            signalSoundTimer_.stop();
            return;
        }
        QApplication::beep();
        --signalSoundRemaining_;
        if (signalSoundRemaining_ <= 0) signalSoundTimer_.stop();
    });
    QTimer::singleShot(0, this, &ConsoleWindow::loadSignalHistory);
    QTimer::singleShot(0, this, &ConsoleWindow::connectMetrics);
    if (autoStart) QTimer::singleShot(0, this, &ConsoleWindow::startServices);
}

ConsoleWindow::~ConsoleWindow() { stopServices(); }

void ConsoleWindow::buildUi()
{
    setWindowTitle(QStringLiteral("ETF 溢价率监控 · 服务端 A 控制台%1")
                       .arg(attachOnly_ ? QStringLiteral(" · 旁路监控") : QString()));
    resize(1180, 860);
    auto *central = new QWidget(this);
    central->setObjectName(QStringLiteral("appRoot"));
    auto *layout = new QVBoxLayout(central);
    layout->setContentsMargins(20, 18, 20, 18);
    layout->setSpacing(14);
    auto *statusBox = new QGroupBox(QStringLiteral("进程与生产状态"), central);
    auto *grid = new QGridLayout(statusBox);
    grid->addWidget(new QLabel(QStringLiteral("A-core")), 0, 0);
    coreState_ = new QLabel(QStringLiteral("STOPPED"));
    grid->addWidget(coreState_, 0, 1);
    grid->addWidget(new QLabel(QStringLiteral("tgw-adapter")), 1, 0);
    adapterState_ = new QLabel(QStringLiteral("STOPPED"));
    grid->addWidget(adapterState_, 1, 1);
    grid->addWidget(new QLabel(QStringLiteral("监管器")), 2, 0);
    restartState_ = new QLabel(QStringLiteral("READY · 5分钟最多自动重启3次"));
    grid->addWidget(restartState_, 2, 1);
    grid->addWidget(new QLabel(QStringLiteral("工程目录")), 3, 0);
    auto *rootLabel = new QLabel(root_);
    rootLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    grid->addWidget(rootLabel, 3, 1);
    grid->addWidget(new QLabel(QStringLiteral("核心指标")), 4, 0);
    metricsState_ = new QLabel(QStringLiteral("等待 8421 状态流"));
    grid->addWidget(metricsState_, 4, 1);
    layout->addWidget(statusBox);

    auto *buttons = new QHBoxLayout;
    auto *start = new QPushButton(QStringLiteral("启动行情服务"));
    start->setObjectName(QStringLiteral("primaryButton"));
    auto *stop = new QPushButton(QStringLiteral("停止"));
    stop->setObjectName(QStringLiteral("dangerButton"));
    auto *restart = new QPushButton(QStringLiteral("重启"));
    auto *clear = new QPushButton(QStringLiteral("清空显示日志"));
    auto *force = new QPushButton(QStringLiteral("盘外人工启动真实行情"));
    auto *snapshot = new QPushButton(QStringLiteral("查看最近原始帧"));
    auto *validate = new QPushButton(QStringLiteral("校验配置"));
    auto *replay = new QPushButton(QStringLiteral("历史回放…"));
    connect(start, &QPushButton::clicked, this, &ConsoleWindow::startServices);
    connect(stop, &QPushButton::clicked, this, &ConsoleWindow::stopServices);
    connect(restart, &QPushButton::clicked, this, &ConsoleWindow::restartServices);
    connect(clear, &QPushButton::clicked, this, [this] { log_->clear(); });
    connect(force, &QPushButton::clicked, this, [this] {
        forceQuotesRequested_ = true;
        replayRequested_ = false;
        restartServices();
    });
    connect(snapshot, &QPushButton::clicked, this, [this] {
        if (metrics_.state() == QAbstractSocket::ConnectedState) {
            metrics_.sendTextMessage(QStringLiteral("{\"op\":\"raw_snapshot\"}"));
        } else {
            appendLog(QStringLiteral("RAW"), QByteArrayLiteral("8421 未连接，无法请求原始帧\n"));
        }
    });
    connect(validate, &QPushButton::clicked, this, &ConsoleWindow::validateConfiguration);
    connect(replay, &QPushButton::clicked, this, &ConsoleWindow::startReplay);
    if (attachOnly_) {
        const QString hint = QStringLiteral("旁路监控模式不允许启动、停止或重启生产子进程");
        for (QPushButton *button : {start, stop, restart, force, replay}) {
            button->setEnabled(false);
            button->setToolTip(hint);
        }
        coreState_->setText(QStringLiteral("EXTERNAL"));
        adapterState_->setText(QStringLiteral("EXTERNAL"));
        restartState_->setText(QStringLiteral("旁路监控 · 不接管生产进程"));
    }
    buttons->addWidget(start);
    buttons->addWidget(stop);
    buttons->addWidget(restart);
    buttons->addWidget(force);
    buttons->addWidget(validate);
    buttons->addWidget(snapshot);
    buttons->addWidget(replay);
    buttons->addStretch();
    buttons->addWidget(clear);
    layout->addLayout(buttons);

    workspaceTabs_ = new QTabWidget(central);
    workspaceTabs_->setObjectName(QStringLiteral("workspaceTabs"));

    const auto configureSignalTable = [](QTableWidget *table) {
        table->setColumnCount(12);
        table->setHorizontalHeaderLabels({QStringLiteral("触发时间"), QStringLiteral("标的"), QStringLiteral("名称"),
                                          QStringLiteral("模型"), QStringLiteral("可卖溢价"), QStringLiteral("溢价30秒"),
                                          QStringLiteral("溢价5分钟"), QStringLiteral("买一150秒"), QStringLiteral("买一300秒"),
                                          QStringLiteral("事件"), QStringLiteral("来源"), QStringLiteral("触发依据")});
        table->setSelectionBehavior(QAbstractItemView::SelectRows);
        table->setSelectionMode(QAbstractItemView::SingleSelection);
        table->setEditTriggers(QAbstractItemView::NoEditTriggers);
        table->setAlternatingRowColors(true);
        table->verticalHeader()->setVisible(false);
        table->verticalHeader()->setDefaultSectionSize(32);
        table->horizontalHeader()->setSectionResizeMode(QHeaderView::ResizeToContents);
        table->horizontalHeader()->setSectionResizeMode(11, QHeaderView::Stretch);
    };

    auto *signalPage = new QWidget(workspaceTabs_);
    auto *signalLayout = new QVBoxLayout(signalPage);
    auto *signalControls = new QHBoxLayout;
    signalStatus_ = new QLabel(QStringLiteral("等待 A-core 信号流"));
    signalStatus_->setObjectName(QStringLiteral("signalStatus"));
    signalStatus_->setStyleSheet(QStringLiteral("color:#526074;font-weight:700"));
    const QString consoleSettingsPath = QDir(root_).filePath(QStringLiteral("config/console-settings.ini"));
    QSettings consoleSettings(consoleSettingsPath, QSettings::IniFormat);
    soundEnabled_ = new QCheckBox(QStringLiteral("实时信号响铃"));
    soundEnabled_->setObjectName(QStringLiteral("consoleSoundEnabled"));
    soundEnabled_->setChecked(consoleSettings.value(QStringLiteral("alerts/sound"), true).toBool());
    popupEnabled_ = new QCheckBox(QStringLiteral("系统通知"));
    popupEnabled_->setObjectName(QStringLiteral("consolePopupEnabled"));
    popupEnabled_->setChecked(consoleSettings.value(QStringLiteral("alerts/popup"), true).toBool());
    auto *viewSignal = new QPushButton(QStringLiteral("查看选中详情"));
    auto *clearSignals = new QPushButton(QStringLiteral("清空实时列表"));
    signalControls->addWidget(signalStatus_, 1);
    signalControls->addWidget(soundEnabled_);
    signalControls->addWidget(popupEnabled_);
    signalControls->addWidget(viewSignal);
    signalControls->addWidget(clearSignals);
    signalLayout->addLayout(signalControls);
    signalTable_ = new QTableWidget(0, 12, signalPage);
    signalTable_->setObjectName(QStringLiteral("signalTable"));
    configureSignalTable(signalTable_);
    signalLayout->addWidget(signalTable_, 1);
    workspaceTabs_->addTab(signalPage, QStringLiteral("实时拉升告警"));
    connect(soundEnabled_, &QCheckBox::toggled, this, [consoleSettingsPath](bool enabled) {
        QSettings(consoleSettingsPath, QSettings::IniFormat).setValue(QStringLiteral("alerts/sound"), enabled);
    });
    connect(popupEnabled_, &QCheckBox::toggled, this, [consoleSettingsPath](bool enabled) {
        QSettings(consoleSettingsPath, QSettings::IniFormat).setValue(QStringLiteral("alerts/popup"), enabled);
    });
    connect(viewSignal, &QPushButton::clicked, this, [this] {
        showSignalDetails(signalTable_, signalTable_->currentRow());
    });
    connect(clearSignals, &QPushButton::clicked, this, [this] {
        signalTable_->setRowCount(0);
        liveSignalCount_ = 0;
        workspaceTabs_->setTabText(0, QStringLiteral("实时拉升告警"));
        signalStatus_->setText(QStringLiteral("实时显示已清空；A-core 磁盘审计记录未删除"));
    });
    connect(signalTable_, &QTableWidget::cellDoubleClicked, this, [this](int row) {
        showSignalDetails(signalTable_, row);
    });

    auto *historyPage = new QWidget(workspaceTabs_);
    auto *historyLayout = new QVBoxLayout(historyPage);
    auto *historyControls = new QHBoxLayout;
    historyFrom_ = new QDateEdit(QDate::currentDate().addDays(-29));
    historyFrom_->setObjectName(QStringLiteral("historyFrom"));
    historyFrom_->setCalendarPopup(true);
    historyFrom_->setDisplayFormat(QStringLiteral("yyyy-MM-dd"));
    historyTo_ = new QDateEdit(QDate::currentDate());
    historyTo_->setObjectName(QStringLiteral("historyTo"));
    historyTo_->setCalendarPopup(true);
    historyTo_->setDisplayFormat(QStringLiteral("yyyy-MM-dd"));
    historySymbol_ = new QLineEdit;
    historySymbol_->setObjectName(QStringLiteral("historySymbol"));
    historySymbol_->setPlaceholderText(QStringLiteral("标的筛选，例如 159866"));
    historyModel_ = new QComboBox;
    historyModel_->setObjectName(QStringLiteral("historyModel"));
    historyModel_->addItem(QStringLiteral("全部模型"), QString());
    historyModel_->addItem(QStringLiteral("溢价率模型"), QStringLiteral("premium"));
    historyModel_->addItem(QStringLiteral("盘口拉涨模型"), QStringLiteral("pull"));
    historyModel_->addItem(QStringLiteral("两模型同时触发"), QStringLiteral("premium+pull"));
    historyModel_->addItem(QStringLiteral("含快速拉涨雷达"), QStringLiteral("contains:radar"));
    auto *refreshHistory = new QPushButton(QStringLiteral("刷新审计记录"));
    refreshHistory->setObjectName(QStringLiteral("historyRefresh"));
    auto *viewHistory = new QPushButton(QStringLiteral("查看选中详情"));
    auto *exportHistory = new QPushButton(QStringLiteral("导出筛选结果…"));
    auto *openHistoryDirectory = new QPushButton(QStringLiteral("打开信号目录"));
    historyControls->addWidget(new QLabel(QStringLiteral("日期")));
    historyControls->addWidget(historyFrom_);
    historyControls->addWidget(new QLabel(QStringLiteral("至")));
    historyControls->addWidget(historyTo_);
    historyControls->addWidget(historySymbol_);
    historyControls->addWidget(historyModel_);
    historyControls->addWidget(refreshHistory);
    historyControls->addWidget(viewHistory);
    historyControls->addWidget(exportHistory);
    historyControls->addWidget(openHistoryDirectory);
    historyLayout->addLayout(historyControls);
    historyStatus_ = new QLabel(QStringLiteral("等待读取 data/signals-YYYYMMDD.jsonl"));
    historyStatus_->setObjectName(QStringLiteral("historyStatus"));
    historyLayout->addWidget(historyStatus_);
    historyTable_ = new QTableWidget(0, 12, historyPage);
    historyTable_->setObjectName(QStringLiteral("signalHistoryTable"));
    configureSignalTable(historyTable_);
    historyLayout->addWidget(historyTable_, 1);
    workspaceTabs_->addTab(historyPage, QStringLiteral("历史信号审计"));
    historyWatcher_ = new QFutureWatcher<QList<QJsonObject>>(this);
    connect(historyWatcher_, &QFutureWatcher<QList<QJsonObject>>::finished, this, [this] {
        const QList<QJsonObject> records = historyWatcher_->result();
        historyTable_->setUpdatesEnabled(false);
        historyTable_->setRowCount(0);
        constexpr int VisibleLimit = 50'000;
        const int shown = std::min<int>(records.size(), VisibleLimit);
        for (int index = 0; index < shown; ++index) appendSignalRow(historyTable_, records.at(index), false);
        historyTable_->setUpdatesEnabled(true);
        historyTable_->viewport()->update();
        historyStatus_->setText(records.size() > VisibleLimit
            ? QStringLiteral("匹配 %1 条；为保证界面流畅，仅显示最新 %2 条。原始审计文件保持完整。")
                  .arg(records.size()).arg(VisibleLimit)
            : QStringLiteral("已载入 %1 条；每行保留原始文件名、行号与完整 JSON。") .arg(records.size()));
    });
    connect(refreshHistory, &QPushButton::clicked, this, &ConsoleWindow::loadSignalHistory);
    connect(historySymbol_, &QLineEdit::returnPressed, this, &ConsoleWindow::loadSignalHistory);
    connect(viewHistory, &QPushButton::clicked, this, [this] {
        showSignalDetails(historyTable_, historyTable_->currentRow());
    });
    connect(exportHistory, &QPushButton::clicked, this, &ConsoleWindow::exportSignalHistory);
    connect(openHistoryDirectory, &QPushButton::clicked, this, [this] {
        QDesktopServices::openUrl(QUrl::fromLocalFile(dataDirectory()));
    });
    connect(historyTable_, &QTableWidget::cellDoubleClicked, this, [this](int row) {
        showSignalDetails(historyTable_, row);
    });

    auto *watchPage = new QWidget(workspaceTabs_);
    auto *watchPageLayout = new QVBoxLayout(watchPage);
    auto *watchBox = new QGroupBox(QStringLiteral("观察标的管理 · 同时支持上海/深圳 · 保存后立即向 TGW 增订或退订"), watchPage);
    auto *watchLayout = new QVBoxLayout(watchBox);
    auto *watchControls = new QHBoxLayout;
    watchControls->addWidget(new QLabel(QStringLiteral("证券代码")));
    watchCode_ = new QLineEdit;
    watchCode_->setPlaceholderText(QStringLiteral("6位代码，例如 513100"));
    watchCode_->setMaxLength(6);
    watchCode_->setValidator(new QRegularExpressionValidator(QRegularExpression(QStringLiteral("[0-9]{0,6}")), watchCode_));
    watchMarket_ = new QComboBox;
    watchMarket_->addItem(QStringLiteral("上海 SH"), QStringLiteral("SH"));
    watchMarket_->addItem(QStringLiteral("深圳 SZ"), QStringLiteral("SZ"));
    auto *addWatch = new QPushButton(QStringLiteral("添加"));
    auto *removeWatch = new QPushButton(QStringLiteral("移除选中"));
    watchControls->addWidget(watchCode_);
    watchControls->addWidget(watchMarket_);
    watchControls->addWidget(addWatch);
    watchControls->addWidget(removeWatch);
    watchControls->addStretch();
    connect(addWatch, &QPushButton::clicked, this, &ConsoleWindow::addWatchSymbol);
    connect(watchCode_, &QLineEdit::returnPressed, this, &ConsoleWindow::addWatchSymbol);
    connect(removeWatch, &QPushButton::clicked, this, &ConsoleWindow::removeSelectedWatchSymbols);
    watchLayout->addLayout(watchControls);
    watchTable_ = new QTableWidget(0, 3);
    watchTable_->setObjectName(QStringLiteral("monitorTable"));
    watchTable_->setHorizontalHeaderLabels({QStringLiteral("标的"), QStringLiteral("市场"), QStringLiteral("名称")});
    watchTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    watchTable_->setSelectionMode(QAbstractItemView::ExtendedSelection);
    watchTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    watchTable_->horizontalHeader()->setSectionResizeMode(QHeaderView::ResizeToContents);
    watchTable_->horizontalHeader()->setStretchLastSection(true);
    watchTable_->verticalHeader()->setVisible(false);
    watchTable_->verticalHeader()->setDefaultSectionSize(32);
    watchTable_->setAlternatingRowColors(true);
    watchLayout->addWidget(watchTable_);
    watchPageLayout->addWidget(watchBox);
    workspaceTabs_->addTab(watchPage, QStringLiteral("观察标的管理"));

    auto *hotPage = new QWidget(workspaceTabs_);
    auto *hotPageLayout = new QVBoxLayout(hotPage);
    auto *hotBox = new QGroupBox(QStringLiteral("额外L1行情维护标的 · 只预热内存快照与19195转发 · 不计算信号、不落盘"), hotPage);
    auto *hotLayout = new QVBoxLayout(hotBox);
    auto *hotControls = new QHBoxLayout;
    hotControls->addWidget(new QLabel(QStringLiteral("证券代码")));
    hotCode_ = new QLineEdit;
    hotCode_->setObjectName(QStringLiteral("hotCode"));
    hotCode_->setPlaceholderText(QStringLiteral("SH/SZ 6位；HK 5位，例如 02800"));
    hotCode_->setMaxLength(6);
    hotCode_->setValidator(new QRegularExpressionValidator(QRegularExpression(QStringLiteral("[0-9]{0,6}")), hotCode_));
    hotMarket_ = new QComboBox;
    hotMarket_->setObjectName(QStringLiteral("hotMarket"));
    hotMarket_->addItem(QStringLiteral("上海 SH"), QStringLiteral("SH"));
    hotMarket_->addItem(QStringLiteral("深圳 SZ"), QStringLiteral("SZ"));
    hotMarket_->addItem(QStringLiteral("港股通 HK（固定深股通路由）"), QStringLiteral("HK"));
    auto *addHot = new QPushButton(QStringLiteral("添加"));
    auto *removeHot = new QPushButton(QStringLiteral("移除选中"));
    hotControls->addWidget(hotCode_);
    hotControls->addWidget(hotMarket_);
    hotControls->addWidget(addHot);
    hotControls->addWidget(removeHot);
    hotControls->addStretch();
    hotLayout->addLayout(hotControls);
    hotTable_ = new QTableWidget(0, 4);
    hotTable_->setObjectName(QStringLiteral("hotL1Table"));
    hotTable_->setHorizontalHeaderLabels({QStringLiteral("标的"), QStringLiteral("业务市场 / 路由"),
                                          QStringLiteral("角色"), QStringLiteral("名称")});
    hotTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    hotTable_->setSelectionMode(QAbstractItemView::ExtendedSelection);
    hotTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    hotTable_->horizontalHeader()->setSectionResizeMode(QHeaderView::ResizeToContents);
    hotTable_->horizontalHeader()->setStretchLastSection(true);
    hotTable_->verticalHeader()->setVisible(false);
    hotTable_->verticalHeader()->setDefaultSectionSize(32);
    hotTable_->setAlternatingRowColors(true);
    hotLayout->addWidget(hotTable_);
    hotPageLayout->addWidget(hotBox);
    workspaceTabs_->addTab(hotPage, QStringLiteral("额外L1行情维护标的"));
    connect(addHot, &QPushButton::clicked, this, &ConsoleWindow::addHotSymbol);
    connect(hotCode_, &QLineEdit::returnPressed, this, &ConsoleWindow::addHotSymbol);
    connect(removeHot, &QPushButton::clicked, this, &ConsoleWindow::removeSelectedHotSymbols);
    connect(hotMarket_, &QComboBox::currentIndexChanged, this, [this] {
        const bool hk = hotMarket_->currentData().toString() == QStringLiteral("HK");
        hotCode_->setMaxLength(hk ? 5 : 6);
        hotCode_->setPlaceholderText(hk ? QStringLiteral("5位港股代码，例如 02800")
                                        : QStringLiteral("6位沪深代码，例如 513100"));
        hotCode_->clear();
    });

    log_ = new QPlainTextEdit(workspaceTabs_);
    log_->setObjectName(QStringLiteral("operationLog"));
    log_->setReadOnly(true);
    log_->setMaximumBlockCount(10'000);
    workspaceTabs_->addTab(log_, QStringLiteral("运行日志"));
    layout->addWidget(workspaceTabs_, 1);
    setCentralWidget(central);
    setStyleSheet(QStringLiteral(R"(
        QMainWindow, QWidget#appRoot { background:#f5f7fa; color:#1f2937; }
        QLabel { background:transparent; color:#253044; }
        QGroupBox { color:#253044; background:white; border:1px solid #dce3ec;
                    border-radius:10px; margin-top:10px; padding:14px 12px 10px 12px;
                    font-weight:700; }
        QGroupBox::title { subcontrol-origin:margin; left:14px; padding:0 6px; }
        QLineEdit, QComboBox, QDateEdit { color:#172033; background:white; border:1px solid #cbd5e1;
                    border-radius:7px; padding:8px; min-height:20px; }
        QLineEdit:focus, QComboBox:focus, QDateEdit:focus { border:1px solid #2f6feb; }
        QPushButton { color:#283548; background:#f8fafc; border:1px solid #cbd5e1;
                      border-radius:7px; padding:8px 14px; font-weight:600; }
        QPushButton:hover { background:#eef3f9; }
        QPushButton#primaryButton { color:white; background:#2f6feb; border-color:#2f6feb; }
        QPushButton#primaryButton:hover { background:#245dcc; }
        QPushButton#dangerButton { color:white; background:#c53b45; border-color:#c53b45; }
        QPushButton#dangerButton:hover { background:#aa2733; }
        QTabWidget::pane { background:white; border:1px solid #dce3ec; border-radius:9px; top:-1px; }
        QTabBar::tab { color:#526074; background:#eaf0f7; border:1px solid #dce3ec;
                       padding:9px 18px; min-width:120px; font-weight:700; }
        QTabBar::tab:selected { color:white; background:#2f6feb; border-color:#2f6feb; }
        QTableWidget#monitorTable, QTableWidget#hotL1Table, QTableWidget#signalTable, QTableWidget#signalHistoryTable {
                      color:#172033; background:white; alternate-background-color:#f8fafc;
                      border:1px solid #dce3ec; border-radius:9px; gridline-color:#e2e8f0; }
        QTableWidget#monitorTable::item, QTableWidget#hotL1Table::item, QTableWidget#signalTable::item,
        QTableWidget#signalHistoryTable::item { padding:6px; }
        QTableWidget#monitorTable::item:selected, QTableWidget#hotL1Table::item:selected, QTableWidget#signalTable::item:selected,
        QTableWidget#signalHistoryTable::item:selected { background:#dce9ff; color:#172033; }
        QHeaderView::section { background:#eef2f7; color:#526074; border:none;
                      border-right:1px solid #dce3ec; border-bottom:1px solid #dce3ec;
                      padding:8px; font-weight:700; }
        QPlainTextEdit#operationLog { color:#253044; background:#ffffff; border:1px solid #dce3ec;
                      border-radius:9px; padding:8px; font-family:Menlo,Consolas,monospace; }
    )"));

    tray_ = new QSystemTrayIcon(QApplication::style()->standardIcon(QStyle::SP_ComputerIcon), this);
    auto *menu = new QMenu(this);
    menu->addAction(QStringLiteral("显示控制台"), this, [this] { show(); raise(); activateWindow(); });
    menu->addAction(QStringLiteral("启动"), this, &ConsoleWindow::startServices);
    menu->addAction(QStringLiteral("停止"), this, &ConsoleWindow::stopServices);
    menu->addSeparator();
    menu->addAction(QStringLiteral("明确退出（停止子进程）"), this, [this] {
        stopServices();
        qApp->quit();
    });
    tray_->setContextMenu(menu);
    tray_->setToolTip(QStringLiteral("ETF 溢价率监控服务端"));
    tray_->show();
    connect(tray_, &QSystemTrayIcon::activated, this, [this](QSystemTrayIcon::ActivationReason reason) {
        if (reason == QSystemTrayIcon::Trigger || reason == QSystemTrayIcon::DoubleClick) { show(); raise(); }
    });
}

void ConsoleWindow::handleMetricsMessage(const QString &message)
{
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(message.toUtf8(), &parseError);
    if (!document.isObject()) {
        appendLog(QStringLiteral("8421"),
                  QStringLiteral("忽略无效 JSON：%1\n").arg(parseError.errorString()).toUtf8());
        return;
    }
    const QJsonObject object = document.object();
    const QString type = object.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("summary")) {
        const QString symbol = object.value(QStringLiteral("s")).toString();
        if (!symbol.isEmpty()) latestSummaries_.insert(symbol, object);
    } else if (type == QStringLiteral("signal")) {
        processSignal(object);
    } else if (type == QStringLiteral("sync_begin")) {
        signalStatus_->setText(QStringLiteral("正在同步 A-core 缓存与最近30分钟信号；补发记录不会响铃"));
        signalStatus_->setStyleSheet(QStringLiteral("color:#2f6feb;font-weight:700"));
    } else if (type == QStringLiteral("sync_complete")) {
        signalStatus_->setText(QStringLiteral("A-core 信号流已同步，等待新的实时触发"));
        signalStatus_->setStyleSheet(QStringLiteral("color:#008f5a;font-weight:700"));
    } else if (type == QStringLiteral("raw_snapshot")) {
        appendLog(QStringLiteral("RAW"), QJsonDocument(object).toJson(QJsonDocument::Indented));
    } else if (type == QStringLiteral("watchlist_ack")) {
        const QJsonArray authoritative = object.value(QStringLiteral("symbols")).toArray();
        if (!authoritative.isEmpty()) {
            watchSymbols_.clear();
            for (const QJsonValue &value : authoritative) watchSymbols_.append(value.toString());
            refreshWatchlistTable();
            refreshHotlistTable();
        }
        if (object.value(QStringLiteral("accepted")).toBool()) {
            appendLog(QStringLiteral("WATCHLIST"),
                      QStringLiteral("A-core 已应用 %1 个观察标的\n")
                          .arg(object.value(QStringLiteral("count")).toInt()).toUtf8());
        } else {
            appendLog(QStringLiteral("WATCHLIST"),
                      QStringLiteral("A-core 拒绝观察列表更新，UI已回滚为服务端权威列表：%1\n")
                          .arg(object.value(QStringLiteral("error")).toString()).toUtf8());
        }
    } else if (type == QStringLiteral("l1_hotlist_ack")) {
        hotSymbols_.clear();
        for (const QJsonValue &value : object.value(QStringLiteral("symbols")).toArray()) {
            hotSymbols_.append(value.toString());
        }
        refreshHotlistTable();
        if (object.value(QStringLiteral("accepted")).toBool()) {
            appendLog(QStringLiteral("L1-HOT"),
                      QStringLiteral("A-core 已原子保存并应用 %1 个额外 L1 热维护标的\n")
                          .arg(object.value(QStringLiteral("count")).toInt()).toUtf8());
        } else {
            appendLog(QStringLiteral("L1-HOT"),
                      QStringLiteral("A-core 拒绝 L1 热维护列表，UI已回滚：%1\n")
                          .arg(object.value(QStringLiteral("error")).toString()).toUtf8());
        }
    } else if (type == QStringLiteral("status")) {
        const double diskGiB = object.value(QStringLiteral("disk_available_bytes")).toInteger() / 1073741824.0;
        metricsState_->setText(QStringLiteral("阶段 %1 · 观察就绪 %2 · L1热维护 %3/%4 · 上游活跃 %5 · SDK队列 %6 · 计算 %7 · 落盘 %8 · 隔离 %9 · 延迟 %10 ms · 磁盘 %11 GiB%12")
                               .arg(object.value(QStringLiteral("phase")).toString())
                               .arg(object.value(QStringLiteral("ready_symbols")).toInt())
                               .arg(object.value(QStringLiteral("l1_hot_ready")).toInt())
                               .arg(object.value(QStringLiteral("l1_hot_symbols")).toInt())
                               .arg(object.value(QStringLiteral("active_upstream_symbols")).toInt())
                               .arg(object.value(QStringLiteral("sdk_queue_depth")).toInt())
                               .arg(QString::fromUtf8(QJsonDocument(object.value(QStringLiteral("worker_queue_depths")).toArray()).toJson(QJsonDocument::Compact)))
                               .arg(object.value(QStringLiteral("persistence_queue_depth")).toInt())
                               .arg(object.value(QStringLiteral("quarantined")).toInteger())
                               .arg(object.value(QStringLiteral("core_latency_ms")).toDouble(), 0, 'f', 2)
                               .arg(diskGiB, 0, 'f', 1)
                               .arg(object.value(QStringLiteral("historical_writes_stopped")).toBool()
                                        ? QStringLiteral(" · 历史写入已停") : QString()));
    }
}

void ConsoleWindow::processSignal(const QJsonObject &signal)
{
    const QString symbol = signal.value(QStringLiteral("symbol")).toString();
    if (symbol.isEmpty()) return;
    const QString eventKey = QStringLiteral("%1|%2|%3")
                                 .arg(signal.value(QStringLiteral("signal_seq")).toInteger())
                                 .arg(signal.value(QStringLiteral("occurred_at")).toString(), symbol);
    if (seenSignals_.contains(eventKey)) return;
    seenSignals_.insert(eventKey);
    appendSignalRow(signalTable_, signal, true);
    ++liveSignalCount_;
    workspaceTabs_->setTabText(0, QStringLiteral("实时拉升告警 · %1").arg(liveSignalCount_));

    const bool backfill = signal.value(QStringLiteral("backfill")).toBool();
    const bool replay = signal.value(QStringLiteral("replay")).toBool();
    const bool realtime = !backfill && !replay;
    const QString name = signal.value(QStringLiteral("name")).toString(watchNames_.value(symbol));
    const QString message = QStringLiteral("%1 %2 · %3 · 可卖溢价 %4 · %5")
                                .arg(symbol, name, modelText(signal.value(QStringLiteral("model")).toString()),
                                     ppmText(signal, QStringLiteral("premium_ppm")),
                                     signal.value(QStringLiteral("reason")).toString());
    signalStatus_->setText((realtime ? QStringLiteral("实时触发：")
                                     : backfill ? QStringLiteral("历史补发：") : QStringLiteral("回放触发：")) + message);
    signalStatus_->setStyleSheet(realtime ? QStringLiteral("color:#d7263d;font-weight:800")
                                          : QStringLiteral("color:#2f6feb;font-weight:700"));
    appendLog(QStringLiteral("SIGNAL"), QJsonDocument(signal).toJson(QJsonDocument::Compact) + '\n');
    if (!realtime) return;
    startSignalSound();
    if (popupEnabled_ && popupEnabled_->isChecked()) {
        tray_->showMessage(QStringLiteral("ETF 拉升信号"), message, QSystemTrayIcon::Warning, 12'000);
    }
}

void ConsoleWindow::appendSignalRow(QTableWidget *table, const QJsonObject &signal, bool prepend)
{
    if (!table) return;
    const int row = prepend ? 0 : table->rowCount();
    table->insertRow(row);
    const QString symbol = signal.value(QStringLiteral("symbol")).toString();
    const QDateTime occurred = QDateTime::fromString(signal.value(QStringLiteral("occurred_at")).toString(), Qt::ISODateWithMs);
    QString source;
    if (signal.contains(QStringLiteral("_audit_file"))) {
        source = QStringLiteral("%1:%2").arg(signal.value(QStringLiteral("_audit_file")).toString())
                                         .arg(signal.value(QStringLiteral("_audit_line")).toInteger());
    } else if (signal.value(QStringLiteral("replay")).toBool()) {
        source = QStringLiteral("回放");
    } else if (signal.value(QStringLiteral("backfill")).toBool()) {
        source = QStringLiteral("30分钟补发");
    } else {
        source = QStringLiteral("实时");
    }
    const QStringList values{
        occurred.isValid() ? occurred.toString(QStringLiteral("MM-dd HH:mm:ss.zzz"))
                           : signal.value(QStringLiteral("occurred_at")).toString(),
        symbol,
        signal.value(QStringLiteral("name")).toString(watchNames_.value(symbol, symbol.left(6))),
        modelText(signal.value(QStringLiteral("model")).toString()),
        ppmText(signal, QStringLiteral("premium_ppm")),
        ppmText(signal, QStringLiteral("rise_30s_ppm")),
        ppmText(signal, QStringLiteral("rise_300s_ppm")),
        ppmText(signal, QStringLiteral("bid_rise_150s_ppm")),
        ppmText(signal, QStringLiteral("bid_rise_300s_ppm")),
        signal.value(QStringLiteral("repeat")).toBool() ? QStringLiteral("重复提醒") : QStringLiteral("首次触发"),
        source,
        signal.value(QStringLiteral("reason")).toString()
    };
    const QColor background = source == QStringLiteral("实时") ? QColor(QStringLiteral("#fff0f2"))
                              : source == QStringLiteral("回放") ? QColor(QStringLiteral("#fff7df"))
                              : QColor();
    for (int column = 0; column < values.size(); ++column) {
        auto *item = new QTableWidgetItem(values.at(column));
        if (background.isValid()) item->setBackground(background);
        if (column == 4 && signal.value(QStringLiteral("premium_ppm")).toInteger() > 0) {
            item->setForeground(QColor(QStringLiteral("#d7263d")));
        }
        table->setItem(row, column, item);
    }
    table->item(row, 0)->setData(Qt::UserRole, QJsonDocument(signal).toJson(QJsonDocument::Compact));
    table->item(row, 0)->setToolTip(QStringLiteral("双击查看完整触发信息与原始 JSON"));
    if (prepend && table->rowCount() > 2'000) table->removeRow(table->rowCount() - 1);
}

void ConsoleWindow::showSignalDetails(QTableWidget *table, int row)
{
    const QJsonObject signal = signalRecord(table, row);
    if (signal.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择信号"), QStringLiteral("请先选择一条信号记录。"));
        return;
    }
    const QString symbol = signal.value(QStringLiteral("symbol")).toString();
    const QJsonObject current = latestSummaries_.value(symbol);
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("%1 拉升信号详情").arg(symbol));
    dialog.resize(780, 690);
    auto *layout = new QVBoxLayout(&dialog);
    auto *title = new QLabel(QStringLiteral("%1  %2\n%3 · %4")
                                 .arg(symbol,
                                      signal.value(QStringLiteral("name")).toString(watchNames_.value(symbol)),
                                      modelText(signal.value(QStringLiteral("model")).toString()),
                                      signal.value(QStringLiteral("reason")).toString()));
    title->setStyleSheet(QStringLiteral("font-size:18px;font-weight:800;color:#b42318"));
    layout->addWidget(title);

    auto *triggerGroup = new QGroupBox(QStringLiteral("触发时刻审计数据"));
    auto *trigger = new QFormLayout(triggerGroup);
    trigger->addRow(QStringLiteral("信号序号"), new QLabel(QString::number(signal.value(QStringLiteral("signal_seq")).toInteger())));
    trigger->addRow(QStringLiteral("触发时间"), new QLabel(signal.value(QStringLiteral("occurred_at")).toString()));
    trigger->addRow(QStringLiteral("模型 / 条件"), new QLabel(modelText(signal.value(QStringLiteral("model")).toString())
                                                        + QStringLiteral(" / ") + signal.value(QStringLiteral("reason")).toString()));
    trigger->addRow(QStringLiteral("可卖溢价率"), new QLabel(ppmText(signal, QStringLiteral("premium_ppm"))));
    trigger->addRow(QStringLiteral("溢价率拉升 30秒 / 5分钟"),
                    new QLabel(ppmText(signal, QStringLiteral("rise_30s_ppm")) + QStringLiteral(" / ")
                               + ppmText(signal, QStringLiteral("rise_300s_ppm"))));
    trigger->addRow(QStringLiteral("买一拉升 150秒 / 300秒"),
                    new QLabel(ppmText(signal, QStringLiteral("bid_rise_150s_ppm")) + QStringLiteral(" / ")
                               + ppmText(signal, QStringLiteral("bid_rise_300s_ppm"))));
    trigger->addRow(QStringLiteral("快速买一 30 / 60 / 90秒"),
                    new QLabel(ppmText(signal, QStringLiteral("bid_rise_30s_ppm")) + QStringLiteral(" / ")
                               + ppmText(signal, QStringLiteral("bid_rise_60s_ppm")) + QStringLiteral(" / ")
                               + ppmText(signal, QStringLiteral("bid_rise_90s_ppm"))));
    trigger->addRow(QStringLiteral("固定动量 3 / 5分钟"),
                    new QLabel(ppmText(signal, QStringLiteral("momentum_3m_ppm")) + QStringLiteral(" / ")
                               + ppmText(signal, QStringLiteral("momentum_5m_ppm"))));
    trigger->addRow(QStringLiteral("局部低点 A3 / A5"),
                    new QLabel(ppmText(signal, QStringLiteral("adaptive_3m_ppm")) + QStringLiteral(" / ")
                               + ppmText(signal, QStringLiteral("adaptive_5m_ppm"))));
    trigger->addRow(QStringLiteral("分钟振幅 / 历史中位数"),
                    new QLabel(ppmText(signal, QStringLiteral("minute_range_ppm")) + QStringLiteral(" / ")
                               + ppmText(signal, QStringLiteral("minute_range_base_ppm"))));
    trigger->addRow(QStringLiteral("触发最新价 / 买一 / IOPV"),
                    new QLabel(priceText(signal, QStringLiteral("last_price_e6")) + QStringLiteral(" / ")
                               + priceText(signal, QStringLiteral("bid1_price_e6")) + QStringLiteral(" / ")
                               + priceText(signal, QStringLiteral("iopv_e6"))));
    trigger->addRow(QStringLiteral("原始行情时间"),
                    new QLabel(signal.contains(QStringLiteral("orig_time"))
                                   ? QString::number(signal.value(QStringLiteral("orig_time")).toInteger())
                                   : QStringLiteral("旧记录未保存")));
    trigger->addRow(QStringLiteral("事件属性"),
                    new QLabel(QStringLiteral("%1 · %2 · %3")
                                   .arg(signal.value(QStringLiteral("repeat")).toBool() ? QStringLiteral("重复提醒") : QStringLiteral("首次触发"),
                                        signal.value(QStringLiteral("replay")).toBool() ? QStringLiteral("回放") : QStringLiteral("生产"),
                                        signal.value(QStringLiteral("backfill")).toBool() ? QStringLiteral("连接补发") : QStringLiteral("实时到达"))));
    if (signal.contains(QStringLiteral("_audit_file"))) {
        trigger->addRow(QStringLiteral("审计位置"),
                        new QLabel(QStringLiteral("%1 第 %2 行")
                                       .arg(signal.value(QStringLiteral("_audit_file")).toString())
                                       .arg(signal.value(QStringLiteral("_audit_line")).toInteger())));
    }
    layout->addWidget(triggerGroup);

    auto *currentGroup = new QGroupBox(QStringLiteral("A-core 当前最新缓存（仅供参考，不替代历史触发值）"));
    auto *currentForm = new QFormLayout(currentGroup);
    if (current.isEmpty()) {
        currentForm->addRow(new QLabel(QStringLiteral("当前尚无该标的 summary 缓存。")));
    } else {
        currentForm->addRow(QStringLiteral("最新价 / 买一 / IOPV"),
                            new QLabel(priceText(current, QStringLiteral("last_price_e6")) + QStringLiteral(" / ")
                                       + priceText(current, QStringLiteral("bid1_price_e6")) + QStringLiteral(" / ")
                                       + priceText(current, QStringLiteral("iopv_e6"))));
        currentForm->addRow(QStringLiteral("当前可卖溢价率"), new QLabel(ppmText(current, QStringLiteral("sell_premium_ppm"))));
        currentForm->addRow(QStringLiteral("IOPV / 映射状态"),
                            new QLabel(QStringLiteral("%1 / %2")
                                           .arg(current.value(QStringLiteral("iopv_static")).toBool()
                                                    ? QStringLiteral("静态") : QStringLiteral("正常"),
                                                current.value(QStringLiteral("mapping_verified")).toBool()
                                                    ? QStringLiteral("已验证") : QStringLiteral("待验证"))));
    }
    layout->addWidget(currentGroup);
    auto *raw = new QPlainTextEdit(QString::fromUtf8(QJsonDocument(signal).toJson(QJsonDocument::Indented)));
    raw->setReadOnly(true);
    raw->setObjectName(QStringLiteral("signalRawJson"));
    layout->addWidget(raw, 1);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Close);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    dialog.exec();
}

QString ConsoleWindow::dataDirectory() const
{
    QString configPath = QDir(root_).filePath(QStringLiteral("config/app.json"));
    if (!QFileInfo::exists(configPath)) configPath = QDir(root_).filePath(QStringLiteral("config/app.example.json"));
    QFile file(configPath);
    const QJsonObject config = QJsonDocument::fromJson(file.open(QIODevice::ReadOnly) ? file.readAll() : QByteArray{}).object();
    const QString configured = config.value(QStringLiteral("data_dir")).toString(QStringLiteral("data"));
    return QDir::isAbsolutePath(configured) ? QDir::cleanPath(configured)
                                            : QDir(root_).absoluteFilePath(configured);
}

void ConsoleWindow::loadSignalHistory()
{
    if (!historyWatcher_ || historyWatcher_->isRunning()) {
        if (historyStatus_) historyStatus_->setText(QStringLiteral("审计记录正在读取，请稍候…"));
        return;
    }
    const QDate from = historyFrom_->date();
    const QDate to = historyTo_->date();
    if (from > to) {
        QMessageBox::warning(this, QStringLiteral("日期无效"), QStringLiteral("开始日期不能晚于结束日期。"));
        return;
    }
    const QString directory = dataDirectory();
    const QString symbolFilter = historySymbol_->text().trimmed().toUpper();
    const QString modelFilter = historyModel_->currentData().toString();
    QStringList files;
    const QDir dataDir(directory);
    for (const QString &name : dataDir.entryList({QStringLiteral("signals-*.jsonl")}, QDir::Files, QDir::Name)) {
        const QDate date = QDate::fromString(name.mid(8, 8), QStringLiteral("yyyyMMdd"));
        if (date.isValid() && date >= from && date <= to) files.append(dataDir.absoluteFilePath(name));
    }
    historyStatus_->setText(QStringLiteral("正在后台读取 %1 个审计文件…").arg(files.size()));
    historyWatcher_->setFuture(QtConcurrent::run([files, symbolFilter, modelFilter] {
        QList<QJsonObject> records;
        for (const QString &path : files) {
            QFile file(path);
            if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) continue;
            qint64 lineNumber = 0;
            while (!file.atEnd()) {
                const QByteArray line = file.readLine();
                ++lineNumber;
                QJsonParseError error;
                QJsonObject object = QJsonDocument::fromJson(line, &error).object();
                if (error.error != QJsonParseError::NoError
                    || object.value(QStringLiteral("type")).toString() != QStringLiteral("signal")) continue;
                const QString symbol = object.value(QStringLiteral("symbol")).toString().toUpper();
                const QString model = object.value(QStringLiteral("model")).toString();
                if (!symbolFilter.isEmpty() && !symbol.contains(symbolFilter)) continue;
                if (modelFilter.startsWith(QStringLiteral("contains:"))) {
                    const QString required = modelFilter.mid(9);
                    if (!model.split(u'+', Qt::SkipEmptyParts).contains(required)) continue;
                } else if (!modelFilter.isEmpty() && model != modelFilter) {
                    continue;
                }
                object.insert(QStringLiteral("_audit_file"), QFileInfo(path).fileName());
                object.insert(QStringLiteral("_audit_line"), lineNumber);
                records.append(object);
            }
        }
        std::sort(records.begin(), records.end(), [](const QJsonObject &left, const QJsonObject &right) {
            const QString leftTime = left.value(QStringLiteral("occurred_at")).toString();
            const QString rightTime = right.value(QStringLiteral("occurred_at")).toString();
            if (leftTime != rightTime) return leftTime > rightTime;
            return left.value(QStringLiteral("signal_seq")).toInteger()
                 > right.value(QStringLiteral("signal_seq")).toInteger();
        });
        return records;
    }));
}

void ConsoleWindow::exportSignalHistory()
{
    if (!historyTable_ || historyTable_->rowCount() == 0) {
        QMessageBox::information(this, QStringLiteral("没有记录"), QStringLiteral("当前筛选结果为空。"));
        return;
    }
    const QString suggested = QDir(root_).filePath(
        QStringLiteral("signal-audit-%1.csv").arg(QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss"))));
    const QString path = QFileDialog::getSaveFileName(this, QStringLiteral("导出信号审计"), suggested,
                                                      QStringLiteral("CSV 文件 (*.csv)"));
    if (path.isEmpty()) return;
    QByteArray output("\xEF\xBB\xBF");
    for (int column = 0; column < historyTable_->columnCount(); ++column) {
        if (column) output.append(',');
        output.append(csvCell(historyTable_->horizontalHeaderItem(column)->text()));
    }
    output.append(QByteArrayLiteral(",\"原始JSON\"\n"));
    for (int row = 0; row < historyTable_->rowCount(); ++row) {
        for (int column = 0; column < historyTable_->columnCount(); ++column) {
            if (column) output.append(',');
            output.append(csvCell(historyTable_->item(row, column) ? historyTable_->item(row, column)->text() : QString()));
        }
        output.append(',');
        output.append(csvCell(QString::fromUtf8(QJsonDocument(signalRecord(historyTable_, row)).toJson(QJsonDocument::Compact))));
        output.append('\n');
    }
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly) || file.write(output) != output.size() || !file.commit()) {
        QMessageBox::critical(this, QStringLiteral("导出失败"), file.errorString());
        return;
    }
    historyStatus_->setText(QStringLiteral("已导出 %1 条到 %2").arg(historyTable_->rowCount()).arg(path));
}

void ConsoleWindow::startSignalSound()
{
    if (!soundEnabled_ || !soundEnabled_->isChecked()) return;
    QApplication::beep();
    signalSoundRemaining_ = 1;
    signalSoundTimer_.start();
}

void ConsoleWindow::loadWatchlistEditor()
{
    watchSymbols_.clear();
    watchNames_.clear();
    QFile namesFile(QDir(root_).filePath(QStringLiteral("config/security_names.tsv")));
    if (namesFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
        while (!namesFile.atEnd()) {
            const QString line = QString::fromUtf8(namesFile.readLine()).trimmed();
            const int tab = line.indexOf(u'\t');
            if (tab > 0) watchNames_.insert(line.left(tab).trimmed().toUpper(), line.mid(tab + 1).trimmed());
        }
    }
    QFile file(QDir(root_).filePath(QStringLiteral("config/watchlist.json")));
    if (!file.open(QIODevice::ReadOnly)) return;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    const QJsonArray values = document.isArray() ? document.array()
                                                 : document.object().value(QStringLiteral("symbols")).toArray();
    for (const QJsonValue &value : values) {
        const QString symbol = value.isString() ? value.toString().trimmed().toUpper()
                                                : value.toObject().value(QStringLiteral("symbol")).toString().trimmed().toUpper();
        if (!watchSymbols_.contains(symbol)) watchSymbols_.append(symbol);
    }
    refreshWatchlistTable();
}

void ConsoleWindow::loadHotlistEditor()
{
    hotSymbols_.clear();
    QFile file(QDir(root_).filePath(QStringLiteral("config/l1_hotlist.json")));
    if (file.open(QIODevice::ReadOnly)) {
        const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
        const QJsonArray values = document.isArray() ? document.array()
                                                     : document.object().value(QStringLiteral("symbols")).toArray();
        for (const QJsonValue &value : values) {
            const QString symbol = value.isString() ? value.toString().trimmed().toUpper()
                                                    : value.toObject().value(QStringLiteral("symbol")).toString().trimmed().toUpper();
            if (!symbol.isEmpty() && !hotSymbols_.contains(symbol)) hotSymbols_.append(symbol);
        }
    }
    refreshHotlistTable();
}

void ConsoleWindow::refreshWatchlistTable()
{
    if (!watchTable_) return;
    watchTable_->setRowCount(0);
    for (const QString &symbol : watchSymbols_) {
        const int row = watchTable_->rowCount();
        watchTable_->insertRow(row);
        watchTable_->setItem(row, 0, new QTableWidgetItem(symbol));
        watchTable_->setItem(row, 1, new QTableWidgetItem(symbol.endsWith(QStringLiteral(".SH"))
                                                            ? QStringLiteral("上海") : QStringLiteral("深圳")));
        watchTable_->setItem(row, 2, new QTableWidgetItem(watchNames_.value(symbol, symbol.left(6))));
    }
    if (auto *box = qobject_cast<QGroupBox *>(watchTable_->parentWidget())) {
        box->setTitle(QStringLiteral("观察标的管理 · 当前 %1 个 · 同时支持上海/深圳 · 保存后立即向 TGW 增订或退订")
                          .arg(watchSymbols_.size()));
    }
}

void ConsoleWindow::refreshHotlistTable()
{
    if (!hotTable_) return;
    hotTable_->setRowCount(0);
    for (const QString &symbol : hotSymbols_) {
        const int row = hotTable_->rowCount();
        hotTable_->insertRow(row);
        QString market;
        if (symbol.endsWith(QStringLiteral(".HK"))) market = QStringLiteral("香港 / 深股通");
        else if (symbol.endsWith(QStringLiteral(".SH"))) market = QStringLiteral("上海 / 普通L1");
        else market = QStringLiteral("深圳 / 普通L1");
        hotTable_->setItem(row, 0, new QTableWidgetItem(symbol));
        hotTable_->setItem(row, 1, new QTableWidgetItem(market));
        hotTable_->setItem(row, 2, new QTableWidgetItem(watchSymbols_.contains(symbol)
                                                           ? QStringLiteral("与观察列表重叠 · 上游单流")
                                                           : QStringLiteral("仅L1热维护")));
        const int dot = symbol.indexOf(u'.');
        hotTable_->setItem(row, 3, new QTableWidgetItem(watchNames_.value(symbol, symbol.left(dot))));
    }
    if (auto *box = qobject_cast<QGroupBox *>(hotTable_->parentWidget())) {
        box->setTitle(QStringLiteral("额外L1行情维护标的 · 当前 %1 个 · 与观察列表重叠时上游仅一流 · 不计算信号、不落盘")
                          .arg(hotSymbols_.size()));
    }
}

bool ConsoleWindow::persistWatchlist()
{
    if (metrics_.state() != QAbstractSocket::ConnectedState) {
        QMessageBox::warning(this, QStringLiteral("A-core 未连接"),
                             QStringLiteral("列表未保存。请先连接 A-core，再修改观察标的。"));
        return false;
    }
    sendWatchlistToCore();
    return true;
}

bool ConsoleWindow::persistHotlist()
{
    if (metrics_.state() != QAbstractSocket::ConnectedState) {
        QMessageBox::warning(this, QStringLiteral("A-core 未连接"),
                             QStringLiteral("列表未保存。请先连接 A-core，再修改 L1 热维护标的。"));
        return false;
    }
    sendHotlistToCore();
    return true;
}

void ConsoleWindow::sendWatchlistToCore()
{
    if (metrics_.state() != QAbstractSocket::ConnectedState || watchSymbols_.isEmpty()) return;
    QJsonArray symbols;
    for (const QString &symbol : watchSymbols_) symbols.append(symbol);
    metrics_.sendTextMessage(QString::fromUtf8(
        QJsonDocument(QJsonObject{{"op", "set_watchlist"}, {"symbols", symbols}}).toJson(QJsonDocument::Compact)));
}

void ConsoleWindow::sendHotlistToCore()
{
    if (metrics_.state() != QAbstractSocket::ConnectedState) return;
    QJsonArray symbols;
    for (const QString &symbol : hotSymbols_) symbols.append(symbol);
    metrics_.sendTextMessage(QString::fromUtf8(
        QJsonDocument(QJsonObject{{"op", "set_l1_hotlist"}, {"symbols", symbols}}).toJson(QJsonDocument::Compact)));
}

void ConsoleWindow::addWatchSymbol()
{
    const QString code = watchCode_->text().trimmed();
    if (!QRegularExpression(QStringLiteral("^[0-9]{6}$")).match(code).hasMatch()) {
        QMessageBox::warning(this, QStringLiteral("代码无效"), QStringLiteral("请输入6位数字证券代码。"));
        return;
    }
    const QString symbol = code + u'.' + watchMarket_->currentData().toString();
    if (watchSymbols_.contains(symbol)) {
        QMessageBox::information(this, QStringLiteral("已存在"), symbol + QStringLiteral(" 已在观察列表中。"));
        return;
    }
    QSet<QString> combined(watchSymbols_.begin(), watchSymbols_.end());
    combined.unite(QSet<QString>(hotSymbols_.begin(), hotSymbols_.end()));
    combined.insert(symbol);
    if (combined.size() > 1000) {
        QMessageBox::warning(this, QStringLiteral("容量已满"),
                             QStringLiteral("观察列表与 L1 热维护列表去重后最多1000个标的。"));
        return;
    }
    watchSymbols_.append(symbol);
    if (!persistWatchlist()) watchSymbols_.removeAll(symbol);
    refreshWatchlistTable();
    refreshHotlistTable();
    watchCode_->clear();
}

void ConsoleWindow::addHotSymbol()
{
    const QString code = hotCode_->text().trimmed();
    const QString market = hotMarket_->currentData().toString();
    const int requiredLength = market == QStringLiteral("HK") ? 5 : 6;
    if (code.size() != requiredLength || !QRegularExpression(QStringLiteral("^[0-9]+$")).match(code).hasMatch()) {
        QMessageBox::warning(this, QStringLiteral("代码无效"),
                             market == QStringLiteral("HK")
                                 ? QStringLiteral("请输入保留前导零的5位港股代码，例如 02800。")
                                 : QStringLiteral("请输入6位沪深证券代码。"));
        return;
    }
    const QString symbol = code + u'.' + market;
    if (hotSymbols_.contains(symbol)) {
        QMessageBox::information(this, QStringLiteral("已存在"), symbol + QStringLiteral(" 已在 L1 热维护列表中。"));
        return;
    }
    QSet<QString> combined(watchSymbols_.begin(), watchSymbols_.end());
    combined.unite(QSet<QString>(hotSymbols_.begin(), hotSymbols_.end()));
    combined.insert(symbol);
    if (combined.size() > 1000) {
        QMessageBox::warning(this, QStringLiteral("容量已满"), QStringLiteral("观察列表与 L1 热维护列表去重后最多1000个标的。"));
        return;
    }
    hotSymbols_.append(symbol);
    if (!persistHotlist()) hotSymbols_.removeAll(symbol);
    refreshHotlistTable();
    hotCode_->clear();
}

void ConsoleWindow::removeSelectedWatchSymbols()
{
    QSet<int> selectedRows;
    for (const QModelIndex &index : watchTable_->selectionModel()->selectedRows()) selectedRows.insert(index.row());
    if (selectedRows.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择"), QStringLiteral("请先选中需要移除的标的行。"));
        return;
    }
    if (selectedRows.size() >= watchSymbols_.size()) {
        QMessageBox::warning(this, QStringLiteral("不能清空"), QStringLiteral("观察列表至少保留1个标的。"));
        return;
    }
    const QStringList before = watchSymbols_;
    QList<int> rows = selectedRows.values();
    std::sort(rows.begin(), rows.end(), std::greater<int>());
    for (int row : rows) watchSymbols_.removeAt(row);
    if (!persistWatchlist()) watchSymbols_ = before;
    refreshWatchlistTable();
    refreshHotlistTable();
}

void ConsoleWindow::removeSelectedHotSymbols()
{
    QSet<int> selectedRows;
    for (const QModelIndex &index : hotTable_->selectionModel()->selectedRows()) selectedRows.insert(index.row());
    if (selectedRows.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("未选择"), QStringLiteral("请先选中需要移除的 L1 热维护标的。"));
        return;
    }
    const QStringList before = hotSymbols_;
    QList<int> rows = selectedRows.values();
    std::sort(rows.begin(), rows.end(), std::greater<int>());
    for (int row : rows) hotSymbols_.removeAt(row);
    if (!persistHotlist()) hotSymbols_ = before;
    refreshHotlistTable();
}

void ConsoleWindow::validateConfiguration()
{
    QString configPath = QDir(root_).filePath(QStringLiteral("config/app.json"));
    if (!QFileInfo::exists(configPath)) configPath = QDir(root_).filePath(QStringLiteral("config/app.example.json"));
    QFile configFile(configPath);
    QJsonParseError error;
    if (!configFile.open(QIODevice::ReadOnly)) {
        appendLog(QStringLiteral("CONFIG"), QStringLiteral("失败：%1\n").arg(configFile.errorString()).toUtf8());
        return;
    }
    const QJsonDocument document = QJsonDocument::fromJson(configFile.readAll(), &error);
    if (!document.isObject()) {
        appendLog(QStringLiteral("CONFIG"), QStringLiteral("失败：JSON %1\n").arg(error.errorString()).toUtf8());
        return;
    }
    const QJsonObject config = document.object();
    const QString watchlistPath = QDir(root_).filePath(config.value(QStringLiteral("watchlist")).toString(QStringLiteral("config/watchlist.json")));
    const QString hotlistPath = QDir(root_).filePath(config.value(QStringLiteral("l1_hotlist")).toString(QStringLiteral("config/l1_hotlist.json")));
    const QString namesPath = QDir(root_).filePath(config.value(QStringLiteral("security_names")).toString(QStringLiteral("config/security_names.tsv")));
    QFile watchlistFile(watchlistPath);
    QFile hotlistFile(hotlistPath);
    QFile namesFile(namesPath);
    if (!watchlistFile.open(QIODevice::ReadOnly) || !hotlistFile.open(QIODevice::ReadOnly)
        || !namesFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
        appendLog(QStringLiteral("CONFIG"), QByteArrayLiteral("失败：watchlist、l1_hotlist 或 security_names 无法读取\n"));
        return;
    }
    const QJsonDocument watchlist = QJsonDocument::fromJson(watchlistFile.readAll());
    const QJsonArray symbols = watchlist.isArray() ? watchlist.array()
                                                   : watchlist.object().value(QStringLiteral("symbols")).toArray();
    const QJsonDocument hotlist = QJsonDocument::fromJson(hotlistFile.readAll());
    const QJsonArray hotSymbols = hotlist.isArray() ? hotlist.array()
                                                    : hotlist.object().value(QStringLiteral("symbols")).toArray();
    QSet<QString> uniqueSymbols;
    bool hotValid = true;
    for (const QJsonValue &value : symbols) uniqueSymbols.insert(value.toString().trimmed().toUpper());
    for (const QJsonValue &value : hotSymbols) {
        const QString symbol = value.toString().trimmed().toUpper();
        const bool domestic = QRegularExpression(QStringLiteral("^[0-9]{6}\\.(SH|SZ)$")).match(symbol).hasMatch();
        const bool hkt = QRegularExpression(QStringLiteral("^[0-9]{5}\\.HK$")).match(symbol).hasMatch();
        hotValid = hotValid && (domestic || hkt);
        uniqueSymbols.insert(symbol);
    }
    int nameCount = 0;
    while (!namesFile.atEnd()) if (!namesFile.readLine().trimmed().isEmpty()) ++nameCount;
    const int monitorPort = config.value(QStringLiteral("monitor_port")).toInt();
    const int legacyPort = config.value(QStringLiteral("legacy_l1_port")).toInt();
    const QString mode = config.value(QStringLiteral("mode")).toString();
    const QString accountPath = QDir(root_).filePath(QStringLiteral("config/tgw_account.ini"));
    const QString nativeTgw = executablePath(QStringLiteral("etf-premium-tgw"));
    const int maximum = config.value(QStringLiteral("max_upstream_symbols")).toInt(1000);
    if (symbols.isEmpty() || uniqueSymbols.size() > maximum || !hotValid || nameCount < 1 || monitorPort <= 0 || monitorPort > 65535
        || legacyPort <= 0 || legacyPort > 65535 || monitorPort == legacyPort
        || !QFileInfo::exists(nativeTgw)
        || (mode == QStringLiteral("live") && !QFileInfo::exists(accountPath))) {
        appendLog(QStringLiteral("CONFIG"),
                  QStringLiteral("失败：monitors=%1 hot=%2 unique=%3 (limit=%4) hot_valid=%5 names=%6 monitor_port=%7 legacy_port=%8 mode=%9 account=%10 native_tgw=%11\n")
                      .arg(symbols.size()).arg(hotSymbols.size()).arg(uniqueSymbols.size()).arg(maximum).arg(hotValid)
                      .arg(nameCount).arg(monitorPort).arg(legacyPort).arg(mode)
                      .arg(QFileInfo::exists(accountPath)).arg(QFileInfo::exists(nativeTgw)).toUtf8());
        return;
    }
    appendLog(QStringLiteral("CONFIG"),
              QStringLiteral("通过：%1 · %2 个观察标的 · %3 个L1热维护 · 去重%4 · %5 条名称 · 8421=%6 · 19195=%7 · mode=%8\n")
                  .arg(configPath).arg(symbols.size()).arg(hotSymbols.size()).arg(uniqueSymbols.size())
                  .arg(nameCount).arg(monitorPort).arg(legacyPort)
                  .arg(config.value(QStringLiteral("mode")).toString()).toUtf8());
}

QString ConsoleWindow::executablePath(const QString &name) const
{
    const QString sibling = QDir(QCoreApplication::applicationDirPath()).filePath(name);
    if (QFileInfo::exists(sibling)) return sibling;

    // Installed bundles live at <prefix>/*.app and the non-GUI core at
    // <prefix>/bin. From Contents/MacOS that is three parent directories.
    const QString installed = QDir(QCoreApplication::applicationDirPath())
                                  .absoluteFilePath(QStringLiteral("../../../bin/") + name);
    if (QFileInfo::exists(installed)) return QDir::cleanPath(installed);

    const QString nativeDebug = QDir(root_).filePath(QStringLiteral("build/native-macos-arm64-debug/") + name);
    if (QFileInfo::exists(nativeDebug)) return nativeDebug;
    const QString nativeRelease = QDir(root_).filePath(QStringLiteral("build/native-macos-arm64-release/") + name);
    if (QFileInfo::exists(nativeRelease)) return nativeRelease;

    const QString debug = QDir(root_).filePath(QStringLiteral("build/macos-arm64-debug-make/") + name);
    if (QFileInfo::exists(debug)) return debug;
    const QString release = QDir(root_).filePath(QStringLiteral("build/macos-arm64-release-make/") + name);
    if (QFileInfo::exists(release)) return release;

    const QString genericDebug = QDir(root_).filePath(QStringLiteral("build/debug/") + name);
    if (QFileInfo::exists(genericDebug)) return genericDebug;
    return QDir(root_).filePath(QStringLiteral("build/release/") + name);
}

void ConsoleWindow::startServices()
{
    if (attachOnly_) return;
    intentionalStop_ = false;
    fault_ = false;
    if (core_.state() == QProcess::NotRunning) startCore();
    QTimer::singleShot(500, this, [this] {
        if (core_.state() != QProcess::NotRunning && adapter_.state() == QProcess::NotRunning) startAdapter();
    });
}

void ConsoleWindow::startCore()
{
    core_.setWorkingDirectory(root_);
    QString config = QDir(root_).filePath(QStringLiteral("config/app.json"));
    if (!QFileInfo::exists(config)) config = QDir(root_).filePath(QStringLiteral("config/app.example.json"));
    QStringList arguments{QStringLiteral("--config"), config};
    if (forceQuotesRequested_) arguments.append(QStringLiteral("--force-quotes"));
    if (replayRequested_) arguments.append(QStringLiteral("--replay"));
    core_.start(executablePath(QStringLiteral("etf-premium-core")), arguments);
    QTimer::singleShot(800, this, &ConsoleWindow::connectMetrics);
}

void ConsoleWindow::startAdapter()
{
    QString config = QDir(root_).filePath(QStringLiteral("config/app.json"));
    if (!QFileInfo::exists(config)) config = QDir(root_).filePath(QStringLiteral("config/app.example.json"));
    const QJsonObject app = QJsonDocument::fromJson([&] {
        QFile file(config);
        return file.open(QIODevice::ReadOnly) ? file.readAll() : QByteArray{};
    }()).object();
    QStringList arguments{QStringLiteral("--socket"), QDir(root_).filePath(app.value(QStringLiteral("adapter_socket")).toString(QStringLiteral("runtime/tgw.sock"))),
                          QStringLiteral("--watchlist"), QDir(root_).filePath(app.value(QStringLiteral("watchlist")).toString(QStringLiteral("config/watchlist.json"))),
                          QStringLiteral("--log"), QDir(root_).filePath(QStringLiteral("logs/tgw-native.log"))};
    const QString account = QDir(root_).filePath(QStringLiteral("config/tgw_account.ini"));
    if (app.value(QStringLiteral("mode")).toString() == QStringLiteral("simulation")) {
        arguments.append(QStringLiteral("--simulate"));
    } else {
        arguments.append({QStringLiteral("--account"), account});
        const QString usernameOverride = QDir(root_).filePath(QStringLiteral("config/tgw_username_override"));
        if (QFileInfo::exists(usernameOverride)) arguments.append({QStringLiteral("--username-file"), usernameOverride});
        const QString caFile = QDir(root_).filePath(QStringLiteral("certs/vendor-dgw-ca.crt"));
        if (QFileInfo::exists(caFile)) arguments.append({QStringLiteral("--ca-file"), caFile});
    }
    adapter_.setWorkingDirectory(root_);
    adapter_.start(executablePath(QStringLiteral("etf-premium-tgw")), arguments);
}

void ConsoleWindow::stopServices()
{
    if (attachOnly_) {
        metrics_.close();
        return;
    }
    intentionalStop_ = true;
    metrics_.close();
    for (QProcess *process : {&replayProcess_, &adapter_, &core_}) {
        if (process->state() == QProcess::NotRunning) continue;
        process->terminate();
        if (!process->waitForFinished(3000)) process->kill();
    }
    updateState();
}

void ConsoleWindow::restartServices()
{
    if (attachOnly_) return;
    stopServices();
    intentionalStop_ = false;
    QTimer::singleShot(300, this, &ConsoleWindow::startServices);
}

void ConsoleWindow::connectMetrics()
{
    if (metrics_.state() == QAbstractSocket::ConnectedState || metrics_.state() == QAbstractSocket::ConnectingState) return;
    metrics_.open(QUrl(QStringLiteral("ws://127.0.0.1:8421/ws/v2/summary")));
}

void ConsoleWindow::startReplay()
{
    if (attachOnly_) return;
    const QString input = QFileDialog::getOpenFileName(this, QStringLiteral("选择 raw JSONL/ZSTD 分区"),
                                                       QDir(root_).filePath(QStringLiteral("data")),
                                                       QStringLiteral("Raw data (raw-*.jsonl raw-*.jsonl.zst)"));
    if (input.isEmpty()) return;
    intentionalStop_ = true;
    if (adapter_.state() != QProcess::NotRunning) {
        adapter_.terminate();
        adapter_.waitForFinished(3000);
    }
    if (core_.state() != QProcess::NotRunning) {
        core_.terminate();
        core_.waitForFinished(3000);
    }
    replayRequested_ = true;
    forceQuotesRequested_ = false;
    intentionalStop_ = false;
    startCore();
    QTimer::singleShot(700, this, [this, input] {
        const QString privatePython = QDir(root_).filePath(QStringLiteral(".venv/bin/python"));
        const QString python = QFileInfo::exists(privatePython) ? privatePython : QStringLiteral("/Users/ellis/miniconda3/envs/ag/bin/python");
        replayProcess_.setWorkingDirectory(root_);
        replayProcess_.start(python, {QDir(root_).filePath(QStringLiteral("tools/replay_raw.py")), input,
                                      QStringLiteral("--socket"), QDir(root_).filePath(QStringLiteral("runtime/tgw.sock"))});
    });
}

bool ConsoleWindow::permitAutomaticRestart()
{
    const QDateTime cutoff = QDateTime::currentDateTime().addSecs(-300);
    while (!restartTimes_.isEmpty() && restartTimes_.front() < cutoff) restartTimes_.removeFirst();
    if (restartTimes_.size() >= 3) return false;
    restartTimes_.append(QDateTime::currentDateTime());
    return true;
}

void ConsoleWindow::handleFinished(const QString &name, int exitCode, QProcess::ExitStatus status)
{
    appendLog(name, QStringLiteral("进程退出 code=%1 status=%2\n").arg(exitCode).arg(status).toUtf8());
    updateState();
    if (intentionalStop_ || fault_) return;
    if (!permitAutomaticRestart()) {
        fault_ = true;
        restartState_->setText(QStringLiteral("FAULT · 5分钟内已重启3次，请人工检查"));
        restartState_->setStyleSheet(QStringLiteral("color:#d7263d;font-weight:700"));
        tray_->showMessage(QStringLiteral("ETF 服务端进入 FAULT"), QStringLiteral("自动重启次数已达上限"), QSystemTrayIcon::Critical);
        return;
    }
    restartState_->setText(QStringLiteral("RECOVERING · %1 异常退出").arg(name));
    QTimer::singleShot(1000, this, &ConsoleWindow::startServices);
}

void ConsoleWindow::appendLog(const QString &source, const QByteArray &bytes)
{
    const QString prefix = QDateTime::currentDateTime().toString(QStringLiteral("HH:mm:ss.zzz")) + QStringLiteral(" [") + source + QStringLiteral("] ");
    for (const QString &line : QString::fromUtf8(bytes).split(u'\n', Qt::SkipEmptyParts)) log_->appendPlainText(prefix + line);
}

void ConsoleWindow::updateState()
{
    if (attachOnly_) {
        coreState_->setText(QStringLiteral("EXTERNAL"));
        adapterState_->setText(QStringLiteral("EXTERNAL"));
        return;
    }
    const auto text = [](QProcess::ProcessState state) {
        return state == QProcess::Running ? QStringLiteral("RUNNING") : state == QProcess::Starting ? QStringLiteral("STARTING") : QStringLiteral("STOPPED");
    };
    coreState_->setText(text(core_.state()));
    adapterState_->setText(text(adapter_.state()));
    coreState_->setStyleSheet(core_.state() == QProcess::Running ? QStringLiteral("color:#008f5a;font-weight:700") : QStringLiteral("color:#d7263d;font-weight:700"));
    adapterState_->setStyleSheet(adapter_.state() == QProcess::Running ? QStringLiteral("color:#008f5a;font-weight:700") : QStringLiteral("color:#d7263d;font-weight:700"));
}

void ConsoleWindow::closeEvent(QCloseEvent *event)
{
    event->ignore();
    hide();
    tray_->showMessage(QStringLiteral("ETF 服务端仍在运行"), QStringLiteral("请用托盘菜单“明确退出”停止子进程。"));
}

} // namespace premium
