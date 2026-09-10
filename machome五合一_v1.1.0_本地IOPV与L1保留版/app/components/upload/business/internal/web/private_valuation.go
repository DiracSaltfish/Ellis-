package web

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/privatevaluation"
)

type PrivateValuationService interface {
	List() privatevaluation.ListResponse
	Fund(symbol string) (privatevaluation.Snapshot, bool)
	MinuteHistory(ctx context.Context, symbol string, days int) (privatevaluation.MinuteHistoryResponse, error)
	MinuteHistoryDate(ctx context.Context, symbol, day string) (privatevaluation.MinuteHistoryResponse, error)
	MinuteHistoryDates(ctx context.Context, symbol string, limit int) ([]string, error)
	ImportHistoricalMinutes(ctx context.Context, symbol string, rows []privatevaluation.HistoricalMinuteInput) (int, error)
	ReplaceHistoricalMinutes(ctx context.Context, symbol string, rows []privatevaluation.HistoricalMinuteInput) (int, error)
	UpdateInput(ctx context.Context, input privatevaluation.Input) (privatevaluation.Snapshot, error)
	UpdateInputs(ctx context.Context, inputs []privatevaluation.Input) (privatevaluation.BatchUpdateResult, error)
}

type privateIndiaHistoryReviewService interface {
	IndiaHistoryReview(ctx context.Context, symbol string, days int) (privatevaluation.IndiaHistoryReviewResponse, error)
}

type privateSilverCloseHistoryService interface {
	SilverCloseHistory(ctx context.Context, symbol string, days int) (privatevaluation.SilverCloseHistoryResponse, error)
}

type privateIndiaNiftyBridgeHistoryReviewService interface {
	IndiaNiftyBridgeHistoryReview(ctx context.Context, symbol string, days int) (privatevaluation.IndiaNiftyBridgeReviewResponse, error)
}

type privateIndiaFinalNAVHistoryImportService interface {
	ImportIndiaFinalNAVHistory(ctx context.Context, symbol string, rows []privatevaluation.IndiaFinalNAVHistoryInput) (int, error)
}

type privateShareChange struct {
	shareDate      string
	shares10K      float64
	shareChange10K *float64
	shareChangePct *float64
}

func cloneFloat64(value *float64) *float64 {
	if value == nil {
		return nil
	}
	cloned := *value
	return &cloned
}

func (s *Server) cachePrivateShareChanges(board domain.YesterdayRedemptionBoardResponse) {
	cache := make(map[string]privateShareChange, len(board.Rows))
	for _, row := range board.Rows {
		symbol := strings.ToUpper(strings.TrimSpace(row.Symbol))
		if symbol == "" {
			continue
		}
		cache[symbol] = privateShareChange{
			shareDate:      row.ShareDate,
			shares10K:      row.Shares10K,
			shareChange10K: cloneFloat64(row.ShareChange10K),
			shareChangePct: cloneFloat64(row.ShareChangePct),
		}
	}
	s.privateShareMu.Lock()
	s.privateShareCache = cache
	s.privateShareReady = true
	s.privateShareMu.Unlock()
}

func (s *Server) ensurePrivateShareChanges(ctx context.Context) {
	s.privateShareMu.RLock()
	ready := s.privateShareReady
	s.privateShareMu.RUnlock()
	if ready || s.snapshots == nil {
		return
	}
	if board, err := s.snapshots.YesterdayRedemptionBoard(ctx); err == nil {
		s.cachePrivateShareChanges(board)
	}
}

func (s *Server) applyPrivateShareChanges(response *privatevaluation.ListResponse) {
	s.privateShareMu.RLock()
	defer s.privateShareMu.RUnlock()
	for index := range response.Funds {
		item := &response.Funds[index]
		cached, ok := s.privateShareCache[item.Symbol]
		if !ok {
			continue
		}
		item.ShareDate = cached.shareDate
		shares := cached.shares10K
		item.Shares10K = &shares
		item.ShareChange10K = cloneFloat64(cached.shareChange10K)
		item.ShareChangePct = cloneFloat64(cached.shareChangePct)
	}
}

func (s *Server) handlePrivateMinuteHistoryImport(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/minute-history/import")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/funds/"), "/"))
	if !privatevaluation.Supported(symbol) {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private fund not found"})
		return
	}
	var payload struct {
		Rows       []privatevaluation.HistoricalMinuteInput `json:"rows"`
		ReplaceDay bool                                     `json:"replace_day,omitempty"`
	}
	const maxHistoryBodyBytes = 4 << 20
	compressedBody := http.MaxBytesReader(w, r.Body, 1<<20)
	body := io.Reader(compressedBody)
	var gzipBody *gzip.Reader
	switch strings.ToLower(strings.TrimSpace(r.Header.Get("Content-Encoding"))) {
	case "", "identity":
	case "gzip":
		var err error
		gzipBody, err = gzip.NewReader(compressedBody)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid gzip request body"})
			return
		}
		defer gzipBody.Close()
		body = http.MaxBytesReader(w, io.NopCloser(gzipBody), maxHistoryBodyBytes)
	default:
		writeJSON(w, http.StatusUnsupportedMediaType, map[string]any{"error": "unsupported Content-Encoding"})
		return
	}
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if err := ensureJSONEOF(decoder); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	var count int
	var err error
	if payload.ReplaceDay {
		count, err = s.privateValuation.ReplaceHistoricalMinutes(r.Context(), symbol, payload.Rows)
	} else {
		count, err = s.privateValuation.ImportHistoricalMinutes(r.Context(), symbol, payload.Rows)
	}
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "imported": count})
}

