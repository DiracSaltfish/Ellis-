package main

import (
	iopv "intranet-iopv"
	"intranet-iopv/internal/live"
	"testing"
	"time"
)

func TestSourceMinuteGates(t *testing.T) {
	cutoff, _ := time.ParseInLocation("2006-01-02 15:04", "2026-09-09 12:00", live.Zone)
	raw := []byte(`{"data":{"hk00001":{"data":{"date":"20260909","data":["0930 10 1","1300 11 2"]}}}}`)
	p, e := prices(raw, "00001.HK", "2026-09-09", cutoff)
	if e != nil || len(p) != 1 || p["09:30"] != 10 {
		t.Fatal(p, e)
	}
	if _, e = prices(raw, "00001.HK", "2026-09-08", cutoff); e == nil {
		t.Fatal("wrong date accepted")
	}
	duplicate := []byte(`{"data":{"hk00001":{"data":{"date":"20260909","data":["0930 10 1","0930 11 2"]}}}}`)
	if _, e = prices(duplicate, "00001.HK", "2026-09-09", cutoff); e == nil {
		t.Fatal("duplicate accepted")
	}
}
func TestNoMissingComponentFillAndCashNotConverted(t *testing.T) {
	b := iopv.Basket{Unit: 100, Cash: -5, Components: []iopv.Component{{Symbol: "00001.HK", Quantity: 10, Mode: 0}, {Mode: 2, Cash: 25}}}
	q := map[string]map[string]float64{"00001.HK": {"09:30": 10}}
	v, missing, e := calculate(b, q, "09:30", .8)
	if e != nil || len(missing) != 0 || v.Midpoint != 1 || v.CNYAssets != 20 {
		t.Fatal(v, missing, e)
	}
	_, missing, e = calculate(b, q, "09:31", .8)
	if e != nil || len(missing) != 1 {
		t.Fatal("forward filled missing price", missing, e)
	}
}
func TestHistoricalFXFreshness(t *testing.T) {
	at, _ := time.Parse(time.RFC3339, "2026-09-09T10:00:00+08:00")
	r := fxRow{At: at, Observed: at.Add(-time.Minute), Date: "2026-09-09", Status: "reference_only", Buy: .85, Sell: .86}
	if !fxUsable(r, "2026-09-09", "10:00") {
		t.Fatal("valid reference rejected")
	}
	r.Observed = at.Add(-24 * time.Hour)
	if fxUsable(r, "2026-09-09", "10:00") {
		t.Fatal("old FX accepted")
	}
	r.Observed = at.Add(time.Second)
	if fxUsable(r, "2026-09-09", "10:00") {
		t.Fatal("future FX accepted")
	}
	r.Observed = at
	r.Status = "cfets_unavailable"
	if fxUsable(r, "2026-09-09", "10:00") {
		t.Fatal("unavailable FX accepted")
	}
}

func TestZeroVolumePlaceholderIsNotHistoricalPrice(t *testing.T) {
	cutoff, _ := time.ParseInLocation("2006-01-02 15:04", "2026-09-09 12:00", live.Zone)
	raw := []byte(`{"data":{"hk00853":{"data":{"date":"20260909","data":["0930 6.075 0 0","1200 6.075 0 0"]}}}}`)
	p, e := prices(raw, "00853.HK", "2026-09-09", cutoff)
	if e != nil || len(p) != 0 {
		t.Fatal("zero volume placeholder accepted", p, e)
	}
}
