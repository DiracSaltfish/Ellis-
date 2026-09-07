package hkconnectfx

import (
	"bufio"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	SchemaVersion           = "hk-connect-fx.v7"
	CloseAuditSchemaVersion = "hk-connect-fx.close-audit.v2"
	ModelVersion            = "cfets-hkd-cny.1600.rolling-median-180.q005.by-direction.v1"
	EastmoneySouthboundPage = "https://data.eastmoney.com/hsgt/hsgtV2.html"
	EastmoneySouthboundFeed = "https://push2.eastmoney.com/api/qt/kamtbs.rtmin/get?fields1=f1,f2,f3,f4&fields2=f51,f54,f52,f58,f53,f62,f56,f57,f60,f61&ut=b2884a393a59ad64002292a3e90d46a5"
	// Retained only for parser compatibility with previously captured fixtures;
	// production polling uses the Eastmoney source above.
	HKEXMarketPage              = "https://www.hkex.com.hk/?sc_lang=en"
	HKEXTurnoverFeed            = "https://www.hkex.com.hk/eng/csm/script/data_SBSH_Turnover_eng.js"
	SSERatesPage                = "https://www.sse.com.cn/services/hkexsc/disclo/ratios/"
	sseRatesEndpoint            = "https://query.sse.com.cn/commonSoaQuery.do"
	SZSERatesPage               = "https://www.szse.cn/szhk/hkbussiness/exchangerate/index.html"
	ChinaMoneySpotPage          = "https://www.chinamoney.com.cn/chinese/mkdatapfx/"
	ChinaMoneySpotFeed          = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/rfx-sp-quot.json"
	ChinaMoneyHistoryPage       = "https://www.chinamoney.com.cn/chinese/homefxrrm/index.html"
	ChinaMoneyHistoryFeed       = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-fx/RefRateHis"
	ChinaMoneyCentralParityPage = "https://www.chinamoney.com.cn/chinese/bkccpr/index.html?tab=2"
	ChinaMoneyCentralParityFeed = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
	szseRatesEndpoint           = "https://www.szse.cn/api/report/ShowReport/data"
	szseSettlementCatalogID     = "SGT_LSHL"
	szseSettlementTabKey        = "tab2"
	referenceSQLID              = "FW_HGT_GGTHL"
	settlementSQLID             = "FW_HGT_JSHDBL"

	publicStartMinute                    = 9*60 + 5
	cfetsHistoryRetryIntervalMinutes     = 30
	cfetsStartMinute                     = 9*60 + 10
	centralParityStartMinute             = 9*60 + 16
	centralParityRetryIntervalMinutes    = 30
	centralParityHistoryDays             = 120
	decisionStart                        = 14*60 + 30
	morningSessionStartMinute            = 9*60 + 30
	morningSessionEndMinute              = 11*60 + 30
	afternoonSessionStartMinute          = 13 * 60
	marketCloseMinute                    = 16 * 60
	officialSettlementBackfillMinute     = 21*60 + 30
	officialSettlementRecoveryStopMinute = 23*60 + 30
	publicFlowFreshness                  = 4*time.Minute + 30*time.Second
	cfetsQuoteFreshness                  = 3 * time.Minute

	marketShanghai = "shanghai"
	marketShenzhen = "shenzhen"

	// The bootstrap correction is the exact 2026-08-19 rolling-model state
	// selected by the audited out-of-sample backtest.  Net-sell HKD had only 17
	// qualifying samples, below the model's minimum of 20, so its correction is
	// deliberately zero rather than an under-supported median.
	positiveResidual        = 0.000576366035021092
	positiveResidualSamples = 55
	negativeResidual        = 0.0
	negativeResidualSamples = 17
	minimumResidualSamples  = 20
	maxDailyHistoryDays     = 2500
	settlementHistoryDir    = "settlement_history"
)

var shanghai = func() *time.Location {
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	return location
}()

type Flow struct {
	Market            string    `json:"market"`
	TradeDate         string    `json:"trade_date"`
	BuyAmountHKD100   float64   `json:"buy_amount_hkd_100m"`
	SellAmountHKD100  float64   `json:"sell_amount_hkd_100m"`
	TotalAmountHKD100 float64   `json:"total_amount_hkd_100m"`
	PublishedAt       time.Time `json:"published_at"`
	FetchedAt         time.Time `json:"fetched_at"`
	FirstSeenAt       time.Time `json:"first_seen_at"`
	LastChangedAt     time.Time `json:"last_changed_at"`
	Samples           int       `json:"samples"`
	Changes           int       `json:"changes"`
	Source            string    `json:"source"`
}

type ReferenceRate struct {
	ValidDate     string    `json:"valid_date"`
	PublishedDate string    `json:"published_date"`
	BuyRate       float64   `json:"buy_rate"`
	SellRate      float64   `json:"sell_rate"`
	MidRate       float64   `json:"mid_rate"`
	FetchedAt     time.Time `json:"fetched_at"`
	Source        string    `json:"source"`
}

type SettlementRate struct {
	ValidDate string    `json:"valid_date"`
	BuyRate   float64   `json:"buy_rate"`
	SellRate  float64   `json:"sell_rate"`
	FetchedAt time.Time `json:"fetched_at"`
	Source    string    `json:"source"`
}

type FXQuote struct {
	Pair       string     `json:"pair"`
	Bid        *float64   `json:"bid,omitempty"`
	Ask        *float64   `json:"ask,omitempty"`
	Healthy    bool       `json:"healthy"`
	ObservedAt *time.Time `json:"observed_at,omitempty"`
	ReceivedAt *time.Time `json:"received_at,omitempty"`
	Source     string     `json:"source"`
	Error      string     `json:"error,omitempty"`
}

type Model struct {
	Version              string  `json:"version"`
	QuoteTargetTime      string  `json:"quote_target_time"`
	RollingWindowDays    int     `json:"rolling_window_days"`
	NetRatioThreshold    float64 `json:"net_ratio_threshold"`
	SeparateDirection    bool    `json:"separate_direction"`
	MinimumSamples       int     `json:"minimum_samples"`
	Residual             float64 `json:"residual_hkd_cny"`
	ResidualSamples      int     `json:"residual_samples"`
	ResidualCalibratedTo string  `json:"residual_calibrated_to"`
	OOSMeanAbsoluteErrBP float64 `json:"oos_mean_absolute_error_bp"`
	OOSRootMeanSquareBP  float64 `json:"oos_root_mean_square_error_bp"`
	OOSMaximumAbsoluteBP float64 `json:"oos_maximum_absolute_error_bp"`
	CalibrationStatus    string  `json:"calibration_status"`
}

type Estimate struct {
	NetRatio                float64  `json:"net_ratio"`
	QuoteDirection          string   `json:"quote_direction"`
	SelectedHKDCNY          float64  `json:"selected_hkd_cny"`
	AdjustedHKDCNY          float64  `json:"adjusted_hkd_cny"`
	MarketHKDCNY            float64  `json:"market_hkd_cny"`
	HalfSpread              float64  `json:"half_spread"`
	PredictedBuySettlement  float64  `json:"predicted_buy_settlement"`
	PredictedSellSettlement float64  `json:"predicted_sell_settlement"`
	BuyLiveVsEstimate       *float64 `json:"buy_live_vs_estimate,omitempty"`
	SellLiveVsEstimate      *float64 `json:"sell_live_vs_estimate,omitempty"`
	AbsoluteErrorBP         *float64 `json:"absolute_error_bp,omitempty"`
}

type Snapshot struct {
	SchemaVersion              string             `json:"schema_version"`
	TradeDate                  string             `json:"trade_date"`
	GeneratedAt                time.Time          `json:"generated_at"`
	Status                     string             `json:"status"`
	StatusText                 string             `json:"status_text"`
	Actionable                 bool               `json:"actionable"`
	DecisionWindow             string             `json:"decision_window"`
	PublicPollStartedAt        *time.Time         `json:"public_poll_started_at,omitempty"`
	Reference                  *ReferenceRate     `json:"reference,omitempty"`
	Flow                       *Flow              `json:"flow,omitempty"`
	FX                         FXQuote            `json:"fx"`
	FXQuotes                   map[string]FXQuote `json:"fx_quotes"`
	LastHealthyFXQuotes        map[string]FXQuote `json:"last_healthy_fx_quotes"`
	CentralParity              *CentralParityRate `json:"central_parity,omitempty"`
	PreviousSettlement         *SettlementRate    `json:"previous_settlement,omitempty"`
	ActualSettlement           *SettlementRate    `json:"actual_settlement,omitempty"`
	Model                      Model              `json:"model"`
	Estimate                   *Estimate          `json:"estimate,omitempty"`
	ShenzhenStatus             string             `json:"shenzhen_status"`
	ShenzhenStatusText         string             `json:"shenzhen_status_text"`
	ShenzhenActionable         bool               `json:"shenzhen_actionable"`
	ShenzhenFlow               *Flow              `json:"shenzhen_flow,omitempty"`
	ShenzhenPreviousSettlement *SettlementRate    `json:"shenzhen_previous_settlement,omitempty"`
	ShenzhenActualSettlement   *SettlementRate    `json:"shenzhen_actual_settlement,omitempty"`
	ShenzhenModel              Model              `json:"shenzhen_model"`
	ShenzhenEstimate           *Estimate          `json:"shenzhen_estimate,omitempty"`
	Messages                   []string           `json:"messages"`
	Storage                    StorageStatus      `json:"storage"`
}

type StorageStatus struct {
	Kind               string     `json:"kind"`
	DataDir            string     `json:"data_dir"`
	MinuteFile         string     `json:"minute_file"`
	ShenzhenMinuteFile string     `json:"shenzhen_minute_file"`
	LastWriteAt        *time.Time `json:"last_write_at,omitempty"`
	LastError          string     `json:"last_error,omitempty"`
}

type MinutePoint struct {
	Timestamp               string   `json:"timestamp"`
	TradeDate               string   `json:"trade_date"`
	Status                  string   `json:"status"`
	Actionable              bool     `json:"actionable"`
	ReferenceMid            *float64 `json:"reference_mid,omitempty"`
	FlowBuyAmountHKD100     *float64 `json:"flow_buy_amount_hkd_100m,omitempty"`
	FlowSellAmountHKD100    *float64 `json:"flow_sell_amount_hkd_100m,omitempty"`
	FlowTotalAmountHKD100   *float64 `json:"flow_total_amount_hkd_100m,omitempty"`
	NetRatio                *float64 `json:"net_ratio,omitempty"`
	HKDCNYBid               *float64 `json:"hkd_cny_bid,omitempty"`
	HKDCNYAsk               *float64 `json:"hkd_cny_ask,omitempty"`
	SelectedHKDCNY          *float64 `json:"selected_hkd_cny,omitempty"`
	PredictedBuySettlement  *float64 `json:"predicted_buy_settlement,omitempty"`
	PredictedSellSettlement *float64 `json:"predicted_sell_settlement,omitempty"`
	BuyChangeVsPrevious     *float64 `json:"buy_change_vs_previous,omitempty"`
	SellChangeVsPrevious    *float64 `json:"sell_change_vs_previous,omitempty"`
	FlowFetchedAt           string   `json:"flow_fetched_at,omitempty"`
	FlowLastChangedAt       string   `json:"flow_last_changed_at,omitempty"`
	FXObservedAt            string   `json:"fx_observed_at,omitempty"`
	MarketHKDCNY            *float64 `json:"market_hkd_cny,omitempty"`
	BuyLiveVsEstimate       *float64 `json:"buy_live_vs_estimate,omitempty"`
	SellLiveVsEstimate      *float64 `json:"sell_live_vs_estimate,omitempty"`
}

type HistoryResponse struct {
	SchemaVersion string        `json:"schema_version"`
	TradeDate     string        `json:"trade_date"`
	Market        string        `json:"market"`
	Rows          []MinutePoint `json:"rows"`
}

// DailySettlementPoint is a published final settlement rate. The current
// trading day deliberately is not included: the UI reserves that last point
// for its live estimate so actual and estimated values remain distinguishable.
type DailySettlementPoint struct {
	TradeDate            string   `json:"trade_date"`
	ActualBuySettlement  float64  `json:"actual_buy_settlement"`
	ActualSellSettlement float64  `json:"actual_sell_settlement"`
	CFETSHKDCNY1600      *float64 `json:"cfets_hkd_cny_1600,omitempty"`
	HKDCNYCentralParity  *float64 `json:"hkd_cny_central_parity,omitempty"`
}

