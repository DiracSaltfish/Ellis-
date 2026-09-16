package live

import (
	"context"
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"
)

type connectRoundTrip func(*http.Request) (*http.Response, error)

func (f connectRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestNonConnectUsesBothRoutesNotQuoteSource(t *testing.T) {
	v := connectSnapshot{Shanghai: connectList{Codes: map[string]string{"00700": "Tencent"}}, Shenzhen: connectList{Codes: map[string]string{"00001": "CK"}}}
	b := iopv.Basket{Components: []iopv.Component{{Symbol: "00700.HK"}, {Symbol: "00001.HK"}, {Symbol: "01698.HK", Name: "腾讯音乐", Quantity: 100}, {Symbol: "01698.HK"}, {Symbol: "00823.HK", Mode: 2, Cash: 123}, {Symbol: "600000.SH"}, {Symbol: "CASH.HK"}}}
	out := nonConnectComponents(b, v)
	if len(out) != 2 || out[0].Symbol != "00823.HK" || out[1].Symbol != "01698.HK" || out[1].Quantity != 100 {
		t.Fatalf("%+v", out)
	}
}
func TestConnectFetchValidatesCompleteLists(t *testing.T) {
	for _, bad := range []bool{false, true} {
		calls := 0
		client := &http.Client{Transport: connectRoundTrip(func(r *http.Request) (*http.Response, error) {
			calls++
			var body string
			if r.Header.Get("Referer") == "" {
				t.Fatal("missing referer")
			}
			if strings.Contains(r.URL.Host, "sse.com") {
				rows := []string{}
				for i := 1; i <= 100; i++ {
					rows = append(rows, fmt.Sprintf(`{"SECURITY_CODE":"%05d","ABBR_CN":"A","UPDATE_DATE":"2026-09-14"}`, i))
				}
				n := 100
				if bad {
					n = 101
				}
				body = fmt.Sprintf(`{"result":[%s],"pageHelp":{"total":%d}}`, strings.Join(rows, ","), n)
			} else {
				rows := []string{}
				for i := 1; i <= 100; i++ {
					rows = append(rows, fmt.Sprintf(`{"zqdm":"%05d","zqjc":"B"}`, i))
				}
				body = fmt.Sprintf(`[{"metadata":{"subname":"2026-09-14","pagecount":1,"recordcount":100,"pageno":1},"data":[%s]}]`, strings.Join(rows, ","))
			}
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body))}, nil
		})}
		v, e := fetchConnect(context.Background(), client, time.Date(2026, 9, 14, 14, 0, 0, 0, time.Local))
		if bad {
			if e == nil {
				t.Fatal("accepted incomplete response")
			}
		} else if e != nil || !v.valid() || calls != 2 {
			t.Fatalf("%v %+v calls=%d", e, v, calls)
		}
	}
}

func TestConnectOfficialSourceOptIn(t *testing.T) {
	path := os.Getenv("CONNECT_LIVE_OUTPUT")
	if path == "" {
		t.Skip("requires explicit live source probe")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	v, e := fetchConnect(ctx, &http.Client{Timeout: 15 * time.Second}, time.Now())
	if e != nil {
		t.Fatal(e)
	}
	raw, _ := json.MarshalIndent(v, "", "  ")
	if e = os.WriteFile(path, raw, 0600); e != nil {
		t.Fatal(e)
	}
	t.Logf("Shanghai %s: %d; Shenzhen %s: %d", v.Shanghai.Date, len(v.Shanghai.Codes), v.Shenzhen.Date, len(v.Shenzhen.Codes))
}
