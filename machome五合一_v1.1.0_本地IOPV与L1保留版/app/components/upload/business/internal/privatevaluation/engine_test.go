package privatevaluation

import (
	"fmt"
	"math"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestCalculateMatchesConfirmedSZ159518TotalBasketGolden(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validInput(now)
	quotes := map[string]domain.Quote{
		TargetSymbol: {
			Symbol:     TargetSymbol,
			Name:       TargetName,
			BidLevels:  []domain.Level{{Level: 1, Price: 1.068, Volume: 100_000}, {Level: 2, Price: 1.067, Volume: 90_000}},
			AskLevels:  []domain.Level{{Level: 1, Price: 1.069, Volume: 80_000}, {Level: 2, Price: 1.070, Volume: 70_000}},
			QuoteDate:  "2026-07-10",
			QuoteTime:  "10:00:00",
			FetchedAt:  now,
			IsRealtime: true,
		},
	}

	snapshot := Calculate(&input, quotes, now)
	if !snapshot.Ready || !snapshot.Actionable {
		t.Fatalf("snapshot ready/actionable = %v/%v, warnings=%v", snapshot.Ready, snapshot.Actionable, snapshot.Warnings)
	}
	valuation := snapshot.Valuation
	if valuation == nil {
		t.Fatal("valuation is nil")
	}
	assertClose(t, valuation.StockComponentBidCNY, 1_070_521.38, 0.01)
	assertClose(t, valuation.StockComponentAskCNY, 1_078_553.16, 0.01)
	assertClose(t, valuation.BasketBidCNY, 1_071_805.99, 0.01)
	assertClose(t, valuation.BasketAskCNY, 1_079_837.77, 0.01)
	assertClose(t, valuation.NAVBid, 1.0718059923, 1e-10)
	assertClose(t, valuation.NAVAsk, 1.0798377712, 1e-10)
	assertClose(t, valuation.BuyDirectionPremiumRate, -0.0026180040, 1e-10)
	if len(snapshot.OrderBook) != 4 {
		t.Fatalf("order book rows = %d, want 4", len(snapshot.OrderBook))
	}
	if snapshot.OrderBook[0].Side != "ask" || snapshot.OrderBook[0].Level != 2 {
		t.Fatalf("first order book row = %+v, want ask2", snapshot.OrderBook[0])
	}
}

func TestCalculateFailsClosedWhenPCFCashComponentIsMissing(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validInput(now)
	input.PCF.EstimateCashComponentCNY = nil
	snapshot := Calculate(&input, validQuotes(now), now)
	if snapshot.Ready || snapshot.Valuation != nil {
		t.Fatalf("missing PCF cash must stop valuation: %+v", snapshot)
	}
	if !strings.Contains(strings.Join(snapshot.Warnings, " "), "estimate_cash_component") {
		t.Fatalf("warnings = %v", snapshot.Warnings)
	}
}

func TestCalculateDisplaysButDoesNotMarkDelayedOrClosedInputActionable(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validInput(now)
	input.IB.MarketDataType = "Delayed"
	input.PCF.Redemption = "N"
	snapshot := Calculate(&input, validQuotes(now), now)
	if !snapshot.Ready {
		t.Fatalf("complete degraded input should still display valuation: %v", snapshot.Warnings)
	}
	if snapshot.Actionable {
		t.Fatal("delayed IB and Redemption=N must not be actionable")
	}
	warnings := strings.Join(snapshot.Warnings, " ")
	if !strings.Contains(warnings, "Redemption") || !strings.Contains(warnings, "不是 Live") {
		t.Fatalf("warnings = %v", snapshot.Warnings)
	}
}

func TestCalculateUsesExplicitIBStreamHeartbeatWithoutRewritingEventTime(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validInput(now)
	input.IB.ObservedAt = now.Add(-5 * time.Minute)
	input.IB.StreamCheckedAt = now
	snapshot := Calculate(&input, validQuotes(now), now)
	if !snapshot.Actionable {
		t.Fatalf("connected stream with unchanged event should remain actionable: %v", snapshot.Warnings)
	}
	if !input.IB.ObservedAt.Equal(now.Add(-5 * time.Minute)) {
		t.Fatalf("market event audit time was rewritten: %s", input.IB.ObservedAt)
	}

	input.IB.StreamCheckedAt = now.Add(-16 * time.Second)
	snapshot = Calculate(&input, validQuotes(now), now)
	if snapshot.Actionable || !strings.Contains(strings.Join(snapshot.Warnings, " "), "心跳超过 15 秒") {
		t.Fatalf("stale stream heartbeat should fail closed: actionable=%v warnings=%v", snapshot.Actionable, snapshot.Warnings)
	}
}

func TestCalculateSZ164824UsesNativeT2MultiMarketINDAInput(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validIndiaT2Input(now)
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SZ164824Symbol
	domestic.Name = SZ164824Name
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 1.018, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 1.019, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SZ164824Symbol] = domestic

	snapshot := CalculateForSymbol(SZ164824Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("164824 snapshot should calculate: %+v", snapshot)
	}
	if snapshot.Actionable {
		t.Fatal("164824 must stay non-actionable until realised redemption settlement is calibrated")
	}
	wantBid := 1.0 * (IndiaStaticRatio + IndiaInvestmentRatio*(101.0/100.0)*(7.07/7.0))
	wantAsk := 1.0 * (IndiaStaticRatio + IndiaInvestmentRatio*(102.0/100.0)*(7.07/7.0))
	assertClose(t, snapshot.Valuation.NAVBid, wantBid, 1e-10)
	assertClose(t, snapshot.Valuation.NAVAsk, wantAsk, 1e-10)
	if snapshot.Input == nil || snapshot.Input.India == nil || snapshot.Input.ValuationAnchorDate() != "2026-07-08" {
		t.Fatalf("164824 input should preserve the T-2 NAV anchor: %+v", snapshot.Input)
	}
	if !strings.Contains(snapshot.Valuation.Formula, "四市场加权") || !strings.Contains(strings.Join(snapshot.Warnings, " "), "实际赎回清算单") {
		t.Fatalf("164824 formula/warnings = %q / %v", snapshot.Valuation.Formula, snapshot.Warnings)
	}
}

