package live

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSignalSettingsPersistenceAndCAS(t *testing.T) {
	s := testService(t)
	post := func(body, remote string) int {
		r := httptest.NewRequest("POST", "/api/v1/manage/signal-settings", strings.NewReader(body))
		r.RemoteAddr = remote
		r.Header.Set("X-IOPV-Manage", "1")
		w := httptest.NewRecorder()
		s.signalSettingsHandler(w, r)
		return w.Code
	}
	valid := `{"premium_pct":1.5,"pull_pct":0.6,"radar_pct":0.8,"revision":1}`
	if got := post(valid, "192.168.1.10:123"); got != 403 {
		t.Fatalf("remote=%d", got)
	}
	for _, bad := range []string{`{"revision":1}`, `{"premium_pct":null,"pull_pct":0.6,"radar_pct":0.8,"revision":1}`, `{"premium_pct":101,"pull_pct":0.6,"radar_pct":0.8,"revision":1}`, valid + `{}`, strings.Replace(valid, `"revision":1`, `"revision":1,"unknown":2`, 1)} {
		if got := post(bad, "127.0.0.1:123"); got != 400 {
			t.Fatalf("invalid=%d for %s", got, bad)
		}
	}
	if got := post(valid, "127.0.0.1:123"); got != 200 {
		t.Fatalf("save=%d", got)
	}
	if got := post(valid, "127.0.0.1:123"); got != 409 {
		t.Fatalf("conflict=%d", got)
	}
	raw, err := os.ReadFile(filepath.Join(s.cfg.DataDir, "signal-settings.json"))
	if err != nil {
		t.Fatal(err)
	}
	var stored SignalSettings
	if err = json.Unmarshal(raw, &stored); err != nil {
		t.Fatal(err)
	}
	if stored.Radar != 0.8 || stored.Revision != 2 {
		t.Fatalf("stored=%+v", stored)
	}
	restored, err := New(s.cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer restored.store.DB.Close()
	if restored.signalSettings != stored {
		t.Fatalf("restart=%+v", restored.signalSettings)
	}
}
