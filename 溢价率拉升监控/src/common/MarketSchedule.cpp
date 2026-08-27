#include "common/MarketSchedule.h"

namespace premium {

ScheduleState MarketSchedule::stateAt(const QDateTime &localTime, bool forceQuotes) const
{
    ScheduleState state;
    const int weekday = localTime.date().dayOfWeek();
    const QTime time = localTime.time();
    if (weekday > 5) {
        state.quotesDesired = forceQuotes;
        state.label = forceQuotes ? QStringLiteral("周末人工行情") : QStringLiteral("周末停机");
        return state;
    }

    if (time >= QTime(9, 15) && time < QTime(9, 30)) {
        state.phase = ScheduleState::Phase::AuctionWarmup;
        state.quotesDesired = true;
        state.label = QStringLiteral("集合竞价预热");
    } else if (time >= QTime(9, 30) && time < QTime(9, 31)) {
        state.phase = ScheduleState::Phase::MorningWarmup;
        state.quotesDesired = true;
        state.resetWindow = time.second() == 0;
        state.label = QStringLiteral("上午连续竞价预热");
    } else if (time >= QTime(9, 31) && time < QTime(11, 30)) {
        state.phase = ScheduleState::Phase::MorningSignals;
        state.quotesDesired = true;
        state.allow30SecondSignal = true;
        state.allow300SecondSignal = time >= QTime(9, 35);
        state.label = QStringLiteral("上午信号监控");
    } else if (time >= QTime(11, 30) && time < QTime(13, 0)) {
        state.phase = ScheduleState::Phase::Lunch;
        state.quotesDesired = true;
        state.label = QStringLiteral("午间仅记录");
    } else if (time >= QTime(13, 0) && time < QTime(13, 1)) {
        state.phase = ScheduleState::Phase::AfternoonWarmup;
        state.quotesDesired = true;
        state.resetWindow = time.second() == 0;
        state.label = QStringLiteral("下午连续竞价预热");
    } else if (time >= QTime(13, 1) && time < QTime(14, 57)) {
        state.phase = ScheduleState::Phase::AfternoonSignals;
        state.quotesDesired = true;
        state.allow30SecondSignal = true;
        state.allow300SecondSignal = time >= QTime(13, 5);
        state.label = QStringLiteral("下午信号监控");
    } else if (time >= QTime(14, 57) && time < QTime(15, 0)) {
        state.phase = ScheduleState::Phase::ClosingAuction;
        state.quotesDesired = true;
        state.label = QStringLiteral("收盘集合竞价仅记录");
    } else {
        state.quotesDesired = forceQuotes;
        state.label = forceQuotes ? QStringLiteral("盘外人工行情") : QStringLiteral("盘外驻留");
    }
    return state;
}

bool MarketSchedule::isSignalPhase(ScheduleState::Phase phase)
{
    return phase == ScheduleState::Phase::MorningSignals
        || phase == ScheduleState::Phase::AfternoonSignals;
}

} // namespace premium