func TestInputNormalizedRetainsBaseSAFEAndCurrentCFETSSpotTimes(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 0, 0, shanghaiLocation)
	input := validIndiaT2Input(now).Normalized(now)
	if input.India == nil {
		t.Fatal("normalized India input missing")
	}
	if got := input.India.BaseFX.QuoteTime; got != "09:15" {
		t.Fatalf("base SAFE parity quote_time=%q, want 09:15", got)
	}
	if got := input.India.CurrentFX.QuoteTime; got != "10:00" {
		t.Fatalf("current CFETS spot quote_time=%q, want 10:00", got)
	}
}

func TestCalculateSZ164824ExposesSeparateNiftyBridgeValuation(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validIndiaT2Input(now)
	newYork, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	bridgeAt := time.Date(2026, 7, 9, 15, 50, 0, 0, newYork)
	niftyBid, niftyAsk := 20_200.0, 20_220.0
	indaReferenceBid, indaReferenceAsk := 100.0, 101.0
	niftyReferenceBid, niftyReferenceAsk := 20_000.0, 20_020.0
	input.India.NiftyBridge = &IndiaNiftyBridgeInput{
		Nifty:                    IBQuoteInput{Symbol: "NIFTY", Contract: "NIFTYQ26", Bid: &niftyBid, Ask: &niftyAsk, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now},
		INDAReference:            IBQuoteInput{Symbol: IndiaReferenceSymbol, Bid: &indaReferenceBid, Ask: &indaReferenceAsk, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: bridgeAt},
		NiftyReference:           IBQuoteInput{Symbol: "NIFTY", Contract: "NIFTYQ26", Bid: &niftyReferenceBid, Ask: &niftyReferenceAsk, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: bridgeAt},
		ReferenceAt:              bridgeAt,
		Beta:                     1,
		ContractSelectionVersion: NiftyContractSelectionVersion,
	}
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol, domestic.Name = SZ164824Symbol, SZ164824Name
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 1.018, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 1.019, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SZ164824Symbol] = domestic

	snapshot := CalculateForSymbol(SZ164824Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil || snapshot.IndiaValuations == nil || snapshot.IndiaValuations.NiftyBridge == nil {
		t.Fatalf("164824 bridge snapshot should expose both variants: %+v", snapshot)
	}
	if snapshot.IndiaValuations.DefaultKey != "nifty_bridge" {
		t.Fatalf("default variant = %q, want nifty_bridge", snapshot.IndiaValuations.DefaultKey)
	}
	// The legacy top-level valuation remains direct INDA for final-NAV and
	// minute-history compatibility; its value must not be overwritten by the
	// China-session hedge proxy.
	wantDirect := IndiaStaticRatio + IndiaInvestmentRatio*(101.0/100.0)*(7.07/7.0)
	assertClose(t, snapshot.Valuation.NAVBid, wantDirect, 1e-10)
	bridgeBid := indaReferenceBid * (niftyBid / niftyReferenceAsk)
	bridgeAsk := indaReferenceAsk * (niftyAsk / niftyReferenceBid)
	wantBridgeBid := IndiaStaticRatio + IndiaInvestmentRatio*(bridgeBid/100.0)*(7.07/7.0)
	wantBridgeAsk := IndiaStaticRatio + IndiaInvestmentRatio*(bridgeAsk/100.0)*(7.07/7.0)
	assertClose(t, snapshot.IndiaValuations.NiftyBridge.Valuation.NAVBid, wantBridgeBid, 1e-10)
	assertClose(t, snapshot.IndiaValuations.NiftyBridge.Valuation.NAVAsk, wantBridgeAsk, 1e-10)
	if !strings.Contains(snapshot.IndiaValuations.NiftyBridge.Valuation.Formula, "合成 INDA") {
		t.Fatalf("bridge formula = %q", snapshot.IndiaValuations.NiftyBridge.Valuation.Formula)
	}
	point, ok := MinuteHistoryPointFromSnapshot(snapshot)
	if !ok || point.NiftyBridgeBidNAV == nil || point.NiftyBridgeAskNAV == nil {
		t.Fatalf("minute point should preserve separately auditable NIFTY bridge NAV: %+v", point)
	}
	assertClose(t, *point.NiftyBridgeBidNAV, wantBridgeBid, 1e-10)
	assertClose(t, *point.NiftyBridgeAskNAV, wantBridgeAsk, 1e-10)
	assertClose(t, point.BasketBidNAV, wantDirect, 1e-10)
}

