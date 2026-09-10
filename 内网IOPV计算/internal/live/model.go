package live

import (
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"strconv"
	"strings"
	"time"
)

var Zone = time.FixedZone("Asia/Shanghai", 28800)

func Day(t time.Time) string { return t.In(Zone).Format("2006-01-02") }
func Channel(s string) string {
	if strings.HasSuffix(s, ".SZ") {
		return "shenzhen"
	}
	return "shanghai"
}

type Candidate struct {
	Symbol string `json:"symbol"`
	Name   string `json:"name"`
	QC     string `json:"qc"`
}
type Quote struct {
	Symbol   string    `json:"symbol"`
	Price    float64   `json:"price"`
	Observed time.Time `json:"observed_at"`
	Received time.Time `json:"received_at"`
	Phase    string    `json:"phase"`
	Book     iopv.Book `json:"book"`
	Session  uint64    `json:"session"`
}
type BookValue struct {
	Side   string   `json:"side"`
	Level  int      `json:"level"`
	Price  float64  `json:"price"`
	Volume float64  `json:"volume"`
	Mid    *float64 `json:"midpoint_premium_pct"`
	Buy    *float64 `json:"settlement_buy_premium_pct"`
	Sell   *float64 `json:"settlement_sell_premium_pct"`
}
type Point struct {
	Suspended         []string    `json:"suspended"`
	SuspensionPending []string    `json:"suspension_pending"`
	FXError           string      `json:"fx_error"`
	FXGenerated       time.Time   `json:"fx_generated_at"`
	HKDAssets         *float64    `json:"hkd_assets"`
	CNYAssets         *float64    `json:"cny_assets"`
	BookValues        []BookValue `json:"book_premiums"`

	Symbol       string     `json:"symbol"`
	Name         string     `json:"name"`
	Date         string     `json:"trade_date"`
	At           time.Time  `json:"calculated_at"`
	Minute       string     `json:"minute"`
	Hash         string     `json:"pcf_sha256"`
	Channel      string     `json:"channel"`
	Mid          *float64   `json:"midpoint_iopv"`
	Buy          *float64   `json:"settlement_buy_iopv"`
	Sell         *float64   `json:"settlement_sell_iopv"`
	ETF          *float64   `json:"etf_price"`
	MidPremium   *float64   `json:"midpoint_premium_pct"`
	BuyPremium   *float64   `json:"settlement_buy_premium_pct"`
	SellPremium  *float64   `json:"settlement_sell_premium_pct"`
	MidFX        *float64   `json:"midpoint_fx"`
	BuyFX        *float64   `json:"settlement_buy_fx"`
	SellFX       *float64   `json:"settlement_sell_fx"`
	FXAt         time.Time  `json:"fx_at"`
	FXModel      string     `json:"fx_model"`
	FXStatus     string     `json:"fx_status"`
	FXActionable bool       `json:"fx_actionable"`
	Components   int        `json:"components"`
	Priced       int        `json:"priced"`
	Missing      []string   `json:"missing"`
	Stale        []string   `json:"stale"`
	Reasons      []string   `json:"reasons"`
	Eligible     bool       `json:"eligible_for_signal"`
	Oldest       time.Time  `json:"oldest_quote_at"`
	ETFAt        time.Time  `json:"etf_quote_at"`
	Book         *iopv.Book `json:"book"`
	Sequence     uint64     `json:"sequence"`
	RunID        string     `json:"run_id"`
	Mode         string     `json:"mode"`
	Unit         float64    `json:"unit"`
	Cash         float64    `json:"cash"`
}

func ptr(v float64) *float64 { return &v }
func premium(p, n *float64) *float64 {
	if p == nil || n == nil || *n <= 0 {
		return nil
	}
	return ptr((*p / *n - 1) * 100)
}

type Decoder struct {
	state map[string]map[string]json.RawMessage
}

