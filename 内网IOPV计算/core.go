package iopv

/*
#cgo CXXFLAGS: -std=c++17 -Wall -Wextra -Werror
#cgo darwin LDFLAGS: -lc++
#cgo linux LDFLAGS: -lstdc++
#include "core.h"
*/
import "C"
import "fmt"

type CoreRow struct {
	Quantity, Price, Cash float64
	Mode                  int
}
type Values struct {
	Midpoint   float64 `json:"midpoint_iopv"`
	Settlement float64 `json:"settlement_iopv"`
	HKDAssets  float64 `json:"hkd_assets"`
	CNYAssets  float64 `json:"cny_assets"`
}

func Calculate(rows []CoreRow, unit, cash, mid, settlement float64) (Values, error) {
	if len(rows) == 0 {
		return Values{}, fmt.Errorf("empty basket")
	}
	input := make([]C.iopv_component_v1, len(rows))
	for i, r := range rows {
		input[i] = C.iopv_component_v1{quantity: C.double(r.Quantity), price: C.double(r.Price), cash: C.double(r.Cash), mode: C.int(r.Mode)}
	}
	var out C.iopv_result_v1
	rc := C.iopv_calculate_v1(&input[0], C.size_t(len(input)), C.double(unit), C.double(cash), C.double(mid), C.double(settlement), &out)
	if rc != 0 {
		return Values{}, fmt.Errorf("core input/result rejected: %d", rc)
	}
	return Values{float64(out.midpoint), float64(out.settlement), float64(out.hkd_assets), float64(out.cny_assets)}, nil
}
