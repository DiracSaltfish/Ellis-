package eastmoney

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestFetchNetValuesParsesLSJZRows(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/f10/lsjz" {
			t.Fatalf("path = %s, want /f10/lsjz", r.URL.Path)
		}
		if got := r.URL.Query().Get("fundCode"); got != "161226" {
			t.Fatalf("fundCode = %s, want 161226", got)
		}
		_, _ = w.Write([]byte(`{
			"Data":{"LSJZList":[
				{"FSRQ":"2026-06-01","DWJZ":"2.0707","LJJZ":"2.0707","JZZZL":"0.30"},
				{"FSRQ":"2026-05-29","DWJZ":"","LJJZ":"","JZZZL":""}
			]},
			"ErrCode":0,
			"ErrMsg":null,
			"TotalCount":2,
			"PageSize":5,
			"PageIndex":1
		}`))
	}))
	defer server.Close()

	client := NewClient(time.Second)
	client.baseURL = server.URL
	rows, err := client.FetchNetValues(context.Background(), "161226", "2026-05-29", "2026-06-02")
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	row := rows[0]
	if row.FundCode != "161226" || row.Date != "2026-06-01" || row.UnitNAV != 2.0707 || row.GrowthRate != 0.30 {
		t.Fatalf("row = %+v", row)
	}
}

func TestUnwrapJSONP(t *testing.T) {
	got := unwrapJSONP(`callback({"ErrCode":0})`)
	if got != `{"ErrCode":0}` {
		t.Fatalf("unwrapJSONP = %s", got)
	}
}