func NewDecoder() *Decoder { return &Decoder{map[string]map[string]json.RawMessage{}} }
func (d *Decoder) Decode(raw []byte, received time.Time, session uint64) (Quote, error) {
	var e struct {
		Tag   string                     `json:"tag"`
		Delta int                        `json:"delta"`
		Data  map[string]json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(raw, &e); err != nil {
		return Quote{}, err
	}
	if (e.Tag != "14" && e.Tag != "16") || (e.Delta != 0 && e.Delta != 1) {
		return Quote{}, fmt.Errorf("unknown feed envelope")
	}
	var code string
	if json.Unmarshal(e.Data["2"], &code) != nil {
		return Quote{}, fmt.Errorf("code missing")
	}
	var market int
	if json.Unmarshal(e.Data["1"], &market) != nil || (market != 101 && market != 102) {
		return Quote{}, fmt.Errorf("market missing")
	}
	symbol := code + ".SZ"
	if market == 101 {
		symbol = code + ".SH"
	}
	tk, pk, phase := "4", "10", "5"
	expected := 21
	if e.Tag == "16" {
		symbol = code + ".HK"
		tk, pk, phase = "3", "11", "4"
		expected = 23
	}
	if len(code) != 6 && e.Tag == "14" || len(code) != 5 && e.Tag == "16" {
		return Quote{}, fmt.Errorf("bad code")
	}
	state := map[string]json.RawMessage{}
	if e.Delta == 1 {
		old, ok := d.state[symbol]
		if !ok {
			return Quote{}, fmt.Errorf("delta before full")
		}
		for k, v := range old {
			state[k] = v
		}
	}
	for k, v := range e.Data {
		state[k] = v
	}
	for i := 1; i <= expected; i++ {
		if _, ok := state[strconv.Itoa(i)]; !ok {
			return Quote{}, fmt.Errorf("incomplete full")
		}
	}
	integer := func(k string) (int64, error) { var v int64; err := json.Unmarshal(state[k], &v); return v, err }
	ts, err := integer(tk)
	if err != nil {
		return Quote{}, err
	}
	s := strconv.FormatInt(ts, 10)
	if len(s) != 17 {
		return Quote{}, fmt.Errorf("timestamp width")
	}
	observed, err := time.ParseInLocation("20060102150405.000", s[:14]+"."+s[14:], Zone)
	if err != nil {
		return Quote{}, err
	}
	if old := d.state[symbol]; old != nil {
		var oldTS int64
		json.Unmarshal(old[tk], &oldTS)
		if ts < oldTS {
			return Quote{}, fmt.Errorf("out of order")
		}
	}
	price, err := integer(pk)
	if err != nil || price < 0 {
		return Quote{}, fmt.Errorf("invalid price")
	}
	q := Quote{Symbol: symbol, Price: float64(price) / 1e6, Observed: observed, Received: received, Session: session}
	json.Unmarshal(state[phase], &q.Phase)
	q.Book.ObservedAt = observed
	levels := func(key string, scale float64) ([]float64, error) {
		var s string
		if err := json.Unmarshal(state[key], &s); err != nil {
			return nil, err
		}
		parts := strings.Split(s, "|")
		if len(parts) < 5 {
			return nil, fmt.Errorf("short book")
		}
		out := make([]float64, 5)
		for i := range out {
			v, e := strconv.ParseInt(parts[i], 10, 64)
			if e != nil || v < 0 {
				return nil, fmt.Errorf("invalid level")
			}
			out[i] = float64(v) / scale
		}
		return out, nil
	}
	bp, e1 := levels("12", 1e6)
	bv, e2 := levels("13", 100)
	ap, e3 := levels("14", 1e6)
	av, e4 := levels("15", 100)
	if e1 != nil || e2 != nil || e3 != nil || e4 != nil {
		return Quote{}, fmt.Errorf("invalid book")
	}
	for i := 0; i < 5; i++ {
		q.Book.Bids[i] = iopv.Level{Price: bp[i], Volume: bv[i]}
		q.Book.Asks[i] = iopv.Level{Price: ap[i], Volume: av[i]}
	}
	d.state[symbol] = state
	return q, nil
}

func reprice(p *Point, q Quote, now time.Time, navFresh bool) {
	p.ETF = nil
	p.MidPremium = nil
	p.BuyPremium = nil
	p.SellPremium = nil
	p.BookValues = nil
	if q.Symbol == "" || Day(q.Observed) != p.Date {
		return
	}
	p.ETFAt = q.Observed
	book := q.Book
	p.Book = &book
	limit := 30 * time.Second
	n, o := now.In(Zone), q.Observed.In(Zone)
	closing := (n.Hour() == 11 && n.Minute() == 30 || n.Hour() == 15 && n.Minute() == 0) && n.Hour() == o.Hour() && n.Minute() == o.Minute()
	if closing {
		limit = time.Minute
	}
	fresh := navFresh && iopv.DomesticMinute(now) && now.Sub(q.Observed) >= -2*time.Second && now.Sub(q.Observed) <= limit && now.Sub(q.Received) <= limit
	if closing {
		p.Eligible = false
	}
	if fresh && q.Price > 0 {
		p.ETF = ptr(q.Price)
		p.MidPremium = premium(p.ETF, p.Mid)
		p.BuyPremium = premium(p.ETF, p.Buy)
		p.SellPremium = premium(p.ETF, p.Sell)
	}
	for i := 0; i < 5; i++ {
		for _, side := range []string{"bid", "ask"} {
			l := q.Book.Bids[i]
			if side == "ask" {
				l = q.Book.Asks[i]
			}
			r := BookValue{Side: side, Level: i + 1, Price: l.Price, Volume: l.Volume}
			if fresh && l.Price > 0 && l.Volume > 0 {
				r.Mid = premium(ptr(l.Price), p.Mid)
				r.Buy = premium(ptr(l.Price), p.Buy)
				r.Sell = premium(ptr(l.Price), p.Sell)
			}
			p.BookValues = append(p.BookValues, r)
		}
	}
}
