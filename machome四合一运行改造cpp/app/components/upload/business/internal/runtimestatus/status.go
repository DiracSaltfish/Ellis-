package runtimestatus

import (
	"context"
	"sync"
	"time"
)

type DailySyncer interface {
	SyncMissing(ctx context.Context, today time.Time) error
}

type scheduleAware interface {
	SetNextRunAt(next time.Time)
}

type Status struct {
	Key                 string     `json:"key"`
	Name                string     `json:"name"`
	Enabled             bool       `json:"enabled"`
	Status              string     `json:"status"`
	Schedule            string     `json:"schedule,omitempty"`
	Running             bool       `json:"running"`
	NextRunAt           *time.Time `json:"next_run_at,omitempty"`
	LastStartedAt       *time.Time `json:"last_started_at,omitempty"`
	LastFinishedAt      *time.Time `json:"last_finished_at,omitempty"`
	LastDurationSeconds float64    `json:"last_duration_seconds,omitempty"`
	LastSuccess         bool       `json:"last_success"`
	LastError           string     `json:"last_error,omitempty"`
	RunCount            int64      `json:"run_count"`
	SuccessCount        int64      `json:"success_count"`
	FailureCount        int64      `json:"failure_count"`
}

type Tracker struct {
	mu       sync.RWMutex
	tasks    []*Task
	stateDir string
}

type Task struct {
	mu        sync.RWMutex
	status    Status
	statePath string
}

type TrackedDailySyncer struct {
	task   *Task
	syncer DailySyncer
}

func NewTracker() *Tracker {
	return &Tracker{}
}

func (t *Tracker) Register(key string, name string, schedule string) *Task {
	task := NewTask(key, name, true, schedule, "")
	task.restore(t.stateDir)
	t.mu.Lock()
	defer t.mu.Unlock()
	t.tasks = append(t.tasks, task)
	return task
}

func (t *Tracker) RegisterDisabled(key string, name string, schedule string, reason string) *Task {
	task := NewTask(key, name, false, schedule, reason)
	t.mu.Lock()
	defer t.mu.Unlock()
	t.tasks = append(t.tasks, task)
	return task
}

func (t *Tracker) Statuses() []Status {
	t.mu.RLock()
	tasks := append([]*Task(nil), t.tasks...)
	t.mu.RUnlock()

	statuses := make([]Status, 0, len(tasks))
	for _, task := range tasks {
		statuses = append(statuses, task.Status())
	}
	return statuses
}

func NewTask(key string, name string, enabled bool, schedule string, disabledReason string) *Task {
	status := Status{
		Key:      key,
		Name:     name,
		Enabled:  enabled,
		Schedule: schedule,
		Status:   "idle",
	}
	if !enabled {
		status.Status = "disabled"
		status.LastError = disabledReason
	}
	return &Task{status: status}
}

func (t *Task) Wrap(syncer DailySyncer) *TrackedDailySyncer {
	return &TrackedDailySyncer{task: t, syncer: syncer}
}

func (t *Task) SetNextRunAt(next time.Time) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.status.NextRunAt = ptrTime(next)
}

func (t *Task) Run(ctx context.Context, today time.Time, run func(context.Context, time.Time) error) error {
	if run == nil {
		return nil
	}
	started := time.Now()
	t.mu.Lock()
	t.status.Enabled = true
	t.status.Running = true
	t.status.Status = "running"
	t.status.LastStartedAt = ptrTime(started)
	t.status.LastFinishedAt = nil
	t.status.LastDurationSeconds = 0
	t.status.LastError = ""
	t.status.LastSuccess = false
	t.status.RunCount++
	t.persistLocked()
	t.mu.Unlock()

	err := run(ctx, today)

	finished := time.Now()
	t.mu.Lock()
	defer t.mu.Unlock()
	t.status.Running = false
	t.status.LastFinishedAt = ptrTime(finished)
	t.status.LastDurationSeconds = finished.Sub(started).Seconds()
	t.status.LastSuccess = err == nil
	if err != nil {
		t.status.Status = "error"
		t.status.LastError = err.Error()
		t.status.FailureCount++
	} else {
		t.status.Status = "ok"
		t.status.LastError = ""
		t.status.SuccessCount++
	}
	t.persistLocked()
	return err
}

func (t *Task) Status() Status {
	t.mu.RLock()
	defer t.mu.RUnlock()
	status := t.status
	status.NextRunAt = cloneTimePtr(status.NextRunAt)
	status.LastStartedAt = cloneTimePtr(status.LastStartedAt)
	status.LastFinishedAt = cloneTimePtr(status.LastFinishedAt)
	return status
}

func (t *TrackedDailySyncer) SyncMissing(ctx context.Context, today time.Time) error {
	if t == nil || t.task == nil || t.syncer == nil {
		return nil
	}
	return t.task.Run(ctx, today, t.syncer.SyncMissing)
}

func (t *TrackedDailySyncer) SetNextRunAt(next time.Time) {
	if t == nil {
		return
	}
	if t.task != nil {
		t.task.SetNextRunAt(next)
	}
	if aware, ok := t.syncer.(scheduleAware); ok {
		aware.SetNextRunAt(next)
	}
}

func ptrTime(value time.Time) *time.Time {
	return &value
}

func cloneTimePtr(value *time.Time) *time.Time {
	if value == nil {
		return nil
	}
	cloned := *value
	return &cloned
}
