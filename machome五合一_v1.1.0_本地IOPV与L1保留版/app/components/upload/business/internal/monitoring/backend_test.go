package monitoring

import (
	"context"
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/hkconnectfx"
	"newnavnav/internal/privatevaluation"
	"newnavnav/internal/snapshot"
)

func TestParseClockSecondsAcceptsSeconds(t *testing.T) {
	if got := parseClockSeconds("09:16:30", 0); got != 9*3600+16*60+30 {
		t.Fatalf("parseClockSeconds = %d", got)
	}
}

func TestBackendConfigReadsStartupGrace(t *testing.T) {
	t.Setenv("PUSHPLUS_MONITOR_STARTUP_GRACE", "75s")
	config := BackendConfigFromEnv(t.TempDir())
	if config.StartupGrace != 75*time.Second {
		t.Fatalf("StartupGrace = %s, want 75s", config.StartupGrace)
	}
	t.Setenv("PUSHPLUS_MONITOR_STARTUP_GRACE", "")
	config = BackendConfigFromEnv(t.TempDir())
	if config.StartupGrace != 3*time.Minute {
		t.Fatalf("default StartupGrace = %s, want 3m", config.StartupGrace)
	}
}

func TestBackendRunnerStartupGraceCanBeCancelled(t *testing.T) {
	called := false
	runner := NewBackendRunner(
		BackendConfig{Enabled: true, Token: "test", StartupGrace: time.Hour},
		BackendSources{Snapshot: func(bool) snapshot.DebugStatus {
			called = true
			return snapshot.DebugStatus{}
		}},
		nil,
	)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	runner.Run(ctx)
	if called {
		t.Fatal("monitor tick ran before the cancelled startup grace completed")
	}
}

func TestBackendRunnerSkipsWeekendsAndConfiguredDates(t *testing.T) {
	runner := &BackendRunner{config: BackendConfig{SkipDates: map[string]bool{"2026-09-01": true}}}
	if runner.isMonitorDay(time.Date(2026, 9, 1, 9, 0, 0, 0, time.UTC)) {
		t.Fatal("configured skip date was monitored")
	}
	if runner.isMonitorDay(time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)) {
		t.Fatal("Saturday was monitored")
	}
	if !runner.isMonitorDay(time.Date(2026, 9, 2, 9, 0, 0, 0, time.UTC)) {
		t.Fatal("weekday was skipped")
	}
}

func TestBackendConfigFullyIgnoresNQAndESByDefault(t *testing.T) {
	t.Setenv("PUSHPLUS_IGNORED_PRIVATE_SYMBOLS", "")
	t.Setenv("PUSHPLUS_ALLOWED_DELAYED_IB_SYMBOLS", "")
	config := BackendConfigFromEnv(t.TempDir())
	for _, symbol := range []string{"SZ159501", "SH513870", "SZ159612", "SH513650"} {
		if !config.IgnoredPrivate[symbol] {
			t.Fatalf("%s was not ignored", symbol)
		}
		if config.AllowedDelayedIB[symbol] {
			t.Fatalf("%s remained in delayed-only policy", symbol)
		}
	}
	now := time.Date(2026, 9, 1, 14, 0, 0, 0, time.Local)
	runner := &BackendRunner{config: config}
	problems, metrics := runner.privateProblems(now, []privatevaluation.Snapshot{{Symbol: "SZ159501"}}, healthyHKFX(now), true, true)
	if len(problems) != 0 || metrics.PrivateExpected != 0 {
		t.Fatalf("ignored NQ fund was monitored: problems=%v metrics=%+v", problems, metrics)
	}
}

func TestDomesticAuctionFreezeWindow(t *testing.T) {
	tests := []struct {
		name string
		now  time.Time
		want bool
	}{
		{name: "before", now: time.Date(2026, 9, 1, 9, 24, 59, 0, time.Local), want: false},
		{name: "start", now: time.Date(2026, 9, 1, 9, 25, 0, 0, time.Local), want: true},
		{name: "before end", now: time.Date(2026, 9, 1, 9, 29, 59, 0, time.Local), want: true},
		{name: "end", now: time.Date(2026, 9, 1, 9, 30, 0, 0, time.Local), want: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := isDomesticAuctionFreeze(test.now); got != test.want {
				t.Fatalf("isDomesticAuctionFreeze(%s) = %v, want %v", test.now.Format("15:04:05"), got, test.want)
			}
		})
	}
}

