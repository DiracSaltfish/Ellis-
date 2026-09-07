package privatevaluation

import (
	"context"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"newnavnav/internal/domain"
)

type QuoteProvider interface {
	CurrentQuotes() map[string]domain.Quote
}

type Repository interface {
	LoadPrivateValuationInputs(ctx context.Context) ([]Input, error)
	UpsertPrivateValuationInput(ctx context.Context, input Input) error
	UpsertPrivateValuationSnapshot(ctx context.Context, snapshot Snapshot) error
}

// InputBatchRepository persists one validated input batch atomically.  It is
// deliberately optional so lightweight read-only/test repositories do not
// have to implement transaction semantics; the production DB repository does.
type InputBatchRepository interface {
	UpsertPrivateValuationInputs(ctx context.Context, inputs []Input) error
}

type BatchUpdateResult struct {
	Accepted []string          `json:"accepted"`
	Rejected map[string]string `json:"rejected"`
}

type HistoricalMinuteDayReplacer interface {
	ReplacePrivateValuationSnapshots(ctx context.Context, symbol string, day time.Time, snapshots []Snapshot) error
}

// MinuteHistoryRepository is deliberately separate from Repository so adding
// chart history never expands the persistence contract used by existing
// private input/snapshot writers.
type MinuteHistoryRepository interface {
	LoadPrivateValuationMinuteHistory(ctx context.Context, symbol string, days int) ([]MinuteHistoryPoint, error)
}

type MinuteHistoryDateRepository interface {
	LoadPrivateValuationMinuteHistoryDate(ctx context.Context, symbol, day string) ([]MinuteHistoryPoint, error)
	LoadPrivateValuationMinuteHistoryDates(ctx context.Context, symbol string, limit int) ([]string, error)
}

type SilverCloseHistoryRepository interface {
	LoadPrivateSilverCloseHistory(ctx context.Context, symbol string, days int) ([]SilverCloseHistoryRow, error)
}

type IndiaHistoryReviewRepository interface {
	LoadPrivateIndiaHistoryReview(ctx context.Context, symbol string, days int) ([]IndiaHistoryReviewSource, error)
}

type IndiaNiftyBridgeReviewRepository interface {
	LoadPrivateIndiaNiftyBridgeHistoryReview(ctx context.Context, symbol string, days int) ([]IndiaNiftyBridgeReviewSource, error)
}

type IndiaFinalNAVHistoryRepository interface {
	UpsertPrivateIndiaFinalNAVHistory(ctx context.Context, symbol string, points []IndiaFinalNAVHistoryPoint) error
}

type Service struct {
	repository Repository
	quotes     QuoteProvider
	now        func() time.Time

	updateMu          sync.Mutex
	refreshMu         sync.Mutex
	mu                sync.RWMutex
	inputs            map[string]Input
	snapshots         map[string]Snapshot
	lastPersistMinute map[string]string
}

func NewService(repository Repository, quotes QuoteProvider) *Service {
	return &Service{
		repository:        repository,
		quotes:            quotes,
		now:               time.Now,
		inputs:            make(map[string]Input),
		snapshots:         make(map[string]Snapshot),
		lastPersistMinute: make(map[string]string),
	}
}

func (s *Service) Warm(ctx context.Context) error {
	if s == nil {
		return fmt.Errorf("private valuation service is nil")
	}
	if s.repository != nil {
		inputs, err := s.repository.LoadPrivateValuationInputs(ctx)
		if err != nil {
			return err
		}
		s.mu.Lock()
		for _, input := range inputs {
			normalized := input.Normalized(input.ReceivedAt)
			if err := normalized.Validate(); err != nil {
				slog.Warn("ignore invalid persisted private valuation input", "symbol", input.Symbol, "error", err)
				continue
			}
			s.inputs[normalized.Symbol] = normalized
		}
		s.mu.Unlock()
	}
	return s.Refresh(ctx, time.Now())
}

func (s *Service) Run(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = 3 * time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			if err := s.Refresh(ctx, now); err != nil {
				slog.Warn("private valuation refresh failed", "error", err)
			}
		}
	}
}

