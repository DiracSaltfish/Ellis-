#include "common/BridgeFrame.h"

#include <QtEndian>

namespace premium {
namespace {

void appendVarint(QByteArray &out, quint64 value)
{
    while (value >= 0x80) {
        out.append(static_cast<char>((value & 0x7fU) | 0x80U));
        value >>= 7U;
    }
    out.append(static_cast<char>(value));
}

void appendKey(QByteArray &out, quint32 field, quint32 wireType)
{
    appendVarint(out, (static_cast<quint64>(field) << 3U) | wireType);
}

void appendUInt(QByteArray &out, quint32 field, quint64 value)
{
    if (value == 0) {
        return;
    }
    appendKey(out, field, 0);
    appendVarint(out, value);
}

void appendBytes(QByteArray &out, quint32 field, const QByteArray &value)
{
    if (value.isEmpty()) {
        return;
    }
    appendKey(out, field, 2);
    appendVarint(out, static_cast<quint64>(value.size()));
    out.append(value);
}

bool readVarint(const QByteArray &data, qsizetype *offset, quint64 *value)
{
    quint64 result = 0;
    int shift = 0;
    while (*offset < data.size() && shift <= 63) {
        const auto byte = static_cast<quint8>(data.at((*offset)++));
        result |= static_cast<quint64>(byte & 0x7fU) << shift;
        if ((byte & 0x80U) == 0) {
            *value = result;
            return true;
        }
        shift += 7;
    }
    return false;
}

bool readBytes(const QByteArray &data, qsizetype *offset, QByteArray *value)
{
    quint64 length = 0;
    if (!readVarint(data, offset, &length)
        || length > static_cast<quint64>(data.size() - *offset)) {
        return false;
    }
    *value = data.mid(*offset, static_cast<qsizetype>(length));
    *offset += static_cast<qsizetype>(length);
    return true;
}

bool skipField(const QByteArray &data, qsizetype *offset, quint32 wireType)
{
    quint64 ignored = 0;
    switch (wireType) {
    case 0:
        return readVarint(data, offset, &ignored);
    case 1:
        if (data.size() - *offset < 8) return false;
        *offset += 8;
        return true;
    case 2: {
        QByteArray bytes;
        return readBytes(data, offset, &bytes);
    }
    case 5:
        if (data.size() - *offset < 4) return false;
        *offset += 4;
        return true;
    default:
        return false;
    }
}

} // namespace

QByteArray BridgeFrame::encodeProtobuf() const
{
    QByteArray out;
    out.reserve(payloadJson.size() + 128);
    appendUInt(out, 1, static_cast<quint32>(kind));
    appendUInt(out, 2, sequence);
    appendBytes(out, 3, sessionId.toUtf8());
    appendUInt(out, 4, static_cast<quint64>(receiveWallNs));
    appendUInt(out, 5, static_cast<quint64>(receiveMonotonicNs));
    appendUInt(out, 6, isDelta ? 1 : 0);
    appendBytes(out, 7, tag.toUtf8());
    appendBytes(out, 8, payloadJson);
    appendUInt(out, 9, sdkQueueDepth);
    appendBytes(out, 10, message.toUtf8());
    return out;
}

QByteArray BridgeFrame::encodeLengthPrefixed() const
{
    const QByteArray payload = encodeProtobuf();
    QByteArray framed(sizeof(quint32), Qt::Uninitialized);
    qToBigEndian(static_cast<quint32>(payload.size()), framed.data());
    framed.append(payload);
    return framed;
}

bool BridgeFrame::decodeProtobuf(const QByteArray &payload, BridgeFrame *out, QString *error)
{
    if (!out) {
        if (error) *error = QStringLiteral("output pointer is null");
        return false;
    }
    BridgeFrame frame;
    qsizetype offset = 0;
    while (offset < payload.size()) {
        quint64 key = 0;
        if (!readVarint(payload, &offset, &key)) {
            if (error) *error = QStringLiteral("invalid protobuf key varint");
            return false;
        }
        const quint32 field = static_cast<quint32>(key >> 3U);
        const quint32 wireType = static_cast<quint32>(key & 0x07U);
        quint64 number = 0;
        QByteArray bytes;
        switch (field) {
        case 1:
        case 2:
        case 4:
        case 5:
        case 6:
        case 9:
            if (wireType != 0 || !readVarint(payload, &offset, &number)) {
                if (error) *error = QStringLiteral("invalid numeric protobuf field %1").arg(field);
                return false;
            }
            if (field == 1) frame.kind = static_cast<Kind>(number);
            else if (field == 2) frame.sequence = number;
            else if (field == 4) frame.receiveWallNs = static_cast<qint64>(number);
            else if (field == 5) frame.receiveMonotonicNs = static_cast<qint64>(number);
            else if (field == 6) frame.isDelta = number != 0;
            else frame.sdkQueueDepth = static_cast<quint32>(number);
            break;
        case 3:
        case 7:
        case 8:
        case 10:
            if (wireType != 2 || !readBytes(payload, &offset, &bytes)) {
                if (error) *error = QStringLiteral("invalid bytes protobuf field %1").arg(field);
                return false;
            }
            if (field == 3) frame.sessionId = QString::fromUtf8(bytes);
            else if (field == 7) frame.tag = QString::fromUtf8(bytes);
            else if (field == 8) frame.payloadJson = bytes;
            else frame.message = QString::fromUtf8(bytes);
            break;
        default:
            if (!skipField(payload, &offset, wireType)) {
                if (error) *error = QStringLiteral("unsupported or truncated protobuf field %1").arg(field);
                return false;
            }
            break;
        }
    }
    *out = std::move(frame);
    return true;
}

QList<QByteArray> takeLengthPrefixedFrames(QByteArray &buffer, qsizetype maximumFrameBytes)
{
    QList<QByteArray> frames;
    while (buffer.size() >= static_cast<qsizetype>(sizeof(quint32))) {
        const quint32 length = qFromBigEndian<quint32>(buffer.constData());
        if (length > static_cast<quint32>(maximumFrameBytes)) {
            buffer.clear();
            return {};
        }
        const qsizetype total = static_cast<qsizetype>(sizeof(quint32)) + length;
        if (buffer.size() < total) {
            break;
        }
        frames.append(buffer.mid(sizeof(quint32), length));
        buffer.remove(0, total);
    }
    return frames;
}

} // namespace premium