func TestCalculateSH513350UsesOwnPCFCalibration(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validInput(now)
	shares := 1_010.5
	cash := -681.67
	input.Symbol = SH513350Symbol
	input.ModelVersion = SH513350ModelVersion
	input.PCF.SecurityID = "513350"
	input.PCF.ComponentCount = 23
	input.PCF.EstimateCashComponentCNY = &cash
	input.PCF.XOPEquivalentShares = &shares
	quotes := validQuotes(now)
	quote := quotes[TargetSymbol]
	quote.Symbol = SH513350Symbol
	quote.BidLevels = []domain.Level{{Level: 1, Price: 1.155, Volume: 100_000}}
	quote.AskLevels = []domain.Level{{Level: 1, Price: 1.156, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SH513350Symbol] = quote

	snapshot := CalculateForSymbol(SH513350Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("SH513350 snapshot not ready: %+v", snapshot)
	}
	if snapshot.Symbol != SH513350Symbol || snapshot.Name != SH513350Name || snapshot.ModelVersion != SH513350ModelVersion {
		t.Fatalf("unexpected SH513350 identity: %+v", snapshot)
	}
	if snapshot.Valuation.XOPEquivalentShares != SH513350XOPEquivalentShares {
		t.Fatalf("SH513350 xop shares = %.4f, want fixed %.4f", snapshot.Valuation.XOPEquivalentShares, SH513350XOPEquivalentShares)
	}
	wantBid := SH513350XOPEquivalentShares*158.61*6.7765 + cash
	assertClose(t, snapshot.Valuation.BasketBidCNY, wantBid, 0.01)
	if !strings.Contains(snapshot.Valuation.Formula, "固定 1046") {
		t.Fatalf("SH513350 formula = %q", snapshot.Valuation.Formula)
	}
}

func TestCalculateSH513350FailsClosedWithoutItsOwnPCFCalibration(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validInput(now)
	input.Symbol = SH513350Symbol
	input.ModelVersion = SH513350ModelVersion
	input.PCF.SecurityID = "513350"
	input.PCF.ComponentCount = 23
	input.PCF.XOPEquivalentShares = nil
	quotes := validQuotes(now)
	quote := quotes[TargetSymbol]
	quote.Symbol = SH513350Symbol
	delete(quotes, TargetSymbol)
	quotes[SH513350Symbol] = quote

	snapshot := CalculateForSymbol(SH513350Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil || snapshot.Valuation.XOPEquivalentShares != SH513350XOPEquivalentShares {
		t.Fatalf("missing SH513350 payload calibration must use fixed shares: %+v", snapshot)
	}
}

func TestCalculateNQProxyUsesContractMultiplierButRemainsPreScanOnly(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validNQInput(now)
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SZ159659Symbol
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 2.000, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 2.001, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SZ159659Symbol] = domestic

	snapshot := CalculateForSymbol(SZ159659Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("NQ proxy snapshot not ready: %+v", snapshot)
	}
	if snapshot.Actionable {
		t.Fatal("Nasdaq pre-scan must not be labelled actionable before realised-redemption calibration")
	}
	wantStock := 0.65 * 20 * 22_000 * 7
	assertClose(t, snapshot.Valuation.StockComponentBidCNY, wantStock, 0.001)
	assertClose(t, snapshot.Valuation.BasketBidCNY, wantStock-2_636.32, 0.001)
	if !strings.Contains(snapshot.Valuation.Formula, "20 USD/点") || !strings.Contains(strings.Join(snapshot.Warnings, " "), "禁止据此执行") {
		t.Fatalf("NQ formula/warnings = %q / %v", snapshot.Valuation.Formula, snapshot.Warnings)
	}
}

func TestCalculateESProxyUsesFundSpecificCoefficientButRemainsPreScanOnly(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validNQInput(now)
	unit, cash, nav := 1_000_000.0, 30_426.57, 2_409_292.79
	fx, bid, ask, contracts := 7.0, 6_200.0, 6_201.0, 1.1
	components := make([]PCFComponentInput, 0, 446)
	for index := 0; index < 446; index++ {
		symbol := fmt.Sprintf("SP%03d", index)
		components = append(components, PCFComponentInput{Symbol: symbol, Name: symbol, Market: "US", Currency: "USD", Quantity: 1})
	}
	input.Symbol = SH513500Symbol
	input.ModelVersion = ESProxyModelVersion
	input.PCF = PCFInput{
		SecurityID: "513500", TradingDay: "2026-07-10", PreTradingDay: "2026-07-08", Creation: "Y", Redemption: "Y",
		CreationRedemptionUnit: &unit, EstimateCashComponentCNY: &cash, NAVPerCU: &nav,
		ComponentCount: len(components), Components: components, XOPEquivalentShares: &contracts,
		SourceURL: "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=513500", SHA256: strings.Repeat("e", 64),
	}
	input.FX = FXInput{Pair: "USD/CNY", Rate: &fx, TradingDay: "2026-07-10", QuoteTime: "10:00", Source: CFETSReferenceRateSource, FetchedAt: now}
	input.IB = IBQuoteInput{Symbol: ESReferenceSymbol, Bid: &bid, Ask: &ask, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now}
	input.Source = "mac-home-private-sp500-pcf-es-uploader"
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SH513500Symbol
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 2.40, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 2.41, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SH513500Symbol] = domestic

	snapshot := CalculateForSymbol(SH513500Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("ES proxy snapshot not ready: %+v", snapshot)
	}
	if snapshot.Actionable {
		t.Fatal("S&P pre-scan must not be labelled actionable before fund-specific settlement calibration")
	}
	wantStock := 1.1 * 50 * 6_200 * 7
	assertClose(t, snapshot.Valuation.StockComponentBidCNY, wantStock, 0.001)
	assertClose(t, snapshot.Valuation.BasketBidCNY, wantStock+cash, 0.001)
	if !strings.Contains(snapshot.Valuation.Formula, "50 USD/点") || !strings.Contains(strings.Join(snapshot.Warnings, " "), "标普 PCF/ES") {
		t.Fatalf("ES formula/warnings = %q / %v", snapshot.Valuation.Formula, snapshot.Warnings)
	}
}

