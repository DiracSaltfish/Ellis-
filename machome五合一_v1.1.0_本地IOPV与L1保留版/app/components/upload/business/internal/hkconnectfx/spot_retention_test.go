package hkconnectfx

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"
)

func TestPartialSpotHealthPersistsAcrossWeekendRestart(t *testing.T) {
	now := localTime(t, "2026-09-04 14:58:00")
	body := func(stamp, usd, jpy string) string {
		return fmt.Sprintf(`{"head":{"rep_code":"200"},"data":{"showDateCN":%q},"records":[{"ccyPair":"USD/CNY","bidPrc":%q,"askPrc":%q},{"ccyPair":"100JPY/CNY","bidPrc":%q,"askPrc":%q}]}`, stamp, usd, usd, jpy, jpy)
	}
	response := body("2026-09-04 14:58:00", "6.9", "4.2")
	dir := t.TempDir()
	client := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) { return jsonResponse(response), nil })}
	s := NewService(Options{DataDir: dir, Now: func() time.Time { return now }, HTTPClient: client})
	s.pollCFETSSpot(context.Background(), now)
	now = localTime(t, "2026-09-07 09:20:00")
	response = body("2026-09-07 09:20:00", "---", "4.3")
	s = NewService(Options{DataDir: dir, Now: func() time.Time { return now }, HTTPClient: client})
	if s.Snapshot().FXQuotes["100JPY/CNY"].Healthy {
		t.Fatal("restored quote cannot be live")
	}
	s.pollCFETSSpot(context.Background(), now)
	got := s.Snapshot()
	if !got.FXQuotes["100JPY/CNY"].Healthy || got.FXQuotes["USD/CNY"].Healthy {
		t.Fatal("unrelated missing USD blocked JPY")
	}
	usd := got.LastHealthyFXQuotes["USD/CNY"]
	if usd.ObservedAt.Format("2006-01-02") != "2026-09-04" || !usd.ObservedAt.Equal(*usd.ReceivedAt) {
		t.Fatal("prior timestamps rewritten")
	}
	// A fresh receipt with an old upstream timestamp must neither refresh health nor overwrite retention.
	response = body("2026-09-04 14:58:00", "7.1", "4.1")
	s.pollCFETSSpot(context.Background(), now)
	if s.Snapshot().FXQuotes["100JPY/CNY"].Healthy {
		t.Fatal("stale source became healthy")
	}
	if *s.Snapshot().LastHealthyFXQuotes["100JPY/CNY"].Bid != 4.3 {
		t.Fatal("retention regressed")
	}
	response = body("2026-09-07 09:20:00", "6.9", "4.3")
	s.pollCFETSSpot(context.Background(), now)
	now = now.Add(181 * time.Second)
	if s.Snapshot().FXQuotes["USD/CNY"].Healthy {
		t.Fatal("stopped poller remained healthy")
	}
}

func TestDuplicatePairDoesNotPoisonOtherCurrency(t *testing.T) {
	now := localTime(t, "2026-09-07 09:30:00")
	response := `{"head":{"rep_code":"200"},"data":{"showDateCN":"2026-09-07 09:30:00"},"records":[{"ccyPair":"USD/CNY","bidPrc":"6.9","askPrc":"6.9"},{"ccyPair":"USD/CNY","bidPrc":"7.0","askPrc":"7.0"},{"ccyPair":"100JPY/CNY","bidPrc":"4.2","askPrc":"4.3"}]}`
	s := NewService(Options{DataDir: t.TempDir(), HTTPClient: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) { return jsonResponse(response), nil })}})
	quotes, err := s.fetchCFETSSpot(context.Background(), now)
	if err != nil || quotes["USD/CNY"].Healthy || !quotes["100JPY/CNY"].Healthy {
		t.Fatalf("per-pair duplicate check: %v %+v", err, quotes)
	}
}

func TestResponseTimestampCannotReplaceMissingSourceObservation(t *testing.T) {
	now := localTime(t, "2026-09-07 09:30:00")
	response := fmt.Sprintf(`{"head":{"rep_code":"200","ts":%d},"data":{"showDateCN":"---"},"records":[{"ccyPair":"100JPY/CNY","bidPrc":"4.2","askPrc":"4.3"}]}`, now.UnixMilli())
	s := NewService(Options{DataDir: t.TempDir(), HTTPClient: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) { return jsonResponse(response), nil })}})
	if _, err := s.fetchCFETSSpot(context.Background(), now); err == nil {
		t.Fatal("transport timestamp manufactured current FX")
	}
}
