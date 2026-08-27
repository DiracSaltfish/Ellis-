#pragma once

#include <QDateTime>
#include <QString>

namespace premium {

struct ScheduleState {
    enum class Phase {
        Offline,
        AuctionWarmup,
        MorningWarmup,
        MorningSignals,
        Lunch,
        AfternoonWarmup,
        AfternoonSignals,
        ClosingAuction,
    };

    Phase phase = Phase::Offline;
    bool cnQuotesDesired = false;
    bool hkQuotesDesired = false;
    bool quotesDesired = false;
    bool allow30SecondSignal = false;
    bool allow300SecondSignal = false;
    bool resetWindow = false;
    QString label;
};

class MarketSchedule {
public:
    [[nodiscard]] ScheduleState stateAt(const QDateTime &localTime, bool forceQuotes = false) const;
    [[nodiscard]] static bool isSignalPhase(ScheduleState::Phase phase);
};

} // namespace premium
