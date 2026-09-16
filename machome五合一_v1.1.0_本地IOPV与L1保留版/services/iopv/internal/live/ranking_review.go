package live

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	iopv "intranet-iopv"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// ReviewRanking is read-only. Original captured scores and snapshots are retained.
func ReviewRanking(dbPath, recoveryDir string, original Ranking) (Ranking, error) {
	u := url.URL{Scheme: "file", Path: dbPath, RawQuery: "mode=ro&_busy_timeout=3000"}
	db, e := sql.Open("sqlite3", u.String())
	if e != nil {
		return original, e
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	s := &Service{store: &Store{DB: db}}
	baskets := map[string]iopv.Basket{}
	rows, e := db.Query("SELECT symbol,hash,raw FROM pcf WHERE trade_date=?", original.Date)
	if e != nil {
		return original, e
	}
	for rows.Next() {
		var symbol, hash string
		var raw []byte
		if e = rows.Scan(&symbol, &hash, &raw); e != nil {
			rows.Close()
			return original, e
		}
		raw, e = decodeArchive(raw)
		if e != nil {
			continue
		}
		b, err := iopv.ParsePCF(raw, symbol, original.Date)
		if err == nil && b.Hash == hash {
			baskets[hash] = b
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return original, e
	}
	recovered := map[string]map[string]Point{}
	file, e := os.Open(filepath.Join(recoveryDir, "ranking-no-trade-"+original.Date+".jsonl"))
	if e != nil && !os.IsNotExist(e) {
		return original, e
	}
	if e == nil {
		defer file.Close()
		scan := bufio.NewScanner(file)
		scan.Buffer(make([]byte, 65536), 1<<20)
		for scan.Scan() {
			var p Point
			if json.Unmarshal(scan.Bytes(), &p) == nil && p.Date == original.Date && p.Mode == "historical_no_trade_verified" && p.RecoverySource == "sina_hk_minline.no_trade.v1" && p.RecoveredAt != "" {
				if recovered[p.Symbol] == nil {
					recovered[p.Symbol] = map[string]Point{}
				}
				recovered[p.Symbol][p.Minute] = p
			}
		}
		if e = scan.Err(); e != nil {
			return original, e
		}
	}
	out := original
	out.Rows = append([]RankingRow(nil), original.Rows...)
	additional := 0
	for i := range out.Rows {
		r := &out.Rows[i]
		if r.Score != nil {
			continue
		}
		r.Reasons = append([]string(nil), r.Reasons...)
		ps, err := s.store.History(r.Symbol, original.Date)
		if err != nil {
			r.Reasons = append(r.Reasons, "复评历史读取失败")
			continue
		}
		if len(ps) == 0 {
			r.Reasons = []string{"没有分钟档案；检查当日PCF及采集"}
			if strings.Contains(strings.ToUpper(r.Name), "TOPIX") {
				r.Reasons = []string{"日股TOPIX标的，不适用于港股结算汇率模型；未取得当日PCF"}
			}
			continue
		}
		verified := 0
		var b iopv.Basket
		for j := range ps {
			p := &ps[j]
			if candidate, ok := baskets[p.Hash]; ok {
				b = candidate
			}
			v, ok := recovered[r.Symbol][p.Minute]
			if ok && v.Hash == p.Hash && v.At.Equal(p.At) && equalNumber(v.HKDAssets, p.HKDAssets) && equalNumber(v.CNYAssets, p.CNYAssets) && equalNumber(v.ETF, p.ETF) {
				reasons := []string{}
				for _, reason := range p.Reasons {
					if reason != "STALE_COMPONENT_MARKS" {
						reasons = append(reasons, reason)
					}
				}
				p.Reasons = reasons
				verified++
			}
		}
		if b.Symbol == "" {
			r.Reasons = append(r.Reasons, "当日PCF档案缺失/解析失败")
			continue
		}
		if !positive(r.MidFX) || !positive(r.BuyFX) {
			continue
		}
		// Use the FX saved at the original cutoff, never a later final settlement.
		fx := iopv.FX{Midpoint: *r.MidFX, Settlement: *r.BuyFX, ObservedAt: r.FXAt, Actionable: false}
		f, err := s.modelFeaturesCoverage(ps, b, original.Date, original.Cutoff, fx, .95)
		reference := false
		if err != nil && strings.Contains(err.Error(), "模型完整分钟覆盖不足") {
			f, err = s.modelFeaturesCoverage(ps, b, original.Date, original.Cutoff, fx, .80)
			reference = err == nil
		}
		if err != nil {
			r.Reasons = []string{"复评：" + err.Error()}
			var coverage *modelCoverageError
			if errors.As(err, &coverage) {
				r.Minutes = coverage.actual
				if coverage.expected > 0 {
					r.Coverage = float64(coverage.actual) / float64(coverage.expected)
				}
			}
			missing := map[string]int{}
			for _, p := range ps {
				if !rankMinute(p.Minute) || p.Minute >= original.Cutoff {
					continue
				}
				for _, v := range p.Missing {
					missing[v]++
				}
			}
			symbols := []string{}
			for v := range missing {
				symbols = append(symbols, v)
			}
			sort.Slice(symbols, func(i, j int) bool {
				if missing[symbols[i]] != missing[symbols[j]] {
					return missing[symbols[i]] > missing[symbols[j]]
				}
				return symbols[i] < symbols[j]
			})
			if len(symbols) > 0 {
				note := ""
				if len(symbols) > 8 {
					note = fmt.Sprintf("等%d只", len(symbols))
					symbols = symbols[:8]
				}
				r.Reasons = append(r.Reasons, "缺价记录最多："+strings.Join(symbols, "、")+note)
			}
			continue
		}
		r.Features = map[string]*float64{}
		r.ModelVersion = flowModels["1445"].Version
		r.ModelHash = flowModels["1445"].Hash
		s.setFlowFeatures(r, f, fx, original.Cutoff)
		r.Pass = false
		label := "复评 · 完整分钟"
		if reference {
			label = "参考评分 · 覆盖80%–95%"
		}
		if verified > 0 {
			label += fmt.Sprintf(" · 新浪核验无新成交%d分钟", verified)
		}
		r.ModelQuality = label
		r.TimeScope = "当前档案复评；保留原盘中快照"
		r.Reasons = append(r.Reasons, label)
		additional++
	}
	sort.SliceStable(out.Rows, func(i, j int) bool {
		a, b := out.Rows[i], out.Rows[j]
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
	reviewed := time.Now()
	out.ReviewedAt = &reviewed
	out.Version = "premium-model.review.v1"
	out.Status = fmt.Sprintf("原盘中评分 + 当前档案复评（新增%d只）；参考分不代表通过初筛；最低80%%有效分钟，原95%%门槛可切回查看", additional)
	return out, nil
}
func equalNumber(a, b *float64) bool { return a != nil && b != nil && *a == *b }

func RankingReviewHandler(upstream url.URL, dbPath string) http.Handler {
	var mu sync.Mutex
	type entry struct {
		at   time.Time
		body []byte
	}
	cache := map[string]entry{}
	client := &http.Client{Timeout: 20 * time.Second}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "GET required", 405)
			return
		}
		date := r.URL.Query().Get("date")
		if date == "" {
			date = Day(time.Now())
		}
		if _, e := time.Parse("2006-01-02", date); e != nil {
			http.Error(w, "bad date", 400)
			return
		}
		mu.Lock()
		defer mu.Unlock()
		key := r.URL.RawQuery
		if c, ok := cache[key]; ok && time.Since(c.at) < time.Minute {
			w.Header().Set("Content-Type", "application/json")
			w.Write(c.body)
			return
		}
		target := upstream
		target.Path = "/api/v1/ranking"
		q := r.URL.Query()
		q.Del("review")
		target.RawQuery = q.Encode()
		req, _ := http.NewRequestWithContext(r.Context(), "GET", target.String(), nil)
		response, e := client.Do(req)
		if e != nil {
			http.Error(w, "ranking upstream unavailable", 502)
			return
		}
		defer response.Body.Close()
		if response.StatusCode != 200 {
			http.Error(w, "该时点未采集快照", response.StatusCode)
			return
		}
		var original Ranking
		if json.NewDecoder(response.Body).Decode(&original) != nil {
			http.Error(w, "invalid ranking", 502)
			return
		}
		result := original
		if len(original.Rows) > 0 {
			result, e = ReviewRanking(dbPath, filepath.Dir(dbPath), original)
			if e != nil {
				http.Error(w, "复评暂不可用，请取消包含复评查看原快照", 503)
				return
			}
		}
		body, e := json.Marshal(result)
		if e != nil {
			http.Error(w, "invalid review", 500)
			return
		}
		if len(cache) > 20 {
			cache = map[string]entry{}
		}
		cache[key] = entry{time.Now(), body}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		w.Write(body)
	})
}
