package snapshot

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/sina"
	"newnavnav/internal/valuation"
)

type SinaClient interface {
	FetchQuotes(ctx context.Context, symbols []string) (map[string]domain.Quote, error)
}

type ValuationRepository interface {
	UpsertDailyPrices(ctx context.Context, prices []domain.DailyPrice) error
	LoadValuationData(ctx context.Context) (domain.ValuationData, error)
}

type holdingSnapshotRepository interface {
	UpsertHoldingSnapshot(ctx context.Context, fundSymbol string, holdingDate string, position float64, source string, holdings []domain.Holding) (int, error)
}

type effectiveRatioFitHistoryLoader interface {
	LoadEffectiveRatioFitHistory(ctx context.Context, symbol string, days int) ([]domain.EffectiveRatioFitHistoryRow, error)
}

type shareHistoryLoader interface {
	LoadShareHistory(ctx context.Context, symbol string, days int) ([]domain.ShareHistoryRecord, error)
}

type latestQuoteLoader interface {
	LoadLatestQuotes(ctx context.Context) (map[string]domain.Quote, error)
}

type ServiceOptions struct {
	Sina                       SinaClient
	Repository                 ValuationRepository
	ShareHistory               shareHistoryLoader
	SnapshotDataDir            string
	SnapshotHTMLDir            string
	UploadedQuoteCachePath     string
	PurchaseInfoCachePath      string
	MinuteHistoryDir           string
	MinuteHistoryRetentionDays int
	RefreshInterval            time.Duration
	EnableSina                 bool
	EnableUploadQuotes         bool
}

type Service struct {
	sina               SinaClient
	repository         ValuationRepository
	shareHistory       shareHistoryLoader
	engine             *valuation.Engine
	snapshotDataDir    string
	snapshotHTMLDir    string
	uploadedQuoteCache string
	purchaseInfoCache  string
	minuteHistory      *MinuteHistoryStore
	refreshInterval    time.Duration
	enableSina         bool
	enableUploadQuotes bool
	navSyncStatus      func() any
	runtimeStatus      func() any
	now                func() time.Time

	mu                      sync.RWMutex
	quotes                  map[string]domain.Quote
	uploadedQuotes          map[string]domain.Quote
	purchaseInfos           map[string]domain.PurchaseInfo
	branches                map[string]domain.BranchSnapshot
	valuationData           domain.ValuationData
	lastError               string
	lastRefreshAt           time.Time
	events                  []SystemEvent
	wsClients               map[string]WSClientStatus
	uploadSources           map[string]UploadSourceStatus
	lastMinuteHistoryMinute string
}

type uploadedQuoteCacheFile struct {
	StoredAt time.Time      `json:"stored_at"`
	Quotes   []domain.Quote `json:"quotes"`
}

type purchaseInfoCacheFile struct {
	StoredAt time.Time             `json:"stored_at"`
	Infos    []domain.PurchaseInfo `json:"infos"`
}

func NewService(opts ServiceOptions) *Service {
	cachePath := strings.TrimSpace(opts.UploadedQuoteCachePath)
	if cachePath == "" && strings.TrimSpace(opts.SnapshotDataDir) != "" {
		cachePath = filepath.Join(opts.SnapshotDataDir, "uploaded_quotes.latest.json")
	}
	purchaseInfoCachePath := strings.TrimSpace(opts.PurchaseInfoCachePath)
	if purchaseInfoCachePath == "" && strings.TrimSpace(opts.SnapshotDataDir) != "" {
		purchaseInfoCachePath = filepath.Join(opts.SnapshotDataDir, "purchase_infos.latest.json")
	}
	return &Service{
		sina:               opts.Sina,
		repository:         opts.Repository,
		shareHistory:       opts.ShareHistory,
		engine:             valuation.NewEngine(),
		snapshotDataDir:    opts.SnapshotDataDir,
		snapshotHTMLDir:    opts.SnapshotHTMLDir,
		uploadedQuoteCache: cachePath,
		purchaseInfoCache:  purchaseInfoCachePath,
		minuteHistory:      NewMinuteHistoryStore(opts.MinuteHistoryDir, opts.MinuteHistoryRetentionDays),
		refreshInterval:    opts.RefreshInterval,
		enableSina:         opts.EnableSina,
		enableUploadQuotes: opts.EnableUploadQuotes,
		now:                time.Now,
		quotes:             make(map[string]domain.Quote),
		uploadedQuotes:     make(map[string]domain.Quote),
		purchaseInfos:      make(map[string]domain.PurchaseInfo),
		branches:           make(map[string]domain.BranchSnapshot),
		valuationData:      domain.EmptyValuationData(),
		wsClients:          make(map[string]WSClientStatus),
		uploadSources:      make(map[string]UploadSourceStatus),
	}
}

