package sina

import (
	"crypto/sha1"
	"encoding/binary"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

func ToSinaSymbol(symbol string) string {
	s := strings.TrimSpace(symbol)
	if s == "" {
		return ""
	}
	lower := strings.ToLower(s)
	switch {
	case strings.HasPrefix(lower, "fx_"):
		return lower
	case strings.HasPrefix(lower, "hf_"):
		return "hf_" + strings.ToUpper(s[3:])
	case strings.HasPrefix(lower, "nf_"):
		return "nf_" + strings.ToUpper(s[3:])
	case strings.HasPrefix(lower, "znb_"):
		return "znb_" + strings.ToUpper(s[4:])
	case strings.HasPrefix(lower, "b_"):
		return "b_" + strings.ToUpper(s[2:])
	case strings.HasPrefix(lower, "rt_hk"):
		return "rt_hk" + s[5:]
	case strings.HasPrefix(lower, "gb_"):
		return "gb_" + strings.ToLower(s[3:])
	}
	if isChinaQuoteSymbol(s) {
		prefix := strings.ToUpper(s[:2])
		code := s[2:]
		switch prefix {
		case "SH":
			return "sh" + code
		case "SZ":
			return "sz" + code
		case "BJ":
			return "bj" + code
		}
	}
	if regexp.MustCompile(`^\d{5}$`).MatchString(s) {
		return "rt_hk" + s
	}
	if strings.HasPrefix(s, "^") && len(s) > 1 {
		return "b_" + strings.ToUpper(s[1:])
	}
	if regexp.MustCompile(`^[A-Z][A-Z0-9.]{0,9}$`).MatchString(s) {
		return "gb_" + strings.ToLower(s)
	}
	return ""
}

func isChinaQuoteSymbol(symbol string) bool {
	if len(symbol) != 8 {
		return false
	}
	prefix := strings.ToUpper(symbol[:2])
	if prefix != "SH" && prefix != "SZ" && prefix != "BJ" {
		return false
	}
	for _, ch := range symbol[2:] {
		if ch < '0' || ch > '9' {
			return false
		}
	}
	return true
}

func ParseResponse(raw string, reverse map[string]string) map[string]domain.Quote {
	out := make(map[string]domain.Quote)
	lines := strings.Split(raw, "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		sinaSymbol, payload, ok := splitLine(line)
		if !ok {
			continue
		}
		symbol := reverse[sinaSymbol]
		if symbol == "" {
			continue
		}
		quote := parsePayload(sinaSymbol, symbol, payload)
		finalizeQuote(&quote, sinaSymbol, time.Now())
		out[symbol] = quote
	}
	return out
}

func splitLine(line string) (string, string, bool) {
	left, right, ok := strings.Cut(line, "=")
	if !ok {
		return "", "", false
	}
	name := strings.TrimPrefix(strings.TrimSpace(left), "var hq_str_")
	payload := strings.TrimSpace(right)
	payload = strings.TrimSuffix(payload, ";")
	payload = strings.Trim(payload, `"`)
	return name, payload, true
}

func parsePayload(sinaSymbol string, symbol string, payload string) domain.Quote {
	switch {
	case strings.HasPrefix(sinaSymbol, "rt_hk"):
		return parseHKPayload(symbol, payload)
	case strings.HasPrefix(sinaSymbol, "gb_"):
		return parseUSPayload(symbol, payload)
	case strings.HasPrefix(sinaSymbol, "fx_"):
		return parseFXPayload(symbol, payload)
	case strings.HasPrefix(sinaSymbol, "hf_"):
		return parseHFFuturePayload(symbol, payload)
	case strings.HasPrefix(sinaSymbol, "nf_"):
		return parseNFFuturePayload(symbol, payload)
	case strings.HasPrefix(sinaSymbol, "znb_"):
		return parseZNBIndexPayload(symbol, payload)
	case strings.HasPrefix(sinaSymbol, "b_"):
		return parseBIndexPayload(symbol, payload)
	default:
		return parseAStockPayload(symbol, payload)
	}
}

func parseAStockPayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:    symbol,
		Source:    "sina",
		FetchedAt: now,
	}
	if len(parts) < 4 || parts[0] == "" {
		quote.Error = "empty sina payload"
		return quote
	}

	quote.Name = parts[0]
	quote.Open = parseFloat(parts, 1)
	quote.PrevClose = parseFloat(parts, 2)
	quote.Price = parseFloat(parts, 3)
	quote.High = parseFloat(parts, 4)
	quote.Low = parseFloat(parts, 5)
	quote.Volume = parseFloat(parts, 8)
	quote.Amount = parseFloat(parts, 9)
	quote.BidLevels = parseAStockLevels(parts, 10)
	quote.AskLevels = parseAStockLevels(parts, 20)
	if quote.Price == 0 && quote.PrevClose != 0 {
		quote.Price = quote.PrevClose
	}
	if quote.PrevClose != 0 {
		quote.ChangePct = (quote.Price/quote.PrevClose - 1) * 100
		quote.LimitUp = roundTick(quote.PrevClose * 1.1)
		quote.LimitDown = roundTick(quote.PrevClose * 0.9)
	}
	if len(parts) > 31 {
		quote.QuoteDate = strings.TrimSpace(parts[30])
		quote.QuoteTime = strings.TrimSpace(parts[31])
	}
	return quote
}

