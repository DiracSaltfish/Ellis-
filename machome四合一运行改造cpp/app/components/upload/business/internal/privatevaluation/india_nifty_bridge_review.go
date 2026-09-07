package privatevaluation

import (
	"math"
	"sort"
	"strings"
	"time"
)

const IndiaNiftyBridgeReviewSchemaVersion = "private-india-nifty-bridge-review.v1"

var indiaNiftyBridgeBaseCheckpoints = []IndiaNiftyBridgeReviewCheckpoint{
	{Minute: "09:35", Label: "开盘稳定点", Kind: "normal"},
	{Minute: "10:30", Label: "上午中段", Kind: "normal"},
	{Minute: "11:25", Label: "午间收盘前", Kind: "normal"},
	{Minute: "13:30", Label: "下午中段", Kind: "normal"},
	{Minute: "14:55", Label: "收盘前", Kind: "normal"},
	{Minute: "15:00", Label: "收盘观测", Kind: "close_observation"},
}

var indiaNiftyBridgeRollOpenCheckpoint = IndiaNiftyBridgeReviewCheckpoint{
	Minute: "09:30",
	Label:  "跨合约开盘观测",
	Kind:   "roll_open",
}

// IndiaNiftyBridgeHistoryPointFromSnapshot extracts a review row only when the
// exact production snapshot is independently auditable. The ordinary minute
// history remains permissive for backwards compatibility; this dedicated data
// set deliberately fails closed around contract rolls.
func IndiaNiftyBridgeHistoryPointFromSnapshot(snapshot Snapshot) (IndiaNiftyBridgeHistoryPoint, bool) {
	if snapshot.Symbol != SZ164824Symbol || !snapshot.Ready || snapshot.Input == nil || snapshot.Input.India == nil ||
		snapshot.Valuation == nil || snapshot.IndiaValuations == nil || snapshot.IndiaValuations.NiftyBridge == nil {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}
	minutePoint, ok := MinuteHistoryPointFromSnapshot(snapshot)
	if !ok || minutePoint.NiftyBridgeBidNAV == nil || minutePoint.NiftyBridgeAskNAV == nil ||
		!finitePositive(minutePoint.MarketPrice) || !finitePositive(minutePoint.BasketBidNAV) ||
		!finitePositive(minutePoint.BasketAskNAV) || minutePoint.BasketAskNAV < minutePoint.BasketBidNAV ||
		!finitePositive(*minutePoint.NiftyBridgeBidNAV) || !finitePositive(*minutePoint.NiftyBridgeAskNAV) ||
		*minutePoint.NiftyBridgeAskNAV < *minutePoint.NiftyBridgeBidNAV {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}

	input := *snapshot.Input
	india := input.India
	bridge := india.NiftyBridge
	if bridge == nil || bridge.ContractSelectionVersion != NiftyContractSelectionVersion ||
		bridge.ReferenceAt.IsZero() || !bridge.ReferenceAt.Before(snapshot.AsOf) ||
		bridge.Nifty.ObservedAt.IsZero() || bridge.INDAReference.ObservedAt.IsZero() ||
		bridge.NiftyReference.ObservedAt.IsZero() || input.IB.ObservedAt.IsZero() || input.GeneratedAt.IsZero() ||
		bridge.Nifty.Bid == nil || bridge.Nifty.Ask == nil ||
		bridge.INDAReference.Bid == nil || bridge.INDAReference.Ask == nil ||
		bridge.NiftyReference.Bid == nil || bridge.NiftyReference.Ask == nil ||
		input.IB.Bid == nil || input.IB.Ask == nil || india.BaseFX.Rate == nil || india.CurrentFX.Rate == nil {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}
	if err := validateIndiaNiftyBridge(bridge); err != nil {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}
	values := []float64{
		*bridge.Nifty.Bid, *bridge.Nifty.Ask,
		*bridge.INDAReference.Bid, *bridge.INDAReference.Ask,
		*bridge.NiftyReference.Bid, *bridge.NiftyReference.Ask,
		*input.IB.Bid, *input.IB.Ask, *india.BaseFX.Rate, *india.CurrentFX.Rate,
		bridge.Beta, india.BaseNAV, india.InvestmentRatio, india.StaticRatio,
	}
	for _, value := range values {
		if !finitePositive(value) {
			return IndiaNiftyBridgeHistoryPoint{}, false
		}
	}
	if *bridge.Nifty.Ask < *bridge.Nifty.Bid || *bridge.INDAReference.Ask < *bridge.INDAReference.Bid ||
		*bridge.NiftyReference.Ask < *bridge.NiftyReference.Bid || *input.IB.Ask < *input.IB.Bid {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}

	currentContract := strings.TrimSpace(bridge.Nifty.Contract)
	referenceContract := strings.TrimSpace(bridge.NiftyReference.Contract)
	if currentContract == "" || referenceContract == "" {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}
	crossContract := currentContract != referenceContract
	if crossContract != (bridge.RollAdjustment != nil) {
		// A cross-contract observation without a basis, or a same-contract
		// observation carrying an unnecessary basis, is not auditable.
		return IndiaNiftyBridgeHistoryPoint{}, false
	}
	if bridge.RollAdjustment != nil {
		if err := validateIndiaNiftyRollAdjustment(bridge.RollAdjustment); err != nil {
			return IndiaNiftyBridgeHistoryPoint{}, false
		}
		if bridge.RollAdjustment.RollDate != snapshot.AsOf.In(shanghaiLocation).Format("2006-01-02") {
			return IndiaNiftyBridgeHistoryPoint{}, false
		}
		roll := bridge.RollAdjustment
		if (roll.Direction == "new_to_old" && (currentContract != roll.NewContract || referenceContract != roll.OldContract)) ||
			(roll.Direction == "old_to_new" && (currentContract != roll.OldContract || referenceContract != roll.NewContract)) {
			return IndiaNiftyBridgeHistoryPoint{}, false
		}
	}

	baseAnchorPrice := 0.0
	for _, anchor := range india.Anchors {
		if !finitePositive(anchor.Price) || !finitePositive(anchor.Weight) {
			return IndiaNiftyBridgeHistoryPoint{}, false
		}
		baseAnchorPrice += anchor.Price * anchor.Weight
	}
	if !finitePositive(baseAnchorPrice) {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}

	// This deliberately replays the conservative crossing formula from raw
	// inputs instead of deriving the synthetic prices from the stored bridge
	// NAV. The bridge NAV itself remains the production engine's calculated
	// value and is not recalculated here.
	syntheticBid := *bridge.INDAReference.Bid * math.Pow(*bridge.Nifty.Bid / *bridge.NiftyReference.Ask, bridge.Beta)
	syntheticAsk := *bridge.INDAReference.Ask * math.Pow(*bridge.Nifty.Ask / *bridge.NiftyReference.Bid, bridge.Beta)
	if !finitePositive(syntheticBid) || !finitePositive(syntheticAsk) || syntheticAsk < syntheticBid {
		return IndiaNiftyBridgeHistoryPoint{}, false
	}

	status := IndiaNiftyBridgeStatusOrdinarySameContract
	localMinute := snapshot.AsOf.In(shanghaiLocation).Truncate(time.Minute)
	if crossContract {
		status = IndiaNiftyBridgeStatusCrossContractAdjusted
	} else {
		day := time.Date(localMinute.Year(), localMinute.Month(), localMinute.Day(), 0, 0, 0, 0, shanghaiLocation)
		if indiaLastTuesdayOfMonth(day).Equal(day) {
			status = IndiaNiftyBridgeStatusCalendarRollDaySameContract
		}
	}

	var rollAudit *IndiaNiftyBridgeRollAudit
	if roll := bridge.RollAdjustment; roll != nil {
		rollAudit = &IndiaNiftyBridgeRollAudit{
			RollDate:    roll.RollDate,
			CapturedAt:  roll.CapturedAt,
			OldContract: roll.OldContract,
			NewContract: roll.NewContract,
			Direction:   roll.Direction,
			BidFactor:   roll.BidFactor,
			AskFactor:   roll.AskFactor,
			Source:      roll.Source,
		}
	}

	return IndiaNiftyBridgeHistoryPoint{
		Symbol:       snapshot.Symbol,
		Minute:       localMinute,
		MarketPrice:  minutePoint.MarketPrice,
		DirectBidNAV: minutePoint.BasketBidNAV,
		DirectAskNAV: minutePoint.BasketAskNAV,
		BridgeBidNAV: *minutePoint.NiftyBridgeBidNAV,
		BridgeAskNAV: *minutePoint.NiftyBridgeAskNAV,
		RollStatus:   status,
		Audit: IndiaNiftyBridgeAudit{
			BaseNAVDate:              india.BaseNAVDate,
			BaseNAV:                  india.BaseNAV,
			InvestmentRatio:          india.InvestmentRatio,
			StaticRatio:              india.StaticRatio,
			BaseAnchorPrice:          baseAnchorPrice,
			BaseFX:                   *india.BaseFX.Rate,
			CurrentFX:                *india.CurrentFX.Rate,
			CurrentFXTradingDay:      india.CurrentFX.TradingDay,
			DirectINDABid:            *input.IB.Bid,
			DirectINDAAsk:            *input.IB.Ask,
			DirectINDAObservedAt:     input.IB.ObservedAt,
			CurrentNiftyBid:          *bridge.Nifty.Bid,
			CurrentNiftyAsk:          *bridge.Nifty.Ask,
			CurrentNiftyContract:     currentContract,
			CurrentNiftyObservedAt:   bridge.Nifty.ObservedAt,
			ReferenceAt:              bridge.ReferenceAt,
			ReferenceINDABid:         *bridge.INDAReference.Bid,
			ReferenceINDAAsk:         *bridge.INDAReference.Ask,
			ReferenceINDAContract:    strings.TrimSpace(bridge.INDAReference.Contract),
			ReferenceINDAObservedAt:  bridge.INDAReference.ObservedAt,
			ReferenceNiftyBid:        *bridge.NiftyReference.Bid,
			ReferenceNiftyAsk:        *bridge.NiftyReference.Ask,
			ReferenceNiftyContract:   referenceContract,
			ReferenceNiftyObservedAt: bridge.NiftyReference.ObservedAt,
			Beta:                     bridge.Beta,
			SyntheticINDABid:         syntheticBid,
			SyntheticINDAAsk:         syntheticAsk,
			ContractSelectionVersion: bridge.ContractSelectionVersion,
			RollAdjustment:           rollAudit,
			InputSource:              input.Source,
			InputGeneratedAt:         input.GeneratedAt,
		},
		Input: input,
	}, true
}