func (s *Service) nowTime() time.Time {
	if s != nil && s.now != nil {
		return s.now()
	}
	return time.Now()
}

func (s *Service) WarmLatestQuotes(ctx context.Context) error {
	if s == nil {
		return nil
	}
	var firstErr error
	totalWarmed := 0
	cacheWarmed := 0
	dbWarmed := 0
	if quotes, err := s.loadUploadedQuoteCache(); err != nil {
		firstErr = err
	} else {
		cacheWarmed = s.mergeWarmQuotes(quotes)
		totalWarmed += cacheWarmed
	}
	if s.repository != nil {
		if loader, ok := s.repository.(latestQuoteLoader); ok && loader != nil {
			quotes, err := loader.LoadLatestQuotes(ctx)
			if err != nil {
				if firstErr == nil {
					firstErr = err
				}
			} else {
				dbWarmed = s.mergeWarmQuotes(quotes)
				totalWarmed += dbWarmed
			}
		}
	}
	if totalWarmed == 0 {
		return firstErr
	}
	s.mu.Lock()
	s.recordEventLocked("info", "startup", "warmed quote fallback", map[string]any{
		"total_quotes": totalWarmed,
		"cache_quotes": cacheWarmed,
		"db_quotes":    dbWarmed,
	})
	s.mu.Unlock()
	return nil
}

func (s *Service) WarmPurchaseInfos(ctx context.Context) error {
	if s == nil {
		return nil
	}
	infos, err := s.loadPurchaseInfoCache()
	if err != nil {
		return err
	}
	if len(infos) == 0 {
		return nil
	}
	return s.UpdatePurchaseInfos(ctx, infos)
}

func (s *Service) mergeWarmQuotes(quotes map[string]domain.Quote) int {
	if s == nil || len(quotes) == 0 {
		return 0
	}
	warmed := 0
	s.mu.Lock()
	defer s.mu.Unlock()
	for symbol, quote := range quotes {
		if existing, ok := s.quotes[symbol]; !ok || existing.Price <= 0 {
			s.quotes[symbol] = quote
			warmed++
		}
		if existing, ok := s.uploadedQuotes[symbol]; !ok || existing.Price <= 0 {
			s.uploadedQuotes[symbol] = quote
		}
	}
	return warmed
}

func (s *Service) Run(ctx context.Context) {
	interval := s.refreshInterval
	if interval <= 0 {
		interval = 5 * time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := s.Refresh(ctx); err != nil {
				slog.Warn("snapshot refresh failed", "error", err)
			}
		}
	}
}

func (s *Service) Refresh(ctx context.Context) error {
	valuationData := domain.EmptyValuationData()
	storedQuotes := s.CurrentQuotes()
	purchaseInfos := s.CurrentPurchaseInfos()
	var loadErr error
	if s.repository != nil {
		valuationData, loadErr = s.repository.LoadValuationData(ctx)
		if loadErr != nil {
			slog.Warn("load valuation data failed", "error", loadErr)
		}
	}
	domain.ApplyStaticValuationData(&valuationData)

	branchSymbols := domain.AllSymbols()
	allSymbols := mergeSymbols(branchSymbols, valuationData.RelatedSymbolsForFunds(branchSymbols))
	fetched := make(map[string]domain.Quote)
	var err error
	if s.enableSina && s.sina != nil {
		fetched, err = s.sina.FetchQuotes(ctx, allSymbols)
	}

	now := s.nowTime()
	quotes := make(map[string]domain.Quote, len(allSymbols))
	for _, symbol := range allSymbols {
		fetchedQuote, hasFetched := fetched[symbol]
		storedQuote, hasStored := storedQuotes[symbol]
		if shouldPreferStoredQuote(now, fetchedQuote, storedQuote) {
			quotes[symbol] = storedQuote
		} else if hasFetched && fetchedQuote.Price > 0 {
			quotes[symbol] = fetchedQuote
		} else if hasStored && storedQuote.Price > 0 {
			quotes[symbol] = storedQuote
		} else {
			quotes[symbol] = missingQuote(symbol, now)
		}
	}
	hasFreshUploadedQuotes := false
	uploadSource := ""
	if s.enableUploadQuotes {
		hasFreshUploadedQuotes, uploadSource = s.applyUploadedQuotes(quotes, now)
	}

	if hasFreshUploadedQuotes && s.claimMinuteHistoryRecord(now) {
		if writeErr := s.recordMinuteHistory(now, quotes, valuationData, uploadSource); writeErr != nil {
			slog.Warn("record minute valuation history failed", "error", writeErr)
		}
	}

	branches := make(map[string]domain.BranchSnapshot, len(domain.Branches))
	for _, branch := range domain.Branches {
		snapshot := s.buildBranchSnapshot(branch, quotes, valuationData, purchaseInfos, now)
		branches[branch.Key] = snapshot
		if writeErr := s.writeSnapshotFiles(snapshot); writeErr != nil {
			slog.Warn("write snapshot file failed", "branch", branch.Key, "error", writeErr)
		}
	}

	s.mu.Lock()
	s.quotes = quotes
	s.branches = branches
	s.valuationData = valuationData
	s.lastRefreshAt = now
	if err != nil || loadErr != nil {
		if err != nil {
			s.lastError = err.Error()
		} else {
			s.lastError = loadErr.Error()
		}
	} else {
		s.lastError = ""
	}
	s.mu.Unlock()

	return err
}

