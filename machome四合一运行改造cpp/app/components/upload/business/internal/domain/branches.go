package domain

import "strings"

var excludedSymbols = map[string]bool{
	"SH518800": true,
	"SH518880": true,
	"SZ159934": true,
	"SZ159937": true,
	"SZ159985": true,
}

var ignoredAuxiliarySymbols = map[string]bool{
	"^GLD-EU":  true,
	"^USO-EU":  true,
	"^USO-HK":  true,
	"^INDA-EU": true,
	"^INDA-HK": true,
	"^INDA-JP": true,
}

var unsupportedLiveQuoteSymbols = map[string]bool{
	"BRK.B": true,
}

func IsExcludedSymbol(symbol string) bool {
	return excludedSymbols[strings.ToUpper(strings.TrimSpace(symbol))]
}

func IsIgnoredAuxiliarySymbol(symbol string) bool {
	return ignoredAuxiliarySymbols[strings.ToUpper(strings.TrimSpace(symbol))]
}

func IsUnsupportedLiveQuoteSymbol(symbol string) bool {
	return unsupportedLiveQuoteSymbols[strings.ToUpper(strings.TrimSpace(symbol))]
}

var Branches = []Branch{
	{
		Key:        "chinafuture",
		NameCN:     "A股商品",
		OldPath:    "/woody/res/chinafuturecn.php",
		NewPath:    "/funds/china-future",
		SortOrder:  20,
		StaleAfter: 60,
		Symbols:    []string{"SZ161226"},
	},
	{
		Key:        "qdii",
		NameCN:     "美股QDII",
		OldPath:    "/woody/res/qdiicn.php",
		NewPath:    "/funds/qdii-us",
		SortOrder:  30,
		StaleAfter: 90,
		Symbols: []string{
			"SH513290", "SH513400", "SZ160140", "SZ161126", "SZ161128", "SZ162415", "SZ164906",
			"SZ159502", "SZ161127", "SH513350", "SZ159518", "SZ162411", "SZ160416", "SZ162719",
			"SH513100", "SH513110", "SH513390", "SH513870", "SZ159501", "SZ159513", "SZ159632", "SZ159659",
			"SZ159660", "SZ159696", "SZ159941", "SZ161130", "SH513300", "SH513500", "SH513650", "SZ159612",
			"SZ161125", "SZ159655",
		},
	},
	{
		Key:        "qdiimix",
		NameCN:     "混合QDII",
		OldPath:    "/woody/res/qdiimixcn.php",
		NewPath:    "/funds/qdii-mix",
		SortOrder:  40,
		StaleAfter: 90,
		Symbols: []string{
			"SH513360", "SZ159509", "SZ159529", "SH501225", "SH501312", "SZ160644", "SZ164824",
			"SZ163208", "SH501018", "SZ160723", "SZ161129", "SZ160216", "SZ161815", "SZ160719", "SZ161116",
			"SZ164701", "SZ165513", "SH513050", "SH513220", "SZ159605", "SZ159607", "SH513090", "SH513230",
			"SH513750", "SH513990", "SZ159567", "SZ159570", "SZ159615", "SZ159751", "SZ159792", "SH513850",
			"SZ159577",
		},
	},
	{
		Key:        "qdiijp",
		NameCN:     "日本QDII",
		OldPath:    "/woody/res/qdiijpcn.php",
		NewPath:    "/funds/qdii-jp",
		SortOrder:  60,
		StaleAfter: 90,
		Symbols:    []string{"SH513000", "SH513520", "SH513880", "SZ159866"},
	},
	{
		Key:        "qdiieu",
		NameCN:     "欧洲QDII",
		OldPath:    "/woody/res/qdiieucn.php",
		NewPath:    "/funds/qdii-eu",
		SortOrder:  70,
		StaleAfter: 90,
		Symbols:    []string{"SH513080", "SH513030", "SZ159561"},
	},
}

func BranchByKey(key string) (Branch, bool) {
	for _, branch := range Branches {
		if branch.Key == key {
			return branch, true
		}
	}
	return Branch{}, false
}

func BranchByOldPath(path string) (Branch, bool) {
	for _, branch := range Branches {
		if branch.OldPath == path {
			return branch, true
		}
	}
	return Branch{}, false
}

func BranchesForSymbol(symbol string) []Branch {
	var out []Branch
	for _, branch := range Branches {
		for _, candidate := range branch.Symbols {
			if candidate == symbol {
				out = append(out, branch)
				break
			}
		}
	}
	return out
}

func AllSymbols() []string {
	seen := make(map[string]bool)
	var out []string
	for _, branch := range Branches {
		for _, symbol := range branch.Symbols {
			if !seen[symbol] && !IsExcludedSymbol(symbol) {
				seen[symbol] = true
				out = append(out, symbol)
			}
		}
	}
	return out
}