func indiaNiftyBridgeReviewTiming() IndiaNiftyBridgeReviewTiming {
	return IndiaNiftyBridgeReviewTiming{
		ChinaSessions:      []string{"09:30-11:30 BJT", "13:00-15:00 BJT"},
		NiftyActiveWindow:  "09:30-11:30、13:00-14:59 BJT 自动口径；15:00 仅作收盘观察",
		ReferenceWindowET:  "15:49-15:51 ET",
		ReferenceCenterET:  "15:50 ET",
		RollRule:           "NIFTY 从每月最后一个周二起使用下月合约；仅当前合约与参考合约不同时使用实盘换月基差校正",
		RollBasisWindowBJT: "最后一个周二前一周一 12:28-12:32 BJT",
		RollBasisCenterBJT: "最后一个周二前一周一 12:30 BJT",
		SelectionVersion:   NiftyContractSelectionVersion,
	}
}

func buildIndiaNiftyBridgeReview(sources []IndiaNiftyBridgeReviewSource) ([]IndiaNiftyBridgeReviewCheckpoint, IndiaNiftyBridgeReviewSummary, []IndiaNiftyBridgeReviewRow) {
	ordered := append([]IndiaNiftyBridgeReviewSource(nil), sources...)
	sort.SliceStable(ordered, func(i, j int) bool { return ordered[i].Minute.Before(ordered[j].Minute) })

	allRows := make([]IndiaNiftyBridgeReviewRow, 0, len(ordered))
	hasCrossContract := false
	for _, source := range ordered {
		row := indiaNiftyBridgeReviewRow(source)
		allRows = append(allRows, row)
		if row.State == IndiaNiftyBridgeStatusCrossContractAdjusted {
			hasCrossContract = true
		}
	}
	summary := summarizeIndiaNiftyBridgeReview(allRows)

	checkpoints := append([]IndiaNiftyBridgeReviewCheckpoint(nil), indiaNiftyBridgeBaseCheckpoints...)
	if hasCrossContract {
		checkpoints = append([]IndiaNiftyBridgeReviewCheckpoint{indiaNiftyBridgeRollOpenCheckpoint}, checkpoints...)
	}
	checkpointByMinute := make(map[string]IndiaNiftyBridgeReviewCheckpoint, len(checkpoints))
	for _, checkpoint := range checkpoints {
		checkpointByMinute[checkpoint.Minute] = checkpoint
	}
	displayed := make([]IndiaNiftyBridgeReviewRow, 0)
	for _, row := range allRows {
		minute := row.Minute.In(shanghaiLocation).Format("15:04")
		checkpoint, ok := checkpointByMinute[minute]
		if !ok || (checkpoint.Kind == "roll_open" && row.State != IndiaNiftyBridgeStatusCrossContractAdjusted) {
			continue
		}
		row.Checkpoint = checkpoint.Minute
		displayed = append(displayed, row)
	}
	// Review pages show the newest day first, while preserving chronological
	// checkpoint order within each day.
	sort.SliceStable(displayed, func(i, j int) bool {
		if displayed[i].TradingDay == displayed[j].TradingDay {
			return displayed[i].Minute.Before(displayed[j].Minute)
		}
		return displayed[i].TradingDay > displayed[j].TradingDay
	})
	return checkpoints, summary, displayed
}

