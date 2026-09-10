package ratiofit

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

const (
	defaultWindowSize         = 5
	defaultBaseLagTradingDays = 2
	defaultLookbackDays       = 45
	defaultCandidateMin       = 0.0
	defaultCandidateMax       = 1.2
	defaultCandidateStep      = 0.0025
	maxCarryForwardDays       = 7
)

type Repository interface {
	LoadValuationData(ctx context.Context) (domain.ValuationData, error)
	UpsertEffectiveRatioFitHistory(ctx context.Context, rows []domain.EffectiveRatioFitHistoryRow) error
}

type Options struct {
	Repository         Repository
	ClosePremiumLookup ClosePremiumLookup
	Logger             *slog.Logger
	WindowSize         int
	BaseLagTradingDays int
	LookbackDays       int
	CandidateMin       float64
	CandidateMax       float64
	CandidateStep      float64
}

type Service struct {
	repository         Repository
	closePremiumLookup ClosePremiumLookup
	logger             *slog.Logger
	windowSize         int
	baseLagTradingDays int
	lookbackDays       int
	candidates         []float64
}

func NewService(opts Options) *Service {
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	windowSize := opts.WindowSize
	if windowSize <= 0 {
		windowSize = defaultWindowSize
	}
	baseLag := opts.BaseLagTradingDays
	if baseLag <= 0 {
		baseLag = defaultBaseLagTradingDays
	}
	lookback := opts.LookbackDays
	if lookback <= 0 {
		lookback = defaultLookbackDays
	}
	candidateMin := opts.CandidateMin
	candidateMax := opts.CandidateMax
	candidateStep := opts.CandidateStep
	if candidateMax <= candidateMin {
		candidateMin = defaultCandidateMin
		candidateMax = defaultCandidateMax
	}
	if candidateStep <= 0 {
		candidateStep = defaultCandidateStep
	}
	return &Service{
		repository:         opts.Repository,
		closePremiumLookup: opts.ClosePremiumLookup,
		logger:             logger,
		windowSize:         windowSize,
		baseLagTradingDays: baseLag,
		lookbackDays:       lookback,
		candidates:         generateCandidates(candidateMin, candidateMax, candidateStep),
	}
}

func (s *Service) SyncMissing(ctx context.Context, today time.Time) error {
	if s.repository == nil {
		return errors.New("effective ratio fit sync requires repository")
	}
	data, err := s.repository.LoadValuationData(ctx)
	if err != nil {
		return err
	}
	domain.ApplyStaticValuationData(&data)
	rows := BuildRows(data, BuildOptions{
		Today:              today,
		WindowSize:         s.windowSize,
		BaseLagTradingDays: s.baseLagTradingDays,
		LookbackDays:       s.lookbackDays,
		Candidates:         s.candidates,
	})
	if err := ApplyClosePremium(rows, s.closePremiumLookup); err != nil {
		return err
	}
	if len(rows) == 0 {
		s.logger.Info("effective ratio fit sync skipped; no supported rows")
		return nil
	}
	if err := s.repository.UpsertEffectiveRatioFitHistory(ctx, rows); err != nil {
		return err
	}
	okRows := 0
	for _, row := range rows {
		if row.Status == "ok" {
			okRows++
		}
	}
	s.logger.Info(
		"effective ratio fit sync finished",
		"rows", len(rows),
		"ok_rows", okRows,
		"window_size", s.windowSize,
		"lookback_days", s.lookbackDays,
	)
	return nil
}

type BuildOptions struct {
	Today              time.Time
	WindowSize         int
	BaseLagTradingDays int
	LookbackDays       int
	Candidates         []float64
}

type observation struct {
	symbol                   string
	targetDate               string
	baseDate                 string
	baseNAV                  float64
	officialNAV              float64
	configuredEffectiveRatio float64
	modelVersion             string
	referenceSymbol          string
	assetMultiplier          float64
	status                   string
	note                     string
}