func (s *Service) claimMinuteHistoryRecord(now time.Time) bool {
	if !isMinuteHistorySessionTime(now) {
		return false
	}
	minute := minuteHistoryMinute(now)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.lastMinuteHistoryMinute == minute {
		return false
	}
	s.lastMinuteHistoryMinute = minute
	return true
}

func (s *Service) recordMinuteHistory(now time.Time, quotes map[string]domain.Quote, valuationData domain.ValuationData, uploadSource string) error {
	if s.minuteHistory == nil {
		return nil
	}
	rows := make([]MinuteHistoryPoint, 0, len(domain.AllSymbols()))
	for _, symbol := range domain.AllSymbols() {
		quote, ok := quotes[symbol]
		if !ok || quote.Price <= 0 || quote.Source == "missing" || quote.Source == "demo" || quote.Error == "demo fallback" {
			continue
		}
		estimate := s.engine.Estimate(quote, quotes, valuationData, now)
		if estimate.FairEst <= 0 && (estimate.RealtimeEst == nil || *estimate.RealtimeEst <= 0) {
			continue
		}
		estimatedNAV := estimate.FairEst
		premiumPct := estimate.FairPremium
		if estimate.RealtimeEst != nil && *estimate.RealtimeEst > 0 {
			estimatedNAV = *estimate.RealtimeEst
			if estimate.RealtimePremium != nil {
				premiumPct = *estimate.RealtimePremium
			} else {
				premiumPct = (quote.Price/estimatedNAV - 1) * 100
			}
		}
		rows = append(rows, MinuteHistoryPoint{
			Symbol:          symbol,
			Minute:          minuteHistoryMinute(now),
			Timestamp:       now.Format(time.RFC3339Nano),
			MarketPrice:     quote.Price,
			EstimatedNAV:    estimatedNAV,
			PremiumPct:      premiumPct,
			OfficialEST:     estimate.OfficialEst,
			FairEST:         estimate.FairEst,
			RealtimeEST:     estimate.RealtimeEst,
			EffectiveRatio:  estimate.EffectiveRatio,
			QuoteSource:     publicSourceLabel(quote.Source),
			QuoteStatus:     quote.RealtimeStatus,
			ModelVersion:    estimate.ModelVersion,
			ReferenceSymbol: estimate.ReferenceSymbol,
			UploadSource:    uploadSource,
		})
	}
	return s.minuteHistory.Upsert(now, rows)
}

func (s *Service) Branches() []domain.Branch {
	branches := append([]domain.Branch(nil), domain.Branches...)
	sort.Slice(branches, func(i, j int) bool {
		return branches[i].SortOrder < branches[j].SortOrder
	})
	return branches
}

func (s *Service) EffectiveRatioFitHistory(ctx context.Context, symbol string, days int) (domain.EffectiveRatioFitHistoryResponse, error) {
	loader, ok := any(s.repository).(effectiveRatioFitHistoryLoader)
	if !ok || loader == nil {
		return domain.EffectiveRatioFitHistoryResponse{
			Symbol:             strings.ToUpper(strings.TrimSpace(symbol)),
			WindowSize:         5,
			BaseLagTradingDays: 2,
		}, nil
	}
	rows, err := loader.LoadEffectiveRatioFitHistory(ctx, symbol, days)
	if err != nil {
		return domain.EffectiveRatioFitHistoryResponse{}, err
	}
	response := domain.EffectiveRatioFitHistoryResponse{
		Symbol:             strings.ToUpper(strings.TrimSpace(symbol)),
		WindowSize:         5,
		BaseLagTradingDays: 2,
		Rows:               rows,
	}
	if len(rows) > 0 {
		response.Symbol = rows[0].Symbol
		response.Name = rows[0].Name
		response.WindowSize = rows[0].WindowSize
		response.BaseLagTradingDays = rows[0].BaseLagTradingDays
	}
	return response, nil
}

