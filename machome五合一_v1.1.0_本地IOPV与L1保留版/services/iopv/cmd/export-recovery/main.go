package main

import (
	"bytes"
	"compress/zlib"
	"database/sql"
	"encoding/json"
	"fmt"
	_ "github.com/mattn/go-sqlite3"
	iopv "intranet-iopv"
	"io"
	"os"
)

func main() {
	db, e := sql.Open("sqlite3", os.Args[1]+"?mode=ro")
	if e != nil {
		panic(e)
	}
	defer db.Close()
	rows, e := db.Query("SELECT symbol,trade_date,hash,raw FROM pcf WHERE trade_date=?", os.Args[2])
	if e != nil {
		panic(e)
	}
	defer rows.Close()
	out := map[string]iopv.Basket{}
	for rows.Next() {
		var s, d, h string
		var raw []byte
		if e = rows.Scan(&s, &d, &h, &raw); e != nil {
			panic(e)
		}
		if bytes.HasPrefix(raw, []byte("IOPZ1")) {
			z, e := zlib.NewReader(bytes.NewReader(raw[5:]))
			if e != nil {
				panic(e)
			}
			raw, e = io.ReadAll(z)
			z.Close()
			if e != nil {
				panic(e)
			}
		}
		b, e := iopv.ParsePCF(raw, s, d)
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			continue
		}
		out[h] = b
	}
	if e = rows.Err(); e != nil {
		panic(e)
	}
	json.NewEncoder(os.Stdout).Encode(out)
}
