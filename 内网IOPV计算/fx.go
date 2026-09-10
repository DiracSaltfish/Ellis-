package iopv

import (
	"encoding/json"
	"fmt"
	"time"
)

type fxEstimate struct {
	Buy  float64 `json:"predicted_buy_settlement"`
	Sell float64 `json:"predicted_sell_settlement"`
}
type fxModel struct {
	Version string `json:"version"`
}
type FXSnapshot struct {
	Schema       string      `json:"schema_version"`
	Date         string      `json:"trade_date"`
	Generated    time.Time   `json:"generated_at"`
	Status       string      `json:"status"`
	Actionable   bool        `json:"actionable"`
	SZStatus     string      `json:"shenzhen_status"`
	SZActionable bool        `json:"shenzhen_actionable"`
	Estimate     *fxEstimate `json:"estimate"`
	SZEstimate   *fxEstimate `json:"shenzhen_estimate"`
	Model        fxModel     `json:"model"`
	SZModel      fxModel     `json:"shenzhen_model"`
	Parity       *struct {
		Pair string  `json:"pair"`
		Date string  `json:"trade_date"`
		Rate float64 `json:"rate"`
	} `json:"central_parity"`
	Quote struct {
		Pair     string    `json:"pair"`
		Healthy  bool      `json:"healthy"`
		Observed time.Time `json:"observed_at"`
	} `json:"fx"`
}

// channel is an explicit valuation assumption, never inferred from ETF listing market.
func ParseFX(raw []byte, date, channel, direction string, now time.Time) (FX, error) {
	var s FXSnapshot
	if e := json.Unmarshal(raw, &s); e != nil {
		return FX{}, e
	}
	fail := func(reason string) (FX, error) { return FX{}, fmt.Errorf("FX: %s", reason) }
	if s.Schema != "hk-connect-fx.v7" {
		return fail("unknown schema")
	}
	if s.Date != date || s.Parity == nil || s.Parity.Date != date || s.Parity.Pair != "HKD/CNY" || !positive(s.Parity.Rate) {
		return fail("missing/current-day parity")
	}
	age, qage := now.Sub(s.Generated), now.Sub(s.Quote.Observed)
	if age < 0 || age > 3*time.Minute || qage < 0 || qage > 3*time.Minute || !s.Quote.Healthy || s.Quote.Pair != "HKD/CNY" {
		return fail(fmt.Sprintf("stale/unhealthy FX: status=%s, healthy=%t, quote_age=%.0fs, snapshot_age=%.0fs", s.Status, s.Quote.Healthy, qage.Seconds(), age.Seconds()))
	}
	est, status, actionable, model := s.Estimate, s.Status, s.Actionable, s.Model.Version
	switch channel {
	case "shanghai":
	case "shenzhen":
		est, status, actionable, model = s.SZEstimate, s.SZStatus, s.SZActionable, s.SZModel.Version
	default:
		return fail("channel required")
	}
	if est == nil || model == "" {
		return fail("estimate missing")
	}
	var rate float64
	switch direction {
	case "buy_hk":
		rate = est.Sell
	case "sell_hk":
		rate = est.Buy
	default:
		return fail("direction required")
	}
	if !positive(rate) {
		return fail("invalid settlement rate")
	}
	return FX{s.Parity.Rate, rate, date, s.Quote.Observed, channel, direction, status, actionable, model}, nil
}
