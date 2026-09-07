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
	defaultSSEEndpoint = "https://query.sse.com.cn/commonQuery.do"
	sseETFSQLID        = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
	sseLOFSQLID        = "COMMON_SSE_SJ_JJSJ_JJGM_LOFGMTJ_L"
	ssePageSize        = "2000"
)

type SSEClient struct {
	httpClient   *http.Client
	endpoint     string
	userAgent    string
	maxPages     int
	pageInterval time.Duration
}

type SSEClientOption func(*SSEClient)

func NewSSEClient(timeout time.Duration, opts ...SSEClientOption) *SSEClient {
	if timeout <= 0 {
		timeout = 20 * time.Second
	}
	client := &SSEClient{
		httpClient:   &http.Client{Timeout: timeout},
		endpoint:     defaultSSEEndpoint,
		userAgent:    "Mozilla/5.0 NewNavNav/1.0",
		maxPages:     100,
		pageInterval: 200 * time.Millisecond,
	}
	for _, opt := range opts {
		if opt != nil {
			opt(client)
		}
	}
	return client
}

func WithSSEEndpoint(endpoint string) SSEClientOption {
	return func(client *SSEClient) {
		if strings.TrimSpace(endpoint) != "" {
			client.endpoint = strings.TrimSpace(endpoint)
		}
	}
}

func WithSSEHTTPClient(httpClient *http.Client) SSEClientOption {
	return func(client *SSEClient) {
		if httpClient != nil {
			client.httpClient = httpClient
		}
	}
}

func (c *SSEClient) FetchFundSizesByDate(ctx context.Context, date string, fundType string) ([]domain.ShareHistoryRecord, error) {
	date = strings.TrimSpace(date)
	fundType = normalizeFundType(fundType)
	if date == "" {
		return nil, fmt.Errorf("share date is required")
	}
	if fundType == "" {
		return nil, fmt.Errorf("fund type must be ETF or LOF")
	}

	var out []domain.ShareHistoryRecord
	for page := 1; page <= c.maxPages; page++ {
		if page > 1 {
			if err := waitForInterval(ctx, c.pageInterval); err != nil {
				return nil, err
			}
		}
		report, err := c.fetchPage(ctx, date, fundType, page)
		if err != nil {
			return nil, err
		}
		for _, item := range report.Result {
			record, ok := item.toShareRecord(fundType)
			if ok {
				out = append(out, record)
			}
		}
		pageCount := report.PageHelp.PageCount
		if pageCount <= 0 || page >= pageCount {
			break
		}
	}
	return out, nil
}

func (c *SSEClient) fetchPage(ctx context.Context, date string, fundType string, page int) (sseReportPayload, error) {
	endpoint, err := url.Parse(c.endpoint)
	if err != nil {
		return sseReportPayload{}, err
	}
	query := endpoint.Query()
	query.Set("isPagination", "true")
	query.Set("pageHelp.pageSize", ssePageSize)
	query.Set("pageHelp.pageNo", strconv.Itoa(page))
	query.Set("pageHelp.beginPage", strconv.Itoa(page))
	query.Set("pageHelp.cacheSize", "1")
	query.Set("pageHelp.endPage", strconv.Itoa(page))
	switch fundType {
	case "ETF":
		query.Set("sqlId", sseETFSQLID)
		query.Set("STAT_DATE", date)
	case "LOF":
		query.Set("sqlId", sseLOFSQLID)
		query.Set("PRODUCT_TYPE", "11,14,15")
		query.Set("SEARCH_DATE", strings.ReplaceAll(date, "-", ""))
		query.Set("type", "inParams")
	default:
		return sseReportPayload{}, fmt.Errorf("fund type must be ETF or LOF")
	}
	endpoint.RawQuery = query.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return sseReportPayload{}, err
	}
	request.Header.Set("Accept", "application/json,text/javascript,*/*;q=0.01")
	request.Header.Set("User-Agent", c.userAgent)
	request.Header.Set("Referer", sseRefererForFundType(fundType))

	response, err := c.httpClient.Do(request)
	if err != nil {
		return sseReportPayload{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 512))
		return sseReportPayload{}, fmt.Errorf("sse report http %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	var report sseReportPayload
	if err := json.NewDecoder(response.Body).Decode(&report); err != nil {
		return sseReportPayload{}, err
	}
	return report, nil
}

func sseRefererForFundType(fundType string) string {
	switch normalizeFundType(fundType) {
	case "LOF":
		return "https://www.sse.com.cn/market/funddata/volumn/lofvolumn/"
	default:
		return "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"
	}
}

type sseReportPayload struct {
	Result   []sseReportRow `json:"result"`
	PageHelp ssePageHelp    `json:"pageHelp"`
}

type ssePageHelp struct {
	PageNo    int `json:"pageNo"`
	PageSize  int `json:"pageSize"`
	PageCount int `json:"pageCount"`
	Total     int `json:"total"`
}

type sseReportRow struct {
	StatDate    string `json:"STAT_DATE"`
	SECCode     string `json:"SEC_CODE"`
	SECName     string `json:"SEC_NAME"`
	TotVol      string `json:"TOT_VOL"`
	TradeDate   string `json:"TRADE_DATE"`
	FundCode    string `json:"FUND_CODE"`
	FundAbbr    string `json:"FUND_ABBR"`
	SECNameFull string `json:"SEC_NAME_FULL"`
	InternalVol string `json:"INTERNAL_VOL"`
}

func (row sseReportRow) toShareRecord(fundType string) (domain.ShareHistoryRecord, bool) {
	switch fundType {
	case "ETF":
		code := strings.TrimSpace(row.SECCode)
		date := strings.TrimSpace(row.StatDate)
		shares, ok := parseShareDecimal(row.TotVol)
		if code == "" || date == "" || !ok || shares <= 0 {
			return domain.ShareHistoryRecord{}, false
		}
		return domain.ShareHistoryRecord{
			Symbol:    "SH" + code,
			Name:      strings.TrimSpace(row.SECName),
			FundType:  "ETF",
			ShareDate: date,
			Shares10K: shares,
			Source:    "sse_etf_volume",
		}, true
	case "LOF":
		code := strings.TrimSpace(row.FundCode)
		date := normalizeSSETradeDate(row.TradeDate)
		name := strings.TrimSpace(row.SECNameFull)
		if name == "" {
			name = strings.TrimSpace(row.FundAbbr)
		}
		shares, ok := parseShareDecimal(row.InternalVol)
		if code == "" || date == "" || !ok || shares <= 0 {
			return domain.ShareHistoryRecord{}, false
		}
		return domain.ShareHistoryRecord{
			Symbol:    "SH" + code,
			Name:      name,
			FundType:  "LOF",
			ShareDate: date,
			Shares10K: shares,
			Source:    "sse_lof_volume",
		}, true
	default:
		return domain.ShareHistoryRecord{}, false
	}
}

func normalizeSSETradeDate(value string) string {
	value = strings.TrimSpace(value)
	if len(value) == 8 {
		return value[:4] + "-" + value[4:6] + "-" + value[6:8]
	}
	if len(value) == len("2006-01-02") {
		return value
	}
	return ""
}
