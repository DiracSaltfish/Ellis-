package privatevaluation

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

var shanghaiLocation = func() *time.Location {
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	return location
}()

const sharedCFETSSpotFreshness = 3 * time.Minute

const (
	cfetsPreopenFallbackStartMinute = 9*60 + 15
	cfetsPreopenFallbackEndMinute   = 9*60 + 35
)

func isIndexProxyMode(mode CalculationMode) bool {
	return mode == CalculationModeNQProxy || mode == CalculationModeESProxy ||
		mode == CalculationModeN225MProxy || mode == CalculationModeDAXProxy
}

// acceptsCFETSPreopenFallback deliberately makes the calendar/time contract
// explicit in the valuation engine, rather than trusting an uploader label.
// A fallback never becomes actionable and cannot leak beyond 09:35.
func acceptsCFETSPreopenFallback(definition FundDefinition, input Input, now time.Time) bool {
	if input.FX.Source != CFETSPreopenFallbackSource || !isIndexProxyMode(definition.CalculationMode) {
		return false
	}
	localNow := now.In(shanghaiLocation)
	if localNow.Weekday() == time.Saturday || localNow.Weekday() == time.Sunday {
		return false
	}
	minute := localNow.Hour()*60 + localNow.Minute()
	today := localNow.Format("2006-01-02")
	return minute >= cfetsPreopenFallbackStartMinute && minute < cfetsPreopenFallbackEndMinute &&
		input.PCF.TradingDay == today && input.PCF.PreTradingDay != "" && input.FX.TradingDay >= input.PCF.PreTradingDay && input.FX.TradingDay < today &&
		input.IB.MarketDataType == "Live" && input.IB.ObservedAt.In(shanghaiLocation).Format("2006-01-02") == today &&
		now.Sub(input.IB.ObservedAt) >= 0 && now.Sub(input.IB.ObservedAt) <= 15*time.Second &&
		now.Sub(input.GeneratedAt) >= 0 && now.Sub(input.GeneratedAt) <= 15*time.Second
}

// Calculate keeps the original single-input call shape for SZ159518 callers.
// New callers must use CalculateForSymbol so an empty input still produces the
// correct private fund identity.
func Calculate(input *Input, quotes map[string]domain.Quote, now time.Time) Snapshot {
	symbol := TargetSymbol
	if input != nil && strings.TrimSpace(input.Symbol) != "" {
		symbol = input.Symbol
	}
	return CalculateForSymbol(symbol, input, quotes, now)
}

