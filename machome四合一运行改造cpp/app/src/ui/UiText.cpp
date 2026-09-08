#include "ui/UiText.h"
#include "common/JsonUtil.h"
#include <QDateTime>
#include <QHash>

namespace hub::ui {
namespace {
QHash<QString, QString> dictionary(const char *text) {
    QHash<QString, QString> result;
    for (const auto &line : QString::fromUtf8(text).split(u'\n')) {
        const auto at = line.indexOf(u'=');
        if (at > 0) result.insert(line.first(at), line.mid(at + 1));
    }
    return result;
}
}
QString stateText(const QString &raw) {
    static const auto names = dictionary(R"(unknown=未知
no_data=暂无数据
scheduled_idle=按计划休息
closed_pcf_cache=收盘休眠 · 申赎清单缓存服务中
closed=已收盘
weekend=周末休市
overnight=隔夜休眠
pcf_prefetch=盘前清单准备
daily_reset=当日数据初始化
wind_start=Wind 启动准备
tbapi_warmup=Wind 接口预热
on_demand=按需连接（默认不连接）
configured=按显式配置连接
cleaned=已退出并清理
idle=空闲
running=运行中
active=工作中
warming=预热中
starting=启动中
initialized=已初始化
stopped=已停止
blocked=已阻止
degraded=部分异常
connecting=连接中
connected=已连接
disconnected=已断开
reconnecting=重连中
recovering=恢复中
ready=已就绪
fresh=数据正常
stale=数据已过期
flowing=行情正常
error=错误
disabled=已禁用
enabled=已启用
missing=缺失
configuration_error=配置错误
pending=等待处理
accepted=已受理
succeeded=已完成
failed=失败
timed_out=已超时
auto=按计划运行
force_running=强制采集
force_stopped=暂停采集
work=工作模式
weekend_test=周末测试模式
authenticated=已登录
unauthenticated=未登录
login_required=需要登录
captcha_required=需要验证码
online=在线
offline=离线
ok=正常
healthy=正常
unhealthy=异常
subscribed=已订阅
monitoring=监控中
cached=缓存数据
managed=统一管理
native=内置模块
native-engine=内置引擎
native_premium_a=内置行情引擎
logic=模块管理
owner=进程管理
shadow=仅观察
bundled_business=包内业务组件
bundled_worker=包内上传任务
website=业务网站
preserved-baseline=原业务版本
record_only=仅记录
production=生产输出
tgw_logged_in=行情源已登录
ws_quote_ack=行情已获接收确认
private_batch_ack=批量数据已获接收确认
waiting=等待中
scheduled=按计划等待
continuous=持续运行
daily=每日执行
interval=定时执行
watchdog=守护检查
backoff=等待重试
true=是
false=否)" );
    return names.value(raw.trimmed().toLower(), raw.isEmpty() ? QStringLiteral("—") : raw);
}
QString fieldText(const QString &raw) {
    static const auto names = dictionary(R"(adapter=服务类型
capabilities=支持的操作
commands=操作列表
control_enabled=允许控制
desired_state=管理目标
display_name=服务名称
engine=业务引擎
headline=状态说明
last_error=最近错误
lifecycle=服务状态
ownership=管理方式
process=进程信息
observed_at=检查时间
owner_lease=进程管理锁
held=已持有
required=需要管理锁
processes=进程列表
running_count=运行进程数
telemetry=业务监测
work_state=业务状态
state=状态
ready=已就绪
running=正在运行
monitoring=正在监控
record_only=仅记录
browser=浏览器
auth=登录认证
data=行情数据
connection_state=连接状态
collector_running=采集器运行
schedule_mode=采集模式
operating_mode=运行模式
live_browser_enabled=浏览器采集已启用
socket_connected=本地通道已连接
handshake_complete=握手完成
wind_helper_state=Wind 采集状态
live_orders_allowed=允许真实委托
notifications_enabled=外部通知
unresolved_outcomes=待核对结果数
safety_interlock=操作保护
outcome_unresolved=存在待核对结果
workers=上传任务
worker_count=任务数量
workers_expected=任务应在运行
jobs=任务详情
business_engine=业务组件
business_success=业务已成功
business_state=业务状态
data_root=数据目录
process_running=业务进程运行
readiness_note=就绪检查说明
readiness_problems=未通过的检查
sink_mode=输出方式
upload_health=上传接收确认
accepted=接收数量
last_failure_at=最近失败时间
last_success_at=最近成功时间
last_heartbeat_at=最近心跳
source=数据来源
stage=处理阶段
symbols=标的列表
updated_at=更新时间
pid=进程编号
id=标识
kind=类型
model_version=模型版本
run_at=执行时间
started_at=启动时间
timezone=时区
exit_code=退出码
next_run_at=下次执行
detail_channel=详情通道
detail_subscriptions=详情订阅
acknowledged=已确认订阅
l1_hotlist=额外基础行情标的
premium_readiness=拉涨服务就绪检查
native=内置运行
b_side_trading_supported=支持客户端交易
status=运行状态
watchlist=观察列表
compatibility_ports=客户端服务端口
tgw_helper=行情采集器
adapter_connected=行情源已连接
adapter_session=行情源会话
adapter_seq=行情序号
adapter_gaps=行情序号缺口
upstream_healthy=行情源正常
upstream_status=行情源状态
ready_symbols=已收到行情标的
watchlist_symbols=观察标的数
l1_hot_symbols=额外行情标的数
l1_hot_ready=额外行情已就绪数
active_upstream_symbols=行情源订阅数
unique_pinned_symbols=固定标的总数
summary_clients=概览客户端数
detail_clients=详情客户端数
l1_clients=基础行情客户端数
phase=当前时段
replay=回放模式
simulation=模拟模式
signals_enabled=信号生成已启用
force_quotes=强制采集行情
core_latency_ms=处理耗时（毫秒）
core_latency_max_ms=最大耗时（毫秒）
quarantined=已隔离异常帧
worker_drops=工作队列丢弃数
worker_queue_depths=工作队列长度
worker_queue_peaks=工作队列峰值
sdk_queue_depth=行情接入队列长度
persistence_queue_depth=待写入记录数
persistence_queue_peak=写入队列峰值
historical_writes_stopped=历史写入已暂停
disk_available_bytes=磁盘可用字节
monitor_slow_client_drops=慢客户端断开数
cn_quotes_desired=应采集境内行情
hk_quotes_desired=应采集港股行情
hkt_enabled=港股通行情已启用
pushplus=消息推送
client_count=已连接客户端数
clients=客户端列表
client_id=客户端标识
connected_at=连接时间
last_sent_at=最后发送时间
message_count=消息数量
remote=远端地址
api_address=行情接口地址
api_live=行情接口在线
api_ready=行情接口就绪
api_running=行情接口运行
auth_state=登录状态
browser_state=浏览器状态
browser_visible=浏览器窗口可见
data_age_ms=行情距今（毫秒）
data_fresh=行情未过期
data_state=行情状态
invalid_responses=无效行情响应
valid_responses=有效行情响应
last_depth_at=最近盘口时间
next_transition=下次时段切换
polling_fallback=轮询备用采集
scheduled_idle=按计划休息
schedule_message=调度说明
schema_version=数据格式版本
service=服务标识
stream_connected=行情流已连接
depth_size=盘口档数
stale_after_ms=过期阈值（毫秒）
symbol=标的代码
ticker_id=行情标的编号
health=健康检查
qmt_backends=QMT 连接
service_identity=服务身份校验
snapshot=业务快照
symbol_count=监控标的数
items=标的明细
pcf_runtime=申赎清单采集
pcf=申赎清单
protocol=协议版本
schedule=运行计划
sequence=快照序号
server_time=服务端时间
started_by=启动来源
wind=Wind 数据源
wind_helper_ok=Wind 采集正常
collection_expected=当前应采集
legacy_execution_allowed=允许旧程序执行
label=说明
tbapi_loaded=Wind 接口已加载
name=名称
custom_name=自定义名称
windcode=Wind 代码
values=实时份额
etfbuynumber=申购笔数
etfbuyamount=申购份额
etfsellnumber=赎回笔数
etfsellamount=赎回份额
netamount=净申购份额
sub_id=订阅编号
age_seconds=数据距今（秒）
last_change_at=最近变化时间
last_change=最近变化
opportunity=机会判断
actionable=满足机会条件
reason=原因
creation_redemption_unit=每篮子份额
trading_day=清单日期
requested_day=请求日期
fund_name=基金名称
component_count=成分数量
component_columns=成分字段
components=成分明细
summary_fields=清单字段
cached_at=缓存时间
creation_allowed=允许申购
redemption_allowed=允许赎回
creation_limit=累计申购上限
redemption_limit=累计赎回上限
net_creation_limit=净申购上限
net_redemption_limit=净赎回上限
available_cash=可用资金
endpoint=连接地址
host=主机地址
port=端口
last_result=最近指令结果
positions=持仓
orders=当日委托
orders_synced=委托已同步
positions_synced=持仓已同步
want_connection=期望连接
welcome_received=协议握手完成
throttle_remaining_ms=限流剩余时间（毫秒）
error=错误
message=消息
type=消息类型
field=字段
old=变化前
new=变化后
text=说明)" );
    return names.value(raw, raw);
}
QString actionText(const QString &raw) {
    static const auto names = dictionary(R"(refresh=刷新状态
set_operating_mode=切换运行模式
start_service=启动服务
stop_service=停止服务
restart_service=重启服务
upload_run_job=重新执行上传任务
upload_ingest_quote=接收行情
upload_ingest_valuation=接收估值
upload_ingest_dataset=接收数据集
upload_ack=确认上传结果
upload_set_fund=更新基金设置
upload_add_message=保存留言
upload_ibkr_reconnect=连接或重连盈透行情
upload_ibkr_disconnect=断开盈透行情
premium_sync=同步行情与信号
premium_raw_snapshot=查看原始行情
premium_detail_subscribe=订阅盘口详情
premium_detail_unsubscribe=取消盘口订阅
premium_set_watchlist=更新观察列表
premium_set_l1_hotlist=更新额外行情标的
webull_get_book=获取盘口
webull_set_mode=切换采集模式
webull_collector_start=启动行情采集
webull_collector_stop=停止行情采集
webull_restart_browser=重启浏览器
webull_show_login=打开登录窗口
redemption_snapshot=获取申赎快照
redemption_history=查询变化历史
redemption_pcf_detail=查看申赎清单
redemption_pcf_refresh=刷新申赎清单
redemption_set_watchlist=更新申赎观察列表
redemption_wind_shutdown_cleanup=关闭 Wind 并清理临时探针
redemption_qmt_connect=连接 QMT
redemption_qmt_disconnect=断开 QMT
redemption_qmt_sync=同步 QMT 数据
command=操作结果
premium.signal=拉涨信号
premium.sync_begin=开始同步
premium.sync_complete=同步完成
redemption.change=申赎份额变化
redemption.pcf=申赎清单更新
redemption.pcf_error=申赎清单采集异常
module.started=服务已启动
module.stopped=服务已停止
module.error=服务异常
process.started=进程已启动
process.exited=进程已退出)" );
    return names.value(raw, raw);
}
QString taskText(const QString &raw) {
    static const auto names = dictionary(R"(website=业务网站
sina=新浪行情
xop-family=油气系列估值
basket-159605=159605 篮子估值
silver=白银估值
china-internet=中概互联估值
india=印度市场估值
nasdaq=纳斯达克估值
sp500=标普 500 估值
nikkei225=日经 225 估值
germany=德国市场估值
rebuild=盘中估值重建
health-monitor=业务健康监测
anchor-backfill=基准数据补全
india-final-nav=印度日终净值
china-history=境内历史数据)" );
    return names.value(raw, raw);
}
QString problemText(const QString &raw) {
    const auto at = raw.indexOf(u':');
    static const auto reasons = dictionary(R"(process_not_ready=进程尚未就绪
missing_or_stale_ack=缺少有效的上传确认，或确认已过期
website_not_ready=业务网站尚未就绪
health_unavailable=健康检查暂不可用)" );
    if (at > 0 && reasons.contains(raw.mid(at + 1)))
        return taskText(raw.first(at)) + QStringLiteral("：") + reasons.value(raw.mid(at + 1));
    if (raw == QStringLiteral("No route to host")) return QStringLiteral("无法连接目标主机，请检查网络与本地网络权限");
    if (raw == QStringLiteral("not MQTT PUBLISH")) return QStringLiteral("收到的消息不是行情发布报文");
    return stateText(raw);
}
QString localTimeText(const QString &raw) {
    if (raw.isEmpty()) return QStringLiteral("—");
    const auto dt = QDateTime::fromString(raw, Qt::ISODateWithMs);
    return dt.isValid() ? dt.toLocalTime().toString(QStringLiteral("MM-dd HH:mm:ss")) : raw;
}
QString valueText(const QJsonValue &value) {
    if (value.isBool()) return value.toBool() ? QStringLiteral("是") : QStringLiteral("否");
    if (value.isString()) {
        const auto raw = value.toString();
        const auto action = actionText(raw);
        if (action != raw) return action;
        if (raw.size() >= 19 && raw.at(10) == u'T') return localTimeText(raw);
        return problemText(raw);
    }
    return displayValue(value);
}
QString serviceTitle(const QString &adapter, const QString &fallback) {
    if(adapter=="monitor_sync")return QStringLiteral("监控数据同步");
    if (adapter == QStringLiteral("upload")) return QStringLiteral("网站与数据上传");
    if (adapter == QStringLiteral("premium")) return QStringLiteral("行情与拉涨监控");
    if (adapter == QStringLiteral("webull")) return QStringLiteral("Webull 行情转发");
    if (adapter == QStringLiteral("realtime") || adapter == QStringLiteral("redemption")) return QStringLiteral("ETF 申赎监控");
    return fallback;
}
QString serviceDescription(const QString &adapter) {
    if(adapter=="monitor_sync")return QStringLiteral("按日期保存人工修正原文件，同步锁定汇率、成交时间及 TEMP");
    if (adapter == QStringLiteral("upload")) return QStringLiteral("采集行情与基金估值，向业务网站同步数据");
    if (adapter == QStringLiteral("premium")) return QStringLiteral("跟踪实时行情、溢价与拉涨信号，为客户端提供盘口");
    if (adapter == QStringLiteral("webull")) return QStringLiteral("采集美股盘口，向已连接的客户端转发行情");
    return QStringLiteral("跟踪 ETF 申赎份额变化、申赎清单与 Wind 采集状态");
}
QString stateStyle(const QJsonObject &p) {
    const auto life = p.value(QStringLiteral("lifecycle")).toString();
    const auto work = p.value(QStringLiteral("work_state")).toString();
    if (work == "degraded" || work == "blocked" || work == "error" || life == "degraded"
        || life == "error" || !p.value("last_error").toString().isEmpty()) return QStringLiteral("stateWarn");
    if (life == "stopped" || work == "scheduled_idle") return QStringLiteral("stateIdle");
    if (life == "running" && (work == "active" || work == "running" || work == "ready")) return QStringLiteral("stateGood");
    return QStringLiteral("stateUnknown");
}
QString styleSheet() {
    return QStringLiteral(R"(
        QWidget { background: #f4f7fb; color: #24364b; font-size: 13px; }
        QMainWindow { background: #f4f7fb; }
        QLabel { background: transparent; }
        #navigation { background: #142b43; color: #b8c9da; border: 0; padding: 22px 10px; }
        #navigation::item { height: 48px; border-radius: 8px; padding-left: 9px; margin-bottom: 5px; }
        #navigation::item:selected { background: #284963; color: white; }
        #navigation::item:hover { background: #203d56; }
        #pageTitle { font-size: 25px; font-weight: 650; color: #16334b; }
        #sectionTitle { font-size: 16px; font-weight: 650; }
        #moduleTitle { font-size: 18px; font-weight: 650; }
        #secondaryText, #metricHint { color: #6c7d90; font-size: 12px; }
        #metric { font-size: 18px; font-weight: 600; }
        #metricValue { font-size: 25px; font-weight: 650; color: #163e56; }
        #metricCaption { color: #61768a; font-size: 12px; }
        #stateGood { background: #e0f3eb; color: #16735a; border-radius: 6px; padding: 5px 10px; }
        #stateIdle { background: #e5eef9; color: #41668e; border-radius: 6px; padding: 5px 10px; }
        #stateWarn { background: #fff0d9; color: #93601a; border-radius: 6px; padding: 5px 10px; }
        #stateUnknown { background: #e9edf2; color: #64758a; border-radius: 6px; padding: 5px 10px; }
        #attention { color: #98631c; }
        QFrame#moduleCard, QFrame#metricCard, QFrame#overviewPanel {
            background: white; border: 1px solid #dde6ef; border-radius: 12px;
        }
        QFrame#noticePanel { background: #eef5fb; border: 1px solid #d9e7f3; border-radius: 9px; }
        QFrame#noticePanel[tone="warning"] { background: #fff7e9; border-color: #efdbb6; }
        QPushButton, QToolButton { background: white; border: 1px solid #cdd9e5; border-radius: 7px; padding: 8px 12px; min-height: 20px; }
        QPushButton:hover, QToolButton:hover { background: #edf5fa; border-color: #86afc5; }
        QPushButton:disabled { color: #9daebe; background: #f0f3f7; border-color: #e1e7ef; }
        QPushButton[riskAction="true"] { color: #89574d; border-color: #dbc4bd; }
        QPushButton[primary="true"] { background: #21667d; color: white; border-color: #21667d; }
        QPushButton[riskAction="true"]:disabled, QPushButton[primary="true"]:disabled { color: #9daebe; background: #f0f3f7; border-color: #e1e7ef; }
        QPushButton:focus, QToolButton:focus { border: 2px solid #3a819e; }
        QTabWidget::pane { background: #f4f7fb; border: 0; border-top: 1px solid #dce5ee; }
        QTabBar::tab { padding: 12px 13px; background: transparent; color: #67788a; border-bottom: 3px solid transparent; }
        QTabBar::tab:selected { color: #195f78; border-bottom: 3px solid #28768b; font-weight: 600; }
        QTabBar::tab:hover { background: #eaf1f7; }
        QTableWidget, QTreeWidget { background: white; alternate-background-color: #f7f9fc; border: 1px solid #dfe7ef; gridline-color: #eef2f7; selection-background-color: #e2f0f7; selection-color: #173b55; }
        QTableWidget::item, QTreeWidget::item { padding: 5px; }
        QHeaderView::section { background: #f1f5f9; color: #667b8f; border: 0; border-bottom: 1px solid #dfe7ef; padding: 10px 8px; font-weight: 500; }
        QPlainTextEdit, QLineEdit, QComboBox, QDateEdit { background: white; border: 1px solid #d5e0eb; border-radius: 5px; padding: 6px; }
        QScrollArea { background: #f4f7fb; border: 0; }
        QScrollBar:vertical { background: transparent; width: 10px; }
        QScrollBar::handle:vertical { background: #c6d2de; border-radius: 5px; min-height: 30px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QToolTip { background: #16334b; color: white; padding: 7px; border: 0; }
    )");
}
}
