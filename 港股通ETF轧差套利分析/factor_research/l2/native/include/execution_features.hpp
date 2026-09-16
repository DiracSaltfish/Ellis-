#pragma once
#include "engine_contract.hpp"
#include <array>
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
namespace etf_l2 {
struct ExecutionStats {
    Quantity total{}, unit_active{}, unit_passive{}, known_unit_original{}, known_unit_executed{}, known_unit_cancelled{}, known_unit_remaining{}, unknown_unit_executed{}, strict_unit_executed{}, large_executed{}, unknown_original_executed{};
    std::uint64_t unit_filled_orders{};
    double placebo_executed_mean{};
};
inline bool near_unit(Quantity q, Quantity unit) {
    if(unit<=0) throw std::invalid_argument("unit must be positive");
    if(q<=0) return false;
    const long double k=std::max(1.0L,std::round(static_cast<long double>(q)/unit));
    return std::abs(static_cast<long double>(q)-k*unit)<=static_cast<long double>(unit)*.01L;
}
inline std::array<ExecutionStats,2> summarize_execution(std::span<const OrderSummary> orders,Quantity unit) {
    if(unit<=0) throw std::invalid_argument("unit must be positive");
    std::array<ExecutionStats,2> out{};
    auto add=[](Quantity& a,Quantity b){if(b<0 || a>std::numeric_limits<Quantity>::max()-b)throw std::overflow_error("execution feature overflow");a+=b;};
    for(const auto& o:orders) {
        if(o.side!=Side::Buy && o.side!=Side::Sell) throw std::invalid_argument("unknown order side");
        auto& x=out[o.side==Side::Buy?0:1];Quantity vol=o.continuous_active_filled;add(vol,o.continuous_passive_filled);add(x.total,vol);
        const bool known=o.quantity_evidence==QuantityEvidence::OriginalReported || o.quantity_evidence==QuantityEvidence::OriginalReconstructed;
        const Quantity basis=known?o.original_quantity:o.continuous_active_filled;
        if(!known)add(x.unknown_original_executed,vol);
        if(near_unit(basis,unit)) {
            add(x.unit_active,o.continuous_active_filled);add(x.unit_passive,o.continuous_passive_filled);
            if(vol>0)++x.unit_filled_orders;
            if(known){add(x.known_unit_original,o.original_quantity);add(x.known_unit_executed,vol);add(x.known_unit_cancelled,o.cancelled);add(x.known_unit_remaining,o.remaining);}
            else add(x.unknown_unit_executed,vol);
        }
        if(basis>0 && basis%unit==0)add(x.strict_unit_executed,vol);
        if((known && static_cast<long double>(o.original_quantity)>=unit*.5L) || static_cast<long double>(o.continuous_active_filled)>=unit*.5L)add(x.large_executed,vol);
        for(double ratio:{.8,1.2,1.3}) {
            const auto fake=static_cast<Quantity>(std::round(unit*ratio/100.0)*100);
            if(fake>0 && near_unit(basis,fake))x.placebo_executed_mean+=static_cast<double>(vol)/3;
        }
    }
    return out;
}
}
