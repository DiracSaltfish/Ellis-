#include "core.h"
#include <cmath>
#include <iostream>
#include <limits>
int main() {
    iopv_component_v1 rows[]={{100,20,0,0},{0,0,30,2}};
    iopv_result_v1 out{};
    if (iopv_calculate_v1(rows,2,1000,-10,.86,.85,&out)!=0 ||
        std::abs(out.midpoint-1.74)>1e-12 || std::abs(out.settlement-1.72)>1e-12) return 1;
    if (iopv_calculate_v1(nullptr,0,1000,0,.86,.85,&out)!=1 || !std::isnan(out.midpoint)) return 2;
    if (iopv_calculate_v1(rows,2,1000,0,.86,.85,nullptr)!=1) return 3;
    rows[0].price=std::numeric_limits<double>::infinity();
    if (iopv_calculate_v1(rows,2,1000,0,.86,.85,&out)!=1 || !std::isnan(out.settlement)) return 4;
    std::cout << "C ABI success, null input/output and invalid-result clearing passed\n";
}
