package live

// Stock Connect eligibility is display-only; it is never input to valuation or scoring.
import (
	"context"
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const connectSSEPage = "https://www.sse.com.cn/services/hkexsc/disclo/eligible/"
const connectSZSEPage = "https://www.szse.cn/szhk/hkbussiness/underlylist/"
const connectSSEAPI = "https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_JYFW_HGT_XXPL_BDZQQD_L&isPagination=true&pageHelp.pageSize=2000&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.endPage=1&pageHelp.cacheSize=1"
const connectSZSEAPI = "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=SGT_GGTBDQD&TABKEY=tab1&PAGENO="

var connectCode = regexp.MustCompile(`^[0-9]{5}$`)

type connectList struct {
	Date  string            `json:"date"`
	Codes map[string]string `json:"codes"`
}
type connectSnapshot struct {
	CheckedAt string      `json:"checked_at"`
	Shanghai  connectList `json:"shanghai"`
	Shenzhen  connectList `json:"shenzhen"`
}

func (v connectSnapshot) valid() bool {
	for _, l := range []connectList{v.Shanghai, v.Shenzhen} {
		if _, e := time.Parse("2006-01-02", l.Date); e != nil || len(l.Codes) < 100 {
			return false
		}
		for c := range l.Codes {
			if !connectCode.MatchString(c) {
				return false
			}
		}
	}
	_, e := time.Parse(time.RFC3339, v.CheckedAt)
	return e == nil
}
func connectJSON(ctx context.Context, client *http.Client, url, referer string, v any) error {
	req, e := http.NewRequestWithContext(ctx, "GET", url, nil)
	if e != nil {
		return e
	}
	req.Header.Set("User-Agent", "Mozilla/5.0")
	req.Header.Set("Referer", referer)
	res, e := client.Do(req)
	if e != nil {
		return e
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return fmt.Errorf("eligibility HTTP %d", res.StatusCode)
	}
	return json.NewDecoder(io.LimitReader(res.Body, 4<<20)).Decode(v)
}
func fetchConnect(ctx context.Context, client *http.Client, now time.Time) (connectSnapshot, error) {
	v := connectSnapshot{CheckedAt: now.Format(time.RFC3339), Shanghai: connectList{Codes: map[string]string{}}, Shenzhen: connectList{Codes: map[string]string{}}}
	var sh struct {
		Result []struct {
			Code string `json:"SECURITY_CODE"`
			Name string `json:"ABBR_CN"`
			Date string `json:"UPDATE_DATE"`
		}
		PageHelp struct{ Total int }
	}
	if e := connectJSON(ctx, client, connectSSEAPI, connectSSEPage, &sh); e != nil {
		return v, e
	}
	for _, r := range sh.Result {
		if v.Shanghai.Date != "" && r.Date != v.Shanghai.Date {
			return v, fmt.Errorf("Shanghai list has mixed dates")
		}
		v.Shanghai.Date = r.Date
		v.Shanghai.Codes[r.Code] = strings.TrimSpace(r.Name)
	}
	if len(v.Shanghai.Codes) != sh.PageHelp.Total {
		return v, fmt.Errorf("incomplete Shanghai list")
	}
	total := 0
	for page := 1; page <= 100; page++ {
		var sz []struct {
			Metadata struct {
				Subname     string
				Pagecount   int
				Recordcount int
				Pageno      int
			}
			Data []struct {
				Code string `json:"zqdm"`
				Name string `json:"zqjc"`
			}
		}
		if e := connectJSON(ctx, client, fmt.Sprint(connectSZSEAPI, page), connectSZSEPage, &sz); e != nil {
			return v, e
		}
		if len(sz) != 1 || sz[0].Metadata.Pageno != page || sz[0].Metadata.Pagecount < 1 || sz[0].Metadata.Pagecount > 100 {
			return v, fmt.Errorf("invalid Shenzhen pagination")
		}
		m := sz[0].Metadata
		if page > 1 && (m.Subname != v.Shenzhen.Date || m.Recordcount != total) {
			return v, fmt.Errorf("Shenzhen list changed while fetching")
		}
		v.Shenzhen.Date = m.Subname
		total = m.Recordcount
		for _, r := range sz[0].Data {
			if _, ok := v.Shenzhen.Codes[r.Code]; ok {
				return v, fmt.Errorf("duplicate Shenzhen security")
			}
			v.Shenzhen.Codes[r.Code] = strings.TrimSpace(r.Name)
		}
		if page == m.Pagecount {
			break
		}
	}
	if len(v.Shenzhen.Codes) != total || !v.valid() {
		return v, fmt.Errorf("incomplete eligibility lists")
	}
	if v.Shanghai.Date > Day(now) || v.Shenzhen.Date > Day(now) {
		return v, fmt.Errorf("future eligibility list")
	}
	return v, nil
}
func (s *Service) loadConnect() {
	raw, e := os.ReadFile(filepath.Join(s.cfg.DataDir, "connect-eligibility.json"))
	if e != nil {
		return
	}
	var v connectSnapshot
	if json.Unmarshal(raw, &v) == nil && v.valid() {
		s.connect = v
	}
}
func (s *Service) connectLoop(ctx context.Context) {
	ticker := time.NewTicker(time.Hour)
	defer ticker.Stop()
	for {
		now := time.Now()
		if now.Weekday() != time.Saturday && now.Weekday() != time.Sunday {
			c, cancel := context.WithTimeout(ctx, 2*time.Minute)
			v, e := fetchConnect(c, &http.Client{Timeout: 15 * time.Second}, now)
			cancel()
			if e == nil {
				raw, _ := json.Marshal(v)
				path := filepath.Join(s.cfg.DataDir, "connect-eligibility.json")
				e = os.WriteFile(path+".tmp", raw, 0600)
				if e == nil {
					e = os.Rename(path+".tmp", path)
				}
				if e == nil {
					s.mu.Lock()
					s.connect = v
					s.mu.Unlock()
				}
			}
			s.setError("connect_eligibility", e)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

type connectComponent struct {
	Symbol   string  `json:"symbol"`
	Name     string  `json:"name"`
	Quantity float64 `json:"quantity"`
	Mode     int     `json:"mode"`
}

func nonConnectComponents(b iopv.Basket, v connectSnapshot) []connectComponent {
	out := []connectComponent{}
	seen := map[string]bool{}
	for _, c := range b.Components {
		if !strings.HasSuffix(c.Symbol, ".HK") || seen[c.Symbol] {
			continue
		}
		code := strings.TrimSuffix(c.Symbol, ".HK")
		if !connectCode.MatchString(code) {
			continue
		}
		seen[c.Symbol] = true
		_, sh := v.Shanghai.Codes[code]
		_, sz := v.Shenzhen.Codes[code]
		if !sh && !sz {
			out = append(out, connectComponent{c.Symbol, c.Name, c.Quantity, c.Mode})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Symbol < out[j].Symbol })
	return out
}
func (s *Service) connectHandler(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	v := s.connect
	b, ok := s.baskets[r.URL.Query().Get("symbol")]
	err := s.errors["connect_eligibility"]
	s.mu.RUnlock()
	status := "ready"
	if !v.valid() {
		status = "list_unavailable"
	} else if !ok || b.Date != Day(time.Now()) {
		status = "pcf_unavailable"
	}
	out := []connectComponent{}
	if status == "ready" {
		out = nonConnectComponents(b, v)
	}
	writeJSON(w, map[string]any{"status": status, "symbol": r.URL.Query().Get("symbol"), "pcf_date": b.Date, "pcf_sha256": b.Hash, "checked_at": v.CheckedAt, "shanghai_date": v.Shanghai.Date, "shenzhen_date": v.Shenzhen.Date, "shanghai_count": len(v.Shanghai.Codes), "shenzhen_count": len(v.Shenzhen.Codes), "components": out, "refresh_error": err, "shanghai_source": connectSSEPage, "shenzhen_source": connectSZSEPage, "basis": "current_buy_eligible_union", "formula_changed": false})
}