func CalculateForSymbol(symbol string, input *Input, quotes map[string]domain.Quote, now time.Time) Snapshot {
	if now.IsZero() {
		now = time.Now()
	}
	definition, supported := Definition(symbol)
	if !supported {
		return Snapshot{
			SchemaVersion: SchemaVersion,
			Symbol:        strings.ToUpper(strings.TrimSpace(symbol)),
			ModelVersion:  ModelVersion,
			AsOf:          now,
			OrderBook:     []OrderBookValuation{},
			Warnings:      []string{"不支持的 private 标的"},
		}
	}
	snapshot := Snapshot{
		SchemaVersion: SchemaVersion,
		Symbol:        definition.Symbol,
		Name:          definition.Name,
		ModelVersion:  definition.ModelVersion,
		ValuationKind: definition.CalculationMode,
		AsOf:          now,
		OrderBook:     []OrderBookValuation{},
		Warnings:      []string{},
	}
	if input == nil {
		snapshot.Warnings = append(snapshot.Warnings, "尚未收到 Mac-home 的 private 估值输入")
		return snapshot
	}
	copyInput := *input
	snapshot.Input = &copyInput
	if err := input.Validate(); err != nil {
		snapshot.Warnings = append(snapshot.Warnings, "private 输入无效："+err.Error())
		return snapshot
	}
	if input.FX.Source == CFETSPreopenFallbackSource {
		snapshot.CalculationState = "preopen_fx_fallback"
	}
	if input.FX.Source == CFETSPreopenFallbackSource && !acceptsCFETSPreopenFallback(definition, *input, now) {
		snapshot.CalculationState = "expired"
		snapshot.Warnings = append(snapshot.Warnings, "上一交易日 CFETS 回退只允许在工作日 09:15–09:35 配合当日 PCF 展示；当前已失效，停止估值")
		return snapshot
	}

	if input.FX.Source == CFETSSpotRateSource && isIndexProxyMode(definition.CalculationMode) && input.IB.MarketDataType != "HistoricalBidAsk" {
		observed := input.FX.SourceObservedAt
		if observed.IsZero() {
			observed = input.FX.FetchedAt
		} // older persisted input compatibility
		snapshot.CalculationState = "realtime"
		if input.PCF.TradingDay != now.In(shanghaiLocation).Format("2006-01-02") || input.FX.TradingDay != input.PCF.TradingDay || now.Sub(observed) > sharedCFETSSpotFreshness || observed.Sub(now) > 30*time.Second {
			snapshot.CalculationState = "expired"
			snapshot.Warnings = append(snapshot.Warnings, "当日 PCF 或 CFETS 即期源已失效，等待新输入")
			return snapshot
		}
	}
	domestic, ok := quoteForSymbol(quotes, definition.Symbol)
	if !ok {
		snapshot.Warnings = append(snapshot.Warnings, "公共 Sina 行情中缺少 "+definition.Symbol)
		return snapshot
	}
	copyQuote := domestic
	snapshot.DomesticQuote = &copyQuote
	domesticBid, okBid := bestLevel(domestic.BidLevels)
	domesticAsk, okAsk := bestLevel(domestic.AskLevels)
	if !okBid || !okAsk {
		snapshot.Warnings = append(snapshot.Warnings, "公共 Sina 行情缺少 "+definition.Symbol+" 买一或卖一")
		return snapshot
	}
	if definition.CalculationMode == CalculationModeFullCashSubstitutionPCF {
		return calculateFullCashSubstitutionPCF(snapshot, *input, domestic, domesticBid, domesticAsk, definition, now)
	}
	if definition.CalculationMode == CalculationModeIndiaT2MultiMarket {
		return calculateIndiaT2MultiMarket(snapshot, *input, domestic, domesticBid, domesticAsk, definition, now)
	}
	if definition.CalculationMode == CalculationModeLOFWeightedAnchor {
		return calculateLOFWeightedAnchor(snapshot, *input, domestic, domesticBid, domesticAsk, definition, now)
	}
	if definition.CalculationMode == CalculationModeSilverSettlement {
		return calculateSilverSettlement(snapshot, *input, domestic, domesticBid, domesticAsk, now)
	}

	fxRate := *input.FX.Rate
	referenceBid := *input.IB.Bid
	referenceAsk := *input.IB.Ask
	cash := *input.PCF.EstimateCashComponentCNY
	proxyUnits := definition.XOPEquivalentShares
	if proxyUnits <= 0 && input.PCF.XOPEquivalentShares != nil {
		proxyUnits = *input.PCF.XOPEquivalentShares
	}
	if !finitePositive(proxyUnits) {
		snapshot.Warnings = append(snapshot.Warnings, "缺少该标的经 PCF/NAV 审计的代理等价数量")
		return snapshot
	}
	contractMultiplier := 1.0
	formula := "XOP Bid/Ask × CFETS USD/CNY + EstimateCashComponent"
	if definition.CalculationMode == CalculationModeNQProxy {
		// CME NQ is quoted in index points; one standard contract is USD20 per
		// point. The raw input coefficient is therefore a (possibly fractional)
		// NQ contract equivalent, not an ETF share count.
		contractMultiplier = 20
		formula = "PCF/NAV 校准 NQ 合约 × 20 USD/点 × NQ Bid/Ask × CFETS USD/CNY + EstimateCashComponent（预扫描）"
	} else if definition.CalculationMode == CalculationModeESProxy {
		// CME ES is quoted in index points; one standard contract is USD50 per
		// point. The coefficient remains fund-specific because identical index
		// names do not imply identical PCF substitution/cash baskets.
		contractMultiplier = 50
		formula = "PCF/NAV 校准 ES 合约 × 50 USD/点 × ES Bid/Ask × CFETS USD/CNY + EstimateCashComponent（预扫描）"
	} else if definition.CalculationMode == CalculationModeN225MProxy {
		// OSE Nikkei 225 mini is quoted in index points and carries JPY100
		// per point. The four domestic products hold different Japanese ETF
		// wrappers, so this is a dated PCF/NAV calibration, not a shared ratio.
		contractMultiplier = 100
		formula = "PCF/NAV 校准 N225M 合约 × 100 JPY/点 × N225M Bid/Ask × CFETS JPY/CNY + EstimateCashComponent（预扫描）"
	} else if definition.CalculationMode == CalculationModeDAXProxy {
		// IBKR exposes Eurex Mini-DAX (FDXM) through the DAX root with a EUR5
		// multiplier. The two domestic funds have different PCF units and share
		// quantities, so each receives an independent dated calibration.
		contractMultiplier = 5
		formula = "PCF 证券腿 ÷ SAFE EUR/CNY ÷ Xetra 17:35 锚点 FDXM（17:30 对照）校准合约数 × 5 EUR/点 × DAX Bid/Ask × CFETS EUR/CNY + EstimateCashComponent（预扫描）"
	}
	stockBid := proxyUnits * contractMultiplier * referenceBid * fxRate
	stockAsk := proxyUnits * contractMultiplier * referenceAsk * fxRate
	basketBid := stockBid + cash
	basketAsk := stockAsk + cash
	if !finitePositive(basketBid) || !finitePositive(basketAsk) {
		snapshot.Warnings = append(snapshot.Warnings, "总篮子资产计算结果无效")
		return snapshot
	}
	redemptionUnit := *input.PCF.CreationRedemptionUnit
	navBid := basketBid / redemptionUnit
	navAsk := basketAsk / redemptionUnit
	if definition.Symbol == SH513350Symbol {
		formula = "固定 1046 股 XOP Bid/Ask × CFETS USD/CNY + EstimateCashComponent"
	}
	valuation := BasketValuation{
		RedemptionUnit:           redemptionUnit,
		XOPEquivalentShares:      proxyUnits,
		EstimateCashComponentCNY: cash,
		StockComponentBidCNY:     round(stockBid, 6),
		StockComponentAskCNY:     round(stockAsk, 6),
		BasketBidCNY:             round(basketBid, 6),
		BasketAskCNY:             round(basketAsk, 6),
		NAVBid:                   round(navBid, 10),
		NAVAsk:                   round(navAsk, 10),
		BuyDirectionPremiumRate:  round(domesticAsk.Price/navBid-1, 10),
		SellDirectionPremiumRate: round(domesticBid.Price/navAsk-1, 10),
		Formula:                  formula,
	}
	snapshot.Valuation = &valuation
	snapshot.OrderBook = buildOrderBook(domestic, navBid, navAsk)
	snapshot.Ready = true
	snapshot.Actionable = appendFreshnessWarnings(&snapshot, *input, domestic, definition, now)
	return snapshot
}

