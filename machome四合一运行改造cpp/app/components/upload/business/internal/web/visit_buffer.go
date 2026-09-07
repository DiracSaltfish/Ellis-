package web

import (
	"context"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"newnavnav/internal/domain"
)

type visitBatchRepository interface {
	AddVisitCounts(ctx context.Context, rows []domain.VisitCount) error
}

type bufferedVisitRepository struct {
	base          VisitRepository
	batch         visitBatchRepository
	flushInterval time.Duration

	mu      sync.Mutex
	pending map[string]domain.VisitCount
}

func NewBufferedVisitRepository(base VisitRepository, ctx context.Context, flushInterval time.Duration) VisitRepository {
	if base == nil {
		return nil
	}
	batch, ok := base.(visitBatchRepository)
	if !ok {
		return base
	}
	if flushInterval <= 0 {
		flushInterval = 30 * time.Second
	}
	repo := &bufferedVisitRepository{
		base:          base,
		batch:         batch,
		flushInterval: flushInterval,
		pending:       make(map[string]domain.VisitCount),
	}
	go repo.run(ctx)
	return repo
}

func (r *bufferedVisitRepository) IncrementVisitCount(_ context.Context, when time.Time, kind string, target string, method string) error {
	kind = strings.TrimSpace(kind)
	target = strings.TrimSpace(target)
	method = normalizeVisitMethod(method)
	if kind == "" || target == "" {
		return nil
	}
	date := when.Format("2006-01-02")
	key := visitKey(date, kind, target, method)

	r.mu.Lock()
	defer r.mu.Unlock()
	row := r.pending[key]
	if row.Date == "" {
		row = domain.VisitCount{
			Date:   date,
			Kind:   kind,
			Target: target,
			Method: displayVisitMethod(method),
		}
	}
	row.Count++
	r.pending[key] = row
	return nil
}

func (r *bufferedVisitRepository) LoadVisitCounts(ctx context.Context, days int) ([]domain.VisitCount, error) {
	rows, err := r.base.LoadVisitCounts(ctx, days)
	if err != nil {
		return nil, err
	}

	merged := make(map[string]domain.VisitCount, len(rows))
	for _, row := range rows {
		method := normalizeVisitMethod(row.Method)
		key := visitKey(row.Date, row.Kind, row.Target, method)
		row.Method = displayVisitMethod(method)
		merged[key] = row
	}

	cutoff := ""
	if days > 0 {
		cutoff = time.Now().AddDate(0, 0, -(days - 1)).Format("2006-01-02")
	}
	for _, row := range r.pendingRows() {
		if cutoff != "" && row.Date < cutoff {
			continue
		}
		method := normalizeVisitMethod(row.Method)
		key := visitKey(row.Date, row.Kind, row.Target, method)
		existing := merged[key]
		if existing.Date == "" {
			existing = row
			existing.Method = displayVisitMethod(method)
		}
		existing.Count += row.Count
		merged[key] = existing
	}

	out := make([]domain.VisitCount, 0, len(merged))
	for _, row := range merged {
		out = append(out, row)
	}
	sortVisitCounts(out)
	return out, nil
}

func (r *bufferedVisitRepository) run(ctx context.Context) {
	ticker := time.NewTicker(r.flushInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			flushCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			if err := r.flush(flushCtx); err != nil {
				slog.Warn("flush buffered visit counts failed during shutdown", "error", err)
			}
			cancel()
			return
		case <-ticker.C:
			flushCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			if err := r.flush(flushCtx); err != nil {
				slog.Warn("flush buffered visit counts failed", "error", err)
			}
			cancel()
		}
	}
}

func (r *bufferedVisitRepository) flush(ctx context.Context) error {
	rows := r.swapPendingRows()
	if len(rows) == 0 {
		return nil
	}
	if err := r.batch.AddVisitCounts(ctx, rows); err != nil {
		r.restorePendingRows(rows)
		return err
	}
	return nil
}

func (r *bufferedVisitRepository) pendingRows() []domain.VisitCount {
	r.mu.Lock()
	defer r.mu.Unlock()
	rows := make([]domain.VisitCount, 0, len(r.pending))
	for _, row := range r.pending {
		rows = append(rows, row)
	}
	return rows
}

func (r *bufferedVisitRepository) swapPendingRows() []domain.VisitCount {
	r.mu.Lock()
	defer r.mu.Unlock()
	rows := make([]domain.VisitCount, 0, len(r.pending))
	for _, row := range r.pending {
		rows = append(rows, row)
	}
	r.pending = make(map[string]domain.VisitCount)
	sortVisitCounts(rows)
	return rows
}

func (r *bufferedVisitRepository) restorePendingRows(rows []domain.VisitCount) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, row := range rows {
		method := normalizeVisitMethod(row.Method)
		key := visitKey(row.Date, row.Kind, row.Target, method)
		existing := r.pending[key]
		if existing.Date == "" {
			existing = domain.VisitCount{
				Date:   row.Date,
				Kind:   row.Kind,
				Target: row.Target,
				Method: displayVisitMethod(method),
			}
		}
		existing.Count += row.Count
		r.pending[key] = existing
	}
}

func sortVisitCounts(rows []domain.VisitCount) {
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Date != rows[j].Date {
			return rows[i].Date > rows[j].Date
		}
		if rows[i].Kind != rows[j].Kind {
			return rows[i].Kind < rows[j].Kind
		}
		if rows[i].Count != rows[j].Count {
			return rows[i].Count > rows[j].Count
		}
		if rows[i].Target != rows[j].Target {
			return rows[i].Target < rows[j].Target
		}
		return rows[i].Method < rows[j].Method
	})
}

func visitKey(date string, kind string, target string, method string) string {
	return date + "\x00" + kind + "\x00" + target + "\x00" + method
}

func normalizeVisitMethod(method string) string {
	method = strings.ToUpper(strings.TrimSpace(method))
	if method == "" {
		return "-"
	}
	return method
}

func displayVisitMethod(method string) string {
	if normalizeVisitMethod(method) == "-" {
		return ""
	}
	return normalizeVisitMethod(method)
}
