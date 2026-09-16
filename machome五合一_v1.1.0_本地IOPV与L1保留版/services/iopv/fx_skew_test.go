package iopv

import (
	"encoding/json"
	"testing"
	"time"
)

func TestFXEnvelopeClockSkew(t *testing.T) {
	now := time.Date(2026, 9, 14, 10, 17, 0, 0, time.FixedZone("CST", 28800))
	v := map[string]any{"schema_version": "hk-connect-fx.v7", "trade_date": "2026-09-14", "generated_at": now.Add(700 * time.Millisecond), "status": "reference_only", "model": map[string]any{"version": "v1"}, "central_parity": map[string]any{"trade_date": "2026-09-14", "pair": "HKD/CNY", "rate": .86}, "fx": map[string]any{"pair": "HKD/CNY", "healthy": true, "observed_at": now.Add(-time.Second)}, "estimate": map[string]any{"predicted_sell_settlement": .85}}
	raw, _ := json.Marshal(v)
	if _, e := ParseFX(raw, "2026-09-14", "shanghai", "buy_hk", now); e != nil {
		t.Fatal(e)
	}
	v["generated_at"] = now.Add(3 * time.Second)
	raw, _ = json.Marshal(v)
	if _, e := ParseFX(raw, "2026-09-14", "shanghai", "buy_hk", now); e == nil {
		t.Fatal("accepted 3s future envelope")
	}
	v["generated_at"] = now
	v["fx"].(map[string]any)["observed_at"] = now.Add(time.Second)
	raw, _ = json.Marshal(v)
	if _, e := ParseFX(raw, "2026-09-14", "shanghai", "buy_hk", now); e == nil {
		t.Fatal("accepted future quote")
	}
}
