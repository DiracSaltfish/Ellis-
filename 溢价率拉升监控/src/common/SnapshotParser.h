#pragma once

#include "common/BridgeFrame.h"
#include "common/MarketTypes.h"

#include <QHash>
#include <QJsonObject>
#include <optional>

namespace premium {

struct ParseResult {
    std::optional<QuoteSnapshot> snapshot;
    QStringList issues;
    QString symbol;
    bool waitingForFull = false;
};

class SnapshotParser {
public:
    ParseResult consume(const BridgeFrame &frame, bool replay = false);
    void resetSession(const QString &sessionId = {});
    void resetSymbol(const QString &symbol);
    [[nodiscard]] int readySymbolCount() const;

private:
    struct RawState {
        QJsonObject data;
        QString sessionId;
        QString symbol;
        bool hasFull = false;
    };

    static QString stringValue(const QJsonObject &object, const QString &named, const QString &numeric);
    static bool integerValue(const QJsonObject &object, const QString &named, const QString &numeric, qint64 *out);
    static bool integerArray(const QJsonObject &object, const QString &named, const QString &numeric,
                             std::array<qint64, 10> *out, QStringList *issues, int expectedLevels = 10);
    static QString extractSymbol(const QJsonObject &data);
    static QString canonicalSymbol(const QJsonObject &data, const QString &tag);
    static QString marketFromRaw(const QJsonObject &data, const QString &symbol);
    static QJsonObject mergedObject(QJsonObject base, const QJsonObject &delta);
    ParseResult mapSnapshot(const BridgeFrame &frame, const QJsonObject &data, bool replay) const;
    ParseResult mapHktSnapshot(const BridgeFrame &frame, const QJsonObject &data, bool replay) const;

    QHash<QString, RawState> states_;
    QString activeSession_;
};

} // namespace premium
