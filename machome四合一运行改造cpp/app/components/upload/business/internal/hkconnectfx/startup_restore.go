package hkconnectfx

import (
	"errors"
	"os"
	"time"
)

// restoreStoredMinuteState replaces post-close network observations with the
// final inputs recorded during the live session. This keeps a deployment after
// 16:00 from changing the day's frozen estimates.
func (s *Service) restoreStoredMinuteState(now time.Time) {
	day := now.In(shanghai).Format("2006-01-02")
	shanghaiRows := s.readStoredMinuteRows(day, marketShanghai)
	shenzhenRows := s.readStoredMinuteRows(day, marketShenzhen)
	shanghaiPoint, shanghaiOK := lastRestorableMinutePoint(shanghaiRows)
	shenzhenPoint, shenzhenOK := lastRestorableMinutePoint(shenzhenRows)
	if !shanghaiOK && !shenzhenOK {
		return
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	if shanghaiOK {
		s.flow = flowFromStoredMinutePoint(shanghaiPoint, marketShanghai)
	}
	if shenzhenOK {
		s.shenzhenFlow = flowFromStoredMinutePoint(shenzhenPoint, marketShenzhen)
	}
	fxPoint := shanghaiPoint
	if !shanghaiOK {
		fxPoint = shenzhenPoint
	}
	s.fx = fxFromStoredMinutePoint(fxPoint)
	if s.fxQuotes == nil {
		s.fxQuotes = initialCFETSSpotQuotes()
	}
	s.fxQuotes[s.fx.Pair] = cloneFX(s.fx)
}

func (s *Service) readStoredMinuteRows(day, market string) []MinutePoint {
	path := s.minutePathForMarket(day, market)
	rows, err := readMinuteCSV(path)
	if err == nil {
		return rows
	}
	if !errors.Is(err, os.ErrNotExist) {
		s.logger.Warn("hk-connect fx startup minute restore failed", "market", market, "path", path, "error", err)
	}
	return nil
}

func lastRestorableMinutePoint(rows []MinutePoint) (MinutePoint, bool) {
	for index := len(rows) - 1; index >= 0; index-- {
		point := rows[index]
		if point.FlowBuyAmountHKD100 != nil && point.FlowSellAmountHKD100 != nil &&
			point.HKDCNYBid != nil && point.HKDCNYAsk != nil {
			return point, true
		}
	}
	return MinutePoint{}, false
}

func flowFromStoredMinutePoint(point MinutePoint, market string) *Flow {
	publishedAt := parseStoredMinuteTime(point.Timestamp, time.Time{})
	fetchedAt := parseStoredMinuteTime(point.FlowFetchedAt, publishedAt)
	lastChangedAt := parseStoredMinuteTime(point.FlowLastChangedAt, fetchedAt)
	total := *point.FlowBuyAmountHKD100 + *point.FlowSellAmountHKD100
	if point.FlowTotalAmountHKD100 != nil {
		total = *point.FlowTotalAmountHKD100
	}
	return &Flow{
		Market:            market,
		TradeDate:         point.TradeDate,
		BuyAmountHKD100:   *point.FlowBuyAmountHKD100,
		SellAmountHKD100:  *point.FlowSellAmountHKD100,
		TotalAmountHKD100: total,
		PublishedAt:       publishedAt,
		FetchedAt:         fetchedAt,
		FirstSeenAt:       fetchedAt,
		LastChangedAt:     lastChangedAt,
		Samples:           1,
		Source:            EastmoneySouthboundFeed,
	}
}

func fxFromStoredMinutePoint(point MinutePoint) FXQuote {
	receivedAt := parseStoredMinuteTime(point.Timestamp, time.Time{})
	observedAt := parseStoredMinuteTime(point.FXObservedAt, receivedAt)
	return FXQuote{
		Pair:       "HKD/CNY",
		Bid:        cloneFloat(point.HKDCNYBid),
		Ask:        cloneFloat(point.HKDCNYAsk),
		Healthy:    true,
		ObservedAt: cloneTime(&observedAt),
		ReceivedAt: cloneTime(&receivedAt),
		Source:     "CFETS_CHINAMONEY",
	}
}

func parseStoredMinuteTime(value string, fallback time.Time) time.Time {
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return fallback
	}
	return parsed.In(shanghai)
}
