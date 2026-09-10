package privatevaluation

import (
	"context"
	"sync"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

type historyRepositoryStub struct {
	rows []MinuteHistoryPoint
}

func (s historyRepositoryStub) LoadPrivateValuationInputs(context.Context) ([]Input, error) {
	return nil, nil
}

func (s historyRepositoryStub) UpsertPrivateValuationInput(context.Context, Input) error {
	return nil
}

func (s historyRepositoryStub) UpsertPrivateValuationSnapshot(context.Context, Snapshot) error {
	return nil
}

func (s historyRepositoryStub) LoadPrivateValuationMinuteHistory(_ context.Context, symbol string, days int) ([]MinuteHistoryPoint, error) {
	if symbol != TargetSymbol || days != 3 {
		return nil, nil
	}
	return append([]MinuteHistoryPoint(nil), s.rows...), nil
}

type snapshotRepositoryStub struct {
	persisted []Snapshot
}

func (s *snapshotRepositoryStub) LoadPrivateValuationInputs(context.Context) ([]Input, error) {
	return nil, nil
}

func (s *snapshotRepositoryStub) UpsertPrivateValuationInput(context.Context, Input) error {
	return nil
}

func (s *snapshotRepositoryStub) UpsertPrivateValuationSnapshot(_ context.Context, snapshot Snapshot) error {
	s.persisted = append(s.persisted, snapshot)
	return nil
}

type quoteProviderStub struct {
	quotes map[string]domain.Quote
}

type batchRepositoryStub struct {
	inputs    []Input
	batches   int
	snapshots []Snapshot
}

type orderedRefreshRepositoryStub struct {
	mu        sync.Mutex
	calls     int
	started   chan struct{}
	release   chan struct{}
	snapshots []Snapshot
}

func (s *orderedRefreshRepositoryStub) LoadPrivateValuationInputs(context.Context) ([]Input, error) {
	return nil, nil
}

func (s *orderedRefreshRepositoryStub) UpsertPrivateValuationInput(context.Context, Input) error {
	return nil
}

func (s *orderedRefreshRepositoryStub) UpsertPrivateValuationInputs(context.Context, []Input) error {
	return nil
}

func (s *orderedRefreshRepositoryStub) UpsertPrivateValuationSnapshot(_ context.Context, snapshot Snapshot) error {
	s.mu.Lock()
	s.calls++
	call := s.calls
	s.mu.Unlock()
	if call == 1 {
		close(s.started)
		<-s.release
	}
	s.mu.Lock()
	s.snapshots = append(s.snapshots, snapshot)
	s.mu.Unlock()
	return nil
}

func (s *batchRepositoryStub) LoadPrivateValuationInputs(context.Context) ([]Input, error) {
	return nil, nil
}

func (s *batchRepositoryStub) UpsertPrivateValuationInput(_ context.Context, input Input) error {
	s.inputs = append(s.inputs, input)
	return nil
}

func (s *batchRepositoryStub) UpsertPrivateValuationInputs(_ context.Context, inputs []Input) error {
	s.batches++
	s.inputs = append(s.inputs, inputs...)
	return nil
}

func (s *batchRepositoryStub) UpsertPrivateValuationSnapshot(_ context.Context, snapshot Snapshot) error {
	s.snapshots = append(s.snapshots, snapshot)
	return nil
}

func (s quoteProviderStub) CurrentQuotes() map[string]domain.Quote {
	return s.quotes
}

func TestUpdateInputsRejectsDuplicatesWithoutPersistence(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 0, 0, time.FixedZone("Asia/Shanghai", 8*60*60))
	repository := &batchRepositoryStub{}
	service := NewService(repository, quoteProviderStub{quotes: validQuotes(now)})
	input := validInput(now)
	result, err := service.UpdateInputs(context.Background(), []Input{input, input})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Accepted) != 0 || result.Rejected[TargetSymbol] == "" || repository.batches != 0 {
		t.Fatalf("result=%+v batches=%d", result, repository.batches)
	}
}

func TestUpdateInputsRejectsOlderGeneratedAt(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 0, 0, time.FixedZone("Asia/Shanghai", 8*60*60))
	repository := &batchRepositoryStub{}
	service := NewService(repository, quoteProviderStub{quotes: validQuotes(now)})
	current := validInput(now)
	service.inputs[TargetSymbol] = current
	older := validInput(now.Add(-time.Minute))
	result, err := service.UpdateInputs(context.Background(), []Input{older})
	if err != nil {
		t.Fatal(err)
	}
	if result.Rejected[TargetSymbol] == "" || repository.batches != 0 {
		t.Fatalf("result=%+v batches=%d", result, repository.batches)
	}
}

