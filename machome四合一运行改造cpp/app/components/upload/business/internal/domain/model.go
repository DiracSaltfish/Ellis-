package domain

import (
	"strings"
	"time"
)

type Branch struct {
	Key        string   `json:"key"`
	NameCN     string   `json:"name_cn"`
	OldPath    string   `json:"old_path"`
	NewPath    string   `json:"new_path"`
	SortOrder  int      `json:"sort_order"`
	Symbols    []string `json:"symbols"`
	StaleAfter int      `json:"stale_after_seconds"`
}

type Quote struct {
	Symbol         string    `json:"symbol"`
	Name           string    `json:"name"`
	Price          float64   `json:"price"`
	PrevClose      float64   `json:"prev_close"`
	Open           float64   `json:"open"`
	High           float64   `json:"high"`
	Low            float64   `json:"low"`
	Volume         float64   `json:"volume"`
	Amount         float64   `json:"amount"`
	ChangePct      float64   `json:"change_pct"`
	LimitUp        float64   `json:"limit_up,omitempty"`
	LimitDown      float64   `json:"limit_down,omitempty"`
	BidLevels      []Level   `json:"bid_levels,omitempty"`
	AskLevels      []Level   `json:"ask_levels,omitempty"`
	QuoteDate      string    `json:"quote_date"`
	QuoteTime      string    `json:"quote_time"`
	Source         string    `json:"source"`
	SourceSymbol   string    `json:"source_symbol,omitempty"`
	QuoteSession   string    `json:"quote_session,omitempty"`
	IsRealtime     bool      `json:"is_realtime"`
	RealtimeStatus string    `json:"realtime_status,omitempty"`
	StaleReason    string    `json:"stale_reason,omitempty"`
	FetchedAt      time.Time `json:"fetched_at"`
	Error          string    `json:"error,omitempty"`
}

type Level struct {
	Level  int     `json:"level"`
	Price  float64 `json:"price"`
	Volume float64 `json:"volume"`
}

type VisitCount struct {
	Date   string `json:"date"`
	Kind   string `json:"kind"`
	Target string `json:"target"`
	Method string `json:"method,omitempty"`
	Count  int64  `json:"count"`
}

type VisitCountResponse struct {
	Days int          `json:"days"`
	Rows []VisitCount `json:"rows"`
}

type EstimateRow struct {
	Symbol          string   `json:"symbol"`
	Name            string   `json:"name"`
	MarketPrice     float64  `json:"market_price"`
	OfficialEst     float64  `json:"official_est"`
	FairEst         float64  `json:"fair_est"`
	RealtimeEst     *float64 `json:"realtime_est"`
	OfficialPremium float64  `json:"official_premium"`
	FairPremium     float64  `json:"fair_premium"`
	RealtimePremium *float64 `json:"realtime_premium"`
	EstimateDate    string   `json:"estimate_date"`
	ModelVersion    string   `json:"model_version"`
	EffectiveRatio  float64  `json:"effective_ratio,omitempty"`
	ReferenceSymbol string   `json:"reference_symbol,omitempty"`
	PurchaseLimit   *float64 `json:"purchase_limit,omitempty"`
	PurchaseStatus  string   `json:"purchase_status,omitempty"`
	Note            string   `json:"note,omitempty"`
}

type PurchaseInfo struct {
	Symbol         string    `json:"symbol"`
	FundCode       string    `json:"fund_code"`
	Status         string    `json:"status"`
	DailyLimitYuan *float64  `json:"daily_limit_yuan,omitempty"`
	RawLimit       string    `json:"raw_limit,omitempty"`
	IsPurchasable  bool      `json:"is_purchasable"`
	Source         string    `json:"source"`
	FetchedAt      time.Time `json:"fetched_at"`
	Error          string    `json:"error,omitempty"`
}

type BranchSnapshot struct {
	SnapshotKey       string        `json:"snapshot_key"`
	SchemaVersion     string        `json:"schema_version"`
	AsOf              time.Time     `json:"as_of"`
	TTLSeconds        int           `json:"ttl_seconds"`
	StaleAfterSeconds int           `json:"stale_after_seconds"`
	Branch            Branch        `json:"branch"`
	EstimateRows      []EstimateRow `json:"estimate_rows"`
	ReferenceCount    int           `json:"-"`
	RealtimeCount     int           `json:"-"`
	StaleCount        int           `json:"-"`
	UnsupportedCount  int           `json:"-"`
	DemoCount         int           `json:"-"`
	Warnings          []string      `json:"warnings"`
}

