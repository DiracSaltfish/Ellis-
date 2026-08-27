#pragma once

#include "common/MarketTypes.h"

#include <QHash>
#include <QList>
#include <optional>

namespace premium {

struct SignalDecision {
    QuoteSnapshot snapshot;
    std::optional<SignalEvent> event;
    qint64 rise30sPpm = 0;
    qint64 rise300sPpm = 0;
};

class SignalEngine {
public:
    struct Point {
        qint64 timeMs = 0;
        qint64 premiumPpm = 0;
        qint64 bid1PriceE6 = 0;
    };

    SignalDecision evaluate(QuoteSnapshot snapshot,
                            const QDateTime &now,
                            bool allow30Seconds,
                            bool allow300Seconds);
    void resetAll();
    void resetSymbol(const QString &symbol);

private:
    struct State {
        QList<Point> points;
        qint64 historyStartMs = 0;
        qint64 lastIopvE6 = 0;
        qint64 lastIopvChangeMs = 0;
        int framesSinceIopvChange = 0;
        bool premiumActive = false;
        qint64 premiumLastAlertPpm = 0;
        qint64 premiumLastAlertMs = 0;
        qint64 premiumBelowResetSinceMs = 0;
        bool pullActive = false;
        qint64 pullLastAlertRisePpm = 0;
        qint64 pullLastAlertMs = 0;
        qint64 pullBelowResetSinceMs = 0;
    };

    QHash<QString, State> states_;
    quint64 signalSequence_ = 0;
};

} // namespace premium
