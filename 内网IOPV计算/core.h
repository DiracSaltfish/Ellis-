#ifndef INTRANET_IOPV_CORE_H
#define INTRANET_IOPV_CORE_H
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Stable v1 C ABI. Values are unscaled currency units, FX is CNY per HKD.
 * mode: 0=HKD marked security, 1=CNY marked security, 2=fixed CNY cash.
 * All ownership stays with caller; no retained pointers; thread safe. */
typedef struct { double quantity, price, cash; int mode; } iopv_component_v1;
typedef struct { double midpoint, settlement, hkd_assets, cny_assets; } iopv_result_v1;
/* 0=success, 1=invalid input, 2=invalid/overflow result. */
int iopv_calculate_v1(const iopv_component_v1*, size_t, double unit,
                     double estimated_cash, double midpoint_fx,
                     double settlement_fx, iopv_result_v1*);
#ifdef __cplusplus
}
#endif
#endif