func calculateLOFWeightedAnchor(snapshot Snapshot, input Input, domestic domain.Quote, domesticBid, domesticAsk domain.Level, definition FundDefinition, now time.Time) Snapshot {
	lof := input.LOF
	if lof == nil || lof.BaseFX.Rate == nil || lof.CurrentFX.Rate == nil || input.IB.Bid == nil || input.IB.Ask == nil {
		snapshot.Warnings = append(snapshot.Warnings, "162411 LOF 收盘锚点输入不完整")
		return snapshot
	}
	baseReference := lof.BaseReference.Price
	baseFX, currentFX := *lof.BaseFX.Rate, *lof.CurrentFX.Rate
	currentBid, currentAsk := *input.IB.Bid, *input.IB.Ask
	effectiveRatio := lof.EffectiveRatio.Value
	staticRatio := 1 - effectiveRatio
	fxMultiplier := currentFX / baseFX
	referenceBidMultiplier := currentBid / baseReference * fxMultiplier
	referenceAskMultiplier := currentAsk / baseReference * fxMultiplier
	staticValue := lof.BaseNAV * staticRatio
	riskBid := lof.BaseNAV * effectiveRatio * referenceBidMultiplier
	riskAsk := lof.BaseNAV * effectiveRatio * referenceAskMultiplier
	navBid, navAsk := staticValue+riskBid, staticValue+riskAsk
	if !finitePositive(navBid) || !finitePositive(navAsk) || navAsk < navBid {
		snapshot.Warnings = append(snapshot.Warnings, "162411 LOF XOP/SAFE 加权 NAV 计算结果无效")
		return snapshot
	}
	formula := "基准单位净值 × [(1−实际有效仓位) + 实际有效仓位 × (当前 XOP Bid/Ask ÷ 基准日 XOP 美东16:00常规收盘价) × (T日 SAFE 美元兑人民币中间价 ÷ 基准日 SAFE 美元兑人民币中间价)]"
	valuation := BasketValuation{
		RedemptionUnit:           1,
		EstimateCashComponentCNY: round(staticValue, 10),
		StockComponentBidCNY:     round(riskBid, 10),
		StockComponentAskCNY:     round(riskAsk, 10),
		BasketBidCNY:             round(navBid, 10),
		BasketAskCNY:             round(navAsk, 10),
		NAVBid:                   round(navBid, 10),
		NAVAsk:                   round(navAsk, 10),
		BuyDirectionPremiumRate:  round(domesticAsk.Price/navBid-1, 10),
		SellDirectionPremiumRate: round(domesticBid.Price/navAsk-1, 10),
		Formula:                  formula,
	}
	var last *float64
	if input.IB.Last != nil {
		value := *input.IB.Last
		last = &value
	}
	snapshot.Valuation = &valuation
	snapshot.LOFValuation = &LOFWeightedAnchorValuation{
		BaseNAV:                        round(lof.BaseNAV, 10),
		BaseNAVDate:                    lof.BaseNAVDate,
		BaseNAVSource:                  lof.BaseNAVSource,
		BaseNAVFetchedAt:               lof.BaseNAVFetchedAt,
		BaseReferenceSymbol:            lof.BaseReference.Symbol,
		BaseReferencePrice:             round(baseReference, 10),
		BaseReferencePriceBasis:        lof.BaseReference.PriceBasis,
		BaseReferenceTargetAt:          lof.BaseReference.TargetAt,
		BaseReferenceObservedAt:        lof.BaseReference.ObservedAt,
		BaseReferenceSource:            lof.BaseReference.Source,
		BaseReferenceCaptureStatus:     lof.BaseReference.CaptureStatus,
		BaseFX:                         round(baseFX, 10),
		BaseFXTradingDay:               lof.BaseFX.TradingDay,
		BaseFXSource:                   lof.BaseFX.Source,
		CurrentFX:                      round(currentFX, 10),
		CurrentFXTradingDay:            lof.CurrentFX.TradingDay,
		CurrentFXSource:                lof.CurrentFX.Source,
		FXMultiplier:                   round(fxMultiplier, 12),
		EffectiveRatio:                 round(effectiveRatio, 10),
		EffectiveRatioSource:           lof.EffectiveRatio.Source,
		DefaultEffectiveRatio:          round(lof.EffectiveRatio.DefaultValue, 10),
		DefaultEffectiveRatioSource:    lof.EffectiveRatio.DefaultSource,
		StaticRatio:                    round(staticRatio, 10),
		CurrentReferenceBid:            round(currentBid, 10),
		CurrentReferenceAsk:            round(currentAsk, 10),
		CurrentReferenceLast:           last,
		CurrentReferenceObservedAt:     input.IB.ObservedAt,
		CurrentReferenceSource:         input.IB.Source,
		CurrentReferenceMarketDataType: input.IB.MarketDataType,
		CurrentReferenceQuoteSession:   input.IB.QuoteSession,
		ReferenceBidMultiplier:         round(referenceBidMultiplier, 12),
		ReferenceAskMultiplier:         round(referenceAskMultiplier, 12),
		NAVBid:                         round(navBid, 10),
		NAVAsk:                         round(navAsk, 10),
	}
	snapshot.OrderBook = buildOrderBook(domestic, navBid, navAsk)
	snapshot.Ready = true
	snapshot.Actionable = appendLOFWeightedAnchorFreshnessWarnings(&snapshot, input, domestic, definition, now)
	return snapshot
}

