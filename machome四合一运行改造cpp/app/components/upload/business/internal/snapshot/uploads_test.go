package snapshot

import (
	"testing"

	"newnavnav/internal/domain"
)

func TestRequiredQuoteSymbolsSkipsIgnoredAndUnsupportedSymbols(t *testing.T) {
	quotes := map[string]domain.Quote{
		"SHOP":     {Symbol: "SHOP"},
		"BRK.B":    {Symbol: "BRK.B"},
		"^USO-EU":  {Symbol: "^USO-EU"},
		"HF_CL":    {Symbol: "HF_CL"},
		"SH501018": {Symbol: "SH501018"},
	}

	required := requiredQuoteSymbolsFromQuotes(quotes)
	got := make(map[string]bool, len(required.Symbols))
	for _, symbol := range required.Symbols {
		got[symbol] = true
	}

	if !got["SHOP"] {
		t.Fatalf("SHOP should remain in required symbols: %+v", required.Symbols)
	}
	if !got["HF_CL"] {
		t.Fatalf("HF_CL should remain in required symbols: %+v", required.Symbols)
	}
	if !got["SH501018"] {
		t.Fatalf("SH501018 should remain in required symbols: %+v", required.Symbols)
	}
	if got["BRK.B"] {
		t.Fatalf("BRK.B should be excluded from realtime required symbols: %+v", required.Symbols)
	}
	if got["^USO-EU"] {
		t.Fatalf("^USO-EU should be excluded from required symbols: %+v", required.Symbols)
	}
}
