package live

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type Config struct {
	SinaFallbackSymbols []string `json:"sina_fallback_symbols"`
	SignalBasis         string   `json:"signal_basis"`
	ParentPID           int      `json:"parent_pid"`
	L1Address           string   `json:"l1_address"`
	Listen              string   `json:"listen"`
	DataDir             string   `json:"data_dir"`
	Account             string   `json:"account_file"`
	Feed                string   `json:"feed_binary"`
	CA                  string   `json:"ca_file"`
	Universe            string   `json:"universe_file"`
}
type Service struct {
	sinaQuotes      map[string]Quote
	sinaLastAttempt time.Time
	nowForSchedule  func() time.Time
	signalSettings  SignalSettings
	mu              sync.RWMutex
	cfg             Config
	store           *Store
	candidates      []Candidate
	baskets         map[string]iopv.Basket
	quotes          map[string]Quote
	latest          map[string]Point
	pending         map[string]map[string]Point
	fxRaw           []byte
	plan            iopv.Plan
	errors          map[string]string
	feedState       string
	feedAt          time.Time
	session         uint64
	sequence        uint64
	runID           string
	started         time.Time
	lastWrite       time.Time
	written         int64
	frames          int64
	rejected        int64
	paused          bool
	refresh         chan struct{}
	restart         chan struct{}
	refreshMu       sync.Mutex
	activeDay       string
	pcfLastAttempt  time.Time
	pcfCompleteDate string
}