func calculateIndiaT2MultiMarket(snapshot Snapshot, input Input, domestic domain.Quote, domesticBid, domesticAsk domain.Level, definition FundDefinition, now time.Time) Snapshot {
	india := input.India
	if india == nil || input.IB.Bid == nil || input.IB.Ask == nil || india.BaseFX.Rate == nil || india.CurrentFX.Rate == nil {
		snapshot.Warnings = append(snapshot.Warnings, "164824 T-2 多市场输入不完整")
		return snapshot
	}
	anchorPrice := 0.0
	for _, anchor := range india.Anchors {
		anchorPrice += anchor.Weight * anchor.Price
	}
	if !finitePositive(anchorPrice) {
		snapshot.Warnings = append(snapshot.Warnings, "164824 T-2 多市场加权 INDA 锚点无效")
		return snapshot
	}
	directFormula := "T-2 单位净值 × [10.63% 静态 + 89.37% × (INDA Bid/Ask ÷ T-2 四市场加权 INDA 锚点) × (T日 SAFE USD/CNY ÷ T-2日 SAFE USD/CNY)]"
	direct, ok := indiaT2Valuation(india, anchorPrice, *input.IB.Bid, *input.IB.Ask, domesticBid, domesticAsk, directFormula)
	if !ok {
		snapshot.Warnings = append(snapshot.Warnings, "164824 T-2 多市场直接 INDA NAV 计算结果无效")
		return snapshot
	}
	directOrderBook := buildOrderBook(domestic, direct.NAVBid, direct.NAVAsk)
	// Keep the established top-level valuation direct-INDA for historical
	// compatibility and for the final-NAV display. The switchable intraday
	// NIFTY reading is carried independently below.
	snapshot.Valuation = &direct
	snapshot.OrderBook = directOrderBook
	variants := &IndiaValuations{
		DefaultKey: "direct_inda",
		DirectINDA: IndiaValuationVariant{
			Key:         "direct_inda",
			Label:       "最终净值口径 · 直接 INDA",
			Description: "使用实时 INDA Bid/Ask；用于最终净值代理，离场时段宽点差仅供参考。",
			QuoteSymbol: IndiaReferenceSymbol,
			Valuation:   direct,
			OrderBook:   directOrderBook,
		},
	}
	if bridgeBid, bridgeAsk, ok := indiaNiftyBridgePrices(india.NiftyBridge); ok {
		bridgeFormula := "T-2 单位净值 × [10.63% 静态 + 89.37% × (合成 INDA Bid/Ask ÷ T-2 四市场加权 INDA 锚点) × (T日 SAFE USD/CNY ÷ T-2日 SAFE USD/CNY)]；合成 INDA = 纽约15:49-15:51（中心15:50）INDA Bid/Ask × (当前 NIFTY Bid/Ask ÷ 同窗 NIFTY Ask/Bid)^β，β=1；NIFTY 从每月最后一个周二起使用下月合约，跨合约时仅用前一周一12:28-12:32 BJT 实盘基差校正"
		if bridge, calculated := indiaT2Valuation(india, anchorPrice, bridgeBid, bridgeAsk, domesticBid, domesticAsk, bridgeFormula); calculated {
			bridgeOrderBook := buildOrderBook(domestic, bridge.NAVBid, bridge.NAVAsk)
			variants.NiftyBridge = &IndiaValuationVariant{
				Key:         "nifty_bridge",
				Label:       "盘中对冲口径 · NIFTY 桥接",
				Description: "以上一美国正常交易日 15:49-15:51 ET（中心15:50）的同步 INDA/NIFTY 参考点，将实时 NIFTY 映射为合成 INDA；用于中国盘中 IOPV 与 NIFTY 对冲。",
				QuoteSymbol: "NIFTY",
				Valuation:   bridge,
				OrderBook:   bridgeOrderBook,
			}
			if indiaChinaContinuousSession(now) {
				variants.DefaultKey = "nifty_bridge"
			}
		} else {
			snapshot.Warnings = append(snapshot.Warnings, "164824 NIFTY 桥接估值计算结果无效；仅展示直接 INDA 口径")
		}
	} else if india.NiftyBridge != nil {
		if india.NiftyBridge.ContractSelectionVersion != NiftyContractSelectionVersion {
			snapshot.Warnings = append(snapshot.Warnings, "164824 NIFTY 桥接采用旧合约选月规则；已停用，仅展示直接 INDA 口径")
		} else {
			snapshot.Warnings = append(snapshot.Warnings, "164824 NIFTY 桥接输入不完整或缺少换月基差；仅展示直接 INDA 口径")
		}
	}
	snapshot.IndiaValuations = variants
	snapshot.Ready = true
	snapshot.Actionable = appendIndiaT2FreshnessWarnings(&snapshot, input, domestic, now)
	return snapshot
}

func indiaT2Valuation(india *IndiaT2Input, anchorPrice, priceBid, priceAsk float64, domesticBid, domesticAsk domain.Level, formula string) (BasketValuation, bool) {
	if india == nil || india.BaseFX.Rate == nil || india.CurrentFX.Rate == nil || !finitePositive(anchorPrice) || !finitePositive(priceBid) || !finitePositive(priceAsk) || priceAsk < priceBid {
		return BasketValuation{}, false
	}
	fxMultiplier := *india.CurrentFX.Rate / *india.BaseFX.Rate
	staticValue := india.BaseNAV * india.StaticRatio
	stockBid := india.BaseNAV * india.InvestmentRatio * (priceBid / anchorPrice) * fxMultiplier
	stockAsk := india.BaseNAV * india.InvestmentRatio * (priceAsk / anchorPrice) * fxMultiplier
	navBid, navAsk := staticValue+stockBid, staticValue+stockAsk
	if !finitePositive(navBid) || !finitePositive(navAsk) || navAsk < navBid {
		return BasketValuation{}, false
	}
	return BasketValuation{
		RedemptionUnit:           1,
		XOPEquivalentShares:      round(anchorPrice, 8),
		EstimateCashComponentCNY: round(staticValue, 10),
		StockComponentBidCNY:     round(stockBid, 10),
		StockComponentAskCNY:     round(stockAsk, 10),
		BasketBidCNY:             round(navBid, 10),
		BasketAskCNY:             round(navAsk, 10),
		NAVBid:                   round(navBid, 10),
		NAVAsk:                   round(navAsk, 10),
		BuyDirectionPremiumRate:  round(domesticAsk.Price/navBid-1, 10),
		SellDirectionPremiumRate: round(domesticBid.Price/navAsk-1, 10),
		Formula:                  formula,
	}, true
}

func indiaNiftyBridgePrices(bridge *IndiaNiftyBridgeInput) (float64, float64, bool) {
	if bridge == nil || bridge.ContractSelectionVersion != NiftyContractSelectionVersion || bridge.Nifty.Bid == nil || bridge.Nifty.Ask == nil || bridge.INDAReference.Bid == nil || bridge.INDAReference.Ask == nil || bridge.NiftyReference.Bid == nil || bridge.NiftyReference.Ask == nil || !finitePositive(bridge.Beta) {
		return 0, 0, false
	}
	// Use the conservative executable envelope. For the synthetic bid, sell
	// current NIFTY at its bid against the reference NIFTY ask; for the ask,
	// buy current NIFTY at its ask against the reference NIFTY bid.
	bid := *bridge.INDAReference.Bid * math.Pow(*bridge.Nifty.Bid / *bridge.NiftyReference.Ask, bridge.Beta)
	ask := *bridge.INDAReference.Ask * math.Pow(*bridge.Nifty.Ask / *bridge.NiftyReference.Bid, bridge.Beta)
	if !finitePositive(bid) || !finitePositive(ask) || ask < bid {
		return 0, 0, false
	}
	return bid, ask, true
}

