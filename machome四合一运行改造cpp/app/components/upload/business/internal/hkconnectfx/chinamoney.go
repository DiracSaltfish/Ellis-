package hkconnectfx

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	cfetsAnchorSource        = "CFETS_CHINAMONEY_REFERENCE_1600"
	cfetsCentralParitySource = "CFETS_CHINAMONEY_CENTRAL_PARITY"
	cfetsCentralParityPair   = "HKD/CNY"
)

type chinaMoneySpotPayload struct {
	Head struct {
		RepCode string `json:"rep_code"`
		TS      int64  `json:"ts"`
	} `json:"head"`
	Data struct {
		ShowDateCN string `json:"showDateCN"`
	} `json:"data"`
	Records []struct {
		Pair string `json:"ccyPair"`
		Bid  string `json:"bidPrc"`
		Ask  string `json:"askPrc"`
	} `json:"records"`
}

type chinaMoneyHistoryPayload struct {
	Head struct {
		RepCode string `json:"rep_code"`
	} `json:"head"`
	Data struct {
		Message string `json:"message"`
	} `json:"data"`
	Records []struct {
		Pair       string `json:"ccyPair"`
		TradeDate  string `json:"dealDate"`
		RateAt1600 string `json:"rateOf16hour"`
	} `json:"records"`
}

type chinaMoneyCentralParityPayload struct {
	Head struct {
		RepCode string `json:"rep_code"`
	} `json:"head"`
	Data struct {
		Currency    string `json:"currency"`
		FlagMessage string `json:"flagMessage"`
		PageTotal   int    `json:"pageTotal"`
	} `json:"data"`
	Records []struct {
		TradeDate string   `json:"date"`
		Values    []string `json:"values"`
	} `json:"records"`
}

func (s *Service) pollCFETSSpot(ctx context.Context, now time.Time) {
	quotes, err := s.fetchCFETSSpot(ctx, now)
	if err != nil {
		s.logger.Warn("hk-connect FX CFETS quote unavailable", "error", err)
	}
	s.mu.Lock()
	if s.fxQuotes == nil {
		s.fxQuotes = initialCFETSSpotQuotes()
	}
	if s.fxQuotes["HKD/CNY"].Bid == nil && s.fx.Bid != nil {
		s.fxQuotes["HKD/CNY"] = cloneFX(s.fx)
	}
	if s.lastHealthyFX == nil {
		s.lastHealthyFX = make(map[string]FXQuote)
	}
	for pair, previous := range s.fxQuotes {
		incoming := quotes[pair]
		if err == nil && incoming.Healthy {
			// An upstream rollback must not replace a newer known observation.
			last := s.lastHealthyFX[pair]
			if last.ObservedAt == nil || !incoming.ObservedAt.Before(*last.ObservedAt) {
				s.fxQuotes[pair] = incoming
				s.lastHealthyFX[pair] = cloneFX(incoming)
				continue
			}
			incoming.Error = "CFETS source timestamp regressed"
		}
		previous.Healthy = false
		// ReceivedAt belongs to the retained valid value, never to a failed poll.
		previous.Error = incoming.Error
		if err != nil {
			previous.Error = err.Error()
		}
		s.fxQuotes[pair] = previous
	}
	s.fx = s.fxQuotes["HKD/CNY"]
	hkd := cloneFX(s.fx)
	retained := cloneFXQuotes(s.lastHealthyFX)
	s.mu.Unlock()
	if err := s.persistLastHealthyFX(retained); err != nil {
		s.logger.Warn("CFETS last healthy persistence failed", "error", err)
	}

	local := now.In(shanghai)
	if local.Hour() == 16 && local.Minute() == 0 && hkd.Healthy && hkd.Bid != nil && hkd.Ask != nil && hkd.ObservedAt != nil {
		age := local.Sub(*hkd.ObservedAt)
		if age >= -30*time.Second && age <= cfetsQuoteFreshness {
			anchor := CFETSAnchor{
				TradeDate:  local.Format("2006-01-02"),
				HKDCNY1600: (*hkd.Bid + *hkd.Ask) / 2,
				FetchedAt:  local,
				Source:     "CFETS_CHINAMONEY_LIVE_FREEZE",
			}
			if err := s.storeCFETSAnchor(anchor); err != nil {
				s.logger.Warn("hk-connect FX CFETS 16:00 freeze failed", "error", err)
			}
		}
	}
}

