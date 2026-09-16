#pragma once
// v1: deterministic full-day observed-trade ledger. No synthetic executions.
#include <cstdint>
#include <span>
#include <string>
#include <vector>
#include <type_traits>

namespace etf_l2 {
using Quantity = std::int64_t;        // ETF shares, never lots
using Price = std::int64_t;           // CNY * 10,000
using Time = std::int64_t;            // microseconds since local midnight
using OrderId = std::uint64_t;

enum class Market : std::uint8_t { Shanghai, Shenzhen };
enum class Side : std::uint8_t { Unknown, Buy, Sell };
enum class Phase : std::uint8_t { Unknown, OpenAuction, Continuous, CloseAuction, Break, Closed };
enum class OrderAction : std::uint8_t { AddFull, AddRestingRemainder, Cancel, Status };
enum class QuantityEvidence : std::uint8_t {
    Unknown, OriginalReported, OriginalReconstructed, ExecutedLowerBound
};
enum class OrderSequenceEvidence : std::uint8_t {
    SourceTotalSequence, CausalWithinTimestampBatch, Unresolved
};
enum class DirectionEvidence : std::uint8_t { Unknown, VendorFlagVerified, VendorFlagUnverified };
enum class ReplayMode : std::uint8_t { ObservedOnly }; // Simulation deliberately separate.

struct RecordRef {
    std::uint32_t source_id{};
    std::uint64_t row_number{};
    std::uint64_t source_sequence{};
    std::uint8_t source_precision_ms{1};
};
// Partition identity is passed once to the engine, not repeated for each event.
struct Partition {
    std::int32_t date{};
    std::uint32_t security_code{}; // authoritative 6-digit ETF code; do not trust vendor suffix
    Market market{};
    std::uint32_t channel{};
    bool channel_known{};
};
struct OrderEvent {
    Time time{};
    OrderId id{};
    Price price{};
    Quantity quantity{};
    Side side{};
    Phase phase{};
    OrderAction action{};
    std::uint16_t raw_order_type{};
    RecordRef source{};
};
struct TradeEvent {
    Time time{};
    std::uint64_t trade_id{};
    OrderId buy_id{}, sell_id{};
    Price price{};
    Quantity quantity{};
    Side aggressor{};
    Phase phase{};
    DirectionEvidence direction_evidence{};
    RecordRef source{};
};
struct QuoteSnapshot {
    Time time{};
    Price best_bid{}, best_ask{};
    Quantity bid_quantity{}, ask_quantity{}, cumulative_trade_quantity{};
    std::int64_t cumulative_notional_x10000{};
    RecordRef source{};
};
struct OrderSummary {
    OrderId id{};
    Side side{};
    Time first_seen{}, original_quantity_known_at{}, last_seen{};
    Quantity original_quantity{}, original_quantity_lower_bound{};
    Quantity reported_resting{}, initial_aggressive_filled{};
    Quantity filled{}, continuous_active_filled{}, continuous_passive_filled{}, auction_filled{};
    std::int64_t active_notional_x10000{}, passive_notional_x10000{};
    Quantity cancelled{}, remaining{};
    QuantityEvidence quantity_evidence{};
    OrderSequenceEvidence sequence_evidence{};
    bool conservation_passed{}, passive_reference_resolved{};
    // Unknown values use evidence flags, not zero-as-known or an invented original quantity.
};
struct MinuteSummary {
    std::uint16_t completed_minute{};
    Quantity active_buy{}, active_sell{}, unknown_direction{}, auction{};
    std::int64_t buy_notional_x10000{}, sell_notional_x10000{};
    std::int64_t unknown_notional_x10000{}, auction_notional_x10000{}; // checked accumulation
    std::uint64_t trades{}, orders{}, cancels{};
    std::uint64_t ambiguous_event_batches{};
};
struct BurstSummary {
    Time start{}, end{};
    Side side{};
    Quantity executed{};
    std::uint64_t nearest_basket_multiple{};
    double basket_distance{};
    std::uint64_t trades{}, exchange_orders{};
    std::uint32_t parameter_set_id{};
    // Segmentation independent of PCF unit; unit-distance computed afterwards.
};
struct AuditIssue {
    std::string code;
    RecordRef first_record;
    std::uint64_t count{};
    Quantity affected_quantity{};
    bool blocks_order_features{}, blocks_trade_features{};
};
struct Audit {
    Quantity total_trade_quantity{}, quote_cumulative_quantity{};
    Time cutoff_time_us{}; // exclusive as-of boundary; no later events are used
    std::int64_t total_notional_x10000{};
    bool quote_reconciled{}, order_features_valid{true}, trade_features_valid{true};
    std::uint64_t duplicate_orders{}, duplicate_cancels{};
    std::uint64_t duplicate_trades{}, unresolved_passive_references{}, overfilled_orders{};
    std::uint64_t unknown_original_orders{}, ambiguous_batches{};
    bool identity_checked{}, source_crc_checked{}, session_profile_checked{};
    std::vector<AuditIssue> issues;
};
struct Timings {
    double parse_seconds{}, index_seconds{}, replay_seconds{}, aggregation_seconds{};
    std::uint64_t estimated_working_bytes{}; // accounting estimate, not measured RSS
};
struct EngineConfig {
    ReplayMode mode{ReplayMode::ObservedOnly};
    Quantity pcf_creation_unit{};
    std::string adapter_version;
    std::string session_profile; // effective-date versioned; SH ETF != SH stock
    bool build_price_levels{false};
    std::uint64_t memory_budget_bytes{}; // conservative preflight estimate; 0 = no cap
    Time cutoff_time_us{86400000000LL}; // exclusive; default whole day
    Time burst_gap_us{1000000};
    Time burst_max_duration_us{5000000};
    Price tick_size{10};
    std::uint32_t burst_max_price_ticks{2};
};
struct DayResult {
    std::vector<OrderSummary> orders;
    std::vector<MinuteSummary> minutes;
    std::vector<BurstSummary> bursts;
    Audit audit;
    Timings timings;
};
// pybind11 wrapper releases the GIL around this call, keeps input ownership alive,
// and transfers result buffers in batches. It must not call Python per event.
Phase classify_phase(const Partition&, Time, const std::string& session_profile);
DayResult process_day(const Partition&, std::span<const OrderEvent>,
                      std::span<const TradeEvent>, std::span<const QuoteSnapshot>,
                      const EngineConfig&);
static_assert(std::is_trivially_copyable_v<OrderEvent>);
static_assert(std::is_trivially_copyable_v<TradeEvent>);
} // namespace etf_l2