type CFETSAnchor struct {
	TradeDate  string
	HKDCNY1600 float64
	FetchedAt  time.Time
	Source     string
}

// CentralParityRate is the official once-daily RMB central parity published
// by CFETS. ChinaMoney identifies the quote as HKD/CNY: CNY per one HKD.
type CentralParityRate struct {
	Pair      string    `json:"pair"`
	TradeDate string    `json:"trade_date"`
	Rate      float64   `json:"rate"`
	FetchedAt time.Time `json:"fetched_at"`
	Source    string    `json:"source"`
}

type DailyHistoryResponse struct {
	SchemaVersion string                 `json:"schema_version"`
	TradeDate     string                 `json:"trade_date"`
	Market        string                 `json:"market"`
	RequestedDays int                    `json:"requested_days"`
	Rows          []DailySettlementPoint `json:"rows"`
}

// CloseAuditRow freezes the final 16:00 estimate and all calculation inputs
// for one market and trading day.  Official settlement fields are filled in by
// later polling when the relevant exchange publishes the final result.
type CloseAuditRow struct {
	Market                  string     `json:"market"`
	TradeDate               string     `json:"trade_date"`
	CapturedAt              time.Time  `json:"captured_at"`
	CaptureKind             string     `json:"capture_kind"`
	Status                  string     `json:"status"`
	ModelVersion            string     `json:"model_version"`
	CalibrationStatus       string     `json:"calibration_status"`
	ResidualHKDCNY          float64    `json:"residual_hkd_cny"`
	ResidualSamples         int        `json:"residual_samples"`
	ReferenceMid            float64    `json:"reference_mid"`
	BuyAmountHKD100         float64    `json:"buy_amount_hkd_100m"`
	SellAmountHKD100        float64    `json:"sell_amount_hkd_100m"`
	NetRatio                float64    `json:"net_ratio"`
	HKDCNYBid               *float64   `json:"hkd_cny_bid,omitempty"`
	HKDCNYAsk               *float64   `json:"hkd_cny_ask,omitempty"`
	CFETSHKDCNY1600         *float64   `json:"cfets_hkd_cny_1600,omitempty"`
	SelectedHKDCNY          float64    `json:"selected_hkd_cny"`
	PredictedBuySettlement  float64    `json:"predicted_buy_settlement"`
	PredictedSellSettlement float64    `json:"predicted_sell_settlement"`
	ActualBuySettlement     *float64   `json:"actual_buy_settlement,omitempty"`
	ActualSellSettlement    *float64   `json:"actual_sell_settlement,omitempty"`
	OfficialSource          string     `json:"official_source,omitempty"`
	OfficialFetchedAt       *time.Time `json:"official_fetched_at,omitempty"`
	BuyErrorBP              *float64   `json:"buy_error_bp,omitempty"`
	SellErrorBP             *float64   `json:"sell_error_bp,omitempty"`
	MeanAbsoluteErrorBP     *float64   `json:"mean_absolute_error_bp,omitempty"`
}

type CloseAuditHistoryResponse struct {
	SchemaVersion   string          `json:"schema_version"`
	Market          string          `json:"market"`
	RequestedDays   int             `json:"requested_days"`
	DecisionWindow  string          `json:"decision_window"`
	MethodologyNote string          `json:"methodology_note"`
	Rows            []CloseAuditRow `json:"rows"`
}

type Options struct {
	DataDir    string
	HTTPClient *http.Client
	Logger     *slog.Logger
	Now        func() time.Time
}

type Service struct {
	mu sync.RWMutex

	dataDir         string
	cfetsAnchors    map[string]CFETSAnchor
	centralParities map[string]CentralParityRate
	httpClient      *http.Client
	logger          *slog.Logger
	now             func() time.Time

	tradeDate                  string
	flow                       *Flow
	shenzhenFlow               *Flow
	reference                  *ReferenceRate
	previousSettlement         *SettlementRate
	actualSettlement           *SettlementRate
	settlementHistory          []SettlementRate
	shenzhenPreviousSettlement *SettlementRate
	shenzhenActualSettlement   *SettlementRate
	shenzhenSettlementHistory  []SettlementRate
	fx                         FXQuote
	lastHealthyFX              map[string]FXQuote
	fxQuotes                   map[string]FXQuote
	publicPollStartedAt        *time.Time
	publicErrors               []string
	lastMorningBootstrapDate   string
	lastPublicMinute           string
	lastFXMinute               string
	lastCFETSHistoryDate       string
	lastCFETSHistoryAttempt    string
	lastCentralParityAttempt   string
	lastOfficialBackfillMinute string
	connectTradingDayDate      string
	connectTradingDayOpen      bool
	lastStoredMinute           string
	lastWriteAt                *time.Time
	lastStorageError           string
}

func NewService(options Options) *Service {
	dataDir := strings.TrimSpace(options.DataDir)
	if dataDir == "" {
		dataDir = filepath.Join("snapshots", "hk_connect_fx")
	}
	client := options.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 12 * time.Second}
	}
	logger := options.Logger
	if logger == nil {
		logger = slog.Default()
	}
	now := options.Now
	if now == nil {
		now = time.Now
	}
	anchors, err := loadCFETSAnchors(filepath.Join(dataDir, "cfets_1600"))
	if err != nil {
		logger.Warn("hk-connect FX historical CFETS anchors unavailable", "directory", filepath.Join(dataDir, "cfets_1600"), "error", err)
	}
	centralParityPath := filepath.Join(dataDir, "cfets_1600", "central_parity", "hkd_cny.csv")
	centralParities, centralParityErr := loadCentralParityRates(centralParityPath)
	if centralParityErr != nil && !errors.Is(centralParityErr, os.ErrNotExist) {
		logger.Warn("hk-connect FX central parity history unavailable", "path", centralParityPath, "error", centralParityErr)
	}
	shanghaiHistory, shanghaiHistoryErr := loadSettlementHistoryCSV(filepath.Join(dataDir, settlementHistoryDir, "shanghai.csv"), SSERatesPage)
	if shanghaiHistoryErr != nil && !errors.Is(shanghaiHistoryErr, os.ErrNotExist) {
		logger.Warn("hk-connect FX Shanghai settlement history unavailable", "path", filepath.Join(dataDir, settlementHistoryDir, "shanghai.csv"), "error", shanghaiHistoryErr)
	}
	shenzhenHistory, shenzhenHistoryErr := loadSettlementHistoryCSV(filepath.Join(dataDir, settlementHistoryDir, "shenzhen.csv"), SZSERatesPage)
	if shenzhenHistoryErr != nil && !errors.Is(shenzhenHistoryErr, os.ErrNotExist) {
		logger.Warn("hk-connect FX Shenzhen settlement history unavailable", "path", filepath.Join(dataDir, settlementHistoryDir, "shenzhen.csv"), "error", shenzhenHistoryErr)
	}
	return &Service{
		dataDir:                   dataDir,
		cfetsAnchors:              anchors,
		centralParities:           centralParities,
		httpClient:                client,
		logger:                    logger,
		now:                       now,
		settlementHistory:         shanghaiHistory,
		shenzhenSettlementHistory: shenzhenHistory,
		fx:                        FXQuote{Pair: "HKD/CNY", Source: "CFETS_CHINAMONEY"},
		fxQuotes:                  restoredFXQuotes(dataDir),
		lastHealthyFX:             loadLastHealthyFX(dataDir),
	}
}

func (s *Service) Run(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	initial := s.now()
	s.pollCentralParityOnStartup(ctx, initial)
	s.restoreAfterCloseStateOnStartup(ctx, initial)
	s.step(ctx, initial)
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			s.step(ctx, now)
		}
	}
}

func (s *Service) step(ctx context.Context, now time.Time) {
	local := now.In(shanghai)
	minute := local.Hour()*60 + local.Minute()
	weekday := local.Weekday() >= time.Monday && local.Weekday() <= time.Friday
	day := local.Format("2006-01-02")
	key := local.Format("2006-01-02T15:04")
	if weekday && minute == publicStartMinute && s.shouldPollMorningBootstrap(day) {
		s.pollMorningBootstrap(ctx, local)
	}
	if weekday && isScheduledCFETSHistoryMinute(minute) && s.shouldPollCFETSHistory(day, key) {
		s.pollCFETSHistory(ctx, local)
	}
	if weekday && isScheduledCentralParityMinute(minute) && s.shouldPollCentralParity(day, key) {
		s.pollCentralParity(ctx, local)
	}
	if weekday && isScheduledCFETSPollMinute(minute) && s.shouldPollCFETS(key) {
		s.pollCFETSSpot(ctx, local)
	}
	if weekday && s.shouldPollPublic(day, key, minute) {
		s.pollPublic(ctx, local)
	}
	if weekday && !s.isKnownClosedConnectTradingDay(day) && minute >= officialSettlementBackfillMinute && minute <= officialSettlementRecoveryStopMinute {
		if s.shouldPollOfficialSettlementBackfill(local, key) {
			s.pollOfficialSettlementBackfill(ctx, local)
		}
	}
	if weekday && !s.isKnownClosedConnectTradingDay(day) && isHKConnectLiveSessionMinute(minute) {
		s.mu.RLock()
		shouldStore := key != s.lastStoredMinute
		s.mu.RUnlock()
		if shouldStore {
			s.persistMinute(local)
		}
	}
}

func isScheduledCFETSHistoryMinute(minute int) bool {
	return minute >= publicStartMinute &&
		minute <= marketCloseMinute+5 &&
		(minute-publicStartMinute)%cfetsHistoryRetryIntervalMinutes == 0
}

func isScheduledCentralParityMinute(minute int) bool {
	return minute >= centralParityStartMinute &&
		minute <= marketCloseMinute+centralParityRetryIntervalMinutes &&
		(minute-centralParityStartMinute)%centralParityRetryIntervalMinutes == 0
}

func (s *Service) pollCentralParityOnStartup(ctx context.Context, now time.Time) {
	local := now.In(shanghai)
	minute := local.Hour()*60 + local.Minute()
	weekday := local.Weekday() >= time.Monday && local.Weekday() <= time.Friday
	if !weekday || minute < centralParityStartMinute {
		return
	}
	day := local.Format("2006-01-02")
	key := local.Format("2006-01-02T15:04")
	if s.shouldPollCentralParity(day, key) {
		s.pollCentralParity(ctx, local)
	}
}

// restoreAfterCloseStateOnStartup repopulates the in-memory 16:00 inputs when
// a deployment restarts the service after the normal intraday polling window.
func (s *Service) restoreAfterCloseStateOnStartup(ctx context.Context, now time.Time) {
	local := now.In(shanghai)
	minute := local.Hour()*60 + local.Minute()
	weekday := local.Weekday() >= time.Monday && local.Weekday() <= time.Friday
	if !weekday || minute <= marketCloseMinute {
		return
	}
	s.pollCFETSSpot(ctx, local)
	s.pollPublic(ctx, local)
	s.restoreStoredMinuteState(local)
}

func (s *Service) shouldPollCFETSHistory(day, key string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.lastCFETSHistoryDate == day || s.lastCFETSHistoryAttempt == key {
		return false
	}
	s.lastCFETSHistoryAttempt = key
	return true
}

func (s *Service) shouldPollCentralParity(day, key string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.centralParities[day]; exists || s.lastCentralParityAttempt == key {
		return false
	}
	s.lastCentralParityAttempt = key
	return true
}

func isHKConnectLiveSessionMinute(minute int) bool {
	return (minute >= morningSessionStartMinute && minute <= morningSessionEndMinute) ||
		(minute >= afternoonSessionStartMinute && minute <= marketCloseMinute)
}

func isScheduledPublicPollMinute(minute int) bool {
	if minute >= morningSessionStartMinute && minute <= morningSessionEndMinute {
		return (minute-morningSessionStartMinute)%2 == 0
	}
	if minute >= afternoonSessionStartMinute && minute <= marketCloseMinute {
		return (minute-afternoonSessionStartMinute)%2 == 0
	}
	return false
}

func isScheduledCFETSPollMinute(minute int) bool {
	return minute >= cfetsStartMinute && minute <= marketCloseMinute && (minute-cfetsStartMinute)%2 == 0
}

func (s *Service) shouldPollCFETS(key string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.lastFXMinute == key {
		return false
	}
	s.lastFXMinute = key
	return true
}

