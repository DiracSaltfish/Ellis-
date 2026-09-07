#include "WebullClient.h"

#include <QAbstractSocket>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPointer>
#include <QRandomGenerator>
#include <QRegularExpression>
#include <QSet>
#include <QSslError>
#include <QStringList>
#include <QThread>
#include <QTimer>
#include <QUrlQuery>
#include <QtMath>
#include <QtWebSockets/QWebSocket>
#include <QtWebSockets/QWebSocketProtocol>

#include <algorithm>
#include <cerrno>
#include <limits>
#include <utility>

#ifdef Q_OS_UNIX
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

using namespace Machome::Webull;

namespace {

constexpr qint64 kErrorRepeatIntervalMs = 30'000;
constexpr qint64 kMaximumCredentialBytes = 1'024;
constexpr qint64 kMaximumControlRequestBytes = 64 * 1024;
constexpr qint64 kMaximumControlResponseBytes = 256 * 1024;
constexpr qsizetype kMaximumPendingControls = 16;

#ifdef Q_OS_UNIX
struct FileIdentity {
    quint64 device = 0;
    quint64 inode = 0;
    quint64 owner = 0;
    quint32 mode = 0;
    qint64 size = -1;
    qint64 modifiedNs = -1;
    qint64 changedNs = -1;
    bool valid = false;

    bool operator==(const FileIdentity &other) const {
        return device == other.device && inode == other.inode
            && owner == other.owner && mode == other.mode && size == other.size
            && modifiedNs == other.modifiedNs && changedNs == other.changedNs
            && valid == other.valid;
    }

    bool operator!=(const FileIdentity &other) const {
        return !(*this == other);
    }
};

qint64 secondsAndNanoseconds(qint64 seconds, qint64 nanoseconds) {
    constexpr qint64 kNanosecondsPerSecond = 1'000'000'000;
    if (seconds < 0
        || seconds > std::numeric_limits<qint64>::max() / kNanosecondsPerSecond) {
        return -1;
    }
    return seconds * kNanosecondsPerSecond + nanoseconds;
}

FileIdentity fileIdentity(const struct stat &metadata) {
#ifdef Q_OS_DARWIN
    const qint64 modifiedNs = secondsAndNanoseconds(
        metadata.st_mtimespec.tv_sec, metadata.st_mtimespec.tv_nsec
    );
    const qint64 changedNs = secondsAndNanoseconds(
        metadata.st_ctimespec.tv_sec, metadata.st_ctimespec.tv_nsec
    );
#else
    const qint64 modifiedNs = secondsAndNanoseconds(metadata.st_mtime, 0);
    const qint64 changedNs = secondsAndNanoseconds(metadata.st_ctime, 0);
#endif
    return {
        static_cast<quint64>(metadata.st_dev),
        static_cast<quint64>(metadata.st_ino),
        static_cast<quint64>(metadata.st_uid),
        static_cast<quint32>(metadata.st_mode & 07777),
        static_cast<qint64>(metadata.st_size),
        modifiedNs,
        changedNs,
        true,
    };
}
#endif

QString boundedText(const QString &value, qsizetype maximum = 2'048) {
    QString result = value;
    result.replace(QChar::Null, QChar::ReplacementCharacter);
    result.replace(QLatin1Char('\r'), QLatin1Char(' '));
    result.replace(QLatin1Char('\n'), QLatin1Char(' '));
    if (result.size() > maximum)
        result = result.left(maximum) + QStringLiteral("…");
    return result;
}

bool hasExplicitUtcOffset(const QString &text) {
    if (text.endsWith(QLatin1Char('Z'), Qt::CaseInsensitive))
        return true;
    const qsizetype timeSeparator = text.indexOf(QLatin1Char('T'));
    if (timeSeparator < 0)
        return false;
    return text.indexOf(QLatin1Char('+'), timeSeparator) >= 0
        || text.indexOf(QLatin1Char('-'), timeSeparator) >= 0;
}

QDateTime parseUtcDateTime(const QString &text) {
    if (!hasExplicitUtcOffset(text))
        return {};
    QDateTime value = QDateTime::fromString(text, Qt::ISODateWithMs);
    if (!value.isValid())
        value = QDateTime::fromString(text, Qt::ISODate);
    return value.isValid() ? value.toUTC() : QDateTime{};
}

struct DecimalParts {
    bool negative = false;
    QString integer;
    QString fraction;
};

bool parsePlainDecimal(
    const QString &text,
    bool allowNegative,
    bool requirePositive,
    DecimalParts *parts = nullptr
) {
    if (text.isEmpty() || text.size() > 128)
        return false;

    qsizetype cursor = 0;
    bool negative = false;
    if (text.front() == QLatin1Char('-')) {
        if (!allowNegative)
            return false;
        negative = true;
        cursor = 1;
    }
    if (cursor >= text.size() || text.at(cursor) == QLatin1Char('+'))
        return false;

    bool dotSeen = false;
    bool digitSeen = false;
    QString integer;
    QString fraction;
    for (; cursor < text.size(); ++cursor) {
        const QChar character = text.at(cursor);
        if (character == QLatin1Char('.')) {
            if (dotSeen)
                return false;
            dotSeen = true;
            continue;
        }
        if (!character.isDigit())
            return false;
        digitSeen = true;
        (dotSeen ? fraction : integer).append(character);
    }
    if (!digitSeen || integer.isEmpty() || (dotSeen && fraction.isEmpty()))
        return false;

    while (integer.size() > 1 && integer.startsWith(QLatin1Char('0')))
        integer.remove(0, 1);
    while (fraction.endsWith(QLatin1Char('0')))
        fraction.chop(1);

    bool isZero = integer == QStringLiteral("0");
    for (const QChar character : std::as_const(fraction))
        isZero = isZero && character == QLatin1Char('0');
    if (negative && isZero)
        negative = false;
    if (requirePositive && (negative || isZero))
        return false;

    if (parts)
        *parts = DecimalParts{negative, integer, fraction};
    return true;
}

int comparePositiveDecimals(const QString &left, const QString &right) {
    DecimalParts lhs;
    DecimalParts rhs;
    if (!parsePlainDecimal(left, false, true, &lhs)
        || !parsePlainDecimal(right, false, true, &rhs)) {
        return 0;
    }
    if (lhs.integer.size() != rhs.integer.size())
        return lhs.integer.size() < rhs.integer.size() ? -1 : 1;
    const int integerCompare = QString::compare(
        lhs.integer, rhs.integer, Qt::CaseSensitive
    );
    if (integerCompare != 0)
        return integerCompare < 0 ? -1 : 1;

    const qsizetype width = std::max(lhs.fraction.size(), rhs.fraction.size());
    const int fractionCompare = QString::compare(
        lhs.fraction.leftJustified(width, QLatin1Char('0')),
        rhs.fraction.leftJustified(width, QLatin1Char('0')),
        Qt::CaseSensitive
    );
    return fractionCompare < 0 ? -1 : fractionCompare > 0 ? 1 : 0;
}

bool jsonNonNegativeInteger(const QJsonValue &value, qint64 *result) {
    constexpr double kMaximumExactJsonInteger = 9'007'199'254'740'991.0;
    if (!value.isDouble())
        return false;
    const double number = value.toDouble(-1.0);
    if (!qIsFinite(number) || number < 0.0 || qFloor(number) != number
        || number > kMaximumExactJsonInteger) {
        return false;
    }
    *result = static_cast<qint64>(number);
    return true;
}

void wipeByteArray(QByteArray *value) {
    if (!value)
        return;
    if (!value->isEmpty())
        value->fill('\0');
    value->clear();
    value->squeeze();
}

bool tokenBytesValid(const QByteArray &candidate) {
    if (candidate.size() < 32 || candidate.size() > kMaximumCredentialBytes)
        return false;
    for (const char byte : candidate) {
        const unsigned char value = static_cast<unsigned char>(byte);
        if (value <= 0x20 || value >= 0x7f) return false;
    }
    return true;
}

bool readRestrictedTokenFile(const QString &configuredPath, QByteArray *token,
                             QString *error) {
    if (token) token->clear();
    if (configuredPath.trimmed().isEmpty()) {
        if (error) *error = QStringLiteral("未配置独立 control_token_file");
        return false;
    }
    const QString path = QFileInfo(configuredPath.trimmed()).absoluteFilePath();
    QByteArray candidate;
#ifdef Q_OS_UNIX
    const QByteArray parentPath = QFile::encodeName(QFileInfo(path).absolutePath());
    struct stat parentMetadata {};
    if (::lstat(parentPath.constData(), &parentMetadata) != 0
        || !S_ISDIR(parentMetadata.st_mode) || S_ISLNK(parentMetadata.st_mode)
        || parentMetadata.st_uid != ::geteuid()
        || (parentMetadata.st_mode & 0077) != 0) {
        if (error) {
            *error = QStringLiteral("控制凭据父目录必须由当前用户拥有、不是符号链接且不向 group/other 授权");
        }
        return false;
    }
    int flags = O_RDONLY | O_NONBLOCK;
#ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const int descriptor = ::open(QFile::encodeName(path).constData(), flags);
    if (descriptor < 0) {
        if (error) *error = QStringLiteral("无法安全打开独立 Webull 控制凭据");
        return false;
    }
    struct stat before {};
    if (::fstat(descriptor, &before) != 0 || !S_ISREG(before.st_mode)
        || (before.st_mode & 07777) != 0600 || before.st_uid != ::geteuid()
        || before.st_nlink != 1 || before.st_size <= 0
        || before.st_size > kMaximumCredentialBytes) {
        ::close(descriptor);
        if (error) *error = QStringLiteral("独立 Webull 控制凭据必须是当前用户的 0600 单链接常规文件");
        return false;
    }
    const FileIdentity beforeIdentity = fileIdentity(before);
    QFile file;
    if (!file.open(descriptor, QIODevice::ReadOnly, QFileDevice::AutoCloseHandle)) {
        ::close(descriptor);
        if (error) *error = QStringLiteral("无法读取独立 Webull 控制凭据");
        return false;
    }
    QByteArray raw = file.read(kMaximumCredentialBytes + 1);
    struct stat after {};
    const bool stable = ::fstat(file.handle(), &after) == 0
        && fileIdentity(after) == beforeIdentity && after.st_nlink == 1
        && raw.size() == after.st_size;
    if (!stable || raw.isEmpty() || raw.size() > kMaximumCredentialBytes) {
        wipeByteArray(&raw);
        if (error) *error = QStringLiteral("Webull 控制凭据在读取期间变化或长度异常");
        return false;
    }
    candidate = raw.trimmed();
    wipeByteArray(&raw);
#else
    const QFileInfo info(path);
    const auto forbidden = QFileDevice::ReadGroup | QFileDevice::WriteGroup
        | QFileDevice::ExeGroup | QFileDevice::ReadOther
        | QFileDevice::WriteOther | QFileDevice::ExeOther;
    if (!info.isFile() || info.isSymLink() || (info.permissions() & forbidden)) {
        if (error) *error = QStringLiteral("独立 Webull 控制凭据权限不安全");
        return false;
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly) || file.size() <= 0
        || file.size() > kMaximumCredentialBytes) {
        if (error) *error = QStringLiteral("无法安全读取独立 Webull 控制凭据");
        return false;
    }
    candidate = file.read(kMaximumCredentialBytes + 1).trimmed();
#endif
    if (!tokenBytesValid(candidate)) {
        wipeByteArray(&candidate);
        if (error) *error = QStringLiteral("独立 Webull 控制凭据必须是 32–1024 位无空格可打印 ASCII");
        return false;
    }
    if (token) *token = candidate;
    wipeByteArray(&candidate);
    return true;
}

enum class EndpointKind {
    Live,
    Ready,
    Status,
    Symbols,
    Book,
    Clients,
};

QString endpointSuffix(EndpointKind endpoint, const QString &symbol) {
    switch (endpoint) {
    case EndpointKind::Live:
        return QStringLiteral("/health/live");
    case EndpointKind::Ready:
        return QStringLiteral("/health/ready");
    case EndpointKind::Status:
        return QStringLiteral("/status");
    case EndpointKind::Symbols:
        return QStringLiteral("/symbols");
    case EndpointKind::Book:
        return QStringLiteral("/book/") + symbol;
    case EndpointKind::Clients:
        return QStringLiteral("/clients");
    }
    return {};
}

QString endpointLabel(EndpointKind endpoint) {
    switch (endpoint) {
    case EndpointKind::Live:
        return QStringLiteral("health/live");
    case EndpointKind::Ready:
        return QStringLiteral("health/ready");
    case EndpointKind::Status:
        return QStringLiteral("status");
    case EndpointKind::Symbols:
        return QStringLiteral("symbols");
    case EndpointKind::Book:
        return QStringLiteral("book");
    case EndpointKind::Clients:
        return QStringLiteral("clients");
    }
    return QStringLiteral("unknown");
}

bool endpointNeedsToken(EndpointKind endpoint) {
    return endpoint != EndpointKind::Live;
}

struct PendingRequest {
    EndpointKind endpoint = EndpointKind::Live;
    QByteArray body;
    qint64 issuedAtMonotonicMs = -1;
    quint64 credentialGeneration = 0;
    bool timedOut = false;
    bool tooLarge = false;
};

struct PendingControl {
    QString requestId;
    QString action;
    QByteArray body;
    bool timedOut = false;
    bool tooLarge = false;
};

}  // namespace

class WebullClientWorker final : public QObject {
public:
    WebullClientWorker(WebullClientConfig config, WebullClient *owner)
        : config_(std::move(config)), owner_(owner) {
        status_.service = QStringLiteral("webull-lv2-gateway");
        status_.staleAfterMs = config_.defaultStaleAfterMs;
        monotonic_.start();
    }

