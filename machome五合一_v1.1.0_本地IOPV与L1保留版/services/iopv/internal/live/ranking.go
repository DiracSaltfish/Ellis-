package live

// Premium ranking is a transparent screening policy, not a calibrated flow model.
import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"math"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

type RankingRow struct {
	FirstMinute    string              `json:"first_minute"`
	Comparison1430 *float64            `json:"comparison_1430"`
	HeuristicScore *float64            `json:"heuristic_score"`
	ModelVersion   string              `json:"model_version"`
	ModelHash      string              `json:"model_hash"`
	ModelQuality   string              `json:"model_quality"`
	TimeScope      string              `json:"time_scope"`
	FXBasis        string              `json:"fx_basis"`
	Features       map[string]*float64 `json:"features"`
	Symbol         string              `json:"symbol"`
	Name           string              `json:"name"`
	QC             string              `json:"qc"`
	Score          *float64            `json:"score"`
	MeanBP         float64             `json:"mean_bp"`
	P10BP          float64             `json:"p10_bp"`
	LastBP         float64             `json:"last_bp"`
	Positive       float64             `json:"positive_fraction"`
	Coverage       float64             `json:"coverage"`
	Minutes        int                 `json:"minutes"`
	FXGapBP        float64             `json:"fx_gap_bp"`
	BuyFX          *float64            `json:"buy_fx"`
	MidFX          *float64            `json:"mid_fx"`
	FXAt           time.Time           `json:"fx_at"`
	LastMinute     string              `json:"last_minute"`
	Pass           bool                `json:"premium_pass"`
	Reasons        []string            `json:"reasons"`
	Parts          map[string]float64  `json:"parts"`
}
type Ranking struct {
	ReviewedAt *time.Time   `json:"reviewed_at,omitempty"`
	Version    string       `json:"version"`
	Date       string       `json:"date"`
	Cutoff     string       `json:"cutoff"`
	Captured   time.Time    `json:"captured_at"`
	Expected   int          `json:"expected_minutes"`
	Status     string       `json:"status"`
	Rows       []RankingRow `json:"rows"`
}
type rankingCache struct {
	sync.RWMutex
	value Ranking
}

