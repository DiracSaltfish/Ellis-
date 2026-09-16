package live

import (
	"embed"
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"math"
	"sort"
	"time"
)

//go:embed models/model_*.json
var modelFiles embed.FS

type treeNode struct {
	Value       float64 `json:"value"`
	Feature     int     `json:"feature"`
	Threshold   float64 `json:"threshold"`
	MissingLeft bool    `json:"missing_left"`
	Left        int     `json:"left"`
	Right       int     `json:"right"`
	Leaf        bool    `json:"leaf"`
}
type flowModel struct {
	Version  string       `json:"version"`
	Features []string     `json:"features"`
	Baseline float64      `json:"baseline"`
	Trees    [][]treeNode `json:"trees"`
	Hash     string       `json:"source_sha256"`
}

var flowModels = loadFlowModels()

func loadFlowModels() map[string]flowModel {
	out := map[string]flowModel{}
	for _, k := range []string{"1430", "1445"} {
		raw, e := modelFiles.ReadFile("models/model_" + k + ".json")
		if e != nil {
			panic(e)
		}
		var m flowModel
		if e = json.Unmarshal(raw, &m); e != nil {
			panic(e)
		}
		out[k] = m
	}
	return out
}
func (m flowModel) score(x []float64) float64 {
	sum := m.Baseline
	for _, tree := range m.Trees {
		i := 0
		for !tree[i].Leaf {
			n := tree[i]
			v := x[n.Feature]
			left := v <= n.Threshold
			if math.IsNaN(v) {
				left = n.MissingLeft
			}
			if left {
				i = n.Left
			} else {
				i = n.Right
			}
		}
		sum += tree[i].Value
	}
	return 1 / (1 + math.Exp(-sum))
}
func quantile(x []float64, q float64) float64 {
	a := append([]float64(nil), x...)
	sort.Float64s(a)
	p := q * float64(len(a)-1)
	i := int(p)
	if i+1 == len(a) {
		return a[i]
	}
	return a[i] + (a[i+1]-a[i])*(p-float64(i))
}
func statsFeatures(f map[string]float64, prefix string, values []float64) {
	n := float64(len(values))
	sum, max, pos, a30, a50 := 0., values[0], 0., 0., 0.
	for _, v := range values {
		sum += v
		if v > max {
			max = v
		}
		if v > 0 {
			pos++
		}
		if v > 30 {
			a30++
		}
		if v > 50 {
			a50++
		}
	}
	mean := sum / n
	variance := 0.
	for _, v := range values {
		variance += (v - mean) * (v - mean)
	}
	idx := len(values) - 16
	if idx < 0 {
		idx = 0
	}
	f[prefix+"_mean_bp"] = mean
	f[prefix+"_max_bp"] = max
	f[prefix+"_premium_bp"] = values[len(values)-1]
	f[prefix+"_positive_fraction"] = pos / n
	f[prefix+"_above30_fraction"] = a30 / n
	f[prefix+"_above50_fraction"] = a50 / n
	f[prefix+"_change15_bp"] = values[len(values)-1] - values[idx]
	f[prefix+"_std_bp"] = math.Sqrt(variance / n)
	f[prefix+"_p10_bp"] = quantile(values, .1)
}

type modelCoverageError struct {
	actual, expected int
	minimum          float64
}

