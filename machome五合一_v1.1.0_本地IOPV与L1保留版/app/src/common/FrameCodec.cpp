#include "common/FrameCodec.h"

#include <QJsonDocument>

namespace hub {

QByteArray FrameCodec::encode(const QJsonObject &object) {
    const QByteArray payload = QJsonDocument(object).toJson(QJsonDocument::Compact);
    const quint32 size = static_cast<quint32>(payload.size());
    QByteArray frame;
    frame.reserve(4 + payload.size());
    frame.append(static_cast<char>((size >> 24) & 0xff));
    frame.append(static_cast<char>((size >> 16) & 0xff));
    frame.append(static_cast<char>((size >> 8) & 0xff));
    frame.append(static_cast<char>(size & 0xff));
    frame.append(payload);
    return frame;
}

bool FrameCodec::consume(QByteArray &buffer, const QByteArray &bytes,
                         QList<QJsonObject> *messages, QString *error,
                         int frameLimitBytes) {
    if (!messages) {
        if (error) *error = QStringLiteral("messages 不能为空");
        return false;
    }
    if (frameLimitBytes <= 0 || buffer.size() > frameLimitBytes + 4) {
        if (error) *error = QStringLiteral("接收缓冲超过限制");
        return false;
    }
    buffer.append(bytes);
    while (buffer.size() >= 4) {
        const auto *raw = reinterpret_cast<const unsigned char *>(buffer.constData());
        const quint32 length = (static_cast<quint32>(raw[0]) << 24) |
                               (static_cast<quint32>(raw[1]) << 16) |
                               (static_cast<quint32>(raw[2]) << 8) |
                               static_cast<quint32>(raw[3]);
        if (length == 0 || length > static_cast<quint32>(frameLimitBytes)) {
            if (error) *error = QStringLiteral("帧长度无效：%1").arg(length);
            return false;
        }
        if (buffer.size() < static_cast<int>(length) + 4) {
            return true;
        }
        QJsonParseError parseError;
        const auto document = QJsonDocument::fromJson(buffer.mid(4, length), &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            if (error) *error = QStringLiteral("帧 JSON 无效：%1").arg(parseError.errorString());
            return false;
        }
        messages->push_back(document.object());
        buffer.remove(0, 4 + static_cast<int>(length));
    }
    // A single read can legitimately contain many complete frames whose total
    // size exceeds the per-frame limit. Only an unfinished frame is bounded.
    if (buffer.size() > frameLimitBytes + 4) {
        if (error) *error = QStringLiteral("接收缓冲超过限制");
        return false;
    }
    return true;
}

} // namespace hub