func (s *Service) UpdateInput(ctx context.Context, input Input) (Snapshot, error) {
	if s == nil {
		return Snapshot{}, fmt.Errorf("private valuation service is unavailable")
	}
	result, err := s.UpdateInputs(ctx, []Input{input})
	if err != nil {
		return Snapshot{}, err
	}
	symbol := strings.ToUpper(strings.TrimSpace(input.Symbol))
	if reason, rejected := result.Rejected[symbol]; rejected {
		return Snapshot{}, fmt.Errorf("%s", reason)
	}
	if len(result.Accepted) != 1 {
		if reason := result.Rejected["_index_0"]; reason != "" {
			return Snapshot{}, fmt.Errorf("%s", reason)
		}
		return Snapshot{}, fmt.Errorf("private valuation input was not accepted")
	}
	snapshot, _ := s.Fund(symbol)
	return snapshot, nil
}

// UpdateInputs validates inputs independently, atomically persists the valid
// subset, swaps that subset into memory together, and refreshes all funds once.
// The update mutex prevents a slower request from overwriting a newer batch
// between the stale check and the database commit.
func (s *Service) UpdateInputs(ctx context.Context, inputs []Input) (BatchUpdateResult, error) {
	result := BatchUpdateResult{Rejected: make(map[string]string)}
	if s == nil {
		return result, fmt.Errorf("private valuation service is unavailable")
	}
	if len(inputs) == 0 {
		return result, fmt.Errorf("private valuation input batch is empty")
	}
	s.updateMu.Lock()
	defer s.updateMu.Unlock()

	now := time.Now()
	if s.now != nil {
		now = s.now()
	}
	normalized := make([]Input, len(inputs))
	counts := make(map[string]int, len(inputs))
	for index, input := range inputs {
		input = input.Normalized(now)
		input.ReceivedAt = now
		normalized[index] = input
		counts[input.Symbol]++
	}

	s.mu.RLock()
	current := make(map[string]Input, len(s.inputs))
	for symbol, input := range s.inputs {
		current[symbol] = input
	}
	s.mu.RUnlock()

	valid := make([]Input, 0, len(normalized))
	for index, input := range normalized {
		key := input.Symbol
		if key == "" {
			key = fmt.Sprintf("_index_%d", index)
		}
		if counts[input.Symbol] > 1 {
			result.Rejected[key] = "duplicate symbol in batch"
			continue
		}
		if err := input.Validate(); err != nil {
			result.Rejected[key] = err.Error()
			continue
		}
		if previous, ok := current[input.Symbol]; ok && input.GeneratedAt.Before(previous.GeneratedAt) {
			result.Rejected[key] = "generated_at is older than the current input"
			continue
		}
		valid = append(valid, input)
	}
	if len(valid) == 0 {
		return result, nil
	}
	if s.repository != nil {
		if repository, ok := s.repository.(InputBatchRepository); ok {
			if err := repository.UpsertPrivateValuationInputs(ctx, valid); err != nil {
				return BatchUpdateResult{Rejected: make(map[string]string)}, err
			}
		} else if len(valid) == 1 {
			if err := s.repository.UpsertPrivateValuationInput(ctx, valid[0]); err != nil {
				return BatchUpdateResult{Rejected: make(map[string]string)}, err
			}
		} else {
			return BatchUpdateResult{Rejected: make(map[string]string)}, fmt.Errorf(
				"private valuation repository does not support atomic input batches",
			)
		}
	}
	// Serialize before exposing the new input generation. If an older periodic
	// refresh is in flight, let it finish first; then the swap, minute marker
	// reset, calculation, and persistence below form one ordered generation.
	s.refreshMu.Lock()
	defer s.refreshMu.Unlock()
	s.mu.Lock()
	for _, input := range valid {
		s.inputs[input.Symbol] = input
		// A periodic refresh may already have persisted this minute from the
		// previous input generation.  Allow the batch refresh below to upsert
		// the accepted symbols with the newly committed generation.
		delete(s.lastPersistMinute, input.Symbol)
		result.Accepted = append(result.Accepted, input.Symbol)
	}
	s.mu.Unlock()
	sort.Strings(result.Accepted)
	err := s.refreshLocked(ctx, now)
	if err != nil {
		return BatchUpdateResult{Rejected: make(map[string]string)}, err
	}
	return result, nil
}