    ~WebullClientWorker() override {
        wipeByteArray(&token_);
        wipeByteArray(&config_.bearerToken);
    }

    void startClient() {
        if (running_) {
            refreshNow();
            return;
        }
        if (!validateConfiguration())
            return;
        ensureInfrastructure();

        // A stop/start is a new observation epoch. Do not promote a cached
        // snapshot from the previous epoch back to fresh before this run has
        // actually received it again.
        hasBook_ = false;
        latestBook_ = {};
        retiredSessions_.clear();
        retiredSessionOrder_.clear();
        bookAcceptedMonotonicMs_ = -1;
        bookInitialAgeMs_ = -1;
        hasServerStatus_ = false;
        hasSymbolsMetadata_ = false;
        status_.dataAgeMs = -1;
        status_.dataFresh = false;
        restFailures_ = 0;
        lastRestFailureBumpMs_ = -1;
        websocketFailures_ = 0;
        running_ = true;
        loadCredentials(false);
        pollTimer_->setInterval(config_.pollIntervalMs);
        pollTimer_->start();
        metadataTimer_->start(config_.metadataPollIntervalMs);
        freshnessTimer_->start(config_.freshnessCheckIntervalMs);

        status_.pollingFallback = true;
        publishStatus();
        emitLog(
            LogSeverity::Info,
            QStringLiteral("CLIENT_STARTED"),
            QStringLiteral("Webull 观测客户端已启动；网络操作位于专用线程")
        );
        postToOwner([](WebullClient *owner) { emit owner->started(); });

        runPollCycle();
        requestEndpoint(EndpointKind::Symbols);
        openWebSocket();
    }

    void stopClient() {
        if (!running_)
            return;
        running_ = false;

        if (pollTimer_)
            pollTimer_->stop();
        if (metadataTimer_)
            metadataTimer_->stop();
        if (freshnessTimer_)
            freshnessTimer_->stop();
        if (websocketReconnectTimer_)
            websocketReconnectTimer_->stop();
        if (websocketConnectTimer_)
            websocketConnectTimer_->stop();
        if (websocketSilenceTimer_)
            websocketSilenceTimer_->stop();

        cancelPendingControls(
            QStringLiteral("CLIENT_STOPPED"),
            QStringLiteral("客户端已停止；在途控制的最终结果不确定"),
            true
        );

        const QList<QNetworkReply *> replies = pending_.keys();
        pending_.clear();
        inFlight_.clear();
        for (QNetworkReply *reply : replies) {
            reply->disconnect(this);
            reply->abort();
            reply->deleteLater();
        }

        if (socket_) {
            socket_->disconnect(this);
            socket_->abort();
            connectWebSocketSignals();
        }

        status_.apiLive = false;
        status_.apiReady = false;
        status_.streamConnected = false;
        status_.pollingFallback = false;
        updateFreshness();
        publishStatus();
        emitLog(
            LogSeverity::Info,
            QStringLiteral("CLIENT_STOPPED"),
            QStringLiteral("Webull 观测客户端已停止；未向网关发送停服命令")
        );
        postToOwner([](WebullClient *owner) { emit owner->stopped(); });
    }

    void shutdown() {
        stopClient();
        owner_.clear();
        wipeByteArray(&token_);
        wipeByteArray(&config_.bearerToken);
    }

    void refreshNow() {
        if (!running_)
            return;
        runPollCycle();
        requestEndpoint(EndpointKind::Symbols);
    }

    void reloadCredentials() {
        if (!running_)
            return;
        const bool loaded = loadCredentials(true);
        if (loaded) {
            emitLog(
                LogSeverity::Info,
                QStringLiteral("TOKEN_RELOADED"),
                QStringLiteral("Bearer 凭据已安全重载")
            );
            restartWebSocket();
            runPollCycle();
        } else if (socket_) {
            status_.apiReady = false;
            hasServerStatus_ = false;
            socket_->abort();
            status_.streamConnected = false;
            status_.pollingFallback = true;
            updateFreshness();
            publishStatus();
        }
    }

    void setBearerToken(QByteArray token) {
        wipeByteArray(&config_.bearerToken);
        config_.bearerToken = std::move(token);
        config_.tokenFile.clear();
        reloadCredentials();
    }

    void setTokenFile(QString path) {
        wipeByteArray(&config_.bearerToken);
        config_.tokenFile = std::move(path);
#ifdef Q_OS_UNIX
        credentialFileIdentity_ = {};
#else
        credentialFileModifiedMs_ = -1;
#endif
        reloadCredentials();
    }

    void submitControl(
        QString requestId,
        QString action,
        QJsonObject arguments
    ) {
        if (!running_ || !network_) {
            emitControlFailure(
                requestId, action, QStringLiteral("CLIENT_STOPPED"),
                QStringLiteral("请先启动 WebullClient 再提交控制请求"), false
            );
            return;
        }
        if (config_.controlBaseUrl.isEmpty()) {
            emitControlFailure(
                requestId, action, QStringLiteral("CONTROL_DISABLED"),
                QStringLiteral("未配置 loopback controlBaseUrl；控制功能保持禁用"),
                false
            );
            return;
        }
        static const QRegularExpression requestIdPattern(
            QStringLiteral("^[A-Za-z0-9_.:-]{8,128}$")
        );
        if (!requestIdPattern.match(requestId).hasMatch()) {
            emitControlFailure(
                requestId, action, QStringLiteral("INVALID_REQUEST_ID"),
                QStringLiteral("request_id 必须是 8–128 位的 ASCII 字母、数字或 _ . : -"),
                false
            );
            return;
        }

        static const QSet<QString> actions{
            QStringLiteral("set_schedule_mode"),
            QStringLiteral("collector_start"),
            QStringLiteral("collector_stop"),
            QStringLiteral("open_login"),
            QStringLiteral("restart_browser"),
        };
        if (!actions.contains(action)) {
            emitControlFailure(
                requestId, action, QStringLiteral("UNSUPPORTED_ACTION"),
                QStringLiteral("控制 action 不在安全白名单中"), false
            );
            return;
        }
        if (action == QStringLiteral("set_schedule_mode")) {
            const QString mode = arguments.value(QStringLiteral("mode")).toString();
            static const QSet<QString> modes{
                QStringLiteral("auto"),
                QStringLiteral("force_running"),
                QStringLiteral("force_stopped"),
            };
            if (arguments.size() != 1 || !modes.contains(mode)) {
                emitControlFailure(
                    requestId, action, QStringLiteral("INVALID_ARGUMENT"),
                    QStringLiteral("set_schedule_mode 仅接受 mode=auto|force_running|force_stopped"),
                    false
                );
                return;
            }
        } else if (!arguments.isEmpty()) {
            emitControlFailure(
                requestId, action, QStringLiteral("INVALID_ARGUMENT"),
                QStringLiteral("该控制 action 不接受 arguments"), false
            );
            return;
        }
        if (pendingControlIds_.contains(requestId)) {
            emitControlFailure(
                requestId, action, QStringLiteral("CONTROL_ALREADY_PENDING"),
                QStringLiteral("相同 request_id 的控制请求仍在处理中"), false
            );
            return;
        }
        if (pendingControls_.size() >= kMaximumPendingControls) {
            emitControlFailure(
                requestId, action, QStringLiteral("CONTROL_BUSY"),
                QStringLiteral("在途控制请求过多，本次未发送"), false
            );
            return;
        }

        const QJsonObject payload{
            {QStringLiteral("request_id"), requestId},
            {QStringLiteral("action"), action},
            {QStringLiteral("arguments"), arguments},
        };
        const QByteArray body = QJsonDocument(payload).toJson(QJsonDocument::Compact);
        if (body.size() > kMaximumControlRequestBytes) {
            emitControlFailure(
                requestId, action, QStringLiteral("CONTROL_REQUEST_TOO_LARGE"),
                QStringLiteral("控制请求超过 64 KiB 安全上限"), false
            );
            return;
        }

        QUrl url = config_.controlBaseUrl;
        url.setPath(QStringLiteral("/v1/commands"));
        url.setQuery(QString{});
        url.setFragment({});
        QNetworkRequest request(url);
        request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
        request.setRawHeader("Accept", "application/json");
        QByteArray controlToken;
        QString tokenError;
        if (!readRestrictedTokenFile(config_.controlTokenFile, &controlToken,
                                     &tokenError)) {
            emitControlFailure(requestId, action,
                               QStringLiteral("CONTROL_AUTH_UNAVAILABLE"),
                               tokenError, false);
            return;
        }
        request.setRawHeader("Authorization", QByteArray("Bearer ") + controlToken);
        wipeByteArray(&controlToken);
        request.setRawHeader("User-Agent", "MachomeHub-WebullClient/1");
        request.setAttribute(
            QNetworkRequest::RedirectPolicyAttribute,
            QNetworkRequest::ManualRedirectPolicy
        );
        request.setAttribute(
            QNetworkRequest::CacheLoadControlAttribute,
            QNetworkRequest::AlwaysNetwork
        );
        request.setTransferTimeout(config_.controlRequestTimeoutMs);

        QNetworkReply *reply = network_->post(request, body);
        const qint64 responseLimit = std::min(
            config_.maximumResponseBytes, kMaximumControlResponseBytes
        );
        reply->setReadBufferSize(responseLimit + 1);
        PendingControl pending;
        pending.requestId = requestId;
        pending.action = action;
        pendingControls_.insert(reply, pending);
        pendingControlIds_.insert(requestId);

        connect(reply, &QIODevice::readyRead, this, [this, reply] {
            consumeControlReplyData(reply);
        });
        connect(reply, &QNetworkReply::metaDataChanged, this, [this, reply, responseLimit] {
            const QVariant header = reply->header(QNetworkRequest::ContentLengthHeader);
            bool ok = false;
            const qint64 length = header.toLongLong(&ok);
            if (ok && length > responseLimit) {
                auto iterator = pendingControls_.find(reply);
                if (iterator != pendingControls_.end()) {
                    iterator->tooLarge = true;
                    reply->abort();
                }
            }
        });
        connect(reply, &QNetworkReply::finished, this, [this, reply] {
            finishControlReply(reply);
        });
        QTimer::singleShot(config_.controlRequestTimeoutMs, reply, [this, reply] {
            auto iterator = pendingControls_.find(reply);
            if (iterator == pendingControls_.end() || reply->isFinished())
                return;
            iterator->timedOut = true;
            reply->abort();
        });
    }

private:
    template <typename Function>
    void postToOwner(Function function) {
        QPointer<WebullClient> owner = owner_;
        if (!owner)
            return;
        QMetaObject::invokeMethod(
            owner,
            [owner, function = std::move(function)]() mutable {
                if (owner)
                    function(owner.data());
            },
            Qt::QueuedConnection
        );
    }

