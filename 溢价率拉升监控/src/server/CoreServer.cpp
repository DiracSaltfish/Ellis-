#include "server/CoreServer.h"

#include "common/PersistenceWriter.h"
#include "server/LegacyL1Server.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLocalSocket>
#include <QNetworkRequest>
#include <QRegularExpression>
#include <QSaveFile>
#include <QStorageInfo>
#include <QWebSocket>

#include <algorithm>

namespace premium {
namespace {

QByteArray compact(const QJsonObject &object) { return QJsonDocument(object).toJson(QJsonDocument::Compact); }

QString absoluteFrom(const QString &root, const QString &path)
{
    return QFileInfo(path).isAbsolute() ? path : QDir(root).absoluteFilePath(path);
}

bool isDomesticSymbol(const QString &symbol)
{
    static const QRegularExpression pattern(QStringLiteral("^[0-9]{6}\\.(SH|SZ)$"));
    return pattern.match(symbol).hasMatch();
}

bool isExactHktSymbol(const QString &symbol)
{
    static const QRegularExpression pattern(QStringLiteral("^[0-9]{5}\\.HK$"));
    return pattern.match(symbol).hasMatch();
}

QString normalizedHotSymbol(const QString &raw)
{
    const QString upper = raw.trimmed().toUpper();
    if (isExactHktSymbol(upper)) return upper;
    return normalizeSymbol(upper);
}

} // namespace

QuoteWorker::QuoteWorker(QObject *parent) : QObject(parent) {}

void QuoteWorker::process(const BridgeFrame &frame, const QDateTime &now, bool allow30, bool allow300,
                          bool replay, bool signalEligible, quint64 publicationGeneration)
{
    ParseResult parsed = parser_.consume(frame, replay);
    if (!parsed.snapshot) {
        Q_EMIT rejected(parsed.symbol, parsed.issues, parsed.waitingForFull);
        return;
    }
    if (!signalEligible) {
        Q_EMIT resultReady(*parsed.snapshot, {}, false, 0, 0, publicationGeneration);
        return;
    }
    SignalDecision decision = signalEngine_.evaluate(std::move(*parsed.snapshot), now, allow30, allow300);
    QJsonObject signal;
    if (decision.event) signal = decision.event->toJson(false);
    Q_EMIT resultReady(decision.snapshot, signal, decision.event.has_value(), decision.rise30sPpm,
                       decision.rise300sPpm, publicationGeneration);
}

void QuoteWorker::reset(const QString &session)
{
    parser_.resetSession(session);
    signalEngine_.resetAll();
}

void QuoteWorker::resetSignals()
{
    signalEngine_.resetAll();
}

void QuoteWorker::resetSymbol(const QString &symbol)
{
    parser_.resetSymbol(symbol);
    signalEngine_.resetSymbol(symbol);
}

void QuoteWorker::resetSignalSymbol(const QString &symbol)
{
    signalEngine_.resetSymbol(symbol);
}

CoreServer::CoreServer(QString configPath, bool simulationOverride, bool replayOverride,
                       bool forceQuotesOverride, QObject *parent)
    : QObject(parent), configPath_(std::move(configPath)), simulation_(simulationOverride),
      replay_(replayOverride), forceQuotes_(forceQuotesOverride),
      monitorServer_(QStringLiteral("ETF Premium Monitor v2"), QWebSocketServer::NonSecureMode, this)
{
    qRegisterMetaType<BridgeFrame>();
    qRegisterMetaType<QuoteSnapshot>();
}

CoreServer::~CoreServer()
{
    scheduleTimer_.stop();
    statusTimer_.stop();
    if (adapterSocket_) {
        disconnect(adapterSocket_, nullptr, this, nullptr);
        adapterSocket_->abort();
        adapterSocket_.clear();
    }
    adapterServer_.close();
    const auto summarySockets = summaryClients_.values();
    for (QWebSocket *socket : summarySockets) {
        disconnect(socket, nullptr, this, nullptr);
        socket->close();
    }
    const auto detailSockets = detailClients_.keys();
    for (QWebSocket *socket : detailSockets) {
        disconnect(socket, nullptr, this, nullptr);
        socket->close();
    }
    summaryClients_.clear();
    detailClients_.clear();
    monitorServer_.close();
    for (QThread *thread : workerThreads_) thread->quit();
    for (QThread *thread : workerThreads_) thread->wait(3000);
    persistenceThread_.quit();
    persistenceThread_.wait(3000);
    if (adapterServer_.isListening()) QLocalServer::removeServer(adapterServer_.serverName());
}

bool CoreServer::loadConfiguration(QString *error)
{
    QFile file(configPath_);
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("cannot open config %1: %2").arg(configPath_, file.errorString());
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (!document.isObject()) {
        if (error) *error = QStringLiteral("invalid config: %1").arg(parseError.errorString());
        return false;
    }
    config_ = document.object();
    rootDirectory_ = QFileInfo(configPath_).absoluteDir().absolutePath() + QStringLiteral("/..");
    rootDirectory_ = QDir(rootDirectory_).canonicalPath();
    if (!simulation_) simulation_ = config_.value(QStringLiteral("mode")).toString() == QStringLiteral("simulation");
    replay_ = replay_ || config_.value(QStringLiteral("mode")).toString() == QStringLiteral("replay");
    hktEnabled_ = config_.value(QStringLiteral("enable_hkt_l1")).toBool(true);
    return true;
}

bool CoreServer::loadWatchlist(QString *error)
{
    const QString path = absoluteFrom(rootDirectory_, config_.value(QStringLiteral("watchlist")).toString(QStringLiteral("config/watchlist.json")));
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("cannot open watchlist %1: %2").arg(path, file.errorString());
        return false;
    }
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    QJsonArray entries;
    if (document.isArray()) entries = document.array();
    else entries = document.object().value(QStringLiteral("symbols")).toArray();
    static const QRegularExpression validPattern(QStringLiteral("^[0-9]{6}\\.(SH|SZ)$"));
    for (const QJsonValue &value : entries) {
        QString symbol;
        QString name;
        if (value.isString()) symbol = normalizeSymbol(value.toString());
        else {
            symbol = normalizeSymbol(value.toObject().value(QStringLiteral("symbol")).toString());
            name = value.toObject().value(QStringLiteral("name")).toString();
        }
        if (!validPattern.match(symbol).hasMatch()) {
            if (error) *error = QStringLiteral("invalid watchlist symbol: %1").arg(symbol);
            return false;
        }
        if (!fixedSymbols_.contains(symbol)) {
            fixedSymbols_.append(symbol);
            names_.insert(symbol, name);
        }
    }
    const int maximum = config_.value(QStringLiteral("max_upstream_symbols")).toInt(1000);
    if (fixedSymbols_.isEmpty() || fixedSymbols_.size() > maximum) {
        if (error) *error = QStringLiteral("watchlist must contain 1..%1 unique symbols, got %2")
                                .arg(maximum).arg(fixedSymbols_.size());
        return false;
    }
    const QString namesPath = absoluteFrom(rootDirectory_, config_.value(QStringLiteral("security_names")).toString(QStringLiteral("config/security_names.tsv")));
    QFile namesFile(namesPath);
    if (!namesFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
        if (error) *error = QStringLiteral("cannot open security names %1: %2").arg(namesPath, namesFile.errorString());
        return false;
    }
    while (!namesFile.atEnd()) {
        const QString line = QString::fromUtf8(namesFile.readLine()).trimmed();
        const int tab = line.indexOf(u'\t');
        if (tab <= 0) continue;
        names_.insert(normalizeSymbol(line.left(tab)), line.mid(tab + 1));
    }
    for (const QString &symbol : fixedSymbols_) {
        if (names_.value(symbol).isEmpty()) names_.insert(symbol, symbol.left(6));
    }
    return true;
}

