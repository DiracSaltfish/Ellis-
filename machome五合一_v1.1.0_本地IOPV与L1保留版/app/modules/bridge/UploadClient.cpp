#include "UploadClient.h"

#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QMetaObject>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPointer>
#include <QRegularExpression>
#include <QThreadPool>
#include <QTimer>
#include <QVariant>

#include <algorithm>
#include <cmath>
#include <utility>

namespace machome::bridge {
namespace {

constexpr int kMaximumHealthFiles = 128;
constexpr qint64 kMaximumHealthFileBytes = 256 * 1024;

QDateTime parseDateTime(const QJsonValue &value)
{
    const QString text = value.toString().trimmed();
    if (text.isEmpty()) {
        return {};
    }
    QDateTime parsed = QDateTime::fromString(text, Qt::ISODateWithMs);
    if (!parsed.isValid()) {
        parsed = QDateTime::fromString(text, Qt::ISODate);
    }
    return parsed.isValid() ? parsed.toUTC() : QDateTime{};
}

QDateTime newestDateTime(std::initializer_list<QDateTime> values)
{
    QDateTime newest;
    for (const QDateTime &value : values) {
        if (value.isValid() && (!newest.isValid() || value > newest)) {
            newest = value;
        }
    }
    return newest;
}

qint64 ageInMilliseconds(const QDateTime &now, const QDateTime &value)
{
    if (!value.isValid()) {
        return -1;
    }
    return std::max<qint64>(0, value.msecsTo(now));
}

QString boundedString(const QJsonObject &object, const char *key, int maximum)
{
    return object.value(QLatin1String(key)).toString().trimmed().left(maximum);
}

bool nonNegativeInteger(const QJsonValue &value)
{
    if (!value.isDouble()) return false;
    const double number = value.toDouble(-1.0);
    return std::isfinite(number) && number >= 0.0 && std::floor(number) == number;
}

bool newNavNavSnapshotStatusContract(const QJsonObject &object)
{
    return nonNegativeInteger(object.value(QStringLiteral("branch_count")))
        && nonNegativeInteger(object.value(QStringLiteral("quote_count")))
        && nonNegativeInteger(object.value(QStringLiteral("uploaded_quote_count")))
        && nonNegativeInteger(object.value(QStringLiteral("purchase_info_count")))
        && object.value(QStringLiteral("uploaded_quotes_enabled")).isBool()
        && object.value(QStringLiteral("refresh_interval_seconds")).isDouble()
        && object.value(QStringLiteral("refresh_interval_seconds")).toDouble(-1.0) >= 0.0
        && object.value(QStringLiteral("branches")).isObject()
        && object.value(QStringLiteral("last_error")).isString()
        && (object.value(QStringLiteral("last_refresh_at")).isString()
            || object.value(QStringLiteral("last_refresh_at")).isNull());
}

} // namespace

struct UploadClient::HealthScanResult {
    UploadWorkerStatusList workers;
    int invalidFiles = 0;
    bool limited = false;
    QString errorCode;
    QString errorMessage;
};

UploadClient::UploadClient(QObject *parent)
    : QObject(parent)
    , m_network(new QNetworkAccessManager(this))
    , m_probeTimer(new QTimer(this))
    , m_probeTimeout(new QTimer(this))
    , m_baseUrl(QStringLiteral("http://127.0.0.1:8080"))
{
    qRegisterMetaType<UploadSiteStatus>();
    qRegisterMetaType<UploadWorkerStatus>();
    qRegisterMetaType<UploadWorkerStatusList>();
    qRegisterMetaType<AggregateState>();

    m_network->setProxy(QNetworkProxy::NoProxy);
    m_probeTimer->setInterval(m_probeIntervalMs);
    m_probeTimer->setSingleShot(false);
    connect(m_probeTimer, &QTimer::timeout, this, &UploadClient::refresh);

    m_probeTimeout->setSingleShot(true);
    connect(m_probeTimeout, &QTimer::timeout, this, [this] {
        if (m_probeReply == nullptr) {
            return;
        }
        m_probeTimedOut = true;
        m_probeReply->abort();
    });
}

UploadClient::~UploadClient()
{
    stop();
}

QUrl UploadClient::baseUrl() const
{
    return m_baseUrl;
}

QString UploadClient::healthDirectory() const
{
    return m_healthDirectory;
}

int UploadClient::probeIntervalMs() const
{
    return m_probeIntervalMs;
}

int UploadClient::requestTimeoutMs() const
{
    return m_requestTimeoutMs;
}

qint64 UploadClient::maximumResponseBytes() const
{
    return m_maximumResponseBytes;
}

qint64 UploadClient::workerFreshnessMs() const
{
    return m_workerFreshnessMs;
}

bool UploadClient::workersExpected() const
{
    return m_workersExpected;
}

bool UploadClient::setBaseUrl(const QUrl &url, QString *errorMessage)
{
    QUrl normalized = url.adjusted(QUrl::StripTrailingSlash);
    const QString scheme = normalized.scheme().toLower();
    const int explicitPort = normalized.port(-1);
    if (!normalized.isValid() || scheme != QStringLiteral("http")
        || normalized.host() != QStringLiteral("127.0.0.1")
        || explicitPort < 1 || explicitPort > 65535
        || !normalized.path().isEmpty() || normalized.hasQuery()
        || !normalized.fragment().isEmpty()) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("NewNavNav 地址必须是含显式端口的 http://127.0.0.1 origin");
        }
        return false;
    }
    if (!normalized.userInfo().isEmpty()) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("地址中禁止包含用户名或密码");
        }
        return false;
    }

    normalized.setScheme(scheme);
    normalized.setPath(QString());
    normalized.setQuery(QString());
    normalized.setFragment(QString());
    if (normalized == m_baseUrl) {
        return true;
    }
    if (m_probeReply != nullptr) {
        QNetworkReply *reply = m_probeReply;
        m_probeReply = nullptr;
        m_probeTimeout->stop();
        disconnect(reply, nullptr, this, nullptr);
        reply->abort();
        reply->deleteLater();
    }
    m_refreshQueued = false;
    m_probeBody.clear();
    m_probeHealthPayload = {};
    m_probeIdentityStage = false;
    m_baseUrl = normalized;
    m_siteStatus = {};
    emit baseUrlChanged(m_baseUrl);
    emit siteStatusChanged(m_siteStatus);
    recomputeAggregate();
    if (m_started) {
        refresh();
    }
    return true;
}

