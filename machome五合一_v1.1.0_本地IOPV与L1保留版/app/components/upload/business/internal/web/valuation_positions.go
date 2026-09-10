package web

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/websocket"
	"newnavnav/internal/snapshot"
)

const (
	valuationPositionSyncDefault      = "default"
	valuationPositionSyncSynced       = "synced"
	valuationPositionSyncMissingLocal = "missing_local"
	valuationPositionSyncMismatch     = "mismatch"
	valuationPositionSyncDrift        = "drift"
	valuationPositionSyncUnknown      = "unknown"

	valuationPositionUpdateTimeout   = 30 * time.Second
	maxValuationPositionRequestBytes = 8 << 10
)

type valuationPositionStateItem struct {
	Symbol    string  `json:"symbol"`
	Ratio     float64 `json:"ratio"`
	Source    string  `json:"source,omitempty"`
	UpdatedAt string  `json:"updated_at,omitempty"`
}

type valuationPositionUpdateItem struct {
	Symbol      string  `json:"symbol"`
	Ratio       float64 `json:"ratio"`
	RequestedBy string  `json:"requested_by,omitempty"`
}

type valuationPositionUpdateRequest struct {
	Symbol string  `json:"symbol"`
	Ratio  float64 `json:"ratio"`
}

type valuationPositionUploaderStatus struct {
	Connected   bool   `json:"connected"`
	ClientID    string `json:"client_id,omitempty"`
	Source      string `json:"source,omitempty"`
	LastSeenAt  string `json:"last_seen_at,omitempty"`
	KnownStates int    `json:"known_states"`
}

type valuationPositionRatioStatus struct {
	Symbol                  string   `json:"symbol"`
	Name                    string   `json:"name"`
	ModelVersion            string   `json:"model_version"`
	ReferenceSymbol         string   `json:"reference_symbol,omitempty"`
	EffectiveRatio          float64  `json:"effective_ratio"`
	DefaultEffectiveRatio   float64  `json:"default_effective_ratio"`
	EffectiveRatioSource    string   `json:"effective_ratio_source,omitempty"`
	ManualOverrideRatio     *float64 `json:"manual_override_ratio,omitempty"`
	ManualOverrideSource    string   `json:"manual_override_source,omitempty"`
	ManualOverrideUpdatedAt string   `json:"manual_override_updated_at,omitempty"`
	ManualOverrideUpdatedBy string   `json:"manual_override_updated_by,omitempty"`
	UploaderLocalRatio      *float64 `json:"uploader_local_ratio,omitempty"`
	UploaderLocalSource     string   `json:"uploader_local_source,omitempty"`
	UploaderLocalUpdatedAt  string   `json:"uploader_local_updated_at,omitempty"`
	UploaderSyncStatus      string   `json:"uploader_sync_status,omitempty"`
}

type navSettingsStatusResponse struct {
	ServerTime       string                          `json:"server_time"`
	MinuteHistoryDay string                          `json:"minute_history_day"`
	Ratios           []valuationPositionRatioStatus  `json:"ratios"`
	Operations       []string                        `json:"operations"`
	Uploader         valuationPositionUploaderStatus `json:"uploader"`
}

type valuationPositionApplyResult struct {
	RequestID    string
	ClientID     string
	ClientSource string
	States       map[string]valuationPositionStateItem
	Error        string
}

type valuationPositionStatusSnapshot struct {
	Uploader valuationPositionUploaderStatus
	States   map[string]valuationPositionStateItem
}

type valuationPositionController interface {
	Snapshot() valuationPositionStatusSnapshot
	ApplyOverride(ctx context.Context, symbol string, ratio float64, actor string) (valuationPositionApplyResult, error)
}

type wsValuationPositionController struct {
	mu      sync.Mutex
	clients map[string]*wsValuationPositionClient
	states  map[string]map[string]valuationPositionStateItem
}

type wsValuationPositionClient struct {
	id         string
	source     string
	conn       *websocket.Conn
	sendMu     sync.Mutex
	lastSeenAt time.Time
	active     bool
	pending    map[string]chan valuationPositionApplyResult
}

func (s *Server) valuationPositionWSController() *wsValuationPositionController {
	controller, _ := s.valuationPositions.(*wsValuationPositionController)
	return controller
}

func newWSValuationPositionController() *wsValuationPositionController {
	return &wsValuationPositionController{
		clients: map[string]*wsValuationPositionClient{},
		states:  map[string]map[string]valuationPositionStateItem{},
	}
}

