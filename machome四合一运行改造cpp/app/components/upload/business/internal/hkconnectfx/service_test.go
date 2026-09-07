package hkconnectfx

import (
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestScheduledSnapshotOmitsZeroFXTimesAndUsesEmptyMessagesArray(t *testing.T) {
	now := localTime(t, "2026-08-21 04:00:00")
	service := NewService(Options{DataDir: t.TempDir(), Now: func() time.Time { return now }})
	payload, err := json.Marshal(service.Snapshot())
	if err != nil {
		t.Fatal(err)
	}
	text := string(payload)
	if strings.Contains(text, "0001-01-01") || !strings.Contains(text, `"messages":[]`) {
		t.Fatalf("unexpected scheduled JSON: %s", text)
	}
}

func TestHKConnectLiveSessionsExcludeLunchAndOutsideHours(t *testing.T) {
	tests := map[string]struct {
		time string
		want bool
	}{
		"before morning open": {time: "09:29", want: false},
		"morning open":        {time: "09:30", want: true},
		"morning close":       {time: "11:30", want: true},
		"lunch":               {time: "12:00", want: false},
		"before afternoon":    {time: "12:59", want: false},
		"afternoon open":      {time: "13:00", want: true},
		"market close":        {time: "16:00", want: true},
		"after close":         {time: "16:01", want: false},
	}
	for name, test := range tests {
		t.Run(name, func(t *testing.T) {
			parsed, err := time.Parse("15:04", test.time)
			if err != nil {
				t.Fatal(err)
			}
			minute := parsed.Hour()*60 + parsed.Minute()
			if got := isHKConnectLiveSessionMinute(minute); got != test.want {
				t.Fatalf("isHKConnectLiveSessionMinute(%s)=%v want %v", test.time, got, test.want)
			}
		})
	}
}

func TestStepSkipsWeekendAndLunchNetworkWork(t *testing.T) {
	service := NewService(Options{DataDir: t.TempDir(), HTTPClient: &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		t.Fatalf("unexpected out-of-session request: %s", request.URL)
		return nil, nil
	})}})
	for _, now := range []time.Time{
		localTime(t, "2026-08-22 09:05:00"),
		localTime(t, "2026-08-22 21:30:00"),
		localTime(t, "2026-08-21 16:01:00"),
	} {
		service.step(context.Background(), now)
	}
}

func TestMorningBootstrapDisablesRestOfKnownClosedTradingDay(t *testing.T) {
	requests := 0
	service := NewService(Options{DataDir: t.TempDir(), HTTPClient: &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Host == "www.chinamoney.com.cn" {
			return jsonResponse(`{"head":{"rep_code":"200"},"data":{"message":""},"records":[]}`), nil
		}
		requests++
		if request.URL.Host == "www.szse.cn" {
			return jsonResponse(`[{"metadata":{"tabkey":"tab1"},"data":[]},{"metadata":{"tabkey":"tab2"},"data":[]}]`), nil
		}
		switch request.URL.Query().Get("sqlId") {
		case referenceSQLID, settlementSQLID:
			return jsonResponse(`{"actionErrors":[],"result":[]}`), nil
		default:
			t.Fatalf("unexpected morning bootstrap request: %s", request.URL)
			return nil, nil
		}
	})}})
	bootstrap := localTime(t, "2026-08-24 09:05:00")
	service.step(context.Background(), bootstrap)
	if requests != 3 || !service.isKnownClosedConnectTradingDay("2026-08-24") {
		t.Fatalf("bootstrap should determine a closed connect day once: requests=%d closed=%v", requests, service.isKnownClosedConnectTradingDay("2026-08-24"))
	}
	for _, now := range []time.Time{
		localTime(t, "2026-08-24 09:30:00"),
		localTime(t, "2026-08-24 13:00:00"),
		localTime(t, "2026-08-24 21:30:00"),
	} {
		service.step(context.Background(), now)
	}
	if requests != 3 {
		t.Fatalf("known closed day must not continue network polling: requests=%d", requests)
	}
}

