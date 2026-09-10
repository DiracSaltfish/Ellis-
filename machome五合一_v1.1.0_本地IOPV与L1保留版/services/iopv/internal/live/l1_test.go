package live

import (
	"context"
	"encoding/json"
	iopv "intranet-iopv"
	"net"
	"testing"
	"time"
)

func TestL1UnitsAndOriginalTimestamps(t *testing.T) {
	now := time.Now()
	b := l1Book{Symbol: "00700.HK", QT: now.Add(-time.Minute).UnixMilli(), RT: now.Add(-time.Minute).UnixMilli(), Price: 500, BP: []float64{499, 498, 497, 496, 495}, AP: []float64{501, 502, 503, 504, 505}, BV: []float64{100, 200, 300, 400, 500}, AV: []float64{200, 300, 400, 500, 600}}
	q, e := decodeL1(b, now, 2)
	if e != nil || q.Price != 500 || q.Book.Bids[0].Volume != 100 || q.Received.UnixMilli() != b.RT {
		t.Fatal(q, e)
	}
	b.AP = nil
	if _, e = decodeL1(b, now, 2); e == nil {
		t.Fatal("short book accepted")
	}
}
func TestL1PartialSubscriptionFails(t *testing.T) {
	ln, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	defer ln.Close()
	go func() {
		c, e := ln.Accept()
		if e != nil {
			return
		}
		defer c.Close()
		json.NewEncoder(c).Encode(map[string]any{"v": 1, "t": "hello", "service": "qmt_l1"})
		var request map[string]any
		json.NewDecoder(c).Decode(&request)
		json.NewEncoder(c).Encode(map[string]any{"v": 1, "t": "ack", "op": "subscribe", "symbols": []string{}, "rejected": []any{map[string]string{"s": "00700.HK", "reason": "upstream_capacity"}}})
	}()
	s := &Service{cfg: Config{L1Address: ln.Addr().String()}}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if e = s.l1Batch(ctx, []string{"00700.HK"}, 1); e == nil {
		t.Fatal("partial subscription accepted")
	}
}

// Replacing the day must reconnect even when tomorrow uses identical symbols.
func TestSameSymbolsReconnectAfterRollover(t *testing.T) {
	ln, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	defer ln.Close()
	accepted := make(chan struct{}, 4)
	go func() {
		for {
			c, e := ln.Accept()
			if e != nil {
				return
			}
			go func() {
				defer c.Close()
				enc := json.NewEncoder(c)
				enc.Encode(map[string]any{"v": 1, "t": "hello", "service": "qmt_l1"})
				var req map[string]any
				dec := json.NewDecoder(c)
				if dec.Decode(&req) != nil {
					return
				}
				enc.Encode(map[string]any{"v": 1, "t": "ack", "op": "subscribe", "symbols": []string{"00700.HK"}})
				accepted <- struct{}{}
				for dec.Decode(&req) == nil {
				}
			}()
		}
	}()
	s := &Service{cfg: Config{L1Address: ln.Addr().String()}, activeDay: "2026-09-09", nowForSchedule: func() time.Time { return at("2026-09-10T10:00:00+08:00") }, plan: iopv.Plan{Subscriptions: []string{"00700.HK"}}, quotes: map[string]Quote{}, errors: map[string]string{}, restart: make(chan struct{}, 1)}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { s.sharedL1Loop(ctx); close(done) }()
	wait := func() {
		t.Helper()
		select {
		case <-accepted:
		case <-time.After(6 * time.Second):
			t.Fatal("no subscription")
		}
	}
	wait()
	s.rollDay(at("2026-09-10T00:00:00+08:00"))
	s.mu.Lock()
	s.plan = iopv.Plan{Subscriptions: []string{"00700.HK"}}
	s.mu.Unlock()
	wait()
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("feed did not stop")
	}
}
