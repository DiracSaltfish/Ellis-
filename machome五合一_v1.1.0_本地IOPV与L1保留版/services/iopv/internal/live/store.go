package live

import (
	"database/sql"
	"encoding/json"
	"fmt"
	_ "github.com/mattn/go-sqlite3"
	"os"
	"path/filepath"
	"sort"
	"time"
)

type Store struct{ DB *sql.DB }

func OpenStore(path string) (*Store, error) {
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return nil, e
	}
	db, e := sql.Open("sqlite3", path+"?_journal_mode=WAL&_busy_timeout=5000&_synchronous=FULL")
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	_, e = db.Exec(`CREATE TABLE IF NOT EXISTS fx_blocks(bucket TEXT PRIMARY KEY,trade_date TEXT NOT NULL,payload BLOB NOT NULL,point_count INTEGER NOT NULL) WITHOUT ROWID; CREATE TABLE IF NOT EXISTS minute_blocks(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,payload BLOB NOT NULL,point_count INTEGER NOT NULL,PRIMARY KEY(symbol,trade_date)) WITHOUT ROWID; CREATE TABLE IF NOT EXISTS minutes(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,minute TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(symbol,trade_date,minute)); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL); CREATE TABLE IF NOT EXISTS pcf(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,hash TEXT NOT NULL,raw BLOB NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(symbol,trade_date,hash)); CREATE TABLE IF NOT EXISTS suspensions(trade_date TEXT NOT NULL,symbol TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,confirmed_at TEXT NOT NULL,PRIMARY KEY(trade_date,symbol)); CREATE TABLE IF NOT EXISTS fx(at TEXT PRIMARY KEY,trade_date TEXT NOT NULL,payload BLOB NOT NULL);`)
	if e != nil {
		db.Close()
		return nil, e
	}
	return &Store{db}, nil
}
func (s *Store) Write(points []Point) error {
	return s.writeBlocks(points)
}

func (s *Store) History(symbol, date string) ([]Point, error) {
	rows, e := s.DB.Query("SELECT payload FROM minutes WHERE symbol=? AND trade_date=? ORDER BY minute", symbol, date)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []Point{}
	for rows.Next() {
		var raw string
		var p Point
		if e = rows.Scan(&raw); e != nil {
			return nil, e
		}
		if e = json.Unmarshal([]byte(raw), &p); e != nil {
			return nil, e
		}
		out = append(out, p)
	}
	if e = rows.Err(); e != nil {
		return nil, e
	}
	rows.Close()
	var block []byte
	e = s.DB.QueryRow("SELECT payload FROM minute_blocks WHERE symbol=? AND trade_date=?", symbol, date).Scan(&block)
	if e != nil && e != sql.ErrNoRows {
		return nil, e
	}
	if e == nil {
		a, err := readBlock(block)
		if err != nil {
			return nil, err
		}
		by := map[string]Point{}
		for _, p := range out {
			by[p.Minute] = p
		}
		for _, v := range a {
			p := unpackPoint(v)
			old, ok := by[p.Minute]
			if !ok || !p.At.Before(old.At) {
				by[p.Minute] = p
			}
		}
		out = out[:0]
		for _, p := range by {
			out = append(out, p)
		}
		sort.Slice(out, func(i, j int) bool { return out[i].Minute < out[j].Minute })
	}
	return out, nil
}
func (s *Store) Dates(symbol string) ([]string, error) {
	r, e := s.DB.Query("SELECT trade_date FROM minutes WHERE symbol=? UNION SELECT trade_date FROM minute_blocks WHERE symbol=? ORDER BY trade_date DESC LIMIT 120", symbol, symbol)
	if e != nil {
		return nil, e
	}
	defer r.Close()
	out := []string{}
	for r.Next() {
		var d string
		if e = r.Scan(&d); e != nil {
			return nil, e
		}
		out = append(out, d)
	}
	return out, r.Err()
}
func (s *Store) Event(kind, msg string) {
	s.DB.Exec("INSERT INTO events(at,kind,message) VALUES(?,?,?)", time.Now().In(Zone).Format(time.RFC3339), kind, msg)
}
func (s *Store) Check() error {
	var answer string
	if e := s.DB.QueryRow("PRAGMA quick_check").Scan(&answer); e != nil {
		return e
	}
	if answer != "ok" {
		return fmt.Errorf("sqlite: %s", answer)
	}
	return nil
}
