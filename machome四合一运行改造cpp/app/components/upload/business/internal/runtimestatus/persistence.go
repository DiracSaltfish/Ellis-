package runtimestatus

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
)

// Each task has an atomic receipt. A process restart must not erase evidence
// that the previous evening's calibration completed successfully.
func NewPersistentTracker(directory string) (*Tracker, error) {
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return nil, os.ErrPermission
	}
	return &Tracker{stateDir: directory}, nil
}

func (t *Task) restore(directory string) {
	if directory == "" {
		return
	}
	digest := sha256.Sum256([]byte(t.status.Key))
	t.statePath = filepath.Join(directory, hex.EncodeToString(digest[:])+".json")
	info, err := os.Lstat(t.statePath)
	if os.IsNotExist(err) {
		return
	}
	if err != nil || !info.Mode().IsRegular() {
		return
	}
	data, err := os.ReadFile(t.statePath)
	var stored Status
	if err != nil || json.Unmarshal(data, &stored) != nil || stored.Key != t.status.Key || stored.Schedule != t.status.Schedule {
		return
	}
	stored.Name = t.status.Name
	stored.Enabled = t.status.Enabled
	stored.NextRunAt = nil // Recomputed by the current scheduler.
	if stored.Running {
		stored.Running = false
		stored.LastSuccess = false
		stored.Status = "interrupted"
		stored.LastError = "process stopped before task completion was recorded"
	}
	t.status = stored
}

// Caller holds the task mutex, so concurrent runs cannot replace newer receipts.
func (t *Task) persistLocked() {
	if t.statePath == "" {
		return
	}
	data, err := json.Marshal(t.status)
	if err != nil {
		return
	}
	f, err := os.CreateTemp(filepath.Dir(t.statePath), ".task-*")
	if err == nil {
		defer os.Remove(f.Name())
		_, err = f.Write(data)
		if err == nil {
			err = f.Sync()
		}
		closeErr := f.Close()
		if err == nil {
			err = closeErr
		}
		if err == nil {
			err = os.Rename(f.Name(), t.statePath)
		}
	}
	if err != nil {
		slog.Error("task receipt persistence failed", "task", t.status.Key, "error", err)
	}
}
