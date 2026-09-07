#include <QApplication>
#include <QDateTime>
#include <QDialog>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QListWidget>
#include <QMainWindow>
#include <QPushButton>
#include <QScrollArea>
#include <QStackedWidget>
#include <QStyleFactory>
#include <QTabWidget>
#include <QTableWidget>
#include <QTextEdit>
#include <QTimer>
#include <QVBoxLayout>

namespace {

struct ModulePreview {
    QString name;
    QString state;
    QString tone;
    QString headline;
    QString metric1;
    QString metric2;
    QString attention;
};

QLabel *label(const QString &text, const QString &objectName = {}) {
    auto *value = new QLabel(text);
    value->setObjectName(objectName);
    value->setWordWrap(true);
    return value;
}

QTableWidget *table(const QStringList &headers, const QList<QStringList> &rows) {
    auto *widget = new QTableWidget(rows.size(), headers.size());
    widget->setHorizontalHeaderLabels(headers);
    widget->verticalHeader()->setVisible(false);
    widget->setAlternatingRowColors(true);
    widget->setSelectionBehavior(QAbstractItemView::SelectRows);
    widget->setEditTriggers(QAbstractItemView::NoEditTriggers);
    widget->horizontalHeader()->setStretchLastSection(true);
    widget->horizontalHeader()->setSectionResizeMode(QHeaderView::ResizeToContents);
    for (int row = 0; row < rows.size(); ++row) {
        for (int column = 0; column < rows[row].size(); ++column) {
            widget->setItem(row, column, new QTableWidgetItem(rows[row][column]));
        }
    }
    return widget;
}

QWidget *simplePanel(const QString &title, const QString &description,
                     const QStringList &headers = {},
                     const QList<QStringList> &rows = {}) {
    auto *page = new QWidget;
    auto *layout = new QVBoxLayout(page);
    layout->setContentsMargins(20, 20, 20, 20);
    layout->setSpacing(12);
    layout->addWidget(label(title, "sectionTitle"));
    layout->addWidget(label(description, "secondaryText"));
    if (!headers.isEmpty()) {
        layout->addWidget(table(headers, rows), 1);
    } else {
        layout->addStretch();
    }
    return page;
}

class MainWindow final : public QMainWindow {
public:
    MainWindow() {
        setWindowTitle(QStringLiteral("Machome 四合一运行中心 · 只读设计原型"));
        resize(1440, 900);
        setMinimumSize(1080, 700);

        auto *root = new QWidget;
        auto *layout = new QHBoxLayout(root);
        layout->setContentsMargins(0, 0, 0, 0);
        layout->setSpacing(0);

        navigation_ = new QListWidget;
        navigation_->setObjectName("navigation");
        navigation_->setFixedWidth(210);
        navigation_->addItems({QStringLiteral("  总览"),
                               QStringLiteral("  Upload 网站"),
                               QStringLiteral("  溢价率 A 端"),
                               QStringLiteral("  Webull 行情"),
                               QStringLiteral("  实时申购赎回")});

        pages_ = new QStackedWidget;
        pages_->addWidget(buildOverview());
        pages_->addWidget(buildUpload());
        pages_->addWidget(buildPremium());
        pages_->addWidget(buildWebull());
        pages_->addWidget(buildRedemption());

        layout->addWidget(navigation_);
        layout->addWidget(pages_, 1);
        setCentralWidget(root);

        connect(navigation_, &QListWidget::currentRowChanged,
                pages_, &QStackedWidget::setCurrentIndex);
        navigation_->setCurrentRow(0);

        setStyleSheet(R"(
            QMainWindow, QWidget { background: #f4f6f9; color: #1f2937; font-size: 14px; }
            #navigation { background: #18212f; color: #cbd5e1; border: 0; padding: 18px 10px; }
            #navigation::item { height: 46px; border-radius: 8px; padding-left: 8px; }
            #navigation::item:selected { background: #2c3c52; color: white; }
            #pageTitle { font-size: 25px; font-weight: 600; }
            #sectionTitle { font-size: 18px; font-weight: 600; }
            #moduleTitle { font-size: 17px; font-weight: 600; }
            #metric { font-size: 20px; font-weight: 600; }
            #secondaryText { color: #64748b; }
            #previewBanner { background: #e8f0fe; color: #264d88; border-radius: 8px; padding: 10px; }
            #warningText { color: #9a6700; }
            #stateGood { background: #dff6e7; color: #146c3a; border-radius: 9px; padding: 4px 9px; }
            #stateIdle { background: #e8f0fe; color: #315c9d; border-radius: 9px; padding: 4px 9px; }
            #stateWarn { background: #fff2cc; color: #8a5a00; border-radius: 9px; padding: 4px 9px; }
            QFrame#moduleCard { background: white; border: 1px solid #dce2ea; border-radius: 12px; }
            QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px 12px; }
            QPushButton:hover { background: #f8fafc; }
            QPushButton:disabled { color: #9ca3af; background: #f1f5f9; }
            QTabWidget::pane { background: white; border: 1px solid #dce2ea; }
            QTabBar::tab { padding: 10px 16px; background: #e9edf3; }
            QTabBar::tab:selected { background: white; }
            QTableWidget, QTextEdit { background: white; border: 1px solid #dce2ea; gridline-color: #e5e7eb; }
            QHeaderView::section { background: #edf1f6; border: 0; border-bottom: 1px solid #dce2ea; padding: 8px; }
        )");
    }

private:
    QWidget *buildOverview() {
        auto *body = new QWidget;
        auto *layout = new QVBoxLayout(body);
        layout->setContentsMargins(26, 22, 26, 26);
        layout->setSpacing(16);

        auto *header = new QHBoxLayout;
        auto *titleBox = new QVBoxLayout;
        titleBox->addWidget(label(QStringLiteral("运行总览"), "pageTitle"));
        titleBox->addWidget(label(QStringLiteral("EllisdeMac-mini.local · arm64 · macOS 26.6"), "secondaryText"));
        auto *clock = label({}, "secondaryText");
        clock->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
        auto *clockTimer = new QTimer(clock);
        connect(clockTimer, &QTimer::timeout, clock, [clock] {
            clock->setText(QDateTime::currentDateTime().toString(QStringLiteral("yyyy-MM-dd HH:mm:ss  CST")));
        });
        clockTimer->start(1000);
        header->addLayout(titleBox, 1);
        header->addWidget(clock);
        layout->addLayout(header);
        layout->addWidget(label(QStringLiteral("只读设计预览 · 运行快照约 2026-09-04 01:00 CST · 未连接生产控制面"),
                                "previewBanner"));

        const QList<ModulePreview> modules{
            {QStringLiteral("Upload 网站"), QStringLiteral("运行中 · 有注意项"), QStringLiteral("stateWarn"),
             QStringLiteral("Web 10 天未退出；日终份额同步已完成"),
             QStringLiteral("8080 · HTTP 200"), QStringLiteral("多个 uploader 常驻"),
             QStringLiteral("health monitor 有历史重启 / PushPlus 失败记录")},
            {QStringLiteral("溢价率 A 端"), QStringLiteral("计划空闲"), QStringLiteral("stateIdle"),
             QStringLiteral("console / core / TGW 均存活"),
             QStringLiteral("208 → 0 计划退订"), QStringLiteral("8421 / 19195 在线"),
             QStringLiteral("生产与本机构建 SHA 不一致")},
            {QStringLiteral("Webull 行情"), QStringLiteral("计划空闲"), QStringLiteral("stateIdle"),
             QStringLiteral("API live；auth authenticated；浏览器已按计划停止"),
             QStringLiteral("上一会话 21,104 帧"), QStringLiteral("invalid 0 · clients 0"),
             QStringLiteral("09:00 自动恢复采集")},
            {QStringLiteral("实时申购赎回"), QStringLiteral("运行中 · 待复核"), QStringLiteral("stateWarn"),
             QStringLiteral("服务在线；盘外 Wind 已退出"),
             QStringLiteral("7 个生产标的"), QStringLiteral("6787 自连接正常"),
             QStringLiteral("盘中 Wind 目录扫描存在 EINTR 警告")}
        };

        auto *grid = new QGridLayout;
        grid->setHorizontalSpacing(14);
        grid->setVerticalSpacing(14);
        for (int index = 0; index < modules.size(); ++index) {
            grid->addWidget(moduleCard(modules[index], index + 1), index / 2, index % 2);
        }
        layout->addLayout(grid, 1);

        auto *events = table({QStringLiteral("时间"), QStringLiteral("模块"), QStringLiteral("级别"), QStringLiteral("事件")},
                             {{QStringLiteral("00:00:08"), QStringLiteral("Webull"), QStringLiteral("信息"), QStringLiteral("登录检查通过，会话有效")},
                              {QStringLiteral("23:52:18"), QStringLiteral("Upload"), QStringLiteral("信息"), QStringLiteral("深交所 46 条份额历史同步完成")},
                              {QStringLiteral("15:00:01"), QStringLiteral("溢价率"), QStringLiteral("信息"), QStringLiteral("国内标的按计划退订")},
                              {QStringLiteral("14:41:58"), QStringLiteral("实时申赎"), QStringLiteral("注意"), QStringLiteral("Wind capture glob 被系统调用中断")}});
        events->setMaximumHeight(180);
        layout->addWidget(label(QStringLiteral("最近事件"), "sectionTitle"));
        layout->addWidget(events);
        return body;
    }

    QFrame *moduleCard(const ModulePreview &module, int pageIndex) {
        auto *card = new QFrame;
        card->setObjectName("moduleCard");
        auto *layout = new QVBoxLayout(card);
        layout->setContentsMargins(18, 16, 18, 16);
        layout->setSpacing(9);

        auto *top = new QHBoxLayout;
        top->addWidget(label(module.name, "moduleTitle"));
        top->addStretch();
        top->addWidget(label(module.state, module.tone));
        layout->addLayout(top);
        layout->addWidget(label(module.headline));

        auto *metrics = new QHBoxLayout;
        metrics->addWidget(label(module.metric1, "metric"));
        metrics->addSpacing(16);
        metrics->addWidget(label(module.metric2, "metric"));
        metrics->addStretch();
        layout->addLayout(metrics);
        layout->addWidget(label(module.attention, "warningText"));

        auto *actions = new QHBoxLayout;
        auto *control = new QPushButton(QStringLiteral("运行控制"));
        control->setEnabled(false);
        control->setToolTip(QStringLiteral("只读原型未连接 Hub Agent"));
        auto *details = new QPushButton(QStringLiteral("查看详情"));
        connect(details, &QPushButton::clicked, this, [this, pageIndex] {
            navigation_->setCurrentRow(pageIndex);
        });
        actions->addStretch();
        actions->addWidget(control);
        actions->addWidget(details);
        layout->addLayout(actions);
        return card;
    }

    QWidget *moduleShell(const QString &title, const QString &subtitle, QTabWidget *tabs) {
        auto *page = new QWidget;
        auto *layout = new QVBoxLayout(page);
        layout->setContentsMargins(26, 22, 26, 26);
        layout->setSpacing(12);

        auto *top = new QHBoxLayout;
        auto *titles = new QVBoxLayout;
        titles->addWidget(label(title, "pageTitle"));
        titles->addWidget(label(subtitle, "secondaryText"));
        auto *control = new QPushButton(QStringLiteral("控制菜单"));
        control->setEnabled(false);
        control->setToolTip(QStringLiteral("只读原型不会操作生产进程"));
        top->addLayout(titles, 1);
        top->addWidget(control);
        layout->addLayout(top);
        layout->addWidget(label(QStringLiteral("设计预览：数据为审计快照，状态操作尚未启用。"), "previewBanner"));
        layout->addWidget(tabs, 1);
        return page;
    }

    void connectSecondLevel(QTableWidget *source, const QString &title) {
        connect(source, &QTableWidget::cellDoubleClicked, this,
                [this, source, title](int row, int) {
                    auto *dialog = new QDialog(this);
                    dialog->setAttribute(Qt::WA_DeleteOnClose);
                    dialog->setWindowTitle(title);
                    dialog->resize(760, 500);
                    auto *layout = new QVBoxLayout(dialog);
                    const QString key = source->item(row, 0) ? source->item(row, 0)->text() : QStringLiteral("详情");
                    layout->addWidget(label(title + QStringLiteral(" · ") + key, "pageTitle"));
                    layout->addWidget(label(QStringLiteral("这是非模态二级窗口示例。正式版由稳定 route/context 恢复，并在断线时保留 stale 状态。"), "secondaryText"));
                    auto *tabs = new QTabWidget;
                    tabs->addTab(simplePanel(QStringLiteral("摘要"), QStringLiteral("字段、构建、数据新鲜度和来源。")), QStringLiteral("摘要"));
                    tabs->addTab(simplePanel(QStringLiteral("原始证据"), QStringLiteral("脱敏后的协议帧、日志游标与审计引用。")), QStringLiteral("原始证据"));
                    layout->addWidget(tabs, 1);
                    dialog->show();
                });
    }

    QWidget *buildUpload() {
        auto *tabs = new QTabWidget;
        auto *services = table({QStringLiteral("子服务"), QStringLiteral("PID"), QStringLiteral("状态"), QStringLiteral("最后业务时间"), QStringLiteral("说明")},
                               {{QStringLiteral("newnavnav-web"), QStringLiteral("913"), QStringLiteral("运行中"), QStringLiteral("23:52:18"), QStringLiteral("127.0.0.1:8080")},
                                {QStringLiteral("sina quote uploader"), QStringLiteral("28896"), QStringLiteral("计划空闲"), QStringLiteral("上一交易日"), QStringLiteral("WS 常驻")},
                                {QStringLiteral("upload health monitor"), QStringLiteral("82237"), QStringLiteral("预热/观察"), QStringLiteral("00:51:50"), QStringLiteral("launchd runs=6")}});
        connectSecondLevel(services, QStringLiteral("Upload 子服务详情"));
        tabs->addTab(simplePanel(QStringLiteral("服务总览"), QStringLiteral("网站与每个 uploader 独立呈现；双击打开非模态详情。"),
                                 {QStringLiteral("子服务"), QStringLiteral("PID"), QStringLiteral("状态"), QStringLiteral("最后业务时间"), QStringLiteral("说明")},
                                 {{QStringLiteral("newnavnav-web"), QStringLiteral("913"), QStringLiteral("运行中"), QStringLiteral("23:52:18"), QStringLiteral("127.0.0.1:8080")},
                                  {QStringLiteral("private uploader group"), QStringLiteral("多个"), QStringLiteral("运行/计划空闲"), QStringLiteral("上一交易日"), QStringLiteral("按 label 分拆")}}), QStringLiteral("服务总览"));
        tabs->addTab(services, QStringLiteral("上传器矩阵"));
        tabs->addTab(simplePanel(QStringLiteral("网站深链"), QStringLiteral("默认外部浏览器；可选 QWebEngine，保留基金详情与三级历史页面。")), QStringLiteral("网站"));
        auto *logs = new QTextEdit(QStringLiteral("[只读] 日志将使用 inode + offset 增量读取。\n不会每次重载整个文件。\n敏感字段在进入 UI 前脱敏。"));
        logs->setReadOnly(true);
        tabs->addTab(logs, QStringLiteral("日志"));
        return moduleShell(QStringLiteral("Upload 网站与上传链"), QStringLiteral("Go/Vue 网站 + 多个 Python uploader"), tabs);
    }

    QWidget *buildPremium() {
        auto *tabs = new QTabWidget;
        auto *alerts = table({QStringLiteral("时间"), QStringLiteral("标的"), QStringLiteral("模型"), QStringLiteral("强度"), QStringLiteral("状态")},
                             {{QStringLiteral("上一交易日 14:57"), QStringLiteral("示例"), QStringLiteral("premium/pull/radar"), QStringLiteral("—"), QStringLiteral("盘后只记录")}});
        connectSecondLevel(alerts, QStringLiteral("溢价率触发详情"));
        tabs->addTab(alerts, QStringLiteral("实时拉升告警"));
        tabs->addTab(simplePanel(QStringLiteral("历史信号审计"), QStringLiteral("分页加载、筛选、原始 JSON 和 worker CSV 导出。")), QStringLiteral("历史信号审计"));
        tabs->addTab(simplePanel(QStringLiteral("观察标的管理"), QStringLiteral("首期只读；后续复用 loopback 原子更新并支持拒绝回滚。")), QStringLiteral("观察标的"));
        tabs->addTab(simplePanel(QStringLiteral("额外 L1 行情维护"), QStringLiteral("保留国内六位与 02800.HK 路由及动态引用。")), QStringLiteral("额外 L1"));
        auto *logs = new QTextEdit(QStringLiteral("console / core / TGW 三路日志\n8421 status: 5 秒推送\n本机 build 与 production SHA 不一致：切换前阻断"));
        logs->setReadOnly(true);
        tabs->addTab(logs, QStringLiteral("运行日志"));
        return moduleShell(QStringLiteral("溢价率上升监控 A 端"), QStringLiteral("Qt/C++ console + core + native TGW"), tabs);
    }

    QWidget *buildWebull() {
        auto *tabs = new QTabWidget;
        auto *book = table({QStringLiteral("买量"), QStringLiteral("买价"), QStringLiteral("档"), QStringLiteral("卖价"), QStringLiteral("卖量")},
                           {{QStringLiteral("320"), QStringLiteral("191.41"), QStringLiteral("1"), QStringLiteral("194.44"), QStringLiteral("10")},
                            {QStringLiteral("25"), QStringLiteral("190.99"), QStringLiteral("2"), QStringLiteral("194.94"), QStringLiteral("1")},
                            {QStringLiteral("500"), QStringLiteral("190.17"), QStringLiteral("3"), QStringLiteral("195.00"), QStringLiteral("101")}});
        tabs->addTab(book, QStringLiteral("实时盘口"));
        auto *clients = table({QStringLiteral("客户端 ID"), QStringLiteral("远端"), QStringLiteral("连接时间"), QStringLiteral("最后发送"), QStringLiteral("消息数")},
                              {{QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("0")}});
        connectSecondLevel(clients, QStringLiteral("Webull 客户端详情"));
        tabs->addTab(clients, QStringLiteral("客户端"));
        tabs->addTab(simplePanel(QStringLiteral("运行与登录"), QStringLiteral("API live · collector stopped · auth authenticated · data no_data · 下一切换 09:00。")), QStringLiteral("运行与登录"));
        auto *logs = new QTextEdit(QStringLiteral("00:00:03 开始登录检查\n00:00:08 authenticated\n00:00:10 浏览器按盘外计划关闭\n\n盘口为上一会话缓存，正式页必须显示 STALE。"));
        logs->setReadOnly(true);
        tabs->addTab(logs, QStringLiteral("日志"));
        return moduleShell(QStringLiteral("Webull 行情转发"), QStringLiteral("Python/PyQt sidecar + Playwright + aiohttp :18765"), tabs);
    }

    QWidget *buildRedemption() {
        auto *tabs = new QTabWidget;
        auto *mainTable = table({QStringLiteral("标的"), QStringLiteral("名称"), QStringLiteral("状态"), QStringLiteral("买份额"), QStringLiteral("卖份额"), QStringLiteral("净份额"), QStringLiteral("买篮子"), QStringLiteral("卖篮子"), QStringLiteral("净篮子"), QStringLiteral("可申购"), QStringLiteral("机会"), QStringLiteral("时间"), QStringLiteral("最近变化")},
                                {{QStringLiteral("159518"), QStringLiteral("示例"), QStringLiteral("盘外"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("上一交易日"), QStringLiteral("无新变化")},
                                 {QStringLiteral("159513"), QStringLiteral("示例"), QStringLiteral("盘外"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("—"), QStringLiteral("上一交易日"), QStringLiteral("无新变化")}});
        connectSecondLevel(mainTable, QStringLiteral("PCF 详情（摘要 / 成分 / QMT1 / QMT2）"));
        tabs->addTab(mainTable, QStringLiteral("实时主表"));
        tabs->addTab(simplePanel(QStringLiteral("PCF 详情"), QStringLiteral("正式版由主表双击按 trade_date/symbol 打开，保留四个 tab。")), QStringLiteral("PCF 详情"));
        tabs->addTab(simplePanel(QStringLiteral("变化历史"), QStringLiteral("日期 + 标的过滤，保留原 8 列和 5,000 条上限。")), QStringLiteral("变化历史"));
        tabs->addTab(simplePanel(QStringLiteral("客户端设置"), QStringLiteral("服务器、重连、心跳、弹窗、声音、重复、音量与冷却。")), QStringLiteral("客户端设置"));
        auto *logs = new QTextEdit(QStringLiteral("服务 PID 914 · 6787 self-connection\n盘外 Wind stopped（符合计划）\n上一交易日出现 Wind capture EINTR 警告：需盘中复核是否丢帧"));
        logs->setReadOnly(true);
        tabs->addTab(logs, QStringLiteral("服务日志"));
        return moduleShell(QStringLiteral("实时申购赎回数据监控"), QStringLiteral("PyQt6/FastAPI sidecar + Wind probe + QMT"), tabs);
    }

    QListWidget *navigation_{};
    QStackedWidget *pages_{};
};

} // namespace

int main(int argc, char *argv[]) {
    QApplication application(argc, argv);
    application.setApplicationName(QStringLiteral("Machome Hub Prototype"));
    application.setOrganizationName(QStringLiteral("Ellis"));
    if (auto *style = QStyleFactory::create(QStringLiteral("Fusion"))) {
        application.setStyle(style);
    }
    MainWindow window;
    window.show();
    return application.exec();
}