type HomeSnapshot struct {
	SnapshotKey   string          `json:"snapshot_key"`
	SchemaVersion string          `json:"schema_version"`
	AsOf          time.Time       `json:"as_of"`
	TTLSeconds    int             `json:"ttl_seconds"`
	Branches      []BranchSummary `json:"branches"`
	Warnings      []string        `json:"warnings"`
}

type BranchSummary struct {
	Branch               Branch         `json:"branch"`
	AsOf                 time.Time      `json:"as_of"`
	ReferenceCount       int            `json:"reference_count"`
	EstimateCount        int            `json:"estimate_count"`
	RealtimeCount        int            `json:"realtime_count"`
	StaleCount           int            `json:"stale_count"`
	UnsupportedCount     int            `json:"unsupported_count"`
	DemoCount            int            `json:"demo_count"`
	MaxFairPremium       float64        `json:"max_fair_premium"`
	MaxFairPremiumSymbol string         `json:"max_fair_premium_symbol"`
	MaxAbsFairPremium    float64        `json:"max_abs_fair_premium"`
	AvgAbsFairPremium    float64        `json:"avg_abs_fair_premium"`
	ModelCounts          map[string]int `json:"model_counts"`
	Warnings             []string       `json:"warnings"`
}

type FundSnapshot struct {
	Symbol             string           `json:"symbol"`
	Branches           []Branch         `json:"branches"`
	Quote              Quote            `json:"quote"`
	Estimate           *EstimateRow     `json:"estimate,omitempty"`
	ValuationReference *Quote           `json:"valuation_reference,omitempty"`
	ValuationInputs    []ValuationInput `json:"valuation_inputs,omitempty"`
	AsOf               time.Time        `json:"as_of"`
}

type ValuationInput struct {
	Role            string     `json:"role"`
	Symbol          string     `json:"symbol"`
	Name            string     `json:"name,omitempty"`
	Market          string     `json:"market,omitempty"`
	Timezone        string     `json:"timezone,omitempty"`
	WeightRatio     float64    `json:"weight_ratio,omitempty"`
	Position        float64    `json:"position,omitempty"`
	HoldingDate     string     `json:"holding_date,omitempty"`
	BaseDate        string     `json:"base_date,omitempty"`
	BasePrice       float64    `json:"base_price,omitempty"`
	BaseSource      string     `json:"base_source,omitempty"`
	CurrentPrice    float64    `json:"current_price,omitempty"`
	OfficialPrice   float64    `json:"official_price,omitempty"`
	ChangePct       float64    `json:"change_pct,omitempty"`
	QuoteDate       string     `json:"quote_date,omitempty"`
	QuoteTime       string     `json:"quote_time,omitempty"`
	FetchedAt       *time.Time `json:"fetched_at,omitempty"`
	Source          string     `json:"source,omitempty"`
	SourceSymbol    string     `json:"source_symbol,omitempty"`
	QuoteSession    string     `json:"quote_session,omitempty"`
	RealtimeStatus  string     `json:"realtime_status,omitempty"`
	StaleReason     string     `json:"stale_reason,omitempty"`
	FXAdjust        float64    `json:"fx_adjust,omitempty"`
	Currency        string     `json:"currency,omitempty"`
	ReferenceSymbol string     `json:"reference_symbol,omitempty"`
	TargetAt        *time.Time `json:"target_at,omitempty"`
	ObservedAt      *time.Time `json:"observed_at,omitempty"`
	CaptureStatus   string     `json:"capture_status,omitempty"`
	Used            bool       `json:"used"`
	Note            string     `json:"note,omitempty"`
}

type FundPair struct {
	FundSymbol string `json:"fund_symbol"`
	PairSymbol string `json:"pair_symbol"`
	PairType   string `json:"pair_type"`
	Note       string `json:"note,omitempty"`
}

type Calibration struct {
	Symbol    string  `json:"symbol"`
	Date      string  `json:"date"`
	Factor    float64 `json:"factor"`
	BaseValue float64 `json:"base_value"`
	Source    string  `json:"source"`
}