    void emitControlFailure(
        const QString &requestId,
        const QString &action,
        const QString &code,
        const QString &message,
        bool outcomeUncertain
    ) {
        const QString safeRequestId = boundedText(requestId, 128);
        const QString safeAction = boundedText(action, 64);
        const QString safeCode = boundedText(code, 128);
        const QString safeMessage = boundedText(message, 2'048);
        postToOwner([
            safeRequestId, safeAction, safeCode, safeMessage, outcomeUncertain
        ](WebullClient *owner) {
            emit owner->controlFailed(
                safeRequestId,
                safeAction,
                safeCode,
                safeMessage,
                outcomeUncertain
            );
        });
        emitLog(
            outcomeUncertain ? LogSeverity::Warning : LogSeverity::Error,
            safeCode,
            QStringLiteral("Webull 控制失败 action=%1 request_id=%2：%3")
                .arg(safeAction, safeRequestId, safeMessage)
        );
    }

    void cancelPendingControls(
        const QString &code,
        const QString &message,
        bool outcomeUncertain
    ) {
        const QList<QNetworkReply *> replies = pendingControls_.keys();
        for (QNetworkReply *reply : replies) {
            const auto iterator = pendingControls_.find(reply);
            if (iterator == pendingControls_.end())
                continue;
            const PendingControl pending = iterator.value();
            pendingControls_.erase(iterator);
            pendingControlIds_.remove(pending.requestId);
            reply->disconnect(this);
            reply->abort();
            reply->deleteLater();
            emitControlFailure(
                pending.requestId,
                pending.action,
                code,
                message,
                outcomeUncertain
            );
        }
    }

    void consumeControlReplyData(QNetworkReply *reply) {
        auto iterator = pendingControls_.find(reply);
        if (iterator == pendingControls_.end() || iterator->tooLarge)
            return;
        const qint64 responseLimit = std::min(
            config_.maximumResponseBytes, kMaximumControlResponseBytes
        );
        while (reply->bytesAvailable() > 0) {
            const qint64 remaining = responseLimit - iterator->body.size();
            if (remaining <= 0) {
                iterator->tooLarge = true;
                reply->abort();
                return;
            }
            const qint64 amount = std::min(reply->bytesAvailable(), remaining + 1);
            iterator->body.append(reply->read(amount));
            if (iterator->body.size() > responseLimit) {
                iterator->tooLarge = true;
                reply->abort();
                return;
            }
        }
    }

    void finishControlReply(QNetworkReply *reply) {
        auto iterator = pendingControls_.find(reply);
        if (iterator == pendingControls_.end()) {
            reply->deleteLater();
            return;
        }
        consumeControlReplyData(reply);
        const PendingControl pending = std::move(iterator.value());
        pendingControls_.erase(iterator);
        pendingControlIds_.remove(pending.requestId);

        const int httpStatus = reply->attribute(
            QNetworkRequest::HttpStatusCodeAttribute
        ).toInt();
        const QNetworkReply::NetworkError networkError = reply->error();
        reply->deleteLater();

        if (!running_)
            return;
        if (pending.tooLarge) {
            emitControlFailure(
                pending.requestId,
                pending.action,
                QStringLiteral("CONTROL_RESPONSE_TOO_LARGE"),
                QStringLiteral("控制响应超过安全上限；命令可能已执行"),
                true
            );
            return;
        }
        if (pending.timedOut) {
            emitControlFailure(
                pending.requestId,
                pending.action,
                QStringLiteral("CONTROL_TIMEOUT"),
                QStringLiteral("控制请求超时；命令可能已执行，请用相同 request_id 重试"),
                true
            );
            return;
        }
        if (httpStatus == 0 || (networkError != QNetworkReply::NoError
                                && httpStatus < 400)) {
            QString code = QStringLiteral("CONTROL_NETWORK_ERROR");
            QString message = QStringLiteral("本机 Webull 控制桥连接失败；命令可能已执行");
            if (networkError == QNetworkReply::ConnectionRefusedError) {
                code = QStringLiteral("CONTROL_CONNECTION_REFUSED");
                message = QStringLiteral("本机 Webull 控制桥未监听；命令未取得可验证回应");
            }
            emitControlFailure(
                pending.requestId, pending.action, code, message, true
            );
            return;
        }

        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(
            pending.body, &parseError
        );
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            emitControlFailure(
                pending.requestId,
                pending.action,
                QStringLiteral("CONTROL_PROTOCOL_ERROR"),
                QStringLiteral("控制桥返回了无效 JSON；命令可能已执行"),
                true
            );
            return;
        }

        const QJsonObject result = document.object();
        const QString returnedRequestId = result.value(QStringLiteral("request_id")).toString();
        const QString returnedAction = result.value(QStringLiteral("action")).toString();
        const bool responseIdentified = returnedRequestId == pending.requestId
            && returnedAction == pending.action;
        if (httpStatus >= 200 && httpStatus < 300
            && result.value(QStringLiteral("ok")).toBool(false)) {
            if (!responseIdentified) {
                emitControlFailure(
                    pending.requestId,
                    pending.action,
                    QStringLiteral("CONTROL_PROTOCOL_ERROR"),
                    QStringLiteral("控制响应的 request_id/action 与请求不匹配"),
                    true
                );
                return;
            }
            QJsonObject confirmedResult = result;
            confirmedResult.insert(QStringLiteral("client_control_completed_monotonic_ms"),
                                   monotonic_.elapsed());
            const QString requestId = pending.requestId;
            const QString action = pending.action;
            postToOwner([requestId, action, confirmedResult](WebullClient *owner) {
                emit owner->controlCompleted(requestId, action, confirmedResult);
            });
            emitLog(
                LogSeverity::Info,
                QStringLiteral("CONTROL_COMPLETED"),
                QStringLiteral("Webull 控制已接受 action=%1 request_id=%2")
                    .arg(action, requestId)
            );
            return;
        }

        QString code = boundedText(
            result.value(QStringLiteral("error")).toString(), 128
        );
        QString message = boundedText(
            result.value(QStringLiteral("message")).toString(), 2'048
        );
        bool uncertain = httpStatus >= 500;
        if (code.isEmpty())
            code = QStringLiteral("CONTROL_HTTP_ERROR");
        if (httpStatus == 401) {
            code = QStringLiteral("AUTH_REJECTED");
            message = QStringLiteral("控制桥拒绝 Bearer token（HTTP 401）");
            uncertain = false;
        } else if (httpStatus == 403) {
            code = QStringLiteral("CIDR_REJECTED");
            message = QStringLiteral("控制桥拒绝非 loopback 客户端（HTTP 403）");
            uncertain = false;
        } else if (httpStatus == 404) {
            code = QStringLiteral("CONTROL_UNAVAILABLE");
            message = QStringLiteral("本地 runner 不提供 /v1/commands（HTTP 404）");
            uncertain = false;
        } else if (httpStatus == 409) {
            code = QStringLiteral("IDEMPOTENCY_CONFLICT");
            if (message.isEmpty())
                message = QStringLiteral("相同 request_id 已用于不同命令");
            uncertain = false;
        } else if (httpStatus == 504 || code == QStringLiteral("CONTROL_TIMEOUT")) {
            code = QStringLiteral("CONTROL_TIMEOUT");
            if (message.isEmpty())
                message = QStringLiteral("控制桥等待 Qt 主线程超时；命令可能已执行");
            uncertain = true;
        } else if (code == QStringLiteral("CONTROL_BUSY")) {
            if (message.isEmpty())
                message = QStringLiteral("控制桥忙，本次命令未投递执行");
            uncertain = false;
        } else if (code == QStringLiteral("CONTROL_FAILED")) {
            if (message.isEmpty())
                message = QStringLiteral("控制桥执行失败；命令可能已部分生效");
            uncertain = true;
        } else if (message.isEmpty()) {
            message = QStringLiteral("控制桥返回 HTTP %1").arg(httpStatus);
        }
        emitControlFailure(
            pending.requestId, pending.action, code, message, uncertain
        );
    }

    void ensureInfrastructure() {
        if (network_)
            return;

        network_ = new QNetworkAccessManager(this);
        // API and control addresses are direct endpoints. Never expose the
        // Authorization header to a process-wide/system HTTP proxy.
        network_->setProxy(QNetworkProxy::NoProxy);
        pollTimer_ = new QTimer(this);
        metadataTimer_ = new QTimer(this);
        freshnessTimer_ = new QTimer(this);
        websocketReconnectTimer_ = new QTimer(this);
        websocketConnectTimer_ = new QTimer(this);
        websocketSilenceTimer_ = new QTimer(this);
        websocketReconnectTimer_->setSingleShot(true);
        websocketConnectTimer_->setSingleShot(true);
        websocketSilenceTimer_->setSingleShot(true);

        connect(pollTimer_, &QTimer::timeout, this, [this] { runPollCycle(); });
        connect(metadataTimer_, &QTimer::timeout, this, [this] {
            maybeReloadCredentialFile();
            requestEndpoint(EndpointKind::Symbols);
        });
        connect(freshnessTimer_, &QTimer::timeout, this, [this] {
            updateFreshness();
            publishStatus();
        });
        connect(websocketReconnectTimer_, &QTimer::timeout, this, [this] {
            openWebSocket();
        });
        connect(websocketConnectTimer_, &QTimer::timeout, this, [this] {
            if (!running_ || !socket_ || websocketHelloReceived_) {
                return;
            }
            const bool upgraded = socket_->state() == QAbstractSocket::ConnectedState;
            socket_->abort();
            reportError(
                upgraded
                    ? QStringLiteral("WEBSOCKET_HELLO_TIMEOUT")
                    : QStringLiteral("WEBSOCKET_TIMEOUT"),
                QStringLiteral("/v2/stream"),
                upgraded
                    ? QStringLiteral("WebSocket 已 Upgrade，但未在时限内收到 v2 hello；继续轮询并准备重连")
                    : QStringLiteral("WebSocket 握手超时，已切换为轮询并准备重连"),
                0,
                static_cast<int>(QNetworkReply::TimeoutError),
                true
            );
            scheduleWebSocketReconnect();
        });
        connect(websocketSilenceTimer_, &QTimer::timeout, this, [this] {
            if (!running_ || !socket_ || !websocketHelloReceived_)
                return;
            status_.streamConnected = false;
            status_.pollingFallback = true;
            reportError(
                QStringLiteral("WEBSOCKET_SILENCE_TIMEOUT"),
                QStringLiteral("/v2/stream"),
                QStringLiteral("WebSocket 超过 %1 ms 未收到盘口或应用层 heartbeat；已启用 REST 回退并重连")
                    .arg(config_.websocketSilenceTimeoutMs),
                0,
                static_cast<int>(QNetworkReply::TimeoutError),
                true
            );
            socket_->abort();
        });

        createWebSocket();
    }

    void createWebSocket() {
        Q_ASSERT(!socket_);
        socket_ = new QWebSocket(QString(), QWebSocketProtocol::VersionLatest, this);
        socket_->setProxy(QNetworkProxy::NoProxy);
        socket_->setMaxAllowedIncomingMessageSize(
            static_cast<quint64>(config_.maximumWebSocketMessageBytes)
        );
        socket_->setMaxAllowedIncomingFrameSize(
            static_cast<quint64>(config_.maximumWebSocketMessageBytes)
        );
        connectWebSocketSignals();
    }

    void connectWebSocketSignals() {
        connect(socket_, &QWebSocket::connected, this, [this] {
            websocketHelloReceived_ = false;
            // An HTTP Upgrade alone is not a usable v2 stream. Keep REST as
            // the authoritative fallback until the application-level hello
            // has also been validated. The connect timer deliberately keeps
            // running and therefore doubles as the hello deadline.
            status_.streamConnected = false;
            status_.pollingFallback = true;
            publishStatus();
            emitLog(
                LogSeverity::Info,
                QStringLiteral("WEBSOCKET_CONNECTED"),
                QStringLiteral("已连接 Webull v2 行情流；等待 hello")
            );
        });
        connect(socket_, &QWebSocket::disconnected, this, [this] {
            websocketConnectTimer_->stop();
            websocketSilenceTimer_->stop();
            const bool wasConnected = status_.streamConnected;
            status_.streamConnected = false;
            status_.pollingFallback = running_;
            publishStatus();
            if (running_) {
                emitLog(
                    wasConnected ? LogSeverity::Warning : LogSeverity::Info,
                    QStringLiteral("WEBSOCKET_DISCONNECTED"),
                    QStringLiteral("WebSocket 已断开；REST 轮询继续提供回退数据")
                );
                scheduleWebSocketReconnect();
            }
        });
        connect(socket_, &QWebSocket::textMessageReceived, this,
                [this](const QString &message) { handleWebSocketText(message); });
        connect(socket_, &QWebSocket::binaryMessageReceived, this,
                [this](const QByteArray &) {
                    failWebSocketProtocol(
                        QStringLiteral("v2 协议要求 JSON text frame，收到未支持的二进制帧")
                    );
                });
        connect(socket_, &QWebSocket::errorOccurred, this,
                [this](QAbstractSocket::SocketError error) {
                    QString code = QStringLiteral("WEBSOCKET_ERROR");
                    QString message = QStringLiteral("WebSocket 连接错误；REST 轮询仍在工作");
                    const QString detail = socket_->errorString();
                    if (detail.contains(QStringLiteral("401"))
                        || detail.contains(QStringLiteral("unauthorized"), Qt::CaseInsensitive)) {
                        code = QStringLiteral("AUTH_REJECTED");
                        message = QStringLiteral("WebSocket Bearer token 被网关拒绝（HTTP 401）");
                    } else if (detail.contains(QStringLiteral("403"))
                               || detail.contains(QStringLiteral("forbidden"), Qt::CaseInsensitive)) {
                        code = QStringLiteral("CIDR_REJECTED");
                        message = QStringLiteral("WebSocket 客户端地址不在网关 CIDR 白名单（HTTP 403）");
                    }
                    reportError(
                        code,
                        QStringLiteral("/v2/stream"),
                        message,
                        0,
                        static_cast<int>(error),
                        code == QStringLiteral("WEBSOCKET_ERROR")
                    );
                    status_.streamConnected = false;
                    status_.pollingFallback = running_;
                    publishStatus();
                });
        connect(socket_, &QWebSocket::sslErrors, this,
                [this](const QList<QSslError> &errors) {
                    reportError(
                        QStringLiteral("TLS_ERROR"),
                        QStringLiteral("/v2/stream"),
                        QStringLiteral("WebSocket TLS 校验失败（%1 项）；未忽略证书错误")
                            .arg(errors.size()),
                        0,
                        static_cast<int>(QNetworkReply::SslHandshakeFailedError),
                        false
                    );
                });
    }