func (s *Service) ShareHistory(ctx context.Context, symbol string, days int) (domain.ShareHistoryResponse, error) {
	clean := strings.ToUpper(strings.TrimSpace(symbol))
	if s.shareHistory == nil {
		return domain.ShareHistoryResponse{
			Symbol: clean,
			Unit:   "万份",
		}, nil
	}
	rows, err := s.shareHistory.LoadShareHistory(ctx, clean, days)
	if err != nil {
		return domain.ShareHistoryResponse{}, err
	}
	response := domain.ShareHistoryResponse{
		Symbol: clean,
		Unit:   "万份",
		Rows:   rows,
	}
	if len(rows) > 0 {
		response.Symbol = rows[0].Symbol
		response.Name = rows[0].Name
		response.FundType = rows[0].FundType
	}
	return response, nil
}

func (s *Service) YesterdayRedemptionBoard(ctx context.Context) (domain.YesterdayRedemptionBoardResponse, error) {
	response := domain.YesterdayRedemptionBoardResponse{
		Unit: "万份",
	}
	if s.shareHistory == nil {
		return response, nil
	}

	type latestRow struct {
		symbol string
		row    domain.ShareHistoryRecord
	}

	symbols := trackedShareHistorySymbols()
	response.TrackedSymbols = len(symbols)
	latestRows := make([]latestRow, 0, len(symbols))
	latestShareDate := ""

	for _, symbol := range symbols {
		rows, err := s.shareHistory.LoadShareHistory(ctx, symbol, 2)
		if err != nil {
			return domain.YesterdayRedemptionBoardResponse{}, err
		}
		if len(rows) == 0 {
			continue
		}
		latestRows = append(latestRows, latestRow{
			symbol: symbol,
			row:    rows[0],
		})
		if rows[0].ShareDate > latestShareDate {
			latestShareDate = rows[0].ShareDate
		}
	}

	response.ShareDate = latestShareDate
	if latestShareDate == "" {
		return response, nil
	}

	boardRows := make([]domain.YesterdayRedemptionBoardRow, 0, len(latestRows))
	for _, item := range latestRows {
		if item.row.ShareDate != latestShareDate {
			response.StaleSymbols++
			continue
		}
		if item.row.ShareChange10K == nil || item.row.ShareChangePct == nil {
			response.MissingChangeRows++
			continue
		}
		switch change := *item.row.ShareChange10K; {
		case change < 0:
			response.RedemptionCount++
		case change > 0:
			response.SubscriptionCount++
		default:
			response.FlatCount++
		}
		boardRows = append(boardRows, domain.YesterdayRedemptionBoardRow{
			Symbol:            item.row.Symbol,
			Name:              item.row.Name,
			ShareDate:         item.row.ShareDate,
			Shares10K:         item.row.Shares10K,
			PreviousShares10K: item.row.PreviousShares10K,
			ShareChange10K:    item.row.ShareChange10K,
			ShareChangePct:    item.row.ShareChangePct,
			ClosePremiumPct:   item.row.ClosePremiumPct,
			BranchNames:       branchNamesForSymbol(item.symbol),
		})
	}

	sort.Slice(boardRows, func(i, j int) bool {
		leftChange := *boardRows[i].ShareChange10K
		rightChange := *boardRows[j].ShareChange10K
		if leftChange != rightChange {
			return leftChange < rightChange
		}
		leftPct := *boardRows[i].ShareChangePct
		rightPct := *boardRows[j].ShareChangePct
		if leftPct != rightPct {
			return leftPct < rightPct
		}
		if boardRows[i].Shares10K != boardRows[j].Shares10K {
			return boardRows[i].Shares10K > boardRows[j].Shares10K
		}
		return boardRows[i].Symbol < boardRows[j].Symbol
	})

	response.Rows = boardRows
	response.IncludedSymbols = len(boardRows)
	return response, nil
}

func trackedShareHistorySymbols() []string {
	seen := make(map[string]bool)
	symbols := make([]string, 0, len(domain.Branches))
	for _, symbol := range domain.AllSymbols() {
		symbol = strings.ToUpper(strings.TrimSpace(symbol))
		if symbol == "" || seen[symbol] {
			continue
		}
		if !strings.HasPrefix(symbol, "SH") && !strings.HasPrefix(symbol, "SZ") {
			continue
		}
		seen[symbol] = true
		symbols = append(symbols, symbol)
	}
	sort.Strings(symbols)
	return symbols
}

