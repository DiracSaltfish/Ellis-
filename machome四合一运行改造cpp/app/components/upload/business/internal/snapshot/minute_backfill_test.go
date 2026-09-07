package snapshot

import (
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestBackfillReferenceMinuteBarsOnlyTouchesImpactedFunds(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 6, 4, 10, 0, 0, 0, shanghaiLocation())
	service := NewService(ServiceOptions{MinuteHistoryDir: root})
	service.now = func() time.Time { return now }
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.LatestNetValues["SZ159659"] = domain.NetValue{
		Symbol: "SZ159659",
		Date:   "2026-06-02",
		NAV:    2.0,
	}
	service.valuationData.LatestNetValues["SH513100"] = domain.NetValue{
		Symbol: "SH513100",
		Date:   "2026-06-02",
		NAV:    1.5,
	}
	service.valuationData.FundPairs["SZ159659"] = []domain.FundPair{
		{FundSymbol: "SZ159659", PairSymbol: "QQQ", PairType: "qdii_us_static"},
	}
	service.valuationData.FundPairs["SH513100"] = []domain.FundPair{
		{FundSymbol: "SH513100", PairSymbol: "HF_NQ", PairType: "qdii_us_static"},
	}
	service.valuationData.DailyPricesByDate["QQQ"] = map[string]domain.DailyPrice{
		"2026-06-02": {Symbol: "QQQ", Date: "2026-06-02", Close: 700, AdjClose: 700, Source: "daily"},
	}
	service.valuationData.DailyPricesByDate["HF_NQ"] = map[string]domain.DailyPrice{
		"2026-06-02": {Symbol: "HF_NQ", Date: "2026-06-02", Close: 21000, AdjClose: 21000, Source: "daily"},
	}
	service.quotes["SZ159659"] = domain.Quote{Symbol: "SZ159659", Name: "target", Price: 2.02, Source: "seed"}
	service.quotes["SH513100"] = domain.Quote{Symbol: "SH513100", Name: "other", Price: 1.52, Source: "seed"}
	service.quotes["HF_NQ"] = domain.Quote{
		Symbol:         "HF_NQ",
		Name:           "NQ",
		Price:          21500,
		PrevClose:      21480,
		Source:         "sina_hf",
		SourceSymbol:   "hf_NQ",
		QuoteSession:   "global_future",
		IsRealtime:     true,
		RealtimeStatus: "realtime",
	}

	if err := service.minuteHistory.Upsert(now, []MinuteHistoryPoint{
		{
			Symbol:         "SZ159659",
			Minute:         "2026-06-04 10:00",
			Timestamp:      now.Format(time.RFC3339Nano),
			MarketPrice:    2.02,
			EstimatedNAV:   2.0,
			PremiumPct:     1.0,
			EffectiveRatio: 1,
			UploadSource:   "seed",
		},
		{
			Symbol:          "SH513100",
			Minute:          "2026-06-04 10:00",
			Timestamp:       now.Format(time.RFC3339Nano),
			MarketPrice:     1.52,
			EstimatedNAV:    1.535714,
			PremiumPct:      -1.022,
			OfficialEST:     1.534286,
			FairEST:         1.535714,
			EffectiveRatio:  1,
			ModelVersion:    "v1.fundpair.daily_factor",
			ReferenceSymbol: "HF_NQ",
			UploadSource:    "seed",
		},
	}); err != nil {
		t.Fatal(err)
	}

	result, err := service.BackfillReferenceMinuteBars("reference_1m", []MinuteBar{{
		Symbol:    "QQQ",
		Minute:    "2026-06-04 10:00",
		Timestamp: now.Format(time.RFC3339Nano),
		LastPrice: 720,
		Source:    "reference_1m",
	}})
	if err != nil {
		t.Fatal(err)
	}
	if !result.OK {
		t.Fatalf("expected backfill ok, got %+v", result)
	}

	rows, err := service.minuteHistory.Read("SZ159659", 1, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) for SZ159659 = %d, want 1", len(rows))
	}
	if rows[0].EstimatedNAV == 2.0 {
		t.Fatalf("SZ159659 estimated_nav not updated: %+v", rows[0])
	}
	if rows[0].UploadSource != "reference_1m" {
		t.Fatalf("SZ159659 upload_source = %q, want reference_1m", rows[0].UploadSource)
	}

	otherRows, err := service.minuteHistory.Read("SH513100", 1, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(otherRows) != 1 {
		t.Fatalf("len(rows) for SH513100 = %d, want 1", len(otherRows))
	}
	if otherRows[0].EstimatedNAV != 1.535714 {
		t.Fatalf("SH513100 estimated_nav = %.6f, want unchanged 1.535714", otherRows[0].EstimatedNAV)
	}
	if otherRows[0].UploadSource != "seed" {
		t.Fatalf("SH513100 upload_source = %q, want unchanged seed", otherRows[0].UploadSource)
	}
}