bool CoreServer::loadHotlist(QString *error)
{
    const QString path = absoluteFrom(rootDirectory_, config_.value(QStringLiteral("l1_hotlist"))
                                                        .toString(QStringLiteral("config/l1_hotlist.json")));
    QFile file(path);
    if (!file.exists()) return true;
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("cannot open L1 hot list %1: %2").arg(path, file.errorString());
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || (!document.isArray() && !document.isObject())) {
        if (error) *error = QStringLiteral("invalid L1 hot list %1: %2").arg(path, parseError.errorString());
        return false;
    }
    const QJsonArray entries = document.isArray() ? document.array()
                                                  : document.object().value(QStringLiteral("symbols")).toArray();
    for (const QJsonValue &value : entries) {
        const QString raw = value.isString() ? value.toString()
                                             : value.toObject().value(QStringLiteral("symbol")).toString();
        const QString symbol = normalizedHotSymbol(raw);
        if (!isDomesticSymbol(symbol) && !isExactHktSymbol(symbol)) {
            if (error) *error = QStringLiteral("invalid L1 hot-list symbol: %1").arg(raw);
            return false;
        }
        if (isExactHktSymbol(symbol) && !hktEnabled_) {
            if (error) *error = QStringLiteral("HKT symbol %1 requires enable_hkt_l1=true").arg(symbol);
            return false;
        }
        if (hotSymbols_.contains(symbol)) {
            if (error) *error = QStringLiteral("duplicate L1 hot-list symbol: %1").arg(symbol);
            return false;
        }
        hotSymbols_.append(symbol);
    }
    QSet<QString> unique(fixedSymbols_.begin(), fixedSymbols_.end());
    unique.unite(QSet<QString>(hotSymbols_.begin(), hotSymbols_.end()));
    const int maximum = config_.value(QStringLiteral("max_upstream_symbols")).toInt(1000);
    if (unique.size() > maximum) {
        if (error) *error = QStringLiteral("watchlist plus L1 hot list has %1 unique symbols; limit is %2")
                                .arg(unique.size()).arg(maximum);
        return false;
    }
    for (const QString &symbol : hotSymbols_) {
        if (names_.value(symbol).isEmpty()) names_.insert(symbol, symbol.left(symbol.indexOf(u'.')));
    }
    return true;
}

bool CoreServer::start(QString *error)
{
    if (!loadConfiguration(error) || !loadWatchlist(error) || !loadHotlist(error)) return false;

    const QString logDir = absoluteFrom(rootDirectory_, config_.value(QStringLiteral("log_dir")).toString(QStringLiteral("logs")));
    const QString dataDir = absoluteFrom(rootDirectory_, config_.value(QStringLiteral("data_dir")).toString(QStringLiteral("data")));
    dataDirectory_ = dataDir;
    QDir().mkpath(logDir);
    QDir().mkpath(dataDir);
    QDir logDirectory(logDir);
    const QDate logCutoff = QDate::currentDate().addDays(-30);
    for (const QFileInfo &entry : logDirectory.entryInfoList(QDir::Files | QDir::NoDotAndDotDot)) {
        if (entry.fileName() != QStringLiteral(".gitkeep") && entry.lastModified().date() < logCutoff) {
            QFile::remove(entry.absoluteFilePath());
        }
    }
    operationsLog_.setFileName(logDirectory.filePath(QStringLiteral("a-core-%1.jsonl").arg(QDate::currentDate().toString(QStringLiteral("yyyyMMdd")))));
    if (!operationsLog_.open(QIODevice::WriteOnly | QIODevice::Append)) {
        qWarning().noquote() << QStringLiteral("cannot open operations log: %1").arg(operationsLog_.errorString());
    }

    persistence_ = new PersistenceWriter(dataDir);
    persistence_->moveToThread(&persistenceThread_);
    connect(this, &CoreServer::persistRaw, persistence_, &PersistenceWriter::appendRaw, Qt::QueuedConnection);
    connect(this, &CoreServer::persistNormalized, persistence_, &PersistenceWriter::appendNormalized, Qt::QueuedConnection);
    connect(this, &CoreServer::persistSignal, persistence_, &PersistenceWriter::appendSignal, Qt::QueuedConnection);
    connect(persistence_, &PersistenceWriter::writeCompleted, this, [this] { persistencePending_.fetch_sub(1); }, Qt::QueuedConnection);
    connect(persistence_, &PersistenceWriter::writeError, this, [this](const QString &message) {
        writeOperational(QStringLiteral("CRITICAL"), QStringLiteral("persistence"), message);
    }, Qt::QueuedConnection);
    connect(persistence_, &PersistenceWriter::storageStateChanged, this, [this](bool enabled, qint64 available) {
        if (!enabled) historicalWritesStopped_ = true;
        writeOperational(enabled ? QStringLiteral("INFO") : QStringLiteral("CRITICAL"), QStringLiteral("storage"),
                         enabled ? QStringLiteral("storage writing restored") : QStringLiteral("historical writing stopped; realtime continues"),
                         {{"available_bytes", available}});
    }, Qt::QueuedConnection);
    connect(&persistenceThread_, &QThread::finished, persistence_, &QObject::deleteLater);
    persistenceThread_.start();
    QMetaObject::invokeMethod(persistence_, &PersistenceWriter::prune, Qt::QueuedConnection);

    for (int index = 0; index < WorkerCount; ++index) {
        auto *thread = new QThread(this);
        auto *worker = new QuoteWorker;
        worker->moveToThread(thread);
        connect(thread, &QThread::finished, worker, &QObject::deleteLater);
        connect(worker, &QuoteWorker::resultReady, this, &CoreServer::publishSnapshot, Qt::QueuedConnection);
        connect(worker, &QuoteWorker::rejected, this, [this](const QString &symbol, const QStringList &issues, bool waiting) {
            ++rejectedFrameCount_;
            writeOperational(waiting ? QStringLiteral("WARN") : QStringLiteral("ERROR"), QStringLiteral("parser"),
                             QStringLiteral("market frame quarantined"),
                             {{"symbol", symbol}, {"waiting_for_full", waiting}, {"issues", QJsonArray::fromStringList(issues)}});
        });
        workerThreads_.append(thread);
        workers_.append(worker);
        thread->start();
    }

    const QString socketPath = absoluteFrom(rootDirectory_, config_.value(QStringLiteral("adapter_socket")).toString(QStringLiteral("runtime/tgw.sock")));
    QDir().mkpath(QFileInfo(socketPath).absolutePath());
    QLocalServer::removeServer(socketPath);
    connect(&adapterServer_, &QLocalServer::newConnection, this, &CoreServer::acceptAdapter);
    if (!adapterServer_.listen(socketPath)) {
        if (error) *error = QStringLiteral("cannot listen adapter socket: %1").arg(adapterServer_.errorString());
        return false;
    }

    const QHostAddress listenAddress(config_.value(QStringLiteral("listen_host")).toString(QStringLiteral("0.0.0.0")));
    const quint16 monitorPort = static_cast<quint16>(config_.value(QStringLiteral("monitor_port")).toInt(8421));
    if (!monitorServer_.listen(listenAddress, monitorPort)) {
        if (error) *error = QStringLiteral("cannot listen 8421: %1").arg(monitorServer_.errorString());
        return false;
    }
    connect(&monitorServer_, &QWebSocketServer::newConnection, this, &CoreServer::acceptWebSocket);

    LegacyL1Server::Limits limits;
    limits.maxClients = config_.value(QStringLiteral("max_l1_clients")).toInt(256);
    limits.maxSymbolsPerClient = config_.value(QStringLiteral("max_l1_symbols_per_client")).toInt(256);
    limits.maxMaintainedSymbols = config_.value(QStringLiteral("max_upstream_symbols")).toInt(1000);
    limits.unsubscribeGraceSeconds = config_.value(QStringLiteral("dynamic_unsubscribe_grace_sec")).toInt(60);
    legacy_ = new LegacyL1Server(fixedSymbols_, hotSymbols_, limits, this);
    connect(legacy_, &LegacyL1Server::desiredSymbolsChanged, this, &CoreServer::sendAdapterControl);
    connect(legacy_, &LegacyL1Server::operationalEvent, this, [this](const QString &message) {
        writeOperational(QStringLiteral("INFO"), QStringLiteral("legacy"), message);
    });
    if (!legacy_->listen(listenAddress, static_cast<quint16>(config_.value(QStringLiteral("legacy_l1_port")).toInt(19195)), error)) return false;

    scheduleTimer_.setInterval(1000);
    connect(&scheduleTimer_, &QTimer::timeout, this, &CoreServer::updateSchedule);
    scheduleTimer_.start();
    statusTimer_.setInterval(5000);
    connect(&statusTimer_, &QTimer::timeout, this, [this] { broadcastSummary(statusObject()); });
    statusTimer_.start();
    updateSchedule();
    writeOperational(QStringLiteral("INFO"), QStringLiteral("core"), QStringLiteral("started"),
                     {{"fixed_symbols", fixedSymbols_.size()}, {"l1_hot_symbols", hotSymbols_.size()},
                      {"hkt_enabled", hktEnabled_}, {"simulation", simulation_}, {"replay", replay_}});
    return true;
}

