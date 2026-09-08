#include "ui/MainWindow.h"

#include "common/JsonUtil.h"
#include "ui/HubClient.h"
#include "ui/AlertController.h"
#include "ui/ModulePages.h"
#include "MonitorSyncPage.h"
#include "ui/UiText.h"

#include <QCloseEvent>
#include <QApplication>
#include <QComboBox>
#include <QDateTime>
#include <QGridLayout>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QLabel>
#include <QListWidget>
#include <QMessageBox>
#include <QMenu>
#include <QMetaObject>
#include <QPushButton>
#include <QSettings>
#include <QSet>
#include <QStackedWidget>
#include <QStatusBar>
#include <QStyle>
#include <QTableWidget>
#include <QSystemTrayIcon>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>

namespace hub {
namespace {

QLabel *makeLabel(const QString &text, const QString &name = {}) {
    auto *label = new QLabel(text);
    label->setObjectName(name);
    label->setWordWrap(true);
    label->setTextFormat(Qt::PlainText);
    return label;
}

QString firstText(const QJsonObject &object, const QStringList &paths,
                  const QString &fallback = QStringLiteral("—")) {
    for (const auto &path : paths) {
        const auto value = valueAt(object, path);
        if (!value.isUndefined() && !value.isNull()) return displayValue(value, fallback);
    }
    return fallback;
}

QString operatorStateText(const QString &raw) { return ui::stateText(raw); }
QString operatorBoolText(const QString &raw) { return ui::stateText(raw); }
QString prettyAction(const QString &raw) { return ui::actionText(raw); }

} // namespace

MainWindow::MainWindow(AppConfig config, QString configPath, QString agentProgram, QWidget *parent, StartMode startMode)
    : QMainWindow(parent), config_(std::move(config)) {
    setWindowTitle(QStringLiteral("Machome 五合一运行中心"));
    resize(1480, 920);
    setMinimumSize(1120, 720);

    auto *root = new QWidget;
    auto *rootLayout = new QHBoxLayout(root);
    rootLayout->setContentsMargins(0, 0, 0, 0);
    rootLayout->setSpacing(0);
    navigation_ = new QListWidget;
    navigation_->setObjectName(QStringLiteral("navigation"));
    navigation_->setFixedWidth(220);
    navigation_->addItem(QStringLiteral("  总览"));
    pages_ = new QStackedWidget;
    pages_->addWidget(buildOverview());

    int pageIndex = 1;
    auto *alertController=new AlertController(this);
    for (const auto &module : config_.modules) {
        if (!module.enabled) continue;
        moduleConfigs_.insert(module.id, module);
        navigation_->addItem(QStringLiteral("  %1").arg(ui::serviceTitle(module.adapter, module.displayName)));
        auto *page = createModulePage(module);
        modulePages_.insert(module.id, page);
        pages_->addWidget(page);
        connect(page, &ModulePage::refreshRequested, this, &MainWindow::refreshClient);
        connect(page, &ModulePage::commandRequested, this, &MainWindow::requestCommand);
        connect(page, &ModulePage::alertRequested, this,
                [this,alertController](const QString &moduleId, const QString &title, const QString &message,
                       bool sound, bool popup) {
            if(moduleId==QStringLiteral("redemption")){alertController->notify(moduleId,title,message,sound,popup);return;}
            if (sound) QApplication::beep();
            if (popup && tray_ && tray_->isVisible()) {
                tray_->showMessage(title, message, QSystemTrayIcon::Information, 8000);
            }
            statusBar()->showMessage(QStringLiteral("%1：%2")
                                         .arg(moduleDisplayName(moduleId), message), 12000);
        });
        if (cards_.contains(module.id)) cards_[module.id].pageIndex = pageIndex;
        ++pageIndex;
    }
    rootLayout->addWidget(navigation_);
    rootLayout->addWidget(pages_, 1);
    setCentralWidget(root);
    connect(navigation_, &QListWidget::currentRowChanged, pages_, &QStackedWidget::setCurrentIndex);
    navigation_->setCurrentRow(0);

    setStyleSheet(ui::styleSheet());
    if (startMode == StartMode::OfflinePreview) {
        agentState_->setText(QStringLiteral("离线预览"));
        agentIdentity_->setText(QStringLiteral("展示保存的历史快照 · 未连接任何业务服务"));
        statusBar()->showMessage(QStringLiteral("离线界面验收：仅展示历史快照，不代表当前服务状态"));
        return;
    }

    clientThread_ = new QThread;
    clientThread_->setObjectName(QStringLiteral("Hub IPC"));
    client_ = new HubClient(config_.socketPath, std::move(configPath), std::move(agentProgram),
                            config_.frameLimitBytes, config_.configHash);
    client_->moveToThread(clientThread_);
    connect(clientThread_, &QThread::finished, client_, &QObject::deleteLater);
    connect(this, &MainWindow::startClient, client_, &HubClient::start, Qt::QueuedConnection);
    connect(this, &MainWindow::stopClient, client_, &HubClient::stop, Qt::QueuedConnection);
    connect(this, &MainWindow::refreshClient, client_, &HubClient::refresh, Qt::QueuedConnection);
    connect(this, &MainWindow::commandClient, client_, &HubClient::sendCommand, Qt::QueuedConnection);
    connect(this, &MainWindow::criticalEventRendered,
            client_, &HubClient::acknowledgeCriticalEvent, Qt::QueuedConnection);
    connect(client_, &HubClient::connectionChanged, this, &MainWindow::onConnection, Qt::QueuedConnection);
    connect(client_, &HubClient::helloReceived, this, &MainWindow::onHello, Qt::QueuedConnection);
    connect(client_, &HubClient::snapshotReceived, this, &MainWindow::onSnapshot, Qt::QueuedConnection);
    connect(client_, &HubClient::eventReceived, this, &MainWindow::onEvent, Qt::QueuedConnection);
    connect(client_, &HubClient::commandReceived, this, &MainWindow::onCommand, Qt::QueuedConnection);
    connect(client_, &HubClient::protocolError, this, &MainWindow::onProtocolError, Qt::QueuedConnection);
    connect(client_, &HubClient::bindingChanged, this, &MainWindow::onBinding, Qt::QueuedConnection);
    clientThread_->start();
    emit startClient();

    if (QSystemTrayIcon::isSystemTrayAvailable()) {
        tray_ = new QSystemTrayIcon(style()->standardIcon(QStyle::SP_ComputerIcon), this);
        tray_->setToolTip(QStringLiteral("Machome 四合一运行中心"));
        auto *menu = new QMenu(this);
        menu->addAction(QStringLiteral("显示运行中心"), this, [this] {
            show();
            raise();
            activateWindow();
        });
        menu->addSeparator();
        menu->addAction(QStringLiteral("退出控制台（后台服务与业务继续运行）"), this, [this] {
            quitting_ = true;
            qApp->quit();
        });
        tray_->setContextMenu(menu);
        connect(tray_, &QSystemTrayIcon::activated, this,
                [this](QSystemTrayIcon::ActivationReason reason) {
            if (reason == QSystemTrayIcon::Trigger || reason == QSystemTrayIcon::DoubleClick) {
                show();
                raise();
                activateWindow();
            }
        });
        tray_->show();
    }

    QSettings settings;
    restoreGeometry(settings.value(QStringLiteral("main/geometry")).toByteArray());
}

MainWindow::~MainWindow() {
    QThread *thread = clientThread_;
    if (!thread) return;
    // Release transient Agent-side observation leases while IPC is still
    // connected. Mutation controls are unaffected, and this never stops a
    // business process or the Agent itself.
    if (controlPlaneReady_) {
        for (auto *page : std::as_const(modulePages_)) {
            if (page) page->prepareForShutdown();
        }
    }
    if (client_ && thread->isRunning()) {
        QMetaObject::invokeMethod(client_, [client = client_, thread] {
            client->stop();
            thread->quit();
        }, Qt::QueuedConnection);
    } else {
        thread->quit();
    }
    if (!thread->wait(10000)) {
        thread->requestInterruption();
        thread->quit();
    }
    if (thread->isRunning() && !thread->wait(5000)) {
        qCritical().noquote()
            << "Hub IPC 线程拒绝协作退出；已解绑并交由进程退出回收";
        clientThread_ = nullptr;
        client_ = nullptr;
        return;
    }
    delete thread;
    clientThread_ = nullptr;
    client_ = nullptr;
}

void MainWindow::closeEvent(QCloseEvent *event) {
    QSettings settings;
    settings.setValue(QStringLiteral("main/geometry"), saveGeometry());
    if (!quitting_ && tray_ && tray_->isVisible()) {
        hide();
        tray_->showMessage(QStringLiteral("Machome 四合一运行中心"),
                           QStringLiteral("控制台已隐藏；后台服务和四个业务模块继续运行。"),
                           QSystemTrayIcon::Information, 4000);
        event->ignore();
        return;
    }
    QMainWindow::closeEvent(event);
}

QWidget *MainWindow::buildOverview() {
    auto *page = new QWidget;
    auto *layout = new QVBoxLayout(page);
    layout->setContentsMargins(26, 20, 26, 24);
    layout->setSpacing(14);
    auto *top = new QHBoxLayout;
    auto *titles = new QVBoxLayout;
    titles->addWidget(makeLabel(QStringLiteral("运行总览"), QStringLiteral("pageTitle")));
    agentIdentity_ = makeLabel(QStringLiteral("正在连接后台服务"), QStringLiteral("secondaryText"));
    titles->addWidget(agentIdentity_);
    top->addLayout(titles, 1);
    agentState_ = makeLabel(QStringLiteral("未连接"), QStringLiteral("stateUnknown"));
    agentState_->setSizePolicy(QSizePolicy::Maximum, QSizePolicy::Maximum);
    clock_ = makeLabel({});
    clock_->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    top->addWidget(agentState_);
    top->addWidget(clock_);
    layout->addLayout(top);

    auto *modeRow = new QHBoxLayout;
    modeRow->addWidget(makeLabel(QStringLiteral("全局运行模式"),
                                 QStringLiteral("sectionTitle")));
    operatingMode_ = new QComboBox(page);
    operatingMode_->setObjectName(QStringLiteral("globalOperatingMode"));
    operatingMode_->addItem(QStringLiteral("工作模式 · 按原程序时段运行"),
                            QStringLiteral("work"));
    operatingMode_->addItem(QStringLiteral("周末测试模式 · 保留写入限制"),
                            QStringLiteral("weekend_test"));
    modeRow->addWidget(operatingMode_, 1);
    applyOperatingMode_ = new QPushButton(QStringLiteral("应用到四个模块"), page);
    applyOperatingMode_->setObjectName(QStringLiteral("applyGlobalOperatingMode"));
    applyOperatingMode_->setEnabled(false);
    modeRow->addWidget(applyOperatingMode_);
    operatingModeState_ = makeLabel(QStringLiteral("当前：等待四个模块状态"),
                                    QStringLiteral("stateUnknown"));
    operatingModeState_->setObjectName(QStringLiteral("globalOperatingModeState"));
    modeRow->addWidget(operatingModeState_);
    layout->addLayout(modeRow);
    layout->addWidget(makeLabel(
        QStringLiteral("周末测试模式会在必要时启动已停止的原生模块，并允许盘外手动连接/采集；"
                       "不会放行真实上传、外部通知、QMT 下单，也不会伪造交易时段信号。"
                       "后台服务或模块重新初始化后默认回到工作模式。"),
        QStringLiteral("secondaryText")));
    connect(applyOperatingMode_, &QPushButton::clicked, page, [this] {
        const QString mode = operatingMode_->currentData().toString();
        operatingModeState_->setText(mode == QStringLiteral("weekend_test")
            ? QStringLiteral("正在切换到周末测试模式…")
            : QStringLiteral("正在恢复工作模式…"));
        for (auto it = moduleConfigs_.cbegin(); it != moduleConfigs_.cend(); ++it) {
            if (it.value().enabled
                && it.value().allowedActions.contains(
                    QStringLiteral("set_operating_mode"))) {
                requestCommand(it.key(), QStringLiteral("set_operating_mode"),
                               {{QStringLiteral("mode"), mode}}, 15000);
            }
        }
    });
    auto *timer = new QTimer(page);
    connect(timer, &QTimer::timeout, page, [this] {
        clock_->setText(QDateTime::currentDateTime().toString(QStringLiteral("yyyy-MM-dd HH:mm:ss")));
    });
    timer->start(1000);

    auto *grid = new QGridLayout;
    grid->setHorizontalSpacing(14);
    grid->setVerticalSpacing(14);
    int index = 0;
    for (const auto &module : config_.modules) {
        if (!module.enabled) continue;
        grid->addWidget(buildCard(module, index + 1), index / 2, index % 2);
        ++index;
    }
    layout->addLayout(grid, 1);
    layout->addWidget(makeLabel(QStringLiteral("最近事件与控制结果"), QStringLiteral("sectionTitle")));
    events_ = new QTableWidget(0, 5);
    events_->setHorizontalHeaderLabels({QStringLiteral("时间"), QStringLiteral("模块"), QStringLiteral("类型"),
                                        QStringLiteral("状态"), QStringLiteral("内容")});
    events_->verticalHeader()->setVisible(false);
    events_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    events_->setSelectionBehavior(QAbstractItemView::SelectRows);
    events_->horizontalHeader()->setStretchLastSection(true);
    events_->setMaximumHeight(230);
    layout->addWidget(events_);
    return page;
}

QWidget *MainWindow::buildCard(const ModuleConfig &module, int pageIndex) {
    auto *card = new QFrame;
    card->setObjectName(QStringLiteral("moduleCard"));
    auto *layout = new QVBoxLayout(card);
    layout->setContentsMargins(18, 15, 18, 15);
    layout->setSpacing(8);
    auto *top = new QHBoxLayout;
    top->addWidget(makeLabel(ui::serviceTitle(module.adapter, module.displayName), QStringLiteral("moduleTitle")));
    top->addStretch();
    auto *state = makeLabel(QStringLiteral("等待状态"), QStringLiteral("stateUnknown"));
    top->addWidget(state);
    layout->addLayout(top);
    auto *headline = makeLabel(QStringLiteral("尚未收到模块快照"));
    auto *metric = makeLabel(QStringLiteral("—"), QStringLiteral("metric"));
    auto *attention = makeLabel(module.ownership == QStringLiteral("shadow")
                                    ? QStringLiteral("仅观察现有业务")
                                    : QStringLiteral("管理方式：%1").arg(ui::stateText(module.ownership)),
                                QStringLiteral("attention"));
    layout->addWidget(headline);
    layout->addWidget(metric);
    layout->addWidget(attention);
    auto *actions = new QHBoxLayout;
    auto *control = new QPushButton(QStringLiteral("刷新"));
    connect(control, &QPushButton::clicked, this, [this, module] { emit refreshClient(module.id); });
    auto *details = new QPushButton(QStringLiteral("查看服务"));
    details->setProperty("primary", true);
    connect(details, &QPushButton::clicked, this, [this, pageIndex] { navigation_->setCurrentRow(pageIndex); });
    actions->addStretch();
    actions->addWidget(control);
    actions->addWidget(details);
    layout->addLayout(actions);
    cards_.insert(module.id, {card, state, headline, metric, attention, control, pageIndex});
    return card;
}

ModulePage *MainWindow::createModulePage(const ModuleConfig &module) {
    if(module.adapter=="monitor_sync")return new MonitorSyncPage(module);
    if (module.adapter == QStringLiteral("upload")) return new UploadPage(module);
    if (module.adapter == QStringLiteral("premium")) return new PremiumPage(module);
    if (module.adapter == QStringLiteral("webull")) return new WebullPage(module);
    return new RealtimePage(module);
}

void MainWindow::onConnection(bool connected, const QString &description) {
    agentState_->setText(description);
    agentState_->setObjectName(connected ? QStringLiteral("stateGood") : QStringLiteral("stateWarn"));
    agentState_->style()->unpolish(agentState_);
    agentState_->style()->polish(agentState_);
    statusBar()->showMessage(description, 5000);
}

void MainWindow::onHello(const QJsonObject &message) {
    agentIdentity_->setText(QStringLiteral("主机 %1 · 后台服务已连接")
                                .arg(message.value(QStringLiteral("host")).toString()));
    agentIdentity_->setToolTip(QStringLiteral("版本：%1\n进程编号：%2\n实例：%3\n构建校验：%4\n配置校验：%5")
                                .arg(message.value(QStringLiteral("version")).toString(),
                                     displayValue(message.value(QStringLiteral("pid"))),
                                     message.value(QStringLiteral("instance_id")).toString(),
                                     message.value(QStringLiteral("artifact_sha256")).toString(),
                                     message.value(QStringLiteral("config_sha256")).toString()));
    for (const auto &moduleValue : message.value(QStringLiteral("modules")).toArray()) {
        const QJsonObject module = moduleValue.toObject();
        QSet<QString> active;
        for (const auto &id : module.value(QStringLiteral("active_command_ids")).toArray()) {
            if (id.isString() && !id.toString().isEmpty()) active.insert(id.toString());
        }
        if (auto *page = modulePages_.value(module.value(QStringLiteral("id")).toString(), nullptr)) {
            page->reconcileActiveCommands(active);
        }
    }
    emit agentReady();
}

void MainWindow::onBinding(bool controlReady, const QString &description) {
    controlPlaneReady_ = controlReady;
    if (applyOperatingMode_) applyOperatingMode_->setEnabled(controlReady);
    statusBar()->showMessage(description, 12000);
}

void MainWindow::onSnapshot(const QJsonObject &message) {
    const QString moduleId = message.value(QStringLiteral("module_id")).toString();
    const qint64 snapshotRevision = message.value(QStringLiteral("control_revision")).toInteger(-1);
    if (snapshotRevision >= 0) {
        moduleRevisions_.insert(moduleId,
                                std::max(moduleRevisions_.value(moduleId, -1), snapshotRevision));
    }
    const QJsonObject payload = message.value(QStringLiteral("payload")).toObject();
    const QJsonObject telemetry = payload.value(QStringLiteral("telemetry")).toObject();
    QString mode = telemetry.value(QStringLiteral("engine")).toObject()
                       .value(QStringLiteral("operating_mode")).toString();
    if (mode.isEmpty()) {
        mode = telemetry.value(QStringLiteral("status")).toObject()
                   .value(QStringLiteral("operating_mode")).toString();
    }
    if (mode == QStringLiteral("work") || mode == QStringLiteral("weekend_test")) {
        moduleOperatingModes_.insert(moduleId, mode);
        if (operatingModeState_) {
            QSet<QString> modes(moduleOperatingModes_.cbegin(),
                                moduleOperatingModes_.cend());
            if (moduleOperatingModes_.size() < moduleConfigs_.size()) {
                operatingModeState_->setText(QStringLiteral("当前：等待四个模块状态"));
            } else if (modes.size() == 1 && modes.contains(QStringLiteral("work"))) {
                operatingModeState_->setText(QStringLiteral("当前：工作模式"));
                operatingMode_->setCurrentIndex(0);
            } else if (modes.size() == 1
                       && modes.contains(QStringLiteral("weekend_test"))) {
                operatingModeState_->setText(QStringLiteral("当前：周末测试模式"));
                operatingMode_->setCurrentIndex(1);
            } else {
                operatingModeState_->setText(QStringLiteral("当前：模块模式不一致"));
            }
        }
    }
    updateCard(moduleId, message);
    if (auto *page = modulePages_.value(moduleId, nullptr)) page->applySnapshot(message);
}

void MainWindow::updateCard(const QString &moduleId, const QJsonObject &message) {
    if (!cards_.contains(moduleId)) return;
    auto &card = cards_[moduleId];
    const auto payload = message.value(QStringLiteral("payload")).toObject();
    const auto telemetry = payload.value(QStringLiteral("telemetry")).toObject();
    const QString lifecycle = payload.value(QStringLiteral("lifecycle")).toString(QStringLiteral("unknown"));
    const QString workState = payload.value(QStringLiteral("work_state")).toString();
    card.state->setText(workState.isEmpty() ? operatorStateText(lifecycle)
        : operatorStateText(lifecycle) + QStringLiteral(" · ") + operatorStateText(workState));
    card.state->setObjectName(ui::stateStyle(payload));
    card.state->style()->unpolish(card.state);
    card.state->style()->polish(card.state);
    card.headline->setText(firstText(payload, {QStringLiteral("headline"), QStringLiteral("health.summary"),
                                               QStringLiteral("last_error")}, QStringLiteral("状态已更新")));
    QString metric;
    if (moduleId == QStringLiteral("upload")) {
        const QJsonObject engine = telemetry.value(QStringLiteral("engine")).toObject();
        int running = 0;
        const auto workers = telemetry.value(QStringLiteral("workers")).toArray();
        for (const auto &v : workers) if (v.toObject().value(QStringLiteral("state")).toString() == QStringLiteral("running")) ++running;
        metric = telemetry.value(QStringLiteral("workers")).isArray()
            ? QStringLiteral("运行任务 %1 / %2 · 业务%3")
                  .arg(running).arg(workers.size())
                  .arg(engine.value(QStringLiteral("ready")).isBool()
                      ? (engine.value(QStringLiteral("ready")).toBool() ? QStringLiteral("已就绪") : QStringLiteral("待就绪"))
                      : QStringLiteral("待确认"))
            : QStringLiteral("等待上传任务状态");
        card.headline->setText(QStringLiteral("行情与估值上传 · 网站业务同步"));
    } else if (moduleId == QStringLiteral("premium")) {
        metric = QStringLiteral("行情覆盖 %1 / %2 · 概览客户端 %3")
                     .arg(firstText(telemetry, {QStringLiteral("status.ready_symbols")}),
                          firstText(telemetry, {QStringLiteral("status.watchlist_symbols")}),
                          firstText(telemetry, {QStringLiteral("status.summary_clients")}));
    } else if (moduleId == QStringLiteral("webull")) {
        card.headline->setText(QStringLiteral("美股盘口采集 · 客户端行情转发"));
        metric = QStringLiteral("登录 %1 · 行情 %2 · 客户端 %3")
                     .arg(operatorStateText(firstText(telemetry, {QStringLiteral("status.auth_state"), QStringLiteral("status.auth.state")})),
                          operatorStateText(firstText(telemetry, {QStringLiteral("status.data_state"), QStringLiteral("status.data.state")})),
                          firstText(telemetry, {QStringLiteral("client_count")}));
    } else {
        card.headline->setText(QStringLiteral("Wind 实时份额 · 每日申赎清单"));
        metric = QStringLiteral("份额监控 %1 · Wind %2 · 标的 %3")
                     .arg(operatorBoolText(firstText(telemetry, {QStringLiteral("health.monitoring"), QStringLiteral("snapshot.monitoring")})).replace(QStringLiteral("是"), QStringLiteral("开启")).replace(QStringLiteral("否"), QStringLiteral("暂停")),
                          operatorStateText(firstText(telemetry, {QStringLiteral("health.wind_state"), QStringLiteral("health.wind.state"), QStringLiteral("health.wind_helper_state")})),
                          firstText(telemetry, {QStringLiteral("symbol_count")}, QStringLiteral("—")));
    }
    card.metric->setText(metric);
    const QString error = firstText(payload, {QStringLiteral("last_error"), QStringLiteral("health.last_error")}, {});
    card.attention->setObjectName(error.isEmpty() || error == QStringLiteral("—") ? QStringLiteral("secondaryText") : QStringLiteral("attention"));
    card.attention->style()->unpolish(card.attention);
    card.attention->style()->polish(card.attention);
    card.attention->setText(error.isEmpty() || error == QStringLiteral("—")
                                ? QStringLiteral("更新于 %1 · 本地时间").arg(ui::localTimeText(message.value(QStringLiteral("timestamp")).toString()))
                                : ui::problemText(error));
}

void MainWindow::onEvent(const QJsonObject &message) {
    const QString moduleId = message.value(QStringLiteral("module_id")).toString();
    if (auto *page = modulePages_.value(moduleId, nullptr)) page->applyEvent(message);
    const QString kind = message.value(QStringLiteral("event_kind")).toString();
    static const QSet<QString> continuousTelemetry{
        QStringLiteral("premium.summary"), QStringLiteral("premium.detail"),
        QStringLiteral("premium.l1_status"), QStringLiteral("premium.raw_snapshot"),
        QStringLiteral("webull.book"), QStringLiteral("webull.clients"),
        QStringLiteral("webull.log"), QStringLiteral("redemption.snapshot"),
        QStringLiteral("redemption.status"), QStringLiteral("process.log")};
    // The overview is an operator event ledger, not a market-data tape.
    if (!continuousTelemetry.contains(kind)) appendEventRow(message);
    const qint64 auditEventId = message.value(QStringLiteral("audit_event_id")).toInteger();
    if (auditEventId > 0) {
        emit criticalEventRendered(
            auditEventId, message.value(QStringLiteral("audit_epoch")).toString(),
            static_cast<quint64>(message.value(
                QStringLiteral("client_delivery_generation")).toInteger()));
    }
}

void MainWindow::onCommand(const QJsonObject &message) {
    const QString moduleId = message.value(QStringLiteral("module_id")).toString();
    const qint64 revision = message.value(QStringLiteral("control_revision")).toInteger(-1);
    if (revision >= 0) {
        moduleRevisions_.insert(moduleId,
                                std::max(moduleRevisions_.value(moduleId, -1), revision));
    }
    if (auto *page = modulePages_.value(moduleId, nullptr)) page->applyCommand(message);
    appendEventRow(message, QStringLiteral("command"));
    const QString state = message.value(QStringLiteral("state")).toString();
    if (state == QStringLiteral("failed") || state == QStringLiteral("timed_out")) {
        statusBar()->showMessage(QStringLiteral("%1：%2").arg(moduleDisplayName(moduleId),
                                                              message.value(QStringLiteral("message")).toString()), 12000);
    }
}

void MainWindow::appendEventRow(const QJsonObject &message, const QString &kindOverride) {
    if (!events_) return;
    events_->insertRow(0);
    const auto payload = message.value(QStringLiteral("payload")).toObject();
    const QString kind = kindOverride.isEmpty() ? message.value(QStringLiteral("event_kind")).toString() : kindOverride;
    const QString content = message.value(QStringLiteral("message")).toString(
        payload.value(QStringLiteral("message")).toString(compactJson(redacted(payload))));
    const QStringList values{ui::localTimeText(message.value(QStringLiteral("timestamp")).toString()),
                             moduleDisplayName(message.value(QStringLiteral("module_id")).toString()), ui::actionText(kind),
                             operatorStateText(message.value(QStringLiteral("state")).toString()), content.left(500)};
    for (int column = 0; column < values.size(); ++column) events_->setItem(0, column, new QTableWidgetItem(values[column]));
    while (events_->rowCount() > 100) events_->removeRow(events_->rowCount() - 1);
}

void MainWindow::onProtocolError(const QString &message) {
    statusBar()->showMessage(message, 12000);
}

void MainWindow::requestCommand(const QString &moduleId, const QString &action,
                                const QJsonObject &arguments, int deadlineMs) {
    if (!controlPlaneReady_) {
        QMessageBox::warning(this, QStringLiteral("控制已锁定"),
                             QStringLiteral("界面尚未连接到配置一致且审计就绪的后台服务。"
                                            "请核对页眉中的 instance/config 后重试。"));
        return;
    }
    const auto config = moduleConfigs_.value(moduleId);
    if (!config.allowedActions.contains(action)) {
        QMessageBox::warning(this, QStringLiteral("动作未授权"),
                             QStringLiteral("%1 未列入该模块的 allowed_actions，命令不会发送。").arg(action));
        return;
    }
    const bool highRisk = config.approvalRequiredActions.contains(action);
    if (highRisk) {
        const bool targetedLifecycle = action.endsWith(QStringLiteral("_service"))
            && !arguments.value(QStringLiteral("target_unit")).toString().isEmpty();
        const QString impact = action == QStringLiteral("redemption_qmt_order")
                                   ? QStringLiteral("这会向所选 QMT 后端提交 1 篮 ETF 申购或赎回；确认后不可由本程序自动撤回。")
                                   : action == QStringLiteral("webull_show_login")
                                   ? QStringLiteral("原生 Webull 采集未运行时会切换为强制运行，然后打开登录窗口。")
                                   : action == QStringLiteral("webull_collector_start")
                                   ? QStringLiteral("这会立即启动 Webull 采集器，并等待 v2 status/ready 确认。")
                                   : action == QStringLiteral("stop_service") && targetedLifecycle
                                   ? QStringLiteral("仅目标 Upload unit 会停止；网站/其他 uploader 不受影响。")
                                   : action == QStringLiteral("stop_service")
                                   ? QStringLiteral("该模块业务将停止；其余三个模块不受影响。")
                                   : action == QStringLiteral("restart_service") && targetedLifecycle
                                   ? QStringLiteral("仅目标 Upload unit 会短暂中断，其他 unit 不受影响。")
                                   : action == QStringLiteral("restart_service")
                                         ? QStringLiteral("该模块将短暂中断并由后台服务验证恢复。")
                                         : QStringLiteral("后台服务将按配置的依赖顺序执行。其余模块不受影响。");
        QString target;
        if (action == QStringLiteral("redemption_qmt_order")) {
            const QString rawSide = arguments.value(QStringLiteral("side")).toString().trimmed().toUpper();
            if (rawSide != QStringLiteral("PURCHASE") && rawSide != QStringLiteral("REDEEM")) {
                QMessageBox::critical(this, QStringLiteral("订单参数无效"),
                                      QStringLiteral("订单方向必须明确为 PURCHASE 或 REDEEM；命令未发送。"));
                return;
            }
            const QString side = rawSide == QStringLiteral("PURCHASE")
                                     ? QStringLiteral("申购") : QStringLiteral("赎回");
            target = QStringLiteral("\n后端：%1\nETF：%2\n方向：%3\n数量：1 篮")
                         .arg(arguments.value(QStringLiteral("backend")).toString(),
                              arguments.value(QStringLiteral("symbol")).toString(), side);
        } else if (action.endsWith(QStringLiteral("_service"))) {
            QStringList targets;
            const QString targetUnit = arguments.value(QStringLiteral("target_unit")).toString();
            for (const auto &unit : config.launchdUnits) {
                if (targetUnit.isEmpty() || unit.id == targetUnit) {
                    targets << QStringLiteral("%1 (id=%2)").arg(unit.label, unit.id);
                }
            }
            if (!targetUnit.isEmpty() && targets.isEmpty()) {
                QMessageBox::critical(this, QStringLiteral("目标无效"),
                                      QStringLiteral("target_unit 不在当前配置中，命令未发送。"));
                return;
            }
            target = QStringLiteral("\nlaunchd 目标：%1").arg(targets.join(QStringLiteral(", ")));
        }
        const QString details = QStringLiteral("模块：%1 (%2)\n动作：%3\n所有权：%4%5\n\n%6")
                                    .arg(config.displayName, moduleId, prettyAction(action),
                                         config.ownership, target, impact);
        if (QMessageBox::warning(this, QStringLiteral("确认高风险操作"), details,
                                 QMessageBox::Cancel | QMessageBox::Ok, QMessageBox::Cancel) != QMessageBox::Ok) {
            return;
        }
    }
    emit commandClient(moduleId, action, arguments, deadlineMs,
                       moduleRevisions_.value(moduleId, -1), QStringLiteral("interactive_ui"));
}

QString MainWindow::moduleDisplayName(const QString &moduleId) const {
    if (!moduleConfigs_.contains(moduleId)) return moduleId;
    const auto &module = moduleConfigs_.value(moduleId);
    return ui::serviceTitle(module.adapter, module.displayName);
}

} // namespace hub
