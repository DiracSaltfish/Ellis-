package main

import (
	"context"
	"flag"
	"fmt"
	iopv "intranet-iopv"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

func main() {
	symbol := flag.String("symbol", "513090.SH", "ETF symbol")
	date := flag.String("date", "", "required trading date YYYY-MM-DD")
	flag.Parse()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	b, raw, e := iopv.FetchPCF(ctx, &http.Client{Timeout: 15 * time.Second}, *symbol, *date)
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
	dir := filepath.Join("outputs", "pcf", *date)
	if e = os.MkdirAll(dir, 0755); e == nil {
		e = os.WriteFile(filepath.Join(dir, *symbol+".xml"), raw, 0644)
	}
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
	fmt.Printf("%s %s rows=%d unit=%.0f cash=%.2f sha256=%s\n", b.Symbol, b.Date, len(b.Components), b.Unit, b.Cash, b.Hash)
}
