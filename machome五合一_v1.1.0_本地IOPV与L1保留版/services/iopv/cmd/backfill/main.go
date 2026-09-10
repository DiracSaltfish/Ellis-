// Offline reconstruction uses archived source minutes and the production C++ core.
// It writes a reviewable JSONL artifact; it never writes the live database.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	iopv "intranet-iopv"
	"intranet-iopv/internal/live"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func positive(v float64) bool { return v > 0 && !math.IsNaN(v) && !math.IsInf(v, 0) }
func ptr(v float64) *float64  { return &v }
func check(e error) {
	if e != nil {
		panic(e)
	}
}
func read(p string, v any) { b, e := os.ReadFile(p); check(e); check(json.Unmarshal(b, v)) }
func prices(raw []byte, symbol, date string, cutoff time.Time) (map[string]float64, error) {
	var d struct {
		Data map[string]struct {
			Data struct {
				Date string   `json:"date"`
				Rows []string `json:"data"`
			} `json:"data"`
		} `json:"data"`
	}
	if e := json.Unmarshal(raw, &d); e != nil {
		return nil, e
	}
	parts := strings.Split(symbol, ".")
	if len(parts) != 2 {
		return nil, fmt.Errorf("bad symbol")
	}
	v, ok := d.Data[strings.ToLower(parts[1])+parts[0]]
	if !ok || v.Data.Date != strings.ReplaceAll(date, "-", "") {
		return nil, fmt.Errorf("missing/wrong source day")
	}
	out := map[string]float64{}
	for _, line := range v.Data.Rows {
		cols := strings.Fields(line)
		if len(cols) < 2 || len(cols[0]) != 4 {
			return nil, fmt.Errorf("bad minute row")
		}
		minute := cols[0][:2] + ":" + cols[0][2:]
		at, e := time.ParseInLocation("2006-01-02 15:04", date+" "+minute, live.Zone)
		if e != nil {
			return nil, e
		}
		if at.After(cutoff) {
			continue
		}
		if minute < "09:30" || minute > "16:08" || (minute > "12:00" && minute < "13:00") {
			continue
		}
		// Provider sometimes expands a suspended previous price into an entire current-day series.
		// Require evidence of a trade by this minute; do not treat zero-volume placeholders as prices.
		if len(cols) < 3 {
			return nil, fmt.Errorf("volume missing")
		}
		volume, ve := strconv.ParseFloat(cols[2], 64)
		if ve != nil || !positive(volume) {
			continue
		}
		p, e := strconv.ParseFloat(cols[1], 64)
		if e != nil || !positive(p) {
			continue
		}
		if _, ok := out[minute]; ok {
			return nil, fmt.Errorf("duplicate minute")
		}
		out[minute] = p
	}
	return out, nil
}

type fxRow struct {
	At       time.Time `json:"timestamp"`
	Date     string    `json:"trade_date"`
	Status   string    `json:"status"`
	Observed time.Time `json:"fx_observed_at"`
	Buy      float64   `json:"predicted_buy_settlement"`
	Sell     float64   `json:"predicted_sell_settlement"`
}