func (s *Service) fetchCFETSSpot(ctx context.Context, now time.Time) (map[string]FXQuote, error) {
	target := ChinaMoneySpotFeed + "?t=" + strconv.FormatInt(now.UnixMilli(), 10)
	var payload chinaMoneySpotPayload
	if err := s.getJSON(ctx, target, ChinaMoneySpotPage, &payload); err != nil {
		return nil, err
	}
	if payload.Head.RepCode != "200" {
		return nil, fmt.Errorf("ChinaMoney rep_code=%q", payload.Head.RepCode)
	}
	quotes := initialCFETSSpotQuotes()
	seen := map[string]bool{}
	for _, row := range payload.Records {
		pair := strings.ToUpper(strings.TrimSpace(row.Pair))
		quote, ok := quotes[pair]
		if !ok {
			continue
		}
		if seen[pair] {
			quote.Healthy = false
			quote.Bid, quote.Ask = nil, nil
			quote.Error = "duplicate CFETS pair"
			quotes[pair] = quote
			continue
		}
		seen[pair] = true
		bid, bidErr := parseCFETSSpotRate(pair, row.Bid)
		ask, askErr := parseCFETSSpotRate(pair, row.Ask)
		if bidErr != nil || askErr != nil || ask < bid {
			quote.Error = "invalid CFETS BID/ASK"
		} else {
			quote.Bid, quote.Ask, quote.Healthy, quote.Error = floatPointer(bid), floatPointer(ask), true, ""
		}
		quotes[pair] = quote
	}
	observed, err := time.ParseInLocation("2006-01-02 15:04:05", strings.TrimSpace(payload.Data.ShowDateCN), shanghai)
	// head.ts is response metadata; it must not manufacture a quote observation.
	if err != nil || observed.IsZero() {
		return nil, fmt.Errorf("invalid ChinaMoney source timestamp %q", payload.Data.ShowDateCN)
	}
	local := now.In(shanghai)
	if observed.After(local.Add(30 * time.Second)) {
		return nil, fmt.Errorf("ChinaMoney source timestamp is in the future")
	}
	for pair, quote := range quotes {
		quote.ReceivedAt = cloneTime(&local)
		quote.Source = "CFETS_CHINAMONEY"
		if quote.Healthy {
			quote.ObservedAt = cloneTime(&observed)
			if local.Sub(observed) > cfetsQuoteFreshness || observed.Format("2006-01-02") != local.Format("2006-01-02") {
				quote.Healthy, quote.Error = false, "stale CFETS source observation"
			}
		}
		quotes[pair] = quote
	}
	return quotes, nil
}

func parseCFETSSpotRate(pair, raw string) (float64, error) {
	value, err := strconv.ParseFloat(strings.TrimSpace(raw), 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return 0, fmt.Errorf("invalid CFETS %s rate %q", pair, raw)
	}
	minimum, maximum := 0.0, 0.0
	switch pair {
	case "HKD/CNY":
		minimum, maximum = 0.5, 1.5
	case "USD/CNY":
		minimum, maximum = 4, 10
	case "EUR/CNY":
		minimum, maximum = 4, 15
	case "100JPY/CNY":
		minimum, maximum = 1, 10
	default:
		return 0, fmt.Errorf("unsupported CFETS spot pair %q", pair)
	}
	if value < minimum || value > maximum {
		return 0, fmt.Errorf("invalid CFETS %s rate %q", pair, raw)
	}
	return value, nil
}

