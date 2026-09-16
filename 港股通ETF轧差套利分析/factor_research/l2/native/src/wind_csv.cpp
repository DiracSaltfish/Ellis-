#include "wind_csv.hpp"
#include <algorithm>
#include <charconv>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <unordered_set>
namespace etf_l2 {
namespace {
#include "header_tokens.inc"
std::vector<std::string_view> fields(const std::string& line) {
    std::vector<std::string_view> out;size_t start=0;
    while(start<=line.size()) {
        size_t end;
        if(start<line.size() && line[start]=='"') {
            end=line.find('"',start+1);
            if(end==std::string::npos || (end+1<line.size() && line[end+1]!=',' && line[end+1]!='\r'))throw std::invalid_argument("unsupported quoted CSV field");
            out.emplace_back(line.data()+start+1,end-start-1);end++;
        } else {
            end=line.find(',',start);if(end==std::string::npos)end=line.size();
            size_t len=end-start;if(len && line[start+len-1]=='\r')--len;
            out.emplace_back(line.data()+start,len);
        }
        if(end>=line.size() || line[end]=='\r')break;
        start=end+1;
    }
    return out;
}
std::string_view trim(std::string_view s) {
    while(!s.empty() && (s.front()==' '||s.front()=='\t'))s.remove_prefix(1);
    while(!s.empty() && (s.back()==' '||s.back()=='\r'||s.back()=='\t'))s.remove_suffix(1);
    return s;
}
std::int64_t number(std::string_view s) {
    s=trim(s);std::int64_t x{};
    auto [end,ec]=std::from_chars(s.data(),s.data()+s.size(),x);
    if(s.empty() || ec!=std::errc{} || end!=s.data()+s.size() || x<0)throw std::invalid_argument("invalid/overflowing integer CSV field");
    return x;
}
Time time_value(std::int64_t t) {
    auto ms=t%1000;t/=1000;auto sec=t%100;t/=100;auto min=t%100;auto hr=t/100;
    if(hr>23||min>59||sec>59)throw std::invalid_argument("invalid HHMMSSmmm");
    return ((hr*60+min)*60+sec)*1000000+ms*1000;
}
Side side(std::string_view s) {s=trim(s);return s=="B"?Side::Buy:s=="S"?Side::Sell:Side::Unknown;}
void header(std::vector<std::string_view> f,const std::vector<std::pair<size_t,size_t>>& expected) {
    if(!f.empty() && f[0].substr(0,3)=="\xef\xbb\xbf")f[0].remove_prefix(3);
    for(auto [col,id]:expected)if(col>=f.size() || (f[col]!=tokens[id].first && f[col]!=tokens[id].second))throw std::invalid_argument("CSV schema/header mismatch");
}
std::vector<std::pair<size_t,size_t>> base_header() {return {{0,0},{1,1},{2,2},{3,3}};}
void identity(const std::vector<std::string_view>& f,const Partition& p,LoadedDay& d) {
    if(number(f[1])!=p.security_code)throw std::invalid_argument("CSV exchange code mismatch");
    const auto s=trim(f[0]);if(s.size()!=9 || s[6]!='.' || number(s.substr(0,6))!=p.security_code || (s.substr(7)!="SH"&&s.substr(7)!="SZ"))throw std::invalid_argument("invalid vendor symbol");
    if(s.substr(7)!=(p.market==Market::Shanghai?"SH":"SZ"))++d.suffix_corrections;
    if(number(f[2])!=p.date)throw std::invalid_argument("CSV trade date mismatch");
}
template<class F> void read(const std::string& path,const std::vector<std::pair<size_t,size_t>>& expected,size_t count,F fn) {
    std::ifstream in(path,std::ios::binary);if(!in)throw std::runtime_error("cannot read CSV: "+path);
    std::string line;if(!std::getline(in,line))throw std::invalid_argument("empty CSV");header(fields(line),expected);
    std::uint64_t row=1;
    while(std::getline(in,line)) {
        ++row;if(line.empty()||line=="\r")continue;
        try {auto f=fields(line);if(f.size()!=count && !(f.size()==count+1 && f.back().empty()))throw std::invalid_argument("CSV column count mismatch");fn(f,row);}
        catch(const std::exception& e){throw std::runtime_error(path+":"+std::to_string(row)+": "+e.what());}
    }
    if(in.bad())throw std::runtime_error("CSV read error: "+path);
}
}
Time parse_cutoff(const std::string& s) {
    if(s.empty())return 86400000000LL;
    if(s.size()!=5 || s[2]!=':')throw std::invalid_argument("cutoff must be HH:MM");
    auto h=number(std::string_view(s).substr(0,2)),m=number(std::string_view(s).substr(3,2));
    if(h>24 || m>59 || (h==24 && m!=0) || (h==0 && m==0))throw std::invalid_argument("invalid cutoff");
    return (h*60+m)*60000000LL;
}
LoadedDay load_wind_csv(const std::string& trade_file,const std::string& order_file,const std::string& quote_file,const Partition& p,const EngineConfig& c) {
    const auto start=std::chrono::steady_clock::now();LoadedDay d;d.partition=p;
    // Validate market and session before reading potentially large files.
    EngineConfig guard=c;guard.build_price_levels=false;process_day(p,{}, {},{},guard);
    auto budget=[&] {
        const long double estimate=static_cast<long double>(d.orders.size())*640+static_cast<long double>(d.trades.size())*1100+d.quotes.size()*sizeof(QuoteSnapshot);
        if(c.memory_budget_bytes && estimate>c.memory_budget_bytes)throw std::length_error("CSV preflight memory budget exceeded");
    };
    auto oh=base_header();oh.insert(oh.end(),{{4,12},{5,13},{6,14},{7,6},{8,15},{9,16}});
    read(order_file,oh,10,[&](const auto& f,std::uint64_t row) {
        if(number(f[2])==0){++d.placeholder_rows;return;}identity(f,p,d);
        if(time_value(number(f[3]))>=c.cutoff_time_us)return;
        const auto kind=trim(f[6]);const auto id=number(f[5]);
        if(p.market==Market::Shanghai && kind=="S"){++d.status_rows;return;}
        OrderEvent e;e.id=static_cast<OrderId>(id);e.time=time_value(number(f[3]));e.phase=classify_phase(p,e.time,c.session_profile);
        e.price=number(f[8]);e.quantity=number(f[9]);e.side=side(f[7]);e.source={1,row,0,1};
        if(p.market==Market::Shanghai) {
            if(kind=="A")e.action=OrderAction::AddRestingRemainder;
            else if(kind=="D")e.action=OrderAction::Cancel;
            else throw std::invalid_argument("unknown SH order kind");
        } else {
            if(kind!="0" && kind!="1" && kind!="2" && kind!="U")throw std::invalid_argument("unknown SZ order kind");
            e.raw_order_type=kind=="U"?85:static_cast<std::uint16_t>(number(kind));e.action=OrderAction::AddFull;
        }
        d.orders.push_back(e);budget();
    });
    auto th=base_header();th.insert(th.end(),{{4,4},{5,5},{6,6},{7,7},{8,8},{9,9},{10,10},{11,11}});
    read(trade_file,th,12,[&](const auto& f,std::uint64_t row) {
        if(number(f[2])==0){++d.placeholder_rows;return;}identity(f,p,d);
        if(time_value(number(f[3]))>=c.cutoff_time_us)return;
        // Vendor exports use either empty text or a single NUL for SH blank trade code.
        auto kind=trim(f[5]);if(p.market==Market::Shanghai && kind.size()==1 && kind[0]=='\0')kind={};
        const auto buy=static_cast<OrderId>(number(f[11])),sell=static_cast<OrderId>(number(f[10]));
        const auto time=time_value(number(f[3]));const auto phase=classify_phase(p,time,c.session_profile);const auto seq=static_cast<std::uint64_t>(number(f[4]));
        if(p.market==Market::Shenzhen && kind=="C") {
            if((buy==0)==(sell==0))throw std::invalid_argument("SZ cancel needs exactly one order id");
            OrderEvent e;e.id=buy?buy:sell;e.time=time;e.phase=phase;e.side=buy?Side::Buy:Side::Sell;e.quantity=number(f[9]);e.action=OrderAction::Cancel;e.source={2,row,seq,1};d.orders.push_back(e);budget();return;
        }
        if((p.market==Market::Shanghai && !kind.empty()) || (p.market==Market::Shenzhen && kind!="0"))throw std::invalid_argument("unknown trade kind");
        TradeEvent e;e.time=time;e.trade_id=seq;e.buy_id=buy;e.sell_id=sell;e.price=number(f[8]);e.quantity=number(f[9]);e.aggressor=side(f[7]);e.phase=phase;e.direction_evidence=e.aggressor==Side::Unknown?DirectionEvidence::Unknown:DirectionEvidence::VendorFlagVerified;e.source={2,row,seq,1};d.trades.push_back(e);budget();
    });
    if(!quote_file.empty()) {
        auto qh=base_header();qh.insert(qh.end(),{{11,17},{17,18},{37,19},{27,20},{47,21}});
        read(quote_file,qh,66,[&](const auto& f,std::uint64_t row) {
            if(number(f[2])==0){++d.placeholder_rows;return;}identity(f,p,d);
            if(time_value(number(f[3]))>=c.cutoff_time_us)return;
            QuoteSnapshot q;q.time=time_value(number(f[3]));q.cumulative_trade_quantity=number(f[11]);q.best_bid=number(f[37]);q.best_ask=number(f[17]);q.bid_quantity=number(f[47]);q.ask_quantity=number(f[27]);q.source={3,row,0,1};d.quotes.push_back(q);budget();
        });
    }
    d.parse_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();return d;
}

void write_result(const DayResult& r,const std::string& directory) {
    namespace fs=std::filesystem;fs::create_directories(directory);
    // Each run gets its own directory. Refuse to overwrite an existing successful result.
    if(fs::exists(fs::path(directory)/"summary.json"))throw std::runtime_error("output already complete; choose a new directory");
    const auto lock=fs::path(directory)/".writing";
    if(!fs::create_directory(lock))throw std::runtime_error("another writer owns output directory");
    struct LockGuard { fs::path path; ~LockGuard(){std::error_code ec;fs::remove(path,ec);} } lock_guard{lock};
    if(fs::exists(fs::path(directory)/"summary.json"))throw std::runtime_error("output already complete");
    std::ofstream o(fs::path(directory)/"orders.csv");o<<"order_id,side,original_quantity,original_quantity_lower_bound,reported_resting,initial_aggressive_filled,filled,active_filled,passive_filled,auction_filled,cancelled,remaining,quantity_evidence,conservation_passed,original_quantity_known_at\n";
    for(const auto& x:r.orders)o<<x.id<<','<<int(x.side)<<','<<x.original_quantity<<','<<x.original_quantity_lower_bound<<','<<x.reported_resting<<','<<x.initial_aggressive_filled<<','<<x.filled<<','<<x.continuous_active_filled<<','<<x.continuous_passive_filled<<','<<x.auction_filled<<','<<x.cancelled<<','<<x.remaining<<','<<int(x.quantity_evidence)<<','<<x.conservation_passed<<','<<x.original_quantity_known_at<<'\n';
    std::ofstream m(fs::path(directory)/"minutes.csv");m<<"completed_minute,active_buy,active_sell,unknown_direction,auction,buy_notional_x10000,sell_notional_x10000,unknown_notional_x10000,auction_notional_x10000,trades,orders,cancels,ambiguous_event_batches\n";
    for(const auto& x:r.minutes)m<<x.completed_minute<<','<<x.active_buy<<','<<x.active_sell<<','<<x.unknown_direction<<','<<x.auction<<','<<x.buy_notional_x10000<<','<<x.sell_notional_x10000<<','<<x.unknown_notional_x10000<<','<<x.auction_notional_x10000<<','<<x.trades<<','<<x.orders<<','<<x.cancels<<','<<x.ambiguous_event_batches<<'\n';
    std::ofstream b(fs::path(directory)/"bursts.csv");b.precision(17);b<<"start_us,end_us,side,executed,trades,exchange_orders,nearest_basket_multiple,basket_distance\n";
    for(const auto& x:r.bursts)b<<x.start<<','<<x.end<<','<<int(x.side)<<','<<x.executed<<','<<x.trades<<','<<x.exchange_orders<<','<<x.nearest_basket_multiple<<','<<x.basket_distance<<'\n';
    o.close();m.close();b.close();if(!o||!m||!b)throw std::runtime_error("output write failure");
    const auto& a=r.audit;std::ofstream j(fs::path(directory)/"summary.json.tmp");j.precision(17);
    j<<"{\"version\":\"1.0.0\",\"total_trade_quantity\":"<<a.total_trade_quantity<<",\"cutoff_time_us\":"<<a.cutoff_time_us<<",\"total_notional_x10000\":"<<a.total_notional_x10000<<",\"quote_cumulative_quantity\":"<<a.quote_cumulative_quantity<<",\"quote_reconciled\":"<<a.quote_reconciled<<",\"order_features_valid\":"<<a.order_features_valid<<",\"trade_features_valid\":"<<a.trade_features_valid<<",\"duplicate_trades\":"<<a.duplicate_trades<<",\"duplicate_orders\":"<<a.duplicate_orders<<",\"duplicate_cancels\":"<<a.duplicate_cancels<<",\"unknown_original_orders\":"<<a.unknown_original_orders<<",\"unresolved_passive_references\":"<<a.unresolved_passive_references<<",\"overfilled_orders\":"<<a.overfilled_orders<<",\"ambiguous_batches\":"<<a.ambiguous_batches<<",\"parse_seconds\":"<<r.timings.parse_seconds<<",\"index_seconds\":"<<r.timings.index_seconds<<",\"replay_seconds\":"<<r.timings.replay_seconds<<",\"aggregation_seconds\":"<<r.timings.aggregation_seconds<<",\"estimated_working_bytes\":"<<r.timings.estimated_working_bytes<<",\"issues\":[";
    bool comma=false;for(const auto& x:a.issues){if(comma)j<<',';comma=true;j<<"{\"code\":\""<<x.code<<"\",\"count\":"<<x.count<<",\"affected_quantity\":"<<x.affected_quantity<<",\"blocks_order_features\":"<<x.blocks_order_features<<",\"blocks_trade_features\":"<<x.blocks_trade_features<<'}';}
    j<<"]}\n";j.close();if(!j)throw std::runtime_error("summary write failure");fs::rename(fs::path(directory)/"summary.json.tmp",fs::path(directory)/"summary.json");
}
} // namespace etf_l2
