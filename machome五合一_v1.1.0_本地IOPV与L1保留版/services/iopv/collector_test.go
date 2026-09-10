package iopv

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type mockTransport func(*http.Request) (*http.Response, error)

func (f mockTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestCollectorBoundaries(t *testing.T) {
	raw := string(mustRead(t, "testdata/pcf/513090.xml"))
	for _, tc := range []struct {
		name, body, date string
		status           int
		ok               bool
	}{{"good", raw, "2026-09-08", 200, true}, {"wrong_day", raw, "2026-09-09", 200, false}, {"forbidden", "Forbidden", "2026-09-08", 403, false}, {"html", "<html>login</html>", "2026-09-08", 200, false}} {
		t.Run(tc.name, func(t *testing.T) {
			client := &http.Client{Transport: mockTransport(func(r *http.Request) (*http.Response, error) {
				if r.Method != "GET" {
					t.Fatal("mutation")
				}
				return &http.Response{StatusCode: tc.status, Body: io.NopCloser(strings.NewReader(tc.body))}, nil
			})}
			_, _, e := FetchPCF(context.Background(), client, "513090.SH", tc.date)
			if (e == nil) != tc.ok {
				t.Fatal(e)
			}
		})
	}
	if _, e := PCFURL("../123", "2026-09-08"); e == nil {
		t.Fatal("bad symbol")
	}
	url, e := PCFURL("159217.SZ", "2026-09-08")
	if e != nil || !strings.HasSuffix(url, "pcf_159217_20260908.xml") {
		t.Fatal(url, e)
	}
	url, e = PCFURL("520600.SH", "2026-09-08")
	if e != nil || !strings.Contains(url, "/pcflist/520600?date=20260908") {
		t.Fatal(url, e)
	}
}

func TestGFF520600PCF(t *testing.T) {
	raw := []byte(`<html>广发基金<div class="title"><span>2026-09-01日内容信息</span></div><table>
<tr><th>最小申购、赎回单位的预估现金部分(单位:元)：</th><td>4.43</td></tr>
<tr><th>最小申购、赎回单位(单位:份)：</th><td>500,000.00</td></tr></table>
<div>成份股信息内容</div><table><tr><td>证券代码</td></tr>
<tr><td>00175</td><td>吉利汽车</td><td>4,937</td><td>允许现金替代</td><td>25.00%</td><td></td><td>75,169.580</td><td>香港交易所</td></tr>
<tr><td>00425</td><td>敏实集团</td><td>358</td><td>必须现金替代</td><td>25.00%</td><td></td><td>8,027.570</td><td>香港交易所</td></tr></table></html>`)
	b, err := ParsePCF(raw, "520600.SH", "2026-09-01")
	if err != nil || b.Unit != 500000 || b.Cash != 4.43 || len(b.Components) != 2 {
		t.Fatal(b, err)
	}
	if b.Components[0].Symbol != "00175.HK" || b.Components[1].Mode != 2 || b.Components[1].Cash != 8027.57 {
		t.Fatal(b.Components)
	}
}
func TestPCFDuplicateAndNormalization(t *testing.T) {
	raw := string(mustRead(t, "testdata/pcf/513090.xml"))
	raw = strings.Replace(raw, "<InstrumentID>00165</InstrumentID>", "<InstrumentID>165</InstrumentID>", 1)
	b, e := ParsePCF([]byte(raw), "513090.SH", "2026-09-08")
	if e != nil || b.Components[0].Symbol != "00165.HK" {
		t.Fatal(b, e)
	}
	raw = strings.Replace(raw, "<InstrumentID>00388</InstrumentID>", "<InstrumentID>00165</InstrumentID>", 1)
	if _, e := ParsePCF([]byte(raw), "513090.SH", "2026-09-08"); e == nil {
		t.Fatal("duplicate after padding")
	}
}
func TestMinuteMissingAndAge(t *testing.T) {
	now, _ := time.Parse(time.RFC3339, "2026-09-09T14:00:00+08:00")
	e := Evaluation{Symbol: "513090.SH", Date: "2026-09-09", CalculatedAt: now.Add(-13 * time.Second), Values: &Values{Midpoint: 1, Settlement: 1}, Eligible: true}
	q := Quote{Price: 1, ObservedAt: now, ReceivedAt: now, Status: "live", Source: "fixture"}
	p, err := SampleMinute(now, e, &q)
	if err != nil || p.Values != nil || p.Premium.MidpointPct != nil || p.Eligible {
		t.Fatal("stale NAV reused")
	}
	e.CalculatedAt = now
	p, err = SampleMinute(now, e, &q)
	if err != nil || p.Values == nil || p.Premium.MidpointPct == nil || !p.Eligible {
		t.Fatal("valid minute missing")
	}
	q.ObservedAt = now.Add(-31 * time.Second)
	p, _ = SampleMinute(now, e, &q)
	if p.ETFPrice != nil || p.Eligible {
		t.Fatal("stale ETF reused")
	}
}
