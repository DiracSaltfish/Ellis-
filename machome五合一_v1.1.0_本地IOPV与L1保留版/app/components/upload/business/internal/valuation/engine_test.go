package valuation

import (
	"math"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func seedUSDCNY(data *domain.ValuationData, baseDate string, currentDate string, baseRate float64, currentRate float64) {
	if data.FXCentralParity == nil {
		data.FXCentralParity = map[string]map[string]domain.FXCentralParity{}
	}
	if data.FXCentralParity["USDCNY"] == nil {
		data.FXCentralParity["USDCNY"] = map[string]domain.FXCentralParity{}
	}
	data.FXCentralParity["USDCNY"][baseDate] = domain.FXCentralParity{Pair: "USDCNY", Date: baseDate, Rate: baseRate, Source: "safe"}
	data.FXCentralParity["USDCNY"][currentDate] = domain.FXCentralParity{Pair: "USDCNY", Date: currentDate, Rate: currentRate, Source: "safe"}
}

func seedJPYCNY(data *domain.ValuationData, baseDate string, currentDate string, baseRate float64, currentRate float64) {
	if data.FXCentralParity == nil {
		data.FXCentralParity = map[string]map[string]domain.FXCentralParity{}
	}
	if data.FXCentralParity["JPYCNY"] == nil {
		data.FXCentralParity["JPYCNY"] = map[string]domain.FXCentralParity{}
	}
	data.FXCentralParity["JPYCNY"][baseDate] = domain.FXCentralParity{Pair: "JPYCNY", Date: baseDate, Rate: baseRate, Source: "safe"}
	data.FXCentralParity["JPYCNY"][currentDate] = domain.FXCentralParity{Pair: "JPYCNY", Date: currentDate, Rate: currentRate, Source: "safe"}
}

func TestHoldingsEstimateSkipsDemoAndSeparatesAfterHours(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.CurrentHoldingDates["FUND"] = domain.HoldingDate{FundSymbol: "FUND", Date: "2026-05-29"}
	data.NetValuesByDate["FUND"] = map[string]domain.NetValue{
		"2026-05-29": {Symbol: "FUND", Date: "2026-05-29", NAV: 100},
	}
	data.LatestNetValues["FUND"] = data.NetValuesByDate["FUND"]["2026-05-29"]
	data.Holdings["FUND"] = []domain.Holding{
		{FundSymbol: "FUND", HoldingDate: "2026-05-29", HoldingSymbol: "GOOD", Ratio: 50},
		{FundSymbol: "FUND", HoldingDate: "2026-05-29", HoldingSymbol: "DEMO", Ratio: 50},
	}
	data.DailyPricesByDate["GOOD"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "GOOD", Date: "2026-05-29", Close: 10, AdjClose: 10},
	}
	data.DailyPricesByDate["DEMO"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "DEMO", Date: "2026-05-29", Close: 10, AdjClose: 10},
	}

	quotes := map[string]domain.Quote{
		"GOOD": {Symbol: "GOOD", Price: 12, PrevClose: 11, Source: "sina_us_after_hours", IsRealtime: true, RealtimeStatus: "realtime"},
		"DEMO": {Symbol: "DEMO", Price: 0.1, Source: "demo", Error: "demo fallback"},
	}
	row, ok := engine.estimateFromHoldings(domain.Quote{Symbol: "FUND", Price: 120}, quotes, data, time.Date(2026, 6, 2, 12, 0, 0, 0, time.Local))
	if !ok {
		t.Fatal("expected holdings estimate")
	}
	if row.OfficialEst != 110 {
		t.Fatalf("official_est = %.4f, want 110", row.OfficialEst)
	}
	if row.FairEst != 120 {
		t.Fatalf("fair_est = %.4f, want 120", row.FairEst)
	}
	if row.RealtimeEst != nil || row.RealtimePremium != nil {
		t.Fatalf("realtime estimate should be nil without an independent realtime source")
	}
	if row.OfficialPremium != 9.090909 {
		t.Fatalf("official_premium = %.6f, want 9.090909", row.OfficialPremium)
	}
}

func TestHoldingsEstimateUsesLatestNAVDateAsPriceBase(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.Positions["FUND"] = 0.99
	data.CurrentHoldingDates["FUND"] = domain.HoldingDate{FundSymbol: "FUND", Date: "2026-05-29"}
	data.NetValuesByDate["FUND"] = map[string]domain.NetValue{
		"2026-05-29": {Symbol: "FUND", Date: "2026-05-29", NAV: 100},
		"2026-06-01": {Symbol: "FUND", Date: "2026-06-01", NAV: 210},
	}
	data.LatestNetValues["FUND"] = data.NetValuesByDate["FUND"]["2026-06-01"]
	data.Holdings["FUND"] = []domain.Holding{
		{FundSymbol: "FUND", HoldingDate: "2026-05-29", HoldingSymbol: "GOOD", Ratio: 100},
	}
	data.DailyPricesByDate["GOOD"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "GOOD", Date: "2026-05-29", Close: 10, AdjClose: 10},
		"2026-06-01": {Symbol: "GOOD", Date: "2026-06-01", Close: 20, AdjClose: 20},
	}
	quotes := map[string]domain.Quote{
		"GOOD": {Symbol: "GOOD", Price: 22, PrevClose: 21, Source: "sina_us_after_hours"},
	}

	row, ok := engine.estimateFromHoldings(domain.Quote{Symbol: "FUND", Price: 230}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if !ok {
		t.Fatal("expected holdings estimate")
	}
	if row.FairEst != 230.79 {
		t.Fatalf("fair_est = %.6f, want 230.79 from latest NAV date base with 99%% position", row.FairEst)
	}
	if !strings.Contains(row.Note, "净值基准日 2026-06-01") {
		t.Fatalf("note = %q, want latest NAV base date", row.Note)
	}
	if !strings.Contains(row.Note, "有效仓位 99.00%") {
		t.Fatalf("note = %q, want effective ratio note", row.Note)
	}
	if row.EffectiveRatio != 0.99 {
		t.Fatalf("effective_ratio = %.6f, want 0.99", row.EffectiveRatio)
	}
}

