package iopv

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
)

var fundPattern = regexp.MustCompile(`^[0-9]{6}\.(SH|SZ)$`)

func PCFURL(symbol, date string) (string, error) {
	if !fundPattern.MatchString(symbol) {
		return "", fmt.Errorf("invalid fund symbol")
	}
	if _, e := time.Parse("2006-01-02", date); e != nil {
		return "", e
	}
	code := symbol[:6]
	if strings.HasSuffix(symbol, ".SH") {
		return "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=" + code, nil
	}
	return "https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_" + code + "_" + strings.ReplaceAll(date, "-", "") + ".xml", nil
}
func FetchPCF(ctx context.Context, client *http.Client, symbol, date string) (Basket, []byte, error) {
	url, err := PCFURL(symbol, date)
	if err != nil {
		return Basket{}, nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return Basket{}, nil, err
	}
	req.Header.Set("User-Agent", "intranet-iopv-prototype/1")
	resp, err := client.Do(req)
	if err != nil {
		return Basket{}, nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return Basket{}, nil, fmt.Errorf("PCF HTTP %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (8<<20)+1))
	if err != nil {
		return Basket{}, nil, err
	}
	b, err := ParsePCF(raw, symbol, date)
	return b, raw, err
}
