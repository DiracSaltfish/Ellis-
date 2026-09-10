package sharesync

import (
	"context"
	"encoding/csv"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"newnavnav/internal/domain"
)

const shareHistoryCSVHeader = "share_date,symbol,fund_type,name,shares_10k,close_premium_pct,source,updated_at"

type CSVStore struct {
	dir                string
	closePremiumLookup ClosePremiumLookup
	mu                 sync.Mutex
}

func NewCSVStore(dir string) *CSVStore {
	dir = strings.TrimSpace(dir)
	if dir == "" {
		dir = "snapshots/share_history"
	}
	return &CSVStore{dir: dir}
}

func (s *CSVStore) SetClosePremiumLookup(lookup ClosePremiumLookup) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closePremiumLookup = lookup
}

func (s *CSVStore) UpsertShareHistory(ctx context.Context, rows []domain.ShareHistoryRecord) error {
	if len(rows) == 0 {
		return nil
	}
	if err := ApplyClosePremium(rows, s.closePremiumLookup); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	if err := os.MkdirAll(s.dir, 0o755); err != nil {
		return err
	}
	grouped := map[string][]domain.ShareHistoryRecord{}
	for _, row := range rows {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		row.Symbol = strings.ToUpper(strings.TrimSpace(row.Symbol))
		row.ShareDate = strings.TrimSpace(row.ShareDate)
		row.FundType = strings.ToUpper(strings.TrimSpace(row.FundType))
		row.Name = strings.TrimSpace(row.Name)
		row.Source = strings.TrimSpace(row.Source)
		if row.Symbol == "" || row.ShareDate == "" || row.Shares10K <= 0 {
			continue
		}
		if _, err := time.Parse("2006-01-02", row.ShareDate); err != nil {
			continue
		}
		if row.UpdatedAt == "" {
			row.UpdatedAt = time.Now().Format(time.RFC3339)
		}
		grouped[row.Symbol] = append(grouped[row.Symbol], row)
	}
	for symbol, symbolRows := range grouped {
		if err := s.upsertSymbolRows(symbol, symbolRows); err != nil {
			return err
		}
	}
	return nil
}

func (s *CSVStore) LoadShareHistory(ctx context.Context, symbol string, days int) ([]domain.ShareHistoryRecord, error) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" {
		return nil, nil
	}
	rows, err := s.readSymbolRows(symbol)
	if err != nil {
		if os.IsNotExist(err) {
			return []domain.ShareHistoryRecord{}, nil
		}
		return nil, err
	}
	sort.Slice(rows, func(i, j int) bool {
		return rows[i].ShareDate > rows[j].ShareDate
	})
	for i := 0; i+1 < len(rows); i++ {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}
		previous := rows[i+1].Shares10K
		if previous <= 0 {
			continue
		}
		change := rows[i].Shares10K - previous
		changePct := change / previous * 100
		rows[i].PreviousShares10K = &previous
		rows[i].ShareChange10K = &change
		rows[i].ShareChangePct = &changePct
	}
	if days > 0 && len(rows) > days {
		rows = rows[:days]
	}
	return rows, nil
}

func (s *CSVStore) LatestShareHistoryDateBySymbolPrefix(ctx context.Context, prefix string) (string, bool, error) {
	prefix = strings.ToUpper(strings.TrimSpace(prefix))
	files, err := filepath.Glob(filepath.Join(s.dir, "*"+prefix+".csv"))
	if err != nil {
		return "", false, err
	}
	latest := ""
	for _, file := range files {
		select {
		case <-ctx.Done():
			return "", false, ctx.Err()
		default:
		}
		rows, err := readShareHistoryCSV(file)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return "", false, err
		}
		for _, row := range rows {
			if row.ShareDate > latest {
				latest = row.ShareDate
			}
		}
	}
	if latest == "" {
		return "", false, nil
	}
	return latest, true, nil
}

