package privatevaluation

import (
	"math"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestSilverAGContractForDateRollsOnDayTen(t *testing.T) {
	tests := map[string]string{
		"2026-08-09": "AG2608",
		"2026-08-10": "AG2610",
		"2026-09-15": "AG2610",
		"2026-10-09": "AG2610",
		"2026-10-10": "AG2612",
		"2026-12-10": "AG2702",
	}
	for day, want := range tests {
		value, err := time.ParseInLocation("2006-01-02", day, shanghaiLocation)
		if err != nil {
			t.Fatal(err)
		}
		if got := SilverAGContractForDate(value); got != want {
			t.Fatalf("SilverAGContractForDate(%s) = %s, want %s", day, got, want)
		}
	}
}

func TestCalculateSilverSettlementProducesBothNAVs(t *testing.T) {
	observed := time.Date(2026, 8, 20, 10, 0, 0, 0, shanghaiLocation)
	input := Input{
		SchemaVersion: InputSchemaVersion,
		Symbol:        SZ161226Symbol,
		ModelVersion:  SZ161226ModelVersion,
		ValuationKind: CalculationModeSilverSettlement,
		Silver: &SilverSettlementInput{
			BaseNAV:                  1.7654,
			BaseNAVDate:              "2026-08-19",
			BaseNAVSource:            "eastmoney_f10",
			BaseNAVFetchedAt:         observed.Add(-time.Hour),
			TradingDay:               "2026-08-20",
			Contract:                 "AG2610",
			ContractSelectionVersion: SilverContractSelectionVersion,
			PreviousSettlement:       15624,
			PreviousSettlementDate:   "2026-08-19",
			PreviousSettlementSource: SilverEastmoneyPreviousSettlementSource,
			FuturesPrice:             15900,
			IntradayAverage:          15800,
			IntradayAverageBasis:     SilverEastmoneyIntradayAverageBasis,
			ObservedAt:               observed,
			Source:                   "EASTMONEY_FUTURES_SSE",
		},
		Source:      "mac-home-private-161226-uploader",
		GeneratedAt: observed,
		ReceivedAt:  observed,
	}
	quote := domain.Quote{
		Symbol: SZ161226Symbol, Name: SZ161226Name, Price: 1.8,
		BidLevels: []domain.Level{{Level: 1, Price: 1.799}},
		AskLevels: []domain.Level{{Level: 1, Price: 1.801}},
		FetchedAt: observed,
	}
	snapshot := CalculateForSymbol(SZ161226Symbol, &input, map[string]domain.Quote{SZ161226Symbol: quote}, observed)
	if !snapshot.Ready || snapshot.SilverValuation == nil || snapshot.Valuation == nil {
		t.Fatalf("expected ready silver snapshot, warnings=%v", snapshot.Warnings)
	}
	invalid := input
	invalidSilver := *input.Silver
	invalid.Silver = &invalidSilver
	invalid.Silver.PreviousSettlementSource = "UNTRUSTED_SOURCE"
	if err := invalid.Validate(); err == nil {
		t.Fatal("expected an unknown silver previous-settlement source to be rejected")
	}
	invalid.Silver.PreviousSettlementSource = SilverEastmoneyPreviousSettlementSource
	invalid.Silver.IntradayAverageBasis = "untrusted_average"
	if err := invalid.Validate(); err == nil {
		t.Fatal("expected an unknown silver intraday-average basis to be rejected")
	}
	wantSettlement := input.Silver.BaseNAV / input.Silver.PreviousSettlement * input.Silver.IntradayAverage
	wantTrading := input.Silver.BaseNAV / input.Silver.PreviousSettlement * input.Silver.FuturesPrice
	if math.Abs(snapshot.SilverValuation.SettlementNAV-wantSettlement) > 1e-9 {
		t.Fatalf("settlement NAV = %.10f, want %.10f", snapshot.SilverValuation.SettlementNAV, wantSettlement)
	}
	if math.Abs(snapshot.SilverValuation.TradingNAV-wantTrading) > 1e-9 {
		t.Fatalf("trading NAV = %.10f, want %.10f", snapshot.SilverValuation.TradingNAV, wantTrading)
	}
	point, ok := MinuteHistoryPointFromSnapshot(snapshot)
	if !ok || point.SettlementNAV == nil || point.TradingNAV == nil || point.ActiveContract != "AG2610" {
		t.Fatalf("expected semantic silver minute point, got %+v", point)
	}
}

func TestSilverMinuteHistoryUsesCommonTradingWindowOnly(t *testing.T) {
	for _, test := range []struct {
		hour, minute int
		want         bool
	}{
		{9, 14, false}, {9, 15, true}, {11, 30, true}, {11, 31, false},
		{12, 0, false}, {13, 0, true}, {15, 0, true}, {15, 1, false},
		{21, 0, false},
	} {
		value := time.Date(2026, 8, 20, test.hour, test.minute, 0, 0, shanghaiLocation)
		if got := IsMinuteHistoryTradingSessionForSymbol(SZ161226Symbol, value); got != test.want {
			t.Fatalf("session %02d:%02d = %v, want %v", test.hour, test.minute, got, test.want)
		}
	}
	value := time.Date(2026, 8, 20, 9, 15, 0, 0, shanghaiLocation)
	if IsMinuteHistoryTradingSessionForSymbol(TargetSymbol, value) {
		t.Fatal("existing private funds must retain the 09:30 session start")
	}
}