func TestDomesticLunchMonitorPause(t *testing.T) {
	tests := []struct {
		name string
		now  time.Time
		want bool
	}{
		{name: "before", now: time.Date(2026, 9, 1, 11, 29, 59, 0, time.Local), want: false},
		{name: "start", now: time.Date(2026, 9, 1, 11, 30, 0, 0, time.Local), want: true},
		{name: "reopen grace", now: time.Date(2026, 9, 1, 13, 0, 59, 0, time.Local), want: true},
		{name: "monitor resumes", now: time.Date(2026, 9, 1, 13, 1, 0, 0, time.Local), want: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := isDomesticLunchMonitorPause(test.now); got != test.want {
				t.Fatalf("isDomesticLunchMonitorPause(%s) = %v, want %v", test.now.Format("15:04:05"), got, test.want)
			}
		})
	}
}

func TestDomesticPublicMonitorCutoff(t *testing.T) {
	tests := []struct {
		name string
		now  time.Time
		want bool
	}{
		{name: "before cutoff", now: time.Date(2026, 9, 1, 14, 56, 59, 0, time.Local), want: true},
		{name: "at cutoff", now: time.Date(2026, 9, 1, 14, 57, 0, 0, time.Local), want: false},
		{name: "during lunch", now: time.Date(2026, 9, 1, 12, 0, 0, 0, time.Local), want: false},
		{name: "after lunch warmup", now: time.Date(2026, 9, 1, 13, 1, 0, 0, time.Local), want: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := isDomesticPublicMonitorWindow(test.now); got != test.want {
				t.Fatalf("isDomesticPublicMonitorWindow(%s) = %v, want %v", test.now.Format("15:04:05"), got, test.want)
			}
		})
	}
}

func TestPrivateMonitorCutoff(t *testing.T) {
	tests := []struct {
		name string
		now  time.Time
		want bool
	}{
		{name: "before cutoff", now: time.Date(2026, 9, 1, 14, 59, 59, 0, time.Local), want: true},
		{name: "at cutoff", now: time.Date(2026, 9, 1, 15, 0, 0, 0, time.Local), want: false},
		{name: "after cutoff", now: time.Date(2026, 9, 1, 15, 5, 0, 0, time.Local), want: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := isPrivateMonitorWindow(test.now); got != test.want {
				t.Fatalf("isPrivateMonitorWindow(%s) = %v, want %v", test.now.Format("15:04:05"), got, test.want)
			}
		})
	}
}

func TestPublicProblemsAllowsFullSnapshotCadence(t *testing.T) {
	now := time.Date(2026, 9, 1, 9, 34, 50, 0, time.Local)
	debug := healthyPublicDebug(now, now.Add(-80*time.Second))
	debug.UploadStatus.Quotes[0].RealtimeStatus = "stale"
	debug.UploadStatus.Quotes[0].StaleReason = "last uploaded quote older than 30s"
	problems, metrics := (&BackendRunner{}).publicProblems(now, debug)
	if len(problems) != 0 {
		t.Fatalf("publicProblems returned %v", problems)
	}
	if metrics.DomesticHealthy != 1 {
		t.Fatalf("DomesticHealthy = %d, want 1", metrics.DomesticHealthy)
	}

	debug = healthyPublicDebug(now, now.Add(-91*time.Second))
	problems, _ = (&BackendRunner{}).publicProblems(now, debug)
	if !hasProblem(problems, "backend.public.stale") {
		t.Fatalf("publicProblems = %v, want stale quote problem", problems)
	}

	debug = healthyPublicDebug(now, now.Add(-10*time.Second))
	debug.UploadStatus.Quotes[0].RealtimeStatus = "unsupported"
	debug.UploadStatus.Quotes[0].StaleReason = "upstream quote is unsupported"
	problems, _ = (&BackendRunner{}).publicProblems(now, debug)
	if !hasProblem(problems, "backend.public.stale") {
		t.Fatalf("publicProblems = %v, want genuine upstream status problem", problems)
	}
}