func indiaNiftyBridgeReviewRow(source IndiaNiftyBridgeReviewSource) IndiaNiftyBridgeReviewRow {
	audit := source.Audit
	row := IndiaNiftyBridgeReviewRow{
		TradingDay:             source.Minute.In(shanghaiLocation).Format("2006-01-02"),
		Minute:                 source.Minute.In(shanghaiLocation),
		State:                  source.RollStatus,
		MarketPrice:            source.MarketPrice,
		OfficialNAV:            source.OfficialNAV,
		DirectNAVBid:           source.DirectBidNAV,
		DirectNAVAsk:           source.DirectAskNAV,
		BridgeNAVBid:           source.BridgeBidNAV,
		BridgeNAVAsk:           source.BridgeAskNAV,
		DirectPremiumVsBidPct:  (source.MarketPrice/source.DirectBidNAV - 1) * 100,
		DirectPremiumVsAskPct:  (source.MarketPrice/source.DirectAskNAV - 1) * 100,
		BridgePremiumVsBidPct:  (source.MarketPrice/source.BridgeBidNAV - 1) * 100,
		BridgePremiumVsAskPct:  (source.MarketPrice/source.BridgeAskNAV - 1) * 100,
		BaseNAVDate:            audit.BaseNAVDate,
		BaseNAV:                audit.BaseNAV,
		InvestmentRatio:        audit.InvestmentRatio,
		StaticRatio:            audit.StaticRatio,
		AnchorPrice:            audit.BaseAnchorPrice,
		BaseFX:                 audit.BaseFX,
		CurrentFX:              audit.CurrentFX,
		SyntheticINDABid:       audit.SyntheticINDABid,
		SyntheticINDAAsk:       audit.SyntheticINDAAsk,
		NiftyBid:               audit.CurrentNiftyBid,
		NiftyAsk:               audit.CurrentNiftyAsk,
		NiftyContract:          audit.CurrentNiftyContract,
		NiftyObservedAt:        audit.CurrentNiftyObservedAt,
		INDAReferenceBid:       audit.ReferenceINDABid,
		INDAReferenceAsk:       audit.ReferenceINDAAsk,
		INDAReferenceContract:  audit.ReferenceINDAContract,
		NiftyReferenceBid:      audit.ReferenceNiftyBid,
		NiftyReferenceAsk:      audit.ReferenceNiftyAsk,
		NiftyReferenceContract: audit.ReferenceNiftyContract,
		ReferenceAt:            audit.ReferenceAt,
		Beta:                   audit.Beta,
		SelectionVersion:       audit.ContractSelectionVersion,
		RollAdjustment:         audit.RollAdjustment,
	}
	if source.OfficialNAV != nil && finitePositive(*source.OfficialNAV) {
		officialPremium := (source.MarketPrice/(*source.OfficialNAV) - 1) * 100
		row.OfficialPremiumPct = &officialPremium
		directBidError := (row.DirectPremiumVsBidPct - officialPremium) * 100
		directAskError := (row.DirectPremiumVsAskPct - officialPremium) * 100
		bridgeBidError := (row.BridgePremiumVsBidPct - officialPremium) * 100
		bridgeAskError := (row.BridgePremiumVsAskPct - officialPremium) * 100
		pairedDelta := (math.Abs(bridgeBidError)+math.Abs(bridgeAskError))/2 -
			(math.Abs(directBidError)+math.Abs(directAskError))/2
		bridgeCloser := pairedDelta < 0
		containsOfficial := *source.OfficialNAV >= source.BridgeBidNAV && *source.OfficialNAV <= source.BridgeAskNAV
		row.DirectErrorVsBidBPS = &directBidError
		row.DirectErrorVsAskBPS = &directAskError
		row.BridgeErrorVsBidBPS = &bridgeBidError
		row.BridgeErrorVsAskBPS = &bridgeAskError
		row.PairedAbsErrorDeltaBPS = &pairedDelta
		row.BridgeCloser = &bridgeCloser
		row.BridgeContainsOfficial = &containsOfficial
	}
	return row
}