void CoreServer::acceptAdapter()
{
    QLocalSocket *incoming = adapterServer_.nextPendingConnection();
    if (adapterSocket_) adapterSocket_->disconnectFromServer();
    ++publicationGeneration_;
    adapterSocket_ = incoming;
    adapterBuffer_.clear();
    adapterSession_.clear();
    lastAdapterSequence_ = 0;
    lastAdapterSymbols_.clear();
    lastAdapterQuotesDesired_ = false;
    cache_.clear();
    latestRawRecord_ = {};
    for (QuoteWorker *worker : workers_) {
        QMetaObject::invokeMethod(worker, [worker] { worker->reset(); }, Qt::QueuedConnection);
    }
    if (legacy_) legacy_->setMarketOnline(false);
    connect(incoming, &QLocalSocket::readyRead, this, &CoreServer::readAdapter);
    connect(incoming, &QLocalSocket::disconnected, this, [this, incoming] {
        incoming->deleteLater();
        if (adapterSocket_ != incoming) return;
        writeOperational(QStringLiteral("WARN"), QStringLiteral("adapter"), QStringLiteral("disconnected"));
        ++publicationGeneration_;
        adapterSocket_.clear();
        lastAdapterSymbols_.clear();
        lastAdapterQuotesDesired_ = false;
        lastAdapterSequence_ = 0;
        adapterSession_.clear();
        cache_.clear();
        latestRawRecord_ = {};
        for (QuoteWorker *worker : workers_) QMetaObject::invokeMethod(worker, [worker] { worker->reset(); }, Qt::QueuedConnection);
        legacy_->setMarketOnline(false);
        broadcastSummary(statusObject());
    });
    writeOperational(QStringLiteral("INFO"), QStringLiteral("adapter"), QStringLiteral("connected"));
    sendAdapterControl(legacy_->maintainedSymbols());
}

void CoreServer::readAdapter()
{
    if (!adapterSocket_) return;
    adapterBuffer_ += adapterSocket_->readAll();
    const QList<QByteArray> payloads = takeLengthPrefixedFrames(adapterBuffer_);
    for (const QByteArray &payload : payloads) {
        BridgeFrame frame;
        QString error;
        if (!BridgeFrame::decodeProtobuf(payload, &frame, &error)) {
            ++rejectedFrameCount_;
            writeOperational(QStringLiteral("ERROR"), QStringLiteral("bridge"), error);
            continue;
        }
        handleFrame(frame);
    }
}

