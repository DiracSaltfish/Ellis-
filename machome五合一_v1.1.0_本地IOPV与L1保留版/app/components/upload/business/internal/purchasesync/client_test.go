package purchasesync

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestFetchPurchaseInfosFiltersTrackedSymbolsFromBatchResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.URL.Query().Get("t"); got != "8" {
			t.Fatalf("t = %q, want 8", got)
		}
		if got := r.URL.Query().Get("page"); got != "1,30000" {
			t.Fatalf("page = %q, want 1,30000", got)
		}
		_, _ = w.Write([]byte(`var reData={datas:[["000001","华夏成长混合","混合型","1","06-11","开放申购","开放赎回","","10.0","100000000000","1.0","1","0.15%"],["501312","华宝海外科技股票(QDII-LOF)A","QDII","2.2470","06-10","限大额","开放赎回","","10.0","100.0","1.0","1","0.12%"],["161226","国投瑞银白银期货(LOF)A","商品","1","06-11","暂停申购","开放赎回","","10.0","100.0","1.0","4","0.10%"]],record:"3",pages:"1",curpage:"1",showday:["2026-06-11"]}`))
	}))
	defer server.Close()

	client := NewClient(time.Second, WithEndpoint(server.URL))
	infos, err := client.FetchPurchaseInfos(context.Background(), []string{"SH501312", "SZ161226"})
	if err != nil {
		t.Fatalf("FetchPurchaseInfos failed: %v", err)
	}
	if len(infos) != 2 {
		t.Fatalf("infos length = %d, want 2", len(infos))
	}
	limit := infos["SH501312"].DailyLimitYuan
	if limit == nil || *limit != 100 {
		t.Fatalf("SH501312 limit = %v, want 100", limit)
	}
	if infos["SH501312"].Status != "限大额" {
		t.Fatalf("SH501312 status = %q, want 限大额", infos["SH501312"].Status)
	}
	blocked := infos["SZ161226"].DailyLimitYuan
	if blocked == nil || *blocked != 0 {
		t.Fatalf("SZ161226 blocked limit = %v, want 0", blocked)
	}
	if infos["SZ161226"].IsPurchasable {
		t.Fatal("SZ161226 should not be purchasable")
	}
}

func TestParseBatchPageHandlesNestedArrays(t *testing.T) {
	payload := []byte(`var reData={datas:[["501312","name","type","2.2470","06-10","限大额","开放赎回","","10.0","100.0","1.0","1","0.12%"]],record:"26725",pages:"1",curpage:"1",showday:["2026-06-11","2026-06-10"]}`)
	page, err := parseBatchPage(payload)
	if err != nil {
		t.Fatalf("parseBatchPage failed: %v", err)
	}
	if page.Record != 26725 || page.Pages != 1 || page.CurPage != 1 {
		t.Fatalf("unexpected metadata: %+v", page)
	}
	if len(page.Rows) != 1 || page.Rows[0][0] != "501312" {
		t.Fatalf("unexpected rows: %+v", page.Rows)
	}
}
