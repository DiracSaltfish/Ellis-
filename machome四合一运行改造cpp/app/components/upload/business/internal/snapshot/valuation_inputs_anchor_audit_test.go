package snapshot

import (
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestWeightedAnchorInputsExposeAnchorAuditTimes(t *testing.T) {
	strategy, ok := domain.WeightedAnchorStrategyForFund("SZ162411")
	if !ok {
		t.Fatal("SZ162411 weighted anchor strategy is missing")
	}
	targetAt := time.Date(2026, 8, 7, 20, 0, 0, 0, time.UTC)
	observedAt := targetAt.Add(2 * time.Second)
	data := domain.EmptyValuationData()
	data.ValuationAnchors[domain.ValuationAnchorSetKey("SZ162411", "2026-08-07", "XOP")] = domain.ValuationAnchorPriceSet{
		FundSymbol:      "SZ162411",
		AnchorDate:      "2026-08-07",
		ReferenceSymbol: "XOP",
		WeightedPrice:   166.41,
		CoverageWeight:  1,
		RequiredWeight:  1,
		Points: []domain.ValuationAnchorPrice{
			{
				FundSymbol:      "SZ162411",
				AnchorDate:      "2026-08-07",
				AnchorKey:       "us_close",
				ReferenceSymbol: "XOP",
				TargetAt:        targetAt,
				TargetTimezone:  "America/New_York",
				ObservedAt:      observedAt,
				Price:           166.41,
				Weight:          1,
				Source:          "sina_us",
				CaptureStatus:   "captured_probe_window",
			},
		},
	}

	inputs := weightedAnchorInputs(
		"2026-08-07",
		map[string]domain.Quote{"XOP": {Symbol: "XOP", Price: 174.6, Source: "us_live"}},
		data,
		strategy,
	)
	var anchor *domain.ValuationInput
	for index := range inputs {
		if inputs[index].Role == "估值锚点" {
			anchor = &inputs[index]
			break
		}
	}
	if anchor == nil {
		t.Fatal("weighted anchor audit input is missing")
	}
	if anchor.TargetAt == nil || !anchor.TargetAt.Equal(targetAt) {
		t.Fatalf("target_at = %v, want %s", anchor.TargetAt, targetAt)
	}
	if anchor.ObservedAt == nil || !anchor.ObservedAt.Equal(observedAt) {
		t.Fatalf("observed_at = %v, want %s", anchor.ObservedAt, observedAt)
	}
	if anchor.CaptureStatus != "captured_probe_window" {
		t.Fatalf("capture_status = %q", anchor.CaptureStatus)
	}
}
