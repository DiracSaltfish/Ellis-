package privatevaluation

import (
	"context"
	"testing"
	"time"
)

func reviewPointer(value float64) *float64 { return &value }

func TestBuildIndiaHistoryReviewRowsFitsIndependentIndiaExposure(t *testing.T) {
	sources := []IndiaHistoryReviewSource{
		{TargetDate: "2026-07-03", OfficialNAV: reviewPointer(1.10), BaseNAVDate: "2026-07-01", BaseNAV: reviewPointer(1), FinalEstimateNAV: 1.10, InvestmentRatio: 0.8937, StaticRatio: 0.1063, BaseAnchorPrice: 50, TargetAnchorPrice: 55, BaseFX: 6.78, TargetFX: 6.78, Source: "test"},
		{TargetDate: "2026-07-02", OfficialNAV: reviewPointer(1.05), BaseNAVDate: "2026-06-30", BaseNAV: reviewPointer(1), FinalEstimateNAV: 1.05, InvestmentRatio: 0.8937, StaticRatio: 0.1063, BaseAnchorPrice: 50, TargetAnchorPrice: 52.5, BaseFX: 6.78, TargetFX: 6.78, Source: "test"},
		{TargetDate: "2026-07-01", OfficialNAV: reviewPointer(1.02), BaseNAVDate: "2026-06-29", BaseNAV: reviewPointer(1), FinalEstimateNAV: 1.02, InvestmentRatio: 0.8937, StaticRatio: 0.1063, BaseAnchorPrice: 50, TargetAnchorPrice: 51, BaseFX: 6.78, TargetFX: 6.78, Source: "test"},
	}
	rows := buildIndiaHistoryReviewRows(sources, 10)
	if len(rows) != 3 || rows[0].TargetDate != "2026-07-03" {
		t.Fatalf("rows = %+v, want newest first", rows)
	}
	latest := rows[0]
	if latest.FinalDeviationPct == nil || *latest.FinalDeviationPct != 0 {
		t.Fatalf("final deviation = %v, want 0", latest.FinalDeviationPct)
	}
	if latest.FittedExposure == nil || *latest.FittedExposure < 0.99 || *latest.FittedExposure > 1.01 {
		t.Fatalf("fitted exposure = %v, want 1", latest.FittedExposure)
	}
	if latest.FitWindowSize != 3 {
		t.Fatalf("fit window = %d, want fit over three final-NAV samples", latest.FitWindowSize)
	}
}

type finalNAVHistoryRepositoryStub struct {
	historyRepositoryStub
	points []IndiaFinalNAVHistoryPoint
}

func (s *finalNAVHistoryRepositoryStub) UpsertPrivateIndiaFinalNAVHistory(_ context.Context, _ string, points []IndiaFinalNAVHistoryPoint) error {
	s.points = append([]IndiaFinalNAVHistoryPoint(nil), points...)
	return nil
}

func TestImportIndiaFinalNAVHistoryCalculatesStaticAndRiskSleeves(t *testing.T) {
	repository := &finalNAVHistoryRepositoryStub{}
	service := NewService(repository, nil)
	count, err := service.ImportIndiaFinalNAVHistory(context.Background(), SZ164824Symbol, []IndiaFinalNAVHistoryInput{{
		TargetDate: "2026-08-03", BaseNAVDate: "2026-07-30", BaseNAV: 1,
		InvestmentRatio: 0.8937, StaticRatio: 0.1063,
		BaseAnchorPrice: 100, TargetAnchorPrice: 110, BaseFX: 7, TargetFX: 7,
		Source: "test", GeneratedAt: time.Date(2026, 8, 5, 9, 0, 0, 0, time.UTC),
	}})
	if err != nil || count != 1 || len(repository.points) != 1 {
		t.Fatalf("imported=%d points=%d err=%v", count, len(repository.points), err)
	}
	if got, want := repository.points[0].FinalEstimateNAV, 1.08937; got < want-1e-10 || got > want+1e-10 {
		t.Fatalf("final estimate=%v, want=%v", got, want)
	}
}