func TestHoldingsEstimateUsesManualRealtimeWhitelist(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.CurrentHoldingDates["SH501312"] = domain.HoldingDate{FundSymbol: "SH501312", Date: "2026-06-02"}
	data.NetValuesByDate["SH501312"] = map[string]domain.NetValue{
		"2026-06-02": {Symbol: "SH501312", Date: "2026-06-02", NAV: 100},
	}
	data.LatestNetValues["SH501312"] = data.NetValuesByDate["SH501312"]["2026-06-02"]
	data.Holdings["SH501312"] = []domain.Holding{
		{FundSymbol: "SH501312", HoldingDate: "2026-06-02", HoldingSymbol: "QQQ", Ratio: 100},
	}
	data.DailyPricesByDate["QQQ"] = map[string]domain.DailyPrice{
		"2026-06-02": {Symbol: "QQQ", Date: "2026-06-02", Close: 746.16, AdjClose: 746.16},
	}
	quotes := map[string]domain.Quote{
		"QQQ": {
			Symbol:         "QQQ",
			Price:          739.93,
			PrevClose:      744.21,
			Source:         "ibkr_us_live",
			QuoteSession:   "us_overnight_live",
			IsRealtime:     true,
			RealtimeStatus: "realtime",
		},
	}

	row, ok := engine.estimateFromHoldings(domain.Quote{Symbol: "SH501312", Price: 99}, quotes, data, time.Date(2026, 6, 4, 11, 0, 0, 0, time.Local))
	if !ok {
		t.Fatal("expected holdings estimate")
	}
	if row.RealtimeEst == nil {
		t.Fatal("expected realtime_est for whitelisted holdings fund")
	}
	if math.Abs(row.OfficialEst-99.738668) > 0.00001 {
		t.Fatalf("official_est = %.6f, want prev-close based 99.738668", row.OfficialEst)
	}
	if math.Abs(row.FairEst-99.165058) > 0.00001 {
		t.Fatalf("fair_est = %.6f, want 99.165058", row.FairEst)
	}
}

func TestWeightedAnchorEstimateUses164701GoldAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ164701"] = domain.NetValue{Symbol: "SZ164701", Date: "2026-06-01", NAV: 1.8}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ164701", "2026-06-01", "HF_GC")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ164701",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_GC",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_GC": {Symbol: "HF_GC", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ164701", Price: 1.77}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.gc" {
		t.Fatalf("model = %s, want weighted GC anchor", row.ModelVersion)
	}
	if row.RealtimeEst == nil || row.RealtimePremium == nil {
		t.Fatalf("expected realtime estimate from GC anchor")
	}
	if row.ReferenceSymbol != "HF_GC" {
		t.Fatalf("reference_symbol = %q, want HF_GC", row.ReferenceSymbol)
	}
	if row.FairEst != 1.97505 {
		t.Fatalf("fair_est = %.6f, want 1.975050", row.FairEst)
	}
	if row.EffectiveRatio != 0.9725 {
		t.Fatalf("effective_ratio = %.6f, want 0.972500", row.EffectiveRatio)
	}
	if !strings.Contains(row.Note, "GC有效仓位 97.25%") {
		t.Fatalf("note = %q, want GC effective ratio", row.Note)
	}
}