func TestRebuildHistoricalMinuteBarsRequiresMinuteReferenceAndWritesDate(t *testing.T) {
	root := t.TempDir()
	service := NewService(ServiceOptions{MinuteHistoryDir: root})
	service.now = func() time.Time {
		return time.Date(2026, 6, 6, 10, 0, 0, 0, shanghaiLocation())
	}
	service.valuationData = domain.EmptyValuationData()
	service.valuationData.NetValuesByDate["SH501018"] = map[string]domain.NetValue{
		"2026-05-06": {
			Symbol: "SH501018",
			Date:   "2026-05-06",
			NAV:    1.0,
		},
	}
	service.valuationData.ValuationAnchors[domain.ValuationAnchorSetKey("SH501018", "2026-05-06", "HF_CL")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SH501018",
		AnchorDate:      "2026-05-06",
		ReferenceSymbol: "HF_CL",
		WeightedPrice:   100,
		CoverageWeight:  1,
		RequiredWeight:  1,
	}
	service.valuationData.FXCentralParity["USDCNY"] = map[string]domain.FXCentralParity{
		"2026-05-06": {Pair: "USDCNY", Date: "2026-05-06", Rate: 7.0, Source: "test"},
		"2026-05-08": {Pair: "USDCNY", Date: "2026-05-08", Rate: 7.0, Source: "test"},
	}
	service.quotes["HF_CL"] = domain.Quote{Symbol: "HF_CL", Price: 999, Source: "current"}

	fundRows := []MinuteBar{{
		Symbol:    "501018.SH",
		Minute:    "2026-05-08 10:00",
		Timestamp: "2026-05-08T10:00:00+08:00",
		LastPrice: 1.05,
		Source:    "qmt_test",
	}}
	result, err := service.RebuildHistoricalMinuteBars("hist_test", fundRows, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.OK || result.Accepted != 0 {
		t.Fatalf("rebuild without minute reference = %+v, want skipped", result)
	}

	result, err = service.RebuildHistoricalMinuteBars("hist_test", fundRows, []MinuteBar{{
		Symbol:    "hf_CL",
		Minute:    "2026-05-08 10:00",
		Timestamp: "2026-05-08T10:00:00+08:00",
		LastPrice: 110,
		Source:    "ibkr_test",
	}})
	if err != nil {
		t.Fatal(err)
	}
	if !result.OK || result.Accepted != 1 {
		t.Fatalf("rebuild result = %+v, want one accepted row", result)
	}

	rows, err := service.minuteHistory.ReadDate("SH501018", "20260508")
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	row := rows[0]
	if row.Symbol != "SH501018" || row.Minute != "2026-05-08 10:00" {
		t.Fatalf("unexpected row identity: %+v", row)
	}
	if row.MarketPrice != 1.05 || row.ReferenceSymbol != "HF_CL" || row.UploadSource != "hist_test" {
		t.Fatalf("unexpected rebuilt row: %+v", row)
	}
	if row.EstimatedNAV <= 0 || row.EstimatedNAV >= 9 {
		t.Fatalf("estimated nav appears to use current quote: %+v", row)
	}
}