func New(cfg Config) (*Service, error) {
	var u struct {
		Candidates []Candidate `json:"candidates"`
	}
	raw, e := os.ReadFile(cfg.Universe)
	if e != nil {
		return nil, e
	}
	if e = json.Unmarshal(raw, &u); e != nil {
		return nil, e
	}
	st, e := OpenStore(filepath.Join(cfg.DataDir, "iopv.sqlite"))
	if e != nil {
		return nil, e
	}
	s := &Service{cfg: cfg, store: st, candidates: u.Candidates, baskets: map[string]iopv.Basket{}, quotes: map[string]Quote{}, latest: map[string]Point{}, pending: map[string]map[string]Point{}, errors: map[string]string{}, feedState: "starting", refresh: make(chan struct{}, 1), restart: make(chan struct{}, 1), runID: fmt.Sprint(time.Now().UnixNano()), started: time.Now(), activeDay: Day(time.Now())}
	s.signalSettings = defaultSignalSettings()
	if raw, e := os.ReadFile(filepath.Join(cfg.DataDir, "signal-settings.json")); e == nil {
		var v SignalSettings
		if json.Unmarshal(raw, &v) != nil || v.validate() != nil || v.Revision < 1 {
			return nil, fmt.Errorf("invalid saved signal settings")
		}
		s.signalSettings = v
	}
	if e := s.loadCachedPCF(); e != nil {
		return nil, e
	}
	return s, nil
}
func (s *Service) setError(key string, e error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e == nil {
		delete(s.errors, key)
	} else {
		s.errors[key] = e.Error()
	}
}
func (s *Service) RefreshPCF(ctx context.Context) {
	if !s.refreshMu.TryLock() {
		return
	}
	defer s.refreshMu.Unlock()
	date := Day(time.Now())
	s.mu.Lock()
	s.pcfLastAttempt = time.Now()
	s.mu.Unlock()
	jobs := make(chan Candidate)
	var wg sync.WaitGroup
	next := map[string]iopv.Basket{}
	s.mu.RLock()
	for k, b := range s.baskets {
		if b.Date == date {
			next[k] = b
		}
	}
	s.mu.RUnlock()
	var lock sync.Mutex
	for w := 0; w < 4; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for c := range jobs {
				var b iopv.Basket
				var raw []byte
				var e error
				// Cache is trusted only after parsing identity/date again; published revisions
				// are fetched on subsequent refresh and atomically replace the whole basket.
				b, raw, e = iopv.FetchPCF(ctx, &http.Client{Timeout: 18 * time.Second}, c.Symbol, date)
				if e != nil {
					var cached []byte
					er := s.store.DB.QueryRow("SELECT raw FROM pcf WHERE symbol=? AND trade_date=? ORDER BY fetched_at DESC LIMIT 1", c.Symbol, date).Scan(&cached)
					if er == nil {
						cached, er = decodeArchive(cached)
						if er == nil {
							b, er = iopv.ParsePCF(cached, c.Symbol, date)
						}
						if er == nil {
							raw = cached
							e = nil
						}
					}
				}
				s.setError("pcf:"+c.Symbol, e)
				if e != nil {
					continue
				}
				if _, e = s.store.DB.Exec("INSERT OR IGNORE INTO pcf VALUES(?,?,?,?,?)", b.Symbol, b.Date, b.Hash, archiveBytes(raw), time.Now().Format(time.RFC3339Nano)); e != nil {
					s.setError("storage", e)
					continue
				}
				lock.Lock()
				next[c.Symbol] = b
				lock.Unlock()
			}
		}()
	}
	for _, c := range s.candidates {
		select {
		case jobs <- c:
		case <-ctx.Done():
			close(jobs)
			wg.Wait()
			return
		}
	}
	close(jobs)
	wg.Wait()
	bs := []iopv.Basket{}
	for _, b := range next {
		bs = append(bs, b)
	}
	plan, e := iopv.BuildPlan(bs)
	if e != nil {
		s.setError("plan", e)
		return
	}
	s.mu.Lock()
	if date != Day(time.Now()) || (s.activeDay != "" && s.activeDay != date) {
		s.mu.Unlock()
		return
	}
	changed := strings.Join(s.plan.Subscriptions, "\n") != strings.Join(plan.Subscriptions, "\n")
	for symbol, b := range next {
		if old, ok := s.baskets[symbol]; !ok || old.Hash != b.Hash {
			delete(s.latest, symbol)
		}
	}
	s.baskets = next
	s.plan = plan
	if len(next) == len(s.candidates) && len(next) > 0 {
		s.pcfCompleteDate = date
	}
	s.mu.Unlock()
	if changed {
		select {
		case s.restart <- struct{}{}:
		default:
		}
	}
	s.store.Event("pcf", fmt.Sprintf("%s loaded %d/%d", date, len(next), len(s.candidates)))
}
func (s *Service) RefreshFX(ctx context.Context) {
	req, _ := http.NewRequestWithContext(ctx, "GET", "https://1navs.com/api/v1/private/hk-connect-fx", nil)
	resp, e := (&http.Client{Timeout: 15 * time.Second}).Do(req)
	if e != nil {
		s.setError("fx", fmt.Errorf("FX network unavailable"))
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		s.setError("fx", fmt.Errorf("FX HTTP %d", resp.StatusCode))
		return
	}
	raw, e := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var snapshot iopv.FXSnapshot
	if e == nil {
		e = json.Unmarshal(raw, &snapshot)
	}
	if e == nil && (snapshot.Schema != "hk-connect-fx.v7" || snapshot.Date != Day(time.Now())) {
		e = fmt.Errorf("FX schema/date mismatch")
	}
	if e != nil {
		s.setError("fx", e)
		return
	}
	s.mu.Lock()
	s.fxRaw = raw
	s.mu.Unlock()
	s.setError("fx", nil)
	e = s.store.WriteFX(snapshot.Generated.Format(time.RFC3339Nano), snapshot.Date, raw)
	s.setError("fx_storage", e)
}
func (s *Service) Run(ctx context.Context) {
	go s.feedLoop(ctx)
	go s.sinaLoop(ctx)
	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			now := time.Now()
			s.mu.RLock()
			due := pcfDue(now, s.pcfLastAttempt, s.pcfCompleteDate)
			s.mu.RUnlock()
			if due {
				s.RefreshPCF(ctx)
			}
			select {
			case <-ctx.Done():
				return
			case <-s.refresh:
				s.RefreshPCF(ctx)
			case <-ticker.C:
			}
		}
	}()
	go func() {
		if fxPolling(time.Now()) {
			s.RefreshFX(ctx)
		}
		ticker := time.NewTicker(15 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if fxPolling(time.Now()) {
					s.RefreshFX(ctx)
				}
			}
		}
	}()
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	start := time.Now()
	var scheduler iopv.Scheduler
	for {
		select {
		case <-ctx.Done():
			s.flush(time.Now(), true)
			return
		case now := <-tick.C:
			s.rollDay(now)
			s.mu.RLock()
			paused := s.paused
			s.mu.RUnlock()
			if !paused && calculationWindow(now) {
				if g, ok := scheduler.Due(time.Since(start)); ok {
					s.calculateGroup(now, g)
				}
			}
			s.flush(now, false)
		}
	}
}
func (s *Service) feedLoop(ctx context.Context) {
	if s.cfg.L1Address != "" {
		s.sharedL1Loop(ctx)
		return
	}
	backoff := time.Second
	for {
		if ctx.Err() != nil {
			return
		}
		s.mu.RLock()
		symbols := subscriptionSymbols(s.plan.Subscriptions, time.Now())
		s.mu.RUnlock()
		if len(symbols) == 0 {
			select {
			case <-ctx.Done():
				return
			case <-s.restart:
				continue
			case <-time.After(time.Second):
				continue
			}
		}
		path := filepath.Join(s.cfg.DataDir, "subscriptions.txt")
		if e := os.WriteFile(path, []byte(strings.Join(symbols, "\n")+"\n"), 0600); e != nil {
			s.setError("feed", e)
			return
		}
		child, cancel := context.WithCancel(ctx)
		cmd := exec.CommandContext(child, s.cfg.Feed, s.cfg.Account, path, s.cfg.CA)
		stdout, e := cmd.StdoutPipe()
		if e != nil {
			cancel()
			s.setError("feed", e)
			return
		}
		cmd.Stderr = io.Discard
		s.mu.Lock()
		s.session++
		session := s.session
		s.quotes = map[string]Quote{}
		s.feedState = "connecting"
		s.mu.Unlock()
		if e = cmd.Start(); e != nil {
			cancel()
			s.setError("feed", fmt.Errorf("feed executable could not start"))
			select {
			case <-ctx.Done():
				return
			case <-time.After(5 * time.Second):
			}
			continue
		}
		done := make(chan struct{})
		go func() {
			boundary := time.NewTicker(time.Second)
			defer boundary.Stop()
			for {
				select {
				case <-boundary.C:
					s.mu.RLock()
					next := subscriptionSymbols(s.plan.Subscriptions, time.Now())
					s.mu.RUnlock()
					if strings.Join(next, "\n") != strings.Join(symbols, "\n") {
						cancel()
						return
					}
				case <-s.restart:
					cancel()
					return
				case <-done:
					return
				case <-ctx.Done():
					cancel()
					return
				}
			}
		}()
		decoder := NewDecoder()
		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 64<<10), 2<<20)
		for scanner.Scan() {
			q, err := decoder.Decode(scanner.Bytes(), time.Now(), session)
			s.mu.Lock()
			if err != nil {
				s.rejected++
			} else {
				s.quotes[q.Symbol] = q
				s.frames++
				s.feedAt = time.Now()
				s.feedState = "streaming"
				delete(s.errors, "feed")
				backoff = time.Second
			}
			s.mu.Unlock()
		}
		cmd.Wait()
		close(done)
		cancel()
		s.mu.Lock()
		s.feedState = "reconnecting"
		s.mu.Unlock()
		if ctx.Err() != nil {
			return
		}
		if quoteWindow(time.Now()) {
			s.setError("feed", fmt.Errorf("TGW disconnected; retrying"))
		} else {
			s.setError("feed", nil)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
}
func (s *Service) calculateGroup(now time.Time, g int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sequence++
	date := Day(now)
	states, statusErr := s.store.Suspensions(date)
	names := map[string]string{}
	for _, c := range s.candidates {
		names[c.Symbol] = c.Name
	}
	var fx iopv.FXSnapshot
	json.Unmarshal(s.fxRaw, &fx)
	for _, symbol := range s.plan.Groups[g] {
		b := s.baskets[symbol]
		p := Point{Symbol: symbol, Name: names[symbol], Date: date, At: now, Minute: now.In(Zone).Format("15:04"), Hash: b.Hash, Channel: Channel(symbol), Components: len(b.Components), Missing: []string{}, Stale: []string{}, Reasons: []string{}, Sequence: s.sequence, RunID: s.runID, Mode: "live", Unit: b.Unit, Cash: b.Cash}
		if b.Date != date {
			p.Reasons = append(p.Reasons, "PCF_DATE_MISMATCH")
			s.latest[symbol] = p
			continue
		}
		p.FXAt = fx.Quote.Observed
		p.FXGenerated = fx.Generated
		if statusErr != nil {
			p.Reasons = append(p.Reasons, "SUSPENSION_STATUS_UNAVAILABLE")
		}
		rows := []iopv.CoreRow{}
		for _, c := range b.Components {
			r := iopv.CoreRow{Quantity: c.Quantity, Mode: c.Mode, Cash: c.Cash}
			if c.Mode != 2 {
				if states[c.Symbol].Status == "suspended" {
					p.Suspended = append(p.Suspended, c.Symbol)
				}
				q, ok := s.componentQuote(c.Symbol, now)
				if !ok || q.Price <= 0 || Day(q.Observed) != date || q.Observed.After(now.Add(2*time.Second)) {
					p.Missing = append(p.Missing, c.Symbol)
					if states[c.Symbol].Status == "" && !s.sinaAllowed(c.Symbol) {
						p.SuspensionPending = append(p.SuspensionPending, c.Symbol)
					}
					continue
				}
				if q.Source == "sina_rt_hk" {
					p.FallbackComponents = append(p.FallbackComponents, c.Symbol)
				}
				r.Price = q.Price
				p.Priced++
				if p.Oldest.IsZero() || q.Observed.Before(p.Oldest) {
					p.Oldest = q.Observed
				}
				if now.Sub(q.Observed) > 120*time.Second || now.Sub(q.Received) > 120*time.Second {
					p.Stale = append(p.Stale, c.Symbol)
				}
			} else {
				p.Priced++
			}
			rows = append(rows, r)
		}
		for _, c := range b.Components {
			if c.Mode == 2 {
				continue
			}
			status := ""
			for _, v := range p.Missing {
				if v == c.Symbol {
					status = "missing"
				}
			}
			for _, v := range p.Stale {
				if v == c.Symbol {
					status = "stale"
				}
			}
			state := states[c.Symbol]
			if status == "" && state.Status != "suspended" {
				continue
			}
			if status == "" {
				status = "available"
			}
			q, _ := s.componentQuote(c.Symbol, now)
			p.ComponentIssues = append(p.ComponentIssues, ComponentIssue{Symbol: c.Symbol, Name: c.Name, QuoteStatus: status, SuspensionStatus: state.Status, Note: state.Note, Observed: q.Observed, Source: q.Source})
		}
		if len(p.Suspended) > 0 {
			p.Reasons = append(p.Reasons, "SUSPENDED_COMPONENT")
		}
		if len(p.Missing) > 0 {
			p.Reasons = append(p.Reasons, "QUOTE_MISSING")
		}
		if len(p.Stale) > 0 {
			p.Reasons = append(p.Reasons, "STALE_COMPONENT_MARKS")
		}
		if now.Sub(s.feedAt) > 30*time.Second {
			p.Reasons = append(p.Reasons, "FEED_STALE")
		}
		if fx.Parity != nil && fx.Parity.Date == date && fx.Parity.Pair == "HKD/CNY" && fx.Parity.Rate > 0 {
			p.MidFX = ptr(fx.Parity.Rate)
		} else {
			p.Reasons = append(p.Reasons, "MIDPOINT_MISSING")
		}
		// FX branches fail independently: an unavailable prediction never suppresses a valid midpoint.
		buy, be := SessionFX(s.fxRaw, date, p.Channel, "buy_hk", now)
		sell, se := SessionFX(s.fxRaw, date, p.Channel, "sell_hk", now)
		if be == nil {
			p.BuyFX = ptr(buy.Settlement)
			p.FXAt = buy.ObservedAt
			p.FXStatus = buy.Status
			p.FXModel = buy.Model
			p.FXActionable = buy.Actionable
		}
		if se == nil {
			p.SellFX = ptr(sell.Settlement)
		}
		if be != nil {
			p.FXError = be.Error()
			p.Reasons = append(p.Reasons, "SETTLEMENT_FX_UNAVAILABLE")
		}
		if len(p.Missing) == 0 && len(rows) > 0 && p.MidFX != nil {
			rate := *p.MidFX
			if p.BuyFX != nil {
				rate = *p.BuyFX
			}
			v, e := iopv.Calculate(rows, b.Unit, b.Cash, *p.MidFX, rate)
			if e == nil {
				p.Mid = ptr(v.Midpoint)
				p.HKDAssets = ptr(v.HKDAssets)
				p.CNYAssets = ptr(v.CNYAssets)
				if p.BuyFX != nil {
					p.Buy = ptr(v.Settlement)
				}
				if p.SellFX != nil {
					p.Sell = ptr((v.HKDAssets**p.SellFX + v.CNYAssets) / b.Unit)
				}
			} else {
				p.Reasons = append(p.Reasons, "INVALID_VALUATION")
			}
		}
		if q, ok := s.quotes[symbol]; ok && q.Price > 0 && Day(q.Observed) == date {
			p.ETFAt = q.Observed
			book := q.Book
			p.Book = &book
			if iopv.DomesticMinute(now) && now.Sub(q.Observed) >= -2*time.Second && now.Sub(q.Observed) <= 30*time.Second && now.Sub(q.Received) <= 30*time.Second {
				p.ETF = ptr(q.Price)
			}
		}
		if p.ETF == nil {
			p.Reasons = append(p.Reasons, "ETF_NOT_LIVE")
		}
		p.MidPremium = premium(p.ETF, p.Mid)
		p.BuyPremium = premium(p.ETF, p.Buy)
		p.SellPremium = premium(p.ETF, p.Sell)
		p.Eligible = len(p.Reasons) == 0 && p.FXActionable && p.Mid != nil && p.Buy != nil
		reprice(&p, s.quotes[symbol], now, true)
		s.latest[symbol] = p
		if iopv.ChartMinute(now) {
			key := date + " " + p.Minute
			if s.pending[key] == nil {
				s.pending[key] = map[string]Point{}
			}
			s.pending[key][symbol] = p
		}
	}
}
func (s *Service) flush(now time.Time, all bool) {
	s.mu.Lock()
	var points []Point
	var keys []string
	current := Day(now) + " " + now.In(Zone).Format("15:04")
	for k, ps := range s.pending {
		if all || k < current {
			keys = append(keys, k)
			for _, p := range ps {
				points = append(points, p)
			}
		}
	}
	s.mu.Unlock()
	if len(points) == 0 {
		return
	}
	err := s.store.Write(points)
	s.setError("storage", err)
	if err == nil {
		s.mu.Lock()
		for _, k := range keys {
			delete(s.pending, k)
		}
		s.lastWrite = now
		s.written += int64(len(points))
		s.mu.Unlock()
	}
}
func (s *Service) Snapshots() []Point {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Point, 0, len(s.candidates))
	for _, c := range s.candidates {
		p, ok := s.latest[c.Symbol]
		if !ok {
			p = Point{Symbol: c.Symbol, Name: c.Name, Date: Day(time.Now()), Channel: Channel(c.Symbol), Reasons: []string{"PCF_PENDING"}, Mode: "live"}
		}
		if p.Date != Day(time.Now()) {
			p.Mid = nil
			p.Buy = nil
			p.Sell = nil
			p.ETF = nil
			p.MidPremium = nil
			p.BuyPremium = nil
			p.SellPremium = nil
			p.Eligible = false
			p.Reasons = []string{"PCF_PENDING"}
		}
		if s.paused || time.Since(p.At) > 12*time.Second {
			p.Eligible = false
			p.ETF = nil
			p.MidPremium = nil
			p.BuyPremium = nil
			p.SellPremium = nil
			p.Reasons = append(append([]string(nil), p.Reasons...), "NAV_STALE")
			if s.paused {
				p.Reasons = append(p.Reasons, "PAUSED")
			}
		}
		fresh := !s.paused && time.Since(p.At) <= 12*time.Second && p.Date == Day(time.Now())
		reprice(&p, s.quotes[c.Symbol], time.Now(), fresh)
		if !fresh || p.ETF == nil || time.Since(p.FXAt) > 3*time.Minute {
			p.Eligible = false
		}
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Symbol < out[j].Symbol })
	return out
}
func (s *Service) Health() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	errs := map[string]string{}
	for k, v := range s.errors {
		errs[k] = v
	}
	return map[string]any{"sina_fallback_symbols": s.sinaSymbols(), "sina_quote_count": len(s.sinaQuotes), "sina_last_attempt": s.sinaLastAttempt, "quote_window_open": quoteWindow(time.Now()), "fx_polling": fxPolling(time.Now()), "active_subscriptions": len(subscriptionSymbols(s.plan.Subscriptions, time.Now())), "subscription_schedule": "09:15–15:01 CN (close buffer); 09:15–16:10 HK", "active_date": s.activeDay, "pcf_schedule": "08:40 Asia/Shanghai weekdays; retry missing every 5m until 16:08", "pcf_last_attempt": s.pcfLastAttempt, "pcf_complete_date": s.pcfCompleteDate, "pcf_next_attempt": nextPCFAttempt(time.Now(), s.pcfLastAttempt, s.pcfCompleteDate), "signal_basis": s.cfg.SignalBasis, "shared_l1": s.cfg.L1Address != "", "schema_version": "intranet-iopv.v1", "mode": "live", "now": time.Now().In(Zone), "started_at": s.started, "run_id": s.runID, "feed_state": s.feedState, "feed_at": s.feedAt, "session": s.session, "frames": s.frames, "rejected_frames": s.rejected, "pcf_ready": len(s.baskets), "candidates": len(s.candidates), "subscriptions": len(s.plan.Subscriptions), "quote_count": len(s.quotes), "group_loads": s.plan.Loads, "sequence": s.sequence, "paused": s.paused, "last_write_at": s.lastWrite, "written_rows": s.written, "errors": errs}
}