func BuildRows(data domain.ValuationData, opts BuildOptions) []domain.EffectiveRatioFitHistoryRow {
	windowSize := opts.WindowSize
	if windowSize <= 0 {
		windowSize = defaultWindowSize
	}
	baseLag := opts.BaseLagTradingDays
	if baseLag <= 0 {
		baseLag = defaultBaseLagTradingDays
	}
	lookbackDays := opts.LookbackDays
	if lookbackDays <= 0 {
		lookbackDays = defaultLookbackDays
	}
	candidates := opts.Candidates
	if len(candidates) == 0 {
		candidates = generateCandidates(defaultCandidateMin, defaultCandidateMax, defaultCandidateStep)
	}
	startDate := opts.Today.AddDate(0, 0, -lookbackDays).Format("2006-01-02")
	funds := supportedFundsForData(data)
	rows := make([]domain.EffectiveRatioFitHistoryRow, 0, len(funds)*lookbackDays)

	for _, fund := range funds {
		sortedDates, navByDate := sortedOfficialNAVs(data, fund.Symbol)
		if len(sortedDates) == 0 {
			continue
		}
		observations := make([]observation, 0, len(sortedDates))
		for _, targetDate := range sortedDates {
			if targetDate < startDate {
				continue
			}
			officialNAV := navByDate[targetDate]
			obs := observation{
				symbol:                   fund.Symbol,
				targetDate:               targetDate,
				officialNAV:              officialNAV,
				configuredEffectiveRatio: fund.ConfiguredEffectiveRatio,
				modelVersion:             fund.ModelVersion,
				referenceSymbol:          fund.ReferenceSymbol,
			}
			baseDate, ok := baseDateFromTradingLag(sortedDates, targetDate, baseLag)
			if !ok {
				obs.status = "missing_base_nav"
				obs.note = "missing base NAV 2 trading days before target date"
				observations = append(observations, obs)
				continue
			}
			obs.baseDate = baseDate
			obs.baseNAV = navByDate[baseDate]
			if obs.baseNAV <= 0 || obs.officialNAV <= 0 {
				obs.status = "missing_base_nav"
				obs.note = "base NAV or official NAV is missing"
				observations = append(observations, obs)
				continue
			}
			multiplier, note, err := fund.AssetMultiplier(data, targetDate, baseDate)
			if err != nil {
				obs.status = "missing_context"
				obs.note = err.Error()
				observations = append(observations, obs)
				continue
			}
			obs.assetMultiplier = multiplier
			obs.status = "ok"
			obs.note = normalizeObservationNote(note)
			observations = append(observations, obs)
		}

		for index, obs := range observations {
			row := domain.EffectiveRatioFitHistoryRow{
				Symbol:                   obs.symbol,
				TargetDate:               obs.targetDate,
				BaseDate:                 obs.baseDate,
				WindowSize:               windowSize,
				BaseLagTradingDays:       baseLag,
				ModelVersion:             obs.modelVersion,
				ReferenceSymbol:          obs.referenceSymbol,
				ConfiguredEffectiveRatio: round6(obs.configuredEffectiveRatio),
				ConfiguredStaticRatio:    round6(1 - obs.configuredEffectiveRatio),
				OfficialNAV:              round6(obs.officialNAV),
				BaseNAV:                  round6(obs.baseNAV),
				Status:                   obs.status,
				Note:                     obs.note,
			}
			if obs.status == "ok" {
				t2Estimate, errValue, errPct := applyRatio(obs, obs.configuredEffectiveRatio)
				row.T2EstimatedNAV = ptrFloat(round6(t2Estimate))
				row.NAVError = ptrFloat(round6(errValue))
				row.NAVErrorPct = ptrFloat(round6(errPct))
			}
			window := observations[maxInt(0, index-windowSize+1) : index+1]
			if len(window) > 0 {
				row.WindowStartDate = window[0].targetDate
				row.WindowEndDate = window[len(window)-1].targetDate
			}
			okWindow := make([]observation, 0, len(window))
			for _, item := range window {
				if item.status == "ok" && item.assetMultiplier > 0 && item.baseNAV > 0 && item.officialNAV > 0 {
					okWindow = append(okWindow, item)
				}
			}
			row.OKDays = len(okWindow)
			switch {
			case len(window) < windowSize:
				row.Status = "insufficient_window"
				row.Note = composeRowNote("window has fewer than 5 official NAV observations", obs.note)
			case obs.status != "ok":
				row.Status = obs.status
				row.Note = obs.note
			case len(okWindow) < windowSize:
				row.Status = "missing_window_context"
				row.Note = composeRowNote("window needs 5 complete observations", obs.note)
			default:
				fittedRatio, windowMAPE, windowMAE, ok := fitRatio(okWindow, candidates)
				if !ok {
					row.Status = "fit_failed"
					row.Note = composeRowNote("no candidate ratio produced a complete fit window", obs.note)
				} else {
					fittedStatic := 1 - fittedRatio
					row.FittedEffectiveRatio = ptrFloat(round6(fittedRatio))
					row.FittedStaticRatio = ptrFloat(round6(fittedStatic))
					row.WindowMAPEPct = ptrFloat(round6(windowMAPE))
					row.WindowMAEAbs = ptrFloat(round6(windowMAE))
					row.Status = "ok"
					row.Note = composeRowNote("rolling 5-observation ratio-only fit", obs.note)
				}
			}
			rows = append(rows, row)
		}
	}

	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Symbol == rows[j].Symbol {
			return rows[i].TargetDate < rows[j].TargetDate
		}
		return rows[i].Symbol < rows[j].Symbol
	})
	return rows
}