func indiaChinaContinuousSession(now time.Time) bool {
	localNow := now.In(shanghaiLocation)
	if localNow.Weekday() == time.Saturday || localNow.Weekday() == time.Sunday {
		return false
	}
	minute := localNow.Hour()*60 + localNow.Minute()
	return (minute >= 9*60+30 && minute <= 11*60+30) || (minute >= 13*60 && minute < 15*60)
}

func calculateFullCashSubstitutionPCF(snapshot Snapshot, input Input, domestic domain.Quote, domesticBid, domesticAsk domain.Level, definition FundDefinition, now time.Time) Snapshot {
	fxRates := make(map[string]float64, len(input.FXRates))
	for _, rate := range input.FXRates {
		if rate.Rate != nil {
			fxRates[rate.Pair] = *rate.Rate
		}
	}
	quotes := make(map[string]MarketQuoteInput, len(input.MarketQuotes))
	for _, quote := range input.MarketQuotes {
		quotes[marketQuoteKey(quote.Market, quote.Symbol)] = quote
	}
	components := make([]ComponentValuation, 0, len(input.PCF.Components))
	stockBid, stockAsk := 0.0, 0.0
	for _, component := range input.PCF.Components {
		quote, ok := quotes[marketQuoteKey(component.Market, component.Symbol)]
		if !ok || quote.Bid == nil || quote.Ask == nil {
			snapshot.Warnings = append(snapshot.Warnings, "缺少 PCF 成分行情："+component.Symbol)
			return snapshot
		}
		fxPair, fxRate := component.Currency+"/CNY", 1.0
		if component.Currency != "CNY" {
			var ok bool
			fxRate, ok = fxRates[fxPair]
			if !ok || !finitePositive(fxRate) {
				snapshot.Warnings = append(snapshot.Warnings, "缺少成分汇率："+fxPair)
				return snapshot
			}
		}
		bidValue := component.Quantity * *quote.Bid * fxRate
		askValue := component.Quantity * *quote.Ask * fxRate
		stockBid += bidValue
		stockAsk += askValue
		components = append(components, ComponentValuation{
			Symbol:      component.Symbol,
			Name:        component.Name,
			Market:      component.Market,
			Currency:    component.Currency,
			Quantity:    component.Quantity,
			Bid:         *quote.Bid,
			Ask:         *quote.Ask,
			FXPair:      fxPair,
			FXRate:      fxRate,
			BidValueCNY: round(bidValue, 6),
			AskValueCNY: round(askValue, 6),
			Source:      quote.Source,
			ObservedAt:  quote.ObservedAt,
		})
	}
	cash := *input.PCF.EstimateCashComponentCNY
	basketBid, basketAsk := stockBid+cash, stockAsk+cash
	if !finitePositive(basketBid) || !finitePositive(basketAsk) {
		snapshot.Warnings = append(snapshot.Warnings, "多市场 PCF 总篮子资产计算结果无效")
		return snapshot
	}
	redemptionUnit := *input.PCF.CreationRedemptionUnit
	navBid, navAsk := basketBid/redemptionUnit, basketAsk/redemptionUnit
	snapshot.Valuation = &BasketValuation{
		RedemptionUnit:           redemptionUnit,
		EstimateCashComponentCNY: cash,
		StockComponentBidCNY:     round(stockBid, 6),
		StockComponentAskCNY:     round(stockAsk, 6),
		BasketBidCNY:             round(basketBid, 6),
		BasketAskCNY:             round(basketAsk, 6),
		NAVBid:                   round(navBid, 10),
		NAVAsk:                   round(navAsk, 10),
		BuyDirectionPremiumRate:  round(domesticAsk.Price/navBid-1, 10),
		SellDirectionPremiumRate: round(domesticBid.Price/navAsk-1, 10),
		Formula:                  fullCashSubstitutionFormula(input.PCF.Components),
	}
	snapshot.Components = components
	snapshot.OrderBook = buildOrderBook(domestic, navBid, navAsk)
	snapshot.Ready = true
	snapshot.Actionable = appendFreshnessWarnings(&snapshot, input, domestic, definition, now)
	return snapshot
}

func fullCashSubstitutionFormula(components []PCFComponentInput) string {
	pairs := make(map[string]struct{}, 2)
	for _, component := range components {
		if component.Currency != "CNY" {
			pairs[component.Currency+"/CNY"] = struct{}{}
		}
	}
	parts := make([]string, 0, len(pairs)+1)
	if _, ok := pairs["HKD/CNY"]; ok {
		parts = append(parts, "HKD/CNY")
		delete(pairs, "HKD/CNY")
	}
	if _, ok := pairs["USD/CNY"]; ok {
		parts = append(parts, "USD/CNY")
		delete(pairs, "USD/CNY")
	}
	for pair := range pairs {
		parts = append(parts, pair)
	}
	sort.Strings(parts)
	if anyCNYComponent(components) {
		parts = append(parts, "CNY 直算")
	}
	return fmt.Sprintf("%d只 PCF 成分股 Bid/Ask × %s + EstimateCashComponent（未扣实际卖出与结算费用）", len(components), strings.Join(parts, "、"))
}

func anyCNYComponent(components []PCFComponentInput) bool {
	for _, component := range components {
		if component.Currency == "CNY" {
			return true
		}
	}
	return false
}

