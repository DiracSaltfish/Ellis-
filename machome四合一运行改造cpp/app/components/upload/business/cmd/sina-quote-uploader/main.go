package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/sina"
)

type requiredSymbolsResponse struct {
	Count   int      `json:"count"`
	Symbols []string `json:"symbols"`
}

type uploadRequest struct {
	Source string       `json:"source"`
	Quotes []quoteInput `json:"quotes"`
}

type quoteInput struct {
	Symbol       string         `json:"symbol"`
	Name         string         `json:"name"`
	Price        float64        `json:"price"`
	PrevClose    float64        `json:"prev_close"`
	Open         float64        `json:"open"`
	High         float64        `json:"high"`
	Low          float64        `json:"low"`
	Volume       float64        `json:"volume"`
	Amount       float64        `json:"amount"`
	ChangePct    float64        `json:"change_pct"`
	LimitUp      float64        `json:"limit_up"`
	LimitDown    float64        `json:"limit_down"`
	BidLevels    []domain.Level `json:"bid_levels"`
	AskLevels    []domain.Level `json:"ask_levels"`
	QuoteDate    string         `json:"quote_date"`
	QuoteTime    string         `json:"quote_time"`
	Source       string         `json:"source"`
	SourceSymbol string         `json:"source_symbol"`
	QuoteSession string         `json:"quote_session"`
}

type uploadResponse struct {
	OK       bool     `json:"ok"`
	Accepted int      `json:"accepted"`
	Enabled  bool     `json:"enabled"`
	Warnings []string `json:"warnings"`
	Error    string   `json:"error"`
}

func main() {
	var (
		serverURL = flag.String("server", envString("NNN_SERVER_URL", "http://127.0.0.1:8080"), "newnavnav server base URL")
		token     = flag.String("token", envString("NNN_UPLOAD_TOKEN", ""), "upload token")
		source    = flag.String("source", envString("NNN_UPLOAD_SOURCE", "home-mac"), "upload source name")
		interval  = flag.Duration("interval", envDuration("NNN_UPLOAD_INTERVAL", 5*time.Second), "upload interval")
		timeout   = flag.Duration("timeout", envDuration("NNN_UPLOAD_TIMEOUT", 8*time.Second), "HTTP timeout")
		once      = flag.Bool("once", envBool("NNN_UPLOAD_ONCE", false), "run one upload then exit")
		symbolCSV = flag.String("symbols", envString("NNN_UPLOAD_SYMBOLS", ""), "optional comma-separated symbols; otherwise fetch required symbols from server")
	)
	flag.Parse()

	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)
	if strings.TrimSpace(*token) == "" {
		logger.Error("NNN_UPLOAD_TOKEN or -token is required")
		os.Exit(2)
	}

	client := &http.Client{Timeout: *timeout}
	sinaClient := sina.NewClient(*timeout)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	run := func() {
		cycleCtx, cancel := context.WithTimeout(ctx, *timeout*3)
		defer cancel()
		symbols := splitSymbols(*symbolCSV)
		if len(symbols) == 0 {
			var err error
			symbols, err = fetchRequiredSymbols(cycleCtx, client, *serverURL, *token)
			if err != nil {
				logger.Warn("fetch required symbols failed", "error", err)
				return
			}
		}
		quotes, err := sinaClient.FetchQuotes(cycleCtx, symbols)
		if err != nil {
			logger.Warn("fetch sina quotes partially failed", "error", err)
		}
		accepted, enabled, err := uploadQuotes(cycleCtx, client, *serverURL, *token, *source, quotes)
		if err != nil {
			logger.Warn("upload quotes failed", "error", err, "quotes", len(quotes))
			return
		}
		logger.Info("quotes uploaded", "symbols", len(symbols), "quotes", len(quotes), "accepted", accepted, "enabled", enabled)
	}

	run()
	if *once {
		return
	}
	ticker := time.NewTicker(*interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			logger.Info("uploader stopped")
			return
		case <-ticker.C:
			run()
		}
	}
}

func fetchRequiredSymbols(ctx context.Context, client *http.Client, serverURL string, token string) ([]string, error) {
	url := strings.TrimRight(serverURL, "/") + "/api/v1/uploads/quotes/required-symbols"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Upload-Token", token)
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("required symbols status %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var parsed requiredSymbolsResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return nil, err
	}
	if len(parsed.Symbols) == 0 {
		return nil, fmt.Errorf("server returned no required symbols")
	}
	return parsed.Symbols, nil
}

func uploadQuotes(ctx context.Context, client *http.Client, serverURL string, token string, source string, quotes map[string]domain.Quote) (int, bool, error) {
	inputs := make([]quoteInput, 0, len(quotes))
	for _, quote := range quotes {
		if quote.Price <= 0 || quote.Error != "" {
			continue
		}
		inputs = append(inputs, quoteInput{
			Symbol:       quote.Symbol,
			Name:         quote.Name,
			Price:        quote.Price,
			PrevClose:    quote.PrevClose,
			Open:         quote.Open,
			High:         quote.High,
			Low:          quote.Low,
			Volume:       quote.Volume,
			Amount:       quote.Amount,
			ChangePct:    quote.ChangePct,
			LimitUp:      quote.LimitUp,
			LimitDown:    quote.LimitDown,
			BidLevels:    quote.BidLevels,
			AskLevels:    quote.AskLevels,
			QuoteDate:    quote.QuoteDate,
			QuoteTime:    quote.QuoteTime,
			Source:       quote.Source,
			SourceSymbol: quote.SourceSymbol,
			QuoteSession: quote.QuoteSession,
		})
	}
	if len(inputs) == 0 {
		return 0, false, fmt.Errorf("no valid quotes to upload")
	}

	accepted := 0
	enabled := false
	for start := 0; start < len(inputs); start += 500 {
		end := start + 500
		if end > len(inputs) {
			end = len(inputs)
		}
		payload, err := json.Marshal(uploadRequest{Source: source, Quotes: inputs[start:end]})
		if err != nil {
			return accepted, enabled, err
		}
		url := strings.TrimRight(serverURL, "/") + "/api/v1/uploads/quotes"
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(payload))
		if err != nil {
			return accepted, enabled, err
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Upload-Token", token)
		resp, err := client.Do(req)
		if err != nil {
			return accepted, enabled, err
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		resp.Body.Close()
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return accepted, enabled, fmt.Errorf("upload status %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
		}
		var parsed uploadResponse
		if err := json.Unmarshal(body, &parsed); err != nil {
			return accepted, enabled, err
		}
		if parsed.Error != "" {
			return accepted, enabled, errors.New(parsed.Error)
		}
		accepted += parsed.Accepted
		enabled = parsed.Enabled
	}
	return accepted, enabled, nil
}

func splitSymbols(value string) []string {
	fields := strings.Split(value, ",")
	out := make([]string, 0, len(fields))
	for _, field := range fields {
		field = strings.TrimSpace(field)
		if field != "" {
			out = append(out, field)
		}
	}
	return out
}

func envString(key string, fallback string) string {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	return value
}

func envDuration(key string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func envBool(key string, fallback bool) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return fallback
	}
}
