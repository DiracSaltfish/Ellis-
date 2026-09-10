package live

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"math"
	"net"
	"strings"
	"sync"
	"time"
)

type l1Book struct {
	Symbol string    `json:"s"`
	QT     int64     `json:"qt"`
	RT     int64     `json:"rt"`
	Price  float64   `json:"lp"`
	BP     []float64 `json:"bp"`
	BV     []float64 `json:"bv"`
	AP     []float64 `json:"ap"`
	AV     []float64 `json:"av"`
}

func decodeL1(b l1Book, now time.Time, session uint64) (Quote, error) {
	if b.Symbol == "" || b.QT <= 0 || b.RT <= 0 || b.Price < 0 || math.IsNaN(b.Price) || math.IsInf(b.Price, 0) || len(b.BP) != 5 || len(b.BV) != 5 || len(b.AP) != 5 || len(b.AV) != 5 {
		return Quote{}, fmt.Errorf("invalid L1 book")
	}
	q := Quote{Symbol: b.Symbol, Price: b.Price, Observed: time.UnixMilli(b.QT), Received: time.UnixMilli(b.RT), Session: session}
	if q.Observed.After(now.Add(2*time.Second)) || q.Received.After(now.Add(2*time.Second)) {
		return Quote{}, fmt.Errorf("future L1 timestamp")
	}
	q.Book.ObservedAt = q.Observed
	for i := 0; i < 5; i++ {
		for _, v := range []float64{b.BP[i], b.BV[i], b.AP[i], b.AV[i]} {
			if v < 0 || math.IsNaN(v) || math.IsInf(v, 0) {
				return Quote{}, fmt.Errorf("invalid L1 level")
			}
		}
		q.Book.Bids[i] = iopv.Level{Price: b.BP[i], Volume: b.BV[i]}
		q.Book.Asks[i] = iopv.Level{Price: b.AP[i], Volume: b.AV[i]}
	}
	return q, nil
}
func (s *Service) l1Batch(ctx context.Context, symbols []string, session uint64) error {
	conn, e := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp", s.cfg.L1Address)
	if e != nil {
		return e
	}
	defer conn.Close()
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			conn.Close()
		case <-done:
		}
	}()
	var writeMu sync.Mutex
	send := func(v any) error {
		writeMu.Lock()
		defer writeMu.Unlock()
		conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
		return json.NewEncoder(conn).Encode(v)
	}
	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 65536), 1<<20)
	wanted := map[string]bool{}
	for _, v := range symbols {
		wanted[v] = true
	}
	hello := false
	acked := false
	for {
		conn.SetReadDeadline(time.Now().Add(35 * time.Second))
		if !scanner.Scan() {
			break
		}
		var m struct {
			V        int               `json:"v"`
			T        string            `json:"t"`
			Service  string            `json:"service"`
			Op       string            `json:"op"`
			Symbols  []string          `json:"symbols"`
			Rejected []json.RawMessage `json:"rejected"`
			Books    []l1Book          `json:"books"`
		}
		if json.Unmarshal(scanner.Bytes(), &m) != nil || m.V != 1 {
			return fmt.Errorf("L1 protocol mismatch")
		}
		if !hello {
			if m.T != "hello" || m.Service != "qmt_l1" {
				return fmt.Errorf("L1 hello mismatch")
			}
			hello = true
			if e = send(map[string]any{"v": 1, "t": "subscribe", "id": "iopv", "symbols": symbols, "interval_ms": 0}); e != nil {
				return e
			}
			go func() {
				tick := time.NewTicker(10 * time.Second)
				defer tick.Stop()
				for {
					select {
					case <-done:
						return
					case <-ctx.Done():
						return
					case <-tick.C:
						if send(map[string]any{"v": 1, "t": "ping"}) != nil {
							conn.Close()
							return
						}
					}
				}
			}()
			continue
		}
		if m.T == "error" {
			return fmt.Errorf("L1 rejected request")
		}
		if m.T == "ack" && m.Op == "subscribe" {
			accepted := map[string]bool{}
			for _, v := range m.Symbols {
				accepted[v] = true
			}
			if len(m.Rejected) > 0 || len(accepted) != len(wanted) {
				return fmt.Errorf("L1 subscription incomplete")
			}
			for v := range wanted {
				if !accepted[v] {
					return fmt.Errorf("L1 subscription mismatch")
				}
			}
			acked = true
			s.setError("feed", nil)
		}
		if m.T != "l1" {
			continue
		}
		if !acked {
			return fmt.Errorf("L1 quote before ack")
		}
		for _, b := range m.Books {
			if !wanted[b.Symbol] {
				return fmt.Errorf("L1 unexpected symbol")
			}
			now := time.Now()
			q, err := decodeL1(b, now, session)
			s.mu.Lock()
			if err != nil {
				s.rejected++
			} else if old, ok := s.quotes[q.Symbol]; !ok || !q.Observed.Before(old.Observed) {
				s.quotes[q.Symbol] = q
				s.frames++
				s.feedAt = now
				s.feedState = "streaming"
			}
			s.mu.Unlock()
		}
	}
	if e = scanner.Err(); e != nil {
		return e
	}
	return fmt.Errorf("L1 disconnected")
}
func (s *Service) sharedL1Loop(ctx context.Context) {
	for ctx.Err() == nil {
		s.mu.RLock()
		symbols := subscriptionSymbols(s.plan.Subscriptions, s.scheduleNow())
		s.mu.RUnlock()
		if len(symbols) == 0 {
			s.mu.Lock()
			s.feedState = "scheduled_idle"
			delete(s.errors, "feed")
			s.mu.Unlock()
			select {
			case <-ctx.Done():
				return
			case <-s.restart:
				continue
			case <-time.After(time.Second):
				continue
			}
		}
		child, cancel := context.WithCancel(ctx)
		s.mu.Lock()
		s.session++
		session := s.session
		s.quotes = map[string]Quote{}
		s.feedState = "connecting"
		s.mu.Unlock()
		var wg sync.WaitGroup
		errors := make(chan error, (len(symbols)+199)/200)
		for start := 0; start < len(symbols); start += 200 {
			end := start + 200
			if end > len(symbols) {
				end = len(symbols)
			}
			batch := append([]string(nil), symbols[start:end]...)
			wg.Add(1)
			go func() { defer wg.Done(); errors <- s.l1Batch(child, batch, session) }()
		}
		boundary := time.NewTicker(time.Second)
	waitSession:
		for {
			select {
			case <-ctx.Done():
				break waitSession
			case <-s.restart:
				break waitSession
			case e := <-errors:
				s.setError("feed", e)
				break waitSession
			case <-boundary.C:
				s.mu.RLock()
				next := subscriptionSymbols(s.plan.Subscriptions, s.scheduleNow())
				s.mu.RUnlock()
				if strings.Join(next, "\n") != strings.Join(symbols, "\n") {
					break waitSession
				}
			}
		}
		boundary.Stop()
		cancel()
		wg.Wait()
		s.mu.Lock()
		s.quotes = map[string]Quote{}
		s.feedState = "reconnecting"
		s.mu.Unlock()
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}
