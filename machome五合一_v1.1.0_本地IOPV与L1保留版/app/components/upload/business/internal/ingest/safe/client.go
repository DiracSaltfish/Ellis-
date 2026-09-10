package safe

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/extrame/xls"
	"newnavnav/internal/domain"
)

const (
	baseURL   = "https://www.safe.gov.cn"
	exportURL = baseURL + "/AppStructured/hlw/exportRMBExcel.do"
)

type Client struct {
	http *http.Client
}

func NewClient(timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	return &Client{
		http: &http.Client{Timeout: timeout},
	}
}

type currencyColumn struct {
	Headers []string
	Pair    string
	Divisor float64
}

var safeColumns = []currencyColumn{
	{Headers: []string{"美元"}, Pair: "USDCNY", Divisor: 100},
	{Headers: []string{"港元", "港币"}, Pair: "HKDCNY", Divisor: 100},
	{Headers: []string{"日元"}, Pair: "JPYCNY", Divisor: 100},
}

func (c *Client) FetchRange(ctx context.Context, start time.Time, end time.Time) ([]domain.FXCentralParity, error) {
	if end.Before(start) {
		return nil, fmt.Errorf("end before start")
	}
	startDay := atDate(start)
	endDay := atDate(end)
	out := make([]domain.FXCentralParity, 0)
	for chunkStart := startDay; !chunkStart.After(endDay); chunkStart = chunkStart.AddDate(0, 0, 367) {
		chunkEnd := chunkStart.AddDate(0, 0, 366)
		if chunkEnd.After(endDay) {
			chunkEnd = endDay
		}
		rows, err := c.fetchChunk(ctx, chunkStart, chunkEnd)
		if err != nil {
			return nil, err
		}
		out = append(out, rows...)
	}
	return out, nil
}

func (c *Client) fetchChunk(ctx context.Context, start time.Time, end time.Time) ([]domain.FXCentralParity, error) {
	form := url.Values{
		"startDate": []string{start.Format("2006-01-02")},
		"endDate":   []string{end.Format("2006-01-02")},
		"queryYN":   []string{"true"},
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, exportURL, strings.NewReader(form.Encode()))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("User-Agent", "Mozilla/5.0")
	req.Header.Set("Referer", baseURL+"/AppStructured/hlw/RMBQuery.do")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("safe export status %d", resp.StatusCode)
	}
	contentType := strings.ToLower(resp.Header.Get("Content-Type"))
	if !strings.Contains(contentType, "application/x-download") {
		return nil, fmt.Errorf("unexpected safe content-type %q", contentType)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if len(body) < 8 || !bytes.HasPrefix(body, []byte{0xD0, 0xCF, 0x11, 0xE0}) {
		return nil, fmt.Errorf("safe response is not xls")
	}
	workbook, err := xls.OpenReader(bytes.NewReader(body), "utf-8")
	if err != nil {
		return nil, err
	}
	if workbook.NumSheets() == 0 {
		return nil, fmt.Errorf("safe workbook has no sheets")
	}
	sheet := workbook.GetSheet(0)
	if sheet == nil {
		return nil, fmt.Errorf("safe workbook missing first sheet")
	}
	headerRow := sheet.Row(0)
	if headerRow == nil {
		return nil, fmt.Errorf("safe workbook missing header row")
	}
	headerToIndex := map[string]int{}
	for i := 0; i < headerRow.LastCol(); i++ {
		header := strings.TrimSpace(headerRow.Col(i))
		if header != "" {
			headerToIndex[header] = i
		}
	}
	dateIndex, ok := headerToIndex["日期"]
	if !ok {
		return nil, fmt.Errorf("safe workbook missing 日期 column")
	}

	rates := make([]domain.FXCentralParity, 0, int(sheet.MaxRow))
	for rowIdx := 1; rowIdx <= int(sheet.MaxRow); rowIdx++ {
		row := sheet.Row(rowIdx)
		if row == nil {
			continue
		}
		dateText := strings.TrimSpace(row.Col(dateIndex))
		if dateText == "" {
			continue
		}
		rateDate, err := parseSAFETextDate(dateText)
		if err != nil {
			continue
		}
		for _, spec := range safeColumns {
			colIndex, ok := safeHeaderIndex(headerToIndex, spec.Headers)
			if !ok {
				continue
			}
			raw := strings.TrimSpace(row.Col(colIndex))
			if raw == "" {
				continue
			}
			value, err := parseSAFEFloat(raw)
			if err != nil || value <= 0 || spec.Divisor <= 0 {
				continue
			}
			rates = append(rates, domain.FXCentralParity{
				Pair:   spec.Pair,
				Date:   rateDate.Format("2006-01-02"),
				Rate:   value / spec.Divisor,
				Source: "safe",
			})
		}
	}
	return rates, nil
}

func atDate(value time.Time) time.Time {
	year, month, day := value.Date()
	return time.Date(year, month, day, 0, 0, 0, 0, time.Local)
}

func parseSAFETextDate(value string) (time.Time, error) {
	value = strings.TrimSpace(value)
	layouts := []string{
		"2006.01.02",
		"2006-01-02",
		"2006/01/02",
		"2006-1-2",
		"2006/1/2",
	}
	for _, layout := range layouts {
		if parsed, err := time.ParseInLocation(layout, value, time.Local); err == nil {
			return parsed, nil
		}
	}
	return time.Time{}, fmt.Errorf("unsupported safe date %q", value)
}

func parseSAFEFloat(value string) (float64, error) {
	value = strings.ReplaceAll(strings.TrimSpace(value), ",", "")
	return strconv.ParseFloat(value, 64)
}

func safeHeaderIndex(headerToIndex map[string]int, headers []string) (int, bool) {
	for _, header := range headers {
		header = strings.TrimSpace(header)
		if header == "" {
			continue
		}
		if index, ok := headerToIndex[header]; ok {
			return index, true
		}
	}
	return 0, false
}