func TestWeightedAnchorEstimateUses513520NikkeiAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH513520"] = domain.NetValue{Symbol: "SH513520", Date: "2026-06-01", NAV: 2.0}
	seedJPYCNY(&data, "2026-06-01", "2026-06-03", 0.045, 0.046)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH513520", "2026-06-01", "HF_NK")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH513520",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_NK",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_NK": {Symbol: "HF_NK", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SH513520", Price: 2.23}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.nk" {
		t.Fatalf("model = %s, want weighted NK anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_NK" {
		t.Fatalf("reference_symbol = %q, want HF_NK", row.ReferenceSymbol)
	}
	if row.FairEst != 2.240178 {
		t.Fatalf("fair_est = %.6f, want 2.240178", row.FairEst)
	}
	if row.EffectiveRatio != 0.965 {
		t.Fatalf("effective_ratio = %.6f, want 0.965000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.240178 {
		t.Fatalf("realtime_est = %+v, want 2.240178", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "JPYCNY 0.045000/0.046000") {
		t.Fatalf("note = %q, want JPYCNY note", row.Note)
	}
	if !strings.Contains(row.Note, "NK有效仓位 96.50%") {
		t.Fatalf("note = %q, want NK effective ratio", row.Note)
	}
}

func TestWeightedAnchorEstimateUses513500SP500AnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH513500"] = domain.NetValue{Symbol: "SH513500", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.2)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH513500", "2026-06-01", "HF_ES")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH513500",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_ES",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_ES": {Symbol: "HF_ES", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SH513500", Price: 2.18}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.es" {
		t.Fatalf("model = %s, want weighted ES anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_ES" {
		t.Fatalf("reference_symbol = %q, want HF_ES", row.ReferenceSymbol)
	}
	if row.FairEst != 2.224056 {
		t.Fatalf("fair_est = %.6f, want 2.224056", row.FairEst)
	}
	if row.EffectiveRatio != 0.97 {
		t.Fatalf("effective_ratio = %.6f, want 0.970000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.224056 {
		t.Fatalf("realtime_est = %+v, want 2.224056", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "USDCNY 7.100000/7.200000") {
		t.Fatalf("note = %q, want USDCNY note", row.Note)
	}
	if !strings.Contains(row.Note, "ES有效仓位 97.00%") {
		t.Fatalf("note = %q, want ES effective ratio", row.Note)
	}
}

func TestWeightedAnchorEstimateUses513290IBBAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH513290"] = domain.NetValue{Symbol: "SH513290", Date: "2026-06-01", NAV: 1.5}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.2)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH513290", "2026-06-01", "IBB")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH513290",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "IBB",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"IBB": {Symbol: "IBB", Price: 110, Source: "daily", QuoteSession: "us_overnight_live"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SH513290", Price: 1.62}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.ibb" {
		t.Fatalf("model = %s, want weighted IBB anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "IBB" {
		t.Fatalf("reference_symbol = %q, want IBB", row.ReferenceSymbol)
	}
	if row.FairEst != 1.673239 {
		t.Fatalf("fair_est = %.6f, want 1.673239", row.FairEst)
	}
	if row.EffectiveRatio != 1 {
		t.Fatalf("effective_ratio = %.6f, want 1.000000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 1.673239 {
		t.Fatalf("realtime_est = %+v, want 1.673239", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "USDCNY 7.100000/7.200000") {
		t.Fatalf("note = %q, want USDCNY note", row.Note)
	}
}

func TestWeightedAnchorEstimateUses513350XOPOilAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH513350"] = domain.NetValue{Symbol: "SH513350", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.2)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH513350", "2026-06-01", "XOP")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH513350",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "XOP",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"XOP": {Symbol: "XOP", Price: 110, Source: "us_live", QuoteSession: "us_overnight_live"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SH513350", Price: 1.22}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.xop" {
		t.Fatalf("model = %s, want weighted XOP anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "XOP" {
		t.Fatalf("reference_symbol = %q, want XOP", row.ReferenceSymbol)
	}
	if row.FairEst != 2.230408 {
		t.Fatalf("fair_est = %.6f, want 2.230408", row.FairEst)
	}
	if row.EffectiveRatio != 0.9975 {
		t.Fatalf("effective_ratio = %.6f, want 0.997500", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.230408 {
		t.Fatalf("realtime_est = %+v, want 2.230408", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "USDCNY 7.100000/7.200000") {
		t.Fatalf("note = %q, want USDCNY note", row.Note)
	}
	if !strings.Contains(row.Note, "XOP有效仓位 99.75%") {
		t.Fatalf("note = %q, want XOP effective ratio", row.Note)
	}
}

func TestWeightedAnchorEstimateStillProducesRealtimeForStaleXOPReference(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ159518"] = domain.NetValue{Symbol: "SZ159518", Date: "2026-06-17", NAV: 1.0513}
	seedUSDCNY(&data, "2026-06-17", "2026-06-22", 7.18, 7.18)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ159518", "2026-06-17", "XOP")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ159518",
		AnchorDate:      "2026-06-17",
		ReferenceSymbol: "XOP",
		WeightedPrice:   155.64,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"XOP": {
			Symbol:         "XOP",
			Price:          153.49,
			PrevClose:      153.40,
			Source:         "us_overnight_live:trades",
			QuoteSession:   "us_overnight_live",
			IsRealtime:     false,
			RealtimeStatus: "stale",
		},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ159518", Price: 1.034}, quotes, data, time.Date(2026, 6, 22, 9, 30, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.xop" {
		t.Fatalf("model = %s, want weighted XOP anchor", row.ModelVersion)
	}
	if row.RealtimeEst == nil {
		t.Fatal("realtime_est should be set even for stale reference quote")
	}
	if row.RealtimePremium == nil {
		t.Fatal("realtime_premium should be set even for stale reference quote")
	}
	wantFair := round6(1.0513 * (0.005 + 0.995*(153.49/155.64)))
	if row.FairEst != wantFair {
		t.Fatalf("fair_est = %.6f, want current-price-based %.6f", row.FairEst, wantFair)
	}
	if strings.Contains(row.Note, "当前参考行情非实时") {
		t.Fatalf("note = %q, should not contain stale fallback message", row.Note)
	}
}

func TestWeightedAnchorEstimateUses501300ZNAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH501300"] = domain.NetValue{Symbol: "SH501300", Date: "2026-06-01", NAV: 1.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH501300", "2026-06-01", "HF_ZN")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH501300",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_ZN",
		WeightedPrice:   110,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_ZN": {Symbol: "HF_ZN", Price: 121, Source: "upload:ibkr", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SH501300", Price: 1.02}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.zn" {
		t.Fatalf("model = %s, want weighted ZN anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_ZN" {
		t.Fatalf("reference_symbol = %q, want HF_ZN", row.ReferenceSymbol)
	}
	if row.FairEst != 1.0765 {
		t.Fatalf("fair_est = %.6f, want 1.076500", row.FairEst)
	}
	if row.EffectiveRatio != 0.765 {
		t.Fatalf("effective_ratio = %.6f, want 0.765000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 1.0765 {
		t.Fatalf("realtime_est = %+v, want 1.076500", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "ZN有效仓位 76.50%") {
		t.Fatalf("note = %q, want ZN effective ratio", row.Note)
	}
}

func TestWeightedAnchorEstimateUses501018CLAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH501018"] = domain.NetValue{Symbol: "SH501018", Date: "2026-06-01", NAV: 2.0}
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
	if row.ReferenceSymbol != "HF_CL" {
		t.Fatalf("reference_symbol = %q, want HF_CL", row.ReferenceSymbol)
	}
	if row.FairEst != 2.16 {
		t.Fatalf("fair_est = %.6f, want 2.160000", row.FairEst)
	}
	if row.EffectiveRatio != 0.8 {
		t.Fatalf("effective_ratio = %.6f, want 0.800000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.16 {
		t.Fatalf("realtime_est = %+v, want 2.160000", row.RealtimeEst)
	}
}

func TestWeightedAnchorEstimateCarriesCurrentFXForwardOnWeekend(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH501018"] = domain.NetValue{Symbol: "SH501018", Date: "2026-06-04", NAV: 1.8499}
	seedUSDCNY(&data, "2026-06-04", "2026-06-05", 6.8203, 6.8157)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH501018", "2026-06-04", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH501018",
		AnchorDate:      "2026-06-04",
		ReferenceSymbol: "HF_CL",
		WeightedPrice:   93.19231,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_CL": {Symbol: "HF_CL", Price: 92.711, Source: "sina_hf", QuoteSession: "global_future"},
	}

	now := time.Date(2026, 6, 6, 12, 0, 0, 0, time.FixedZone("CST", 8*60*60))
	row := engine.Estimate(domain.Quote{Symbol: "SH501018", Price: 1.949}, quotes, data, now)
	if row.ModelVersion != "v1.weighted_anchor.cl" {
		t.Fatalf("model = %s, want weighted CL anchor", row.ModelVersion)
	}
	want := round6(1.8499 * (0.2 + 0.8*(92.711/93.19231)*(6.8157/6.8203)))
	if row.FairEst != want {
		t.Fatalf("fair_est = %.6f, want %.6f", row.FairEst, want)
	}
}

func TestMaxValuationBaseDateUsesT2Weekdays(t *testing.T) {
	loc := time.FixedZone("CST", 8*60*60)
	cases := []struct {
		name string
		now  time.Time
		want string
	}{
		{
			name: "regular trading day",
			now:  time.Date(2026, 6, 4, 10, 0, 0, 0, loc),
			want: "2026-06-02",
		},
		{
			name: "weekend rolls to next monday",
			now:  time.Date(2026, 6, 6, 12, 0, 0, 0, loc),
			want: "2026-06-04",
		},
		{
			name: "monday morning",
			now:  time.Date(2026, 6, 8, 9, 30, 0, 0, loc),
			want: "2026-06-04",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := MaxValuationBaseDate(tc.now); got != tc.want {
				t.Fatalf("MaxValuationBaseDate() = %s, want %s", got, tc.want)
			}
		})
	}
}

func TestWeightedAnchorEstimateUsesT2NAVAndAnchorOnMonday(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.NetValuesByDate["SH501018"] = map[string]domain.NetValue{
		"2026-06-04": {Symbol: "SH501018", Date: "2026-06-04", NAV: 1.8},
		"2026-06-05": {Symbol: "SH501018", Date: "2026-06-05", NAV: 2.0},
	}
	data.LatestNetValues["SH501018"] = data.NetValuesByDate["SH501018"]["2026-06-05"]
	seedUSDCNY(&data, "2026-06-04", "2026-06-08", 7.0, 7.0)
	data.FXCentralParity["USDCNY"]["2026-06-05"] = domain.FXCentralParity{Pair: "USDCNY", Date: "2026-06-05", Rate: 7.0, Source: "safe"}
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH501018", "2026-06-04", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH501018",
		AnchorDate:      "2026-06-04",
		ReferenceSymbol: "HF_CL",
		WeightedPrice:   90,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SH501018", "2026-06-05", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH501018",
		AnchorDate:      "2026-06-05",
		ReferenceSymbol: "HF_CL",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_CL": {Symbol: "HF_CL", Price: 99, Source: "sina_hf", QuoteSession: "global_future"},
	}

	now := time.Date(2026, 6, 8, 9, 35, 0, 0, time.FixedZone("CST", 8*60*60))
	row := engine.Estimate(domain.Quote{Symbol: "SH501018", Price: 1.95}, quotes, data, now)
	if row.ModelVersion != "v1.weighted_anchor.cl" {
		t.Fatalf("model = %s, want weighted CL anchor", row.ModelVersion)
	}
	if row.FairEst != 1.944 {
		t.Fatalf("fair_est = %.6f, want 1.944000 from 2026-06-04 T-2 base", row.FairEst)
	}
	if !strings.Contains(row.Note, "净值基准日 2026-06-04") {
		t.Fatalf("note = %q, want T-2 base date", row.Note)
	}
}