func parseCFETSRate(raw string) (float64, error) {
	value, err := strconv.ParseFloat(strings.TrimSpace(raw), 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0.5 || value > 1.5 {
		return 0, fmt.Errorf("invalid CFETS HKD/CNY rate %q", raw)
	}
	return value, nil
}

func parseCentralParityRate(raw string) (float64, error) {
	value, err := strconv.ParseFloat(strings.TrimSpace(raw), 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0.5 || value > 1.5 {
		return 0, fmt.Errorf("invalid CFETS HKD/CNY central parity %q", raw)
	}
	return value, nil
}

func (s *Service) pollCFETSHistory(ctx context.Context, now time.Time) {
	local := now.In(shanghai)
	anchors, err := s.fetchCFETSHistory(ctx, local.AddDate(0, 0, -120), local)
	if err != nil {
		s.logger.Warn("hk-connect FX CFETS history unavailable", "error", err)
		return
	}
	for _, anchor := range anchors {
		if err := s.storeCFETSAnchor(anchor); err != nil {
			s.logger.Warn("hk-connect FX CFETS history persistence failed", "date", anchor.TradeDate, "error", err)
			return
		}
	}
	s.mu.Lock()
	s.lastCFETSHistoryDate = local.Format("2006-01-02")
	s.mu.Unlock()
}

func (s *Service) pollCentralParity(ctx context.Context, now time.Time) {
	local := now.In(shanghai)
	rates, err := s.fetchCentralParity(ctx, local.AddDate(0, 0, -centralParityHistoryDays), local)
	if err != nil {
		s.logger.Warn("hk-connect FX central parity unavailable", "error", err)
		return
	}
	today := local.Format("2006-01-02")
	foundToday := false
	for _, rate := range rates {
		if rate.TradeDate == today {
			foundToday = true
			break
		}
	}
	if !foundToday {
		s.logger.Info("hk-connect FX central parity not published yet", "date", today)
		return
	}
	if err := s.storeCentralParityRates(rates); err != nil {
		s.logger.Warn("hk-connect FX central parity persistence failed", "error", err)
	}
}

func (s *Service) fetchCentralParity(ctx context.Context, start, end time.Time) ([]CentralParityRate, error) {
	start = start.In(shanghai)
	end = end.In(shanghai)
	byDate := make(map[string]CentralParityRate)
	fetchedAt := s.now().In(shanghai)
	pageTotal := 1
	for page := 1; page <= pageTotal; page++ {
		query := url.Values{
			"startDate": {start.Format("2006-01-02")},
			"endDate":   {end.Format("2006-01-02")},
			"currency":  {cfetsCentralParityPair},
			"pageNum":   {strconv.Itoa(page)},
			"pageSize":  {"10"},
		}
		var payload chinaMoneyCentralParityPayload
		if err := s.postJSON(ctx, ChinaMoneyCentralParityFeed+"?"+query.Encode(), ChinaMoneyCentralParityPage, &payload); err != nil {
			return nil, err
		}
		if payload.Head.RepCode != "200" || strings.TrimSpace(payload.Data.FlagMessage) != "" {
			return nil, fmt.Errorf("ChinaMoney central parity rejected: code=%q message=%q", payload.Head.RepCode, payload.Data.FlagMessage)
		}
		if strings.TrimSpace(payload.Data.Currency) != cfetsCentralParityPair {
			return nil, fmt.Errorf("unexpected ChinaMoney central parity pair %q", payload.Data.Currency)
		}
		if payload.Data.PageTotal < 0 || payload.Data.PageTotal > 50 {
			return nil, fmt.Errorf("invalid ChinaMoney central parity page total %d", payload.Data.PageTotal)
		}
		if payload.Data.PageTotal > pageTotal {
			pageTotal = payload.Data.PageTotal
		}
		for _, row := range payload.Records {
			if _, err := time.ParseInLocation("2006-01-02", row.TradeDate, shanghai); err != nil {
				return nil, fmt.Errorf("invalid ChinaMoney central parity date %q", row.TradeDate)
			}
			if len(row.Values) != 1 {
				return nil, fmt.Errorf("unexpected ChinaMoney central parity values for %s", row.TradeDate)
			}
			if _, duplicate := byDate[row.TradeDate]; duplicate {
				return nil, fmt.Errorf("duplicate ChinaMoney central parity date %s", row.TradeDate)
			}
			value, err := parseCentralParityRate(row.Values[0])
			if err != nil {
				return nil, err
			}
			byDate[row.TradeDate] = CentralParityRate{
				Pair: cfetsCentralParityPair, TradeDate: row.TradeDate, Rate: value,
				FetchedAt: fetchedAt, Source: cfetsCentralParitySource,
			}
		}
	}
	rates := make([]CentralParityRate, 0, len(byDate))
	for _, rate := range byDate {
		rates = append(rates, rate)
	}
	sort.Slice(rates, func(i, j int) bool { return rates[i].TradeDate < rates[j].TradeDate })
	return rates, nil
}

func (s *Service) fetchCFETSHistory(ctx context.Context, start, end time.Time) ([]CFETSAnchor, error) {
	start = start.In(shanghai)
	end = end.In(shanghai)
	byDate := make(map[string]CFETSAnchor)
	for cursor := time.Date(start.Year(), start.Month(), 1, 0, 0, 0, 0, shanghai); !cursor.After(end); cursor = cursor.AddDate(0, 1, 0) {
		monthStart := cursor
		if monthStart.Before(start) {
			monthStart = start
		}
		monthEnd := cursor.AddDate(0, 1, -1)
		if monthEnd.After(end) {
			monthEnd = end
		}
		query := url.Values{
			"lang": {"cn"}, "startDateTool": {monthStart.Format("02 Jan 2006")},
			"endDateTool": {monthEnd.Format("02 Jan 2006")}, "currencyCode": {"HKD.CNY"},
		}
		var payload chinaMoneyHistoryPayload
		if err := s.postJSON(ctx, ChinaMoneyHistoryFeed+"?"+query.Encode(), ChinaMoneyHistoryPage, &payload); err != nil {
			return nil, err
		}
		if payload.Head.RepCode != "200" || strings.TrimSpace(payload.Data.Message) != "" {
			return nil, fmt.Errorf("ChinaMoney history rejected: code=%q message=%q", payload.Head.RepCode, payload.Data.Message)
		}
		for _, row := range payload.Records {
			if strings.TrimSpace(row.Pair) != "HKD/CNY" {
				return nil, fmt.Errorf("unexpected ChinaMoney history pair %q", row.Pair)
			}
			if _, err := time.ParseInLocation("2006-01-02", row.TradeDate, shanghai); err != nil {
				return nil, fmt.Errorf("invalid ChinaMoney history date %q", row.TradeDate)
			}
			value, err := parseCFETSRate(row.RateAt1600)
			if err != nil {
				if strings.TrimSpace(row.RateAt1600) == "" || row.RateAt1600 == "---" || row.RateAt1600 == "/" {
					continue
				}
				return nil, err
			}
			byDate[row.TradeDate] = CFETSAnchor{TradeDate: row.TradeDate, HKDCNY1600: value, FetchedAt: s.now().In(shanghai), Source: cfetsAnchorSource}
		}
	}
	rows := make([]CFETSAnchor, 0, len(byDate))
	for _, row := range byDate {
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].TradeDate < rows[j].TradeDate })
	return rows, nil
}