func parseAStockLevels(parts []string, start int) []domain.Level {
	levels := make([]domain.Level, 0, 5)
	for level := 1; level <= 5; level++ {
		volumeIdx := start + (level-1)*2
		priceIdx := volumeIdx + 1
		price := parseFloat(parts, priceIdx)
		volume := parseFloat(parts, volumeIdx)
		if price <= 0 && volume <= 0 {
			continue
		}
		levels = append(levels, domain.Level{
			Level:  level,
			Price:  price,
			Volume: volume,
		})
	}
	return levels
}

func parseHKPayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:    symbol,
		Source:    "sina_hk",
		FetchedAt: now,
	}
	if len(parts) < 19 || parts[1] == "" {
		quote.Error = "empty sina hk payload"
		return quote
	}
	quote.Name = parts[1]
	quote.Open = parseFloat(parts, 2)
	quote.PrevClose = parseFloat(parts, 3)
	quote.High = parseFloat(parts, 4)
	quote.Low = parseFloat(parts, 5)
	quote.Price = parseFloat(parts, 6)
	quote.Amount = parseFloat(parts, 11)
	quote.Volume = parseFloat(parts, 12)
	if quote.PrevClose != 0 {
		quote.ChangePct = (quote.Price/quote.PrevClose - 1) * 100
	}
	quote.QuoteDate = strings.ReplaceAll(strings.TrimSpace(parts[17]), "/", "-")
	quote.QuoteTime = trimTime(parts[18])
	return quote
}

func parseUSPayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:       symbol,
		Source:       "sina_us",
		QuoteSession: "regular",
		FetchedAt:    now,
	}
	if len(parts) < 8 || parts[0] == "" {
		quote.Error = "empty sina us payload"
		return quote
	}
	quote.Name = parts[0]
	quote.Price = parseFloat(parts, 1)
	quote.ChangePct = parseFloat(parts, 2)
	quote.Open = parseFloat(parts, 5)
	quote.High = parseFloat(parts, 6)
	quote.Low = parseFloat(parts, 7)
	quote.Volume = parseFloat(parts, 10)
	if len(parts) > 26 {
		quote.PrevClose = parseFloat(parts, 26)
	}
	if quote.PrevClose == 0 && quote.ChangePct != -100 {
		quote.PrevClose = quote.Price / (1 + quote.ChangePct/100)
	}
	if len(parts) > 3 {
		date, tm := splitDateTime(parts[3])
		quote.QuoteDate = date
		quote.QuoteTime = tm
	}
	if afterPrice := parseFloat(parts, 21); afterPrice > 0 {
		year := now.Year()
		if parsedYear := int(parseFloat(parts, 29)); parsedYear > 2000 {
			year = parsedYear
		}
		afterAt, afterOK := parseUSMarketTimestamp(valueAt(parts, 24), year)
		closeAt, closeOK := parseUSMarketTimestamp(valueAt(parts, 25), year)
		if afterOK && (!closeOK || afterAt.After(closeAt)) {
			quote.Price = afterPrice
			quote.ChangePct = parseFloat(parts, 22)
			if regularPrice := parseFloat(parts, 1); regularPrice > 0 {
				quote.PrevClose = regularPrice
			}
			if quote.PrevClose == 0 && quote.ChangePct != -100 {
				quote.PrevClose = quote.Price / (1 + quote.ChangePct/100)
			}
			quote.Source = "sina_us_after_hours"
			quote.QuoteSession = "extended"
			quote.QuoteDate = afterAt.In(shanghaiLocation()).Format("2006-01-02")
			quote.QuoteTime = afterAt.In(shanghaiLocation()).Format("15:04:05")
		}
	}
	return quote
}

func parseFXPayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:    symbol,
		Source:    "sina_fx",
		FetchedAt: now,
	}
	if len(parts) < 9 {
		quote.Error = "empty sina fx payload"
		return quote
	}
	quote.QuoteTime = trimTime(parts[0])
	quote.Price = parseFloat(parts, 1)
	quote.Open = parseFloat(parts, 2)
	quote.High = parseFloat(parts, 3)
	quote.Low = parseFloat(parts, 6)
	quote.Name = parts[8]
	if len(parts) > 9 {
		quote.ChangePct = parseFloat(parts, 9)
	}
	if len(parts) > 17 {
		quote.QuoteDate = strings.TrimSpace(parts[len(parts)-1])
	}
	return quote
}

func parseHFFuturePayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:       symbol,
		Source:       "sina_hf",
		QuoteSession: "global_future",
		FetchedAt:    now,
	}
	if len(parts) < 14 {
		quote.Error = "empty sina hf payload"
		return quote
	}
	quote.Price = parseFloat(parts, 0)
	quote.High = parseFloat(parts, 4)
	quote.Low = parseFloat(parts, 5)
	quote.QuoteTime = trimTime(parts[6])
	quote.PrevClose = parseFloat(parts, 7)
	quote.Open = parseFloat(parts, 8)
	quote.Volume = parseFloat(parts, 9)
	quote.QuoteDate = strings.TrimSpace(parts[12])
	quote.Name = strings.TrimSpace(parts[13])
	if quote.PrevClose != 0 {
		quote.ChangePct = (quote.Price/quote.PrevClose - 1) * 100
	}
	return quote
}

func parseNFFuturePayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:       symbol,
		Source:       "sina_nf",
		QuoteSession: "cn_future",
		FetchedAt:    now,
	}
	if len(parts) < 18 || parts[0] == "" {
		quote.Error = "empty sina nf payload"
		return quote
	}
	quote.Name = strings.TrimSpace(parts[0])
	quote.QuoteTime = normalizeCompactTime(parts[1])
	quote.Price = parseFloat(parts, 2)
	quote.High = parseFloat(parts, 3)
	quote.Low = parseFloat(parts, 4)
	quote.Open = parseFloat(parts, 6)
	quote.PrevClose = parseFloat(parts, 10)
	quote.Amount = parseFloat(parts, 13)
	quote.Volume = parseFloat(parts, 14)
	quote.QuoteDate = strings.TrimSpace(parts[17])
	if quote.PrevClose != 0 {
		quote.ChangePct = (quote.Price/quote.PrevClose - 1) * 100
	}
	return quote
}

func parseZNBIndexPayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:       symbol,
		Source:       "sina_znb",
		QuoteSession: "global_index",
		FetchedAt:    now,
	}
	if len(parts) < 6 || parts[0] == "" {
		quote.Error = "empty sina znb payload"
		return quote
	}
	quote.Name = strings.TrimSpace(parts[0])
	quote.Price = parseFloat(parts, 1)
	quote.ChangePct = parseFloat(parts, 3)
	if len(parts) >= 8 {
		quote.QuoteDate = strings.TrimSpace(parts[6])
		quote.QuoteTime = trimTime(parts[7])
		quote.Open = parseFloat(parts, 8)
		quote.PrevClose = parseFloat(parts, 9)
		quote.High = parseFloat(parts, 10)
		quote.Low = parseFloat(parts, 11)
		quote.Volume = parseFloat(parts, 12)
	} else if timestamp := int64(parseFloat(parts, 5)); timestamp > 0 {
		quoteAt := time.Unix(timestamp, 0).In(shanghaiLocation())
		quote.QuoteDate = quoteAt.Format("2006-01-02")
		quote.QuoteTime = quoteAt.Format("15:04:05")
	} else {
		quote.QuoteTime = trimTime(parts[4])
	}
	if quote.PrevClose == 0 && quote.ChangePct != -100 {
		quote.PrevClose = quote.Price / (1 + quote.ChangePct/100)
	}
	return quote
}

func parseBIndexPayload(symbol string, payload string) domain.Quote {
	now := time.Now()
	parts := strings.Split(payload, ",")
	quote := domain.Quote{
		Symbol:       symbol,
		Source:       "sina_b_index",
		QuoteSession: "index",
		FetchedAt:    now,
	}
	if len(parts) < 6 || parts[0] == "" {
		quote.Error = "empty sina b index payload"
		return quote
	}
	quote.Name = strings.TrimSpace(parts[0])
	quote.Price = parseFloat(parts, 1)
	quote.ChangePct = parseFloat(parts, 3)
	if len(parts) >= 8 {
		quote.QuoteDate = strings.TrimSpace(parts[6])
		quote.QuoteTime = trimTime(parts[7])
		quote.Open = parseFloat(parts, 8)
		quote.PrevClose = parseFloat(parts, 9)
		quote.High = parseFloat(parts, 10)
		quote.Low = parseFloat(parts, 11)
		quote.Volume = parseFloat(parts, 12)
	} else {
		quote.QuoteDate = strings.TrimSpace(parts[5])
		change := parseFloat(parts, 2)
		if change != 0 {
			quote.PrevClose = quote.Price - change
		}
	}
	if quote.PrevClose == 0 && quote.ChangePct != -100 {
		quote.PrevClose = quote.Price / (1 + quote.ChangePct/100)
	}
	return quote
}

