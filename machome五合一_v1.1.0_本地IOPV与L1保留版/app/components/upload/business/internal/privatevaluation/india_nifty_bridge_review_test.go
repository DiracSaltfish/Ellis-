package privatevaluation

import (
	"context"
	"math"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestIndiaNiftyBridgeHistoryPointAuditsProductionCrossingFormula(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 30, 0, 0, shanghaiLocation)
	snapshot := indiaNiftyReviewTestSnapshot(t, now, "NIFTYQ26", "NIFTYQ26", nil)
	point, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(snapshot)
	if !ok {
		t.Fatal("complete production bridge snapshot should be persisted")
	}
	bridge := snapshot.Input.India.NiftyBridge
	wantBid := *bridge.INDAReference.Bid * (*bridge.Nifty.Bid / *bridge.NiftyReference.Ask)
	wantAsk := *bridge.INDAReference.Ask * (*bridge.Nifty.Ask / *bridge.NiftyReference.Bid)
	assertClose(t, point.Audit.SyntheticINDABid, wantBid, 1e-12)
	assertClose(t, point.Audit.SyntheticINDAAsk, wantAsk, 1e-12)
	assertClose(t, point.BridgeBidNAV, snapshot.IndiaValuations.NiftyBridge.Valuation.NAVBid, 1e-12)
	assertClose(t, point.BridgeAskNAV, snapshot.IndiaValuations.NiftyBridge.Valuation.NAVAsk, 1e-12)
	if point.RollStatus != IndiaNiftyBridgeStatusOrdinarySameContract {
		t.Fatalf("state=%q, want ordinary same-contract", point.RollStatus)
	}
}

func TestIndiaNiftyBridgeHistoryPointFailsClosedOnRollAuditMismatch(t *testing.T) {
	now := time.Date(2026, 8, 25, 9, 30, 0, 0, shanghaiLocation)
	roll := &IndiaNiftyRollAdjustmentInput{
		RollDate:    "2026-08-25",
		CapturedAt:  time.Date(2026, 8, 24, 12, 30, 0, 0, shanghaiLocation),
		OldContract: "NIFTYQ26",
		NewContract: "NIFTYU26",
		Direction:   "new_to_old",
		BidFactor:   0.997,
		AskFactor:   1.003,
		Source:      "IBKR_TWS_LIVE_SGX_NIFTY_ROLL_1230_BJT",
	}

	crossWithoutRoll := indiaNiftyReviewTestSnapshot(t, now, "NIFTYU26", "NIFTYQ26", nil)
	if _, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(crossWithoutRoll); ok {
		t.Fatal("cross-contract snapshot without audited basis must not enter review history")
	}

	sameWithRoll := indiaNiftyReviewTestSnapshot(t, now, "NIFTYU26", "NIFTYU26", roll)
	if _, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(sameWithRoll); ok {
		t.Fatal("same-contract snapshot with an unnecessary roll basis must not enter review history")
	}

	crossWithRoll := indiaNiftyReviewTestSnapshot(t, now, "NIFTYU26", "NIFTYQ26", roll)
	point, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(crossWithRoll)
	if !ok || point.RollStatus != IndiaNiftyBridgeStatusCrossContractAdjusted || point.Audit.RollAdjustment == nil {
		t.Fatalf("audited cross-contract snapshot=%+v ok=%v", point, ok)
	}

	historicalRoll := *roll
	historicalRoll.Source = IndiaNiftyHistoricalRollSource
	historicalSnapshot := indiaNiftyReviewTestSnapshot(t, now, "NIFTYU26", "NIFTYQ26", &historicalRoll)
	if point, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(historicalSnapshot); !ok ||
		point.Audit.RollAdjustment == nil || point.Audit.RollAdjustment.Source != IndiaNiftyHistoricalRollSource {
		t.Fatalf("audited historical BID/ASK roll snapshot=%+v ok=%v", point, ok)
	}

	unknownSource := crossWithRoll
	unknownInput := *unknownSource.Input
	unknownIndia := *unknownInput.India
	unknownBridge := *unknownIndia.NiftyBridge
	unknownRoll := *unknownBridge.RollAdjustment
	unknownRoll.Source = "UNVERIFIED_ROLL_SOURCE"
	unknownBridge.RollAdjustment = &unknownRoll
	unknownIndia.NiftyBridge = &unknownBridge
	unknownInput.India = &unknownIndia
	unknownSource.Input = &unknownInput
	if _, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(unknownSource); ok {
		t.Fatal("unknown roll-basis source must fail closed")
	}

	invalidReference := crossWithRoll
	invalidInput := *invalidReference.Input
	invalidIndia := *invalidInput.India
	invalidBridge := *invalidIndia.NiftyBridge
	invalidBridge.ReferenceAt = now.Add(time.Minute)
	invalidIndia.NiftyBridge = &invalidBridge
	invalidInput.India = &invalidIndia
	invalidReference.Input = &invalidInput
	if _, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(invalidReference); ok {
		t.Fatal("reference_at at or after the China minute must fail closed")
	}
}

