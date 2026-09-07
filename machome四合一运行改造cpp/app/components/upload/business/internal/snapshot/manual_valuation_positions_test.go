package snapshot

import (
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func findNavSettingsRow(t *testing.T, rows []NavSettingsRatioStatus, symbol string) NavSettingsRatioStatus {
	t.Helper()
	for _, row := range rows {
		if row.Symbol == symbol {
			return row
		}
	}
	t.Fatalf("missing row for %s", symbol)
	return NavSettingsRatioStatus{}
}

func TestNavSettingsStatusIncludesManualOverrideMetadata(t *testing.T) {
	service := NewService(ServiceOptions{})
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.ManualPositionOverrides["SH501018"] = domain.ManualValuationPositionOverride{
		Symbol:    "SH501018",
		Ratio:     0.73,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: time.Date(2026, 6, 19, 9, 30, 0, 0, time.UTC),
	}
	service.quotes["SH501018"] = domain.Quote{Symbol: "SH501018", Name: "南方原油"}

	status := service.NavSettingsStatus(time.Date(2026, 6, 19, 9, 35, 0, 0, time.UTC))
	row := findNavSettingsRow(t, status.Ratios, "SH501018")

	if row.Name != "南方原油" {
		t.Fatalf("name = %q, want 南方原油", row.Name)
	}
	if row.EffectiveRatio != 0.73 {
		t.Fatalf("effective_ratio = %.6f, want 0.730000", row.EffectiveRatio)
	}
	if row.DefaultEffectiveRatio != 0.8 {
		t.Fatalf("default_effective_ratio = %.6f, want 0.800000", row.DefaultEffectiveRatio)
	}
	if row.EffectiveRatioSource != domain.EffectiveRatioSourceManualOverride {
		t.Fatalf("effective_ratio_source = %q, want %q", row.EffectiveRatioSource, domain.EffectiveRatioSourceManualOverride)
	}
	if row.ManualOverrideRatio == nil || *row.ManualOverrideRatio != 0.73 {
		t.Fatalf("manual_override_ratio = %+v, want 0.73", row.ManualOverrideRatio)
	}
	if row.ManualOverrideSource != "debug_ws:home-mac" {
		t.Fatalf("manual_override_source = %q, want debug_ws:home-mac", row.ManualOverrideSource)
	}
	if row.ManualOverrideUpdatedBy != "Dirac" {
		t.Fatalf("manual_override_updated_by = %q, want Dirac", row.ManualOverrideUpdatedBy)
	}
	if row.ManualOverrideUpdatedAt == "" {
		t.Fatal("manual_override_updated_at should be populated")
	}
}

func TestNavSettingsStatusIncludesMissingHoldingsAndCommodityFutureFunds(t *testing.T) {
	service := NewService(ServiceOptions{})
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.ManualPositionOverrides["SH501312"] = domain.ManualValuationPositionOverride{
		Symbol:    "SH501312",
		Ratio:     0.66,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: time.Date(2026, 6, 19, 9, 30, 0, 0, time.UTC),
	}
	service.valuationData.ManualPositionOverrides["SZ161226"] = domain.ManualValuationPositionOverride{
		Symbol:    "SZ161226",
		Ratio:     0.82,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: time.Date(2026, 6, 19, 9, 31, 0, 0, time.UTC),
	}
	service.valuationData.CurrentHoldingDates["SH501312"] = domain.HoldingDate{FundSymbol: "SH501312", Date: "2026-06-02"}
	service.valuationData.Holdings["SH501312"] = []domain.Holding{
		{FundSymbol: "SH501312", HoldingDate: "2026-06-02", HoldingSymbol: "QQQ", Ratio: 100},
	}
	service.valuationData.CurrentHoldingDates["SZ161226"] = domain.HoldingDate{FundSymbol: "SZ161226", Date: "2026-06-02"}
	service.valuationData.Holdings["SZ161226"] = []domain.Holding{
		{FundSymbol: "SZ161226", HoldingDate: "2026-06-02", HoldingSymbol: "USO", Ratio: 100},
	}
	service.quotes["SH501312"] = domain.Quote{Symbol: "SH501312", Name: "华宝海外科技"}
	service.quotes["SZ161226"] = domain.Quote{Symbol: "SZ161226", Name: "国投瑞银白银"}

	status := service.NavSettingsStatus(time.Date(2026, 6, 19, 9, 35, 0, 0, time.UTC))

	holdingsRow := findNavSettingsRow(t, status.Ratios, "SH501312")
	if holdingsRow.ModelVersion != "v1.holdings.cn" {
		t.Fatalf("SH501312 model_version = %q, want v1.holdings.cn", holdingsRow.ModelVersion)
	}
	if holdingsRow.EffectiveRatio != 0.66 {
		t.Fatalf("SH501312 effective_ratio = %.6f, want 0.660000", holdingsRow.EffectiveRatio)
	}
	if holdingsRow.DefaultEffectiveRatio != 1 {
		t.Fatalf("SH501312 default_effective_ratio = %.6f, want 1.000000", holdingsRow.DefaultEffectiveRatio)
	}

	commodityFutureRow := findNavSettingsRow(t, status.Ratios, "SZ161226")
	if commodityFutureRow.ModelVersion != "v1.commodity_futures.cn" {
		t.Fatalf("SZ161226 model_version = %q, want v1.commodity_futures.cn", commodityFutureRow.ModelVersion)
	}
	if commodityFutureRow.ReferenceSymbol != "HF_CL" {
		t.Fatalf("SZ161226 reference_symbol = %q, want HF_CL", commodityFutureRow.ReferenceSymbol)
	}
	if commodityFutureRow.EffectiveRatio != 0.82 {
		t.Fatalf("SZ161226 effective_ratio = %.6f, want 0.820000", commodityFutureRow.EffectiveRatio)
	}
}

func TestRecomputeTodayMinuteHistoryUsesManualOverrideRatio(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 6, 3, 14, 0, 0, 0, shanghaiLocation())
	service := NewService(ServiceOptions{MinuteHistoryDir: root})
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.LatestNetValues["SH501018"] = domain.NetValue{
		Symbol: "SH501018",
		Date:   "2026-06-01",
		NAV:    2.0,
	}
	service.valuationData.ManualPositionOverrides["SH501018"] = domain.ManualValuationPositionOverride{
		Symbol:    "SH501018",
		Ratio:     0.73,
		Source:    "debug_ws:home-mac",
		UpdatedBy: "Dirac",
		UpdatedAt: now,
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
		EffectiveRatio:  0.8,
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

	rows, err := service.minuteHistory.Read("SH501018", 1, now)
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
	if row.OfficialEST != 2.0 {
		t.Fatalf("official_est = %.6f, want 2.000000", row.OfficialEST)
	}
	if row.EstimatedNAV != 2.1825 {
		t.Fatalf("estimated_nav = %.6f, want 2.182500", row.EstimatedNAV)
	}
	if row.FairEST != 2.1825 {
		t.Fatalf("fair_est = %.6f, want 2.182500", row.FairEST)
	}
	if row.PremiumPct != 5.383734 {
		t.Fatalf("premium_pct = %.6f, want 5.383734", row.PremiumPct)
	}
	if row.UploadSource != "navsettings_recompute" {
		t.Fatalf("upload_source = %q, want navsettings_recompute", row.UploadSource)
	}
}