func (s *Service) shouldPollMorningBootstrap(day string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.lastMorningBootstrapDate == day {
		return false
	}
	s.lastMorningBootstrapDate = day
	return true
}

func (s *Service) shouldPollPublic(day, key string, minute int) bool {
	if !isScheduledPublicPollMinute(minute) {
		return false
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	return !(s.connectTradingDayDate == day && !s.connectTradingDayOpen) && key != s.lastPublicMinute
}

func (s *Service) isKnownClosedConnectTradingDay(day string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.connectTradingDayDate == day && !s.connectTradingDayOpen
}

func (s *Service) PollNow(ctx context.Context, now time.Time) {
	s.pollPublic(ctx, now.In(shanghai))
}

// pollMorningBootstrap establishes whether Stock Connect is open today using
// the current-day SSE reference rate, then refreshes the published settlement
// history for the previous day's audit. It intentionally does not request the
// intraday Eastmoney flow before the opening auction has completed.
func (s *Service) pollMorningBootstrap(ctx context.Context, now time.Time) {
	day := now.Format("2006-01-02")
	started := now
	references, referenceErr := s.fetchReferenceRates(ctx, now.AddDate(0, 0, -10), now)
	settlements, settlementErr := s.fetchSettlementRates(ctx, now.AddDate(0, 0, -45), now)
	shenzhenSettlements, shenzhenSettlementErr := s.fetchShenzhenSettlementRates(ctx, now.AddDate(0, 0, -45), now)

	errorsList := make([]string, 0, 3)
	if referenceErr != nil {
		errorsList = append(errorsList, "参考汇率: "+referenceErr.Error())
	}
	if settlementErr != nil {
		errorsList = append(errorsList, "上交所结算汇率: "+settlementErr.Error())
	}
	if shenzhenSettlementErr != nil {
		errorsList = append(errorsList, "深交所结算汇率: "+shenzhenSettlementErr.Error())
	}

	reference := selectReference(references, day)
	s.mu.Lock()
	s.tradeDate = day
	s.publicPollStartedAt = &started
	s.applyReferenceRateLocked(day, reference, referenceErr)
	s.applySettlementRatesLocked(day, settlements, settlementErr, shenzhenSettlements, shenzhenSettlementErr)
	closed := s.connectTradingDayDate == day && !s.connectTradingDayOpen
	s.mu.Unlock()

	if settlementErr == nil {
		if err := s.reconcileCloseAudits(marketShanghai, settlementRatesFromRows(settlements, SSERatesPage)); err != nil {
			errorsList = append(errorsList, "沪港通审计回填: "+err.Error())
		}
	}
	if shenzhenSettlementErr == nil {
		if err := s.reconcileCloseAudits(marketShenzhen, settlementRatesFromRows(shenzhenSettlements, SZSERatesPage)); err != nil {
			errorsList = append(errorsList, "深港通审计回填: "+err.Error())
		}
	}

	s.mu.Lock()
	s.publicErrors = errorsList
	s.mu.Unlock()
	if len(errorsList) > 0 {
		s.logger.Warn("hk-connect fx morning bootstrap incomplete", "errors", strings.Join(errorsList, " | "))
		return
	}
	if closed {
		s.logger.Info("hk-connect fx trading closed; intraday polling disabled", "trade_date", day)
	}
}

// shouldPollOfficialSettlementBackfill schedules the fixed 21:30 official-only
// pull. A service restart after that time gets one recovery query for the same
// day; the next routine retry remains the following business day's 09:05 poll.
func (s *Service) shouldPollOfficialSettlementBackfill(now time.Time, key string) bool {
	day := now.Format("2006-01-02")
	s.mu.Lock()
	defer s.mu.Unlock()
	if settlementPublishedForDay(s.actualSettlement, day) && settlementPublishedForDay(s.shenzhenActualSettlement, day) {
		return false
	}
	if strings.HasPrefix(s.lastOfficialBackfillMinute, day+"T") {
		return false
	}
	s.lastOfficialBackfillMinute = key
	return true
}

func settlementPublishedForDay(rate *SettlementRate, day string) bool {
	return rate != nil && rate.ValidDate == day && validRate(rate.BuyRate) && validRate(rate.SellRate)
}

func (s *Service) pollPublic(ctx context.Context, now time.Time) {
	day := now.Format("2006-01-02")
	started := now
	s.mu.Lock()
	s.tradeDate = day
	s.publicPollStartedAt = &started
	s.lastPublicMinute = now.Format("2006-01-02T15:04")
	s.mu.Unlock()

	shanghaiFlow, shenzhenFlow, flowErr := s.fetchSouthboundFlows(ctx, day, now)
	references, referenceErr := s.fetchReferenceRates(ctx, now.AddDate(0, 0, -10), now)
	settlements, settlementErr := s.fetchSettlementRates(ctx, now.AddDate(0, 0, -45), now)
	shenzhenSettlements, shenzhenSettlementErr := s.fetchShenzhenSettlementRates(ctx, now.AddDate(0, 0, -45), now)

	errorsList := make([]string, 0, 6)
	if flowErr != nil {
		errorsList = append(errorsList, "成交概况: "+flowErr.Error())
	}
	if referenceErr != nil {
		errorsList = append(errorsList, "参考汇率: "+referenceErr.Error())
	}
	if settlementErr != nil {
		errorsList = append(errorsList, "上交所结算汇率: "+settlementErr.Error())
	}
	if shenzhenSettlementErr != nil {
		errorsList = append(errorsList, "深交所结算汇率: "+shenzhenSettlementErr.Error())
	}

	s.mu.Lock()
	if shanghaiFlow != nil {
		s.flow = observedFlow(s.flow, shanghaiFlow, now)
	}
	if shenzhenFlow != nil {
		s.shenzhenFlow = observedFlow(s.shenzhenFlow, shenzhenFlow, now)
	}
	s.applyReferenceRateLocked(day, selectReference(references, day), referenceErr)
	s.applySettlementRatesLocked(day, settlements, settlementErr, shenzhenSettlements, shenzhenSettlementErr)
	s.mu.Unlock()

	if settlementErr == nil {
		if err := s.reconcileCloseAudits(marketShanghai, settlementRatesFromRows(settlements, SSERatesPage)); err != nil {
			errorsList = append(errorsList, "沪港通审计回填: "+err.Error())
		}
	}
	if shenzhenSettlementErr == nil {
		if err := s.reconcileCloseAudits(marketShenzhen, settlementRatesFromRows(shenzhenSettlements, SZSERatesPage)); err != nil {
			errorsList = append(errorsList, "深港通审计回填: "+err.Error())
		}
	}

	s.mu.Lock()
	s.publicErrors = errorsList
	s.mu.Unlock()
	if len(errorsList) > 0 {
		s.logger.Warn("hk-connect fx public poll incomplete", "errors", strings.Join(errorsList, " | "))
	}
}

func (s *Service) applyReferenceRateLocked(day string, reference *ReferenceRate, referenceErr error) {
	if referenceErr != nil {
		return
	}
	s.reference = reference
	s.connectTradingDayDate = day
	s.connectTradingDayOpen = reference != nil
}

// pollOfficialSettlementBackfill fetches only the two exchange-published
// settlement feeds. It deliberately does not request Eastmoney flow or
// reference-rate data after the market is closed.
func (s *Service) pollOfficialSettlementBackfill(ctx context.Context, now time.Time) {
	day := now.Format("2006-01-02")
	settlements, settlementErr := s.fetchSettlementRates(ctx, now.AddDate(0, 0, -45), now)
	shenzhenSettlements, shenzhenSettlementErr := s.fetchShenzhenSettlementRates(ctx, now.AddDate(0, 0, -45), now)

	errorsList := make([]string, 0, 2)
	if settlementErr != nil {
		errorsList = append(errorsList, "上交所结算汇率: "+settlementErr.Error())
	}
	if shenzhenSettlementErr != nil {
		errorsList = append(errorsList, "深交所结算汇率: "+shenzhenSettlementErr.Error())
	}

	s.mu.Lock()
	s.tradeDate = day
	s.applySettlementRatesLocked(day, settlements, settlementErr, shenzhenSettlements, shenzhenSettlementErr)
	shanghaiPublished := settlementPublishedForDay(s.actualSettlement, day)
	shenzhenPublished := settlementPublishedForDay(s.shenzhenActualSettlement, day)
	s.mu.Unlock()

	if settlementErr == nil {
		if err := s.reconcileCloseAudits(marketShanghai, settlementRatesFromRows(settlements, SSERatesPage)); err != nil {
			errorsList = append(errorsList, "沪港通审计回填: "+err.Error())
		}
	}
	if shenzhenSettlementErr == nil {
		if err := s.reconcileCloseAudits(marketShenzhen, settlementRatesFromRows(shenzhenSettlements, SZSERatesPage)); err != nil {
			errorsList = append(errorsList, "深港通审计回填: "+err.Error())
		}
	}

	if len(errorsList) > 0 {
		s.logger.Warn("hk-connect fx official settlement backfill incomplete", "errors", strings.Join(errorsList, " | "))
		return
	}
	if shanghaiPublished || shenzhenPublished {
		s.logger.Info("hk-connect fx official settlement backfilled", "trade_date", day, "shanghai", shanghaiPublished, "shenzhen", shenzhenPublished)
	}
}

func (s *Service) applySettlementRatesLocked(day string, settlements []rateRow, settlementErr error, shenzhenSettlements []rateRow, shenzhenSettlementErr error) {
	if settlement := selectSettlement(settlements, day, SSERatesPage); settlement != nil {
		s.actualSettlement = settlement
	}
	if previous := previousSettlement(settlements, day, SSERatesPage); previous != nil {
		s.previousSettlement = previous
	}
	if settlementErr == nil {
		s.settlementHistory = mergeSettlementHistories(s.settlementHistory, settlementRatesFromRows(settlements, SSERatesPage))
	}
	if settlement := selectSettlement(shenzhenSettlements, day, SZSERatesPage); settlement != nil {
		s.shenzhenActualSettlement = settlement
	}
	if previous := previousSettlement(shenzhenSettlements, day, SZSERatesPage); previous != nil {
		s.shenzhenPreviousSettlement = previous
	}
	if shenzhenSettlementErr == nil {
		s.shenzhenSettlementHistory = mergeSettlementHistories(s.shenzhenSettlementHistory, settlementRatesFromRows(shenzhenSettlements, SZSERatesPage))
	}
}

func observedFlow(previous, next *Flow, now time.Time) *Flow {
	if previous != nil && previous.TradeDate == next.TradeDate {
		next.FirstSeenAt = previous.FirstSeenAt
		next.Samples = previous.Samples + 1
		next.Changes = previous.Changes
		if sameFlow(previous, next) {
			next.LastChangedAt = previous.LastChangedAt
		} else {
			next.LastChangedAt = now
			next.Changes++
		}
		return next
	}
	next.FirstSeenAt = now
	next.LastChangedAt = now
	next.Samples = 1
	return next
}

func sameFlow(left, right *Flow) bool {
	return left != nil && right != nil &&
		left.BuyAmountHKD100 == right.BuyAmountHKD100 &&
		left.SellAmountHKD100 == right.SellAmountHKD100 &&
		left.TotalAmountHKD100 == right.TotalAmountHKD100
}

func validRate(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value > 0
}

func (s *Service) Snapshot() Snapshot {
	now := s.now().In(shanghai)
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.snapshotLocked(now)
}

func (s *Service) snapshotLocked(now time.Time) Snapshot {
	day := now.Format("2006-01-02")
	if s.tradeDate != "" {
		day = s.tradeDate
	}
	minutePath, _ := filepath.Abs(s.minutePath(day))
	shenzhenMinutePath, _ := filepath.Abs(s.minutePathForMarket(day, marketShenzhen))
	shanghaiState := s.marketStateLocked(now, day, marketShanghai, s.flow, s.previousSettlement, s.actualSettlement)
	shenzhenState := s.marketStateLocked(now, day, marketShenzhen, s.shenzhenFlow, s.shenzhenPreviousSettlement, s.shenzhenActualSettlement)
	var centralParity *CentralParityRate
	if value, exists := s.centralParities[day]; exists {
		centralParity = cloneCentralParity(&value)
	}
	result := Snapshot{
		SchemaVersion:              SchemaVersion,
		TradeDate:                  day,
		GeneratedAt:                now,
		Status:                     shanghaiState.Status,
		StatusText:                 shanghaiState.StatusText,
		Actionable:                 shanghaiState.Actionable,
		DecisionWindow:             "14:30–16:00",
		PublicPollStartedAt:        cloneTime(s.publicPollStartedAt),
		Reference:                  cloneReference(s.reference),
		Flow:                       cloneFlow(s.flow),
		FX:                         freshFXQuotes(map[string]FXQuote{"HKD/CNY": s.fx}, now)["HKD/CNY"],
		FXQuotes:                   freshFXQuotes(s.fxQuotes, now),
		LastHealthyFXQuotes:        cloneFXQuotes(s.lastHealthyFX),
		CentralParity:              centralParity,
		PreviousSettlement:         cloneSettlement(s.previousSettlement),
		ActualSettlement:           cloneSettlement(s.actualSettlement),
		Model:                      shanghaiState.Model,
		Estimate:                   shanghaiState.Estimate,
		ShenzhenStatus:             shenzhenState.Status,
		ShenzhenStatusText:         shenzhenState.StatusText,
		ShenzhenActionable:         shenzhenState.Actionable,
		ShenzhenFlow:               cloneFlow(s.shenzhenFlow),
		ShenzhenPreviousSettlement: cloneSettlement(s.shenzhenPreviousSettlement),
		ShenzhenActualSettlement:   cloneSettlement(s.shenzhenActualSettlement),
		ShenzhenModel:              shenzhenState.Model,
		ShenzhenEstimate:           shenzhenState.Estimate,
		Messages:                   append([]string{}, s.publicErrors...),
		Storage: StorageStatus{
			Kind:               "processed-minute-csv",
			DataDir:            s.dataDir,
			MinuteFile:         minutePath,
			ShenzhenMinuteFile: shenzhenMinutePath,
			LastWriteAt:        cloneTime(s.lastWriteAt),
			LastError:          s.lastStorageError,
		},
	}
	return result
}

type marketState struct {
	Status     string
	StatusText string
	Actionable bool
	Model      Model
	Estimate   *Estimate
}

func (s *Service) marketStateLocked(now time.Time, day, market string, flow *Flow, previous, actual *SettlementRate) marketState {
	state := marketState{
		Status:     "scheduled",
		StatusText: "等待 09:05 启动东财港股通盘中成交额与上交所汇率轮询",
		Model:      modelForMarketDirection(market, 0),
	}
	minute := now.Hour()*60 + now.Minute()
	if minute < publicStartMinute {
		return state
	}
	if flow == nil || flow.TradeDate != day {
		state.Status = "waiting_eastmoney"
		state.StatusText = fmt.Sprintf("等待东财%s港股通实时买入/卖出成交额", marketLabel(market))
		return state
	}
	if s.reference == nil || s.reference.ValidDate != day {
		state.Status = "waiting_sse_rates"
		state.StatusText = "等待上交所当日参考汇率"
		return state
	}
	if previous == nil {
		state.Status = "waiting_settlement_rates"
		state.StatusText = "等待前一交易日最终结算汇率"
		return state
	}
	if s.fx.Bid == nil || s.fx.Ask == nil || s.fx.ObservedAt == nil || s.fx.ObservedAt.IsZero() {
		state.Status = "waiting_cfets"
		state.StatusText = "等待 09:10 后的外汇交易中心 HKD/CNY BID/ASK"
		return state
	}
	total := flow.BuyAmountHKD100 + flow.SellAmountHKD100
	if total <= 0 {
		state.Status = "waiting_eastmoney"
		state.StatusText = fmt.Sprintf("东财%s港股通当日成交额尚不可用", marketLabel(market))
		return state
	}
	q := (flow.BuyAmountHKD100 - flow.SellAmountHKD100) / total
	model := modelForMarketDirection(market, q)
	state.Model = model
	selected := 0.0
	direction := "成交净额为零：使用 HKD/CNY 中间价"
	if q > 0 {
		selected = *s.fx.Ask
		direction = "净买入港币：使用 HKD/CNY ASK"
	} else if q < 0 {
		selected = *s.fx.Bid
		direction = "净卖出港币：使用 HKD/CNY BID"
	} else {
		selected = (*s.fx.Bid + *s.fx.Ask) / 2
	}
	adjusted := selected + model.Residual
	d := q * (adjusted - s.reference.MidRate)
	predBuy := s.reference.MidRate - d
	predSell := s.reference.MidRate + d
	marketHKDCNY := (*s.fx.Bid + *s.fx.Ask) / 2
	estimate := &Estimate{
		NetRatio:                q,
		QuoteDirection:          direction,
		SelectedHKDCNY:          selected,
		AdjustedHKDCNY:          adjusted,
		MarketHKDCNY:            marketHKDCNY,
		HalfSpread:              d,
		PredictedBuySettlement:  predBuy,
		PredictedSellSettlement: predSell,
	}
	buyLiveVsEstimate := marketHKDCNY/predBuy - 1
	sellLiveVsEstimate := marketHKDCNY/predSell - 1
	estimate.BuyLiveVsEstimate = &buyLiveVsEstimate
	estimate.SellLiveVsEstimate = &sellLiveVsEstimate
	if actual != nil && actual.ValidDate == day {
		errorBP := (math.Abs(predBuy-actual.BuyRate) + math.Abs(predSell-actual.SellRate)) / 2 * 10000
		estimate.AbsoluteErrorBP = &errorBP
	}
	state.Estimate = estimate

	fxAge := now.Sub(*s.fx.ObservedAt)
	flowAge := now.Sub(flow.PublishedAt)
	if actual != nil && actual.ValidDate == day {
		state.Status = "final_comparison"
		state.StatusText = "实际结算汇率已发布，可查看估值误差"
		return state
	}
	if minute > marketCloseMinute {
		state.Status = "closed"
		state.StatusText = "16:00 估值窗口已结束，保留最后可用估值"
		return state
	}
	if !s.fx.Healthy {
		state.Status = "cfets_unavailable"
		state.StatusText = "外汇交易中心抓取异常；保留最后报价但禁止操作"
		return state
	}
	if fxAge < -30*time.Second || fxAge > cfetsQuoteFreshness {
		state.Status = "stale_cfets"
		state.StatusText = "外汇交易中心报价超过 3 分钟，禁止操作"
		return state
	}
	if flowAge < -30*time.Second || flowAge > publicFlowFreshness {
		state.Status = "stale_eastmoney"
		state.StatusText = "东财成交额发布时间超过 4 分 30 秒，禁止操作"
		return state
	}
	if minute < decisionStart {
		state.Status = "reference_only"
		state.StatusText = "估值仅供观察；14:30 后才进入较可靠决策窗口"
		return state
	}
	state.Status = "live"
	state.StatusText = "东财成交额与外汇交易中心数据新鲜，处于 14:30–16:00 决策窗口"
	state.Actionable = true
	return state
}

func marketLabel(market string) string {
	if market == marketShenzhen {
		return "深"
	}
	return "沪"
}

func modelForDirection(q float64) Model {
	residual, samples := negativeResidual, negativeResidualSamples
	if q >= 0 {
		residual, samples = positiveResidual, positiveResidualSamples
	}
	if samples < minimumResidualSamples {
		residual = 0
	}
	return Model{
		Version:              ModelVersion,
		QuoteTargetTime:      "16:00",
		RollingWindowDays:    180,
		NetRatioThreshold:    0.05,
		SeparateDirection:    true,
		MinimumSamples:       minimumResidualSamples,
		Residual:             residual,
		ResidualSamples:      samples,
		ResidualCalibratedTo: "2026-08-19",
		OOSMeanAbsoluteErrBP: 0.22147199107354096,
		OOSRootMeanSquareBP:  0.3541069027757257,
		OOSMaximumAbsoluteBP: 2.4493744366856216,
		CalibrationStatus:    "validated_shanghai",
	}
}

func modelForMarketDirection(market string, q float64) Model {
	model := modelForDirection(q)
	if market != marketShenzhen {
		return model
	}
	// SH and SZ use the same published netting formula and reference midpoint,
	// but the exchange-confirmed final rates are distinct. Do not transfer the
	// Shanghai-only residual calibration to Shenzhen before its own history is
	// collected and validated.
	model.Version = "official-formula.zero-residual.pending-shenzhen-calibration.v1"
	model.Residual = 0
	model.ResidualSamples = 0
	model.ResidualCalibratedTo = ""
	model.OOSMeanAbsoluteErrBP = 0
	model.OOSRootMeanSquareBP = 0
	model.OOSMaximumAbsoluteBP = 0
	model.CalibrationStatus = "pending_shenzhen"
	return model
}

func cloneTime(value *time.Time) *time.Time {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func cloneFlow(value *Flow) *Flow {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func cloneReference(value *ReferenceRate) *ReferenceRate {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func cloneSettlement(value *SettlementRate) *SettlementRate {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func cloneCentralParity(value *CentralParityRate) *CentralParityRate {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func cloneFX(value FXQuote) FXQuote {
	copy := value
	if value.Bid != nil {
		bid := *value.Bid
		copy.Bid = &bid
	}
	if value.Ask != nil {
		ask := *value.Ask
		copy.Ask = &ask
	}
	copy.ObservedAt = cloneTime(value.ObservedAt)
	copy.ReceivedAt = cloneTime(value.ReceivedAt)
	return copy
}

func initialCFETSSpotQuotes() map[string]FXQuote {
	quotes := make(map[string]FXQuote, 4)
	for _, pair := range []string{"USD/CNY", "HKD/CNY", "EUR/CNY", "100JPY/CNY"} {
		quotes[pair] = FXQuote{Pair: pair, Source: "CFETS_CHINAMONEY"}
	}
	return quotes
}

func cloneFXQuotes(values map[string]FXQuote) map[string]FXQuote {
	quotes := make(map[string]FXQuote, len(values))
	for pair, quote := range values {
		quotes[pair] = cloneFX(quote)
	}
	return quotes
}

func (s *Service) persistMinute(now time.Time) {
	s.mu.RLock()
	snapshot := s.snapshotLocked(now)
	s.mu.RUnlock()
	shanghaiPoint := pointFromSnapshot(snapshot)
	shenzhenPoint := pointFromShenzhenSnapshot(snapshot)
	shanghaiPath := s.minutePath(shanghaiPoint.TradeDate)
	shenzhenPath := s.minutePathForMarket(shenzhenPoint.TradeDate, marketShenzhen)
	err := appendMinuteCSV(shanghaiPath, shanghaiPoint)
	if err == nil {
		err = appendMinuteCSV(shenzhenPath, shenzhenPoint)
	}
	if err == nil && now.Hour()*60+now.Minute() == marketCloseMinute {
		err = s.persistCloseAudits(snapshot)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastStoredMinute = now.Format("2006-01-02T15:04")
	if err != nil {
		s.lastStorageError = err.Error()
		s.logger.Error("hk-connect fx CSV persistence failed", "shanghai_path", shanghaiPath, "shenzhen_path", shenzhenPath, "error", err)
		return
	}
	written := now
	s.lastWriteAt = &written
	s.lastStorageError = ""
}

func (s *Service) minutePath(day string) string {
	return s.minutePathForMarket(day, marketShanghai)
}

func (s *Service) minutePathForMarket(day, market string) string {
	year := "unknown"
	if len(day) >= 4 {
		year = day[:4]
	}
	if market == marketShenzhen {
		return filepath.Join(s.dataDir, "minute_shenzhen", year, day+".csv")
	}
	return filepath.Join(s.dataDir, "minute", year, day+".csv")
}

func (s *Service) closeAuditPathForMarket(day, market string) string {
	year := "unknown"
	if len(day) >= 4 {
		year = day[:4]
	}
	return filepath.Join(s.dataDir, "close_audit", market, year+".csv")
}

func (s *Service) History(day, market string) (HistoryResponse, error) {
	day = strings.TrimSpace(day)
	if day == "" {
		day = s.now().In(shanghai).Format("2006-01-02")
	}
	if _, err := time.Parse("2006-01-02", day); err != nil {
		return HistoryResponse{}, fmt.Errorf("date must be YYYY-MM-DD")
	}
	market, err := normalizeMarket(market)
	if err != nil {
		return HistoryResponse{}, err
	}
	rows, err := readMinuteCSV(s.minutePathForMarket(day, market))
	if errors.Is(err, os.ErrNotExist) {
		rows, err = []MinutePoint{}, nil
	}
	return HistoryResponse{SchemaVersion: SchemaVersion, TradeDate: day, Market: market, Rows: rows}, err
}

func normalizeMarket(value string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", marketShanghai:
		return marketShanghai, nil
	case marketShenzhen:
		return marketShenzhen, nil
	default:
		return "", fmt.Errorf("market must be shanghai or shenzhen")
	}
}

// DailyHistory returns a market's preceding published settlement rates. The
// requested count includes the current-day prediction rendered by the UI, so a
// seven-day chart needs six actual historical rows plus one live forecast.
func (s *Service) DailyHistory(days int, market string) (DailyHistoryResponse, error) {
	if days < 2 || days > maxDailyHistoryDays {
		return DailyHistoryResponse{}, fmt.Errorf("days must be between 2 and %d", maxDailyHistoryDays)
	}
	market, err := normalizeMarket(market)
	if err != nil {
		return DailyHistoryResponse{}, err
	}
	now := s.now().In(shanghai)
	s.mu.RLock()
	tradeDate := s.tradeDate
	if tradeDate == "" {
		tradeDate = now.Format("2006-01-02")
	}
	history := append([]SettlementRate(nil), s.settlementHistory...)
	if market == marketShenzhen {
		history = append([]SettlementRate(nil), s.shenzhenSettlementHistory...)
	}
	anchors := make(map[string]CFETSAnchor, len(s.cfetsAnchors))
	for date, anchor := range s.cfetsAnchors {
		anchors[date] = anchor
	}
	centralParities := make(map[string]CentralParityRate, len(s.centralParities))
	for date, parity := range s.centralParities {
		centralParities[date] = parity
	}
	s.mu.RUnlock()

	rows := make([]DailySettlementPoint, 0, days-1)
	for _, rate := range history {
		if rate.ValidDate >= tradeDate {
			continue
		}
		anchor := anchors[rate.ValidDate]
		centralParity := centralParities[rate.ValidDate]
		rows = append(rows, DailySettlementPoint{
			TradeDate:            rate.ValidDate,
			ActualBuySettlement:  rate.BuyRate,
			ActualSellSettlement: rate.SellRate,
			CFETSHKDCNY1600:      optionalValidRate(anchor.HKDCNY1600),
			HKDCNYCentralParity:  optionalValidRate(centralParity.Rate),
		})
	}
	if len(rows) > days-1 {
		rows = rows[len(rows)-(days-1):]
	}
	return DailyHistoryResponse{SchemaVersion: SchemaVersion, TradeDate: tradeDate, Market: market, RequestedDays: days, Rows: rows}, nil
}

// CloseAuditHistory reads immutable end-of-session checkpoints from CSV. It
// never recalculates historical estimates: only the official settlement fields
// may be filled after the exchange publishes them.
func (s *Service) CloseAuditHistory(days int, market string) (CloseAuditHistoryResponse, error) {
	if days < 1 || days > 3650 {
		return CloseAuditHistoryResponse{}, fmt.Errorf("days must be between 1 and 3650")
	}
	market, err := normalizeMarket(market)
	if err != nil {
		return CloseAuditHistoryResponse{}, err
	}
	rows, err := s.readCloseAudits(market)
	if err != nil {
		return CloseAuditHistoryResponse{}, err
	}
	s.mu.RLock()
	anchors := make(map[string]CFETSAnchor, len(s.cfetsAnchors))
	for date, anchor := range s.cfetsAnchors {
		anchors[date] = anchor
	}
	s.mu.RUnlock()
	for index := range rows {
		anchor := anchors[rows[index].TradeDate]
		if value := optionalValidRate(anchor.HKDCNY1600); value != nil {
			rows[index].CFETSHKDCNY1600 = value
		}
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].TradeDate == rows[j].TradeDate {
			return rows[i].CapturedAt.After(rows[j].CapturedAt)
		}
		return rows[i].TradeDate > rows[j].TradeDate
	})
	if len(rows) > days {
		rows = rows[:days]
	}
	return CloseAuditHistoryResponse{
		SchemaVersion:   CloseAuditSchemaVersion,
		Market:          market,
		RequestedDays:   days,
		DecisionWindow:  "14:30–16:00",
		MethodologyNote: "每个交易日保存北京时间 16:00 的港股通双边估值、成交额、外汇交易中心 HKD/CNY 报价与模型状态；16:00 先冻结实时中间价，随后由中国货币网官方 16:00 参考汇率覆盖；交易所最终结算汇率公布后自动回填，估值误差按（预测 ÷ 实际 − 1）×10000 计算。",
		Rows:            rows,
	}, nil
}

func pointFromSnapshot(snapshot Snapshot) MinutePoint {
	point := MinutePoint{Timestamp: snapshot.GeneratedAt.Format(time.RFC3339), TradeDate: snapshot.TradeDate, Status: snapshot.Status, Actionable: snapshot.Actionable}
	if snapshot.Reference != nil {
		point.ReferenceMid = floatPointer(snapshot.Reference.MidRate)
	}
	if snapshot.Flow != nil {
		point.FlowBuyAmountHKD100 = floatPointer(snapshot.Flow.BuyAmountHKD100)
		point.FlowSellAmountHKD100 = floatPointer(snapshot.Flow.SellAmountHKD100)
		point.FlowTotalAmountHKD100 = floatPointer(snapshot.Flow.TotalAmountHKD100)
		point.FlowFetchedAt = snapshot.Flow.FetchedAt.Format(time.RFC3339)
		point.FlowLastChangedAt = snapshot.Flow.LastChangedAt.Format(time.RFC3339)
	}
	point.HKDCNYBid = cloneFloat(snapshot.FX.Bid)
	point.HKDCNYAsk = cloneFloat(snapshot.FX.Ask)
	if snapshot.FX.ObservedAt != nil && !snapshot.FX.ObservedAt.IsZero() {
		point.FXObservedAt = snapshot.FX.ObservedAt.Format(time.RFC3339)
	}
	if snapshot.Estimate != nil {
		point.NetRatio = floatPointer(snapshot.Estimate.NetRatio)
		point.SelectedHKDCNY = floatPointer(snapshot.Estimate.SelectedHKDCNY)
		point.PredictedBuySettlement = floatPointer(snapshot.Estimate.PredictedBuySettlement)
		point.PredictedSellSettlement = floatPointer(snapshot.Estimate.PredictedSellSettlement)
		point.MarketHKDCNY = floatPointer(snapshot.Estimate.MarketHKDCNY)
		point.BuyLiveVsEstimate = cloneFloat(snapshot.Estimate.BuyLiveVsEstimate)
		point.SellLiveVsEstimate = cloneFloat(snapshot.Estimate.SellLiveVsEstimate)
	}
	return point
}

func pointFromShenzhenSnapshot(snapshot Snapshot) MinutePoint {
	point := MinutePoint{Timestamp: snapshot.GeneratedAt.Format(time.RFC3339), TradeDate: snapshot.TradeDate, Status: snapshot.ShenzhenStatus, Actionable: snapshot.ShenzhenActionable}
	if snapshot.Reference != nil {
		point.ReferenceMid = floatPointer(snapshot.Reference.MidRate)
	}
	if snapshot.ShenzhenFlow != nil {
		point.FlowBuyAmountHKD100 = floatPointer(snapshot.ShenzhenFlow.BuyAmountHKD100)
		point.FlowSellAmountHKD100 = floatPointer(snapshot.ShenzhenFlow.SellAmountHKD100)
		point.FlowTotalAmountHKD100 = floatPointer(snapshot.ShenzhenFlow.TotalAmountHKD100)
		point.FlowFetchedAt = snapshot.ShenzhenFlow.FetchedAt.Format(time.RFC3339)
		point.FlowLastChangedAt = snapshot.ShenzhenFlow.LastChangedAt.Format(time.RFC3339)
	}
	point.HKDCNYBid = cloneFloat(snapshot.FX.Bid)
	point.HKDCNYAsk = cloneFloat(snapshot.FX.Ask)
	if snapshot.FX.ObservedAt != nil && !snapshot.FX.ObservedAt.IsZero() {
		point.FXObservedAt = snapshot.FX.ObservedAt.Format(time.RFC3339)
	}
	if snapshot.ShenzhenEstimate != nil {
		point.NetRatio = floatPointer(snapshot.ShenzhenEstimate.NetRatio)
		point.SelectedHKDCNY = floatPointer(snapshot.ShenzhenEstimate.SelectedHKDCNY)
		point.PredictedBuySettlement = floatPointer(snapshot.ShenzhenEstimate.PredictedBuySettlement)
		point.PredictedSellSettlement = floatPointer(snapshot.ShenzhenEstimate.PredictedSellSettlement)
		point.MarketHKDCNY = floatPointer(snapshot.ShenzhenEstimate.MarketHKDCNY)
		point.BuyLiveVsEstimate = cloneFloat(snapshot.ShenzhenEstimate.BuyLiveVsEstimate)
		point.SellLiveVsEstimate = cloneFloat(snapshot.ShenzhenEstimate.SellLiveVsEstimate)
	}
	return point
}

func (s *Service) persistCloseAudits(snapshot Snapshot) error {
	rows := make([]CloseAuditRow, 0, 2)
	if row, ok := closeAuditFromSnapshot(snapshot, marketShanghai); ok {
		rows = append(rows, row)
	}
	if row, ok := closeAuditFromSnapshot(snapshot, marketShenzhen); ok {
		rows = append(rows, row)
	}
	if len(rows) == 0 {
		return errors.New("no valid close estimate is available for audit capture")
	}
	var failures []string
	for _, row := range rows {
		if err := upsertCloseAuditCSV(s.closeAuditPathForMarket(row.TradeDate, row.Market), row); err != nil {
			failures = append(failures, row.Market+": "+err.Error())
		}
	}
	if len(failures) > 0 {
		return errors.New(strings.Join(failures, " | "))
	}
	return nil
}

func closeAuditFromSnapshot(snapshot Snapshot, market string) (CloseAuditRow, bool) {
	row := CloseAuditRow{Market: market, TradeDate: snapshot.TradeDate, CapturedAt: snapshot.GeneratedAt, CaptureKind: "final_close"}
	var flow *Flow
	var estimate *Estimate
	var actual *SettlementRate
	var model Model
	if market == marketShenzhen {
		flow, estimate, actual, model = snapshot.ShenzhenFlow, snapshot.ShenzhenEstimate, snapshot.ShenzhenActualSettlement, snapshot.ShenzhenModel
		row.Status = snapshot.ShenzhenStatus
	} else {
		flow, estimate, actual, model = snapshot.Flow, snapshot.Estimate, snapshot.ActualSettlement, snapshot.Model
		row.Status = snapshot.Status
	}
	if snapshot.Reference == nil || flow == nil || estimate == nil || !validRate(snapshot.Reference.MidRate) || !validRate(estimate.PredictedBuySettlement) || !validRate(estimate.PredictedSellSettlement) {
		return CloseAuditRow{}, false
	}
	row.ModelVersion = model.Version
	row.CalibrationStatus = model.CalibrationStatus
	row.ResidualHKDCNY = model.Residual
	row.ResidualSamples = model.ResidualSamples
	row.ReferenceMid = snapshot.Reference.MidRate
	row.BuyAmountHKD100 = flow.BuyAmountHKD100
	row.SellAmountHKD100 = flow.SellAmountHKD100
	row.NetRatio = estimate.NetRatio
	row.HKDCNYBid = cloneFloat(snapshot.FX.Bid)
	row.HKDCNYAsk = cloneFloat(snapshot.FX.Ask)
	row.SelectedHKDCNY = estimate.SelectedHKDCNY
	if snapshot.FX.Bid != nil && snapshot.FX.Ask != nil {
		row.CFETSHKDCNY1600 = floatPointer((*snapshot.FX.Bid + *snapshot.FX.Ask) / 2)
	}
	row.PredictedBuySettlement = estimate.PredictedBuySettlement
	row.PredictedSellSettlement = estimate.PredictedSellSettlement
	if actual != nil && actual.ValidDate == row.TradeDate {
		applyOfficialSettlement(&row, *actual)
	}
	return row, true
}

func applyOfficialSettlement(row *CloseAuditRow, actual SettlementRate) {
	if !validRate(actual.BuyRate) || !validRate(actual.SellRate) {
		return
	}
	row.ActualBuySettlement = floatPointer(actual.BuyRate)
	row.ActualSellSettlement = floatPointer(actual.SellRate)
	row.OfficialSource = actual.Source
	row.OfficialFetchedAt = cloneTime(&actual.FetchedAt)
	buyError := (row.PredictedBuySettlement/actual.BuyRate - 1) * 10000
	sellError := (row.PredictedSellSettlement/actual.SellRate - 1) * 10000
	meanAbsolute := (math.Abs(buyError) + math.Abs(sellError)) / 2
	row.BuyErrorBP = &buyError
	row.SellErrorBP = &sellError
	row.MeanAbsoluteErrorBP = &meanAbsolute
}

var legacyCloseAuditFields = []string{
	"market", "trade_date", "captured_at", "capture_kind", "status", "model_version", "calibration_status", "residual_hkd_cnh", "residual_samples",
	"reference_mid", "buy_amount_hkd_100m", "sell_amount_hkd_100m", "net_ratio", "cnh_hkd_bid", "cnh_hkd_ask", "selected_hkd_cnh",
	"predicted_buy_settlement", "predicted_sell_settlement", "actual_buy_settlement", "actual_sell_settlement", "official_source", "official_fetched_at",
	"buy_error_bp", "sell_error_bp", "mean_absolute_error_bp",
}

var closeAuditFields = []string{
	"market", "trade_date", "captured_at", "capture_kind", "status", "model_version", "calibration_status", "residual_hkd_cny", "residual_samples",
	"reference_mid", "buy_amount_hkd_100m", "sell_amount_hkd_100m", "net_ratio", "hkd_cny_bid", "hkd_cny_ask", "cfets_hkd_cny_1600", "selected_hkd_cny",
	"predicted_buy_settlement", "predicted_sell_settlement", "actual_buy_settlement", "actual_sell_settlement", "official_source", "official_fetched_at",
	"buy_error_bp", "sell_error_bp", "mean_absolute_error_bp",
}

func (s *Service) readCloseAudits(market string) ([]CloseAuditRow, error) {
	directory := filepath.Join(s.dataDir, "close_audit", market)
	years, err := os.ReadDir(directory)
	if errors.Is(err, os.ErrNotExist) {
		return []CloseAuditRow{}, nil
	}
	if err != nil {
		return nil, err
	}
	rows := make([]CloseAuditRow, 0, 64)
	for _, entry := range years {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".csv") {
			continue
		}
		path := filepath.Join(directory, entry.Name())
		stored, readErr := readCloseAuditCSV(path)
		if readErr != nil {
			return nil, fmt.Errorf("read %s: %w", path, readErr)
		}
		rows = append(rows, stored...)
	}
	return rows, nil
}

func (s *Service) reconcileCloseAudits(market string, settlements []SettlementRate) error {
	if len(settlements) == 0 {
		return nil
	}
	actuals := make(map[string]SettlementRate, len(settlements))
	for _, settlement := range settlements {
		actuals[settlement.ValidDate] = settlement
	}
	directory := filepath.Join(s.dataDir, "close_audit", market)
	files, err := os.ReadDir(directory)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, file := range files {
		if file.IsDir() || !strings.HasSuffix(file.Name(), ".csv") {
			continue
		}
		path := filepath.Join(directory, file.Name())
		rows, readErr := readCloseAuditCSV(path)
		if readErr != nil {
			return fmt.Errorf("read %s: %w", path, readErr)
		}
		changed := false
		for index := range rows {
			actual, found := actuals[rows[index].TradeDate]
			if !found || (rows[index].ActualBuySettlement != nil && rows[index].ActualSellSettlement != nil) {
				continue
			}
			applyOfficialSettlement(&rows[index], actual)
			changed = true
		}
		if changed {
			if writeErr := writeCloseAuditCSV(path, rows); writeErr != nil {
				return writeErr
			}
		}
	}
	return nil
}

func upsertCloseAuditCSV(path string, row CloseAuditRow) error {
	rows, err := readCloseAuditCSV(path)
	if errors.Is(err, os.ErrNotExist) {
		rows = []CloseAuditRow{}
	} else if err != nil {
		return err
	}
	updated := false
	for index := range rows {
		if rows[index].TradeDate == row.TradeDate {
			rows[index] = row
			updated = true
			break
		}
	}
	if !updated {
		rows = append(rows, row)
	}
	return writeCloseAuditCSV(path, rows)
}

func writeCloseAuditCSV(path string, rows []CloseAuditRow) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].TradeDate < rows[j].TradeDate })
	temporary, err := os.CreateTemp(filepath.Dir(path), ".close-audit-*.csv")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	writer := csv.NewWriter(temporary)
	if err := writer.Write(closeAuditFields); err != nil {
		temporary.Close()
		return err
	}
	for _, row := range rows {
		if err := writer.Write(closeAuditRecord(row)); err != nil {
			temporary.Close()
			return err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}

func closeAuditRecord(row CloseAuditRow) []string {
	return []string{
		row.Market, row.TradeDate, row.CapturedAt.Format(time.RFC3339Nano), row.CaptureKind, row.Status, row.ModelVersion, row.CalibrationStatus,
		strconv.FormatFloat(row.ResidualHKDCNY, 'f', 12, 64), strconv.Itoa(row.ResidualSamples), strconv.FormatFloat(row.ReferenceMid, 'f', 12, 64),
		strconv.FormatFloat(row.BuyAmountHKD100, 'f', 12, 64), strconv.FormatFloat(row.SellAmountHKD100, 'f', 12, 64), strconv.FormatFloat(row.NetRatio, 'f', 12, 64),
		formatFloat(row.HKDCNYBid), formatFloat(row.HKDCNYAsk), formatFloat(row.CFETSHKDCNY1600), strconv.FormatFloat(row.SelectedHKDCNY, 'f', 12, 64),
		strconv.FormatFloat(row.PredictedBuySettlement, 'f', 12, 64), strconv.FormatFloat(row.PredictedSellSettlement, 'f', 12, 64),
		formatFloat(row.ActualBuySettlement), formatFloat(row.ActualSellSettlement), row.OfficialSource, formatOptionalTime(row.OfficialFetchedAt),
		formatFloat(row.BuyErrorBP), formatFloat(row.SellErrorBP), formatFloat(row.MeanAbsoluteErrorBP),
	}
}

func readCloseAuditCSV(path string) ([]CloseAuditRow, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	reader := csv.NewReader(file)
	header, err := reader.Read()
	if err != nil {
		return nil, err
	}
	legacy := sameCSVFields(header, legacyCloseAuditFields)
	if !legacy && !sameCSVFields(header, closeAuditFields) {
		return nil, fmt.Errorf("unexpected close audit CSV header")
	}
	rows := make([]CloseAuditRow, 0, 64)
	for {
		record, readErr := reader.Read()
		if errors.Is(readErr, io.EOF) {
			break
		}
		if readErr != nil {
			return nil, readErr
		}
		if len(record) != len(header) {
			return nil, fmt.Errorf("unexpected close audit CSV row width")
		}
		row, parseErr := parseCloseAuditRecord(record, legacy)
		if parseErr != nil {
			return nil, parseErr
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func parseCloseAuditRecord(record []string, legacy bool) (CloseAuditRow, error) {
	capturedAt, err := time.Parse(time.RFC3339Nano, record[2])
	if err != nil {
		return CloseAuditRow{}, fmt.Errorf("invalid audit captured_at: %w", err)
	}
	selectedIndex, predictedBuyIndex, predictedSellIndex := 16, 17, 18
	actualBuyIndex, actualSellIndex, sourceIndex, fetchedIndex := 19, 20, 21, 22
	buyErrorIndex, sellErrorIndex, meanErrorIndex := 23, 24, 25
	if legacy {
		selectedIndex, predictedBuyIndex, predictedSellIndex = 15, 16, 17
		actualBuyIndex, actualSellIndex, sourceIndex, fetchedIndex = 18, 19, 20, 21
		buyErrorIndex, sellErrorIndex, meanErrorIndex = 22, 23, 24
	}
	values := make([]float64, 8)
	for index, recordIndex := range []int{7, 9, 10, 11, 12, selectedIndex, predictedBuyIndex, predictedSellIndex} {
		value, parseErr := strconv.ParseFloat(record[recordIndex], 64)
		if parseErr != nil || math.IsNaN(value) || math.IsInf(value, 0) {
			return CloseAuditRow{}, fmt.Errorf("invalid audit value %q", record[recordIndex])
		}
		values[index] = value
	}
	residualSamples, err := strconv.Atoi(record[8])
	if err != nil || residualSamples < 0 {
		return CloseAuditRow{}, fmt.Errorf("invalid audit residual samples %q", record[8])
	}
	officialFetchedAt, err := parseOptionalTime(record[fetchedIndex])
	if err != nil {
		return CloseAuditRow{}, err
	}
	row := CloseAuditRow{
		Market: record[0], TradeDate: record[1], CapturedAt: capturedAt, CaptureKind: record[3], Status: record[4], ModelVersion: record[5], CalibrationStatus: record[6],
		ResidualHKDCNY: values[0], ResidualSamples: residualSamples, ReferenceMid: values[1], BuyAmountHKD100: values[2], SellAmountHKD100: values[3], NetRatio: values[4],
		SelectedHKDCNY: values[5], PredictedBuySettlement: values[6], PredictedSellSettlement: values[7],
		ActualBuySettlement: parseOptionalFloat(record[actualBuyIndex]), ActualSellSettlement: parseOptionalFloat(record[actualSellIndex]), OfficialSource: record[sourceIndex], OfficialFetchedAt: officialFetchedAt,
		BuyErrorBP: parseOptionalFloat(record[buyErrorIndex]), SellErrorBP: parseOptionalFloat(record[sellErrorIndex]), MeanAbsoluteErrorBP: parseOptionalFloat(record[meanErrorIndex]),
	}
	if !legacy {
		row.HKDCNYBid = parseOptionalFloat(record[13])
		row.HKDCNYAsk = parseOptionalFloat(record[14])
		row.CFETSHKDCNY1600 = parseOptionalFloat(record[15])
	}
	return row, nil
}

func formatOptionalTime(value *time.Time) string {
	if value == nil || value.IsZero() {
		return ""
	}
	return value.Format(time.RFC3339Nano)
}

func parseOptionalTime(value string) (*time.Time, error) {
	if strings.TrimSpace(value) == "" {
		return nil, nil
	}
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return nil, fmt.Errorf("invalid audit official_fetched_at: %w", err)
	}
	return &parsed, nil
}

func floatPointer(value float64) *float64 { return &value }
func optionalValidRate(value float64) *float64 {
	if !validRate(value) {
		return nil
	}
	return floatPointer(value)
}
func cloneFloat(value *float64) *float64 {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

var legacyMinuteFields = []string{
	"timestamp", "trade_date", "status", "actionable", "reference_mid",
	"flow_buy_amount_hkd_100m", "flow_sell_amount_hkd_100m", "flow_total_amount_hkd_100m", "net_ratio",
	"cnh_hkd_bid", "cnh_hkd_ask", "selected_hkd_cnh", "predicted_buy_settlement", "predicted_sell_settlement",
	"buy_change_vs_previous", "sell_change_vs_previous", "flow_fetched_at", "flow_last_changed_at", "ib_observed_at",
}

var legacyMinuteFieldsV4 = append(append([]string{}, legacyMinuteFields...), "ib_bid_hkd_cnh", "buy_live_vs_estimate", "sell_live_vs_estimate")

var minuteFields = []string{
	"timestamp", "trade_date", "status", "actionable", "reference_mid",
	"flow_buy_amount_hkd_100m", "flow_sell_amount_hkd_100m", "flow_total_amount_hkd_100m", "net_ratio",
	"hkd_cny_bid", "hkd_cny_ask", "selected_hkd_cny", "predicted_buy_settlement", "predicted_sell_settlement",
	"buy_change_vs_previous", "sell_change_vs_previous", "flow_fetched_at", "flow_last_changed_at", "fx_observed_at",
	"market_hkd_cny", "buy_live_vs_estimate", "sell_live_vs_estimate",
}

func appendMinuteCSV(path string, point MinutePoint) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if last, err := lastCSVTimestamp(path); err == nil && last == point.Timestamp[:16] {
		return nil
	}
	fields := minuteFields
	if info, err := os.Stat(path); err == nil && info.Size() > 0 {
		existing, readErr := readCSVHeader(path)
		if readErr != nil {
			return readErr
		}
		switch {
		case sameCSVFields(existing, minuteFields):
		case sameCSVFields(existing, legacyMinuteFieldsV4):
			fields = legacyMinuteFieldsV4
		case sameCSVFields(existing, legacyMinuteFields):
			// Preserve an in-progress legacy day rather than corrupting the
			// established file.  New fields start with the next trading day.
			fields = legacyMinuteFields
		default:
			return fmt.Errorf("unexpected minute CSV header")
		}
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	writer := csv.NewWriter(file)
	if info.Size() == 0 {
		if err := writer.Write(fields); err != nil {
			return err
		}
	}
	record := []string{
		point.Timestamp, point.TradeDate, point.Status, strconv.FormatBool(point.Actionable), formatFloat(point.ReferenceMid),
		formatFloat(point.FlowBuyAmountHKD100), formatFloat(point.FlowSellAmountHKD100), formatFloat(point.FlowTotalAmountHKD100), formatFloat(point.NetRatio),
		formatFloat(point.HKDCNYBid), formatFloat(point.HKDCNYAsk), formatFloat(point.SelectedHKDCNY), formatFloat(point.PredictedBuySettlement), formatFloat(point.PredictedSellSettlement),
		formatFloat(point.BuyChangeVsPrevious), formatFloat(point.SellChangeVsPrevious), point.FlowFetchedAt, point.FlowLastChangedAt, point.FXObservedAt,
		formatFloat(point.MarketHKDCNY), formatFloat(point.BuyLiveVsEstimate), formatFloat(point.SellLiveVsEstimate),
	}
	if sameCSVFields(fields, legacyMinuteFields) || sameCSVFields(fields, legacyMinuteFieldsV4) {
		legacyRecord := append([]string{}, record...)
		legacyRecord[9], legacyRecord[10], legacyRecord[11], legacyRecord[18], legacyRecord[19] = "", "", "", "", ""
		record = legacyRecord[:len(fields)]
	}
	if err := writer.Write(record); err != nil {
		return err
	}
	writer.Flush()
	return writer.Error()
}

func readCSVHeader(path string) ([]string, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	return csv.NewReader(file).Read()
}

func sameCSVFields(left, right []string) bool {
	return strings.Join(left, "\x00") == strings.Join(right, "\x00")
}

func lastCSVTimestamp(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	last := ""
	for scanner.Scan() {
		line := scanner.Text()
		if strings.TrimSpace(line) != "" {
			last = line
		}
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}
	if last == "" || strings.HasPrefix(last, "timestamp,") {
		return "", nil
	}
	reader := csv.NewReader(strings.NewReader(last))
	record, err := reader.Read()
	if err != nil || len(record) == 0 {
		return "", err
	}
	if len(record[0]) >= 16 {
		return record[0][:16], nil
	}
	return record[0], nil
}

func readMinuteCSV(path string) ([]MinutePoint, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	reader := csv.NewReader(file)
	header, err := reader.Read()
	if err != nil {
		return nil, err
	}
	legacyV3 := sameCSVFields(header, legacyMinuteFields)
	legacyV4 := sameCSVFields(header, legacyMinuteFieldsV4)
	if !legacyV3 && !legacyV4 && !sameCSVFields(header, minuteFields) {
		return nil, fmt.Errorf("unexpected minute CSV header")
	}
	rows := make([]MinutePoint, 0, 420)
	for {
		record, err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, err
		}
		if len(record) != len(header) {
			return nil, fmt.Errorf("unexpected minute CSV row width")
		}
		point := MinutePoint{Timestamp: record[0], TradeDate: record[1], Status: record[2], Actionable: record[3] == "true", ReferenceMid: parseOptionalFloat(record[4]), FlowBuyAmountHKD100: parseOptionalFloat(record[5]), FlowSellAmountHKD100: parseOptionalFloat(record[6]), FlowTotalAmountHKD100: parseOptionalFloat(record[7]), NetRatio: parseOptionalFloat(record[8]), PredictedBuySettlement: parseOptionalFloat(record[12]), PredictedSellSettlement: parseOptionalFloat(record[13]), BuyChangeVsPrevious: parseOptionalFloat(record[14]), SellChangeVsPrevious: parseOptionalFloat(record[15]), FlowFetchedAt: record[16], FlowLastChangedAt: record[17]}
		if !legacyV3 && !legacyV4 {
			point.HKDCNYBid = parseOptionalFloat(record[9])
			point.HKDCNYAsk = parseOptionalFloat(record[10])
			point.SelectedHKDCNY = parseOptionalFloat(record[11])
			point.FXObservedAt = record[18]
			point.MarketHKDCNY = parseOptionalFloat(record[19])
		}
		if !legacyV3 {
			point.BuyLiveVsEstimate = parseOptionalFloat(record[20])
			point.SellLiveVsEstimate = parseOptionalFloat(record[21])
		}
		rows = append(rows, point)
	}
	return rows, nil
}

func formatFloat(value *float64) string {
	if value == nil {
		return ""
	}
	return strconv.FormatFloat(*value, 'f', 12, 64)
}

func parseOptionalFloat(value string) *float64 {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil {
		return nil
	}
	return &parsed
}

func (s *Service) fetchOverview(ctx context.Context, day string, fetchedAt time.Time) (*Flow, error) {
	target := HKEXTurnoverFeed + "?_=" + strconv.FormatInt(fetchedAt.UnixMilli(), 10)
	body, err := s.getText(ctx, target, HKEXMarketPage)
	if err != nil {
		return nil, err
	}
	flow, err := parseHKEXTurnoverScript(body, fetchedAt)
	if err != nil {
		return nil, err
	}
	if flow.TradeDate != day {
		return nil, fmt.Errorf("HKEX returned trade date %q, want %q", flow.TradeDate, day)
	}
	return flow, nil
}

func parseHKEXTurnoverScript(body string, fetchedAt time.Time) (*Flow, error) {
	start := strings.Index(body, "[")
	end := strings.LastIndex(body, "]")
	if start < 0 || end <= start {
		return nil, fmt.Errorf("HKEX turnover response does not contain an array")
	}
	var payload []struct {
		Section []struct {
			Subtitle []any   `json:"subtitle"`
			Item     [][]any `json:"item"`
		} `json:"section"`
	}
	if err := json.Unmarshal([]byte(body[start:end+1]), &payload); err != nil {
		return nil, fmt.Errorf("decode HKEX turnover: %w", err)
	}
	if len(payload) == 0 || len(payload[0].Section) == 0 {
		return nil, fmt.Errorf("HKEX turnover response has no section")
	}
	section := payload[0].Section[0]
	if len(section.Subtitle) < 2 {
		return nil, fmt.Errorf("HKEX turnover response has no source timestamp")
	}
	publishedAt, err := time.ParseInLocation("02/01/2006 (15:04)", strings.TrimSpace(fmt.Sprint(section.Subtitle[1])), shanghai)
	if err != nil {
		return nil, fmt.Errorf("parse HKEX turnover timestamp: %w", err)
	}
	values := make(map[string]float64, 3)
	for _, item := range section.Item {
		if len(item) < 2 {
			continue
		}
		label := strings.ToLower(strings.TrimSpace(fmt.Sprint(item[0])))
		amount, amountErr := parseHKEXAmountHKD100(fmt.Sprint(item[1]))
		if amountErr != nil {
			return nil, fmt.Errorf("%s: %w", label, amountErr)
		}
		values[label] = amount
	}
	buy, buyOK := values["buy trades"]
	sell, sellOK := values["sell trades"]
	total, totalOK := values["buy and sell trades"]
	if !buyOK || !sellOK || !totalOK {
		return nil, fmt.Errorf("HKEX turnover response is missing buy, sell, or total")
	}
	// HKEX publishes integer HKD millions, so the independently rounded total
	// can differ from rounded buy+sell by a few million.
	if math.Abs(buy+sell-total) > 0.05 {
		return nil, fmt.Errorf("HKEX buy + sell does not match total")
	}
	return &Flow{
		TradeDate:         publishedAt.Format("2006-01-02"),
		BuyAmountHKD100:   buy,
		SellAmountHKD100:  sell,
		TotalAmountHKD100: total,
		PublishedAt:       publishedAt,
		FetchedAt:         fetchedAt,
		Source:            HKEXTurnoverFeed,
	}, nil
}

func parseHKEXAmountHKD100(text string) (float64, error) {
	text = strings.TrimSpace(text)
	if !strings.HasPrefix(text, "HK$") {
		return 0, fmt.Errorf("invalid HKEX HKD amount %q", text)
	}
	fields := strings.Fields(strings.TrimSpace(strings.TrimPrefix(text, "HK$")))
	if len(fields) == 0 {
		return 0, fmt.Errorf("invalid HKEX HKD amount %q", text)
	}
	value, err := strconv.ParseFloat(strings.ReplaceAll(fields[0], ",", ""), 64)
	if err != nil || !validRateOrZero(value) {
		return 0, fmt.Errorf("invalid HKEX HKD amount %q", text)
	}
	multiplier := 1.0
	if len(fields) > 1 {
		switch strings.ToLower(fields[1]) {
		case "thou", "thousand", "k":
			multiplier = 1e3
		case "mil", "million", "m":
			multiplier = 1e6
		case "bil", "billion", "b":
			multiplier = 1e9
		case "tril", "trillion", "t":
			multiplier = 1e12
		default:
			return 0, fmt.Errorf("unsupported HKEX amount unit %q", fields[1])
		}
	}
	return value * multiplier / 1e8, nil
}

type eastmoneySouthboundResponse struct {
	RC   int
	Data struct {
		N2SDate string
		N2S     []string
	}
}

// fetchSouthboundFlows reads Eastmoney's one-minute southbound feed. Its
// field order is: time, SH net, SH buy, SZ net, SH sell, total net, SZ buy,
// SZ sell, total buy, total sell. Values are in ten-thousand HKD; this
// converts them to hundred-million HKD for the estimator.
func (s *Service) fetchSouthboundFlows(ctx context.Context, day string, fetchedAt time.Time) (*Flow, *Flow, error) {
	var payload eastmoneySouthboundResponse
	target := EastmoneySouthboundFeed + "&_=" + strconv.FormatInt(fetchedAt.UnixMilli(), 10)
	if err := s.getJSON(ctx, target, EastmoneySouthboundPage, &payload); err != nil {
		return nil, nil, err
	}
	shanghaiFlow, shenzhenFlow, err := parseEastmoneySouthbound(payload, fetchedAt)
	if err != nil {
		return nil, nil, err
	}
	if shanghaiFlow.TradeDate != day || shenzhenFlow.TradeDate != day {
		return nil, nil, fmt.Errorf("Eastmoney returned trade date %q/%q, want %q", shanghaiFlow.TradeDate, shenzhenFlow.TradeDate, day)
	}
	return shanghaiFlow, shenzhenFlow, nil
}

func parseEastmoneySouthbound(payload eastmoneySouthboundResponse, fetchedAt time.Time) (*Flow, *Flow, error) {
	if payload.RC != 0 {
		return nil, nil, fmt.Errorf("Eastmoney response rc=%d", payload.RC)
	}
	day := strings.TrimSpace(payload.Data.N2SDate)
	if _, err := time.ParseInLocation("2006-01-02", day, shanghai); err != nil {
		return nil, nil, fmt.Errorf("invalid Eastmoney trade date %q", day)
	}
	for index := len(payload.Data.N2S) - 1; index >= 0; index-- {
		fields := strings.Split(payload.Data.N2S[index], ",")
		if len(fields) < 10 {
			continue
		}
		values := make([]float64, len(fields))
		valid := true
		for _, fieldIndex := range []int{2, 4, 6, 7, 8, 9} {
			value, err := strconv.ParseFloat(strings.TrimSpace(fields[fieldIndex]), 64)
			if err != nil || !validRateOrZero(value) {
				valid = false
				break
			}
			values[fieldIndex] = value / 1e4
		}
		if !valid {
			continue
		}
		minute := strings.TrimSpace(fields[0])
		if len(minute) == 4 {
			minute = "0" + minute
		}
		publishedAt, err := time.ParseInLocation("2006-01-02 15:04", day+" "+minute, shanghai)
		if err != nil {
			continue
		}
		shanghaiBuy, shanghaiSell := values[2], values[4]
		shenzhenBuy, shenzhenSell := values[6], values[7]
		totalBuy, totalSell := values[8], values[9]
		if math.Abs((shanghaiBuy+shenzhenBuy)-totalBuy) > 0.02 || math.Abs((shanghaiSell+shenzhenSell)-totalSell) > 0.02 {
			return nil, nil, fmt.Errorf("Eastmoney SH/SZ totals do not reconcile")
		}
		return &Flow{
				Market: marketShanghai, TradeDate: day,
				BuyAmountHKD100: shanghaiBuy, SellAmountHKD100: shanghaiSell, TotalAmountHKD100: shanghaiBuy + shanghaiSell,
				PublishedAt: publishedAt, FetchedAt: fetchedAt, Source: EastmoneySouthboundFeed,
			}, &Flow{
				Market: marketShenzhen, TradeDate: day,
				BuyAmountHKD100: shenzhenBuy, SellAmountHKD100: shenzhenSell, TotalAmountHKD100: shenzhenBuy + shenzhenSell,
				PublishedAt: publishedAt, FetchedAt: fetchedAt, Source: EastmoneySouthboundFeed,
			}, nil
	}
	return nil, nil, errors.New("Eastmoney response has no usable southbound minute row")
}

type rateRow struct {
	ValidDate     string
	PublishedDate string
	Buy           float64
	Sell          float64
	FetchedAt     time.Time
}

func (s *Service) fetchReferenceRates(ctx context.Context, start, end time.Time) ([]rateRow, error) {
	return s.fetchRates(ctx, referenceSQLID, start, end)
}

func (s *Service) fetchSettlementRates(ctx context.Context, start, end time.Time) ([]rateRow, error) {
	return s.fetchRates(ctx, settlementSQLID, start, end)
}

// fetchShenzhenSettlementRates uses the SZSE's published "结算汇兑比率"
// report tab.  Its reference-rate tab is deliberately not used here: the
// common reference midpoint continues to come from SSE, while the two markets
// retain their independently confirmed final settlement rates.
func (s *Service) fetchShenzhenSettlementRates(ctx context.Context, start, end time.Time) ([]rateRow, error) {
	query := url.Values{
		"SHOWTYPE":  []string{"JSON"},
		"CATALOGID": []string{szseSettlementCatalogID},
		"TABKEY":    []string{szseSettlementTabKey},
		"txtStart":  []string{start.Format("2006-01-02")},
		"txtEnd":    []string{end.Format("2006-01-02")},
	}
	var payload []struct {
		Metadata struct {
			TabKey string `json:"tabkey"`
		} `json:"metadata"`
		Data []struct {
			ValidDate string `json:"sxrq"`
			BuyRate   string `json:"mrhl"`
			SellRate  string `json:"mchl"`
		} `json:"data"`
	}
	fetchedAt := s.now().In(shanghai)
	if err := s.getJSON(ctx, szseRatesEndpoint+"?"+query.Encode(), SZSERatesPage, &payload); err != nil {
		return nil, err
	}
	for _, report := range payload {
		if report.Metadata.TabKey != szseSettlementTabKey {
			continue
		}
		rows := make([]rateRow, 0, len(report.Data))
		for _, raw := range report.Data {
			validDate, err := normalizeSSEDate(raw.ValidDate)
			if err != nil {
				return nil, fmt.Errorf("invalid SZSE settlement date: %w", err)
			}
			buy, err := parsePublishedRate(raw.BuyRate)
			if err != nil {
				return nil, fmt.Errorf("invalid SZSE buy settlement rate: %w", err)
			}
			sell, err := parsePublishedRate(raw.SellRate)
			if err != nil {
				return nil, fmt.Errorf("invalid SZSE sell settlement rate: %w", err)
			}
			if buy <= 0 || sell <= 0 {
				return nil, errors.New("SZSE returned non-positive settlement rate")
			}
			rows = append(rows, rateRow{ValidDate: validDate, PublishedDate: validDate, Buy: buy, Sell: sell, FetchedAt: fetchedAt})
		}
		return rows, nil
	}
	return nil, errors.New("SZSE response is missing the settlement-rate tab")
}

func settlementRatesFromRows(rows []rateRow, source string) []SettlementRate {
	latest := make(map[string]rateRow, len(rows))
	for _, row := range rows {
		if row.ValidDate == "" {
			continue
		}
		current, exists := latest[row.ValidDate]
		if !exists || row.PublishedDate >= current.PublishedDate {
			latest[row.ValidDate] = row
		}
	}
	history := make([]SettlementRate, 0, len(latest))
	for _, row := range latest {
		history = append(history, SettlementRate{
			ValidDate: row.ValidDate,
			BuyRate:   row.Buy,
			SellRate:  row.Sell,
			FetchedAt: row.FetchedAt,
			Source:    source,
		})
	}
	sort.Slice(history, func(i, j int) bool { return history[i].ValidDate < history[j].ValidDate })
	return history
}

// loadSettlementHistoryCSV reads the checked-in historical settlement export.
// The public SSE/SZSE endpoints only return a short trailing window, so this
// file supplies the older rows needed by the interactive long-history chart.
func loadSettlementHistoryCSV(path, source string) ([]SettlementRate, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	reader := csv.NewReader(file)
	if _, err := reader.Read(); err != nil {
		return nil, err
	}
	rows := make(map[string]SettlementRate)
	for {
		record, err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, err
		}
		if len(record) < 3 {
			return nil, fmt.Errorf("settlement history row has %d fields, want at least 3", len(record))
		}
		validDate, err := normalizeSSEDate(strings.TrimPrefix(strings.TrimSpace(record[0]), "\ufeff"))
		if err != nil {
			return nil, fmt.Errorf("invalid settlement history date: %w", err)
		}
		buy, err := parsePublishedRate(record[1])
		if err != nil || buy <= 0 {
			return nil, fmt.Errorf("invalid settlement history buy rate for %s", validDate)
		}
		sell, err := parsePublishedRate(record[2])
		if err != nil || sell <= 0 {
			return nil, fmt.Errorf("invalid settlement history sell rate for %s", validDate)
		}
		rows[validDate] = SettlementRate{ValidDate: validDate, BuyRate: buy, SellRate: sell, Source: source}
	}
	history := make([]SettlementRate, 0, len(rows))
	for _, row := range rows {
		history = append(history, row)
	}
	sort.Slice(history, func(i, j int) bool { return history[i].ValidDate < history[j].ValidDate })
	return history, nil
}

// mergeSettlementHistories preserves older persisted history and lets fresh
// exchange responses replace overlapping dates.
func mergeSettlementHistories(existing, fresh []SettlementRate) []SettlementRate {
	byDate := make(map[string]SettlementRate, len(existing)+len(fresh))
	for _, row := range existing {
		if row.ValidDate != "" {
			byDate[row.ValidDate] = row
		}
	}
	for _, row := range fresh {
		if row.ValidDate != "" {
			byDate[row.ValidDate] = row
		}
	}
	merged := make([]SettlementRate, 0, len(byDate))
	for _, row := range byDate {
		merged = append(merged, row)
	}
	sort.Slice(merged, func(i, j int) bool { return merged[i].ValidDate < merged[j].ValidDate })
	return merged
}

func (s *Service) fetchRates(ctx context.Context, sqlID string, start, end time.Time) ([]rateRow, error) {
	query := url.Values{
		"isPagination": []string{"true"}, "updateDate": []string{start.Format("20060102")}, "updateDateEnd": []string{end.Format("20060102")},
		"sqlId": []string{sqlID}, "pageHelp.cacheSize": []string{"1"}, "pageHelp.pageSize": []string{"100"}, "pageHelp.pageNo": []string{"1"}, "pageHelp.beginPage": []string{"1"}, "pageHelp.endPage": []string{"1"},
	}
	var payload struct {
		ActionErrors any              `json:"actionErrors"`
		Result       []map[string]any `json:"result"`
	}
	fetchedAt := s.now().In(shanghai)
	if err := s.getJSON(ctx, sseRatesEndpoint+"?"+query.Encode(), SSERatesPage, &payload); err != nil {
		return nil, err
	}
	if hasActionErrors(payload.ActionErrors) {
		return nil, fmt.Errorf("SSE actionErrors: %v", payload.ActionErrors)
	}
	rows := make([]rateRow, 0, len(payload.Result))
	for _, raw := range payload.Result {
		validDate, err := normalizeSSEDate(fmt.Sprint(raw["validDate"]))
		if err != nil {
			return nil, err
		}
		publishedDate, _ := normalizeSSEDate(fmt.Sprint(raw["updateDate"]))
		buy, err := mapFloat(raw, "buyPrice")
		if err != nil {
			return nil, err
		}
		sell, err := mapFloat(raw, "sellPrice")
		if err != nil {
			return nil, err
		}
		if buy <= 0 || sell <= 0 {
			return nil, fmt.Errorf("SSE returned non-positive rate")
		}
		rows = append(rows, rateRow{ValidDate: validDate, PublishedDate: publishedDate, Buy: buy, Sell: sell, FetchedAt: fetchedAt})
	}
	return rows, nil
}

func (s *Service) getJSON(ctx context.Context, target, referer string, destination any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return err
	}
	request.Header.Set("Accept", "application/json, text/plain, */*")
	request.Header.Set("Referer", referer)
	request.Header.Set("User-Agent", "Mozilla/5.0 (compatible; NewNavNav-HKConnectFX/1.0)")
	response, err := s.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", response.StatusCode)
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 4<<20))
	return decoder.Decode(destination)
}

