package domain

import "strings"

type CommodityBasketLeg struct {
	Key        string
	Symbol     string
	Label      string
	FXPair     string
	Weight     float64
	Components []string
}

type CommodityBasketStrategy struct {
	FundSymbol string
	Label      string
	Legs       []CommodityBasketLeg
}

var commodityBasketStrategies = map[string]CommodityBasketStrategy{
	"SZ160216": {
		FundSymbol: "SZ160216",
		Label:      "SI/CL/HG",
		Legs: []CommodityBasketLeg{
			{
				Key:    "silver",
				Symbol: "HF_SI",
				Label:  "SI",
				FXPair: "USDCNY",
				Weight: 0.36483374999999996,
				Components: []string{
					"ISHARES SILVER TRUST",
				},
			},
			{
				Key:    "oil",
				Symbol: "HF_CL",
				Label:  "CL",
				FXPair: "USDCNY",
				Weight: 0.20995725,
				Components: []string{
					"UNITED STATES OIL FUND LP",
				},
			},
			{
				Key:    "copper",
				Symbol: "HF_HG",
				Label:  "HG",
				FXPair: "USDCNY",
				Weight: 0.10270900000000001,
				Components: []string{
					"UNITED STATES COPPER INDEX FUND",
				},
			},
		},
	},
	"SZ161815": {
		FundSymbol: "SZ161815",
		Label:      "GC/CL/SI/HG",
		Legs: []CommodityBasketLeg{
			{
				Key:    "gold",
				Symbol: "HF_GC",
				Label:  "GC",
				FXPair: "USDCNY",
				Weight: 0.37497566133762496,
				Components: []string{
					"ISHARES GOLD TRUST",
					"SPDR GOLD TRUST ETF",
					"GOLDMAN SACHS PHYSICAL GOLD",
					"ABRDN PHYSICAL GOLD SHARES",
				},
			},
			{
				Key:    "oil",
				Symbol: "HF_CL",
				Label:  "CL",
				FXPair: "USDCNY",
				Weight: 0.3408192626034193,
				Components: []string{
					"WT BRENT CRUDE OIL",
					"WT WTI CRUDE OIL",
					"ISHARES GSCI COMMODITY DYNAM ETF",
				},
			},
			{
				Key:    "silver",
				Symbol: "HF_SI",
				Label:  "SI",
				FXPair: "USDCNY",
				Weight: 0.0217488676393431,
				Components: []string{
					"ISHARES SILVER TRUST",
				},
			},
			{
				Key:    "copper",
				Symbol: "HF_HG",
				Label:  "HG",
				FXPair: "USDCNY",
				Weight: 0.05995620841961263,
				Components: []string{
					"ABRDN BLOOMBERG ALL COMMODIT LNGR DATED ETF",
				},
			},
		},
	},
}

func CommodityBasketStrategyForFund(symbol string) (CommodityBasketStrategy, bool) {
	strategy, ok := commodityBasketStrategies[strings.ToUpper(strings.TrimSpace(symbol))]
	return strategy, ok
}

func CommodityBasketStrategies() []CommodityBasketStrategy {
	out := make([]CommodityBasketStrategy, 0, len(commodityBasketStrategies))
	for _, strategy := range commodityBasketStrategies {
		out = append(out, strategy)
	}
	return out
}

func (strategy CommodityBasketStrategy) InvestmentRatio() float64 {
	total := 0.0
	for _, leg := range strategy.Legs {
		if leg.Weight > 0 {
			total += leg.Weight
		}
	}
	if total < 0 {
		return 0
	}
	if total > 1 {
		return 1
	}
	return total
}

func (strategy CommodityBasketStrategy) StaticRatio() float64 {
	staticRatio := 1 - strategy.InvestmentRatio()
	if staticRatio < 0 {
		return 0
	}
	return staticRatio
}
