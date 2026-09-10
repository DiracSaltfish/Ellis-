package snapshot

import (
	"fmt"
	"log/slog"
	"regexp"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/sina"
)

const uploadedQuoteStaleAfter = 30 * time.Second

type UploadedQuoteStatus struct {
	Enabled bool           `json:"enabled"`
	Count   int            `json:"count"`
	Symbols []string       `json:"symbols"`
	Quotes  []domain.Quote `json:"quotes,omitempty"`
}

type RequiredQuoteSymbols struct {
	Count   int      `json:"count"`
	Symbols []string `json:"symbols"`
}

func (s *Service) UpsertUploadedQuotes(source string, quotes []domain.Quote) (int, []string) {
	now := time.Now()
	source = strings.TrimSpace(source)
	if source == "" {
		source = "external_upload"
	}

	accepted := 0
	var warnings []string
	var persistedQuotes map[string]domain.Quote
	s.mu.Lock()
	for _, quote := range quotes {
		symbol := normalizeUploadSymbol(quote.Symbol)
		if symbol == "" {
			warnings = append(warnings, "missing symbol")
			continue
		}
		if quote.Price <= 0 {
			warnings = append(warnings, fmt.Sprintf("%s price must be positive", symbol))
			continue
		}

		quote.Symbol = symbol
		if strings.TrimSpace(quote.Source) == "" {
			quote.Source = "upload:" + source
		}
		if strings.TrimSpace(quote.SourceSymbol) == "" {
			quote.SourceSymbol = sina.ToSinaSymbol(symbol)
			if quote.SourceSymbol == "" {
				quote.SourceSymbol = symbol
			}
		}
		if strings.TrimSpace(quote.QuoteSession) == "" {
			quote.QuoteSession = "external_upload"
		}
		if strings.TrimSpace(quote.QuoteDate) == "" {
			quote.QuoteDate = now.Format("2006-01-02")
		}
		if strings.TrimSpace(quote.QuoteTime) == "" {
			quote.QuoteTime = now.Format("15:04:05")
		}
		if quote.FetchedAt.IsZero() {
			quote.FetchedAt = now
		}
		if quote.ChangePct == 0 && quote.PrevClose > 0 {
			quote.ChangePct = (quote.Price/quote.PrevClose - 1) * 100
		}
		quote = preserveOrderBookFields(quote, s.uploadedQuotes[symbol], s.quotes[symbol])
		quote.IsRealtime = true
		quote.RealtimeStatus = "realtime"
		quote.StaleReason = ""
		s.uploadedQuotes[symbol] = quote
		accepted++
	}
	status := s.uploadSources[source]
	status.Source = source
	status.LastUploadAt = now
	status.UploadCount++
	status.QuoteCount += accepted
	status.LastAccepted = accepted
	status.LastWarnings = append([]string(nil), warnings...)
	s.uploadSources[source] = status
	s.recordEventLocked("info", "upload", "quotes uploaded", map[string]any{
		"source":   source,
		"accepted": accepted,
		"warnings": len(warnings),
	})
	if accepted > 0 && s.uploadedQuoteCache != "" {
		persistedQuotes = make(map[string]domain.Quote, len(s.uploadedQuotes))
		for symbol, quote := range s.uploadedQuotes {
			persistedQuotes[symbol] = quote
		}
	}
	s.mu.Unlock()
	if len(persistedQuotes) > 0 {
		if err := s.persistUploadedQuoteCache(persistedQuotes); err != nil {
			slog.Warn("persist uploaded quote cache failed", "error", err)
		}
	}
	return accepted, warnings
}

func preserveOrderBookFields(quote domain.Quote, previous ...domain.Quote) domain.Quote {
	for _, existing := range previous {
		if quote.LimitUp == 0 {
			quote.LimitUp = existing.LimitUp
		}
		if quote.LimitDown == 0 {
			quote.LimitDown = existing.LimitDown
		}
		if len(quote.BidLevels) == 0 {
			quote.BidLevels = existing.BidLevels
		}
		if len(quote.AskLevels) == 0 {
			quote.AskLevels = existing.AskLevels
		}
	}
	return quote
}

func (s *Service) UploadedQuoteStatus(includeQuotes bool) UploadedQuoteStatus {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return uploadedQuoteStatusFromMap(s.enableUploadQuotes, s.uploadedQuotes, includeQuotes)
}

