package main

import (
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestShouldRunStartupPurchaseStatusSync(t *testing.T) {
	location := time.FixedZone("CST", 8*60*60)
	beforeDailyRun := time.Date(2026, 6, 11, 8, 4, 0, 0, location)
	afterDailyRun := time.Date(2026, 6, 11, 8, 6, 0, 0, location)
	yesterday := time.Date(2026, 6, 10, 8, 6, 0, 0, location)

	if shouldRunStartupPurchaseStatusSync(beforeDailyRun, nil, "08:05") {
		t.Fatal("did not expect startup sync before configured time")
	}
	if !shouldRunStartupPurchaseStatusSync(afterDailyRun, nil, "08:05") {
		t.Fatal("expected startup sync after configured time without cache")
	}
	if !shouldRunStartupPurchaseStatusSync(afterDailyRun, map[string]domain.PurchaseInfo{
		"SH501312": {Symbol: "SH501312", FetchedAt: yesterday},
	}, "08:05") {
		t.Fatal("expected startup sync after configured time with stale cache")
	}
	if shouldRunStartupPurchaseStatusSync(afterDailyRun, map[string]domain.PurchaseInfo{
		"SH501312": {Symbol: "SH501312", FetchedAt: afterDailyRun},
	}, "08:05") {
		t.Fatal("did not expect startup sync with today cache")
	}
	if shouldRunStartupPurchaseStatusSync(time.Date(2026, 6, 11, 8, 29, 0, 0, location), nil, "08:30") {
		t.Fatal("did not expect startup sync before custom configured time")
	}
	if !shouldRunStartupPurchaseStatusSync(afterDailyRun, nil, "invalid") {
		t.Fatal("expected invalid clock time to fall back to 08:05")
	}
}

func TestPurchaseStatusClock(t *testing.T) {
	hour, minute := purchaseStatusClock("07:59")
	if hour != 7 || minute != 59 {
		t.Fatalf("expected 07:59, got %02d:%02d", hour, minute)
	}
	hour, minute = purchaseStatusClock("")
	if hour != 8 || minute != 5 {
		t.Fatalf("expected fallback 08:05, got %02d:%02d", hour, minute)
	}
}
