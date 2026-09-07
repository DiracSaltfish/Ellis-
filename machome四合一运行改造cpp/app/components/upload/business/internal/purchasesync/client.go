package purchasesync

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
	defaultEndpoint = "https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx"
	sourceName      = "eastmoney_purchase_status"
)

type Client struct {
	httpClient   *http.Client
	endpoint     string
	userAgent    string
	pageSize     int
	pageInterval time.Duration
}

type ClientOption func(*Client)

func NewClient(timeout time.Duration, opts ...ClientOption) *Client {
	if timeout <= 0 {
		timeout = 15 * time.Second
	}
	client := &Client{
		httpClient:   &http.Client{Timeout: timeout},
		endpoint:     defaultEndpoint,
		userAgent:    "Mozilla/5.0 NewNavNav/1.0",
		pageSize:     30000,
		pageInterval: 10 * time.Second,
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

func WithPageSize(pageSize int) ClientOption {
	return func(client *Client) {
		if pageSize > 0 {
			client.pageSize = pageSize
		}
	}
}

func WithPageInterval(interval time.Duration) ClientOption {
	return func(client *Client) {
		client.SetPageInterval(interval)
	}
}

func (c *Client) SetPageInterval(interval time.Duration) {
	if interval > 0 {
		c.pageInterval = interval
	}
}

func (c *Client) FetchPurchaseInfos(ctx context.Context, symbols []string) (map[string]domain.PurchaseInfo, error) {
	wanted := wantedFundCodes(symbols)
	if len(wanted) == 0 {
		return map[string]domain.PurchaseInfo{}, nil
	}
	out := make(map[string]domain.PurchaseInfo, len(wanted))
	totalPages := 1
	var firstErr error
	for page := 1; page <= totalPages; page++ {
		if page > 1 {
			if err := waitForInterval(ctx, c.pageInterval); err != nil {
				return out, err
			}
		}
		payload, err := c.fetchPage(ctx, page)
		if err != nil {
			if firstErr == nil {
				firstErr = err
			}
			break
		}
		if payload.Pages > totalPages {
			totalPages = payload.Pages
		}
		fetchedAt := time.Now()
		for _, row := range payload.Rows {
			if len(row) < 12 {
				continue
			}
			code := strings.TrimSpace(row[0])
			symbol, ok := wanted[code]
			if !ok {
				continue
			}
			out[symbol] = purchaseInfoFromBatchRow(symbol, row, fetchedAt)
		}
		if len(out) == len(wanted) {
			break
		}
	}
	if len(out) == 0 {
		if firstErr != nil {
			return out, firstErr
		}
		return out, fmt.Errorf("eastmoney purchase status batch matched no tracked symbols")
	}
	if len(out) < len(wanted) && firstErr == nil {
		firstErr = fmt.Errorf("eastmoney purchase status batch missing %d of %d tracked symbols", len(wanted)-len(out), len(wanted))
	}
	return out, firstErr
}

type batchPage struct {
	Rows    [][]string
	Record  int
	Pages   int
	CurPage int
}

func parseBatchPage(body []byte) (batchPage, error) {
	text := string(body)
	datasValue, err := extractJSValue(text, "datas")
	if err != nil {
		return batchPage{}, err
	}
	var rows [][]string
	if err := json.Unmarshal([]byte(datasValue), &rows); err != nil {
		return batchPage{}, err
	}
	page := batchPage{
		Rows:    rows,
		Record:  parseJSStringInt(text, "record"),
		Pages:   parseJSStringInt(text, "pages"),
		CurPage: parseJSStringInt(text, "curpage"),
	}
	if page.Pages <= 0 {
		page.Pages = 1
	}
	return page, nil
}

func extractJSValue(text string, key string) (string, error) {
	needle := key + ":"
	start := strings.Index(text, needle)
	if start < 0 {
		return "", fmt.Errorf("eastmoney purchase status missing %s", key)
	}
	start += len(needle)
	for start < len(text) && (text[start] == ' ' || text[start] == '\n' || text[start] == '\r' || text[start] == '\t') {
		start++
	}
	if start >= len(text) || text[start] != '[' {
		return "", fmt.Errorf("eastmoney purchase status %s is not an array", key)
	}
	depth := 0
	inString := false
	escaped := false
	for idx := start; idx < len(text); idx++ {
		ch := text[idx]
		if inString {
			if escaped {
				escaped = false
				continue
			}
			if ch == '\\' {
				escaped = true
				continue
			}
			if ch == '"' {
				inString = false
			}
			continue
		}
		switch ch {
		case '"':
			inString = true
		case '[':
			depth++
		case ']':
			depth--
			if depth == 0 {
				return text[start : idx+1], nil
			}
		}
	}
	return "", fmt.Errorf("eastmoney purchase status unterminated %s array", key)
}

func parseJSStringInt(text string, key string) int {
	needle := key + ":\""
	start := strings.Index(text, needle)
	if start < 0 {
		return 0
	}
	start += len(needle)
	end := strings.IndexByte(text[start:], '"')
	if end < 0 {
		return 0
	}
	value, err := strconv.Atoi(text[start : start+end])
	if err != nil {
		return 0
	}
	return value
}

func wantedFundCodes(symbols []string) map[string]string {
	out := make(map[string]string, len(symbols))
	for _, symbol := range symbols {
		symbol = strings.ToUpper(strings.TrimSpace(symbol))
		code := fundCodeFromSymbol(symbol)
		if code == "" {
			continue
		}
		out[code] = symbol
	}
	return out
}

func purchaseInfoFromBatchRow(symbol string, row []string, fetchedAt time.Time) domain.PurchaseInfo {
	status := strings.TrimSpace(row[5])
	rawLimit := strings.TrimSpace(row[9])
	limit, _ := parseDailyLimit(rawLimit)
	isPurchasable := isPurchasableStatus(status)
	if !isPurchasable {
		zero := 0.0
		limit = &zero
	}
	return domain.PurchaseInfo{
		Symbol:         symbol,
		FundCode:       strings.TrimSpace(row[0]),
		Status:         status,
		DailyLimitYuan: limit,
		RawLimit:       rawLimit,
		IsPurchasable:  isPurchasable,
		Source:         sourceName,
		FetchedAt:      fetchedAt,
	}
}

func (c *Client) fetchPage(ctx context.Context, page int) (batchPage, error) {
	endpoint, err := url.Parse(c.endpoint)
	if err != nil {
		return batchPage{}, err
	}
	query := endpoint.Query()
	query.Set("t", "8")
	query.Set("page", fmt.Sprintf("%d,%d", page, c.pageSize))
	query.Set("js", "reData")
	query.Set("sort", "fcode,asc")
	endpoint.RawQuery = query.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return batchPage{}, err
	}
	request.Header.Set("Accept", "text/javascript, application/javascript, */*")
	request.Header.Set("User-Agent", c.userAgent)
	request.Header.Set("Referer", "https://fund.eastmoney.com/Fund_sgzt_bzdm.html")

	response, err := c.httpClient.Do(request)
	if err != nil {
		return batchPage{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 512))
		return batchPage{}, fmt.Errorf("eastmoney purchase status http %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 16*1024*1024))
	if err != nil {
		return batchPage{}, err
	}
	return parseBatchPage(body)
}

func fundCodeFromSymbol(symbol string) string {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	symbol = strings.TrimPrefix(symbol, "SH")
	symbol = strings.TrimPrefix(symbol, "SZ")
	if len(symbol) != 6 {
		return ""
	}
	for _, ch := range symbol {
		if ch < '0' || ch > '9' {
			return ""
		}
	}
	return symbol
}

func parseDailyLimit(raw string) (*float64, bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" || raw == "--" || raw == "---" {
		return nil, false
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return nil, false
	}
	return &value, true
}

func isPurchasableStatus(status string) bool {
	status = strings.TrimSpace(status)
	if status == "" || status == "--" || status == "---" {
		return false
	}
	for _, blocked := range []string{"暂停", "停止", "封闭", "终止", "失败", "场内交易", "禁止"} {
		if strings.Contains(status, blocked) {
			return false
		}
	}
	return strings.Contains(status, "申购") || strings.Contains(status, "限大额")
}

func waitForInterval(ctx context.Context, interval time.Duration) error {
	if interval <= 0 {
		return nil
	}
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
