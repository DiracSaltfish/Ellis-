package live

import (
	"encoding/json"
	iopv "intranet-iopv"
	"time"
)

// After 16:00 retain the website's explicitly closed estimate through the chart
// endpoint. It remains a frozen reference; never mark it actionable or refresh its as-of.
func SessionFX(raw []byte, date, channel, direction string, now time.Time) (iopv.FX, error) {
	f, err := iopv.ParseFX(raw, date, channel, direction, now)
	if err == nil {
		return f, nil
	}
	var s iopv.FXSnapshot
	if json.Unmarshal(raw, &s) != nil {
		return f, err
	}
	n := now.In(Zone)
	if n.Hour() != 16 || n.Minute() > 8 || s.Date != date || s.Schema != "hk-connect-fx.v7" || s.Parity == nil || s.Parity.Date != date || s.Parity.Pair != "HKD/CNY" || s.Parity.Rate <= 0 || s.Quote.Pair != "HKD/CNY" || Day(s.Quote.Observed) != date || now.Sub(s.Generated) > 3*time.Minute || now.Before(s.Generated) {
		return f, err
	}
	observed := s.Quote.Observed.In(Zone)
	if observed.Hour() < 15 || (observed.Hour() == 15 && observed.Minute() < 57) || now.Before(observed) {
		return f, err
	}
	status, estimate, model := s.Status, s.Estimate, s.Model.Version
	if channel == "shenzhen" {
		status, estimate, model = s.SZStatus, s.SZEstimate, s.SZModel.Version
	} else if channel != "shanghai" {
		return f, err
	}
	if status != "closed" && status != "final_comparison" || estimate == nil {
		return f, err
	}
	rate := estimate.Sell
	if direction == "sell_hk" {
		rate = estimate.Buy
	} else if direction != "buy_hk" {
		return f, err
	}
	if rate <= 0 {
		return f, err
	}
	return iopv.FX{Midpoint: s.Parity.Rate, Settlement: rate, Date: date, ObservedAt: s.Quote.Observed, Channel: channel, Direction: direction, Status: "closed_reference", Actionable: false, Model: model}, nil
}
