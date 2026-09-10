#include "ui/ServiceOverview.h"
#include "ui/UiText.h"
#include "ui/UiWidgets.h"
#include <QPushButton>
#include "common/JsonUtil.h"
#include <QFrame>
#include <QGridLayout>
#include <QHeaderView>
#include <QJsonArray>
#include <QLabel>
#include <QScrollArea>
#include <QStyle>
#include <QTableWidget>
#include <QToolButton>
#include <QTreeWidget>
#include <QVBoxLayout>

namespace hub {
namespace {
QLabel *label(const QString &text, const QString &name) {
    auto *result = new QLabel(text);
    result->setObjectName(name);
    result->setWordWrap(true);
    result->setTextFormat(Qt::PlainText);
    result->setTextInteractionFlags(Qt::TextSelectableByMouse);
    return result;
}
QFrame *panel(const QString &name) {
    auto *result = new QFrame;
    result->setObjectName(name);
    return result;
}
QString count(const QJsonValue &value) {
    return value.isDouble() ? QString::number(value.toInteger()) : QStringLiteral("—");
}
QString ratio(const QJsonValue &numerator, const QJsonValue &denominator) {
    return count(numerator) + QStringLiteral(" / ") + count(denominator);
}
QString boolean(const QJsonValue &value, const QString &yes, const QString &no) {
    return value.isBool() ? (value.toBool() ? yes : no) : QStringLiteral("待确认");
}
QJsonValue first(const QJsonObject &object, const QStringList &paths) {
    for (const auto &path : paths) {
        const auto value = valueAt(object, path);
        if (!value.isNull() && !value.isUndefined()) return value;
    }
    return QJsonValue(QJsonValue::Undefined);
}
}

ServiceOverview::ServiceOverview(QString adapter, QWidget *parent)
    : QWidget(parent), adapter_(std::move(adapter)) {
    setObjectName(QStringLiteral("serviceOverview"));
    auto *outer = new QVBoxLayout(this);
    outer->setContentsMargins(0, 0, 0, 0);
    auto *scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    auto *body = new QWidget;
    bodyLayout_ = new QVBoxLayout(body);
    bodyLayout_->setContentsMargins(1, 6, 1, 6);
    bodyLayout_->setSpacing(10);
    auto *metrics = new QGridLayout;
    metrics->setHorizontalSpacing(12);
    for (int i = 0; i < 4; ++i) {
        auto *card = panel(QStringLiteral("metricCard"));
        auto *layout = new QVBoxLayout(card);
        layout->setContentsMargins(12, 10, 12, 10);
        layout->setSpacing(5);
        captions_[i] = label({}, QStringLiteral("metricCaption"));
        values_[i] = label(QStringLiteral("—"), QStringLiteral("metricValue"));
        values_[i]->setProperty("metricIndex", i);
        hints_[i] = label({}, QStringLiteral("metricHint"));
        layout->addWidget(captions_[i]);
        layout->addWidget(values_[i]);
        layout->addWidget(hints_[i]);
        metrics->addWidget(card, 0, i);
        metrics->setColumnStretch(i, 1);
    }
    bodyLayout_->addLayout(metrics);
    notice_ = panel(QStringLiteral("noticePanel"));
    auto *noticeLayout = new QVBoxLayout(notice_);
    noticeLayout->setContentsMargins(18, 14, 18, 14);
    noticeTitle_ = label(QStringLiteral("等待服务状态"), QStringLiteral("sectionTitle"));
    noticeText_ = label(QStringLiteral("连接运行中心后，这里会显示需要关注的情况。"), QStringLiteral("noticeText"));
    noticeLayout->addWidget(noticeTitle_);
    noticeLayout->addWidget(noticeText_);
    bodyLayout_->addWidget(notice_);

    auto *columns = new QVBoxLayout;
    columns->setSpacing(16);
    auto *list = panel(QStringLiteral("overviewPanel"));
    auto *listLayout = new QVBoxLayout(list);
    listLayout->setContentsMargins(18, 16, 18, 16);
    listTitle_ = label({}, QStringLiteral("sectionTitle"));
    listLayout->addWidget(listTitle_);
    table_ = new QTableWidget(0, 3);
    table_->setObjectName(QStringLiteral("overviewStatusTable"));
    table_->setHorizontalHeaderLabels({QStringLiteral("项目"), QStringLiteral("状态"), QStringLiteral("补充信息")});
    table_->verticalHeader()->hide();
    table_->verticalHeader()->setDefaultSectionSize(37);
    table_->setShowGrid(false);
    table_->setWordWrap(false);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table_->setSelectionBehavior(QAbstractItemView::SelectRows);
    table_->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    table_->setMinimumHeight(80);
    listLayout->addWidget(table_);
    table_->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    more_=new QToolButton;more_->setText(QStringLiteral("显示全部项目"));more_->setCheckable(true);listLayout->addWidget(more_,0,Qt::AlignLeft);
    connect(more_,&QToolButton::toggled,this,[this](bool on){showAll_=on;more_->setText(on?QStringLiteral("收起项目"):QStringLiteral("显示全部项目"));applySnapshot(lastPayload_);});
    columns->addWidget(list);
    auto *facts = panel(QStringLiteral("overviewPanel"));
    auto *factsLayout = new QGridLayout(facts);
    factsLayout->setContentsMargins(20, 16, 20, 16);
    factsLayout->setSpacing(12);
    factsLayout->addWidget(label(QStringLiteral("运行信息"), QStringLiteral("sectionTitle")),0,0,1,3);
    for (int i = 0; i < 6; ++i) {
        auto *pair = new QVBoxLayout;
        pair->setSpacing(4);
        factNames_[i] = label({}, QStringLiteral("metricCaption"));
        facts_[i] = label({}, QStringLiteral("factValue"));
        pair->addWidget(factNames_[i]);
        pair->addWidget(facts_[i]);
        factsLayout->addLayout(pair,1+i/3,i%3);
    }
    columns->addWidget(facts);
    bodyLayout_->addLayout(columns);
    bodyLayout_->addStretch();
    scroll->setWidget(body);
    outer->addWidget(scroll);
    applySnapshot({});
}

void ServiceOverview::metric(int index, const QString &caption, const QString &value, const QString &hint) {
    captions_[index]->setText(caption);
    values_[index]->setText(value);
    hints_[index]->setText(hint);
}

void ServiceOverview::setDiagnostics(QTreeWidget *tree) {
    auto *box = panel(QStringLiteral("overviewPanel"));
    auto *layout = new QVBoxLayout(box);
    layout->setContentsMargins(14, 10, 14, 10);
    auto *toggle = new QToolButton;
    toggle->setObjectName(QStringLiteral("diagnosticsToggle"));
    toggle->setText(QStringLiteral("诊断详情 · 查看全部字段"));
    toggle->setCheckable(true);
    toggle->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    toggle->setArrowType(Qt::RightArrow);
    layout->addWidget(toggle, 0, Qt::AlignLeft);
    tree->setMinimumHeight(180);tree->setMaximumHeight(300);
    tree->hide();
    layout->addWidget(tree);
    connect(toggle, &QToolButton::toggled, tree, [tree, toggle](bool expanded) {
        tree->setVisible(expanded);
        toggle->setArrowType(expanded ? Qt::DownArrow : Qt::RightArrow);
    });
    bodyLayout_->insertWidget(bodyLayout_->count() - 1, box);
}

void ServiceOverview::applySnapshot(const QJsonObject &p) {
    lastPayload_=p;
    const auto t = p.value("telemetry").toObject();
    const auto engine = t.value("engine").toObject();
    const auto status = t.value("status").toObject();
    QList<QStringList> rows;
    QList<QPair<QString, QString>> facts;
    QStringList problems;
    const auto error = p.value("last_error").toString();
    if (!error.isEmpty()) problems << ui::problemText(error);
    QString note;
    const auto operating = first(t, {"engine.operating_mode", "status.operating_mode", "snapshot.operating_mode"});
    facts.append({QStringLiteral("运行模式"), ui::valueText(operating)});

    if (adapter_ == "upload") {
        const auto workersValue = t.value("workers");
        const auto workers = workersValue.toArray();
        int running = 0, ready = 0, confirmed = 0;
        for (const auto &v : workers) {
            const auto worker = v.toObject();
            if (worker.value("state").toString() == "running") ++running;
            if (worker.value("ready").toBool()) ++ready;
            if (!worker.value("last_success_at").toString().isEmpty()) ++confirmed;
            rows.append({ui::taskText(worker.value("id").toString()),
                         ui::stateText(worker.value("state").toString()),
                         worker.value("last_success_at").toString().isEmpty()
                             ? QStringLiteral("尚无接收确认记录")
                             : QStringLiteral("确认于 ") + ui::localTimeText(worker.value("last_success_at").toString())});
        }
        const auto problemsValue = engine.value("readiness_problems");
        for (const auto &v : problemsValue.toArray()) problems << ui::problemText(v.toString());
        metric(0, QStringLiteral("运行任务"), workersValue.isArray() ? QStringLiteral("%1 / %2").arg(running).arg(workers.size()) : QStringLiteral("—"), QStringLiteral("进程运行数 / 任务总数"));
        metric(1, QStringLiteral("业务就绪"), workersValue.isArray() ? QStringLiteral("%1 / %2").arg(ready).arg(workers.size()) : QStringLiteral("—"), QStringLiteral("以服务端就绪检查为准"));
        metric(2, QStringLiteral("有确认记录"), workersValue.isArray() ? QString::number(confirmed) : QStringLiteral("—"), QStringLiteral("有上次成功时间的任务"));
        metric(3, QStringLiteral("待关注检查"), problemsValue.isArray() ? QString::number(problemsValue.toArray().size()) : QStringLiteral("—"), QStringLiteral("缺失或过期的确认需核对"));
        listTitle_->setText(QStringLiteral("上传任务概况"));
        facts.append({QStringLiteral("业务输出"), boolean(engine.value("record_only"), QStringLiteral("仅记录"), QStringLiteral("按生产配置输出"))});
        facts.append({QStringLiteral("任务安排"), boolean(t.value("workers_expected"), QStringLiteral("当前应运行"), QStringLiteral("允许按计划休息"))});
        facts.append({QStringLiteral("业务方式"), ui::valueText(engine.value("sink_mode"))});
        facts.append({QStringLiteral("盈透行情连接"), boolean(valueAt(engine, "ibkr.handshake_complete"), QStringLiteral("握手完成"), QStringLiteral("尚未握手"))});
        facts.append({QStringLiteral("业务就绪"), boolean(engine.value("ready"), QStringLiteral("检查通过"), QStringLiteral("检查尚未通过"))});
        note = QStringLiteral("进程运行不等于数据已送达。接收确认的时间和有效性，请结合“上传任务”查看。");
        if (engine.value("collection_expected").isBool() && !engine.value("collection_expected").toBool())
            note = QStringLiteral("行情上传已按时段休眠；网站、缓存和日终任务继续服务。运行任务数表示常驻进程数，不代表仍在采集。");
    } else if (adapter_ == "premium") {
        const bool resting = status.value("cn_quotes_desired").isBool()
            && status.value("hk_quotes_desired").isBool()
            && !status.value("cn_quotes_desired").toBool()
            && !status.value("hk_quotes_desired").toBool();
        metric(0, QStringLiteral("观察标的行情"), ratio(status.value("ready_symbols"), status.value("watchlist_symbols")), QStringLiteral("已收到行情 / 观察标的"));
        metric(1, QStringLiteral("额外基础行情"), ratio(status.value("l1_hot_ready"), status.value("l1_hot_symbols")), QStringLiteral("已就绪 / 额外标的"));
        metric(2, QStringLiteral("行情源连接"), boolean(status.value("adapter_connected"), QStringLiteral("已连接"), QStringLiteral("未连接")), QStringLiteral("连接与数据健康独立检查"));
        metric(3, QStringLiteral("概览客户端"), count(status.value("summary_clients")), QStringLiteral("当前订阅拉涨与溢价的客户端"));
        listTitle_->setText(QStringLiteral("行情链路"));
        rows.append({QStringLiteral("行情源"), resting ? QStringLiteral("按计划休眠") : boolean(status.value("upstream_healthy"), QStringLiteral("数据正常"), QStringLiteral("数据异常")), ui::valueText(status.value("upstream_status"))});
        rows.append({QStringLiteral("拉涨信号"), boolean(status.value("signals_enabled"), QStringLiteral("允许生成"), QStringLiteral("当前不生成")), ui::valueText(status.value("phase"))});
        rows.append({QStringLiteral("详情通道"), ui::valueText(valueAt(t, "detail_channel.state")), count(status.value("detail_clients")) + QStringLiteral(" 个客户端")});
        rows.append({QStringLiteral("基础行情通道"), count(status.value("l1_clients")) + QStringLiteral(" 个客户端"), QStringLiteral("向客户端提供基础盘口")});
        rows.append({QStringLiteral("历史记录"), boolean(status.value("historical_writes_stopped"), QStringLiteral("写入已暂停"), QStringLiteral("写入未暂停")), count(status.value("persistence_queue_depth")) + QStringLiteral(" 条待写入")});
        facts.append({QStringLiteral("当前时段"), ui::valueText(status.value("phase"))});
        facts.append({QStringLiteral("回放模式"), boolean(status.value("replay"), QStringLiteral("正在回放"), QStringLiteral("未开启"))});
        facts.append({QStringLiteral("处理耗时"), ui::valueText(status.value("core_latency_ms")) + QStringLiteral(" 毫秒")});
        facts.append({QStringLiteral("行情序号缺口"), count(status.value("adapter_gaps"))});
        facts.append({QStringLiteral("已隔离异常报文"), count(status.value("quarantined"))});
        if (!resting && status.value("upstream_healthy").isBool() && !status.value("upstream_healthy").toBool()) problems << QStringLiteral("行情源未通过健康检查，请查看行情采集状态。");
        if (status.value("historical_writes_stopped").toBool()) problems << QStringLiteral("历史写入已暂停，请检查磁盘与写入队列。");
        note = QStringLiteral("信号是否生成由当前时段决定；午间或盘外仍可能继续接收行情。");
    } else if (adapter_ == "webull") {
        metric(0, QStringLiteral("Webull 登录"), ui::valueText(first(status, {"auth_state", "auth.state", "auth"})), QStringLiteral("账号会话状态"));
        metric(1, QStringLiteral("行情采集"), boolean(status.value("collector_running"), QStringLiteral("采集中"), QStringLiteral("未采集")), QStringLiteral("浏览器行情采集器"));
        metric(2, QStringLiteral("行情数据"), ui::valueText(first(status, {"data_state", "data.state", "data"})), QStringLiteral("数据就绪与登录状态分开判断"));
        metric(3, QStringLiteral("接收客户端"), count(first(t, {"client_count", "status.client_count"})), QStringLiteral("当前连接的行情使用方"));
        listTitle_->setText(QStringLiteral("采集与转发链路"));
        rows.append({QStringLiteral("行情接口"), boolean(status.value("api_live"), QStringLiteral("在线"), QStringLiteral("离线")), ui::valueText(status.value("api_address"))});
        rows.append({QStringLiteral("浏览器"), ui::valueText(first(status, {"browser_state", "browser.state", "browser"})), boolean(status.value("browser_visible"), QStringLiteral("窗口可见"), QStringLiteral("窗口未显示"))});
        rows.append({QStringLiteral("行情流"), boolean(status.value("stream_connected"), QStringLiteral("已连接"), QStringLiteral("未连接")), boolean(status.value("polling_fallback"), QStringLiteral("备用轮询已开启"), QStringLiteral("备用轮询未开启"))});
        rows.append({QStringLiteral("有效行情响应"), count(status.value("valid_responses")), QStringLiteral("累计接收到的有效数据")});
        rows.append({QStringLiteral("无效行情响应"), count(status.value("invalid_responses")), QStringLiteral("累计未通过解析的数据")});
        facts.append({QStringLiteral("采集模式"), ui::valueText(status.value("schedule_mode"))});
        facts.append({QStringLiteral("当前安排"), ui::valueText(status.value("schedule_message"))});
        facts.append({QStringLiteral("最近盘口"), ui::localTimeText(status.value("last_depth_at").toString())});
        facts.append({QStringLiteral("下次时段切换"), ui::localTimeText(status.value("next_transition").toString())});
        facts.append({QStringLiteral("采集检查时间"), ui::localTimeText(status.value("observed_at").toString())});
        const auto auth = first(status, {"auth_state", "auth.state", "auth"}).toString();
        if (auth == "login_required" || auth == "unauthenticated") problems << QStringLiteral("Webull 尚未登录，请在“采集与登录”打开登录窗口。");
        if (first(status, {"data_state", "data.state", "data"}).toString() == "no_data")
            note = QStringLiteral("尚未收到有效行情。休市时可能没有更新，请结合交易时段判断；登录成功不代表已有盘口。");
        else note = QStringLiteral("查看实时盘口可核对行情内容；登录与采集操作位于“采集与登录”。");
    } else {
        auto snapshot = t.value("snapshot").toObject();
        if (snapshot.isEmpty()) snapshot = engine;
        const bool resting = snapshot.value("operating_mode").toString() == "work"
            && valueAt(snapshot, "schedule.monitoring_desired").isBool()
            && !valueAt(snapshot, "schedule.monitoring_desired").toBool()
            && !snapshot.value("monitoring").toBool();
        const auto itemsValue = snapshot.value("items");
        const auto items = itemsValue.toArray();
        int pcfReady = 0;
        for (const auto &v : items) {
            const auto item = v.toObject();
            const auto pcf = item.value("pcf").toObject();
            if (pcf.value("status").toString() == "ready") ++pcfReady;
            rows.append({item.value("symbol").toString() + "  " + item.value("name").toString(),
                         ui::valueText(item.value("status")), QStringLiteral("清单：") + ui::valueText(pcf.value("status"))});
        }
        metric(0, QStringLiteral("份额监控"), resting ? QStringLiteral("按计划休眠") : boolean(snapshot.value("monitoring"), QStringLiteral("监控中"), QStringLiteral("未监控")), QStringLiteral("实时申购与赎回份额变化"));
        metric(1, QStringLiteral("监控标的"), itemsValue.isArray() ? QString::number(items.size()) : count(t.value("symbol_count")), QStringLiteral("观察列表中的 ETF"));
        metric(2, QStringLiteral("申赎清单就绪"), itemsValue.isArray() ? QStringLiteral("%1 / %2").arg(pcfReady).arg(items.size()) : QStringLiteral("—"), QStringLiteral("服务端判定为就绪的清单"));
        metric(3, QStringLiteral("Wind 数据源"), ui::valueText(first(t, {"health.wind_helper_state", "snapshot.wind.state", "engine.wind.state"})), QStringLiteral("采集进程与数据订阅状态"));
        const auto windRunning = valueAt(snapshot, "wind.running");
        if (resting && windRunning.isBool()) {
            metric(3, QStringLiteral("Wind 金融终端"), windRunning.toBool()
                ? QStringLiteral("仍在运行") : QStringLiteral("已关闭"), QStringLiteral("休眠期间应退出，查询服务继续在线"));
            if (windRunning.toBool()) problems << QStringLiteral("当前已停采，但 Wind 仍在运行；请检查自动关闭结果，或使用上方“关闭 Wind 并清理临时探针”。");
        }
        listTitle_->setText(QStringLiteral("监控与清单概况"));
        facts.append({QStringLiteral("当前时段"), ui::valueText(first(snapshot, {"schedule.phase", "state"}))});
        const auto backends = t.value("qmt_backends").toObject();
        for (const auto &key : {QStringLiteral("QMT1"), QStringLiteral("QMT2")}) {
            const auto backend = backends.value(key).toObject();
            const bool onDemand = backend.value("connection_policy").toString() == "on_demand"
                && backend.value("connection_state").toString() == "disconnected";
            facts.append({key + QStringLiteral(" 数据连接"), onDemand
                ? QStringLiteral("按需连接（默认不连接）") : ui::valueText(backend.value("connection_state"))});
        }
        facts.append({QStringLiteral("Wind 接口"), boolean(valueAt(snapshot, "wind.tbapi_loaded"), QStringLiteral("已加载"), QStringLiteral("未加载"))});
        facts.append({QStringLiteral("服务状态更新"), ui::localTimeText(snapshot.value("server_time").toString())});
        if (!resting && valueAt(t, "health.wind_helper_ok").isBool() && !valueAt(t, "health.wind_helper_ok").toBool()) problems << QStringLiteral("Wind 采集尚未就绪，请检查数据源与订阅状态。");
        note = QStringLiteral("首份累计数据用作基准；后续份额变化显示在“实时监控”，清单详情可从标的进入。");
        if (resting) note = QStringLiteral("当前按计划停止份额采集，历史数据和申赎清单仍可查询。服务端默认不连接 QMT，客户端交易不受此设置影响。");
    }
    for (int i = 0; i < 6; ++i) {
        factNames_[i]->setText(i < facts.size() ? facts[i].first : QString());
        facts_[i]->setText(i < facts.size() ? facts[i].second : QString());
    }
    const bool waiting = t.isEmpty();
    const bool warning = !problems.isEmpty() || ui::stateStyle(p) == "stateWarn";
    notice_->setProperty("tone", warning ? "warning" : "info");
    notice_->style()->unpolish(notice_);
    notice_->style()->polish(notice_);
    noticeTitle_->setText(waiting ? QStringLiteral("等待服务状态") : warning ? QStringLiteral("需要关注") : QStringLiteral("当前运行说明"));
    if (waiting) note = QStringLiteral("尚未收到业务快照，暂不判断服务是否正常。");
    else if (warning && problems.isEmpty()) note.prepend(QStringLiteral("服务端报告部分异常，请结合下方状态核对。\n"));
    noticeText_->setText(problems.isEmpty() ? note : problems.mid(0,3).join(QStringLiteral("\n")) + (problems.size()>3 ? QStringLiteral("\n另有 %1 项，请查看诊断详情。").arg(problems.size()-3):QString()));
    noticeText_->setToolTip(problems.join(QStringLiteral("\n")));
    const int visibleRows=showAll_?rows.size():qMin(6,rows.size());
    more_->setVisible(rows.size()>6);
    table_->setRowCount(visibleRows);
    for (int row = 0; row < visibleRows; ++row) {
        for (int column = 0; column < 3; ++column) {
            auto *item = table_->item(row, column);
            if (!item) { item = new QTableWidgetItem; table_->setItem(row, column, item); }
            const auto text = rows[row].value(column);
            item->setText(text);
            item->setToolTip(text);
        }
    }
    table_->setFixedHeight(table_->horizontalHeader()->height()+qMax(1,visibleRows)*37+4);
}
}