func (s *Service) postJSON(ctx context.Context, target, referer string, destination any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, target, http.NoBody)
	if err != nil {
		return err
	}
	request.Header.Set("Accept", "application/json, text/plain, */*")
	request.Header.Set("Referer", referer)
	request.Header.Set("User-Agent", "Mozilla/5.0 (compatible; NewNavNav-HKConnectFX/1.0)")
	response, err := s.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", response.StatusCode)
	}
	return json.NewDecoder(io.LimitReader(response.Body, 4<<20)).Decode(destination)
}

var cfetsAnchorFields = []string{"trade_date", "hkd_cny_1600", "fetched_at", "source"}

func (s *Service) storeCFETSAnchor(anchor CFETSAnchor) error {
	if _, err := time.ParseInLocation("2006-01-02", anchor.TradeDate, shanghai); err != nil || !validRate(anchor.HKDCNY1600) {
		return fmt.Errorf("invalid CFETS anchor")
	}
	path := filepath.Join(s.dataDir, "cfets_1600", anchor.TradeDate[:4]+".csv")
	rows, err := readCFETSAnchorCSV(path)
	if errors.Is(err, os.ErrNotExist) {
		rows = []CFETSAnchor{}
	} else if err != nil {
		return err
	}
	updated := false
	for index := range rows {
		if rows[index].TradeDate == anchor.TradeDate {
			if rows[index].Source == cfetsAnchorSource && anchor.Source != cfetsAnchorSource {
				anchor = rows[index]
			}
			rows[index] = anchor
			updated = true
			break
		}
	}
	if !updated {
		rows = append(rows, anchor)
	}
	if err := writeCFETSAnchorCSV(path, rows); err != nil {
		return err
	}
	s.mu.Lock()
	s.cfetsAnchors[anchor.TradeDate] = anchor
	s.mu.Unlock()
	return nil
}

func loadCFETSAnchors(directory string) (map[string]CFETSAnchor, error) {
	anchors := make(map[string]CFETSAnchor)
	entries, err := os.ReadDir(directory)
	if errors.Is(err, os.ErrNotExist) {
		return anchors, nil
	}
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(strings.ToLower(entry.Name()), ".csv") {
			continue
		}
		rows, err := readCFETSAnchorCSV(filepath.Join(directory, entry.Name()))
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			anchors[row.TradeDate] = row
		}
	}
	return anchors, nil
}

