package live

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// Page-only feed. The Agent owns subscriptions; this reader cannot mutate them.
func hkRedemptionHandler(w http.ResponseWriter, r *http.Request) {
	home, err := os.UserHomeDir()
	if err != nil {
		http.Error(w, "home unavailable", 503)
		return
	}
	path := filepath.Join(home, "Library/Application Support/MachomeHub/data/redemption/hk-connect-snapshot.json")
	serveHKSnapshot(w, r, path, time.Now())
}
func serveHKSnapshot(w http.ResponseWriter, r *http.Request, path string, now time.Time) {
	w.Header().Set("Cache-Control", "no-store")
	f, err := os.Open(path)
	if err != nil {
		http.Error(w, "申赎后台尚未提供数据", 503)
		return
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 2*1024*1024+1))
	if err != nil || len(raw) > 2*1024*1024 {
		http.Error(w, "申赎快照读取失败", 503)
		return
	}
	var data map[string]any
	if json.Unmarshal(raw, &data) != nil || data["pool_id"] != "hk_connect" || data["schema_version"] != float64(1) {
		http.Error(w, "申赎快照格式错误", 503)
		return
	}
	stamp, _ := data["server_time"].(string)
	at, err := time.Parse(time.RFC3339Nano, stamp)
	stale := err != nil || now.Sub(at) > 5*time.Second || at.Sub(now) > 5*time.Second
	data["feed_stale"] = stale
	data["page_time"] = now.UTC().Format(time.RFC3339Nano)
	writeJSON(w, data)
}