func TestPublicProblemsIgnoresQuoteAgeDuringAuctionFreeze(t *testing.T) {
	now := time.Date(2026, 9, 1, 9, 28, 0, 0, time.Local)
	debug := healthyPublicDebug(now, now.Add(-10*time.Minute))
	debug.UploadStatus.Quotes[0].RealtimeStatus = "stale"
	debug.UploadStatus.Quotes[0].StaleReason = "last uploaded quote older than 30s"
	problems, _ := (&BackendRunner{}).publicProblems(now, debug)
	if hasProblem(problems, "backend.public.stale") {
		t.Fatalf("publicProblems returned auction-freeze stale problem: %v", problems)
	}
}

func TestPublicProblemsIgnoresQuoteAndUploadAgeDuringLunch(t *testing.T) {
	now := time.Date(2026, 9, 1, 12, 0, 0, 0, time.Local)
	debug := healthyPublicDebug(now, now.Add(-10*time.Minute))
	debug.UploadSources[0].LastUploadAt = now.Add(-10 * time.Minute)
	problems, _ := (&BackendRunner{}).publicProblems(now, debug)
	if hasProblem(problems, "backend.public.stale") || hasProblem(problems, "backend.public.upload") {
		t.Fatalf("publicProblems returned lunch freshness problem: %v", problems)
	}
}

func TestPrivateProblemsDoesNotDeriveFailuresFromStaleUpload(t *testing.T) {
	now := time.Date(2026, 9, 1, 9, 35, 0, 0, time.Local)
	rate := 7.0
	funds := []privatevaluation.Snapshot{{
		Symbol: "SZ159501",
		Input: &privatevaluation.Input{
			GeneratedAt: now.Add(-time.Minute),
			PCF:         privatevaluation.PCFInput{TradingDay: "2026-08-31"},
			IB:          privatevaluation.IBQuoteInput{Symbol: "NQ", MarketDataType: "Delayed"},
			FX: privatevaluation.FXInput{
				Source:    "CFETS",
				Rate:      &rate,
				FetchedAt: now.Add(-10 * time.Minute),
			},
		},
	}}
	problems, metrics := (&BackendRunner{}).privateProblems(now, funds, healthyHKFX(now), true, true)
	if !hasProblem(problems, "backend.private.stale") {
		t.Fatalf("privateProblems = %v, want stale input problem", problems)
	}
	for _, key := range []string{"backend.private.pcf", "backend.private.ib", "backend.private.fx"} {
		if hasProblem(problems, key) {
			t.Fatalf("privateProblems derived %s from stale input: %v", key, problems)
		}
	}
	if metrics.PCFExpected != 1 || metrics.PrivateHealthy != 0 {
		t.Fatalf("metrics = %+v, want one expected PCF and no healthy private input", metrics)
	}
}

func TestPrivateProblemsIgnoresConfiguredFund(t *testing.T) {
	now := time.Date(2026, 9, 1, 9, 37, 0, 0, time.Local)
	runner := &BackendRunner{config: BackendConfig{IgnoredPrivate: map[string]bool{"SZ159605": true}}}
	problems, metrics := runner.privateProblems(now, []privatevaluation.Snapshot{{Symbol: "SZ159605"}}, healthyHKFX(now), true, true)
	if len(problems) != 0 {
		t.Fatalf("privateProblems returned ignored-fund problems: %v", problems)
	}
	if metrics.PrivateExpected != 0 || metrics.PCFExpected != 0 {
		t.Fatalf("ignored fund was counted in metrics: %+v", metrics)
	}
}