func localTime(t *testing.T, value string) time.Time {
	t.Helper()
	parsed, err := time.ParseInLocation("2006-01-02 15:04:05", value, shanghai)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func seededService(t *testing.T, now time.Time, buy, sell float64) *Service {
	t.Helper()
	service := NewService(Options{DataDir: t.TempDir(), Now: func() time.Time { return now }})
	service.tradeDate = now.Format("2006-01-02")
	service.flow = &Flow{Market: marketShanghai, TradeDate: service.tradeDate, BuyAmountHKD100: buy, SellAmountHKD100: sell, TotalAmountHKD100: buy + sell, PublishedAt: now, FetchedAt: now, FirstSeenAt: now, LastChangedAt: now, Samples: 1, Source: EastmoneySouthboundFeed}
	service.shenzhenFlow = &Flow{Market: marketShenzhen, TradeDate: service.tradeDate, BuyAmountHKD100: 120, SellAmountHKD100: 80, TotalAmountHKD100: 200, PublishedAt: now, FetchedAt: now, FirstSeenAt: now, LastChangedAt: now, Samples: 1, Source: EastmoneySouthboundFeed}
	service.reference = &ReferenceRate{ValidDate: service.tradeDate, BuyRate: .8341, SellRate: .8857, MidRate: .8599, FetchedAt: now, Source: SSERatesPage}
	service.previousSettlement = &SettlementRate{ValidDate: "2026-08-20", BuyRate: .8591, SellRate: .8595, FetchedAt: now, Source: SSERatesPage}
	service.shenzhenPreviousSettlement = &SettlementRate{ValidDate: "2026-08-20", BuyRate: .85911, SellRate: .85969, FetchedAt: now, Source: SZSERatesPage}
	bid, ask := .85720, .85722
	observed := now
	received := now
	service.fx = FXQuote{Pair: "HKD/CNY", Bid: &bid, Ask: &ask, Healthy: true, ObservedAt: &observed, ReceivedAt: &received, Source: "CFETS_CHINAMONEY"}
	return service
}

func TestSnapshotUsesDirectionalQuoteAndDecisionWindow(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	service := seededService(t, now, 300, 200)
	snapshot := service.Snapshot()
	if snapshot.Status != "live" || !snapshot.Actionable {
		t.Fatalf("status=%s actionable=%v", snapshot.Status, snapshot.Actionable)
	}
	if snapshot.Estimate == nil {
		t.Fatal("estimate is nil")
	}
	q := .2
	selected := .85722
	market := (.85720 + .85722) / 2
	d := q * (selected + positiveResidual - .8599)
	if math.Abs(snapshot.Estimate.NetRatio-q) > 1e-12 || math.Abs(snapshot.Estimate.SelectedHKDCNY-selected) > 1e-12 {
		t.Fatalf("unexpected q/quote: %+v", snapshot.Estimate)
	}
	if math.Abs(snapshot.Estimate.PredictedSellSettlement-(.8599+d)) > 1e-12 || math.Abs(snapshot.Estimate.PredictedBuySettlement-(.8599-d)) > 1e-12 {
		t.Fatalf("unexpected predictions: %+v", snapshot.Estimate)
	}
	if math.Abs(snapshot.Estimate.MarketHKDCNY-market) > 1e-12 {
		t.Fatalf("CFETS HKD/CNY=%v want=%v", snapshot.Estimate.MarketHKDCNY, market)
	}
	if snapshot.Estimate.BuyLiveVsEstimate == nil || snapshot.Estimate.SellLiveVsEstimate == nil {
		t.Fatalf("live versus estimate fields are absent: %+v", snapshot.Estimate)
	}
	if math.Abs(*snapshot.Estimate.BuyLiveVsEstimate-(market/snapshot.Estimate.PredictedBuySettlement-1)) > 1e-12 ||
		math.Abs(*snapshot.Estimate.SellLiveVsEstimate-(market/snapshot.Estimate.PredictedSellSettlement-1)) > 1e-12 {
		t.Fatalf("unexpected live-versus-estimate fields: %+v", snapshot.Estimate)
	}
	if snapshot.Model.ResidualSamples != positiveResidualSamples || snapshot.Model.Residual != positiveResidual {
		t.Fatalf("unexpected positive residual: %+v", snapshot.Model)
	}
}

func TestEastmoneyMinuteTimestampAllowsTwoMinutePublishingLag(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:30")
	service := seededService(t, now, 300, 200)
	service.flow.PublishedAt = now.Add(-120 * time.Second)
	if snapshot := service.Snapshot(); snapshot.Status != "live" || !snapshot.Actionable {
		t.Fatalf("normal HKEX publishing lag should remain live: %+v", snapshot)
	}
	service.flow.PublishedAt = now.Add(-271 * time.Second)
	if snapshot := service.Snapshot(); snapshot.Status != "stale_eastmoney" || snapshot.Actionable {
		t.Fatalf("stale Eastmoney timestamp must fail closed: %+v", snapshot)
	}
}

func TestNetSellDirectionFailsClosedOnInsufficientResidualSamples(t *testing.T) {
	now := localTime(t, "2026-08-21 10:00:00")
	service := seededService(t, now, 100, 300)
	snapshot := service.Snapshot()
	if snapshot.Status != "reference_only" || snapshot.Actionable {
		t.Fatalf("status=%s actionable=%v", snapshot.Status, snapshot.Actionable)
	}
	if snapshot.Model.ResidualSamples != negativeResidualSamples || snapshot.Model.Residual != 0 {
		t.Fatalf("insufficient negative residual must be zero: %+v", snapshot.Model)
	}
	want := .85720
	if snapshot.Estimate == nil || math.Abs(snapshot.Estimate.SelectedHKDCNY-want) > 1e-12 {
		t.Fatalf("net sell should use HKD/CNY bid: %+v", snapshot.Estimate)
	}
}

func TestShenzhenEstimateUsesItsOwnFlowAndUncalibratedResidual(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	service := seededService(t, now, 300, 200)
	service.shenzhenFlow.BuyAmountHKD100 = 80
	service.shenzhenFlow.SellAmountHKD100 = 120
	service.shenzhenFlow.TotalAmountHKD100 = 200
	snapshot := service.Snapshot()
	if snapshot.ShenzhenStatus != "live" || !snapshot.ShenzhenActionable || snapshot.ShenzhenEstimate == nil {
		t.Fatalf("Shenzhen estimate is not live: %+v", snapshot)
	}
	if snapshot.ShenzhenEstimate.NetRatio != -.2 {
		t.Fatalf("Shenzhen q=%v, want -0.2", snapshot.ShenzhenEstimate.NetRatio)
	}
	if snapshot.ShenzhenModel.CalibrationStatus != "pending_shenzhen" || snapshot.ShenzhenModel.Residual != 0 || snapshot.ShenzhenModel.ResidualSamples != 0 {
		t.Fatalf("Shanghai calibration leaked into Shenzhen: %+v", snapshot.ShenzhenModel)
	}
	if snapshot.Estimate == nil || snapshot.Estimate.NetRatio != .2 {
		t.Fatalf("Shanghai estimate should stay independent: %+v", snapshot.Estimate)
	}
}

func TestCFETSFailureIsRecordedWithoutDiscardingLastQuote(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	service := seededService(t, now, 300, 200)
	service.fx.Healthy = false
	service.fx.Error = "upstream timeout"
	snapshot := service.Snapshot()
	if snapshot.Status != "cfets_unavailable" || snapshot.Estimate == nil || snapshot.FX.Bid == nil || snapshot.FX.Error != "upstream timeout" {
		t.Fatalf("CFETS failure was not retained safely: %+v", snapshot)
	}
}

func TestProcessedMinuteCSVIsDeduplicated(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	service := seededService(t, now, 300, 200)
	service.persistMinute(now)
	service.persistMinute(now.Add(10 * time.Second))
	history, err := service.History("2026-08-21", marketShanghai)
	if err != nil {
		t.Fatal(err)
	}
	if len(history.Rows) != 1 || history.Rows[0].PredictedSellSettlement == nil {
		t.Fatalf("history=%+v", history)
	}
	shenzhenHistory, err := service.History("2026-08-21", marketShenzhen)
	if err != nil {
		t.Fatal(err)
	}
	if shenzhenHistory.Market != marketShenzhen || len(shenzhenHistory.Rows) != 1 || shenzhenHistory.Rows[0].PredictedSellSettlement == nil {
		t.Fatalf("Shenzhen history=%+v", shenzhenHistory)
	}
}

func TestDailyHistoryUsesPriorActualRatesAndReservesCurrentDayForPrediction(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	service := seededService(t, now, 300, 200)
	service.settlementHistory = []SettlementRate{
		{ValidDate: "2026-08-12", BuyRate: .8510, SellRate: .8514},
		{ValidDate: "2026-08-13", BuyRate: .8520, SellRate: .8524},
		{ValidDate: "2026-08-14", BuyRate: .8530, SellRate: .8534},
		{ValidDate: "2026-08-17", BuyRate: .8540, SellRate: .8544},
		{ValidDate: "2026-08-18", BuyRate: .8550, SellRate: .8554},
		{ValidDate: "2026-08-19", BuyRate: .8560, SellRate: .8564},
		{ValidDate: "2026-08-20", BuyRate: .8570, SellRate: .8574},
		{ValidDate: "2026-08-21", BuyRate: .8580, SellRate: .8584},
	}
	service.shenzhenSettlementHistory = []SettlementRate{
		{ValidDate: "2026-08-12", BuyRate: .8511, SellRate: .8515},
		{ValidDate: "2026-08-13", BuyRate: .8521, SellRate: .8525},
		{ValidDate: "2026-08-14", BuyRate: .8531, SellRate: .8535},
		{ValidDate: "2026-08-17", BuyRate: .8541, SellRate: .8545},
		{ValidDate: "2026-08-18", BuyRate: .8551, SellRate: .8555},
		{ValidDate: "2026-08-19", BuyRate: .8561, SellRate: .8565},
		{ValidDate: "2026-08-20", BuyRate: .8571, SellRate: .8575},
		{ValidDate: "2026-08-21", BuyRate: .8581, SellRate: .8585},
	}
	history, err := service.DailyHistory(7, marketShanghai)
	if err != nil {
		t.Fatal(err)
	}
	if len(history.Rows) != 6 || history.Rows[0].TradeDate != "2026-08-13" || history.Rows[5].TradeDate != "2026-08-20" {
		t.Fatalf("unexpected daily history: %+v", history)
	}
	if history.Rows[0].ActualBuySettlement != .8520 || history.Rows[5].ActualSellSettlement != .8574 {
		t.Fatalf("unexpected actual rates: %+v", history.Rows)
	}
	shenzhenHistory, err := service.DailyHistory(7, marketShenzhen)
	if err != nil {
		t.Fatal(err)
	}
	if shenzhenHistory.Market != marketShenzhen || len(shenzhenHistory.Rows) != 6 || shenzhenHistory.Rows[0].ActualBuySettlement != .8521 || shenzhenHistory.Rows[5].ActualSellSettlement != .8575 {
		t.Fatalf("unexpected Shenzhen daily history: %+v", shenzhenHistory)
	}
	if _, err := service.DailyHistory(1, marketShanghai); err == nil {
		t.Fatal("days=1 should fail validation")
	}
	if _, err := service.DailyHistory(maxDailyHistoryDays, marketShanghai); err != nil {
		t.Fatalf("max daily history days should be supported: %v", err)
	}
	if _, err := service.DailyHistory(maxDailyHistoryDays+1, marketShanghai); err == nil {
		t.Fatal("days above the daily history limit should fail validation")
	}
	if _, err := service.DailyHistory(7, "other"); err == nil {
		t.Fatal("invalid market should fail validation")
	}
}

func TestNewServiceLoadsAndMergesPersistedSettlementHistory(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	dataDir := t.TempDir()
	historyDir := filepath.Join(dataDir, settlementHistoryDir)
	if err := os.MkdirAll(historyDir, 0o755); err != nil {
		t.Fatal(err)
	}
	shanghaiCSV := strings.Join([]string{
		"适用日期,买入结算汇兑比率,卖出结算汇兑比率,货币种类",
		"2020-01-02,0.89366,0.89374,HKD",
		"2026-08-20,0.85922,0.85958,HKD",
	}, "\n")
	shenzhenCSV := strings.Join([]string{
		"适用日期,买入结算汇兑比率,卖出结算汇兑比率,货币种类",
		"2020-01-02,0.89365,0.89375,HKD",
		"2026-08-20,0.85920,0.85960,HKD",
	}, "\n")
	if err := os.WriteFile(filepath.Join(historyDir, "shanghai.csv"), []byte(shanghaiCSV), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(historyDir, "shenzhen.csv"), []byte(shenzhenCSV), 0o644); err != nil {
		t.Fatal(err)
	}

	service := NewService(Options{DataDir: dataDir, Now: func() time.Time { return now }})
	shanghai, err := service.DailyHistory(maxDailyHistoryDays, marketShanghai)
	if err != nil {
		t.Fatal(err)
	}
	if len(shanghai.Rows) != 2 || shanghai.Rows[0].TradeDate != "2020-01-02" || shanghai.Rows[1].ActualSellSettlement != .85958 {
		t.Fatalf("unexpected persisted Shanghai history: %+v", shanghai.Rows)
	}
	shenzhen, err := service.DailyHistory(maxDailyHistoryDays, marketShenzhen)
	if err != nil {
		t.Fatal(err)
	}
	if len(shenzhen.Rows) != 2 || shenzhen.Rows[0].ActualBuySettlement != .89365 || shenzhen.Rows[1].ActualSellSettlement != .85960 {
		t.Fatalf("unexpected persisted Shenzhen history: %+v", shenzhen.Rows)
	}

	merged := mergeSettlementHistories(service.settlementHistory, []SettlementRate{{ValidDate: "2026-08-20", BuyRate: .86, SellRate: .861, Source: SSERatesPage}})
	if len(merged) != 2 || merged[1].BuyRate != .86 || merged[1].SellRate != .861 {
		t.Fatalf("fresh official history should override matching static date: %+v", merged)
	}
}

func TestDailyHistoryIncludesCFETS1600Anchors(t *testing.T) {
	now := localTime(t, "2026-08-21 14:35:00")
	service := seededService(t, now, 300, 200)
	service.cfetsAnchors["2026-08-19"] = CFETSAnchor{TradeDate: "2026-08-19", HKDCNY1600: .8561, FetchedAt: now, Source: cfetsAnchorSource}
	service.cfetsAnchors["2026-08-20"] = CFETSAnchor{TradeDate: "2026-08-20", HKDCNY1600: .8571, FetchedAt: now, Source: cfetsAnchorSource}
	service.settlementHistory = []SettlementRate{
		{ValidDate: "2026-08-18", BuyRate: .8540, SellRate: .8544},
		{ValidDate: "2026-08-19", BuyRate: .8550, SellRate: .8554},
		{ValidDate: "2026-08-20", BuyRate: .8560, SellRate: .8564},
		{ValidDate: "2026-08-21", BuyRate: .8570, SellRate: .8574},
	}
	history, err := service.DailyHistory(3, marketShanghai)
	if err != nil {
		t.Fatal(err)
	}
	if len(history.Rows) != 2 {
		t.Fatalf("row count=%d", len(history.Rows))
	}
	first, last := history.Rows[0], history.Rows[1]
	if first.CFETSHKDCNY1600 == nil || *first.CFETSHKDCNY1600 != .856100 {
		t.Fatalf("16:00 CFETS anchor missing from first row: %+v", first)
	}
	if last.CFETSHKDCNY1600 == nil || *last.CFETSHKDCNY1600 != .857100 {
		t.Fatalf("CFETS anchor must be retained: %+v", last)
	}
}

func TestDailyHistoryIncludesCentralParitySeries(t *testing.T) {
	now := localTime(t, "2026-09-02 14:35:00")
	service := seededService(t, now, 300, 200)
	service.settlementHistory = []SettlementRate{
		{ValidDate: "2026-09-01", BuyRate: .85723, SellRate: .85737},
		{ValidDate: "2026-09-02", BuyRate: .85758, SellRate: .85762},
	}
	service.centralParities["2026-09-01"] = CentralParityRate{
		Pair: "HKD/CNY", TradeDate: "2026-09-01", Rate: .86498,
		FetchedAt: now, Source: cfetsCentralParitySource,
	}
	history, err := service.DailyHistory(2, marketShanghai)
	if err != nil {
		t.Fatal(err)
	}
	if len(history.Rows) != 1 || history.Rows[0].HKDCNYCentralParity == nil || *history.Rows[0].HKDCNYCentralParity != .86498 {
		t.Fatalf("central parity missing from daily history: %+v", history.Rows)
	}
}

func TestCFETSAnchorPersistsAndOfficialHistoryOverridesLiveFreeze(t *testing.T) {
	now := localTime(t, "2026-08-21 16:00:10")
	dataDir := t.TempDir()
	service := NewService(Options{DataDir: dataDir, Now: func() time.Time { return now }})
	if err := service.storeCFETSAnchor(CFETSAnchor{TradeDate: "2026-08-21", HKDCNY1600: .85720, FetchedAt: now, Source: "CFETS_CHINAMONEY_LIVE_FREEZE"}); err != nil {
		t.Fatal(err)
	}
	if err := service.storeCFETSAnchor(CFETSAnchor{TradeDate: "2026-08-21", HKDCNY1600: .85715, FetchedAt: now.AddDate(0, 0, 1), Source: cfetsAnchorSource}); err != nil {
		t.Fatal(err)
	}
	restarted := NewService(Options{DataDir: dataDir, Now: func() time.Time { return now.AddDate(0, 0, 1) }})
	anchor := restarted.cfetsAnchors["2026-08-21"]
	if math.Abs(anchor.HKDCNY1600-.85715) > 1e-12 || anchor.Source != cfetsAnchorSource {
		t.Fatalf("persisted 16:00 anchor=%+v", anchor)
	}
}

func TestProcessedMinuteCSVPreservesLegacyHeaderDuringTradingDay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "minute.csv")
	legacyRecord := strings.Join([]string{
		"2026-08-21T14:34:00+08:00", "2026-08-21", "live", "true", "0.859900000000",
		"300.000000000000", "200.000000000000", "500.000000000000", "0.200000000000",
		"1.163400000000", "1.163500000000", "0.859549252192", "0.859929000000", "0.859871000000",
		"", "", "2026-08-21T14:34:00+08:00", "2026-08-21T14:34:00+08:00", "2026-08-21T14:34:00+08:00",
	}, ",")
	if err := os.WriteFile(path, []byte(strings.Join([]string{strings.Join(legacyMinuteFields, ","), legacyRecord}, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	live := .85721
	if err := appendMinuteCSV(path, MinutePoint{Timestamp: "2026-08-21T14:35:00+08:00", TradeDate: "2026-08-21", Status: "live", MarketHKDCNY: &live}); err != nil {
		t.Fatal(err)
	}
	header, err := readCSVHeader(path)
	if err != nil {
		t.Fatal(err)
	}
	if !sameCSVFields(header, legacyMinuteFields) {
		t.Fatalf("legacy header changed: %v", header)
	}
	rows, err := readMinuteCSV(path)
	if err != nil || len(rows) != 2 || rows[1].MarketHKDCNY != nil {
		t.Fatalf("legacy file should remain readable without new fields: rows=%+v err=%v", rows, err)
	}
}

func TestFinalCloseAuditFreezesEstimateAndLaterBackfillsOfficialSettlement(t *testing.T) {
	now := localTime(t, "2026-08-21 16:00:00")
	service := seededService(t, now, 300, 200)
	service.shenzhenFlow.BuyAmountHKD100 = 80
	service.shenzhenFlow.SellAmountHKD100 = 120
	service.shenzhenFlow.TotalAmountHKD100 = 200
	service.persistMinute(now)
	shanghai, err := service.CloseAuditHistory(10, marketShanghai)
	if err != nil {
		t.Fatal(err)
	}
	if shanghai.SchemaVersion != CloseAuditSchemaVersion || len(shanghai.Rows) != 1 {
		t.Fatalf("unexpected Shanghai audit: %+v", shanghai)
	}
	before := shanghai.Rows[0]
	if before.CaptureKind != "final_close" || before.CapturedAt.Format("15:04") != "16:00" || before.ActualBuySettlement != nil || before.ModelVersion != ModelVersion || before.CFETSHKDCNY1600 == nil {
		t.Fatalf("unexpected frozen audit checkpoint: %+v", before)
	}
	shenzhen, err := service.CloseAuditHistory(10, marketShenzhen)
	if err != nil {
		t.Fatal(err)
	}
	if len(shenzhen.Rows) != 1 || shenzhen.Rows[0].NetRatio != -.2 || shenzhen.Rows[0].CalibrationStatus != "pending_shenzhen" {
		t.Fatalf("unexpected Shenzhen audit: %+v", shenzhen)
	}

	actual := SettlementRate{ValidDate: "2026-08-21", BuyRate: .8589, SellRate: .8593, FetchedAt: now.Add(2 * time.Hour), Source: SSERatesPage}
	if err := service.reconcileCloseAudits(marketShanghai, []SettlementRate{actual}); err != nil {
		t.Fatal(err)
	}
	after, err := service.CloseAuditHistory(10, marketShanghai)
	if err != nil {
		t.Fatal(err)
	}
	if len(after.Rows) != 1 || after.Rows[0].ActualBuySettlement == nil || after.Rows[0].MeanAbsoluteErrorBP == nil || after.Rows[0].PredictedBuySettlement != before.PredictedBuySettlement || after.Rows[0].PredictedSellSettlement != before.PredictedSellSettlement {
		t.Fatalf("official backfill should not change frozen estimate: before=%+v after=%+v", before, after.Rows)
	}
}

func TestStepBackfillsOfficialSettlementAfterCloseAndReconcilesAudits(t *testing.T) {
	closeAt := localTime(t, "2026-08-21 16:00:00")
	backfillAt := localTime(t, "2026-08-21 21:30:00")
	service := seededService(t, closeAt, 300, 200)
	service.persistMinute(closeAt)
	service.now = func() time.Time { return backfillAt }
	requests := 0
	service.httpClient = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		requests++
		if request.URL.Host == "www.szse.cn" {
			return jsonResponse(`[{"metadata":{"tabkey":"tab1"},"data":[]},{"metadata":{"tabkey":"tab2"},"data":[{"sxrq":"2026-08-21","mrhl":"0.85720","mchl":"0.85740","yhbzl":"HKD"}]}]`), nil
		}
		if request.URL.Query().Get("sqlId") == settlementSQLID {
			return jsonResponse(`{"actionErrors":[],"result":[{"validDate":"20260821","updateDate":"20260821","buyPrice":"0.85710","sellPrice":"0.85750"}]}`), nil
		}
		t.Fatalf("unexpected official settlement request: %s", request.URL)
		return nil, nil
	})}

	service.step(context.Background(), backfillAt.Add(-time.Minute))
	if requests != 0 {
		t.Fatalf("official settlement should not be fetched before the 21:30 fixed time: requests=%d", requests)
	}
	service.step(context.Background(), backfillAt)
	snapshot := service.Snapshot()
	if snapshot.ActualSettlement == nil || snapshot.ActualSettlement.ValidDate != "2026-08-21" || snapshot.ActualSettlement.BuyRate != .8571 || snapshot.ShenzhenActualSettlement == nil || snapshot.ShenzhenActualSettlement.SellRate != .8574 {
		t.Fatalf("late official settlement was not applied: %+v", snapshot)
	}
	if service.lastOfficialBackfillMinute != "2026-08-21T21:30" {
		t.Fatalf("fixed-time backfill was not recorded: %q", service.lastOfficialBackfillMinute)
	}
	for _, market := range []string{marketShanghai, marketShenzhen} {
		audit, err := service.CloseAuditHistory(10, market)
		if err != nil || len(audit.Rows) != 1 || audit.Rows[0].ActualBuySettlement == nil || audit.Rows[0].MeanAbsoluteErrorBP == nil {
			t.Fatalf("%s close audit not reconciled: rows=%+v err=%v", market, audit.Rows, err)
		}
	}

	service.step(context.Background(), backfillAt.Add(time.Minute))
	if service.lastOfficialBackfillMinute != "2026-08-21T21:30" {
		t.Fatalf("published settlements should suppress further backfill: %q", service.lastOfficialBackfillMinute)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) { return fn(request) }