func TestUpdateInputsUsesOneBatchPersistence(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 0, 0, time.FixedZone("Asia/Shanghai", 8*60*60))
	repository := &batchRepositoryStub{}
	service := NewService(repository, quoteProviderStub{quotes: validQuotes(now)})
	result, err := service.UpdateInputs(context.Background(), []Input{validInput(now)})
	if err != nil {
		t.Fatal(err)
	}
	if repository.batches != 1 || len(repository.inputs) != 1 || len(result.Accepted) != 1 || result.Accepted[0] != TargetSymbol {
		t.Fatalf("result=%+v repository=%+v", result, repository)
	}
}

func TestUpdateInputsRepersistsAcceptedSymbolInCurrentMinute(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 30, 0, time.FixedZone("Asia/Shanghai", 8*60*60))
	repository := &batchRepositoryStub{}
	service := NewService(repository, quoteProviderStub{quotes: validQuotes(now)})
	service.now = func() time.Time { return now }
	service.lastPersistMinute[TargetSymbol] = now.In(shanghaiLocation).Format("2006-01-02 15:04")
	if _, err := service.UpdateInputs(context.Background(), []Input{validInput(now)}); err != nil {
		t.Fatal(err)
	}
	if len(repository.snapshots) != 1 || repository.snapshots[0].Symbol != TargetSymbol {
		t.Fatalf("persisted snapshots = %+v", repository.snapshots)
	}
}

func TestUpdateInputsWaitsForInflightRefreshBeforeSwappingGeneration(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 30, 0, time.FixedZone("Asia/Shanghai", 8*60*60))
	repository := &orderedRefreshRepositoryStub{
		started: make(chan struct{}),
		release: make(chan struct{}),
	}
	service := NewService(repository, quoteProviderStub{quotes: validQuotes(now)})
	service.now = func() time.Time { return now }
	service.inputs[TargetSymbol] = validInput(now.Add(-time.Second))

	refreshDone := make(chan error, 1)
	go func() { refreshDone <- service.Refresh(context.Background(), now) }()
	<-repository.started

	newInput := validInput(now)
	updateDone := make(chan error, 1)
	go func() {
		_, err := service.UpdateInputs(context.Background(), []Input{newInput})
		updateDone <- err
	}()
	select {
	case err := <-updateDone:
		close(repository.release)
		t.Fatalf("batch completed before the old refresh was released: %v", err)
	case <-time.After(50 * time.Millisecond):
	}
	close(repository.release)
	if err := <-refreshDone; err != nil {
		t.Fatal(err)
	}
	if err := <-updateDone; err != nil {
		t.Fatal(err)
	}

	repository.mu.Lock()
	snapshots := append([]Snapshot(nil), repository.snapshots...)
	repository.mu.Unlock()
	if len(snapshots) != 2 || snapshots[1].Input == nil ||
		!snapshots[1].Input.GeneratedAt.Equal(newInput.GeneratedAt) {
		t.Fatalf("persisted generations = %+v", snapshots)
	}
}

func TestUpdateInputsPersistsValidSubsetAndReportsInvalidFund(t *testing.T) {
	now := time.Date(2026, 7, 10, 10, 0, 0, 0, time.FixedZone("Asia/Shanghai", 8*60*60))
	repository := &batchRepositoryStub{}
	service := NewService(repository, quoteProviderStub{quotes: validQuotes(now)})
	invalid := Input{SchemaVersion: InputSchemaVersion, Symbol: SH513350Symbol}
	result, err := service.UpdateInputs(context.Background(), []Input{validInput(now), invalid})
	if err != nil {
		t.Fatal(err)
	}
	if repository.batches != 1 || len(repository.inputs) != 1 ||
		len(result.Accepted) != 1 || result.Accepted[0] != TargetSymbol ||
		result.Rejected[SH513350Symbol] == "" {
		t.Fatalf("result=%+v repository=%+v", result, repository)
	}
}

