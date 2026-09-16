#include "engine_contract.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <map>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <tuple>

namespace etf_l2 {
namespace {
constexpr Time second=1000000, minute=60*second, hour=60*minute;
constexpr auto maxq=std::numeric_limits<std::int64_t>::max();
using Clock=std::chrono::steady_clock;
std::int64_t add(std::int64_t a,std::int64_t b) {
    if(a<0 || b<0 || a>maxq-b) throw std::overflow_error("quantity/notional addition overflow");
    return a+b;
}
std::int64_t notional(Price p,Quantity q) {
    if(p<=0 || q<=0) throw std::invalid_argument("invalid trade price/quantity");
    if(q>maxq/p) throw std::overflow_error("trade notional overflow");
    return p*q;
}
bool known(Side x) { return x==Side::Buy || x==Side::Sell; }
bool auction(Phase x) { return x==Phase::OpenAuction || x==Phase::CloseAuction; }
void check_time(Time t) { if(t<0 || t>=24*hour) throw std::invalid_argument("invalid time"); }
void validate_partition(const Partition& p,const EngineConfig& c) {
    if(!std::chrono::year_month_day{std::chrono::year{p.date/10000},std::chrono::month{static_cast<unsigned>(p.date/100%100)},std::chrono::day{static_cast<unsigned>(p.date%100)}}.ok()) throw std::invalid_argument("invalid calendar date");
    (void)classify_phase(p,0,c.session_profile);
    const bool sh=p.security_code/100000==5, sz=p.security_code/1000==159;
    if(!((sh && p.market==Market::Shanghai)||(sz && p.market==Market::Shenzhen)))
        throw std::invalid_argument("market/security identity mismatch");
    if(c.cutoff_time_us<=0 || c.cutoff_time_us>24*hour)throw std::invalid_argument("invalid as-of cutoff");
    if(c.pcf_creation_unit<=0 || c.tick_size<=0 || c.burst_gap_us<0 || c.burst_max_duration_us<0)
        throw std::invalid_argument("invalid engine configuration");
    if(c.build_price_levels) throw std::invalid_argument("v1 is an observed ledger; price queue replay is not implemented");
}
bool equal_trade(const TradeEvent& a,const TradeEvent& b) {
    return std::tie(a.time,a.buy_id,a.sell_id,a.price,a.quantity,a.aggressor,a.phase,a.direction_evidence)==
           std::tie(b.time,b.buy_id,b.sell_id,b.price,b.quantity,b.aggressor,b.phase,b.direction_evidence);
}
bool equal_order(const OrderEvent& a,const OrderEvent& b) {
    return std::tie(a.time,a.id,a.price,a.quantity,a.side,a.phase,a.action,a.raw_order_type)==
           std::tie(b.time,b.id,b.price,b.quantity,b.side,b.phase,b.action,b.raw_order_type);
}
struct State {
    OrderSummary out;
    const OrderEvent* submitted{};
    Time earliest_fill{24*hour}, earliest_passive{24*hour}, earliest_cancel{24*hour};
    Quantity pre_rest_active{}, passive_unknown{};
    RecordRef ref{};
};
struct CancelKey {
    std::uint32_t source;std::uint64_t seq,row;
    bool operator==(const CancelKey& x) const { return source==x.source && seq==x.seq && row==x.row; }
};
struct CancelHash { size_t operator()(const CancelKey& k)const { return std::hash<std::uint64_t>{}(k.seq^(k.row<<1)^k.source); } };
}

Phase classify_phase(const Partition& p,Time t,const std::string& profile) {
    check_time(t);
    const bool h1=profile=="cn_etf_2026_h1" && p.date>=20260101 && p.date<20260706;
    const bool y2025=profile=="cn_etf_2025" && p.date>=20250101 && p.date<=20251231;
    const bool sz_july=profile=="sz_etf_20260706" && p.market==Market::Shenzhen && p.date>=20260706 && p.date<=20260707;
    const bool sh_new=profile=="sh_etf_20260706" && p.market==Market::Shanghai && p.date>=20260706 && p.date<=20260912;
    if(!h1 && !y2025 && !sz_july && !sh_new) throw std::invalid_argument("unsupported dated session profile");
    // Source auction fills are reported just after 09:25; retain them in auction phase.
    if(t>=9*hour+15*minute && t<9*hour+30*minute) return Phase::OpenAuction;
    if(t>=9*hour+30*minute && t<=11*hour+30*minute) return Phase::Continuous;
    if(t>11*hour+30*minute && t<13*hour) return Phase::Break;
    if((h1 || y2025) && p.market==Market::Shanghai && t>=13*hour && t<=15*hour) return Phase::Continuous;
    // Wind export reports the 15:00 auction after 15:00:01; one-minute reporting allowance, not a trading extension.
    if(sh_new && t>=13*hour && t<14*hour+57*minute) return Phase::Continuous;
    if(sh_new && t>=14*hour+57*minute && t<15*hour+minute) return Phase::CloseAuction;
    if(p.market==Market::Shenzhen && t>=13*hour && t<14*hour+57*minute) return Phase::Continuous;
    if(p.market==Market::Shenzhen && t>=14*hour+57*minute && t<=15*hour+second) return Phase::CloseAuction;
    return Phase::Closed;
}

DayResult process_day(const Partition& partition,std::span<const OrderEvent> orders,
                      std::span<const TradeEvent> trades,std::span<const QuoteSnapshot> quotes,
                      const EngineConfig& config) {
    const auto start=Clock::now();validate_partition(partition,config);DayResult result;
    auto& audit=result.audit;audit.cutoff_time_us=config.cutoff_time_us;audit.identity_checked=true;audit.session_profile_checked=true;
    // Conservative accounting, including hash tables/state/output; actual RSS measured by benchmark runner.
    const long double estimate=static_cast<long double>(orders.size())*640+static_cast<long double>(trades.size())*1100+quotes.size()*sizeof(QuoteSnapshot);
    if(estimate>=static_cast<long double>(std::numeric_limits<std::uint64_t>::max())) throw std::length_error("input too large");
    result.timings.estimated_working_bytes=static_cast<std::uint64_t>(estimate);
    if(config.memory_budget_bytes && estimate>config.memory_budget_bytes) throw std::length_error("preflight memory budget exceeded");
    auto issue=[&](const std::string& code,const RecordRef& ref,Quantity qty,bool block_order,bool block_trade) {
        auto it=std::find_if(audit.issues.begin(),audit.issues.end(),[&](const auto& x){return x.code==code;});
        if(it==audit.issues.end())audit.issues.push_back({code,ref,1,qty,block_order,block_trade});
        else {++it->count;it->affected_quantity=add(it->affected_quantity,qty);}
        if(block_order)audit.order_features_valid=false;
        if(block_trade)audit.trade_features_valid=false;
    };
    std::unordered_map<OrderId,size_t> index;index.reserve(orders.size());
    std::vector<State> state;state.reserve(orders.size());
    auto get=[&](OrderId id,Side side,Time time,const RecordRef& ref)->State& {
        if(!id || !known(side))throw std::invalid_argument("invalid order reference");
        auto it=index.find(id);
        if(it==index.end()) {
            State s;s.out.id=id;s.out.side=side;s.out.first_seen=time;s.out.last_seen=time;s.ref=ref;
            index.emplace(id,state.size());state.push_back(s);return state.back();
        }
        auto& s=state[it->second];
        if(s.out.side!=side)throw std::invalid_argument("order id side collision; check channel scope");
        s.out.first_seen=std::min(s.out.first_seen,time);s.out.last_seen=std::max(s.out.last_seen,time);return s;
    };
    std::map<unsigned,MinuteSummary> minutes;
    auto minute_row=[&](Time t,Phase phase)->MinuteSummary& {
        unsigned m=static_cast<unsigned>(t/minute+1);
        if((phase==Phase::Continuous && (t==11*hour+30*minute || t==15*hour)) || phase==Phase::CloseAuction)m=static_cast<unsigned>(t/minute);
        auto& row=minutes[m];row.completed_minute=static_cast<std::uint16_t>(m);return row;
    };
    std::unordered_map<CancelKey,const OrderEvent*,CancelHash> cancel_seen;
    std::vector<const OrderEvent*> cancels;
    for(const auto& e:orders) {
        if(e.time>=config.cutoff_time_us)continue;
        check_time(e.time);
        if(e.phase!=classify_phase(partition,e.time,config.session_profile))throw std::invalid_argument("order phase/profile mismatch");
        if(e.action==OrderAction::Status)continue;
        if(!known(e.side)||e.quantity<=0||e.price<0)throw std::invalid_argument("invalid order");
        auto& s=get(e.id,e.side,e.time,e.source);
        if(e.action==OrderAction::Cancel) {
            const bool has_ref=e.source.row_number || e.source.source_sequence;
            CancelKey key{e.source.source_id,e.source.source_sequence,e.source.source_sequence?0:e.source.row_number};
            if(has_ref) {
                auto [it,inserted]=cancel_seen.emplace(key,&e);
                if(!inserted) {
                    if(!equal_order(*it->second,e))throw std::invalid_argument("conflicting cancel identity");
                    ++audit.duplicate_cancels;continue;
                }
            }
            cancels.push_back(&e);continue;
        }
        const auto expected=partition.market==Market::Shanghai?OrderAction::AddRestingRemainder:OrderAction::AddFull;
        if(e.action!=expected)throw std::invalid_argument("wrong exchange order semantics");
        if(s.submitted) {
            if(!equal_order(*s.submitted,e))throw std::invalid_argument("conflicting duplicate order");
            ++audit.duplicate_orders;continue;
        }
        s.submitted=&e;s.out.reported_resting=e.quantity;s.out.original_quantity=e.quantity;
        s.out.original_quantity_known_at=e.time;s.out.quantity_evidence=QuantityEvidence::OriginalReported;
        ++minute_row(e.time,e.phase).orders;
    }
    for(const auto* ep:cancels) {
        const auto& e=*ep;auto& s=get(e.id,e.side,e.time,e.source);
        s.out.cancelled=add(s.out.cancelled,e.quantity);s.earliest_cancel=std::min(s.earliest_cancel,e.time);
        ++minute_row(e.time,e.phase).cancels;
    }
    result.timings.index_seconds=std::chrono::duration<double>(Clock::now()-start).count();
    const auto replay_start=Clock::now();
    std::unordered_map<std::uint64_t,const TradeEvent*> seen;seen.reserve(trades.size());
    std::vector<const TradeEvent*> tape;tape.reserve(trades.size());
    for(const auto& t:trades) {
        if(t.time>=config.cutoff_time_us)continue;
        check_time(t.time);if(!t.trade_id)throw std::invalid_argument("zero trade id");
        if(t.phase!=classify_phase(partition,t.time,config.session_profile))throw std::invalid_argument("trade phase/profile mismatch");
        if(!t.buy_id||!t.sell_id||t.buy_id==t.sell_id)throw std::invalid_argument("invalid dual order refs");
        if(t.aggressor!=Side::Unknown && !known(t.aggressor))throw std::invalid_argument("invalid aggressor enum");
        const auto money=notional(t.price,t.quantity);
        auto [it,fresh]=seen.emplace(t.trade_id,&t);
        if(!fresh) {
            if(!equal_trade(*it->second,t))throw std::invalid_argument("conflicting trade id; check channel scope");
            ++audit.duplicate_trades;continue;
        }
        tape.push_back(&t);audit.total_trade_quantity=add(audit.total_trade_quantity,t.quantity);
        audit.total_notional_x10000=add(audit.total_notional_x10000,money);
        const bool cont=t.phase==Phase::Continuous;
        const bool direction=known(t.aggressor) && t.direction_evidence!=DirectionEvidence::Unknown;
        for(Side side:{Side::Buy,Side::Sell}) {
            auto& s=get(side==Side::Buy?t.buy_id:t.sell_id,side,t.time,t.source);
            s.out.filled=add(s.out.filled,t.quantity);s.earliest_fill=std::min(s.earliest_fill,t.time);
            if(cont && direction) {
                if(t.aggressor==side) {
                    s.out.continuous_active_filled=add(s.out.continuous_active_filled,t.quantity);
                    s.out.active_notional_x10000=add(s.out.active_notional_x10000,money);
                    if(partition.market==Market::Shanghai && s.submitted && t.time<=s.submitted->time && s.submitted->phase==Phase::Continuous)
                        s.pre_rest_active=add(s.pre_rest_active,t.quantity);
                    else if(partition.market==Market::Shanghai && s.submitted && t.time>s.submitted->time)
                        issue("active_fill_after_resting_add",t.source,t.quantity,true,false);
                } else {
                    s.out.continuous_passive_filled=add(s.out.continuous_passive_filled,t.quantity);
                    s.out.passive_notional_x10000=add(s.out.passive_notional_x10000,money);
                    s.earliest_passive=std::min(s.earliest_passive,t.time);
                    if(!s.submitted) {++audit.unresolved_passive_references;issue("unresolved_passive_reference",t.source,t.quantity,true,false);}
                }
            } else if(auction(t.phase))s.out.auction_filled=add(s.out.auction_filled,t.quantity);
            else s.passive_unknown=add(s.passive_unknown,t.quantity);
        }
        auto& row=minute_row(t.time,t.phase);++row.trades;
        if(auction(t.phase)) {row.auction=add(row.auction,t.quantity);row.auction_notional_x10000=add(row.auction_notional_x10000,money);}
        else if(cont && direction && t.aggressor==Side::Buy) {row.active_buy=add(row.active_buy,t.quantity);row.buy_notional_x10000=add(row.buy_notional_x10000,money);}
        else if(cont && direction && t.aggressor==Side::Sell) {row.active_sell=add(row.active_sell,t.quantity);row.sell_notional_x10000=add(row.sell_notional_x10000,money);}
        else {row.unknown_direction=add(row.unknown_direction,t.quantity);row.unknown_notional_x10000=add(row.unknown_notional_x10000,money);issue("unclassified_trade_direction_or_phase",t.source,t.quantity,false,true);}
    }
    for(auto& s:state) {
        auto& o=s.out;o.original_quantity_lower_bound=add(o.filled,o.cancelled);
        if(!s.submitted) {
            ++audit.unknown_original_orders;o.quantity_evidence=QuantityEvidence::ExecutedLowerBound;
            o.sequence_evidence=OrderSequenceEvidence::Unresolved;o.passive_reference_resolved=o.continuous_passive_filled==0 && o.auction_filled==0;
            if(partition.market==Market::Shenzhen || o.cancelled || o.auction_filled || s.passive_unknown)
                issue("missing_original_order",s.ref,o.original_quantity_lower_bound,true,false);
        } else {
            if(partition.market==Market::Shanghai) {
                o.initial_aggressive_filled=s.pre_rest_active;o.original_quantity=add(o.reported_resting,s.pre_rest_active);
                o.quantity_evidence=QuantityEvidence::OriginalReconstructed;
            }
            o.remaining=o.original_quantity-o.original_quantity_lower_bound;
            o.passive_reference_resolved=s.earliest_passive>=s.submitted->time;
            const bool bad_time=(partition.market==Market::Shenzhen?s.earliest_fill:s.earliest_passive)<s.submitted->time || s.earliest_cancel<s.submitted->time;
            o.conservation_passed=o.remaining>=0 && !bad_time;
            if(o.remaining<0) {++audit.overfilled_orders;issue("overfilled_order",s.ref,-o.remaining,true,false);}
            if(bad_time)issue("order_event_time_conflict",s.ref,o.filled,true,false);
            o.sequence_evidence=OrderSequenceEvidence::CausalWithinTimestampBatch;
        }
        result.orders.push_back(o);
    }
    std::sort(result.orders.begin(),result.orders.end(),[](const auto& a,const auto& b){return a.id<b.id;});
    result.timings.replay_seconds=std::chrono::duration<double>(Clock::now()-replay_start).count();
    const auto agg_start=Clock::now();
    std::sort(tape.begin(),tape.end(),[](const auto* a,const auto* b){return std::tie(a->time,a->trade_id)<std::tie(b->time,b->trade_id);});
    // Source has no cross-stream total ordering. Expose timestamp batches containing >1 observed event.
    std::unordered_map<Time,std::uint32_t> batch_counts;
    for(const auto& s:state)if(s.submitted)++batch_counts[s.submitted->time];
    for(const auto* e:cancels)++batch_counts[e->time];
    for(const auto* t:tape)++batch_counts[t->time];
    for(const auto& [time,count]:batch_counts)if(count>1) {++audit.ambiguous_batches;++minute_row(time,classify_phase(partition,time,config.session_profile)).ambiguous_event_batches;}
    BurstSummary burst;bool have=false;Price low{},high{};std::unordered_set<OrderId> active_ids;
    auto finish=[&]() {
        if(!have)return;
        burst.exchange_orders=active_ids.size();const auto u=config.pcf_creation_unit;
        auto k=burst.executed/u;const auto remainder=burst.executed%u;
        if(remainder>=u-remainder)++k;if(k<1)k=1;
        burst.nearest_basket_multiple=static_cast<std::uint64_t>(k);
        burst.basket_distance=std::abs(static_cast<double>(burst.executed)/static_cast<double>(u)-static_cast<double>(k));
        result.bursts.push_back(burst);have=false;active_ids.clear();
    };
    for(const auto* t:tape) {
        if(t->phase!=Phase::Continuous || !known(t->aggressor) || t->direction_evidence==DirectionEvidence::Unknown) {finish();continue;}
        const auto next_low=have?std::min(low,t->price):t->price,next_high=have?std::max(high,t->price):t->price;
        const auto allowed=static_cast<long double>(config.tick_size)*config.burst_max_price_ticks;
        if(have && (burst.side!=t->aggressor || t->time-burst.end>config.burst_gap_us || t->time-burst.start>config.burst_max_duration_us || static_cast<long double>(next_high-next_low)>allowed))finish();
        if(!have) {burst={};burst.start=t->time;burst.side=t->aggressor;low=t->price;high=t->price;have=true;}
        low=std::min(low,t->price);high=std::max(high,t->price);burst.end=t->time;burst.executed=add(burst.executed,t->quantity);++burst.trades;
        active_ids.insert(t->aggressor==Side::Buy?t->buy_id:t->sell_id);
    }
    finish();for(auto& [key,row]:minutes)result.minutes.push_back(row);
    if(!quotes.empty()) {
        const QuoteSnapshot* last=nullptr;
        for(const auto& q:quotes) {if(q.time>=config.cutoff_time_us)continue;check_time(q.time);if(q.cumulative_trade_quantity<0)throw std::invalid_argument("negative quote volume");if(!last || std::tie(q.time,q.source.row_number)>std::tie(last->time,last->source.row_number))last=&q;}
        if(last) {
        audit.quote_cumulative_quantity=last->cumulative_trade_quantity;
        if(!tape.empty() && last->time<tape.back()->time)issue("quote_before_last_trade",last->source,0,false,false);
        else if(audit.total_trade_quantity!=last->cumulative_trade_quantity)issue("quote_volume_mismatch",last->source,0,false,true);
        else audit.quote_reconciled=true;
        }
    }
    result.timings.aggregation_seconds=std::chrono::duration<double>(Clock::now()-agg_start).count();
    return result;
}
} // namespace etf_l2
