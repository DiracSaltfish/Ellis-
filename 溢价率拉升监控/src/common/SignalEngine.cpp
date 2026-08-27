#include "common/SignalEngine.h"

#include <algorithm>
#include <limits>

namespace premium {
namespace {

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

qint64 minimumBidInWindow(const QList<SignalEngine::Point> &points, qint64 cutoffMs,
                          bool historyComplete, bool *found)
{
    qint64 minimum = std::numeric_limits<qint64>::max();
    int count = 0;
    for (const auto &point : points) {
        if (point.timeMs >= cutoffMs && point.bid1PriceE6 > 0) {
            minimum = std::min(minimum, point.bid1PriceE6);
            ++count;
        }
    }
    *found = historyComplete && count >= 2;
    return *found ? minimum : 0;
}

bool onlyMissingIopv(const QStringList &issues)
{
    return !issues.isEmpty() && std::all_of(issues.cbegin(), issues.cend(), [](const QString &issue) {
        return issue == QStringLiteral("iopv_non_positive");
    });
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
    state.points.append({nowMs, snapshot.sellPremiumPpm, snapshot.bidPricesE6[0]});
    while (!state.points.isEmpty() && state.points.front().timeMs < nowMs - 300'000) {
        state.points.removeFirst();
    }

    bool found30 = false;
    bool found300 = false;
    const qint64 min30 = minimumInWindow(state.points, nowMs - 30'000, &found30);
    const qint64 min300 = minimumInWindow(state.points, nowMs - 300'000, &found300);
    decision.rise30sPpm = found30 ? snapshot.sellPremiumPpm - min30 : 0;
    decision.rise300sPpm = found300 ? snapshot.sellPremiumPpm - min300 : 0;

    bool foundBid150 = false;
    bool foundBid300 = false;
    const qint64 minBid150 = minimumBidInWindow(state.points, nowMs - 150'000,
                                                 nowMs - state.historyStartMs >= 150'000, &foundBid150);
    const qint64 minBid300 = minimumBidInWindow(state.points, nowMs - 300'000,
                                                 nowMs - state.historyStartMs >= 300'000, &foundBid300);
    snapshot.bidRise150sPpm = foundBid150 ? calculateRatioPpm(snapshot.bidPricesE6[0], minBid150) : 0;
    snapshot.bidRise300sPpm = foundBid300 ? calculateRatioPpm(snapshot.bidPricesE6[0], minBid300) : 0;

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
    const bool triggerPull150 = allow30Seconds && foundBid150 && snapshot.bidRise150sPpm > 4'000;
    const bool triggerPull300 = allow300Seconds && foundBid300 && snapshot.bidRise300sPpm > 8'000;
    const bool pullGate = !hasUsableIopv || snapshot.sellPremiumPpm > 6'000;
    const bool pullCondition = pullGate && (triggerPull150 || triggerPull300);

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

    if (premiumAlert || pullAlert) {
        if (premiumAlert) {
            state.premiumLastAlertPpm = snapshot.sellPremiumPpm;
            state.premiumLastAlertMs = nowMs;
        }
        if (pullAlert) {
            state.pullLastAlertRisePpm = pullRise;
            state.pullLastAlertMs = nowMs;
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
        event.model = premiumAlert && pullAlert ? QStringLiteral("premium+pull")
                    : premiumAlert ? QStringLiteral("premium") : QStringLiteral("pull");
        QStringList reasons;
        if (premiumAlert) {
            reasons.append(trigger30 && trigger300 ? QStringLiteral("30s+5m")
                           : trigger30 ? QStringLiteral("30s") : QStringLiteral("5m"));
        }
        if (pullAlert) {
            reasons.append(triggerPull150 && triggerPull300 ? QStringLiteral("盘口150s+300s")
                           : triggerPull150 ? QStringLiteral("盘口150s") : QStringLiteral("盘口300s"));
        }
        event.reason = reasons.join(u'+');
        event.repeat = (!premiumAlert || premiumRepeat) && (!pullAlert || pullRepeat);
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
