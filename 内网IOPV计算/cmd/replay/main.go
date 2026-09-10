// Offline replay intentionally never labels historical prices as live quotes.
package main

import (
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"math"
	"os"
	"path/filepath"
	"strings"
)

func check(e error) {
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
func read(path string, v any) { b, e := os.ReadFile(path); check(e); check(json.Unmarshal(b, v)) }
func save(path string, v any) {
	b, e := json.MarshalIndent(v, "", "  ")
	check(e)
	check(os.WriteFile(path, append(b, '\n'), 0644))
}
func main() {
	var manifest []struct {
		Symbol, Date, QC    string
		Expected, Published float64
	}
	var prices map[string]struct {
		Price float64
		Date  string
	}
	read("testdata/manifest.json", &manifest)
	read("testdata/close_prices.json", &prices)
	var baskets []iopv.Basket
	var results []any
	maxError := 0.0
	staleFunds := 0
	for _, m := range manifest {
		raw, e := os.ReadFile(filepath.Join("testdata/pcf", strings.Split(m.Symbol, ".")[0]+".xml"))
		check(e)
		b, e := iopv.ParsePCF(raw, m.Symbol, m.Date)
		check(e)
		baskets = append(baskets, b)
		var rows []iopv.CoreRow
		stale := []string{}
		for _, c := range b.Components {
			p := prices[c.Symbol]
			rows = append(rows, iopv.CoreRow{Quantity: c.Quantity, Price: p.Price, Cash: c.Cash, Mode: c.Mode})
			if c.Mode != 2 && p.Date != m.Date {
				stale = append(stale, c.Symbol)
			}
		}
		v, e := iopv.Calculate(rows, b.Unit, b.Cash, .86482, .86482)
		check(e)
		diff := math.Abs(v.Midpoint - m.Expected)
		maxError = math.Max(maxError, diff)
		if len(stale) > 0 {
			staleFunds++
		}
		results = append(results, map[string]any{"symbol": m.Symbol, "date": m.Date, "midpoint_iopv": v.Midpoint, "published_nav": m.Published, "published_error_bp": (v.Midpoint/m.Published - 1) * 10000, "expected": m.Expected, "absolute_difference": diff, "qc": m.QC, "stale_components": stale, "mode": "historical_replay", "eligible_for_signal": false, "pcf_sha256": b.Hash})
	}
	plan, e := iopv.BuildPlan(baskets)
	check(e)
	check(os.MkdirAll("outputs", 0755))
	save("outputs/replay.json", results)
	save("outputs/subscriptions.json", plan)
	summary := map[string]any{"funds": len(results), "subscriptions": len(plan.Subscriptions), "group_loads": plan.Loads, "max_abs_difference_from_prior_audit": maxError, "funds_with_old_close_prices": staleFunds, "historical_midpoint_fx": .86482, "settlement_replay": "not available; no historical predicted FX injected"}
	save("outputs/replay_summary.json", summary)
	b, _ := json.Marshal(summary)
	fmt.Println(string(b))
}