func buildOrderBook(quote domain.Quote, navBid float64, navAsk float64) []OrderBookValuation {
	asks := positiveLevels(quote.AskLevels)
	bids := positiveLevels(quote.BidLevels)
	sort.SliceStable(asks, func(i, j int) bool { return asks[i].Level > asks[j].Level })
	sort.SliceStable(bids, func(i, j int) bool { return bids[i].Level < bids[j].Level })
	rows := make([]OrderBookValuation, 0, len(asks)+len(bids))
	appendRows := func(side string, levels []domain.Level) {
		for _, level := range levels {
			rows = append(rows, OrderBookValuation{
				Side:                      side,
				Level:                     level.Level,
				Price:                     level.Price,
				Volume:                    level.Volume,
				PremiumRateVsBasketBidNAV: round(level.Price/navBid-1, 10),
				PremiumRateVsBasketAskNAV: round(level.Price/navAsk-1, 10),
			})
		}
	}
	appendRows("ask", asks)
	appendRows("bid", bids)
	return rows
}

func appendFreshnessWarnings(snapshot *Snapshot, input Input, domestic domain.Quote, definition FundDefinition, now time.Time) bool {
	localNow := now.In(shanghaiLocation)
	actionable := true
	minute := localNow.Hour()*60 + localNow.Minute()
	inContinuousSession := (minute >= 9*60+30 && minute <= 11*60+30) || (minute >= 13*60 && minute < 15*60)
	if localNow.Weekday() == time.Saturday || localNow.Weekday() == time.Sunday || !inContinuousSession {
		snapshot.Warnings = append(snapshot.Warnings, "当前不在中国连续竞价时段（工作日 09:30-11:30、13:00-15:00）")
		actionable = false
	}
	today := localNow.Format("2006-01-02")
	if input.PCF.TradingDay != today {
		snapshot.Warnings = append(snapshot.Warnings, fmt.Sprintf("PCF 日期为 %s，并非当前交易日 %s", input.PCF.TradingDay, today))
		actionable = false
	}
	if input.PCF.Redemption != "Y" {
		snapshot.Warnings = append(snapshot.Warnings, "当日 PCF 不允许赎回（Redemption != Y）")
		actionable = false
	}
	if definition.ExpectedSecurityComponentCount > 0 && input.PCF.ComponentCount != definition.ExpectedSecurityComponentCount {
		snapshot.Warnings = append(snapshot.Warnings, fmt.Sprintf("PCF 证券成分数由预期的 %d 变为 %d，篮子需复核", definition.ExpectedSecurityComponentCount, input.PCF.ComponentCount))
		actionable = false
	}
	if definition.CalculationMode == CalculationModeFullCashSubstitutionPCF {
		if input.PCF.Creation != "Y" {
			snapshot.Warnings = append(snapshot.Warnings, "当日 PCF 不允许申购（Creation != Y）")
			actionable = false
		}
		return appendFullCashSubstitutionFreshnessWarnings(snapshot, input, domestic, now, actionable)
	}
	if input.FX.Source == CFETSPreopenFallbackSource {
		snapshot.Warnings = append(snapshot.Warnings, fmt.Sprintf("使用上一交易日 CFETS %s %s，配合当日指数期货报价仅供集合竞价观察，禁止执行", input.FX.TradingDay, input.FX.QuoteTime))
		actionable = false
	} else if input.FX.TradingDay != today {
		snapshot.Warnings = append(snapshot.Warnings, fmt.Sprintf("CFETS 使用回退交易日 %s，并非当前交易日 %s", input.FX.TradingDay, today))
		actionable = false
	}
	if input.GeneratedAt.IsZero() || now.Sub(input.GeneratedAt) > 15*time.Second || input.GeneratedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "Mac-home private 采集心跳超过 15 秒未更新")
		actionable = false
	}
	if checkedAt := ibQuoteStreamCheckedAt(input.IB); checkedAt.IsZero() || now.Sub(checkedAt) > 15*time.Second || checkedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "IB "+definition.ReferenceSymbol+" Bid/Ask 心跳超过 15 秒未更新")
		actionable = false
	}
	if input.IB.MarketDataType != "Live" {
		snapshot.Warnings = append(snapshot.Warnings, "IB 行情不是 Live（Delayed/Frozen 仅供展示）")
		actionable = false
	}
	fxFreshness := 90 * time.Second
	fxWarning := "CFETS 采集心跳超过 90 秒未更新"
	if input.FX.Source == CFETSSpotRateSource {
		fxFreshness = sharedCFETSSpotFreshness
		fxWarning = "共享 CFETS 即期汇率超过 3 分钟未更新"
	}
	if input.FX.FetchedAt.IsZero() || now.Sub(input.FX.FetchedAt) > fxFreshness || input.FX.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, fxWarning)
		actionable = false
	}
	if domestic.FetchedAt.IsZero() || now.Sub(domestic.FetchedAt) > 15*time.Second || domestic.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "公共 Sina 五档超过 15 秒未更新")
		actionable = false
	}
	if quoteAt, ok := domesticQuoteTime(domestic); !ok || localNow.Sub(quoteAt) > 15*time.Second || quoteAt.Sub(localNow) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "Sina 行情时间戳不是 15 秒内的当前盘口")
		actionable = false
	}
	if definition.CalculationMode == CalculationModeNQProxy {
		snapshot.Warnings = append(snapshot.Warnings, "纳指 PCF/NQ 仅为指示性预扫描：尚无该代码实际退款/现金差额校准，禁止据此执行赎回套利")
		actionable = false
	} else if definition.CalculationMode == CalculationModeESProxy {
		snapshot.Warnings = append(snapshot.Warnings, "标普 PCF/ES 仅为指示性预扫描：卖券窗口、最终汇率与实际退款/现金差额尚未逐代码校准，禁止据此执行赎回套利")
		actionable = false
	} else if definition.CalculationMode == CalculationModeN225MProxy {
		snapshot.Warnings = append(snapshot.Warnings, "日经 PCF/N225M 仅为指示性预扫描：各基金 T+1 代理卖出、最终日元汇率与实际退款/现金差额尚未逐代码校准，禁止据此执行赎回套利")
		actionable = false
	} else if definition.CalculationMode == CalculationModeDAXProxy {
		snapshot.Warnings = append(snapshot.Warnings, "德国 DAX PCF/FDXM 仅为指示性预扫描：系数已按 Xetra 17:35 收盘集合竞价时点（17:30 对照）锚定，但最终卖券时点、欧元汇率与实际现金差额尚未逐代码校准，禁止据此执行赎回套利")
		actionable = false
	}
	return actionable
}