func branchNamesForSymbol(symbol string) []string {
	branches := domain.BranchesForSymbol(strings.ToUpper(strings.TrimSpace(symbol)))
	if len(branches) == 0 {
		return nil
	}
	names := make([]string, 0, len(branches))
	for _, branch := range branches {
		if strings.TrimSpace(branch.NameCN) == "" {
			continue
		}
		names = append(names, branch.NameCN)
	}
	return names
}

func (s *Service) CurrentQuotes() map[string]domain.Quote {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]domain.Quote, len(s.quotes))
	for symbol, quote := range s.quotes {
		out[symbol] = quote
	}
	return out
}

func (s *Service) CurrentPurchaseInfos() map[string]domain.PurchaseInfo {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return clonePurchaseInfos(s.purchaseInfos)
}

func (s *Service) UpdatePurchaseInfos(ctx context.Context, infos map[string]domain.PurchaseInfo) error {
	_ = ctx
	if s == nil || len(infos) == 0 {
		return nil
	}
	cleaned := normalizePurchaseInfos(infos)
	if len(cleaned) == 0 {
		return nil
	}

	var snapshots []domain.BranchSnapshot
	var cache map[string]domain.PurchaseInfo
	s.mu.Lock()
	if s.purchaseInfos == nil {
		s.purchaseInfos = make(map[string]domain.PurchaseInfo)
	}
	for symbol, info := range cleaned {
		s.purchaseInfos[symbol] = info
	}
	s.applyPurchaseInfosToBranchesLocked()
	for _, snapshot := range s.branches {
		snapshots = append(snapshots, snapshot)
	}
	cache = clonePurchaseInfos(s.purchaseInfos)
	s.recordEventLocked("info", "purchase_status", "purchase infos updated", map[string]any{
		"updated": len(cleaned),
		"cached":  len(cache),
	})
	s.mu.Unlock()

	if err := s.persistPurchaseInfoCache(cache); err != nil {
		return err
	}
	for _, snapshot := range snapshots {
		if err := s.writeSnapshotFiles(snapshot); err != nil {
			slog.Warn("write purchase status snapshot file failed", "branch", snapshot.Branch.Key, "error", err)
		}
	}
	return nil
}

func (s *Service) BranchSnapshot(key string) (domain.BranchSnapshot, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	snapshot, ok := s.branches[key]
	return snapshot, ok
}

func (s *Service) HomeSnapshot() domain.HomeSnapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()

	summaries := make([]domain.BranchSummary, 0, len(domain.Branches))
	warnings := []string{}
	asOf := time.Time{}
	for _, branch := range domain.Branches {
		snapshot, ok := s.branches[branch.Key]
		if !ok {
			warnings = append(warnings, fmt.Sprintf("%s snapshot missing", branch.Key))
			summaries = append(summaries, domain.BranchSummary{
				Branch:      branch,
				ModelCounts: map[string]int{},
				Warnings:    []string{"snapshot missing"},
			})
			continue
		}
		if snapshot.AsOf.After(asOf) {
			asOf = snapshot.AsOf
		}
		summaries = append(summaries, summarizeBranch(snapshot))
	}

	return domain.HomeSnapshot{
		SnapshotKey:   "home:funds:cn",
		SchemaVersion: "v1",
		AsOf:          asOf,
		TTLSeconds:    5,
		Branches:      summaries,
		Warnings:      warnings,
	}
}

func (s *Service) FundSnapshot(symbol string) (domain.FundSnapshot, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	quote, ok := s.quotes[symbol]
	if !ok {
		return domain.FundSnapshot{}, false
	}

	branches := domain.BranchesForSymbol(symbol)
	var estimate *domain.EstimateRow
	asOf := time.Time{}
	for _, branch := range branches {
		snapshot, exists := s.branches[branch.Key]
		if !exists {
			continue
		}
		if snapshot.AsOf.After(asOf) {
			asOf = snapshot.AsOf
		}
		for _, row := range snapshot.EstimateRows {
			if row.Symbol == symbol {
				copyRow := row
				estimate = &copyRow
			}
		}
	}
	var valuationReference *domain.Quote
	if estimate != nil && estimate.ReferenceSymbol != "" {
		if referenceQuote, ok := quoteForSymbol(s.quotes, estimate.ReferenceSymbol); ok {
			copyQuote := referenceQuote
			valuationReference = &copyQuote
		}
	}
	asOf = defaultTime(asOf, time.Now())
	valuationInputs := buildValuationInputs(symbol, quote, estimate, s.quotes, s.valuationData, asOf)

	return domain.FundSnapshot{
		Symbol:             symbol,
		Branches:           branches,
		Quote:              quote,
		Estimate:           estimate,
		ValuationReference: valuationReference,
		ValuationInputs:    valuationInputs,
		AsOf:               asOf,
	}, true
}