func fxUsable(r fxRow, date, minute string) bool {
	return r.Date == date && r.At.In(live.Zone).Format("15:04") == minute && live.Day(r.Observed) == date && !r.Observed.After(r.At) && r.At.Sub(r.Observed) <= 3*time.Minute && positive(r.Buy) && positive(r.Sell) && r.Status != "cfets_unavailable"
}
func calculate(b iopv.Basket, quotes map[string]map[string]float64, minute string, mid float64) (iopv.Values, []string, error) {
	rows := []iopv.CoreRow{}
	missing := []string{}
	for _, c := range b.Components {
		price := quotes[c.Symbol][minute]
		if c.Mode != 2 && !positive(price) {
			missing = append(missing, c.Symbol)
		}
		rows = append(rows, iopv.CoreRow{Quantity: c.Quantity, Price: price, Cash: c.Cash, Mode: c.Mode})
	}
	if len(missing) > 0 {
		return iopv.Values{}, missing, nil
	}
	v, e := iopv.Calculate(rows, b.Unit, b.Cash, mid, mid)
	return v, missing, e
}
func main() {
	root := flag.String("root", "", "raw archive directory")
	date := flag.String("date", "", "trade date")
	end := flag.String("cutoff", "12:00", "last completed minute")
	output := flag.String("output", "backfill.jsonl", "output JSONL")
	flag.Parse()
	cutoff, e := time.ParseInLocation("2006-01-02 15:04", *date+" "+*end, live.Zone)
	check(e)
	if !cutoff.Before(time.Now()) {
		panic("cutoff must be historical")
	}
	var parity iopv.FXSnapshot
	read(filepath.Join(*root, "fx-snapshot.json"), &parity)
	if parity.Schema != "hk-connect-fx.v7" || parity.Date != *date || parity.Parity == nil || parity.Parity.Date != *date || parity.Parity.Pair != "HKD/CNY" || !positive(parity.Parity.Rate) {
		panic("parity date/schema")
	}
	var seeds []live.Point
	read(filepath.Join(*root, "pcf/snapshots.json"), &seeds)
	quotes := map[string]map[string]float64{}
	var plan []string
	read(filepath.Join(*root, "quote-plan.json"), &plan)
	rejected := map[string]string{}
	for _, s := range plan {
		raw, e := os.ReadFile(filepath.Join(*root, "quotes", s+".json"))
		if e != nil {
			rejected[s] = e.Error()
			continue
		}
		p, e := prices(raw, s, *date, cutoff)
		if e != nil {
			rejected[s] = e.Error()
			continue
		}
		quotes[s] = p
	}
	fx := map[string]map[string]fxRow{}
	for _, channel := range []string{"shanghai", "shenzhen"} {
		var h struct {
			Schema string  `json:"schema_version"`
			Date   string  `json:"trade_date"`
			Market string  `json:"market"`
			Rows   []fxRow `json:"rows"`
		}
		read(filepath.Join(*root, "fx-"+channel+".json"), &h)
		if h.Schema != "hk-connect-fx.v7" || h.Date != *date || h.Market != channel {
			panic("FX history identity mismatch")
		}
		fx[channel] = map[string]fxRow{}
		for _, r := range h.Rows {
			if live.Day(r.At) != *date {
				panic("FX row date mismatch")
			}
			m := r.At.In(live.Zone).Format("15:04")
			if _, ok := fx[channel][m]; ok {
				panic("duplicate FX minute")
			}
			fx[channel][m] = r
		}
	}
	file, e := os.OpenFile(*output, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	check(e)
	defer file.Close()
	enc := json.NewEncoder(file)
	now := time.Now().In(live.Zone)
	run := "historical-backfill-" + now.Format("20060102T150405")
	counts := map[string]int{}
	funds := map[string]int{}
	for _, seed := range seeds {
		if seed.Date != *date {
			panic("seed day mismatch")
		}
		raw, e := os.ReadFile(filepath.Join(*root, "pcf", seed.Symbol+".xml"))
		check(e)
		b, e := iopv.ParsePCF(raw, seed.Symbol, *date)
		check(e)
		if b.Hash != seed.Hash {
			panic("PCF hash mismatch")
		}
		channel := live.Channel(b.Symbol)
		start, _ := time.ParseInLocation("2006-01-02 15:04", *date+" 09:30", live.Zone)
		for t := start; !t.After(cutoff); t = t.Add(time.Minute) {
			m := t.Format("15:04")
			if m > "12:00" && m < "13:00" {
				continue
			}
			v, missing, e := calculate(b, quotes, m, parity.Parity.Rate)
			check(e)
			p := live.Point{Symbol: b.Symbol, Name: seed.Name, Date: *date, At: now, Minute: m, Hash: b.Hash, Channel: channel, MidFX: ptr(parity.Parity.Rate), Components: len(b.Components), Priced: len(b.Components) - len(missing), Missing: missing, Reasons: []string{"historical_reconstruction", "source:tencent_minute_exact_label", "not_live_signal"}, Eligible: false, RunID: run, Mode: "historical_reconstruction", Unit: b.Unit, Cash: b.Cash, Oldest: t, FXModel: "hk-connect-fx.v7/history", FXStatus: "historical_fx_missing"}
			if len(missing) == 0 {
				p.Mid = ptr(v.Midpoint)
				p.HKDAssets = ptr(v.HKDAssets)
				p.CNYAssets = ptr(v.CNYAssets)
				counts["midpoint"]++
				funds[b.Symbol]++
			} else {
				p.Reasons = append(p.Reasons, "missing_historical_components")
				counts["missing_components"]++
			}
			r, ok := fx[channel][m]
			if ok {
				p.FXAt = r.Observed
				p.FXGenerated = r.At
				p.FXStatus = r.Status
			}
			if ok && fxUsable(r, *date, m) {
				p.BuyFX = ptr(r.Sell)
				p.SellFX = ptr(r.Buy)
				if p.Mid != nil {
					p.Buy = ptr((v.HKDAssets*r.Sell + v.CNYAssets) / b.Unit)
					p.Sell = ptr((v.HKDAssets*r.Buy + v.CNYAssets) / b.Unit)
					counts["settlement"]++
				}
			} else {
				p.Reasons = append(p.Reasons, "historical_fx_missing_or_stale")
			}
			if (m <= "11:30" || m >= "13:00") && m <= "15:00" {
				if etf := quotes[b.Symbol][m]; positive(etf) {
					p.ETF = ptr(etf)
					p.ETFAt = t
					if p.Mid != nil {
						p.MidPremium = ptr((etf / *p.Mid - 1) * 100)
						counts["premium"]++
					}
					if p.Buy != nil {
						p.BuyPremium = ptr((etf / *p.Buy - 1) * 100)
						p.SellPremium = ptr((etf / *p.Sell - 1) * 100)
					}
				}
			}
			counts["rows"]++
			check(enc.Encode(p))
		}
	}
	summary := map[string]any{"date": *date, "cutoff": *end, "run_id": run, "counts": counts, "funds_with_midpoint": len(funds), "rejected_sources": rejected, "fund_counts": funds, "policy": "Exact source minute; no fill, no current FX substituted; historical points are never signal eligible"}
	b, e := json.MarshalIndent(summary, "", "  ")
	check(e)
	check(os.WriteFile(*output+".summary.json", b, 0600))
	fmt.Println(string(b))
}