func parseFloat(parts []string, idx int) float64 {
	if idx >= len(parts) {
		return 0
	}
	value, err := strconv.ParseFloat(strings.TrimSpace(parts[idx]), 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return 0
	}
	return value
}

func roundTick(value float64) float64 {
	return math.Round(value*1000) / 1000
}

func valueAt(parts []string, idx int) string {
	if idx >= len(parts) {
		return ""
	}
	return strings.TrimSpace(parts[idx])
}

func splitDateTime(value string) (string, string) {
	fields := strings.Fields(strings.TrimSpace(value))
	if len(fields) == 0 {
		return "", ""
	}
	if len(fields) == 1 {
		return fields[0], ""
	}
	return fields[0], trimTime(fields[1])
}

func normalizeCompactTime(value string) string {
	value = strings.TrimSpace(value)
	if regexp.MustCompile(`^\d{6}$`).MatchString(value) {
		return value[:2] + ":" + value[2:4] + ":" + value[4:6]
	}
	return trimTime(value)
}

func trimTime(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= len("15:04:05") {
		return value[:len("15:04:05")]
	}
	return value
}

func parseUSMarketTimestamp(value string, year int) (time.Time, bool) {
	fields := strings.Fields(strings.TrimSpace(value))
	if len(fields) < 3 || strings.EqualFold(fields[0], "--") {
		return time.Time{}, false
	}
	loc := newYorkLocation()
	candidate := strings.Join(fields[:3], " ") + " " + strconv.Itoa(year)
	parsed, err := time.ParseInLocation("Jan 02 03:04PM 2006", candidate, loc)
	if err != nil {
		return time.Time{}, false
	}
	return parsed, true
}

func finalizeQuote(quote *domain.Quote, sinaSymbol string, now time.Time) {
	quote.SourceSymbol = sinaSymbol
	if quote.FetchedAt.IsZero() {
		quote.FetchedAt = now
	}
	if quote.Error != "" {
		quote.RealtimeStatus = "unsupported"
		quote.StaleReason = quote.Error
		return
	}

	quoteAt, ok := quoteTimeInShanghai(*quote)
	if !ok {
		quote.RealtimeStatus = "unsupported"
		quote.StaleReason = "missing quote timestamp"
		return
	}

	threshold := 10 * time.Minute
	sessionOpen := false
	switch {
	case isAStockSinaSymbol(sinaSymbol):
		quote.QuoteSession = defaultString(quote.QuoteSession, "cn_regular")
		sessionOpen = isCNStockSession(now)
	case strings.HasPrefix(sinaSymbol, "rt_hk"):
		quote.QuoteSession = defaultString(quote.QuoteSession, "hk_regular")
		sessionOpen = isHKStockSession(now)
	case strings.HasPrefix(sinaSymbol, "gb_"):
		threshold = 20 * time.Minute
		sessionOpen = isUSExtendedSession(now)
	case strings.HasPrefix(sinaSymbol, "fx_"):
		quote.QuoteSession = defaultString(quote.QuoteSession, "fx_weekday")
		sessionOpen = isFXSession(now)
	case strings.HasPrefix(sinaSymbol, "hf_"):
		threshold = 20 * time.Minute
		sessionOpen = isGlobalFutureSession(now)
	case strings.HasPrefix(sinaSymbol, "nf_"):
		sessionOpen = isCNFutureSession(now)
	case strings.HasPrefix(sinaSymbol, "znb_"), strings.HasPrefix(sinaSymbol, "b_"):
		threshold = 20 * time.Minute
		sessionOpen = true
	default:
		sessionOpen = true
	}

	age := now.In(shanghaiLocation()).Sub(quoteAt)
	if age < 0 {
		age = 0
	}
	if sessionOpen && age <= threshold {
		quote.IsRealtime = true
		quote.RealtimeStatus = "realtime"
		return
	}
	if !sessionOpen {
		quote.RealtimeStatus = "closed"
		quote.StaleReason = "market closed"
		return
	}
	quote.RealtimeStatus = "stale"
	quote.StaleReason = "quote timestamp older than refresh threshold"
}

