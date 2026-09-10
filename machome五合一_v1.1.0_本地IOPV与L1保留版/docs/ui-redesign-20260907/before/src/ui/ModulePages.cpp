#include "AlertController.h"
#include "ui/ModulePages.h"

#include "common/JsonUtil.h"
#include "ui/PremiumHistory.h"

#include <QComboBox>
#include <QCheckBox>
#include <QCloseEvent>
#include <QDate>
#include <QDateEdit>
#include <QDateTime>
#include <QDesktopServices>
#include <QEvent>
#include <QFormLayout>
#include <QFileDialog>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QInputDialog>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QRegularExpression>
#include <QStyle>
#include <QSettings>
#include <QTableWidget>
#include <QTabWidget>
#include <QTextDocument>
#include <QTimer>
#include <QTreeWidget>
#include <QUrl>
#include <QVBoxLayout>

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace hub {
namespace {

QLabel *textLabel(const QString &text, const QString &name = {}) {
    auto *label = new QLabel(text);
    label->setObjectName(name);
    label->setWordWrap(true);
    return label;
}

QString operatorStateText(const QString &raw) {
    static const QHash<QString, QString> names{
        {QStringLiteral("unknown"), QStringLiteral("未知")},
        {QStringLiteral("no_data"), QStringLiteral("暂无数据")},
        {QStringLiteral("scheduled_idle"), QStringLiteral("计划空闲")},
        {QStringLiteral("idle"), QStringLiteral("空闲")},
        {QStringLiteral("running"), QStringLiteral("运行中")},
        {QStringLiteral("active"), QStringLiteral("活跃")},
        {QStringLiteral("warming"), QStringLiteral("预热中")},
        {QStringLiteral("starting"), QStringLiteral("正在启动")},
        {QStringLiteral("initialized"), QStringLiteral("已初始化")},
        {QStringLiteral("stopped"), QStringLiteral("已停止")},
        {QStringLiteral("blocked"), QStringLiteral("已阻止")},
        {QStringLiteral("degraded"), QStringLiteral("降级")},
        {QStringLiteral("connecting"), QStringLiteral("连接中")},
        {QStringLiteral("connected"), QStringLiteral("已连接")},
        {QStringLiteral("disconnected"), QStringLiteral("已断开")},
        {QStringLiteral("reconnecting"), QStringLiteral("重连中")},
        {QStringLiteral("ready"), QStringLiteral("就绪")},
        {QStringLiteral("fresh"), QStringLiteral("数据新鲜")},
        {QStringLiteral("stale"), QStringLiteral("数据过期")},
        {QStringLiteral("flowing"), QStringLiteral("数据流入中")},
        {QStringLiteral("error"), QStringLiteral("错误")},
        {QStringLiteral("disabled"), QStringLiteral("已禁用")},
        {QStringLiteral("enabled"), QStringLiteral("已启用")},
        {QStringLiteral("missing"), QStringLiteral("缺失")},
        {QStringLiteral("configuration_error"), QStringLiteral("配置错误")},
        {QStringLiteral("pending"), QStringLiteral("待处理")},
        {QStringLiteral("accepted"), QStringLiteral("已受理")},
        {QStringLiteral("succeeded"), QStringLiteral("成功")},
        {QStringLiteral("failed"), QStringLiteral("失败")},
        {QStringLiteral("timed_out"), QStringLiteral("超时")},
        {QStringLiteral("auto"), QStringLiteral("自动")},
        {QStringLiteral("force_running"), QStringLiteral("强制运行")},
        {QStringLiteral("force_stopped"), QStringLiteral("强制停止")},
        {QStringLiteral("work"), QStringLiteral("工作模式")},
        {QStringLiteral("weekend_test"), QStringLiteral("周末测试模式")},
        {QStringLiteral("authenticated"), QStringLiteral("已登录")},
        {QStringLiteral("unauthenticated"), QStringLiteral("未登录")},
        {QStringLiteral("online"), QStringLiteral("在线")},
        {QStringLiteral("offline"), QStringLiteral("离线")},
    };
    const QString normalized = raw.trimmed().toLower();
    return names.value(normalized, raw.isEmpty() ? QStringLiteral("—") : raw);
}

QString operatorFieldText(const QString &raw) {
    static const QHash<QString, QString> names{
        {QStringLiteral("state"), QStringLiteral("状态")},
        {QStringLiteral("work_state"), QStringLiteral("工作状态")},
        {QStringLiteral("lifecycle"), QStringLiteral("生命周期")},
        {QStringLiteral("ready"), QStringLiteral("就绪")},
        {QStringLiteral("running"), QStringLiteral("运行中")},
        {QStringLiteral("monitoring"), QStringLiteral("监控中")},
        {QStringLiteral("record_only"), QStringLiteral("仅记录")},
        {QStringLiteral("last_error"), QStringLiteral("最近错误")},
        {QStringLiteral("browser"), QStringLiteral("浏览器")},
        {QStringLiteral("auth"), QStringLiteral("登录认证")},
        {QStringLiteral("data"), QStringLiteral("行情数据")},
        {QStringLiteral("connection_state"), QStringLiteral("连接状态")},
        {QStringLiteral("collector_running"), QStringLiteral("采集器运行中")},
        {QStringLiteral("schedule_mode"), QStringLiteral("调度模式")},
        {QStringLiteral("operating_mode"), QStringLiteral("运行模式")},
        {QStringLiteral("live_browser_enabled"), QStringLiteral("实时浏览器已启用")},
        {QStringLiteral("socket_connected"), QStringLiteral("本地通道已连接")},
        {QStringLiteral("handshake_complete"), QStringLiteral("握手已完成")},
        {QStringLiteral("wind_helper_state"), QStringLiteral("Wind 探针状态")},
        {QStringLiteral("live_orders_allowed"), QStringLiteral("实盘下单允许")},
        {QStringLiteral("notifications_enabled"), QStringLiteral("外部通知已启用")},
    };
    const QString translated = names.value(raw);
    return translated.isEmpty() ? raw : QStringLiteral("%1 (%2)").arg(translated, raw);
}

QString operatorValueText(const QJsonValue &value) {
    if (value.isBool()) return value.toBool() ? QStringLiteral("是") : QStringLiteral("否");
    if (value.isString()) return operatorStateText(value.toString());
    return displayValue(value);
}

QTableWidget *makeTable(const QStringList &headers) {
    auto *table = new QTableWidget(0, headers.size());
    table->setHorizontalHeaderLabels(headers);
    table->verticalHeader()->setVisible(false);
    table->setAlternatingRowColors(true);
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->horizontalHeader()->setStretchLastSection(true);
    table->horizontalHeader()->setSectionResizeMode(QHeaderView::Interactive);
    return table;
}

void setCell(QTableWidget *table, int row, int column, const QString &value,
             const QJsonObject &raw = {}) {
    auto *item = table->item(row, column);
    if (!item) {
        item = new QTableWidgetItem;
        table->setItem(row, column, item);
    }
    if (item->text() != value) item->setText(value);
    if (!raw.isEmpty()) {
        const QString encoded = QString::fromUtf8(
            QJsonDocument(raw).toJson(QJsonDocument::Compact));
        if (item->data(Qt::UserRole).toString() != encoded) {
            item->setData(Qt::UserRole, encoded);
        }
    }
}

QString firstValue(const QJsonObject &object, const QStringList &keys, const QString &fallback = QStringLiteral("—")) {
    for (const auto &key : keys) {
        const auto value = valueAt(object, key);
        if (!value.isUndefined() && !value.isNull()) return displayValue(value, fallback);
    }
    return fallback;
}

QJsonArray arrayCandidate(const QJsonValue &value) {
    if (value.isArray()) return value.toArray();
    if (!value.isObject()) return {};
    const auto object = value.toObject();
    for (const auto &key : {QStringLiteral("items"), QStringLiteral("rows"), QStringLiteral("data"),
                            QStringLiteral("symbols"), QStringLiteral("clients"), QStringLiteral("history")}) {
        if (object.value(key).isArray()) return object.value(key).toArray();
    }
    QJsonArray result;
    for (auto it = object.begin(); it != object.end(); ++it) {
        if (it.value().isObject()) {
            auto row = it.value().toObject();
            if (!row.contains(QStringLiteral("symbol"))) row.insert(QStringLiteral("symbol"), it.key());
            result.append(row);
        }
    }
    return result;
}

void addTreeValue(QTreeWidgetItem *parent, const QString &key, const QJsonValue &value) {
    if (value.isObject()) {
        auto *item = new QTreeWidgetItem(parent, {operatorFieldText(key), QStringLiteral("{…}")});
        const auto object = value.toObject();
        for (auto it = object.begin(); it != object.end(); ++it) addTreeValue(item, it.key(), it.value());
        return;
    }
    if (value.isArray()) {
        auto *item = new QTreeWidgetItem(parent, {operatorFieldText(key), QStringLiteral("[%1]").arg(value.toArray().size())});
        int index = 0;
        for (const auto &entry : value.toArray()) addTreeValue(item, QString::number(index++), entry);
        return;
    }
    new QTreeWidgetItem(parent, {operatorFieldText(key), operatorValueText(value)});
}

void fillTree(QTreeWidget *tree, const QJsonObject &object) {
    tree->setUpdatesEnabled(false);
    tree->clear();
    auto *root = tree->invisibleRootItem();
    for (auto it = object.begin(); it != object.end(); ++it) addTreeValue(root, it.key(), it.value());
    tree->expandToDepth(1);
    tree->resizeColumnToContents(0);
    tree->setUpdatesEnabled(true);
}

QTreeWidget *makeTree() {
    auto *tree = new QTreeWidget;
    tree->setHeaderLabels({QStringLiteral("字段"), QStringLiteral("值")});
    tree->setAlternatingRowColors(true);
    return tree;
}

QString pretty(const QJsonValue &value) {
    if (value.isObject()) return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Indented));
    if (value.isArray()) return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Indented));
    return displayValue(value);
}

QJsonObject eventPayload(const QJsonObject &message) {
    return message.value(QStringLiteral("payload")).toObject();
}

QStringList lines(const QPlainTextEdit *edit) {
    QStringList result;
    for (const auto &line : edit->toPlainText().split(u'\n')) {
        const QString value = line.trimmed();
        if (!value.isEmpty() && !value.startsWith(u'#')) result.push_back(value);
    }
    return result;
}

bool finiteNumber(const QJsonValue &value, double *result = nullptr) {
    if (!value.isDouble()) return false;
    const double number = value.toDouble();
    if (!std::isfinite(number)) return false;
    if (result) *result = number;
    return true;
}

QString groupedInteger(qint64 value, bool signedPositive = false) {
    QString digits = QString::number(value);
    const bool negative = digits.startsWith(u'-');
    if (negative) digits.remove(0, 1);
    for (int index = digits.size() - 4; index > 0; index -= 4) digits.insert(index, u' ');
    if (negative) return u'-' + digits;
    return signedPositive && value > 0 ? u'+' + digits : digits;
}

QString shareText(const QJsonValue &value, bool signedPositive = false) {
    double number = 0.0;
    if (!finiteNumber(value, &number)
        || number < static_cast<double>(std::numeric_limits<qint64>::min())
        || number > static_cast<double>(std::numeric_limits<qint64>::max())) {
        return QStringLiteral("—");
    }
    return groupedInteger(static_cast<qint64>(std::llround(number)), signedPositive);
}

QString basketText(const QJsonObject &item, const QString &field, bool signedPositive = false) {
    const auto values = item.value(QStringLiteral("values")).toObject();
    const auto pcf = item.value(QStringLiteral("pcf")).toObject();
    double amount = 0.0;
    double unit = 0.0;
    if (pcf.value(QStringLiteral("status")).toString() != QStringLiteral("ready")
        || !finiteNumber(values.value(field), &amount)
        || !finiteNumber(pcf.value(QStringLiteral("creation_redemption_unit")), &unit)
        || unit <= 0.0) {
        return QStringLiteral("待确认");
    }
    const double baskets = amount / unit;
    const qint64 rounded = static_cast<qint64>(std::llround(baskets));
    if (std::abs(baskets - static_cast<double>(rounded)) < 1e-9) {
        return groupedInteger(rounded, signedPositive);
    }
    QString text = QString::number(std::abs(baskets), 'f', 2);
    while (text.endsWith(u'0')) text.chop(1);
    if (text.endsWith(u'.')) text.chop(1);
    if (baskets < 0.0) return u'-' + text;
    return signedPositive && baskets > 0.0 ? u'+' + text : text;
}

QString availableCreationBaskets(const QJsonObject &item) {
    for (const auto &key : {QStringLiteral("available_creation_baskets"),
                            QStringLiteral("creation_baskets")}) {
        const auto legacy = item.value(key);
        if (!legacy.isUndefined() && !legacy.isNull()) return displayValue(legacy);
    }

    const auto values = item.value(QStringLiteral("values")).toObject();
    const auto pcf = item.value(QStringLiteral("pcf")).toObject();
    double creation = 0.0;
    double net = 0.0;
    double creationLimit = 0.0;
    double netCreationLimit = 0.0;
    if (pcf.value(QStringLiteral("status")).toString() != QStringLiteral("ready")
        || !finiteNumber(values.value(QStringLiteral("etfbuyamount")), &creation)
        || !finiteNumber(values.value(QStringLiteral("netamount")), &net)
        || !finiteNumber(pcf.value(QStringLiteral("creation_limit")), &creationLimit)
        || !finiteNumber(pcf.value(QStringLiteral("net_creation_limit")), &netCreationLimit)
        || !pcf.value(QStringLiteral("creation_allowed")).isBool()) {
        return QStringLiteral("待确认");
    }
    if (!pcf.value(QStringLiteral("creation_allowed")).toBool()
        || (creationLimit > 0.0 && creation >= creationLimit)
        || (netCreationLimit > 0.0 && net >= netCreationLimit)) {
        return QStringLiteral("0");
    }
    if (creationLimit <= 0.0 && netCreationLimit <= 0.0) return QStringLiteral("不限");

    double remaining = std::numeric_limits<double>::infinity();
    if (creationLimit > 0.0) remaining = std::min(remaining, std::max(0.0, creationLimit - creation));
    if (netCreationLimit > 0.0) remaining = std::min(remaining, std::max(0.0, netCreationLimit - net));
    double unit = 0.0;
    if (!finiteNumber(pcf.value(QStringLiteral("creation_redemption_unit")), &unit) || unit <= 0.0) {
        return QStringLiteral("待确认");
    }
    return groupedInteger(static_cast<qint64>(std::floor(remaining / unit + 1e-9)));
}

QString realtimeStatusText(const QJsonValue &value) {
    const QString state = value.toString();
    static const QHash<QString, QString> names{
        {QStringLiteral("waiting"), QStringLiteral("等待数据")},
        {QStringLiteral("connecting"), QStringLiteral("连接中")},
        {QStringLiteral("monitoring"), QStringLiteral("监控中")},
        {QStringLiteral("cached"), QStringLiteral("缓存数据")},
        {QStringLiteral("stopped"), QStringLiteral("已停止")},
        {QStringLiteral("error"), QStringLiteral("错误")},
        {QStringLiteral("reconnecting"), QStringLiteral("重连中")},
    };
    return names.value(state, operatorStateText(state));
}

QString lastChangesText(const QJsonValue &value) {
    if (value.isString()) return value.toString().trimmed();
    if (!value.isArray()) return QStringLiteral("—");
    QStringList text;
    for (const auto &entry : value.toArray()) {
        if (!entry.isObject()) continue;
        const QString item = entry.toObject().value(QStringLiteral("text")).toString().trimmed();
        if (!item.isEmpty()) text.append(item);
    }
    return text.isEmpty() ? QStringLiteral("—") : text.join(QStringLiteral("；"));
}

