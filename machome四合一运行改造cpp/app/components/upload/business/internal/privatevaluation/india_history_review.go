package privatevaluation

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"
)

const indiaHistoryFitWindow = 10

type indiaReviewObservation struct {
	row      *IndiaHistoryReviewRow
	estimate float64
	baseNAV  float64
	official float64
}

func copyFloat(value float64) *float64 {
	if !finite(value) {
		return nil
	}
	copy := value
	return &copy
}

func buildIndiaHistoryReviewRows(sources []IndiaHistoryReviewSource, window int) []IndiaHistoryReviewRow {
	if window <= 0 {
		window = indiaHistoryFitWindow
	}
	// The database returns newest first. Work chronologically so each row's
	// calibration contains only that day and prior observed outcomes.
	sort.Slice(sources, func(i, j int) bool { return sources[i].TargetDate < sources[j].TargetDate })
	observations := make([]indiaReviewObservation, 0, len(sources))
	rows := make([]*IndiaHistoryReviewRow, 0, len(sources))
	for _, source := range sources {
		row := IndiaHistoryReviewRow{
			TargetDate:        source.TargetDate,
			OfficialNAV:       source.OfficialNAV,
			BaseNAVDate:       source.BaseNAVDate,
			BaseNAV:           source.BaseNAV,
			FinalEstimateNAV:  copyFloat(source.FinalEstimateNAV),
			InvestmentRatio:   copyFloat(source.InvestmentRatio),
			StaticRatio:       copyFloat(source.StaticRatio),
			BaseAnchorPrice:   copyFloat(source.BaseAnchorPrice),
			TargetAnchorPrice: copyFloat(source.TargetAnchorPrice),
			BaseFX:            copyFloat(source.BaseFX),
			TargetFX:          copyFloat(source.TargetFX),
			Source:            source.Source,
			Status:            "pending_official_nav",
		}
		rowPointer := &row
		if validIndiaReviewBase(source.OfficialNAV, source.BaseNAV) {
			row.Status = "ok"
			row.FinalDeviationPct = deviationPct(source.FinalEstimateNAV, *source.OfficialNAV)
			observations = append(observations, indiaReviewObservation{row: rowPointer, estimate: source.FinalEstimateNAV, baseNAV: *source.BaseNAV, official: *source.OfficialNAV})
		} else if source.OfficialNAV != nil {
			row.Status = "missing_t2_base_nav"
			row.Note = "缺少可审计的 T−2 官方净值基准"
		}
		rows = append(rows, rowPointer)
		applyIndiaExposureFit(observations, window)
	}
	// API tables are most useful newest-first, while the fit above remains
	// strictly causal because it was calculated before this reversal.
	for left, right := 0, len(rows)-1; left < right; left, right = left+1, right-1 {
		rows[left], rows[right] = rows[right], rows[left]
	}
	result := make([]IndiaHistoryReviewRow, 0, len(rows))
	for _, row := range rows {
		result = append(result, *row)
	}
	return result
}

func validIndiaReviewBase(official, base *float64) bool {
	return official != nil && base != nil && finitePositive(*official) && finitePositive(*base)
}

func deviationPct(estimate, official float64) *float64 {
	if !finitePositive(estimate) || !finitePositive(official) {
		return nil
	}
	return copyFloat((estimate/official - 1) * 100)
}

func applyIndiaExposureFit(observations []indiaReviewObservation, window int) {
	if len(observations) == 0 {
		return
	}
	end := len(observations)
	start := end - window
	if start < 0 {
		start = 0
	}
	selected := observations[start:end]
	if len(selected) < 3 {
		return
	}
	var sumXX, sumXY, absolutePctError float64
	for _, observation := range selected {
		x := observation.estimate/observation.baseNAV - 1
		y := observation.official/observation.baseNAV - 1
		sumXX += x * x
		sumXY += x * y
		absolutePctError += math.Abs(observation.estimate/observation.official-1) * 100
	}
	if sumXX <= 1e-12 {
		return
	}
	exposure := sumXY / sumXX
	mape := absolutePctError / float64(len(selected))
	latest := observations[end-1].row
	latest.FittedExposure = copyFloat(exposure)
	latest.WindowMAPEPct = copyFloat(mape)
	latest.FitWindowSize = len(selected)
}

func prepareIndiaFinalNAVHistoryPoint(input IndiaFinalNAVHistoryInput) (IndiaFinalNAVHistoryPoint, error) {
	input.TargetDate = strings.TrimSpace(input.TargetDate)
	input.BaseNAVDate = strings.TrimSpace(input.BaseNAVDate)
	input.Source = strings.TrimSpace(input.Source)
	target, err := time.Parse("2006-01-02", input.TargetDate)
	if err != nil {
		return IndiaFinalNAVHistoryPoint{}, fmt.Errorf("invalid India final-NAV target date: %w", err)
	}
	base, err := time.Parse("2006-01-02", input.BaseNAVDate)
	if err != nil {
		return IndiaFinalNAVHistoryPoint{}, fmt.Errorf("invalid India final-NAV base date: %w", err)
	}
	if !base.Before(target) {
		return IndiaFinalNAVHistoryPoint{}, fmt.Errorf("India final-NAV base date must precede target date")
	}
	for _, value := range []float64{input.BaseNAV, input.InvestmentRatio, input.StaticRatio, input.BaseAnchorPrice, input.TargetAnchorPrice, input.BaseFX, input.TargetFX} {
		if !finitePositive(value) {
			return IndiaFinalNAVHistoryPoint{}, fmt.Errorf("India final-NAV history contains a non-positive component")
		}
	}
	if math.Abs(input.InvestmentRatio+input.StaticRatio-1) > 0.000001 {
		return IndiaFinalNAVHistoryPoint{}, fmt.Errorf("India final-NAV investment and static ratios must sum to 1")
	}
	if input.Source == "" {
		return IndiaFinalNAVHistoryPoint{}, fmt.Errorf("India final-NAV history source is required")
	}
	if input.GeneratedAt.IsZero() {
		input.GeneratedAt = time.Now().UTC()
	}
	final := input.BaseNAV * (input.StaticRatio + input.InvestmentRatio*(input.TargetAnchorPrice/input.BaseAnchorPrice)*(input.TargetFX/input.BaseFX))
	if !finitePositive(final) {
		return IndiaFinalNAVHistoryPoint{}, fmt.Errorf("India final-NAV estimate is invalid")
	}
	return IndiaFinalNAVHistoryPoint{IndiaFinalNAVHistoryInput: input, FinalEstimateNAV: final}, nil
}