func (s *Service) MinuteHistory(symbol string, days int) (MinuteHistoryResponse, error) {
	if days <= 0 {
		days = 2
	}
	if s.minuteHistory != nil && s.minuteHistory.retentionDays > 0 && days > s.minuteHistory.retentionDays {
		days = s.minuteHistory.retentionDays
	}
	day := ""
	if days == 1 {
		day = minuteHistoryLogicalDay(s.nowTime()).Format("20060102")
	}
	rows, err := s.minuteHistory.Read(symbol, days, s.nowTime())
	if err != nil {
		return MinuteHistoryResponse{}, err
	}
	return MinuteHistoryResponse{
		Symbol: strings.ToUpper(strings.TrimSpace(symbol)),
		Days:   days,
		Date:   day,
		Rows:   minuteHistoryChartRows(rows, day),
	}, nil
}

func (s *Service) MinuteHistoryForDate(symbol string, day string) (MinuteHistoryResponse, error) {
	day = normalizeMinuteHistoryDayKey(day)
	rows, err := s.minuteHistory.ReadDate(symbol, day)
	if err != nil {
		return MinuteHistoryResponse{}, err
	}
	return MinuteHistoryResponse{
		Symbol: strings.ToUpper(strings.TrimSpace(symbol)),
		Days:   1,
		Date:   day,
		Rows:   minuteHistoryChartRows(rows, day),
	}, nil
}

func (s *Service) MinuteHistoryDates(symbol string, limit int) (MinuteHistoryDatesResponse, error) {
	dates, err := s.minuteHistory.AvailableDates(symbol, limit, s.nowTime())
	if err != nil {
		return MinuteHistoryDatesResponse{}, err
	}
	return MinuteHistoryDatesResponse{
		Symbol: strings.ToUpper(strings.TrimSpace(symbol)),
		Dates:  dates,
	}, nil
}

func quoteForSymbol(quotes map[string]domain.Quote, symbol string) (domain.Quote, bool) {
	if quote, ok := quotes[symbol]; ok {
		return quote, true
	}
	upper := strings.ToUpper(symbol)
	for key, quote := range quotes {
		if strings.ToUpper(key) == upper {
			return quote, true
		}
	}
	return domain.Quote{}, false
}

func summarizeBranch(snapshot domain.BranchSnapshot) domain.BranchSummary {
	summary := domain.BranchSummary{
		Branch:           snapshot.Branch,
		AsOf:             snapshot.AsOf,
		ReferenceCount:   snapshot.ReferenceCount,
		EstimateCount:    len(snapshot.EstimateRows),
		RealtimeCount:    snapshot.RealtimeCount,
		StaleCount:       snapshot.StaleCount,
		UnsupportedCount: snapshot.UnsupportedCount,
		DemoCount:        snapshot.DemoCount,
		ModelCounts:      map[string]int{},
		Warnings:         cloneWarnings(snapshot.Warnings),
	}

	totalAbs := 0.0
	for _, row := range snapshot.EstimateRows {
		summary.ModelCounts[row.ModelVersion]++
		absPremium := math.Abs(row.FairPremium)
		totalAbs += absPremium
		if absPremium > summary.MaxAbsFairPremium {
			summary.MaxAbsFairPremium = round6(absPremium)
			summary.MaxFairPremium = row.FairPremium
			summary.MaxFairPremiumSymbol = row.Symbol
		}
	}
	if len(snapshot.EstimateRows) > 0 {
		summary.AvgAbsFairPremium = round6(totalAbs / float64(len(snapshot.EstimateRows)))
	}
	return summary
}

func cloneWarnings(values []string) []string {
	if len(values) == 0 {
		return []string{}
	}
	return append([]string(nil), values...)
}

func (s *Service) Status() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.statusLocked()
}

func (s *Service) statusLocked() map[string]any {
	out := map[string]any{
		"branch_count":             len(s.branches),
		"quote_count":              len(s.quotes),
		"uploaded_quote_count":     len(s.uploadedQuotes),
		"purchase_info_count":      len(s.purchaseInfos),
		"uploaded_quotes_enabled":  s.enableUploadQuotes,
		"refresh_interval_seconds": s.refreshInterval.Seconds(),
		"last_refresh_at":          s.lastRefreshAt,
		"last_error":               s.lastError,
		"branches":                 map[string]any{},
	}
	branches := out["branches"].(map[string]any)
	for key, snapshot := range s.branches {
		branches[key] = map[string]any{
			"as_of":       snapshot.AsOf,
			"rows":        snapshot.ReferenceCount,
			"estimate":    len(snapshot.EstimateRows),
			"ttl_seconds": snapshot.TTLSeconds,
		}
	}
	return out
}