QString realtimeSymbol(const QJsonObject &item) {
    QString symbol = item.value(QStringLiteral("symbol")).toString().trimmed().toUpper();
    if (symbol.isEmpty()) symbol = item.value(QStringLiteral("windcode")).toString().trimmed().toUpper();
    return symbol;
}

QString premiumPpmText(const QJsonObject &object, const QString &key) {
    if (!object.contains(key) || object.value(key).isNull()) return QStringLiteral("—");
    return QStringLiteral("%1%").arg(object.value(key).toInteger() / 10'000.0, 0, 'f', 3);
}

QString premiumModelText(const QString &model) {
    if (model.isEmpty()) return QStringLiteral("—");
    QStringList result;
    for (const QString &part : model.split(u'+', Qt::SkipEmptyParts)) {
        if (part == QStringLiteral("premium")) result.append(QStringLiteral("溢价率"));
        else if (part == QStringLiteral("pull")) result.append(QStringLiteral("盘口拉涨"));
        else if (part == QStringLiteral("radar")) result.append(QStringLiteral("快速拉涨雷达"));
        else result.append(part);
    }
    return result.join(QStringLiteral(" + "));
}

QStringList premiumHistoryValues(const QJsonObject &record) {
    const QDateTime occurred = QDateTime::fromString(
        record.value(QStringLiteral("occurred_at")).toString(), Qt::ISODateWithMs);
    const QString symbol = record.value(QStringLiteral("symbol")).toString();
    QString source = QStringLiteral("实时");
    if (record.contains(QStringLiteral("_audit_file"))) {
        source = QStringLiteral("%1:%2").arg(record.value(QStringLiteral("_audit_file")).toString())
                                           .arg(record.value(QStringLiteral("_audit_line")).toInteger());
    } else if (record.value(QStringLiteral("replay")).toBool()) {
        source = QStringLiteral("回放");
    } else if (record.value(QStringLiteral("backfill")).toBool()) {
        source = QStringLiteral("30分钟补发");
    }
    return {
        occurred.isValid() ? occurred.toString(QStringLiteral("MM-dd HH:mm:ss.zzz"))
                           : record.value(QStringLiteral("occurred_at")).toString(),
        symbol, record.value(QStringLiteral("name")).toString(symbol.left(6)),
        premiumModelText(record.value(QStringLiteral("model")).toString()),
        premiumPpmText(record, QStringLiteral("premium_ppm")),
        premiumPpmText(record, QStringLiteral("rise_30s_ppm")),
        premiumPpmText(record, QStringLiteral("rise_300s_ppm")),
        premiumPpmText(record, QStringLiteral("bid_rise_150s_ppm")),
        premiumPpmText(record, QStringLiteral("bid_rise_300s_ppm")),
        record.value(QStringLiteral("repeat")).toBool() ? QStringLiteral("重复提醒")
                                                         : QStringLiteral("首次触发"),
        source, record.value(QStringLiteral("reason")).toString(),
    };
}

QString baseSymbol(QString value) {
    value = value.trimmed().toUpper();
    if (value.size() >= 6) return value.left(6);
    return value;
}

bool belongsToSymbol(const QJsonObject &object, const QString &symbol) {
    const QString candidate = firstValue(object,
        {QStringLiteral("code"), QStringLiteral("symbol"), QStringLiteral("stock_code")}, {});
    return !candidate.isEmpty() && baseSymbol(candidate) == baseSymbol(symbol);
}

void showJsonDetail(QWidget *parent, const QString &title, const QJsonObject &object) {
    auto *window = new QWidget(parent, Qt::Window);
    window->setAttribute(Qt::WA_DeleteOnClose);
    window->setWindowTitle(title);
    window->resize(760, 620);
    auto *layout = new QVBoxLayout(window);
    auto *editor = new QPlainTextEdit(pretty(object));
    editor->setReadOnly(true);
    layout->addWidget(editor);
    window->show();
}

} // namespace

ModulePage::ModulePage(ModuleConfig config, QWidget *parent)
    : QWidget(parent), config_(std::move(config)) {}

QWidget *ModulePage::buildHeader(const QString &subtitle, const QList<QWidget *> &extraActions) {
    auto *widget = new QWidget;
    auto *layout = new QHBoxLayout(widget);
    layout->setContentsMargins(0, 0, 0, 0);
    auto *titles = new QVBoxLayout;
    titles->addWidget(textLabel(config_.displayName, QStringLiteral("pageTitle")));
    titles->addWidget(textLabel(subtitle, QStringLiteral("secondaryText")));
    auto *stateRow = new QHBoxLayout;
    stateLabel_ = textLabel(QStringLiteral("等待状态"), QStringLiteral("stateUnknown"));
    freshnessLabel_ = textLabel(QStringLiteral("尚未收到快照"), QStringLiteral("secondaryText"));
    stateRow->addWidget(stateLabel_);
    stateRow->addWidget(freshnessLabel_);
    stateRow->addStretch();
    titles->addLayout(stateRow);
    layout->addLayout(titles, 1);

    for (auto *action : extraActions) layout->addWidget(action);
    auto *refresh = new QPushButton(QStringLiteral("刷新"));
    connect(refresh, &QPushButton::clicked, this, [this] { emit refreshRequested(config_.id); });
    layout->addWidget(refresh);

    legacyButton_ = new QPushButton(QStringLiteral("旧界面"));
    legacyButton_->setVisible(!config_.settings.value(QStringLiteral("legacy_app")).toString().isEmpty());
    connect(legacyButton_, &QPushButton::clicked, this, [this] { send(QStringLiteral("open_legacy_ui")); });
    layout->addWidget(legacyButton_);

    acknowledgeButton_ = new QPushButton(QStringLiteral("解除未知结果锁"));
    acknowledgeButton_->setProperty("riskAction", true);
    acknowledgeButton_->setVisible(false);
    connect(acknowledgeButton_, &QPushButton::clicked, this, [this] {
        bool ok = false;
        const QString evidence = QInputDialog::getText(
            this, QStringLiteral("填写权威核对证据"),
            QStringLiteral("请填写已核对的订单号、进程 PID、服务状态或其他证据（至少 8 字符）："),
            QLineEdit::Normal, {}, &ok).trimmed();
        if (!ok) return;
        if (evidence.size() < 8) {
            appendLog(QStringLiteral("解除失败：权威核对证据至少需要 8 个字符"));
            return;
        }
        send(QStringLiteral("acknowledge_uncertain"),
             {{QStringLiteral("evidence"), evidence}}, 45000);
    });
    layout->addWidget(acknowledgeButton_);

    const bool nativeEngine = config_.engine == QStringLiteral("native");
    startButton_ = new QPushButton(nativeEngine ? QStringLiteral("启动模块")
                                                : QStringLiteral("启动服务"));
    stopButton_ = new QPushButton(nativeEngine ? QStringLiteral("停止模块")
                                               : QStringLiteral("停止服务"));
    restartButton_ = new QPushButton(nativeEngine ? QStringLiteral("重启模块")
                                                  : QStringLiteral("重启服务"));
    startButton_->setProperty("riskAction", true);
    stopButton_->setProperty("riskAction", true);
    restartButton_->setProperty("riskAction", true);
    connect(startButton_, &QPushButton::clicked, this, [this] { send(QStringLiteral("start_service")); });
    connect(stopButton_, &QPushButton::clicked, this, [this] { send(QStringLiteral("stop_service")); });
    connect(restartButton_, &QPushButton::clicked, this, [this] { send(QStringLiteral("restart_service")); });
    layout->addWidget(startButton_);
    layout->addWidget(stopButton_);
    layout->addWidget(restartButton_);
    updateControlState({});
    return widget;
}

void ModulePage::setContent(QWidget *content) {
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(24, 18, 24, 22);
    layout->setSpacing(12);
    layout->addWidget(buildHeader(config_.engine == QStringLiteral("native")
        ? QStringLiteral("由四合一 Agent 内的独立模块线程运行")
        : QStringLiteral("独立线程采集；业务进程保持进程隔离")));
    layout->addWidget(content, 1);
}

void ModulePage::updateControlState(const QJsonObject &snapshot) {
    const auto payload = snapshot.value(QStringLiteral("payload")).toObject();
    const QString ownership = payload.value(QStringLiteral("ownership")).toString(config_.ownership);
    const bool allowed = payload.value(QStringLiteral("control_enabled")).toBool(config_.controlEnabled)
        && (ownership == QStringLiteral("owner")
            || (config_.engine == QStringLiteral("native")
                && ownership == QStringLiteral("logic")));
    const QString lifecycle = payload.value(QStringLiteral("lifecycle"))
                                  .toString(QStringLiteral("unknown"));
    const bool running = lifecycle == QStringLiteral("running")
        || lifecycle == QStringLiteral("degraded");
    const auto update = [this, allowed](QPushButton *button, const QString &action,
                                       bool meaningful) {
        if (!button) return;
        const bool actionAllowed = config_.allowedActions.contains(action);
        button->setEnabled(allowed && actionAllowed && meaningful && !commandBusy_);
        button->setToolTip(allowed
            ? (config_.engine == QStringLiteral("native")
                   ? QStringLiteral("直接控制 Agent 内原生模块，不会启动旧程序")
                   : QStringLiteral("将显示影响范围确认"))
            : QStringLiteral("需开启模块控制权限并将启停动作加入允许列表"));
    };
    update(startButton_, QStringLiteral("start_service"), !running);
    update(stopButton_, QStringLiteral("stop_service"), running);
    update(restartButton_, QStringLiteral("restart_service"), running);
}

void ModulePage::applySnapshot(const QJsonObject &message) {
    lastSnapshot_ = message;
    const auto payload = message.value(QStringLiteral("payload")).toObject();
    const QString lifecycle = payload.value(QStringLiteral("lifecycle")).toString(QStringLiteral("unknown"));
    const QString workState = payload.value(QStringLiteral("work_state")).toString();
    stateLabel_->setText(workState.isEmpty() ? operatorStateText(lifecycle)
        : operatorStateText(lifecycle) + QStringLiteral(" · ") + operatorStateText(workState));
    stateLabel_->setObjectName(lifecycle == QStringLiteral("running") ? QStringLiteral("stateGood")
                               : lifecycle == QStringLiteral("stopped") ? QStringLiteral("stateIdle")
                                                                        : QStringLiteral("stateWarn"));
    stateLabel_->style()->unpolish(stateLabel_);
    stateLabel_->style()->polish(stateLabel_);
    freshnessLabel_->setText(QStringLiteral("快照 %1").arg(message.value(QStringLiteral("timestamp")).toString()));
    const bool unresolved = payload.value(QStringLiteral("safety_interlock")).toObject()
                                .value(QStringLiteral("outcome_unresolved")).toBool(false)
        || payload.value(QStringLiteral("unresolved_outcomes")).toInt() > 0;
    if (acknowledgeButton_) {
        acknowledgeButton_->setVisible(unresolved
            && config_.allowedActions.contains(QStringLiteral("acknowledge_uncertain")));
        acknowledgeButton_->setEnabled(unresolved && !commandBusy_ && logicalControlAllowed(message));
        acknowledgeButton_->setToolTip(unresolved
            ? QStringLiteral("仅在完成权威外部核对后使用；Agent 会要求一次性审批票据") : QString());
    }
    updateControlState(message);
}

void ModulePage::applyEvent(const QJsonObject &message) {
    const QString kind = message.value(QStringLiteral("event_kind")).toString();
    static const QSet<QString> continuousTelemetry{
        QStringLiteral("premium.summary"), QStringLiteral("premium.detail"),
        QStringLiteral("premium.l1_status"), QStringLiteral("premium.raw_snapshot"),
        QStringLiteral("webull.book"), QStringLiteral("webull.clients"),
        QStringLiteral("webull.log"), QStringLiteral("redemption.snapshot"),
        QStringLiteral("redemption.status"), QStringLiteral("process.log")};
    if (continuousTelemetry.contains(kind)) return;
    appendLog(QStringLiteral("%1  %2  %3")
                  .arg(message.value(QStringLiteral("timestamp")).toString(),
                       kind,
                       compactJson(redacted(eventPayload(message)))));
}

void ModulePage::applyCommand(const QJsonObject &message) {
    const QString state = message.value(QStringLiteral("state")).toString();
    const QString commandId = message.value(QStringLiteral("command_id")).toString();
    if (!commandId.isEmpty()) {
        const int rank = state == QStringLiteral("accepted") ? 1
            : state == QStringLiteral("running") ? 2
            : state == QStringLiteral("succeeded") || state == QStringLiteral("failed")
                    || state == QStringLiteral("timed_out") ? 3 : 0;
        const qint64 timestamp = QDateTime::fromString(
            message.value(QStringLiteral("timestamp")).toString(), Qt::ISODateWithMs)
                                      .toMSecsSinceEpoch();
        const int previousRank = commandStateRanks_.value(commandId, -1);
        const qint64 previousTimestamp = commandStateTimestamps_.value(commandId, -1);
        if (rank < previousRank || (rank == previousRank && timestamp >= 0
                                    && previousTimestamp >= timestamp)) {
            return;
        }
        commandStateRanks_.insert(commandId, rank);
        commandStateTimestamps_.insert(commandId, timestamp);
        if (state == QStringLiteral("accepted") || state == QStringLiteral("running")) {
            busyCommandIds_.insert(commandId);
        } else if (state == QStringLiteral("succeeded") || state == QStringLiteral("failed")
                   || state == QStringLiteral("timed_out")) {
            busyCommandIds_.remove(commandId);
        }
        while (commandStateRanks_.size() > 4096) {
            auto it = commandStateRanks_.begin();
            while (it != commandStateRanks_.end() && busyCommandIds_.contains(it.key())) ++it;
            if (it == commandStateRanks_.end()) break;
            commandStateTimestamps_.remove(it.key());
            commandStateRanks_.erase(it);
        }
    }
    commandBusy_ = !busyCommandIds_.isEmpty();
    updateBusyControls();
    appendLog(QStringLiteral("%1  command %2: %3 — %4")
                  .arg(message.value(QStringLiteral("timestamp")).toString(),
                       message.value(QStringLiteral("action")).toString(),
                       operatorStateText(message.value(QStringLiteral("state")).toString()),
                       message.value(QStringLiteral("message")).toString()));
}

void ModulePage::reconcileActiveCommands(const QSet<QString> &commandIds) {
    busyCommandIds_ = commandIds;
    commandBusy_ = !busyCommandIds_.isEmpty();
    updateBusyControls();
}

void ModulePage::appendLog(const QString &text) {
    if (!eventLog_) return;
    if (eventLog_->document()->maximumBlockCount() == 0) {
        eventLog_->document()->setMaximumBlockCount(500);
    }
    eventLog_->appendPlainText(text);
}

void ModulePage::send(const QString &action, const QJsonObject &arguments, int deadlineMs) {
    emit commandRequested(config_.id, action, arguments, deadlineMs);
}

bool ModulePage::logicalControlAllowed(const QJsonObject &snapshot) const {
    const auto payload = snapshot.value(QStringLiteral("payload")).toObject();
    const bool configured = payload.value(QStringLiteral("control_enabled")).toBool(config_.controlEnabled);
    const QString ownership = payload.value(QStringLiteral("ownership")).toString(config_.ownership);
    return configured && ownership != QStringLiteral("shadow") && !commandBusy_;
}

void ModulePage::updateBusyControls() {
    updateControlState(lastSnapshot_);
    if (acknowledgeButton_ && acknowledgeButton_->isVisible()) {
        acknowledgeButton_->setEnabled(!commandBusy_ && logicalControlAllowed(lastSnapshot_));
    }
}