type supportedFund struct {
	Symbol                   string
	ModelVersion             string
	ReferenceSymbol          string
	ConfiguredEffectiveRatio float64
	AssetMultiplier          func(data domain.ValuationData, targetDate string, baseDate string) (float64, string, error)
}

func supportedFunds() []supportedFund {
	out := make([]supportedFund, 0, len(domain.WeightedAnchorStrategies())+len(domain.CommodityBasketStrategies()))
	for _, strategy := range domain.WeightedAnchorStrategies() {
		strategy := strategy
		out = append(out, supportedFund{
			Symbol:                   strategy.FundSymbol,
			ModelVersion:             "v1.weighted_anchor." + strings.ToLower(strategy.Label),
			ReferenceSymbol:          strategy.ReferenceSymbol,
			ConfiguredEffectiveRatio: strategy.InvestmentRatio,
			AssetMultiplier: func(data domain.ValuationData, targetDate string, baseDate string) (float64, string, error) {
				return weightedAnchorMultiplier(data, strategy, targetDate, baseDate)
			},
		})
	}
	for _, strategy := range domain.CommodityBasketStrategies() {
		strategy := strategy
		out = append(out, supportedFund{
			Symbol:                   strategy.FundSymbol,
			ModelVersion:             "v1.commodity_basket.cn",
			ReferenceSymbol:          "basket",
			ConfiguredEffectiveRatio: strategy.InvestmentRatio(),
			AssetMultiplier: func(data domain.ValuationData, targetDate string, baseDate string) (float64, string, error) {
				return commodityBasketMultiplier(data, strategy, targetDate, baseDate)
			},
		})
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Symbol < out[j].Symbol
	})
	return out
}

func supportedFundsForData(data domain.ValuationData) []supportedFund {
	out := supportedFunds()
	seen := make(map[string]bool, len(out))
	for _, fund := range out {
		seen[fund.Symbol] = true
	}
	for symbol, holdingDate := range data.CurrentHoldingDates {
		if seen[symbol] || strings.TrimSpace(holdingDate.Date) == "" {
			continue
		}
		if !supportsHoldingsRatioFit(data, symbol, holdingDate.Date) {
			continue
		}
		symbol := symbol
		out = append(out, supportedFund{
			Symbol:                   symbol,
			ModelVersion:             "v1.holdings.cn",
			ReferenceSymbol:          "holdings",
			ConfiguredEffectiveRatio: positionOf(data, symbol),
			AssetMultiplier: func(data domain.ValuationData, targetDate string, baseDate string) (float64, string, error) {
				return holdingsMultiplier(data, symbol, targetDate, baseDate)
			},
		})
	}
	for symbol, pairs := range data.FundPairs {
		if seen[symbol] || len(pairs) == 0 {
			continue
		}
		pair, ok := primaryFundPair(pairs)
		if !ok {
			continue
		}
		fundSymbol := symbol
		fundPair := pair
		out = append(out, supportedFund{
			Symbol:                   fundSymbol,
			ModelVersion:             "v1.fundpair",
			ReferenceSymbol:          fundPair.PairSymbol,
			ConfiguredEffectiveRatio: positionOf(data, fundSymbol),
			AssetMultiplier: func(data domain.ValuationData, targetDate string, baseDate string) (float64, string, error) {
				return fundPairMultiplier(data, fundPair, targetDate, baseDate)
			},
		})
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Symbol < out[j].Symbol
	})
	return out
}