func TestMinuteHistoryUsesPrivateRepositoryAndMergesCurrentMinute(t *testing.T) {
	shanghai := time.FixedZone("Asia/Shanghai", 8*60*60)
	first := MinuteHistoryPoint{
		Minute:                   time.Date(2026, 7, 9, 14, 59, 0, 0, shanghai),
		MarketPrice:              1.065,
		BasketBidNAV:             1.066,
		BasketAskNAV:             1.068,
		BuyDirectionPremiumRate:  -0.001,
		SellDirectionPremiumRate: -0.002,
	}
	service := NewService(historyRepositoryStub{rows: []MinuteHistoryPoint{first}}, nil)
	service.snapshots[TargetSymbol] = Snapshot{
		Symbol:        TargetSymbol,
		AsOf:          time.Date(2026, 7, 10, 10, 0, 30, 0, shanghai),
		DomesticQuote: &domain.Quote{Symbol: TargetSymbol, Price: 1.0685},
		Valuation: &BasketValuation{
			NAVBid:                   1.0718059923,
			NAVAsk:                   1.0798377712,
			BuyDirectionPremiumRate:  -0.002618004,
			SellDirectionPremiumRate: -0.010963,
		},
	}

	history, err := service.MinuteHistory(context.Background(), TargetSymbol, 3)
	if err != nil {
		t.Fatal(err)
	}
	if history.Days != 3 || len(history.Rows) != 2 {
		t.Fatalf("history = %+v", history)
	}
	if !history.Rows[0].Minute.Before(history.Rows[1].Minute) {
		t.Fatalf("rows are not chronological: %+v", history.Rows)
	}
	if history.Rows[1].BasketBidNAV != 1.0718059923 || history.Rows[1].MarketPrice != 1.0685 {
		t.Fatalf("merged current row = %+v", history.Rows[1])
	}
}

func TestNormalizeMinuteHistoryDaysRejectsUnsupportedSpan(t *testing.T) {
	if _, err := NormalizeMinuteHistoryDays(2); err == nil {
		t.Fatal("expected unsupported day span to fail")
	}
}

func TestMinuteHistoryTradingSessionMatchesChinaContinuousAuction(t *testing.T) {
	shanghai := time.FixedZone("Asia/Shanghai", 8*60*60)
	cases := []struct {
		name string
		at   time.Time
		want bool
	}{
		{name: "before open", at: time.Date(2026, 7, 10, 9, 29, 0, 0, shanghai), want: false},
		{name: "morning open", at: time.Date(2026, 7, 10, 9, 30, 0, 0, shanghai), want: true},
		{name: "morning close", at: time.Date(2026, 7, 10, 11, 30, 0, 0, shanghai), want: true},
		{name: "lunch break", at: time.Date(2026, 7, 10, 12, 0, 0, 0, shanghai), want: false},
		{name: "afternoon open", at: time.Date(2026, 7, 10, 13, 0, 0, 0, shanghai), want: true},
		{name: "market close", at: time.Date(2026, 7, 10, 15, 0, 0, 0, shanghai), want: true},
		{name: "after close", at: time.Date(2026, 7, 10, 15, 1, 0, 0, shanghai), want: false},
		{name: "weekend", at: time.Date(2026, 7, 11, 10, 0, 0, 0, shanghai), want: false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if got := IsMinuteHistoryTradingSession(testCase.at); got != testCase.want {
				t.Fatalf("IsMinuteHistoryTradingSession(%s) = %t, want %t", testCase.at, got, testCase.want)
			}
		})
	}
}

func TestMinuteHistoryDropsRepositoryRowsOutsideTradingSession(t *testing.T) {
	shanghai := time.FixedZone("Asia/Shanghai", 8*60*60)
	service := NewService(historyRepositoryStub{rows: []MinuteHistoryPoint{
		{Minute: time.Date(2026, 7, 10, 10, 0, 0, 0, shanghai), MarketPrice: 1.068, BasketBidNAV: 1.07, BasketAskNAV: 1.08},
		{Minute: time.Date(2026, 7, 10, 18, 0, 0, 0, shanghai), MarketPrice: 1.068, BasketBidNAV: 1.07, BasketAskNAV: 1.08},
	}}, nil)
	history, err := service.MinuteHistory(context.Background(), TargetSymbol, 3)
	if err != nil {
		t.Fatal(err)
	}
	if len(history.Rows) != 1 || history.Rows[0].Minute.Hour() != 10 {
		t.Fatalf("history should retain only the in-session point: %+v", history.Rows)
	}
}

