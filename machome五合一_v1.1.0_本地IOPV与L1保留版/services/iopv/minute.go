package iopv

import (
	"fmt"
	"time"
)

// Exchange calendars/holiday overrides belong to the adapter. These are intraday
// display boundaries for a normal joint trading day, not a holiday calendar.
var china = time.FixedZone("Asia/Shanghai", 8*3600)

func minuteOfDay(t time.Time) int { t = t.In(china); return t.Hour()*60 + t.Minute() }
func ChartMinute(t time.Time) bool {
	m := minuteOfDay(t)
	return (m >= 570 && m <= 720) || (m >= 780 && m <= 968)
}
func DomesticMinute(t time.Time) bool {
	m := minuteOfDay(t)
	return (m >= 570 && m <= 690) || (m >= 780 && m <= 900)
}

type MinutePoint struct {
	Schema       string    `json:"schema_version"`
	Symbol       string    `json:"symbol"`
	Timestamp    time.Time `json:"timestamp"`
	Values       *Values   `json:"values"`
	ETFPrice     *float64  `json:"etf_price"`
	Premium      Premium   `json:"premium"`
	FX           FX        `json:"fx"`
	PCFHash      string    `json:"pcf_sha256"`
	CalculatedAt time.Time `json:"calculated_at"`
	Status       string    `json:"status"`
	Eligible     bool      `json:"eligible_for_signal"`
}

// One observed sample per minute, not a claim that different fund groups are simultaneous.
// Invoke with the last immutable evaluation at the minute close, retain original as-of time.
func SampleMinute(at time.Time, e Evaluation, q *Quote) (MinutePoint, error) {
	if !ChartMinute(at) {
		return MinutePoint{}, fmt.Errorf("outside chart session")
	}
	p := MinutePoint{Schema: "intranet-iopv.minute.v1", Symbol: e.Symbol, Timestamp: at.In(china).Truncate(time.Minute), FX: e.FX, PCFHash: e.PCFHash, CalculatedAt: e.CalculatedAt, Status: "missing", Eligible: false}
	age := at.Sub(e.CalculatedAt)
	if e.Date == at.In(china).Format("2006-01-02") && age >= 0 && age <= 12*time.Second && e.Values != nil {
		p.Values = e.Values
		p.Status = "observed"
		p.Eligible = e.Eligible
	}
	if DomesticMinute(at) && q != nil && q.Status == "live" && q.Source != "" && positive(q.Price) {
		qa, ra := at.Sub(q.ObservedAt), at.Sub(q.ReceivedAt)
		if qa >= 0 && qa <= 30*time.Second && ra >= 0 && ra <= 30*time.Second {
			v := q.Price
			p.ETFPrice = &v
			p.Premium = PremiumAt(v, p.Values)
		}
	}
	// Signal eligibility requires a usable domestic quote, even if NAV remains observable.
	p.Eligible = p.Eligible && p.ETFPrice != nil
	return p, nil
}

type Level struct {
	Price  float64 `json:"price"`
	Volume float64 `json:"volume"`
}
type Book struct {
	Bids       [5]Level  `json:"bids"`
	Asks       [5]Level  `json:"asks"`
	ObservedAt time.Time `json:"observed_at"`
}
type PremiumLevel struct {
	Level
	Premium Premium `json:"premium"`
}

func BookPremiums(book Book, v *Values, now time.Time) ([5]PremiumLevel, [5]PremiumLevel) {
	var bids, asks [5]PremiumLevel
	age := now.Sub(book.ObservedAt)
	usable := DomesticMinute(now) && age >= 0 && age <= 30*time.Second
	for i := 0; i < 5; i++ {
		bids[i].Level = book.Bids[i]
		asks[i].Level = book.Asks[i]
		if usable {
			if positive(book.Bids[i].Volume) {
				bids[i].Premium = PremiumAt(book.Bids[i].Price, v)
			}
			if positive(book.Asks[i].Volume) {
				asks[i].Premium = PremiumAt(book.Asks[i].Price, v)
			}
		}
	}
	return bids, asks
}
