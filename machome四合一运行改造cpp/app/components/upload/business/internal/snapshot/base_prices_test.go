package snapshot

import (
	"context"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

type fakeBasePriceRepo struct {
	data   domain.ValuationData
	prices []domain.DailyPrice
}

func (r *fakeBasePriceRepo) EnsureSymbols(ctx context.Context, symbols []string) error {
	return nil
}

func (r *fakeBasePriceRepo) UpsertLatestQuotes(ctx context.Context, quotes map[string]domain.Quote) error {
	return nil
}

func (r *fakeBasePriceRepo) UpsertDailyPrices(ctx context.Context, prices []domain.DailyPrice) error {
	r.prices = append(r.prices, prices...)
	return nil
}

func (r *fakeBasePriceRepo) LoadLatestQuotes(ctx context.Context) (map[string]domain.Quote, error) {
	return map[string]domain.Quote{}, nil
}

func (r *fakeBasePriceRepo) LoadValuationData(ctx context.Context) (domain.ValuationData, error) {
	return r.data, nil
}

func TestMissingValuationAnchorRequestsFor501018SummerAndWinter(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH501018"] = domain.NetValue{Symbol: "SH501018", Date: "2026-06-01", NAV: 2}

	requests := missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	summer := requestsByFundAnchorDateAndKey(requests, "SH501018", "2026-06-01")
	if summer["jp_close"].TargetBeijingTime != "2026-06-01T14:30:00+08:00" {
		t.Fatalf("summer jp target = %s", summer["jp_close"].TargetBeijingTime)
	}
	if summer["eu_close"].TargetBeijingTime != "2026-06-01T23:30:00+08:00" {
		t.Fatalf("summer eu target = %s", summer["eu_close"].TargetBeijingTime)
	}
	if summer["us_close"].TargetBeijingTime != "2026-06-02T04:00:00+08:00" {
		t.Fatalf("summer us target = %s", summer["us_close"].TargetBeijingTime)
	}

	data = domain.EmptyValuationData()
	data.LatestNetValues["SH501018"] = domain.NetValue{Symbol: "SH501018", Date: "2026-12-01", NAV: 2}
	requests = missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 12, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	winter := requestsByFundAnchorDateAndKey(requests, "SH501018", "2026-12-01")
	if winter["eu_close"].TargetBeijingTime != "2026-12-02T00:30:00+08:00" {
		t.Fatalf("winter eu target = %s", winter["eu_close"].TargetBeijingTime)
	}
	if winter["us_close"].TargetBeijingTime != "2026-12-02T05:00:00+08:00" {
		t.Fatalf("winter us target = %s", winter["us_close"].TargetBeijingTime)
	}
}

func TestMissingValuationAnchorRequestsFor160723UsesCashAdjustedCLAnchors(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ160723"] = domain.NetValue{Symbol: "SZ160723", Date: "2026-06-01", NAV: 2}

	requests := missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	summer := requestsByFundAnchorDateAndKey(requests, "SZ160723", "2026-06-01")
	if summer["hk_close"].TargetBeijingTime != "2026-06-01T16:00:00+08:00" {
		t.Fatalf("summer hk target = %s", summer["hk_close"].TargetBeijingTime)
	}
	if summer["jp_close"].TargetBeijingTime != "2026-06-01T14:30:00+08:00" {
		t.Fatalf("summer jp target = %s", summer["jp_close"].TargetBeijingTime)
	}
	if summer["eu_close"].TargetBeijingTime != "2026-06-01T23:30:00+08:00" {
		t.Fatalf("summer eu target = %s", summer["eu_close"].TargetBeijingTime)
	}
	if summer["us_close"].TargetBeijingTime != "2026-06-02T04:00:00+08:00" {
		t.Fatalf("summer us target = %s", summer["us_close"].TargetBeijingTime)
	}
	if summer["eu_close"].Weight != 0.4956 {
		t.Fatalf("eu weight = %.6f, want 0.4956", summer["eu_close"].Weight)
	}
	if summer["us_close"].Weight != 0.4246 {
		t.Fatalf("us weight = %.6f, want 0.4246", summer["us_close"].Weight)
	}

	data = domain.EmptyValuationData()
	data.LatestNetValues["SZ160723"] = domain.NetValue{Symbol: "SZ160723", Date: "2026-12-01", NAV: 2}
	requests = missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 12, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	winter := requestsByFundAnchorDateAndKey(requests, "SZ160723", "2026-12-01")
	if winter["eu_close"].TargetBeijingTime != "2026-12-02T00:30:00+08:00" {
		t.Fatalf("winter eu target = %s", winter["eu_close"].TargetBeijingTime)
	}
	if winter["us_close"].TargetBeijingTime != "2026-12-02T05:00:00+08:00" {
		t.Fatalf("winter us target = %s", winter["us_close"].TargetBeijingTime)
	}
}

func TestMissingValuationAnchorRequestsFor161129UsesFOFCLAnchors(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ161129"] = domain.NetValue{Symbol: "SZ161129", Date: "2026-06-01", NAV: 2}

	requests := missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	summer := requestsByFundAnchorDateAndKey(requests, "SZ161129", "2026-06-01")
	if summer["hk_close"].TargetBeijingTime != "2026-06-01T16:00:00+08:00" {
		t.Fatalf("summer hk target = %s", summer["hk_close"].TargetBeijingTime)
	}
	if summer["eu_close"].TargetBeijingTime != "2026-06-01T23:30:00+08:00" {
		t.Fatalf("summer eu target = %s", summer["eu_close"].TargetBeijingTime)
	}
	if summer["us_close"].TargetBeijingTime != "2026-06-02T04:00:00+08:00" {
		t.Fatalf("summer us target = %s", summer["us_close"].TargetBeijingTime)
	}
	if summer["hk_close"].Weight != 0.1351 {
		t.Fatalf("hk weight = %.6f, want 0.1351", summer["hk_close"].Weight)
	}
	if summer["eu_close"].Weight != 0.4135 {
		t.Fatalf("eu weight = %.6f, want 0.4135", summer["eu_close"].Weight)
	}
	if summer["us_close"].Weight != 0.4514 {
		t.Fatalf("us weight = %.6f, want 0.4514", summer["us_close"].Weight)
	}

	data = domain.EmptyValuationData()
	data.LatestNetValues["SZ161129"] = domain.NetValue{Symbol: "SZ161129", Date: "2026-12-01", NAV: 2}
	requests = missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 12, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	winter := requestsByFundAnchorDateAndKey(requests, "SZ161129", "2026-12-01")
	if winter["eu_close"].TargetBeijingTime != "2026-12-02T00:30:00+08:00" {
		t.Fatalf("winter eu target = %s", winter["eu_close"].TargetBeijingTime)
	}
	if winter["us_close"].TargetBeijingTime != "2026-12-02T05:00:00+08:00" {
		t.Fatalf("winter us target = %s", winter["us_close"].TargetBeijingTime)
	}
}

func TestMissingValuationAnchorRequestsFor160719UsesGoldAnchors(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ160719"] = domain.NetValue{Symbol: "SZ160719", Date: "2026-06-01", NAV: 2}

	requests := missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	summer := requestsByFundAnchorDateAndKey(requests, "SZ160719", "2026-06-01")
	if summer["ch_close"].TargetBeijingTime != "2026-06-01T23:30:00+08:00" {
		t.Fatalf("summer ch target = %s", summer["ch_close"].TargetBeijingTime)
	}
	if summer["us_close"].TargetBeijingTime != "2026-06-02T04:00:00+08:00" {
		t.Fatalf("summer us target = %s", summer["us_close"].TargetBeijingTime)
	}
	if summer["ch_close"].Weight != 0.4498 {
		t.Fatalf("ch weight = %.6f, want 0.4498", summer["ch_close"].Weight)
	}
	if summer["us_close"].Weight != 0.5502 {
		t.Fatalf("us weight = %.6f, want 0.5502", summer["us_close"].Weight)
	}

	data = domain.EmptyValuationData()
	data.LatestNetValues["SZ160719"] = domain.NetValue{Symbol: "SZ160719", Date: "2026-12-01", NAV: 2}
	requests = missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 12, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	winter := requestsByFundAnchorDateAndKey(requests, "SZ160719", "2026-12-01")
	if winter["ch_close"].TargetBeijingTime != "2026-12-02T00:30:00+08:00" {
		t.Fatalf("winter ch target = %s", winter["ch_close"].TargetBeijingTime)
	}
	if winter["us_close"].TargetBeijingTime != "2026-12-02T05:00:00+08:00" {
		t.Fatalf("winter us target = %s", winter["us_close"].TargetBeijingTime)
	}
}

func TestMissingValuationAnchorRequestsFor164701UsesUSGoldAnchor(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ164701"] = domain.NetValue{Symbol: "SZ164701", Date: "2026-06-01", NAV: 2}

	requests := missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	summer := requestsByFundAnchorDateAndKey(requests, "SZ164701", "2026-06-01")
	if summer["us_close"].TargetBeijingTime != "2026-06-02T04:00:00+08:00" {
		t.Fatalf("summer us target = %s", summer["us_close"].TargetBeijingTime)
	}
	if summer["us_close"].Weight != 1 {
		t.Fatalf("us weight = %.6f, want 1.0", summer["us_close"].Weight)
	}

	data = domain.EmptyValuationData()
	data.LatestNetValues["SZ164701"] = domain.NetValue{Symbol: "SZ164701", Date: "2026-12-01", NAV: 2}
	requests = missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 12, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	winter := requestsByFundAnchorDateAndKey(requests, "SZ164701", "2026-12-01")
	if winter["us_close"].TargetBeijingTime != "2026-12-02T05:00:00+08:00" {
		t.Fatalf("winter us target = %s", winter["us_close"].TargetBeijingTime)
	}
}

func TestMissingValuationAnchorRequestsFor513350UsesUSXOPOpenAnchor(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH513350"] = domain.NetValue{Symbol: "SH513350", Date: "2026-06-01", NAV: 2}

	requests := missingValuationAnchorPriceRequestsFromData(data, time.Date(2026, 6, 3, 9, 0, 0, 0, shanghaiLocation()), 0)
	summer := requestsByFundAnchorDateAndKey(requests, "SH513350", "2026-06-01")
	if summer["us_close"].ReferenceSymbol != "XOP" {
		t.Fatalf("reference_symbol = %s", summer["us_close"].ReferenceSymbol)
	}
	if summer["us_close"].TargetBeijingTime != "2026-06-02T04:00:00+08:00" {
		t.Fatalf("summer us target = %s", summer["us_close"].TargetBeijingTime)
	}
	if summer["us_close"].Weight != 1 {
		t.Fatalf("us weight = %.6f, want 1.0", summer["us_close"].Weight)
	}
}

func requestsByFundAnchorDateAndKey(requests []ValuationAnchorPriceRequestItem, fundSymbol string, anchorDate string) map[string]ValuationAnchorPriceRequestItem {
	out := map[string]ValuationAnchorPriceRequestItem{}
	for _, request := range requests {
		if request.FundSymbol == fundSymbol && request.AnchorDate == anchorDate {
			out[request.AnchorKey] = request
		}
	}
	return out
}

func TestMissingDailyPriceRequestsSkipsWeightedAnchorFunds(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ164701"] = domain.NetValue{Symbol: "SZ164701", Date: "2026-06-01", NAV: 1.8}
	data.CurrentHoldingDates["SZ164701"] = domain.HoldingDate{FundSymbol: "SZ164701", Date: "2026-05-29"}
	data.Holdings["SZ164701"] = []domain.Holding{
		{FundSymbol: "SZ164701", HoldingDate: "2026-05-29", HoldingSymbol: "GLD", Ratio: 99},
		{FundSymbol: "SZ164701", HoldingDate: "2026-05-29", HoldingSymbol: "SLV", Ratio: 1},
	}
	data.DailyPricesByDate["GLD"] = map[string]domain.DailyPrice{
		"2026-06-01": {Symbol: "GLD", Date: "2026-06-01", Close: 100},
	}

	service := NewService(ServiceOptions{Repository: &fakeBasePriceRepo{data: data}})
	requests, err := service.MissingDailyPriceRequests(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}

	for _, request := range requests {
		if request.Symbol == "GLD" || request.Symbol == "SLV" || request.Symbol == "HF_GC" || request.Symbol == "HF_SI" {
			t.Fatalf("weighted anchor fund should not request daily prices, got %+v", requests)
		}
	}
}

func TestMissingDailyPriceRequestsSkipsXOPOilWeightedAnchorFunds(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH513350"] = domain.NetValue{Symbol: "SH513350", Date: "2026-06-01", NAV: 1.8}
	data.CurrentHoldingDates["SH513350"] = domain.HoldingDate{FundSymbol: "SH513350", Date: "2026-05-29"}
	data.Holdings["SH513350"] = []domain.Holding{
		{FundSymbol: "SH513350", HoldingDate: "2026-05-29", HoldingSymbol: "XOP", Ratio: 99},
	}

	service := NewService(ServiceOptions{Repository: &fakeBasePriceRepo{data: data}})
	requests, err := service.MissingDailyPriceRequests(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, request := range requests {
		if request.Symbol == "XOP" {
			t.Fatalf("weighted anchor XOP fund should not request daily prices, got %+v", requests)
		}
	}
}

func TestMissingDailyPriceRequestsUsesCommodityBasketLegs(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SZ161815"] = domain.NetValue{Symbol: "SZ161815", Date: "2026-06-01", NAV: 1.8}
	data.CurrentHoldingDates["SZ161815"] = domain.HoldingDate{FundSymbol: "SZ161815", Date: "2026-05-29"}
	data.Holdings["SZ161815"] = []domain.Holding{
		{FundSymbol: "SZ161815", HoldingDate: "2026-05-29", HoldingSymbol: "GLD", Ratio: 73.34},
		{FundSymbol: "SZ161815", HoldingDate: "2026-05-29", HoldingSymbol: "^USO-EU", Ratio: 26.66},
	}

	service := NewService(ServiceOptions{Repository: &fakeBasePriceRepo{data: data}})
	requests, err := service.MissingDailyPriceRequests(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}

	got := map[string]DailyPriceRequestItem{}
	for _, request := range requests {
		got[request.Symbol] = request
	}
	for _, symbol := range []string{"HF_GC", "HF_CL", "HF_SI", "HF_HG"} {
		if _, exists := got[symbol]; !exists {
			t.Fatalf("%s should be requested for commodity basket fund: %+v", symbol, requests)
		}
	}
	if _, exists := got["GLD"]; exists {
		t.Fatalf("basket fund should not request legacy GLD base price: %+v", requests)
	}
	if _, exists := got["^USO-EU"]; exists {
		t.Fatalf("basket fund should not request auxiliary holding base price: %+v", requests)
	}
}

func TestUpsertRequestedDailyPricesFiltersInvalidRows(t *testing.T) {
	repo := &fakeBasePriceRepo{data: domain.EmptyValuationData()}
	service := NewService(ServiceOptions{Repository: repo})

	accepted, err := service.UpsertRequestedDailyPrices(context.Background(), "mac-home", []domain.DailyPrice{
		{Symbol: "HF_CL", Date: "2026-06-01", Close: 91.2},
		{Symbol: "HF_GC", Date: "2026-06-01", Close: 0},
	})
	if err != nil {
		t.Fatal(err)
	}
	if accepted != 1 || len(repo.prices) != 1 {
		t.Fatalf("accepted=%d stored=%d, want 1/1", accepted, len(repo.prices))
	}
	if repo.prices[0].AdjClose != 91.2 || repo.prices[0].Source != "ws_daily_price:mac-home" {
		t.Fatalf("stored price = %+v", repo.prices[0])
	}
}

func TestMissingDailyPriceRequestsSkipsIgnoredAuxiliarySymbols(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH501018"] = domain.NetValue{Symbol: "SH501018", Date: "2026-06-01", NAV: 1.8}
	data.CurrentHoldingDates["SH501018"] = domain.HoldingDate{FundSymbol: "SH501018", Date: "2026-05-29"}
	data.Holdings["SH501018"] = []domain.Holding{
		{FundSymbol: "SH501018", HoldingDate: "2026-05-29", HoldingSymbol: "^USO-EU", Ratio: 57.93},
		{FundSymbol: "SH501018", HoldingDate: "2026-05-29", HoldingSymbol: "USO", Ratio: 42.07},
	}

	service := NewService(ServiceOptions{Repository: &fakeBasePriceRepo{data: data}})
	requests, err := service.MissingDailyPriceRequests(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}

	got := map[string]DailyPriceRequestItem{}
	for _, request := range requests {
		got[request.Symbol] = request
	}
	if _, exists := got["^USO-EU"]; exists {
		t.Fatalf("^USO-EU should be ignored: %+v", requests)
	}
	if _, exists := got["HF_CL"]; exists {
		t.Fatalf("weighted anchor fund should not request HF_CL daily price: %+v", requests)
	}
}

func TestMissingDailyPriceRequestsSkipsUnsupportedLiveQuoteSymbols(t *testing.T) {
	data := domain.EmptyValuationData()
	data.LatestNetValues["SH513850"] = domain.NetValue{Symbol: "SH513850", Date: "2026-06-01", NAV: 1.0}
	data.CurrentHoldingDates["SH513850"] = domain.HoldingDate{FundSymbol: "SH513850", Date: "2026-05-29"}
	data.Holdings["SH513850"] = []domain.Holding{
		{FundSymbol: "SH513850", HoldingDate: "2026-05-29", HoldingSymbol: "BRK.B", Ratio: 10},
		{FundSymbol: "SH513850", HoldingDate: "2026-05-29", HoldingSymbol: "AAPL", Ratio: 90},
	}

	service := NewService(ServiceOptions{Repository: &fakeBasePriceRepo{data: data}})
	requests, err := service.MissingDailyPriceRequests(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}

	got := map[string]DailyPriceRequestItem{}
	for _, request := range requests {
		got[request.Symbol] = request
	}
	if _, exists := got["BRK.B"]; exists {
		t.Fatalf("BRK.B should be ignored entirely: %+v", requests)
	}
	if len(requests) != 0 {
		t.Fatalf("weighted anchor funds should resolve through anchor requests instead of daily price requests: %+v", requests)
	}
}