void CoreServer::handleFrame(const BridgeFrame &frame)
{
    if (lastAdapterSequence_ && frame.sequence != lastAdapterSequence_ + 1) {
        ++adapterGapCount_;
        ++publicationGeneration_;
        cache_.clear();
        latestRawRecord_ = {};
        const QString session = frame.sessionId;
        for (QuoteWorker *worker : workers_) {
            QMetaObject::invokeMethod(worker, [worker, session] { worker->reset(session); }, Qt::QueuedConnection);
        }
        if (legacy_) legacy_->setMarketOnline(false);
        writeOperational(QStringLiteral("CRITICAL"), QStringLiteral("bridge"),
                         QStringLiteral("adapter sequence gap; all shards invalidated until new full"),
                         {{"expected_seq", static_cast<qint64>(lastAdapterSequence_ + 1)},
                          {"actual_seq", static_cast<qint64>(frame.sequence)},
                          {"session", frame.sessionId}});
    }
    lastAdapterSequence_ = frame.sequence;
    lastSdkQueueDepth_ = frame.sdkQueueDepth;
    if (!adapterSession_.isEmpty() && frame.sessionId != adapterSession_) {
        ++publicationGeneration_;
        cache_.clear();
        latestRawRecord_ = {};
        for (QuoteWorker *worker : workers_) {
            const QString session = frame.sessionId;
            QMetaObject::invokeMethod(worker, [worker, session] { worker->reset(session); }, Qt::QueuedConnection);
        }
        if (legacy_) legacy_->setMarketOnline(false);
    }
    adapterSession_ = frame.sessionId;
    if (frame.kind == BridgeFrame::Kind::MarketEvent) {
        const QString routedSymbol = symbolHint(frame);
        const bool retainMarketEvent = shouldPersistMarketEvent(routedSymbol);
        QJsonObject rawRecord{{"adapter_seq", static_cast<qint64>(frame.sequence)}, {"session", frame.sessionId},
                              {"routed_symbol", routedSymbol},
                              {"receive_wall_ns", frame.receiveWallNs}, {"receive_monotonic_ns", frame.receiveMonotonicNs},
                              {"full", !frame.isDelta}, {"delta", frame.isDelta}, {"tag", frame.tag},
                              {"sdk_queue_depth", static_cast<int>(frame.sdkQueueDepth)},
                              {"core_observed_adapter_gaps", static_cast<qint64>(adapterGapCount_)},
                              {"event", QJsonDocument::fromJson(frame.payloadJson).object()}};
        if (retainMarketEvent) latestRawRecord_ = rawRecord;
        if (retainMarketEvent && !historicalWritesStopped_ && persistencePending_.load() < 10'000) {
            const int pending = persistencePending_.fetch_add(1) + 1;
            persistencePeak_.store(std::max(persistencePeak_.load(), pending));
            Q_EMIT persistRaw(compact(rawRecord), QDate::currentDate());
        } else if (retainMarketEvent && !historicalWritesStopped_) {
            historicalWritesStopped_ = true;
            writeOperational(QStringLiteral("CRITICAL"), QStringLiteral("persistence"),
                             QStringLiteral("persistence queue reached 10000; historical writes stopped, realtime continues"));
        }
        routeMarketFrame(frame);
    } else {
        writeOperational(QStringLiteral("INFO"), QStringLiteral("adapter"), frame.message,
                         {{"sequence", static_cast<qint64>(frame.sequence)}, {"queue_depth", static_cast<int>(frame.sdkQueueDepth)}});
    }
}

QString CoreServer::symbolHint(const BridgeFrame &frame) const
{
    const QJsonObject envelope = QJsonDocument::fromJson(frame.payloadJson).object();
    const QJsonObject data = envelope.value(QStringLiteral("data")).toObject();
    QString code = data.value(QStringLiteral("security_code")).toString();
    if (code.isEmpty()) code = data.value(QStringLiteral("2")).toString();
    if (code.isEmpty() && data.value(QStringLiteral("2")).isDouble()) {
        code = QString::number(data.value(QStringLiteral("2")).toInteger());
    }
    if (code.isEmpty()) code = envelope.value(QStringLiteral("symbol")).toString();
    if (frame.tag == QStringLiteral("16")) {
        const QString upper = code.trimmed().toUpper();
        if (isExactHktSymbol(upper)) return upper;
        if (upper.size() == 5) {
            for (const QChar character : upper) if (!character.isDigit()) return {};
            return upper + QStringLiteral(".HK");
        }
        return {};
    }
    return normalizeSymbol(code);
}

bool CoreServer::shouldPersistMarketEvent(const QString &symbol) const
{
    if (fixedSymbols_.contains(symbol)) return true;
    if (hotSymbols_.contains(symbol)) return false;
    return config_.value(QStringLiteral("capture_dynamic_market_data")).toBool(false);
}

void CoreServer::routeMarketFrame(const BridgeFrame &frame)
{
    const QString symbol = symbolHint(frame);
    if (symbol.isEmpty()) {
        ++rejectedFrameCount_;
        writeOperational(QStringLiteral("ERROR"), QStringLiteral("router"), QStringLiteral("symbol unavailable"));
        return;
    }
    const uint hash = qHash(symbol);
    const int workerIndex = static_cast<int>(hash % WorkerCount);
    QuoteWorker *worker = workers_.at(workerIndex);
    const int pending = workerPending_[workerIndex].fetch_add(1) + 1;
    workerPeak_[workerIndex].store(std::max(workerPeak_[workerIndex].load(), pending));
    if (pending > 4096) {
        workerPending_[workerIndex].fetch_sub(1);
        const quint64 drops = workerDropCount_.fetch_add(1) + 1;
        ++rejectedFrameCount_;
        ++publicationGeneration_;
        QMetaObject::invokeMethod(worker, [worker] { worker->reset(); }, Qt::QueuedConnection);
        if (drops == 1 || drops % 100 == 0) {
            writeOperational(QStringLiteral("CRITICAL"), QStringLiteral("compute_queue"),
                             QStringLiteral("worker queue overflow; shard invalidated until new full"),
                             {{"worker", workerIndex}, {"symbol", symbol}, {"limit", 4096},
                              {"dropped", static_cast<qint64>(drops)}});
        }
        return;
    }
    const QDateTime now = QDateTime::currentDateTime();
    const bool allow30 = (simulation_ || replay_) ? true : scheduleState_.allow30SecondSignal;
    const bool allow300 = (simulation_ || replay_) ? true : scheduleState_.allow300SecondSignal;
    const bool signalEligible = fixedSymbols_.contains(symbol) && !symbol.endsWith(QStringLiteral(".HK"));
    const quint64 publicationGeneration = publicationGeneration_;
    QMetaObject::invokeMethod(worker, [this, worker, workerIndex, frame, now, allow30, allow300,
                                       signalEligible, publicationGeneration, replay = replay_] {
        worker->process(frame, now, allow30, allow300, replay, signalEligible, publicationGeneration);
        workerPending_[workerIndex].fetch_sub(1);
    }, Qt::QueuedConnection);
}