void UploadClient::setHealthDirectory(const QString &directory)
{
    QString normalized = directory.trimmed();
    if (normalized == QStringLiteral("~")) {
        normalized = QDir::homePath();
    } else if (normalized.startsWith(QStringLiteral("~/"))) {
        normalized = QDir::home().filePath(normalized.mid(2));
    }
    normalized = QDir::cleanPath(normalized);
    if (directory.trimmed().isEmpty()) {
        normalized.clear();
    } else {
        normalized = QFileInfo(normalized).absoluteFilePath();
    }
    if (normalized == m_healthDirectory) {
        return;
    }
    m_healthDirectory = normalized;
    ++m_healthScanGeneration;
    emit healthDirectoryChanged(m_healthDirectory);
    if (m_started) {
        beginHealthScan();
    } else {
        m_workerStatuses.clear();
        emit workerStatusesChanged(m_workerStatuses);
        recomputeAggregate();
    }
}

void UploadClient::setProbeIntervalMs(int intervalMs)
{
    m_probeIntervalMs = std::clamp(intervalMs, 500, 3'600'000);
    m_probeTimer->setInterval(m_probeIntervalMs);
}

void UploadClient::setRequestTimeoutMs(int timeoutMs)
{
    m_requestTimeoutMs = std::clamp(timeoutMs, 250, 300'000);
}

void UploadClient::setMaximumResponseBytes(qint64 bytes)
{
    m_maximumResponseBytes = std::clamp<qint64>(bytes, 1'024, 16 * 1024 * 1024);
}

void UploadClient::setWorkerFreshnessMs(qint64 freshnessMs)
{
    m_workerFreshnessMs = std::clamp<qint64>(freshnessMs, 1'000, 24LL * 60 * 60 * 1000);
    if (m_started) {
        beginHealthScan();
    }
}

void UploadClient::setWorkersExpected(bool expected)
{
    if (m_workersExpected == expected) {
        return;
    }
    m_workersExpected = expected;
    recomputeAggregate();
}

UploadSiteStatus UploadClient::siteStatus() const
{
    return m_siteStatus;
}

UploadWorkerStatusList UploadClient::workerStatuses() const
{
    return m_workerStatuses;
}

UploadClient::AggregateState UploadClient::aggregateState() const
{
    return m_aggregateState;
}

QUrl UploadClient::deepLink(const QString &absolutePath) const
{
    QString path = absolutePath.trimmed();
    if (path.isEmpty()) {
        path = QStringLiteral("/");
    }
    if (!path.startsWith(QLatin1Char('/')) || path.contains(QLatin1Char('\n'))
        || path.contains(QLatin1Char('\r'))) {
        return {};
    }
    const QUrl relative(path);
    if (!relative.isRelative() || relative.hasFragment()) {
        return {};
    }
    QUrl base = m_baseUrl;
    base.setPath(QStringLiteral("/"));
    const QUrl resolved = base.resolved(relative);
    if (resolved.scheme() != m_baseUrl.scheme() || resolved.host() != m_baseUrl.host()
        || resolved.port() != m_baseUrl.port()) {
        return {};
    }
    return resolved;
}

QUrl UploadClient::homeUrl() const
{
    return deepLink(QStringLiteral("/"));
}

QUrl UploadClient::debugUrl() const
{
    return deepLink(QStringLiteral("/debug"));
}

QUrl UploadClient::navSettingsUrl() const
{
    return deepLink(QStringLiteral("/navsettings"));
}

QUrl UploadClient::fundUrl(const QString &symbol) const
{
    const QString normalized = normalizeFundSymbol(symbol);
    return normalized.isEmpty() ? QUrl{} : deepLink(QStringLiteral("/funds/") + normalized);
}

QUrl UploadClient::effectiveRatioHistoryUrl(const QString &symbol) const
{
    const QString normalized = normalizeFundSymbol(symbol);
    return normalized.isEmpty()
        ? QUrl{}
        : deepLink(QStringLiteral("/funds/") + normalized + QStringLiteral("/effective-ratio-history"));
}

QUrl UploadClient::shareHistoryUrl(const QString &symbol) const
{
    const QString normalized = normalizeFundSymbol(symbol);
    return normalized.isEmpty()
        ? QUrl{}
        : deepLink(QStringLiteral("/funds/") + normalized + QStringLiteral("/share-history"));
}

void UploadClient::start()
{
    if (m_started) {
        refresh();
        return;
    }
    m_started = true;
    m_probeTimer->start();
    refresh();
}

void UploadClient::stop()
{
    m_started = false;
    m_probeTimer->stop();
    m_probeTimeout->stop();
    m_refreshQueued = false;
    m_probeHealthPayload = {};
    m_probeIdentityStage = false;
    m_healthScanQueued = false;
    ++m_healthScanGeneration;
    if (m_probeReply != nullptr) {
        QNetworkReply *reply = m_probeReply;
        m_probeReply = nullptr;
        disconnect(reply, nullptr, this, nullptr);
        reply->abort();
        reply->deleteLater();
    }
}

void UploadClient::refresh()
{
    beginSiteProbe();
    beginHealthScan();
}

void UploadClient::beginSiteProbe()
{
    if (m_probeReply != nullptr) {
        m_refreshQueued = true;
        return;
    }

    m_probeElapsed.start();
    m_probeHealthPayload = {};
    beginSiteProbeRequest(QStringLiteral("/api/v1/health"), false);
}

void UploadClient::beginSiteProbeRequest(const QString &path, bool identityStage)
{
    m_probeIdentityStage = identityStage;
    QNetworkRequest request(endpointUrl(path));
    request.setAttribute(QNetworkRequest::CacheLoadControlAttribute, QNetworkRequest::AlwaysNetwork);
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
    request.setRawHeader("Accept", "application/json");
    request.setRawHeader("User-Agent", "MachomeHub/1");

    m_probeBody.clear();
    m_probeTimedOut = false;
    m_probeTooLarge = false;
    m_probeReply = m_network->get(request);
    QNetworkReply *reply = m_probeReply;

    connect(reply, &QNetworkReply::readyRead, this, [this, reply] {
        if (reply != m_probeReply || m_probeTooLarge) {
            return;
        }
        const QByteArray chunk = reply->readAll();
        if (m_probeBody.size() + chunk.size() > m_maximumResponseBytes) {
            m_probeTooLarge = true;
            m_probeBody.clear();
            reply->abort();
            return;
        }
        m_probeBody.append(chunk);
    });
    connect(reply, &QNetworkReply::finished, this, &UploadClient::finishSiteProbe);
    m_probeTimeout->start(m_requestTimeoutMs);
}

void UploadClient::finishSiteProbe()
{
    QNetworkReply *reply = m_probeReply;
    if (reply == nullptr) {
        return;
    }
    m_probeTimeout->stop();
    if (!m_probeTooLarge && !m_probeTimedOut && reply->isOpen()) {
        const QByteArray tail = reply->readAll();
        if (m_probeBody.size() + tail.size() > m_maximumResponseBytes) {
            m_probeTooLarge = true;
            m_probeBody.clear();
        } else {
            m_probeBody.append(tail);
        }
    }

    const bool identityStage = m_probeIdentityStage;
    UploadSiteStatus status;
    status.observedAt = QDateTime::currentDateTimeUtc();
    status.latencyMs = m_probeElapsed.isValid() ? std::max<qint64>(0, m_probeElapsed.elapsed()) : -1;
    status.httpStatus = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();

    if (m_probeTimedOut) {
        status.errorCode = QStringLiteral("timeout");
        status.errorMessage = QStringLiteral("NewNavNav 健康探测超时");
    } else if (m_probeTooLarge) {
        status.errorCode = QStringLiteral("response_too_large");
        status.errorMessage = QStringLiteral("NewNavNav 健康响应超过大小限制");
    } else if (status.httpStatus > 0 && (status.httpStatus < 200 || status.httpStatus >= 300)) {
        status.reachable = true;
        status.errorCode = QStringLiteral("http_error");
        status.errorMessage = QStringLiteral("NewNavNav 健康端点返回 HTTP %1").arg(status.httpStatus);
    } else if (reply->error() != QNetworkReply::NoError) {
        status.reachable = status.httpStatus > 0;
        status.errorCode = QStringLiteral("network_error");
        status.errorMessage = redact(reply->errorString());
    } else {
        status.reachable = true;
        const QString contentType = reply->header(QNetworkRequest::ContentTypeHeader)
                                        .toString().toLower();
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(m_probeBody, &parseError);
        if (!contentType.startsWith(QStringLiteral("application/json"))) {
            status.errorCode = QStringLiteral("identity_content_type");
            status.errorMessage = QStringLiteral("NewNavNav 身份端点未返回 application/json");
        } else if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            status.errorCode = QStringLiteral("invalid_json");
            status.errorMessage = QStringLiteral("NewNavNav 健康端点未返回 JSON 对象");
        } else {
            status.payload = document.object();
            if (!identityStage) {
                const QString declaredService = status.payload
                    .value(QStringLiteral("service")).toString();
                const QString declaredModule = status.payload
                    .value(QStringLiteral("module")).toString();
                const bool declarationMatches =
                    (declaredService.isEmpty()
                     || declaredService == QStringLiteral("newnavnav-web"))
                    && (declaredModule.isEmpty()
                        || declaredModule == QStringLiteral("upload-website"));
                if (!status.payload.value(QStringLiteral("ok")).isBool()
                    || !status.payload.value(QStringLiteral("ok")).toBool(false)) {
                    status.errorCode = QStringLiteral("api_unhealthy");
                    status.errorMessage = QStringLiteral("NewNavNav 健康端点未确认 ok=true");
                } else if (!declarationMatches) {
                    status.errorCode = QStringLiteral("service_identity_mismatch");
                    status.errorMessage = QStringLiteral("Upload 健康响应显式 service/module 与 NewNavNav 不匹配");
                } else {
                    m_probeHealthPayload = status.payload;
                    m_probeReply = nullptr;
                    reply->deleteLater();
                    beginSiteProbeRequest(QStringLiteral("/api/v1/snapshots/status"), true);
                    return;
                }
            } else if (!newNavNavSnapshotStatusContract(status.payload)) {
                status.errorCode = QStringLiteral("service_identity_mismatch");
                status.errorMessage = QStringLiteral("/api/v1/snapshots/status 不符合 NewNavNav 独特合同");
            } else {
                QJsonObject verified = m_probeHealthPayload;
                verified.insert(QStringLiteral("service"), QStringLiteral("newnavnav-web"));
                verified.insert(QStringLiteral("module"), QStringLiteral("upload-website"));
                verified.insert(QStringLiteral("identity_verified"), true);
                verified.insert(QStringLiteral("identity_contract"),
                                QStringLiteral("health+snapshot-status.v1"));
                verified.insert(QStringLiteral("snapshot_status"), status.payload);
                status.payload = verified;
                status.healthy = true;
            }
        }
    }

    m_siteStatus = status;
    m_probeReply = nullptr;
    m_probeIdentityStage = false;
    m_probeHealthPayload = {};
    reply->deleteLater();
    emit siteStatusChanged(m_siteStatus);
    if (!status.errorCode.isEmpty()) {
        emit probeError(status.errorCode, status.errorMessage);
    }
    recomputeAggregate();

    const bool rerun = m_refreshQueued;
    m_refreshQueued = false;
    if (rerun && m_started) {
        beginSiteProbe();
    }
}

void UploadClient::beginHealthScan()
{
    if (m_healthScanInFlight) {
        m_healthScanQueued = true;
        return;
    }
    if (m_healthDirectory.isEmpty()) {
        m_workerStatuses.clear();
        emit workerStatusesChanged(m_workerStatuses);
        emit healthScanFinished(0, 0, false);
        recomputeAggregate();
        return;
    }

    m_healthScanInFlight = true;
    const quint64 generation = ++m_healthScanGeneration;
    const QString directory = m_healthDirectory;
    const qint64 freshnessMs = m_workerFreshnessMs;
    QPointer<UploadClient> guard(this);

    QThreadPool::globalInstance()->start([guard, generation, directory, freshnessMs] {
        HealthScanResult result;
        QHash<QString, UploadWorkerStatus> bySource;
        const QDateTime now = QDateTime::currentDateTimeUtc();

        const QFileInfo directoryInfo(directory);
        if (!directoryInfo.exists() || !directoryInfo.isDir() || !directoryInfo.isReadable()) {
            result.errorCode = QStringLiteral("health_directory_unavailable");
            result.errorMessage = QStringLiteral("配置的 upload worker 健康目录不存在或不可读");
        }

        QDirIterator iterator(
            directory,
            QStringList{QStringLiteral("*.json")},
            QDir::Files | QDir::Readable | QDir::NoSymLinks,
            QDirIterator::NoIteratorFlags);
        int encountered = 0;
        while (iterator.hasNext()) {
            const QString path = iterator.next();
            ++encountered;
            if (encountered > kMaximumHealthFiles) {
                result.limited = true;
                break;
            }
            const QFileInfo info(path);
            if (!info.isFile() || info.isSymLink() || info.size() < 0
                || info.size() > kMaximumHealthFileBytes) {
                ++result.invalidFiles;
                continue;
            }
            QFile file(path);
            if (!file.open(QIODevice::ReadOnly)) {
                ++result.invalidFiles;
                continue;
            }
            const QByteArray body = file.read(kMaximumHealthFileBytes + 1);
            if (body.size() > kMaximumHealthFileBytes) {
                ++result.invalidFiles;
                continue;
            }
            QJsonParseError parseError;
            const QJsonDocument document = QJsonDocument::fromJson(body, &parseError);
            if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
                ++result.invalidFiles;
                continue;
            }

            const QJsonObject object = document.object();
            if (object.contains(QStringLiteral("schema_version"))
                && object.value(QStringLiteral("schema_version")).toInt(-1) != 1) {
                ++result.invalidFiles;
                continue;
            }
            UploadWorkerStatus worker;
            worker.source = boundedString(object, "source", 128);
            if (worker.source.isEmpty()) {
                ++result.invalidFiles;
                continue;
            }
            bool pidOk = false;
            worker.pid = object.value(QStringLiteral("pid")).toVariant().toLongLong(&pidOk);
            if (!pidOk || worker.pid <= 0) {
                worker.pid = -1;
            }
            worker.state = boundedString(object, "state", 64);
            worker.stage = boundedString(object, "stage", 128);
            worker.updatedAt = parseDateTime(object.value(QStringLiteral("updated_at")));
            worker.lastSuccessAt = parseDateTime(object.value(QStringLiteral("last_success_at")));
            worker.lastFailureAt = parseDateTime(object.value(QStringLiteral("last_failure_at")));
            worker.lastHeartbeatAt = parseDateTime(object.value(QStringLiteral("last_heartbeat_at")));
            worker.accepted = object.value(QStringLiteral("accepted")).toVariant().toLongLong();
            if (!object.contains(QStringLiteral("accepted"))) {
                worker.accepted = -1;
            }
            const QJsonArray symbols = object.value(QStringLiteral("symbols")).toArray();
            for (const QJsonValue &symbol : symbols) {
                const QString value = symbol.toString().trimmed().toUpper();
                if (!value.isEmpty() && worker.symbols.size() < 512) {
                    worker.symbols.append(value.left(32));
                }
            }
            worker.detail = UploadClient::redact(boundedString(object, "detail", 500));
            worker.lastError = UploadClient::redact(boundedString(object, "last_error", 1'000));

            const QDateTime statusReference = newestDateTime(
                {worker.updatedAt, worker.lastHeartbeatAt, worker.lastSuccessAt, worker.lastFailureAt});
            worker.statusAgeMs = ageInMilliseconds(now, statusReference);
            worker.successAgeMs = ageInMilliseconds(now, worker.lastSuccessAt);
            worker.statusFresh = worker.statusAgeMs >= 0 && worker.statusAgeMs <= freshnessMs;
            worker.successFresh = worker.successAgeMs >= 0 && worker.successAgeMs <= freshnessMs;

            auto existing = bySource.find(worker.source);
            if (existing == bySource.end() || !existing->updatedAt.isValid()
                || (worker.updatedAt.isValid() && worker.updatedAt > existing->updatedAt)) {
                bySource.insert(worker.source, worker);
            }
        }

        result.workers = bySource.values();
        std::sort(result.workers.begin(), result.workers.end(), [](const auto &left, const auto &right) {
            return left.source.localeAwareCompare(right.source) < 0;
        });

        if (!guard) {
            return;
        }
        QMetaObject::invokeMethod(
            guard.data(),
            [guard, generation, result] {
                if (guard) {
                    guard->applyHealthScan(generation, result);
                }
            },
            Qt::QueuedConnection);
    });
}

void UploadClient::applyHealthScan(quint64 generation, const HealthScanResult &result)
{
    m_healthScanInFlight = false;
    if (generation == m_healthScanGeneration) {
        m_workerStatuses = result.workers;
        emit workerStatusesChanged(m_workerStatuses);
        emit healthScanFinished(m_workerStatuses.size(), result.invalidFiles, result.limited);
        if (!result.errorCode.isEmpty()) {
            emit probeError(result.errorCode, result.errorMessage);
        }
        recomputeAggregate();
    }

    const bool rerun = m_healthScanQueued || generation != m_healthScanGeneration;
    m_healthScanQueued = false;
    if (rerun && m_started) {
        beginHealthScan();
    }
}

void UploadClient::recomputeAggregate()
{
    int healthyWorkers = 0;
    int liveErrors = 0;
    for (const UploadWorkerStatus &worker : std::as_const(m_workerStatuses)) {
        const bool reportsError = worker.state.compare(QStringLiteral("error"), Qt::CaseInsensitive) == 0;
        const bool reportsOk = worker.state.compare(QStringLiteral("ok"), Qt::CaseInsensitive) == 0;
        if (worker.statusFresh && reportsError) {
            ++liveErrors;
        }
        if (worker.statusFresh && reportsOk && (!m_workersExpected || worker.successFresh)) {
            ++healthyWorkers;
        }
    }

    const int totalWorkers = m_workerStatuses.size();
    const bool healthFilesConfigured = !m_healthDirectory.isEmpty();
    AggregateState state = AggregateState::Unknown;
    QString summary;

    if (!m_siteStatus.observedAt.isValid()) {
        state = AggregateState::Unknown;
        summary = QStringLiteral("等待 NewNavNav 首次健康探测");
    } else if (!m_workersExpected && m_siteStatus.healthy && liveErrors == 0) {
        state = AggregateState::ScheduledIdle;
        summary = QStringLiteral("网站正常，upload worker 当前不在必须工作时段");
    } else if (m_siteStatus.healthy
               && (!healthFilesConfigured || (totalWorkers > 0 && healthyWorkers == totalWorkers))) {
        state = AggregateState::Healthy;
        summary = healthFilesConfigured
            ? QStringLiteral("网站正常，%1/%2 个 worker 健康").arg(healthyWorkers).arg(totalWorkers)
            : QStringLiteral("网站正常，未配置本地 worker 健康目录");
    } else if (!m_siteStatus.reachable && healthyWorkers == 0) {
        state = AggregateState::Offline;
        summary = QStringLiteral("网站不可达，且没有新鲜 worker 状态");
    } else {
        state = AggregateState::Degraded;
        if (!m_siteStatus.healthy) {
            summary = QStringLiteral("网站健康未通过；worker %1/%2 健康").arg(healthyWorkers).arg(totalWorkers);
        } else if (totalWorkers == 0 && healthFilesConfigured) {
            summary = QStringLiteral("网站正常，但未读到 worker 健康文件");
        } else {
            summary = QStringLiteral("网站正常，但仅 %1/%2 个 worker 健康").arg(healthyWorkers).arg(totalWorkers);
        }
    }

    m_aggregateState = state;
    emit aggregateStatusChanged(state, healthyWorkers, totalWorkers, summary);
}

QUrl UploadClient::endpointUrl(const QString &path) const
{
    QUrl url = m_baseUrl;
    url.setPath(path);
    url.setQuery(QString());
    url.setFragment(QString());
    return url;
}

QString UploadClient::normalizeFundSymbol(const QString &symbol)
{
    QString value = symbol.trimmed().toUpper();
    static const QRegularExpression prefixed(QStringLiteral("^(SH|SZ)[0-9]{5,6}$"));
    static const QRegularExpression digits(QStringLiteral("^[0-9]{6}$"));
    if (prefixed.match(value).hasMatch()) {
        return value;
    }
    if (!digits.match(value).hasMatch()) {
        return {};
    }
    const QString exchange = value.startsWith(QLatin1Char('5')) || value.startsWith(QLatin1Char('6'))
            || value.startsWith(QLatin1Char('9'))
        ? QStringLiteral("SH")
        : QStringLiteral("SZ");
    return exchange + value;
}

QString UploadClient::redact(const QString &text)
{
    QString value = text.left(2'048);
    static const QRegularExpression assignment(
        QStringLiteral("(?i)\\b(token|password|passwd|authorization|cookie|secret)\\b\\s*[:=]\\s*[^\\s,;]+"));
    value.replace(assignment, QStringLiteral("\\1=[REDACTED]"));
    static const QRegularExpression bearer(QStringLiteral("(?i)\\bBearer\\s+[A-Za-z0-9._~+/=-]+"));
    value.replace(bearer, QStringLiteral("Bearer [REDACTED]"));
    return value;
}

} // namespace machome::bridge