func rankMinute(m string) bool { return m >= "09:30" && m < "11:30" || m >= "13:00" && m < "15:00" }
func rankWindow(now time.Time) bool {
	m := sessionMinute(now)
	return m >= 571 && m <= 690 || m >= 780 && m <= 900
}
func rankExpected(cutoff string) int {
	n := 0
	for m := 570; m < 900; m++ {
		h := fmt.Sprintf("%02d:%02d", m/60, m%60)
		if h < cutoff && rankMinute(h) {
			n++
		}
	}
	return n
}
func clip(v float64) float64   { return math.Max(0, math.Min(1, v)) }
func positive(v *float64) bool { return v != nil && *v > 0 && !math.IsNaN(*v) && !math.IsInf(*v, 0) }
func rankReasonsUsable(p Point) bool {
	for _, reason := range p.Reasons {
		if reason != "STALE_COMPONENT_MARKS" || !verifiedLastTrade(p) {
			return false
		}
	}
	return true
}
func rankPoint(p Point, date, cutoff string) bool {
	return p.Date == date && rankMinute(p.Minute) && p.Minute < cutoff && Day(p.At) == date && p.At.In(Zone).Format("15:04") < cutoff && rankReasonsUsable(p) && positive(p.ETF) && positive(p.Mid) && positive(p.Buy) && positive(p.MidFX) && positive(p.BuyFX)
}
func rankRow(c Candidate, points []Point, date, cutoff string, expected int) RankingRow {
	r := RankingRow{Symbol: c.Symbol, Name: c.Name, QC: c.QC, Reasons: []string{}, Parts: map[string]float64{}}
	// Deduplicate before aggregation; never allow repeated ticks to weight a minute more heavily.
	by := map[string]Point{}
	for _, p := range points {
		if rankPoint(p, date, cutoff) {
			if old, ok := by[p.Minute]; !ok || p.At.After(old.At) {
				by[p.Minute] = p
			}
		}
	}
	ps := []Point{}
	for _, p := range by {
		ps = append(ps, p)
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].Minute < ps[j].Minute })
	if len(ps) == 0 {
		r.Reasons = append(r.Reasons, "无有效分钟")
		return r
	}
	last := ps[len(ps)-1]
	r.LastMinute = last.Minute
	r.BuyFX = last.BuyFX
	r.MidFX = last.MidFX
	r.FXAt = last.FXAt
	values := []float64{}
	for _, p := range ps {
		// Revalue every completed minute using the last available FX pair in this run.
		// Legacy archives without exposure are omitted if their FX needs rebasing.
		b := *p.Buy
		if *p.BuyFX != *last.BuyFX {
			if p.HKDAssets == nil || p.Unit <= 0 {
				continue
			}
			h := *p.HKDAssets / p.Unit
			if h < 0 {
				continue
			}
			b += h * (*last.BuyFX - *p.BuyFX)
		}
		if b <= 0 {
			continue
		}
		v := (*p.ETF/b - 1) * 10000
		if math.IsNaN(v) || math.IsInf(v, 0) {
			continue
		}
		values = append(values, v)
		r.MeanBP += v
		if v > 0 {
			r.Positive++
		}
		if p.Minute == last.Minute {
			r.LastBP = v
		}
	}
	r.Minutes = len(values)
	if r.Minutes == 0 {
		r.Reasons = append(r.Reasons, "汇率重估不可用")
		return r
	}
	r.MeanBP /= float64(r.Minutes)
	r.Positive /= float64(r.Minutes)
	if expected > 0 {
		r.Coverage = float64(r.Minutes) / float64(expected)
	}
	sort.Float64s(values)
	r.P10BP = values[int(float64(len(values)-1)*.1)]
	r.FXGapBP = (*last.Mid / *last.Buy - 1) * 10000
	r.Parts = map[string]float64{"累计溢价": 40 * clip(r.MeanBP/50), "持续性": 30 * r.Positive, "低分位": 20 * clip(r.P10BP/30), "最新溢价": 10 * clip(r.LastBP/50)}
	score := 0.0
	for _, v := range r.Parts {
		score += v
	}
	r.Score = &score
	if expected < 30 {
		r.Reasons = append(r.Reasons, "有效观察窗口不足30分钟")
	}
	if r.Coverage < .95 {
		r.Reasons = append(r.Reasons, "分钟覆盖不足95%")
	}
	end, _ := time.Parse("15:04", cutoff)
	lm, _ := time.Parse("15:04", r.LastMinute)
	if cutoff != "13:00" && end.Sub(lm) > 2*time.Minute {
		r.Reasons = append(r.Reasons, "最新分钟缺失")
	}
	if r.MeanBP < 10 {
		r.Reasons = append(r.Reasons, "累计结算溢价低于10bp")
	}
	if r.Positive < .7 {
		r.Reasons = append(r.Reasons, "正溢价时间不足70%")
	}
	if r.LastBP <= 0 {
		r.Reasons = append(r.Reasons, "最新结算溢价非正")
	}
	if r.FXGapBP <= 0 {
		r.Reasons = append(r.Reasons, "中间价优势非正")
	}
	if !last.FXActionable {
		r.Reasons = append(r.Reasons, "汇率模型仅供参考")
	}
	if c.Symbol == "513130.SH" {
		r.Reasons = append(r.Reasons, "历史质量排除")
	}
	r.Pass = len(r.Reasons) == 0
	return r
}
func (s *Service) computeRanking(now time.Time) Ranking {
	date, cutoff := Day(now), now.In(Zone).Format("15:04")
	if cutoff > "15:00" {
		cutoff = "15:00"
	}
	v := Ranking{Version: "premium-model.v2", Date: date, Cutoff: cutoff, Captured: now, Expected: rankExpected(cutoff), Rows: []RankingRow{}, Status: "原纯溢价模型分（非校准概率）；T日实时预估汇率；实时剩余额度及费用需另核"}
	s.mu.RLock()
	cs := append([]Candidate(nil), s.candidates...)
	paused := s.paused
	fxRaw := append([]byte(nil), s.fxRaw...)
	baskets := make(map[string]iopv.Basket)
	for k, b := range s.baskets {
		baskets[k] = b
	}
	s.mu.RUnlock()
	for _, c := range cs {
		if !strings.HasPrefix(c.Symbol, "5") || !strings.HasSuffix(c.Symbol, ".SH") {
			continue
		}
		ps, e := s.store.History(c.Symbol, date)
		r := rankRow(c, ps, date, cutoff, v.Expected)
		s.applyFlowModel(&r, ps, baskets[c.Symbol], now, fxRaw)
		if e != nil {
			r.Pass = false
			r.Reasons = append(r.Reasons, "历史读取失败")
		}
		if paused {
			r.Pass = false
			r.Score = nil
			r.ModelQuality = "已暂停"
			r.Reasons = append(r.Reasons, "估值已暂停")
		}
		v.Rows = append(v.Rows, r)
	}
	sort.Slice(v.Rows, func(i, j int) bool {
		a, b := v.Rows[i], v.Rows[j]
		if a.Pass != b.Pass {
			return a.Pass
		}
		if (a.Score != nil) != (b.Score != nil) {
			return a.Score != nil
		}
		if a.Score != nil && b.Score != nil && *a.Score != *b.Score {
			return *a.Score > *b.Score
		}
		return a.Symbol < b.Symbol
	})
	return v
}
func (s *Service) rankingLoop(ctx context.Context) {
	s.setError("ranking_restore", s.restoreSessionRanking(time.Now()))
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	last := ""
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-tick.C:
			key := now.In(Zone).Format("2006-01-02 15:04")
			// Allow the existing minute flush to complete. Only a single calculation per minute.
			if !rankWindow(now) || now.Second() < 5 || key == last {
				continue
			}
			last = key
			v := s.computeRanking(now)
			s.ranking.Lock()
			s.ranking.value = v
			s.ranking.Unlock()
			if v.Cutoff == "14:00" || v.Cutoff == "14:30" || v.Cutoff == "15:00" {
				// No inferred snapshots for missed slots or holidays without any same-day data.
				has := false
				for _, r := range v.Rows {
					has = has || r.Minutes > 0
				}
				if !has {
					continue
				}
				raw, e := json.Marshal(v)
				if e == nil {
					_, e = s.store.DB.Exec("INSERT OR IGNORE INTO ranking_snapshots(trade_date,slot,payload) VALUES(?,?,?)", v.Date, v.Cutoff, string(raw))
				}
				s.setError("ranking_snapshot", e)
			}
		}
	}
}
func (s *Service) rankingHandler(w http.ResponseWriter, r *http.Request) {
	date, slot := r.URL.Query().Get("date"), r.URL.Query().Get("slot")
	if date == "" {
		date = Day(time.Now())
	}
	if _, e := time.Parse("2006-01-02", date); e != nil {
		http.Error(w, "bad date", 400)
		return
	}
	if slot != "" {
		if slot != "14:00" && slot != "14:30" && slot != "15:00" {
			http.Error(w, "bad slot", 400)
			return
		}
		var raw string
		e := s.store.DB.QueryRow("SELECT payload FROM ranking_snapshots WHERE trade_date=? AND slot=?", date, slot).Scan(&raw)
		if e == sql.ErrNoRows {
			http.Error(w, "该时点未采集快照", 404)
			return
		}
		if e != nil {
			http.Error(w, "storage unavailable", 503)
			return
		}
		var v Ranking
		if json.Unmarshal([]byte(raw), &v) != nil {
			http.Error(w, "invalid snapshot", 503)
			return
		}
		writeJSON(w, v)
		return
	}
	s.ranking.RLock()
	v := s.ranking.value
	s.ranking.RUnlock()
	if v.Date != date {
		var raw string
		err := s.store.DB.QueryRow("SELECT payload FROM ranking_snapshots WHERE trade_date=? ORDER BY slot DESC LIMIT 1", date).Scan(&raw)
		if err == nil && json.Unmarshal([]byte(raw), &v) == nil && v.Date == date {
			v.Status += "；最近已保存快照（非新实时计算）"
		} else {
			v = Ranking{Version: "premium-model.v2", Date: date, Status: "尚无当日计算结果；历史请选固定快照", Rows: []RankingRow{}}
		}
	}
	writeJSON(w, v)
}
