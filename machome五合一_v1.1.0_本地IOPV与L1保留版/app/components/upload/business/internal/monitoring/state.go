package monitoring

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type Problem struct {
	Key      string
	Title    string
	Detail   string
	Severity string
}

type incidentState struct {
	Title          string    `json:"title"`
	Detail         string    `json:"detail"`
	Severity       string    `json:"severity"`
	OpenedAt       time.Time `json:"opened_at"`
	LastObservedAt time.Time `json:"last_observed_at"`
	LastAttemptAt  time.Time `json:"last_attempt_at,omitempty"`
	LastNotifiedAt time.Time `json:"last_notified_at,omitempty"`
	GoodCount      int       `json:"good_count,omitempty"`
}

type persistentState struct {
	Incidents map[string]incidentState `json:"incidents"`
	Sent      map[string]time.Time     `json:"sent"`
}

type IncidentManager struct {
	sender        Sender
	repeat        time.Duration
	retry         time.Duration
	recoverChecks int
	statePath     string
	logger        *slog.Logger
	now           func() time.Time
	state         persistentState
}

func NewIncidentManager(sender Sender, repeat time.Duration, statePath string, logger *slog.Logger) *IncidentManager {
	if repeat < time.Minute {
		repeat = 6 * time.Minute
	}
	if logger == nil {
		logger = slog.Default()
	}
	m := &IncidentManager{
		sender:        sender,
		repeat:        repeat,
		retry:         30 * time.Second,
		recoverChecks: 3,
		statePath:     strings.TrimSpace(statePath),
		logger:        logger,
		now:           time.Now,
		state: persistentState{
			Incidents: make(map[string]incidentState),
			Sent:      make(map[string]time.Time),
		},
	}
	m.load()
	return m
}

// Reconcile confirms realtime failures for one minute before notifying, repeats
// only after the configured interval, and closes notified incidents after several
// clean samples. An unnotified incident is cancelled on the first clean sample.
func (m *IncidentManager) Reconcile(ctx context.Context, problems []Problem) {
	m.ReconcileScope(ctx, "", problems)
}

// ReconcileScope updates only incidents whose keys start with scope. This lets
// market-session checks pause outside their active window without falsely
// reporting that a stale stream recovered merely because the market closed.
func (m *IncidentManager) ReconcileScope(ctx context.Context, scope string, problems []Problem, pausedScopes ...string) {
	if m == nil {
		return
	}
	scope = strings.TrimSpace(scope)
	now := m.now()
	paused := func(key string) bool {
		for _, prefix := range pausedScopes {
			if strings.HasPrefix(key, prefix) {
				return true
			}
		}
		return false
	}
	for _, prefix := range pausedScopes {
		m.PauseScope(prefix)
	}
	china := time.FixedZone("Asia/Shanghai", 8*60*60)
	for key, state := range m.state.Incidents {
		observed := state.LastObservedAt
		if observed.IsZero() {
			observed = state.OpenedAt
		}
		if strings.HasPrefix(key, scope) && incidentConfirmationDelay(key) > 0 && !observed.IsZero() && observed.In(china).Format("2006-01-02") != now.In(china).Format("2006-01-02") {
			delete(m.state.Incidents, key)
		}
	}
	active := make(map[string]Problem, len(problems))
	for _, problem := range problems {
		problem.Key = strings.TrimSpace(problem.Key)
		if problem.Key == "" || paused(problem.Key) {
			continue
		}
		if problem.Severity == "" {
			problem.Severity = "warning"
		}
		active[problem.Key] = problem
		state, exists := m.state.Incidents[problem.Key]
		if !exists {
			state = incidentState{OpenedAt: now}
			if incidentConfirmationDelay(problem.Key) > 0 {
				m.logger.Info("monitor incident pending", "incident", problem.Key, "detail", problem.Detail, "confirm_after", time.Minute)
			}
		}
		state.Title = problem.Title
		state.Detail = problem.Detail
		state.Severity = problem.Severity
		state.LastObservedAt = now
		state.GoodCount = 0
		shouldNotify := state.LastNotifiedAt.IsZero() || now.Sub(state.LastNotifiedAt) >= m.repeat
		canAttempt := state.LastAttemptAt.IsZero() || now.Sub(state.LastAttemptAt) >= m.retry
		confirmed := !state.LastNotifiedAt.IsZero() || now.Sub(state.OpenedAt) >= incidentConfirmationDelay(problem.Key)
		if confirmed && shouldNotify && canAttempt {
			state.LastAttemptAt = now
			m.state.Incidents[problem.Key] = state
			m.save()
			if err := m.sender.Send(ctx, "【NAVNAV 后端异常】"+problem.Title, incidentContent(problem, state.OpenedAt, now)); err != nil {
				m.logger.Warn("pushplus incident notification failed", "incident", problem.Key, "error", err)
			} else {
				state.LastNotifiedAt = now
				m.logger.Info("monitor incident notified", "incident", problem.Key, "opened_at", state.OpenedAt)
			}
		}
		m.state.Incidents[problem.Key] = state
	}

	keys := make([]string, 0, len(m.state.Incidents))
	for key := range m.state.Incidents {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		if scope != "" && !strings.HasPrefix(key, scope) {
			continue
		}
		if paused(key) {
			continue
		}
		if _, ok := active[key]; ok {
			continue
		}
		state := m.state.Incidents[key]
		if state.LastNotifiedAt.IsZero() {
			delete(m.state.Incidents, key)
			m.logger.Info("monitor pending incident cancelled", "incident", key, "reason", "healthy", "duration", now.Sub(state.OpenedAt))
			continue
		}
		state.GoodCount++
		if state.GoodCount < m.recoverChecks {
			m.state.Incidents[key] = state
			continue
		}
		delete(m.state.Incidents, key)
		content := fmt.Sprintf("## 故障已恢复\n\n- 事件：%s\n- 开始：%s\n- 恢复：%s\n- 持续：%s",
			state.Title,
			state.OpenedAt.Format(time.RFC3339),
			now.Format(time.RFC3339),
			now.Sub(state.OpenedAt).Round(time.Second),
		)
		if err := m.sender.Send(ctx, "【NAVNAV 后端恢复】"+state.Title, content); err != nil {
			m.logger.Warn("pushplus recovery notification failed", "incident", key, "error", err)
		} else {
			m.logger.Info("monitor recovery notified", "incident", key)
		}
	}
	m.save()
}

