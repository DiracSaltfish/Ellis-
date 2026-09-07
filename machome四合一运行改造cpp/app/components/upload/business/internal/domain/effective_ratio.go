package domain

import "strings"

const (
	EffectiveRatioSourceManualOverride       = "manual_override"
	EffectiveRatioSourceWeightedAnchor       = "weighted_anchor"
	EffectiveRatioSourceCommodityBasket      = "commodity_basket"
	EffectiveRatioSourceSingleCommodity      = "single_commodity_future"
	EffectiveRatioSourceConfiguredPosition   = "configured_position"
	EffectiveRatioSourceImplicitFullPosition = "implicit_full_position"
)

func DefaultEffectiveRatio(data ValuationData, symbol string) (ratio float64, source string) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" {
		return 1, EffectiveRatioSourceImplicitFullPosition
	}
	if strategy, ok := WeightedAnchorStrategyForFund(symbol); ok && strategy.InvestmentRatio > 0 {
		return strategy.InvestmentRatio, EffectiveRatioSourceWeightedAnchor
	}
	if strategy, ok := CommodityBasketStrategyForFund(symbol); ok && strategy.InvestmentRatio() > 0 {
		return strategy.InvestmentRatio(), EffectiveRatioSourceCommodityBasket
	}
	if strategy, ok := SingleCommodityFutureStrategyForFund(symbol); ok && strategy.InvestmentRatio > 0 {
		return strategy.InvestmentRatio, EffectiveRatioSourceSingleCommodity
	}
	if value, ok := data.Positions[symbol]; ok && value > 0 {
		return value, EffectiveRatioSourceConfiguredPosition
	}
	return 1, EffectiveRatioSourceImplicitFullPosition
}

func EffectiveRatio(data ValuationData, symbol string) (ratio float64, source string, override *ManualValuationPositionOverride) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if item, ok := data.ManualPositionOverrides[symbol]; ok && item.Ratio > 0 {
		copy := item
		copy.Symbol = symbol
		return copy.Ratio, EffectiveRatioSourceManualOverride, &copy
	}
	ratio, source = DefaultEffectiveRatio(data, symbol)
	return ratio, source, nil
}
