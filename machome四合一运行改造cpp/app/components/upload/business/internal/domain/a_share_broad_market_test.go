package domain

import "testing"

func TestChinaBroadMarketETFsRemainOutsideValuationUniverse(t *testing.T) {
	funds := ChinaBroadMarketETFs()
	if len(funds) != 86 {
		t.Fatalf("fund count = %d, want 86", len(funds))
	}
	allSymbols := make(map[string]bool)
	for _, symbol := range AllSymbols() {
		allSymbols[symbol] = true
	}
	seen := make(map[string]bool)
	for _, fund := range funds {
		if fund.Symbol == "" || fund.Name == "" || fund.IndexName == "" {
			t.Fatalf("incomplete research fund: %#v", fund)
		}
		if seen[fund.Symbol] {
			t.Fatalf("duplicate research symbol: %s", fund.Symbol)
		}
		seen[fund.Symbol] = true
		if allSymbols[fund.Symbol] {
			t.Fatalf("research symbol %s leaked into AllSymbols", fund.Symbol)
		}
	}
}

func TestShareHistorySymbolsIncludesChinaBroadMarketResearch(t *testing.T) {
	tracked := make(map[string]bool)
	for _, symbol := range ShareHistorySymbols() {
		tracked[symbol] = true
	}
	for _, fund := range ChinaBroadMarketETFs() {
		if !tracked[fund.Symbol] {
			t.Fatalf("share-history symbols missing %s", fund.Symbol)
		}
	}
}
