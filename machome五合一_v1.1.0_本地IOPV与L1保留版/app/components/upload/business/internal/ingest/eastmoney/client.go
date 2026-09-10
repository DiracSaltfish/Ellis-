package eastmoney

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
)

const defaultBaseURL = "https://api.fund.eastmoney.com"

type Client struct {
	baseURL    string
	httpClient *http.Client
}

func NewClient(timeout time.Duration) *Client {
	return &Client{
		baseURL:    defaultBaseURL,
		httpClient: &http.Client{Timeout: timeout},
	}
}

type NetValueRecord struct {
	FundCode       string
	Date           string
	UnitNAV        float64
	AccumulatedNAV float64
	GrowthRate     float64
	Source         string
}

type lsjzResponse struct {
	Data struct {
		LSJZList []struct {
			FSRQ  string `json:"FSRQ"`
			DWJZ  string `json:"DWJZ"`
			LJJZ  string `json:"LJJZ"`
			JZZZL string `json:"JZZZL"`
		} `json:"LSJZList"`
	} `json:"Data"`
	ErrCode    int    `json:"ErrCode"`
	ErrMsg     string `json:"ErrMsg"`
	TotalCount int    `json:"TotalCount"`
}

func (c *Client) FetchNetValues(ctx context.Context, fundCode string, startDate string, endDate string) ([]NetValueRecord, error) {
	fundCode = strings.TrimSpace(fundCode)
	if fundCode == "" {
		return nil, fmt.Errorf("fund code is required")
	}

	query := url.Values{}
	query.Set("fundCode", fundCode)
	query.Set("pageIndex", "1")
	query.Set("pageSize", "200")
	query.Set("startDate", startDate)
	query.Set("endDate", endDate)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/f10/lsjz?"+query.Encode(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Referer", "https://fundf10.eastmoney.com/jjjz_"+fundCode+".html")
	req.Header.Set("User-Agent", "Mozilla/5.0 newnavnav-eastmoney-nav/0.1")
	req.Header.Set("Accept", "application/json,text/plain,*/*")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("eastmoney status %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	payload := strings.TrimSpace(string(body))
	payload = unwrapJSONP(payload)

	var decoded lsjzResponse
	if err := json.Unmarshal([]byte(payload), &decoded); err != nil {
		return nil, err
	}
	if decoded.ErrCode != 0 {
		if decoded.ErrMsg == "" {
			decoded.ErrMsg = "unknown error"
		}
		return nil, fmt.Errorf("eastmoney errcode %d: %s", decoded.ErrCode, decoded.ErrMsg)
	}

	records := make([]NetValueRecord, 0, len(decoded.Data.LSJZList))
	for _, item := range decoded.Data.LSJZList {
		unitNAV := parseFloat(item.DWJZ)
		if item.FSRQ == "" || unitNAV <= 0 {
			continue
		}
		records = append(records, NetValueRecord{
			FundCode:       fundCode,
			Date:           item.FSRQ,
			UnitNAV:        unitNAV,
			AccumulatedNAV: parseFloat(item.LJJZ),
			GrowthRate:     parseFloat(item.JZZZL),
			Source:         "eastmoney",
		})
	}
	return records, nil
}

func unwrapJSONP(payload string) string {
	if strings.HasPrefix(payload, "{") || strings.HasPrefix(payload, "[") {
		return payload
	}
	open := strings.Index(payload, "(")
	close := strings.LastIndex(payload, ")")
	if open >= 0 && close > open {
		return payload[open+1 : close]
	}
	return payload
}

func parseFloat(value string) float64 {
	value = strings.TrimSpace(strings.TrimSuffix(value, "%"))
	if value == "" || value == "--" {
		return 0
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil {
		return 0
	}
	return parsed
}
