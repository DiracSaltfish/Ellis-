package snapshot

import (
	"sort"
	"time"

	"newnavnav/internal/domain"
)

const maxSystemEvents = 200

type SystemEvent struct {
	At      time.Time      `json:"at"`
	Level   string         `json:"level"`
	Source  string         `json:"source"`
	Message string         `json:"message"`
	Details map[string]any `json:"details,omitempty"`
}

type WSClientStatus struct {
	ID           string    `json:"id"`
	Source       string    `json:"source"`
	RemoteAddr   string    `json:"remote_addr"`
	ConnectedAt  time.Time `json:"connected_at"`
	LastSeenAt   time.Time `json:"last_seen_at"`
	LastUploadAt time.Time `json:"last_upload_at,omitempty"`
	UploadCount  int       `json:"upload_count"`
	QuoteCount   int       `json:"quote_count"`
	ErrorCount   int       `json:"error_count"`
	LastError    string    `json:"last_error,omitempty"`
	Active       bool      `json:"active"`
}

type UploadSourceStatus struct {
	Source       string    `json:"source"`
	LastUploadAt time.Time `json:"last_upload_at"`
	UploadCount  int       `json:"upload_count"`
	QuoteCount   int       `json:"quote_count"`
	LastAccepted int       `json:"last_accepted"`
	LastWarnings []string  `json:"last_warnings,omitempty"`
}

type QuoteSourceSummary struct {
	Source string `json:"source"`
	Count  int    `json:"count"`
}

type QuoteFreshnessSummary struct {
	Realtime    int `json:"realtime"`
	Stale       int `json:"stale"`
	Unsupported int `json:"unsupported"`
	Demo        int `json:"demo"`
}

type DebugStatus struct {
	ServerTime     time.Time             `json:"server_time"`
	SnapshotStatus map[string]any        `json:"snapshot_status"`
	NAVSyncStatus  any                   `json:"nav_sync_status,omitempty"`
	RuntimeStatus  any                   `json:"runtime_status,omitempty"`
	UploadStatus   UploadedQuoteStatus   `json:"upload_status"`
	Required       RequiredQuoteSymbols  `json:"required"`
	WSClients      []WSClientStatus      `json:"ws_clients"`
	UploadSources  []UploadSourceStatus  `json:"upload_sources"`
	QuoteSources   []QuoteSourceSummary  `json:"quote_sources"`
	Freshness      QuoteFreshnessSummary `json:"freshness"`
	RecentEvents   []SystemEvent         `json:"recent_events"`
	SampleQuotes   []domain.Quote        `json:"sample_quotes,omitempty"`
}

func (s *Service) SetNAVSyncStatusProvider(provider func() any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.navSyncStatus = provider
}

func (s *Service) SetRuntimeStatusProvider(provider func() any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.runtimeStatus = provider
}

func (s *Service) RecordEvent(level string, source string, message string, details map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.recordEventLocked(level, source, message, details)
}

func (s *Service) recordEventLocked(level string, source string, message string, details map[string]any) {
	event := SystemEvent{
		At:      time.Now(),
		Level:   level,
		Source:  source,
		Message: message,
		Details: details,
	}
	s.events = append(s.events, event)
	if len(s.events) > maxSystemEvents {
		copy(s.events, s.events[len(s.events)-maxSystemEvents:])
		s.events = s.events[:maxSystemEvents]
	}
}

func (s *Service) RegisterWSClient(id string, source string, remoteAddr string) {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	s.wsClients[id] = WSClientStatus{
		ID:          id,
		Source:      source,
		RemoteAddr:  remoteAddr,
		ConnectedAt: now,
		LastSeenAt:  now,
		Active:      true,
	}
	s.recordEventLocked("info", "ws", "client connected", map[string]any{"id": id, "source": source, "remote_addr": remoteAddr})
}

func (s *Service) MarkWSClientSeen(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	client := s.wsClients[id]
	client.LastSeenAt = time.Now()
	client.Active = true
	s.wsClients[id] = client
}