func TestWeightedAnchorEstimateUses160723CashAdjustedCLAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ160723"] = domain.NetValue{Symbol: "SZ160723", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ160723", "2026-06-01", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ160723",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_CL",
		WeightedPrice:   100,
		CoverageWeight:  0.9999,
		RequiredWeight:  0.9999,
	}
	quotes := map[string]domain.Quote{
		"HF_CL": {Symbol: "HF_CL", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ160723", Price: 2.1}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.cl" {
		t.Fatalf("model = %s, want weighted CL anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_CL" {
		t.Fatalf("reference_symbol = %q, want HF_CL", row.ReferenceSymbol)
	}
	if row.FairEst != 2.135 {
		t.Fatalf("fair_est = %.6f, want 2.135000", row.FairEst)
	}
	if row.EffectiveRatio != 0.675 {
		t.Fatalf("effective_ratio = %.6f, want 0.675000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.135 {
		t.Fatalf("realtime_est = %+v, want 2.135000", row.RealtimeEst)
	}
}

func TestWeightedAnchorEstimateUses161129FOFCLAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ161129"] = domain.NetValue{Symbol: "SZ161129", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ161129", "2026-06-01", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ161129",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_CL",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_CL": {Symbol: "HF_CL", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ161129", Price: 2.1}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.cl" {
		t.Fatalf("model = %s, want weighted CL anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_CL" {
		t.Fatalf("reference_symbol = %q, want HF_CL", row.ReferenceSymbol)
	}
	if row.FairEst != 2.1435 {
		t.Fatalf("fair_est = %.6f, want 2.143500", row.FairEst)
	}
	if row.EffectiveRatio != 0.7175 {
		t.Fatalf("effective_ratio = %.6f, want 0.717500", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.1435 {
		t.Fatalf("realtime_est = %+v, want 2.143500", row.RealtimeEst)
	}
}

func TestWeightedAnchorEstimateCarriesForwardMissingAnchorPoint(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.NetValuesByDate["SZ161129"] = map[string]domain.NetValue{
		"2026-05-22": {Symbol: "SZ161129", Date: "2026-05-22", NAV: 1.9},
		"2026-05-25": {Symbol: "SZ161129", Date: "2026-05-25", NAV: 2.0},
	}
	data.LatestNetValues["SZ161129"] = data.NetValuesByDate["SZ161129"]["2026-05-25"]
	seedUSDCNY(&data, "2026-05-25", "2026-05-27", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ161129", "2026-05-25", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ161129",
		AnchorDate:      "2026-05-25",
		ReferenceSymbol: "HF_CL",
		Points: []domain.ValuationAnchorPrice{
			{FundSymbol: "SZ161129", AnchorDate: "2026-05-25", AnchorKey: "hk_close", ReferenceSymbol: "HF_CL", Price: 100, Weight: 0.1351},
			{FundSymbol: "SZ161129", AnchorDate: "2026-05-25", AnchorKey: "eu_close", ReferenceSymbol: "HF_CL", Price: 100, Weight: 0.4135},
		},
	}
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ161129", "2026-05-22", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ161129",
		AnchorDate:      "2026-05-22",
		ReferenceSymbol: "HF_CL",
		Points: []domain.ValuationAnchorPrice{
			{FundSymbol: "SZ161129", AnchorDate: "2026-05-22", AnchorKey: "us_close", ReferenceSymbol: "HF_CL", Price: 100, Weight: 0.4514},
		},
	}
	quotes := map[string]domain.Quote{
		"HF_CL": {Symbol: "HF_CL", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ161129", Price: 2.1}, quotes, data, time.Date(2026, 5, 27, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.cl" {
		t.Fatalf("model = %s, want weighted CL anchor", row.ModelVersion)
	}
	if row.FairEst != 2.1435 {
		t.Fatalf("fair_est = %.6f, want 2.143500", row.FairEst)
	}
	if !strings.Contains(row.Note, "HF_CL us_close uses 2026-05-22 for 2026-05-25") {
		t.Fatalf("note = %q, want carry-forward warning", row.Note)
	}
}

func TestWeightedAnchorEstimateUses160719GoldAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ160719"] = domain.NetValue{Symbol: "SZ160719", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ160719", "2026-06-01", "HF_GC")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ160719",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_GC",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_GC": {Symbol: "HF_GC", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ160719", Price: 2.1}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.gc" {
		t.Fatalf("model = %s, want weighted GC anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_GC" {
		t.Fatalf("reference_symbol = %q, want HF_GC", row.ReferenceSymbol)
	}
	if row.FairEst != 2.19 {
		t.Fatalf("fair_est = %.6f, want 2.190000", row.FairEst)
	}
	if row.EffectiveRatio != 0.95 {
		t.Fatalf("effective_ratio = %.6f, want 0.950000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.19 {
		t.Fatalf("realtime_est = %+v, want 2.190000", row.RealtimeEst)
	}
}

func TestWeightedAnchorEstimateUses161116GoldAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ161116"] = domain.NetValue{Symbol: "SZ161116", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ161116", "2026-06-01", "HF_GC")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ161116",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_GC",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_GC": {Symbol: "HF_GC", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ161116", Price: 2.1}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.gc" {
		t.Fatalf("model = %s, want weighted GC anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_GC" {
		t.Fatalf("reference_symbol = %q, want HF_GC", row.ReferenceSymbol)
	}
	if row.FairEst != 2.186979 {
		t.Fatalf("fair_est = %.6f, want 2.186979", row.FairEst)
	}
	if row.EffectiveRatio != 0.934896 {
		t.Fatalf("effective_ratio = %.6f, want 0.934896", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.186979 {
		t.Fatalf("realtime_est = %+v, want 2.186979", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "GC有效仓位 93.49%") {
		t.Fatalf("note = %q, want GC effective ratio", row.Note)
	}
}

func TestWeightedAnchorEstimateUses165513GoldAnchorSet(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ165513"] = domain.NetValue{Symbol: "SZ165513", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ165513", "2026-06-01", "HF_GC")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ165513",
		AnchorDate:      "2026-06-01",
		ReferenceSymbol: "HF_GC",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	quotes := map[string]domain.Quote{
		"HF_GC": {Symbol: "HF_GC", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ165513", Price: 2.1}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.weighted_anchor.gc" {
		t.Fatalf("model = %s, want weighted GC anchor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_GC" {
		t.Fatalf("reference_symbol = %q, want HF_GC", row.ReferenceSymbol)
	}
	if row.FairEst != 2.193 {
		t.Fatalf("fair_est = %.6f, want 2.193000", row.FairEst)
	}
	if row.EffectiveRatio != 0.965 {
		t.Fatalf("effective_ratio = %.6f, want 0.965000", row.EffectiveRatio)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.193 {
		t.Fatalf("realtime_est = %+v, want 2.193000", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "GC有效仓位 96.50%") {
		t.Fatalf("note = %q, want GC effective ratio", row.Note)
	}
}

func TestCommodityBasketEstimateUses161815CompositeLegs(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ161815"] = domain.NetValue{Symbol: "SZ161815", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.1, 7.1)
	for _, symbol := range []string{"HF_GC", "HF_CL", "HF_SI", "HF_HG"} {
		data.DailyPricesByDate[symbol] = map[string]domain.DailyPrice{
			"2026-06-01": {Symbol: symbol, Date: "2026-06-01", Close: 100, AdjClose: 100},
		}
	}
	quotes := map[string]domain.Quote{
		"HF_GC": {Symbol: "HF_GC", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_CL": {Symbol: "HF_CL", Price: 120, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_SI": {Symbol: "HF_SI", Price: 90, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_HG": {Symbol: "HF_HG", Price: 95, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ161815", Price: 2.21}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.commodity_basket.cn" {
		t.Fatalf("model = %s, want commodity basket", row.ModelVersion)
	}
	if row.ReferenceSymbol != "" {
		t.Fatalf("reference_symbol = %q, want empty for composite model", row.ReferenceSymbol)
	}
	if row.OfficialEst != 2.200977 {
		t.Fatalf("official_est = %.6f, want 2.200977", row.OfficialEst)
	}
	if row.FairEst != 2.200977 {
		t.Fatalf("fair_est = %.6f, want 2.200977", row.FairEst)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.200977 || row.RealtimePremium == nil {
		t.Fatalf("realtime estimate = %+v premium=%+v, want futures realtime estimate", row.RealtimeEst, row.RealtimePremium)
	}
	if row.EffectiveRatio != 0.7975 {
		t.Fatalf("effective_ratio = %.6f, want 0.797500", row.EffectiveRatio)
	}
	if !strings.Contains(row.Note, "商品有效仓位 79.75%") {
		t.Fatalf("note = %q, want effective commodity ratio", row.Note)
	}
	if !strings.Contains(row.Note, "使用 4/4 条估值腿") {
		t.Fatalf("note = %q, want leg coverage", row.Note)
	}
}

func TestCommodityBasketEstimateUses160216FutureCompositeLegs(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ160216"] = domain.NetValue{Symbol: "SZ160216", Date: "2026-06-01", NAV: 2.0}
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
	if row.ReferenceSymbol != "" {
		t.Fatalf("reference_symbol = %q, want empty for composite model", row.ReferenceSymbol)
	}
	if row.OfficialEst != 2.1355 {
		t.Fatalf("official_est = %.6f, want 2.135500", row.OfficialEst)
	}
	if row.FairEst != 2.1355 {
		t.Fatalf("fair_est = %.6f, want 2.135500", row.FairEst)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.1355 || row.RealtimePremium == nil {
		t.Fatalf("realtime estimate = %+v premium=%+v, want futures realtime estimate", row.RealtimeEst, row.RealtimePremium)
	}
	if row.EffectiveRatio != 0.6775 {
		t.Fatalf("effective_ratio = %.6f, want 0.677500", row.EffectiveRatio)
	}
	if !strings.Contains(row.Note, "商品有效仓位 67.75%") {
		t.Fatalf("note = %q, want effective commodity ratio", row.Note)
	}
	if !strings.Contains(row.Note, "使用 3/3 条估值腿") {
		t.Fatalf("note = %q, want leg coverage", row.Note)
	}
}

func TestWeightedAnchorEstimateAppliesCentralParityRatio(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH501018"] = domain.NetValue{Symbol: "SH501018", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.0, 7.2)
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
	if row.FairEst != 2.210286 {
		t.Fatalf("fair_est = %.6f, want 2.210286 with FX ratio", row.FairEst)
	}
	if row.RealtimeEst == nil || *row.RealtimeEst != 2.210286 {
		t.Fatalf("realtime_est = %+v, want 2.210286 with FX ratio", row.RealtimeEst)
	}
	if !strings.Contains(row.Note, "USDCNY 7.000000/7.200000") {
		t.Fatalf("note = %q, want FX note", row.Note)
	}
}

func TestCommodityBasketEstimateAppliesCentralParityRatio(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ161815"] = domain.NetValue{Symbol: "SZ161815", Date: "2026-06-01", NAV: 2.0}
	seedUSDCNY(&data, "2026-06-01", "2026-06-03", 7.0, 7.2)
	for _, symbol := range []string{"HF_GC", "HF_CL", "HF_SI", "HF_HG"} {
		data.DailyPricesByDate[symbol] = map[string]domain.DailyPrice{
			"2026-06-01": {Symbol: symbol, Date: "2026-06-01", Close: 100, AdjClose: 100},
		}
	}
	quotes := map[string]domain.Quote{
		"HF_GC": {Symbol: "HF_GC", Price: 110, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_CL": {Symbol: "HF_CL", Price: 120, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_SI": {Symbol: "HF_SI", Price: 90, Source: "sina_hf", QuoteSession: "global_future"},
		"HF_HG": {Symbol: "HF_HG", Price: 95, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "SZ161815", Price: 2.21}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.FairEst != 2.252291 {
		t.Fatalf("fair_est = %.6f, want 2.252291 with FX ratio", row.FairEst)
	}
	if row.OfficialEst != 2.252291 {
		t.Fatalf("official_est = %.6f, want 2.252291 with FX ratio", row.OfficialEst)
	}
	if !strings.Contains(row.Note, "GC USDCNY 7.000000/7.200000") {
		t.Fatalf("note = %q, want FX note", row.Note)
	}
}

func TestCommodityFutureEstimateUsesLatestNAVDateAsFutureBase(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.CurrentHoldingDates["OIL"] = domain.HoldingDate{FundSymbol: "OIL", Date: "2026-05-29"}
	data.NetValuesByDate["OIL"] = map[string]domain.NetValue{
		"2026-05-29": {Symbol: "OIL", Date: "2026-05-29", NAV: 1},
		"2026-06-01": {Symbol: "OIL", Date: "2026-06-01", NAV: 2},
	}
	data.LatestNetValues["OIL"] = data.NetValuesByDate["OIL"]["2026-06-01"]
	data.Holdings["OIL"] = []domain.Holding{
		{FundSymbol: "OIL", HoldingDate: "2026-05-29", HoldingSymbol: "USO", Ratio: 100},
	}
	data.DailyPricesByDate["USO"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "USO", Date: "2026-05-29", Close: 100, AdjClose: 100},
		"2026-06-01": {Symbol: "USO", Date: "2026-06-01", Close: 200, AdjClose: 200},
	}
	data.DailyPricesByDate["HF_CL"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "HF_CL", Date: "2026-05-29", Close: 50, AdjClose: 50},
		"2026-06-01": {Symbol: "HF_CL", Date: "2026-06-01", Close: 100, AdjClose: 100},
	}
	quotes := map[string]domain.Quote{
		"USO":   {Symbol: "USO", Price: 220, PrevClose: 210, Source: "sina_us"},
		"HF_CL": {Symbol: "HF_CL", Price: 110, PrevClose: 100, Source: "sina_hf"},
	}

	row := engine.Estimate(domain.Quote{Symbol: "OIL", Price: 2.3}, quotes, data, time.Date(2026, 6, 3, 10, 0, 0, 0, time.Local))
	if row.ModelVersion != "v1.commodity_futures.cn" {
		t.Fatalf("model = %s, want commodity futures", row.ModelVersion)
	}
	if row.FairEst != 2.42 {
		t.Fatalf("fair_est = %.6f, want 2.42 from latest NAV date futures base", row.FairEst)
	}
	if !strings.Contains(row.Note, "净值基准日 2026-06-01") {
		t.Fatalf("note = %q, want latest NAV base date", row.Note)
	}
}

func TestCommodityFutureEstimateReplacesUSOWithCL(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.CurrentHoldingDates["OIL"] = domain.HoldingDate{FundSymbol: "OIL", Date: "2026-05-29"}
	data.NetValuesByDate["OIL"] = map[string]domain.NetValue{
		"2026-05-29": {Symbol: "OIL", Date: "2026-05-29", NAV: 1},
	}
	data.LatestNetValues["OIL"] = data.NetValuesByDate["OIL"]["2026-05-29"]
	data.Holdings["OIL"] = []domain.Holding{
		{FundSymbol: "OIL", HoldingDate: "2026-05-29", HoldingSymbol: "USO", Ratio: 100, FXAdjust: 0.9998},
	}
	data.DailyPricesByDate["USO"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "USO", Date: "2026-05-29", Close: 129.09, AdjClose: 129.09},
	}
	data.DailyPricesByDate["HF_CL"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "HF_CL", Date: "2026-05-29", Close: 92.16, AdjClose: 92.16, Source: "sina_daily_close:us"},
	}
	quotes := map[string]domain.Quote{
		"USO":   {Symbol: "USO", Price: 134.9, PrevClose: 135.5, Source: "sina_us_after_hours"},
		"HF_CL": {Symbol: "HF_CL", Price: 91.175, PrevClose: 92.16, Source: "sina_hf", IsRealtime: false},
	}

	row := engine.Estimate(domain.Quote{Symbol: "OIL", Price: 1.1}, quotes, data, time.Date(2026, 6, 2, 14, 30, 0, 0, time.Local))
	if row.ModelVersion != "v1.commodity_futures.cn" {
		t.Fatalf("model = %s, want commodity futures", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_CL" {
		t.Fatalf("reference_symbol = %q, want HF_CL", row.ReferenceSymbol)
	}
	if !strings.Contains(row.Note, "CL") {
		t.Fatalf("note = %q, want CL", row.Note)
	}
}

func TestCommodityFutureEstimateUsesUSCloseFutureBaseInsteadOfFuturePrevClose(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.CurrentHoldingDates["OIL"] = domain.HoldingDate{FundSymbol: "OIL", Date: "2026-05-29"}
	data.NetValuesByDate["OIL"] = map[string]domain.NetValue{
		"2026-05-29": {Symbol: "OIL", Date: "2026-05-29", NAV: 1},
	}
	data.LatestNetValues["OIL"] = data.NetValuesByDate["OIL"]["2026-05-29"]
	data.Holdings["OIL"] = []domain.Holding{
		{FundSymbol: "OIL", HoldingDate: "2026-05-29", HoldingSymbol: "USO", Ratio: 100},
	}
	data.DailyPricesByDate["USO"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "USO", Date: "2026-05-29", Close: 100, AdjClose: 100},
	}
	data.DailyPricesByDate["HF_CL"] = map[string]domain.DailyPrice{
		"2026-05-29": {Symbol: "HF_CL", Date: "2026-05-29", Close: 80, AdjClose: 80, Source: "sina_daily_close:us"},
	}
	quotes := map[string]domain.Quote{
		"USO":   {Symbol: "USO", Price: 100, PrevClose: 100, Source: "sina_us"},
		"HF_CL": {Symbol: "HF_CL", Price: 88, PrevClose: 999, Source: "sina_hf", IsRealtime: false},
	}

	row := engine.Estimate(domain.Quote{Symbol: "OIL", Price: 1}, quotes, data, time.Date(2026, 6, 2, 14, 30, 0, 0, time.Local))
	if row.ModelVersion != "v1.commodity_futures.cn" {
		t.Fatalf("model = %s, want commodity futures", row.ModelVersion)
	}
	if row.FairEst != 1.1 {
		t.Fatalf("fair_est = %.6f, want 1.100000 from 88/80, not future prev_close", row.FairEst)
	}
}

func TestFundPairSkipsMismatchedCalibrationScale(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.FundPairs["FUND"] = []domain.FundPair{
		{FundSymbol: "FUND", PairSymbol: "ETF", PairType: "seed"},
		{FundSymbol: "FUND", PairSymbol: "INDEX", PairType: "palmmicro"},
	}
	data.LatestCalibrations["FUND"] = domain.Calibration{
		Symbol:    "FUND",
		Date:      "2026-06-01",
		Factor:    1000,
		BaseValue: 5,
		Source:    "palmmicro",
	}
	quotes := map[string]domain.Quote{
		"ETF":   {Symbol: "ETF", Price: 5, Source: "sina"},
		"INDEX": {Symbol: "INDEX", Price: 5000, Source: "sina"},
	}

	row, ok := engine.estimateFromFundPair(
		domain.Quote{Symbol: "FUND", Price: 5.1, Source: "sina"},
		quotes,
		data,
		time.Date(2026, 6, 3, 12, 0, 0, 0, time.Local),
	)
	if !ok {
		t.Fatal("expected fund-pair estimate")
	}
	if row.FairEst != 5 {
		t.Fatalf("fair_est = %.6f, want 5", row.FairEst)
	}
	if row.RealtimeEst != nil || row.RealtimePremium != nil {
		t.Fatalf("realtime estimate should be nil without an independent realtime source")
	}
	if row.ModelVersion != "v1.fundpair.calibrated" {
		t.Fatalf("model = %s, want calibrated", row.ModelVersion)
	}
	if !strings.Contains(row.Note, "配对标的 INDEX") {
		t.Fatalf("note = %q, want INDEX pair", row.Note)
	}
}

func TestFundPairUsesPreviousCloseBootstrap(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.FundPairs["FUND"] = []domain.FundPair{
		{FundSymbol: "FUND", PairSymbol: "QQQ", PairType: "qdii_us_static"},
	}
	quotes := map[string]domain.Quote{
		"QQQ": {Symbol: "QQQ", Price: 110, PrevClose: 100, Source: "sina_us", IsRealtime: true, RealtimeStatus: "realtime"},
	}

	row, ok := engine.estimateFromFundPair(
		domain.Quote{Symbol: "FUND", Price: 10, PrevClose: 9, Source: "sina"},
		quotes,
		data,
		time.Date(2026, 6, 2, 12, 0, 0, 0, time.Local),
	)
	if !ok {
		t.Fatal("expected fund-pair estimate")
	}
	if row.FairEst != 9.9 {
		t.Fatalf("fair_est = %.6f, want 9.9", row.FairEst)
	}
	if row.RealtimeEst != nil || row.RealtimePremium != nil {
		t.Fatalf("realtime estimate should be nil without an independent realtime source")
	}
	if row.OfficialPremium != 11.111111 || row.FairPremium != 1.010101 {
		t.Fatalf("premium = %.6f/%.6f, want 11.111111/1.010101", row.OfficialPremium, row.FairPremium)
	}
	if row.ModelVersion != "v1.fundpair.prev_close_bootstrap" {
		t.Fatalf("model = %s, want prev_close_bootstrap", row.ModelVersion)
	}
}

func TestFundPairLooksUpZNBQuoteCaseInsensitively(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.FundPairs["FUND"] = []domain.FundPair{
		{FundSymbol: "FUND", PairSymbol: "znb_NKY", PairType: "qdii_jp_static"},
	}
	quotes := map[string]domain.Quote{
		"ZNB_NKY": {Symbol: "ZNB_NKY", Price: 1100, PrevClose: 1000, Source: "sina_znb"},
	}

	row, ok := engine.estimateFromFundPair(
		domain.Quote{Symbol: "FUND", Price: 10, PrevClose: 9, Source: "sina"},
		quotes,
		data,
		time.Date(2026, 6, 2, 12, 0, 0, 0, time.Local),
	)
	if !ok {
		t.Fatal("expected znb fund-pair estimate")
	}
	if row.FairEst != 9.9 {
		t.Fatalf("fair_est = %.6f, want 9.9", row.FairEst)
	}
}

func TestStaticFundPairIgnoresCalibration(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.FundPairs["SH513010"] = []domain.FundPair{
		{FundSymbol: "SH513010", PairSymbol: "03032", PairType: "qdii_hk_static"},
	}
	data.LatestCalibrations["SH513010"] = domain.Calibration{
		Symbol:    "SH513010",
		Date:      "2026-06-01",
		Factor:    2,
		BaseValue: 9,
		Source:    "seed",
	}
	quotes := map[string]domain.Quote{
		"03032": {Symbol: "03032", Price: 110, PrevClose: 100, Source: "sina_hk"},
	}

	row, ok := engine.estimateFromFundPair(
		domain.Quote{Symbol: "SH513010", Price: 10, PrevClose: 9, Source: "sina"},
		quotes,
		data,
		time.Date(2026, 6, 2, 12, 0, 0, 0, time.Local),
	)
	if !ok {
		t.Fatal("expected static fund-pair estimate")
	}
	if row.FairEst != 9.9 {
		t.Fatalf("fair_est = %.6f, want prev-close bootstrap 9.9", row.FairEst)
	}
	if row.ModelVersion != "v1.fundpair.prev_close_bootstrap" {
		t.Fatalf("model = %s, want prev_close_bootstrap", row.ModelVersion)
	}
	if row.RealtimeEst == nil || row.RealtimePremium == nil {
		t.Fatalf("expected realtime estimate for hk pair source")
	}
}

func TestStaticFundPairUsesMatchingAutoDailyCalibration(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.FundPairs["FUND"] = []domain.FundPair{
		{FundSymbol: "FUND", PairSymbol: "QQQ", PairType: "qdii_us_static"},
	}
	data.LatestCalibrations["FUND"] = domain.Calibration{
		Symbol:    "FUND",
		Date:      "2026-06-01",
		Factor:    50,
		BaseValue: 10,
		Source:    "auto_daily:QQQ",
	}
	quotes := map[string]domain.Quote{
		"QQQ": {Symbol: "QQQ", Price: 550, PrevClose: 500, Source: "sina_us"},
	}

	row, ok := engine.estimateFromFundPair(
		domain.Quote{Symbol: "FUND", Price: 11, PrevClose: 10, Source: "sina"},
		quotes,
		data,
		time.Date(2026, 6, 3, 12, 0, 0, 0, time.Local),
	)
	if !ok {
		t.Fatal("expected static fund-pair estimate")
	}
	if row.FairEst != 11 {
		t.Fatalf("fair_est = %.6f, want 11", row.FairEst)
	}
	if row.ModelVersion != "v1.fundpair.auto_daily" {
		t.Fatalf("model = %s, want auto_daily", row.ModelVersion)
	}
}

func TestStaticFundPairWithNQUsesRealtimeEstimate(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.FundPairs["SH513100"] = []domain.FundPair{
		{FundSymbol: "SH513100", PairSymbol: "HF_NQ", PairType: "qdii_us_static"},
	}
	data.LatestNetValues["SH513100"] = domain.NetValue{
		Symbol: "SH513100",
		Date:   "2026-06-02",
		NAV:    1.7664,
		Source: "official",
	}
	data.DailyPricesByDate["HF_NQ"] = map[string]domain.DailyPrice{
		"2026-06-02": {Symbol: "HF_NQ", Date: "2026-06-02", Close: 30600, AdjClose: 30600, Source: "ibkr_future_1m"},
	}
	quotes := map[string]domain.Quote{
		"HF_NQ": {Symbol: "HF_NQ", Price: 30906, PrevClose: 30707.195, Source: "sina_hf", QuoteSession: "global_future"},
	}

	row, ok := engine.estimateFromFundPair(
		domain.Quote{Symbol: "SH513100", Price: 1.80, PrevClose: 1.77, Source: "sina"},
		quotes,
		data,
		time.Date(2026, 6, 4, 10, 0, 0, 0, time.Local),
	)
	if !ok {
		t.Fatal("expected NQ fund-pair estimate")
	}
	if row.ReferenceSymbol != "HF_NQ" {
		t.Fatalf("reference_symbol = %q, want HF_NQ", row.ReferenceSymbol)
	}
	if row.ModelVersion != "v1.fundpair.daily_factor" {
		t.Fatalf("model = %s, want daily_factor", row.ModelVersion)
	}
	if row.RealtimeEst == nil || row.RealtimePremium == nil {
		t.Fatalf("expected realtime estimate for HF_NQ pair source")
	}
}

func TestAutoDailyCalibrationDoesNotApplyToDifferentPair(t *testing.T) {
	engine := NewEngine()
	data := domain.EmptyValuationData()
	data.FundPairs["FUND"] = []domain.FundPair{
		{FundSymbol: "FUND", PairSymbol: "SPY", PairType: "qdii_us_static"},
	}
	data.LatestCalibrations["FUND"] = domain.Calibration{
		Symbol:    "FUND",
		Date:      "2026-06-01",
		Factor:    50,
		BaseValue: 10,
		Source:    "auto_daily:QQQ",
	}
	quotes := map[string]domain.Quote{
		"SPY": {Symbol: "SPY", Price: 110, PrevClose: 100, Source: "sina_us"},
	}

	row, ok := engine.estimateFromFundPair(
		domain.Quote{Symbol: "FUND", Price: 10, PrevClose: 9, Source: "sina"},
		quotes,
		data,
		time.Date(2026, 6, 2, 12, 0, 0, 0, time.Local),
	)
	if !ok {
		t.Fatal("expected fund-pair estimate through fallback")
	}
	if row.ModelVersion != "v1.fundpair.prev_close_bootstrap" {
		t.Fatalf("model = %s, want prev_close_bootstrap", row.ModelVersion)
	}
}
