package privatevaluation

import (
	"math"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestSZ162411DefinitionUsesIndependentLOFWeightedAnchorModel(t *testing.T) {
	definition, ok := Definition(SZ162411Symbol)
	if !ok {
		t.Fatal("SZ162411 private definition is missing")
	}
	if definition.CalculationMode != CalculationModeLOFWeightedAnchor || definition.ModelVersion != SZ162411ModelVersion {
		t.Fatalf("definition = %+v", definition)
	}
	if definition.ReferenceSymbol != ReferenceSymbol || definition.FXPair != "USD/CNY" {
		t.Fatalf("reference contract = %+v", definition)
	}
	if definition.ExpectedRedemptionUnit != 0 || definition.XOPEquivalentShares != 0 {
		t.Fatalf("LOF must not inherit an ETF redemption basket: %+v", definition)
	}
}

func TestCalculateSZ162411MatchesPublicWeightedAnchorGolden(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	last := 174.58
	input.IB.Bid, input.IB.Ask, input.IB.Last = &last, &last, &last
	// An unchanged overnight quote retains its market-event timestamp. The
	// current GeneratedAt heartbeat proves the TWS collector is still live.
	input.IB.ObservedAt = now.Add(-5 * time.Minute)
	quotes := validSZ162411Quotes(now, 0.9220, 0.9225)

	snapshot := CalculateForSymbol(SZ162411Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil || snapshot.LOFValuation == nil {
		t.Fatalf("snapshot not ready: %+v", snapshot)
	}
	if snapshot.ValuationKind != CalculationModeLOFWeightedAnchor || snapshot.ModelVersion != SZ162411ModelVersion {
		t.Fatalf("snapshot identity = %+v", snapshot)
	}
	// Corrected close sample: 0.8804 × [4.5% + 95.5% ×
	// (174.58/166.41) × (6.7900/6.7904)] = 0.921626743564...
	assertClose(t, snapshot.Valuation.NAVBid, 0.921626743564, 1e-10)
	assertClose(t, snapshot.Valuation.NAVAsk, 0.921626743564, 1e-10)
	assertClose(t, snapshot.LOFValuation.StaticRatio, 0.045, 1e-12)
	if snapshot.LOFValuation.BaseReferencePriceBasis != LOFReferencePriceRegularClose ||
		snapshot.LOFValuation.EffectiveRatioSource != LOFEffectiveRatioWeightedAnchor ||
		snapshot.LOFValuation.CurrentReferenceQuoteSession != "us_overnight_live" {
		t.Fatalf("calculation trace = %+v", snapshot.LOFValuation)
	}
	if snapshot.LOFValuation.CurrentReferenceLast == nil || *snapshot.LOFValuation.CurrentReferenceLast != last {
		t.Fatalf("current last audit = %+v", snapshot.LOFValuation.CurrentReferenceLast)
	}
	if len(snapshot.OrderBook) != 2 {
		t.Fatalf("order book = %+v", snapshot.OrderBook)
	}
	assertClose(t, snapshot.Valuation.BuyDirectionPremiumRate, 0.9225/snapshot.Valuation.NAVBid-1, 1e-10)
	assertClose(t, snapshot.Valuation.SellDirectionPremiumRate, 0.9220/snapshot.Valuation.NAVAsk-1, 1e-10)
	if !strings.Contains(snapshot.Valuation.Formula, "美东16:00常规收盘价") || strings.Contains(snapshot.Valuation.Formula, "PCF") || strings.Contains(snapshot.Valuation.Formula, "CFETS") {
		t.Fatalf("formula = %q", snapshot.Valuation.Formula)
	}
	if snapshot.Actionable {
		t.Fatal("LOF indicator must not be advertised as a PCF redemption trade")
	}
	if len(snapshot.Warnings) != 0 {
		t.Fatalf("fresh in-session LOF inputs should have no data-health warnings: %+v", snapshot.Warnings)
	}
}

func TestCalculateSZ162411AcceptsSMARTLiveSession(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	input.IB.QuoteSession = "us_smart_live"
	quotes := validSZ162411Quotes(now, 0.9220, 0.9225)

	snapshot := CalculateForSymbol(SZ162411Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.Valuation == nil || snapshot.LOFValuation == nil {
		t.Fatalf("SMART snapshot not ready: %+v", snapshot)
	}
	if snapshot.Actionable {
		t.Fatal("LOF indicator must remain non-actionable after SMART is accepted")
	}
	if len(snapshot.Warnings) != 0 {
		t.Fatalf("fresh SMART live inputs should have no data-health warnings: %+v", snapshot.Warnings)
	}
	if snapshot.LOFValuation.CurrentReferenceQuoteSession != "us_smart_live" {
		t.Fatalf("SMART quote session audit = %q", snapshot.LOFValuation.CurrentReferenceQuoteSession)
	}
}

func TestCalculateSZ162411StillWarnsForUnknownQuoteSession(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	input.IB.QuoteSession = "unknown"
	quotes := validSZ162411Quotes(now, 0.9220, 0.9225)

	snapshot := CalculateForSymbol(SZ162411Symbol, &input, quotes, now)
	if len(snapshot.Warnings) != 1 || !strings.Contains(snapshot.Warnings[0], "quote_session 为 unknown") {
		t.Fatalf("unknown quote session warnings = %+v", snapshot.Warnings)
	}
}

func TestCalculateSZ162411KeepsBidAskSidesAndMinutePersistence(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 1, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	quotes := validSZ162411Quotes(now, 0.921, 0.923)
	snapshot := CalculateForSymbol(SZ162411Symbol, &input, quotes, now)
	if snapshot.Valuation == nil || snapshot.Valuation.NAVAsk <= snapshot.Valuation.NAVBid {
		t.Fatalf("bid/ask valuation = %+v", snapshot.Valuation)
	}
	point, ok := MinuteHistoryPointFromSnapshot(snapshot)
	if !ok {
		t.Fatalf("snapshot did not produce a minute point: %+v", snapshot)
	}
	if point.PCFTradingDay != input.LOF.BaseNAVDate {
		t.Fatalf("persisted anchor date = %q, want %q", point.PCFTradingDay, input.LOF.BaseNAVDate)
	}
	if point.XOPEquivalentShares != nil {
		t.Fatalf("LOF must not persist an XOP-share PCF calibration: %+v", point.XOPEquivalentShares)
	}
	assertClose(t, point.BasketBidNAV, snapshot.Valuation.NAVBid, 1e-12)
	assertClose(t, point.BasketAskNAV, snapshot.Valuation.NAVAsk, 1e-12)
}

func TestSZ162411InputAllowsPublicAnchorRecordAtTargetClose(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	input.LOF.BaseReference.CaptureStatus = "public_anchor_record"
	input.LOF.BaseReference.ObservedAt = input.LOF.BaseReference.TargetAt
	if err := input.Normalized(now).Validate(); err != nil {
		t.Fatalf("public production anchor must validate: %v", err)
	}
}

func TestSZ162411InputAllowsAuditedUSCloseCarryWithinSevenDays(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	newYork, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	input.LOF.BaseReference.TargetAt = time.Date(2026, 8, 3, 16, 0, 0, 0, newYork)
	input.LOF.BaseReference.ObservedAt = input.LOF.BaseReference.TargetAt.Add(-2 * time.Second)
	if err := input.Normalized(now).Validate(); err != nil {
		t.Fatalf("audited four-day US close carry must validate: %v", err)
	}
	input.LOF.BaseReference.TargetAt = time.Date(2026, 7, 30, 16, 0, 0, 0, newYork)
	input.LOF.BaseReference.ObservedAt = input.LOF.BaseReference.TargetAt
	if err := input.Normalized(now).Validate(); err == nil || !strings.Contains(err.Error(), "prior seven days") {
		t.Fatalf("eight-day close carry must fail: %v", err)
	}
}

func TestSZ162411InputRejectsObservationOutsideCloseWindow(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	input.LOF.BaseReference.ObservedAt = input.LOF.BaseReference.TargetAt.Add(-3*time.Minute - time.Second)
	if err := input.Normalized(now).Validate(); err == nil || !strings.Contains(err.Error(), "±180 seconds") {
		t.Fatalf("observation outside close window must fail: %v", err)
	}
}

func TestSZ162411InputRejectsPCFAndCFETSContamination(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	for _, mutate := range []func(*Input){
		func(input *Input) { input.PCF.SecurityID = "159518" },
		func(input *Input) {
			rate := 6.8
			input.FX = FXInput{Pair: "USD/CNY", Rate: &rate, TradingDay: "2026-08-11", QuoteTime: "10:00", Source: CFETSReferenceRateSource, FetchedAt: now}
		},
		func(input *Input) { input.FXRates = []FXInput{{Pair: "USD/CNY"}} },
		func(input *Input) { input.MarketQuotes = []MarketQuoteInput{{Symbol: "XOP"}} },
	} {
		input := validSZ162411Input(now)
		mutate(&input)
		if err := input.Normalized(now).Validate(); err == nil || !strings.Contains(err.Error(), "must not contain PCF or CFETS") {
			t.Fatalf("contaminated input error = %v", err)
		}
	}
}

func TestSZ162411InputRequiresRegularCloseNotHistoricalBidAsk(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	input.LOF.BaseReference.PriceBasis = "bid_ask_midpoint"
	if err := input.Normalized(now).Validate(); err == nil || !strings.Contains(err.Error(), LOFReferencePriceRegularClose) {
		t.Fatalf("historical midpoint must fail: %v", err)
	}
}

func TestSZ162411ManualEffectiveRatioRequiresCompleteAudit(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	input.LOF.EffectiveRatio.Value = 0.96
	input.LOF.EffectiveRatio.Source = LOFEffectiveRatioManualOverride
	if err := input.Normalized(now).Validate(); err == nil || !strings.Contains(err.Error(), "override audit") {
		t.Fatalf("incomplete override must fail: %v", err)
	}
	input.LOF.EffectiveRatio.OverrideSource = "private-settings:public-model"
	input.LOF.EffectiveRatio.OverrideUpdatedAt = now.Add(-time.Hour)
	input.LOF.EffectiveRatio.OverrideUpdatedBy = "operator"
	if err := input.Normalized(now).Validate(); err != nil {
		t.Fatalf("audited override must validate: %v", err)
	}
}

func TestSZ162411NormalizesQuoteSessionAndAuditSources(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	input.IB.QuoteSession = " us_overnight_live "
	input.LOF.BaseNAVSource = " public_api "
	normalized := input.Normalized(now)
	if normalized.IB.QuoteSession != "us_overnight_live" || normalized.LOF.BaseNAVSource != "public_api" {
		t.Fatalf("normalized input = %+v", normalized)
	}
	if normalized.ValuationKind != CalculationModeLOFWeightedAnchor {
		t.Fatalf("valuation kind = %q", normalized.ValuationKind)
	}
	if normalized.ValuationAnchorDate() != "2026-08-07" {
		t.Fatalf("anchor date = %q", normalized.ValuationAnchorDate())
	}
}

func TestSZ162411RejectsWrongValuationKind(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now).Normalized(now)
	input.ValuationKind = CalculationModeXOPProxy
	if err := input.Validate(); err == nil || !strings.Contains(err.Error(), "valuation_kind") {
		t.Fatalf("wrong valuation kind error = %v", err)
	}
}

func validSZ162411Input(now time.Time) Input {
	baseFX, currentFX := 6.7904, 6.7900
	bid, ask, last := 174.57, 174.59, 174.58
	newYork, err := time.LoadLocation("America/New_York")
	if err != nil {
		panic(err)
	}
	target := time.Date(2026, 8, 7, 16, 0, 0, 0, newYork)
	return Input{
		SchemaVersion: InputSchemaVersion,
		Symbol:        SZ162411Symbol,
		ModelVersion:  SZ162411ModelVersion,
		LOF: &LOFWeightedAnchorInput{
			BaseNAV:          0.8804,
			BaseNAVDate:      "2026-08-07",
			BaseNAVSource:    "public_api:/api/v1/funds/SZ162411",
			BaseNAVFetchedAt: now,
			BaseReference: LOFBaseReferenceInput{
				Symbol: ReferenceSymbol, Price: 166.41, PriceBasis: LOFReferencePriceRegularClose,
				TargetAt: target, ObservedAt: target, Source: "sina_us", CaptureStatus: "public_anchor_record",
			},
			BaseFX:    FXInput{Pair: "USD/CNY", Rate: &baseFX, TradingDay: "2026-08-07", QuoteTime: "09:15", Source: "SAFE_CENTRAL_PARITY", FetchedAt: now},
			CurrentFX: FXInput{Pair: "USD/CNY", Rate: &currentFX, TradingDay: "2026-08-11", QuoteTime: "09:15", Source: "SAFE_CENTRAL_PARITY", FetchedAt: now},
			EffectiveRatio: LOFEffectiveRatioInput{
				Value: SZ162411DefaultEffectiveRatio, Source: LOFEffectiveRatioWeightedAnchor,
				DefaultValue: SZ162411DefaultEffectiveRatio, DefaultSource: LOFEffectiveRatioWeightedAnchor,
			},
		},
		IB: IBQuoteInput{
			Symbol: ReferenceSymbol, Bid: &bid, Ask: &ask, Last: &last,
			MarketDataType: "Live", QuoteSession: "us_overnight_live",
			Source: "IBKR_TWS", ObservedAt: now,
		},
		Source: "mac-home-private-162411-uploader", GeneratedAt: now, ReceivedAt: now,
	}
}

func validSZ162411Quotes(now time.Time, bid, ask float64) map[string]domain.Quote {
	return map[string]domain.Quote{
		SZ162411Symbol: {
			Symbol: SZ162411Symbol, Name: SZ162411Name, Price: (bid + ask) / 2,
			BidLevels: []domain.Level{{Level: 1, Price: bid, Volume: 1000}},
			AskLevels: []domain.Level{{Level: 1, Price: ask, Volume: 900}},
			QuoteDate: now.In(shanghaiLocation).Format("2006-01-02"),
			QuoteTime: now.In(shanghaiLocation).Format("15:04:05"),
			FetchedAt: now, IsRealtime: true,
		},
	}
}

func TestSZ162411ReferenceMultiplierUsesBothFXAndXOP(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	snapshot := CalculateForSymbol(SZ162411Symbol, &input, validSZ162411Quotes(now, 0.92, 0.93), now)
	trace := snapshot.LOFValuation
	if trace == nil {
		t.Fatalf("missing trace: %+v", snapshot)
	}
	wantBid := *input.IB.Bid / input.LOF.BaseReference.Price * (*input.LOF.CurrentFX.Rate / *input.LOF.BaseFX.Rate)
	if math.Abs(trace.ReferenceBidMultiplier-wantBid) > 1e-12 {
		t.Fatalf("reference bid multiplier = %.12f, want %.12f", trace.ReferenceBidMultiplier, wantBid)
	}
}

func TestSZ162411UsesDomesticFetchHeartbeatNotLastMarketEvent(t *testing.T) {
	now := time.Date(2026, 8, 11, 10, 0, 0, 0, shanghaiLocation)
	input := validSZ162411Input(now)
	quotes := validSZ162411Quotes(now, 0.92, 0.93)
	domestic := quotes[SZ162411Symbol]
	domestic.FetchedAt = now.Add(-60 * time.Second)
	domestic.QuoteTime = now.Add(-3 * time.Minute).Format("15:04:05")
	quotes[SZ162411Symbol] = domestic
	snapshot := CalculateForSymbol(SZ162411Symbol, &input, quotes, now)
	if len(snapshot.Warnings) != 0 {
		t.Fatalf("fresh rotating-batch fetch with unchanged market event should remain healthy: %+v", snapshot.Warnings)
	}

	domestic.FetchedAt = now.Add(-91 * time.Second)
	quotes[SZ162411Symbol] = domestic
	snapshot = CalculateForSymbol(SZ162411Symbol, &input, quotes, now)
	if len(snapshot.Warnings) != 1 || !strings.Contains(snapshot.Warnings[0], "超过 90 秒") {
		t.Fatalf("stale domestic fetch warning = %+v", snapshot.Warnings)
	}
}
