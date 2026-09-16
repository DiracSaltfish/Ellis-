package live

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestHKSnapshotQuality(t *testing.T) {
	p := filepath.Join(t.TempDir(), "snapshot.json")
	now := time.Now().UTC()
	for _, tc := range []struct {
		name, body string
		status     int
		stale      bool
	}{
		{"missing", "", 503, false}, {"malformed", "{", 503, false}, {"wrong pool", `{"pool_id":"legacy","schema_version":1}`, 503, false},
		{"fresh", `{"pool_id":"hk_connect","schema_version":1,"server_time":"` + now.Format(time.RFC3339Nano) + `","items":[]}`, 200, false},
		{"old", `{"pool_id":"hk_connect","schema_version":1,"server_time":"2020-01-01T00:00:00Z","items":[]}`, 200, true},
		{"future", `{"pool_id":"hk_connect","schema_version":1,"server_time":"2099-01-01T00:00:00Z","items":[]}`, 200, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.body != "" {
				os.WriteFile(p, []byte(tc.body), 0600)
			}
			w := httptest.NewRecorder()
			serveHKSnapshot(w, httptest.NewRequest("GET", "/", nil), p, now)
			if w.Code != tc.status {
				t.Fatalf("status %d", w.Code)
			}
			if tc.status == 200 {
				var d map[string]any
				json.Unmarshal(w.Body.Bytes(), &d)
				if d["feed_stale"] != tc.stale {
					t.Fatal(d)
				}
			}
		})
	}
}
