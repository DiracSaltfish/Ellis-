#pragma once

#include <QByteArray>
#include <QList>
#include <QString>

namespace premium {

struct BridgeFrame {
    enum class Kind : quint32 {
        Unspecified = 0,
        MarketEvent = 1,
        AdapterStatus = 2,
        Control = 3,
        ControlResult = 4,
    };

    Kind kind = Kind::Unspecified;
    quint64 sequence = 0;
    QString sessionId;
    qint64 receiveWallNs = 0;
    qint64 receiveMonotonicNs = 0;
    bool isDelta = false;
    QString tag;
    QByteArray payloadJson;
    quint32 sdkQueueDepth = 0;
    QString message;

    [[nodiscard]] QByteArray encodeProtobuf() const;
    [[nodiscard]] QByteArray encodeLengthPrefixed() const;
    static bool decodeProtobuf(const QByteArray &payload, BridgeFrame *out, QString *error = nullptr);
};

// Extracts all complete big-endian uint32 length-prefixed protobuf frames and
// leaves an incomplete tail in buffer.
QList<QByteArray> takeLengthPrefixedFrames(QByteArray &buffer, qsizetype maximumFrameBytes = 64 * 1024 * 1024);

} // namespace premium