func (s *Service) MarkWSClientUpload(id string, accepted int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	client := s.wsClients[id]
	client.LastSeenAt = time.Now()
	client.LastUploadAt = client.LastSeenAt
	client.UploadCount++
	client.QuoteCount += accepted
	client.Active = true
	client.LastError = ""
	s.wsClients[id] = client
}

func (s *Service) MarkWSClientError(id string, message string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	client := s.wsClients[id]
	client.LastSeenAt = time.Now()
	client.ErrorCount++
	client.LastError = message
	client.Active = true
	s.wsClients[id] = client
	s.recordEventLocked("warn", "ws", message, map[string]any{"id": id})
}

func (s *Service) UnregisterWSClient(id string, reason string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	client := s.wsClients[id]
	client.LastSeenAt = time.Now()
	client.Active = false
	if reason != "" && reason != "connection closed" {
		client.LastError = reason
	}
	s.wsClients[id] = client
	s.recordEventLocked("info", "ws", "client disconnected", map[string]any{"id": id, "reason": reason})
}

func (s *Service) DebugStatus(includeQuotes bool) DebugStatus {
	s.mu.RLock()
	navSyncStatusProvider := s.navSyncStatus
	runtimeStatusProvider := s.runtimeStatus

	wsClients := make([]WSClientStatus, 0, len(s.wsClients))
	for _, client := range s.wsClients {
		wsClients = append(wsClients, client)
	}
	sort.Slice(wsClients, func(i, j int) bool {
		return wsClients[i].ConnectedAt.After(wsClients[j].ConnectedAt)
	})

	uploadSources := make([]UploadSourceStatus, 0, len(s.uploadSources))
	for _, source := range s.uploadSources {
		uploadSources = append(uploadSources, source)
	}
	sort.Slice(uploadSources, func(i, j int) bool {
		return uploadSources[i].LastUploadAt.After(uploadSources[j].LastUploadAt)
	})

	sourceCounts := make(map[string]int)
	freshness := QuoteFreshnessSummary{}
	for _, quote := range s.quotes {
		sourceCounts[quote.Source]++
		if quote.Source == "demo" || quote.Error == "demo fallback" {
			freshness.Demo++
		}
		switch quote.RealtimeStatus {
		case "realtime":
			freshness.Realtime++
		case "unsupported":
			freshness.Unsupported++
		default:
			freshness.Stale++
		}
	}
	quoteSources := make([]QuoteSourceSummary, 0, len(sourceCounts))
	for source, count := range sourceCounts {
		quoteSources = append(quoteSources, QuoteSourceSummary{Source: source, Count: count})
	}
	sort.Slice(quoteSources, func(i, j int) bool {
		if quoteSources[i].Count == quoteSources[j].Count {
			return quoteSources[i].Source < quoteSources[j].Source
		}
		return quoteSources[i].Count > quoteSources[j].Count
	})

	required := requiredQuoteSymbolsFromQuotes(s.quotes)
	uploadStatus := uploadedQuoteStatusFromMap(s.enableUploadQuotes, s.uploadedQuotes, includeQuotes)
	events := append([]SystemEvent(nil), s.events...)
	sort.Slice(events, func(i, j int) bool {
		return events[i].At.After(events[j].At)
	})
	var samples []domain.Quote
	if includeQuotes {
		samples = make([]domain.Quote, 0, len(s.quotes))
		for _, quote := range s.quotes {
			samples = append(samples, quote)
		}
		sort.Slice(samples, func(i, j int) bool {
			return samples[i].Symbol < samples[j].Symbol
		})
		if len(samples) > 200 {
			samples = samples[:200]
		}
	}

	status := DebugStatus{
		ServerTime:     time.Now(),
		SnapshotStatus: s.statusLocked(),
		UploadStatus:   uploadStatus,
		Required:       required,
		WSClients:      wsClients,
		UploadSources:  uploadSources,
		QuoteSources:   quoteSources,
		Freshness:      freshness,
		RecentEvents:   events,
		SampleQuotes:   samples,
	}
	s.mu.RUnlock()

	if navSyncStatusProvider != nil {
		status.NAVSyncStatus = navSyncStatusProvider()
	}
	if runtimeStatusProvider != nil {
		status.RuntimeStatus = runtimeStatusProvider()
	}
	return status
}