func primaryFundPair(pairs []domain.FundPair) (domain.FundPair, bool) {
	for _, pair := range pairs {
		if strings.TrimSpace(pair.PairSymbol) == "" {
			continue
		}
		return pair, true
	}
	return domain.FundPair{}, false
}

func sortedOfficialNAVs(data domain.ValuationData, symbol string) ([]string, map[string]float64) {
	byDate := data.NetValuesByDate[symbol]
	if len(byDate) == 0 {
		return nil, nil
	}
	dates := make([]string, 0, len(byDate))
	navs := make(map[string]float64, len(byDate))
	for day, item := range byDate {
		if item.NAV <= 0 {
			continue
		}
		dates = append(dates, day)
		navs[day] = item.NAV
	}
	sort.Strings(dates)
	return dates, navs
}

func baseDateFromTradingLag(sortedDates []string, targetDate string, lag int) (string, bool) {
	if lag <= 0 {
		return targetDate, true
	}
	index := sort.SearchStrings(sortedDates, targetDate)
	if index >= len(sortedDates) || sortedDates[index] != targetDate {
		index--
	}
	if index < 0 {
		return "", false
	}
	baseIndex := index - lag
	if baseIndex < 0 || baseIndex >= len(sortedDates) {
		return "", false
	}
	return sortedDates[baseIndex], true
}

func weightedAnchorMultiplier(data domain.ValuationData, strategy domain.WeightedAnchorStrategy, targetDate string, baseDate string) (float64, string, error) {
	targetPrice, targetWarnings, err := weightedAnchorPriceWithCarryForward(data, strategy, targetDate)
	if err != nil {
		return 0, "", err
	}
	basePrice, baseWarnings, err := weightedAnchorPriceWithCarryForward(data, strategy, baseDate)
	if err != nil {
		return 0, "", err
	}
	targetFX, baseFX, err := requireExactFXPair(data, strategy.FXPair, targetDate, baseDate)
	if err != nil {
		return 0, "", err
	}
	warnings := append(targetWarnings, baseWarnings...)
	return (targetPrice / basePrice) * (targetFX / baseFX), strings.Join(warnings, "; "), nil
}

func commodityBasketMultiplier(data domain.ValuationData, strategy domain.CommodityBasketStrategy, targetDate string, baseDate string) (float64, string, error) {
	investmentRatio := strategy.InvestmentRatio()
	if investmentRatio <= 0 {
		return 0, "", errors.New("basket investment ratio is zero")
	}
	sleeveMultiplier := 0.0
	usedWeight := 0.0
	warnings := make([]string, 0)
	for _, leg := range strategy.Legs {
		if leg.Symbol == "" || leg.Weight <= 0 {
			continue
		}
		targetPrice, targetActualDate, ok := dailyPriceWithCarryForward(data, leg.Symbol, targetDate, maxCarryForwardDays)
		if !ok || targetPrice <= 0 {
			return 0, "", errors.New(leg.Symbol + " missing daily price for " + targetDate)
		}
		basePrice, baseActualDate, ok := dailyPriceWithCarryForward(data, leg.Symbol, baseDate, maxCarryForwardDays)
		if !ok || basePrice <= 0 {
			return 0, "", errors.New(leg.Symbol + " missing daily price for " + baseDate)
		}
		targetFX, baseFX, err := requireExactFXPair(data, leg.FXPair, targetDate, baseDate)
		if err != nil {
			return 0, "", err
		}
		weightInSleeve := leg.Weight / investmentRatio
		sleeveMultiplier += weightInSleeve * (targetPrice / basePrice) * (targetFX / baseFX)
		usedWeight += weightInSleeve
		if targetActualDate != targetDate {
			warnings = append(warnings, fmt.Sprintf("%s uses %s for %s", leg.Symbol, targetActualDate, targetDate))
		}
		if baseActualDate != baseDate {
			warnings = append(warnings, fmt.Sprintf("%s uses %s for %s", leg.Symbol, baseActualDate, baseDate))
		}
	}
	if usedWeight+1e-9 < 1 {
		return 0, "", errors.New("basket leg coverage is incomplete")
	}
	return sleeveMultiplier, strings.Join(warnings, "; "), nil
}

