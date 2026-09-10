package live

import (
	"database/sql"
	"encoding/json"
	"fmt"
	_ "github.com/mattn/go-sqlite3"
	"os"
	"path/filepath"
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
	_, e = db.Exec(`CREATE TABLE IF NOT EXISTS minutes(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,minute TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(symbol,trade_date,minute)); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL); CREATE TABLE IF NOT EXISTS pcf(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,hash TEXT NOT NULL,raw BLOB NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(symbol,trade_date,hash)); CREATE TABLE IF NOT EXISTS suspensions(trade_date TEXT NOT NULL,symbol TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,confirmed_at TEXT NOT NULL,PRIMARY KEY(trade_date,symbol)); CREATE TABLE IF NOT EXISTS fx(at TEXT PRIMARY KEY,trade_date TEXT NOT NULL,payload BLOB NOT NULL);`)
	if e != nil {
		db.Close()
		return nil, e
	}
	return &Store{db}, nil
}
func (s *Store) Write(points []Point) error {
	tx, e := s.DB.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	stmt, e := tx.Prepare(`INSERT INTO minutes VALUES(?,?,?,?) ON CONFLICT(symbol,trade_date,minute) DO UPDATE SET payload=excluded.payload WHERE json_extract(excluded.payload,'$.calculated_at')>json_extract(minutes.payload,'$.calculated_at')`)
	if e != nil {
		return e
	}
	defer stmt.Close()
	for _, p := range points {
		raw, e := json.Marshal(p)
		if e != nil {
			return e
		}
		if _, e = stmt.Exec(p.Symbol, p.Date, p.Minute, string(raw)); e != nil {
			return e
		}
	}
	return tx.Commit()
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
	return out, rows.Err()
}
func (s *Store) Dates(symbol string) ([]string, error) {
	r, e := s.DB.Query("SELECT DISTINCT trade_date FROM minutes WHERE symbol=? ORDER BY trade_date DESC LIMIT 120", symbol)
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
