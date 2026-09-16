#include "wind_csv.hpp"
#include "execution_features.hpp"
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <memory>
#include <type_traits>
namespace py=pybind11;
using namespace etf_l2;
namespace {
template<class T> py::dtype dtype(){if constexpr(std::is_enum_v<T>)return py::dtype::of<std::underlying_type_t<T>>();else return py::dtype::of<T>();}
template<class S,class T> py::array col(const std::vector<S>& v,T S::*member,const py::capsule& owner){
    const void* ptr=v.empty()?nullptr:static_cast<const void*>(&(v.front().*member));
    py::array a(dtype<T>(),{static_cast<py::ssize_t>(v.size())},{static_cast<py::ssize_t>(sizeof(S))},ptr,owner);
    a.attr("setflags")(false);return a;
}
py::dict pack(std::unique_ptr<DayResult> result,Quantity unit){
    auto* r=result.release();py::capsule owner(r,[](void* p){delete static_cast<DayResult*>(p);});
    py::dict out,o,m,b,a,t;
#define ORDER(name,member) o[name]=col(r->orders,&OrderSummary::member,owner)
    ORDER("order_id",id);ORDER("side",side);ORDER("original_quantity",original_quantity);ORDER("original_quantity_lower_bound",original_quantity_lower_bound);ORDER("reported_resting",reported_resting);ORDER("initial_aggressive_filled",initial_aggressive_filled);ORDER("filled",filled);ORDER("active_filled",continuous_active_filled);ORDER("passive_filled",continuous_passive_filled);ORDER("auction_filled",auction_filled);ORDER("active_notional_x10000",active_notional_x10000);ORDER("passive_notional_x10000",passive_notional_x10000);ORDER("cancelled",cancelled);ORDER("remaining",remaining);ORDER("quantity_evidence",quantity_evidence);ORDER("conservation_passed",conservation_passed);ORDER("original_quantity_known_at",original_quantity_known_at);
#undef ORDER
#define MINUTE(name) m[#name]=col(r->minutes,&MinuteSummary::name,owner)
    MINUTE(completed_minute);MINUTE(active_buy);MINUTE(active_sell);MINUTE(unknown_direction);MINUTE(auction);MINUTE(buy_notional_x10000);MINUTE(sell_notional_x10000);MINUTE(unknown_notional_x10000);MINUTE(auction_notional_x10000);MINUTE(trades);MINUTE(orders);MINUTE(cancels);MINUTE(ambiguous_event_batches);
#undef MINUTE
#define BURST(name) b[#name]=col(r->bursts,&BurstSummary::name,owner)
    BURST(start);BURST(end);BURST(side);BURST(executed);BURST(trades);BURST(exchange_orders);BURST(nearest_basket_multiple);BURST(basket_distance);
#undef BURST
#define AUDIT(name) a[#name]=r->audit.name
    AUDIT(cutoff_time_us);AUDIT(total_trade_quantity);AUDIT(total_notional_x10000);AUDIT(quote_cumulative_quantity);AUDIT(quote_reconciled);AUDIT(order_features_valid);AUDIT(trade_features_valid);AUDIT(duplicate_trades);AUDIT(duplicate_orders);AUDIT(duplicate_cancels);AUDIT(unknown_original_orders);AUDIT(overfilled_orders);AUDIT(unresolved_passive_references);AUDIT(ambiguous_batches);
#undef AUDIT
    py::list issues;for(const auto& x:r->audit.issues){py::dict i;i["code"]=x.code;i["count"]=x.count;i["affected_quantity"]=x.affected_quantity;i["blocks_order_features"]=x.blocks_order_features;i["blocks_trade_features"]=x.blocks_trade_features;issues.append(i);}a["issues"]=issues;
    t["parse_seconds"]=r->timings.parse_seconds;t["index_seconds"]=r->timings.index_seconds;t["replay_seconds"]=r->timings.replay_seconds;t["aggregation_seconds"]=r->timings.aggregation_seconds;t["estimated_working_bytes"]=r->timings.estimated_working_bytes;
    py::dict execution;if(r->audit.order_features_valid && r->audit.trade_features_valid){const auto features=summarize_execution(r->orders,unit);
    for(size_t side=0;side<2;++side){const auto& f=features[side];py::dict row;
#define EF(name) row[#name]=f.name
        EF(total);EF(unit_active);EF(unit_passive);EF(known_unit_original);EF(known_unit_executed);EF(known_unit_cancelled);EF(known_unit_remaining);EF(unknown_unit_executed);EF(strict_unit_executed);EF(large_executed);EF(unknown_original_executed);EF(unit_filled_orders);EF(placebo_executed_mean);
#undef EF
        execution[side==0?"buy":"sell"]=row;
    }}out["execution"]=execution;
    out["orders"]=o;out["minutes"]=m;out["bursts"]=b;out["audit"]=a;out["timing"]=t;return out;
}
}
PYBIND11_MODULE(etf_l2,m){
    m.attr("__version__")="1.0.0";
    m.def("process_files",[](const std::string& trade_file,const std::string& order_file,const std::string& quote_file,std::uint32_t code,std::int32_t date,Quantity unit,Time gap_ms,std::uint64_t memory_budget,const std::string& cutoff,const std::string& session_profile){
        Partition p;p.date=date;p.security_code=code;p.market=code/100000==5?Market::Shanghai:Market::Shenzhen;
        EngineConfig c;c.pcf_creation_unit=unit;c.session_profile=session_profile;c.adapter_version="wind_gb18030_utf8_v1";
        if(gap_ms<0 || gap_ms>3600000)throw std::invalid_argument("gap_ms outside supported range");
        c.cutoff_time_us=parse_cutoff(cutoff);c.burst_gap_us=gap_ms*1000;c.memory_budget_bytes=memory_budget;
        std::unique_ptr<DayResult> result;std::uint64_t suffix{},placeholder{},status{};
        {py::gil_scoped_release release;auto data=load_wind_csv(trade_file,order_file,quote_file,p,c);result=std::make_unique<DayResult>(process_day(p,data.orders,data.trades,data.quotes,c));result->timings.parse_seconds=data.parse_seconds;suffix=data.suffix_corrections;placeholder=data.placeholder_rows;status=data.status_rows;}
        auto out=pack(std::move(result),unit);out["suffix_corrected_rows"]=suffix;out["placeholder_rows"]=placeholder;out["status_rows"]=status;return out;
    },py::arg("trade_file"),py::arg("order_file"),py::arg("quote_file"),py::arg("code"),py::arg("date"),py::arg("unit"),py::arg("gap_ms")=1000,py::arg("memory_budget_bytes")=0,py::arg("cutoff")="",py::arg("session_profile")="cn_etf_2026_h1");
}
