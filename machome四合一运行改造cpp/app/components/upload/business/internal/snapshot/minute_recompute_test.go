package snapshot

import (
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func ptrFloat(value float64) *float64 {
	return &value
}

func TestRecomputeTodayMinuteHistoryWeightedAnchor(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 6, 3, 14, 0, 0, 0, shanghaiLocation())
	service := NewService(ServiceOptions{MinuteHistoryDir: root})
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.LatestNetValues["SH501018"] = domain.NetValue{
		Symbol: "SH501018",
		Date:   "2026-06-01",
		NAV:    2.0,
	}

	if err := service.minuteHistory.Upsert(now, []MinuteHistoryPoint{{
		Symbol:          "SH501018",
		Minute:          "2026-06-03 14:00",
		Timestamp:       now.Format(time.RFC3339Nano),
		MarketPrice:     2.3,
		EstimatedNAV:    2.2,
		PremiumPct:      4.545455,
		OfficialEST:     2.0,
		FairEST:         2.2,
		ModelVersion:    "v1.weighted_anchor.cl",
		ReferenceSymbol: "HF_CL",
	}}); err != nil {
		t.Fatal(err)
	}

	result, err := service.RecomputeTodayMinuteHistory(now)
	if err != nil {
		t.Fatal(err)
	}
	if !result.OK {
		t.Fatalf("expected recompute ok, got %+v", result)
	}
	if result.UpdatedRows != 1 {
		t.Fatalf("updated_rows = %d, want 1", result.UpdatedRows)
	}

	rows, err := service.minuteHistory.Read("SH501018", 1, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	row := rows[0]
	if row.EffectiveRatio != 0.8 {
		t.Fatalf("effective_ratio = %.6f, want 0.800000", row.EffectiveRatio)
	}
	if row.EstimatedNAV != 2.16 {
		t.Fatalf("estimated_nav = %.6f, want 2.160000", row.EstimatedNAV)
	}
	if row.FairEST != 2.16 {
		t.Fatalf("fair_est = %.6f, want 2.160000", row.FairEST)
	}
	if row.OfficialEST != 2.0 {
		t.Fatalf("official_est = %.6f, want 2.000000", row.OfficialEST)
	}
	if row.PremiumPct != 6.481481 {
		t.Fatalf("premium_pct = %.6f, want 6.481481", row.PremiumPct)
	}
	if row.UploadSource != "navsettings_recompute" {
		t.Fatalf("upload_source = %q, want navsettings_recompute", row.UploadSource)
	}
}

func TestCurrentMinuteHistoryRatioConfigCoversAllValuationPaths(t *testing.T) {
	data := domain.EmptyValuationData()
	data.CurrentHoldingDates["SH501312"] = domain.HoldingDate{FundSymbol: "SH501312", Date: "2026-06-02"}
	data.Holdings["SH501312"] = []domain.Holding{
		{FundSymbol: "SH501312", HoldingDate: "2026-06-02", HoldingSymbol: "QQQ", Ratio: 100},
	}
	data.CurrentHoldingDates["SZ161226"] = domain.HoldingDate{FundSymbol: "SZ161226", Date: "2026-06-02"}
	data.Holdings["SZ161226"] = []domain.Holding{
		{FundSymbol: "SZ161226", HoldingDate: "2026-06-02", HoldingSymbol: "USO", Ratio: 100},
	}
	data.FundPairs["SZ159659"] = []domain.FundPair{
		{FundSymbol: "SZ159659", PairSymbol: "HF_NQ", PairType: "qdii_us_static"},
	}

	cases := []struct {
		symbol        string
		wantModel     string
		wantReference string
		wantDefault   float64
	}{
		{symbol: "SH501018", wantModel: "v1.weighted_anchor.cl", wantReference: "HF_CL", wantDefault: 0.8},
		{symbol: "SZ160216", wantModel: "v1.commodity_basket.cn", wantDefault: 0.6775},
		{symbol: "SZ161226", wantModel: "v1.commodity_futures.cn", wantReference: "HF_CL", wantDefault: 1},
		{symbol: "SH501312", wantModel: "v1.holdings.cn", wantDefault: 1},
		{symbol: "SZ159659", wantModel: "v1.fundpair", wantReference: "HF_NQ", wantDefault: 1},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.symbol, func(t *testing.T) {
			config, ok := currentMinuteHistoryRatioConfig(data, tc.symbol)
			if !ok {
				t.Fatalf("expected config for %s", tc.symbol)
			}
			if config.ModelVersion != tc.wantModel {
				t.Fatalf("model_version = %q, want %q", config.ModelVersion, tc.wantModel)
			}
			if config.ReferenceSymbol != tc.wantReference {
				t.Fatalf("reference_symbol = %q, want %q", config.ReferenceSymbol, tc.wantReference)
			}
			if config.DefaultRatio != tc.wantDefault {
				t.Fatalf("default_ratio = %.6f, want %.6f", config.DefaultRatio, tc.wantDefault)
			}
		})
	}
}