void CoreServer::acceptWebSocket()
{
    while (monitorServer_.hasPendingConnections()) {
        QWebSocket *socket = monitorServer_.nextPendingConnection();
        const int limit = config_.value(QStringLiteral("max_monitor_clients")).toInt(256);
        const QString path = socket->requestUrl().path();
        if (path == QStringLiteral("/ws/v2/summary")) {
            if (summaryClients_.size() >= limit) {
                socket->close(QWebSocketProtocol::CloseCodePolicyViolated, QStringLiteral("summary client limit"));
                socket->deleteLater();
                continue;
            }
            summaryClients_.insert(socket);
            connect(socket, &QWebSocket::textMessageReceived, this, [this, socket](const QString &message) { handleSummaryMessage(socket, message); });
            sendSummarySync(socket);
        } else if (path == QStringLiteral("/ws/v2/detail")) {
            if (detailClients_.size() >= limit) {
                socket->close(QWebSocketProtocol::CloseCodePolicyViolated, QStringLiteral("detail client limit"));
                socket->deleteLater();
                continue;
            }
            detailClients_.insert(socket, {});
            connect(socket, &QWebSocket::textMessageReceived, this, [this, socket](const QString &message) { handleDetailMessage(socket, message); });
            sendJson(socket, {{"type", "hello"}, {"channel", "detail"}, {"max_symbols", config_.value("max_detail_symbols_per_client").toInt(4)}});
        } else {
            socket->close(QWebSocketProtocol::CloseCodePolicyViolated, QStringLiteral("unknown path"));
        }
        connect(socket, &QWebSocket::disconnected, this, [this, socket] {
            summaryClients_.remove(socket);
            detailClients_.remove(socket);
            socket->deleteLater();
        });
    }
}

void CoreServer::handleSummaryMessage(QWebSocket *socket, const QString &message)
{
    const QJsonObject request = QJsonDocument::fromJson(message.toUtf8()).object();
    if (request.value(QStringLiteral("op")).toString() == QStringLiteral("sync")) sendSummarySync(socket);
    else if (request.value(QStringLiteral("op")).toString() == QStringLiteral("status")) sendJson(socket, statusObject());
    else if (request.value(QStringLiteral("op")).toString() == QStringLiteral("raw_snapshot")) {
        const QString routedSymbol = latestRawRecord_.value(QStringLiteral("routed_symbol")).toString();
        const bool available = !routedSymbol.isEmpty() && shouldPersistMarketEvent(routedSymbol);
        QJsonObject response = available ? latestRawRecord_ : QJsonObject{};
        response.insert(QStringLiteral("type"), QStringLiteral("raw_snapshot"));
        response.insert(QStringLiteral("available"), available);
        sendJson(socket, response);
    } else if (request.value(QStringLiteral("op")).toString() == QStringLiteral("set_watchlist")) {
        replaceWatchlist(socket, request.value(QStringLiteral("symbols")).toArray());
    } else if (request.value(QStringLiteral("op")).toString() == QStringLiteral("set_l1_hotlist")) {
        replaceHotlist(socket, request.value(QStringLiteral("symbols")).toArray());
    }
}

bool CoreServer::persistSymbolList(const QString &configKey, const QString &fallbackPath,
                                   const QStringList &symbols, QString *error) const
{
    const QString path = absoluteFrom(rootDirectory_, config_.value(configKey).toString(fallbackPath));
    QDir().mkpath(QFileInfo(path).absolutePath());
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        if (error) *error = QStringLiteral("cannot write %1: %2").arg(path, file.errorString());
        return false;
    }
    const QJsonObject payload{{"version", 1}, {"symbols", QJsonArray::fromStringList(symbols)}};
    if (file.write(QJsonDocument(payload).toJson(QJsonDocument::Indented)) < 0 || !file.commit()) {
        if (error) *error = QStringLiteral("cannot atomically save %1: %2").arg(path, file.errorString());
        return false;
    }
    return true;
}

void CoreServer::replaceWatchlist(QWebSocket *socket, const QJsonArray &symbols)
{
    QJsonObject response{{"type", "watchlist_ack"}, {"accepted", false}};
    auto reject = [this, socket, &response](const QString &message) {
        response.insert(QStringLiteral("error"), message);
        response.insert(QStringLiteral("symbols"), QJsonArray::fromStringList(fixedSymbols_));
        sendJson(socket, response);
    };
    if (!socket || !socket->peerAddress().isLoopback()) {
        reject(QStringLiteral("set_watchlist is restricted to the local A-console"));
        return;
    }
    const int maximum = config_.value(QStringLiteral("max_upstream_symbols")).toInt(1000);
    if (symbols.isEmpty() || symbols.size() > maximum) {
        reject(QStringLiteral("watchlist size must be 1..%1").arg(maximum));
        return;
    }
    QStringList replacement;
    for (const QJsonValue &value : symbols) {
        const QString normalized = normalizeSymbol(value.toString());
        if (!isDomesticSymbol(normalized)) {
            reject(QStringLiteral("invalid monitored symbol: %1").arg(value.toString()));
            return;
        }
        if (!replacement.contains(normalized)) replacement.append(normalized);
    }
    if (replacement.size() != symbols.size()) {
        reject(QStringLiteral("duplicate symbols are not allowed"));
        return;
    }
    QSet<QString> unique(replacement.begin(), replacement.end());
    unique.unite(QSet<QString>(hotSymbols_.begin(), hotSymbols_.end()));
    if (unique.size() > maximum) {
        reject(QStringLiteral("monitor and L1 hot lists would maintain %1 unique symbols; limit is %2")
                   .arg(unique.size()).arg(maximum));
        return;
    }

    QString legacyError;
    if (!legacy_ || !legacy_->replacePinnedSymbols(replacement, hotSymbols_, &legacyError)) {
        reject(legacyError.isEmpty() ? QStringLiteral("L1 gateway unavailable") : legacyError);
        return;
    }
    QString persistenceError;
    if (!persistSymbolList(QStringLiteral("watchlist"), QStringLiteral("config/watchlist.json"),
                           replacement, &persistenceError)) {
        legacy_->replacePinnedSymbols(fixedSymbols_, hotSymbols_);
        reject(persistenceError);
        return;
    }

    const QSet<QString> oldSet(fixedSymbols_.begin(), fixedSymbols_.end());
    const QSet<QString> newSet(replacement.begin(), replacement.end());
    const QSet<QString> removed = oldSet - newSet;
    if (oldSet != newSet) ++publicationGeneration_;
    fixedSymbols_ = replacement;
    const QString latestRawSymbol = latestRawRecord_.value(QStringLiteral("routed_symbol")).toString();
    if (!latestRawSymbol.isEmpty() && !shouldPersistMarketEvent(latestRawSymbol)) latestRawRecord_ = {};
    for (const QString &symbol : fixedSymbols_) {
        if (names_.value(symbol).isEmpty()) names_.insert(symbol, symbol.left(6));
    }
    for (const QString &symbol : removed) {
        for (QuoteWorker *worker : workers_) {
            QMetaObject::invokeMethod(worker, [worker, symbol] { worker->resetSignalSymbol(symbol); }, Qt::QueuedConnection);
        }
        signalHistory_.erase(std::remove_if(signalHistory_.begin(), signalHistory_.end(), [&symbol](const QJsonObject &event) {
            return event.value(QStringLiteral("symbol")).toString() == symbol;
        }), signalHistory_.end());
        QJsonObject removedEvent{{"type", "symbol_removed"}, {"symbol", symbol}};
        broadcastSummary(removedEvent);
    }
    const QSet<QString> added = newSet - oldSet;
    for (const QString &symbol : added) {
        for (QuoteWorker *worker : workers_) {
            QMetaObject::invokeMethod(worker, [worker, symbol] { worker->resetSignalSymbol(symbol); }, Qt::QueuedConnection);
        }
    }
    response.insert(QStringLiteral("accepted"), true);
    response.insert(QStringLiteral("count"), fixedSymbols_.size());
    response.insert(QStringLiteral("symbols"), QJsonArray::fromStringList(fixedSymbols_));
    sendJson(socket, response);
    writeOperational(QStringLiteral("INFO"), QStringLiteral("watchlist"), QStringLiteral("runtime watchlist replaced"),
                     {{"count", fixedSymbols_.size()}, {"removed", removed.size()}});
}