func (s *CSVStore) upsertSymbolRows(symbol string, rows []domain.ShareHistoryRecord) error {
	path := filepath.Join(s.dir, shareHistoryFilename(symbol))
	existing, err := readShareHistoryCSV(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	byDate := make(map[string]domain.ShareHistoryRecord, len(existing)+len(rows))
	for _, row := range existing {
		byDate[row.ShareDate] = row
	}
	for _, row := range rows {
		if old, ok := byDate[row.ShareDate]; ok && row.Name == "" {
			row.Name = old.Name
		}
		if old, ok := byDate[row.ShareDate]; ok && row.ClosePremiumPct == nil {
			row.ClosePremiumPct = cloneFloatPointer(old.ClosePremiumPct)
		}
		byDate[row.ShareDate] = row
	}
	merged := make([]domain.ShareHistoryRecord, 0, len(byDate))
	for _, row := range byDate {
		merged = append(merged, row)
	}
	sort.Slice(merged, func(i, j int) bool {
		return merged[i].ShareDate < merged[j].ShareDate
	})
	return writeShareHistoryCSV(path, merged)
}

func (s *CSVStore) readSymbolRows(symbol string) ([]domain.ShareHistoryRecord, error) {
	return readShareHistoryCSV(filepath.Join(s.dir, shareHistoryFilename(symbol)))
}

func shareHistoryFilename(symbol string) string {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if len(symbol) >= 3 {
		prefix := symbol[:2]
		code := symbol[2:]
		if (prefix == "SZ" || prefix == "SH" || prefix == "BJ") && code != "" {
			return code + prefix + ".csv"
		}
	}
	clean := strings.NewReplacer("/", "_", "\\", "_", ":", "_").Replace(symbol)
	return clean + ".csv"
}

func readShareHistoryCSV(path string) ([]domain.ShareHistoryRecord, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	reader := csv.NewReader(file)
	reader.FieldsPerRecord = -1
	header, err := reader.Read()
	if err != nil {
		if err == io.EOF {
			return nil, nil
		}
		return nil, err
	}
	index := csvHeaderIndex(header)
	rows := []domain.ShareHistoryRecord{}
	for {
		record, err := reader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		row := parseShareHistoryCSVRecord(record, index)
		if row.Symbol == "" || row.ShareDate == "" || row.Shares10K <= 0 {
			continue
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func writeShareHistoryCSV(path string, rows []domain.ShareHistoryRecord) error {
	tmp := path + ".tmp"
	file, err := os.Create(tmp)
	if err != nil {
		return err
	}
	writer := csv.NewWriter(file)
	if err := writer.Write(strings.Split(shareHistoryCSVHeader, ",")); err != nil {
		file.Close()
		_ = os.Remove(tmp)
		return err
	}
	for _, row := range rows {
		if err := writer.Write([]string{
			row.ShareDate,
			row.Symbol,
			row.FundType,
			row.Name,
			strconv.FormatFloat(row.Shares10K, 'f', 4, 64),
			formatNullableFloat(row.ClosePremiumPct, 6),
			row.Source,
			row.UpdatedAt,
		}); err != nil {
			file.Close()
			_ = os.Remove(tmp)
			return err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		file.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := file.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}

func csvHeaderIndex(header []string) map[string]int {
	index := make(map[string]int, len(header))
	for i, name := range header {
		index[strings.TrimSpace(name)] = i
	}
	return index
}

func parseShareHistoryCSVRecord(record []string, index map[string]int) domain.ShareHistoryRecord {
	value := func(name string) string {
		i, ok := index[name]
		if !ok || i < 0 || i >= len(record) {
			return ""
		}
		return strings.TrimSpace(record[i])
	}
	shares, _ := strconv.ParseFloat(strings.ReplaceAll(value("shares_10k"), ",", ""), 64)
	closePremiumPct, _ := strconv.ParseFloat(strings.ReplaceAll(value("close_premium_pct"), ",", ""), 64)
	var closePremiumPtr *float64
	if value("close_premium_pct") != "" {
		closePremiumPtr = &closePremiumPct
	}
	return domain.ShareHistoryRecord{
		ShareDate:       strings.TrimSpace(value("share_date")),
		Symbol:          strings.ToUpper(strings.TrimSpace(value("symbol"))),
		FundType:        strings.ToUpper(strings.TrimSpace(value("fund_type"))),
		Name:            strings.TrimSpace(value("name")),
		Shares10K:       shares,
		ClosePremiumPct: closePremiumPtr,
		Source:          strings.TrimSpace(value("source")),
		UpdatedAt:       strings.TrimSpace(value("updated_at")),
	}
}

func (s *CSVStore) PathForSymbol(symbol string) string {
	return filepath.Join(s.dir, shareHistoryFilename(symbol))
}

func (s *CSVStore) String() string {
	return fmt.Sprintf("share history csv store: %s", s.dir)
}

func (s *CSVStore) BackfillClosePremium(ctx context.Context, symbols []string) (map[string]int, error) {
	if s.closePremiumLookup == nil {
		return map[string]int{}, nil
	}
	targets, err := s.backfillTargetSymbols(symbols)
	if err != nil {
		return nil, err
	}
	updated := make(map[string]int, len(targets))
	for _, symbol := range targets {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}
		rows, err := s.readSymbolRows(symbol)
		if err != nil {
			return nil, err
		}
		before := countRowsWithClosePremium(rows)
		if err := ApplyClosePremium(rows, s.closePremiumLookup); err != nil {
			return nil, err
		}
		after := countRowsWithClosePremium(rows)
		if after == before {
			continue
		}
		if err := writeShareHistoryCSV(filepath.Join(s.dir, shareHistoryFilename(symbol)), rows); err != nil {
			return nil, err
		}
		updated[symbol] = after - before
	}
	return updated, nil
}

func (s *CSVStore) backfillTargetSymbols(symbols []string) ([]string, error) {
	if len(symbols) > 0 {
		out := make([]string, 0, len(symbols))
		seen := map[string]bool{}
		for _, symbol := range symbols {
			symbol = strings.ToUpper(strings.TrimSpace(symbol))
			if symbol == "" || seen[symbol] {
				continue
			}
			seen[symbol] = true
			out = append(out, symbol)
		}
		sort.Strings(out)
		return out, nil
	}
	entries, err := os.ReadDir(s.dir)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(strings.ToLower(entry.Name()), ".csv") {
			continue
		}
		if symbol, ok := shareHistorySymbolFromFilename(entry.Name()); ok {
			out = append(out, symbol)
		}
	}
	sort.Strings(out)
	return out, nil
}

func shareHistorySymbolFromFilename(name string) (string, bool) {
	name = strings.TrimSpace(name)
	if !strings.HasSuffix(strings.ToLower(name), ".csv") {
		return "", false
	}
	base := strings.TrimSuffix(name, filepath.Ext(name))
	if len(base) > 2 {
		suffix := strings.ToUpper(base[len(base)-2:])
		code := strings.ToUpper(base[:len(base)-2])
		if (suffix == "SZ" || suffix == "SH" || suffix == "BJ") && code != "" {
			return suffix + code, true
		}
	}
	return strings.ToUpper(base), true
}

func countRowsWithClosePremium(rows []domain.ShareHistoryRecord) int {
	count := 0
	for _, row := range rows {
		if row.ClosePremiumPct != nil {
			count++
		}
	}
	return count
}

func formatNullableFloat(value *float64, digits int) string {
	if value == nil {
		return ""
	}
	return strconv.FormatFloat(*value, 'f', digits, 64)
}