func appendIndiaT2FreshnessWarnings(snapshot *Snapshot, input Input, domestic domain.Quote, now time.Time) bool {
	india := input.India
	if india == nil {
		snapshot.Warnings = append(snapshot.Warnings, "164824 T-2 输入缺失")
		return false
	}
	actionable := true
	localNow := now.In(shanghaiLocation)
	minute := localNow.Hour()*60 + localNow.Minute()
	inContinuousSession := (minute >= 9*60+30 && minute <= 11*60+30) || (minute >= 13*60 && minute <= 15*60)
	if localNow.Weekday() == time.Saturday || localNow.Weekday() == time.Sunday || !inContinuousSession {
		snapshot.Warnings = append(snapshot.Warnings, "当前不在中国连续竞价时段（工作日 09:30-11:30、13:00-15:00）")
		actionable = false
	}
	today := localNow.Format("2006-01-02")
	if india.CurrentFX.TradingDay != today {
		snapshot.Warnings = append(snapshot.Warnings, fmt.Sprintf("T日 CFETS USD/CNY 即期汇率为 %s，并非当前交易日 %s", india.CurrentFX.TradingDay, today))
		actionable = false
	}
	if input.GeneratedAt.IsZero() || now.Sub(input.GeneratedAt) > 15*time.Second || input.GeneratedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "Mac-home 164824 采集心跳超过 15 秒未更新")
		actionable = false
	}
	if checkedAt := ibQuoteStreamCheckedAt(input.IB); checkedAt.IsZero() || now.Sub(checkedAt) > 15*time.Second || checkedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "IB INDA Bid/Ask 心跳超过 15 秒未更新")
		actionable = false
	}
	if input.IB.MarketDataType != "Live" {
		snapshot.Warnings = append(snapshot.Warnings, "IB INDA 行情不是 Live（Delayed/Frozen 仅供展示）")
		actionable = false
	}
	indiaFXFreshness := 90 * time.Second
	indiaFXWarning := "SAFE USD/CNY 中间价采集心跳超过 90 秒未更新"
	if india.CurrentFX.Source == CFETSSpotRateSource {
		indiaFXFreshness = sharedCFETSSpotFreshness
		indiaFXWarning = "共享 CFETS USD/CNY 即期汇率超过 3 分钟未更新"
	}
	if india.CurrentFX.FetchedAt.IsZero() || now.Sub(india.CurrentFX.FetchedAt) > indiaFXFreshness || india.CurrentFX.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, indiaFXWarning)
		actionable = false
	}
	if domestic.FetchedAt.IsZero() || now.Sub(domestic.FetchedAt) > 15*time.Second || domestic.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "公共 Sina 五档超过 15 秒未更新")
		actionable = false
	}
	if quoteAt, ok := domesticQuoteTime(domestic); !ok || localNow.Sub(quoteAt) > 15*time.Second || quoteAt.Sub(localNow) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "Sina 行情时间戳不是 15 秒内的当前盘口")
		actionable = false
	}
	// The Q2 weights and the T-2 proxy reproduce the disclosed marking rule,
	// but a real redemption still has T+3 dealing, settlement and realised
	// execution basis. Keep the screen explicitly non-executable until those
	// realised redemption observations are calibrated fund-by-fund.
	message := "164824 为 T-2 披露持仓的指示性估值；尚未用实际赎回清算单校准交易费用、换汇和到账损益，禁止自动执行"
	if actionable {
		message += "（当前实时数据健康，但仍需人工执行判断）"
	}
	snapshot.Warnings = append(snapshot.Warnings, message)
	return false
}

func appendLOFWeightedAnchorFreshnessWarnings(snapshot *Snapshot, input Input, domestic domain.Quote, definition FundDefinition, now time.Time) bool {
	lof := input.LOF
	if lof == nil {
		snapshot.Warnings = append(snapshot.Warnings, "162411 LOF 收盘锚点输入缺失")
		return false
	}
	localNow := now.In(shanghaiLocation)
	minute := localNow.Hour()*60 + localNow.Minute()
	inContinuousSession := (minute >= 9*60+30 && minute <= 11*60+30) || (minute >= 13*60 && minute <= 15*60)
	if localNow.Weekday() == time.Saturday || localNow.Weekday() == time.Sunday || !inContinuousSession {
		snapshot.Warnings = append(snapshot.Warnings, "当前不在中国连续竞价时段（工作日 09:30-11:30、13:00-15:00）")
	}
	today := localNow.Format("2006-01-02")
	if lof.CurrentFX.TradingDay != today {
		warning := fmt.Sprintf("T日 SAFE USD/CNY 中间价为 %s，并非当前交易日 %s", lof.CurrentFX.TradingDay, today)
		if lof.CurrentFX.Source == CFETSSpotRateSource {
			warning = fmt.Sprintf("T日 CFETS USD/CNY 即期汇率为 %s，并非当前交易日 %s", lof.CurrentFX.TradingDay, today)
		}
		snapshot.Warnings = append(snapshot.Warnings, warning)
	}
	if input.GeneratedAt.IsZero() || now.Sub(input.GeneratedAt) > 15*time.Second || input.GeneratedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "Mac-home 162411 采集心跳超过 15 秒未更新")
	}
	checkedAt := input.IB.StreamCheckedAt
	if checkedAt.IsZero() {
		checkedAt = input.GeneratedAt
	}
	if checkedAt.IsZero() || now.Sub(checkedAt) > 15*time.Second || checkedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "IB "+definition.ReferenceSymbol+" 数据流超过 15 秒未确认")
	}
	// An executable quote may remain unchanged for longer than 15 seconds.
	// GeneratedAt above is the collector/TWS connection heartbeat;
	// IB.ObservedAt remains the actual quote-event audit and must not be
	// relabelled as a connectivity heartbeat merely because price is unchanged.
	if input.IB.MarketDataType != "Live" {
		snapshot.Warnings = append(snapshot.Warnings, "IB "+definition.ReferenceSymbol+" 行情不是 Live（Delayed/Frozen 仅供展示）")
	}
	if input.IB.QuoteSession != "us_overnight_live" && input.IB.QuoteSession != "us_smart_live" {
		snapshot.Warnings = append(snapshot.Warnings, "IB XOP 当前 quote_session 为 "+input.IB.QuoteSession+"，请按页面所示时段判断流动性")
	}
	lofFXFreshness := 90 * time.Second
	lofFXWarning := "SAFE USD/CNY 中间价采集心跳超过 90 秒未更新"
	if lof.CurrentFX.Source == CFETSSpotRateSource {
		lofFXFreshness = sharedCFETSSpotFreshness
		lofFXWarning = "共享 CFETS USD/CNY 即期汇率超过 3 分钟未更新"
	}
	if lof.CurrentFX.FetchedAt.IsZero() || now.Sub(lof.CurrentFX.FetchedAt) > lofFXFreshness || lof.CurrentFX.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, lofFXWarning)
	}
	// The QDII public branch is published on a rotating symbol batch and
	// declares a 90-second freshness window. Use the successful fetch heartbeat
	// for the five-level book: quote_time is the last market event and can stay
	// unchanged even while fresh order-book snapshots continue to arrive.
	if domestic.FetchedAt.IsZero() || now.Sub(domestic.FetchedAt) > 90*time.Second || domestic.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "公共 Sina 五档采集超过 90 秒未更新")
	}
	return false
}

