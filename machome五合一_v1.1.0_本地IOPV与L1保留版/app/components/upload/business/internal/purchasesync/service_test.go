package purchasesync

import (
	"context"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

type fakeBatchClient struct {
	calls   int
	symbols []string
	infos   map[string]domain.PurchaseInfo
}

func (c *fakeBatchClient) FetchPurchaseInfos(ctx context.Context, symbols []string) (map[string]domain.PurchaseInfo, error) {
	c.calls++
	c.symbols = append([]string(nil), symbols...)
	return c.infos, nil
}

type fakePurchaseApplier struct {
	calls int
	infos map[string]domain.PurchaseInfo
}

func (a *fakePurchaseApplier) UpdatePurchaseInfos(ctx context.Context, infos map[string]domain.PurchaseInfo) error {
	a.calls++
	a.infos = infos
	return nil
}

func TestServiceUsesSingleBatchFetch(t *testing.T) {
	client := &fakeBatchClient{
		infos: map[string]domain.PurchaseInfo{
			"SH501312": {Symbol: "SH501312", Status: "限大额"},
		},
	}
	applier := &fakePurchaseApplier{}
	service := NewService(Options{
		Client:  client,
		Applier: applier,
		Symbols: []string{"SH501312", "SZ161226"},
	})

	if err := service.SyncMissing(context.Background(), time.Time{}); err != nil {
		t.Fatalf("SyncMissing failed: %v", err)
	}
	if client.calls != 1 {
		t.Fatalf("client calls = %d, want 1", client.calls)
	}
	if len(client.symbols) != 2 {
		t.Fatalf("client symbols = %+v, want 2 symbols", client.symbols)
	}
	if applier.calls != 1 {
		t.Fatalf("applier calls = %d, want 1", applier.calls)
	}
	if applier.infos["SH501312"].Status != "限大额" {
		t.Fatalf("applied infos = %+v", applier.infos)
	}
}
