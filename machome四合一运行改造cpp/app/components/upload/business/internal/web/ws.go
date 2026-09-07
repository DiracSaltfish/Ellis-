package web

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net/http"
	"strings"
	"time"

	"golang.org/x/net/websocket"
	"newnavnav/internal/domain"
	"newnavnav/internal/snapshot"
)

type quoteWSMessage struct {
	Type                    string                                     `json:"type"`
	Source                  string                                     `json:"source,omitempty"`
	Seq                     int64                                      `json:"seq,omitempty"`
	RequestID               string                                     `json:"request_id,omitempty"`
	Reason                  string                                     `json:"reason,omitempty"`
	SentAt                  string                                     `json:"sent_at,omitempty"`
	Symbols                 []string                                   `json:"symbols,omitempty"`
	Quotes                  []uploadQuoteItem                          `json:"quotes,omitempty"`
	DailyPriceRequests      []snapshot.DailyPriceRequestItem           `json:"daily_price_requests,omitempty"`
	DailyPrices             []dailyPriceUploadItem                     `json:"daily_prices,omitempty"`
	AnchorRequests          []snapshot.ValuationAnchorPriceRequestItem `json:"valuation_anchor_price_requests,omitempty"`
	AnchorPrices            []valuationAnchorPriceUpload               `json:"valuation_anchor_prices,omitempty"`
	ValuationPositionStates []valuationPositionStateItem               `json:"valuation_position_states,omitempty"`
	Warnings                []string                                   `json:"warnings,omitempty"`
	Error                   string                                     `json:"error,omitempty"`
}

type quoteWSResponse struct {
	Type                     string                                     `json:"type"`
	RequestID                string                                     `json:"request_id,omitempty"`
	ServerTime               string                                     `json:"server_time,omitempty"`
	RequiredSymbols          []string                                   `json:"required_symbols,omitempty"`
	DailyPriceRequests       []snapshot.DailyPriceRequestItem           `json:"daily_price_requests,omitempty"`
	AnchorRequests           []snapshot.ValuationAnchorPriceRequestItem `json:"valuation_anchor_price_requests,omitempty"`
	ValuationPositionUpdates []valuationPositionUpdateItem              `json:"valuation_position_updates,omitempty"`
	PushIntervalSeconds      int                                        `json:"push_interval_seconds,omitempty"`
	Accepted                 int                                        `json:"accepted,omitempty"`
	Enabled                  bool                                       `json:"enabled,omitempty"`
	Warnings                 []string                                   `json:"warnings,omitempty"`
	Error                    string                                     `json:"error,omitempty"`
}

type dailyPriceUploadItem struct {
	Symbol   string  `json:"symbol"`
	Date     string  `json:"date"`
	Close    float64 `json:"close"`
	AdjClose float64 `json:"adj_close"`
	Source   string  `json:"source"`
}

func (s *Server) handleQuoteWebSocket(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeUpload(r) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "upload token required"})
		return
	}
	websocket.Handler(func(conn *websocket.Conn) {
		s.serveQuoteWebSocket(conn, r)
	}).ServeHTTP(w, r)
}