func appendFullCashSubstitutionFreshnessWarnings(snapshot *Snapshot, input Input, domestic domain.Quote, now time.Time, actionable bool) bool {
	localNow := now.In(shanghaiLocation)
	today := localNow.Format("2006-01-02")
	for _, rate := range input.FXRates {
		if rate.TradingDay != today {
			snapshot.Warnings = append(snapshot.Warnings, fmt.Sprintf("%s 使用回退交易日 %s，并非当前交易日 %s", rate.Pair, rate.TradingDay, today))
			actionable = false
		}
		freshness := 90 * time.Second
		message := rate.Pair + " 采集心跳超过 90 秒未更新"
		if rate.Source == CFETSSpotRateSource {
			freshness = sharedCFETSSpotFreshness
			message = rate.Pair + " 共享 CFETS 即期汇率超过 3 分钟未更新"
		}
		if rate.FetchedAt.IsZero() || now.Sub(rate.FetchedAt) > freshness || rate.FetchedAt.Sub(now) > 30*time.Second {
			snapshot.Warnings = append(snapshot.Warnings, message)
			actionable = false
		}
	}
	for _, quote := range input.MarketQuotes {
		checkedAt := quote.StreamCheckedAt
		if checkedAt.IsZero() {
			checkedAt = quote.ObservedAt
		}
		if checkedAt.IsZero() || now.Sub(checkedAt) > 15*time.Second || checkedAt.Sub(now) > 30*time.Second {
			snapshot.Warnings = append(snapshot.Warnings, quote.Market+":"+quote.Symbol+" Bid/Ask 心跳超过 15 秒未更新")
			actionable = false
		}
		if quote.MarketDataType != "Live" {
			snapshot.Warnings = append(snapshot.Warnings, quote.Market+":"+quote.Symbol+" 行情不是 Live（Delayed/Frozen 仅供展示）")
			actionable = false
		}
	}
	if input.GeneratedAt.IsZero() || now.Sub(input.GeneratedAt) > 15*time.Second || input.GeneratedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "Mac-home private 采集心跳超过 15 秒未更新")
		actionable = false
	}
	if domestic.FetchedAt.IsZero() || now.Sub(domestic.FetchedAt) > 15*time.Second || domestic.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "公共 Sina 五档超过 15 秒未更新")
		actionable = false
	}
	if quoteAt, ok := domesticQuoteTime(domestic); !ok || localNow.Sub(quoteAt) > 15*time.Second || quoteAt.Sub(localNow) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "Sina 行情时间戳不是 15 秒内的当前盘口")
		actionable = false
	}
	// 159605 is full cash substitution. Its current basket is informative, but
	// it cannot be represented as a locked stock-delivery redemption trade.
	snapshot.Warnings = append(snapshot.Warnings, "全现金替代 PCF：当前仅为盘中指示性篮子估值，最终赎回收入取决于基金实际卖出、实际现金差额、费用与后续到账")
	return false
}

func ibQuoteStreamCheckedAt(quote IBQuoteInput) time.Time {
	if !quote.StreamCheckedAt.IsZero() {
		return quote.StreamCheckedAt
	}
	return quote.ObservedAt
}

func quoteForSymbol(quotes map[string]domain.Quote, symbol string) (domain.Quote, bool) {
	if quote, ok := quotes[symbol]; ok {
		return quote, true
	}
	for key, quote := range quotes {
		if key == symbol || quote.Symbol == symbol {
			return quote, true
		}
	}
	return domain.Quote{}, false
}

func bestLevel(levels []domain.Level) (domain.Level, bool) {
	for _, level := range levels {
		if finitePositive(level.Price) {
			return level, true
		}
	}
	return domain.Level{}, false
}

func positiveLevels(levels []domain.Level) []domain.Level {
	out := make([]domain.Level, 0, len(levels))
	for _, level := range levels {
		if finitePositive(level.Price) {
			out = append(out, level)
		}
	}
	return out
}

func round(value float64, places int) float64 {
	factor := 1.0
	for i := 0; i < places; i++ {
		factor *= 10
	}
	return math.Round(value*factor) / factor
}

func domesticQuoteTime(quote domain.Quote) (time.Time, bool) {
	if quote.QuoteDate == "" || quote.QuoteTime == "" {
		return time.Time{}, false
	}
	parsed, err := time.ParseInLocation("2006-01-02 15:04:05", quote.QuoteDate+" "+quote.QuoteTime, shanghaiLocation)
	return parsed, err == nil
}
