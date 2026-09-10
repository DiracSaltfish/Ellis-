// Export the current PCF-backed IOPV subscription universe for raw-history staging.
package main

import (
	"bytes"
	"compress/zlib"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"intranet-iopv"
)

func decodeStoredPCF(raw []byte) ([]byte, error) {
	if !bytes.HasPrefix(raw, []byte("IOPZ1")) {
		return raw, nil
	}
	z, err := zlib.NewReader(bytes.NewReader(raw[len("IOPZ1"):]))
	if err != nil {
		return nil, err
	}
	defer z.Close()
	return io.ReadAll(z)
}

type manifest struct {
	Schema        string   `json:"schema"`
	PCFDate       string   `json:"pcf_date"`
	ExportedAt    string   `json:"exported_at"`
	ETFs          []string `json:"etfs"`
	HKComponents  []string `json:"hk_components"`
	Subscriptions []string `json:"subscriptions"`
	RejectedPCFs  []string `json:"rejected_pcfs"`
}

func main() {
	dbPath := flag.String("db", "", "IOPV SQLite database")
	date := flag.String("date", "", "PCF date YYYY-MM-DD")
	output := flag.String("output", "", "manifest JSON output")
	flag.Parse()
	if *dbPath == "" || *date == "" || *output == "" {
		flag.Usage()
		os.Exit(2)
	}
	if _, err := time.Parse("2006-01-02", *date); err != nil {
		panic(err)
	}
	db, err := sql.Open("sqlite3", *dbPath)
	if err != nil {
		panic(err)
	}
	defer db.Close()
	rows, err := db.Query("select symbol,raw from pcf where trade_date=? order by symbol", *date)
	if err != nil {
		panic(err)
	}
	defer rows.Close()
	etfs, hk, rejected := map[string]bool{}, map[string]bool{}, []string{}
	for rows.Next() {
		var symbol string
		var raw []byte
		if err := rows.Scan(&symbol, &raw); err != nil {
			panic(err)
		}
		raw, err = decodeStoredPCF(raw)
		if err != nil {
			rejected = append(rejected, fmt.Sprintf("%s: stored PCF decode: %v", symbol, err))
			continue
		}
		basket, err := iopv.ParsePCF(raw, symbol, *date)
		if err != nil {
			rejected = append(rejected, fmt.Sprintf("%s: %v", symbol, err))
			continue
		}
		etfs[basket.Symbol] = true
		for _, component := range basket.Components {
			if strings.HasSuffix(component.Symbol, ".HK") && component.Mode != 2 {
				hk[component.Symbol] = true
			}
		}
	}
	if err := rows.Err(); err != nil {
		panic(err)
	}
	toSorted := func(values map[string]bool) []string {
		out := make([]string, 0, len(values))
		for value := range values {
			out = append(out, value)
		}
		sort.Strings(out)
		return out
	}
	m := manifest{Schema: "iopv-subscription-history-stage.v1", PCFDate: *date, ExportedAt: time.Now().UTC().Format(time.RFC3339), ETFs: toSorted(etfs), HKComponents: toSorted(hk), RejectedPCFs: rejected}
	m.Subscriptions = append(append([]string{}, m.ETFs...), m.HKComponents...)
	sort.Strings(m.Subscriptions)
	wire, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		panic(err)
	}
	if err := os.WriteFile(*output, append(wire, '\n'), 0600); err != nil {
		panic(err)
	}
}
