package live

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
)

type SignalSettings struct {
	Premium  float64 `json:"premium_pct"`
	Pull     float64 `json:"pull_pct"`
	Radar    float64 `json:"radar_pct"`
	Revision int64   `json:"revision"`
}

func defaultSignalSettings() SignalSettings { return SignalSettings{1.5, 0.6, 0.6, 1} }
func (v SignalSettings) validate() error {
	for _, n := range []float64{v.Premium, v.Pull, v.Radar} {
		if math.IsNaN(n) || math.IsInf(n, 0) || n < -20 || n > 100 {
			return fmt.Errorf("premium threshold must be -20..100 percent")
		}
	}
	return nil
}
func (s *Service) signalSettingsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == "GET" {
		s.mu.RLock()
		v := s.signalSettings
		s.mu.RUnlock()
		writeJSON(w, v)
		return
	}
	if !localRequest(r) || r.Header.Get("X-IOPV-Manage") != "1" {
		http.Error(w, "local management only", 403)
		return
	}
	var input struct {
		Premium  *float64 `json:"premium_pct"`
		Pull     *float64 `json:"pull_pct"`
		Radar    *float64 `json:"radar_pct"`
		Revision *int64   `json:"revision"`
	}
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2048))
	d.DisallowUnknownFields()
	if d.Decode(&input) != nil || input.Premium == nil || input.Pull == nil || input.Radar == nil || input.Revision == nil || d.Decode(new(any)) != io.EOF {
		http.Error(w, "invalid thresholds", 400)
		return
	}
	v := SignalSettings{*input.Premium, *input.Pull, *input.Radar, *input.Revision}
	if v.validate() != nil {
		http.Error(w, "invalid thresholds", 400)
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if v.Revision != s.signalSettings.Revision {
		http.Error(w, "settings changed; refresh first", 409)
		return
	}
	v.Revision++
	raw, _ := json.Marshal(v)
	path := filepath.Join(s.cfg.DataDir, "signal-settings.json")
	f, e := os.CreateTemp(s.cfg.DataDir, "signal-settings-*.tmp")
	if e != nil {
		http.Error(w, "storage unavailable", 500)
		return
	}
	name := f.Name()
	defer os.Remove(name)
	if _, e = f.Write(raw); e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e == nil {
		e = os.Rename(name, path)
	}
	if e != nil {
		http.Error(w, "save failed", 500)
		return
	}
	s.signalSettings = v
	s.store.Event("signal_settings", string(raw))
	writeJSON(w, v)
}