func (c *wsValuationPositionController) Snapshot() valuationPositionStatusSnapshot {
	if c == nil {
		return valuationPositionStatusSnapshot{States: map[string]valuationPositionStateItem{}}
	}
	c.mu.Lock()
	defer c.mu.Unlock()

	connected := c.preferredClientLocked(true)
	known := connected
	if known == nil {
		known = c.preferredClientLocked(false)
	}
	snapshot := valuationPositionStatusSnapshot{
		States: map[string]valuationPositionStateItem{},
	}
	if connected != nil {
		snapshot.Uploader.Connected = true
		snapshot.Uploader.ClientID = connected.id
		snapshot.Uploader.Source = connected.source
		if !connected.lastSeenAt.IsZero() {
			snapshot.Uploader.LastSeenAt = connected.lastSeenAt.Format(time.RFC3339Nano)
		}
	}
	if known == nil {
		return snapshot
	}
	for symbol, item := range c.states[known.id] {
		snapshot.States[symbol] = item
	}
	snapshot.Uploader.KnownStates = len(snapshot.States)
	if snapshot.Uploader.ClientID == "" {
		snapshot.Uploader.ClientID = known.id
		snapshot.Uploader.Source = known.source
		if !known.lastSeenAt.IsZero() {
			snapshot.Uploader.LastSeenAt = known.lastSeenAt.Format(time.RFC3339Nano)
		}
	}
	return snapshot
}

func (c *wsValuationPositionController) ApplyOverride(ctx context.Context, symbol string, ratio float64, actor string) (valuationPositionApplyResult, error) {
	if c == nil {
		return valuationPositionApplyResult{}, errors.New("valuation position controller is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" || ratio <= 0 {
		return valuationPositionApplyResult{}, errors.New("symbol and ratio are required")
	}

	c.mu.Lock()
	client := c.preferredClientLocked(true)
	if client == nil {
		c.mu.Unlock()
		return valuationPositionApplyResult{}, errors.New("no active uploader websocket client")
	}
	requestID := fmt.Sprintf("valuation-position-%d", time.Now().UnixNano())
	ch := make(chan valuationPositionApplyResult, 1)
	client.pending[requestID] = ch
	c.mu.Unlock()

	sendErr := c.send(client.id, quoteWSResponse{
		Type:       "valuation_position_set_request",
		RequestID:  requestID,
		ServerTime: time.Now().Format(time.RFC3339Nano),
		ValuationPositionUpdates: []valuationPositionUpdateItem{{
			Symbol:      symbol,
			Ratio:       ratio,
			RequestedBy: strings.TrimSpace(actor),
		}},
	})
	if sendErr != nil {
		c.finishPending(client.id, requestID, valuationPositionApplyResult{})
		return valuationPositionApplyResult{}, sendErr
	}

	timeout := valuationPositionUpdateTimeout
	if deadline, ok := ctx.Deadline(); ok {
		timeout = time.Until(deadline)
	}
	if timeout <= 0 {
		timeout = valuationPositionUpdateTimeout
	}

	select {
	case result := <-ch:
		if result.Error != "" {
			return result, errors.New(result.Error)
		}
		return result, nil
	case <-ctx.Done():
		c.finishPending(client.id, requestID, valuationPositionApplyResult{})
		return valuationPositionApplyResult{}, ctx.Err()
	case <-time.After(timeout):
		c.finishPending(client.id, requestID, valuationPositionApplyResult{})
		return valuationPositionApplyResult{}, errors.New("timed out waiting for uploader override acknowledgement")
	}
}

func (c *wsValuationPositionController) registerClient(id string, source string, conn *websocket.Conn) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	client := &wsValuationPositionClient{
		id:         id,
		source:     source,
		conn:       conn,
		lastSeenAt: time.Now(),
		active:     true,
		pending:    map[string]chan valuationPositionApplyResult{},
	}
	c.clients[id] = client
	if _, ok := c.states[id]; !ok {
		c.states[id] = map[string]valuationPositionStateItem{}
	}
}

func (c *wsValuationPositionController) unregisterClient(id string) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	client, ok := c.clients[id]
	if !ok {
		return
	}
	client.active = false
	client.lastSeenAt = time.Now()
}

func (c *wsValuationPositionController) markSeen(id string) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if client, ok := c.clients[id]; ok {
		client.lastSeenAt = time.Now()
		client.active = true
	}
}

func (c *wsValuationPositionController) requestState(id string) {
	if c == nil {
		return
	}
	_ = c.send(id, quoteWSResponse{
		Type:       "valuation_position_state_request",
		RequestID:  fmt.Sprintf("valuation-position-state-%d", time.Now().UnixNano()),
		ServerTime: time.Now().Format(time.RFC3339Nano),
	})
}