void CoreServer::replaceHotlist(QWebSocket *socket, const QJsonArray &symbols)
{
    QJsonObject response{{"type", "l1_hotlist_ack"}, {"accepted", false}};
    auto reject = [this, socket, &response](const QString &message) {
        response.insert(QStringLiteral("error"), message);
        response.insert(QStringLiteral("symbols"), QJsonArray::fromStringList(hotSymbols_));
        sendJson(socket, response);
    };
    if (!socket || !socket->peerAddress().isLoopback()) {
        reject(QStringLiteral("set_l1_hotlist is restricted to the local A-console"));
        return;
    }
    const int maximum = config_.value(QStringLiteral("max_upstream_symbols")).toInt(1000);
    if (symbols.size() > maximum) {
        reject(QStringLiteral("L1 hot-list size must be 0..%1").arg(maximum));
        return;
    }
    QStringList replacement;
    for (const QJsonValue &value : symbols) {
        const QString normalized = normalizedHotSymbol(value.toString());
        if (!isDomesticSymbol(normalized) && !isExactHktSymbol(normalized)) {
            reject(QStringLiteral("invalid L1 hot-list symbol: %1").arg(value.toString()));
            return;
        }
        if (isExactHktSymbol(normalized) && !hktEnabled_) {
            reject(QStringLiteral("HKT is disabled by enable_hkt_l1=false"));
            return;
        }
        if (!replacement.contains(normalized)) replacement.append(normalized);
    }
    if (replacement.size() != symbols.size()) {
        reject(QStringLiteral("duplicate symbols are not allowed"));
        return;
    }
    QSet<QString> unique(fixedSymbols_.begin(), fixedSymbols_.end());
    unique.unite(QSet<QString>(replacement.begin(), replacement.end()));
    if (unique.size() > maximum) {
        reject(QStringLiteral("monitor and L1 hot lists would maintain %1 unique symbols; limit is %2")
                   .arg(unique.size()).arg(maximum));
        return;
    }

    QString legacyError;
    if (!legacy_ || !legacy_->replacePinnedSymbols(fixedSymbols_, replacement, &legacyError)) {
        reject(legacyError.isEmpty() ? QStringLiteral("L1 gateway unavailable") : legacyError);
        return;
    }
    QString persistenceError;
    if (!persistSymbolList(QStringLiteral("l1_hotlist"), QStringLiteral("config/l1_hotlist.json"),
                           replacement, &persistenceError)) {
        legacy_->replacePinnedSymbols(fixedSymbols_, hotSymbols_);
        reject(persistenceError);
        return;
    }
    const QSet<QString> oldSet(hotSymbols_.begin(), hotSymbols_.end());
    hotSymbols_ = replacement;
    const QString latestRawSymbol = latestRawRecord_.value(QStringLiteral("routed_symbol")).toString();
    if (!latestRawSymbol.isEmpty() && !shouldPersistMarketEvent(latestRawSymbol)) latestRawRecord_ = {};
    for (const QString &symbol : hotSymbols_) {
        if (names_.value(symbol).isEmpty()) names_.insert(symbol, symbol.left(symbol.indexOf(u'.')));
    }
    response.insert(QStringLiteral("accepted"), true);
    response.insert(QStringLiteral("count"), hotSymbols_.size());
    response.insert(QStringLiteral("symbols"), QJsonArray::fromStringList(hotSymbols_));
    sendJson(socket, response);
    writeOperational(QStringLiteral("INFO"), QStringLiteral("l1_hotlist"),
                     QStringLiteral("runtime L1 hot list replaced"),
                     {{"count", hotSymbols_.size()},
                      {"added", (QSet<QString>(hotSymbols_.begin(), hotSymbols_.end()) - oldSet).size()},
                      {"removed", (oldSet - QSet<QString>(hotSymbols_.begin(), hotSymbols_.end())).size()}});
}

void CoreServer::handleDetailMessage(QWebSocket *socket, const QString &message)
{
    const QJsonObject request = QJsonDocument::fromJson(message.toUtf8()).object();
    const QString op = request.value(QStringLiteral("op")).toString();
    const QString symbol = normalizeSymbol(request.value(QStringLiteral("symbol")).toString());
    auto it = detailClients_.find(socket);
    if (it == detailClients_.end()) return;
    if (op == QStringLiteral("subscribe")) {
        const bool valid = symbol.size() == 9 && symbol.at(6) == u'.'
                        && (symbol.endsWith(QStringLiteral(".SH")) || symbol.endsWith(QStringLiteral(".SZ")));
        if (!valid) {
            sendJson(socket, {{"type", "error"}, {"code", "invalid_symbol"}, {"symbol", symbol}});
            return;
        }
        const int maximum = config_.value(QStringLiteral("max_detail_symbols_per_client")).toInt(4);
        if (!it->symbols.contains(symbol) && it->symbols.size() >= maximum) {
            sendJson(socket, {{"type", "error"}, {"code", "detail_limit"}, {"symbol", symbol}});
            return;
        }
        it->symbols.insert(symbol);
        sendJson(socket, {{"type", "detail_ack"}, {"op", "subscribe"}, {"symbol", symbol}});
        if (cache_.contains(symbol)) {
            QJsonObject object = cache_.value(symbol).toDetailJson();
            object.insert(QStringLiteral("type"), QStringLiteral("detail"));
            object.insert(QStringLiteral("cached"), true);
            sendJson(socket, object);
        }
    } else if (op == QStringLiteral("unsubscribe")) {
        it->symbols.remove(symbol);
        sendJson(socket, {{"type", "detail_ack"}, {"op", "unsubscribe"}, {"symbol", symbol}});
    }
}

