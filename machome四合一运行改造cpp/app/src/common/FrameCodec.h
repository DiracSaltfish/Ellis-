#pragma once

#include <QByteArray>
#include <QJsonObject>
#include <QList>

namespace hub {

class FrameCodec final {
public:
    static QByteArray encode(const QJsonObject &object);
    static bool consume(QByteArray &buffer, const QByteArray &bytes,
                        QList<QJsonObject> *messages, QString *error = nullptr,
                        int frameLimitBytes = 1024 * 1024);
};

} // namespace hub