type NetValue struct {
	Symbol     string  `json:"symbol"`
	Date       string  `json:"date"`
	NAV        float64 `json:"nav"`
	Source     string  `json:"source"`
	Confidence string  `json:"confidence"`
}

type HoldingDate struct {
	FundSymbol string `json:"fund_symbol"`
	Date       string `json:"date"`
	Source     string `json:"source"`
}

type Holding struct {
	FundSymbol    string  `json:"fund_symbol"`
	HoldingDate   string  `json:"holding_date"`
	HoldingSymbol string  `json:"holding_symbol"`
	HoldingName   string  `json:"holding_name,omitempty"`
	Ratio         float64 `json:"ratio"`
	FXAdjust      float64 `json:"fx_adjust"`
	Currency      string  `json:"currency,omitempty"`
	Source        string  `json:"source"`
}

type DailyPrice struct {
	Symbol   string  `json:"symbol"`
	Date     string  `json:"date"`
	Close    float64 `json:"close"`
	AdjClose float64 `json:"adj_close"`
	Source   string  `json:"source"`
}

type ValuationAnchorPrice struct {
	FundSymbol      string    `json:"fund_symbol"`
	AnchorDate      string    `json:"anchor_date"`
	AnchorKey       string    `json:"anchor_key"`
	ReferenceSymbol string    `json:"reference_symbol"`
	TargetAt        time.Time `json:"target_at"`
	TargetTimezone  string    `json:"target_timezone,omitempty"`
	ObservedAt      time.Time `json:"observed_at,omitempty"`
	Price           float64   `json:"price"`
	Weight          float64   `json:"weight"`
	Source          string    `json:"source"`
	CaptureStatus   string    `json:"capture_status,omitempty"`
}

type ValuationAnchorPriceSet struct {
	FundSymbol      string                 `json:"fund_symbol"`
	AnchorDate      string                 `json:"anchor_date"`
	ReferenceSymbol string                 `json:"reference_symbol"`
	WeightedPrice   float64                `json:"weighted_price"`
	CoverageWeight  float64                `json:"coverage_weight"`
	RequiredWeight  float64                `json:"required_weight"`
	Points          []ValuationAnchorPrice `json:"points"`
}

type FXQuote struct {
	Pair string  `json:"pair"`
	Rate float64 `json:"rate"`
}

type FXCentralParity struct {
	Pair   string  `json:"pair"`
	Date   string  `json:"date"`
	Rate   float64 `json:"rate"`
	Source string  `json:"source,omitempty"`
}

type EffectiveRatioFitHistoryRow struct {
	Symbol                    string   `json:"symbol"`
	Name                      string   `json:"name,omitempty"`
	TargetDate                string   `json:"target_date"`
	BaseDate                  string   `json:"base_date,omitempty"`
	WindowStartDate           string   `json:"window_start_date,omitempty"`
	WindowEndDate             string   `json:"window_end_date,omitempty"`
	WindowSize                int      `json:"window_size"`
	BaseLagTradingDays        int      `json:"base_lag_trading_days"`
	OKDays                    int      `json:"ok_days"`
	ModelVersion              string   `json:"model_version"`
	ReferenceSymbol           string   `json:"reference_symbol,omitempty"`
	ConfiguredEffectiveRatio  float64  `json:"configured_effective_ratio"`
	ConfiguredStaticRatio     float64  `json:"configured_static_ratio"`
	FittedEffectiveRatio      *float64 `json:"fitted_effective_ratio,omitempty"`
	FittedStaticRatio         *float64 `json:"fitted_static_ratio,omitempty"`
	OfficialNAV               float64  `json:"official_nav"`
	BaseNAV                   float64  `json:"base_nav"`
	T2EstimatedNAV            *float64 `json:"t2_estimated_nav,omitempty"`
	NAVError                  *float64 `json:"nav_error,omitempty"`
	NAVErrorPct               *float64 `json:"nav_error_pct,omitempty"`
	ClosingRealtimeMinute     string   `json:"closing_realtime_minute,omitempty"`
	ClosingRealtimePremiumPct *float64 `json:"closing_realtime_premium_pct,omitempty"`
	WindowMAPEPct             *float64 `json:"window_mape_pct,omitempty"`
	WindowMAEAbs              *float64 `json:"window_mae_abs,omitempty"`
	Status                    string   `json:"status"`
	Note                      string   `json:"note,omitempty"`
	CreatedAt                 string   `json:"created_at,omitempty"`
	UpdatedAt                 string   `json:"updated_at,omitempty"`
}