func quoteTimeInShanghai(quote domain.Quote) (time.Time, bool) {
	date := strings.TrimSpace(quote.QuoteDate)
	tm := normalizeCompactTime(quote.QuoteTime)
	if date == "" || tm == "" {
		return time.Time{}, false
	}
	if len(tm) == len("15:04") {
		tm += ":00"
	}
	parsed, err := time.ParseInLocation("2006-01-02 15:04:05", date+" "+tm, shanghaiLocation())
	if err != nil {
		return time.Time{}, false
	}
	return parsed, true
}

func isAStockSinaSymbol(symbol string) bool {
	return strings.HasPrefix(symbol, "sh") || strings.HasPrefix(symbol, "sz") || strings.HasPrefix(symbol, "bj")
}

func isCNStockSession(now time.Time) bool {
	t := now.In(shanghaiLocation())
	if !isWeekday(t) {
		return false
	}
	minute := t.Hour()*60 + t.Minute()
	return inMinuteRange(minute, 9*60+15, 11*60+30) || inMinuteRange(minute, 13*60, 15*60)
}

func isHKStockSession(now time.Time) bool {
	t := now.In(shanghaiLocation())
	if !isWeekday(t) {
		return false
	}
	minute := t.Hour()*60 + t.Minute()
	return inMinuteRange(minute, 9*60+30, 12*60) || inMinuteRange(minute, 13*60, 16*60+10)
}

func isCNFutureSession(now time.Time) bool {
	t := now.In(shanghaiLocation())
	minute := t.Hour()*60 + t.Minute()
	if t.Weekday() == time.Saturday {
		return inMinuteRange(minute, 0, 2*60+30)
	}
	if !isWeekday(t) {
		return false
	}
	return inMinuteRange(minute, 9*60, 11*60+30) ||
		inMinuteRange(minute, 13*60+30, 15*60) ||
		inMinuteRange(minute, 21*60, 24*60) ||
		inMinuteRange(minute, 0, 2*60+30)
}

func isUSExtendedSession(now time.Time) bool {
	t := now.In(newYorkLocation())
	if !isWeekday(t) {
		return false
	}
	minute := t.Hour()*60 + t.Minute()
	return inMinuteRange(minute, 4*60, 20*60)
}

func isFXSession(now time.Time) bool {
	t := now.In(newYorkLocation())
	if t.Weekday() == time.Saturday {
		return false
	}
	if t.Weekday() == time.Sunday && t.Hour() < 17 {
		return false
	}
	if t.Weekday() == time.Friday && t.Hour() >= 17 {
		return false
	}
	return true
}

func isGlobalFutureSession(now time.Time) bool {
	t := now.In(newYorkLocation())
	if t.Weekday() == time.Saturday {
		return false
	}
	if t.Weekday() == time.Sunday && t.Hour() < 18 {
		return false
	}
	if t.Weekday() == time.Friday && t.Hour() >= 17 {
		return false
	}
	return true
}

func isWeekday(t time.Time) bool {
	return t.Weekday() >= time.Monday && t.Weekday() <= time.Friday
}

func inMinuteRange(value int, start int, end int) bool {
	return value >= start && value <= end
}

func defaultString(value string, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func shanghaiLocation() *time.Location {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.Local
	}
	return loc
}

func newYorkLocation() *time.Location {
	loc, err := time.LoadLocation("America/New_York")
	if err != nil {
		return time.UTC
	}
	return loc
}

func DemoQuote(symbol string) domain.Quote {
	hash := sha1.Sum([]byte(symbol))
	base := 0.5 + float64(binary.BigEndian.Uint16(hash[:2])%6000)/1000
	prev := base * (0.985 + float64(hash[2]%30)/1000)
	change := 0.0
	if prev != 0 {
		change = (base/prev - 1) * 100
	}
	now := time.Now()
	return domain.Quote{
		Symbol:         symbol,
		Name:           symbol,
		Price:          round4(base),
		PrevClose:      round4(prev),
		Open:           round4(prev),
		High:           round4(base * 1.01),
		Low:            round4(base * 0.99),
		ChangePct:      round4(change),
		QuoteDate:      now.Format("2006-01-02"),
		QuoteTime:      now.Format("15:04"),
		Source:         "demo",
		SourceSymbol:   ToSinaSymbol(symbol),
		IsRealtime:     false,
		RealtimeStatus: "unsupported",
		StaleReason:    "demo fallback",
		FetchedAt:      now,
		Error:          "demo fallback",
	}
}

func round4(value float64) float64 {
	return math.Round(value*10000) / 10000
}