func TestCalculateN225MProxyUsesJPYContractMultiplierButRemainsPreScanOnly(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := validN225MInput(now)
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SH513520Symbol
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 2.22, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 2.23, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SH513520Symbol] = domestic

	snapshot := CalculateForSymbol(SH513520Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("N225M proxy snapshot not ready: %+v", snapshot)
	}
	if snapshot.Actionable {
		t.Fatal("Nikkei pre-scan must not be labelled actionable before settlement calibration")
	}
	wantStock := 4.0 * 100 * 66_800 * 0.041924
	assertClose(t, snapshot.Valuation.StockComponentBidCNY, wantStock, 0.001)
	assertClose(t, snapshot.Valuation.BasketBidCNY, wantStock-936.18, 0.001)
	if !strings.Contains(snapshot.Valuation.Formula, "100 JPY/点") || !strings.Contains(strings.Join(snapshot.Warnings, " "), "日经 PCF/N225M") {
		t.Fatalf("N225M formula/warnings = %q / %v", snapshot.Valuation.Formula, snapshot.Warnings)
	}

	input.FX.Pair = "USD/CNY"
	if err := input.Validate(); err == nil {
		t.Fatal("Nikkei input must reject a USD/CNY feed")
	}
}

func TestCalculateDAXProxyUsesMiniDAXMultiplierAndEURCNY(t *testing.T) {
	now := time.Date(2026, 7, 31, 10, 0, 5, 0, shanghaiLocation)
	input := validDAXInput(now)
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SH513030Symbol
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 1.800, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 1.801, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SH513030Symbol] = domestic

	snapshot := CalculateForSymbol(SH513030Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("DAX proxy snapshot not ready: %+v", snapshot)
	}
	if snapshot.Actionable {
		t.Fatal("DAX pre-scan must not be labelled actionable before settlement calibration")
	}
	wantStock := 0.902 * 5 * 25_805 * 7.768
	assertClose(t, snapshot.Valuation.StockComponentBidCNY, wantStock, 0.001)
	assertClose(t, snapshot.Valuation.BasketBidCNY, wantStock+1_969.57, 0.001)
	if !strings.Contains(snapshot.Valuation.Formula, "5 EUR/点") || !strings.Contains(strings.Join(snapshot.Warnings, " "), "德国 DAX PCF/FDXM") {
		t.Fatalf("DAX formula/warnings = %q / %v", snapshot.Valuation.Formula, snapshot.Warnings)
	}

	input.FX.Pair = "USD/CNY"
	if err := input.Validate(); err == nil {
		t.Fatal("DAX input must reject a USD/CNY feed")
	}
}

func TestSH513350AcceptsOnlyTheDatedCFETS1630SpotClose(t *testing.T) {
	now := time.Date(2026, 6, 24, 16, 30, 0, 0, shanghaiLocation)
	input := validInput(now)
	input.Symbol = SH513350Symbol
	input.ModelVersion = SH513350ModelVersion
	input.PCF.SecurityID = "513350"
	input.PCF.ComponentCount = 23
	input.FX.QuoteTime = "16:30"
	input.FX.Source = CFETSUSDCNYSpotCloseSource
	if err := input.Validate(); err != nil {
		t.Fatalf("SH513350 16:30 spot close should validate: %v", err)
	}

	input.FX.QuoteTime = "18:00"
	if err := input.Validate(); err == nil {
		t.Fatal("SH513350 must reject a spot-close source with an hourly timestamp")
	}

	input.FX.QuoteTime = "16:30"
	input.FX.Source = CFETSReferenceRateSource
	if err := input.Validate(); err == nil {
		t.Fatal("ordinary CFETS reference rates must reject a non-hourly timestamp")
	}

	input.FX.Source = CFETSUSDCNYSpotCloseSource
	input.Symbol = TargetSymbol
	input.ModelVersion = ModelVersion
	input.PCF.SecurityID = "159518"
	input.PCF.ComponentCount = 51
	if err := input.Validate(); err == nil {
		t.Fatal("SZ159518 must not accept SH513350's historical spot-close source")
	}
}