func (s *Service) Refresh(ctx context.Context, now time.Time) error {
	if s == nil {
		return fmt.Errorf("private valuation service is nil")
	}
	// Calculations happen outside s.mu. Serialize whole refresh generations so
	// an older periodic refresh can never overwrite snapshots produced after a
	// newly accepted input batch.
	s.refreshMu.Lock()
	defer s.refreshMu.Unlock()
	return s.refreshLocked(ctx, now)
}

// refreshLocked runs one full snapshot generation. The caller must hold
// refreshMu so input swaps and minute-history writes remain generation ordered.
func (s *Service) refreshLocked(ctx context.Context, now time.Time) error {
	quotes := map[string]domain.Quote{}
	if s.quotes != nil {
		quotes = s.quotes.CurrentQuotes()
	}

	s.mu.RLock()
	inputs := make(map[string]Input, len(s.inputs))
	for symbol, input := range s.inputs {
		inputs[symbol] = input
	}
	s.mu.RUnlock()

	definitions := Definitions()
	next := make(map[string]Snapshot, len(definitions))
	for _, definition := range definitions {
		input, ok := inputs[definition.Symbol]
		if ok {
			next[definition.Symbol] = CalculateForSymbol(definition.Symbol, &input, quotes, now)
		} else {
			next[definition.Symbol] = CalculateForSymbol(definition.Symbol, nil, quotes, now)
		}
	}

	s.mu.Lock()
	s.snapshots = next
	toPersist := make([]Snapshot, 0, len(next))
	minute := now.In(shanghaiLocation).Format("2006-01-02 15:04")
	if s.repository != nil {
		for symbol, snapshot := range next {
			if !IsMinuteHistoryTradingSessionForSymbol(symbol, now) || !snapshot.Ready || s.lastPersistMinute[symbol] == minute {
				continue
			}
			s.lastPersistMinute[symbol] = minute
			toPersist = append(toPersist, snapshot)
		}
	}
	s.mu.Unlock()

	var firstErr error
	for _, snapshot := range toPersist {
		if err := s.repository.UpsertPrivateValuationSnapshot(ctx, snapshot); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			s.mu.Lock()
			delete(s.lastPersistMinute, snapshot.Symbol)
			s.mu.Unlock()
		}
	}
	return firstErr
}