void CoreServer::publishSnapshot(const QuoteSnapshot &incoming, const QJsonObject &signal, bool hasSignal,
                                 qint64 rise30sPpm, qint64 rise300sPpm, quint64 publicationGeneration)
{
    if (publicationGeneration != publicationGeneration_) return;
    QuoteSnapshot snapshot = incoming;
    snapshot.name = names_.value(snapshot.symbol);
    snapshot.publishWallNs = QDateTime::currentMSecsSinceEpoch() * 1'000'000LL;
    QJsonObject auditSignal = signal;
    if (hasSignal) {
        auditSignal.insert(QStringLiteral("name"), snapshot.name);
        auditSignal.insert(QStringLiteral("last_price_e6"), snapshot.lastPriceE6);
        auditSignal.insert(QStringLiteral("bid1_price_e6"), snapshot.bidPricesE6[0]);
        auditSignal.insert(QStringLiteral("iopv_e6"), snapshot.iopvE6);
        auditSignal.insert(QStringLiteral("orig_time"), snapshot.origTime);
        auditSignal.insert(QStringLiteral("source_session"), snapshot.sourceSession);
        auditSignal.insert(QStringLiteral("receive_wall_ns"), snapshot.receiveWallNs);
        auditSignal.insert(QStringLiteral("publish_wall_ns"), snapshot.publishWallNs);
        auditSignal.insert(QStringLiteral("iopv_static"), snapshot.iopvStatic);
        auditSignal.insert(QStringLiteral("mapping_verified"), snapshot.numericMappingVerified);
        auditSignal.insert(QStringLiteral("mapping_version"), snapshot.mappingVersion);
        auditSignal.insert(QStringLiteral("quality"), QJsonArray::fromStringList(snapshot.qualityIssues));
    }
    lastCoreLatencyNs_ = snapshot.receiveWallNs > 0 ? std::max<qint64>(0, snapshot.publishWallNs - snapshot.receiveWallNs) : 0;
    maxCoreLatencyNs_ = std::max(maxCoreLatencyNs_, lastCoreLatencyNs_);
    cache_.insert(snapshot.symbol, snapshot);
    legacy_->setMarketOnline(true);
    legacy_->publish(snapshot);

    const bool monitored = fixedSymbols_.contains(snapshot.symbol);
    if (monitored) {
        QJsonObject summary = snapshot.toSummaryJson();
        summary.insert(QStringLiteral("type"), QStringLiteral("summary"));
        summary.insert(QStringLiteral("rise_30s_ppm"), rise30sPpm);
        summary.insert(QStringLiteral("rise_300s_ppm"), rise300sPpm);
        broadcastSummary(summary);
    }
    const bool retainMarketEvent = shouldPersistMarketEvent(snapshot.symbol);
    if (retainMarketEvent && !historicalWritesStopped_ && persistencePending_.load() < 10'000) {
        const int pending = persistencePending_.fetch_add(1) + 1;
        persistencePeak_.store(std::max(persistencePeak_.load(), pending));
        Q_EMIT persistNormalized(compact(snapshot.toDetailJson()), QDate::currentDate());
    } else if (retainMarketEvent && !historicalWritesStopped_) {
        historicalWritesStopped_ = true;
        writeOperational(QStringLiteral("CRITICAL"), QStringLiteral("persistence"),
                         QStringLiteral("persistence queue reached 10000; historical writes stopped, realtime continues"));
    }

    for (auto it = detailClients_.begin(); monitored && it != detailClients_.end(); ++it) {
        if (!it->symbols.contains(snapshot.symbol)) continue;
        QJsonObject detail = snapshot.toDetailJson();
        detail.insert(QStringLiteral("type"), QStringLiteral("detail"));
        detail.insert(QStringLiteral("cached"), false);
        sendJson(it.key(), detail);
    }

    if (monitored && hasSignal) {
        signalHistory_.append(auditSignal);
        const QDateTime cutoff = QDateTime::currentDateTime().addSecs(-1800);
        while (!signalHistory_.isEmpty()
               && QDateTime::fromString(signalHistory_.front().value(QStringLiteral("occurred_at")).toString(), Qt::ISODateWithMs) < cutoff) {
            signalHistory_.removeFirst();
        }
        broadcastSummary(auditSignal);
        if (!historicalWritesStopped_ && persistencePending_.load() < 10'000) {
            const int pending = persistencePending_.fetch_add(1) + 1;
            persistencePeak_.store(std::max(persistencePeak_.load(), pending));
            Q_EMIT persistSignal(compact(auditSignal));
        } else if (!historicalWritesStopped_) {
            historicalWritesStopped_ = true;
            writeOperational(QStringLiteral("CRITICAL"), QStringLiteral("persistence"),
                             QStringLiteral("persistence queue reached 10000; signal history write dropped, realtime continues"));
        }
    }
}

void CoreServer::sendSummarySync(QWebSocket *socket)
{
    sendJson(socket, {{"type", "sync_begin"}, {"server_time", QDateTime::currentDateTime().toString(Qt::ISODateWithMs)},
                      {"replay", replay_}, {"fixed_symbols", fixedSymbols_.size()}});
    for (const QuoteSnapshot &snapshot : cache_) {
        if (!fixedSymbols_.contains(snapshot.symbol)) continue;
        QJsonObject object = snapshot.toSummaryJson();
        object.insert(QStringLiteral("type"), QStringLiteral("summary"));
        object.insert(QStringLiteral("backfill"), true);
        sendJson(socket, object);
    }
    const QDateTime cutoff = QDateTime::currentDateTime().addSecs(-1800);
    for (QJsonObject signal : signalHistory_) {
        if (QDateTime::fromString(signal.value(QStringLiteral("occurred_at")).toString(), Qt::ISODateWithMs) < cutoff) continue;
        signal.insert(QStringLiteral("backfill"), true);
        sendJson(socket, signal);
    }
    sendJson(socket, {{"type", "sync_complete"}, {"server_time", QDateTime::currentDateTime().toString(Qt::ISODateWithMs)}});
}

void CoreServer::sendJson(QWebSocket *socket, const QJsonObject &object)
{
    if (!socket || socket->state() != QAbstractSocket::ConnectedState) return;
    if (socket->bytesToWrite() > 1024 * 1024) {
        ++monitorSlowClientDrops_;
        socket->close(QWebSocketProtocol::CloseCodeGoingAway, QStringLiteral("slow client buffer exceeded"));
        return;
    }
    socket->sendTextMessage(QString::fromUtf8(compact(object)));
}

void CoreServer::broadcastSummary(const QJsonObject &object)
{
    const QString message = QString::fromUtf8(compact(object));
    for (QWebSocket *socket : summaryClients_) {
        if (socket->state() != QAbstractSocket::ConnectedState) continue;
        if (socket->bytesToWrite() > 1024 * 1024) {
            ++monitorSlowClientDrops_;
            socket->close(QWebSocketProtocol::CloseCodeGoingAway, QStringLiteral("slow client buffer exceeded"));
            continue;
        }
        socket->sendTextMessage(message);
    }
}