func readCFETSAnchorCSV(path string) ([]CFETSAnchor, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	reader := csv.NewReader(file)
	header, err := reader.Read()
	if err != nil {
		return nil, err
	}
	if strings.Join(header, "\x00") != strings.Join(cfetsAnchorFields, "\x00") {
		return nil, fmt.Errorf("unexpected CFETS anchor CSV header")
	}
	rows := []CFETSAnchor{}
	for {
		record, err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil || len(record) != len(cfetsAnchorFields) {
			return nil, fmt.Errorf("invalid CFETS anchor CSV row")
		}
		value, valueErr := parseCFETSRate(record[1])
		fetchedAt, timeErr := time.Parse(time.RFC3339Nano, record[2])
		if valueErr != nil || timeErr != nil {
			return nil, fmt.Errorf("invalid CFETS anchor CSV value")
		}
		rows = append(rows, CFETSAnchor{TradeDate: record[0], HKDCNY1600: value, FetchedAt: fetchedAt, Source: record[3]})
	}
	return rows, nil
}

func writeCFETSAnchorCSV(path string, rows []CFETSAnchor) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].TradeDate < rows[j].TradeDate })
	temporary, err := os.CreateTemp(filepath.Dir(path), ".cfets-anchor-*.csv")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	writer := csv.NewWriter(temporary)
	if err := writer.Write(cfetsAnchorFields); err != nil {
		temporary.Close()
		return err
	}
	for _, row := range rows {
		if err := writer.Write([]string{row.TradeDate, strconv.FormatFloat(row.HKDCNY1600, 'f', 8, 64), row.FetchedAt.Format(time.RFC3339Nano), row.Source}); err != nil {
			temporary.Close()
			return err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}

var centralParityFields = []string{"trade_date", "pair", "rate", "fetched_at", "source"}

func (s *Service) storeCentralParityRates(rates []CentralParityRate) error {
	path := filepath.Join(s.dataDir, "cfets_1600", "central_parity", "hkd_cny.csv")
	existing, err := loadCentralParityRates(path)
	if errors.Is(err, os.ErrNotExist) {
		existing = make(map[string]CentralParityRate)
	} else if err != nil {
		return err
	}
	for _, rate := range rates {
		if rate.Pair != cfetsCentralParityPair {
			return fmt.Errorf("invalid central parity pair %q", rate.Pair)
		}
		if _, err := time.ParseInLocation("2006-01-02", rate.TradeDate, shanghai); err != nil || !validRate(rate.Rate) {
			return fmt.Errorf("invalid central parity rate")
		}
		existing[rate.TradeDate] = rate
	}
	merged := make([]CentralParityRate, 0, len(existing))
	for _, rate := range existing {
		merged = append(merged, rate)
	}
	if err := writeCentralParityCSV(path, merged); err != nil {
		return err
	}
	s.mu.Lock()
	for _, rate := range rates {
		s.centralParities[rate.TradeDate] = rate
	}
	s.mu.Unlock()
	return nil
}

func loadCentralParityRates(path string) (map[string]CentralParityRate, error) {
	rates := make(map[string]CentralParityRate)
	file, err := os.Open(path)
	if err != nil {
		return rates, err
	}
	defer file.Close()
	reader := csv.NewReader(file)
	header, err := reader.Read()
	if err != nil {
		return nil, err
	}
	if strings.Join(header, "\x00") != strings.Join(centralParityFields, "\x00") {
		return nil, fmt.Errorf("unexpected central parity CSV header")
	}
	for {
		record, err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil || len(record) != len(centralParityFields) {
			return nil, fmt.Errorf("invalid central parity CSV row")
		}
		if _, err := time.ParseInLocation("2006-01-02", record[0], shanghai); err != nil || record[1] != cfetsCentralParityPair {
			return nil, fmt.Errorf("invalid central parity CSV identity")
		}
		value, valueErr := parseCentralParityRate(record[2])
		fetchedAt, timeErr := time.Parse(time.RFC3339Nano, record[3])
		if valueErr != nil || timeErr != nil {
			return nil, fmt.Errorf("invalid central parity CSV value")
		}
		rates[record[0]] = CentralParityRate{
			Pair: record[1], TradeDate: record[0], Rate: value,
			FetchedAt: fetchedAt, Source: record[4],
		}
	}
	return rates, nil
}

func writeCentralParityCSV(path string, rates []CentralParityRate) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	sort.Slice(rates, func(i, j int) bool { return rates[i].TradeDate < rates[j].TradeDate })
	temporary, err := os.CreateTemp(filepath.Dir(path), ".central-parity-*.csv")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	writer := csv.NewWriter(temporary)
	if err := writer.Write(centralParityFields); err != nil {
		temporary.Close()
		return err
	}
	for _, rate := range rates {
		if err := writer.Write([]string{
			rate.TradeDate, rate.Pair, strconv.FormatFloat(rate.Rate, 'f', 8, 64),
			rate.FetchedAt.Format(time.RFC3339Nano), rate.Source,
		}); err != nil {
			temporary.Close()
			return err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}