func TestPrivateProblemsAllowsTenMinuteCFETSDelay(t *testing.T) {
	now := time.Date(2026, 9, 3, 10, 15, 0, 0, time.Local)
	symbols := []string{"SH513000", "SH513520", "SH513880", "SZ159866", "SH513030", "SZ159561", "SH513350", "SZ159518", "SZ162411"}
	for _, test := range []struct {
		name string
		age  time.Duration
		want bool
	}{
		{name: "previous alert age", age: 3*time.Minute + 17*time.Second},
		{name: "within tolerance", age: 9*time.Minute + 59*time.Second},
		{name: "exactly ten minutes", age: 10 * time.Minute},
		{name: "exceeds ten minutes", age: 10*time.Minute + time.Second, want: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			fxAt := now.Add(-test.age)
			rate, bid, ask := 1.0, 100.0, 101.0
			funds := make([]privatevaluation.Snapshot, 0, len(symbols))
			for _, symbol := range symbols {
				funds = append(funds, privatevaluation.Snapshot{
					Symbol: symbol,
					Input: &privatevaluation.Input{
						GeneratedAt: now,
						PCF:         privatevaluation.PCFInput{TradingDay: now.Format("2006-01-02")},
						IB: privatevaluation.IBQuoteInput{
							Symbol: "test", Bid: &bid, Ask: &ask, MarketDataType: "Live", StreamCheckedAt: now,
						},
						FX: privatevaluation.FXInput{Source: "CFETS_SPOT_RATE", Rate: &rate, FetchedAt: fxAt},
					},
				})
			}
			problems, metrics := (&BackendRunner{}).privateProblems(now, funds, healthyHKFX(fxAt), true, true)
			for _, key := range []string{"backend.private.fx.nikkei225", "backend.private.fx.germany", "backend.private.fx.xop", "backend.private.hkfx_usdcny"} {
				if got := hasExactProblem(problems, key); got != test.want {
					t.Fatalf("%s at age %s = %v, want %v: %v", key, test.age, got, test.want, problems)
				}
			}
			wantHealthy := len(symbols)
			if test.want {
				wantHealthy = 0
			} else if len(problems) != 0 {
				t.Fatalf("unexpected problems within CFETS tolerance: %v", problems)
			}
			if metrics.PrivateHealthy != wantHealthy || metrics.CFETSAge != test.age {
				t.Fatalf("metrics = %+v, want healthy=%d and CFETS age=%s", metrics, wantHealthy, test.age)
			}
		})
	}
}

func TestPrivateProblemsCFETSFailedRefreshAllowsRecentValidatedCache(t *testing.T) {
	now := time.Date(2026, 9, 3, 10, 15, 0, 0, time.Local)
	recent, boundary, expired := now.Add(-5*time.Minute), now.Add(-10*time.Minute), now.Add(-10*time.Minute-time.Second)
	zero := time.Time{}
	for _, test := range []struct {
		name       string
		observedAt *time.Time
		want       bool
	}{
		{name: "transient failure with cache", observedAt: &recent},
		{name: "cache exactly ten minutes old", observedAt: &boundary},
		{name: "cache expired", observedAt: &expired, want: true},
		{name: "no successful quote yet", want: true},
		{name: "zero quote timestamp", observedAt: &zero, want: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			bid, ask := 7.0, 7.01
			hkfx := hkconnectfx.Snapshot{FXQuotes: map[string]hkconnectfx.FXQuote{
				"USD/CNY": {Pair: "USD/CNY", Bid: &bid, Ask: &ask, Healthy: false, ObservedAt: test.observedAt, Error: "temporary TLS refresh failure"},
			}}
			problems, _ := (&BackendRunner{}).privateProblems(now, nil, hkfx, true, true)
			if got := hasExactProblem(problems, "backend.private.hkfx_usdcny"); got != test.want {
				t.Fatalf("CFETS refresh problem = %v, want %v: %v", got, test.want, problems)
			}
		})
	}
}