func TestIndiaNiftyBridgeHistoryPointClassifiesCalendarRollDaySameContract(t *testing.T) {
	now := time.Date(2026, 8, 25, 10, 30, 0, 0, shanghaiLocation)
	snapshot := indiaNiftyReviewTestSnapshot(t, now, "NIFTYU26", "NIFTYU26", nil)
	point, ok := IndiaNiftyBridgeHistoryPointFromSnapshot(snapshot)
	if !ok || point.RollStatus != IndiaNiftyBridgeStatusCalendarRollDaySameContract {
		t.Fatalf("calendar roll point=%+v ok=%v", point, ok)
	}
}

func TestIndiaNiftyBridgeReviewUsesAllMinutesForDayFirstSummaryAndOnlyReturnsCheckpoints(t *testing.T) {
	official := 1.0
	baseAudit := IndiaNiftyBridgeAudit{
		BaseNAVDate:              "2026-07-08",
		BaseNAV:                  1,
		InvestmentRatio:          IndiaInvestmentRatio,
		StaticRatio:              IndiaStaticRatio,
		BaseAnchorPrice:          100,
		BaseFX:                   7,
		CurrentFX:                7,
		SyntheticINDABid:         100,
		SyntheticINDAAsk:         101,
		CurrentNiftyBid:          20_000,
		CurrentNiftyAsk:          20_010,
		CurrentNiftyContract:     "NIFTYQ26",
		ReferenceINDABid:         100,
		ReferenceINDAAsk:         101,
		ReferenceNiftyBid:        20_000,
		ReferenceNiftyAsk:        20_010,
		ReferenceNiftyContract:   "NIFTYQ26",
		Beta:                     1,
		ContractSelectionVersion: NiftyContractSelectionVersion,
	}
	sources := []IndiaNiftyBridgeReviewSource{
		indiaNiftyReviewSourceAt("2026-07-09", "09:35", 1.10, 0.99, 1.01, 0.995, 1.005, &official, baseAudit),
		indiaNiftyReviewSourceAt("2026-07-10", "09:35", 1.10, 0.90, 0.92, 0.88, 0.90, &official, baseAudit),
		indiaNiftyReviewSourceAt("2026-07-10", "09:36", 1.10, 0.90, 0.92, 0.88, 0.90, &official, baseAudit),
		indiaNiftyReviewSourceAt("2026-07-10", "10:30", 1.10, 0.90, 0.92, 0.88, 0.90, &official, baseAudit),
	}
	checkpoints, summary, rows := buildIndiaNiftyBridgeReview(sources)
	if len(checkpoints) != 6 {
		t.Fatalf("checkpoints=%v", checkpoints)
	}
	if len(rows) != 3 { // 09:36 contributes to summary but is not returned.
		t.Fatalf("display rows=%d, want 3", len(rows))
	}
	if summary.MinuteSamples != 4 || summary.PairedSamples != 4 || summary.TradingDays != 2 {
		t.Fatalf("summary counts=%+v", summary)
	}

	allRows := make([]IndiaNiftyBridgeReviewRow, 0, 4)
	dayOne := IndiaNiftyBridgeReviewRow{
		TradingDay: "2026-07-09", State: IndiaNiftyBridgeStatusOrdinarySameContract,
		DirectErrorVsBidBPS: floatPointer(10), DirectErrorVsAskBPS: floatPointer(-10),
		BridgeErrorVsBidBPS: floatPointer(5), BridgeErrorVsAskBPS: floatPointer(-5),
		PairedAbsErrorDeltaBPS: floatPointer(-5), BridgeCloser: boolPointer(true), BridgeContainsOfficial: boolPointer(true),
	}
	allRows = append(allRows, dayOne)
	for range 3 {
		allRows = append(allRows, IndiaNiftyBridgeReviewRow{
			TradingDay: "2026-07-10", State: IndiaNiftyBridgeStatusOrdinarySameContract,
			DirectErrorVsBidBPS: floatPointer(100), DirectErrorVsAskBPS: floatPointer(-100),
			BridgeErrorVsBidBPS: floatPointer(120), BridgeErrorVsAskBPS: floatPointer(-120),
			PairedAbsErrorDeltaBPS: floatPointer(20), BridgeCloser: boolPointer(false), BridgeContainsOfficial: boolPointer(false),
		})
	}
	dayFirst := summarizeIndiaNiftyBridgeReview(allRows)
	assertPointerClose(t, dayFirst.DirectBidPremiumMAEBPS, 55)
	assertPointerClose(t, dayFirst.BridgeBidPremiumMAEBPS, 62.5)
	assertPointerClose(t, dayFirst.PairedMAEDeltaBPS, 7.5)
	assertPointerClose(t, dayFirst.BridgeWinRatePct, 50)
	assertPointerClose(t, dayFirst.BridgeIntervalCoveragePct, 50)
}

