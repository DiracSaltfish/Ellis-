package snapshot

import (
	"encoding/csv"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const minuteHistoryFile = "valuation.csv"
const (
	defaultMinuteHistoryRetentionDays = 45
	minuteHistorySessionStartMinute   = 9*60 + 30
	minuteHistorySessionEndMinute     = 15 * 60
	minuteHistoryRolloverMinute       = 9 * 60
)

type MinuteHistoryPoint struct {
	Symbol          string   `json:"symbol"`
	Minute          string   `json:"minute"`
	Timestamp       string   `json:"timestamp"`
	MarketPrice     float64  `json:"market_price"`
	EstimatedNAV    float64  `json:"estimated_nav"`
	PremiumPct      float64  `json:"premium_pct"`
	OfficialEST     float64  `json:"official_est,omitempty"`
	FairEST         float64  `json:"fair_est,omitempty"`
	RealtimeEST     *float64 `json:"realtime_est,omitempty"`
	EffectiveRatio  float64  `json:"effective_ratio,omitempty"`
	QuoteSource     string   `json:"quote_source,omitempty"`
	QuoteStatus     string   `json:"quote_status,omitempty"`
	ModelVersion    string   `json:"model_version,omitempty"`
	ReferenceSymbol string   `json:"reference_symbol,omitempty"`
	UploadSource    string   `json:"upload_source,omitempty"`
}

type MinuteHistoryChartPoint struct {
	// Min keeps the minute label compact in the transport payload.
	Minute string `json:"min"`
	// Mkp is the rounded market price for that minute.
	MarketPrice float64 `json:"mkp"`
	// EstNAV is the rounded estimated NAV for that minute.
	EstimatedNAV float64 `json:"estnav"`
	// Pmp is the rounded premium/discount percentage for that minute.
	PremiumPct float64 `json:"pmp"`
	// Oest is the rounded official (base) NAV used that minute. It is omitted
	// when zero so legacy replay clients can keep treating absent as unknown.
	OfficialEst float64 `json:"oest,omitempty"`
}

type MinuteHistoryResponse struct {
	Symbol string                    `json:"symbol"`
	Days   int                       `json:"days"`
	Date   string                    `json:"date,omitempty"`
	Rows   []MinuteHistoryChartPoint `json:"rows"`
}

type MinuteHistoryDatesResponse struct {
	Symbol string   `json:"symbol"`
	Dates  []string `json:"dates"`
}

type MinuteHistoryStore struct {
	root          string
	retentionDays int
	mu            sync.Mutex
}

func NewMinuteHistoryStore(root string, retentionDays ...int) *MinuteHistoryStore {
	root = strings.TrimSpace(root)
	if root == "" {
		return nil
	}
	days := defaultMinuteHistoryRetentionDays
	if len(retentionDays) > 0 && retentionDays[0] > 0 {
		days = retentionDays[0]
	}
	return &MinuteHistoryStore{root: root, retentionDays: days}
}

func (s *MinuteHistoryStore) Upsert(now time.Time, rows []MinuteHistoryPoint) error {
	if s == nil || s.root == "" || len(rows) == 0 {
		return nil
	}

	rowsByDaySymbol := make(map[string]map[string][]MinuteHistoryPoint)
	for _, row := range rows {
		row.Symbol = strings.ToUpper(strings.TrimSpace(row.Symbol))
		if row.Symbol == "" || row.Minute == "" || row.EstimatedNAV <= 0 {
			continue
		}
		if !isMinuteHistorySessionMinute(row.Minute) {
			continue
		}
		day := minuteHistoryDayFromMinute(row.Minute)
		if day == "" {
			day = minuteHistoryDay(now)
		}
		if rowsByDaySymbol[day] == nil {
			rowsByDaySymbol[day] = make(map[string][]MinuteHistoryPoint)
		}
		rowsByDaySymbol[day][row.Symbol] = append(rowsByDaySymbol[day][row.Symbol], row)
	}
	if len(rowsByDaySymbol) == 0 {
		return nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if err := s.cleanupLocked(now); err != nil {
		return err
	}
	for day, rowsBySymbol := range rowsByDaySymbol {
		for symbol, symbolRows := range rowsBySymbol {
			current, err := s.readSymbolDayLocked(day, symbol)
			if err != nil {
				return err
			}
			byMinute := make(map[string]MinuteHistoryPoint, len(current)+len(symbolRows))
			for _, row := range current {
				if row.Symbol == "" || row.Minute == "" || !isMinuteHistorySessionMinute(row.Minute) {
					continue
				}
				byMinute[row.Minute] = row
			}
			for _, row := range symbolRows {
				row.Symbol = symbol
				byMinute[row.Minute] = row
			}
			merged := make([]MinuteHistoryPoint, 0, len(byMinute))
			for _, row := range byMinute {
				merged = append(merged, row)
			}
			sortMinuteHistoryRows(merged)
			if err := writeMinuteHistoryFile(s.symbolPath(day, symbol), merged); err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *MinuteHistoryStore) Read(symbol string, days int, now time.Time) ([]MinuteHistoryPoint, error) {
	if s == nil || s.root == "" {
		return nil, nil
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" {
		return nil, nil
	}
	if days <= 0 {
		days = 2
	}
	if s.retentionDays > 0 && days > s.retentionDays {
		days = s.retentionDays
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	rows := make([]MinuteHistoryPoint, 0)
	for _, day := range recentMinuteHistoryDays(now, days) {
		dayRows, err := s.readSymbolDayLocked(day, symbol)
		if err != nil {
			return nil, err
		}
		for _, row := range dayRows {
			if strings.EqualFold(row.Symbol, symbol) && isMinuteHistorySessionMinute(row.Minute) {
				rows = append(rows, row)
			}
		}
	}
	sortMinuteHistoryRows(rows)
	return rows, nil
}

func (s *MinuteHistoryStore) ReadDate(symbol string, day string) ([]MinuteHistoryPoint, error) {
	if s == nil || s.root == "" {
		return nil, nil
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	day = normalizeMinuteHistoryDayKey(day)
	if symbol == "" || day == "" {
		return nil, nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	rows, err := s.readSymbolDayLocked(day, symbol)
	if err != nil {
		return nil, err
	}
	filtered := rows[:0]
	for _, row := range rows {
		if strings.EqualFold(row.Symbol, symbol) && isMinuteHistorySessionMinute(row.Minute) {
			filtered = append(filtered, row)
		}
	}
	sortMinuteHistoryRows(filtered)
	return filtered, nil
}

func (s *MinuteHistoryStore) ReadDay(day string) ([]MinuteHistoryPoint, error) {
	if s == nil || s.root == "" {
		return nil, nil
	}
	day = normalizeMinuteHistoryDayKey(day)
	if day == "" {
		return nil, nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	rows, err := s.readDayLocked(day)
	if err != nil {
		return nil, err
	}
	sortMinuteHistoryRows(rows)
	return rows, nil
}

func (s *MinuteHistoryStore) ReplaceDay(day string, rows []MinuteHistoryPoint) error {
	if s == nil || s.root == "" {
		return nil
	}
	day = normalizeMinuteHistoryDayKey(day)
	if day == "" {
		return nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if err := s.removeSymbolFilesLocked(day); err != nil {
		return err
	}
	rowsBySymbol := make(map[string][]MinuteHistoryPoint)
	for _, row := range rows {
		row.Symbol = strings.ToUpper(strings.TrimSpace(row.Symbol))
		if row.Symbol == "" {
			continue
		}
		rowsBySymbol[row.Symbol] = append(rowsBySymbol[row.Symbol], row)
	}
	sortMinuteHistoryRows(rows)
	for symbol, symbolRows := range rowsBySymbol {
		sortMinuteHistoryRows(symbolRows)
		if err := writeMinuteHistoryFile(s.symbolPath(day, symbol), symbolRows); err != nil {
			return err
		}
	}
	return nil
}

func (s *MinuteHistoryStore) ReplaceDaySymbols(day string, symbols []string, rows []MinuteHistoryPoint) error {
	if s == nil || s.root == "" {
		return nil
	}
	day = normalizeMinuteHistoryDayKey(day)
	if day == "" {
		return nil
	}

	symbolSet := make(map[string]bool)
	for _, symbol := range symbols {
		symbol = strings.ToUpper(strings.TrimSpace(symbol))
		if symbol != "" {
			symbolSet[symbol] = true
		}
	}
	for _, row := range rows {
		symbol := strings.ToUpper(strings.TrimSpace(row.Symbol))
		if symbol != "" {
			symbolSet[symbol] = true
		}
	}
	if len(symbolSet) == 0 {
		return nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	for symbol := range symbolSet {
		path := s.symbolPath(day, symbol)
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	rowsBySymbol := make(map[string][]MinuteHistoryPoint)
	for _, row := range rows {
		row.Symbol = strings.ToUpper(strings.TrimSpace(row.Symbol))
		if row.Symbol == "" || !symbolSet[row.Symbol] || row.Minute == "" || !isMinuteHistorySessionMinute(row.Minute) {
			continue
		}
		rowsBySymbol[row.Symbol] = append(rowsBySymbol[row.Symbol], row)
	}
	for symbol := range symbolSet {
		symbolRows := rowsBySymbol[symbol]
		sortMinuteHistoryRows(symbolRows)
		if err := writeMinuteHistoryFile(s.symbolPath(day, symbol), symbolRows); err != nil {
			return err
		}
	}
	return nil
}

func (s *MinuteHistoryStore) AvailableDates(symbol string, limit int, now time.Time) ([]string, error) {
	if s == nil || s.root == "" {
		return nil, nil
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" {
		return nil, nil
	}
	if limit <= 0 {
		limit = s.retentionDays
	}
	if s.retentionDays > 0 && limit > s.retentionDays {
		limit = s.retentionDays
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	entries, err := os.ReadDir(s.root)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	days := make([]string, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		day := normalizeMinuteHistoryDayKey(entry.Name())
		if day == "" {
			continue
		}
		days = append(days, day)
	}
	sort.Sort(sort.Reverse(sort.StringSlice(days)))

	out := make([]string, 0, limit)
	for _, day := range days {
		rows, err := s.readSymbolDayLocked(day, symbol)
		if err != nil {
			return nil, err
		}
		if len(rows) == 0 {
			continue
		}
		out = append(out, day)
		if len(out) >= limit {
			break
		}
	}
	return out, nil
}

func minuteHistoryMinute(t time.Time) string {
	loc := shanghaiLocation()
	return t.In(loc).Format("2006-01-02 15:04")
}

func minuteHistoryDay(t time.Time) string {
	loc := shanghaiLocation()
	return t.In(loc).Format("20060102")
}

func minuteHistoryDayFromMinute(value string) string {
	parsed, err := parseMinuteHistoryMinute(value)
	if err != nil {
		return ""
	}
	return parsed.In(shanghaiLocation()).Format("20060102")
}

func minuteHistoryChartRows(rows []MinuteHistoryPoint, day string) []MinuteHistoryChartPoint {
	day = normalizeMinuteHistoryDayKey(day)
	out := make([]MinuteHistoryChartPoint, 0, len(rows))
	for _, row := range rows {
		minute := row.Minute
		if day != "" {
			minute = minuteHistoryTimeLabel(row.Minute)
		}
		out = append(out, MinuteHistoryChartPoint{
			Minute:       minute,
			MarketPrice:  roundMinuteHistoryTransportValue(row.MarketPrice, 3),
			EstimatedNAV: roundMinuteHistoryTransportValue(row.EstimatedNAV, 4),
			PremiumPct:   roundMinuteHistoryTransportValue(row.PremiumPct, 2),
			OfficialEst:  roundMinuteHistoryTransportValue(row.OfficialEST, 4),
		})
	}
	return out
}

func roundMinuteHistoryTransportValue(value float64, digits int) float64 {
	if digits < 0 {
		return value
	}
	scale := math.Pow10(digits)
	return math.Round(value*scale) / scale
}

func minuteHistoryTimeLabel(value string) string {
	parsed, err := parseMinuteHistoryMinute(value)
	if err == nil {
		return parsed.In(shanghaiLocation()).Format("15:04")
	}
	if len(value) >= 5 {
		tail := value[len(value)-5:]
		if len(tail) == 5 && tail[2] == ':' {
			return tail
		}
	}
	return value
}

func normalizeMinuteHistoryDayKey(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	for _, pattern := range []string{"20060102", "2006-01-02"} {
		if parsed, err := time.ParseInLocation(pattern, value, shanghaiLocation()); err == nil {
			return parsed.Format("20060102")
		}
	}
	return ""
}

func isMinuteHistorySessionTime(t time.Time) bool {
	loc := shanghaiLocation()
	local := t.In(loc)
	switch local.Weekday() {
	case time.Saturday, time.Sunday:
		return false
	}
	minuteOfDay := local.Hour()*60 + local.Minute()
	return minuteOfDay >= minuteHistorySessionStartMinute && minuteOfDay <= minuteHistorySessionEndMinute
}

func isMinuteHistorySessionMinute(value string) bool {
	parsed, err := parseMinuteHistoryMinute(value)
	if err != nil {
		return false
	}
	return isMinuteHistorySessionTime(parsed)
}

func parseMinuteHistoryMinute(value string) (time.Time, error) {
	value = strings.TrimSpace(value)
	loc := shanghaiLocation()
	for _, pattern := range []string{
		"2006-01-02 15:04",
		"2006-01-02 15:04:05",
		"2006-01-02T15:04",
		"2006-01-02T15:04:05",
		"200601021504",
		"20060102150405",
	} {
		if parsed, err := time.ParseInLocation(pattern, value, loc); err == nil {
			return parsed, nil
		}
	}
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err == nil {
		return parsed.In(loc), nil
	}
	return time.Time{}, err
}

func recentMinuteHistoryDays(now time.Time, days int) []string {
	out := make([]string, 0, days)
	base := minuteHistoryLogicalDay(now)
	for i := days - 1; i >= 0; i-- {
		out = append(out, base.AddDate(0, 0, -i).Format("20060102"))
	}
	return out
}

func (s *MinuteHistoryStore) cleanupLocked(now time.Time) error {
	entries, err := os.ReadDir(s.root)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	retentionDays := s.retentionDays
	if retentionDays <= 0 {
		retentionDays = defaultMinuteHistoryRetentionDays
	}
	cutoff := minuteHistoryLogicalDay(now).AddDate(0, 0, -(retentionDays - 1)).Format("20060102")
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		name := entry.Name()
		if len(name) != 8 || name >= cutoff {
			continue
		}
		if _, err := time.Parse("20060102", name); err != nil {
			continue
		}
		if err := os.RemoveAll(filepath.Join(s.root, name)); err != nil {
			return err
		}
	}
	return nil
}

func (s *MinuteHistoryStore) symbolPath(day string, symbol string) string {
	return filepath.Join(s.root, day, minuteHistorySymbolFile(symbol))
}

func minuteHistorySymbolFile(symbol string) string {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" {
		return ""
	}
	var builder strings.Builder
	for _, ch := range symbol {
		switch {
		case ch >= 'A' && ch <= 'Z':
			builder.WriteRune(ch)
		case ch >= '0' && ch <= '9':
			builder.WriteRune(ch)
		case ch == '_' || ch == '-' || ch == '.':
			builder.WriteRune(ch)
		default:
			builder.WriteByte('_')
		}
	}
	return builder.String() + ".csv"
}

func (s *MinuteHistoryStore) readSymbolDayLocked(day string, symbol string) ([]MinuteHistoryPoint, error) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if day == "" || symbol == "" {
		return nil, nil
	}
	path := s.symbolPath(day, symbol)
	if _, err := os.Stat(path); err == nil {
		rows, err := readMinuteHistoryFile(path)
		if err != nil {
			return nil, err
		}
		return filterMinuteHistoryRows(rows, symbol), nil
	} else if !os.IsNotExist(err) {
		return nil, err
	}

	legacyPath := filepath.Join(s.root, day, minuteHistoryFile)
	rows, err := readMinuteHistoryFile(legacyPath)
	if err != nil {
		return nil, err
	}
	return filterMinuteHistoryRows(rows, symbol), nil
}

func (s *MinuteHistoryStore) readDayLocked(day string) ([]MinuteHistoryPoint, error) {
	byKey := make(map[string]MinuteHistoryPoint)
	legacyPath := filepath.Join(s.root, day, minuteHistoryFile)
	legacyRows, err := readMinuteHistoryFile(legacyPath)
	if err != nil {
		return nil, err
	}
	for _, row := range legacyRows {
		if row.Symbol == "" || row.Minute == "" || !isMinuteHistorySessionMinute(row.Minute) {
			continue
		}
		byKey[row.Symbol+"|"+row.Minute] = row
	}

	dayPath := filepath.Join(s.root, day)
	entries, err := os.ReadDir(dayPath)
	if os.IsNotExist(err) {
		return mapMinuteHistoryRows(byKey), nil
	}
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if entry.IsDir() || entry.Name() == minuteHistoryFile || filepath.Ext(entry.Name()) != ".csv" {
			continue
		}
		rows, err := readMinuteHistoryFile(filepath.Join(dayPath, entry.Name()))
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			if row.Symbol == "" || row.Minute == "" || !isMinuteHistorySessionMinute(row.Minute) {
				continue
			}
			byKey[row.Symbol+"|"+row.Minute] = row
		}
	}
	return mapMinuteHistoryRows(byKey), nil
}

func (s *MinuteHistoryStore) removeSymbolFilesLocked(day string) error {
	dayPath := filepath.Join(s.root, day)
	entries, err := os.ReadDir(dayPath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if entry.IsDir() || entry.Name() == minuteHistoryFile || filepath.Ext(entry.Name()) != ".csv" {
			continue
		}
		if err := os.Remove(filepath.Join(dayPath, entry.Name())); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

func filterMinuteHistoryRows(rows []MinuteHistoryPoint, symbol string) []MinuteHistoryPoint {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	out := make([]MinuteHistoryPoint, 0, len(rows))
	for _, row := range rows {
		if strings.EqualFold(row.Symbol, symbol) && row.Minute != "" && isMinuteHistorySessionMinute(row.Minute) {
			out = append(out, row)
		}
	}
	sortMinuteHistoryRows(out)
	return out
}

func mapMinuteHistoryRows(rows map[string]MinuteHistoryPoint) []MinuteHistoryPoint {
	out := make([]MinuteHistoryPoint, 0, len(rows))
	for _, row := range rows {
		out = append(out, row)
	}
	return out
}

func minuteHistoryLogicalDay(t time.Time) time.Time {
	loc := shanghaiLocation()
	local := t.In(loc)
	minuteOfDay := local.Hour()*60 + local.Minute()
	if minuteOfDay < minuteHistoryRolloverMinute {
		local = local.AddDate(0, 0, -1)
	}
	return time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, loc)
}

func readMinuteHistoryFile(path string) ([]MinuteHistoryPoint, error) {
	file, err := os.Open(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer file.Close()

	reader := csv.NewReader(file)
	records, err := reader.ReadAll()
	if err != nil {
		return nil, err
	}
	if len(records) <= 1 {
		return nil, nil
	}
	rows := make([]MinuteHistoryPoint, 0, len(records)-1)
	for _, record := range records[1:] {
		row := minuteHistoryPointFromRecord(record)
		if row.Symbol != "" && row.Minute != "" {
			rows = append(rows, row)
		}
	}
	return rows, nil
}

func writeMinuteHistoryFile(path string, rows []MinuteHistoryPoint) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp := path + ".tmp"
	file, err := os.Create(tmp)
	if err != nil {
		return err
	}
	writer := csv.NewWriter(file)
	if err := writer.Write(minuteHistoryFields()); err != nil {
		file.Close()
		return err
	}
	for _, row := range rows {
		if err := writer.Write(minuteHistoryRecord(row)); err != nil {
			file.Close()
			return err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func minuteHistoryFields() []string {
	return []string{
		"symbol",
		"minute",
		"timestamp",
		"market_price",
		"estimated_nav",
		"premium_pct",
		"official_est",
		"fair_est",
		"realtime_est",
		"effective_ratio",
		"quote_source",
		"quote_status",
		"model_version",
		"reference_symbol",
		"upload_source",
	}
}

func minuteHistoryRecord(row MinuteHistoryPoint) []string {
	realtime := ""
	if row.RealtimeEST != nil {
		realtime = formatFloat(*row.RealtimeEST)
	}
	return []string{
		row.Symbol,
		row.Minute,
		row.Timestamp,
		formatFloat(row.MarketPrice),
		formatFloat(row.EstimatedNAV),
		formatFloat(row.PremiumPct),
		formatFloat(row.OfficialEST),
		formatFloat(row.FairEST),
		realtime,
		formatFloat(row.EffectiveRatio),
		row.QuoteSource,
		row.QuoteStatus,
		row.ModelVersion,
		row.ReferenceSymbol,
		row.UploadSource,
	}
}

func minuteHistoryPointFromRecord(record []string) MinuteHistoryPoint {
	value := func(index int) string {
		if index >= len(record) {
			return ""
		}
		return strings.TrimSpace(record[index])
	}
	var realtime *float64
	if parsed, ok := parseFloat(value(8)); ok {
		realtime = &parsed
	}
	return MinuteHistoryPoint{
		Symbol:          strings.ToUpper(value(0)),
		Minute:          value(1),
		Timestamp:       value(2),
		MarketPrice:     parseFloatOrZero(value(3)),
		EstimatedNAV:    parseFloatOrZero(value(4)),
		PremiumPct:      parseFloatOrZero(value(5)),
		OfficialEST:     parseFloatOrZero(value(6)),
		FairEST:         parseFloatOrZero(value(7)),
		RealtimeEST:     realtime,
		EffectiveRatio:  parseFloatOrZero(value(9)),
		QuoteSource:     publicSourceLabel(value(10)),
		QuoteStatus:     value(11),
		ModelVersion:    value(12),
		ReferenceSymbol: value(13),
		UploadSource:    value(14),
	}
}

func sortMinuteHistoryRows(rows []MinuteHistoryPoint) {
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Minute == rows[j].Minute {
			return rows[i].Symbol < rows[j].Symbol
		}
		return rows[i].Minute < rows[j].Minute
	})
}

func formatFloat(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}

func parseFloatOrZero(value string) float64 {
	parsed, _ := parseFloat(value)
	return parsed
}

func parseFloat(value string) (float64, bool) {
	value = strings.TrimSpace(value)
	if value == "" {
		return 0, false
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil {
		return 0, false
	}
	return parsed, true
}

func shanghaiLocation() *time.Location {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	return loc
}