UploadPage::UploadPage(ModuleConfig config, QWidget *parent)
    : ModulePage(std::move(config), parent) {
    auto *tabs = new QTabWidget;
    auto *overview = new QWidget;
    auto *overviewLayout = new QVBoxLayout(overview);
    workersExpectedLabel_ = textLabel(QStringLiteral("上传任务预期：应运行（异常即阻断 / fail-closed）"),
                                      QStringLiteral("uploadWorkersExpectedState"));
    overviewLayout->addWidget(workersExpectedLabel_);
    auto *ibkrControls = new QHBoxLayout;
    ibkrControls->addWidget(textLabel(QStringLiteral("IBKR / TWS 原生行情通道"),
                                      QStringLiteral("secondaryText")));
    ibkrControls->addStretch();
    ibkrReconnectButton_ = new QPushButton(QStringLiteral("连接 / 重连 IBKR"));
    ibkrDisconnectButton_ = new QPushButton(QStringLiteral("断开 IBKR"));
    connect(ibkrReconnectButton_, &QPushButton::clicked, this, [this] {
        send(QStringLiteral("upload_ibkr_reconnect"), {}, 30000);
    });
    connect(ibkrDisconnectButton_, &QPushButton::clicked, this, [this] {
        send(QStringLiteral("upload_ibkr_disconnect"), {}, 10000);
    });
    ibkrControls->addWidget(ibkrReconnectButton_);
    ibkrControls->addWidget(ibkrDisconnectButton_);
    overviewLayout->addLayout(ibkrControls);
    runtimeTree_ = makeTree();
    overviewLayout->addWidget(runtimeTree_, 1);
    tabs->addTab(overview, QStringLiteral("服务总览"));
    workersTable_ = makeTable({QStringLiteral("任务"), QStringLiteral("类型"),
                               QStringLiteral("状态"), QStringLiteral("阶段"),
                               QStringLiteral("时区 / 时间窗"), QStringLiteral("下次执行"),
                               QStringLiteral("上次成功"), QStringLiteral("模型"),
                               QStringLiteral("手工重跑")});
    workersTable_->setObjectName(QStringLiteral("uploadJobsTable"));
    tabs->addTab(workersTable_, QStringLiteral("上传器矩阵"));

    fundsTable_ = makeTable({QStringLiteral("基金"), QStringLiteral("名称"),
                             QStringLiteral("分支"), QStringLiteral("现价"),
                             QStringLiteral("NAV"), QStringLiteral("篮子买一净值"),
                             QStringLiteral("篮子卖一净值"), QStringLiteral("仓位"),
                             QStringLiteral("更新时间")});
    fundsTable_->setObjectName(QStringLiteral("uploadFundsTable"));
    tabs->addTab(fundsTable_, QStringLiteral("基金与详情"));

    historyTable_ = makeTable({QStringLiteral("类型"), QStringLiteral("标的"),
                               QStringLiteral("日期"), QStringLiteral("值"),
                               QStringLiteral("来源"), QStringLiteral("详情")});
    historyTable_->setObjectName(QStringLiteral("uploadHistoryTable"));
    tabs->addTab(historyTable_, QStringLiteral("历史 / 昨日赎回"));

    recordsTable_ = makeTable({QStringLiteral("记录号"), QStringLiteral("任务"),
                               QStringLiteral("幂等键"), QStringLiteral("SHA-256"),
                               QStringLiteral("输出模式"), QStringLiteral("记录时间"),
                               QStringLiteral("确认（ACK）")});
    recordsTable_->setObjectName(QStringLiteral("uploadRecordsTable"));
    tabs->addTab(recordsTable_, QStringLiteral("Debug / 统计"));

    auto *settings = new QWidget;
    auto *settingsLayout = new QFormLayout(settings);
    navSymbolEdit_ = new QLineEdit;
    navSymbolEdit_->setObjectName(QStringLiteral("uploadNavSymbol"));
    navValueEdit_ = new QLineEdit;
    sharesValueEdit_ = new QLineEdit;
    positionValueEdit_ = new QLineEdit;
    navSymbolEdit_->setPlaceholderText(QStringLiteral("SZ159518"));
    navValueEdit_->setPlaceholderText(QStringLiteral("NAV，可留空"));
    sharesValueEdit_->setPlaceholderText(QStringLiteral("份额，可留空"));
    positionValueEdit_->setPlaceholderText(QStringLiteral("有效仓位 0–1，可留空"));
    settingsLayout->addRow(QStringLiteral("基金代码"), navSymbolEdit_);
    settingsLayout->addRow(QStringLiteral("NAV"), navValueEdit_);
    settingsLayout->addRow(QStringLiteral("份额"), sharesValueEdit_);
    settingsLayout->addRow(QStringLiteral("有效仓位"), positionValueEdit_);
    auto *saveNav = new QPushButton(QStringLiteral("保存到原生 Upload 仓储"));
    saveNav->setObjectName(QStringLiteral("uploadSaveFund"));
    connect(saveNav, &QPushButton::clicked, this, [this] {
        QJsonObject arguments{{QStringLiteral("symbol"), navSymbolEdit_->text().trimmed()}};
        bool ok = false;
        const double nav = navValueEdit_->text().toDouble(&ok);
        if (ok) arguments.insert(QStringLiteral("nav"), nav);
        const double shares = sharesValueEdit_->text().toDouble(&ok);
        if (ok) arguments.insert(QStringLiteral("shares"), shares);
        const double position = positionValueEdit_->text().toDouble(&ok);
        if (ok) arguments.insert(QStringLiteral("position_ratio"), position);
        send(QStringLiteral("upload_set_fund"), arguments);
    });
    settingsLayout->addRow(saveNav);
    tabs->addTab(settings, QStringLiteral("NAV 设置"));

    auto *guide = new QWidget;
    auto *guideLayout = new QVBoxLayout(guide);
    guideLayout->addWidget(textLabel(
        QStringLiteral("上传器矩阵显示任务状态和上次成功时间。记录模式用于本地演练；包内业务模式运行原网站与上传器，以上传端实际返回的 ACK 判断成功。"),
        QStringLiteral("secondaryText")));
    messageEdit_ = new QPlainTextEdit;
    messageEdit_->setPlaceholderText(QStringLiteral("留言内容（只写入 MachomeHub/data/upload）"));
    guideLayout->addWidget(messageEdit_);
    auto *saveMessage = new QPushButton(QStringLiteral("保存留言"));
    connect(saveMessage, &QPushButton::clicked, this, [this] {
        send(QStringLiteral("upload_add_message"),
             {{QStringLiteral("message"), messageEdit_->toPlainText().trimmed()}});
    });
    guideLayout->addWidget(saveMessage);
    guideLayout->addStretch();
    const bool bundledBusiness=config_.settings.value(QStringLiteral("sink_mode")).toString()==QStringLiteral("bundled_business");
    if(bundledBusiness){
        saveNav->setEnabled(false);saveMessage->setEnabled(false);
        auto *website=new QPushButton(QStringLiteral("打开业务网站（净值、历史、仓位设置与留言）"));
        guideLayout->insertWidget(0,website);
        guideLayout->insertWidget(1,textLabel(QStringLiteral("当前使用原网站业务和数据库。上传器矩阵显示包内进程及实际 ACK 时间；业务编辑请在网站完成。")));
        connect(website,&QPushButton::clicked,this,[this]{
            QDesktopServices::openUrl(QUrl(config_.settings.value(QStringLiteral("web_base_url")).toString(QStringLiteral("http://127.0.0.1:8080"))));
        });
    }
    tabs->addTab(guide, QStringLiteral("指南与留言"));
    eventLog_ = new QPlainTextEdit;
    eventLog_->setObjectName(QStringLiteral("moduleEventLog"));
    eventLog_->setReadOnly(true);
    tabs->addTab(eventLog_, QStringLiteral("事件与日志"));
    setContent(tabs);
}

void UploadPage::applySnapshot(const QJsonObject &message) {
    ModulePage::applySnapshot(message);
    const auto payload = message.value(QStringLiteral("payload")).toObject();
    fillTree(runtimeTree_, payload);
    const auto telemetry = payload.value(QStringLiteral("telemetry")).toObject();
    const bool expected = telemetry.value(QStringLiteral("workers_expected")).toBool(true);
    workersExpectedLabel_->setText(expected
        ? QStringLiteral("上传任务预期：应运行（异常即阻断 / fail-closed；超时或缺失将告警）")
        : QStringLiteral("上传任务预期：允许排程休眠"));
    const QJsonArray workers = telemetry.value(QStringLiteral("workers")).toArray();
    jobControlButtons_.clear();
    workersTable_->setRowCount(workers.size());
    int row = 0;
    for (const auto &value : workers) {
        const QJsonObject worker = value.toObject();
        const QString id = worker.value(QStringLiteral("id")).toString();
        QString window = worker.value(QStringLiteral("timezone")).toString();
        const QString runAt = worker.value(QStringLiteral("run_at")).toString();
        if (!runAt.isEmpty()) window += QStringLiteral(" ") + runAt;
        for (const auto &item : worker.value(QStringLiteral("windows")).toArray()) {
            const auto range = item.toObject();
            window += QStringLiteral(" %1–%2")
                          .arg(range.value(QStringLiteral("start")).toString(),
                               range.value(QStringLiteral("end")).toString());
        }
        setCell(workersTable_, row, 0, id, worker);
        setCell(workersTable_, row, 1, worker.value(QStringLiteral("kind")).toString());
        setCell(workersTable_, row, 2, operatorStateText(worker.value(QStringLiteral("state")).toString()));
        setCell(workersTable_, row, 3, operatorStateText(worker.value(QStringLiteral("stage")).toString()));
        setCell(workersTable_, row, 4, window);
        setCell(workersTable_, row, 5, worker.value(QStringLiteral("next_run_at")).toString());
        setCell(workersTable_, row, 6, worker.value(QStringLiteral("last_success_at")).toString());
        setCell(workersTable_, row, 7, worker.value(QStringLiteral("model_version")).toString());
        auto *button = new QPushButton(QStringLiteral("重跑"), workersTable_);
        button->setObjectName(QStringLiteral("uploadRunJob_") + id);
        button->setProperty("jobId", id);
        connect(button, &QPushButton::clicked, this, [this, id] {
            send(QStringLiteral("upload_run_job"), {{QStringLiteral("job_id"), id}});
        });
        workersTable_->setCellWidget(row, 8, button);
        jobControlButtons_.append(button);
        ++row;
    }

    const QJsonArray funds = telemetry.value(QStringLiteral("funds")).toArray();
    fundsTable_->setRowCount(funds.size());
    row = 0;
    for (const auto &value : funds) {
        const QJsonObject fund = value.toObject();
        const QJsonObject valuation = fund.value(QStringLiteral("valuation")).toObject();
        setCell(fundsTable_, row, 0, fund.value(QStringLiteral("symbol")).toString(), fund);
        setCell(fundsTable_, row, 1, fund.value(QStringLiteral("name")).toString());
        setCell(fundsTable_, row, 2, fund.value(QStringLiteral("branch")).toString());
        setCell(fundsTable_, row, 3, displayValue(fund.value(QStringLiteral("price"))));
        setCell(fundsTable_, row, 4, displayValue(fund.value(QStringLiteral("nav"))));
        setCell(fundsTable_, row, 5, displayValue(valuation.value(QStringLiteral("basket_bid_nav"))));
        setCell(fundsTable_, row, 6, displayValue(valuation.value(QStringLiteral("basket_ask_nav"))));
        setCell(fundsTable_, row, 7, displayValue(fund.value(QStringLiteral("position_ratio"))));
        setCell(fundsTable_, row, 8, fund.value(QStringLiteral("updated_at")).toString());
        ++row;
    }

    const QJsonObject history = telemetry.value(QStringLiteral("history")).toObject();
    QList<QJsonObject> historyRows;
    auto appendHistory = [&historyRows](const QJsonArray &items, const QString &kind,
                                        const QString &field) {
        for (const auto &value : items) {
            QJsonObject item = value.toObject();
            item.insert(QStringLiteral("kind_label"), kind);
            item.insert(QStringLiteral("display_value"), displayValue(item.value(field)));
            historyRows.append(item);
        }
    };
    appendHistory(history.value(QStringLiteral("net_values")).toArray(),
                  QStringLiteral("单位净值"), QStringLiteral("nav"));
    appendHistory(history.value(QStringLiteral("share_history")).toArray(),
                  QStringLiteral("份额(万份)"), QStringLiteral("shares_10k"));
    appendHistory(history.value(QStringLiteral("daily_prices")).toArray(),
                  QStringLiteral("收盘价"), QStringLiteral("close"));
    appendHistory(history.value(QStringLiteral("holdings")).toArray(),
                  QStringLiteral("持仓"), QStringLiteral("quantity"));
    for (const auto &value : history.value(QStringLiteral("purchase_status")).toArray()) {
        QJsonObject item = value.toObject();
        item.insert(QStringLiteral("kind_label"), QStringLiteral("申购状态"));
        item.insert(QStringLiteral("display_value"),
                    displayValue(item.value(QStringLiteral("daily_limit_yuan"))));
        item.insert(QStringLiteral("detail"), item.value(QStringLiteral("status")));
        historyRows.append(item);
    }
    appendHistory(history.value(QStringLiteral("fx_rates")).toArray(),
                  QStringLiteral("SAFE 中间价"), QStringLiteral("rate"));
    for (const auto &value : history.value(QStringLiteral("pcf_cache")).toArray()) {
        QJsonObject item = value.toObject();
        item.insert(QStringLiteral("kind_label"), QStringLiteral("PCF 缓存"));
        item.insert(QStringLiteral("display_value"),
                    item.value(QStringLiteral("sha256")).toString().left(12));
        item.insert(QStringLiteral("detail"), item.value(QStringLiteral("source_url")));
        historyRows.append(item);
    }
    QHash<QString, QJsonObject> newerShare;
    for (const auto &value : history.value(QStringLiteral("share_history")).toArray()) {
        const QJsonObject item = value.toObject();
        const QString symbol = item.value(QStringLiteral("symbol")).toString();
        if (!newerShare.contains(symbol)) {
            newerShare.insert(symbol, item);
            continue;
        }
        const QJsonObject newest = newerShare.take(symbol);
        const double redeemed = item.value(QStringLiteral("shares_10k")).toDouble()
            - newest.value(QStringLiteral("shares_10k")).toDouble();
        if (redeemed > 0) {
            QJsonObject rank = newest;
            rank.insert(QStringLiteral("kind_label"), QStringLiteral("昨日赎回"));
            rank.insert(QStringLiteral("display_value"), displayValue(redeemed));
            rank.insert(QStringLiteral("detail"), QStringLiteral("份额减少 %1 万份").arg(redeemed));
            historyRows.prepend(rank);
        }
    }
    historyTable_->setRowCount(historyRows.size());
    row = 0;
    for (const QJsonObject &item : historyRows) {
        setCell(historyTable_, row, 0, item.value(QStringLiteral("kind_label")).toString(), item);
        setCell(historyTable_, row, 1, item.value(QStringLiteral("symbol")).toString());
        setCell(historyTable_, row, 2, item.value(QStringLiteral("date")).toString());
        setCell(historyTable_, row, 3, item.value(QStringLiteral("display_value")).toString());
        setCell(historyTable_, row, 4, item.value(QStringLiteral("source")).toString());
        setCell(historyTable_, row, 5, item.value(QStringLiteral("detail")).toString(
                    item.value(QStringLiteral("holding_symbol")).toString()));
        ++row;
    }
    const QJsonArray records = telemetry.value(QStringLiteral("upload_records")).toArray();
    recordsTable_->setRowCount(records.size());
    row = 0;
    for (const auto &value : records) {
        const QJsonObject item = value.toObject();
        setCell(recordsTable_, row, 0, item.value(QStringLiteral("record_id")).toString(), item);
        setCell(recordsTable_, row, 1, item.value(QStringLiteral("job_id")).toString());
        setCell(recordsTable_, row, 2, item.value(QStringLiteral("idempotency_key")).toString());
        setCell(recordsTable_, row, 3, item.value(QStringLiteral("sha256")).toString());
        setCell(recordsTable_, row, 4, item.value(QStringLiteral("sink_mode")).toString());
        setCell(recordsTable_, row, 5, item.value(QStringLiteral("created_at")).toString());
        setCell(recordsTable_, row, 6, item.value(QStringLiteral("acked_at")).toString());
        ++row;
    }
    updateBusyControls();
}

