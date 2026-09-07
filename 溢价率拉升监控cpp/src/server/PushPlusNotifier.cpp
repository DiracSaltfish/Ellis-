#include "server/PushPlusNotifier.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QProcessEnvironment>
#include <QSaveFile>
#include <QTimer>
#include <QUrl>

#include <algorithm>

namespace premium {
namespace {

QString expandedPath(QString path, const QString &rootDirectory)
{
    path = path.trimmed();
    if (path == QStringLiteral("~")) path = QDir::homePath();
    else if (path.startsWith(QStringLiteral("~/"))) path = QDir::homePath() + path.mid(1);
    if (path.isEmpty()) return {};
    if (QDir::isAbsolutePath(path)) return QDir::cleanPath(path);
    return QDir(rootDirectory).absoluteFilePath(path);
}

bool parsedBool(const QString &value, bool fallback)
{
    const QString normalized = value.trimmed().toLower();
    if (normalized == QStringLiteral("1") || normalized == QStringLiteral("true")
        || normalized == QStringLiteral("yes") || normalized == QStringLiteral("on")) return true;
    if (normalized == QStringLiteral("0") || normalized == QStringLiteral("false")
        || normalized == QStringLiteral("no") || normalized == QStringLiteral("off")) return false;
    return fallback;
}

QHash<QString, QString> readEnvironmentFile(const QString &path, QString *error)
{
    QHash<QString, QString> values;
    if (path.isEmpty()) return values;
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        if (error) *error = QStringLiteral("cannot open PushPlus environment file %1: %2")
                                .arg(path, file.errorString());
        return values;
    }
    while (!file.atEnd()) {
        QString line = QString::fromUtf8(file.readLine()).trimmed();
        if (line.isEmpty() || line.startsWith(u'#')) continue;
        if (line.startsWith(QStringLiteral("export "))) line = line.mid(7).trimmed();
        const qsizetype equals = line.indexOf(u'=');
        if (equals <= 0) continue;
        const QString key = line.left(equals).trimmed();
        QString value = line.mid(equals + 1).trimmed();
        if (value.size() >= 2
            && ((value.startsWith(u'\'') && value.endsWith(u'\''))
                || (value.startsWith(u'\"') && value.endsWith(u'\"')))) {
            value = value.mid(1, value.size() - 2);
        }
        values.insert(key, value);
    }
    return values;
}

QString environmentValue(const QHash<QString, QString> &fileValues, const QString &key)
{
    const QProcessEnvironment process = QProcessEnvironment::systemEnvironment();
    if (process.contains(key)) return process.value(key);
    return fileValues.value(key);
}

QString markdownText(QString value)
{
    value.replace(u'\r', u' ');
    value.replace(u'\n', u' ');
    value.replace(u'|', QStringLiteral("\\|"));
    return value.trimmed();
}

qint64 integerValue(const QJsonObject &object, const QString &key)
{
    return static_cast<qint64>(object.value(key).toDouble());
}

QString percentFromPpm(qint64 ppm)
{
    return QString::number(static_cast<double>(ppm) / 10'000.0, 'f', 4) + u'%';
}

QString priceFromE6(qint64 price)
{
    if (price <= 0) return QStringLiteral("--");
    return QString::number(static_cast<double>(price) / 1'000'000.0, 'f', 4);
}

QString itemKey(const QJsonObject &item)
{
    return item.value(QStringLiteral("_pushplus_key")).toString();
}

bool isStartupItem(const QJsonObject &item)
{
    return item.value(QStringLiteral("_pushplus_kind")).toString() == QStringLiteral("startup");
}

int apiResponseCode(const QJsonValue &value)
{
    if (value.isDouble()) return value.toInt(-1);
    bool ok = false;
    const int code = value.toString().toInt(&ok);
    return ok ? code : -1;
}

} // namespace

PushPlusNotifier::PushPlusNotifier(PushPlusConfig config, QObject *parent)
    : QObject(parent), config_(std::move(config))
{
}

PushPlusConfig PushPlusNotifier::fromApplicationConfig(const QJsonObject &applicationConfig,
                                                       const QString &rootDirectory, QString *error)
{
    PushPlusConfig config;
    const QJsonObject object = applicationConfig.value(QStringLiteral("pushplus")).toObject();
    config.enabled = object.value(QStringLiteral("enabled")).toBool(false);
    config.startupNotification = object.value(QStringLiteral("startup_notification")).toBool(false);
    config.endpoint = object.value(QStringLiteral("endpoint")).toString(config.endpoint).trimmed();
    config.channel = object.value(QStringLiteral("channel")).toString(config.channel).trimmed();
    config.batchWindowMs = std::clamp(object.value(QStringLiteral("batch_window_ms")).toInt(config.batchWindowMs),
                                      1'000, 60'000);
    config.maxRequestsPerMinute = std::clamp(
        object.value(QStringLiteral("max_requests_per_minute")).toInt(config.maxRequestsPerMinute), 1, 4);
    config.dailyRequestLimit = std::clamp(
        object.value(QStringLiteral("daily_request_limit")).toInt(config.dailyRequestLimit), 1, 180);
    config.requestTimeoutMs = std::clamp(
        object.value(QStringLiteral("request_timeout_ms")).toInt(config.requestTimeoutMs), 1'000, 30'000);
    config.maxSignalAgeSeconds = std::clamp(
        object.value(QStringLiteral("max_signal_age_seconds")).toInt(config.maxSignalAgeSeconds), 60, 600);
    config.maxBatchSignals = std::clamp(
        object.value(QStringLiteral("max_batch_signals")).toInt(config.maxBatchSignals), 1, 100);
    config.stateFile = expandedPath(
        object.value(QStringLiteral("state_file")).toString(QStringLiteral("runtime/pushplus-notifier-state.json")),
        rootDirectory);

    QString environmentPath = object.value(QStringLiteral("env_file")).toString().trimmed();
    const QString processEnvironmentPath = qEnvironmentVariable("PREMIUM_PUSHPLUS_ENV_FILE").trimmed();
    if (!processEnvironmentPath.isEmpty()) environmentPath = processEnvironmentPath;
    if (environmentPath.isEmpty()) environmentPath = QStringLiteral("~/.newnavnav-monitor.env");
    environmentPath = expandedPath(environmentPath, rootDirectory);

    QString environmentError;
    QHash<QString, QString> fileValues;
    if (QFileInfo::exists(environmentPath)) fileValues = readEnvironmentFile(environmentPath, &environmentError);
    else if (config.enabled) environmentError = QStringLiteral("PushPlus environment file does not exist: %1")
                                                    .arg(environmentPath);

    const QString enabledOverride = environmentValue(fileValues, QStringLiteral("PREMIUM_PUSHPLUS_ENABLED"));
    if (!enabledOverride.isEmpty()) config.enabled = parsedBool(enabledOverride, config.enabled);

    QString value = environmentValue(fileValues, QStringLiteral("PREMIUM_PUSHPLUS_ENDPOINT"));
    if (value.isEmpty()) value = environmentValue(fileValues, QStringLiteral("PUSHPLUS_ENDPOINT"));
    if (!value.isEmpty()) config.endpoint = value.trimmed();
    value = environmentValue(fileValues, QStringLiteral("PREMIUM_PUSHPLUS_CHANNEL"));
    if (value.isEmpty()) value = environmentValue(fileValues, QStringLiteral("PUSHPLUS_CHANNEL"));
    if (!value.isEmpty()) config.channel = value.trimmed();
    config.token = environmentValue(fileValues, QStringLiteral("PREMIUM_PUSHPLUS_TOKEN"));
    if (config.token.isEmpty()) config.token = environmentValue(fileValues, QStringLiteral("PUSHPLUS_TOKEN"));

    if (!config.enabled) return config;
    if (!environmentError.isEmpty()) {
        if (error) *error = environmentError;
        config.enabled = false;
        return config;
    }
    if (config.token.trimmed().isEmpty()) {
        if (error) *error = QStringLiteral("PushPlus is enabled but no PREMIUM_PUSHPLUS_TOKEN or PUSHPLUS_TOKEN is configured");
        config.enabled = false;
        return config;
    }
    const QUrl endpoint(config.endpoint);
    if (!endpoint.isValid() || endpoint.scheme().toLower() != QStringLiteral("https") || endpoint.host().isEmpty()) {
        if (error) *error = QStringLiteral("PushPlus endpoint must be a valid HTTPS URL");
        config.enabled = false;
    }
    return config;
}

QString PushPlusNotifier::eventKey(const QJsonObject &signal)
{
    return QStringLiteral("%1|%2|%3|%4|%5")
        .arg(signal.value(QStringLiteral("source_session")).toString(),
             signal.value(QStringLiteral("symbol")).toString(),
             signal.value(QStringLiteral("occurred_at")).toString(),
             QString::number(static_cast<qint64>(signal.value(QStringLiteral("signal_seq")).toDouble())),
             signal.value(QStringLiteral("model")).toString());
}

PushPlusMessage PushPlusNotifier::buildMessage(const QList<QJsonObject> &items)
{
    PushPlusMessage message;
    if (items.size() == 1 && isStartupItem(items.front())) {
        message.title = QStringLiteral("【ETF监控】A端 PushPlus 已启用");
        message.content = QStringLiteral(
            "## A端信号推送链路已启动\n\n"
            "- 启动时间：%1\n"
            "- 聚合窗口：%2 秒\n"
            "- 频率上限：%3 次/分钟\n"
            "- 每日硬上限：%4 次\n\n"
            "> 这是一条部署验证消息；正式消息只由 A-core 已确认的实时触发信号生成。")
            .arg(items.front().value(QStringLiteral("occurred_at")).toString(),
                 QString::number(items.front().value(QStringLiteral("batch_window_ms")).toInt() / 1000),
                 QString::number(items.front().value(QStringLiteral("max_requests_per_minute")).toInt()),
                 QString::number(items.front().value(QStringLiteral("daily_request_limit")).toInt()));
        return message;
    }

    QStringList lines;
    QString firstTime;
    QString lastTime;
    for (const QJsonObject &item : items) {
        if (isStartupItem(item)) continue;
        const QString occurred = item.value(QStringLiteral("occurred_at")).toString();
        const QDateTime parsed = QDateTime::fromString(occurred, Qt::ISODateWithMs);
        const QString time = parsed.isValid() ? parsed.toString(QStringLiteral("HH:mm:ss")) : occurred;
        if (firstTime.isEmpty()) firstTime = time;
        lastTime = time;
        const QString symbol = markdownText(item.value(QStringLiteral("symbol")).toString());
        const QString name = markdownText(item.value(QStringLiteral("name")).toString());
        QString model = markdownText(item.value(QStringLiteral("model")).toString());
        if (model.isEmpty()) model = markdownText(item.value(QStringLiteral("signal_model")).toString());
        if (model.isEmpty()) model = QStringLiteral("signal");
        const qint64 premiumPpm = integerValue(item, QStringLiteral("premium_ppm"));
        const qint64 bid1 = integerValue(item, QStringLiteral("bid1_price_e6"));
        QString line = QStringLiteral("- %1 **%2 %3** · %4 · 溢价 %5 · 买一 %6")
                           .arg(time, symbol, name, model, percentFromPpm(premiumPpm), priceFromE6(bid1));
        const QString reason = markdownText(item.value(QStringLiteral("reason")).toString());
        if (!reason.isEmpty()) line += QStringLiteral(" · %1").arg(reason);
        lines.append(line);
        ++message.signalCount;
    }
    message.title = message.signalCount == 1
        ? QStringLiteral("【ETF拉升】%1 %2").arg(
              items.front().value(QStringLiteral("symbol")).toString(),
              items.front().value(QStringLiteral("name")).toString())
        : QStringLiteral("【ETF拉升】%1 条信号 %2–%3").arg(message.signalCount).arg(firstTime, lastTime);
    message.content = QStringLiteral("## ETF 溢价率拉升信号\n\n%1\n\n> A-core 实时信号；同一聚合窗口合并发送。")
                          .arg(lines.join(u'\n'));
    return message;
}

void PushPlusNotifier::start()
{
    if (started_ || !config_.enabled) return;
    network_ = new QNetworkAccessManager(this);
    flushTimer_ = new QTimer(this);
    flushTimer_->setSingleShot(true);
    connect(flushTimer_, &QTimer::timeout, this, &PushPlusNotifier::flush);
    accountingDate_ = QDate::currentDate();
    loadState();
    resetDailyStateIfNeeded();
    started_ = true;
    Q_EMIT operationalEvent(QStringLiteral("INFO"), QStringLiteral("PushPlus notifier started"),
                            {{QStringLiteral("endpoint_host"), QUrl(config_.endpoint).host()},
                             {QStringLiteral("channel"), config_.channel},
                             {QStringLiteral("batch_window_ms"), config_.batchWindowMs},
                             {QStringLiteral("max_requests_per_minute"), config_.maxRequestsPerMinute},
                             {QStringLiteral("daily_request_limit"), config_.dailyRequestLimit}});
    if (config_.startupNotification) {
        QJsonObject item{{QStringLiteral("_pushplus_kind"), QStringLiteral("startup")},
                         {QStringLiteral("occurred_at"), QDateTime::currentDateTime().toString(Qt::ISODateWithMs)},
                         {QStringLiteral("batch_window_ms"), config_.batchWindowMs},
                         {QStringLiteral("max_requests_per_minute"), config_.maxRequestsPerMinute},
                         {QStringLiteral("daily_request_limit"), config_.dailyRequestLimit}};
        const QString key = QStringLiteral("startup|%1|%2")
                                .arg(QDate::currentDate().toString(Qt::ISODate),
                                     item.value(QStringLiteral("occurred_at")).toString());
        enqueueItem(item, key);
    }
    pruneExpiredItems();
    if (!pending_.isEmpty()) scheduleFlush(config_.startupNotification ? 250 : config_.batchWindowMs);
    publishStatus();
}

void PushPlusNotifier::stop()
{
    if (!started_) return;
    if (flushTimer_) flushTimer_->stop();
    if (reply_) {
        disconnect(reply_, nullptr, this, nullptr);
        reply_->abort();
        reply_->deleteLater();
        reply_ = nullptr;
    }
    saveState();
    started_ = false;
    publishStatus();
}

void PushPlusNotifier::enqueueSignal(const QJsonObject &signal)
{
    if (!started_ || !config_.enabled) return;
    if (signal.value(QStringLiteral("backfill")).toBool(false)
        || signal.value(QStringLiteral("replay")).toBool(false)) return;
    const QString key = eventKey(signal);
    if (key.trimmed().isEmpty() || acknowledgedKeys_.contains(key) || queuedKeys_.contains(key)) return;
    enqueueItem(signal, key);
}

void PushPlusNotifier::enqueueItem(QJsonObject item, const QString &key)
{
    if (acknowledgedKeys_.contains(key) || queuedKeys_.contains(key)) return;
    item.insert(QStringLiteral("_pushplus_key"), key);
    item.insert(QStringLiteral("_pushplus_enqueued_ms"), QDateTime::currentMSecsSinceEpoch());
    pending_.append(item);
    queuedKeys_.insert(key);
    saveState();
    if (started_ && currentBatch_.isEmpty() && !reply_) scheduleFlush(config_.batchWindowMs);
    publishStatus();
}

void PushPlusNotifier::scheduleFlush(int delayMs)
{
    if (!started_ || !flushTimer_) return;
    const int bounded = std::clamp(delayMs, 1, 86'400'000);
    if (!flushTimer_->isActive() || flushTimer_->remainingTime() > bounded) flushTimer_->start(bounded);
}

void PushPlusNotifier::flush()
{
    if (!started_ || reply_) return;
    resetDailyStateIfNeeded();
    pruneExpiredItems();
    if (currentBatch_.isEmpty()) {
        if (pending_.isEmpty()) {
            publishStatus();
            return;
        }
        QList<QJsonObject> candidate;
        while (!pending_.isEmpty() && candidate.size() < config_.maxBatchSignals) {
            candidate.append(pending_.front());
            if (candidate.size() > 1 && buildMessage(candidate).content.toUtf8().size() > 14'000) {
                candidate.removeLast();
                break;
            }
            pending_.removeFirst();
        }
        currentBatch_ = candidate;
        currentRetry_ = 0;
        saveState();
    }
    dispatchCurrentBatch();
}

void PushPlusNotifier::dispatchCurrentBatch()
{
    if (currentBatch_.isEmpty() || reply_) return;
    const int delay = rateLimitDelayMs();
    if (delay > 0) {
        scheduleFlush(delay);
        publishStatus();
        return;
    }

    const PushPlusMessage message = buildMessage(currentBatch_);
    QJsonObject payload{{QStringLiteral("token"), config_.token},
                        {QStringLiteral("title"), message.title.left(100)},
                        {QStringLiteral("content"), message.content},
                        {QStringLiteral("template"), QStringLiteral("markdown")},
                        {QStringLiteral("channel"), config_.channel},
                        {QStringLiteral("timestamp"), QDateTime::currentMSecsSinceEpoch() + 120'000}};
    QNetworkRequest request{QUrl(config_.endpoint)};
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json; charset=utf-8"));
    request.setRawHeader("User-Agent", "etf-premium-core/0.5");
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    requestTimesMs_.append(now);
    ++requestsToday_;
    saveState();
    reply_ = network_->post(request, QJsonDocument(payload).toJson(QJsonDocument::Compact));
    connect(reply_, &QNetworkReply::finished, this, &PushPlusNotifier::handleReply);
    QNetworkReply *requestReply = reply_;
    QTimer::singleShot(config_.requestTimeoutMs, requestReply, [requestReply] {
        if (requestReply->isRunning()) {
            requestReply->setProperty("pushplus_timeout", true);
            requestReply->abort();
        }
    });
    publishStatus();
}

void PushPlusNotifier::handleReply()
{
    QNetworkReply *finished = reply_;
    if (!finished) return;
    reply_ = nullptr;
    const int httpStatus = finished->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    const QNetworkReply::NetworkError networkError = finished->error();
    const bool timedOut = finished->property("pushplus_timeout").toBool();
    const QByteArray body = finished->readAll();
    const QJsonDocument response = QJsonDocument::fromJson(body);
    const QJsonObject responseObject = response.object();
    const int code = apiResponseCode(responseObject.value(QStringLiteral("code")));
    QString apiMessage = responseObject.value(QStringLiteral("msg")).toString();
    if (apiMessage.isEmpty()) apiMessage = responseObject.value(QStringLiteral("message")).toString();
    apiMessage = apiMessage.left(200);
    const QString networkMessage = timedOut ? QStringLiteral("request timeout") : finished->errorString();
    finished->deleteLater();

    if (networkError == QNetworkReply::NoError && httpStatus >= 200 && httpStatus < 300 && code == 200) {
        completeCurrentBatch();
        lastSuccessAt_ = QDateTime::currentDateTime().toString(Qt::ISODateWithMs);
        lastError_.clear();
        pausedReason_.clear();
        ++acceptedBatches_;
        Q_EMIT operationalEvent(QStringLiteral("INFO"), QStringLiteral("PushPlus batch accepted"),
                                {{QStringLiteral("signal_count"), buildMessage(currentBatch_).signalCount},
                                 {QStringLiteral("requests_today"), requestsToday_}});
        currentBatch_.clear();
        currentRetry_ = 0;
        saveState();
        publishStatus();
        if (!pending_.isEmpty()) scheduleFlush(config_.batchWindowMs);
        return;
    }

    ++failedRequests_;
    lastError_ = QStringLiteral("http=%1 code=%2 %3")
                     .arg(httpStatus).arg(code).arg(apiMessage.isEmpty() ? networkMessage : apiMessage);
    Q_EMIT operationalEvent(QStringLiteral("ERROR"), QStringLiteral("PushPlus request failed"),
                            {{QStringLiteral("http_status"), httpStatus}, {QStringLiteral("api_code"), code},
                             {QStringLiteral("error"), lastError_},
                             {QStringLiteral("attempt"), currentRetry_ + 1},
                             {QStringLiteral("signal_count"), buildMessage(currentBatch_).signalCount}});

    if (code == 900) {
        pausedReason_ = QStringLiteral("pushplus_daily_limit");
        for (auto it = currentBatch_.crbegin(); it != currentBatch_.crend(); ++it) pending_.prepend(*it);
        currentBatch_.clear();
        currentRetry_ = 0;
        saveState();
        publishStatus();
        scheduleFlush(rateLimitDelayMs());
        return;
    }

    const bool transient = timedOut || networkError != QNetworkReply::NoError || httpStatus >= 500 || code == 500;
    if (transient && currentRetry_ < 2 && requestsToday_ < config_.dailyRequestLimit) {
        ++currentRetry_;
        saveState();
        publishStatus();
        scheduleFlush(currentRetry_ == 1 ? 5'000 : 15'000);
        return;
    }
    dropCurrentBatch(QStringLiteral("permanent_or_retry_exhausted"));
    saveState();
    publishStatus();
    if (!pending_.isEmpty()) scheduleFlush(config_.batchWindowMs);
}

void PushPlusNotifier::completeCurrentBatch()
{
    for (const QJsonObject &item : currentBatch_) {
        const QString key = itemKey(item);
        queuedKeys_.remove(key);
        acknowledgedKeys_.insert(key);
        if (!isStartupItem(item)) ++sentSignals_;
    }
}

void PushPlusNotifier::dropCurrentBatch(const QString &reason)
{
    int signalCount = 0;
    for (const QJsonObject &item : currentBatch_) {
        queuedKeys_.remove(itemKey(item));
        if (!isStartupItem(item)) ++signalCount;
    }
    droppedSignals_ += signalCount;
    currentBatch_.clear();
    currentRetry_ = 0;
    Q_EMIT operationalEvent(QStringLiteral("ERROR"), QStringLiteral("PushPlus batch dropped"),
                            {{QStringLiteral("reason"), reason}, {QStringLiteral("signal_count"), signalCount}});
}

void PushPlusNotifier::resetDailyStateIfNeeded()
{
    const QDate today = QDate::currentDate();
    if (accountingDate_ == today) return;
    accountingDate_ = today;
    requestsToday_ = 0;
    acceptedBatches_ = 0;
    sentSignals_ = 0;
    failedRequests_ = 0;
    droppedSignals_ = 0;
    expiredSignals_ = 0;
    acknowledgedKeys_.clear();
    requestTimesMs_.clear();
    pausedReason_.clear();
    lastError_.clear();
    saveState();
}

void PushPlusNotifier::pruneExpiredItems()
{
    const qint64 cutoff = QDateTime::currentMSecsSinceEpoch() - config_.maxSignalAgeSeconds * 1000LL;
    for (qsizetype index = pending_.size() - 1; index >= 0; --index) {
        const QJsonObject &item = pending_.at(index);
        if (isStartupItem(item)) continue;
        qint64 time = static_cast<qint64>(item.value(QStringLiteral("_pushplus_enqueued_ms")).toDouble());
        const QDateTime occurred = QDateTime::fromString(
            item.value(QStringLiteral("occurred_at")).toString(), Qt::ISODateWithMs);
        if (occurred.isValid()) time = occurred.toMSecsSinceEpoch();
        if (time >= cutoff) continue;
        queuedKeys_.remove(itemKey(item));
        pending_.removeAt(index);
        ++expiredSignals_;
    }
}

int PushPlusNotifier::rateLimitDelayMs()
{
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    while (!requestTimesMs_.isEmpty() && requestTimesMs_.front() <= now - 60'000) requestTimesMs_.removeFirst();
    if (!pausedReason_.isEmpty() || requestsToday_ >= config_.dailyRequestLimit) {
        if (pausedReason_.isEmpty()) pausedReason_ = QStringLiteral("local_daily_limit");
        const QDateTime nextDay(QDate::currentDate().addDays(1), QTime(0, 0, 1));
        return static_cast<int>(std::clamp<qint64>(now < nextDay.toMSecsSinceEpoch()
                                                      ? nextDay.toMSecsSinceEpoch() - now : 1'000,
                                                  1'000, 86'400'000));
    }
    if (requestTimesMs_.size() < config_.maxRequestsPerMinute) return 0;
    return static_cast<int>(std::clamp<qint64>(requestTimesMs_.front() + 60'001 - now, 1, 60'001));
}

void PushPlusNotifier::loadState()
{
    QFile file(config_.stateFile);
    if (!file.exists()) return;
    if (!file.open(QIODevice::ReadOnly)) {
        Q_EMIT operationalEvent(QStringLiteral("WARN"), QStringLiteral("cannot read PushPlus state"),
                                {{QStringLiteral("error"), file.errorString()}});
        return;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (!document.isObject()) {
        Q_EMIT operationalEvent(QStringLiteral("WARN"), QStringLiteral("invalid PushPlus state ignored"),
                                {{QStringLiteral("error"), parseError.errorString()}});
        return;
    }
    const QJsonObject object = document.object();
    const bool sameDay = QDate::fromString(object.value(QStringLiteral("date")).toString(), Qt::ISODate)
                         == QDate::currentDate();
    if (sameDay) {
        requestsToday_ = object.value(QStringLiteral("requests_today")).toInt();
        acceptedBatches_ = object.value(QStringLiteral("accepted_batches")).toInt();
        sentSignals_ = object.value(QStringLiteral("sent_signals")).toInt();
        failedRequests_ = object.value(QStringLiteral("failed_requests")).toInt();
        droppedSignals_ = object.value(QStringLiteral("dropped_signals")).toInt();
        expiredSignals_ = object.value(QStringLiteral("expired_signals")).toInt();
        for (const QJsonValue &value : object.value(QStringLiteral("acknowledged_keys")).toArray())
            acknowledgedKeys_.insert(value.toString());
    }
    const auto restore = [this](const QJsonArray &array) {
        for (const QJsonValue &value : array) {
            const QJsonObject item = value.toObject();
            const QString key = itemKey(item);
            if (key.isEmpty() || queuedKeys_.contains(key) || acknowledgedKeys_.contains(key)) continue;
            pending_.append(item);
            queuedKeys_.insert(key);
        }
    };
    restore(object.value(QStringLiteral("in_flight")).toArray());
    restore(object.value(QStringLiteral("pending")).toArray());
}

void PushPlusNotifier::saveState() const
{
    if (config_.stateFile.isEmpty()) return;
    QDir().mkpath(QFileInfo(config_.stateFile).absolutePath());
    QJsonArray pending;
    for (const QJsonObject &item : pending_) pending.append(item);
    QJsonArray inFlight;
    for (const QJsonObject &item : currentBatch_) inFlight.append(item);
    QJsonArray acknowledged;
    for (const QString &key : acknowledgedKeys_) acknowledged.append(key);
    const QJsonObject object{{QStringLiteral("date"), accountingDate_.toString(Qt::ISODate)},
                             {QStringLiteral("requests_today"), requestsToday_},
                             {QStringLiteral("accepted_batches"), acceptedBatches_},
                             {QStringLiteral("sent_signals"), sentSignals_},
                             {QStringLiteral("failed_requests"), failedRequests_},
                             {QStringLiteral("dropped_signals"), droppedSignals_},
                             {QStringLiteral("expired_signals"), expiredSignals_},
                             {QStringLiteral("acknowledged_keys"), acknowledged},
                             {QStringLiteral("pending"), pending},
                             {QStringLiteral("in_flight"), inFlight}};
    QSaveFile file(config_.stateFile);
    if (!file.open(QIODevice::WriteOnly)) return;
    file.write(QJsonDocument(object).toJson(QJsonDocument::Compact));
    if (file.commit()) QFile::setPermissions(config_.stateFile, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
}

void PushPlusNotifier::publishStatus()
{
    Q_EMIT statusChanged(statusObject());
}

QJsonObject PushPlusNotifier::statusObject() const
{
    return {{QStringLiteral("enabled"), config_.enabled}, {QStringLiteral("started"), started_},
            {QStringLiteral("pending"), pending_.size()},
            {QStringLiteral("in_flight"), currentBatch_.size()},
            {QStringLiteral("request_active"), reply_ != nullptr},
            {QStringLiteral("requests_today"), requestsToday_},
            {QStringLiteral("daily_request_limit"), config_.dailyRequestLimit},
            {QStringLiteral("accepted_batches"), acceptedBatches_},
            {QStringLiteral("sent_signals"), sentSignals_},
            {QStringLiteral("failed_requests"), failedRequests_},
            {QStringLiteral("dropped_signals"), droppedSignals_},
            {QStringLiteral("expired_signals"), expiredSignals_},
            {QStringLiteral("paused_reason"), pausedReason_},
            {QStringLiteral("last_success_at"), lastSuccessAt_},
            {QStringLiteral("last_error"), lastError_}};
}

} // namespace premium