// Only realtime availability checks get an extra confirmation period. CFETS
// already has a ten-minute freshness tolerance; PCF and daily jobs are deadlines.
func incidentConfirmationDelay(key string) time.Duration {
	if strings.HasPrefix(key, "backend.public.") || key == "backend.core.snapshot" || key == "backend.core.snapshot_source" {
		return time.Minute
	}
	for _, prefix := range []string{"backend.private.missing", "backend.private.stale", "backend.private.ib"} {
		if key == prefix || strings.HasPrefix(key, prefix+".") {
			return time.Minute
		}
	}
	return 0
}

// PauseScope discards unconfirmed realtime incidents without declaring recovery.
// Keep delivered incidents so a session boundary cannot reset repeat throttling.
func (m *IncidentManager) PauseScope(scope string) {
	if m == nil {
		return
	}
	changed := false
	for key, state := range m.state.Incidents {
		if strings.HasPrefix(key, scope) && state.GoodCount != 0 {
			state.GoodCount = 0
			m.state.Incidents[key] = state
			changed = true
		}
		if strings.HasPrefix(key, scope) && incidentConfirmationDelay(key) > 0 && state.LastNotifiedAt.IsZero() {
			delete(m.state.Incidents, key)
			changed = true
			m.logger.Info("monitor pending incident cancelled", "incident", key, "reason", "outside monitor window")
		}
	}
	if changed {
		m.save()
	}
}

func (m *IncidentManager) SendOnce(ctx context.Context, key string, title string, content string) bool {
	if m == nil || strings.TrimSpace(key) == "" {
		return false
	}
	if _, sent := m.state.Sent[key]; sent {
		return true
	}
	if err := m.sender.Send(ctx, title, content); err != nil {
		m.logger.Warn("pushplus scheduled notification failed", "key", key, "error", err)
		return false
	}
	m.state.Sent[key] = m.now()
	m.pruneSent()
	m.save()
	return true
}

func incidentContent(problem Problem, openedAt time.Time, now time.Time) string {
	confirmation := ""
	if incidentConfirmationDelay(problem.Key) > 0 {
		confirmation = "\n- 确认：异常已连续存在至少 1 分钟"
	}
	return fmt.Sprintf("## 数据监控告警\n\n- 级别：%s\n- 首次发现：%s\n- 本次检查：%s\n- 详情：%s%s\n\n同一事件持续期间，重复通知间隔不少于 6 分钟。",
		problem.Severity,
		openedAt.Format(time.RFC3339),
		now.Format(time.RFC3339),
		strings.TrimSpace(problem.Detail),
		confirmation,
	)
}

func (m *IncidentManager) load() {
	if m.statePath == "" {
		return
	}
	data, err := os.ReadFile(m.statePath)
	if err != nil {
		if !os.IsNotExist(err) {
			m.logger.Warn("read monitor state failed", "error", err)
		}
		return
	}
	var state persistentState
	if err := json.Unmarshal(data, &state); err != nil {
		m.logger.Warn("decode monitor state failed", "error", err)
		return
	}
	if state.Incidents == nil {
		state.Incidents = make(map[string]incidentState)
	}
	if state.Sent == nil {
		state.Sent = make(map[string]time.Time)
	}
	// Downtime is not evidence of continuously observed failure. Retain delivered
	// incidents and daily report receipts, but start pending timers afresh.
	for key, incident := range state.Incidents {
		if incidentConfirmationDelay(key) > 0 && incident.LastNotifiedAt.IsZero() {
			delete(state.Incidents, key)
			m.logger.Info("monitor pending incident cancelled", "incident", key, "reason", "monitor restarted")
		}
	}
	m.state = state
}

func (m *IncidentManager) save() {
	if m == nil || m.statePath == "" {
		return
	}
	data, err := json.MarshalIndent(m.state, "", "  ")
	if err != nil {
		m.logger.Warn("encode monitor state failed", "error", err)
		return
	}
	if err := os.MkdirAll(filepath.Dir(m.statePath), 0o750); err != nil {
		m.logger.Warn("create monitor state directory failed", "error", err)
		return
	}
	tmp := m.statePath + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		m.logger.Warn("write monitor state failed", "error", err)
		return
	}
	if err := os.Rename(tmp, m.statePath); err != nil {
		m.logger.Warn("replace monitor state failed", "error", err)
	}
}

func (m *IncidentManager) pruneSent() {
	cutoff := m.now().AddDate(0, 0, -14)
	for key, sentAt := range m.state.Sent {
		if sentAt.Before(cutoff) {
			delete(m.state.Sent, key)
		}
	}
}
