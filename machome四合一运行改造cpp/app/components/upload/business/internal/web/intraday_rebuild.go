package web

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/websocket"
	"newnavnav/internal/privatevaluation"
)

const (
	intradayRebuildStateQueued     = "queued"
	intradayRebuildStateDispatched = "dispatched"
	intradayRebuildStateCapturing  = "capturing"
	intradayRebuildStateAwaitingFX = "awaiting_fx"
	intradayRebuildStateCompleted  = "completed"
	intradayRebuildStateFailed     = "failed"
	intradayRebuildStateCancelled  = "cancelled"
)

var intradayRebuildTerminalStates = map[string]bool{
	intradayRebuildStateCompleted: true,
	intradayRebuildStateFailed:    true,
	intradayRebuildStateCancelled: true,
}

// intradayRebuildJob deliberately contains only command metadata and progress.
// The private minute inputs themselves continue through the validated private
// history import endpoint, where the existing service rebuilds the snapshots.
type intradayRebuildJob struct {
	ID          string    `json:"id"`
	TradeDate   string    `json:"trade_date"`
	Symbols     []string  `json:"symbols"`
	FXPolicy    string    `json:"fx_policy"`
	AsOf        string    `json:"as_of,omitempty"`
	RequestedBy string    `json:"requested_by"`
	State       string    `json:"state"`
	Progress    int       `json:"progress"`
	Message     string    `json:"message,omitempty"`
	Error       string    `json:"error,omitempty"`
	AgentID     string    `json:"agent_id,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	StartedAt   time.Time `json:"started_at,omitempty"`
	FinishedAt  time.Time `json:"finished_at,omitempty"`
}

type intradayRebuildCreateRequest struct {
	TradeDate string   `json:"trade_date"`
	Symbols   []string `json:"symbols"`
	FXPolicy  string   `json:"fx_policy"`
	AsOf      string   `json:"as_of,omitempty"`
}

type intradayRebuildAgentStatus struct {
	Connected    bool      `json:"connected"`
	AgentID      string    `json:"agent_id,omitempty"`
	Version      string    `json:"version,omitempty"`
	Capabilities []string  `json:"capabilities,omitempty"`
	LastSeenAt   time.Time `json:"last_seen_at,omitempty"`
}

type intradayRebuildStatusResponse struct {
	Agent intradayRebuildAgentStatus `json:"agent"`
	Jobs  []intradayRebuildJob       `json:"jobs"`
}

type intradayRebuildWSMessage struct {
	Type         string              `json:"type"`
	AgentID      string              `json:"agent_id,omitempty"`
	Version      string              `json:"version,omitempty"`
	Capabilities []string            `json:"capabilities,omitempty"`
	JobID        string              `json:"job_id,omitempty"`
	State        string              `json:"state,omitempty"`
	Progress     int                 `json:"progress,omitempty"`
	Message      string              `json:"message,omitempty"`
	Error        string              `json:"error,omitempty"`
	Job          *intradayRebuildJob `json:"job,omitempty"`
}

type intradayRebuildAgent struct {
	id           string
	version      string
	capabilities []string
	conn         *websocket.Conn
	sendMu       sync.Mutex
	lastSeenAt   time.Time
}

type intradayRebuildController struct {
	mu        sync.Mutex
	jobs      map[string]intradayRebuildJob
	agent     *intradayRebuildAgent
	storePath string
}

func newIntradayRebuildController() *intradayRebuildController {
	return &intradayRebuildController{jobs: make(map[string]intradayRebuildJob)}
}

func (c *intradayRebuildController) setStorePath(path string) error {
	if c == nil {
		return errors.New("intraday rebuild controller is unavailable")
	}
	path = strings.TrimSpace(path)
	c.mu.Lock()
	defer c.mu.Unlock()
	c.storePath = path
	if path == "" {
		return nil
	}
	body, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var stored struct {
		Jobs []intradayRebuildJob `json:"jobs"`
	}
	if err := json.Unmarshal(body, &stored); err != nil {
		return fmt.Errorf("decode intraday rebuild job store: %w", err)
	}
	for _, job := range stored.Jobs {
		if job.ID == "" {
			continue
		}
		// A command may not have reached Mac-home before the web process
		// restarted. Requeue it rather than treating a memory-only dispatch as
		// successful.
		if job.State == intradayRebuildStateDispatched || job.State == intradayRebuildStateCapturing {
			job.State = intradayRebuildStateQueued
			job.Message = "网站重启后等待 Mac-home Agent 重新确认"
			job.UpdatedAt = time.Now()
		}
		c.jobs[job.ID] = job
	}
	return c.persistLocked()
}

func (c *intradayRebuildController) persistLocked() error {
	if c.storePath == "" {
		return nil
	}
	jobs := make([]intradayRebuildJob, 0, len(c.jobs))
	for _, job := range c.jobs {
		jobs = append(jobs, cloneIntradayRebuildJob(job))
	}
	sort.Slice(jobs, func(i, j int) bool { return jobs[i].CreatedAt.After(jobs[j].CreatedAt) })
	body, err := json.MarshalIndent(struct {
		Jobs []intradayRebuildJob `json:"jobs"`
	}{Jobs: jobs}, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(c.storePath), 0o750); err != nil {
		return err
	}
	temporary := c.storePath + ".tmp"
	if err := os.WriteFile(temporary, body, 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, c.storePath)
}

func cloneIntradayRebuildJob(job intradayRebuildJob) intradayRebuildJob {
	job.Symbols = append([]string(nil), job.Symbols...)
	return job
}

func (c *intradayRebuildController) create(request intradayRebuildCreateRequest, actor string, now time.Time) (intradayRebuildJob, error) {
	if c == nil {
		return intradayRebuildJob{}, errors.New("intraday rebuild controller is unavailable")
	}
	tradeDate, symbols, policy, asOf, err := normalizeIntradayRebuildRequest(request, now)
	if err != nil {
		return intradayRebuildJob{}, err
	}
	id, err := newIntradayRebuildID()
	if err != nil {
		return intradayRebuildJob{}, err
	}
	job := intradayRebuildJob{
		ID:          id,
		TradeDate:   tradeDate,
		Symbols:     symbols,
		FXPolicy:    policy,
		AsOf:        asOf,
		RequestedBy: strings.TrimSpace(actor),
		State:       intradayRebuildStateQueued,
		Message:     "等待 Mac-home Agent 连接",
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	c.mu.Lock()
	c.jobs[job.ID] = job
	if err := c.persistLocked(); err != nil {
		delete(c.jobs, job.ID)
		c.mu.Unlock()
		return intradayRebuildJob{}, err
	}
	c.dispatchLocked(job.ID)
	job = c.jobs[job.ID]
	c.mu.Unlock()
	return cloneIntradayRebuildJob(job), nil
}

func normalizeIntradayRebuildRequest(request intradayRebuildCreateRequest, now time.Time) (string, []string, string, string, error) {
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return "", nil, "", "", err
	}
	tradeDate := strings.TrimSpace(request.TradeDate)
	if tradeDate == "" {
		tradeDate = now.In(location).Format("2006-01-02")
	}
	parsedDate, err := time.ParseInLocation("2006-01-02", tradeDate, location)
	if err != nil || parsedDate.Weekday() == time.Saturday || parsedDate.Weekday() == time.Sunday {
		return "", nil, "", "", errors.New("trade_date must be a Shanghai trading weekday in YYYY-MM-DD format")
	}
	if parsedDate.Format("2006-01-02") != now.In(location).Format("2006-01-02") {
		return "", nil, "", "", errors.New("intraday rebuild only accepts the current Shanghai trade date")
	}
	policy := strings.TrimSpace(request.FXPolicy)
	if policy == "" {
		policy = "capture_only"
	}
	switch policy {
	case "capture_only", "provisional_previous_close", "provisional_safe_parity", "final_cfets":
	default:
		return "", nil, "", "", errors.New("unsupported fx_policy")
	}
	seen := make(map[string]struct{})
	symbols := make([]string, 0, len(request.Symbols))
	for _, raw := range request.Symbols {
		symbol := strings.ToUpper(strings.TrimSpace(raw))
		if symbol == "" {
			continue
		}
		if !privatevaluation.Supported(symbol) {
			return "", nil, "", "", fmt.Errorf("unsupported private fund %s", symbol)
		}
		if _, ok := seen[symbol]; ok {
			continue
		}
		seen[symbol] = struct{}{}
		symbols = append(symbols, symbol)
	}
	if len(symbols) == 0 {
		for _, definition := range privatevaluation.Definitions() {
			symbols = append(symbols, definition.Symbol)
		}
	}
	if len(symbols) > 64 {
		return "", nil, "", "", errors.New("too many symbols in intraday rebuild request")
	}
	sort.Strings(symbols)
	asOf := strings.TrimSpace(request.AsOf)
	if asOf != "" {
		value, parseErr := time.Parse(time.RFC3339, asOf)
		if parseErr != nil || value.In(location).Format("2006-01-02") != tradeDate || value.After(now.Add(time.Minute)) {
			return "", nil, "", "", errors.New("as_of must be a current-day RFC3339 timestamp")
		}
	}
	return tradeDate, symbols, policy, asOf, nil
}

func newIntradayRebuildID() (string, error) {
	bytes := make([]byte, 8)
	if _, err := rand.Read(bytes); err != nil {
		return "", err
	}
	return "intraday-rebuild-" + time.Now().UTC().Format("20060102T150405") + "-" + hex.EncodeToString(bytes), nil
}

func (c *intradayRebuildController) status() intradayRebuildStatusResponse {
	if c == nil {
		return intradayRebuildStatusResponse{}
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	response := intradayRebuildStatusResponse{Jobs: make([]intradayRebuildJob, 0, len(c.jobs))}
	if c.agent != nil {
		response.Agent = intradayRebuildAgentStatus{
			Connected: true, AgentID: c.agent.id, Version: c.agent.version,
			Capabilities: append([]string(nil), c.agent.capabilities...), LastSeenAt: c.agent.lastSeenAt,
		}
	}
	for _, job := range c.jobs {
		response.Jobs = append(response.Jobs, cloneIntradayRebuildJob(job))
	}
	sort.Slice(response.Jobs, func(i, j int) bool { return response.Jobs[i].CreatedAt.After(response.Jobs[j].CreatedAt) })
	return response
}

func (c *intradayRebuildController) job(id string) (intradayRebuildJob, bool) {
	if c == nil {
		return intradayRebuildJob{}, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	job, ok := c.jobs[id]
	return cloneIntradayRebuildJob(job), ok
}

func (c *intradayRebuildController) registerAgent(id, version string, capabilities []string, conn *websocket.Conn) {
	if c == nil || strings.TrimSpace(id) == "" || conn == nil {
		return
	}
	c.mu.Lock()
	c.agent = &intradayRebuildAgent{id: strings.TrimSpace(id), version: strings.TrimSpace(version), capabilities: normalizeCapabilities(capabilities), conn: conn, lastSeenAt: time.Now()}
	for jobID, job := range c.jobs {
		if job.State == intradayRebuildStateQueued || job.State == intradayRebuildStateDispatched {
			c.dispatchLocked(jobID)
		}
	}
	_ = c.persistLocked()
	c.mu.Unlock()
}

func normalizeCapabilities(values []string) []string {
	seen := make(map[string]struct{})
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func (c *intradayRebuildController) unregisterAgent(conn *websocket.Conn) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.agent != nil && c.agent.conn == conn {
		c.agent = nil
	}
}

func (c *intradayRebuildController) receive(message intradayRebuildWSMessage) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.agent != nil {
		c.agent.lastSeenAt = time.Now()
	}
	if message.JobID == "" {
		return
	}
	job, ok := c.jobs[message.JobID]
	if !ok || intradayRebuildTerminalStates[job.State] {
		return
	}
	if c.agent != nil {
		job.AgentID = c.agent.id
	}
	if strings.TrimSpace(message.State) != "" {
		job.State = strings.TrimSpace(message.State)
	}
	if message.Progress >= 0 && message.Progress <= 100 {
		job.Progress = message.Progress
	}
	if strings.TrimSpace(message.Message) != "" {
		job.Message = strings.TrimSpace(message.Message)
	}
	if strings.TrimSpace(message.Error) != "" {
		job.Error = strings.TrimSpace(message.Error)
	} else if job.State == intradayRebuildStateCapturing || job.State == intradayRebuildStateCompleted {
		// A retry that has moved back to capture must not keep displaying the
		// previous, expected "CFETS not yet published" error as a live failure.
		job.Error = ""
	}
	if job.State == intradayRebuildStateCapturing && job.StartedAt.IsZero() {
		job.StartedAt = time.Now()
	}
	if intradayRebuildTerminalStates[job.State] {
		job.FinishedAt = time.Now()
	}
	job.UpdatedAt = time.Now()
	c.jobs[job.ID] = job
	_ = c.persistLocked()
}

func (c *intradayRebuildController) dispatchLocked(id string) {
	if c.agent == nil || c.agent.conn == nil {
		return
	}
	job, ok := c.jobs[id]
	if !ok || intradayRebuildTerminalStates[job.State] {
		return
	}
	job.State = intradayRebuildStateDispatched
	job.AgentID = c.agent.id
	job.Message = "已通过 WSS 下发至 Mac-home Agent"
	job.UpdatedAt = time.Now()
	c.jobs[id] = job
	if err := c.sendLocked(intradayRebuildWSMessage{Type: "job_request", JobID: id, Job: &job}); err != nil {
		job.State = intradayRebuildStateQueued
		job.Message = "Mac-home Agent 发送失败，等待重连"
		job.Error = err.Error()
		job.UpdatedAt = time.Now()
		c.jobs[id] = job
	}
	_ = c.persistLocked()
}

func (c *intradayRebuildController) sendLocked(message intradayRebuildWSMessage) error {
	if c.agent == nil || c.agent.conn == nil {
		return errors.New("Mac-home Agent is offline")
	}
	c.agent.sendMu.Lock()
	defer c.agent.sendMu.Unlock()
	return websocket.JSON.Send(c.agent.conn, message)
}

func (s *Server) handleDebugIntradayRebuildJobs(w http.ResponseWriter, r *http.Request) {
	if s.intradayRebuild == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "intraday rebuild is unavailable"})
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/debug/private-intraday-rebuild/jobs")
	if path == "" || path == "/" {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, http.StatusOK, s.intradayRebuild.status())
		case http.MethodPost:
			var request intradayRebuildCreateRequest
			decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<10))
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&request); err != nil {
				writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
				return
			}
			if err := ensureJSONEOF(decoder); err != nil {
				writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
				return
			}
			job, err := s.intradayRebuild.create(request, s.debugActorName(r), time.Now())
			if err != nil {
				writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
				return
			}
			writeJSON(w, http.StatusAccepted, job)
		default:
			w.Header().Set("Allow", "GET, POST")
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		}
		return
	}
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	job, ok := s.intradayRebuild.job(strings.Trim(path, "/"))
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "intraday rebuild job not found"})
		return
	}
	writeJSON(w, http.StatusOK, job)
}

func (s *Server) handleIntradayRebuildWebSocket(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeIntradayRebuildAgent(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "intraday rebuild agent token required"})
		return
	}
	if s.intradayRebuild == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "intraday rebuild is unavailable"})
		return
	}
	websocket.Handler(func(conn *websocket.Conn) {
		defer s.intradayRebuild.unregisterAgent(conn)
		for {
			var message intradayRebuildWSMessage
			if err := websocket.JSON.Receive(conn, &message); err != nil {
				return
			}
			switch message.Type {
			case "hello":
				s.intradayRebuild.registerAgent(message.AgentID, message.Version, message.Capabilities, conn)
				_ = websocket.JSON.Send(conn, intradayRebuildWSMessage{Type: "hello_ack", Message: "intraday rebuild agent registered"})
			case "job_ack", "job_progress", "job_complete", "job_waiting_fx", "job_failed", "job_cancelled", "ping":
				if message.Type == "job_complete" {
					message.State = intradayRebuildStateCompleted
					message.Progress = 100
				} else if message.Type == "job_waiting_fx" {
					message.State = intradayRebuildStateAwaitingFX
				} else if message.Type == "job_failed" {
					message.State = intradayRebuildStateFailed
				} else if message.Type == "job_cancelled" {
					message.State = intradayRebuildStateCancelled
				}
				s.intradayRebuild.receive(message)
				if message.Type == "ping" {
					_ = websocket.JSON.Send(conn, intradayRebuildWSMessage{Type: "pong"})
				}
			default:
				_ = websocket.JSON.Send(conn, intradayRebuildWSMessage{Type: "error", Error: "unsupported intraday rebuild message"})
			}
		}
	}).ServeHTTP(w, r)
}