func (s *Service) getText(ctx context.Context, target, referer string) (string, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return "", err
	}
	request.Header.Set("Accept", "text/javascript, application/javascript, text/plain, */*")
	request.Header.Set("Referer", referer)
	request.Header.Set("User-Agent", "Mozilla/5.0 (compatible; NewNavNav-HKConnectFX/1.0)")
	response, err := s.httpClient.Do(request)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("HTTP %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return "", err
	}
	return string(body), nil
}

func mapFloat(row map[string]any, key string) (float64, error) {
	value, err := parsePublishedRate(fmt.Sprint(row[key]))
	if err != nil {
		return 0, fmt.Errorf("invalid SSE field %s: %w", key, err)
	}
	return value, nil
}

func parsePublishedRate(raw string) (float64, error) {
	text := strings.ReplaceAll(strings.TrimSpace(raw), ",", "")
	value, err := strconv.ParseFloat(text, 64)
	if err != nil || !validRateOrZero(value) {
		return 0, fmt.Errorf("invalid rate %q", text)
	}
	return value, nil
}

func hasActionErrors(value any) bool {
	if value == nil {
		return false
	}
	if list, ok := value.([]any); ok {
		return len(list) > 0
	}
	if fields, ok := value.(map[string]any); ok {
		return len(fields) > 0
	}
	return strings.TrimSpace(fmt.Sprint(value)) != ""
}