func TestIndiaNiftyBridgeReviewAddsRollOpenOnlyForActualCrossContractDay(t *testing.T) {
	official := 1.0
	audit := IndiaNiftyBridgeAudit{ContractSelectionVersion: NiftyContractSelectionVersion}
	source := indiaNiftyReviewSourceAt("2026-08-25", "09:30", 1, 0.99, 1.01, 0.995, 1.005, &official, audit)
	source.RollStatus = IndiaNiftyBridgeStatusCrossContractAdjusted
	checkpoints, _, rows := buildIndiaNiftyBridgeReview([]IndiaNiftyBridgeReviewSource{source})
	if len(checkpoints) != 7 || checkpoints[0].Kind != "roll_open" || checkpoints[0].Minute != "09:30" {
		t.Fatalf("cross-contract checkpoints=%+v", checkpoints)
	}
	if len(rows) != 1 || rows[0].Checkpoint != "09:30" || rows[0].State != IndiaNiftyBridgeStatusCrossContractAdjusted {
		t.Fatalf("cross-contract rows=%+v", rows)
	}

	source.RollStatus = IndiaNiftyBridgeStatusCalendarRollDaySameContract
	checkpoints, _, rows = buildIndiaNiftyBridgeReview([]IndiaNiftyBridgeReviewSource{source})
	if len(checkpoints) != 6 || len(rows) != 0 {
		t.Fatalf("same-contract calendar day must not expose roll-open: checkpoints=%+v rows=%+v", checkpoints, rows)
	}
}

func TestServiceIndiaNiftyBridgeHistoryReviewUsesAllMinutesButReturnsFrozenCheckpointContract(t *testing.T) {
	official := 1.0
	audit := IndiaNiftyBridgeAudit{ContractSelectionVersion: NiftyContractSelectionVersion}
	repository := &indiaNiftyReviewTestRepository{sources: []IndiaNiftyBridgeReviewSource{
		indiaNiftyReviewSourceAt("2026-07-10", "09:35", 1.1, 0.99, 1.01, 0.995, 1.005, &official, audit),
		indiaNiftyReviewSourceAt("2026-07-10", "09:36", 1.1, 0.99, 1.01, 0.995, 1.005, &official, audit),
	}}
	review, err := NewService(repository, nil).IndiaNiftyBridgeHistoryReview(context.Background(), SZ164824Symbol, 120)
	if err != nil {
		t.Fatal(err)
	}
	if review.SchemaVersion != IndiaNiftyBridgeReviewSchemaVersion || review.AsOf.IsZero() {
		t.Fatalf("response identity=%+v", review)
	}
	if review.Summary.MinuteSamples != 2 || review.Summary.PairedSamples != 2 || len(review.Rows) != 1 || review.Rows[0].Checkpoint != "09:35" {
		t.Fatalf("all-minute summary/checkpoint rows=%+v", review)
	}
	if review.Timing.NiftyActiveWindow != "09:30-11:30、13:00-14:59 BJT 自动口径；15:00 仅作收盘观察" {
		t.Fatalf("nifty active window=%q", review.Timing.NiftyActiveWindow)
	}
}

