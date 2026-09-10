package sina

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"newnavnav/internal/domain"
	"golang.org/x/text/encoding/simplifiedchinese"
)

const chromeUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"

type Client struct {
	httpClient *http.Client
}

func NewClient(timeout time.Duration) *Client {
	return &Client{
		httpClient: &http.Client{Timeout: timeout},
	}
}

func (c *Client) FetchQuotes(ctx context.Context, symbols []string) (map[string]domain.Quote, error) {
	if len(symbols) == 0 {
		return map[string]domain.Quote{}, nil
	}

	out := make(map[string]domain.Quote)
	var errs []error
	for _, batch := range batches(symbols, 80) {
		quotes, err := c.fetchBatch(ctx, batch)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		for symbol, quote := range quotes {
			out[symbol] = quote
		}
	}
	return out, errors.Join(errs...)
}

func (c *Client) fetchBatch(ctx context.Context, symbols []string) (map[string]domain.Quote, error) {
	sinaSymbols := make([]string, 0, len(symbols))
	reverse := make(map[string]string, len(symbols))
	for _, symbol := range symbols {
		sinaSymbol := ToSinaSymbol(symbol)
		if sinaSymbol == "" {
			continue
		}
		sinaSymbols = append(sinaSymbols, sinaSymbol)
		reverse[sinaSymbol] = symbol
	}
	if len(sinaSymbols) == 0 {
		return map[string]domain.Quote{}, nil
	}

	url := "https://hq.sinajs.cn/list=" + strings.Join(sinaSymbols, ",")
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Referer", "https://finance.sina.com.cn")
	req.Header.Set("User-Agent", chromeUserAgent)
	req.Header.Set("Accept", "*/*")
	req.Header.Set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
	req.Header.Set("Cache-Control", "no-cache")
	req.Header.Set("Pragma", "no-cache")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("sina status %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	decoded, decodeErr := simplifiedchinese.GB18030.NewDecoder().Bytes(body)
	if decodeErr == nil {
		body = decoded
	}
	return ParseResponse(string(body), reverse), nil
}

func batches(symbols []string, size int) [][]string {
	var out [][]string
	for start := 0; start < len(symbols); start += size {
		end := start + size
		if end > len(symbols) {
			end = len(symbols)
		}
		out = append(out, symbols[start:end])
	}
	return out
}