func uploadedQuoteStatusFromMap(enabled bool, uploaded map[string]domain.Quote, includeQuotes bool) UploadedQuoteStatus {
	now := time.Now()
	symbols := make([]string, 0, len(uploaded))
	quotes := make([]domain.Quote, 0, len(uploaded))
	for symbol, quote := range uploaded {
		symbols = append(symbols, symbol)
		if includeQuotes {
			quotes = append(quotes, quoteWithUploadFreshness(quote, now))
		}
	}
	sort.Strings(symbols)
	sort.Slice(quotes, func(i, j int) bool {
		return quotes[i].Symbol < quotes[j].Symbol
	})
	return UploadedQuoteStatus{
		Enabled: enabled,
		Count:   len(symbols),
		Symbols: symbols,
		Quotes:  quotes,
	}
}

func (s *Service) RequiredQuoteSymbols() RequiredQuoteSymbols {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return requiredQuoteSymbolsFromQuotes(s.quotes)
}

func requiredQuoteSymbolsFromQuotes(quotes map[string]domain.Quote) RequiredQuoteSymbols {
	seen := make(map[string]bool)
	symbols := make([]string, 0, len(quotes))
	for symbol := range quotes {
		if symbol == "" || seen[symbol] || domain.IsIgnoredAuxiliarySymbol(symbol) || domain.IsUnsupportedLiveQuoteSymbol(symbol) {
			continue
		}
		seen[symbol] = true
		symbols = append(symbols, symbol)
	}
	sort.Strings(symbols)
	return RequiredQuoteSymbols{
		Count:   len(symbols),
		Symbols: symbols,
	}
}

func (s *Service) applyUploadedQuotes(quotes map[string]domain.Quote, now time.Time) (bool, string) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	hasFresh := false
	for symbol, quote := range s.uploadedQuotes {
		fresh := quote.FetchedAt.IsZero() || now.Sub(quote.FetchedAt) <= uploadedQuoteStaleAfter
		if fresh {
			hasFresh = true
		}
		current := quotes[symbol]
		if !fresh && current.Price > 0 && current.Source != "" && current.Source != "missing" && !sameUploadedQuoteOrigin(current, quote) {
			continue
		}
		quotes[symbol] = quoteWithUploadFreshness(quote, now)
	}
	sources := make([]string, 0, len(s.uploadSources))
	for source, status := range s.uploadSources {
		if !status.LastUploadAt.IsZero() && now.Sub(status.LastUploadAt) <= uploadedQuoteStaleAfter {
			sources = append(sources, source)
		}
	}
	sort.Strings(sources)
	return hasFresh, strings.Join(sources, ",")
}

func quoteWithUploadFreshness(quote domain.Quote, now time.Time) domain.Quote {
	if quote.FetchedAt.IsZero() || now.Sub(quote.FetchedAt) <= uploadedQuoteStaleAfter {
		return quote
	}
	quote.IsRealtime = false
	quote.RealtimeStatus = "stale"
	quote.StaleReason = fmt.Sprintf("last uploaded quote older than %s", uploadedQuoteStaleAfter)
	return quote
}

func sameUploadedQuoteOrigin(current domain.Quote, uploaded domain.Quote) bool {
	return strings.TrimSpace(current.Source) == strings.TrimSpace(uploaded.Source) &&
		strings.TrimSpace(current.SourceSymbol) == strings.TrimSpace(uploaded.SourceSymbol) &&
		strings.TrimSpace(current.QuoteSession) == strings.TrimSpace(uploaded.QuoteSession)
}

func normalizeUploadSymbol(symbol string) string {
	symbol = strings.TrimSpace(symbol)
	if symbol == "" {
		return ""
	}
	lower := strings.ToLower(symbol)
	switch {
	case strings.HasPrefix(lower, "fx_"):
		return lower
	case strings.HasPrefix(lower, "hf_"):
		return "HF_" + strings.ToUpper(symbol[3:])
	case strings.HasPrefix(lower, "nf_"):
		return "nf_" + strings.ToUpper(symbol[3:])
	case strings.HasPrefix(lower, "znb_"):
		return "znb_" + strings.ToUpper(symbol[4:])
	case strings.HasPrefix(lower, "b_"):
		return "b_" + strings.ToUpper(symbol[2:])
	case strings.HasPrefix(lower, "rt_hk"):
		return "rt_hk" + symbol[5:]
	case strings.HasPrefix(lower, "gb_"):
		return "gb_" + strings.ToLower(symbol[3:])
	}
	if regexp.MustCompile(`^\d{5}$`).MatchString(symbol) {
		return symbol
	}
	if strings.HasPrefix(symbol, "^") {
		return strings.ToUpper(symbol)
	}
	if len(symbol) >= 3 {
		prefix := strings.ToUpper(symbol[:2])
		if prefix == "SH" || prefix == "SZ" || prefix == "BJ" {
			return prefix + symbol[2:]
		}
	}
	return strings.ToUpper(symbol)
}
