package valuation

import (
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestEstimateUsesManualOverrideForWeightedAnchor(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH501018"] = domain.NetValue{Symbol: "SH501018", Date: "2026-06-01", NAV: 2.0}
	data.ManualPositionOverrides["SH501018"] = domain.ManualValuationPositionOverride{
		Symbol:    "SH501018",
		Ratio:     0.73,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: time.Date(2026, 6, 19, 9, 30, 0, 0, time.UTC),
	}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH501018", "2026-06-01", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH501018",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_CL",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_CL": {Symbol: "HF_CL", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SH501018", Price: 2.1}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))

	if row.ModelVersion != "v1.weighted_anchor.cl" {
		t.Fatalf("model = %s, want weighted CL anchor", row.ModelVersion)
	}
	if row.EffectiveRatio != 0.73 {
		t.Fatalf("effective_ratio = %.6f, want 0.730000", row.EffectiveRatio)
	}
	if row.FairEst != 2.146 {
		t.Fatalf("fair_est = %.6f, want 2.146000", row.FairEst)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.146 {
		t.Fatalf("realtime_est = %+v, want 2.146000", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "CL有效仓位 73.00%") {
		t.Fatalf("note = %q, want manual override ratio", row.Note)
	}
}

func TestEstimateUsesManualOverrideForCommodityBasket(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ160216"] = domain.NetValue{Symbol: "SZ160216", Date: "2026-06-01", NAV: 2.0}
	data.ManualPositionOverrides["SZ160216"] = domain.ManualValuationPositionOverride{
		Symbol:    "SZ160216",
		Ratio:     0.5,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: time.Date(2026, 6, 19, 9, 30, 0, 0, time.UTC),
	}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	for _, symbol := range []string{"HF_SI", "HF_CL", "HF_HG"} {
		data.DailyPricesByDate[symbol] = map[string]domain.DailyPrice{
			"2026-06-01": {Symbol: symbol, Date: "2026-06-01", Close: 100, AdjClose: 100},
		}
	}
	quotes := map[string]domain.Quote{
		"HF_SI": {Symbol: "HF_SI", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_CL": {Symbol: "HF_CL", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_HG": {Symbol: "HF_HG", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ160216", Price: 0.72}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))

	if row.ModelVersion != "v1.commodity_basket.cn" {
		t.Fatalf("model = %s, want commodity basket", row.ModelVersion)
	}
	if row.EffectiveRatio != 0.5 {
		t.Fatalf("effective_ratio = %.6f, want 0.500000", row.EffectiveRatio)
	}
	if row.OfficialEst != 2.1 {
		t.Fatalf("official_est = %.6f, want 2.100000", row.OfficialEst)
	}
	if row.FairEst != 2.1 {
		t.Fatalf("fair_est = %.6f, want 2.100000", row.FairEst)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.1 {
		t.Fatalf("realtime_est = %+v, want 2.100000", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "商品有效仓位 50.00%") {
		t.Fatalf("note = %q, want manual override ratio", row.Note)
	}
}
