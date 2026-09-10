package iopv

import (
	"fmt"
	"math"
	"sort"
	"time"
)

func positive(v float64) bool { return v > 0 && !math.IsNaN(v) && !math.IsInf(v, 0) }

type Quote struct {
	Price      float64   `json:"price"`
	ObservedAt time.Time `json:"observed_at"`
	ReceivedAt time.Time `json:"received_at"`
	Status     string    `json:"status"`
	Source     string    `json:"source"`
}
type FX struct {
	Midpoint   float64   `json:"midpoint"`
	Settlement float64   `json:"settlement"`
	Date       string    `json:"trade_date"`
	ObservedAt time.Time `json:"observed_at"`
	Channel    string    `json:"channel"`
	Direction  string    `json:"direction"`
	Status     string    `json:"status"`
	Actionable bool      `json:"actionable"`
	Model      string    `json:"model"`
}
type Evaluation struct {
	Symbol       string    `json:"symbol"`
	Date         string    `json:"trade_date"`
	CalculatedAt time.Time `json:"calculated_at"`
	PCFHash      string    `json:"pcf_sha256"`
	Values       *Values   `json:"values"`
	FX           FX        `json:"fx"`
	Eligible     bool      `json:"eligible_for_signal"`
	Reasons      []string  `json:"reasons"`
}

// Input quote map is an immutable snapshot owned by caller for this invocation.
// No automatic previous-close fallback: stale/suspended values require a later explicit policy.
func Evaluate(b Basket, quotes map[string]Quote, fx FX, now time.Time, approved bool) Evaluation {
	out := Evaluation{Symbol: b.Symbol, Date: b.Date, CalculatedAt: now, PCFHash: b.Hash, FX: fx, Reasons: []string{}}
	reject := func(s string) Evaluation { out.Reasons = append(out.Reasons, s); return out }
	if !approved {
		return reject("QC_NOT_APPROVED")
	}
	if b.Date != now.In(time.FixedZone("CST", 8*3600)).Format("2006-01-02") || fx.Date != b.Date {
		return reject("DATE_MISMATCH")
	}
	if (fx.Channel != "shanghai" && fx.Channel != "shenzhen") || (fx.Direction != "buy_hk" && fx.Direction != "sell_hk") {
		return reject("FX_POLICY_MISSING")
	}
	age := now.Sub(fx.ObservedAt)
	if age < 0 || age > 3*time.Minute {
		return reject("FX_STALE")
	}
	rows := make([]CoreRow, 0, len(b.Components))
	for _, c := range b.Components {
		row := CoreRow{Quantity: c.Quantity, Cash: c.Cash, Mode: c.Mode}
		if c.Mode != 2 {
			q, ok := quotes[c.Symbol]
			if !ok {
				return reject("QUOTE_MISSING:" + c.Symbol)
			}
			observed, received := now.Sub(q.ObservedAt), now.Sub(q.ReceivedAt)
			if q.Status != "live" || q.Source == "" || observed < 0 || received < 0 || observed > 30*time.Second || received > 30*time.Second {
				return reject("QUOTE_NOT_LIVE:" + c.Symbol)
			}
			row.Price = q.Price
		}
		rows = append(rows, row)
	}
	values, err := Calculate(rows, b.Unit, b.Cash, fx.Midpoint, fx.Settlement)
	if err != nil {
		return reject("INVALID_VALUATION:" + err.Error())
	}
	out.Values = &values
	etf, hasETF := quotes[b.Symbol]
	etfFresh := hasETF && etf.Status == "live" && etf.Source != "" && positive(etf.Price) &&
		now.Sub(etf.ObservedAt) >= 0 && now.Sub(etf.ObservedAt) <= 30*time.Second &&
		now.Sub(etf.ReceivedAt) >= 0 && now.Sub(etf.ReceivedAt) <= 30*time.Second
	out.Eligible = fx.Actionable && fx.Status != "reference_only" && DomesticMinute(now) && etfFresh
	if !etfFresh {
		out.Reasons = append(out.Reasons, "ETF_QUOTE_NOT_LIVE")
	}
	if !DomesticMinute(now) {
		out.Reasons = append(out.Reasons, "DOMESTIC_MARKET_CLOSED")
	}
	if !fx.Actionable {
		out.Reasons = append(out.Reasons, "FX_REFERENCE_ONLY")
	}
	return out
}

type Premium struct {
	MidpointPct   *float64 `json:"midpoint_pct"`
	SettlementPct *float64 `json:"settlement_pct"`
}

func PremiumAt(price float64, v *Values) Premium {
	if v == nil || !positive(price) || !positive(v.Midpoint) || !positive(v.Settlement) {
		return Premium{}
	}
	a, b := (price/v.Midpoint-1)*100, (price/v.Settlement-1)*100
	if math.IsInf(a, 0) || math.IsInf(b, 0) {
		return Premium{}
	}
	return Premium{&a, &b}
}

type Plan struct {
	Groups        [3][]string `json:"groups"`
	Loads         [3]int      `json:"component_loads"`
	Subscriptions []string    `json:"subscriptions"`
}

func BuildPlan(baskets []Basket) (Plan, error) {
	p := Plan{}
	baskets = append([]Basket(nil), baskets...)
	sort.Slice(baskets, func(i, j int) bool {
		if len(baskets[i].Components) == len(baskets[j].Components) {
			return baskets[i].Symbol < baskets[j].Symbol
		}
		return len(baskets[i].Components) > len(baskets[j].Components)
	})
	symbols, etfs := map[string]bool{}, map[string]bool{}
	for _, b := range baskets {
		if etfs[b.Symbol] {
			return p, fmt.Errorf("duplicate ETF")
		}
		etfs[b.Symbol] = true
		symbols[b.Symbol] = true
		g := 0
		for j := 1; j < 3; j++ {
			if p.Loads[j] < p.Loads[g] {
				g = j
			}
		}
		p.Groups[g] = append(p.Groups[g], b.Symbol)
		p.Loads[g] += len(b.Components)
		for _, c := range b.Components {
			if c.Mode != 2 {
				symbols[c.Symbol] = true
			}
		}
	}
	for s := range symbols {
		p.Subscriptions = append(p.Subscriptions, s)
	}
	sort.Strings(p.Subscriptions)
	return p, nil
}

type Scheduler struct {
	last        int64
	initialized bool
}

// elapsed must be monotonic time since scheduler start; missed ticks are skipped.
func (s *Scheduler) Due(elapsed time.Duration) (int, bool) {
	if elapsed < 0 {
		return 0, false
	}
	tick := int64(elapsed / (3 * time.Second))
	if s.initialized && tick <= s.last {
		return 0, false
	}
	s.last = tick
	s.initialized = true
	return int(tick % 3), true
}