func (s *Service) buildBranchSnapshot(branch domain.Branch, quotes map[string]domain.Quote, valuationData domain.ValuationData, purchaseInfos map[string]domain.PurchaseInfo, now time.Time) domain.BranchSnapshot {
	estimateRows := make([]domain.EstimateRow, 0, len(branch.Symbols))
	referenceCount := 0
	realtimeCount := 0
	staleCount := 0
	unsupportedCount := 0
	demoCount := 0
	warnings := []string{}
	for _, symbol := range branch.Symbols {
		quote, ok := quotes[symbol]
		if !ok {
			quote = missingQuote(symbol, now)
			warnings = append(warnings, fmt.Sprintf("%s missing real quote", symbol))
		}
		referenceCount++
		if quote.Source == "demo" || quote.Error == "demo fallback" {
			demoCount++
		}
		switch quote.RealtimeStatus {
		case "realtime":
			realtimeCount++
		case "unsupported":
			unsupportedCount++
		default:
			staleCount++
		}
		row := s.engine.Estimate(quote, quotes, valuationData, now)
		applyPurchaseInfoToEstimateRow(&row, purchaseInfos)
		estimateRows = append(estimateRows, row)
	}
	return domain.BranchSnapshot{
		SnapshotKey:       "branch:" + branch.Key + ":cn",
		SchemaVersion:     "v1",
		AsOf:              now,
		TTLSeconds:        5,
		StaleAfterSeconds: branch.StaleAfter,
		Branch:            branch,
		EstimateRows:      estimateRows,
		ReferenceCount:    referenceCount,
		RealtimeCount:     realtimeCount,
		StaleCount:        staleCount,
		UnsupportedCount:  unsupportedCount,
		DemoCount:         demoCount,
		Warnings:          warnings,
	}
}

func applyPurchaseInfoToEstimateRow(row *domain.EstimateRow, infos map[string]domain.PurchaseInfo) {
	if row == nil || len(infos) == 0 {
		return
	}
	info, ok := infos[strings.ToUpper(strings.TrimSpace(row.Symbol))]
	if !ok {
		return
	}
	row.PurchaseStatus = info.Status
	if info.DailyLimitYuan == nil {
		row.PurchaseLimit = nil
		return
	}
	value := *info.DailyLimitYuan
	row.PurchaseLimit = &value
}

func missingQuote(symbol string, now time.Time) domain.Quote {
	return domain.Quote{
		Symbol:         symbol,
		Name:           symbol,
		QuoteDate:      now.Format("2006-01-02"),
		QuoteTime:      now.Format("15:04:05"),
		Source:         "missing",
		SourceSymbol:   sina.ToSinaSymbol(symbol),
		QuoteSession:   "missing",
		IsRealtime:     false,
		RealtimeStatus: "unsupported",
		StaleReason:    "no real quote available",
		FetchedAt:      now,
		Error:          "no real quote available",
	}
}

func shouldPreferStoredQuote(now time.Time, fetched domain.Quote, stored domain.Quote) bool {
	if stored.Price <= 0 || fetched.Price <= 0 {
		return false
	}
	if strings.TrimSpace(stored.QuoteDate) != now.Format("2006-01-02") {
		return false
	}
	if !strings.EqualFold(strings.TrimSpace(fetched.Source), "sina_us_after_hours") {
		return false
	}
	source := strings.ToLower(strings.TrimSpace(stored.Source))
	session := strings.ToLower(strings.TrimSpace(stored.QuoteSession))
	if strings.Contains(session, "us_overnight_live") ||
		strings.HasPrefix(source, "us_overnight_live:") ||
		strings.EqualFold(source, "us_live") {
		return true
	}
	return false
}

func mergeSymbols(groups ...[]string) []string {
	seen := make(map[string]bool)
	var out []string
	for _, group := range groups {
		for _, symbol := range group {
			if symbol == "" || seen[symbol] {
				continue
			}
			seen[symbol] = true
			out = append(out, symbol)
		}
	}
	return out
}

func defaultTime(value time.Time, fallback time.Time) time.Time {
	if value.IsZero() {
		return fallback
	}
	return value
}

func round6(value float64) float64 {
	return math.Round(value*1_000_000) / 1_000_000
}

func (s *Service) writeSnapshotFiles(snapshot domain.BranchSnapshot) error {
	if err := os.MkdirAll(s.snapshotDataDir, 0o755); err != nil {
		return err
	}
	if err := os.MkdirAll(s.snapshotHTMLDir, 0o755); err != nil {
		return err
	}

	data, err := json.MarshalIndent(domain.NewBranchListSnapshot(snapshot), "", "  ")
	if err != nil {
		return err
	}
	dataPath := filepath.Join(s.snapshotDataDir, snapshot.Branch.Key+".cn.json")
	if err := atomicWrite(dataPath, data); err != nil {
		return err
	}

	html := RenderBranchHTML(snapshot)
	htmlPath := filepath.Join(s.snapshotHTMLDir, snapshot.Branch.Key+"cn.html")
	return atomicWrite(htmlPath, []byte(html))
}