void CoreServer::sendAdapterControl(const QStringList &symbols)
{
    QSet<QString> active;
    for (const QString &symbol : symbols) {
        if (symbol.endsWith(QStringLiteral(".HK"))) {
            if (hktEnabled_ && scheduleState_.hkQuotesDesired) active.insert(symbol);
        } else if (scheduleState_.cnQuotesDesired) {
            active.insert(symbol);
        }
    }
    const bool quotesDesired = !active.isEmpty();
    if (active == lastAdapterSymbols_ && quotesDesired == lastAdapterQuotesDesired_) return;

    const QSet<QString> released = lastAdapterSymbols_ - active;
    if (!released.isEmpty()) ++publicationGeneration_;
    if (released.contains(latestRawRecord_.value(QStringLiteral("routed_symbol")).toString())) {
        latestRawRecord_ = {};
    }
    for (const QString &symbol : released) {
        cache_.remove(symbol);
        for (QuoteWorker *worker : workers_) {
            QMetaObject::invokeMethod(worker, [worker, symbol] { worker->resetSymbol(symbol); }, Qt::QueuedConnection);
        }
    }
    if (!adapterSocket_ || adapterSocket_->state() != QLocalSocket::ConnectedState) return;
    QJsonArray array;
    QStringList ordered(active.begin(), active.end());
    std::sort(ordered.begin(), ordered.end());
    for (const QString &symbol : ordered) array.append(symbol);
    BridgeFrame control;
    control.kind = BridgeFrame::Kind::Control;
    control.message = QStringLiteral("set_symbols");
    control.payloadJson = compact({{"op", "set_symbols"}, {"symbols", array}, {"quotes_desired", quotesDesired}});
    adapterSocket_->write(control.encodeLengthPrefixed());
    lastAdapterSymbols_ = active;
    lastAdapterQuotesDesired_ = quotesDesired;
}

void CoreServer::updateSchedule()
{
    const ScheduleState next = schedule_.stateAt(QDateTime::currentDateTime(), simulation_ || replay_ || forceQuotes_);
    if (next.phase != scheduleState_.phase || next.quotesDesired != scheduleState_.quotesDesired
        || next.cnQuotesDesired != scheduleState_.cnQuotesDesired
        || next.hkQuotesDesired != scheduleState_.hkQuotesDesired) {
        if (next.phase == ScheduleState::Phase::MorningWarmup
            || next.phase == ScheduleState::Phase::AfternoonWarmup
            || next.phase == ScheduleState::Phase::Lunch
            || next.phase == ScheduleState::Phase::Offline) {
            for (QuoteWorker *worker : workers_) {
                QMetaObject::invokeMethod(worker, [worker] { worker->resetSignals(); }, Qt::QueuedConnection);
            }
        }
        scheduleState_ = next;
        sendAdapterControl(legacy_ ? legacy_->maintainedSymbols() : fixedSymbols_);
        writeOperational(QStringLiteral("INFO"), QStringLiteral("schedule"), next.label,
                         {{"quotes_desired", next.quotesDesired}, {"cn_quotes_desired", next.cnQuotesDesired},
                          {"hk_quotes_desired", next.hkQuotesDesired}, {"allow_30s", next.allow30SecondSignal},
                          {"allow_5m", next.allow300SecondSignal}});
    } else scheduleState_ = next;
}

QJsonObject CoreServer::statusObject() const
{
    QJsonArray workerDepths;
    QJsonArray workerPeaks;
    for (int i = 0; i < WorkerCount; ++i) {
        workerDepths.append(workerPending_[i].load());
        workerPeaks.append(workerPeak_[i].load());
    }
    const QStorageInfo storage(dataDirectory_);
    const qint64 diskAvailable = storage.isValid() && storage.isReady() ? storage.bytesAvailable() : -1;
    int readyMonitored = 0;
    for (const QString &symbol : fixedSymbols_) if (cache_.contains(symbol)) ++readyMonitored;
    int readyHot = 0;
    for (const QString &symbol : hotSymbols_) if (cache_.contains(symbol)) ++readyHot;
    QSet<QString> pinned(fixedSymbols_.begin(), fixedSymbols_.end());
    pinned.unite(QSet<QString>(hotSymbols_.begin(), hotSymbols_.end()));
    return {{"type", "status"}, {"server_time", QDateTime::currentDateTime().toString(Qt::ISODateWithMs)},
            {"adapter_connected", !adapterSocket_.isNull()}, {"adapter_session", adapterSession_},
            {"adapter_seq", static_cast<qint64>(lastAdapterSequence_)}, {"adapter_gaps", static_cast<qint64>(adapterGapCount_)},
            {"sdk_queue_depth", static_cast<int>(lastSdkQueueDepth_)}, {"worker_queue_depths", workerDepths},
            {"worker_queue_peaks", workerPeaks}, {"worker_drops", static_cast<qint64>(workerDropCount_.load())},
            {"persistence_queue_depth", persistencePending_.load()}, {"persistence_queue_peak", persistencePeak_.load()},
            {"historical_writes_stopped", historicalWritesStopped_},
            {"disk_available_bytes", diskAvailable},
            {"quarantined", static_cast<qint64>(rejectedFrameCount_)}, {"ready_symbols", readyMonitored},
            {"watchlist_symbols", fixedSymbols_.size()},
            {"l1_hot_symbols", hotSymbols_.size()}, {"l1_hot_ready", readyHot},
            {"unique_pinned_symbols", pinned.size()}, {"active_upstream_symbols", lastAdapterSymbols_.size()},
            {"cn_quotes_desired", scheduleState_.cnQuotesDesired},
            {"hk_quotes_desired", scheduleState_.hkQuotesDesired}, {"hkt_enabled", hktEnabled_},
            {"summary_clients", summaryClients_.size()}, {"detail_clients", detailClients_.size()},
            {"monitor_slow_client_drops", static_cast<qint64>(monitorSlowClientDrops_)},
            {"core_latency_ms", lastCoreLatencyNs_ / 1'000'000.0},
            {"core_latency_max_ms", maxCoreLatencyNs_ / 1'000'000.0},
            {"l1_clients", legacy_ ? legacy_->clientCount() : 0}, {"phase", scheduleState_.label},
            {"signals_enabled", simulation_ || replay_ || scheduleState_.allow30SecondSignal},
            {"force_quotes", forceQuotes_}, {"simulation", simulation_}, {"replay", replay_}};
}

void CoreServer::writeOperational(const QString &level, const QString &component, const QString &message,
                                  const QJsonObject &fields)
{
    QJsonObject object = fields;
    object.insert(QStringLiteral("time"), QDateTime::currentDateTime().toString(Qt::ISODateWithMs));
    object.insert(QStringLiteral("level"), level);
    object.insert(QStringLiteral("component"), component);
    object.insert(QStringLiteral("message"), message);
    const QByteArray line = compact(object) + '\n';
    if (operationsLog_.isOpen()) {
        operationsLog_.write(line);
        operationsLog_.flush();
    }
    qInfo().noquote() << QString::fromUtf8(line).trimmed();
}

} // namespace premium