type indiaNiftyBridgeDailyMetrics struct {
	minuteCount       int
	directBidAbsSum   float64
	directAskAbsSum   float64
	bridgeBidAbsSum   float64
	bridgeAskAbsSum   float64
	pairedDeltaSum    float64
	bridgeWins        int
	bridgeContainsNAV int
}

func summarizeIndiaNiftyBridgeReview(rows []IndiaNiftyBridgeReviewRow) IndiaNiftyBridgeReviewSummary {
	summary := IndiaNiftyBridgeReviewSummary{
		MinuteSamples: len(rows),
	}
	allDays := make(map[string]struct{})
	ordinaryDays := make(map[string]struct{})
	calendarRollDays := make(map[string]struct{})
	crossContractDays := make(map[string]struct{})
	daily := make(map[string]*indiaNiftyBridgeDailyMetrics)
	for _, row := range rows {
		allDays[row.TradingDay] = struct{}{}
		switch row.State {
		case IndiaNiftyBridgeStatusCalendarRollDaySameContract:
			calendarRollDays[row.TradingDay] = struct{}{}
		case IndiaNiftyBridgeStatusCrossContractAdjusted:
			crossContractDays[row.TradingDay] = struct{}{}
		default:
			ordinaryDays[row.TradingDay] = struct{}{}
		}
		if row.DirectErrorVsBidBPS == nil || row.DirectErrorVsAskBPS == nil ||
			row.BridgeErrorVsBidBPS == nil || row.BridgeErrorVsAskBPS == nil ||
			row.PairedAbsErrorDeltaBPS == nil || row.BridgeCloser == nil || row.BridgeContainsOfficial == nil {
			continue
		}
		metrics := daily[row.TradingDay]
		if metrics == nil {
			metrics = &indiaNiftyBridgeDailyMetrics{}
			daily[row.TradingDay] = metrics
		}
		metrics.minuteCount++
		metrics.directBidAbsSum += math.Abs(*row.DirectErrorVsBidBPS)
		metrics.directAskAbsSum += math.Abs(*row.DirectErrorVsAskBPS)
		metrics.bridgeBidAbsSum += math.Abs(*row.BridgeErrorVsBidBPS)
		metrics.bridgeAskAbsSum += math.Abs(*row.BridgeErrorVsAskBPS)
		metrics.pairedDeltaSum += *row.PairedAbsErrorDeltaBPS
		if *row.BridgeCloser {
			metrics.bridgeWins++
		}
		if *row.BridgeContainsOfficial {
			metrics.bridgeContainsNAV++
		}
		summary.PairedSamples++
	}
	summary.TradingDays = len(allDays)
	summary.OrdinaryDays = len(ordinaryDays)
	summary.CalendarRollDays = len(calendarRollDays)
	summary.CrossContractAdjustedDays = len(crossContractDays)
	if len(daily) == 0 {
		return summary
	}

	var directBid, directAsk, bridgeBid, bridgeAsk, pairedDelta, winRate, coverage float64
	for _, metrics := range daily {
		if metrics.minuteCount <= 0 {
			continue
		}
		count := float64(metrics.minuteCount)
		directBid += metrics.directBidAbsSum / count
		directAsk += metrics.directAskAbsSum / count
		bridgeBid += metrics.bridgeBidAbsSum / count
		bridgeAsk += metrics.bridgeAskAbsSum / count
		pairedDelta += metrics.pairedDeltaSum / count
		winRate += float64(metrics.bridgeWins) / count * 100
		coverage += float64(metrics.bridgeContainsNAV) / count * 100
	}
	count := float64(len(daily))
	directBid /= count
	directAsk /= count
	bridgeBid /= count
	bridgeAsk /= count
	pairedDelta /= count
	winRate /= count
	coverage /= count
	summary.DirectBidPremiumMAEBPS = floatPointer(directBid)
	summary.DirectAskPremiumMAEBPS = floatPointer(directAsk)
	summary.BridgeBidPremiumMAEBPS = floatPointer(bridgeBid)
	summary.BridgeAskPremiumMAEBPS = floatPointer(bridgeAsk)
	summary.PairedMAEDeltaBPS = floatPointer(pairedDelta)
	summary.BridgeWinRatePct = floatPointer(winRate)
	summary.BridgeIntervalCoveragePct = floatPointer(coverage)
	return summary
}

func floatPointer(value float64) *float64 { return &value }
