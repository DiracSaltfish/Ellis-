#include "common/SignalEngine.h"

#include <algorithm>
#include <limits>

namespace premium {
namespace {

constexpr qint64 MinuteMs = 60'000;
constexpr int MinuteHistoryLimit = 130;

qint64 minimumInWindow(const QList<SignalEngine::Point> &points, qint64 cutoffMs, bool *found)
{
    qint64 minimum = std::numeric_limits<qint64>::max();
    int count = 0;
    for (const auto &point : points) {
        if (point.timeMs >= cutoffMs) {
            minimum = std::min(minimum, point.premiumPpm);
            ++count;
        }
    }
    *found = count >= 2;
    return *found ? minimum : 0;
}

struct BidWindowStats {
    qint64 minimumE6 = 0;
    qint64 maximumE6 = 0;
    qint64 firstTimeMs = 0;
    qint64 lastTimeMs = 0;
    qint64 maximumGapMs = 0;
    int count = 0;
    int upwardMoves = 0;
};

BidWindowStats bidWindowStats(const QList<SignalEngine::Point> &points, qint64 cutoffMs)
{
    BidWindowStats result;
    qint64 previous = 0;
    result.minimumE6 = std::numeric_limits<qint64>::max();
    for (const auto &point : points) {
        if (point.timeMs < cutoffMs || point.bid1PriceE6 <= 0) continue;
        result.minimumE6 = std::min(result.minimumE6, point.bid1PriceE6);
        result.maximumE6 = std::max(result.maximumE6, point.bid1PriceE6);
        if (result.firstTimeMs == 0) result.firstTimeMs = point.timeMs;
        if (result.lastTimeMs > 0) {
            result.maximumGapMs = std::max(result.maximumGapMs, point.timeMs - result.lastTimeMs);
        }
        result.lastTimeMs = point.timeMs;
        if (previous > 0 && point.bid1PriceE6 > previous) ++result.upwardMoves;
        previous = point.bid1PriceE6;
        ++result.count;
    }
    if (result.count == 0) result.minimumE6 = 0;
    return result;
}

bool hasCompleteBidWindow(const BidWindowStats &stats, qint64 nowMs, qint64 windowMs)
{
    constexpr qint64 TickCoverageToleranceMs = 10'000;
    return stats.count >= 2
        && stats.firstTimeMs <= nowMs - windowMs + TickCoverageToleranceMs
        && stats.lastTimeMs >= nowMs - TickCoverageToleranceMs
        && stats.maximumGapMs <= TickCoverageToleranceMs;
}

bool onlyMissingIopv(const QStringList &issues)
{
    return !issues.isEmpty() && std::all_of(issues.cbegin(), issues.cend(), [](const QString &issue) {
        return issue == QStringLiteral("iopv_non_positive");
    });
}

const SignalEngine::MinuteBar *barAt(const QList<SignalEngine::MinuteBar> &bars, qint64 startMs)
{
    for (auto it = bars.crbegin(); it != bars.crend(); ++it) {
        if (it->startMs == startMs) return &*it;
        if (it->startMs < startMs) break;
    }
    return nullptr;
}

SignalEngine::MinuteMetrics metricsForBar(const SignalEngine::MinuteBar &bar,
                                          const QList<SignalEngine::MinuteBar> &completed)
{
    SignalEngine::MinuteMetrics metrics;
    metrics.startMs = bar.startMs;
    const auto *previous1 = barAt(completed, bar.startMs - MinuteMs);
    const auto *previous2 = barAt(completed, bar.startMs - 2 * MinuteMs);
    const auto *previous3 = barAt(completed, bar.startMs - 3 * MinuteMs);
    const auto *previous4 = barAt(completed, bar.startMs - 4 * MinuteMs);
    const auto *previous5 = barAt(completed, bar.startMs - 5 * MinuteMs);
    if (previous1 && previous2 && previous3) {
        const qint64 minimum = std::min({previous1->closeE6, previous2->closeE6, previous3->closeE6});
        metrics.up3Ppm = calculateRatioPpm(bar.closeE6, minimum);
        metrics.hasUp3 = minimum > 0;
    }
    if (previous1 && previous2 && previous3 && previous4 && previous5) {
        const qint64 minimum = std::min({previous1->closeE6, previous2->closeE6, previous3->closeE6,
                                         previous4->closeE6, previous5->closeE6});
        metrics.up5Ppm = calculateRatioPpm(bar.closeE6, minimum);
        metrics.hasUp5 = minimum > 0;
    }
    if (previous1 && previous1->closeE6 > 0 && bar.highE6 >= bar.lowE6) {
        metrics.rangePpm = calculateRatioPpm(previous1->closeE6 + (bar.highE6 - bar.lowE6),
                                             previous1->closeE6);
        metrics.hasRange = true;
    }
    return metrics;
}

qint64 medianMetric(const QList<SignalEngine::MinuteMetrics> &history,
                    qint64 SignalEngine::MinuteMetrics::*member,
                    bool SignalEngine::MinuteMetrics::*valid)
{
    QList<qint64> values;
    values.reserve(std::min<qsizetype>(120, history.size()));
    const qsizetype first = std::max<qsizetype>(0, history.size() - 120);
    for (int index = first; index < history.size(); ++index) {
        if (history.at(index).*valid) values.append(history.at(index).*member);
    }
    if (values.isEmpty()) return 0;
    std::sort(values.begin(), values.end());
    const int middle = values.size() / 2;
    if ((values.size() & 1) != 0) return values.at(middle);
    return (values.at(middle - 1) + values.at(middle)) / 2;
}

void updateMinuteState(QList<SignalEngine::MinuteBar> &completed,
                       QList<SignalEngine::MinuteMetrics> &completedMetrics,
                       SignalEngine::MinuteBar &current, bool &hasCurrent,
                       qint64 nowMs, qint64 priceE6)
{
    if (priceE6 <= 0) return;
    const qint64 minuteStart = (nowMs / MinuteMs) * MinuteMs;
    if (!hasCurrent) {
        current = {minuteStart, priceE6, priceE6, priceE6, priceE6};
        hasCurrent = true;
        return;
    }
    if (minuteStart < current.startMs) return;
    if (minuteStart > current.startMs) {
        completedMetrics.append(metricsForBar(current, completed));
        completed.append(current);
        while (completed.size() > MinuteHistoryLimit) completed.removeFirst();
        while (completedMetrics.size() > MinuteHistoryLimit) completedMetrics.removeFirst();
        current = {minuteStart, priceE6, priceE6, priceE6, priceE6};
        return;
    }
    current.highE6 = std::max(current.highE6, priceE6);
    current.lowE6 = std::min(current.lowE6, priceE6);
    current.closeE6 = priceE6;
}

} // namespace

SignalDecision SignalEngine::evaluate(QuoteSnapshot snapshot,
                                      const QDateTime &now,
                                      bool allow30Seconds,
                                      bool allow300Seconds)
{
    SignalDecision decision;
    State &state = states_[snapshot.symbol];
    const qint64 nowMs = now.toMSecsSinceEpoch();

    snapshot.sellPremiumPpm = calculateRatioPpm(snapshot.bidPricesE6[0], snapshot.iopvE6);
    snapshot.displayPremiumPpm = calculateRatioPpm(snapshot.lastPriceE6, snapshot.iopvE6);
    snapshot.changePpm = calculateRatioPpm(snapshot.lastPriceE6, snapshot.preClosePriceE6);

    if (snapshot.iopvE6 > 0) {
        if (state.lastIopvE6 != snapshot.iopvE6) {
            state.lastIopvE6 = snapshot.iopvE6;
            state.lastIopvChangeMs = nowMs;
            state.framesSinceIopvChange = 1;
        } else {
            ++state.framesSinceIopvChange;
        }
        snapshot.iopvStatic = state.framesSinceIopvChange >= 5
                           && state.lastIopvChangeMs > 0
                           && nowMs - state.lastIopvChangeMs >= 120'000;
    }

    const bool hasUsableIopv = snapshot.iopvE6 > 0 && snapshot.qualityIssues.isEmpty();
    const bool validBook = snapshot.sourceReady && snapshot.bidPricesE6[0] > 0
                        && (snapshot.qualityIssues.isEmpty()
                            || (snapshot.iopvE6 <= 0 && onlyMissingIopv(snapshot.qualityIssues)));
    if (!validBook) {
        decision.snapshot = std::move(snapshot);
        return decision;
    }

    if (state.historyStartMs == 0) state.historyStartMs = nowMs;
    const Point point{nowMs, snapshot.sellPremiumPpm, snapshot.bidPricesE6[0], snapshot.lastPriceE6};
    if (state.points.isEmpty() || nowMs - state.points.back().timeMs >= 1'000) {
        state.points.append(point);
    } else {
        state.points.back() = point;
    }
    while (!state.points.isEmpty() && state.points.front().timeMs < nowMs - 360'000) {
        state.points.removeFirst();
    }

    updateMinuteState(state.completedMinutes, state.completedMinuteMetrics,
                      state.currentMinute, state.hasCurrentMinute, nowMs, snapshot.lastPriceE6);

    bool found30 = false;
    bool found300 = false;
    const qint64 min30 = minimumInWindow(state.points, nowMs - 30'000, &found30);
    const qint64 min300 = minimumInWindow(state.points, nowMs - 300'000, &found300);
    decision.rise30sPpm = found30 ? snapshot.sellPremiumPpm - min30 : 0;
    decision.rise300sPpm = found300 ? snapshot.sellPremiumPpm - min300 : 0;

    const BidWindowStats bid30 = bidWindowStats(state.points, nowMs - 30'000);
    const BidWindowStats bid60 = bidWindowStats(state.points, nowMs - 60'000);
    const BidWindowStats bid90 = bidWindowStats(state.points, nowMs - 90'000);
    const BidWindowStats bid150 = bidWindowStats(state.points, nowMs - 150'000);
    const BidWindowStats bid300 = bidWindowStats(state.points, nowMs - 300'000);
    const bool complete30 = nowMs - state.historyStartMs >= 30'000
                         && hasCompleteBidWindow(bid30, nowMs, 30'000);
    const bool complete60 = nowMs - state.historyStartMs >= 60'000
                         && hasCompleteBidWindow(bid60, nowMs, 60'000);
    const bool complete90 = nowMs - state.historyStartMs >= 90'000
                         && hasCompleteBidWindow(bid90, nowMs, 90'000);
    const bool complete150 = nowMs - state.historyStartMs >= 150'000 && bid150.count >= 2;
    const bool complete300 = nowMs - state.historyStartMs >= 300'000 && bid300.count >= 2;
    snapshot.bidRise30sPpm = complete30 ? calculateRatioPpm(snapshot.bidPricesE6[0], bid30.minimumE6) : 0;
    snapshot.bidRise60sPpm = complete60 ? calculateRatioPpm(snapshot.bidPricesE6[0], bid60.minimumE6) : 0;
    snapshot.bidRise90sPpm = complete90 ? calculateRatioPpm(snapshot.bidPricesE6[0], bid90.minimumE6) : 0;
    snapshot.bidRise150sPpm = complete150 ? calculateRatioPpm(snapshot.bidPricesE6[0], bid150.minimumE6) : 0;
    snapshot.bidRise300sPpm = complete300 ? calculateRatioPpm(snapshot.bidPricesE6[0], bid300.minimumE6) : 0;

    MinuteMetrics currentMetrics;
    if (state.hasCurrentMinute) currentMetrics = metricsForBar(state.currentMinute, state.completedMinutes);
    const qint64 currentMinuteStart = state.hasCurrentMinute ? state.currentMinute.startMs : 0;
    const auto *minute3 = currentMinuteStart > 0 ? barAt(state.completedMinutes, currentMinuteStart - 3 * MinuteMs) : nullptr;
    const auto *minute5 = currentMinuteStart > 0 ? barAt(state.completedMinutes, currentMinuteStart - 5 * MinuteMs) : nullptr;
    if (minute3 && minute3->closeE6 > 0) snapshot.momentum3mPpm = calculateRatioPpm(snapshot.lastPriceE6, minute3->closeE6);
    if (minute5 && minute5->closeE6 > 0) snapshot.momentum5mPpm = calculateRatioPpm(snapshot.lastPriceE6, minute5->closeE6);
    snapshot.adaptive3mPpm = currentMetrics.hasUp3 ? currentMetrics.up3Ppm : 0;
    snapshot.adaptive5mPpm = currentMetrics.hasUp5 ? currentMetrics.up5Ppm : 0;
    snapshot.minuteRangePpm = currentMetrics.hasRange ? currentMetrics.rangePpm : 0;
    snapshot.minuteRangeBasePpm = medianMetric(state.completedMinuteMetrics,
                                               &MinuteMetrics::rangePpm, &MinuteMetrics::hasRange);
    const qint64 adaptive3Base = medianMetric(state.completedMinuteMetrics,
                                              &MinuteMetrics::up3Ppm, &MinuteMetrics::hasUp3);
    const qint64 adaptive5Base = medianMetric(state.completedMinuteMetrics,
                                              &MinuteMetrics::up5Ppm, &MinuteMetrics::hasUp5);

    if (state.premiumActive) {
        if (snapshot.sellPremiumPpm < 13'000) {
            if (state.premiumBelowResetSinceMs == 0) state.premiumBelowResetSinceMs = nowMs;
            if (nowMs - state.premiumBelowResetSinceMs >= 30'000) {
                state.premiumActive = false;
                state.premiumLastAlertPpm = 0;
                state.premiumLastAlertMs = 0;
                state.premiumBelowResetSinceMs = 0;
            }
        } else {
            state.premiumBelowResetSinceMs = 0;
        }
    }

    const bool trigger30 = allow30Seconds && found30 && decision.rise30sPpm >= 2'000;
    const bool trigger300 = allow300Seconds && found300 && decision.rise300sPpm >= 10'000;
    const bool premiumCondition = hasUsableIopv && snapshot.sellPremiumPpm > 15'000 && (trigger30 || trigger300);
    const bool triggerPull150 = allow30Seconds && complete150 && snapshot.bidRise150sPpm > 4'000;
    const bool triggerPull300 = allow300Seconds && complete300 && snapshot.bidRise300sPpm > 8'000;
    const bool pullGate = !hasUsableIopv || snapshot.sellPremiumPpm > 6'000;
    const bool pullCondition = pullGate && (triggerPull150 || triggerPull300);

    const bool triggerMomentum3 = allow30Seconds && minute3 && snapshot.momentum3mPpm >= 3'000;
    const bool triggerMomentum5 = allow300Seconds && minute5 && snapshot.momentum5mPpm >= 5'000;
    const qint64 adaptive3Threshold = std::max<qint64>(1'500, adaptive3Base * 5 / 2);
    const qint64 adaptive5Threshold = std::max<qint64>(2'500, adaptive5Base * 11 / 5);
    const bool triggerAdaptive3 = allow30Seconds && currentMetrics.hasUp3
                               && snapshot.adaptive3mPpm >= adaptive3Threshold;
    const bool triggerAdaptive5 = allow300Seconds && currentMetrics.hasUp5
                               && snapshot.adaptive5mPpm >= adaptive5Threshold;
    const qint64 rangeThreshold = std::max<qint64>(1'500, snapshot.minuteRangeBasePpm * 2);
    const auto *previousMinute = currentMinuteStart > 0
                               ? barAt(state.completedMinutes, currentMinuteStart - MinuteMs) : nullptr;
    const bool triggerRange = allow30Seconds && currentMetrics.hasRange && previousMinute
                           && snapshot.lastPriceE6 > previousMinute->closeE6
                           && snapshot.minuteRangePpm >= rangeThreshold;
    const bool triggerFast30 = allow30Seconds && complete30 && bid30.upwardMoves >= 2
                            && snapshot.bidPricesE6[0] >= bid30.maximumE6
                            && snapshot.bidRise30sPpm >= 1'500;
    const bool triggerFast60 = allow30Seconds && complete60 && bid60.upwardMoves >= 2
                            && snapshot.bidPricesE6[0] >= bid60.maximumE6
                            && snapshot.bidRise60sPpm >= 2'000;
    const bool triggerFast90 = allow30Seconds && complete90 && bid90.upwardMoves >= 2
                            && snapshot.bidPricesE6[0] >= bid90.maximumE6
                            && snapshot.bidRise90sPpm >= 2'500;
    const bool radarTrigger = triggerMomentum3 || triggerMomentum5 || triggerAdaptive3 || triggerAdaptive5
                           || triggerRange || triggerFast30 || triggerFast60 || triggerFast90;
    const bool radarCondition = hasUsableIopv && snapshot.sellPremiumPpm > 6'000 && radarTrigger;

    if (state.pullActive) {
        if (!pullCondition) {
            if (state.pullBelowResetSinceMs == 0) state.pullBelowResetSinceMs = nowMs;
            if (nowMs - state.pullBelowResetSinceMs >= 30'000) {
                state.pullActive = false;
                state.pullLastAlertRisePpm = 0;
                state.pullLastAlertMs = 0;
                state.pullBelowResetSinceMs = 0;
            }
        } else {
            state.pullBelowResetSinceMs = 0;
        }
    }
    if (state.radarActive) {
        if (!radarCondition) {
            if (state.radarBelowResetSinceMs == 0) state.radarBelowResetSinceMs = nowMs;
            if (nowMs - state.radarBelowResetSinceMs >= 30'000) {
                state.radarActive = false;
                state.radarLastAlertStrengthPpm = 0;
                state.radarLastAlertMs = 0;
                state.radarBelowResetSinceMs = 0;
            }
        } else {
            state.radarBelowResetSinceMs = 0;
        }
    }

    bool premiumAlert = false;
    bool premiumRepeat = false;
    if (premiumCondition && !state.premiumActive) {
        state.premiumActive = true;
        premiumAlert = true;
    } else if (premiumCondition && state.premiumActive) {
        const bool largeNewHigh = snapshot.sellPremiumPpm - state.premiumLastAlertPpm >= 2'000;
        const bool timedNewHigh = nowMs - state.premiumLastAlertMs >= 60'000
                               && snapshot.sellPremiumPpm > state.premiumLastAlertPpm;
        premiumAlert = largeNewHigh || timedNewHigh;
        premiumRepeat = premiumAlert;
    }

    const qint64 pullRise = std::max(snapshot.bidRise150sPpm, snapshot.bidRise300sPpm);
    bool pullAlert = false;
    bool pullRepeat = false;
    if (pullCondition && !state.pullActive) {
        state.pullActive = true;
        pullAlert = true;
    } else if (pullCondition && state.pullActive) {
        const bool largeNewHigh = pullRise - state.pullLastAlertRisePpm >= 2'000;
        const bool timedNewHigh = nowMs - state.pullLastAlertMs >= 60'000
                               && pullRise > state.pullLastAlertRisePpm;
        pullAlert = largeNewHigh || timedNewHigh;
        pullRepeat = pullAlert;
    }

    const qint64 radarStrength = std::max({snapshot.momentum3mPpm, snapshot.momentum5mPpm,
                                           snapshot.adaptive3mPpm, snapshot.adaptive5mPpm,
                                           snapshot.minuteRangePpm, snapshot.bidRise30sPpm,
                                           snapshot.bidRise60sPpm, snapshot.bidRise90sPpm});
    bool radarAlert = false;
    bool radarRepeat = false;
    if (radarCondition && !state.radarActive) {
        state.radarActive = true;
        radarAlert = true;
    } else if (radarCondition && state.radarActive) {
        const bool largeNewHigh = radarStrength - state.radarLastAlertStrengthPpm >= 2'000;
        const bool timedNewHigh = nowMs - state.radarLastAlertMs >= 60'000
                               && radarStrength > state.radarLastAlertStrengthPpm;
        radarAlert = largeNewHigh || timedNewHigh;
        radarRepeat = radarAlert;
    }

    if (premiumAlert || pullAlert || radarAlert) {
        if (premiumAlert) {
            state.premiumLastAlertPpm = snapshot.sellPremiumPpm;
            state.premiumLastAlertMs = nowMs;
        }
        if (pullAlert) {
            state.pullLastAlertRisePpm = pullRise;
            state.pullLastAlertMs = nowMs;
        }
        if (radarAlert) {
            state.radarLastAlertStrengthPpm = radarStrength;
            state.radarLastAlertMs = nowMs;
        }
        SignalEvent event;
        event.sequence = ++signalSequence_;
        event.symbol = snapshot.symbol;
        event.occurredAt = now;
        event.premiumPpm = snapshot.sellPremiumPpm;
        event.rise30sPpm = decision.rise30sPpm;
        event.rise300sPpm = decision.rise300sPpm;
        event.bidRise150sPpm = snapshot.bidRise150sPpm;
        event.bidRise300sPpm = snapshot.bidRise300sPpm;
        event.bidRise30sPpm = snapshot.bidRise30sPpm;
        event.bidRise60sPpm = snapshot.bidRise60sPpm;
        event.bidRise90sPpm = snapshot.bidRise90sPpm;
        event.momentum3mPpm = snapshot.momentum3mPpm;
        event.momentum5mPpm = snapshot.momentum5mPpm;
        event.adaptive3mPpm = snapshot.adaptive3mPpm;
        event.adaptive5mPpm = snapshot.adaptive5mPpm;
        event.minuteRangePpm = snapshot.minuteRangePpm;
        event.minuteRangeBasePpm = snapshot.minuteRangeBasePpm;
        QStringList models;
        if (premiumAlert) models.append(QStringLiteral("premium"));
        if (pullAlert) models.append(QStringLiteral("pull"));
        if (radarAlert) models.append(QStringLiteral("radar"));
        event.model = models.join(u'+');
        QStringList reasons;
        if (premiumAlert) {
            reasons.append(trigger30 && trigger300 ? QStringLiteral("30s+5m")
                           : trigger30 ? QStringLiteral("30s") : QStringLiteral("5m"));
        }
        if (pullAlert) {
            reasons.append(triggerPull150 && triggerPull300 ? QStringLiteral("盘口150s+300s")
                           : triggerPull150 ? QStringLiteral("盘口150s") : QStringLiteral("盘口300s"));
        }
        if (radarAlert) {
            if (triggerMomentum3) reasons.append(QStringLiteral("M3"));
            if (triggerMomentum5) reasons.append(QStringLiteral("M5"));
            if (triggerAdaptive3) reasons.append(QStringLiteral("A3"));
            if (triggerAdaptive5) reasons.append(QStringLiteral("A5"));
            if (triggerRange) reasons.append(QStringLiteral("RANGE_UP"));
            if (triggerFast30) reasons.append(QStringLiteral("T30"));
            if (triggerFast60) reasons.append(QStringLiteral("T60"));
            if (triggerFast90) reasons.append(QStringLiteral("T90"));
        }
        event.reason = reasons.join(u'+');
        event.repeat = (!premiumAlert || premiumRepeat)
                    && (!pullAlert || pullRepeat)
                    && (!radarAlert || radarRepeat);
        event.replay = snapshot.replay;
        decision.event = event;
    }

    decision.snapshot = std::move(snapshot);
    return decision;
}

void SignalEngine::resetAll()
{
    states_.clear();
}

void SignalEngine::resetSymbol(const QString &symbol)
{
    states_.remove(symbol);
}

} // namespace premium