func TestHistoricalIndexProxyAcceptsDatedCFETS1630SpotCloseOnly(t *testing.T) {
	now := time.Date(2026, 6, 24, 16, 30, 0, 0, shanghaiLocation)
	for _, target := range []struct {
		symbol, model, securityID, reference string
	}{
		{SZ159659Symbol, NQProxyModelVersion, "159659", NQReferenceSymbol},
		{SH513500Symbol, ESProxyModelVersion, "513500", ESReferenceSymbol},
	} {
		input := validNQInput(now)
		input.Symbol = target.symbol
		input.ModelVersion = target.model
		input.PCF.SecurityID = target.securityID
		input.FX.QuoteTime = "16:30"
		input.FX.Source = CFETSUSDCNYSpotCloseSource
		input.IB.Symbol = target.reference
		input.IB.MarketDataType = "HistoricalBidAsk"
		if err := input.Validate(); err != nil {
			t.Fatalf("%s historical spot close should validate: %v", target.symbol, err)
		}
		input.IB.MarketDataType = "Live"
		if err := input.Validate(); err == nil {
			t.Fatalf("%s live index feed must reject the 16:30 spot-close source", target.symbol)
		}
	}
}

func TestNQProxyAllowsAuditedDatedComponentCountChange(t *testing.T) {
	now := time.Date(2026, 6, 24, 16, 30, 0, 0, shanghaiLocation)
	input := validNQInput(now)
	input.PCF.Components = input.PCF.Components[:101]
	input.PCF.ComponentCount = len(input.PCF.Components)
	for _, marketDataType := range []string{"Live", "HistoricalBidAsk"} {
		input.IB.MarketDataType = marketDataType
		if err := input.Validate(); err != nil {
			t.Fatalf("NQ PCF with its complete dated count should validate for %s input: %v", marketDataType, err)
		}
	}
}

func TestHistoricalFixedProxyRequiresHistoricalIndexInput(t *testing.T) {
	now := time.Date(2026, 6, 24, 16, 30, 0, 0, shanghaiLocation)
	input := validNQInput(now)
	input.PCF.HistoricalFixedProxy = true
	input.IB.MarketDataType = "HistoricalBidAsk"
	if err := input.Validate(); err != nil {
		t.Fatalf("historical NQ fixed proxy should validate: %v", err)
	}
	input.IB.MarketDataType = "Live"
	if err := input.Validate(); err == nil {
		t.Fatal("live index input must reject historical fixed proxy")
	}
	nikkei := validN225MInput(now)
	nikkei.PCF.HistoricalFixedProxy = true
	nikkei.IB.MarketDataType = "HistoricalBidAsk"
	if err := nikkei.Validate(); err != nil {
		t.Fatalf("historical N225M fixed proxy should validate: %v", err)
	}
	nikkei.IB.MarketDataType = "Live"
	if err := nikkei.Validate(); err == nil {
		t.Fatal("live N225M input must reject historical fixed proxy")
	}
}

func TestMinuteHistoryPointPreservesDatedXOPEquivalentCalibration(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 0, 0, shanghaiLocation)
	input := validInput(now)
	input.Symbol = SH513350Symbol
	input.ModelVersion = SH513350ModelVersion
	input.PCF.SecurityID = "513350"
	input.PCF.ComponentCount = 51
	shares := 1046.8317
	input.PCF.XOPEquivalentShares = &shares
	input.PCF.TradingDay = "2026-07-10"
	input.FX.TradingDay = "2026-07-10"
	input.GeneratedAt = now
	input.IB.ObservedAt = input.GeneratedAt
	input.FX.FetchedAt = input.GeneratedAt
	quotes := validQuotes(input.GeneratedAt)
	quote := quotes[TargetSymbol]
	quote.Symbol = SH513350Symbol
	delete(quotes, TargetSymbol)
	quotes[SH513350Symbol] = quote
	snapshot := CalculateForSymbol(SH513350Symbol, &input, quotes, input.GeneratedAt)
	if !snapshot.Ready {
		t.Fatalf("SH513350 snapshot should be ready: %+v", snapshot)
	}
	point, ok := MinuteHistoryPointFromSnapshot(snapshot)
	if !ok {
		t.Fatal("expected private minute-history point")
	}
	if point.PCFTradingDay != input.PCF.TradingDay || point.XOPEquivalentShares == nil || *point.XOPEquivalentShares != SH513350XOPEquivalentShares {
		t.Fatalf("history calibration = %+v, want PCF day %s and fixed shares %.4f", point, input.PCF.TradingDay, SH513350XOPEquivalentShares)
	}
}