func (s *Service) Fund(symbol string) (Snapshot, bool) {
	if s == nil {
		return Snapshot{}, false
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if !Supported(symbol) {
		return Snapshot{}, false
	}
	s.mu.RLock()
	snapshot, ok := s.snapshots[symbol]
	s.mu.RUnlock()
	if !ok {
		return CalculateForSymbol(symbol, nil, nil, time.Now()), true
	}
	return expireCachedPreopen(snapshot, time.Now()), true
}

// Enforce expiry on reads as well as refreshes, even if an upstream/DB request
// delays the periodic refresh. This never rewrites persisted historical rows.
func expireCachedPreopen(snapshot Snapshot, now time.Time) Snapshot {
	if snapshot.Input == nil || snapshot.Input.FX.Source != CFETSPreopenFallbackSource {
		return snapshot
	}
	definition, ok := Definition(snapshot.Symbol)
	if ok && acceptsCFETSPreopenFallback(definition, *snapshot.Input, now) {
		return snapshot
	}
	snapshot.Ready, snapshot.Actionable = false, false
	snapshot.Valuation = nil
	snapshot.OrderBook = []OrderBookValuation{}
	snapshot.CalculationState = "expired"
	snapshot.Warnings = append(append([]string{}, snapshot.Warnings...), "预开盘缓存已失效，等待当日 CFETS 与新期货报价")
	return snapshot
}

func (s *Service) MinuteHistory(ctx context.Context, symbol string, days int) (MinuteHistoryResponse, error) {
	if s == nil {
		return MinuteHistoryResponse{}, fmt.Errorf("private valuation service is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if !Supported(symbol) {
		return MinuteHistoryResponse{}, fmt.Errorf("private fund not found")
	}
	days, err := NormalizeMinuteHistoryDays(days)
	if err != nil {
		return MinuteHistoryResponse{}, err
	}

	rows := make([]MinuteHistoryPoint, 0)
	if repository, ok := s.repository.(MinuteHistoryRepository); ok {
		loaded, err := repository.LoadPrivateValuationMinuteHistory(ctx, symbol, days)
		if err != nil {
			return MinuteHistoryResponse{}, err
		}
		rows = append(rows, loaded...)
	}
	if snapshot, ok := s.Fund(symbol); ok {
		if current, ok := MinuteHistoryPointFromSnapshot(snapshot); ok {
			rows = append(rows, current)
		}
	}

	byMinute := make(map[string]MinuteHistoryPoint, len(rows))
	for _, row := range rows {
		if !IsMinuteHistoryTradingSessionForSymbol(symbol, row.Minute) {
			continue
		}
		key := row.Minute.In(shanghaiLocation).Truncate(time.Minute).Format(time.RFC3339)
		byMinute[key] = row
	}
	rows = rows[:0]
	for _, row := range byMinute {
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Minute.Before(rows[j].Minute) })
	return MinuteHistoryResponse{Symbol: symbol, Days: days, Rows: rows}, nil
}

func (s *Service) MinuteHistoryDate(ctx context.Context, symbol, day string) (MinuteHistoryResponse, error) {
	if s == nil {
		return MinuteHistoryResponse{}, fmt.Errorf("private valuation service is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if !Supported(symbol) {
		return MinuteHistoryResponse{}, fmt.Errorf("private fund not found")
	}
	day = strings.ReplaceAll(strings.TrimSpace(day), "-", "")
	if _, err := time.ParseInLocation("20060102", day, shanghaiLocation); err != nil {
		return MinuteHistoryResponse{}, fmt.Errorf("date must be YYYYMMDD")
	}
	repository, ok := s.repository.(MinuteHistoryDateRepository)
	if !ok {
		return MinuteHistoryResponse{}, fmt.Errorf("private history dates unavailable")
	}
	rows, err := repository.LoadPrivateValuationMinuteHistoryDate(ctx, symbol, day)
	if err != nil {
		return MinuteHistoryResponse{}, err
	}
	filtered := rows[:0]
	for _, row := range rows {
		if IsMinuteHistoryTradingSessionForSymbol(symbol, row.Minute) {
			filtered = append(filtered, row)
		}
	}
	sort.Slice(filtered, func(i, j int) bool { return filtered[i].Minute.Before(filtered[j].Minute) })
	return MinuteHistoryResponse{Symbol: symbol, Days: 1, Rows: filtered}, nil
}

func (s *Service) MinuteHistoryDates(ctx context.Context, symbol string, limit int) ([]string, error) {
	if s == nil {
		return nil, fmt.Errorf("private valuation service is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if !Supported(symbol) {
		return nil, fmt.Errorf("private fund not found")
	}
	repository, ok := s.repository.(MinuteHistoryDateRepository)
	if !ok {
		return nil, fmt.Errorf("private history dates unavailable")
	}
	if limit <= 0 || limit > 90 {
		limit = 90
	}
	return repository.LoadPrivateValuationMinuteHistoryDates(ctx, symbol, limit)
}

func (s *Service) SilverCloseHistory(ctx context.Context, symbol string, days int) (SilverCloseHistoryResponse, error) {
	if s == nil {
		return SilverCloseHistoryResponse{}, fmt.Errorf("private valuation service is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol != SZ161226Symbol {
		return SilverCloseHistoryResponse{}, fmt.Errorf("silver close history is only available for %s", SZ161226Symbol)
	}
	if days <= 0 {
		days = 365
	}
	if days > 3650 {
		return SilverCloseHistoryResponse{}, fmt.Errorf("days must not exceed 3650")
	}
	repository, ok := s.repository.(SilverCloseHistoryRepository)
	if !ok {
		return SilverCloseHistoryResponse{}, fmt.Errorf("silver close history is unavailable")
	}
	rows, err := repository.LoadPrivateSilverCloseHistory(ctx, symbol, days)
	if err != nil {
		return SilverCloseHistoryResponse{}, err
	}
	for index := range rows {
		row := &rows[index]
		if row.OfficialNAV != nil && finitePositive(*row.OfficialNAV) && finitePositive(row.SettlementNAV) {
			deviation := round(row.SettlementNAV/(*row.OfficialNAV)-1, 10)
			row.SettlementDeviationRate = &deviation
		}
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].TradingDay == rows[j].TradingDay {
			return rows[i].CloseMinute.After(rows[j].CloseMinute)
		}
		return rows[i].TradingDay > rows[j].TradingDay
	})
	return SilverCloseHistoryResponse{
		SchemaVersion:   SchemaVersion,
		Symbol:          SZ161226Symbol,
		Name:            SZ161226Name,
		ModelVersion:    SZ161226ModelVersion,
		MethodologyNote: "每日取北京时间 15:00 前最后一个已持久化估值点；结算估值偏差 = 结算估值 ÷ 同日官方净值 − 1。官方净值公布后自动补齐偏差。",
		Rows:            rows,
	}, nil
}

// IndiaHistoryReview returns a diagnostic calibration history for SZ164824.
// It fits a rolling scalar against its own T-2 NAV return chain; the scalar is
// a model exposure coefficient, never an ETF creation/redemption ratio.
func (s *Service) IndiaHistoryReview(ctx context.Context, symbol string, days int) (IndiaHistoryReviewResponse, error) {
	if s == nil {
		return IndiaHistoryReviewResponse{}, fmt.Errorf("private valuation service is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol != SZ164824Symbol {
		return IndiaHistoryReviewResponse{}, fmt.Errorf("India history review is only available for %s", SZ164824Symbol)
	}
	if days <= 0 || days > 365 {
		days = 120
	}
	repository, ok := s.repository.(IndiaHistoryReviewRepository)
	if !ok {
		return IndiaHistoryReviewResponse{}, fmt.Errorf("private India history review is unavailable")
	}
	sources, err := repository.LoadPrivateIndiaHistoryReview(ctx, symbol, days)
	if err != nil {
		return IndiaHistoryReviewResponse{}, err
	}
	rows := buildIndiaHistoryReviewRows(sources, 10)
	definition, _ := Definition(symbol)
	return IndiaHistoryReviewResponse{
		Symbol:          symbol,
		Name:            definition.Name,
		ModelVersion:    definition.ModelVersion,
		FitWindow:       10,
		Rows:            rows,
		MethodologyNote: "本表只复盘最终净值：以官方 T−2 净值为基准，分别取 T−2 与 T 日日本、香港、欧洲、美国四个收盘时点的 INDA 加权锚点，并按对应两日 SAFE 人民币兑美元中间价换算。最终估值=T−2净值×[静态比例+风险资产比例×(T日加权锚点/T−2加权锚点)×(T日汇率/T−2汇率)]；偏差率=(最终估值/官方 T 日净值−1)。NIFTY 桥接仅保留在 Private 标的详情页的中国盘中实时 IOPV，不参与本表。模型暴露系数以最近 10 个已公布样本作无截距收益率拟合，仅用于复盘校准，不是基金实际仓位或交易指令。",
	}, nil
}

// IndiaNiftyBridgeHistoryReview compares the production NIFTY bridge with the
// direct-INDA valuation at identical domestic minutes. Summary MAEs are first
// averaged within each trading day, then across days, so a partially captured
// day cannot be outweighed by a day with more minute rows.
func (s *Service) IndiaNiftyBridgeHistoryReview(ctx context.Context, symbol string, days int) (IndiaNiftyBridgeReviewResponse, error) {
	if s == nil {
		return IndiaNiftyBridgeReviewResponse{}, fmt.Errorf("private valuation service is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol != SZ164824Symbol {
		return IndiaNiftyBridgeReviewResponse{}, fmt.Errorf("India NIFTY bridge history review is only available for %s", SZ164824Symbol)
	}
	if days <= 0 || days > 365 {
		days = 120
	}
	repository, ok := s.repository.(IndiaNiftyBridgeReviewRepository)
	if !ok {
		return IndiaNiftyBridgeReviewResponse{}, fmt.Errorf("private India NIFTY bridge history review is unavailable")
	}
	sources, err := repository.LoadPrivateIndiaNiftyBridgeHistoryReview(ctx, symbol, days)
	if err != nil {
		return IndiaNiftyBridgeReviewResponse{}, err
	}
	checkpoints, summary, rows := buildIndiaNiftyBridgeReview(sources)
	asOf := time.Now()
	if len(sources) > 0 {
		asOf = sources[0].Minute
		for _, source := range sources[1:] {
			if source.Minute.After(asOf) {
				asOf = source.Minute
			}
		}
	}
	definition, _ := Definition(symbol)
	return IndiaNiftyBridgeReviewResponse{
		SchemaVersion:   IndiaNiftyBridgeReviewSchemaVersion,
		Symbol:          symbol,
		Name:            definition.Name,
		ModelVersion:    definition.ModelVersion,
		AsOf:            asOf,
		MethodologyNote: "本页复盘中国盘中 NIFTY 桥接是否改善相对国内分钟价的溢价率估计。同一分钟以公开 market_price 分别除以直接 INDA 与 NIFTY 桥接的 NAV Bid/Ask；官方溢价率=market_price/官方 T 日净值−1，误差为模型溢价率减官方溢价率，单位 bps。汇总先在每个交易日内对全分钟取平均，再对交易日等权平均；ΔMAE=桥接绝对误差−直接 INDA 绝对误差，负值表示改善。桥接 NAV 直接采用生产引擎结果，合成 INDA 则从保存的同步 Bid/Ask 独立重算以供审计。历史国内行情只保存同分钟公开分钟价，不是当时买一/卖一，因而本页不是可执行盘口价差回放。",
		Checkpoints:     checkpoints,
		Timing:          indiaNiftyBridgeReviewTiming(),
		Summary:         summary,
		Rows:            rows,
	}, nil
}

// ImportIndiaFinalNAVHistory stores only end-of-day final-NAV replays.  It
// never writes the intraday minute-history table, so historical reporting
// cannot overwrite the NIFTY bridge IOPV used during China trading hours.
func (s *Service) ImportIndiaFinalNAVHistory(ctx context.Context, symbol string, rows []IndiaFinalNAVHistoryInput) (int, error) {
	if s == nil || s.repository == nil {
		return 0, fmt.Errorf("private India final-NAV history repository is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol != SZ164824Symbol || len(rows) == 0 || len(rows) > 366 {
		return 0, fmt.Errorf("India final-NAV history import must contain 1 to 366 %s rows", SZ164824Symbol)
	}
	repository, ok := s.repository.(IndiaFinalNAVHistoryRepository)
	if !ok {
		return 0, fmt.Errorf("private India final-NAV history repository is unavailable")
	}
	points := make([]IndiaFinalNAVHistoryPoint, 0, len(rows))
	seen := make(map[string]struct{}, len(rows))
	for _, row := range rows {
		point, err := prepareIndiaFinalNAVHistoryPoint(row)
		if err != nil {
			return 0, err
		}
		if _, exists := seen[point.TargetDate]; exists {
			return 0, fmt.Errorf("duplicate India final-NAV history target date: %s", point.TargetDate)
		}
		seen[point.TargetDate] = struct{}{}
		points = append(points, point)
	}
	if err := repository.UpsertPrivateIndiaFinalNAVHistory(ctx, symbol, points); err != nil {
		return 0, err
	}
	return len(points), nil
}

// ImportHistoricalMinutes writes only private snapshots. It never updates the
// current input or public quote state, and accepts only China-session minutes.
func (s *Service) ImportHistoricalMinutes(ctx context.Context, symbol string, rows []HistoricalMinuteInput) (int, error) {
	snapshots, _, err := s.prepareHistoricalMinutes(symbol, rows)
	if err != nil {
		return 0, err
	}
	for _, snapshot := range snapshots {
		if err := s.repository.UpsertPrivateValuationSnapshot(ctx, snapshot); err != nil {
			return 0, err
		}
	}
	return len(snapshots), nil
}

// ReplaceHistoricalMinutes validates and calculates a complete day before the
// repository transaction removes the old day. This prevents a partial replay
// or a missing futures minute from leaving stale old-model rows in the chart.
func (s *Service) ReplaceHistoricalMinutes(ctx context.Context, symbol string, rows []HistoricalMinuteInput) (int, error) {
	snapshots, day, err := s.prepareHistoricalMinutes(symbol, rows)
	if err != nil {
		return 0, err
	}
	replacer, ok := s.repository.(HistoricalMinuteDayReplacer)
	if !ok {
		return 0, fmt.Errorf("private valuation history repository cannot replace a complete day")
	}
	if err := replacer.ReplacePrivateValuationSnapshots(ctx, symbol, day, snapshots); err != nil {
		return 0, err
	}
	return len(snapshots), nil
}

func (s *Service) prepareHistoricalMinutes(symbol string, rows []HistoricalMinuteInput) ([]Snapshot, time.Time, error) {
	if s == nil || s.repository == nil {
		return nil, time.Time{}, fmt.Errorf("private valuation history repository is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if !Supported(symbol) || len(rows) == 0 || len(rows) > 500 {
		return nil, time.Time{}, fmt.Errorf("private history import must contain 1 to 500 supported rows")
	}
	seen := make(map[string]struct{}, len(rows))
	snapshots := make([]Snapshot, 0, len(rows))
	var historyDay time.Time
	for _, row := range rows {
		if !IsMinuteHistoryTradingSessionForSymbol(symbol, row.Minute) || !finitePositive(row.MarketPrice) {
			return nil, time.Time{}, fmt.Errorf("historical minute must be an in-session positive-price point")
		}
		input := row.Input.Normalized(row.Minute)
		input.ReceivedAt = row.Minute
		if input.Symbol == "" {
			input.Symbol = symbol
		}
		minuteDay := row.Minute.In(shanghaiLocation).Format("2006-01-02")
		parsedMinuteDay, err := time.ParseInLocation("2006-01-02", minuteDay, shanghaiLocation)
		if err != nil {
			return nil, time.Time{}, err
		}
		if historyDay.IsZero() {
			historyDay = parsedMinuteDay
		} else if !historyDay.Equal(parsedMinuteDay) {
			return nil, time.Time{}, fmt.Errorf("historical replacement rows must belong to one Shanghai trading day")
		}
		pcfMatchesMinute := input.ValuationAnchorDate() == minuteDay
		if !pcfMatchesMinute && input.IsHistoricalFixedIndexProxy() {
			pcfDay, err := time.ParseInLocation("2006-01-02", input.PCF.TradingDay, shanghaiLocation)
			minuteDate, minuteErr := time.ParseInLocation("2006-01-02", minuteDay, shanghaiLocation)
			pcfMatchesMinute = err == nil && minuteErr == nil && !pcfDay.Before(minuteDate)
		}
		if definition, ok := Definition(symbol); ok && (definition.CalculationMode == CalculationModeIndiaT2MultiMarket || definition.CalculationMode == CalculationModeLOFWeightedAnchor || definition.CalculationMode == CalculationModeSilverSettlement) {
			anchorDay, err := time.ParseInLocation("2006-01-02", input.ValuationAnchorDate(), shanghaiLocation)
			minuteDate, minuteErr := time.ParseInLocation("2006-01-02", minuteDay, shanghaiLocation)
			// The released LOF NAV is normally T-2. It must be dated no later
			// than the domestic minute being replayed, never a future NAV.
			pcfMatchesMinute = err == nil && minuteErr == nil && !anchorDay.After(minuteDate)
		}
		if input.Symbol != symbol || !pcfMatchesMinute {
			return nil, time.Time{}, fmt.Errorf("historical input symbol or PCF date does not match minute")
		}
		if err := input.Validate(); err != nil {
			return nil, time.Time{}, err
		}
		key := row.Minute.In(shanghaiLocation).Truncate(time.Minute).Format(time.RFC3339)
		if _, exists := seen[key]; exists {
			return nil, time.Time{}, fmt.Errorf("duplicate historical minute %s", key)
		}
		seen[key] = struct{}{}
		definition, _ := Definition(symbol)
		quote := domain.Quote{Symbol: symbol, Name: definition.Name, Price: row.MarketPrice, PrevClose: row.MarketPrice, BidLevels: []domain.Level{{Level: 1, Price: row.MarketPrice}}, AskLevels: []domain.Level{{Level: 1, Price: row.MarketPrice}}, QuoteDate: row.Minute.In(shanghaiLocation).Format("2006-01-02"), QuoteTime: row.Minute.In(shanghaiLocation).Format("15:04:05"), Source: "public_minute_history", QuoteSession: "cn_regular"}
		snapshot := CalculateForSymbol(symbol, &input, map[string]domain.Quote{symbol: quote}, row.Minute)
		if !snapshot.Ready || snapshot.Valuation == nil {
			return nil, time.Time{}, fmt.Errorf("failed to calculate historical minute %s", key)
		}
		if symbol == SZ161226Symbol {
			snapshot.Warnings = append(snapshot.Warnings, "历史回填：基金价格来自公开行情，AG 收盘/结算来自 Sina 日线，仅回填日终点")
		} else {
			snapshot.Warnings = append(snapshot.Warnings, "历史回填：使用公开分钟标的价格与 IBKR BID_ASK 一分钟数据，仅供复盘")
		}
		snapshots = append(snapshots, snapshot)
	}
	return snapshots, historyDay, nil
}

func (s *Service) List() ListResponse {
	now := time.Now()
	response := ListResponse{
		SchemaVersion: SchemaVersion,
		AsOf:          now,
		Funds:         make([]ListItem, 0, len(Definitions())),
	}
	for _, definition := range Definitions() {
		snapshot, _ := s.Fund(definition.Symbol)
		item := ListItem{
			Symbol:        snapshot.Symbol,
			Name:          snapshot.Name,
			ModelVersion:  snapshot.ModelVersion,
			ValuationKind: snapshot.ValuationKind,
			Ready:         snapshot.Ready,
			Actionable:    snapshot.Actionable,
			AsOf:          snapshot.AsOf,
			Warnings:      append([]string(nil), snapshot.Warnings...),
		}
		if snapshot.DomesticQuote != nil {
			if level, ok := bestLevel(snapshot.DomesticQuote.BidLevels); ok {
				value := level.Price
				item.MarketBid = &value
			}
			if level, ok := bestLevel(snapshot.DomesticQuote.AskLevels); ok {
				value := level.Price
				item.MarketAsk = &value
			}
		}
		if snapshot.Valuation != nil {
			navBid := snapshot.Valuation.NAVBid
			navAsk := snapshot.Valuation.NAVAsk
			buyPremium := snapshot.Valuation.BuyDirectionPremiumRate
			sellPremium := snapshot.Valuation.SellDirectionPremiumRate
			item.BasketBidNAV = &navBid
			item.BasketAskNAV = &navAsk
			item.BuyDirectionPremiumRate = &buyPremium
			item.SellDirectionPremiumRate = &sellPremium
		}
		if silver := snapshot.SilverValuation; silver != nil {
			settlementNAV := silver.SettlementNAV
			tradingNAV := silver.TradingNAV
			settlementPremium := silver.SettlementPremiumRate
			tradingPremium := silver.TradingPremiumRate
			item.SettlementNAV = &settlementNAV
			item.TradingNAV = &tradingNAV
			item.SettlementPremiumRate = &settlementPremium
			item.TradingPremiumRate = &tradingPremium
			item.ActiveContract = silver.Contract
		}
		response.Funds = append(response.Funds, item)
		if snapshot.AsOf.After(response.AsOf) {
			response.AsOf = snapshot.AsOf
		}
	}
	return response
}