func TestRecomputeTodayMinuteHistoryFundPairUsesConfiguredRatio(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 6, 3, 14, 0, 0, 0, time.Local)
	service := NewService(ServiceOptions{MinuteHistoryDir: root})
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.Positions["SZ159659"] = 0.996659
	service.valuationData.LatestNetValues["SZ159659"] = domain.NetValue{
		Symbol: "SZ159659",
		Date:   "2026-06-02",
		NAV:    2.2466,
	}
	service.valuationData.FundPairs["SZ159659"] = []domain.FundPair{
		{FundSymbol: "SZ159659", PairSymbol: "HF_NQ", PairType: "qdii_us_static"},
	}

	if err := service.minuteHistory.Upsert(now, []MinuteHistoryPoint{{
		Symbol:          "SZ159659",
		Minute:          "2026-06-03 14:00",
		Timestamp:       now.Format(time.RFC3339Nano),
		MarketPrice:     2.406,
		EstimatedNAV:    2.291532,
		PremiumPct:      5.0,
		OfficialEST:     2.2466,
		FairEST:         2.291532,
		RealtimeEST:     ptrFloat(2.291532),
		ModelVersion:    "v1.fundpair.daily_factor",
		ReferenceSymbol: "HF_NQ",
		EffectiveRatio:  1.0,
	}}); err != nil {
		t.Fatal(err)
	}

	result, err := service.RecomputeTodayMinuteHistory(now)
	if err != nil {
		t.Fatal(err)
	}
	if !result.OK || result.UpdatedRows != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}

	rows, err := service.minuteHistory.Read("SZ159659", 1, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	row := rows[0]
	if row.EffectiveRatio != 0.996659 {
		t.Fatalf("effective_ratio = %.6f, want 0.996659", row.EffectiveRatio)
	}
	if row.ModelVersion != "v1.fundpair.daily_factor" {
		t.Fatalf("model_version = %q, want v1.fundpair.daily_factor", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_NQ" {
		t.Fatalf("reference_symbol = %q, want HF_NQ", row.ReferenceSymbol)
	}
	if row.RealtimeEST == nil {
		t.Fatal("realtime_est should remain set")
	}
}

func TestRecomputeTodayMinuteHistoryHoldingsUsesManualOverrideRatio(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 6, 3, 14, 0, 0, 0, shanghaiLocation())
	service := NewService(ServiceOptions{MinuteHistoryDir: root})
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.LatestNetValues["SH501312"] = domain.NetValue{
		Symbol: "SH501312",
		Date:   "2026-06-02",
		NAV:    100,
	}
	service.valuationData.CurrentHoldingDates["SH501312"] = domain.HoldingDate{FundSymbol: "SH501312", Date: "2026-06-02"}
	service.valuationData.Holdings["SH501312"] = []domain.Holding{
		{FundSymbol: "SH501312", HoldingDate: "2026-06-02", HoldingSymbol: "QQQ", Ratio: 100},
	}
	service.valuationData.ManualPositionOverrides["SH501312"] = domain.ManualValuationPositionOverride{
		Symbol:    "SH501312",
		Ratio:     0.73,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: now,
	}

	if err := service.minuteHistory.Upsert(now, []MinuteHistoryPoint{{
		Symbol:         "SH501312",
		Minute:         "2026-06-03 14:00",
		Timestamp:      now.Format(time.RFC3339Nano),
		MarketPrice:    109,
		EstimatedNAV:   110,
		PremiumPct:     -0.909091,
		OfficialEST:    110,
		FairEST:        110,
		ModelVersion:   "v1.holdings.cn",
		EffectiveRatio: 1.0,
	}}); err != nil {
		t.Fatal(err)
	}

	result, err := service.RecomputeTodayMinuteHistory(now)
	if err != nil {
		t.Fatal(err)
	}
	if !result.OK || result.UpdatedRows != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}

	rows, err := service.minuteHistory.Read("SH501312", 1, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	row := rows[0]
	if row.EffectiveRatio != 0.73 {
		t.Fatalf("effective_ratio = %.6f, want 0.730000", row.EffectiveRatio)
	}
	if row.EstimatedNAV != 107.3 {
		t.Fatalf("estimated_nav = %.6f, want 107.300000", row.EstimatedNAV)
	}
	if row.ModelVersion != "v1.holdings.cn" {
		t.Fatalf("model_version = %q, want v1.holdings.cn", row.ModelVersion)
	}
}