func indiaNiftyReviewTestSnapshot(t *testing.T, now time.Time, currentContract, referenceContract string, roll *IndiaNiftyRollAdjustmentInput) Snapshot {
	t.Helper()
	input := validIndiaT2Input(now)
	newYork, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	referenceDay := now.AddDate(0, 0, -1)
	if roll != nil || currentContract != referenceContract {
		referenceDay = time.Date(2026, 8, 21, 12, 0, 0, 0, shanghaiLocation)
	}
	referenceAt := time.Date(referenceDay.Year(), referenceDay.Month(), referenceDay.Day(), 15, 50, 0, 0, newYork)
	niftyBid, niftyAsk := 20_200.0, 20_220.0
	indaReferenceBid, indaReferenceAsk := 100.0, 101.0
	niftyReferenceBid, niftyReferenceAsk := 20_000.0, 20_020.0
	input.India.NiftyBridge = &IndiaNiftyBridgeInput{
		Nifty:                    IBQuoteInput{Symbol: "NIFTY", Contract: currentContract, Bid: &niftyBid, Ask: &niftyAsk, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: now},
		INDAReference:            IBQuoteInput{Symbol: IndiaReferenceSymbol, Contract: "INDA", Bid: &indaReferenceBid, Ask: &indaReferenceAsk, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: referenceAt},
		NiftyReference:           IBQuoteInput{Symbol: "NIFTY", Contract: referenceContract, Bid: &niftyReferenceBid, Ask: &niftyReferenceAsk, MarketDataType: "Live", Source: "IBKR_TWS", ObservedAt: referenceAt},
		ReferenceAt:              referenceAt,
		Beta:                     1,
		ContractSelectionVersion: NiftyContractSelectionVersion,
		RollAdjustment:           roll,
	}
	quotes := validQuotes(now)
	domestic := quotes[TargetSymbol]
	domestic.Symbol = SZ164824Symbol
	domestic.Name = SZ164824Name
	domestic.Price = 1.0185
	domestic.BidLevels = []domain.Level{{Level: 1, Price: 1.018, Volume: 100_000}}
	domestic.AskLevels = []domain.Level{{Level: 1, Price: 1.019, Volume: 100_000}}
	delete(quotes, TargetSymbol)
	quotes[SZ164824Symbol] = domestic
	snapshot := CalculateForSymbol(SZ164824Symbol, &input, quotes, now)
	if !snapshot.Ready || snapshot.IndiaValuations == nil || snapshot.IndiaValuations.NiftyBridge == nil {
		t.Fatalf("test bridge snapshot is invalid: %+v", snapshot)
	}
	return snapshot
}

func indiaNiftyReviewSourceAt(day, minute string, market, directBid, directAsk, bridgeBid, bridgeAsk float64, official *float64, audit IndiaNiftyBridgeAudit) IndiaNiftyBridgeReviewSource {
	parsed, _ := time.ParseInLocation("2006-01-02 15:04", day+" "+minute, shanghaiLocation)
	return IndiaNiftyBridgeReviewSource{
		IndiaNiftyBridgeHistoryPoint: IndiaNiftyBridgeHistoryPoint{
			Symbol:       SZ164824Symbol,
			Minute:       parsed,
			MarketPrice:  market,
			DirectBidNAV: directBid,
			DirectAskNAV: directAsk,
			BridgeBidNAV: bridgeBid,
			BridgeAskNAV: bridgeAsk,
			RollStatus:   IndiaNiftyBridgeStatusOrdinarySameContract,
			Audit:        audit,
		},
		OfficialNAV: official,
	}
}

func assertPointerClose(t *testing.T, actual *float64, want float64) {
	t.Helper()
	if actual == nil || math.Abs(*actual-want) > 1e-9 {
		t.Fatalf("actual=%v want=%v", actual, want)
	}
}

func boolPointer(value bool) *bool { return &value }

// Compile-time assertions keep the test fixtures aligned with repository
// contracts used by Service without requiring a database.
type indiaNiftyReviewTestRepository struct {
	sources []IndiaNiftyBridgeReviewSource
}

func (r *indiaNiftyReviewTestRepository) LoadPrivateValuationInputs(context.Context) ([]Input, error) {
	return nil, nil
}

func (r *indiaNiftyReviewTestRepository) UpsertPrivateValuationInput(context.Context, Input) error {
	return nil
}

func (r *indiaNiftyReviewTestRepository) UpsertPrivateValuationSnapshot(context.Context, Snapshot) error {
	return nil
}

func (r *indiaNiftyReviewTestRepository) LoadPrivateIndiaNiftyBridgeHistoryReview(context.Context, string, int) ([]IndiaNiftyBridgeReviewSource, error) {
	return append([]IndiaNiftyBridgeReviewSource(nil), r.sources...), nil
}

var _ Repository = (*indiaNiftyReviewTestRepository)(nil)
var _ IndiaNiftyBridgeReviewRepository = (*indiaNiftyReviewTestRepository)(nil)