func validRateOrZero(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0
}

func normalizeSSEDate(value string) (string, error) {
	value = strings.TrimSpace(value)
	for _, layout := range []string{"20060102", "2006-01-02"} {
		if parsed, err := time.Parse(layout, value); err == nil {
			return parsed.Format("2006-01-02"), nil
		}
	}
	return "", fmt.Errorf("invalid SSE date %q", value)
}

func selectReference(rows []rateRow, day string) *ReferenceRate {
	var candidates []rateRow
	for _, row := range rows {
		if row.ValidDate == day {
			candidates = append(candidates, row)
		}
	}
	if len(candidates) == 0 {
		return nil
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].PublishedDate < candidates[j].PublishedDate })
	row := candidates[len(candidates)-1]
	return &ReferenceRate{ValidDate: row.ValidDate, PublishedDate: row.PublishedDate, BuyRate: row.Buy, SellRate: row.Sell, MidRate: (row.Buy + row.Sell) / 2, FetchedAt: row.FetchedAt, Source: SSERatesPage}
}

func selectSettlement(rows []rateRow, day, source string) *SettlementRate {
	for index := len(rows) - 1; index >= 0; index-- {
		row := rows[index]
		if row.ValidDate == day {
			return &SettlementRate{ValidDate: row.ValidDate, BuyRate: row.Buy, SellRate: row.Sell, FetchedAt: row.FetchedAt, Source: source}
		}
	}
	return nil
}

func previousSettlement(rows []rateRow, day, source string) *SettlementRate {
	var candidates []rateRow
	for _, row := range rows {
		if row.ValidDate < day {
			candidates = append(candidates, row)
		}
	}
	if len(candidates) == 0 {
		return nil
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].ValidDate < candidates[j].ValidDate })
	row := candidates[len(candidates)-1]
	return &SettlementRate{ValidDate: row.ValidDate, BuyRate: row.Buy, SellRate: row.Sell, FetchedAt: row.FetchedAt, Source: source}
}
