#include "modules/webull/WebullEngine.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QRandomGenerator>
#include <QRegularExpression>
#include <QSaveFile>
#include <QTcpServer>
#include <QTcpSocket>
#include <QUuid>
#include <QUrlQuery>

#include <algorithm>
#include <cmath>
#include <optional>

#ifdef Q_OS_UNIX
#include <sys/stat.h>
#endif

namespace machome::webull {
namespace {

constexpr qint64 kMaximumHelperLine = 2 * 1024 * 1024;

QString canonicalDecimal(const QString &raw, bool positive, QString *error)
{
    const QString value = raw.trimmed();
    if (value.isEmpty() || value.size() > 128 || value.startsWith(u'+')
        || value.contains(u'e', Qt::CaseInsensitive)) {
        if (error) *error = QStringLiteral("数值不是有限普通十进制：%1").arg(raw.left(64));
        return {};
    }
    qsizetype offset = value.startsWith(u'-') ? 1 : 0;
    bool dot = false;
    bool digit = false;
    for (qsizetype i = offset; i < value.size(); ++i) {
        const QChar c = value.at(i);
        if (c == u'.' && !dot) { dot = true; continue; }
        if (!c.isDigit()) {
            if (error) *error = QStringLiteral("数值包含非法字符");
            return {};
        }
        digit = true;
    }
    if (!digit || offset == value.size() || value.endsWith(u'.')) {
        if (error) *error = QStringLiteral("数值格式不完整");
        return {};
    }
    bool ok = false;
    const long double number = value.toDouble(&ok);
    if (!ok || !std::isfinite(static_cast<double>(number))
        || (positive ? number <= 0 : number < 0)) {
        if (error) *error = positive ? QStringLiteral("价格必须为正数")
                                     : QStringLiteral("数量不得为负数");
        return {};
    }
    QString result = value;
    bool negative = result.startsWith(u'-');
    if (negative) result.remove(0, 1);
    QString integer = result.section(u'.', 0, 0);
    QString fraction = result.contains(u'.') ? result.section(u'.', 1) : QString{};
    while (integer.size() > 1 && integer.startsWith(u'0')) integer.remove(0, 1);
    while (fraction.endsWith(u'0')) fraction.chop(1);
    result = integer + (fraction.isEmpty() ? QString{} : u'.' + fraction);
    if (negative && result != QStringLiteral("0")) result.prepend(u'-');
    return result;
}

QString decimalText(long double value)
{
    QString text = QString::number(static_cast<double>(value), 'f', 12);
    while (text.contains(u'.') && text.endsWith(u'0')) text.chop(1);
    if (text.endsWith(u'.')) text.chop(1);
    return text == QStringLiteral("-0") ? QStringLiteral("0") : text;
}

QString addUnsignedDecimals(const QString &left, const QString &right)
{
    const int leftScale = left.contains(u'.') ? left.size() - left.indexOf(u'.') - 1 : 0;
    const int rightScale = right.contains(u'.') ? right.size() - right.indexOf(u'.') - 1 : 0;
    const int scale = std::max(leftScale, rightScale);
    QString a = left; a.remove(u'.'); a.append(QString(scale - leftScale, u'0'));
    QString b = right; b.remove(u'.'); b.append(QString(scale - rightScale, u'0'));
    const int width = std::max(a.size(), b.size());
    a.prepend(QString(width - a.size(), u'0')); b.prepend(QString(width - b.size(), u'0'));
    QString sum(width, u'0'); int carry = 0;
    for (int i = width - 1; i >= 0; --i) {
        const int value = a.at(i).digitValue() + b.at(i).digitValue() + carry;
        sum[i] = QChar(u'0' + (value % 10)); carry = value / 10;
    }
    if (carry) sum.prepend(QChar(u'0' + carry));
    if (scale) sum.insert(sum.size() - scale, u'.');
    while (sum.size() > 1 && sum.startsWith(u'0') && !sum.startsWith(QStringLiteral("0."))) sum.remove(0, 1);
    while (sum.contains(u'.') && sum.endsWith(u'0')) sum.chop(1);
    if (sum.endsWith(u'.')) sum.chop(1);
    return sum;
}

int compareUnsignedDecimals(const QString &left, const QString &right)
{
    QString leftInteger = left.section(u'.', 0, 0), rightInteger = right.section(u'.', 0, 0);
    if (leftInteger.size() != rightInteger.size()) return leftInteger.size() < rightInteger.size() ? -1 : 1;
    const int integerComparison = QString::compare(leftInteger, rightInteger, Qt::CaseSensitive);
    if (integerComparison) return integerComparison < 0 ? -1 : 1;
    QString leftFraction = left.contains(u'.') ? left.section(u'.', 1) : QString{};
    QString rightFraction = right.contains(u'.') ? right.section(u'.', 1) : QString{};
    const int scale = std::max(leftFraction.size(), rightFraction.size());
    leftFraction.append(QString(scale - leftFraction.size(), u'0'));
    rightFraction.append(QString(scale - rightFraction.size(), u'0'));
    const int fractionComparison = QString::compare(leftFraction, rightFraction, Qt::CaseSensitive);
    return fractionComparison < 0 ? -1 : fractionComparison > 0 ? 1 : 0;
}

struct DecimalValue {
    QString text;
    long double number = 0;
};

bool normalizedLevels(const QJsonArray &input, bool descending, int maximum,
                      QJsonArray *output, QString *error)
{
    QHash<QString, QString> totals;
    for (const auto &value : input) {
        if (!value.isObject()) {
            if (error) *error = QStringLiteral("盘口档位必须是对象");
            return false;
        }
        const auto object = value.toObject();
        QString priceRaw = object.value(QStringLiteral("price")).toVariant().toString();
        QString volumeRaw = object.value(QStringLiteral("volume")).toVariant().toString();
        QString localError;
        const QString price = canonicalDecimal(priceRaw, true, &localError);
        const QString volume = canonicalDecimal(volumeRaw, false, &localError);
        if (price.isEmpty() || volume.isEmpty()) {
            if (error) *error = localError;
            return false;
        }
        totals[price] = addUnsignedDecimals(totals.value(price, QStringLiteral("0")), volume);
    }
    if (totals.isEmpty()) {
        if (error) *error = QStringLiteral("盘口一侧为空");
        return false;
    }
    QList<DecimalValue> levels;
    for (auto it = totals.cbegin(); it != totals.cend(); ++it)
        levels.append({it.key(), 0});
    std::sort(levels.begin(), levels.end(), [descending](const auto &a, const auto &b) {
        const int comparison = compareUnsignedDecimals(a.text, b.text);
        return descending ? comparison > 0 : comparison < 0;
    });
    *output = QJsonArray{};
    const int count = std::min(maximum, static_cast<int>(levels.size()));
    for (int i = 0; i < count; ++i) {
        const auto &level = levels.at(i);
        output->append(QJsonObject{{QStringLiteral("level"), i + 1},
                                   {QStringLiteral("price"), level.text},
                                   {QStringLiteral("volume"), totals.value(level.text)}});
    }
    return true;
}

bool readVarint(const QByteArray &data, qsizetype *offset, quint64 *value)
{
    quint64 result = 0;
    int shift = 0;
    for (int i = 0; i < 10 && *offset < data.size(); ++i) {
        const quint8 byte = static_cast<quint8>(data.at((*offset)++));
        result |= quint64(byte & 0x7f) << shift;
        if (!(byte & 0x80)) { *value = result; return true; }
        shift += 7;
    }
    return false;
}

struct ProtoField { int number = 0; int wire = 0; QByteArray bytes; quint64 varint = 0; };

bool protoFields(const QByteArray &data, QList<ProtoField> *fields)
{
    fields->clear();
    qsizetype offset = 0;
    while (offset < data.size()) {
        quint64 key = 0;
        if (!readVarint(data, &offset, &key) || (key >> 3) == 0) return false;
        ProtoField field;
        field.number = static_cast<int>(key >> 3);
        field.wire = static_cast<int>(key & 7);
        if (field.wire == 0) {
            if (!readVarint(data, &offset, &field.varint)) return false;
        } else if (field.wire == 1) {
            if (offset + 8 > data.size()) return false;
            field.bytes = data.mid(offset, 8); offset += 8;
        } else if (field.wire == 2) {
            quint64 size = 0;
            if (!readVarint(data, &offset, &size) || size > quint64(data.size() - offset)) return false;
            field.bytes = data.mid(offset, static_cast<qsizetype>(size)); offset += static_cast<qsizetype>(size);
        } else if (field.wire == 5) {
            if (offset + 4 > data.size()) return false;
            field.bytes = data.mid(offset, 4); offset += 4;
        } else return false;
        fields->append(field);
    }
    return true;
}

QJsonObject parseProtoLevel(const QByteArray &data)
{
    QList<ProtoField> fields;
    if (!protoFields(data, &fields)) return {};
    QHash<int, QByteArray> values;
    for (const auto &field : fields)
        if (field.wire == 2 && field.number >= 1 && field.number <= 3)
            values.insert(field.number, field.bytes);
    QString error;
    const QString price = canonicalDecimal(QString::fromUtf8(values.value(1)), true, &error);
    const QString volume = canonicalDecimal(QString::fromUtf8(values.value(2)), false, &error);
    if (price.isEmpty() || volume.isEmpty()) return {};
    return {{QStringLiteral("price"), price}, {QStringLiteral("volume"), volume}};
}

bool parseDepthContainer(const QByteArray &data, QJsonArray *bids, QJsonArray *asks)
{
    QList<ProtoField> fields;
    if (!protoFields(data, &fields)) return false;
    QJsonArray rawBids, rawAsks;
    for (const auto &field : fields) {
        if (field.wire != 2 || (field.number != 1 && field.number != 2)) continue;
        const auto level = parseProtoLevel(field.bytes);
        if (!level.isEmpty()) (field.number == 1 ? rawAsks : rawBids).append(level);
    }
    if (rawBids.isEmpty() || rawAsks.isEmpty()) return false;
    *bids = rawBids; *asks = rawAsks;
    return true;
}

QByteArray websocketFrame(const QByteArray &payload, quint8 opcode = 0x1)
{
    QByteArray frame;
    frame.append(char(0x80 | opcode));
    const quint64 size = static_cast<quint64>(payload.size());
    if (size < 126) frame.append(char(size));
    else if (size <= 0xffff) {
        frame.append(char(126)); frame.append(char((size >> 8) & 0xff)); frame.append(char(size & 0xff));
    } else {
        frame.append(char(127));
        for (int shift = 56; shift >= 0; shift -= 8) frame.append(char((size >> shift) & 0xff));
    }
    frame.append(payload);
    return frame;
}

QString newToken()
{
    QByteArray random(32, Qt::Uninitialized);
    for (char &byte : random) byte = char(QRandomGenerator::global()->bounded(256));
    return QString::fromLatin1(random.toBase64(QByteArray::Base64UrlEncoding | QByteArray::OmitTrailingEquals));
}

QString reasonPhrase(int status)
{
    switch (status) { case 200: return QStringLiteral("OK"); case 101: return QStringLiteral("Switching Protocols");
    case 401: return QStringLiteral("Unauthorized"); case 404: return QStringLiteral("Not Found");
    case 503: return QStringLiteral("Service Unavailable"); default: return QStringLiteral("Bad Request"); }
}

} // namespace

class WebullV2Server final : public QObject {
public:
    explicit WebullV2Server(WebullEngine *engine) : QObject(engine), engine_(engine)
    {
        heartbeat_.setInterval(25'000);
        ping_.setInterval(20'000);
        connect(&ping_, &QTimer::timeout, this, [this] {
            const auto now = QDateTime::currentMSecsSinceEpoch();
            for (auto *socket : clients_.keys()) {
                if (awaitingPong_.contains(socket) || socket->bytesToWrite() > 256 * 1024) {
                    socket->abort(); continue;
                }
                awaitingPong_.insert(socket, now);
                socket->write(websocketFrame(QByteArray::number(now), 0x9));
            }
        });
        connect(&heartbeat_, &QTimer::timeout, this, [this] {
            const auto latest = engine_->publicBook();
            const QJsonObject value{{"type","heartbeat"},{"schema_version",2},
                {"at",QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
                {"session_id",latest.value("session_id")},{"last_sequence",latest.value("sequence")},
                {"last_captured_at",latest.value("captured_at")},{"data_state",engine_->dataState_}};
            const auto frame=websocketFrame(QJsonDocument(value).toJson(QJsonDocument::Compact));
            for(auto *socket:clients_.keys()) {
                if(socket->bytesToWrite()==0 && !pending_.contains(socket)) {
                    auto heartbeat = value;
                    heartbeat.insert("schema_version", clients_.value(socket).value("schema_version"));
                    socket->write(websocketFrame(QJsonDocument(heartbeat).toJson(QJsonDocument::Compact)));
                }
            }
        });
        connect(&server_, &QTcpServer::newConnection, this, [this] {
            while (auto *socket = server_.nextPendingConnection()) {
                socket->setReadBufferSize(64 * 1024 + 1);
                buffers_.insert(socket, {});
                QTimer::singleShot(10'000, socket, [this, socket] {
                    if (!clients_.contains(socket)) socket->abort();
                });
                connect(socket, &QTcpSocket::bytesWritten, this, [this, socket](qint64) {
                    if (socket->bytesToWrite() == 0 && pending_.contains(socket))
                        sendBook(socket, pending_.take(socket));
                });
                connect(socket, &QTcpSocket::readyRead, this, [this, socket] { consume(socket); });
                connect(socket, &QTcpSocket::disconnected, this, [this, socket] {
                    clients_.remove(socket); buffers_.remove(socket); pending_.remove(socket); awaitingPong_.remove(socket); socket->deleteLater(); publishClients();
                });
            }
        });
    }

    bool start(quint16 port, QString *error)
    {
        if (server_.isListening()) return true;
        const auto settings = engine_->context_.settings;
        const QString host = settings.value("api_host").toString("127.0.0.1");
        networks_.clear();
        const auto cidrs = settings.value("api_allowed_cidrs").toArray(
            QJsonArray{QStringLiteral("127.0.0.0/8"), QStringLiteral("::1/128")});
        for (const auto &cidr : cidrs) {
            const auto network = QHostAddress::parseSubnet(cidr.toString());
            if (network.first.isNull() || network.second < 0) {
                if(error)*error=QStringLiteral("invalid api_allowed_cidrs"); return false;
            }
            networks_.append(network);
        }
        if (networks_.isEmpty() || QHostAddress(host).isNull()) {
            if(error)*error=QStringLiteral("api_host and api_allowed_cidrs are required"); return false;
        }
        if (!server_.listen(QHostAddress(host), port)) {
            if (error) *error = server_.errorString();
            return false;
        }
        heartbeat_.start(); ping_.start();
        return true;
    }
    void stop()
    {
        for (auto *socket : buffers_.keys()) socket->abort();
        heartbeat_.stop(); ping_.stop(); clients_.clear(); buffers_.clear(); pending_.clear(); awaitingPong_.clear(); server_.close(); publishClients();
    }
    bool listening() const { return server_.isListening(); }
    quint16 port() const { return server_.serverPort(); }
    int clientCount() const { return clients_.size(); }
    QJsonArray clientsJson() const
    {
        QJsonArray result;
        for (auto it = clients_.cbegin(); it != clients_.cend(); ++it) result.append(it.value());
        return result;
    }
    void broadcast(const QJsonObject &book)
    {
        for (auto *socket : clients_.keys()) {
            if (socket->bytesToWrite() > 0) {
                if (pending_.contains(socket)) {
                    auto &client = clients_[socket];
                    client.insert("messages_dropped", client.value("messages_dropped").toInteger() + 1);
                }
                pending_.insert(socket, book); // capacity one, replace older pending snapshot
            } else sendBook(socket, book);
        }
        publishClients();
    }

private:
    static QJsonObject legacyBook(const QJsonObject &book) {
        QJsonObject result{{"schema_version",1}};
        for (const auto *key : {"symbol","ticker_id","captured_at","sequence","changed","content_hash"})
            result.insert(QLatin1String(key), book.value(QLatin1String(key)));
        const auto depth = book.value("book").toObject();
        for (const auto *side : {"bids","asks"}) {
            QJsonArray levels;
            for (const auto &entry : depth.value(QLatin1String(side)).toArray()) {
                const auto row = entry.toObject();
                levels.append(QJsonObject{{"price",row.value("price")},{"volume",row.value("volume")},{"mic",""}});
            }
            result.insert(QLatin1String(side),levels);
        }
        return result;
    }
    void sendBook(QTcpSocket *socket, const QJsonObject &book) {
        if (!clients_.contains(socket)) return;
        auto &client = clients_[socket];
        const bool legacy = client.value("schema_version").toInt() == 1;
        const QJsonObject value{{"type",legacy?"depth":"depth_snapshot"},{"data",legacy?legacyBook(book):book}};
        socket->write(websocketFrame(QJsonDocument(value).toJson(QJsonDocument::Compact)));
        client.insert("last_sent_at",QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
        client.insert("messages_sent",client.value("messages_sent").toInteger()+1);
    }

    void respond(QTcpSocket *socket, int status, const QJsonValue &value,
                 const QList<QPair<QByteArray,QByteArray>> &headers = {})
    {
        const QByteArray body = QJsonDocument(value.isObject() ? QJsonDocument(value.toObject())
                                                               : QJsonDocument(value.toArray())).toJson(QJsonDocument::Compact);
        QByteArray response = "HTTP/1.1 " + QByteArray::number(status) + ' ' + reasonPhrase(status).toUtf8() + "\r\n";
        response += "Content-Type: application/json\r\nConnection: close\r\nContent-Length: "
            + QByteArray::number(body.size()) + "\r\n";
        if (socket->property("v1").toBool())
            response += "Deprecation: true\r\nLink: </v2/status>; rel=\"successor-version\"\r\n";
        for (const auto &header : headers) response += header.first + ": " + header.second + "\r\n";
        response += "\r\n" + body;
        socket->write(response); socket->disconnectFromHost();
    }
    void consume(QTcpSocket *socket)
    {
        auto &buffer = buffers_[socket]; buffer += socket->readAll();
        if (buffer.size() > 64 * 1024) { socket->disconnectFromHost(); return; }
        if (clients_.contains(socket)) {
            while (buffer.size() >= 2) {
                const quint8 first=quint8(buffer.at(0)), second=quint8(buffer.at(1));
                quint64 size=second&0x7f; qsizetype offset=2;
                if(size==126){if(buffer.size()<4)return;size=(quint8(buffer.at(2))<<8)|quint8(buffer.at(3));offset=4;}
                else if(size==127){if(buffer.size()<10)return;size=0;for(int i=2;i<10;++i)size=(size<<8)|quint8(buffer.at(i));offset=10;}
                const bool masked=second&0x80;
                if (!masked || !(first & 0x80) || (first & 0x70)) {socket->abort();return;}
                if(masked)offset+=4;
                if (buffer.size() < offset) return;
                if(size>64*1024){socket->disconnectFromHost();return;}
                if(quint64(buffer.size()-offset)<size)return;
                QByteArray payload=buffer.mid(offset,qsizetype(size));
                if(masked){const QByteArray mask=buffer.mid(offset-4,4);for(qsizetype i=0;i<payload.size();++i)payload[i]=char(quint8(payload.at(i))^quint8(mask.at(i%4)));}
                buffer.remove(0,offset+qsizetype(size));
                const quint8 opcode=first&0x0f;
                if(opcode==0x8){socket->disconnectFromHost();return;}
                if(opcode>=8 && size>125){socket->abort();return;}
                if(opcode==0x9)socket->write(websocketFrame(payload,0xA));
                else if(opcode==0xA)awaitingPong_.remove(socket);
                else if(opcode==1 && payload=="ping" && socket->bytesToWrite()<256*1024)
                    socket->write(websocketFrame(QJsonDocument(QJsonObject{{"type","pong"},
                        {"at",QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)}}).toJson(QJsonDocument::Compact)));
            }
            return;
        }
        const qsizetype end = buffer.indexOf("\r\n\r\n");
        if (end < 0) return;
        const QList<QByteArray> lines = buffer.left(end).split('\n');
        const QList<QByteArray> first = lines.value(0).trimmed().split(' ');
        if (first.size() < 2 || first.at(0) != "GET") { respond(socket, 400, QJsonObject{{"error","bad_request"}}); return; }
        const QString target = QString::fromUtf8(first.at(1));
        QString path = target.section(u'?', 0, 0);
        const bool legacy = path.startsWith(QStringLiteral("/v1/"));
        socket->setProperty("v1",legacy);
        if (legacy) path.replace(0,4,QStringLiteral("/v2/"));
        bool allowed = false;
        for (const auto &network : networks_) allowed |= socket->peerAddress().isInSubnet(network);
        if (!allowed) {respond(socket,403,QJsonObject{{"error","client network is not allowed"}});return;}
        const QUrl requestUrl(QStringLiteral("http://127.0.0.1") + target);
        QHash<QByteArray,QByteArray> headers;
        for (qsizetype i = 1; i < lines.size(); ++i) {
            const QByteArray line = lines.at(i).trimmed(); const qsizetype colon = line.indexOf(':');
            if (colon > 0) headers.insert(line.left(colon).trimmed().toLower(), line.mid(colon + 1).trimmed());
        }
        const bool live = path == QStringLiteral("/v2/health/live") || path == QStringLiteral("/v1/health/live");
        const QByteArray authorization = headers.value("authorization");
        const QByteArray candidate = authorization.left(7).toLower()=="bearer " ? authorization.mid(7).trimmed() : QByteArray{};
        unsigned int difference = unsigned(candidate.size() ^ engine_->apiToken_.size());
        for (qsizetype i=0;i<engine_->apiToken_.size();++i)
            difference |= quint8(engine_->apiToken_.at(i)) ^ quint8(i<candidate.size()?candidate.at(i):0);
        if (!live && difference != 0) {
            respond(socket, 401, QJsonObject{{"error","Bearer token required"}}, {{"WWW-Authenticate","Bearer"}}); return;
        }
        const bool upgrade = headers.value("upgrade").toLower() == "websocket";
        if (path == QStringLiteral("/v2/stream") && upgrade) {
            const QString requestedSymbol = QUrlQuery(requestUrl).queryItemValue(QStringLiteral("symbol")).trimmed().toUpper();
            if (!requestedSymbol.isEmpty() && requestedSymbol != engine_->symbol_) {
                respond(socket, 404, QJsonObject{{"error","symbol_not_configured"}}); return;
            }
            const QByteArray key = headers.value("sec-websocket-key");
            if (key.isEmpty()) { respond(socket, 400, QJsonObject{{"error","missing_websocket_key"}}); return; }
            const QByteArray accept = QCryptographicHash::hash(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
                                                               QCryptographicHash::Sha1).toBase64();
            socket->write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n");
            buffer.clear();
            const QString id = QUuid::createUuid().toString(QUuid::WithoutBraces).left(12);
            clients_.insert(socket, {{QStringLiteral("client_id"), id},
                                     {QStringLiteral("remote"), socket->peerAddress().toString()},
                                     {QStringLiteral("connected_at"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
                                     {QStringLiteral("last_sent_at"), QJsonValue::Null},
                                     {QStringLiteral("messages_sent"), 0}, {QStringLiteral("messages_dropped"), 0}, {QStringLiteral("schema_version"),legacy?1:2}});
            const QJsonObject hello{{QStringLiteral("type"), QStringLiteral("hello")},
                                    {QStringLiteral("schema_version"), 2},
                                    {QStringLiteral("service"), QStringLiteral("webull-lv2-gateway")},
                                    {QStringLiteral("client_id"), id}, {QStringLiteral("symbol"), engine_->symbol_},
                                    {QStringLiteral("snapshot_semantics"), QStringLiteral("full_replace")},
                                    {QStringLiteral("aggregation"), QStringLiteral("price")}};
            if (!legacy) socket->write(websocketFrame(QJsonDocument(hello).toJson(QJsonDocument::Compact)));
            if (!engine_->latestBook_.isEmpty()) sendBook(socket,engine_->publicBook());
            publishClients(); return;
        }
        if (path == QStringLiteral("/v2/health/live") || path == QStringLiteral("/v1/health/live"))
            respond(socket, 200, QJsonObject{{"ok",true},{"service","webull-lv2-gateway"},{"api_version",2}});
        else if (path == QStringLiteral("/v2/health/ready"))
            respond(socket, engine_->dataState_ == QStringLiteral("flowing") ? 200 : 503,
                    QJsonObject{{"ready",engine_->dataState_ == QStringLiteral("flowing")},{"status",engine_->publicStatus()}});
        else if (path == QStringLiteral("/v2/status")) {
            auto response=engine_->publicStatus();response.insert("status",engine_->publicStatus());
            response.insert("depth",engine_->latestBook_.isEmpty()?QJsonValue::Null:QJsonValue(legacy?legacyBook(engine_->publicBook()):engine_->publicBook()));
            response.insert("client_count",clients_.size());respond(socket,200,response);
        }
        else if (path == QStringLiteral("/v2/symbols")) respond(socket, 200, engine_->symbolsPayload());
        else if (path == QStringLiteral("/v2/clients")) respond(socket, 200, QJsonObject{{"clients",clientsJson()}});
        else if (path == QStringLiteral("/v2/book/") + engine_->symbol_ || path == QStringLiteral("/v2/depth/") + engine_->symbol_) {
            if (engine_->latestBook_.isEmpty()) respond(socket, 503, QJsonObject{{"error","no valid depth has been received"}});
            else respond(socket, 200, legacy ? legacyBook(engine_->publicBook()) : engine_->publicBook());
        } else respond(socket, 404, QJsonObject{{"error","not_found"}});
    }
    void publishClients()
    {
        Machome::Webull::ClientList list;
        for (const auto &object : clients_) {
            Machome::Webull::ClientInfo value;
            value.clientId = object.value(QStringLiteral("client_id")).toString();
            value.remote = object.value(QStringLiteral("remote")).toString();
            value.connectedAt = QDateTime::fromString(object.value(QStringLiteral("connected_at")).toString(), Qt::ISODateWithMs);
            value.lastSentAt = QDateTime::fromString(object.value(QStringLiteral("last_sent_at")).toString(), Qt::ISODateWithMs);
            value.hasLastSentAt = value.lastSentAt.isValid();
            value.messagesSent = object.value(QStringLiteral("messages_sent")).toInteger(); list.append(value);
        }
        emit engine_->clientsUpdated(list);
    }
    WebullEngine *engine_;
    QTcpServer server_;
    QHash<QTcpSocket*, QByteArray> buffers_;
    QHash<QTcpSocket*, QJsonObject> clients_;
    QTimer heartbeat_;
    QTimer ping_;
    QList<QPair<QHostAddress,int>> networks_;
    QHash<QTcpSocket*,QJsonObject> pending_;
    QHash<QTcpSocket*,qint64> awaitingPong_;
};

ScheduleDecision WebullSchedule::evaluate(const QDateTime &utcNow, const QString &mode,
                                          const QTime &start, const QTime &stop,
                                          const QTimeZone &zone,
                                          const QString &operatingMode)
{
    ScheduleDecision result;
    if (!utcNow.isValid() || !zone.isValid() || !start.isValid() || !stop.isValid()
        || start >= stop
        || !QSet<QString>{QStringLiteral("work"), QStringLiteral("weekend_test")}
                .contains(operatingMode)) {
        result.reason = QStringLiteral("invalid_schedule"); return result;
    }
    result.localNow = utcNow.toTimeZone(zone);
    const bool weekday = result.localNow.date().dayOfWeek() <= 5;
    const bool inside = weekday && result.localNow.time() >= start && result.localNow.time() < stop;
    const bool automaticCollection = operatingMode == QStringLiteral("work") && inside;
    result.collectorDesired = mode == QStringLiteral("force_running")
        || (mode == QStringLiteral("auto") && automaticCollection);
    if (mode == QStringLiteral("force_stopped")) result.collectorDesired = false;
    result.scheduledIdle = mode == QStringLiteral("auto") && !automaticCollection;
    result.reason = operatingMode == QStringLiteral("weekend_test")
        && mode == QStringLiteral("auto")
        ? QStringLiteral("周末测试模式：等待 UI 手动启动")
        : mode == QStringLiteral("auto")
        ? (inside ? QStringLiteral("计划采集时间窗内") : QStringLiteral("不在计划采集时间窗"))
        : (mode == QStringLiteral("force_running") ? QStringLiteral("手动强制采集")
                                                   : QStringLiteral("手动强制停止"));
    if (operatingMode == QStringLiteral("weekend_test")) return result;
    QDate date = result.localNow.date();
    for (int add = 0; add <= 8; ++add) {
        const QDate candidate = date.addDays(add);
        if (candidate.dayOfWeek() > 5) continue;
        for (const auto &clock : {start, stop}) {
            const QDateTime local(candidate, clock, zone);
            if (local > result.localNow) {
                if (!result.nextTransitionUtc.isValid() || local < result.nextTransitionUtc.toTimeZone(zone))
                    result.nextTransitionUtc = local.toUTC();
            }
        }
        if (result.nextTransitionUtc.isValid()) break;
    }
    return result;
}

bool WebullSchedule::loginCheckDue(const QDateTime &utcNow, const QSet<QString> &completed,
                                   QString *slotKey, const QTimeZone &zone, int catchUpMinutes)
{
    if (!utcNow.isValid() || !zone.isValid()) return false;
    const auto local = utcNow.toTimeZone(zone);
    for (const QTime time : {QTime(0,0), QTime(9,0)}) {
        const QDateTime scheduled(local.date(), time, zone);
        const QString key = local.date().toString(Qt::ISODate) + u'T' + time.toString(QStringLiteral("HH:mm"));
        const qint64 age = scheduled.msecsTo(local);
        if (age >= 0 && age <= qint64(catchUpMinutes) * 60'000 && !completed.contains(key)) {
            if (slotKey) *slotKey = key; return true;
        }
    }
    return false;
}

WebullEngine::WebullEngine(QObject *parent) : hub::IModuleEngine(parent)
{
    // The original GatewayRuntime evaluated its long-running schedule every
    // five seconds. Keep that retry/transition cadence in work mode.
    scheduleTimer_.setInterval(5'000);
    freshnessTimer_.setInterval(1'000);
    helperRestartTimer_.setSingleShot(true);
    connect(&scheduleTimer_, &QTimer::timeout, this, &WebullEngine::evaluateSchedule);
    connect(&freshnessTimer_, &QTimer::timeout, this, &WebullEngine::checkFreshness);
    connect(&helperRestartTimer_, &QTimer::timeout, this, [this] { startHelper(false); });
    connect(&helper_, &QProcess::readyReadStandardOutput, this, &WebullEngine::helperReadyRead);
    connect(&helper_, qOverload<int,QProcess::ExitStatus>(&QProcess::finished), this, &WebullEngine::helperFinished);
    connect(&helper_, &QProcess::errorOccurred, this, &WebullEngine::helperError);
}

WebullEngine::~WebullEngine() { stop(hub::StopMode::Immediate); }

bool WebullEngine::normalizeHttpDepth(const QJsonObject &body, int maximumLevels,
                                      QJsonArray *bids, QJsonArray *asks, QString *error)
{
    QJsonObject depth = body.value(QStringLiteral("depth")).toObject();
    if (depth.isEmpty() && body.value(QStringLiteral("data")).isObject()) {
        const auto data = body.value(QStringLiteral("data")).toObject();
        depth = data.value(QStringLiteral("depth")).toObject(); if (depth.isEmpty()) depth = data;
    }
    if (depth.isEmpty()) depth = body;
    const auto rawBids = depth.value(QStringLiteral("ntvAggBidList"));
    const auto rawAsks = depth.value(QStringLiteral("ntvAggAskList"));
    if (!rawBids.isArray() || !rawAsks.isArray() || rawBids.toArray().isEmpty() || rawAsks.toArray().isEmpty()
        || rawBids.toArray().size() > 50 || rawAsks.toArray().size() > 50) {
        if (error) *error = QStringLiteral("missing/empty/oversized ntvAggBidList or ntvAggAskList");
        return false;
    }
    return normalizedLevels(rawBids.toArray(), true, maximumLevels, bids, error)
        && normalizedLevels(rawAsks.toArray(), false, maximumLevels, asks, error);
}

bool WebullEngine::decodeMqttDepth(const QByteArray &frame, const QString &tickerId,
                                   int minimumSideLevels, int maximumLevels,
                                   QJsonArray *bids, QJsonArray *asks, QString *error)
{
    if (frame.isEmpty() || (quint8(frame.at(0)) >> 4) != 3) {
        if (error) *error = QStringLiteral("not MQTT PUBLISH"); return false;
    }
    qsizetype offset = 1; quint64 remaining = 0;
    if (!readVarint(frame, &offset, &remaining) || remaining > quint64(frame.size() - offset) || offset + 2 > frame.size()) {
        if (error) *error = QStringLiteral("truncated MQTT PUBLISH"); return false;
    }
    const qsizetype packetEnd = offset + static_cast<qsizetype>(remaining);
    const int topicSize = (quint8(frame.at(offset)) << 8) | quint8(frame.at(offset + 1)); offset += 2;
    if (offset + topicSize > packetEnd) { if (error) *error = QStringLiteral("truncated MQTT topic"); return false; }
    QJsonParseError parseError;
    const auto topic = QJsonDocument::fromJson(frame.mid(offset, topicSize), &parseError).object(); offset += topicSize;
    const int qos = (quint8(frame.at(0)) >> 1) & 3;
    if (qos) offset += 2;
    if (parseError.error != QJsonParseError::NoError
        || topic.value(QStringLiteral("type")).toInt() != 109
        || topic.value(QStringLiteral("tickerId")).toVariant().toString() != tickerId
        || offset > packetEnd) {
        if (error) *error = QStringLiteral("MQTT topic is not target type-109"); return false;
    }
    QList<ProtoField> outer;
    if (!protoFields(frame.mid(offset, packetEnd - offset), &outer)) {
        if (error) *error = QStringLiteral("invalid protobuf envelope"); return false;
    }
    QJsonArray rawBids, rawAsks;
    bool found = false;
    for (const auto &field : outer)
        if (field.wire == 2 && parseDepthContainer(field.bytes, &rawBids, &rawAsks)) { found = true; break; }
    if (!found || rawBids.size() < minimumSideLevels || rawAsks.size() < minimumSideLevels) {
        if (error) *error = QStringLiteral("non-authoritative shallow or invalid type-109 depth"); return false;
    }
    return normalizedLevels(rawBids, true, maximumLevels, bids, error)
        && normalizedLevels(rawAsks, false, maximumLevels, asks, error);
}

void WebullEngine::initialize(const hub::ModuleContext &context)
{
    if (running_) return;
    context_ = context;
    QString error;
    if (!validateContext(&error) || !prepareStorage(&error) || !loadLatest(&error)) {
        state_ = QStringLiteral("blocked"); lastError_ = error; publishError(QStringLiteral("configuration_error"), error); return;
    }
    initialized_ = true; state_ = QStringLiteral("initialized"); publishState();
}

bool WebullEngine::validateContext(QString *error)
{
    const auto settings = context_.settings;
    // Never restore a test mode from configuration or persisted runtime data.
    // Every initialization/process restart returns to the production baseline.
    operatingMode_ = QStringLiteral("work");
    symbol_ = settings.value(QStringLiteral("default_symbol")).toString(QStringLiteral("XOP")).trimmed().toUpper();
    tickerId_ = settings.value(QStringLiteral("ticker_id")).toVariant().toString();
    if (tickerId_.isEmpty()) tickerId_ = QStringLiteral("913243629");
    staleAfterMs_ = settings.value(QStringLiteral("freshness_ms")).toInteger(90'000);
    maximumLevels_ = settings.value(QStringLiteral("depth_size")).toInt(50);
    apiPort_ = settings.value(QStringLiteral("api_port")).toInt(18765);
    apiEnabled_ = settings.value(QStringLiteral("api_enabled")).toBool(true);
    liveBrowserEnabled_ = settings.value(QStringLiteral("live_browser_enabled")).toBool(false);
    const QString initialMode = settings.value(QStringLiteral("initial_schedule_mode"))
                                    .toString(QStringLiteral("auto"));
    if (!QSet<QString>{QStringLiteral("auto"), QStringLiteral("force_running"),
                       QStringLiteral("force_stopped")}.contains(initialMode)) {
        if (error) *error = QStringLiteral("Webull initial_schedule_mode 无效");
        return false;
    }
    scheduleMode_ = initialMode;
    fixtureMode_ = settings.value(QStringLiteral("fixture_event")).isObject();
    if (symbol_.isEmpty() || symbol_.size() > 24 || tickerId_.isEmpty() || maximumLevels_ < 1
        || maximumLevels_ > 50 || staleAfterMs_ < 1'000 || staleAfterMs_ > 600'000
        || apiPort_ < 0 || apiPort_ > 65535) {
        if (error) *error = QStringLiteral("Webull native settings invalid"); return false;
    }
    const QString clean = QDir::cleanPath(context_.dataRoot);
    if (clean.isEmpty() || clean.contains(QStringLiteral("WebullData"), Qt::CaseInsensitive)
        || clean.contains(QStringLiteral("WebullLV2Gateway"), Qt::CaseInsensitive)) {
        if (error) *error = QStringLiteral("Webull data_root must be MachomeHub-owned"); return false;
    }
    return true;
}

bool WebullEngine::prepareStorage(QString *error)
{
    QDir directory;
    if (!directory.mkpath(context_.dataRoot + QStringLiteral("/depth"))
        || !directory.mkpath(context_.dataRoot + QStringLiteral("/runtime"))
        || !directory.mkpath(context_.dataRoot + QStringLiteral("/chrome-profile"))) {
        if (error) *error = QStringLiteral("cannot create Webull data root"); return false;
    }
    tokenFile_ = context_.dataRoot + QStringLiteral("/runtime/api.token");
    QFile token(tokenFile_);
    if (token.exists()) {
        if (!token.open(QIODevice::ReadOnly)) { if (error) *error = token.errorString(); return false; }
        apiToken_ = token.readAll().trimmed();
    } else {
        if (!token.open(QIODevice::WriteOnly | QIODevice::NewOnly)) { if (error) *error = token.errorString(); return false; }
        apiToken_ = newToken().toLatin1(); token.write(apiToken_ + '\n'); token.close();
    }
#ifdef Q_OS_UNIX
    ::chmod(QFile::encodeName(tokenFile_).constData(), 0600);
#endif
    if (apiToken_.size() < 32) { if (error) *error = QStringLiteral("Webull API token too short"); return false; }
    return loadLoginChecks(error);
}

bool WebullEngine::loadLoginChecks(QString *error)
{
    const QString path = context_.dataRoot + QStringLiteral("/runtime/login_checks.json");
    QFile file(path);
    if (!file.exists()) return true;
    if (!file.open(QIODevice::ReadOnly)) { if (error) *error = file.errorString(); return false; }
    QJsonParseError parse;
    const auto document = QJsonDocument::fromJson(file.readAll(), &parse);
    if (parse.error != QJsonParseError::NoError || !document.isObject()) {
        completedLoginChecks_.clear(); completedLoginResults_.clear();
        return true;
    }
    completedLoginChecks_.clear(); completedLoginResults_.clear();
    static const QRegularExpression keyPattern(QStringLiteral("^\\d{4}-\\d{2}-\\d{2}T(?:00|09):00$"));
    const auto completed = document.object().value(QStringLiteral("completed"));
    if (completed.isObject()) {
        const QJsonObject completedObject = completed.toObject();
        for (auto it = completedObject.constBegin(); it != completedObject.constEnd(); ++it) {
            if (!keyPattern.match(it.key()).hasMatch() || !it.value().isObject()) continue;
            completedLoginChecks_.insert(it.key()); completedLoginResults_.insert(it.key(), it.value().toObject());
        }
    } else if (completed.isArray()) {
        for (const auto &value : completed.toArray()) {
            const QString key = value.toString();
            if (keyPattern.match(key).hasMatch()) {
                completedLoginChecks_.insert(key);
                completedLoginResults_.insert(key, QJsonObject{{QStringLiteral("outcome"),QStringLiteral("migrated")}});
            }
        }
    }
    return true;
}

bool WebullEngine::persistLoginChecks(QString *error)
{
    const QDate cutoff = nowUtc().toTimeZone(QTimeZone("Asia/Shanghai")).date().addDays(-31);
    for (auto it = completedLoginChecks_.begin(); it != completedLoginChecks_.end();) {
        const QDate date = QDate::fromString(it->left(10), Qt::ISODate);
        if (!date.isValid() || date < cutoff) {
            completedLoginResults_.remove(*it); it = completedLoginChecks_.erase(it);
        }
        else ++it;
    }
    QStringList ordered;
    for (const auto &key : completedLoginChecks_) ordered.append(key);
    std::sort(ordered.begin(), ordered.end());
    QJsonObject completed; for (const auto &key : ordered) completed.insert(key, completedLoginResults_.value(key));
    QSaveFile file(context_.dataRoot + QStringLiteral("/runtime/login_checks.json"));
    if (!file.open(QIODevice::WriteOnly)) { if (error) *error = file.errorString(); return false; }
    file.write(QJsonDocument(QJsonObject{{QStringLiteral("schema_version"), 1},
                                         {QStringLiteral("completed"), completed}}).toJson(QJsonDocument::Indented));
    if (!file.commit()) { if (error) *error = file.errorString(); return false; }
    return true;
}

void WebullEngine::finishLoginCheck(const QString &outcome)
{
    if (activeLoginCheckKey_.isEmpty()) return;
    const QString key = activeLoginCheckKey_;
    completedLoginChecks_.insert(key);
    completedLoginResults_.insert(key, QJsonObject{{QStringLiteral("outcome"),outcome},
        {QStringLiteral("completed_at"),iso(nowUtc())}});
    activeLoginCheckKey_.clear(); activeLoginStartedUtc_ = {}; temporaryLoginUntilUtc_ = {};
    QString error;
    if (!persistLoginChecks(&error)) publishError(QStringLiteral("login_check_persist_failed"),error,true);
    publishLog(outcome == QStringLiteral("timeout") ? Machome::Webull::LogSeverity::Warning : Machome::Webull::LogSeverity::Info,
               QStringLiteral("login_check_finished"),
               QStringLiteral("完成计划登录检查 %1 outcome=%2").arg(key,outcome));
}

bool WebullEngine::loadLatest(QString *error)
{
    QFile file(context_.dataRoot + QStringLiteral("/depth/latest.json"));
    if (!file.exists()) return true;
    if (!file.open(QIODevice::ReadOnly)) { if (error) *error = file.errorString(); return false; }
    QJsonParseError parse;
    const auto document = QJsonDocument::fromJson(file.readAll(), &parse);
    if (parse.error != QJsonParseError::NoError || !document.isObject()) {
        if (error) *error = QStringLiteral("latest.json is malformed"); return false;
    }
    latestBook_ = document.object(); sequence_ = latestBook_.value(QStringLiteral("sequence")).toInteger();
    sessionId_ = latestBook_.value(QStringLiteral("session_id")).toString();
    lastHash_ = latestBook_.value(QStringLiteral("content_hash")).toString();
    lastDepthUtc_ = QDateTime::fromString(latestBook_.value(QStringLiteral("captured_at")).toString(), Qt::ISODateWithMs).toUTC();
    dataState_ = QStringLiteral("stale"); return true;
}

void WebullEngine::start()
{
    if (!initialized_ || running_) return;
    running_ = true; stopping_ = false;
    sessionId_ = QUuid::createUuid().toString(QUuid::WithoutBraces);
    sequence_ = 0; state_ = QStringLiteral("warming");
    startApi(); scheduleTimer_.start(); freshnessTimer_.start(); evaluateSchedule(); publishState();
    Machome::Webull::SymbolInfo symbolInfo{symbol_, tickerId_, maximumLevels_, staleAfterMs_};
    emit symbolsUpdated(Machome::Webull::SymbolList{symbolInfo});
    if (fixtureMode_) {
        QString error;
        if (!processRawEvent(context_.settings.value(QStringLiteral("fixture_event")).toObject(), &error))
            publishError(QStringLiteral("fixture_rejected"), error);
    }
}

void WebullEngine::stop(hub::StopMode)
{
    if (!running_ && !initialized_) return;
    stopping_ = true; running_ = false; scheduleTimer_.stop(); freshnessTimer_.stop(); helperRestartTimer_.stop();
    stopHelper(); stopApi(); collectorRunning_ = false; browserState_ = QStringLiteral("stopped");
    activeLoginCheckKey_.clear(); activeLoginStartedUtc_ = {}; temporaryLoginUntilUtc_ = {};
    operatingMode_ = QStringLiteral("work"); scheduleMode_ = QStringLiteral("auto");
    state_ = QStringLiteral("stopped"); publishState();
}

void WebullEngine::startApi()
{
    if (!apiEnabled_) return;
    if (!api_) api_ = new WebullV2Server(this);
    QString error;
    if (!api_->start(static_cast<quint16>(apiPort_), &error)) {
        lastError_ = QStringLiteral("18765 v2 listen failed: %1").arg(error); publishError(QStringLiteral("api_bind_failed"), lastError_);
    }
}

void WebullEngine::stopApi() { if (api_) api_->stop(); }

QDateTime WebullEngine::nowUtc() const { return testNowUtc_.isValid() ? testNowUtc_ : QDateTime::currentDateTimeUtc(); }
QString WebullEngine::iso(const QDateTime &value) const { return value.isValid() ? value.toUTC().toString(Qt::ISODateWithMs) : QString{}; }
void WebullEngine::setNowForTest(const QDateTime &value) { testNowUtc_ = value.toUTC(); }
void WebullEngine::clearNowForTest() { testNowUtc_ = {}; }
void WebullEngine::evaluateScheduleForTest() { evaluateSchedule(); }
bool WebullEngine::collectorDesiredNow() const
{
    return WebullSchedule::evaluate(nowUtc(), scheduleMode_, QTime(9, 0), QTime(16, 0),
                                    QTimeZone("Asia/Shanghai"), operatingMode_)
            .collectorDesired
        || (temporaryLoginUntilUtc_.isValid() && nowUtc() < temporaryLoginUntilUtc_);
}

void WebullEngine::evaluateSchedule()
{
    if (!running_) return;
    if (!activeLoginCheckKey_.isEmpty() && temporaryLoginUntilUtc_.isValid()
        && nowUtc() >= temporaryLoginUntilUtc_) {
        finishLoginCheck(QStringLiteral("timeout"));
    }
    const auto decision = WebullSchedule::evaluate(nowUtc(), scheduleMode_, QTime(9, 0),
                                                   QTime(16, 0),
                                                   QTimeZone("Asia/Shanghai"),
                                                   operatingMode_);
    const bool temporaryLogin = temporaryLoginUntilUtc_.isValid() && nowUtc() < temporaryLoginUntilUtc_;
    const bool desired = decision.collectorDesired || temporaryLogin;
    if (desired && !collectorRunning_ && !helperRestartTimer_.isActive()) startHelper(false);
    else if (!desired && collectorRunning_) stopHelper();

    QString slot;
    if (operatingMode_ == QStringLiteral("work")
        && scheduleMode_ == QStringLiteral("auto") && activeLoginCheckKey_.isEmpty()
        && WebullSchedule::loginCheckDue(nowUtc(), completedLoginChecks_, &slot)) {
        activeLoginCheckKey_ = slot;
        activeLoginStartedUtc_ = nowUtc();
        temporaryLoginUntilUtc_ = nowUtc().addSecs(120);
        if (!collectorRunning_) startHelper(false);
        sendHelperCommand(QStringLiteral("check_auth"));
        publishLog(Machome::Webull::LogSeverity::Info, QStringLiteral("login_check"),
                   QStringLiteral("执行计划登录检查 %1").arg(slot));
    }
    state_ = collectorRunning_ ? (dataState_ == QStringLiteral("flowing") ? QStringLiteral("active") : QStringLiteral("warming"))
                               : (decision.scheduledIdle ? QStringLiteral("scheduled_idle") : QStringLiteral("degraded"));
    publishState();
}

void WebullEngine::checkFreshness()
{
    if (!lastDepthUtc_.isValid()) return;
    const qint64 age = lastDepthUtc_.msecsTo(nowUtc());
    if (age > staleAfterMs_ && dataState_ == QStringLiteral("flowing")) {
        dataState_ = QStringLiteral("stale"); state_ = collectorRunning_ ? QStringLiteral("degraded") : state_;
        publishState();
    }
}

void WebullEngine::startHelper(bool visible)
{
    if (collectorRunning_ || helper_.state() != QProcess::NotRunning) return;
    if (fixtureMode_) {
        browserState_ = QStringLiteral("running"); browserVisible_ = visible; collectorRunning_ = true; publishState(); return;
    }
    // record-only protects outbound business mutations. Browser market-data
    // collection is a read path and is independently gated here.
    if (!liveBrowserEnabled_) {
        browserState_ = QStringLiteral("stopped"); collectorRunning_ = false;
        return;
    }
    QString script = context_.settings.value(QStringLiteral("helper_script")).toString();
    if (script.isEmpty()) script = QDir(QCoreApplication::applicationDirPath()).absoluteFilePath(
        QStringLiteral("../Helpers/machome-webull-browser-helper"));
    const QString clean = QFileInfo(script).canonicalFilePath();
    const QString appRoot = QFileInfo(QCoreApplication::applicationDirPath() + QStringLiteral("/..")).canonicalFilePath();
    const QString sourceHelper = QFileInfo(QCoreApplication::applicationDirPath() + QStringLiteral("/machome-webull-browser-helper")).canonicalFilePath();
    const bool testAllowed = context_.settings.value(QStringLiteral("allow_source_helper_for_tests")).toBool(false)
        && clean == sourceHelper;
    if (clean.isEmpty() || (!clean.startsWith(appRoot + u'/') && !testAllowed)) {
        publishError(QStringLiteral("helper_path_rejected"), QStringLiteral("browser helper must be inside app bundle")); return;
    }
    helper_.setProgram(clean);
    helper_.setArguments({QStringLiteral("--profile"), context_.dataRoot + QStringLiteral("/chrome-profile"),
                          QStringLiteral("--ticker-id"), tickerId_, QStringLiteral("--symbol"), symbol_});
    helper_.setProcessChannelMode(QProcess::SeparateChannels);
    helper_.setStandardErrorFile(QProcess::nullDevice());
    helper_.start();
    if (!helper_.waitForStarted(3'000)) { publishError(QStringLiteral("helper_start_failed"), helper_.errorString(), true); restartHelperLater(); return; }
    lastHelperStartUtc_ = nowUtc(); browserState_ = QStringLiteral("starting"); browserVisible_ = false; collectorRunning_ = true;
    sendHelperCommand(visible ? QStringLiteral("open_login") : QStringLiteral("start_collector"));
    if (!activeLoginCheckKey_.isEmpty()) sendHelperCommand(QStringLiteral("check_auth"));
    publishState();
}

void WebullEngine::stopHelper()
{
    helperRestartTimer_.stop();
    intentionalHelperStop_ = true;
    if (helper_.state() != QProcess::NotRunning) {
        sendHelperCommand(QStringLiteral("shutdown")); helper_.closeWriteChannel();
        if (!helper_.waitForFinished(2'000)) { helper_.terminate(); if (!helper_.waitForFinished(1'000)) helper_.kill(); helper_.waitForFinished(1'000); }
    }
    collectorRunning_ = false; browserVisible_ = false; browserState_ = QStringLiteral("stopped"); helperBuffer_.clear();
    intentionalHelperStop_ = false;
}

void WebullEngine::sendHelperCommand(const QString &action, const QJsonObject &arguments)
{
    if (helper_.state() == QProcess::NotRunning) return;
    helper_.write(QJsonDocument(QJsonObject{{QStringLiteral("command"), action},
                                            {QStringLiteral("arguments"), arguments}}).toJson(QJsonDocument::Compact) + '\n');
}

void WebullEngine::helperReadyRead()
{
    helperBuffer_ += helper_.readAllStandardOutput();
    if (helperBuffer_.size() > kMaximumHelperLine) { publishError(QStringLiteral("helper_protocol_overflow"), QStringLiteral("helper output exceeded limit")); stopHelper(); return; }
    qsizetype newline = -1;
    while ((newline = helperBuffer_.indexOf('\n')) >= 0) {
        const QByteArray line = helperBuffer_.left(newline); helperBuffer_.remove(0, newline + 1);
        if (line.trimmed().isEmpty()) continue;
        QJsonParseError parse; const auto doc = QJsonDocument::fromJson(line, &parse);
        QString error;
        if (parse.error != QJsonParseError::NoError || !doc.isObject() || !processRawEvent(doc.object(), &error)) {
            ++invalidResponses_; publishError(QStringLiteral("helper_invalid_event"), error.isEmpty() ? parse.errorString() : error);
        }
    }
}

void WebullEngine::helperFinished(int exitCode, QProcess::ExitStatus status)
{
    collectorRunning_ = false; browserVisible_ = false; browserState_ = QStringLiteral("error");
    if (!stopping_ && !intentionalHelperStop_ && collectorDesiredNow()) {
        publishError(QStringLiteral("helper_crashed"), QStringLiteral("browser helper exited code=%1 status=%2").arg(exitCode).arg(int(status)), true);
        restartHelperLater();
    }
    publishState();
}

void WebullEngine::helperError(QProcess::ProcessError) { if (!stopping_ && !intentionalHelperStop_) publishError(QStringLiteral("helper_error"), helper_.errorString(), true); }
void WebullEngine::restartHelperLater()
{
    if (!running_ || !collectorDesiredNow() || helperRestartTimer_.isActive()) return;
    // The old runtime retried on the next five-second schedule evaluation and
    // kept retrying while collection remained desired.
    ++helperRestartAttempt_;
    helperRestartTimer_.start(5'000);
}

bool WebullEngine::processRawEvent(const QJsonObject &event, QString *error)
{
    const QString type = event.value(QStringLiteral("type")).toString();
    if (type == QStringLiteral("hello")) return true;
    if (type == QStringLiteral("ack")) {
        if (!event.value(QStringLiteral("ok")).toBool(true))
            publishError(QStringLiteral("helper_command_failed"), event.value(QStringLiteral("message")).toString());
        return true;
    }
    if (type == QStringLiteral("error")) {
        publishError(event.value(QStringLiteral("code")).toString(QStringLiteral("helper_error")),
                     event.value(QStringLiteral("message")).toString(QStringLiteral("browser helper error")), true);
        return true;
    }
    if (type == QStringLiteral("browser")) {
        browserState_ = event.value(QStringLiteral("state")).toString(QStringLiteral("running"));
        browserVisible_ = event.value(QStringLiteral("visible")).toBool(false);
        collectorRunning_ = event.value(QStringLiteral("collector_running")).toBool(collectorRunning_);
        if (browserState_ == QStringLiteral("running") && collectorRunning_) helperRestartAttempt_ = 0;
        publishState(); return true;
    }
    if (type == QStringLiteral("auth")) {
        const QString state = event.value(QStringLiteral("state")).toString();
        if (!QSet<QString>{"unknown","authenticated","login_required","captcha_required"}.contains(state)) {
            if (error) *error = QStringLiteral("invalid auth state"); return false;
        }
        authState_ = state;
        if (state == QStringLiteral("login_required") || state == QStringLiteral("captcha_required")) {
            emit eventReady(QStringLiteral("webull.auth_required"),
                            {{QStringLiteral("state"), state},
                             {QStringLiteral("checked_at"), event.value(QStringLiteral("checked_at"))},
                             {QStringLiteral("external_notification_sent"), false}});
        }
        QDateTime checkedAt = QDateTime::fromString(event.value(QStringLiteral("checked_at")).toString(),Qt::ISODateWithMs);
        if (!checkedAt.isValid()) checkedAt = QDateTime::fromString(event.value(QStringLiteral("checked_at")).toString(),Qt::ISODate);
        if (!activeLoginCheckKey_.isEmpty() && state != QStringLiteral("unknown")
            && checkedAt.isValid() && checkedAt.toUTC() >= activeLoginStartedUtc_) {
            finishLoginCheck(state);
        }
        publishState(); return true;
    }
    QJsonArray bids, asks;
    if (type == QStringLiteral("raw_http")) {
        if (!event.value(QStringLiteral("body")).isObject()
            || !normalizeHttpDepth(event.value(QStringLiteral("body")).toObject(), maximumLevels_, &bids, &asks, error)) return false;
    } else if (type == QStringLiteral("raw_mqtt")) {
        const QByteArray frame = QByteArray::fromBase64(event.value(QStringLiteral("base64")).toString().toLatin1());
        // MQTT acknowledgments and heartbeat packets are normal traffic;
        // they are not failed depth responses and carry no market data.
        if (!frame.isEmpty() && (quint8(frame.at(0)) >> 4) != 3) return true;
        if (!decodeMqttDepth(frame, tickerId_, 5, maximumLevels_, &bids, &asks, error)) return false;
    } else {
        if (error) *error = QStringLiteral("unsupported helper event type"); return false;
    }
    return publishDepth(bids, asks, event.value(QStringLiteral("captured_at")).toString(),
                        type == QStringLiteral("raw_http") ? QStringLiteral("HTTP") : QStringLiteral("MQTT"), error);
}

bool WebullEngine::ingestRawEventForTest(const QJsonObject &event, QString *error) { return processRawEvent(event, error); }

bool WebullEngine::publishDepth(const QJsonArray &bids, const QJsonArray &asks,
                                const QString &capturedAtText, const QString &source, QString *error)
{
    QDateTime captured = QDateTime::fromString(capturedAtText, Qt::ISODateWithMs);
    if (!captured.isValid()) captured = QDateTime::fromString(capturedAtText, Qt::ISODate);
    const bool explicitZone = capturedAtText.endsWith(u'Z', Qt::CaseInsensitive)
        || (capturedAtText.indexOf(u'T') >= 0
            && (capturedAtText.indexOf(u'+', capturedAtText.indexOf(u'T')) >= 0
                || capturedAtText.indexOf(u'-', capturedAtText.indexOf(u'T')) >= 0));
    if (!captured.isValid() || !explicitZone) {
        if (error) *error = QStringLiteral("captured_at must contain an explicit UTC offset");
        return false;
    }
    captured = captured.toUTC();
    if (nowUtc().msecsTo(captured) > 5'000) {
        if (error) *error = QStringLiteral("captured_at exceeds maximum future skew");
        return false;
    }
    const QByteArray compact = QJsonDocument(QJsonObject{{QStringLiteral("bids"), bids}, {QStringLiteral("asks"), asks}})
                                   .toJson(QJsonDocument::Compact);
    const QString hash = QString::fromLatin1(QCryptographicHash::hash(compact, QCryptographicHash::Sha256).toHex().left(16));
    const bool changed = hash != lastHash_; lastHash_ = hash; ++sequence_; ++validResponses_;
    const auto bid = bids.first().toObject(); const auto ask = asks.first().toObject();
    const long double bestBid = bid.value(QStringLiteral("price")).toString().toDouble();
    const long double bestAsk = ask.value(QStringLiteral("price")).toString().toDouble();
    const long double spread = bestAsk - bestBid;
    const QString marketState = spread < 0 ? QStringLiteral("crossed") : spread == 0 ? QStringLiteral("locked") : QStringLiteral("normal");
    latestBook_ = {{QStringLiteral("schema_version"), 2}, {QStringLiteral("type"), QStringLiteral("depth_snapshot")},
                   {QStringLiteral("symbol"), symbol_}, {QStringLiteral("ticker_id"), tickerId_},
                   {QStringLiteral("captured_at"), iso(captured)}, {QStringLiteral("published_at"), iso(nowUtc())},
                   {QStringLiteral("session_id"), sessionId_}, {QStringLiteral("sequence"), sequence_},
                   {QStringLiteral("changed"), changed}, {QStringLiteral("content_hash"), hash},
                   {QStringLiteral("source"), source},
                   {QStringLiteral("book"), QJsonObject{{QStringLiteral("aggregation"), QStringLiteral("price")},
                       {QStringLiteral("depth"), std::max(bids.size(), asks.size())}, {QStringLiteral("bid_depth"), bids.size()},
                       {QStringLiteral("ask_depth"), asks.size()}, {QStringLiteral("bids"), bids}, {QStringLiteral("asks"), asks},
                       {QStringLiteral("inside_market"), QJsonObject{{QStringLiteral("best_bid"), bid.value(QStringLiteral("price"))},
                           {QStringLiteral("best_ask"), ask.value(QStringLiteral("price"))},
                           {QStringLiteral("spread"), decimalText(spread)},
                           {QStringLiteral("mid_price"), decimalText((bestBid + bestAsk) / 2)},
                           {QStringLiteral("state"), marketState}}}}}};
    lastDepthUtc_ = captured; dataState_ = QStringLiteral("flowing"); state_ = QStringLiteral("active"); lastError_.clear();
    if (!persistBook(latestBook_, error)) return false;
    Machome::Webull::BookSnapshot typed;
    typed.symbol = symbol_; typed.tickerId = tickerId_; typed.sessionId = sessionId_; typed.sequence = sequence_;
    typed.capturedAt = captured; typed.publishedAt = nowUtc(); typed.changed = changed; typed.contentHash = hash;
    typed.source = source; typed.ageMs = 0; typed.fresh = true;
    for (const auto &value : bids) { const auto o=value.toObject(); typed.bids.append({o.value("level").toInt(),o.value("price").toString(),o.value("volume").toString()}); }
    for (const auto &value : asks) { const auto o=value.toObject(); typed.asks.append({o.value("level").toInt(),o.value("price").toString(),o.value("volume").toString()}); }
    typed.inside.bestBid = bid.value(QStringLiteral("price")).toString(); typed.inside.bestAsk = ask.value(QStringLiteral("price")).toString();
    typed.inside.spread = decimalText(spread); typed.inside.midPrice = decimalText((bestBid + bestAsk)/2); typed.inside.state = marketState;
    emit bookUpdated(typed); emit eventReady(QStringLiteral("webull.book"), publicBook()); if (api_) api_->broadcast(publicBook()); publishState(); return true;
}

bool WebullEngine::persistBook(const QJsonObject &book, QString *error)
{
    QSaveFile latest(context_.dataRoot + QStringLiteral("/depth/latest.json"));
    if (!latest.open(QIODevice::WriteOnly)) { if(error)*error=latest.errorString(); return false; }
    latest.write(QJsonDocument(book).toJson(QJsonDocument::Indented));
    if (!latest.commit()) { if(error)*error=latest.errorString(); return false; }
    if (book.value(QStringLiteral("changed")).toBool()) {
        const QString day = book.value(QStringLiteral("captured_at")).toString().left(10);
        QFile history(context_.dataRoot + QStringLiteral("/depth/") + symbol_.toLower() + QStringLiteral("_depth_") + day + QStringLiteral(".jsonl"));
        if (!history.open(QIODevice::WriteOnly | QIODevice::Append)) { if(error)*error=history.errorString(); return false; }
        history.write(QJsonDocument(book).toJson(QJsonDocument::Compact) + '\n');
    }
    return true;
}

QJsonObject WebullEngine::publicBook() const { return latestBook_; }
QJsonArray WebullEngine::publicClients() const { return api_ ? api_->clientsJson() : QJsonArray{}; }
QJsonObject WebullEngine::symbolsPayload() const
{
    return {{QStringLiteral("schema_version"),2},{QStringLiteral("snapshot_semantics"),QStringLiteral("full_replace")},
            {QStringLiteral("price_encoding"),QStringLiteral("decimal_string")},{QStringLiteral("volume_encoding"),QStringLiteral("decimal_string")},
            {QStringLiteral("symbols"),QJsonArray{QJsonObject{{QStringLiteral("symbol"),symbol_},{QStringLiteral("ticker_id"),tickerId_},
                                                               {QStringLiteral("depth_size"),maximumLevels_},{QStringLiteral("stale_after_ms"),staleAfterMs_}}}}};
}

QJsonObject WebullEngine::publicStatus() const
{
    const auto decision = WebullSchedule::evaluate(nowUtc(), scheduleMode_, QTime(9, 0),
                                                   QTime(16, 0),
                                                   QTimeZone("Asia/Shanghai"),
                                                   operatingMode_);
    const qint64 age = lastDepthUtc_.isValid() ? lastDepthUtc_.msecsTo(nowUtc()) : -1;
    return {{QStringLiteral("schema_version"),2},{QStringLiteral("service"),QStringLiteral("webull-lv2-gateway")},
            {QStringLiteral("engine"),QStringLiteral("native")},{QStringLiteral("state"),state_},
            {QStringLiteral("browser"),browserState_},{QStringLiteral("auth"),authState_},{QStringLiteral("data"),dataState_},
            {QStringLiteral("collector_running"),collectorRunning_},{QStringLiteral("api_running"),api_ && api_->listening()},
            {QStringLiteral("api_address"),QStringLiteral("http://%1:%2").arg(context_.settings.value("api_host").toString("127.0.0.1")).arg(api_?api_->port():apiPort_)},
            {QStringLiteral("schedule_mode"),scheduleMode_},{QStringLiteral("operating_mode"),operatingMode_},
            {QStringLiteral("schedule_message"),decision.reason},
            {QStringLiteral("scheduled_idle"),decision.scheduledIdle},{QStringLiteral("next_transition"),iso(decision.nextTransitionUtc)},
            {QStringLiteral("last_depth_at"),iso(lastDepthUtc_)},{QStringLiteral("data_age_ms"),age},
            {QStringLiteral("valid_responses"),validResponses_},{QStringLiteral("invalid_responses"),invalidResponses_},
            {QStringLiteral("client_count"),api_?api_->clientCount():0},{QStringLiteral("last_error"),lastError_},
            {QStringLiteral("record_only"),context_.recordOnly},{QStringLiteral("live_browser_enabled"),liveBrowserEnabled_},
            {QStringLiteral("browser_visible"),browserVisible_}};
}

QJsonObject WebullEngine::snapshot() const { return {{QStringLiteral("ready"),initialized_},{QStringLiteral("running"),running_},
    {QStringLiteral("operating_mode"),operatingMode_},{QStringLiteral("status"),publicStatus()},
    {QStringLiteral("book"),publicBook()},{QStringLiteral("clients"),publicClients()},{QStringLiteral("symbols"),symbolsPayload().value("symbols")}}; }

void WebullEngine::publishState()
{
    const auto statusObject = publicStatus(); emit snapshotReady(snapshot());
    Machome::Webull::GatewayStatus status;
    status.observedAt=nowUtc(); status.apiLive=api_&&api_->listening(); status.apiReady=dataState_==QStringLiteral("flowing");
    status.apiReportedRunning=status.apiLive; status.collectorRunning=collectorRunning_; status.scheduledIdle=statusObject.value("scheduled_idle").toBool();
    status.dataFresh=lastDepthUtc_.isValid()&&lastDepthUtc_.msecsTo(nowUtc())<=staleAfterMs_; status.dataAgeMs=statusObject.value("data_age_ms").toInteger();
    status.staleAfterMs=staleAfterMs_; status.clientCount=api_?api_->clientCount():0; status.service=QStringLiteral("webull-lv2-gateway");
    status.browserState=browserState_; status.authState=authState_; status.dataState=dataState_; status.scheduleMode=scheduleMode_;
    status.scheduleMessage=statusObject.value("schedule_message").toString(); status.apiAddress=statusObject.value("api_address").toString();
    status.lastDepthAt=iso(lastDepthUtc_); status.lastError=lastError_; status.validResponses=validResponses_; status.invalidResponses=invalidResponses_;
    status.rawStatus=statusObject; emit statusUpdated(status);
}

void WebullEngine::publishLog(Machome::Webull::LogSeverity severity, const QString &code, const QString &message)
{
    emit logEntry({nowUtc(),severity,code,message}); emit eventReady(QStringLiteral("webull.log"),{{"code",code},{"message",message},{"at",iso(nowUtc())}});
}
void WebullEngine::publishError(const QString &code, const QString &message, bool retryable)
{
    lastError_=message; Machome::Webull::ApiError error; error.occurredAt=nowUtc(); error.code=code; error.endpoint=QStringLiteral("native"); error.message=message; error.retryable=retryable;
    emit errorOccurred(error); emit eventReady(QStringLiteral("webull.error"),{{"code",code},{"message",message},{"retryable",retryable}});
}

void WebullEngine::refreshNow() { publishState(); if (!latestBook_.isEmpty()) emit eventReady(QStringLiteral("webull.book"),publicBook()); }

void WebullEngine::submitControl(const QString &requestId, const QString &action, const QJsonObject &arguments)
{
    QString error;
    if (action == QStringLiteral("set_operating_mode")) {
        const QString mode = arguments.value(QStringLiteral("mode")).toString();
        if (arguments.size() != 1
            || !QSet<QString>{QStringLiteral("work"), QStringLiteral("weekend_test")}
                    .contains(mode)) {
            emit controlFailed(requestId, action, QStringLiteral("INVALID_ARGUMENT"),
                               QStringLiteral("set_operating_mode 仅接受 mode=work|weekend_test"),
                               false);
            return;
        }
        operatingMode_ = mode;
        if (operatingMode_ == QStringLiteral("work")) {
            // Returning to work mode must also return to the old program's
            // authoritative auto schedule; a force_running choice made for a
            // weekend test must not leak into Monday production.
            scheduleMode_ = QStringLiteral("auto");
        } else {
            activeLoginCheckKey_.clear();
            activeLoginStartedUtc_ = {};
            temporaryLoginUntilUtc_ = {};
        }
        if (running_) evaluateSchedule();
        emit controlCompleted(requestId, action,
                              {{QStringLiteral("ok"), true},
                               {QStringLiteral("request_id"), requestId},
                               {QStringLiteral("action"), action},
                               {QStringLiteral("operating_mode"), operatingMode_},
                               {QStringLiteral("schedule_mode"), scheduleMode_},
                               {QStringLiteral("collector_running"), collectorRunning_},
                               {QStringLiteral("postcondition_verified"), false}});
        return;
    }
    if (!running_) {
        emit controlFailed(requestId, action, QStringLiteral("MODULE_STOPPED"),
                           QStringLiteral("Webull 原生模块已停止，请先在 UI 中启动模块"), false);
        return;
    }
    const bool needsBrowser = action == QStringLiteral("collector_start")
        || action == QStringLiteral("restart_browser")
        || action == QStringLiteral("open_login")
        || (action == QStringLiteral("set_schedule_mode")
            && arguments.value(QStringLiteral("mode")).toString()
                   == QStringLiteral("force_running"));
    if (needsBrowser && !liveBrowserEnabled_ && !fixtureMode_) {
        emit controlFailed(requestId, action, QStringLiteral("POLICY_BLOCKED"),
                           QStringLiteral("实时浏览器采集未启用"), false);
        return;
    }
    if (action == QStringLiteral("set_schedule_mode")) {
        const QString mode=arguments.value(QStringLiteral("mode")).toString();
        if (!QSet<QString>{"auto","force_running","force_stopped"}.contains(mode)) error=QStringLiteral("invalid schedule mode");
        else scheduleMode_=mode;
    } else if (action == QStringLiteral("collector_start")) scheduleMode_=QStringLiteral("force_running");
    else if (action == QStringLiteral("collector_stop")) scheduleMode_=QStringLiteral("force_stopped");
    else if (action == QStringLiteral("restart_browser")) {
        if (operatingMode_ == QStringLiteral("weekend_test"))
            scheduleMode_ = QStringLiteral("force_running");
        stopHelper(); startHelper(false);
    }
    else if (action == QStringLiteral("open_login")) {
        // A fresh helper prevents a delayed disconnect from the old headless
        // CDP socket from killing the newly opened visible browser.
        scheduleMode_=QStringLiteral("force_running");
        stopHelper(); startHelper(true);
    }
    else error=QStringLiteral("unsupported native Webull action");
    if (!error.isEmpty()) { emit controlFailed(requestId,action,QStringLiteral("INVALID_ARGUMENT"),error,false); return; }
    evaluateSchedule();
    QJsonObject result{{QStringLiteral("ok"),true},{QStringLiteral("request_id"),requestId},{QStringLiteral("action"),action},
                       {QStringLiteral("schedule_mode"),scheduleMode_},{QStringLiteral("operating_mode"),operatingMode_},
                       {QStringLiteral("collector_running"),collectorRunning_},
                       {QStringLiteral("postcondition_verified"),false},
                       {QStringLiteral("restart_transition_observed"),
                            action == QStringLiteral("restart_browser")},
                       {QStringLiteral("client_control_completed_monotonic_ms"),0}};
    emit controlCompleted(requestId,action,result);
}

void WebullEngine::submitCommand(const QString &action, const QJsonObject &arguments, const QString &commandId)
{
    if (action == QStringLiteral("refresh") || action == QStringLiteral("webull_get_book")) {
        refreshNow(); emit commandFinished(commandId,true,QStringLiteral("native Webull snapshot refreshed"),snapshot()); return;
    }
    QString nativeAction;
    if (action == QStringLiteral("set_operating_mode")) {
        const QString mode = arguments.value(QStringLiteral("mode")).toString();
        if (arguments.size() != 1
            || !QSet<QString>{QStringLiteral("work"), QStringLiteral("weekend_test")}
                    .contains(mode)) {
            emit commandFinished(commandId, false,
                                 QStringLiteral("invalid Webull operating mode"),
                                 {{QStringLiteral("mode"), mode}});
            return;
        }
        nativeAction = QStringLiteral("set_operating_mode");
    }
    else if (action==QStringLiteral("webull_set_mode")) {
        const QString mode = arguments.value(QStringLiteral("mode")).toString();
        if (!QSet<QString>{"auto","force_running","force_stopped"}.contains(mode)) {
            emit commandFinished(commandId,false,QStringLiteral("invalid schedule mode"),{{"mode",mode}}); return;
        }
        nativeAction=QStringLiteral("set_schedule_mode");
    }
    else if(action==QStringLiteral("webull_collector_start")) nativeAction=QStringLiteral("collector_start");
    else if(action==QStringLiteral("webull_collector_stop")) nativeAction=QStringLiteral("collector_stop");
    else if(action==QStringLiteral("webull_restart_browser")) nativeAction=QStringLiteral("restart_browser");
    else if(action==QStringLiteral("webull_show_login")) nativeAction=QStringLiteral("open_login");
    else { emit commandFinished(commandId,false,QStringLiteral("unsupported command"),{{"action",action}}); return; }
    submitControl(commandId,nativeAction,arguments);
    emit commandFinished(commandId,true,QStringLiteral("native Webull command applied"),publicStatus());
}

} // namespace machome::webull