void UploadPage::applyEvent(const QJsonObject &message) {
    ModulePage::applyEvent(message);
}

void UploadPage::updateBusyControls() {
    ModulePage::updateBusyControls();
    const auto payload = lastSnapshot_.value(QStringLiteral("payload")).toObject();
    const bool ownerControl = payload.value(QStringLiteral("control_enabled"))
                                  .toBool(config_.controlEnabled)
        && payload.value(QStringLiteral("ownership")).toString(config_.ownership)
               != QStringLiteral("shadow")
        && !commandBusy_;
    for (const auto &guard : std::as_const(jobControlButtons_)) {
        if (!guard) continue;
        guard->setEnabled(ownerControl
                          && config_.allowedActions.contains(QStringLiteral("upload_run_job")));
    }
    const auto engine = payload.value(QStringLiteral("telemetry")).toObject()
                            .value(QStringLiteral("engine")).toObject();
    const auto ibkr = engine.value(QStringLiteral("ibkr")).toObject();
    const bool running = engine.value(QStringLiteral("running")).toBool(false);
    if (ibkrReconnectButton_) {
        ibkrReconnectButton_->setEnabled(ownerControl && running
            && ibkr.value(QStringLiteral("enabled")).toBool(false)
            && config_.allowedActions.contains(QStringLiteral("upload_ibkr_reconnect")));
    }
    if (ibkrDisconnectButton_) {
        ibkrDisconnectButton_->setEnabled(ownerControl && running
            && ibkr.value(QStringLiteral("running")).toBool(false)
            && config_.allowedActions.contains(QStringLiteral("upload_ibkr_disconnect")));
    }
}

PremiumDetailWindow::PremiumDetailWindow(QString symbol, QString name,
                                         QWidget *parent)
    : QWidget(parent, Qt::Window)
    , symbol_(std::move(symbol))
    , name_(std::move(name)) {
    setAttribute(Qt::WA_DeleteOnClose);
    setObjectName(QStringLiteral("premiumDetailWindow_") + symbol_);
    setWindowTitle(QStringLiteral("%1 %2 · 十档行情详情").arg(symbol_, name_));
    resize(940, 760);

    auto *layout = new QVBoxLayout(this);
    auto *top = new QHBoxLayout;
    headlineLabel_ = textLabel(QStringLiteral("%1 %2 · 等待 A 端详情快照")
                                   .arg(symbol_, name_),
                               QStringLiteral("pageTitle"));
    connectionLabel_ = textLabel(QStringLiteral("正在订阅"),
                                 QStringLiteral("stateWarn"));
    connectionLabel_->setObjectName(QStringLiteral("premiumDetailConnection"));
    top->addWidget(headlineLabel_, 1);
    top->addWidget(connectionLabel_);
    layout->addLayout(top);
    marketLabel_ = textLabel(
        QStringLiteral("纯行情详情：本窗口不包含 B 端交易或 QMT 指令入口。"),
        QStringLiteral("secondaryText"));
    marketLabel_->setObjectName(QStringLiteral("premiumDetailMarket"));
    layout->addWidget(marketLabel_);

    auto *tabs = new QTabWidget;
    bookTable_ = makeTable({QStringLiteral("盘口"), QStringLiteral("价格"),
                            QStringLiteral("数量"), QStringLiteral("原始定点值")});
    bookTable_->setObjectName(QStringLiteral("premiumDetailBook"));
    bookTable_->setRowCount(20);
    tabs->addTab(bookTable_, QStringLiteral("十档盘口"));
    fieldsTree_ = makeTree();
    fieldsTree_->setObjectName(QStringLiteral("premiumDetailFields"));
    tabs->addTab(fieldsTree_, QStringLiteral("全部行情字段"));
    rawView_ = new QPlainTextEdit;
    rawView_->setObjectName(QStringLiteral("premiumDetailRaw"));
    rawView_->setReadOnly(true);
    tabs->addTab(rawView_, QStringLiteral("原始 JSON"));
    layout->addWidget(tabs, 1);
}

