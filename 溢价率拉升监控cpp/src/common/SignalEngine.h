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
        qint64 lastPriceE6 = 0;
    };

    struct MinuteBar {
        qint64 startMs = 0;
        qint64 openE6 = 0;
        qint64 highE6 = 0;
        qint64 lowE6 = 0;
        qint64 closeE6 = 0;
    };

    struct MinuteMetrics {
        qint64 startMs = 0;
        qint64 up3Ppm = 0;
        qint64 up5Ppm = 0;
        qint64 rangePpm = 0;
        bool hasUp3 = false;
        bool hasUp5 = false;
        bool hasRange = false;
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
        QList<MinuteBar> completedMinutes;
        QList<MinuteMetrics> completedMinuteMetrics;
        MinuteBar currentMinute;
        bool hasCurrentMinute = false;
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
        bool radarActive = false;
        qint64 radarLastAlertStrengthPpm = 0;
        qint64 radarLastAlertMs = 0;
        qint64 radarBelowResetSinceMs = 0;
    };

    QHash<QString, State> states_;
    quint64 signalSequence_ = 0;
};

} // namespace premium