type EffectiveRatioFitHistoryResponse struct {
	Symbol             string                        `json:"symbol"`
	Name               string                        `json:"name,omitempty"`
	WindowSize         int                           `json:"window_size"`
	BaseLagTradingDays int                           `json:"base_lag_trading_days"`
	Rows               []EffectiveRatioFitHistoryRow `json:"rows"`
}

type ManualValuationPositionOverride struct {
	Symbol    string    `json:"symbol"`
	Ratio     float64   `json:"ratio"`
	Source    string    `json:"source,omitempty"`
	UpdatedBy string    `json:"updated_by,omitempty"`
	UpdatedAt time.Time `json:"updated_at,omitempty"`
}

type ShareHistoryRecord struct {
	Symbol            string   `json:"symbol"`
	Name              string   `json:"name,omitempty"`
	FundType          string   `json:"-"`
	ShareDate         string   `json:"share_date"`
	Shares10K         float64  `json:"shares_10k"`
	PreviousShares10K *float64 `json:"previous_shares_10k,omitempty"`
	ShareChange10K    *float64 `json:"share_change_10k,omitempty"`
	ShareChangePct    *float64 `json:"share_change_pct,omitempty"`
	ClosePremiumPct   *float64 `json:"-"`
	Source            string   `json:"-"`
	CreatedAt         string   `json:"created_at,omitempty"`
	UpdatedAt         string   `json:"updated_at,omitempty"`
}

type ShareHistoryResponse struct {
	Symbol   string               `json:"symbol"`
	Name     string               `json:"name,omitempty"`
	FundType string               `json:"-"`
	Unit     string               `json:"unit"`
	Rows     []ShareHistoryRecord `json:"rows"`
}

type YesterdayRedemptionBoardRow struct {
	Symbol            string   `json:"symbol"`
	Name              string   `json:"name,omitempty"`
	ShareDate         string   `json:"share_date"`
	Shares10K         float64  `json:"shares_10k"`
	PreviousShares10K *float64 `json:"previous_shares_10k,omitempty"`
	ShareChange10K    *float64 `json:"share_change_10k,omitempty"`
	ShareChangePct    *float64 `json:"share_change_pct,omitempty"`
	ClosePremiumPct   *float64 `json:"close_premium_pct,omitempty"`
	BranchNames       []string `json:"branch_names,omitempty"`
}

type YesterdayRedemptionBoardResponse struct {
	ShareDate         string                        `json:"share_date"`
	Unit              string                        `json:"unit"`
	TrackedSymbols    int                           `json:"tracked_symbols"`
	IncludedSymbols   int                           `json:"included_symbols"`
	StaleSymbols      int                           `json:"stale_symbols"`
	MissingChangeRows int                           `json:"missing_change_rows"`
	RedemptionCount   int                           `json:"redemption_count"`
	SubscriptionCount int                           `json:"subscription_count"`
	FlatCount         int                           `json:"flat_count"`
	Rows              []YesterdayRedemptionBoardRow `json:"rows"`
}

type ValuationData struct {
	FundPairs               map[string][]FundPair
	Positions               map[string]float64
	ManualPositionOverrides map[string]ManualValuationPositionOverride
	LatestCalibrations      map[string]Calibration
	LatestNetValues         map[string]NetValue
	NetValuesByDate         map[string]map[string]NetValue
	CurrentHoldingDates     map[string]HoldingDate
	Holdings                map[string][]Holding
	DailyPricesByDate       map[string]map[string]DailyPrice
	ValuationAnchors        map[string]ValuationAnchorPriceSet
	FXQuotes                map[string]FXQuote
	FXCentralParity         map[string]map[string]FXCentralParity
}