func TestPrivateProblemsAllowsConfiguredDelayedIB(t *testing.T) {
	now := time.Date(2026, 9, 1, 9, 37, 0, 0, time.Local)
	bid, ask := 29504.75, 29505.75
	fund := privatevaluation.Snapshot{
		Symbol: "SZ159501",
		Input: &privatevaluation.Input{
			GeneratedAt: now,
			PCF:         privatevaluation.PCFInput{TradingDay: "2026-09-01"},
			IB: privatevaluation.IBQuoteInput{
				Symbol:          "NQ",
				Bid:             &bid,
				Ask:             &ask,
				MarketDataType:  "Delayed",
				StreamCheckedAt: now,
			},
		},
	}
	runner := &BackendRunner{config: BackendConfig{AllowedDelayedIB: map[string]bool{"SZ159501": true}}}
	problems, metrics := runner.privateProblems(now, []privatevaluation.Snapshot{fund}, healthyHKFX(now), true, true)
	if hasProblem(problems, "backend.private.ib") {
		t.Fatalf("privateProblems rejected configured delayed IB quote: %v", problems)
	}
	if metrics.PrivateHealthy != 1 {
		t.Fatalf("PrivateHealthy = %d, want 1", metrics.PrivateHealthy)
	}

	runner.config.AllowedDelayedIB = nil
	problems, _ = runner.privateProblems(now, []privatevaluation.Snapshot{fund}, healthyHKFX(now), true, true)
	if !hasProblem(problems, "backend.private.ib") {
		t.Fatalf("privateProblems accepted delayed IB without configuration: %v", problems)
	}
}

func TestPrivateProblemsSkipsSilverDuringExchangeBreak(t *testing.T) {
	runner := &BackendRunner{}
	funds := []privatevaluation.Snapshot{{Symbol: "SZ161226"}}
	duringBreak := time.Date(2026, 9, 1, 10, 20, 0, 0, time.Local)
	problems, metrics := runner.privateProblems(duringBreak, funds, healthyHKFX(duringBreak), true, true)
	if len(problems) != 0 || metrics.PrivateExpected != 0 {
		t.Fatalf("silver break was monitored: problems=%v metrics=%+v", problems, metrics)
	}

	beforeBreak := time.Date(2026, 9, 1, 10, 14, 59, 0, time.Local)
	problems, metrics = runner.privateProblems(beforeBreak, funds, healthyHKFX(beforeBreak), true, true)
	if !hasProblem(problems, "backend.private.missing") || metrics.PrivateExpected != 1 {
		t.Fatalf("active silver session was skipped: problems=%v metrics=%+v", problems, metrics)
	}

	if isSilverCollectionWindow(time.Date(2026, 9, 1, 10, 30, 59, 0, time.Local)) {
		t.Fatal("silver reopen grace was monitored before 10:31")
	}
	if !isSilverCollectionWindow(time.Date(2026, 9, 1, 10, 31, 0, 0, time.Local)) {
		t.Fatal("silver was not monitored after reopen grace")
	}
	if isSilverCollectionWindow(time.Date(2026, 9, 1, 11, 30, 0, 0, time.Local)) {
		t.Fatal("silver lunch break was monitored at 11:30")
	}
	if isSilverCollectionWindow(time.Date(2026, 9, 1, 13, 30, 59, 0, time.Local)) {
		t.Fatal("silver afternoon reopen grace was monitored before 13:31")
	}
	if !isSilverCollectionWindow(time.Date(2026, 9, 1, 13, 31, 0, 0, time.Local)) {
		t.Fatal("silver was not monitored after afternoon reopen grace")
	}
	if !isSilverCollectionWindow(time.Date(2026, 9, 1, 14, 59, 59, 0, time.Local)) {
		t.Fatal("silver was not monitored before the afternoon close")
	}
	if isSilverCollectionWindow(time.Date(2026, 9, 1, 15, 0, 0, 0, time.Local)) {
		t.Fatal("silver was monitored after the 15:00 afternoon close")
	}
}