func (c *wsValuationPositionController) handleMessage(id string, msg quoteWSMessage) bool {
	if c == nil {
		return false
	}
	switch msg.Type {
	case "valuation_position_state", "valuation_position_set_result":
		states := normalizeValuationPositionStates(msg.ValuationPositionStates)
		c.mu.Lock()
		if _, ok := c.states[id]; !ok {
			c.states[id] = map[string]valuationPositionStateItem{}
		}
		if msg.Type == "valuation_position_state" {
			c.states[id] = states
		} else {
			for symbol, item := range states {
				c.states[id][symbol] = item
			}
		}
		result := valuationPositionApplyResult{
			RequestID:    msg.RequestID,
			ClientID:     id,
			ClientSource: c.clientSourceLocked(id),
			States:       states,
			Error:        strings.TrimSpace(msg.Error),
		}
		ch := c.takePendingLocked(id, msg.RequestID)
		c.mu.Unlock()
		if ch != nil {
			ch <- result
		}
		return true
	case "error":
		if strings.TrimSpace(msg.RequestID) == "" {
			return false
		}
		c.mu.Lock()
		ch := c.takePendingLocked(id, msg.RequestID)
		c.mu.Unlock()
		if ch != nil {
			ch <- valuationPositionApplyResult{
				RequestID:    msg.RequestID,
				ClientID:     id,
				ClientSource: "",
				Error:        strings.TrimSpace(msg.Error),
			}
			return true
		}
	}
	return false
}

func (c *wsValuationPositionController) send(id string, payload quoteWSResponse) error {
	c.mu.Lock()
	client := c.clients[id]
	c.mu.Unlock()
	if client == nil || client.conn == nil {
		return errors.New("uploader websocket client is unavailable")
	}
	client.sendMu.Lock()
	defer client.sendMu.Unlock()
	if err := websocket.JSON.Send(client.conn, payload); err != nil {
		return err
	}
	return nil
}

func (c *wsValuationPositionController) finishPending(id string, requestID string, result valuationPositionApplyResult) {
	c.mu.Lock()
	ch := c.takePendingLocked(id, requestID)
	c.mu.Unlock()
	if ch != nil {
		select {
		case ch <- result:
		default:
		}
	}
}

func (c *wsValuationPositionController) clientSourceLocked(id string) string {
	if client, ok := c.clients[id]; ok {
		return client.source
	}
	return ""
}

func (c *wsValuationPositionController) takePendingLocked(id string, requestID string) chan valuationPositionApplyResult {
	client, ok := c.clients[id]
	if !ok || requestID == "" {
		return nil
	}
	ch := client.pending[requestID]
	delete(client.pending, requestID)
	return ch
}

func (c *wsValuationPositionController) preferredClientLocked(connectedOnly bool) *wsValuationPositionClient {
	var best *wsValuationPositionClient
	for _, client := range c.clients {
		if client == nil {
			continue
		}
		if connectedOnly && !client.active {
			continue
		}
		if best == nil {
			best = client
			continue
		}
		bestHome := strings.Contains(strings.ToLower(best.source), "home-mac")
		clientHome := strings.Contains(strings.ToLower(client.source), "home-mac")
		switch {
		case clientHome && !bestHome:
			best = client
		case clientHome == bestHome && client.lastSeenAt.After(best.lastSeenAt):
			best = client
		}
	}
	return best
}

func normalizeValuationPositionStates(items []valuationPositionStateItem) map[string]valuationPositionStateItem {
	out := make(map[string]valuationPositionStateItem, len(items))
	for _, item := range items {
		symbol := strings.ToUpper(strings.TrimSpace(item.Symbol))
		if symbol == "" || item.Ratio <= 0 {
			continue
		}
		item.Symbol = symbol
		out[symbol] = item
	}
	return out
}

func buildNavSettingsStatusResponse(base snapshot.NavSettingsStatus, control valuationPositionController) navSettingsStatusResponse {
	response := navSettingsStatusResponse{
		ServerTime:       base.ServerTime,
		MinuteHistoryDay: base.MinuteHistoryDay,
		Operations:       append([]string(nil), base.Operations...),
		Ratios:           make([]valuationPositionRatioStatus, 0, len(base.Ratios)),
	}
	snapshotState := valuationPositionStatusSnapshot{States: map[string]valuationPositionStateItem{}}
	if control != nil {
		snapshotState = control.Snapshot()
	}
	response.Uploader = snapshotState.Uploader
	for _, row := range base.Ratios {
		current := valuationPositionRatioStatus{
			Symbol:                  row.Symbol,
			Name:                    row.Name,
			ModelVersion:            row.ModelVersion,
			ReferenceSymbol:         row.ReferenceSymbol,
			EffectiveRatio:          row.EffectiveRatio,
			DefaultEffectiveRatio:   row.DefaultEffectiveRatio,
			EffectiveRatioSource:    row.EffectiveRatioSource,
			ManualOverrideRatio:     row.ManualOverrideRatio,
			ManualOverrideSource:    row.ManualOverrideSource,
			ManualOverrideUpdatedAt: row.ManualOverrideUpdatedAt,
			ManualOverrideUpdatedBy: row.ManualOverrideUpdatedBy,
			UploaderSyncStatus:      valuationPositionSyncUnknown,
		}
		local, hasLocal := snapshotState.States[row.Symbol]
		if hasLocal {
			value := round6(local.Ratio)
			current.UploaderLocalRatio = &value
			current.UploaderLocalSource = local.Source
			current.UploaderLocalUpdatedAt = local.UpdatedAt
		}
		current.UploaderSyncStatus = valuationPositionSyncStatus(row, local, hasLocal)
		response.Ratios = append(response.Ratios, current)
	}
	sort.Slice(response.Ratios, func(i, j int) bool {
		return response.Ratios[i].Symbol < response.Ratios[j].Symbol
	})
	return response
}