func EmptyValuationData() ValuationData {
	return ValuationData{
		FundPairs:               map[string][]FundPair{},
		Positions:               map[string]float64{},
		ManualPositionOverrides: map[string]ManualValuationPositionOverride{},
		LatestCalibrations:      map[string]Calibration{},
		LatestNetValues:         map[string]NetValue{},
		NetValuesByDate:         map[string]map[string]NetValue{},
		CurrentHoldingDates:     map[string]HoldingDate{},
		Holdings:                map[string][]Holding{},
		DailyPricesByDate:       map[string]map[string]DailyPrice{},
		ValuationAnchors:        map[string]ValuationAnchorPriceSet{},
		FXQuotes:                map[string]FXQuote{},
		FXCentralParity:         map[string]map[string]FXCentralParity{},
	}
}

func (data ValuationData) RelatedSymbols() []string {
	return data.relatedSymbolsForFunds(nil)
}

func (data ValuationData) RelatedSymbolsForFunds(funds []string) []string {
	allowed := make(map[string]bool, len(funds))
	for _, fund := range funds {
		if fund != "" {
			allowed[fund] = true
		}
	}
	return data.relatedSymbolsForFunds(allowed)
}

func (data ValuationData) relatedSymbolsForFunds(allowed map[string]bool) []string {
	seen := make(map[string]bool)
	var symbols []string
	add := func(symbol string) {
		if symbol == "" || IsExcludedSymbol(symbol) || seen[symbol] {
			return
		}
		seen[symbol] = true
		symbols = append(symbols, symbol)
	}
	allowFund := func(fund string) bool {
		return allowed == nil || allowed[fund]
	}
	for fund, pairs := range data.FundPairs {
		if IsExcludedSymbol(fund) || !allowFund(fund) {
			continue
		}
		add(fund)
		for _, pair := range pairs {
			add(pair.PairSymbol)
		}
	}
	for _, strategy := range WeightedAnchorStrategies() {
		if IsExcludedSymbol(strategy.FundSymbol) || !allowFund(strategy.FundSymbol) {
			continue
		}
		add(strategy.FundSymbol)
		add(strategy.ReferenceSymbol)
	}
	for _, strategy := range CommodityBasketStrategies() {
		if IsExcludedSymbol(strategy.FundSymbol) || !allowFund(strategy.FundSymbol) {
			continue
		}
		add(strategy.FundSymbol)
		for _, leg := range strategy.Legs {
			add(leg.Symbol)
		}
	}
	for fund, holdings := range data.Holdings {
		if IsExcludedSymbol(fund) || !allowFund(fund) {
			continue
		}
		add(fund)
		if strategy, ok := SingleCommodityFutureStrategyForFund(fund); ok {
			add(strategy.ReferenceSymbol)
			continue
		}
		if strategy, ok := WeightedAnchorStrategyForFund(fund); ok {
			add(strategy.ReferenceSymbol)
			continue
		}
		if strategy, ok := CommodityBasketStrategyForFund(fund); ok {
			for _, leg := range strategy.Legs {
				add(leg.Symbol)
			}
			continue
		}
		for _, holding := range holdings {
			add(holding.HoldingSymbol)
			if reference, ok := CommodityFutureReferenceForHolding(holding.HoldingSymbol); ok {
				add(reference.Symbol)
			}
			switch holding.Currency {
			case "USD":
				add("fx_susdcny")
			case "HKD":
				add("fx_shkdcny")
			}
		}
	}
	return symbols
}

type CommodityFutureReference struct {
	Symbol string
	Label  string
}

func CommodityFutureReferenceForHolding(symbol string) (CommodityFutureReference, bool) {
	switch strings.ToUpper(strings.TrimSpace(symbol)) {
	case "GLD":
		return CommodityFutureReference{Symbol: "HF_GC", Label: "MGC"}, true
	case "SLV":
		return CommodityFutureReference{Symbol: "HF_SI", Label: "SI"}, true
	case "USO":
		return CommodityFutureReference{Symbol: "HF_CL", Label: "CL"}, true
	default:
		return CommodityFutureReference{}, false
	}
}

func IsCommodityFutureReferenceSymbol(symbol string) bool {
	switch strings.ToUpper(strings.TrimSpace(symbol)) {
	case "HF_GC", "HF_SI", "HF_CL", "HF_HG", "HF_ZN", "HF_NQ", "HF_NK", "HF_ES":
		return true
	default:
		return false
	}
}