func jsonResponse(body string) *http.Response {
	return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body))}
}

func TestParseEastmoneySouthboundSeparatesShanghaiAndShenzhen(t *testing.T) {
	fetchedAt := localTime(t, "2026-08-21 09:46:00")
	shanghaiFlow, shenzhenFlow, err := parseEastmoneySouthbound(eastmoneySouthboundResponse{RC: 0, Data: struct {
		N2SDate string
		N2S     []string
	}{N2SDate: "2026-08-21", N2S: []string{
		"09:44,-100000,1200000,20000,1100000,-80000,700000,680000,1900000,1780000",
		"09:45,-120000,1234567,15000,1114567,-105000,765432,750432,2000000,1864999",
	}}}, fetchedAt)
	if err != nil {
		t.Fatal(err)
	}
	if shanghaiFlow.Market != marketShanghai || shanghaiFlow.BuyAmountHKD100 != 123.4567 || shanghaiFlow.SellAmountHKD100 != 111.4567 {
		t.Fatalf("unexpected Shanghai flow: %+v", shanghaiFlow)
	}
	if shenzhenFlow.Market != marketShenzhen || shenzhenFlow.BuyAmountHKD100 != 76.5432 || shenzhenFlow.SellAmountHKD100 != 75.0432 {
		t.Fatalf("unexpected Shenzhen flow: %+v", shenzhenFlow)
	}
	if got := shanghaiFlow.PublishedAt.Format("2006-01-02 15:04"); got != "2026-08-21 09:45" {
		t.Fatalf("published_at=%s", got)
	}
}

