package snapshot

import (
	"context"
	"regexp"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/valuation"
)

type DailyPriceRequestItem struct {
	Symbol string `json:"symbol"`
	Date   string `json:"date"`
	Market string `json:"market"`
	Reason string `json:"reason,omitempty"`
}

func (s *Service) MissingDailyPriceRequests(ctx context.Context, limit int) ([]DailyPriceRequestItem, error) {
	if s.repository == nil {
		return nil, nil
	}
	data, err := s.repository.LoadValuationData(ctx)
	if err != nil {
		return nil, err
	}
	domain.ApplyStaticValuationData(&data)
	now := time.Now()

	seen := map[string]bool{}
	requests := make([]DailyPriceRequestItem, 0)
	add := func(symbol string, date string, reason string) {
		symbol = strings.TrimSpace(symbol)
		date = strings.TrimSpace(date)
		if symbol == "" || date == "" || domain.IsExcludedSymbol(symbol) || domain.IsIgnoredAuxiliarySymbol(symbol) || domain.IsUnsupportedLiveQuoteSymbol(symbol) {
			return
		}
		if hasDailyPrice(data, symbol, date) {
			return
		}
		key := symbol + "|" + date
		if seen[key] {
			return
		}
		market := basePriceMarket(symbol)
		if market == "" {
			return
		}
		seen[key] = true
		requests = append(requests, DailyPriceRequestItem{
			Symbol: symbol,
			Date:   date,
			Market: market,
			Reason: reason,
		})
	}

	for _, fund := range domain.AllSymbols() {
		nav, ok := valuation.ValuationNetValue(data, fund, now)
		if !ok || nav.Date == "" || nav.NAV <= 0 {
			continue
		}
		if _, ok := domain.WeightedAnchorStrategyForFund(fund); ok {
			continue
		}
		for _, pair := range data.FundPairs[fund] {
			if pair.PairSymbol != "" {
				add(pair.PairSymbol, nav.Date, "fund_pair:"+fund)
			}
		}
		if strategy, ok := domain.CommodityBasketStrategyForFund(fund); ok {
			for _, leg := range strategy.Legs {
				add(leg.Symbol, nav.Date, "commodity_basket:"+fund)
			}
			continue
		}
		if strategy, ok := domain.SingleCommodityFutureStrategyForFund(fund); ok {
			add(strategy.ReferenceSymbol, nav.Date, "single_commodity_future:"+fund)
			continue
		}
		holdingDate, ok := data.CurrentHoldingDates[fund]
		if !ok || holdingDate.Date == "" {
			continue
		}
		for _, holding := range data.Holdings[fund] {
			if holding.HoldingDate != holdingDate.Date || holding.HoldingSymbol == "" {
				continue
			}
			add(holding.HoldingSymbol, nav.Date, "holding:"+fund)
			if reference, ok := domain.CommodityFutureReferenceForHolding(holding.HoldingSymbol); ok {
				add(reference.Symbol, nav.Date, "commodity_reference:"+fund)
			}
		}
	}

	sort.Slice(requests, func(i, j int) bool {
		if requests[i].Date == requests[j].Date {
			return requests[i].Symbol < requests[j].Symbol
		}
		return requests[i].Date < requests[j].Date
	})
	if limit > 0 && len(requests) > limit {
		requests = requests[:limit]
	}
	return requests, nil
}

func (s *Service) UpsertRequestedDailyPrices(ctx context.Context, source string, prices []domain.DailyPrice) (int, error) {
	if s.repository == nil {
		return 0, nil
	}
	valid := make([]domain.DailyPrice, 0, len(prices))
	for _, price := range prices {
		price.Symbol = strings.TrimSpace(price.Symbol)
		price.Date = strings.TrimSpace(price.Date)
		if price.Symbol == "" || price.Date == "" || price.Close <= 0 {
			continue
		}
		if price.AdjClose <= 0 {
			price.AdjClose = price.Close
		}
		if price.Source == "" {
			price.Source = "ws_daily_price:" + strings.TrimSpace(source)
		}
		valid = append(valid, price)
	}
	if len(valid) == 0 {
		return 0, nil
	}
	if err := s.repository.UpsertDailyPrices(ctx, valid); err != nil {
		return 0, err
	}
	s.RecordEvent("info", "daily_price", "daily base prices uploaded", map[string]any{
		"source": strings.TrimSpace(source),
		"count":  len(valid),
	})
	return len(valid), nil
}

func hasDailyPrice(data domain.ValuationData, symbol string, date string) bool {
	byDate := data.DailyPricesByDate[symbol]
	if byDate == nil {
		return false
	}
	price, ok := byDate[date]
	return ok && (price.AdjClose > 0 || price.Close > 0)
}

func basePriceMarket(symbol string) string {
	symbol = strings.TrimSpace(symbol)
	lower := strings.ToLower(symbol)
	upper := strings.ToUpper(symbol)
	switch {
	case domain.IsCommodityFutureReferenceSymbol(symbol):
		return "us_commodity_futures"
	case len(upper) == 8 && (strings.HasPrefix(upper, "SH") || strings.HasPrefix(upper, "SZ") || strings.HasPrefix(upper, "BJ")):
		return "cn"
	case strings.HasPrefix(lower, "rt_hk") || regexp.MustCompile(`^\d{5}$`).MatchString(symbol):
		return "hk"
	case strings.HasPrefix(lower, "znb_nky"), strings.HasPrefix(lower, "znb_tpx"):
		return "jp"
	case strings.HasPrefix(lower, "znb_dax"), strings.HasPrefix(lower, "znb_cac"):
		return "eu"
	case strings.HasSuffix(upper, "-EU"):
		return "eu"
	case strings.HasSuffix(upper, "-JP"):
		return "jp"
	case strings.HasSuffix(upper, "-HK"):
		return "hk"
	case strings.HasPrefix(lower, "gb_"):
		return "us"
	case isUSLikeSymbol(upper):
		return "us"
	default:
		return ""
	}
}

func isUSLikeSymbol(symbol string) bool {
	if symbol == "" || len(symbol) > 10 {
		return false
	}
	for _, ch := range symbol {
		if (ch < 'A' || ch > 'Z') && (ch < '0' || ch > '9') && ch != '.' {
			return false
		}
	}
	return symbol[0] >= 'A' && symbol[0] <= 'Z'
}
