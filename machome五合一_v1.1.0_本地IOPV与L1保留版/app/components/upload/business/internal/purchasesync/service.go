package purchasesync

import (
	"context"
	"errors"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"newnavnav/internal/domain"
)

type PurchaseClient interface {
	FetchPurchaseInfos(ctx context.Context, symbols []string) (map[string]domain.PurchaseInfo, error)
}

type PageIntervalSetter interface {
	SetPageInterval(interval time.Duration)
}

type PurchaseInfoApplier interface {
	UpdatePurchaseInfos(ctx context.Context, infos map[string]domain.PurchaseInfo) error
}

type Options struct {
	Client        PurchaseClient
	Applier       PurchaseInfoApplier
	Symbols       []string
	Logger        *slog.Logger
	QueryInterval time.Duration
}

type Service struct {
	client        PurchaseClient
	applier       PurchaseInfoApplier
	symbols       []string
	logger        *slog.Logger
	queryInterval time.Duration
	runMu         sync.Mutex
}

func NewService(opts Options) *Service {
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	interval := opts.QueryInterval
	if interval <= 0 {
		interval = 10 * time.Second
	}
	if setter, ok := opts.Client.(PageIntervalSetter); ok {
		setter.SetPageInterval(interval)
	}
	return &Service{
		client:        opts.Client,
		applier:       opts.Applier,
		symbols:       uniqueSymbols(opts.Symbols),
		logger:        logger,
		queryInterval: interval,
	}
}

func (s *Service) SyncMissing(ctx context.Context, today time.Time) error {
	s.runMu.Lock()
	defer s.runMu.Unlock()

	if s.client == nil {
		return errors.New("purchase status sync requires client")
	}
	if s.applier == nil {
		return errors.New("purchase status sync requires applier")
	}
	if len(s.symbols) == 0 {
		s.logger.Info("purchase status sync skipped; no symbols")
		return nil
	}

	infos, runErr := s.client.FetchPurchaseInfos(ctx, s.symbols)
	if len(infos) == 0 {
		if runErr != nil {
			return runErr
		}
		return errors.New("purchase status sync produced no rows")
	}
	for symbol, info := range infos {
		if info.FetchedAt.IsZero() {
			info.FetchedAt = today
			infos[symbol] = info
		}
	}
	if err := s.applier.UpdatePurchaseInfos(ctx, infos); err != nil {
		return err
	}
	s.logger.Info("purchase status sync finished", "symbols", len(s.symbols), "updated", len(infos))
	return runErr
}

func uniqueSymbols(symbols []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(symbols))
	for _, symbol := range symbols {
		symbol = strings.ToUpper(strings.TrimSpace(symbol))
		if symbol == "" || seen[symbol] {
			continue
		}
		seen[symbol] = true
		out = append(out, symbol)
	}
	sort.Strings(out)
	return out
}