func TestParseHKEXAmountSupportsPublishedUnits(t *testing.T) {
	tests := map[string]float64{"HK$100 Mil": 1, "HK$1.5 Bil": 15, "HK$200,000,000": 2}
	for input, want := range tests {
		got, err := parseHKEXAmountHKD100(input)
		if err != nil || math.Abs(got-want) > 1e-12 {
			t.Fatalf("parseHKEXAmountHKD100(%q)=%v, %v; want %v", input, got, err, want)
		}
	}
}

func TestPollNowLoadsEastmoneyFlowsAndSSERates(t *testing.T) {
	now := localTime(t, "2026-08-21 09:05:00")
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Host == "push2.eastmoney.com" {
			return jsonResponse(`{"rc":0,"data":{"n2sDate":"2026-08-21","n2s":["09:05,-400000,1200000,100000,800000,-300000,700000,600000,1900000,1400000"]}}`), nil
		}
		if request.URL.Host == "www.szse.cn" {
			if request.URL.Query().Get("CATALOGID") != szseSettlementCatalogID || request.URL.Query().Get("TABKEY") != szseSettlementTabKey {
				t.Fatalf("unexpected SZSE URL: %s", request.URL)
			}
			return jsonResponse(`[{"metadata":{"tabkey":"tab1"},"data":[]},{"metadata":{"tabkey":"tab2"},"data":[{"sxrq":"2026-08-20","mrhl":"0.85911","mchl":"0.85969","yhbzl":"HKD"}]}]`), nil
		}
		switch request.URL.Query().Get("sqlId") {
		case referenceSQLID:
			return jsonResponse(`{"actionErrors":[],"result":[{"validDate":"20260821","updateDate":"20260821","buyPrice":"0.83410","sellPrice":"0.88570"}]}`), nil
		case settlementSQLID:
			return jsonResponse(`{"actionErrors":[],"result":[{"validDate":"20260820","buyPrice":"0.85910","sellPrice":"0.85950"}]}`), nil
		default:
			t.Fatalf("unexpected URL: %s", request.URL)
			return nil, nil
		}
	})}
	service := NewService(Options{DataDir: t.TempDir(), HTTPClient: client, Now: func() time.Time { return now }})
	service.PollNow(context.Background(), now)
	snapshot := service.Snapshot()
	if snapshot.Flow == nil || snapshot.Flow.BuyAmountHKD100 != 120 || snapshot.ShenzhenFlow == nil || snapshot.ShenzhenFlow.BuyAmountHKD100 != 70 || snapshot.Reference == nil || snapshot.Reference.MidRate != .8599 {
		t.Fatalf("public inputs not loaded: %+v", snapshot)
	}
	if snapshot.PreviousSettlement == nil || snapshot.PreviousSettlement.ValidDate != "2026-08-20" {
		t.Fatalf("previous settlement not selected: %+v", snapshot.PreviousSettlement)
	}
	if snapshot.ShenzhenPreviousSettlement == nil || snapshot.ShenzhenPreviousSettlement.ValidDate != "2026-08-20" || snapshot.ShenzhenPreviousSettlement.Source != SZSERatesPage {
		t.Fatalf("Shenzhen previous settlement not selected: %+v", snapshot.ShenzhenPreviousSettlement)
	}
	if snapshot.Status != "waiting_cfets" {
		t.Fatalf("status=%s want waiting_cfets", snapshot.Status)
	}
}