func (s *Server) handlePrivateIndiaFinalNAVHistoryImport(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}
	service, ok := s.privateValuation.(privateIndiaFinalNAVHistoryImportService)
	if !ok {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private India final-NAV history import is unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/final-nav-history/import")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/funds/"), "/"))
	if symbol != privatevaluation.SZ164824Symbol {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private India final-NAV history is only available for SZ164824"})
		return
	}
	var payload struct {
		Rows []privatevaluation.IndiaFinalNAVHistoryInput `json:"rows"`
	}
	const maxFinalNAVHistoryBodyBytes = 1 << 20
	compressedBody := http.MaxBytesReader(w, r.Body, 1<<20)
	body := io.Reader(compressedBody)
	var gzipBody *gzip.Reader
	switch strings.ToLower(strings.TrimSpace(r.Header.Get("Content-Encoding"))) {
	case "", "identity":
	case "gzip":
		var err error
		gzipBody, err = gzip.NewReader(compressedBody)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid gzip request body"})
			return
		}
		defer gzipBody.Close()
		body = http.MaxBytesReader(w, io.NopCloser(gzipBody), maxFinalNAVHistoryBodyBytes)
	default:
		writeJSON(w, http.StatusUnsupportedMediaType, map[string]any{"error": "unsupported Content-Encoding"})
		return
	}
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if err := ensureJSONEOF(decoder); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	count, err := service.ImportIndiaFinalNAVHistory(r.Context(), symbol, payload.Rows)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "imported": count})
}

func (s *Server) SetPrivateValuationService(service PrivateValuationService) {
	s.privateValuation = service
}

func (s *Server) handlePrivateFundList(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	s.ensurePrivateShareChanges(r.Context())
	response := s.privateValuation.List()
	s.applyPrivateShareChanges(&response)
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) handlePrivateFund(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/private/funds/"), "/"))
	if symbol == "" || strings.Contains(symbol, "/") {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid symbol"})
		return
	}
	snapshot, ok := s.privateValuation.Fund(symbol)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private fund not found"})
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) handlePrivateFundMinuteHistory(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/minute-history")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/funds/"), "/"))
	if !privatevaluation.Supported(symbol) {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private fund not found"})
		return
	}
	if day := strings.TrimSpace(r.URL.Query().Get("date")); day != "" {
		history, err := s.privateValuation.MinuteHistoryDate(r.Context(), symbol, day)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, history)
		return
	}
	days := 1
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "days must be 1, 3, or 5"})
			return
		}
		days = parsed
	}
	history, err := s.privateValuation.MinuteHistory(r.Context(), symbol, days)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, history)
}

func (s *Server) handlePrivateSilverCloseHistory(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	service, ok := s.privateValuation.(privateSilverCloseHistoryService)
	if !ok {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "silver close history unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/close-history")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/funds/"), "/"))
	if symbol != privatevaluation.SZ161226Symbol {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "silver close history not found"})
		return
	}
	days := 365
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed <= 0 || parsed > 3650 {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "days must be between 1 and 3650"})
			return
		}
		days = parsed
	}
	history, err := service.SilverCloseHistory(r.Context(), symbol, days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, history)
}

func (s *Server) handlePrivateIndiaHistoryReview(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	service, ok := s.privateValuation.(privateIndiaHistoryReviewService)
	if !ok {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private India history review is unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/india-history-review")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/funds/"), "/"))
	if symbol != privatevaluation.SZ164824Symbol {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private India history review is only available for SZ164824"})
		return
	}
	days := 120
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed <= 0 || parsed > 365 {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "days must be between 1 and 365"})
			return
		}
		days = parsed
	}
	review, err := service.IndiaHistoryReview(r.Context(), symbol, days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, review)
}

func (s *Server) handlePrivateIndiaNiftyBridgeHistoryReview(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	service, ok := s.privateValuation.(privateIndiaNiftyBridgeHistoryReviewService)
	if !ok {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private India NIFTY bridge history review is unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/nifty-bridge-history-review")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/funds/"), "/"))
	if symbol != privatevaluation.SZ164824Symbol {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private India NIFTY bridge history review is only available for SZ164824"})
		return
	}
	days := 120
	if raw := strings.TrimSpace(r.URL.Query().Get("days")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed <= 0 || parsed > 365 {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "days must be between 1 and 365"})
			return
		}
		days = parsed
	}
	review, err := service.IndiaNiftyBridgeHistoryReview(r.Context(), symbol, days)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, review)
}