func atomicWrite(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func (s *Service) loadUploadedQuoteCache() (map[string]domain.Quote, error) {
	if s == nil || strings.TrimSpace(s.uploadedQuoteCache) == "" {
		return nil, nil
	}
	data, err := os.ReadFile(s.uploadedQuoteCache)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var payload uploadedQuoteCacheFile
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, err
	}
	out := make(map[string]domain.Quote, len(payload.Quotes))
	for _, quote := range payload.Quotes {
		symbol := strings.ToUpper(strings.TrimSpace(quote.Symbol))
		if symbol == "" || quote.Price <= 0 {
			continue
		}
		quote.Symbol = symbol
		out[symbol] = quote
	}
	return out, nil
}

func (s *Service) persistUploadedQuoteCache(quotes map[string]domain.Quote) error {
	if s == nil || strings.TrimSpace(s.uploadedQuoteCache) == "" {
		return nil
	}
	symbols := make([]string, 0, len(quotes))
	for symbol, quote := range quotes {
		if strings.TrimSpace(symbol) == "" || quote.Price <= 0 {
			continue
		}
		symbols = append(symbols, symbol)
	}
	sort.Strings(symbols)
	payload := uploadedQuoteCacheFile{
		StoredAt: time.Now(),
		Quotes:   make([]domain.Quote, 0, len(symbols)),
	}
	for _, symbol := range symbols {
		payload.Quotes = append(payload.Quotes, quotes[symbol])
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(s.uploadedQuoteCache), 0o755); err != nil {
		return err
	}
	return atomicWrite(s.uploadedQuoteCache, data)
}

func (s *Service) loadPurchaseInfoCache() (map[string]domain.PurchaseInfo, error) {
	if s == nil || strings.TrimSpace(s.purchaseInfoCache) == "" {
		return nil, nil
	}
	data, err := os.ReadFile(s.purchaseInfoCache)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var payload purchaseInfoCacheFile
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, err
	}
	out := make(map[string]domain.PurchaseInfo, len(payload.Infos))
	for _, info := range payload.Infos {
		symbol := strings.ToUpper(strings.TrimSpace(info.Symbol))
		if symbol == "" {
			continue
		}
		info.Symbol = symbol
		out[symbol] = info
	}
	return out, nil
}

func (s *Service) persistPurchaseInfoCache(infos map[string]domain.PurchaseInfo) error {
	if s == nil || strings.TrimSpace(s.purchaseInfoCache) == "" {
		return nil
	}
	symbols := make([]string, 0, len(infos))
	for symbol := range infos {
		if strings.TrimSpace(symbol) == "" {
			continue
		}
		symbols = append(symbols, symbol)
	}
	sort.Strings(symbols)
	payload := purchaseInfoCacheFile{
		StoredAt: time.Now(),
		Infos:    make([]domain.PurchaseInfo, 0, len(symbols)),
	}
	for _, symbol := range symbols {
		payload.Infos = append(payload.Infos, infos[symbol])
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(s.purchaseInfoCache), 0o755); err != nil {
		return err
	}
	return atomicWrite(s.purchaseInfoCache, data)
}

func (s *Service) applyPurchaseInfosToBranchesLocked() {
	if s == nil || len(s.purchaseInfos) == 0 || len(s.branches) == 0 {
		return
	}
	for key, snapshot := range s.branches {
		for idx := range snapshot.EstimateRows {
			applyPurchaseInfoToEstimateRow(&snapshot.EstimateRows[idx], s.purchaseInfos)
		}
		s.branches[key] = snapshot
	}
}

func clonePurchaseInfos(infos map[string]domain.PurchaseInfo) map[string]domain.PurchaseInfo {
	out := make(map[string]domain.PurchaseInfo, len(infos))
	for symbol, info := range infos {
		out[symbol] = info
	}
	return out
}

func normalizePurchaseInfos(infos map[string]domain.PurchaseInfo) map[string]domain.PurchaseInfo {
	out := make(map[string]domain.PurchaseInfo, len(infos))
	for key, info := range infos {
		symbol := strings.ToUpper(strings.TrimSpace(info.Symbol))
		if symbol == "" {
			symbol = strings.ToUpper(strings.TrimSpace(key))
		}
		if symbol == "" {
			continue
		}
		info.Symbol = symbol
		out[symbol] = info
	}
	return out
}
