#pragma once
#include "engine_contract.hpp"
namespace etf_l2 {
struct LoadedDay {
    Partition partition;
    std::vector<OrderEvent> orders;
    std::vector<TradeEvent> trades;
    std::vector<QuoteSnapshot> quotes;
    std::uint64_t placeholder_rows{}, suffix_corrections{}, status_rows{};
    double parse_seconds{};
};
Time parse_cutoff(const std::string& hhmm);
LoadedDay load_wind_csv(const std::string& trade_file,const std::string& order_file,
                       const std::string& quote_file,const Partition&,const EngineConfig&);
void write_result(const DayResult&,const std::string& output_directory);
} // namespace etf_l2