func holdingsMultiplier(data domain.ValuationData, fundSymbol string, targetDate string, baseDate string) (float64, string, error) {
	holdingDate, ok := data.CurrentHoldingDates[fundSymbol]
	if !ok || strings.TrimSpace(holdingDate.Date) == "" {
		return 0, "", errors.New(fundSymbol + " missing current holding date")
	}
	holdings := data.Holdings[fundSymbol]
	if len(holdings) == 0 {
		return 0, "", errors.New(fundSymbol + " missing holdings")
	}
	totalRatio := 0.0
	weightedMultiplier := 0.0
	used := 0
	warnings := make([]string, 0)
	for _, holding := range holdings {
		if holding.HoldingDate != holdingDate.Date || holding.Ratio <= 0 {
			continue
		}
		targetPrice, targetActualDate, ok := dailyPriceWithCarryForward(data, holding.HoldingSymbol, targetDate, maxCarryForwardDays)
		if !ok || targetPrice <= 0 {
			return 0, "", errors.New(holding.HoldingSymbol + " missing daily price for " + targetDate)
		}
		basePrice, baseActualDate, ok := dailyPriceWithCarryForward(data, holding.HoldingSymbol, baseDate, maxCarryForwardDays)
		if !ok || basePrice <= 0 {
			return 0, "", errors.New(holding.HoldingSymbol + " missing daily price for " + baseDate)
		}
		multiplier := targetPrice / basePrice
		if pair := holdingFXPair(holding); pair != "" {
			targetFX, baseFX, err := requireExactFXPair(data, pair, targetDate, baseDate)
			if err != nil {
				return 0, "", err
			}
			multiplier *= targetFX / baseFX
		}
		weightedMultiplier += holding.Ratio * multiplier
		totalRatio += holding.Ratio
		used++
		if targetActualDate != targetDate {
			warnings = append(warnings, fmt.Sprintf("%s uses %s for %s", holding.HoldingSymbol, targetActualDate, targetDate))
		}
		if baseActualDate != baseDate {
			warnings = append(warnings, fmt.Sprintf("%s uses %s for %s", holding.HoldingSymbol, baseActualDate, baseDate))
		}
	}
	if used == 0 || totalRatio <= 0 {
		return 0, "", errors.New(fundSymbol + " has no usable current holdings")
	}
	return weightedMultiplier / totalRatio, strings.Join(warnings, "; "), nil
}

func fundPairMultiplier(data domain.ValuationData, pair domain.FundPair, targetDate string, baseDate string) (float64, string, error) {
	targetPrice, targetActualDate, ok := dailyPriceWithCarryForward(data, pair.PairSymbol, targetDate, maxCarryForwardDays)
	if !ok || targetPrice <= 0 {
		return 0, "", errors.New(pair.PairSymbol + " missing daily price for " + targetDate)
	}
	basePrice, baseActualDate, ok := dailyPriceWithCarryForward(data, pair.PairSymbol, baseDate, maxCarryForwardDays)
	if !ok || basePrice <= 0 {
		return 0, "", errors.New(pair.PairSymbol + " missing daily price for " + baseDate)
	}
	warnings := make([]string, 0, 2)
	if targetActualDate != targetDate {
		warnings = append(warnings, fmt.Sprintf("%s uses %s for %s", pair.PairSymbol, targetActualDate, targetDate))
	}
	if baseActualDate != baseDate {
		warnings = append(warnings, fmt.Sprintf("%s uses %s for %s", pair.PairSymbol, baseActualDate, baseDate))
	}
	return targetPrice / basePrice, strings.Join(warnings, "; "), nil
}

func supportsHoldingsRatioFit(data domain.ValuationData, fundSymbol string, holdingDate string) bool {
	for _, holding := range data.Holdings[fundSymbol] {
		if holding.HoldingDate != holdingDate || holding.Ratio <= 0 {
			continue
		}
		if holdingFXPair(holding) != "" {
			return true
		}
	}
	return false
}

func holdingFXPair(holding domain.Holding) string {
	currency := strings.ToUpper(strings.TrimSpace(holding.Currency))
	if currency == "" {
		currency = inferredHoldingCurrency(holding.HoldingSymbol)
	}
	switch currency {
	case "", "CNY":
		return ""
	case "HKD":
		return "HKDCNY"
	case "USD":
		return "USDCNY"
	default:
		return ""
	}
}

func inferredHoldingCurrency(symbol string) string {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	switch {
	case isHKSymbol(symbol):
		return "HKD"
	case strings.HasPrefix(symbol, "SH"), strings.HasPrefix(symbol, "SZ"), strings.HasPrefix(symbol, "BJ"):
		return "CNY"
	default:
		return "USD"
	}
}