func TestPrivateProblemsAllowsMinuteSilverInputCadence(t *testing.T) {
	now := time.Date(2026, 9, 1, 14, 30, 30, 0, time.Local)
	fund := privatevaluation.Snapshot{
		Symbol: "SZ161226",
		Input: &privatevaluation.Input{
			GeneratedAt: now.Add(-89 * time.Second),
			Silver:      &privatevaluation.SilverSettlementInput{ObservedAt: now.Add(-2*time.Minute - 59*time.Second)},
		},
	}
	problems, metrics := (&BackendRunner{}).privateProblems(now, []privatevaluation.Snapshot{fund}, healthyHKFX(now), true, true)
	if hasProblem(problems, "backend.private.stale") || hasProblem(problems, "backend.private.ib") {
		t.Fatalf("minute silver input cadence was rejected: %v", problems)
	}
	if metrics.PrivateHealthy != 1 {
		t.Fatalf("PrivateHealthy = %d, want 1", metrics.PrivateHealthy)
	}

	fund.Input.GeneratedAt = now.Add(-91 * time.Second)
	problems, _ = (&BackendRunner{}).privateProblems(now, []privatevaluation.Snapshot{fund}, healthyHKFX(now), true, true)
	if !hasProblem(problems, "backend.private.stale") {
		t.Fatalf("stale silver input was accepted: %v", problems)
	}
}

func TestInvalidLiveInputAllowsMinuteSilverCadence(t *testing.T) {
	now := time.Date(2026, 9, 1, 14, 30, 30, 0, time.Local)
	input := &privatevaluation.Input{Silver: &privatevaluation.SilverSettlementInput{ObservedAt: now.Add(-2*time.Minute - 59*time.Second)}}
	if reason := invalidLiveInput(now, input, false); reason != "" {
		t.Fatalf("minute silver observation was rejected: %s", reason)
	}
	input.Silver.ObservedAt = now.Add(-3*time.Minute - time.Second)
	if reason := invalidLiveInput(now, input, false); reason != "silver:stale" {
		t.Fatalf("stale silver observation returned %q", reason)
	}
}

func TestInvalidLiveInputUsesSessionAwareNiftyFreshness(t *testing.T) {
	bid, ask := 25_100.0, 25_102.0
	quote := privatevaluation.IBQuoteInput{
		Symbol:         "NIFTY",
		Bid:            &bid,
		Ask:            &ask,
		MarketDataType: "Live",
	}
	input := &privatevaluation.Input{
		India: &privatevaluation.IndiaT2Input{
			NiftyBridge: &privatevaluation.IndiaNiftyBridgeInput{Nifty: quote},
		},
	}

	preopen := time.Date(2026, 9, 2, 11, 30, 0, 0, time.Local)
	input.India.NiftyBridge.Nifty.ObservedAt = preopen.Add(-2*time.Minute - 59*time.Second)
	if reason := invalidLiveInput(preopen, input, false); reason != "" {
		t.Fatalf("preopen NIFTY quote was rejected too early: %s", reason)
	}
	input.India.NiftyBridge.Nifty.ObservedAt = preopen.Add(-3*time.Minute - time.Second)
	if reason := invalidLiveInput(preopen, input, false); reason != "NIFTY:stale" {
		t.Fatalf("preopen stale NIFTY quote returned %q", reason)
	}

	open := time.Date(2026, 9, 2, 11, 45, 0, 0, time.Local)
	input.India.NiftyBridge.Nifty.ObservedAt = open.Add(-89 * time.Second)
	if reason := invalidLiveInput(open, input, false); reason != "" {
		t.Fatalf("open-session NIFTY quote was rejected too early: %s", reason)
	}
	input.India.NiftyBridge.Nifty.ObservedAt = open.Add(-91 * time.Second)
	if reason := invalidLiveInput(open, input, false); reason != "NIFTY:stale" {
		t.Fatalf("open-session stale NIFTY quote returned %q", reason)
	}
}

