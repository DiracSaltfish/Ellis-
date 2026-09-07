package snapshot

import "testing"

func TestPublicSourceLabelPassthrough(t *testing.T) {
	cases := map[string]string{
		"ibkr_daily":                  "ibkr_daily",
		"ibkr_us_live":                "ibkr_us_live",
		"ibkr_us_overnight_1m:trades": "ibkr_us_overnight_1m:trades",
		"upload:home-mac-ib":          "upload:home-mac-ib",
		"upload:home-mac-ib-foo":      "upload:home-mac-ib-foo",
		"sina":                        "sina",
		"palmmicro":                   "palmmicro",
		"":                            "",
	}
	for input, want := range cases {
		if got := publicSourceLabel(input); got != want {
			t.Fatalf("publicSourceLabel(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestPublicVisibleTextPassthrough(t *testing.T) {
	cases := map[string]string{
		"ibkr_anchor_1m": "ibkr_anchor_1m",
		"daily":          "daily",
		"":               "",
	}
	for input, want := range cases {
		if got := publicVisibleText(input); got != want {
			t.Fatalf("publicVisibleText(%q) = %q, want %q", input, got, want)
		}
	}
}