func (s *Server) handlePrivateMinuteHistoryDates(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/minute-history/dates")
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(path, "/api/v1/private/funds/"), "/"))
	if !privatevaluation.Supported(symbol) {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private fund not found"})
		return
	}
	dates, err := s.privateValuation.MinuteHistoryDates(r.Context(), symbol, 90)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"symbol": symbol, "dates": dates})
}

func (s *Server) handlePrivateValuationInput(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	symbol := strings.ToUpper(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/private/inputs/"), "/"))
	if !privatevaluation.Supported(symbol) {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "private fund not found"})
		return
	}
	const maxInputBodyBytes = 256 << 10
	compressedBody := http.MaxBytesReader(w, r.Body, maxInputBodyBytes)
	body := io.Reader(compressedBody)
	var gzipBody *gzip.Reader
	switch strings.ToLower(strings.TrimSpace(r.Header.Get("Content-Encoding"))) {
	case "", "identity":
	case "gzip":
		var err error
		gzipBody, err = gzip.NewReader(compressedBody)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid gzip request body"})
			return
		}
		defer gzipBody.Close()
		body = http.MaxBytesReader(w, io.NopCloser(gzipBody), maxInputBodyBytes)
	default:
		writeJSON(w, http.StatusUnsupportedMediaType, map[string]any{"error": "unsupported Content-Encoding"})
		return
	}
	var input privatevaluation.Input
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if err := ensureJSONEOF(decoder); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if strings.TrimSpace(input.Symbol) == "" {
		input.Symbol = symbol
	}
	if strings.ToUpper(strings.TrimSpace(input.Symbol)) != symbol {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "payload symbol does not match path"})
		return
	}
	_, err := s.privateValuation.UpdateInput(r.Context(), input)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "symbol": symbol})
}

func (s *Server) handlePrivateValuationInputBatch(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}
	if s.privateValuation == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "private valuation service unavailable"})
		return
	}
	const maxBatchBodyBytes = 2 << 20
	compressedBody := http.MaxBytesReader(w, r.Body, maxBatchBodyBytes)
	body := io.Reader(compressedBody)
	var gzipBody *gzip.Reader
	switch strings.ToLower(strings.TrimSpace(r.Header.Get("Content-Encoding"))) {
	case "", "identity":
	case "gzip":
		var err error
		gzipBody, err = gzip.NewReader(compressedBody)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid gzip request body"})
			return
		}
		defer func() { _ = gzipBody.Close() }()
		body = http.MaxBytesReader(w, io.NopCloser(gzipBody), maxBatchBodyBytes)
	default:
		writeJSON(w, http.StatusUnsupportedMediaType, map[string]any{"error": "unsupported Content-Encoding"})
		return
	}
	var payload struct {
		SchemaVersion int                      `json:"schema_version"`
		BatchID       string                   `json:"batch_id"`
		Source        string                   `json:"source"`
		GeneratedAt   time.Time                `json:"generated_at"`
		Inputs        []privatevaluation.Input `json:"inputs"`
	}
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if err := ensureJSONEOF(decoder); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if payload.SchemaVersion != 1 || strings.TrimSpace(payload.BatchID) == "" ||
		strings.TrimSpace(payload.Source) == "" || payload.GeneratedAt.IsZero() ||
		len(payload.Inputs) == 0 || len(payload.Inputs) > 100 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "batch metadata and 1 to 100 inputs are required"})
		return
	}
	for _, input := range payload.Inputs {
		if strings.TrimSpace(input.Symbol) == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "every batch input must identify symbol"})
			return
		}
	}
	result, err := s.privateValuation.UpdateInputs(r.Context(), payload.Inputs)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":       len(result.Rejected) == 0,
		"batch_id": payload.BatchID,
		"accepted": result.Accepted,
		"rejected": result.Rejected,
	})
}

func (s *Server) servePrivateFrontend(w http.ResponseWriter, r *http.Request) {
	setPrivateResponseHeaders(w)
	index := filepath.Join(s.frontendDist, "private.html")
	if _, err := os.Stat(index); err == nil {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		http.ServeFile(w, r, index)
		return
	}
	writeJSON(w, http.StatusServiceUnavailable, map[string]any{
		"error": "private frontend not built; run npm run build in frontend",
	})
}

func setPrivateResponseHeaders(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "private, no-store")
	w.Header().Set("X-Robots-Tag", "noindex, nofollow, noarchive")
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	err := decoder.Decode(&extra)
	if errors.Is(err, io.EOF) {
		return nil
	}
	if err == nil {
		return errors.New("request body must contain exactly one JSON object")
	}
	return err
}