func (e *modelCoverageError) Error() string {
	return fmt.Sprintf("模型完整分钟覆盖不足%.0f%%或不足30分钟（%d/%d）", e.minimum*100, e.actual, e.expected)
}
func (s *Service) modelFeatures(points []Point, b iopv.Basket, date, cutoff string, fx iopv.FX) (map[string]float64, error) {
	return s.modelFeaturesCoverage(points, b, date, cutoff, fx, .95)
}
func (s *Service) modelFeaturesCoverage(points []Point, b iopv.Basket, date, cutoff string, fx iopv.FX, minimum float64) (map[string]float64, error) {
	if b.Date != date || b.PrevDate == "" || !positive(b.PrevNAV) || b.Unit <= 0 || b.CreationAllowed == nil || b.RedemptionAllowed == nil {
		return nil, fmt.Errorf("当日PCF缺少模型元数据（前日日期/NAV/申赎开关）")
	}
	f := map[string]float64{}
	rows, e := s.store.DB.Query("SELECT trade_date,shares_10k,share_change_10k FROM daily_shares WHERE symbol=? AND trade_date<? ORDER BY trade_date DESC LIMIT 5", b.Symbol, date)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	count := 0
	prev := 0.
	for rows.Next() {
		var d string
		var shares, change *float64
		if e = rows.Scan(&d, &shares, &change); e != nil {
			return nil, e
		}
		if !positive(shares) || change == nil || *shares-*change <= 0 {
			return nil, fmt.Errorf("历史份额值异常")
		}
		pct := *change / (*shares - *change) * 100
		if count == 0 {
			if d != b.PrevDate {
				return nil, fmt.Errorf("历史份额最新日期%s与PCF前日%s不一致", d, b.PrevDate)
			}
			prev = *shares * 10000
			f["lag_flow_pct"] = pct
		}
		f["lag5_flow_pct"] += pct
		count++
	}
	if e = rows.Err(); e != nil {
		return nil, e
	}
	if count != 5 {
		return nil, fmt.Errorf("历史份额不足5期")
	}
	rows.Close()
	f["log_prev_assets"] = math.Log(prev * *b.PrevNAV)
	f["v2_log_previous_U"] = math.Log1p(prev / b.Unit)
	f["v2_is_sh"] = 1
	f["creation_allowed"] = 0
	f["redemption_allowed"] = 0
	if *b.CreationAllowed {
		f["creation_allowed"] = 1
	}
	if *b.RedemptionAllowed {
		f["redemption_allowed"] = 1
	}
	for k, v := range map[string]*float64{"creation": b.CreationLimit, "redemption": b.RedemptionLimit} {
		f["v2_"+k+"_limit_pct"] = math.NaN()
		f["v2_"+k+"_limit_known"] = 0
		if v != nil && !math.IsNaN(*v) && !math.IsInf(*v, 0) && *v >= 0 && *v < 1e14 {
			f["v2_"+k+"_limit_pct"] = *v / prev * 100
			f["v2_"+k+"_limit_known"] = 1
		}
	}
	by := map[string]Point{}
	for _, p := range points {
		if modelPointUsable(p, date, cutoff) {
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

	// Use per-minute increments, including invalid-price predecessor bars. This
	// excludes turnover in price-invalid minutes just as the training feature does.
	all := map[string]Point{}
	for _, p := range points {
		if p.Date == date && p.Minute < cutoff && Day(p.At) == date && p.At.In(Zone).Format("15:04") < cutoff && p.CumulativeAmount != nil {
			if old, ok := all[p.Minute]; !ok || p.At.After(old.At) {
				all[p.Minute] = p
			}
		}
	}
	ordered := []Point{}
	for _, p := range all {
		ordered = append(ordered, p)
	}
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Minute < ordered[j].Minute })
	increments := map[string]float64{}
	var prior *Point
	for i := range ordered {
		p := &ordered[i]
		v := *p.CumulativeAmount
		if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
			return nil, fmt.Errorf("累计成交额非有限值")
		}
		if prior != nil && v < *prior.CumulativeAmount {
			return nil, fmt.Errorf("累计成交额倒退")
		}
		if p.Minute == "09:30" {
			increments[p.Minute] = v
		} else if prior != nil {
			t, _ := time.Parse("15:04", p.Minute)
			pt, _ := time.Parse("15:04", prior.Minute)
			if t.Sub(pt) == time.Minute || p.Minute == "13:00" && prior.Minute >= "11:29" && prior.Minute <= "13:00" {
				increments[p.Minute] = v - *prior.CumulativeAmount
			}
		}
		prior = p
	}
	turnover := 0.
	ep, mp, lp := []float64{}, []float64{}, []float64{}
	cum := 0.
	first := true
	lastMinute := ""
	firstMinute := ""
	for _, p := range ps {
		if p.Hash != b.Hash {
			return nil, fmt.Errorf("盘中PCF版本混用")
		}
		amount, amountOK := increments[p.Minute]
		if !amountOK || p.HKDAssets == nil || p.CNYAssets == nil || p.Unit != b.Unit || p.CumulativeAmount == nil {
			continue
		}
		if *p.CumulativeAmount < 0 || (!first && *p.CumulativeAmount < cum) {
			return nil, fmt.Errorf("累计成交额倒退/异常")
		}
		mid := (*p.HKDAssets*fx.Midpoint + *p.CNYAssets) / b.Unit
		buy := (*p.HKDAssets*fx.Settlement + *p.CNYAssets) / b.Unit
		if mid <= 0 || buy <= 0 {
			continue
		}
		turnover += amount
		ep = append(ep, *p.ETF)
		mp = append(mp, (*p.ETF/mid-1)*10000)
		lp = append(lp, (*p.ETF/buy-1)*10000)
		if first {
			f["first_basket"] = mid
			firstMinute = p.Minute
		}
		first = false
		cum = *p.CumulativeAmount
		f["last_basket"] = mid
		f["fx_gap_bp"] = (mid/buy - 1) * 10000
		lastMinute = p.Minute
	}
	n := len(ep)
	if n < 30 || float64(n)/float64(rankExpected(cutoff)) < minimum {
		return nil, &modelCoverageError{n, rankExpected(cutoff), minimum}
	}
	// Match training: returns start at the first usable minute, subject to 95% coverage.
	firstTime, _ := time.Parse("15:04", firstMinute)
	f["first_minute_id"] = float64(firstTime.Hour()*60 + firstTime.Minute())
	t, _ := time.Parse("15:04", cutoff)
	expectedLast := t.Add(-time.Minute).Format("15:04")
	if cutoff == "13:00" {
		expectedLast = "11:29"
	}
	if lastMinute != expectedLast {
		return nil, fmt.Errorf("模型最新分钟不完整")
	}
	statsFeatures(f, "mid", mp)
	statsFeatures(f, "settlement", lp)
	f["etf_return_bp"] = (ep[n-1]/ep[0] - 1) * 10000
	f["basket_return_bp"] = (f["last_basket"]/f["first_basket"] - 1) * 10000
	f["turnover_pct"] = turnover / (prev * *b.PrevNAV) * 100
	lastTime, _ := time.Parse("15:04", lastMinute)
	f["last_minute_id"] = float64(lastTime.Hour()*60 + lastTime.Minute())
	f["model_minutes"] = float64(n)
	return f, nil
}
func (s *Service) applyFlowModel(r *RankingRow, points []Point, b iopv.Basket, now time.Time, raw []byte) {
	r.HeuristicScore = r.Score
	r.Score = nil
	r.ModelQuality = "不可评分"
	r.FXBasis = "T日中间价 + T日港股通结算实时预估"
	r.Features = map[string]*float64{}
	cut := now.In(Zone).Format("15:04")
	if cut > "15:00" {
		cut = "15:00"
	}
	key := "1445"

	m := flowModels[key]
	r.ModelVersion = m.Version
	r.ModelHash = m.Hash
	r.TimeScope = "非训练时点参考"
	if cut == "14:45" {
		r.TimeScope = "原训练时点（实时预估汇率仍需验证）"
	}
	fx, e := iopv.ParseFX(raw, Day(now), "shanghai", "buy_hk", now)
	if e != nil {
		r.Pass = false
		r.Reasons = append(r.Reasons, "模型汇率质控："+e.Error())
		return
	}
	r.BuyFX = ptr(fx.Settlement)
	r.MidFX = ptr(fx.Midpoint)
	r.FXAt = fx.ObservedAt
	f, e := s.modelFeatures(points, b, Day(now), cut, fx)
	if e != nil {
		r.Pass = false
		r.Reasons = append(r.Reasons, e.Error())
		return
	}
	s.setFlowFeatures(r, f, fx, cut)
}
func (s *Service) setFlowFeatures(r *RankingRow, f map[string]float64, fx iopv.FX, cut string) {
	m := flowModels["1445"]
	x := make([]float64, len(m.Features))
	for i, k := range m.Features {
		v, ok := f[k]
		if !ok || math.IsInf(v, 0) || math.IsNaN(v) && k != "v2_creation_limit_pct" && k != "v2_redemption_limit_pct" {
			r.Pass = false
			r.Reasons = append(r.Reasons, "模型特征异常："+k)
			return
		}
		x[i] = v
		if !math.IsNaN(v) {
			r.Features[k] = ptr(v)
		} else {
			r.Features[k] = nil
		}
	}
	fm := int(f["first_minute_id"])
	r.FirstMinute = fmt.Sprintf("%02d:%02d", fm/60, fm%60)
	lm := int(f["last_minute_id"])
	r.LastMinute = fmt.Sprintf("%02d:%02d", lm/60, lm%60)
	r.Minutes = int(f["model_minutes"])
	r.Coverage = f["model_minutes"] / float64(rankExpected(cut))
	r.Score = ptr(m.score(x))
	r.Comparison1430 = ptr(flowModels["1430"].score(x))
	r.ModelQuality = "输入完整"
	r.MeanBP = f["settlement_mean_bp"]
	r.P10BP = f["settlement_p10_bp"]
	r.LastBP = f["settlement_premium_bp"]
	r.Positive = f["settlement_positive_fraction"]
	r.FXGapBP = f["fx_gap_bp"]
	// Recompute the gate using the exact same fixed T-day FX features passed to the model.
	r.Reasons = []string{}
	if r.MeanBP < 10 {
		r.Reasons = append(r.Reasons, "平均溢价低于10bp")
	}
	if r.Positive < .7 {
		r.Reasons = append(r.Reasons, "正溢价时间不足70%")
	}
	if r.LastBP <= 0 || r.FXGapBP <= 0 {
		r.Reasons = append(r.Reasons, "最新溢价/中间价优势非正")
	}
	if f["creation_allowed"] != 1 || f["redemption_allowed"] != 1 {
		r.Reasons = append(r.Reasons, "PCF申赎未同时开放")
	}
	if !fx.Actionable {
		r.Reasons = append(r.Reasons, "汇率模型仅供参考")
	}
	if r.Symbol == "513130.SH" {
		r.Reasons = append(r.Reasons, "历史质量排除")
	}
	r.Pass = len(r.Reasons) == 0
}

// FX is validated once at inference time, then applied to every recorded asset
// basket. A historical FX outage does not invalidate independently sound assets.
func modelPointUsable(p Point, date, cutoff string) bool {
	if p.Date != date || !rankMinute(p.Minute) || p.Minute >= cutoff || Day(p.At) != date || p.At.In(Zone).Format("15:04") >= cutoff || !positive(p.ETF) || p.HKDAssets == nil || p.CNYAssets == nil {
		return false
	}
	for _, v := range []float64{*p.HKDAssets, *p.CNYAssets} {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return false
		}
	}
	if *p.HKDAssets < 0 {
		return false
	}
	for _, reason := range p.Reasons {
		if reason == "STALE_COMPONENT_MARKS" && verifiedLastTrade(p) {
			continue
		}
		if reason != "SETTLEMENT_FX_UNAVAILABLE" && reason != "MIDPOINT_MISSING" {
			return false
		}
	}
	return true
}