func isHKSymbol(symbol string) bool {
	if len(symbol) != 5 {
		return false
	}
	for _, ch := range symbol {
		if ch < '0' || ch > '9' {
			return false
		}
	}
	return true
}

func positionOf(data domain.ValuationData, symbol string) float64 {
	if strategy, ok := domain.WeightedAnchorStrategyForFund(symbol); ok && strategy.InvestmentRatio > 0 {
		return strategy.InvestmentRatio
	}
	if strategy, ok := domain.CommodityBasketStrategyForFund(symbol); ok && strategy.InvestmentRatio() > 0 {
		return strategy.InvestmentRatio()
	}
	if value, ok := data.Positions[symbol]; ok && value > 0 {
		return value
	}
	return 1
}

func requireAnchorSet(data domain.ValuationData, strategy domain.WeightedAnchorStrategy, targetDate string) (domain.ValuationAnchorPriceSet, error) {
	key := domain.ValuationAnchorSetKey(strategy.FundSymbol, targetDate, strategy.ReferenceSymbol)
	set, ok := data.ValuationAnchors[key]
	if !ok || set.WeightedPrice <= 0 {
		return domain.ValuationAnchorPriceSet{}, errors.New(strategy.ReferenceSymbol + " missing weighted anchor for " + targetDate)
	}
	if set.CoverageWeight+1e-9 < set.RequiredWeight {
		return domain.ValuationAnchorPriceSet{}, errors.New(strategy.ReferenceSymbol + " anchor coverage incomplete for " + targetDate)
	}
	return set, nil
}

func dailyPriceExact(data domain.ValuationData, symbol string, targetDate string) (float64, bool) {
	byDate := data.DailyPricesByDate[symbol]
	if byDate == nil {
		return 0, false
	}
	price, ok := byDate[targetDate]
	if !ok {
		return 0, false
	}
	if price.AdjClose > 0 {
		return price.AdjClose, true
	}
	return price.Close, price.Close > 0
}

func dailyPriceWithCarryForward(data domain.ValuationData, symbol string, targetDate string, maxDays int) (float64, string, bool) {
	if price, ok := dailyPriceExact(data, symbol, targetDate); ok && price > 0 {
		return price, targetDate, true
	}
	day, err := time.Parse("2006-01-02", targetDate)
	if err != nil {
		return 0, "", false
	}
	for offset := 1; offset <= maxDays; offset++ {
		candidate := day.AddDate(0, 0, -offset).Format("2006-01-02")
		if price, ok := dailyPriceExact(data, symbol, candidate); ok && price > 0 {
			return price, candidate, true
		}
	}
	return 0, "", false
}

func weightedAnchorPriceWithCarryForward(data domain.ValuationData, strategy domain.WeightedAnchorStrategy, anchorDate string) (float64, []string, error) {
	weighted := 0.0
	coverage := 0.0
	warnings := make([]string, 0)
	for _, point := range strategy.Points {
		price, actualDate, ok := anchorPointPriceWithCarryForward(data, strategy.FundSymbol, strategy.ReferenceSymbol, point.Key, anchorDate, maxCarryForwardDays)
		if !ok || price <= 0 {
			return 0, nil, errors.New(strategy.ReferenceSymbol + " missing " + point.Key + " anchor for " + anchorDate)
		}
		weighted += price * point.Weight
		coverage += point.Weight
		if actualDate != anchorDate {
			warnings = append(warnings, fmt.Sprintf("%s %s uses %s for %s", strategy.ReferenceSymbol, point.Key, actualDate, anchorDate))
		}
	}
	if coverage <= 0 {
		return 0, nil, errors.New(strategy.ReferenceSymbol + " anchor coverage incomplete for " + anchorDate)
	}
	return weighted / coverage, warnings, nil
}

func anchorPointPriceWithCarryForward(data domain.ValuationData, fundSymbol string, referenceSymbol string, anchorKey string, anchorDate string, maxDays int) (float64, string, bool) {
	if price, ok := anchorPointPriceExact(data, fundSymbol, referenceSymbol, anchorKey, anchorDate); ok && price > 0 {
		return price, anchorDate, true
	}
	day, err := time.Parse("2006-01-02", anchorDate)
	if err != nil {
		return 0, "", false
	}
	for offset := 1; offset <= maxDays; offset++ {
		candidate := day.AddDate(0, 0, -offset).Format("2006-01-02")
		if price, ok := anchorPointPriceExact(data, fundSymbol, referenceSymbol, anchorKey, candidate); ok && price > 0 {
			return price, candidate, true
		}
	}
	return 0, "", false
}