func TestCalculateSZ159605UsesFullCashSubstitutionPCFBasket(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := valid159605Input(now)
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SZ159605Symbol
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 0.812, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 0.813, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SZ159605Symbol] = domestic

	snapshot := CalculateForSymbol(SZ159605Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("SZ159605 snapshot not ready: %+v", snapshot)
	}
	if snapshot.Actionable {
		t.Fatal("full-cash-substitution valuation must not be labelled a locked actionable trade")
	}
	if snapshot.ModelVersion != SZ159605ModelVersion || len(snapshot.Components) != 30 {
		t.Fatalf("SZ159605 model/components = %q/%d", snapshot.ModelVersion, len(snapshot.Components))
	}
	wantStockBid := 23*10.0*0.87 + 7*100.0*7.0
	assertClose(t, snapshot.Valuation.StockComponentBidCNY, wantStockBid, 0.001)
	if !strings.Contains(snapshot.Valuation.Formula, "30只") || !strings.Contains(strings.Join(snapshot.Warnings, " "), "全现金替代") {
		t.Fatalf("SZ159605 formula/warnings = %q / %v", snapshot.Valuation.Formula, snapshot.Warnings)
	}
}

func TestCalculateSZ159605RequiresPCFCreationFlag(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := valid159605Input(now)
	input.PCF.Creation = ""
	snapshot := CalculateForSymbol(SZ159605Symbol, &input, validQuotes(now), now)
	if snapshot.Ready || !strings.Contains(strings.Join(snapshot.Warnings, " "), "pcf.creation") {
		t.Fatalf("SZ159605 must fail closed without PCF Creation: %+v", snapshot)
	}
}

