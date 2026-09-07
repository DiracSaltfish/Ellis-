package db

import (
	"testing"
	"time"

	"newnavnav/internal/privatevaluation"
)

func TestPrivateInputFXMetadataUsesSingleFX(t *testing.T) {
	input := privatevaluation.Input{
		FX: privatevaluation.FXInput{TradingDay: "2026-07-13", QuoteTime: "10:00"},
	}
	day, quoteTime := privateInputFXMetadata(input)
	if day != "2026-07-13" || quoteTime != "10:00" {
		t.Fatalf("metadata = %s %s", day, quoteTime)
	}
}

func TestPrivateIndiaNiftyBridgeSnapshotMinuteUsesShanghaiWallClock(t *testing.T) {
	instant := time.Date(2026, 7, 28, 1, 35, 0, 0, time.UTC)
	if got := privateIndiaNiftyBridgeSnapshotMinute(instant); got != "2026-07-28 09:35:00" {
		t.Fatalf("snapshot minute = %q, want Shanghai checkpoint wall clock", got)
	}
}

func TestPrivateInputFXMetadataUsesMultiMarketFX(t *testing.T) {
	input := privatevaluation.Input{
		FXRates: []privatevaluation.FXInput{
			{Pair: "USD/CNY", TradingDay: "2026-07-10", QuoteTime: "18:00"},
			{Pair: "HKD/CNY", TradingDay: "2026-07-10", QuoteTime: "18:00"},
		},
	}
	day, quoteTime := privateInputFXMetadata(input)
	if day != "2026-07-10" || quoteTime != "18:00" {
		t.Fatalf("metadata = %s %s", day, quoteTime)
	}
}

func TestPrivateInputFXMetadataUsesLOFCurrentSAFEParity(t *testing.T) {
	input := privatevaluation.Input{
		LOF: &privatevaluation.LOFWeightedAnchorInput{
			CurrentFX: privatevaluation.FXInput{TradingDay: "2026-08-11", QuoteTime: "09:15"},
		},
	}
	day, quoteTime := privateInputFXMetadata(input)
	if day != "2026-08-11" || quoteTime != "09:15" {
		t.Fatalf("metadata = %s %s", day, quoteTime)
	}
}