func anchorPointPriceExact(data domain.ValuationData, fundSymbol string, referenceSymbol string, anchorKey string, anchorDate string) (float64, bool) {
	setKey := domain.ValuationAnchorSetKey(fundSymbol, anchorDate, referenceSymbol)
	set, ok := data.ValuationAnchors[setKey]
	if !ok {
		return 0, false
	}
	for _, point := range set.Points {
		if point.AnchorKey == anchorKey && point.Price > 0 {
			return point.Price, true
		}
	}
	return 0, false
}

func requireExactFXPair(data domain.ValuationData, pair string, targetDate string, baseDate string) (float64, float64, error) {
	pair = strings.ToUpper(strings.TrimSpace(pair))
	if pair == "" {
		return 1, 1, nil
	}
	targetFX, ok := exactCentralParity(data, pair, targetDate)
	if !ok || targetFX <= 0 {
		return 0, 0, errors.New(pair + " missing FX for " + targetDate)
	}
	baseFX, ok := exactCentralParity(data, pair, baseDate)
	if !ok || baseFX <= 0 {
		return 0, 0, errors.New(pair + " missing FX for " + baseDate)
	}
	return targetFX, baseFX, nil
}

func exactCentralParity(data domain.ValuationData, pair string, rateDate string) (float64, bool) {
	byDate := data.FXCentralParity[strings.ToUpper(strings.TrimSpace(pair))]
	if byDate == nil {
		return 0, false
	}
	row, ok := byDate[strings.TrimSpace(rateDate)]
	if !ok {
		return 0, false
	}
	return row.Rate, row.Rate > 0
}

func fitRatio(window []observation, candidates []float64) (float64, float64, float64, bool) {
	bestRatio := 0.0
	bestMAPE := 0.0
	bestMAE := 0.0
	found := false
	for _, candidate := range candidates {
		sumAbsPct := 0.0
		sumAbsErr := 0.0
		complete := true
		for _, item := range window {
			_, errValue, errPct := applyRatio(item, candidate)
			if math.IsNaN(errValue) || math.IsNaN(errPct) {
				complete = false
				break
			}
			sumAbsErr += math.Abs(errValue)
			sumAbsPct += math.Abs(errPct)
		}
		if !complete {
			continue
		}
		mape := sumAbsPct / float64(len(window))
		mae := sumAbsErr / float64(len(window))
		if !found || mape < bestMAPE-1e-12 {
			bestRatio = candidate
			bestMAPE = mape
			bestMAE = mae
			found = true
		}
	}
	return bestRatio, bestMAPE, bestMAE, found
}

func applyRatio(obs observation, ratio float64) (float64, float64, float64) {
	if obs.baseNAV <= 0 || obs.officialNAV <= 0 || obs.assetMultiplier <= 0 {
		return math.NaN(), math.NaN(), math.NaN()
	}
	staticRatio := 1 - ratio
	predicted := obs.baseNAV * (staticRatio + ratio*obs.assetMultiplier)
	errValue := predicted - obs.officialNAV
	errPct := errValue / obs.officialNAV * 100
	return predicted, errValue, errPct
}

func generateCandidates(minValue float64, maxValue float64, step float64) []float64 {
	values := make([]float64, 0, int((maxValue-minValue)/step)+2)
	for current := minValue; current <= maxValue+step/2; current += step {
		values = append(values, math.Round(current*1e10)/1e10)
	}
	return values
}

func ptrFloat(value float64) *float64 {
	out := value
	return &out
}

func normalizeObservationNote(note string) string {
	note = strings.TrimSpace(note)
	if note == "" {
		return "ok"
	}
	return note
}

func composeRowNote(base string, extra string) string {
	base = strings.TrimSpace(base)
	extra = strings.TrimSpace(extra)
	if extra == "" || extra == "ok" {
		return base
	}
	if base == "" {
		return extra
	}
	return base + "; " + extra
}

func round6(value float64) float64 {
	return math.Round(value*1_000_000) / 1_000_000
}

func maxInt(a int, b int) int {
	if a > b {
		return a
	}
	return b
}