func (s *Server) serveQuoteWebSocket(conn *websocket.Conn, r *http.Request) {
	clientID := newWSClientID()
	source := strings.TrimSpace(r.URL.Query().Get("source"))
	if source == "" {
		source = "ws-uploader"
	}
	controller := s.valuationPositionWSController()
	if controller != nil {
		controller.registerClient(clientID, source, conn)
		defer controller.unregisterClient(clientID)
	}
	s.snapshots.RegisterWSClient(clientID, source, clientRemoteAddr(r))
	defer s.snapshots.UnregisterWSClient(clientID, "connection closed")

	required := s.snapshots.RequiredQuoteSymbols()
	if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{
		Type:                "hello",
		ServerTime:          time.Now().Format(time.RFC3339Nano),
		RequiredSymbols:     required.Symbols,
		PushIntervalSeconds: 3,
		Enabled:             s.snapshots.UploadedQuoteStatus(false).Enabled,
	}); err != nil {
		s.snapshots.MarkWSClientError(clientID, err.Error())
		return
	}
	requestID := clientID + "-initial"
	_ = s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{
		Type:            "quote_request",
		RequestID:       requestID,
		ServerTime:      time.Now().Format(time.RFC3339Nano),
		RequiredSymbols: required.Symbols,
	})
	lastDailyPriceRequestAt := time.Time{}
	lastAnchorRequestAt := time.Time{}
	s.sendMissingDailyPriceRequest(conn, clientID, &lastDailyPriceRequestAt, true)
	s.sendMissingValuationAnchorRequest(conn, clientID, &lastAnchorRequestAt, true)
	if controller != nil {
		controller.requestState(clientID)
	}

	for {
		var msg quoteWSMessage
		if err := websocket.JSON.Receive(conn, &msg); err != nil {
			if !isNormalWSError(err) {
				s.snapshots.MarkWSClientError(clientID, err.Error())
			}
			return
		}
		s.snapshots.MarkWSClientSeen(clientID)
		if controller != nil {
			controller.markSeen(clientID)
			if controller.handleMessage(clientID, msg) {
				continue
			}
		}
		switch msg.Type {
		case "hello", "ping":
			if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{Type: "pong", ServerTime: time.Now().Format(time.RFC3339Nano)}); err != nil {
				s.snapshots.MarkWSClientError(clientID, err.Error())
				return
			}
		case "quotes", "quote_response":
			if len(msg.Quotes) == 0 {
				if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{Type: "error", RequestID: msg.RequestID, Error: "quotes is required"}); err != nil {
					s.snapshots.MarkWSClientError(clientID, err.Error())
					return
				}
				continue
			}
			if len(msg.Quotes) > 500 {
				if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{Type: "error", RequestID: msg.RequestID, Error: "quotes limit is 500 per message"}); err != nil {
					s.snapshots.MarkWSClientError(clientID, err.Error())
					return
				}
				continue
			}
			uploadSource := strings.TrimSpace(msg.Source)
			if uploadSource == "" {
				uploadSource = source
			}
			accepted, warnings := s.snapshots.UpsertUploadedQuotes(uploadSource, uploadItemsToQuotes(msg.Quotes, time.Now()))
			s.snapshots.MarkWSClientUpload(clientID, accepted)
			if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{
				Type:       "ack",
				RequestID:  msg.RequestID,
				ServerTime: time.Now().Format(time.RFC3339Nano),
				Accepted:   accepted,
				Enabled:    s.snapshots.UploadedQuoteStatus(false).Enabled,
				Warnings:   warnings,
			}); err != nil {
				s.snapshots.MarkWSClientError(clientID, err.Error())
				return
			}
			s.sendMissingDailyPriceRequest(conn, clientID, &lastDailyPriceRequestAt, false)
			s.sendMissingValuationAnchorRequest(conn, clientID, &lastAnchorRequestAt, false)
		case "daily_prices":
			accepted, err := s.snapshots.UpsertRequestedDailyPrices(context.Background(), source, dailyPriceItemsToDomain(msg.DailyPrices))
			if err != nil {
				s.snapshots.MarkWSClientError(clientID, err.Error())
				if sendErr := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{Type: "error", RequestID: msg.RequestID, Error: err.Error()}); sendErr != nil {
					return
				}
				continue
			}
			if len(msg.Warnings) > 0 {
				s.snapshots.RecordEvent("warn", "daily_price", "uploader returned daily price warnings", map[string]any{
					"source":   source,
					"warnings": msg.Warnings,
				})
			}
			if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{
				Type:       "ack",
				RequestID:  msg.RequestID,
				ServerTime: time.Now().Format(time.RFC3339Nano),
				Accepted:   accepted,
				Enabled:    s.snapshots.UploadedQuoteStatus(false).Enabled,
				Warnings:   msg.Warnings,
			}); err != nil {
				s.snapshots.MarkWSClientError(clientID, err.Error())
				return
			}
			s.sendMissingDailyPriceRequest(conn, clientID, &lastDailyPriceRequestAt, false)
			s.sendMissingValuationAnchorRequest(conn, clientID, &lastAnchorRequestAt, false)
		case "valuation_anchor_prices":
			accepted, err := s.snapshots.UpsertValuationAnchorPrices(context.Background(), source, valuationAnchorUploadsToDomain(msg.AnchorPrices))
			if err != nil {
				s.snapshots.MarkWSClientError(clientID, err.Error())
				if sendErr := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{Type: "error", RequestID: msg.RequestID, Error: err.Error()}); sendErr != nil {
					return
				}
				continue
			}
			warnings := append([]string(nil), accepted.Warnings...)
			if len(msg.Warnings) > 0 {
				warnings = append(warnings, msg.Warnings...)
				s.snapshots.RecordEvent("warn", "valuation_anchor", "uploader returned valuation anchor warnings", map[string]any{
					"source":   source,
					"warnings": msg.Warnings,
				})
			}
			if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{
				Type:       "ack",
				RequestID:  msg.RequestID,
				ServerTime: time.Now().Format(time.RFC3339Nano),
				Accepted:   accepted.Accepted,
				Enabled:    s.snapshots.UploadedQuoteStatus(false).Enabled,
				Warnings:   warnings,
			}); err != nil {
				s.snapshots.MarkWSClientError(clientID, err.Error())
				return
			}
			s.sendMissingValuationAnchorRequest(conn, clientID, &lastAnchorRequestAt, false)
		case "error":
			message := strings.TrimSpace(msg.Error)
			if message == "" {
				message = "client reported error"
			}
			s.snapshots.MarkWSClientError(clientID, message)
		default:
			err := fmt.Sprintf("unsupported ws message type %q", msg.Type)
			s.snapshots.MarkWSClientError(clientID, err)
			if sendErr := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{Type: "error", Error: err}); sendErr != nil {
				return
			}
		}
	}
}

