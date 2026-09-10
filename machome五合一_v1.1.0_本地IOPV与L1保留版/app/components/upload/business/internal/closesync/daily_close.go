package closesync

import (
	"context"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"
	_ "time/tzdata"

	"newnavnav/internal/domain"
	"newnavnav/internal/runtimestatus"
)

type QuoteProvider interface {
	CurrentQuotes() map[string]domain.Quote
}

type Repository interface {
	UpsertDailyPrices(ctx context.Context, prices []domain.DailyPrice) error
}

type Service struct {
	quotes     QuoteProvider
	repository Repository
	logger     *slog.Logger
	markets    []MarketSchedule
	tasks      map[string]*runtimestatus.Task
}

type Options struct {
	Quotes     QuoteProvider
	Repository Repository
	Logger     *slog.Logger
	Markets    []MarketSchedule
}

type MarketSchedule struct {
	Market   string
	Timezone string
	Hour     int
	Minute   int
}

func DefaultMarketSchedules() []MarketSchedule {
	return []MarketSchedule{
		{Market: "cn", Timezone: "Asia/Shanghai", Hour: 15, Minute: 10},
		{Market: "hk", Timezone: "Asia/Hong_Kong", Hour: 16, Minute: 15},
		{Market: "jp", Timezone: "Asia/Tokyo", Hour: 15, Minute: 45},
		{Market: "eu", Timezone: "Europe/Berlin", Hour: 17, Minute: 45},
		{Market: "us_commodity_futures", Timezone: "America/New_York", Hour: 16, Minute: 0},
		{Market: "us", Timezone: "America/New_York", Hour: 16, Minute: 20},
	}
}

func NewService(opts Options) *Service {
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	markets := opts.Markets
	if len(markets) == 0 {
		markets = DefaultMarketSchedules()
	}
	tasks := make(map[string]*runtimestatus.Task, len(markets))
	for _, schedule := range markets {
		tasks[schedule.Market] = runtimestatus.NewTask(
			"daily_close:"+schedule.Market,
			"每日收盘价 "+schedule.Market,
			true,
			fmt.Sprintf("%s %02d:%02d", schedule.Timezone, schedule.Hour, schedule.Minute),
			"",
		)
	}
	return &Service{
		quotes:     opts.Quotes,
		repository: opts.Repository,
		logger:     logger,
		markets:    markets,
		tasks:      tasks,
	}
}

func (s *Service) Run(ctx context.Context) {
	for _, schedule := range s.markets {
		schedule := schedule
		go s.runMarket(ctx, schedule)
	}
}

func (s *Service) runMarket(ctx context.Context, schedule MarketSchedule) {
	task := s.tasks[schedule.Market]
	loc, err := time.LoadLocation(schedule.Timezone)
	if err != nil {
		if task != nil {
			_ = task.Run(ctx, time.Now(), func(context.Context, time.Time) error {
				return err
			})
		}
		s.logger.Warn("daily close schedule disabled; timezone unavailable", "market", schedule.Market, "timezone", schedule.Timezone, "error", err)
		return
	}
	for {
		next := nextRunTime(time.Now(), loc, schedule.Hour, schedule.Minute)
		if task != nil {
			task.SetNextRunAt(next)
		}
		s.logger.Info("daily close persistence scheduled", "market", schedule.Market, "timezone", schedule.Timezone, "next_run", next.Format(time.RFC3339))
		timer := time.NewTimer(time.Until(next))
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
			run := func(ctx context.Context, today time.Time) error {
				return s.PersistMarketClose(ctx, schedule.Market, today)
			}
			if task != nil {
				err = task.Run(ctx, next, run)
			} else {
				err = run(ctx, next)
			}
			if err != nil {
				s.logger.Warn("daily close persistence failed", "market", schedule.Market, "error", err)
			}
		}
	}
}

func (s *Service) Statuses() []runtimestatus.Status {
	statuses := make([]runtimestatus.Status, 0, len(s.markets))
	for _, schedule := range s.markets {
		if task := s.tasks[schedule.Market]; task != nil {
			statuses = append(statuses, task.Status())
		}
	}
	return statuses
}

