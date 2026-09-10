#include "core.h"
#include <cmath>
#include <limits>

namespace {
bool positive(double x) { return std::isfinite(x) && x > 0; }
// Neumaier accumulation also compensates on arm64 where long double is double.
struct Sum {
    double value = 0, correction = 0;
    void add(double x) {
        double t = value + x;
        correction += std::abs(value) >= std::abs(x) ? (value-t)+x : (x-t)+value;
        value = t;
    }
    double total() const { return value + correction; }
};
}
extern "C" int iopv_calculate_v1(const iopv_component_v1* rows, size_t n,
    double unit, double cash, double mid, double settlement, iopv_result_v1* out) {
    if (!out) return 1;
    const double nan = std::numeric_limits<double>::quiet_NaN();
    *out = {nan,nan,nan,nan};
    if (!rows || n == 0 || !positive(unit) || !std::isfinite(cash) ||
        !positive(mid) || !positive(settlement)) return 1;
    Sum hk, cn; cn.add(cash);
    for (size_t i=0; i<n; ++i) {
        const auto &r = rows[i];
        if (!std::isfinite(r.quantity) || r.quantity < 0) return 1;
        if (r.mode == 2) {
            if (!std::isfinite(r.cash) || r.cash < 0) return 1;
            cn.add(r.cash);
        } else if (r.mode == 0 || r.mode == 1) {
            if (!positive(r.price)) return 1;
            (r.mode == 0 ? hk : cn).add(r.quantity*r.price);
        } else return 1;
    }
    double a = (hk.total()*mid + cn.total())/unit;
    double b = (hk.total()*settlement + cn.total())/unit;
    if (!positive(a) || !positive(b) || !std::isfinite(hk.total()) ||
        !std::isfinite(cn.total())) return 2;
    *out = {a,b,hk.total(),cn.total()};
    return 0;
}