func TestCalculateSH513220IncludesCNPCFComponentsWithoutSyntheticFX(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := valid159605Input(now)
	input.Symbol = SH513220Symbol
	input.ModelVersion = SH513220ModelVersion
	input.PCF.SecurityID = "513220"
	for index := 0; index < 9; index++ {
		input.PCF.Components[index].Market = "CN"
		input.PCF.Components[index].Currency = "CNY"
		input.PCF.Components[index].Symbol = fmt.Sprintf("CN%06d", index+1)
		input.PCF.Components[index].Name = input.PCF.Components[index].Symbol
		input.MarketQuotes[index].Market = "CN"
		input.MarketQuotes[index].Currency = "CNY"
		input.MarketQuotes[index].Symbol = input.PCF.Components[index].Symbol
		input.MarketQuotes[index].Source = "SINA_A_STOCK"
		bid, ask := 20.0, 21.0
		input.MarketQuotes[index].Bid, input.MarketQuotes[index].Ask = &bid, &ask
	}
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SH513220Symbol
	delete(quotes, TargetSymbol)
	quotes[SH513220Symbol] = domestic

	snapshot := CalculateForSymbol(SH513220Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil {
		t.Fatalf("SH513220 snapshot not ready: %+v", snapshot)
	}
	wantStockBid := 9*20.0 + 14*10.0*0.87 + 7*100.0*7.0
	assertClose(t, snapshot.Valuation.StockComponentBidCNY, wantStockBid, 0.001)
	if !strings.Contains(snapshot.Valuation.Formula, "CNY 直算") || len(snapshot.Components) != 30 {
		t.Fatalf("SH513220 formula/components = %q/%d", snapshot.Valuation.Formula, len(snapshot.Components))
	}
}

func TestDatedFullPCFAllowsAuditedConstituentCountChange(t *testing.T) {
	// A dated manager PCF can legitimately differ from today's constituent
	// count. Historical import must replay that basket exactly rather than
	// reject it or silently pad it with a later PCF.
	now := time.Date(2026, 7, 10, 10, 0, 5, 0, shanghaiLocation)
	input := valid159605Input(now)
	input.Symbol = SH513050Symbol
	input.ModelVersion = SH513050ModelVersion
	input.PCF.SecurityID = "513050"
	input.PCF.Components = input.PCF.Components[:29]
	input.PCF.ComponentCount = len(input.PCF.Components)
	input.MarketQuotes = input.MarketQuotes[:29]
	if err := input.Validate(); err != nil {
		t.Fatalf("dated full PCF with a different constituent count must remain importable: %v", err)
	}
}

func validInput(now time.Time) Input {
	unit := 1_000_000.0
	cash := 1_284.61
	nav := 1_099_440.21
	fx := 6.7765
	bid := 158.61
	ask := 159.80
	last := 159.40
	return Input{
		SchemaVersion: InputSchemaVersion,
		Symbol:        TargetSymbol,
		ModelVersion:  ModelVersion,
		PCF: PCFInput{
			SecurityID:               "159518",
			TradingDay:               "2026-07-10",
			PreTradingDay:            "2026-07-08",
			Redemption:               "Y",
			CreationRedemptionUnit:   &unit,
			EstimateCashComponentCNY: &cash,
			NAVPerCU:                 &nav,
			ComponentCount:           51,
			SourceURL:                "https://reportdocs.static.szse.cn/pcf.xml",
			SHA256:                   strings.Repeat("a", 64),
		},
		FX: FXInput{
			Pair:       "USD/CNY",
			Rate:       &fx,
			TradingDay: "2026-07-10",
			QuoteTime:  "10:00",
			Source:     "CFETS_REFERENCE_RATE",
			FetchedAt:  now,
		},
		IB: IBQuoteInput{
			Symbol:         ReferenceSymbol,
			Bid:            &bid,
			Ask:            &ask,
			Last:           &last,
			MarketDataType: "Live",
			Source:         "IBKR_TWS",
			ObservedAt:     now,
		},
		Source:      "mac-home-private-uploader",
		GeneratedAt: now,
		ReceivedAt:  now,
	}
}

func validIndiaT2Input(now time.Time) Input {
	baseFX, currentFX, bid, ask := 7.0, 7.07, 101.0, 102.0
	baseDay := "2026-07-08"
	anchors := make([]IndiaAnchorInput, 0, len(indiaAnchorRules))
	for _, rule := range indiaAnchorRules {
		target, err := indiaAnchorTarget(baseDay, rule)
		if err != nil {
			panic(err)
		}
		anchors = append(anchors, IndiaAnchorInput{
			Key: rule.Key, Label: rule.Label, Weight: rule.Weight, Price: 100,
			TargetAt: target, ObservedAt: target.Add(30 * time.Second),
			Source: "IBKR_TWS_INDA_BID_ASK_1M", CaptureStatus: "exact_1m",
		})
	}
	return Input{
		SchemaVersion: InputSchemaVersion, Symbol: SZ164824Symbol, ModelVersion: SZ164824ModelVersion,
		India: &IndiaT2Input{
			BaseNAV: 1, BaseNAVDate: baseDay,
			BaseFX:          FXInput{Pair: "USD/CNY", Rate: &baseFX, TradingDay: baseDay, QuoteTime: "09:15", Source: "SAFE_CENTRAL_PARITY", FetchedAt: now},
			CurrentFX:       FXInput{Pair: "USD/CNY", Rate: &currentFX, TradingDay: "2026-07-10", QuoteTime: "10:00", Source: CFETSSpotRateSource, FetchedAt: now},
			InvestmentRatio: IndiaInvestmentRatio, StaticRatio: IndiaStaticRatio, Anchors: anchors,
			PortfolioAsOf: "2026-06-30", PortfolioSource: "2026Q2 report",
		},
		IB:     IBQuoteInput{Symbol: IndiaReferenceSymbol, Bid: &bid, Ask: &ask, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now},
		Source: "test-private-164824", GeneratedAt: now,
	}
}

func valid159605Input(now time.Time) Input {
	unit := 1_000_000.0
	cash := -114.02
	usdCNY := 7.0
	hkdCNY := 0.87
	components := make([]PCFComponentInput, 0, 30)
	marketQuotes := make([]MarketQuoteInput, 0, 30)
	for index := 0; index < 30; index++ {
		market, currency, bid, ask := "HK", "HKD", 10.0, 10.1
		if index >= 23 {
			market, currency, bid, ask = "US", "USD", 100.0, 101.0
		}
		symbol := fmt.Sprintf("%s%02d", market, index+1)
		components = append(components, PCFComponentInput{Symbol: symbol, Name: symbol, Market: market, Currency: currency, Quantity: 1})
		marketQuotes = append(marketQuotes, MarketQuoteInput{
			Symbol: symbol, Market: market, Currency: currency, Bid: &bid, Ask: &ask,
			MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now,
		})
	}
	return Input{
		SchemaVersion: InputSchemaVersion,
		Symbol:        SZ159605Symbol,
		ModelVersion:  SZ159605ModelVersion,
		PCF: PCFInput{
			SecurityID: "159605", TradingDay: "2026-07-10", Creation: "Y", Redemption: "Y",
			CreationRedemptionUnit: &unit, EstimateCashComponentCNY: &cash,
			ComponentCount: 30, Components: components,
			SourceURL: "https://private.example/pcf/159605.xml", SHA256: strings.Repeat("b", 64),
		},
		FXRates: []FXInput{
			{Pair: "USD/CNY", Rate: &usdCNY, TradingDay: "2026-07-10", QuoteTime: "10:01", Source: CFETSSpotRateSource, FetchedAt: now},
			{Pair: "HKD/CNY", Rate: &hkdCNY, TradingDay: "2026-07-10", QuoteTime: "10:01", Source: CFETSSpotRateSource, FetchedAt: now},
		},
		MarketQuotes: marketQuotes,
		Source:       "mac-home-private-multimarket-uploader", GeneratedAt: now, ReceivedAt: now,
	}
}

func validNQInput(now time.Time) Input {
	unit, cash, nav := 1_000_000.0, -2_636.32, 2_144_465.28
	fx, bid, ask, contracts := 7.0, 22_000.0, 22_001.0, 0.65
	components := make([]PCFComponentInput, 0, 103)
	for index := 0; index < 103; index++ {
		symbol := fmt.Sprintf("NQ%03d", index)
		components = append(components, PCFComponentInput{Symbol: symbol, Name: symbol, Market: "US", Currency: "USD", Quantity: 1})
	}
	return Input{
		SchemaVersion: InputSchemaVersion, Symbol: SZ159659Symbol, ModelVersion: NQProxyModelVersion,
		PCF: PCFInput{
			SecurityID: "159659", TradingDay: "2026-07-10", PreTradingDay: "2026-07-08", Creation: "Y", Redemption: "Y",
			CreationRedemptionUnit: &unit, EstimateCashComponentCNY: &cash, NAVPerCU: &nav,
			ComponentCount: len(components), Components: components, XOPEquivalentShares: &contracts,
			SourceURL: "https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_159659_20260710.xml", SHA256: strings.Repeat("c", 64),
		},
		FX:     FXInput{Pair: "USD/CNY", Rate: &fx, TradingDay: "2026-07-10", QuoteTime: "10:01", Source: CFETSSpotRateSource, FetchedAt: now},
		IB:     IBQuoteInput{Symbol: NQReferenceSymbol, Bid: &bid, Ask: &ask, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now},
		Source: "mac-home-private-nasdaq-uploader", GeneratedAt: now, ReceivedAt: now,
	}
}

func validN225MInput(now time.Time) Input {
	unit, cash, nav := 500_000.0, -936.18, 1_111_109.88
	fx, bid, ask, contracts := 0.041924, 66_800.0, 66_805.0, 4.0
	return Input{
		SchemaVersion: InputSchemaVersion, Symbol: SH513520Symbol, ModelVersion: N225MProxyModelVersion,
		PCF: PCFInput{
			SecurityID: "513520", TradingDay: "2026-07-10", PreTradingDay: "2026-07-09", Creation: "Y", Redemption: "Y",
			CreationRedemptionUnit: &unit, EstimateCashComponentCNY: &cash, NAVPerCU: &nav,
			ComponentCount: 1, Components: []PCFComponentInput{{Symbol: "1321", Name: "日经225", Market: "JP", Currency: "JPY", Quantity: 378}}, XOPEquivalentShares: &contracts,
			SourceURL: "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=513520", SHA256: strings.Repeat("f", 64),
		},
		FX:     FXInput{Pair: "JPY/CNY", Rate: &fx, TradingDay: "2026-07-10", QuoteTime: "10:01", Source: CFETSSpotRateSource, FetchedAt: now},
		IB:     IBQuoteInput{Symbol: N225MReferenceSymbol, Bid: &bid, Ask: &ask, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now},
		Source: "mac-home-private-nikkei225-pcf-n225m-uploader", GeneratedAt: now, ReceivedAt: now,
	}
}

func validDAXInput(now time.Time) Input {
	unit, cash, nav := 500_000.0, 1_969.57, 897_636.98
	fx, bid, ask, contracts := 7.768, 25_805.0, 25_807.0, 0.902
	components := make([]PCFComponentInput, 0, 40)
	for index := 0; index < 40; index++ {
		symbol := fmt.Sprintf("DE%02d", index)
		components = append(components, PCFComponentInput{Symbol: symbol, Name: symbol, Market: "DE", Currency: "EUR", Quantity: 1})
	}
	return Input{
		SchemaVersion: InputSchemaVersion, Symbol: SH513030Symbol, ModelVersion: DAXProxyModelVersion,
		PCF: PCFInput{
			SecurityID: "513030", TradingDay: "2026-07-31", PreTradingDay: "2026-07-29", Creation: "Y", Redemption: "Y",
			CreationRedemptionUnit: &unit, EstimateCashComponentCNY: &cash, NAVPerCU: &nav,
			ComponentCount: len(components), Components: components, XOPEquivalentShares: &contracts,
			SourceURL: "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=513030", SHA256: strings.Repeat("d", 64),
		},
		FX:     FXInput{Pair: "EUR/CNY", Rate: &fx, TradingDay: "2026-07-31", QuoteTime: "10:01", Source: CFETSSpotRateSource, FetchedAt: now},
		IB:     IBQuoteInput{Symbol: DAXReferenceSymbol, Bid: &bid, Ask: &ask, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now},
		Source: "mac-home-private-germany-pcf-fdxm-xetra1735-uploader", GeneratedAt: now, ReceivedAt: now,
	}
}

func validQuotes(now time.Time) map[string]domain.Quote {
	return map[string]domain.Quote{
		TargetSymbol: {
			Symbol:     TargetSymbol,
			BidLevels:  []domain.Level{{Level: 1, Price: 1.068, Volume: 100_000}},
			AskLevels:  []domain.Level{{Level: 1, Price: 1.069, Volume: 80_000}},
			QuoteDate:  now.In(shanghaiLocation).Format("2006-01-02"),
			QuoteTime:  now.In(shanghaiLocation).Format("15:04:05"),
			FetchedAt:  now,
			IsRealtime: true,
		},
	}
}

func assertClose(t *testing.T, got float64, want float64, tolerance float64) {
	t.Helper()
	if math.Abs(got-want) > tolerance {
		t.Fatalf("got %.12f, want %.12f (tolerance %.12f)", got, want, tolerance)
	}
}
