package sharesync

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

const (
	defaultEndpoint = "https://www.szse.cn/api/report/ShowReport/data"
	catalogID       = "scsj_fund_jjgm"
	sourceName      = "szse_fund_size"
)

type Client struct {
	httpClient *http.Client
	endpoint   string
	userAgent  string
	maxPages   int
}

type ClientOption func(*Client)

func NewClient(timeout time.Duration, opts ...ClientOption) *Client {
	if timeout <= 0 {
		timeout = 15 * time.Second
	}
	client := &Client{
		httpClient: &http.Client{Timeout: timeout},
		endpoint:   defaultEndpoint,
		userAgent:  "Mozilla/5.0 NewNavNav/1.0",
		maxPages:   100,
	}
	for _, opt := range opts {
		if opt != nil {
			opt(client)
		}
	}
	return client
}

func WithEndpoint(endpoint string) ClientOption {
	return func(client *Client) {
		if strings.TrimSpace(endpoint) != "" {
			client.endpoint = strings.TrimSpace(endpoint)
		}
	}
}

func WithHTTPClient(httpClient *http.Client) ClientOption {
	return func(client *Client) {
		if httpClient != nil {
			client.httpClient = httpClient
		}
	}
}

func (c *Client) FetchFundSizes(ctx context.Context, startDate string, endDate string, fundType string, symbol string) ([]domain.ShareHistoryRecord, error) {
	startDate = strings.TrimSpace(startDate)
	endDate = strings.TrimSpace(endDate)
	fundType = normalizeFundType(fundType)
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if startDate == "" {
		return nil, fmt.Errorf("share start date is required")
	}
	if endDate == "" {
		endDate = startDate
	}
	if fundType == "" {
		return nil, fmt.Errorf("fund type must be ETF or LOF")
	}
	code := strings.TrimSpace(symbol)
	if strings.HasPrefix(code, "SZ") {
		code = strings.TrimPrefix(code, "SZ")
	}
	if code == "" {
		return nil, fmt.Errorf("fund symbol is required")
	}

	var out []domain.ShareHistoryRecord
	for page := 1; page <= c.maxPages; page++ {
		report, err := c.fetchPage(ctx, startDate, endDate, fundType, code, page)
		if err != nil {
			return nil, err
		}
		if report.Error != "" {
			return nil, fmt.Errorf("szse report error: %s", report.Error)
		}
		for _, item := range report.Data {
			record, ok := item.toRecord(fundType)
			if ok {
				out = append(out, record)
			}
		}
		pageCount := report.Metadata.PageCount
		if pageCount <= 0 || page >= pageCount {
			break
		}
	}
	return out, nil
}

func (c *Client) fetchPage(ctx context.Context, startDate string, endDate string, fundType string, code string, page int) (reportPayload, error) {
	endpoint, err := url.Parse(c.endpoint)
	if err != nil {
		return reportPayload{}, err
	}
	query := endpoint.Query()
	query.Set("SHOWTYPE", "JSON")
	query.Set("CATALOGID", catalogID)
	query.Set("jjlb", fundType)
	query.Set("txtStart", startDate)
	query.Set("txtEnd", endDate)
	query.Set("txtDm", code)
	query.Set("PAGENO", strconv.Itoa(page))
	query.Set("random", strconv.FormatInt(time.Now().UnixNano(), 10))
	endpoint.RawQuery = query.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return reportPayload{}, err
	}
	request.Header.Set("Accept", "application/json, text/plain, */*")
	request.Header.Set("User-Agent", c.userAgent)
	request.Header.Set("Referer", refererForFundType(fundType))

	response, err := c.httpClient.Do(request)
	if err != nil {
		return reportPayload{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 512))
		return reportPayload{}, fmt.Errorf("szse report http %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	var envelope []reportPayload
	if err := json.NewDecoder(response.Body).Decode(&envelope); err != nil {
		return reportPayload{}, err
	}
	if len(envelope) == 0 {
		return reportPayload{}, fmt.Errorf("szse report response is empty")
	}
	return envelope[0], nil
}

func refererForFundType(fundType string) string {
	switch normalizeFundType(fundType) {
	case "LOF":
		return "https://www.szse.cn/market/fund/volume/lof/index.html"
	default:
		return "https://www.szse.cn/market/fund/volume/etf/index.html"
	}
}

func normalizeFundType(value string) string {
	switch strings.ToUpper(strings.TrimSpace(value)) {
	case "ETF":
		return "ETF"
	case "LOF":
		return "LOF"
	default:
		return ""
	}
}

type reportPayload struct {
	Metadata reportMetadata `json:"metadata"`
	Data     []reportRow    `json:"data"`
	Error    string         `json:"error"`
}

type reportMetadata struct {
	PageNo      int `json:"pageno"`
	PageCount   int `json:"pagecount"`
	RecordCount int `json:"recordcount"`
}

type reportRow struct {
	SizeDate          string `json:"size_date"`
	FundCode          string `json:"fund_code"`
	SecurityShortName string `json:"security_short_name"`
	CurrentSize       string `json:"current_size"`
}

func (row reportRow) toRecord(fundType string) (domain.ShareHistoryRecord, bool) {
	code := strings.TrimSpace(row.FundCode)
	date := strings.TrimSpace(row.SizeDate)
	shares, ok := parseShareDecimal(row.CurrentSize)
	if code == "" || date == "" || !ok || shares <= 0 {
		return domain.ShareHistoryRecord{}, false
	}
	return domain.ShareHistoryRecord{
		Symbol:    "SZ" + code,
		Name:      strings.TrimSpace(row.SecurityShortName),
		FundType:  fundType,
		ShareDate: date,
		Shares10K: shares,
		Source:    sourceName,
	}, true
}

func parseShareDecimal(value string) (float64, bool) {
	clean := strings.ReplaceAll(strings.TrimSpace(value), ",", "")
	if clean == "" || clean == "-" || clean == "--" {
		return 0, false
	}
	parsed, err := strconv.ParseFloat(clean, 64)
	if err != nil {
		return 0, false
	}
	return parsed, true
}