func (s *Server) sendMissingValuationAnchorRequest(conn *websocket.Conn, clientID string, lastSent *time.Time, force bool) {
	if !force && !lastSent.IsZero() && time.Since(*lastSent) < 5*time.Minute {
		return
	}
	requests, err := s.snapshots.MissingValuationAnchorPriceRequests(context.Background(), 500)
	if err != nil {
		s.snapshots.MarkWSClientError(clientID, err.Error())
		return
	}
	if len(requests) == 0 {
		*lastSent = time.Now()
		return
	}
	*lastSent = time.Now()
	s.snapshots.RecordEvent("info", "valuation_anchor", "requesting missing valuation anchor prices", map[string]any{
		"client_id": clientID,
		"count":     len(requests),
	})
	if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{
		Type:           "valuation_anchor_price_request",
		RequestID:      clientID + "-anchor-" + fmt.Sprint(time.Now().UnixNano()),
		ServerTime:     time.Now().Format(time.RFC3339Nano),
		AnchorRequests: requests,
	}); err != nil {
		s.snapshots.MarkWSClientError(clientID, err.Error())
	}
}

func (s *Server) sendMissingDailyPriceRequest(conn *websocket.Conn, clientID string, lastSent *time.Time, force bool) {
	if !force && !lastSent.IsZero() && time.Since(*lastSent) < 5*time.Minute {
		return
	}
	requests, err := s.snapshots.MissingDailyPriceRequests(context.Background(), 500)
	if err != nil {
		s.snapshots.MarkWSClientError(clientID, err.Error())
		return
	}
	if len(requests) == 0 {
		*lastSent = time.Now()
		return
	}
	*lastSent = time.Now()
	s.snapshots.RecordEvent("info", "daily_price", "requesting missing daily base prices", map[string]any{
		"client_id": clientID,
		"count":     len(requests),
	})
	if err := s.sendQuoteWSResponse(conn, clientID, quoteWSResponse{
		Type:               "daily_price_request",
		RequestID:          clientID + "-daily-" + fmt.Sprint(time.Now().UnixNano()),
		ServerTime:         time.Now().Format(time.RFC3339Nano),
		DailyPriceRequests: requests,
	}); err != nil {
		s.snapshots.MarkWSClientError(clientID, err.Error())
	}
}

func (s *Server) sendQuoteWSResponse(conn *websocket.Conn, clientID string, payload quoteWSResponse) error {
	if controller := s.valuationPositionWSController(); controller != nil && clientID != "" {
		return controller.send(clientID, payload)
	}
	return websocket.JSON.Send(conn, payload)
}

func dailyPriceItemsToDomain(items []dailyPriceUploadItem) []domain.DailyPrice {
	prices := make([]domain.DailyPrice, 0, len(items))
	for _, item := range items {
		prices = append(prices, domain.DailyPrice{
			Symbol:   item.Symbol,
			Date:     item.Date,
			Close:    item.Close,
			AdjClose: item.AdjClose,
			Source:   item.Source,
		})
	}
	return prices
}

func newWSClientID() string {
	var buf [8]byte
	if _, err := rand.Read(buf[:]); err == nil {
		return hex.EncodeToString(buf[:])
	}
	return fmt.Sprintf("%d", time.Now().UnixNano())
}

func isNormalWSError(err error) bool {
	text := strings.ToLower(err.Error())
	return strings.Contains(text, "eof") || strings.Contains(text, "closed")
}

func clientRemoteAddr(r *http.Request) string {
	if forwarded := strings.TrimSpace(r.Header.Get("X-Forwarded-For")); forwarded != "" {
		parts := strings.Split(forwarded, ",")
		return strings.TrimSpace(parts[0])
	}
	if realIP := strings.TrimSpace(r.Header.Get("X-Real-IP")); realIP != "" {
		return realIP
	}
	return r.RemoteAddr
}