func (s *Service) PersistMarketClose(ctx context.Context, market string, runAt time.Time) error {
	if s.quotes == nil || s.repository == nil {
		return fmt.Errorf("daily close persistence requires quote provider and repository")
	}
	market = strings.ToLower(strings.TrimSpace(market))
	if isMarketWeekend(market, runAt) {
		s.logger.Info("daily close persistence skipped; market weekend", "market", market, "run_at", runAt.Format(time.RFC3339))
		return nil
	}
	quotes := s.quotes.CurrentQuotes()
	prices := make([]domain.DailyPrice, 0)
	for _, quote := range quotes {
		if domain.IsExcludedSymbol(quote.Symbol) || !shouldPersistSymbolForMarket(market, quote.Symbol) {
			continue
		}
		price, ok := closePriceForQuote(quote)
		if !ok {
			continue
		}
		tradeDate, ok := tradeDateForQuote(market, quote, runAt)
		if !ok {
			continue
		}
		prices = append(prices, domain.DailyPrice{
			Symbol:   quote.Symbol,
			Date:     tradeDate,
			Close:    price,
			AdjClose: price,
			Source:   "sina_daily_close:" + market,
		})
	}
	sort.Slice(prices, func(i, j int) bool {
		return prices[i].Symbol < prices[j].Symbol
	})
	if len(prices) == 0 {
		s.logger.Warn("daily close persistence skipped; no eligible quotes", "market", market)
		return nil
	}
	if err := s.repository.UpsertDailyPrices(ctx, prices); err != nil {
		return err
	}
	s.logger.Info("daily close prices persisted", "market", market, "rows", len(prices), "trade_date", prices[0].Date)
	return nil
}

func isMarketWeekend(market string, runAt time.Time) bool {
	loc := marketLocation(market)
	if loc == nil {
		return false
	}
	weekday := runAt.In(loc).Weekday()
	return weekday == time.Saturday || weekday == time.Sunday
}

func MarketOfSymbol(symbol string) string {
	symbol = strings.TrimSpace(symbol)
	lower := strings.ToLower(symbol)
	switch {
	case len(symbol) == 8 && (strings.HasPrefix(symbol, "SH") || strings.HasPrefix(symbol, "SZ") || strings.HasPrefix(symbol, "BJ")):
		return "cn"
	case strings.HasPrefix(lower, "rt_hk"):
		return "hk"
	case isHKCode(symbol):
		return "hk"
	case strings.HasPrefix(lower, "gb_"):
		return "us"
	case isUSSymbol(symbol):
		return "us"
	case strings.HasPrefix(lower, "znb_nky"), strings.HasPrefix(lower, "znb_tpx"):
		return "jp"
	case strings.HasPrefix(lower, "znb_dax"), strings.HasPrefix(lower, "znb_cac"):
		return "eu"
	default:
		return ""
	}
}

func shouldPersistSymbolForMarket(market string, symbol string) bool {
	if market == "us_commodity_futures" {
		return domain.IsCommodityFutureReferenceSymbol(symbol)
	}
	if domain.IsCommodityFutureReferenceSymbol(symbol) {
		return false
	}
	return MarketOfSymbol(symbol) == market
}

func closePriceForQuote(quote domain.Quote) (float64, bool) {
	if quote.Source == "demo" || quote.Error == "demo fallback" {
		return 0, false
	}
	if strings.EqualFold(quote.Source, "sina_us_after_hours") && quote.PrevClose > 0 {
		return quote.PrevClose, true
	}
	if quote.Price > 0 {
		return quote.Price, true
	}
	if quote.PrevClose > 0 {
		return quote.PrevClose, true
	}
	return 0, false
}

func tradeDateForQuote(market string, quote domain.Quote, runAt time.Time) (string, bool) {
	loc := marketLocation(market)
	if loc == nil {
		return "", false
	}
	localDate := runAt.In(loc).Format("2006-01-02")
	if quote.QuoteDate == "" {
		return localDate, true
	}
	switch market {
	case "us", "us_commodity_futures", "eu":
		return localDate, true
	default:
		return quote.QuoteDate, true
	}
}

func marketLocation(market string) *time.Location {
	name := "Asia/Shanghai"
	switch market {
	case "hk":
		name = "Asia/Hong_Kong"
	case "jp":
		name = "Asia/Tokyo"
	case "eu":
		name = "Europe/Berlin"
	case "us", "us_commodity_futures":
		name = "America/New_York"
	}
	loc, err := time.LoadLocation(name)
	if err != nil {
		return nil
	}
	return loc
}

func nextRunTime(now time.Time, loc *time.Location, hour int, minute int) time.Time {
	localNow := now.In(loc)
	year, month, day := localNow.Date()
	nextLocal := time.Date(year, month, day, hour, minute, 0, 0, loc)
	if !nextLocal.After(localNow) {
		nextLocal = nextLocal.AddDate(0, 0, 1)
	}
	return nextLocal
}

func isHKCode(symbol string) bool {
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

func isUSSymbol(symbol string) bool {
	if len(symbol) == 0 || len(symbol) > 10 {
		return false
	}
	for _, ch := range symbol {
		if (ch < 'A' || ch > 'Z') && (ch < '0' || ch > '9') && ch != '.' {
			return false
		}
	}
	return symbol[0] >= 'A' && symbol[0] <= 'Z'
}