func valuationPositionSyncStatus(row snapshot.NavSettingsRatioStatus, local valuationPositionStateItem, hasLocal bool) string {
	switch {
	case row.ManualOverrideRatio == nil && !hasLocal:
		return valuationPositionSyncDefault
	case row.ManualOverrideRatio == nil && hasLocal:
		return valuationPositionSyncDrift
	case row.ManualOverrideRatio != nil && !hasLocal:
		return valuationPositionSyncMissingLocal
	case row.ManualOverrideRatio != nil && hasLocal && math.Abs(*row.ManualOverrideRatio-local.Ratio) <= 1e-9:
		return valuationPositionSyncSynced
	default:
		return valuationPositionSyncMismatch
	}
}

func round6(value float64) float64 {
	return math.Round(value*1e6) / 1e6
}

func (s *Server) handleNavSettingsStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, buildNavSettingsStatusResponse(s.snapshots.NavSettingsStatus(time.Now()), s.valuationPositions))
}

func (s *Server) handleNavSettingsUpdateValuationPosition(w http.ResponseWriter, r *http.Request) {
	var req valuationPositionUpdateRequest
	if err := decodeStrictJSONBody(w, r, maxValuationPositionRequestBytes, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(req.Symbol))
	if symbol == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "symbol is required"})
		return
	}
	if math.IsNaN(req.Ratio) || math.IsInf(req.Ratio, 0) || req.Ratio <= 0 || req.Ratio > 1.2 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "ratio must be within (0, 1.2]"})
		return
	}

	current := s.snapshots.NavSettingsStatus(time.Now())
	if _, ok := findNavSettingsRatio(current.Ratios, symbol); !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "symbol is not supported by current valuation controls"})
		return
	}
	if s.valuationPositions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "valuation position controller is unavailable"})
		return
	}

	actor := s.debugActorName(r)
	ctx, cancel := context.WithTimeout(r.Context(), valuationPositionUpdateTimeout)
	defer cancel()
	result, err := s.valuationPositions.ApplyOverride(ctx, symbol, req.Ratio, actor)
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": err.Error()})
		return
	}

	source := "debug_ws"
	if result.ClientSource != "" {
		source = "debug_ws:" + strings.TrimSpace(result.ClientSource)
	}
	if err := s.snapshots.UpdateManualValuationPosition(r.Context(), symbol, req.Ratio, source, actor); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	refreshErr := s.snapshots.Refresh(r.Context())
	s.snapshots.RecordEvent("info", "valuation_position", "manual valuation position updated", map[string]any{
		"symbol":        symbol,
		"ratio":         round6(req.Ratio),
		"updated_by":    actor,
		"uploader":      result.ClientSource,
		"uploader_id":   result.ClientID,
		"request_id":    result.RequestID,
		"refresh_error": errorString(refreshErr),
	})

	status := buildNavSettingsStatusResponse(s.snapshots.NavSettingsStatus(time.Now()), s.valuationPositions)
	row, _ := findValuationPositionRatioStatus(status.Ratios, symbol)
	payload := map[string]any{
		"ok":              true,
		"symbol":          symbol,
		"effective_ratio": round6(req.Ratio),
		"uploader_source": result.ClientSource,
		"message":         fmt.Sprintf("%s 估值仓位已更新为 %.4f", symbol, round6(req.Ratio)),
		"status":          status,
	}
	if row != nil {
		payload["row"] = row
		payload["sync_status"] = row.UploaderSyncStatus
	}
	if refreshErr != nil {
		payload["refresh_error"] = refreshErr.Error()
	}
	writeJSON(w, http.StatusOK, payload)
}

func findNavSettingsRatio(rows []snapshot.NavSettingsRatioStatus, symbol string) (*snapshot.NavSettingsRatioStatus, bool) {
	for index := range rows {
		if rows[index].Symbol == symbol {
			return &rows[index], true
		}
	}
	return nil, false
}

func findValuationPositionRatioStatus(rows []valuationPositionRatioStatus, symbol string) (*valuationPositionRatioStatus, bool) {
	for index := range rows {
		if rows[index].Symbol == symbol {
			return &rows[index], true
		}
	}
	return nil, false
}

func errorString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
