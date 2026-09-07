package web

import (
	"context"
	"net/http"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

type fakeBufferedVisitRepository struct {
	loaded  []domain.VisitCount
	batches [][]domain.VisitCount
}

func (r *fakeBufferedVisitRepository) IncrementVisitCount(ctx context.Context, when time.Time, kind string, target string, method string) error {
	return nil
}

func (r *fakeBufferedVisitRepository) LoadVisitCounts(ctx context.Context, days int) ([]domain.VisitCount, error) {
	out := make([]domain.VisitCount, len(r.loaded))
	copy(out, r.loaded)
	return out, nil
}

func (r *fakeBufferedVisitRepository) AddVisitCounts(ctx context.Context, rows []domain.VisitCount) error {
	copyRows := make([]domain.VisitCount, len(rows))
	copy(copyRows, rows)
	r.batches = append(r.batches, copyRows)
	return nil
}

func TestBufferedVisitRepositoryFlushAggregatesCounts(t *testing.T) {
	base := &fakeBufferedVisitRepository{}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	repo := NewBufferedVisitRepository(base, ctx, time.Hour)
	buffered, ok := repo.(*bufferedVisitRepository)
	if !ok {
		t.Fatal("expected buffered visit repository")
	}

	when := time.Date(2026, 6, 6, 13, 30, 0, 0, time.Local)
	for i := 0; i < 3; i++ {
		if err := buffered.IncrementVisitCount(context.Background(), when, "api", "/api/v1/funds/:symbol", http.MethodGet); err != nil {
			t.Fatalf("increment failed: %v", err)
		}
	}

	if err := buffered.flush(context.Background()); err != nil {
		t.Fatalf("flush failed: %v", err)
	}
	if len(base.batches) != 1 {
		t.Fatalf("batch count = %d, want 1", len(base.batches))
	}
	if len(base.batches[0]) != 1 {
		t.Fatalf("row count = %d, want 1", len(base.batches[0]))
	}
	row := base.batches[0][0]
	if row.Count != 3 {
		t.Fatalf("row count value = %d, want 3", row.Count)
	}
	if row.Method != http.MethodGet {
		t.Fatalf("row method = %q, want %q", row.Method, http.MethodGet)
	}
}

func TestBufferedVisitRepositoryLoadVisitCountsIncludesPending(t *testing.T) {
	// LoadVisitCounts deliberately applies a rolling day window. Keep this
	// fixture inside that window so the test exercises pending-row merging,
	// rather than becoming date-dependent as wall-clock time advances.
	now := time.Now().In(time.Local)
	base := &fakeBufferedVisitRepository{
		loaded: []domain.VisitCount{{
			Date:   now.Format("2006-01-02"),
			Kind:   "api",
			Target: "/api/v1/funds/:symbol",
			Method: "GET",
			Count:  5,
		}},
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	repo := NewBufferedVisitRepository(base, ctx, time.Hour)
	buffered, ok := repo.(*bufferedVisitRepository)
	if !ok {
		t.Fatal("expected buffered visit repository")
	}
	if err := buffered.IncrementVisitCount(context.Background(), now, "api", "/api/v1/funds/:symbol", http.MethodGet); err != nil {
		t.Fatalf("increment failed: %v", err)
	}
	if err := buffered.IncrementVisitCount(context.Background(), now, "api", "/api/v1/funds/:symbol", http.MethodGet); err != nil {
		t.Fatalf("increment failed: %v", err)
	}

	rows, err := buffered.LoadVisitCounts(context.Background(), 30)
	if err != nil {
		t.Fatalf("load failed: %v", err)
	}
	if len(rows) != 1 {
		t.Fatalf("row count = %d, want 1", len(rows))
	}
	if rows[0].Count != 7 {
		t.Fatalf("merged count = %d, want 7", rows[0].Count)
	}
	if rows[0].Method != "GET" {
		t.Fatalf("merged method = %q, want GET", rows[0].Method)
	}
}