func TestRefreshPersistsPrivateMinuteOnlyDuringTradingSession(t *testing.T) {
	shanghai := time.FixedZone("Asia/Shanghai", 8*60*60)
	insideSession := time.Date(2026, 7, 10, 10, 0, 0, 0, shanghai)
	afterClose := time.Date(2026, 7, 10, 18, 0, 0, 0, shanghai)
	repository := &snapshotRepositoryStub{}
	service := NewService(repository, quoteProviderStub{quotes: validQuotes(insideSession)})
	service.inputs[TargetSymbol] = validInput(insideSession)

	if err := service.Refresh(context.Background(), insideSession); err != nil {
		t.Fatal(err)
	}
	if len(repository.persisted) != 1 {
		t.Fatalf("in-session persisted snapshots = %d, want 1", len(repository.persisted))
	}
	if err := service.Refresh(context.Background(), afterClose); err != nil {
		t.Fatal(err)
	}
	if len(repository.persisted) != 1 {
		t.Fatalf("after-close refresh must not write minute history; snapshots = %d", len(repository.persisted))
	}
}

func TestListIncludesEachRegisteredPrivateFund(t *testing.T) {
	service := NewService(nil, nil)
	response := service.List()
	if len(response.Funds) != 31 {
		t.Fatalf("private list items = %d, want 31: %+v", len(response.Funds), response.Funds)
	}
	wantSymbols := []string{
		TargetSymbol, SH513350Symbol, SZ159605Symbol, SZ159607Symbol, SH513050Symbol, SH513220Symbol,
		SH513100Symbol, SH513110Symbol, SH513300Symbol, SH513390Symbol, SH513870Symbol,
		SZ159501Symbol, SZ159513Symbol, SZ159632Symbol, SZ159659Symbol, SZ159660Symbol, SZ159696Symbol, SZ159941Symbol,
		SH513500Symbol, SH513650Symbol, SZ159612Symbol, SZ159655Symbol,
		SH513000Symbol, SH513520Symbol, SH513880Symbol, SZ159866Symbol,
		SH513030Symbol, SZ159561Symbol,
		SZ164824Symbol, SZ162411Symbol, SZ161226Symbol,
	}
	for index, symbol := range wantSymbols {
		if response.Funds[index].Symbol != symbol {
			t.Fatalf("private list symbol[%d] = %s, want %s; all=%+v", index, response.Funds[index].Symbol, symbol, response.Funds)
		}
	}
	if response.Funds[0].Symbol != TargetSymbol || response.Funds[1].Symbol != SH513350Symbol || response.Funds[2].Symbol != SZ159605Symbol || response.Funds[3].Symbol != SZ159607Symbol || response.Funds[4].Symbol != SH513050Symbol || response.Funds[5].Symbol != SH513220Symbol {
		t.Fatalf("private list symbols = %+v", response.Funds)
	}
	if response.Funds[1].ModelVersion != SH513350ModelVersion {
		t.Fatalf("SH513350 model = %q", response.Funds[1].ModelVersion)
	}
	if response.Funds[2].ModelVersion != SZ159605ModelVersion {
		t.Fatalf("SZ159605 model = %q", response.Funds[2].ModelVersion)
	}
	if response.Funds[5].ModelVersion != SH513220ModelVersion {
		t.Fatalf("SH513220 model = %q", response.Funds[5].ModelVersion)
	}
	if response.Funds[28].ModelVersion != SZ164824ModelVersion {
		t.Fatalf("SZ164824 model = %q", response.Funds[28].ModelVersion)
	}
	if response.Funds[29].ModelVersion != SZ162411ModelVersion || response.Funds[29].ValuationKind != CalculationModeLOFWeightedAnchor {
		t.Fatalf("SZ162411 item = %+v", response.Funds[29])
	}
}

func TestListIncludesBothPremiumDirections(t *testing.T) {
	service := NewService(nil, nil)
	service.snapshots[TargetSymbol] = Snapshot{
		SchemaVersion: SchemaVersion,
		Symbol:        TargetSymbol,
		Name:          TargetName,
		ModelVersion:  ModelVersion,
		Valuation: &BasketValuation{
			BuyDirectionPremiumRate:  -0.0026,
			SellDirectionPremiumRate: -0.0110,
		},
	}
	response := service.List()
	item := response.Funds[0]
	if item.BuyDirectionPremiumRate == nil || *item.BuyDirectionPremiumRate != -0.0026 {
		t.Fatalf("buy direction premium = %v", item.BuyDirectionPremiumRate)
	}
	if item.SellDirectionPremiumRate == nil || *item.SellDirectionPremiumRate != -0.0110 {
		t.Fatalf("sell direction premium = %v", item.SellDirectionPremiumRate)
	}
}
