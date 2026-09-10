package domain

import "strings"

type SingleCommodityFutureStrategy struct {
	FundSymbol       string
	ReferenceSymbol  string
	Label            string
	InvestmentRatio  float64
	FXHoldingSymbol  string
	StaticRatioLabel string
}

var singleCommodityFutureStrategies = map[string]SingleCommodityFutureStrategy{}

func SingleCommodityFutureStrategyForFund(symbol string) (SingleCommodityFutureStrategy, bool) {
	strategy, ok := singleCommodityFutureStrategies[strings.ToUpper(strings.TrimSpace(symbol))]
	return strategy, ok
}