func TestPrivateProblemsSkipsNikkeiAfterFuturesCutoff(t *testing.T) {
	runner := &BackendRunner{}
	nikkei := []privatevaluation.Snapshot{{Symbol: "SH513000"}}

	beforeCutoff := time.Date(2026, 9, 1, 14, 39, 59, 0, time.Local)
	problems, metrics := runner.privateProblems(beforeCutoff, nikkei, healthyHKFX(beforeCutoff), true, true)
	if !hasProblem(problems, "backend.private.missing") || metrics.PrivateExpected != 1 {
		t.Fatalf("active Nikkei session was skipped: problems=%v metrics=%+v", problems, metrics)
	}

	atCutoff := time.Date(2026, 9, 1, 14, 40, 0, 0, time.Local)
	problems, metrics = runner.privateProblems(atCutoff, nikkei, healthyHKFX(atCutoff), true, true)
	if len(problems) != 0 || metrics.PrivateExpected != 0 {
		t.Fatalf("Nikkei was monitored after cutoff: problems=%v metrics=%+v", problems, metrics)
	}

	nonNikkei := []privatevaluation.Snapshot{{Symbol: "SZ159501"}}
	problems, metrics = runner.privateProblems(atCutoff, nonNikkei, healthyHKFX(atCutoff), true, true)
	if !hasProblem(problems, "backend.private.missing") || metrics.PrivateExpected != 1 {
		t.Fatalf("non-Nikkei private fund was skipped: problems=%v metrics=%+v", problems, metrics)
	}
}

func TestPrivateProblemsSeparatesIndependentUploaderFamilies(t *testing.T) {
	now := time.Date(2026, 9, 2, 10, 20, 0, 0, time.Local)
	funds := []privatevaluation.Snapshot{
		{Symbol: "SH513000", Input: &privatevaluation.Input{GeneratedAt: now.Add(-time.Minute)}},
		{Symbol: "SZ159561", Input: &privatevaluation.Input{GeneratedAt: now.Add(-time.Minute)}},
	}
	problems, _ := (&BackendRunner{}).privateProblems(now, funds, healthyHKFX(now), true, true)
	if !hasExactProblem(problems, "backend.private.stale.nikkei225") {
		t.Fatalf("Nikkei stale incident was not independent: %v", problems)
	}
	if !hasExactProblem(problems, "backend.private.stale.germany") {
		t.Fatalf("Germany stale incident was not independent: %v", problems)
	}
}

func TestScheduledReportContentDoesNotClaimPrivateReadiness(t *testing.T) {
	now := time.Date(2026, 9, 1, 9, 16, 30, 0, time.Local)
	preopen := preopenContent(now, nil, backendMetrics{})
	if strings.Contains(preopen, "PCF") || strings.Contains(preopen, "私有估值") || strings.Contains(preopen, "CFETS") {
		t.Fatalf("preopen report includes premature private checks: %s", preopen)
	}
	realtime := realtimeContent(now, nil, backendMetrics{})
	if strings.Contains(realtime, "私有/IB") || strings.Contains(realtime, "PCF") || strings.Contains(realtime, "CFETS") {
		t.Fatalf("09:16 report includes premature private checks: %s", realtime)
	}
}

func healthyHKFX(now time.Time) hkconnectfx.Snapshot {
	return hkconnectfx.Snapshot{FXQuotes: map[string]hkconnectfx.FXQuote{
		"USD/CNY": {Pair: "USD/CNY", Healthy: true, ObservedAt: &now},
	}}
}

func healthyPublicDebug(now, quoteFetchedAt time.Time) snapshot.DebugStatus {
	return snapshot.DebugStatus{
		Required: snapshot.RequiredQuoteSymbols{Symbols: []string{"SZ159501"}},
		UploadStatus: snapshot.UploadedQuoteStatus{Quotes: []domain.Quote{{
			Symbol:         "SZ159501",
			QuoteDate:      now.Format("2006-01-02"),
			RealtimeStatus: "realtime",
			FetchedAt:      quoteFetchedAt,
		}}},
		WSClients:     []snapshot.WSClientStatus{{Source: "home-mac", Active: true, LastSeenAt: now}},
		UploadSources: []snapshot.UploadSourceStatus{{Source: "home-mac", LastUploadAt: now}},
	}
}

func hasProblem(problems []Problem, key string) bool {
	for _, problem := range problems {
		if problem.Key == key || strings.HasPrefix(problem.Key, key+".") {
			return true
		}
	}
	return false
}

func hasExactProblem(problems []Problem, key string) bool {
	for _, problem := range problems {
		if problem.Key == key {
			return true
		}
	}
	return false
}
