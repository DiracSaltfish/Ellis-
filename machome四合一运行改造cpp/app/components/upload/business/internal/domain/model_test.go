package domain

import "testing"

func TestRelatedSymbolsUseHFNKForNikkeiWeightedAnchorFunds(t *testing.T) {
	data := EmptyValuationData()
	ApplyStaticValuationData(&data)

	symbols := data.RelatedSymbolsForFunds([]string{"SH513520"})

	foundHFNK := false
	foundZNBNKY := false
	for _, symbol := range symbols {
		switch symbol {
		case "HF_NK":
			foundHFNK = true
		case "znb_NKY":
			foundZNBNKY = true
		}
	}

	if !foundHFNK {
		t.Fatalf("expected HF_NK in related symbols, got %v", symbols)
	}
	if foundZNBNKY {
		t.Fatalf("unexpected znb_NKY in related symbols: %v", symbols)
	}
}

func TestRelatedSymbolsUseHFESForSP500WeightedAnchorFunds(t *testing.T) {
	data := EmptyValuationData()
	ApplyStaticValuationData(&data)

	symbols := data.RelatedSymbolsForFunds([]string{"SH513500"})

	foundHFES := false
	foundSPY := false
	for _, symbol := range symbols {
		switch symbol {
		case "HF_ES":
			foundHFES = true
		case "SPY":
			foundSPY = true
		}
	}

	if !foundHFES {
		t.Fatalf("expected HF_ES in related symbols, got %v", symbols)
	}
	if foundSPY {
		t.Fatalf("unexpected SPY in related symbols: %v", symbols)
	}
}

func TestRelatedSymbolsUseRWRForUSREITWeightedAnchorFund(t *testing.T) {
	data := EmptyValuationData()
	ApplyStaticValuationData(&data)

	symbols := data.RelatedSymbolsForFunds([]string{"SZ160140"})

	foundRWR := false
	foundVNQ := false
	for _, symbol := range symbols {
		switch symbol {
		case "RWR":
			foundRWR = true
		case "VNQ":
			foundVNQ = true
		}
	}

	if !foundRWR {
		t.Fatalf("expected RWR in related symbols, got %v", symbols)
	}
	if foundVNQ {
		t.Fatalf("unexpected VNQ in related symbols: %v", symbols)
	}
}

func TestRelatedSymbolsUseWeightedAnchorProxyForMSCIUS50Funds(t *testing.T) {
	data := EmptyValuationData()
	ApplyStaticValuationData(&data)

	sh513850 := data.RelatedSymbolsForFunds([]string{"SH513850"})
	if !containsSymbol(sh513850, "OEF") {
		t.Fatalf("expected OEF in related symbols for SH513850, got %v", sh513850)
	}

	sz159577 := data.RelatedSymbolsForFunds([]string{"SZ159577"})
	if !containsSymbol(sz159577, "MGC") {
		t.Fatalf("expected MGC in related symbols for SZ159577, got %v", sz159577)
	}
}

func containsSymbol(symbols []string, target string) bool {
	for _, symbol := range symbols {
		if symbol == target {
			return true
		}
	}
	return false
}