void PremiumDetailWindow::applyDetail(const QJsonObject &detail) {
    const QString incoming = detail.value(QStringLiteral("s")).toString(
        detail.value(QStringLiteral("symbol")).toString()).trimmed().toUpper();
    if (incoming != symbol_) return;
    const QString incomingName = detail.value(QStringLiteral("name")).toString();
    if (!incomingName.isEmpty()) name_ = incomingName;
    auto price = [&detail](const QString &key, int decimals = 3) {
        const auto value = detail.value(key);
        return value.isDouble()
            ? QString::number(static_cast<double>(value.toInteger()) / 1'000'000.0,
                              'f', decimals)
            : QStringLiteral("—");
    };
    auto ppm = [&detail](const QString &key) {
        const auto value = detail.value(key);
        return value.isDouble()
            ? QStringLiteral("%1%").arg(
                  static_cast<double>(value.toInteger()) / 10'000.0, 0, 'f', 3)
            : QStringLiteral("—");
    };
    headlineLabel_->setText(
        QStringLiteral("%1 %2 · 现价 %3 · 买一 %4 · IOPV %5 · 可卖溢价 %6")
            .arg(symbol_, name_, price(QStringLiteral("last_price_e6")),
                 price(QStringLiteral("bid1_price_e6")),
                 price(QStringLiteral("iopv_e6"), 4),
                 ppm(QStringLiteral("sell_premium_ppm"))));
    marketLabel_->setText(
        QStringLiteral("阶段 %1 · 交易所时间 %2 · %3 · level_count=%4")
            .arg(detail.value(QStringLiteral("trading_phase")).toString(
                     QStringLiteral("—")),
                 displayValue(detail.value(QStringLiteral("orig_time"))),
                 detail.value(QStringLiteral("cached")).toBool()
                     ? QStringLiteral("A 端缓存首帧") : QStringLiteral("实时主推"),
                 displayValue(detail.value(QStringLiteral("level_count")))));

    const QJsonArray bidPrices = detail.value(QStringLiteral("bid_prices_e6")).toArray();
    const QJsonArray askPrices = detail.value(QStringLiteral("ask_prices_e6")).toArray();
    const QJsonArray bidVolumes = detail.value(QStringLiteral("bid_volumes_e2")).toArray();
    const QJsonArray askVolumes = detail.value(QStringLiteral("ask_volumes_e2")).toArray();
    auto setLevel = [this](int row, const QString &side, const QJsonValue &priceValue,
                           const QJsonValue &volumeValue) {
        const qint64 priceE6 = priceValue.toInteger();
        const qint64 volumeE2 = volumeValue.toInteger();
        setCell(bookTable_, row, 0, side);
        setCell(bookTable_, row, 1,
                priceValue.isDouble()
                    ? QString::number(static_cast<double>(priceE6) / 1'000'000.0,
                                      'f', 3)
                    : QStringLiteral("—"));
        setCell(bookTable_, row, 2,
                volumeValue.isDouble()
                    ? QString::number(static_cast<double>(volumeE2) / 100.0,
                                      'f', 0)
                    : QStringLiteral("—"));
        setCell(bookTable_, row, 3,
                priceValue.isDouble() && volumeValue.isDouble()
                    ? QStringLiteral("p=%1 / v=%2").arg(priceE6).arg(volumeE2)
                    : QStringLiteral("—"));
    };
    for (int index = 9; index >= 0; --index) {
        const int row = 9 - index;
        setLevel(row, QStringLiteral("卖%1").arg(index + 1),
                 index < askPrices.size() ? askPrices.at(index) : QJsonValue{},
                 index < askVolumes.size() ? askVolumes.at(index) : QJsonValue{});
    }
    for (int index = 0; index < 10; ++index) {
        setLevel(10 + index, QStringLiteral("买%1").arg(index + 1),
                 index < bidPrices.size() ? bidPrices.at(index) : QJsonValue{},
                 index < bidVolumes.size() ? bidVolumes.at(index) : QJsonValue{});
    }
    fillTree(fieldsTree_, detail);
    rawView_->setPlainText(
        QString::fromUtf8(QJsonDocument(detail).toJson(QJsonDocument::Indented)));
    setSubscriptionAcknowledged(true);
}

void PremiumDetailWindow::setConnectionState(const QString &state,
                                             const QString &description) {
    const bool connected = state == QStringLiteral("connected");
    connectionLabel_->setText(description.isEmpty() ? state : description);
    connectionLabel_->setObjectName(connected ? QStringLiteral("stateGood")
                                              : QStringLiteral("stateWarn"));
    connectionLabel_->style()->unpolish(connectionLabel_);
    connectionLabel_->style()->polish(connectionLabel_);
}

void PremiumDetailWindow::setSubscriptionAcknowledged(bool acknowledged) {
    connectionLabel_->setText(acknowledged ? QStringLiteral("A 端已确认订阅")
                                           : QStringLiteral("等待 A 端订阅 ACK"));
    connectionLabel_->setObjectName(acknowledged ? QStringLiteral("stateGood")
                                                 : QStringLiteral("stateWarn"));
    connectionLabel_->style()->unpolish(connectionLabel_);
    connectionLabel_->style()->polish(connectionLabel_);
}

void PremiumDetailWindow::closeEvent(QCloseEvent *event) {
    if (!closePublished_) {
        closePublished_ = true;
        emit closed(symbol_);
    }
    QWidget::closeEvent(event);
}

PremiumPage::PremiumPage(ModuleConfig config, QWidget *parent)
    : ModulePage(std::move(config), parent) {
    summaryRenderTimer_ = new QTimer(this);
    summaryRenderTimer_->setSingleShot(true);
    summaryRenderTimer_->setInterval(500);
    connect(summaryRenderTimer_, &QTimer::timeout,
            this, &PremiumPage::flushPendingSummaries);
    auto *tabs = new QTabWidget;
    auto *live = new QWidget;
    auto *liveLayout = new QVBoxLayout(live);
    auto *buttons = new QHBoxLayout;
    auto *sync = new QPushButton(QStringLiteral("重新同步"));
    auto *raw = new QPushButton(QStringLiteral("请求原始快照"));
    syncLabel_ = textLabel(QStringLiteral("等待 sync"), QStringLiteral("secondaryText"));
    soundEnabled_ = new QCheckBox(QStringLiteral("声音"));
    popupEnabled_ = new QCheckBox(QStringLiteral("系统通知"));
    QSettings alertSettings;
    soundEnabled_->setChecked(alertSettings.value(QStringLiteral("alerts/premium_sound"), true).toBool());
    popupEnabled_->setChecked(alertSettings.value(QStringLiteral("alerts/premium_popup"), true).toBool());
    connect(soundEnabled_, &QCheckBox::toggled, this, [](bool enabled) {
        QSettings().setValue(QStringLiteral("alerts/premium_sound"), enabled);
    });
    connect(popupEnabled_, &QCheckBox::toggled, this, [](bool enabled) {
        QSettings().setValue(QStringLiteral("alerts/premium_popup"), enabled);
    });
    connect(sync, &QPushButton::clicked, this, [this] { send(QStringLiteral("premium_sync")); });
    connect(raw, &QPushButton::clicked, this, [this] { send(QStringLiteral("premium_raw_snapshot")); });
    buttons->addWidget(sync);
    buttons->addWidget(raw);
    buttons->addWidget(syncLabel_);
    buttons->addWidget(soundEnabled_);
    buttons->addWidget(popupEnabled_);
    buttons->addStretch();
    liveLayout->addLayout(buttons);
    signalsTable_ = makeTable({QStringLiteral("时间"), QStringLiteral("标的"), QStringLiteral("名称"),
                               QStringLiteral("模型/事件"), QStringLiteral("溢价率"), QStringLiteral("窗口"),
                               QStringLiteral("级别")});
    signalsTable_->setObjectName(QStringLiteral("premiumSignalsTable"));
    liveLayout->addWidget(signalsTable_);
    connect(signalsTable_, &QTableWidget::cellDoubleClicked, this, [this](int row, int) {
        if (auto *item = signalsTable_->item(row, 0)) {
            const QByteArray data = item->data(Qt::UserRole).toString().toUtf8();
            openDetail(QJsonDocument::fromJson(data).object());
        }
    });
    tabs->addTab(live, QStringLiteral("实时拉升告警"));
    summariesTable_ = makeTable({QStringLiteral("标的"), QStringLiteral("名称"), QStringLiteral("现价"),
                                 QStringLiteral("IOPV"), QStringLiteral("溢价率"), QStringLiteral("更新时间"),
                                 QStringLiteral("状态")});
    summariesTable_->setObjectName(QStringLiteral("premiumSummariesTable"));
    connect(summariesTable_, &QTableWidget::cellDoubleClicked, this, [this](int row, int) {
        if (auto *item = summariesTable_->item(row, 0)) {
            const QByteArray data = item->data(Qt::UserRole).toString().toUtf8();
            openDetail(QJsonDocument::fromJson(data).object());
        }
    });
    tabs->addTab(summariesTable_, QStringLiteral("实时全景"));

    auto *history = new QWidget;
    auto *historyLayout = new QVBoxLayout(history);
    auto *historyControls = new QHBoxLayout;
    historyFrom_ = new QDateEdit(QDate::currentDate().addDays(-29));
    historyFrom_->setCalendarPopup(true);
    historyFrom_->setDisplayFormat(QStringLiteral("yyyy-MM-dd"));
    historyTo_ = new QDateEdit(QDate::currentDate());
    historyTo_->setCalendarPopup(true);
    historyTo_->setDisplayFormat(QStringLiteral("yyyy-MM-dd"));
    historySymbol_ = new QLineEdit;
    historySymbol_->setPlaceholderText(QStringLiteral("标的筛选，例如 159866"));
    historyModel_ = new QComboBox;
    historyModel_->addItem(QStringLiteral("全部模型"), QString());
    historyModel_->addItem(QStringLiteral("溢价率"), QStringLiteral("premium"));
    historyModel_->addItem(QStringLiteral("盘口拉涨"), QStringLiteral("pull"));
    historyModel_->addItem(QStringLiteral("溢价率 + 盘口拉涨"), QStringLiteral("premium+pull"));
    historyModel_->addItem(QStringLiteral("含快速拉涨雷达"), QStringLiteral("contains:radar"));
    auto *loadHistory = new QPushButton(QStringLiteral("后台读取"));
    auto *exportHistory = new QPushButton(QStringLiteral("导出 CSV"));
    auto *openDirectory = new QPushButton(QStringLiteral("打开审计目录"));
    historyPreviousButton_ = new QPushButton(QStringLiteral("上一页"));
    historyNextButton_ = new QPushButton(QStringLiteral("下一页"));
    historyPreviousButton_->setEnabled(false);
    historyNextButton_->setEnabled(false);
    historyControls->addWidget(textLabel(QStringLiteral("从")));
    historyControls->addWidget(historyFrom_);
    historyControls->addWidget(textLabel(QStringLiteral("到")));
    historyControls->addWidget(historyTo_);
    historyControls->addWidget(historySymbol_);
    historyControls->addWidget(historyModel_);
    historyControls->addWidget(loadHistory);
    historyControls->addWidget(exportHistory);
    historyControls->addWidget(openDirectory);
    historyControls->addWidget(historyPreviousButton_);
    historyControls->addWidget(historyNextButton_);
    historyLayout->addLayout(historyControls);
    historyStatus_ = textLabel(QStringLiteral("只读 data/signals-YYYYMMDD.jsonl；解析与导出均在后台线程执行。"),
                               QStringLiteral("secondaryText"));
    historyLayout->addWidget(historyStatus_);
    historyTable_ = makeTable({QStringLiteral("触发时间"), QStringLiteral("标的"), QStringLiteral("名称"),
                               QStringLiteral("模型"), QStringLiteral("可卖溢价率"), QStringLiteral("拉升30秒"),
                               QStringLiteral("拉升5分钟"), QStringLiteral("买一150秒"), QStringLiteral("买一300秒"),
                               QStringLiteral("提醒类型"), QStringLiteral("来源"), QStringLiteral("触发原因")});
    historyTable_->setObjectName(QStringLiteral("premiumHistoryTable"));
    historyLayout->addWidget(historyTable_, 1);
    historyLoader_ = new PremiumHistoryLoader(this);
    connect(loadHistory, &QPushButton::clicked, this, [this] {
        historyLoader_->load(
            AppConfig::expandPath(config_.settings.value(QStringLiteral("signal_directory")).toString()),
            historyFrom_->date(), historyTo_->date(), historySymbol_->text(),
            historyModel_->currentData().toString());
    });
    connect(historySymbol_, &QLineEdit::returnPressed, loadHistory, &QPushButton::click);
    connect(historyPreviousButton_, &QPushButton::clicked, this, [this] {
        renderHistoryPage(historyPage_ - 1);
    });
    connect(historyNextButton_, &QPushButton::clicked, this, [this] {
        renderHistoryPage(historyPage_ + 1);
    });
    connect(openDirectory, &QPushButton::clicked, this, [this] {
        QDesktopServices::openUrl(QUrl::fromLocalFile(
            AppConfig::expandPath(config_.settings.value(QStringLiteral("signal_directory")).toString())));
    });
    connect(exportHistory, &QPushButton::clicked, this, [this] {
        if (historyRecords_.isEmpty()) {
            historyStatus_->setText(QStringLiteral("当前筛选结果为空，无法导出。"));
            return;
        }
        const QString suggested = QStringLiteral("signal-audit-%1.csv")
                                      .arg(QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss")));
        const QString path = QFileDialog::getSaveFileName(
            this, QStringLiteral("导出信号审计"), suggested, QStringLiteral("CSV 文件 (*.csv)"));
        if (!path.isEmpty()) historyLoader_->exportCsv(path, historyRecords_);
    });
    connect(historyLoader_, &PremiumHistoryLoader::loadStarted, this, [this](int count) {
        historyStatus_->setText(QStringLiteral("正在后台读取 %1 个审计文件…").arg(count));
    });
    connect(historyLoader_, &PremiumHistoryLoader::loadFinished,
            this, &PremiumPage::populateHistory);
    connect(historyLoader_, &PremiumHistoryLoader::failed, this, [this](const QString &message) {
        historyStatus_->setText(message);
    });
    connect(historyLoader_, &PremiumHistoryLoader::exportFinished, this,
            [this](bool, const QString &message) { historyStatus_->setText(message); });
    connect(historyTable_, &QTableWidget::cellDoubleClicked, this, [this](int row, int) {
        constexpr int PageSize = 2'000;
        const int recordIndex = historyPage_ * PageSize + row;
        if (row < 0 || recordIndex < 0 || recordIndex >= historyRecords_.size()) return;
        showJsonDetail(this, QStringLiteral("历史信号详情"), historyRecords_.at(recordIndex).toObject());
    });
    tabs->addTab(history, QStringLiteral("历史信号审计"));

    runtimeTree_ = makeTree();
    tabs->addTab(runtimeTree_, QStringLiteral("运行状态"));
    auto listEditor = [this](bool hot) {
        auto *page = new QWidget;
        auto *layout = new QVBoxLayout(page);
        layout->addWidget(textLabel(hot ? QStringLiteral("每行一个额外 L1 维护标的；服务端拒绝时保留原值。")
                                        : QStringLiteral("每行一个观察标的；保存通过 8421 loopback 合同执行。"),
                                    QStringLiteral("secondaryText")));
        auto *edit = new QPlainTextEdit;
        edit->setPlaceholderText(hot ? QStringLiteral("510300.SH\n02800.HK")
                                     : QStringLiteral("510300.SH\n159915.SZ"));
        layout->addWidget(edit, 1);
        auto *save = new QPushButton(QStringLiteral("保存并等待服务端确认"));
        mutationButtons_.append(save);
        connect(save, &QPushButton::clicked, this, [this, edit, hot] {
            const QStringList symbols = lines(edit);
            QJsonObject arguments{{QStringLiteral("symbols"), QJsonArray::fromStringList(symbols)}};
            if (symbols.isEmpty() && !hot) {
                QMessageBox::information(
                    this, QStringLiteral("观察列表不能为空"),
                    QStringLiteral("溢价率主观察列表至少需要 1 个标的；未发送任何变更。"));
                return;
            }
            if (symbols.isEmpty()) {
                const auto answer = QMessageBox::warning(
                    this, QStringLiteral("确认清空列表"),
                    QStringLiteral("此操作会清空全部额外 L1 标的。是否继续？"),
                    QMessageBox::Yes | QMessageBox::No, QMessageBox::No);
                if (answer != QMessageBox::Yes) return;
                arguments.insert(QStringLiteral("confirm_empty"), true);
            }
            send(hot ? QStringLiteral("premium_set_l1_hotlist") : QStringLiteral("premium_set_watchlist"),
                 arguments);
        });
        layout->addWidget(save);
        if (hot) hotlistEdit_ = edit; else watchlistEdit_ = edit;
        return page;
    };
    tabs->addTab(listEditor(false), QStringLiteral("观察标的管理"));
    tabs->addTab(listEditor(true), QStringLiteral("额外 L1 标的"));
    eventLog_ = new QPlainTextEdit;
    eventLog_->setObjectName(QStringLiteral("moduleEventLog"));
    eventLog_->setReadOnly(true);
    tabs->addTab(eventLog_, QStringLiteral("运行日志"));
    for (auto *button : std::as_const(mutationButtons_)) {
        button->setEnabled(config_.controlEnabled && config_.ownership != QStringLiteral("shadow"));
    }
    setContent(tabs);
}

void PremiumPage::applySnapshot(const QJsonObject &message) {
    ModulePage::applySnapshot(message);
    const auto payload = message.value(QStringLiteral("payload")).toObject();
    fillTree(runtimeTree_, payload);
    const auto telemetry = payload.value(QStringLiteral("telemetry")).toObject();
    const bool canMutate = logicalControlAllowed(message);
    for (auto *button : std::as_const(mutationButtons_)) {
        button->setEnabled(canMutate);
        button->setToolTip(canMutate ? QStringLiteral("通过现有 loopback 协议保存并等待 ACK")
                                     : QStringLiteral("需 control_enabled=true 且 ownership=logic/owner"));
    }
    const auto watch = telemetry.value(QStringLiteral("watchlist"));
    const auto hot = telemetry.value(QStringLiteral("l1_hotlist"));
    if (watch.isArray() && watchlistEdit_->toPlainText().trimmed().isEmpty()) {
        QStringList values;
        for (const auto &value : watch.toArray()) values.push_back(value.toString());
        watchlistEdit_->setPlainText(values.join(u'\n'));
    }
    if (hot.isArray() && hotlistEdit_->toPlainText().trimmed().isEmpty()) {
        QStringList values;
        for (const auto &value : hot.toArray()) values.push_back(value.toString());
        hotlistEdit_->setPlainText(values.join(u'\n'));
    }
    const auto detailChannel = telemetry.value(QStringLiteral("detail_channel")).toObject();
    const QString detailState = detailChannel.value(QStringLiteral("state")).toString();
    const QString detailDescription = detailChannel.value(QStringLiteral("detail")).toString();
    QSet<QString> acknowledged;
    const auto acknowledgedValues = telemetry.value(QStringLiteral("detail_subscriptions"))
                                        .toObject()
                                        .value(QStringLiteral("acknowledged")).toArray();
    for (const auto &value : acknowledgedValues) acknowledged.insert(value.toString());
    for (auto it = detailWindows_.begin(); it != detailWindows_.end(); ++it) {
        if (!it.value()) continue;
        it.value()->setConnectionState(detailState, detailDescription);
        it.value()->setSubscriptionAcknowledged(acknowledged.contains(it.key()));
    }
}

void PremiumPage::updateBusyControls() {
    ModulePage::updateBusyControls();
    const bool allowed = logicalControlAllowed(lastSnapshot_);
    for (auto *button : std::as_const(mutationButtons_)) button->setEnabled(allowed);
}

void PremiumPage::applyEvent(const QJsonObject &message) {
    ModulePage::applyEvent(message);
    const QString kind = message.value(QStringLiteral("event_kind")).toString();
    const auto payload = eventPayload(message);
    if (kind == QStringLiteral("premium.detail_state")) {
        for (auto it = detailWindows_.begin(); it != detailWindows_.end(); ++it) {
            if (it.value()) {
                it.value()->setConnectionState(
                    payload.value(QStringLiteral("state")).toString(),
                    payload.value(QStringLiteral("detail")).toString());
            }
        }
        return;
    }
    if (kind == QStringLiteral("premium.detail_ack")) {
        const QString symbol = payload.value(QStringLiteral("symbol"))
                                   .toString().trimmed().toUpper();
        if (payload.value(QStringLiteral("op")).toString()
                == QStringLiteral("subscribe")) {
            if (const auto window = detailWindows_.value(symbol); window) {
                window->setSubscriptionAcknowledged(true);
            }
        }
        return;
    }
    if (kind == QStringLiteral("premium.detail")) {
        const QString symbol = payload.value(QStringLiteral("symbol")).toString(
            payload.value(QStringLiteral("s")).toString()).trimmed().toUpper();
        if (const auto window = detailWindows_.value(symbol); window) {
            window->applyDetail(payload);
        }
        return;
    }
    if (kind == QStringLiteral("premium.symbol_removed")) {
        const QString symbol = payload.value(QStringLiteral("symbol"))
                                   .toString().trimmed().toUpper();
        pendingSummaries_.remove(symbol);
        const auto found = summaryRows_.find(symbol);
        if (found != summaryRows_.end()) {
            const int removedRow = found.value();
            summaryRows_.erase(found);
            summariesTable_->removeRow(removedRow);
            for (auto row = summaryRows_.begin(); row != summaryRows_.end(); ++row) {
                if (row.value() > removedRow) --row.value();
            }
        }
        if (const auto window = detailWindows_.value(symbol); window) window->close();
        return;
    }
    if (kind.contains(QStringLiteral("signal"), Qt::CaseInsensitive)) {
        const bool inserted = addSignal(payload);
        if (inserted && !message.value(QStringLiteral("replayed")).toBool(false)
            && !payload.value(QStringLiteral("backfill")).toBool()
            && !payload.value(QStringLiteral("replay")).toBool()) {
            const QString symbol = firstValue(payload, {QStringLiteral("symbol")}, QStringLiteral("ETF"));
            const QString model = premiumModelText(payload.value(QStringLiteral("model")).toString());
            emit alertRequested(config_.id, QStringLiteral("%1 溢价率拉升").arg(symbol),
                                QStringLiteral("%1 · %2 · %3")
                                    .arg(model,
                                         premiumPpmText(payload, QStringLiteral("premium_ppm")),
                                         payload.value(QStringLiteral("reason")).toString()),
                                soundEnabled_->isChecked(), popupEnabled_->isChecked());
        }
    }
    else if (kind.contains(QStringLiteral("summary"), Qt::CaseInsensitive)) {
        const QString symbol = firstValue(
            payload, {QStringLiteral("symbol"), QStringLiteral("code")}, {});
        if (!symbol.isEmpty()) pendingSummaries_.insert(symbol, payload);
        if (summaryRenderTimer_ && !summaryRenderTimer_->isActive()) {
            summaryRenderTimer_->start();
        }
    }
    else if (kind.contains(QStringLiteral("sync"), Qt::CaseInsensitive)) {
        syncLabel_->setText(firstValue(payload, {QStringLiteral("state"), QStringLiteral("type")}, kind));
    }
}

void PremiumPage::openDetail(const QJsonObject &payload) {
    const QString symbol = payload.value(QStringLiteral("symbol")).toString(
        payload.value(QStringLiteral("s")).toString()).trimmed().toUpper();
    static const QRegularExpression validSymbol(
        QStringLiteral("^[0-9]{6}\\.(?:SH|SZ)$"));
    if (!validSymbol.match(symbol).hasMatch()) {
        QMessageBox::warning(this, QStringLiteral("无法打开详情"),
                             QStringLiteral("详情合同要求 NNNNNN.SH/SZ 标的。"));
        return;
    }
    if (const auto existing = detailWindows_.value(symbol); existing) {
        existing->show();
        existing->raise();
        existing->activateWindow();
        return;
    }
    for (auto it = detailWindows_.begin(); it != detailWindows_.end();) {
        if (!it.value()) it = detailWindows_.erase(it);
        else ++it;
    }
    if (detailWindows_.size() >= 4) {
        QMessageBox::warning(this, QStringLiteral("详情页上限"),
                             QStringLiteral("每个运行中心最多同时打开 4 个实时详情页。"));
        return;
    }
    if (!config_.allowedActions.contains(QStringLiteral("premium_detail_subscribe"))) {
        QMessageBox::warning(this, QStringLiteral("详情订阅未授权"),
                             QStringLiteral("配置未开放 premium_detail_subscribe。"));
        return;
    }
    auto *window = new PremiumDetailWindow(
        symbol, payload.value(QStringLiteral("name")).toString(), this);
    detailWindows_.insert(symbol, window);
    connect(window, &PremiumDetailWindow::closed, this,
            [this, window](const QString &closedSymbol) {
        const auto found = detailWindows_.find(closedSymbol);
        if (found != detailWindows_.end() && found.value() == window) {
            detailWindows_.erase(found);
        }
        if (config_.allowedActions.contains(
                QStringLiteral("premium_detail_unsubscribe"))) {
            send(QStringLiteral("premium_detail_unsubscribe"),
                 {{QStringLiteral("symbol"), closedSymbol}}, 15000);
        }
    });
    window->show();
    send(QStringLiteral("premium_detail_subscribe"),
         {{QStringLiteral("symbol"), symbol}}, 15000);
}

void PremiumPage::prepareForShutdown() {
    // Detail subscriptions live in the long-running Agent, not in this
    // transient window. Release every lease before MainWindow disconnects its
    // IPC client so closing/restarting the GUI cannot exhaust the four-symbol
    // A-side limit. Copy first because closeEvent normally mutates the hash.
    const auto symbols = detailWindows_.keys();
    const auto windows = detailWindows_.values();
    for (const QString &symbol : symbols) {
        if (config_.allowedActions.contains(
                QStringLiteral("premium_detail_unsubscribe"))) {
            send(QStringLiteral("premium_detail_unsubscribe"),
                 {{QStringLiteral("symbol"), symbol}}, 15000);
        }
    }
    for (const auto &guard : windows) {
        if (!guard) continue;
        disconnect(guard, &PremiumDetailWindow::closed, this, nullptr);
        guard->close();
    }
    detailWindows_.clear();
}

bool PremiumPage::addSignal(const QJsonObject &payload) {
    auto identity = [](QJsonObject value) {
        value.remove(QStringLiteral("backfill"));
        value.remove(QStringLiteral("replay"));
        value.remove(QStringLiteral("replayed"));
        const QString id = value.value(QStringLiteral("event_id")).toString();
        return id.isEmpty() ? QJsonDocument(value).toJson(QJsonDocument::Compact) : id.toUtf8();
    };
    const auto key = identity(payload);
    for (int row = 0; row < signalsTable_->rowCount(); ++row) {
        if (identity(signalsTable_->item(row, 0)->data(Qt::UserRole).toJsonObject()) == key)
            return false;
    }
    signalsTable_->insertRow(0);
    setCell(signalsTable_, 0, 0, firstValue(payload, {QStringLiteral("timestamp"), QStringLiteral("ts"), QStringLiteral("time")}), payload);
    setCell(signalsTable_, 0, 1, firstValue(payload, {QStringLiteral("symbol"), QStringLiteral("code")}));
    setCell(signalsTable_, 0, 2, firstValue(payload, {QStringLiteral("name"), QStringLiteral("display_name")}));
    setCell(signalsTable_, 0, 3, firstValue(payload, {QStringLiteral("model"), QStringLiteral("event"), QStringLiteral("kind")}));
    setCell(signalsTable_, 0, 4, firstValue(payload, {QStringLiteral("premium_pct"), QStringLiteral("premium_rate"), QStringLiteral("premium")}));
    setCell(signalsTable_, 0, 5, firstValue(payload, {QStringLiteral("window_sec"), QStringLiteral("window")}));
    setCell(signalsTable_, 0, 6, firstValue(payload, {QStringLiteral("severity"), QStringLiteral("level")}));
    if (signalsTable_->rowCount() > 2000) signalsTable_->removeRow(signalsTable_->rowCount() - 1);
    return true;
}

void PremiumPage::upsertSummary(const QJsonObject &payload) {
    const QString symbol = firstValue(payload, {QStringLiteral("symbol"), QStringLiteral("code")}, {});
    if (symbol.isEmpty()) return;
    int row = summaryRows_.value(symbol, -1);
    if (row < 0) {
        row = summariesTable_->rowCount();
        summariesTable_->insertRow(row);
        summaryRows_.insert(symbol, row);
    }
    setCell(summariesTable_, row, 0, symbol, payload);
    setCell(summariesTable_, row, 1, firstValue(payload, {QStringLiteral("name"), QStringLiteral("display_name")}));
    setCell(summariesTable_, row, 2, firstValue(payload, {QStringLiteral("last"), QStringLiteral("price")}));
    setCell(summariesTable_, row, 3, firstValue(payload, {QStringLiteral("iopv"), QStringLiteral("nav")}));
    setCell(summariesTable_, row, 4, firstValue(payload, {QStringLiteral("premium_pct"), QStringLiteral("premium_rate"), QStringLiteral("premium")}));
    setCell(summariesTable_, row, 5, firstValue(payload, {QStringLiteral("timestamp"), QStringLiteral("updated_at"), QStringLiteral("ts")}));
    setCell(summariesTable_, row, 6, firstValue(payload, {QStringLiteral("state"), QStringLiteral("status")}));
}

void PremiumPage::flushPendingSummaries() {
    if (pendingSummaries_.isEmpty()) return;
    const auto summaries = std::exchange(pendingSummaries_, {});
    summariesTable_->setUpdatesEnabled(false);
    for (const auto &payload : summaries) upsertSummary(payload);
    summariesTable_->setUpdatesEnabled(true);
    summariesTable_->viewport()->update();
}

void PremiumPage::populateHistory(const QJsonArray &records, const QJsonObject &statistics) {
    historyRecords_ = records;
    historyStatistics_ = statistics;
    renderHistoryPage(0);
}

void PremiumPage::renderHistoryPage(int page) {
    constexpr int PageSize = 2'000;
    const int recordCount = static_cast<int>(historyRecords_.size());
    const int pageCount = std::max(1, (recordCount + PageSize - 1) / PageSize);
    historyPage_ = std::clamp(page, 0, pageCount - 1);
    const int first = historyPage_ * PageSize;
    const int last = std::min(first + PageSize, recordCount);
    historyTable_->setUpdatesEnabled(false);
    historyTable_->setRowCount(last - first);
    int row = 0;
    for (int index = first; index < last; ++index) {
        const auto value = historyRecords_.at(index);
        const auto record = value.toObject();
        const auto values = premiumHistoryValues(record);
        for (int column = 0; column < values.size(); ++column) {
            setCell(historyTable_, row, column, values.at(column), column == 0 ? record : QJsonObject{});
        }
        ++row;
    }
    historyTable_->setUpdatesEnabled(true);
    historyPreviousButton_->setEnabled(historyPage_ > 0);
    historyNextButton_->setEnabled(historyPage_ + 1 < pageCount);
    const bool truncated = historyStatistics_.value(QStringLiteral("truncated")).toBool();
    historyStatus_->setText(
        QStringLiteral("已读取 %1 个文件，保留 %2 条；第 %3/%4 页显示 %5–%6，拒绝坏行 %7%8")
            .arg(historyStatistics_.value(QStringLiteral("files")).toInt())
            .arg(historyRecords_.size())
            .arg(historyPage_ + 1)
            .arg(pageCount)
            .arg(historyRecords_.isEmpty() ? 0 : first + 1)
            .arg(last)
            .arg(historyStatistics_.value(QStringLiteral("rejected_lines")).toInt())
            .arg(truncated ? QStringLiteral("（已达到记录数或字节安全上限）") : QString()));
}

WebullPage::WebullPage(ModuleConfig config, QWidget *parent)
    : ModulePage(std::move(config), parent) {
    auto *tabs = new QTabWidget;
    auto *book = new QWidget;
    auto *bookLayout = new QVBoxLayout(book);
    auto *bookActions = new QHBoxLayout;
    symbolEdit_ = new QLineEdit(config_.settings.value(QStringLiteral("default_symbol")).toString(QStringLiteral("XOP")));
    symbolEdit_->setMaximumWidth(150);
    symbolEdit_->setReadOnly(true);
    symbolEdit_->setToolTip(QStringLiteral("标的由原生 WebullEngine 配置决定；切换需修改配置并重启模块"));
    auto *load = new QPushButton(QStringLiteral("加载盘口"));
    staleLabel_ = textLabel(QStringLiteral("尚无行情"), QStringLiteral("stateWarn"));
    connect(load, &QPushButton::clicked, this, [this] {
        send(QStringLiteral("webull_get_book"), QJsonObject{{QStringLiteral("symbol"), symbolEdit_->text().trimmed()}});
    });
    bookActions->addWidget(textLabel(QStringLiteral("标的")));
    bookActions->addWidget(symbolEdit_);
    bookActions->addWidget(load);
    bookActions->addWidget(staleLabel_);
    bookActions->addStretch();
    bookLayout->addLayout(bookActions);
    bookTable_ = makeTable({QStringLiteral("方向"), QStringLiteral("档位"), QStringLiteral("价格"),
                            QStringLiteral("数量"), QStringLiteral("订单数")});
    bookLayout->addWidget(bookTable_);
    tabs->addTab(book, QStringLiteral("实时盘口"));
    clientsTable_ = makeTable({QStringLiteral("Client ID"), QStringLiteral("Remote"), QStringLiteral("连接时间"),
                               QStringLiteral("最后发送"), QStringLiteral("消息数")});
    tabs->addTab(clientsTable_, QStringLiteral("客户端"));

    auto *runtime = new QWidget;
    auto *runtimeLayout = new QVBoxLayout(runtime);
    auto *modes = new QHBoxLayout;
    for (const auto &pair : {qMakePair(QStringLiteral("自动"), QStringLiteral("auto")),
                             qMakePair(QStringLiteral("强制采集"), QStringLiteral("force_running")),
                             qMakePair(QStringLiteral("暂停采集"), QStringLiteral("force_stopped"))}) {
        auto *button = new QPushButton(pair.first);
        mutationButtons_.append(button);
        connect(button, &QPushButton::clicked, this, [this, pair] {
            send(QStringLiteral("webull_set_mode"), QJsonObject{{QStringLiteral("mode"), pair.second}});
        });
        modes->addWidget(button);
    }
    auto *restartBrowser = new QPushButton(QStringLiteral("重启浏览器"));
    auto *login = new QPushButton(QStringLiteral("打开登录窗口（可能启动采集）"));
    auto *startCollector = new QPushButton(QStringLiteral("立即启动采集"));
    auto *stopCollector = new QPushButton(QStringLiteral("立即停止采集"));
    mutationButtons_.append(startCollector);
    mutationButtons_.append(stopCollector);
    mutationButtons_.append(restartBrowser);
    mutationButtons_.append(login);
    connect(restartBrowser, &QPushButton::clicked, this, [this] { send(QStringLiteral("webull_restart_browser")); });
    connect(login, &QPushButton::clicked, this, [this] { send(QStringLiteral("webull_show_login")); });
    connect(startCollector, &QPushButton::clicked, this, [this] { send(QStringLiteral("webull_collector_start")); });
    connect(stopCollector, &QPushButton::clicked, this, [this] { send(QStringLiteral("webull_collector_stop")); });
    modes->addWidget(startCollector);
    modes->addWidget(stopCollector);
    modes->addWidget(restartBrowser);
    modes->addWidget(login);
    modes->addStretch();
    runtimeLayout->addLayout(modes);
    runtimeTree_ = makeTree();
    runtimeLayout->addWidget(runtimeTree_);
    tabs->addTab(runtime, QStringLiteral("运行与登录"));
    eventLog_ = new QPlainTextEdit;
    eventLog_->setObjectName(QStringLiteral("moduleEventLog"));
    eventLog_->setReadOnly(true);
    tabs->addTab(eventLog_, QStringLiteral("日志"));
    const bool initiallyEnabled = config_.controlEnabled && config_.ownership != QStringLiteral("shadow")
        && (config_.engine == QStringLiteral("native")
            || !config_.settings.value(QStringLiteral("control_base_url")).toString().isEmpty());
    for (auto *button : std::as_const(mutationButtons_)) button->setEnabled(initiallyEnabled);
    setContent(tabs);
}

void WebullPage::applySnapshot(const QJsonObject &message) {
    ModulePage::applySnapshot(message);
    const auto payload = message.value(QStringLiteral("payload")).toObject();
    fillTree(runtimeTree_, payload);
    const auto telemetry = payload.value(QStringLiteral("telemetry")).toObject();
    const bool nativeEngine = config_.engine == QStringLiteral("native");
    const bool canMutate = logicalControlAllowed(message)
        && (nativeEngine || !config_.settings.value(QStringLiteral("control_base_url")).toString().isEmpty());
    for (auto *button : std::as_const(mutationButtons_)) {
        button->setEnabled(canMutate);
        button->setToolTip(canMutate
            ? (nativeEngine ? QStringLiteral("直接调用 Agent 内原生 WebullEngine")
                            : QStringLiteral("通过 127.0.0.1 Webull 控制桥执行并等待最终 ACK"))
            : QStringLiteral("需启用原生 engine 或 logic/owner 控制权限"));
    }
    updateBook(telemetry.value(QStringLiteral("book")).toObject());
    updateClients(telemetry.value(QStringLiteral("clients")));
    const QString freshness = firstValue(telemetry, {QStringLiteral("freshness"), QStringLiteral("status.data_state"),
                                                      QStringLiteral("status.data.state")}, QStringLiteral("unknown"));
    staleLabel_->setText(freshness.contains(QStringLiteral("fresh"), Qt::CaseInsensitive)
                             ? QStringLiteral("数据新鲜")
                             : QStringLiteral("数据过期 / %1").arg(operatorStateText(freshness)));
    staleLabel_->setObjectName(freshness.contains(QStringLiteral("fresh"), Qt::CaseInsensitive)
                                   ? QStringLiteral("stateGood") : QStringLiteral("stateWarn"));
    staleLabel_->style()->unpolish(staleLabel_);
    staleLabel_->style()->polish(staleLabel_);
}

void WebullPage::updateBusyControls() {
    ModulePage::updateBusyControls();
    const bool allowed = logicalControlAllowed(lastSnapshot_)
        && (config_.engine == QStringLiteral("native")
            || !config_.settings.value(QStringLiteral("control_base_url")).toString().isEmpty());
    for (auto *button : std::as_const(mutationButtons_)) button->setEnabled(allowed);
}

void WebullPage::applyEvent(const QJsonObject &message) {
    ModulePage::applyEvent(message);
    const QString kind = message.value(QStringLiteral("event_kind")).toString();
    const auto payload = eventPayload(message);
    if (kind.contains(QStringLiteral("book"), Qt::CaseInsensitive) ||
        kind.contains(QStringLiteral("depth"), Qt::CaseInsensitive)) updateBook(payload);
    if (kind.contains(QStringLiteral("client"), Qt::CaseInsensitive)) updateClients(payload);
}

void WebullPage::updateBook(const QJsonObject &book) {
    if (book.isEmpty()) return;
    QJsonArray rows;
    auto appendSide = [&rows, &book](const QString &side) {
        const auto levels = book.value(side).toArray();
        int level = 1;
        for (const auto &value : levels) {
            auto object = value.toObject();
            object.insert(QStringLiteral("_side"), side);
            object.insert(QStringLiteral("_level"), level++);
            rows.append(object);
        }
    };
    appendSide(QStringLiteral("asks"));
    appendSide(QStringLiteral("bids"));
    bookTable_->setRowCount(rows.size());
    int row = 0;
    for (const auto &value : rows) {
        const auto object = value.toObject();
        setCell(bookTable_, row, 0, object.value(QStringLiteral("_side")).toString(), object);
        setCell(bookTable_, row, 1, QString::number(object.value(QStringLiteral("_level")).toInt()));
        setCell(bookTable_, row, 2, firstValue(object, {QStringLiteral("price"), QStringLiteral("px")}));
        setCell(bookTable_, row, 3, firstValue(object, {QStringLiteral("size"), QStringLiteral("quantity"), QStringLiteral("volume")}));
        setCell(bookTable_, row, 4, firstValue(object, {QStringLiteral("orders"), QStringLiteral("order_count")}));
        ++row;
    }
}

void WebullPage::updateClients(const QJsonValue &clients) {
    const auto rows = arrayCandidate(clients);
    clientsTable_->setRowCount(rows.size());
    int row = 0;
    for (const auto &value : rows) {
        const auto object = value.toObject();
        setCell(clientsTable_, row, 0, firstValue(object, {QStringLiteral("client_id"), QStringLiteral("id")}), object);
        setCell(clientsTable_, row, 1, firstValue(object, {QStringLiteral("remote"), QStringLiteral("remote_addr")}));
        setCell(clientsTable_, row, 2, firstValue(object, {QStringLiteral("connected_at"), QStringLiteral("created_at")}));
        setCell(clientsTable_, row, 3, firstValue(object, {QStringLiteral("last_sent_at"), QStringLiteral("last_send")}));
        setCell(clientsTable_, row, 4, firstValue(object, {QStringLiteral("message_count"), QStringLiteral("sent_count")}));
        ++row;
    }
}

RealtimePage::RealtimePage(ModuleConfig config, QWidget *parent)
    : ModulePage(std::move(config), parent) {
    auto *tabs = new QTabWidget;
    auto *main = new QWidget;
    auto *mainLayout = new QVBoxLayout(main);
    auto *controls = new QHBoxLayout;
    soundEnabled_ = new QCheckBox(QStringLiteral("变化提示音"));
    popupEnabled_ = new QCheckBox(QStringLiteral("变化系统通知"));
    QSettings realtimeAlertSettings;
    soundEnabled_->setChecked(realtimeAlertSettings.value(QStringLiteral("alerts/redemption_sound"), true).toBool());
    popupEnabled_->setChecked(realtimeAlertSettings.value(QStringLiteral("alerts/redemption_popup"), true).toBool());
    connect(soundEnabled_, &QCheckBox::toggled, this, [](bool enabled) {
        QSettings().setValue(QStringLiteral("alerts/redemption_sound"), enabled);
    });
    connect(popupEnabled_, &QCheckBox::toggled, this, [](bool enabled) {
        QSettings().setValue(QStringLiteral("alerts/redemption_popup"), enabled);
    });
    for (const auto &pair : {qMakePair(QStringLiteral("开始监控"), QStringLiteral("redemption_monitor_start")),
                             qMakePair(QStringLiteral("停止监控"), QStringLiteral("redemption_monitor_stop")),
                             qMakePair(QStringLiteral("启动 Wind"), QStringLiteral("redemption_wind_start")),
                             qMakePair(QStringLiteral("安全退出 Wind"), QStringLiteral("redemption_wind_shutdown_cleanup")),
                             qMakePair(QStringLiteral("刷新 PCF"), QStringLiteral("redemption_pcf_refresh"))}) {
        auto *button = new QPushButton(pair.first);
        connect(button, &QPushButton::clicked, this, [this, pair] { send(pair.second, {}, 120000); });
        mutationButtons_.append(button);
        controls->addWidget(button);
    }
    controls->addStretch();
    controls->addWidget(soundEnabled_);
    controls->addWidget(popupEnabled_);
    auto *alertSettingsButton=new QPushButton(QStringLiteral("提醒设置"));
    controls->addWidget(alertSettingsButton);
    connect(alertSettingsButton,&QPushButton::clicked,this,[this]{AlertController::configure(this);});
    mainLayout->addLayout(controls);
    snapshotTable_ = makeTable({QStringLiteral("标的"), QStringLiteral("名称"), QStringLiteral("状态"),
                                QStringLiteral("买份额"), QStringLiteral("卖份额"), QStringLiteral("净份额"),
                                QStringLiteral("买篮子"), QStringLiteral("卖篮子"), QStringLiteral("净篮子"),
                                QStringLiteral("可申购篮子"), QStringLiteral("机会"), QStringLiteral("时间"),
                                QStringLiteral("最近变化")});
    snapshotTable_->setObjectName(QStringLiteral("realtimeSnapshotTable"));
    mainLayout->addWidget(snapshotTable_);
    connect(snapshotTable_, &QTableWidget::cellDoubleClicked, this, [this](int row, int) { openPcfForRow(row); });
    tabs->addTab(main, QStringLiteral("实时监控"));

    auto *watchlist = new QWidget;
    auto *watchlistLayout = new QVBoxLayout(watchlist);
    watchlistLayout->addWidget(textLabel(
        QStringLiteral("每行一个 6 位深市 ETF 代码。保存观察列表可能重建 Wind 订阅，仅 logic/owner 且本机 loopback 可执行。"),
        QStringLiteral("secondaryText")));
    watchlistEdit_ = new QPlainTextEdit;
    watchlistEdit_->setObjectName(QStringLiteral("realtimeWatchlistEdit"));
    watchlistEdit_->setPlaceholderText(QStringLiteral("159518\n159393"));
    watchlistEdit_->setMaximumHeight(150);
    watchlistLayout->addWidget(watchlistEdit_);
    auto *saveWatchlist = new QPushButton(QStringLiteral("保存观察列表"));
    saveWatchlist->setObjectName(QStringLiteral("realtimeSaveWatchlist"));
    connect(saveWatchlist, &QPushButton::clicked, this, [this] {
        const QStringList symbols = lines(watchlistEdit_);
        const QJsonObject arguments{{QStringLiteral("symbols"), QJsonArray::fromStringList(symbols)}};
        if (symbols.isEmpty()) {
            QMessageBox::information(
                this, QStringLiteral("观察列表不能为空"),
                QStringLiteral("实时申购赎回服务至少需要 1 个标的；未发送任何变更。"));
            return;
        }
        send(QStringLiteral("redemption_set_watchlist"),
             arguments,
             120000);
    });
    mutationButtons_.append(saveWatchlist);
    watchlistLayout->addWidget(saveWatchlist, 0, Qt::AlignLeft);

    auto *nameForm = new QFormLayout;
    nameSymbolEdit_ = new QLineEdit;
    nameSymbolEdit_->setObjectName(QStringLiteral("realtimeNameSymbol"));
    nameSymbolEdit_->setPlaceholderText(QStringLiteral("159518"));
    symbolNameEdit_ = new QLineEdit;
    symbolNameEdit_->setObjectName(QStringLiteral("realtimeSymbolName"));
    symbolNameEdit_->setMaxLength(40);
    nameForm->addRow(QStringLiteral("标的代码"), nameSymbolEdit_);
    nameForm->addRow(QStringLiteral("自定义名称"), symbolNameEdit_);
    watchlistLayout->addLayout(nameForm);
    auto *saveName = new QPushButton(QStringLiteral("保存标的名称"));
    saveName->setObjectName(QStringLiteral("realtimeSaveSymbolName"));
    connect(saveName, &QPushButton::clicked, this, [this] {
        const QString name = symbolNameEdit_->text().simplified();
        if (name.isEmpty()) {
            const auto answer = QMessageBox::warning(
                this, QStringLiteral("确认删除自定义名称"),
                QStringLiteral("空名称会删除该标的的自定义名称。是否继续？"),
                QMessageBox::Yes | QMessageBox::No, QMessageBox::No);
            if (answer != QMessageBox::Yes) return;
        }
        send(QStringLiteral("redemption_set_symbol_name"),
             QJsonObject{{QStringLiteral("symbol"), nameSymbolEdit_->text().trimmed()},
                         {QStringLiteral("name"), name}},
             120000);
    });
    mutationButtons_.append(saveName);
    watchlistLayout->addWidget(saveName, 0, Qt::AlignLeft);
    watchlistLayout->addStretch();
    tabs->addTab(watchlist, QStringLiteral("观察列表与名称"));

    auto *history = new QWidget;
    auto *historyLayout = new QVBoxLayout(history);
    auto *filters = new QHBoxLayout;
    historyDateEdit_ = new QLineEdit(QDate::currentDate().toString(Qt::ISODate));
    historySymbolEdit_ = new QLineEdit;
    historySymbolEdit_->setPlaceholderText(QStringLiteral("可选标的"));
    auto *query = new QPushButton(QStringLiteral("查询历史"));
    connect(query, &QPushButton::clicked, this, [this] {
        send(QStringLiteral("redemption_get_history"),
             QJsonObject{{QStringLiteral("date"), historyDateEdit_->text().trimmed()},
                         {QStringLiteral("symbol"), historySymbolEdit_->text().trimmed()}});
    });
    filters->addWidget(textLabel(QStringLiteral("日期")));
    filters->addWidget(historyDateEdit_);
    filters->addWidget(historySymbolEdit_);
    filters->addWidget(query);
    filters->addStretch();
    historyLayout->addLayout(filters);
    historyTable_ = makeTable({QStringLiteral("时间"), QStringLiteral("标的"), QStringLiteral("方向"),
                               QStringLiteral("原值"), QStringLiteral("新值"), QStringLiteral("变化"),
                               QStringLiteral("篮子"), QStringLiteral("状态")});
    historyLayout->addWidget(historyTable_);
    tabs->addTab(history, QStringLiteral("变化历史"));
    runtimeTree_ = makeTree();
    tabs->addTab(runtimeTree_, QStringLiteral("主机 / Wind / QMT 状态"));
    eventLog_ = new QPlainTextEdit;
    eventLog_->setObjectName(QStringLiteral("moduleEventLog"));
    eventLog_->setReadOnly(true);
    tabs->addTab(eventLog_, QStringLiteral("服务日志"));
    setContent(tabs);
    updateMutationControlState({});
}

void RealtimePage::applySnapshot(const QJsonObject &message) {
    ModulePage::applySnapshot(message);
    const auto payload = message.value(QStringLiteral("payload")).toObject();
    fillTree(runtimeTree_, payload);
    const auto telemetry = payload.value(QStringLiteral("telemetry")).toObject();
    qmtBackends_ = telemetry.value(QStringLiteral("qmt_backends")).toObject();
    updateMutationControlState(payload);
    const auto watchlist = telemetry.value(QStringLiteral("watchlist")).toArray();
    if (!watchlist.isEmpty() && watchlistEdit_ && !watchlistEdit_->hasFocus()
        && !watchlistEdit_->document()->isModified()) {
        QStringList symbols;
        for (const auto &entry : watchlist) {
            const QString symbol = entry.toString().trimmed();
            if (!symbol.isEmpty()) symbols.append(symbol);
        }
        watchlistEdit_->setPlainText(symbols.join(u'\n'));
        watchlistEdit_->document()->setModified(false);
    }
    updateRows(telemetry.value(QStringLiteral("snapshot")), true);
    for (const auto &window : std::as_const(pcfWindows_)) {
        if (window) {
            window->setLiveOrdersEnabled(
                telemetry.value(QStringLiteral("health")).toObject()
                    .value(QStringLiteral("live_orders_allowed")).toBool(false));
            window->applyQmtTelemetry(qmtBackends_);
        }
    }
}

void RealtimePage::applyEvent(const QJsonObject &message) {
    ModulePage::applyEvent(message);
    const QString kind = message.value(QStringLiteral("event_kind")).toString();
    const auto payload = eventPayload(message);
    if (kind.contains(QStringLiteral("snapshot"), Qt::CaseInsensitive)) {
        updateRows(payload, true);
    } else if (kind.contains(QStringLiteral("change"), Qt::CaseInsensitive)) {
        updateRows(payload, false);
        const auto changedRows = arrayCandidate(payload);
        QStringList descriptions;
        for (const auto &value : changedRows) {
            if (!value.isObject()) continue;
            const auto envelope = value.toObject();
            const auto current = envelope.value(QStringLiteral("current")).isObject()
                ? envelope.value(QStringLiteral("current")).toObject() : envelope;
            const QString symbol = realtimeSymbol(current);
            const QString changes = lastChangesText(current.value(QStringLiteral("last_change")));
            descriptions.append(QStringLiteral("%1 %2").arg(symbol, changes));
        }
        if (descriptions.isEmpty()) {
            const QString symbol = realtimeSymbol(payload);
            if (!symbol.isEmpty()) descriptions.append(symbol);
        }
        if (!descriptions.isEmpty()
            && !message.value(QStringLiteral("replayed")).toBool(false)
            && !payload.value(QStringLiteral("backfill")).toBool(false)
            && !payload.value(QStringLiteral("replay")).toBool(false)) {
            emit alertRequested(config_.id, QStringLiteral("ETF 申购赎回数据变化"),
                                descriptions.join(QStringLiteral("；")).left(500),
                                soundEnabled_->isChecked(), popupEnabled_->isChecked());
        }
    }
    if (kind.contains(QStringLiteral("history"), Qt::CaseInsensitive)) {
        const auto rows = arrayCandidate(payload);
        const int pageIndex = payload.value(QStringLiteral("page_index")).toInt(0);
        if (pageIndex == 0) historyTable_->setRowCount(0);
        int row = historyTable_->rowCount();
        historyTable_->setRowCount(row + rows.size());
        for (const auto &value : rows) {
            const auto object = value.toObject();
            const QStringList keys{QStringLiteral("timestamp"), QStringLiteral("symbol"), QStringLiteral("direction"),
                                   QStringLiteral("old_value"), QStringLiteral("new_value"), QStringLiteral("delta"),
                                   QStringLiteral("basket_count"), QStringLiteral("status")};
            for (int column = 0; column < keys.size(); ++column) setCell(historyTable_, row, column, firstValue(object, {keys[column]}), object);
            ++row;
        }
    }
    if (kind.contains(QStringLiteral("pcf"), Qt::CaseInsensitive)) {
        const QString symbol = firstValue(payload, {QStringLiteral("symbol"), QStringLiteral("code")}, {});
        if (!symbol.isEmpty() && pcfWindows_.contains(symbol) && pcfWindows_.value(symbol)) {
            pcfWindows_.value(symbol)->applyData(payload);
        }
    }
}

void RealtimePage::applyCommand(const QJsonObject &message) {
    ModulePage::applyCommand(message);
    if (message.value(QStringLiteral("state")).toString() != QStringLiteral("succeeded")) return;
    const QString action = message.value(QStringLiteral("action")).toString();
    if (action == QStringLiteral("redemption_set_watchlist") && watchlistEdit_) {
        watchlistEdit_->document()->setModified(false);
    } else if (action == QStringLiteral("redemption_set_symbol_name")) {
        nameSymbolEdit_->clear();
        symbolNameEdit_->clear();
    }
}

void RealtimePage::updateMutationControlState(const QJsonObject &payload) {
    const bool allowed = logicalControlAllowed(QJsonObject{{QStringLiteral("payload"), payload}});
    mutationAllowed_ = allowed;
    for (auto *button : std::as_const(mutationButtons_)) {
        button->setEnabled(allowed);
        button->setToolTip(allowed
            ? QStringLiteral("命令仍需通过 RealtimeClient 的 loopback 安全检查")
            : QStringLiteral("需设置 control_enabled=true 且 ownership=logic/owner"));
    }
    for (const auto &window : std::as_const(pcfWindows_)) {
        if (window) window->setMutationEnabled(allowed);
    }
}

void RealtimePage::updateBusyControls() {
    ModulePage::updateBusyControls();
    updateMutationControlState(lastSnapshot_.value(QStringLiteral("payload")).toObject());
}

void RealtimePage::updateRows(const QJsonValue &snapshot, bool fullSnapshot) {
    const auto rows = arrayCandidate(snapshot);
    if (fullSnapshot) realtimeItems_.clear();
    for (const auto &value : rows) {
        if (!value.isObject()) continue;
        const auto envelope = value.toObject();
        QJsonObject item = envelope.value(QStringLiteral("current")).isObject()
            ? envelope.value(QStringLiteral("current")).toObject() : envelope;
        QString symbol = realtimeSymbol(item);
        if (symbol.isEmpty()) symbol = realtimeSymbol(envelope);
        if (symbol.isEmpty()) continue;
        if (!item.contains(QStringLiteral("symbol"))) item.insert(QStringLiteral("symbol"), symbol);
        realtimeItems_.insert(symbol, item);
    }

    QStringList symbols = realtimeItems_.keys();
    std::sort(symbols.begin(), symbols.end());
    snapshotTable_->setRowCount(symbols.size());
    int row = 0;
    for (const QString &symbol : symbols) {
        const auto item = realtimeItems_.value(symbol);
        const auto values = item.value(QStringLiteral("values")).toObject();
        const auto opportunity = item.value(QStringLiteral("opportunity")).toObject();
        const bool nested = !values.isEmpty();
        setCell(snapshotTable_, row, 0, symbol, item);
        setCell(snapshotTable_, row, 1, firstValue(item, {QStringLiteral("name")}));
        setCell(snapshotTable_, row, 2, realtimeStatusText(item.value(QStringLiteral("status"))));
        setCell(snapshotTable_, row, 3, nested ? shareText(values.value(QStringLiteral("etfbuyamount")))
                                               : firstValue(item, {QStringLiteral("buy_shares"), QStringLiteral("etfbuyamount")}));
        setCell(snapshotTable_, row, 4, nested ? shareText(values.value(QStringLiteral("etfsellamount")))
                                               : firstValue(item, {QStringLiteral("sell_shares"), QStringLiteral("etfsellamount")}));
        setCell(snapshotTable_, row, 5, nested ? shareText(values.value(QStringLiteral("netamount")), true)
                                               : firstValue(item, {QStringLiteral("net_shares"), QStringLiteral("netamount")}));
        setCell(snapshotTable_, row, 6, nested ? basketText(item, QStringLiteral("etfbuyamount"))
                                               : firstValue(item, {QStringLiteral("buy_baskets")}));
        setCell(snapshotTable_, row, 7, nested ? basketText(item, QStringLiteral("etfsellamount"))
                                               : firstValue(item, {QStringLiteral("sell_baskets")}));
        setCell(snapshotTable_, row, 8, nested ? basketText(item, QStringLiteral("netamount"), true)
                                               : firstValue(item, {QStringLiteral("net_baskets")}));
        setCell(snapshotTable_, row, 9, availableCreationBaskets(item));
        setCell(snapshotTable_, row, 10,
                !opportunity.value(QStringLiteral("label")).toString().isEmpty()
                    ? opportunity.value(QStringLiteral("label")).toString()
                    : firstValue(item, {QStringLiteral("opportunity"), QStringLiteral("actionable")}, QStringLiteral("待确认")));
        setCell(snapshotTable_, row, 11,
                firstValue(item, {QStringLiteral("updated_at"), QStringLiteral("timestamp")}));
        setCell(snapshotTable_, row, 12,
                lastChangesText(item.contains(QStringLiteral("last_change"))
                                    ? item.value(QStringLiteral("last_change")) : item.value(QStringLiteral("change"))));
        ++row;
    }
}

void RealtimePage::openPcfForRow(int row) {
    auto *item = snapshotTable_->item(row, 0);
    if (!item) return;
    const QString symbol = item->text();
    if (symbol.isEmpty() || symbol == QStringLiteral("—")) return;
    auto window = pcfWindows_.value(symbol);
    if (!window) {
        window = new PcfDetailWindow(symbol, this);
        pcfWindows_.insert(symbol, window);
        connect(window, &QObject::destroyed, this, [this, symbol] { pcfWindows_.remove(symbol); });
        connect(window, &PcfDetailWindow::qmtCommandRequested, this,
                [this](const QString &action, const QJsonObject &arguments, int deadlineMs) {
            send(action, arguments, deadlineMs);
        });
    }
    window->setMutationEnabled(mutationAllowed_);
    window->setLiveOrdersEnabled(
        lastSnapshot_.value(QStringLiteral("payload")).toObject()
            .value(QStringLiteral("telemetry")).toObject()
            .value(QStringLiteral("health")).toObject()
            .value(QStringLiteral("live_orders_allowed")).toBool(false));
    window->applyQmtTelemetry(qmtBackends_);
    window->show();
    window->raise();
    window->activateWindow();
    send(QStringLiteral("redemption_get_pcf"),
         QJsonObject{{QStringLiteral("symbol"), symbol}});
}

PcfDetailWindow::PcfDetailWindow(QString symbol, QWidget *parent)
    : QWidget(parent, Qt::Window), symbol_(std::move(symbol)) {
    setAttribute(Qt::WA_DeleteOnClose);
    setWindowTitle(QStringLiteral("%1 · PCF / QMT 二级详情").arg(symbol_));
    resize(980, 720);
    auto *layout = new QVBoxLayout(this);
    status_ = textLabel(QStringLiteral("正在请求 PCF…"), QStringLiteral("stateIdle"));
    layout->addWidget(status_);
    auto *tabs = new QTabWidget;
    auto add = [tabs](const QString &title) {
        auto *editor = new QPlainTextEdit;
        editor->setReadOnly(true);
        tabs->addTab(editor, title);
        return editor;
    };
    summary_ = add(QStringLiteral("清单摘要"));
    components_ = add(QStringLiteral("成分证券"));
    tabs->addTab(buildQmtTab(QStringLiteral("QMT1")), QStringLiteral("QMT1"));
    tabs->addTab(buildQmtTab(QStringLiteral("QMT2")), QStringLiteral("QMT2"));
    layout->addWidget(tabs, 1);
}

QWidget *PcfDetailWindow::buildQmtTab(const QString &backend) {
    auto *page = new QWidget;
    page->setObjectName(QStringLiteral("qmtTab_%1").arg(backend));
    auto *layout = new QVBoxLayout(page);
    QmtWidgets widgets;

    auto *top = new QHBoxLayout;
    widgets.state = textLabel(QStringLiteral("%1 尚未连接").arg(backend), QStringLiteral("stateIdle"));
    widgets.connectButton = new QPushButton(QStringLiteral("连接"));
    widgets.disconnectButton = new QPushButton(QStringLiteral("断开"));
    widgets.syncButton = new QPushButton(QStringLiteral("重新全量同步"));
    widgets.connectButton->setObjectName(QStringLiteral("qmtConnect_%1").arg(backend));
    widgets.disconnectButton->setObjectName(QStringLiteral("qmtDisconnect_%1").arg(backend));
    widgets.syncButton->setObjectName(QStringLiteral("qmtSync_%1").arg(backend));
    connect(widgets.connectButton, &QPushButton::clicked, this, [this, backend] {
        emit qmtCommandRequested(QStringLiteral("redemption_qmt_connect"),
                                 {{QStringLiteral("backend"), backend}}, 20000);
    });
    connect(widgets.disconnectButton, &QPushButton::clicked, this, [this, backend] {
        emit qmtCommandRequested(QStringLiteral("redemption_qmt_disconnect"),
                                 {{QStringLiteral("backend"), backend}}, 5000);
    });
    connect(widgets.syncButton, &QPushButton::clicked, this, [this, backend] {
        emit qmtCommandRequested(QStringLiteral("redemption_qmt_sync"),
                                 {{QStringLiteral("backend"), backend}}, 15000);
    });
    top->addWidget(widgets.state);
    top->addStretch();
    top->addWidget(widgets.connectButton);
    top->addWidget(widgets.disconnectButton);
    top->addWidget(widgets.syncButton);
    layout->addLayout(top);

    auto *trade = new QHBoxLayout;
    trade->addWidget(textLabel(QStringLiteral("交易保护：只接受 1 篮；按钮必须双击，随后主窗口还会再次确认。"),
                               QStringLiteral("attention")), 1);
    widgets.purchaseButton = new QPushButton(QStringLiteral("双击申购 1 篮"));
    widgets.redeemButton = new QPushButton(QStringLiteral("双击赎回 1 篮"));
    widgets.purchaseButton->setObjectName(QStringLiteral("qmtPurchase_%1").arg(backend));
    widgets.redeemButton->setObjectName(QStringLiteral("qmtRedeem_%1").arg(backend));
    for (auto *button : {widgets.purchaseButton, widgets.redeemButton}) {
        button->setProperty("riskAction", true);
        button->setProperty("qmt_backend", backend);
        button->installEventFilter(this);
    }
    widgets.purchaseButton->setProperty("qmt_side", QStringLiteral("PURCHASE"));
    widgets.redeemButton->setProperty("qmt_side", QStringLiteral("REDEEM"));
    trade->addWidget(widgets.purchaseButton);
    trade->addWidget(widgets.redeemButton);
    layout->addLayout(trade);

    auto *details = new QTabWidget;
    widgets.runtime = makeTree();
    widgets.runtime->setObjectName(QStringLiteral("qmtRuntime_%1").arg(backend));
    details->addTab(widgets.runtime, QStringLiteral("连接 / 资金"));
    widgets.positions = makeTable({QStringLiteral("代码"), QStringLiteral("名称"), QStringLiteral("持仓"),
                                   QStringLiteral("可用"), QStringLiteral("市值")});
    widgets.positions->setObjectName(QStringLiteral("qmtPositions_%1").arg(backend));
    details->addTab(widgets.positions, QStringLiteral("持仓"));
    widgets.orders = makeTable({QStringLiteral("时间"), QStringLiteral("代码"), QStringLiteral("方向"),
                                QStringLiteral("状态"), QStringLiteral("数量"), QStringLiteral("成交"),
                                QStringLiteral("委托号")});
    widgets.orders->setObjectName(QStringLiteral("qmtOrders_%1").arg(backend));
    details->addTab(widgets.orders, QStringLiteral("当日委托"));
    widgets.lastResult = new QPlainTextEdit;
    widgets.lastResult->setReadOnly(true);
    widgets.lastResult->setObjectName(QStringLiteral("qmtResult_%1").arg(backend));
    details->addTab(widgets.lastResult, QStringLiteral("最后指令结果"));
    layout->addWidget(details, 1);

    qmtWidgets_.insert(backend, widgets);
    return page;
}

void PcfDetailWindow::setMutationEnabled(bool enabled) {
    mutationEnabled_ = enabled;
    for (auto it = qmtWidgets_.begin(); it != qmtWidgets_.end(); ++it) {
        auto &widgets = it.value();
        for (auto *button : {widgets.connectButton, widgets.disconnectButton,
                             widgets.syncButton}) {
            if (!button) continue;
            button->setEnabled(enabled);
            button->setToolTip(enabled
                ? QStringLiteral("通过 Hub Agent 的独立 %1 客户端执行").arg(it.key())
                : QStringLiteral("需 control_enabled=true 且 ownership=logic/owner"));
        }
        for (auto *button : {widgets.purchaseButton, widgets.redeemButton}) {
            if (!button) continue;
            button->setEnabled(enabled && liveOrdersEnabled_);
            button->setToolTip(!enabled
                ? QStringLiteral("需先开启模块逻辑控制权限")
                : liveOrdersEnabled_
                    ? QStringLiteral("实盘下单已启用；需双击并再次确认")
                    : QStringLiteral("实盘下单未启用；QMT 连接和同步仍可正常使用"));
        }
    }
}

void PcfDetailWindow::setLiveOrdersEnabled(bool enabled) {
    liveOrdersEnabled_ = enabled;
    setMutationEnabled(mutationEnabled_);
}

bool PcfDetailWindow::eventFilter(QObject *watched, QEvent *event) {
    if (event->type() == QEvent::MouseButtonDblClick) {
        auto *button = qobject_cast<QPushButton *>(watched);
        if (button && !button->property("qmt_side").toString().isEmpty()) {
            if (mutationEnabled_ && button->isEnabled()) {
                emit qmtCommandRequested(
                    QStringLiteral("redemption_qmt_order"),
                    {{QStringLiteral("backend"), button->property("qmt_backend").toString()},
                     {QStringLiteral("symbol"), symbol_},
                     {QStringLiteral("side"), button->property("qmt_side").toString()}},
                    20000);
            }
            return true;
        }
    }
    return QWidget::eventFilter(watched, event);
}

void PcfDetailWindow::applyQmtTelemetry(const QJsonObject &backends) {
    for (auto it = qmtWidgets_.cbegin(); it != qmtWidgets_.cend(); ++it) {
        QJsonObject snapshot = backends.value(it.key()).toObject();
        if (snapshot.isEmpty()) snapshot = backends.value(it.key().toLower()).toObject();
        updateQmtTab(it.key(), snapshot);
    }
}

void PcfDetailWindow::updateQmtTab(const QString &backend, const QJsonObject &snapshot) {
    if (!qmtWidgets_.contains(backend)) return;
    auto &widgets = qmtWidgets_[backend];
    if (snapshot.isEmpty()) {
        widgets.state->setText(QStringLiteral("%1 尚无状态").arg(backend));
        fillTree(widgets.runtime, {{QStringLiteral("backend"), backend},
                                   {QStringLiteral("state"), QStringLiteral("unknown")}});
        widgets.positions->setRowCount(0);
        widgets.orders->setRowCount(0);
        widgets.lastResult->setPlainText(QStringLiteral("尚无指令结果"));
        return;
    }

    const QString state = snapshot.value(QStringLiteral("connection_state")).toString(
        snapshot.value(QStringLiteral("state")).toString(QStringLiteral("unknown")));
    const bool ready = snapshot.value(QStringLiteral("ready")).toBool();
    const QString cash = displayValue(snapshot.value(QStringLiteral("available_cash")));
    widgets.state->setText(QStringLiteral("%1 · %2 · 可用资金 %3 · 节流剩余 %4 ms")
                               .arg(backend, ready ? QStringLiteral("就绪") : operatorStateText(state), cash,
                                    displayValue(snapshot.value(QStringLiteral("throttle_remaining_ms")), QStringLiteral("0"))));
    widgets.state->setObjectName(ready ? QStringLiteral("stateGood")
                                       : state == QStringLiteral("disconnected") ? QStringLiteral("stateIdle")
                                                                                   : QStringLiteral("stateWarn"));
    widgets.state->style()->unpolish(widgets.state);
    widgets.state->style()->polish(widgets.state);
    fillTree(widgets.runtime, snapshot);

    QJsonArray positions;
    for (const auto &value : snapshot.value(QStringLiteral("positions")).toArray()) {
        if (value.isObject() && belongsToSymbol(value.toObject(), symbol_)) positions.append(value);
    }
    widgets.positions->setRowCount(positions.size());
    int row = 0;
    for (const auto &value : positions) {
        const auto position = value.toObject();
        setCell(widgets.positions, row, 0, firstValue(position, {QStringLiteral("code"), QStringLiteral("symbol")}), position);
        setCell(widgets.positions, row, 1, firstValue(position, {QStringLiteral("name"), QStringLiteral("stock_name")}));
        setCell(widgets.positions, row, 2, firstValue(position, {QStringLiteral("volume"), QStringLiteral("total_volume"), QStringLiteral("qty")}));
        setCell(widgets.positions, row, 3, firstValue(position, {QStringLiteral("can_use_volume"), QStringLiteral("available_volume"), QStringLiteral("available")}));
        setCell(widgets.positions, row, 4, firstValue(position, {QStringLiteral("market_value"), QStringLiteral("value")}));
        ++row;
    }

    QJsonArray orders;
    for (const auto &value : snapshot.value(QStringLiteral("orders")).toArray()) {
        if (value.isObject() && belongsToSymbol(value.toObject(), symbol_)) orders.append(value);
    }
    widgets.orders->setRowCount(orders.size());
    row = 0;
    for (const auto &value : orders) {
        const auto order = value.toObject();
        const QStringList keys{QStringLiteral("time"), QStringLiteral("code"), QStringLiteral("direction"),
                               QStringLiteral("status"), QStringLiteral("qty"), QStringLiteral("traded_qty"),
                               QStringLiteral("order_id")};
        for (int column = 0; column < keys.size(); ++column) {
            setCell(widgets.orders, row, column, firstValue(order, {keys[column]}), order);
        }
        ++row;
    }
    widgets.lastResult->setPlainText(pretty(snapshot.value(QStringLiteral("last_result"))));
}

void PcfDetailWindow::applyData(const QJsonObject &object) {
    status_->setText(QStringLiteral("已更新 %1").arg(utcNow()));
    summary_->setPlainText(pretty(object.value(QStringLiteral("summary")).isUndefined()
                                      ? QJsonValue(object) : object.value(QStringLiteral("summary"))));
    components_->setPlainText(pretty(object.value(QStringLiteral("components"))));
    QJsonObject embedded;
    if (object.value(QStringLiteral("qmt1")).isObject()) {
        embedded.insert(QStringLiteral("QMT1"), object.value(QStringLiteral("qmt1")));
    }
    if (object.value(QStringLiteral("qmt2")).isObject()) {
        embedded.insert(QStringLiteral("QMT2"), object.value(QStringLiteral("qmt2")));
    }
    if (!embedded.isEmpty()) applyQmtTelemetry(embedded);
}

} // namespace hub