    bool validateConfiguration() {
        config_.symbol = config_.symbol.trimmed().toUpper();
        if (config_.symbol.isEmpty() || config_.symbol.size() > 32) {
            reportError(
                QStringLiteral("CONFIG_SYMBOL"), {},
                QStringLiteral("Webull symbol 为空或过长"), 0, 0, false
            );
            return false;
        }
        for (const QChar character : std::as_const(config_.symbol)) {
            if (!character.isLetterOrNumber() && character != QLatin1Char('.')
                && character != QLatin1Char('-') && character != QLatin1Char('_')) {
                reportError(
                    QStringLiteral("CONFIG_SYMBOL"), {},
                    QStringLiteral("Webull symbol 含不允许的字符"), 0, 0, false
                );
                return false;
            }
        }

        const QString scheme = config_.apiBaseUrl.scheme().toLower();
        if (!config_.apiBaseUrl.isValid()
            || (scheme != QStringLiteral("http") && scheme != QStringLiteral("https"))
            || config_.apiBaseUrl.host().isEmpty()
            || !config_.apiBaseUrl.userInfo().isEmpty()
            || config_.apiBaseUrl.hasQuery()
            || !config_.apiBaseUrl.fragment().isEmpty()) {
            reportError(
                QStringLiteral("CONFIG_API_URL"), {},
                QStringLiteral("apiBaseUrl 必须是无用户信息、query 和 fragment 的 HTTP(S) URL"),
                0, 0, false
            );
            return false;
        }
        QString apiPath = config_.apiBaseUrl.path();
        while (apiPath.size() > 1 && apiPath.endsWith(QLatin1Char('/')))
            apiPath.chop(1);
        if (!apiPath.isEmpty() && apiPath != QStringLiteral("/")
            && apiPath != QStringLiteral("/v2")) {
            reportError(
                QStringLiteral("CONFIG_API_URL"), {},
                QStringLiteral("apiBaseUrl 的 path 必须为空、/ 或 /v2"),
                0, 0, false
            );
            return false;
        }
        config_.apiBaseUrl.setPath(QStringLiteral("/v2"));

        if (!config_.streamUrl.isEmpty()) {
            const QString streamScheme = config_.streamUrl.scheme().toLower();
            QString streamPath = config_.streamUrl.path();
            while (streamPath.size() > 1 && streamPath.endsWith(QLatin1Char('/')))
                streamPath.chop(1);
            if (!config_.streamUrl.isValid()
                || (streamScheme != QStringLiteral("ws")
                    && streamScheme != QStringLiteral("wss"))
                || config_.streamUrl.host().isEmpty()
                || !config_.streamUrl.userInfo().isEmpty()
                || !config_.streamUrl.fragment().isEmpty()
                || streamPath != QStringLiteral("/v2/stream")) {
                reportError(
                    QStringLiteral("CONFIG_STREAM_URL"), {},
                    QStringLiteral("streamUrl 必须是无用户信息和 fragment、path 为 /v2/stream 的 ws(s) URL"),
                    0, 0, false
                );
                return false;
            }
            config_.streamUrl.setPath(QStringLiteral("/v2/stream"));
        }

        if (!config_.controlBaseUrl.isEmpty()) {
            const QString controlScheme = config_.controlBaseUrl.scheme().toLower();
            const QString controlHost = config_.controlBaseUrl.host().toLower();
            QString controlPath = config_.controlBaseUrl.path();
            while (controlPath.size() > 1 && controlPath.endsWith(QLatin1Char('/')))
                controlPath.chop(1);
            if (!config_.controlBaseUrl.isValid()
                || controlScheme != QStringLiteral("http")
                || controlHost != QStringLiteral("127.0.0.1")
                || !config_.controlBaseUrl.userInfo().isEmpty()
                || config_.controlBaseUrl.hasQuery()
                || !config_.controlBaseUrl.fragment().isEmpty()
                || (!controlPath.isEmpty() && controlPath != QStringLiteral("/")
                    && controlPath != QStringLiteral("/v1"))) {
                reportError(
                    QStringLiteral("CONFIG_CONTROL_URL"), {},
                    QStringLiteral("controlBaseUrl 必须是 literal http://127.0.0.1 URL，path 为 /v1"),
                    0, 0, false
                );
                return false;
            }
            config_.controlBaseUrl.setPath(QStringLiteral("/v1"));
            if (config_.controlTokenFile.trimmed().isEmpty()) {
                reportError(
                    QStringLiteral("CONFIG_CONTROL_TOKEN"), {},
                    QStringLiteral("配置 controlBaseUrl 时必须使用独立 controlTokenFile"),
                    0, 0, false
                );
                return false;
            }
        }

        config_.pollIntervalMs = std::clamp(config_.pollIntervalMs, 250, 300'000);
        config_.metadataPollIntervalMs = std::clamp(
            config_.metadataPollIntervalMs, 1'000, 3'600'000
        );
        config_.requestTimeoutMs = std::clamp(config_.requestTimeoutMs, 250, 120'000);
        config_.controlRequestTimeoutMs = std::clamp(
            config_.controlRequestTimeoutMs, 1'000, 120'000);
        config_.freshnessCheckIntervalMs = std::clamp(
            config_.freshnessCheckIntervalMs, 250, 60'000
        );
        config_.websocketConnectTimeoutMs = std::clamp(
            config_.websocketConnectTimeoutMs, 500, 120'000
        );
        config_.websocketSilenceTimeoutMs = std::clamp(
            config_.websocketSilenceTimeoutMs, 30'000, 10 * 60 * 1'000
        );
        config_.reconnectInitialMs = std::clamp(config_.reconnectInitialMs, 250, 60'000);
        config_.reconnectMaximumMs = std::max(
            config_.reconnectInitialMs,
            std::clamp(config_.reconnectMaximumMs, 250, 300'000)
        );
        config_.restBackoffMaximumMs = std::max(
            config_.pollIntervalMs,
            std::clamp(config_.restBackoffMaximumMs, 250, 300'000)
        );
        config_.maximumResponseBytes = std::clamp<qint64>(
            config_.maximumResponseBytes, 4'096, 16 * 1024 * 1024
        );
        config_.maximumWebSocketMessageBytes = std::clamp<qint64>(
            config_.maximumWebSocketMessageBytes, 4'096, 16 * 1024 * 1024
        );
        config_.defaultStaleAfterMs = std::clamp<qint64>(
            config_.defaultStaleAfterMs, 1'000, 24 * 60 * 60 * 1'000
        );
        config_.maximumFutureSkewMs = std::clamp<qint64>(
            config_.maximumFutureSkewMs, 0, 5 * 60 * 1'000
        );
        config_.maximumBookLevels = std::clamp(config_.maximumBookLevels, 1, 1'000);
        config_.maximumClients = std::clamp(config_.maximumClients, 1, 65'536);
        status_.staleAfterMs = config_.defaultStaleAfterMs;
        return true;
    }

    bool tokenIsValid(const QByteArray &candidate) const {
        return tokenBytesValid(candidate);
    }

    void beginCredentialGeneration() {
        ++credentialGeneration_;
        const QList<QNetworkReply *> replies = pending_.keys();
        for (QNetworkReply *reply : replies) {
            const auto iterator = pending_.find(reply);
            if (iterator == pending_.end()
                || !endpointNeedsToken(iterator->endpoint)) {
                continue;
            }
            inFlight_.remove(static_cast<int>(iterator->endpoint));
            pending_.erase(iterator);
            reply->disconnect(this);
            reply->abort();
            reply->deleteLater();
        }
    }

    bool loadCredentials(bool isReload) {
        // A failed explicit reload must not silently keep using an older
        // credential whose file is now missing or has unsafe permissions.
        beginCredentialGeneration();
        wipeByteArray(&token_);
        QByteArray candidate;
        if (!config_.bearerToken.isEmpty()) {
            candidate = config_.bearerToken.trimmed();
#ifdef Q_OS_UNIX
            credentialFileIdentity_ = {};
#endif
        } else if (!config_.tokenFile.trimmed().isEmpty()) {
            const QString path = QFileInfo(config_.tokenFile).absoluteFilePath();
#ifdef Q_OS_UNIX
            const QByteArray encodedPath = QFile::encodeName(path);
            int openFlags = O_RDONLY | O_NONBLOCK;
#ifdef O_CLOEXEC
            openFlags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
            openFlags |= O_NOFOLLOW;
#endif
            const int descriptor = ::open(encodedPath.constData(), openFlags);
            if (descriptor < 0) {
                reportError(
                    QStringLiteral("TOKEN_FILE_INVALID"), path,
                    QStringLiteral("Bearer token 文件无法安全打开；文件可能不存在或为符号链接"),
                    0, 0, false
                );
                return false;
            }

            struct stat metadata {};
            if (::fstat(descriptor, &metadata) != 0 || !S_ISREG(metadata.st_mode)) {
                ::close(descriptor);
                reportError(
                    QStringLiteral("TOKEN_FILE_INVALID"), path,
                    QStringLiteral("Bearer token 文件必须是常规文件且不能是符号链接"),
                    0, 0, false
                );
                return false;
            }
            if ((metadata.st_mode & 07777) != 0600) {
                ::close(descriptor);
                reportError(
                    QStringLiteral("TOKEN_FILE_PERMISSIONS"), path,
                    QStringLiteral("Bearer token 文件权限必须严格为 0600"),
                    0, 0, false
                );
                return false;
            }
            if (metadata.st_uid != ::geteuid()) {
                ::close(descriptor);
                reportError(
                    QStringLiteral("TOKEN_FILE_OWNER"), path,
                    QStringLiteral("Bearer token 文件必须由当前用户拥有"),
                    0, 0, false
                );
                return false;
            }
            if (metadata.st_size <= 0 || metadata.st_size > kMaximumCredentialBytes) {
                ::close(descriptor);
                reportError(
                    QStringLiteral("TOKEN_FILE_READ"), path,
                    QStringLiteral("Bearer token 文件大小异常"),
                    0, 0, true
                );
                return false;
            }

            const FileIdentity before = fileIdentity(metadata);
            QFile file;
            if (!file.open(
                    descriptor,
                    QIODevice::ReadOnly,
                    QFileDevice::AutoCloseHandle)) {
                ::close(descriptor);
                reportError(
                    QStringLiteral("TOKEN_FILE_READ"), path,
                    QStringLiteral("无法读取已安全打开的 Bearer token 文件"),
                    0, 0, true
                );
                return false;
            }
            QByteArray raw = file.read(kMaximumCredentialBytes + 1);
            struct stat afterMetadata {};
            const bool stable = ::fstat(file.handle(), &afterMetadata) == 0
                && fileIdentity(afterMetadata) == before
                && raw.size() == afterMetadata.st_size;
            if (!stable || raw.isEmpty() || raw.size() > kMaximumCredentialBytes) {
                wipeByteArray(&raw);
                reportError(
                    QStringLiteral("TOKEN_FILE_CHANGED"), path,
                    QStringLiteral("Bearer token 文件在读取期间发生变化或长度异常；已拒绝使用"),
                    0, 0, true
                );
                return false;
            }
            candidate = raw.trimmed();
            wipeByteArray(&raw);
            credentialFileIdentity_ = before;
#else
            const QFileInfo info(path);
            const auto forbidden = QFileDevice::ReadGroup | QFileDevice::WriteGroup
                | QFileDevice::ExeGroup | QFileDevice::ReadOther
                | QFileDevice::WriteOther | QFileDevice::ExeOther;
            if (!info.isFile() || info.isSymLink() || (info.permissions() & forbidden)) {
                reportError(
                    QStringLiteral("TOKEN_FILE_PERMISSIONS"), path,
                    QStringLiteral("Bearer token 文件必须是仅当前用户可访问的常规文件"),
                    0, 0, false
                );
                return false;
            }
            QFile file(path);
            if (!file.open(QIODevice::ReadOnly) || file.size() <= 0
                || file.size() > kMaximumCredentialBytes) {
                reportError(
                    QStringLiteral("TOKEN_FILE_READ"), path,
                    QStringLiteral("无法安全读取 Bearer token 文件或文件大小异常"),
                    0, 0, true
                );
                return false;
            }
            candidate = file.read(kMaximumCredentialBytes + 1).trimmed();
            credentialFileModifiedMs_ = QFileInfo(file).lastModified().toMSecsSinceEpoch();
#endif
        }

        if (!tokenIsValid(candidate)) {
            reportError(
                candidate.isEmpty() ? QStringLiteral("TOKEN_MISSING")
                                    : QStringLiteral("TOKEN_INVALID"),
                QStringLiteral("/v2"),
                candidate.isEmpty()
                    ? QStringLiteral("未配置 Bearer token；仅公开 live 健康检查可用")
                    : QStringLiteral("Bearer token 格式无效（要求 32–1024 个可打印 ASCII 字符）"),
                0, 0, false
            );
            wipeByteArray(&candidate);
            return false;
        }

        token_ = candidate;
        wipeByteArray(&candidate);
        if (!isReload) {
            emitLog(
                LogSeverity::Info,
                QStringLiteral("TOKEN_READY"),
                QStringLiteral("Bearer 凭据已载入（值已隐藏）")
            );
        }
        if (lastErrorCode_.startsWith(QStringLiteral("TOKEN_"))
            || lastErrorCode_ == QStringLiteral("AUTH_REJECTED")) {
            lastErrorCode_.clear();
            status_.lastError.clear();
        }
        return true;
    }

    void maybeReloadCredentialFile() {
        if (!config_.bearerToken.isEmpty() || config_.tokenFile.trimmed().isEmpty())
            return;
#ifdef Q_OS_UNIX
        struct stat metadata {};
        const QByteArray encodedPath = QFile::encodeName(
            QFileInfo(config_.tokenFile).absoluteFilePath()
        );
        FileIdentity current;
        if (::lstat(encodedPath.constData(), &metadata) == 0
            && S_ISREG(metadata.st_mode) && !S_ISLNK(metadata.st_mode)) {
            current = fileIdentity(metadata);
        }
        if (current != credentialFileIdentity_) {
            reloadCredentials();
            if (token_.isEmpty())
                credentialFileIdentity_ = current;
        }
#else
        const QFileInfo info(config_.tokenFile);
        const qint64 modified = info.exists()
            ? info.lastModified().toMSecsSinceEpoch() : -1;
        if (modified != credentialFileModifiedMs_)
            reloadCredentials();
#endif
    }

    QUrl endpointUrl(EndpointKind endpoint) const {
        QUrl url = config_.apiBaseUrl;
        QString path = url.path();
        while (path.size() > 1 && path.endsWith(QLatin1Char('/')))
            path.chop(1);
        if (path.isEmpty() || path == QStringLiteral("/"))
            path = QStringLiteral("/v2");
        else if (!path.endsWith(QStringLiteral("/v2")))
            path += QStringLiteral("/v2");
        path += endpointSuffix(endpoint, config_.symbol);
        url.setPath(path);
        url.setQuery(QUrlQuery{});
        url.setFragment({});
        return url;
    }

    QUrl websocketUrl() const {
        QUrl url = config_.streamUrl;
        if (url.isEmpty()) {
            url = config_.apiBaseUrl;
            url.setScheme(url.scheme().toLower() == QStringLiteral("https")
                              ? QStringLiteral("wss") : QStringLiteral("ws"));
            QString path = url.path();
            while (path.size() > 1 && path.endsWith(QLatin1Char('/')))
                path.chop(1);
            if (path.isEmpty() || path == QStringLiteral("/"))
                path = QStringLiteral("/v2");
            else if (!path.endsWith(QStringLiteral("/v2")))
                path += QStringLiteral("/v2");
            url.setPath(path + QStringLiteral("/stream"));
        }
        url.setUserInfo({});
        url.setFragment({});
        QUrlQuery query;
        query.addQueryItem(QStringLiteral("symbol"), config_.symbol);
        url.setQuery(query);
        return url;
    }

    void runPollCycle() {
        if (!running_)
            return;
        maybeReloadCredentialFile();
        requestEndpoint(EndpointKind::Live);
        if (token_.isEmpty())
            return;
        requestEndpoint(EndpointKind::Ready);
        requestEndpoint(EndpointKind::Status);
        requestEndpoint(EndpointKind::Book);
        requestEndpoint(EndpointKind::Clients);
    }

    void requestEndpoint(EndpointKind endpoint) {
        if (!running_ || !network_)
            return;
        const int endpointKey = static_cast<int>(endpoint);
        if (inFlight_.contains(endpointKey))
            return;
        if (endpointNeedsToken(endpoint) && token_.isEmpty())
            return;

        const QUrl url = endpointUrl(endpoint);
        QNetworkRequest request(url);
        request.setRawHeader("Accept", "application/json");
        request.setRawHeader("User-Agent", "MachomeHub-WebullClient/1");
        request.setAttribute(
            QNetworkRequest::RedirectPolicyAttribute,
            QNetworkRequest::ManualRedirectPolicy
        );
        request.setAttribute(
            QNetworkRequest::CacheLoadControlAttribute,
            QNetworkRequest::AlwaysNetwork
        );
        request.setTransferTimeout(config_.requestTimeoutMs);
        if (endpointNeedsToken(endpoint))
            request.setRawHeader("Authorization", QByteArray("Bearer ") + token_);

        QNetworkReply *reply = network_->get(request);
        reply->setReadBufferSize(config_.maximumResponseBytes + 1);
        PendingRequest pending;
        pending.endpoint = endpoint;
        pending.issuedAtMonotonicMs = monotonic_.elapsed();
        pending.credentialGeneration = credentialGeneration_;
        pending_.insert(reply, pending);
        inFlight_.insert(endpointKey);

        connect(reply, &QIODevice::readyRead, this,
                [this, reply] { consumeReplyData(reply); });
        connect(reply, &QNetworkReply::metaDataChanged, this, [this, reply] {
            const QVariant lengthHeader = reply->header(QNetworkRequest::ContentLengthHeader);
            bool ok = false;
            const qint64 length = lengthHeader.toLongLong(&ok);
            if (ok && length > config_.maximumResponseBytes) {
                auto iterator = pending_.find(reply);
                if (iterator != pending_.end()) {
                    iterator->tooLarge = true;
                    reply->abort();
                }
            }
        });
        connect(reply, &QNetworkReply::finished, this,
                [this, reply] { finishReply(reply); });
        connect(reply, &QNetworkReply::sslErrors, this,
                [this, reply](const QList<QSslError> &errors) {
                    const auto iterator = pending_.constFind(reply);
                    if (iterator == pending_.cend())
                        return;
                    reportError(
                        QStringLiteral("TLS_ERROR"),
                        endpointUrl(iterator->endpoint).path(),
                        QStringLiteral("REST TLS 校验失败（%1 项）；未忽略证书错误")
                            .arg(errors.size()),
                        0,
                        static_cast<int>(QNetworkReply::SslHandshakeFailedError),
                        false
                    );
                });
        QTimer::singleShot(config_.requestTimeoutMs, reply, [this, reply] {
            auto iterator = pending_.find(reply);
            if (iterator == pending_.end() || reply->isFinished())
                return;
            iterator->timedOut = true;
            reply->abort();
        });
    }

    void consumeReplyData(QNetworkReply *reply) {
        auto iterator = pending_.find(reply);
        if (iterator == pending_.end() || iterator->tooLarge)
            return;

        while (reply->bytesAvailable() > 0) {
            const qint64 remaining = config_.maximumResponseBytes
                - iterator->body.size();
            if (remaining <= 0) {
                iterator->tooLarge = true;
                reply->abort();
                return;
            }
            const qint64 amount = std::min(reply->bytesAvailable(), remaining + 1);
            iterator->body.append(reply->read(amount));
            if (iterator->body.size() > config_.maximumResponseBytes) {
                iterator->tooLarge = true;
                reply->abort();
                return;
            }
        }
    }

    void markEndpointUnavailable(EndpointKind endpoint) {
        if (endpoint == EndpointKind::Live) {
            status_.apiLive = false;
            status_.apiReady = false;
        } else if (endpoint == EndpointKind::Ready) {
            status_.apiReady = false;
        }
    }

    void finishReply(QNetworkReply *reply) {
        auto iterator = pending_.find(reply);
        if (iterator == pending_.end()) {
            reply->deleteLater();
            return;
        }
        consumeReplyData(reply);
        PendingRequest pending = std::move(iterator.value());
        pending_.erase(iterator);
        inFlight_.remove(static_cast<int>(pending.endpoint));

        const int httpStatus = reply->attribute(
            QNetworkRequest::HttpStatusCodeAttribute
        ).toInt();
        const QNetworkReply::NetworkError networkError = reply->error();
        const QString endpoint = endpointUrl(pending.endpoint).path();
        reply->deleteLater();

        if (!running_)
            return;
        if (endpointNeedsToken(pending.endpoint)
            && pending.credentialGeneration != credentialGeneration_) {
            return;
        }
        if (pending.tooLarge) {
            markEndpointUnavailable(pending.endpoint);
            reportError(
                QStringLiteral("RESPONSE_TOO_LARGE"), endpoint,
                QStringLiteral("响应超过 %1 字节安全上限")
                    .arg(config_.maximumResponseBytes),
                httpStatus, 0, false
            );
            noteRestFailure();
            return;
        }
        if (pending.timedOut) {
            markEndpointUnavailable(pending.endpoint);
            reportError(
                QStringLiteral("REQUEST_TIMEOUT"), endpoint,
                QStringLiteral("REST 请求在 %1 ms 内未完成")
                    .arg(config_.requestTimeoutMs),
                httpStatus, static_cast<int>(QNetworkReply::TimeoutError), true
            );
            noteRestFailure();
            return;
        }

        const bool semanticUnavailable = httpStatus == 503
            && (pending.endpoint == EndpointKind::Ready
                || pending.endpoint == EndpointKind::Book);
        if (httpStatus >= 300 && !semanticUnavailable) {
            reportHttpError(pending.endpoint, httpStatus, networkError);
            if (httpStatus >= 500 || httpStatus == 0)
                noteRestFailure();
            return;
        }
        if (networkError != QNetworkReply::NoError && !semanticUnavailable) {
            reportNetworkError(pending.endpoint, networkError);
            noteRestFailure();
            return;
        }
        if (pending.endpoint == EndpointKind::Book && httpStatus == 503) {
            status_.apiReady = false;
            if (status_.dataState.isEmpty())
                status_.dataState = QStringLiteral("no_data");
            publishStatus();
            return;
        }

        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(
            pending.body, &parseError
        );
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            markEndpointUnavailable(pending.endpoint);
            reportProtocolError(
                endpoint,
                QStringLiteral("REST 返回的 JSON 对象无效：%1")
                    .arg(parseError.errorString())
            );
            return;
        }

        handleJsonResponse(
            pending.endpoint,
            document.object(),
            httpStatus,
            pending.issuedAtMonotonicMs
        );
        if (pending.endpoint == EndpointKind::Live
            || pending.endpoint == EndpointKind::Status) {
            noteRestSuccess();
        }
    }

    void reportHttpError(
        EndpointKind endpoint,
        int httpStatus,
        QNetworkReply::NetworkError networkError
    ) {
        markEndpointUnavailable(endpoint);
        QString code = QStringLiteral("HTTP_ERROR");
        QString message = QStringLiteral("REST %1 返回 HTTP %2")
            .arg(endpointLabel(endpoint)).arg(httpStatus);
        bool retryable = httpStatus >= 500;
        if (httpStatus == 401) {
            code = QStringLiteral("AUTH_REJECTED");
            message = QStringLiteral("Bearer token 被网关拒绝（HTTP 401）；请重载凭据");
            retryable = false;
        } else if (httpStatus == 403) {
            code = QStringLiteral("CIDR_REJECTED");
            message = QStringLiteral("客户端地址不在网关 allowed_cidrs 白名单（HTTP 403）");
            retryable = false;
        } else if (httpStatus == 404) {
            code = QStringLiteral("ENDPOINT_NOT_FOUND");
            message = QStringLiteral("网关不存在预期 v2 端点（HTTP 404）；请核对版本和 base URL");
            retryable = false;
        } else if (httpStatus >= 300 && httpStatus < 400) {
            code = QStringLiteral("REDIRECT_REJECTED");
            message = QStringLiteral("为防止 Bearer 泄露，客户端拒绝跟随 HTTP 重定向（HTTP %1）")
                .arg(httpStatus);
            retryable = false;
        }
        reportError(
            code,
            endpointUrl(endpoint).path(),
            message,
            httpStatus,
            static_cast<int>(networkError),
            retryable
        );
    }

    void reportNetworkError(
        EndpointKind endpoint,
        QNetworkReply::NetworkError networkError
    ) {
        markEndpointUnavailable(endpoint);
        QString code = QStringLiteral("NETWORK_ERROR");
        QString message = QStringLiteral("无法访问 Webull 网关；将指数退避重试");
        switch (networkError) {
        case QNetworkReply::ConnectionRefusedError:
            code = QStringLiteral("CONNECTION_REFUSED");
            message = QStringLiteral("Webull 网关连接被拒绝；请确认 18765 服务正在监听");
            break;
        case QNetworkReply::HostNotFoundError:
            code = QStringLiteral("HOST_NOT_FOUND");
            message = QStringLiteral("无法解析 Webull 网关主机名");
            break;
        case QNetworkReply::TimeoutError:
            code = QStringLiteral("REQUEST_TIMEOUT");
            message = QStringLiteral("Webull 网关网络请求超时");
            break;
        case QNetworkReply::SslHandshakeFailedError:
            code = QStringLiteral("TLS_ERROR");
            message = QStringLiteral("Webull 网关 TLS 握手失败；未忽略证书错误");
            break;
        default:
            break;
        }
        reportError(
            code,
            endpointUrl(endpoint).path(),
            message,
            0,
            static_cast<int>(networkError),
            true
        );
    }

    void handleJsonResponse(
        EndpointKind endpoint,
        const QJsonObject &object,
        int httpStatus,
        qint64 issuedAtMonotonicMs
    ) {
        switch (endpoint) {
        case EndpointKind::Live:
            handleLive(object);
            break;
        case EndpointKind::Ready:
            handleReady(object, httpStatus, issuedAtMonotonicMs);
            break;
        case EndpointKind::Status:
            handleStatus(object, issuedAtMonotonicMs);
            break;
        case EndpointKind::Symbols:
            handleSymbols(object);
            break;
        case EndpointKind::Book:
            handleBookObject(
                object,
                QStringLiteral("rest-book"),
                issuedAtMonotonicMs
            );
            break;
        case EndpointKind::Clients:
            handleClients(object);
            break;
        }
    }

    void handleLive(const QJsonObject &object) {
        const bool ok = object.value(QStringLiteral("ok")).toBool(false);
        const QString service = object.value(QStringLiteral("service")).toString();
        const int version = object.value(QStringLiteral("api_version")).toInt(-1);
        if (!ok || service != QStringLiteral("webull-lv2-gateway") || version != 2) {
            status_.apiLive = false;
            status_.apiReady = false;
            reportProtocolError(
                QStringLiteral("/v2/health/live"),
                QStringLiteral("live 响应的 service/api_version 与 Webull v2 合同不符")
            );
            return;
        }
        status_.apiLive = true;
        status_.service = service;
        if (lastErrorCode_.startsWith(QStringLiteral("NETWORK_"))
            || lastErrorCode_ == QStringLiteral("CONNECTION_REFUSED")
            || lastErrorCode_ == QStringLiteral("HOST_NOT_FOUND")
            || lastErrorCode_ == QStringLiteral("REQUEST_TIMEOUT")) {
            lastErrorCode_.clear();
            status_.lastError.clear();
        }
        publishStatus();
    }

    void updateStatusFields(const QJsonObject &serverStatus) {
        status_.rawStatus = serverStatus;
        status_.browserState = boundedText(
            serverStatus.value(QStringLiteral("browser")).toString(), 64
        );
        status_.authState = boundedText(
            serverStatus.value(QStringLiteral("auth")).toString(), 64
        );
        status_.dataState = boundedText(
            serverStatus.value(QStringLiteral("data")).toString(), 64
        );
        hasServerStatus_ = !status_.dataState.isEmpty();
        status_.browserMessage = boundedText(
            serverStatus.value(QStringLiteral("browser_message")).toString(), 512
        );
        status_.authMessage = boundedText(
            serverStatus.value(QStringLiteral("auth_message")).toString(), 512
        );
        status_.dataMessage = boundedText(
            serverStatus.value(QStringLiteral("data_message")).toString(), 512
        );
        status_.collectorRunning = serverStatus.value(
            QStringLiteral("collector_running")
        ).toBool(false);
        status_.apiReportedRunning = serverStatus.value(
            QStringLiteral("api_running")
        ).toBool(false);
        status_.apiAddress = boundedText(
            serverStatus.value(QStringLiteral("api_address")).toString(), 256
        );
        status_.scheduleMode = boundedText(
            serverStatus.value(QStringLiteral("schedule_mode")).toString(), 64
        );
        status_.scheduleMessage = boundedText(
            serverStatus.value(QStringLiteral("schedule_message")).toString(), 512
        );
        status_.loginMonitorMessage = boundedText(
            serverStatus.value(QStringLiteral("login_monitor_message")).toString(), 512
        );
        status_.lastDepthAt = boundedText(
            serverStatus.value(QStringLiteral("last_depth_at")).toString(), 64
        );
        status_.lastAuthSuccessAt = boundedText(
            serverStatus.value(QStringLiteral("last_auth_success_at")).toString(), 64
        );
        status_.lastAuthRequiredAt = boundedText(
            serverStatus.value(QStringLiteral("last_auth_required_at")).toString(), 64
        );
        status_.lastAuthCheckedAt = boundedText(
            serverStatus.value(QStringLiteral("last_auth_checked_at")).toString(), 64
        );
        status_.lastLoginCheckAt = boundedText(
            serverStatus.value(QStringLiteral("last_login_check_at")).toString(), 64
        );
        status_.lastLoginAlertAt = boundedText(
            serverStatus.value(QStringLiteral("last_login_alert_at")).toString(), 64
        );
        status_.lastLoginAlertError = boundedText(
            serverStatus.value(QStringLiteral("last_login_alert_error")).toString(), 512
        );

        qint64 integer = 0;
        if (jsonNonNegativeInteger(
                serverStatus.value(QStringLiteral("valid_responses")), &integer)) {
            status_.validResponses = integer;
        }
        if (jsonNonNegativeInteger(
                serverStatus.value(QStringLiteral("invalid_responses")), &integer)) {
            status_.invalidResponses = integer;
        }

        const QJsonValue sourceInterval = serverStatus.value(
            QStringLiteral("source_interval_ms")
        );
        status_.hasSourceInterval = sourceInterval.isDouble()
            && qIsFinite(sourceInterval.toDouble()) && sourceInterval.toDouble() >= 0.0;
        if (status_.hasSourceInterval)
            status_.sourceIntervalMs = sourceInterval.toDouble();

        const QJsonValue latency = serverStatus.value(
            QStringLiteral("gateway_latency_ms")
        );
        status_.hasGatewayLatency = latency.isDouble()
            && qIsFinite(latency.toDouble()) && latency.toDouble() >= 0.0;
        if (status_.hasGatewayLatency)
            status_.gatewayLatencyMs = latency.toDouble();

        const QString serverError = boundedText(
            serverStatus.value(QStringLiteral("last_error")).toString(), 2'048
        );
        if (!serverError.isEmpty()) {
            status_.lastError = serverError;
            if (serverError != lastServerError_) {
                lastServerError_ = serverError;
                emitLog(
                    LogSeverity::Warning,
                    QStringLiteral("GATEWAY_LAST_ERROR"),
                    QStringLiteral("网关报告错误：%1").arg(serverError)
                );
            }
        } else if (lastErrorCode_.isEmpty()) {
            status_.lastError.clear();
            lastServerError_.clear();
        }
    }

    void handleReady(const QJsonObject &object, int httpStatus,
                     qint64 issuedAtMonotonicMs) {
        if (!object.value(QStringLiteral("ready")).isBool()) {
            status_.apiReady = false;
            reportProtocolError(
                QStringLiteral("/v2/health/ready"),
                QStringLiteral("ready 响应缺少布尔字段 ready")
            );
            return;
        }
        status_.apiReady = object.value(QStringLiteral("ready")).toBool()
            && httpStatus == 200;
        status_.readyProbeIssuedMonotonicMs = issuedAtMonotonicMs;
        const QJsonValue nestedStatus = object.value(QStringLiteral("status"));
        if (nestedStatus.isObject())
            updateStatusFields(nestedStatus.toObject());
        updateFreshness();
        publishStatus();
    }

    void handleStatus(const QJsonObject &object, qint64 issuedAtMonotonicMs) {
        const QJsonValue statusValue = object.value(QStringLiteral("status"));
        if (!statusValue.isObject()) {
            reportProtocolError(
                QStringLiteral("/v2/status"),
                QStringLiteral("status 响应缺少对象字段 status")
            );
            return;
        }
        updateStatusFields(statusValue.toObject());
        status_.statusProbeIssuedMonotonicMs = issuedAtMonotonicMs;
        qint64 clients = 0;
        if (jsonNonNegativeInteger(
                object.value(QStringLiteral("client_count")), &clients)) {
            status_.clientCount = static_cast<int>(
                std::min<qint64>(clients, std::numeric_limits<int>::max())
            );
        }
        const QJsonValue depth = object.value(QStringLiteral("depth"));
        if (depth.isObject())
            handleBookObject(
                depth.toObject(),
                QStringLiteral("rest-status"),
                issuedAtMonotonicMs
            );
        updateFreshness();
        publishStatus();
    }

    void handleSymbols(const QJsonObject &object) {
        if (object.value(QStringLiteral("schema_version")).toInt(-1) != 2
            || object.value(QStringLiteral("snapshot_semantics")).toString()
                   != QStringLiteral("full_replace")
            || object.value(QStringLiteral("price_encoding")).toString()
                   != QStringLiteral("decimal_string")
            || object.value(QStringLiteral("volume_encoding")).toString()
                   != QStringLiteral("decimal_string")) {
            reportProtocolError(
                QStringLiteral("/v2/symbols"),
                QStringLiteral("symbols 响应的 v2 编码合同不匹配")
            );
            return;
        }
        const QJsonValue symbolsValue = object.value(QStringLiteral("symbols"));
        if (!symbolsValue.isArray() || symbolsValue.toArray().size() > 1'000) {
            reportProtocolError(
                QStringLiteral("/v2/symbols"),
                QStringLiteral("symbols 字段不是合理大小的数组")
            );
            return;
        }

        SymbolList symbols;
        bool configuredFound = false;
        for (const QJsonValue &value : symbolsValue.toArray()) {
            if (!value.isObject()) {
                reportProtocolError(
                    QStringLiteral("/v2/symbols"),
                    QStringLiteral("symbols 数组含非对象元素")
                );
                return;
            }
            const QJsonObject item = value.toObject();
            SymbolInfo symbol;
            symbol.symbol = item.value(QStringLiteral("symbol")).toString().trimmed().toUpper();
            symbol.tickerId = item.value(QStringLiteral("ticker_id")).toString();
            symbol.depthSize = item.value(QStringLiteral("depth_size")).toInt(0);
            qint64 stale = 0;
            if (symbol.symbol.isEmpty() || symbol.tickerId.isEmpty()
                || symbol.depthSize <= 0
                || !jsonNonNegativeInteger(
                    item.value(QStringLiteral("stale_after_ms")), &stale)
                || stale < 1'000 || stale > 24 * 60 * 60 * 1'000) {
                reportProtocolError(
                    QStringLiteral("/v2/symbols"),
                    QStringLiteral("symbols 数组含无效标的元数据")
                );
                return;
            }
            symbol.staleAfterMs = stale;
            symbols.append(symbol);
            if (symbol.symbol == config_.symbol) {
                configuredFound = true;
                status_.staleAfterMs = stale;
            }
        }
        if (!configuredFound) {
            reportProtocolError(
                QStringLiteral("/v2/symbols"),
                QStringLiteral("网关未声明配置标的 %1").arg(config_.symbol)
            );
            return;
        }

        hasSymbolsMetadata_ = true;
        updateFreshness();
        postToOwner([symbols](WebullClient *owner) {
            emit owner->symbolsUpdated(symbols);
        });
        publishStatus();
    }

    void handleClients(const QJsonObject &object) {
        const QJsonValue clientsValue = object.value(QStringLiteral("clients"));
        if (!clientsValue.isArray()
            || clientsValue.toArray().size() > config_.maximumClients) {
            reportProtocolError(
                QStringLiteral("/v2/clients"),
                QStringLiteral("clients 字段不是合理大小的数组")
            );
            return;
        }

        ClientList clients;
        clients.reserve(clientsValue.toArray().size());
        for (const QJsonValue &value : clientsValue.toArray()) {
            if (!value.isObject()) {
                reportProtocolError(
                    QStringLiteral("/v2/clients"),
                    QStringLiteral("clients 数组含非对象元素")
                );
                return;
            }
            const QJsonObject item = value.toObject();
            ClientInfo client;
            client.clientId = boundedText(
                item.value(QStringLiteral("client_id")).toString(), 128
            );
            client.remote = boundedText(
                item.value(QStringLiteral("remote")).toString(), 256
            );
            client.connectedAt = parseUtcDateTime(
                item.value(QStringLiteral("connected_at")).toString()
            );
            const QJsonValue lastSent = item.value(QStringLiteral("last_sent_at"));
            if (lastSent.isString() && !lastSent.toString().isEmpty()) {
                client.lastSentAt = parseUtcDateTime(lastSent.toString());
                client.hasLastSentAt = client.lastSentAt.isValid();
            }
            qint64 sent = 0;
            if (client.clientId.isEmpty() || client.remote.isEmpty()
                || !client.connectedAt.isValid()
                || !jsonNonNegativeInteger(
                    item.value(QStringLiteral("messages_sent")), &sent)
                || (lastSent.isString() && !lastSent.toString().isEmpty()
                    && !client.hasLastSentAt)) {
                reportProtocolError(
                    QStringLiteral("/v2/clients"),
                    QStringLiteral("clients 数组含无效客户端记录")
                );
                return;
            }
            client.messagesSent = sent;
            clients.append(client);
        }
        status_.clientCount = clients.size();
        postToOwner([clients](WebullClient *owner) {
            emit owner->clientsUpdated(clients);
        });
        publishStatus();
    }

    bool parseBook(
        const QJsonObject &data,
        const QString &source,
        BookSnapshot *snapshot,
        QString *error
    ) const {
        if (data.value(QStringLiteral("schema_version")).toInt(-1) != 2
            || data.value(QStringLiteral("type")).toString()
                   != QStringLiteral("depth_snapshot")) {
            *error = QStringLiteral("不支持的 snapshot schema/type");
            return false;
        }

        snapshot->symbol = data.value(QStringLiteral("symbol")).toString().trimmed().toUpper();
        snapshot->tickerId = data.value(QStringLiteral("ticker_id")).toString();
        snapshot->sessionId = boundedText(
            data.value(QStringLiteral("session_id")).toString(), 128
        );
        qint64 sequence = 0;
        if (!jsonNonNegativeInteger(data.value(QStringLiteral("sequence")), &sequence)
            || sequence <= 0) {
            *error = QStringLiteral("sequence 必须是正整数");
            return false;
        }
        snapshot->sequence = sequence;
        snapshot->capturedAt = parseUtcDateTime(
            data.value(QStringLiteral("captured_at")).toString()
        );
        snapshot->publishedAt = parseUtcDateTime(
            data.value(QStringLiteral("published_at")).toString()
        );
        const QJsonValue changedValue = data.value(QStringLiteral("changed"));
        const QJsonValue hashValue = data.value(QStringLiteral("content_hash"));
        if (!changedValue.isBool() || !hashValue.isString()) {
            *error = QStringLiteral("changed/content_hash 字段类型无效");
            return false;
        }
        snapshot->changed = changedValue.toBool();
        snapshot->contentHash = boundedText(
            hashValue.toString(), 128
        );
        snapshot->source = source;

        if (snapshot->symbol != config_.symbol || snapshot->tickerId.isEmpty()
            || snapshot->sessionId.isEmpty() || !snapshot->capturedAt.isValid()
            || !snapshot->publishedAt.isValid() || snapshot->contentHash.isEmpty()) {
            *error = QStringLiteral("snapshot 标识、标的或 UTC 时间戳无效");
            return false;
        }
        if (snapshot->capturedAt.msecsTo(snapshot->publishedAt) < 0) {
            *error = QStringLiteral("published_at 早于 captured_at");
            return false;
        }
        const qint64 futureBy = QDateTime::currentDateTimeUtc().msecsTo(
            snapshot->capturedAt
        );
        if (futureBy > config_.maximumFutureSkewMs) {
            *error = QStringLiteral("captured_at 超前本机时钟 %1 ms").arg(futureBy);
            return false;
        }

        const QJsonValue sourceInterval = data.value(QStringLiteral("source_interval_ms"));
        if (!sourceInterval.isNull() && !sourceInterval.isUndefined()) {
            if (!sourceInterval.isDouble() || !qIsFinite(sourceInterval.toDouble())
                || sourceInterval.toDouble() < 0.0) {
                *error = QStringLiteral("source_interval_ms 无效");
                return false;
            }
            snapshot->hasSourceInterval = true;
            snapshot->sourceIntervalMs = sourceInterval.toDouble();
        }
        const QJsonValue latency = data.value(QStringLiteral("gateway_latency_ms"));
        if (!latency.isDouble() || !qIsFinite(latency.toDouble())
            || latency.toDouble() < 0.0) {
            *error = QStringLiteral("gateway_latency_ms 无效");
            return false;
        }
        snapshot->gatewayLatencyMs = latency.toDouble();

        const QJsonValue bookValue = data.value(QStringLiteral("book"));
        if (!bookValue.isObject()) {
            *error = QStringLiteral("缺少 book 对象");
            return false;
        }
        const QJsonObject book = bookValue.toObject();
        if (book.value(QStringLiteral("aggregation")).toString()
            != QStringLiteral("price")) {
            *error = QStringLiteral("只支持 price 聚合订单簿");
            return false;
        }

        if (!parseBookSide(
                book.value(QStringLiteral("bids")), true, &snapshot->bids, error)
            || !parseBookSide(
                book.value(QStringLiteral("asks")), false, &snapshot->asks, error)) {
            return false;
        }

        const int bidDepth = book.value(QStringLiteral("bid_depth")).toInt(-1);
        const int askDepth = book.value(QStringLiteral("ask_depth")).toInt(-1);
        const int depth = book.value(QStringLiteral("depth")).toInt(-1);
        if (bidDepth != snapshot->bids.size() || askDepth != snapshot->asks.size()
            || depth != std::max(snapshot->bids.size(), snapshot->asks.size())) {
            *error = QStringLiteral("book depth 计数与档位数组不一致");
            return false;
        }

        const QJsonValue insideValue = book.value(QStringLiteral("inside_market"));
        if (!insideValue.isObject()) {
            *error = QStringLiteral("缺少 inside_market 对象");
            return false;
        }
        const QJsonObject inside = insideValue.toObject();
        snapshot->inside.bestBid = inside.value(QStringLiteral("best_bid")).toString();
        snapshot->inside.bestAsk = inside.value(QStringLiteral("best_ask")).toString();
        snapshot->inside.spread = inside.value(QStringLiteral("spread")).toString();
        snapshot->inside.midPrice = inside.value(QStringLiteral("mid_price")).toString();
        snapshot->inside.state = inside.value(QStringLiteral("state")).toString();

        if (snapshot->inside.bestBid != snapshot->bids.constFirst().price
            || snapshot->inside.bestAsk != snapshot->asks.constFirst().price
            || !parsePlainDecimal(snapshot->inside.spread, true, false)
            || !parsePlainDecimal(snapshot->inside.midPrice, false, true)) {
            *error = QStringLiteral("inside_market 数值或一档边界无效");
            return false;
        }
        const int insideOrder = comparePositiveDecimals(
            snapshot->inside.bestBid, snapshot->inside.bestAsk
        );
        const QString expectedState = insideOrder < 0
            ? QStringLiteral("normal")
            : insideOrder == 0 ? QStringLiteral("locked")
                               : QStringLiteral("crossed");
        if (snapshot->inside.state != expectedState) {
            *error = QStringLiteral("inside_market.state 与买一/卖一关系不一致");
            return false;
        }
        return true;
    }

    bool parseBookSide(
        const QJsonValue &value,
        bool bidSide,
        QList<PriceLevel> *levels,
        QString *error
    ) const {
        if (!value.isArray()) {
            *error = bidSide ? QStringLiteral("bids 不是数组")
                             : QStringLiteral("asks 不是数组");
            return false;
        }
        const QJsonArray array = value.toArray();
        if (array.isEmpty() || array.size() > config_.maximumBookLevels) {
            *error = QStringLiteral("订单簿侧为空或超过 %1 档")
                .arg(config_.maximumBookLevels);
            return false;
        }

        levels->clear();
        levels->reserve(array.size());
        for (qsizetype index = 0; index < array.size(); ++index) {
            if (!array.at(index).isObject()) {
                *error = QStringLiteral("订单簿第 %1 项不是对象").arg(index);
                return false;
            }
            const QJsonObject item = array.at(index).toObject();
            qint64 levelNumber = 0;
            PriceLevel level;
            level.price = item.value(QStringLiteral("price")).toString();
            level.volume = item.value(QStringLiteral("volume")).toString();
            if (!jsonNonNegativeInteger(item.value(QStringLiteral("level")), &levelNumber)
                || levelNumber != index + 1
                || !parsePlainDecimal(level.price, false, true)
                || !parsePlainDecimal(level.volume, false, false)) {
                *error = QStringLiteral("订单簿第 %1 项的 level/price/volume 无效")
                    .arg(index);
                return false;
            }
            level.level = static_cast<int>(levelNumber);
            if (!levels->isEmpty()) {
                const int ordering = comparePositiveDecimals(
                    levels->constLast().price, level.price
                );
                if ((bidSide && ordering <= 0) || (!bidSide && ordering >= 0)) {
                    *error = bidSide
                        ? QStringLiteral("bids 未严格按价格降序")
                        : QStringLiteral("asks 未严格按价格升序");
                    return false;
                }
            }
            levels->append(level);
        }
        return true;
    }

    bool handleBookObject(
        const QJsonObject &object,
        const QString &source,
        qint64 issuedAtMonotonicMs = -1
    ) {
        const QString endpoint = source == QStringLiteral("websocket")
            ? QStringLiteral("/v2/stream")
            : source == QStringLiteral("rest-status")
                ? QStringLiteral("/v2/status")
                : QStringLiteral("/v2/book");
        BookSnapshot snapshot;
        QString error;
        if (!parseBook(object, source, &snapshot, &error)) {
            reportProtocolError(
                endpoint,
                QStringLiteral("无效完整盘口：%1").arg(error)
            );
            return false;
        }

        if (hasBook_ && snapshot.sessionId == latestBook_.sessionId) {
            if (snapshot.sequence < latestBook_.sequence)
                return true;
            if (snapshot.sequence == latestBook_.sequence) {
                if (!snapshot.contentHash.isEmpty()
                    && !latestBook_.contentHash.isEmpty()
                    && snapshot.contentHash != latestBook_.contentHash) {
                    reportProtocolError(
                        endpoint,
                        QStringLiteral("同一 session/sequence 出现不同 content_hash")
                    );
                    return false;
                }
                return true;
            }
            if (snapshot.sequence != latestBook_.sequence + 1) {
                emitLogRateLimited(
                    QStringLiteral("sequence-gap"),
                    LogSeverity::Warning,
                    QStringLiteral("SEQUENCE_GAP"),
                    QStringLiteral("盘口序号跳跃：期望 %1，收到 %2；按 full_replace 应用新快照")
                        .arg(latestBook_.sequence + 1).arg(snapshot.sequence),
                    10'000
                );
            }
        } else if (hasBook_) {
            if (retiredSessions_.contains(snapshot.sessionId))
                return true;
            // A REST request issued before the latest accepted book can finish
            // after a gateway restart. Its old-session reply must not flip the
            // client back. Unlike wall-clock comparison, this remains correct
            // across NTP adjustments.
            if (source.startsWith(QStringLiteral("rest-"))
                && issuedAtMonotonicMs >= 0
                && bookAcceptedMonotonicMs_ >= 0
                && issuedAtMonotonicMs <= bookAcceptedMonotonicMs_) {
                return true;
            }
            retiredSessions_.insert(latestBook_.sessionId);
            retiredSessionOrder_.append(latestBook_.sessionId);
            while (retiredSessionOrder_.size() > 64) {
                retiredSessions_.remove(retiredSessionOrder_.takeFirst());
            }
            emitLog(
                LogSeverity::Info,
                QStringLiteral("SESSION_CHANGED"),
                QStringLiteral("网关 session 已变化；序号基线已重置")
            );
        }

        latestBook_ = snapshot;
        hasBook_ = true;
        bookAcceptedMonotonicMs_ = monotonic_.elapsed();
        bookInitialAgeMs_ = std::max<qint64>(
            0,
            latestBook_.capturedAt.msecsTo(QDateTime::currentDateTimeUtc())
        );
        status_.lastDepthAt = snapshot.capturedAt.toString(Qt::ISODateWithMs);
        status_.hasSourceInterval = snapshot.hasSourceInterval;
        status_.sourceIntervalMs = snapshot.sourceIntervalMs;
        status_.hasGatewayLatency = true;
        status_.gatewayLatencyMs = snapshot.gatewayLatencyMs;
        updateFreshness(false);
        latestBook_.ageMs = status_.dataAgeMs;
        latestBook_.fresh = status_.dataFresh;

        const BookSnapshot accepted = latestBook_;
        postToOwner([accepted](WebullClient *owner) {
            emit owner->bookUpdated(accepted);
        });
        publishStatus();
        return true;
    }

    void openWebSocket() {
        if (!running_ || token_.isEmpty() || !socket_)
            return;
        const QUrl url = websocketUrl();
        const QString scheme = url.scheme().toLower();
        if (!url.isValid() || url.host().isEmpty()
            || (scheme != QStringLiteral("ws") && scheme != QStringLiteral("wss"))) {
            reportError(
                QStringLiteral("CONFIG_STREAM_URL"), {},
                QStringLiteral("streamUrl 必须是有效的 ws:// 或 wss:// URL"),
                0, 0, false
            );
            return;
        }
        if (socket_->state() != QAbstractSocket::UnconnectedState)
            socket_->abort();

        QNetworkRequest request(url);
        request.setRawHeader("Authorization", QByteArray("Bearer ") + token_);
        request.setRawHeader("User-Agent", "MachomeHub-WebullClient/1");
        request.setAttribute(
            QNetworkRequest::RedirectPolicyAttribute,
            QNetworkRequest::ManualRedirectPolicy
        );
        websocketHelloReceived_ = false;
        socket_->open(request);
        websocketConnectTimer_->start(config_.websocketConnectTimeoutMs);
    }

    void restartWebSocket() {
        if (!socket_ || !running_)
            return;
        websocketReconnectTimer_->stop();
        websocketConnectTimer_->stop();
        websocketSilenceTimer_->stop();
        // Credential rotation must not let the old socket's disconnected
        // callback schedule a timer that later aborts the replacement socket.
        // Retire the old QObject with all worker callbacks detached, then open
        // a fresh instance carrying only the new Authorization header.
        QWebSocket *retired = socket_;
        socket_ = nullptr;
        retired->disconnect(this);
        retired->abort();
        retired->deleteLater();
        createWebSocket();
        websocketFailures_ = 0;
        websocketHelloReceived_ = false;
        status_.streamConnected = false;
        status_.pollingFallback = true;
        publishStatus();
        openWebSocket();
    }

    void scheduleWebSocketReconnect() {
        if (!running_ || token_.isEmpty() || websocketReconnectTimer_->isActive())
            return;
        const int exponent = std::min(websocketFailures_, 20);
        qint64 raw = config_.reconnectInitialMs;
        for (int index = 0; index < exponent
             && raw < config_.reconnectMaximumMs; ++index) {
            raw = std::min<qint64>(raw * 2, config_.reconnectMaximumMs);
        }
        ++websocketFailures_;
        const int jitterRange = static_cast<int>(std::max<qint64>(1, raw / 10));
        const int jitter = QRandomGenerator::global()->bounded(jitterRange * 2 + 1)
            - jitterRange;
        const int delay = static_cast<int>(
            std::clamp<qint64>(raw + jitter, 250, config_.reconnectMaximumMs)
        );
        websocketReconnectTimer_->start(delay);
        status_.pollingFallback = true;
        publishStatus();
    }

    void failWebSocketProtocol(const QString &message) {
        status_.streamConnected = false;
        status_.pollingFallback = running_;
        reportProtocolError(QStringLiteral("/v2/stream"), message);
        if (socket_ && socket_->state() != QAbstractSocket::UnconnectedState)
            socket_->abort();
    }

    void handleWebSocketText(const QString &message) {
        const QByteArray bytes = message.toUtf8();
        if (bytes.size() > config_.maximumWebSocketMessageBytes) {
            reportError(
                QStringLiteral("WEBSOCKET_MESSAGE_TOO_LARGE"),
                QStringLiteral("/v2/stream"),
                QStringLiteral("WebSocket 消息超过 %1 字节安全上限")
                    .arg(config_.maximumWebSocketMessageBytes),
                0, 0, false
            );
            socket_->abort();
            return;
        }

        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(bytes, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            failWebSocketProtocol(
                QStringLiteral("WebSocket JSON 无效：%1").arg(parseError.errorString())
            );
            return;
        }

        const QJsonObject root = document.object();
        const QString type = root.value(QStringLiteral("type")).toString();
        if (type == QStringLiteral("hello")) {
            if (root.value(QStringLiteral("schema_version")).toInt(-1) != 2
                || root.value(QStringLiteral("service")).toString()
                    != QStringLiteral("webull-lv2-gateway")
                || root.value(QStringLiteral("symbol")).toString().trimmed().toUpper()
                    != config_.symbol
                || root.value(QStringLiteral("snapshot_semantics")).toString()
                    != QStringLiteral("full_replace")
                || root.value(QStringLiteral("aggregation")).toString()
                    != QStringLiteral("price")) {
                failWebSocketProtocol(
                    QStringLiteral("WebSocket hello 与 v2/full_replace 合同不匹配")
                );
                return;
            }
            websocketHelloReceived_ = true;
            websocketConnectTimer_->stop();
            websocketSilenceTimer_->start(config_.websocketSilenceTimeoutMs);
            websocketFailures_ = 0;
            status_.streamConnected = true;
            status_.pollingFallback = false;
            publishStatus();
            emitLog(
                LogSeverity::Info,
                QStringLiteral("WEBSOCKET_READY"),
                QStringLiteral("WebSocket v2 hello 已验证")
            );
            return;
        }
        if (!websocketHelloReceived_) {
            failWebSocketProtocol(
                QStringLiteral("WebSocket 在 hello 前发送了业务消息")
            );
            return;
        }
        if (type == QStringLiteral("depth_snapshot")) {
            const QJsonValue data = root.value(QStringLiteral("data"));
            if (!data.isObject()) {
                failWebSocketProtocol(
                    QStringLiteral("depth_snapshot 缺少 data 对象")
                );
                return;
            }
            if (!handleBookObject(data.toObject(), QStringLiteral("websocket"))) {
                status_.streamConnected = false;
                status_.pollingFallback = running_;
                publishStatus();
                socket_->abort();
            } else {
                websocketSilenceTimer_->start(config_.websocketSilenceTimeoutMs);
            }
            return;
        }
        if (type == QStringLiteral("heartbeat")) {
            if (root.value(QStringLiteral("schema_version")).toInt(-1) != 2) {
                failWebSocketProtocol(
                    QStringLiteral("heartbeat schema_version 不是 2")
                );
                return;
            }
            // A gateway heartbeat proves transport liveness only. It must not
            // refresh capturedAt or extend the market-data freshness window.
            const QString dataState = root.value(QStringLiteral("data_state")).toString();
            if (!dataState.isEmpty())
                status_.dataState = boundedText(dataState, 64);
            websocketSilenceTimer_->start(config_.websocketSilenceTimeoutMs);
            updateFreshness();
            publishStatus();
            return;
        }
        if (type == QStringLiteral("pong")) {
            websocketSilenceTimer_->start(config_.websocketSilenceTimeoutMs);
            return;
        }

        emitLogRateLimited(
            QStringLiteral("unknown-websocket-message"),
            LogSeverity::Debug,
            QStringLiteral("WEBSOCKET_UNKNOWN_MESSAGE"),
            QStringLiteral("已忽略前向兼容的未知 WebSocket 消息类型"),
            60'000
        );
    }

    void updateFreshness(bool publishBookOnBoundary = true) {
        qint64 age = -1;
        bool fresh = false;
        if (hasBook_) {
            const qint64 wallAge = std::max<qint64>(
                0,
                latestBook_.capturedAt.msecsTo(QDateTime::currentDateTimeUtc())
            );
            const qint64 elapsedSinceAcceptance = bookAcceptedMonotonicMs_ >= 0
                ? std::max<qint64>(0, monotonic_.elapsed() - bookAcceptedMonotonicMs_)
                : 0;
            const qint64 monotonicAge = bookInitialAgeMs_ >= 0
                && elapsedSinceAcceptance
                    <= std::numeric_limits<qint64>::max() - bookInitialAgeMs_
                ? bookInitialAgeMs_ + elapsedSinceAcceptance
                : std::numeric_limits<qint64>::max();
            age = std::max(wallAge, monotonicAge);
            const bool serverAllowsFresh = hasServerStatus_
                && status_.dataState == QStringLiteral("flowing");
            fresh = running_ && hasSymbolsMetadata_ && serverAllowsFresh
                && age <= status_.staleAfterMs;
            latestBook_.ageMs = age;
            latestBook_.fresh = fresh;
        }
        status_.dataAgeMs = age;
        status_.dataFresh = fresh;
        if (!freshnessEmitted_ || fresh != lastFresh_) {
            freshnessEmitted_ = true;
            lastFresh_ = fresh;
            postToOwner([fresh, age](WebullClient *owner) {
                emit owner->freshnessChanged(fresh, age);
            });
            if (publishBookOnBoundary && hasBook_) {
                const BookSnapshot updated = latestBook_;
                postToOwner([updated](WebullClient *owner) {
                    emit owner->bookUpdated(updated);
                });
            }
            if (!fresh && hasBook_) {
                emitLog(
                    LogSeverity::Warning,
                    QStringLiteral("BOOK_STALE"),
                    QStringLiteral("最新盘口已超过新鲜度门限；保留展示但不得用于实时决策")
                );
            }
        }
    }

    void publishStatus() {
        status_.observedAt = QDateTime::currentDateTimeUtc();
        status_.pollingFallback = running_ && !status_.streamConnected;
        const GatewayStatus snapshot = status_;
        postToOwner([snapshot](WebullClient *owner) {
            emit owner->statusUpdated(snapshot);
        });

        if (!transportEmitted_ || status_.apiLive != lastApiLive_
            || status_.streamConnected != lastStreamConnected_
            || status_.pollingFallback != lastPollingFallback_) {
            transportEmitted_ = true;
            lastApiLive_ = status_.apiLive;
            lastStreamConnected_ = status_.streamConnected;
            lastPollingFallback_ = status_.pollingFallback;
            const bool apiLive = status_.apiLive;
            const bool stream = status_.streamConnected;
            const bool fallback = status_.pollingFallback;
            postToOwner([apiLive, stream, fallback](WebullClient *owner) {
                emit owner->transportStateChanged(apiLive, stream, fallback);
            });
        }
    }

    void emitLog(LogSeverity severity, const QString &code, const QString &message) {
        const LogEntry entry{
            QDateTime::currentDateTimeUtc(),
            severity,
            boundedText(code, 128),
            boundedText(message, 2'048),
        };
        postToOwner([entry](WebullClient *owner) {
            emit owner->logEntry(entry);
        });
    }

    void emitLogRateLimited(
        const QString &key,
        LogSeverity severity,
        const QString &code,
        const QString &message,
        qint64 intervalMs
    ) {
        const qint64 now = monotonic_.elapsed();
        const qint64 last = logLastEmittedMs_.value(key, -intervalMs);
        if (now - last < intervalMs)
            return;
        logLastEmittedMs_.insert(key, now);
        emitLog(severity, code, message);
    }

    void reportProtocolError(const QString &endpoint, const QString &message) {
        reportError(
            QStringLiteral("PROTOCOL_ERROR"), endpoint, message,
            0, 0, false
        );
    }

    void reportError(
        const QString &code,
        const QString &endpoint,
        const QString &message,
        int httpStatus,
        int networkError,
        bool retryable
    ) {
        const QString safeCode = boundedText(code, 128);
        const QString safeEndpoint = boundedText(endpoint, 256);
        const QString safeMessage = boundedText(message, 2'048);
        lastErrorCode_ = safeCode;
        status_.lastError = safeMessage;
        publishStatus();

        const QString signature = safeCode + QLatin1Char('|') + safeEndpoint
            + QLatin1Char('|') + QString::number(httpStatus)
            + QLatin1Char('|') + QString::number(networkError);
        const qint64 now = monotonic_.elapsed();
        const qint64 last = errorLastEmittedMs_.value(signature, -kErrorRepeatIntervalMs);
        if (now - last < kErrorRepeatIntervalMs)
            return;
        errorLastEmittedMs_.insert(signature, now);

        const ApiError error{
            QDateTime::currentDateTimeUtc(),
            safeCode,
            safeEndpoint,
            safeMessage,
            httpStatus,
            networkError,
            retryable,
        };
        postToOwner([error](WebullClient *owner) {
            emit owner->errorOccurred(error);
        });
        emitLog(
            retryable ? LogSeverity::Warning : LogSeverity::Error,
            safeCode,
            safeMessage
        );
    }

    void noteRestFailure() {
        const qint64 now = monotonic_.elapsed();
        if (lastRestFailureBumpMs_ >= 0
            && now - lastRestFailureBumpMs_ < config_.pollIntervalMs / 2) {
            return;
        }
        lastRestFailureBumpMs_ = now;
        restFailures_ = std::min(restFailures_ + 1, 20);
        qint64 interval = config_.pollIntervalMs;
        for (int index = 0; index < restFailures_
             && interval < config_.restBackoffMaximumMs; ++index) {
            interval = std::min<qint64>(interval * 2, config_.restBackoffMaximumMs);
        }
        if (pollTimer_)
            pollTimer_->setInterval(static_cast<int>(interval));
    }

    void noteRestSuccess() {
        restFailures_ = 0;
        lastRestFailureBumpMs_ = -1;
        if (pollTimer_ && pollTimer_->interval() != config_.pollIntervalMs)
            pollTimer_->setInterval(config_.pollIntervalMs);
    }

    WebullClientConfig config_;
    QPointer<WebullClient> owner_;
    QNetworkAccessManager *network_ = nullptr;
    QWebSocket *socket_ = nullptr;
    QTimer *pollTimer_ = nullptr;
    QTimer *metadataTimer_ = nullptr;
    QTimer *freshnessTimer_ = nullptr;
    QTimer *websocketReconnectTimer_ = nullptr;
    QTimer *websocketConnectTimer_ = nullptr;
    QTimer *websocketSilenceTimer_ = nullptr;
    QHash<QNetworkReply *, PendingRequest> pending_;
    QSet<int> inFlight_;
    QHash<QNetworkReply *, PendingControl> pendingControls_;
    QSet<QString> pendingControlIds_;
    QByteArray token_;
    quint64 credentialGeneration_ = 0;
#ifdef Q_OS_UNIX
    FileIdentity credentialFileIdentity_;
#else
    qint64 credentialFileModifiedMs_ = -1;
#endif
    bool running_ = false;
    bool websocketHelloReceived_ = false;
    int websocketFailures_ = 0;
    int restFailures_ = 0;
    qint64 lastRestFailureBumpMs_ = -1;

    GatewayStatus status_;
    BookSnapshot latestBook_;
    bool hasBook_ = false;
    QSet<QString> retiredSessions_;
    QStringList retiredSessionOrder_;
    qint64 bookAcceptedMonotonicMs_ = -1;
    qint64 bookInitialAgeMs_ = -1;
    bool hasServerStatus_ = false;
    bool hasSymbolsMetadata_ = false;
    bool freshnessEmitted_ = false;
    bool lastFresh_ = false;
    QString lastErrorCode_;
    QString lastServerError_;

    bool transportEmitted_ = false;
    bool lastApiLive_ = false;
    bool lastStreamConnected_ = false;
    bool lastPollingFallback_ = false;
    QElapsedTimer monotonic_;
    QHash<QString, qint64> errorLastEmittedMs_;
    QHash<QString, qint64> logLastEmittedMs_;
};

WebullClient::WebullClient(WebullClientConfig config, QObject *parent)
    : QObject(parent), config_(std::move(config)) {
    qRegisterMetaType<LogSeverity>();
    qRegisterMetaType<LogEntry>();
    qRegisterMetaType<ApiError>();
    qRegisterMetaType<PriceLevel>();
    qRegisterMetaType<InsideMarket>();
    qRegisterMetaType<BookSnapshot>();
    qRegisterMetaType<ClientInfo>();
    qRegisterMetaType<ClientList>();
    qRegisterMetaType<SymbolInfo>();
    qRegisterMetaType<SymbolList>();
    qRegisterMetaType<GatewayStatus>();
    qRegisterMetaType<WebullClientConfig>();

    thread_ = new QThread(this);
    thread_->setObjectName(QStringLiteral("webull-client-network"));
    worker_ = new WebullClientWorker(config_, this);
    worker_->moveToThread(thread_);
    connect(thread_, &QThread::finished, worker_, &QObject::deleteLater);
    thread_->start();

    wipeByteArray(&config_.bearerToken);
}

WebullClient::~WebullClient() {
    if (worker_ && thread_ && thread_->isRunning()) {
        if (QThread::currentThread() == thread_) {
            worker_->shutdown();
            thread_->quit();
        } else {
            QMetaObject::invokeMethod(
                worker_,
                [worker = worker_, thread = thread_] {
                    worker->shutdown();
                    thread->quit();
                },
                Qt::QueuedConnection
            );
            if (!thread_->wait(10000)) {
                thread_->requestInterruption();
                thread_->quit();
                if (!thread_->wait(5000)) {
                    qCritical().noquote()
                        << "Webull 网络线程拒绝协作退出；"
                           "拒绝强制终止，交由进程退出回收";
                    // The thread was parented to this facade. Detach it so
                    // QObject destruction cannot delete a running QThread.
                    // The worker holds QPointer<WebullClient>, so it safely
                    // observes this facade disappearing.
                    thread_->setParent(nullptr);
                    thread_ = nullptr;
                }
            }
        }
    }
    worker_ = nullptr;
    wipeByteArray(&config_.bearerToken);
}

QString WebullClient::symbol() const {
    return config_.symbol.trimmed().toUpper();
}

QUrl WebullClient::apiBaseUrl() const {
    return config_.apiBaseUrl;
}

void WebullClient::start() {
    if (!worker_)
        return;
    QMetaObject::invokeMethod(
        worker_,
        [worker = worker_] { worker->startClient(); },
        Qt::QueuedConnection
    );
}

void WebullClient::stop() {
    if (!worker_)
        return;
    QMetaObject::invokeMethod(
        worker_,
        [worker = worker_] { worker->stopClient(); },
        Qt::QueuedConnection
    );
}

void WebullClient::refreshNow() {
    if (!worker_)
        return;
    QMetaObject::invokeMethod(
        worker_,
        [worker = worker_] { worker->refreshNow(); },
        Qt::QueuedConnection
    );
}

void WebullClient::reloadCredentials() {
    if (!worker_)
        return;
    QMetaObject::invokeMethod(
        worker_,
        [worker = worker_] { worker->reloadCredentials(); },
        Qt::QueuedConnection
    );
}

void WebullClient::setBearerToken(const QByteArray &token) {
    if (!worker_)
        return;
    QMetaObject::invokeMethod(
        worker_,
        [worker = worker_, token] { worker->setBearerToken(token); },
        Qt::QueuedConnection
    );
}

void WebullClient::setTokenFile(const QString &path) {
    if (!worker_)
        return;
    QMetaObject::invokeMethod(
        worker_,
        [worker = worker_, path] { worker->setTokenFile(path); },
        Qt::QueuedConnection
    );
}

void WebullClient::submitControl(
    const QString &requestId,
    const QString &action,
    const QJsonObject &arguments
) {
    if (!worker_)
        return;
    QMetaObject::invokeMethod(
        worker_,
        [worker = worker_, requestId, action, arguments] {
            worker->submitControl(requestId, action, arguments);
        },
        Qt::QueuedConnection
    );
}