func TestRecomputeTodayMinuteHistoryCommodityFuturesUsesManualOverrideRatio(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 6, 3, 14, 0, 0, 0, shanghaiLocation())
	service := NewService(ServiceOptions{MinuteHistoryDir: root})
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.LatestNetValues["SZ161226"] = domain.NetValue{
		Symbol: "SZ161226",
		Date:   "2026-06-02",
		NAV:    1,
	}
	service.valuationData.CurrentHoldingDates["SZ161226"] = domain.HoldingDate{FundSymbol: "SZ161226", Date: "2026-06-02"}
	service.valuationData.Holdings["SZ161226"] = []domain.Holding{
		{FundSymbol: "SZ161226", HoldingDate: "2026-06-02", HoldingSymbol: "USO", Ratio: 100},
	}
	service.valuationData.ManualPositionOverrides["SZ161226"] = domain.ManualValuationPositionOverride{
		Symbol:    "SZ161226",
		Ratio:     0.8,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: now,
	}

	if err := service.minuteHistory.Upsert(now, []MinuteHistoryPoint{{
		Symbol:          "SZ161226",
		Minute:          "2026-06-03 14:00",
		Timestamp:       now.Format(time.RFC3339Nano),
		MarketPrice:     1.09,
		EstimatedNAV:    1.1,
		PremiumPct:      -0.909091,
		OfficialEST:     1.1,
		FairEST:         1.1,
		ModelVersion:    "v1.commodity_futures.cn",
		ReferenceSymbol: "HF_CL",
		EffectiveRatio:  1.0,
	}}); err != nil {
		t.Fatal(err)
	}

	result, err := service.RecomputeTodayMinuteHistory(now)
	if err != nil {
		t.Fatal(err)
	}
	if !result.OK || result.UpdatedRows != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}

	rows, err := service.minuteHistory.Read("SZ161226", 1, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	row := rows[0]
	if row.EffectiveRatio != 0.8 {
		t.Fatalf("effective_ratio = %.6f, want 0.800000", row.EffectiveRatio)
	}
	if row.EstimatedNAV != 1.08 {
		t.Fatalf("estimated_nav = %.6f, want 1.080000", row.EstimatedNAV)
	}
	if row.ModelVersion != "v1.commodity_futures.cn" {
		t.Fatalf("model_version = %q, want v1.commodity_futures.cn", row.ModelVersion)
	}
	if row.ReferenceSymbol != "HF_CL" {
		t.Fatalf("reference_symbol = %q, want HF_CL", row.ReferenceSymbol)
	}
}
