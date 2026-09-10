package ratiofit

import (
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestBuildRowsSupportsHoldingsFunds(t *testing.T) {
	data := domain.EmptyValuationData()
	data.Positions["SH513050"] = 0.99
	data.CurrentHoldingDates["SH513050"] = domain.HoldingDate{FundSymbol: "SH513050", Date: "2026-06-01", Source: "palmmicro"}
	data.Holdings["SH513050"] = []domain.Holding{
		{FundSymbol: "SH513050", HoldingDate: "2026-06-01", HoldingSymbol: "00700", Ratio: 60, Currency: "HKD", Source: "palmmicro"},
		{FundSymbol: "SH513050", HoldingDate: "2026-06-01", HoldingSymbol: "PDD", Ratio: 40, Currency: "USD", Source: "palmmicro"},
	}

	hkPrices := map[string]float64{
		"2026-05-06": 100,
		"2026-05-07": 102,
		"2026-05-08": 101,
		"2026-05-09": 104,
	}
	usPrices := map[string]float64{
		"2026-05-06": 50,
		"2026-05-07": 51,
		"2026-05-08": 53,
		"2026-05-09": 54,
	}
	hkdCNY := map[string]float64{
		"2026-05-06": 0.92,
		"2026-05-07": 0.921,
		"2026-05-08": 0.919,
		"2026-05-09": 0.918,
	}
	usdCNY := map[string]float64{
		"2026-05-06": 7.20,
		"2026-05-07": 7.21,
		"2026-05-08": 7.22,
		"2026-05-09": 7.19,
	}
	data.DailyPricesByDate["00700"] = map[string]domain.DailyPrice{}
	data.DailyPricesByDate["PDD"] = map[string]domain.DailyPrice{}
	data.FXCentralParity["HKDCNY"] = map[string]domain.FXCentralParity{}
	data.FXCentralParity["USDCNY"] = map[string]domain.FXCentralParity{}

	dates := []string{"2026-05-06", "2026-05-07", "2026-05-08", "2026-05-09"}
	for _, day := range dates {
		data.DailyPricesByDate["00700"][day] = domain.DailyPrice{Symbol: "00700", Date: day, Close: hkPrices[day], AdjClose: hkPrices[day], Source: "test"}
		data.DailyPricesByDate["PDD"][day] = domain.DailyPrice{Symbol: "PDD", Date: day, Close: usPrices[day], AdjClose: usPrices[day], Source: "test"}
		data.FXCentralParity["HKDCNY"][day] = domain.FXCentralParity{Pair: "HKDCNY", Date: day, Rate: hkdCNY[day], Source: "safe"}
		data.FXCentralParity["USDCNY"][day] = domain.FXCentralParity{Pair: "USDCNY", Date: day, Rate: usdCNY[day], Source: "safe"}
	}

	nav := 1.0
	data.NetValuesByDate["SH513050"] = map[string]domain.NetValue{
		"2026-05-06": {Symbol: "SH513050", Date: "2026-05-06", NAV: nav, Source: "eastmoney", Confidence: "official"},
	}
	for index := 1; index < len(dates); index++ {
		targetDate := dates[index]
		baseDate := dates[index-1]
		sleeveMultiplier := 0.6*(hkPrices[targetDate]/hkPrices[baseDate])*(hkdCNY[targetDate]/hkdCNY[baseDate]) +
			0.4*(usPrices[targetDate]/usPrices[baseDate])*(usdCNY[targetDate]/usdCNY[baseDate])
		nav = nav * (0.01 + 0.99*sleeveMultiplier)
		data.NetValuesByDate["SH513050"][targetDate] = domain.NetValue{
			Symbol:     "SH513050",
			Date:       targetDate,
			NAV:        nav,
			Source:     "eastmoney",
			Confidence: "official",
		}
	}

	rows := BuildRows(data, BuildOptions{
		Today:              time.Date(2026, 5, 9, 12, 0, 0, 0, time.Local),
		WindowSize:         3,
		BaseLagTradingDays: 1,
		LookbackDays:       10,
		Candidates:         []float64{0.98, 0.99, 1.0},
	})

	if len(rows) != 4 {
		t.Fatalf("len(rows) = %d, want 4", len(rows))
	}
	last := rows[len(rows)-1]
	if last.Symbol != "SH513050" {
		t.Fatalf("last symbol = %q, want SH513050", last.Symbol)
	}
	if last.ModelVersion != "v1.holdings.cn" {
		t.Fatalf("model_version = %q, want v1.holdings.cn", last.ModelVersion)
	}
	if last.ReferenceSymbol != "holdings" {
		t.Fatalf("reference_symbol = %q, want holdings", last.ReferenceSymbol)
	}
	if last.Status != "ok" {
		t.Fatalf("status = %q, want ok", last.Status)
	}
	if last.FittedEffectiveRatio == nil || *last.FittedEffectiveRatio != 0.99 {
		t.Fatalf("fitted_effective_ratio = %v, want 0.99", last.FittedEffectiveRatio)
	}
	if last.ConfiguredEffectiveRatio != 0.99 {
		t.Fatalf("configured_effective_ratio = %.6f, want 0.99", last.ConfiguredEffectiveRatio)
	}
}

func TestBuildRowsSupportsFundPairFunds(t *testing.T) {
	data := domain.EmptyValuationData()
	data.Positions["SZ159659"] = 0.996659
	data.FundPairs["SZ159659"] = []domain.FundPair{
		{FundSymbol: "SZ159659", PairSymbol: "HF_NQ", PairType: "qdii_us_static"},
	}
	data.DailyPricesByDate["HF_NQ"] = map[string]domain.DailyPrice{
		"2026-05-06": {Symbol: "HF_NQ", Date: "2026-05-06", Close: 29000, AdjClose: 29000, Source: "ibkr"},
		"2026-05-07": {Symbol: "HF_NQ", Date: "2026-05-07", Close: 29290, AdjClose: 29290, Source: "ibkr"},
		"2026-05-08": {Symbol: "HF_NQ", Date: "2026-05-08", Close: 29143.55, AdjClose: 29143.55, Source: "ibkr"},
		"2026-05-09": {Symbol: "HF_NQ", Date: "2026-05-09", Close: 29434.9855, AdjClose: 29434.9855, Source: "ibkr"},
	}

	nav := 2.0
	data.NetValuesByDate["SZ159659"] = map[string]domain.NetValue{
		"2026-05-06": {Symbol: "SZ159659", Date: "2026-05-06", NAV: nav, Source: "eastmoney", Confidence: "official"},
	}
	dates := []string{"2026-05-06", "2026-05-07", "2026-05-08", "2026-05-09"}
	for index := 1; index < len(dates); index++ {
		targetDate := dates[index]
		baseDate := dates[index-1]
		multiplier := data.DailyPricesByDate["HF_NQ"][targetDate].AdjClose / data.DailyPricesByDate["HF_NQ"][baseDate].AdjClose
		nav = nav * ((1 - 0.996659) + 0.996659*multiplier)
		data.NetValuesByDate["SZ159659"][targetDate] = domain.NetValue{
			Symbol:     "SZ159659",
			Date:       targetDate,
			NAV:        nav,
			Source:     "eastmoney",
			Confidence: "official",
		}
	}

	rows := BuildRows(data, BuildOptions{
		Today:              time.Date(2026, 5, 9, 12, 0, 0, 0, time.Local),
		WindowSize:         3,
		BaseLagTradingDays: 1,
		LookbackDays:       10,
		Candidates:         []float64{0.99, 0.996659, 1.0},
	})

	if len(rows) != 4 {
		t.Fatalf("len(rows) = %d, want 4", len(rows))
	}
	last := rows[len(rows)-1]
	if last.Symbol != "SZ159659" {
		t.Fatalf("last symbol = %q, want SZ159659", last.Symbol)
	}
	if last.ModelVersion != "v1.fundpair" {
		t.Fatalf("model_version = %q, want v1.fundpair", last.ModelVersion)
	}
	if last.ReferenceSymbol != "HF_NQ" {
		t.Fatalf("reference_symbol = %q, want HF_NQ", last.ReferenceSymbol)
	}
	if last.Status != "ok" {
		t.Fatalf("status = %q, want ok", last.Status)
	}
	if last.FittedEffectiveRatio == nil || *last.FittedEffectiveRatio != 0.996659 {
		t.Fatalf("fitted_effective_ratio = %v, want 0.996659", last.FittedEffectiveRatio)
	}
}
