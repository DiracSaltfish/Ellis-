package live

import (
	"bytes"
	"compress/zlib"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"sort"
)

// Versioned per-fund/day blocks compress repeated metadata across minutes.
// Fixed prices apply only to persisted history, never the live Point.
type archivePoint struct {
	Meta         Point     `json:"q"`
	Prices       [4]*int64 `json:"p"`
	PremiumValid [3]bool   `json:"v"`
}

func packPoint(p Point) archivePoint {
	a := archivePoint{Meta: p, PremiumValid: [3]bool{p.MidPremium != nil, p.BuyPremium != nil, p.SellPremium != nil}}
	for i, v := range []*float64{p.ETF, p.Mid, p.Buy, p.Sell} {
		if v != nil {
			scale := 10000.
			if i == 0 {
				scale = 1000
			}
			n := int64(math.Round(*v * scale))
			a.Prices[i] = &n
		}
	}
	a.Meta.ETF = nil
	a.Meta.Mid = nil
	a.Meta.Buy = nil
	a.Meta.Sell = nil
	a.Meta.MidPremium = nil
	a.Meta.BuyPremium = nil
	a.Meta.SellPremium = nil
	a.Meta.Book = nil
	a.Meta.BookValues = nil
	a.Meta.HKDAssets = nil
	a.Meta.CNYAssets = nil
	return a
}
func unpackPoint(a archivePoint) Point {
	p := a.Meta
	v := [4]*float64{}
	for i, n := range a.Prices {
		if n != nil {
			scale := 10000.
			if i == 0 {
				scale = 1000
			}
			v[i] = ptr(float64(*n) / scale)
		}
	}
	p.ETF = v[0]
	p.Mid = v[1]
	p.Buy = v[2]
	p.Sell = v[3]
	if a.PremiumValid[0] {
		p.MidPremium = premium(p.ETF, p.Mid)
	}
	if a.PremiumValid[1] {
		p.BuyPremium = premium(p.ETF, p.Buy)
	}
	if a.PremiumValid[2] {
		p.SellPremium = premium(p.ETF, p.Sell)
	}
	return p
}

var archiveMagic = []byte("IOPZ1")

func compressArchive(raw []byte) ([]byte, error) {
	var b bytes.Buffer
	b.Write(archiveMagic)
	z, e := zlib.NewWriterLevel(&b, zlib.BestCompression)
	if e != nil {
		return nil, e
	}
	if _, e = z.Write(raw); e != nil {
		return nil, e
	}
	if e = z.Close(); e != nil {
		return nil, e
	}
	return b.Bytes(), nil
}
func decodeArchive(raw []byte) ([]byte, error) {
	if !bytes.HasPrefix(raw, archiveMagic) {
		return raw, nil
	}
	z, e := zlib.NewReader(bytes.NewReader(raw[len(archiveMagic):]))
	if e != nil {
		return nil, e
	}
	defer z.Close()
	b, e := io.ReadAll(io.LimitReader(z, 32<<20))
	if len(b) >= 32<<20 {
		return nil, fmt.Errorf("archive exceeds limit")
	}
	return b, e
}
func readBlock(raw []byte) ([]archivePoint, error) {
	b, e := decodeArchive(raw)
	if e != nil {
		return nil, e
	}
	var a []archivePoint
	e = json.Unmarshal(b, &a)
	return a, e
}
func (s *Store) writeBlocks(points []Point) error {
	tx, e := s.DB.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	groups := map[[2]string][]Point{}
	for _, p := range points {
		k := [2]string{p.Symbol, p.Date}
		groups[k] = append(groups[k], p)
	}
	for k, ps := range groups {
		var raw []byte
		old := []archivePoint{}
		e = tx.QueryRow("SELECT payload FROM minute_blocks WHERE symbol=? AND trade_date=?", k[0], k[1]).Scan(&raw)
		if e != nil && e != sql.ErrNoRows {
			return e
		}
		if e == nil {
			old, e = readBlock(raw)
			if e != nil {
				return e
			}
		}
		byMinute := map[string]archivePoint{}
		for _, p := range old {
			byMinute[p.Meta.Minute] = p
		}
		for _, p := range ps {
			v, ok := byMinute[p.Minute]
			if !ok || p.At.After(v.Meta.At) {
				byMinute[p.Minute] = packPoint(p)
			}
		}
		out := make([]archivePoint, 0, len(byMinute))
		for _, p := range byMinute {
			out = append(out, p)
		}
		sort.Slice(out, func(i, j int) bool { return out[i].Meta.Minute < out[j].Meta.Minute })
		b, e := json.Marshal(out)
		if e != nil {
			return e
		}
		b, e = compressArchive(b)
		if e != nil {
			return e
		}
		if _, e = tx.Exec("INSERT INTO minute_blocks VALUES(?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET payload=excluded.payload,point_count=excluded.point_count", k[0], k[1], b, len(out)); e != nil {
			return e
		}
	}
	return tx.Commit()
}

// CompactLegacy is an offline operation on a backed-up database. It can be retried.
func (s *Store) CompactLegacy() (int, error) {
	rows, e := s.DB.Query("SELECT payload FROM minutes")
	if e != nil {
		return 0, e
	}
	var ps []Point
	for rows.Next() {
		var raw []byte
		if e = rows.Scan(&raw); e != nil {
			rows.Close()
			return 0, e
		}
		var p Point
		if e = json.Unmarshal(raw, &p); e != nil {
			rows.Close()
			return 0, e
		}
		ps = append(ps, p)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return 0, e
	}
	if e = s.writeBlocks(ps); e != nil {
		return 0, e
	}
	// Validate every migrated point before deleting the original rows.
	checked := map[[2]string]map[string]archivePoint{}
	for _, p := range ps {
		k := [2]string{p.Symbol, p.Date}
		m, ok := checked[k]
		if !ok {
			var raw []byte
			if e = s.DB.QueryRow("SELECT payload FROM minute_blocks WHERE symbol=? AND trade_date=?", k[0], k[1]).Scan(&raw); e != nil {
				return 0, e
			}
			a, err := readBlock(raw)
			if err != nil {
				return 0, err
			}
			m = map[string]archivePoint{}
			for _, v := range a {
				m[v.Meta.Minute] = v
			}
			checked[k] = m
		}
		v, ok := m[p.Minute]
		if !ok || v.Meta.At.Before(p.At) {
			return 0, fmt.Errorf("migration missing/newer minute")
		}
		if v.Meta.At.Equal(p.At) {
			want, _ := json.Marshal(packPoint(p))
			got, _ := json.Marshal(v)
			if !bytes.Equal(want, got) {
				return 0, fmt.Errorf("migration value mismatch")
			}
		}
	}
	if _, e = s.DB.Exec("DELETE FROM minutes"); e != nil {
		return 0, e
	}
	for _, spec := range [][2]string{{"pcf", "raw"}} {
		rs, err := s.DB.Query("SELECT rowid," + spec[1] + " FROM " + spec[0])
		if err != nil {
			return 0, err
		}
		type item struct {
			id int64
			b  []byte
		}
		var items []item
		for rs.Next() {
			var x item
			if err = rs.Scan(&x.id, &x.b); err != nil {
				rs.Close()
				return 0, err
			}
			items = append(items, x)
		}
		err = rs.Err()
		rs.Close()
		if err != nil {
			return 0, err
		}
		for _, x := range items {
			if bytes.HasPrefix(x.b, archiveMagic) {
				continue
			}
			b, err := compressArchive(x.b)
			if err != nil {
				return 0, err
			}
			if _, err = s.DB.Exec("UPDATE "+spec[0]+" SET "+spec[1]+"=? WHERE rowid=?", b, x.id); err != nil {
				return 0, err
			}
		}
	}
	if e = s.compactFX(); e != nil {
		return 0, e
	}
	if _, e = s.DB.Exec("PRAGMA wal_checkpoint(TRUNCATE)"); e != nil {
		return 0, e
	}
	_, e = s.DB.Exec("VACUUM")
	return len(ps), e
}

func archiveBytes(b []byte) []byte {
	z, e := compressArchive(b)
	if e != nil {
		return b
	}
	return z
}

// Hourly FX source blocks preserve raw snapshot bytes and timestamps losslessly.
type fxArchive struct {
	At  string `json:"t"`
	Raw []byte `json:"r"`
}

func (s *Store) WriteFX(at, date string, raw []byte) error {
	return s.writeFXBatch(date, []fxArchive{{at, raw}})
}
func (s *Store) writeFXBatch(date string, items []fxArchive) error {
	groups := map[string][]fxArchive{}
	for _, x := range items {
		if len(x.At) < 13 {
			return fmt.Errorf("invalid FX timestamp")
		}
		k := x.At[:13]
		groups[k] = append(groups[k], x)
	}
	tx, e := s.DB.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	for hour, xs := range groups {
		var b []byte
		all := map[string][]byte{}
		e = tx.QueryRow("SELECT payload FROM fx_blocks WHERE bucket=?", hour).Scan(&b)
		if e != nil && e != sql.ErrNoRows {
			return e
		}
		if e == nil {
			b, e = decodeArchive(b)
			if e != nil {
				return e
			}
			if e = json.Unmarshal(b, &all); e != nil {
				return e
			}
		}
		for _, x := range xs {
			if _, ok := all[x.At]; !ok {
				all[x.At] = x.Raw
			}
		}
		b, e = json.Marshal(all)
		if e != nil {
			return e
		}
		b, e = compressArchive(b)
		if e != nil {
			return e
		}
		if _, e = tx.Exec("INSERT INTO fx_blocks VALUES(?,?,?,?) ON CONFLICT(bucket) DO UPDATE SET payload=excluded.payload,point_count=excluded.point_count", hour, date, b, len(all)); e != nil {
			return e
		}
	}
	return tx.Commit()
}
func (s *Store) compactFX() error {
	rows, e := s.DB.Query("SELECT at,trade_date,payload FROM fx")
	if e != nil {
		return e
	}
	groups := map[string][]fxArchive{}
	for rows.Next() {
		var at, day string
		var raw []byte
		if e = rows.Scan(&at, &day, &raw); e != nil {
			rows.Close()
			return e
		}
		raw, e = decodeArchive(raw)
		if e != nil {
			rows.Close()
			return e
		}
		groups[day] = append(groups[day], fxArchive{at, raw})
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for day, xs := range groups {
		if e = s.writeFXBatch(day, xs); e != nil {
			return e
		}
		cache := map[string]map[string][]byte{}
		for _, x := range xs {
			key := x.At[:13]
			m, ok := cache[key]
			if !ok {
				var b []byte
				if e = s.DB.QueryRow("SELECT payload FROM fx_blocks WHERE bucket=?", key).Scan(&b); e != nil {
					return e
				}
				b, e = decodeArchive(b)
				if e != nil {
					return e
				}
				if e = json.Unmarshal(b, &m); e != nil {
					return e
				}
				cache[key] = m
			}
			if !bytes.Equal(m[x.At], x.Raw) {
				return fmt.Errorf("FX migration mismatch")
			}
		}
	}
	_, e = s.DB.Exec("DELETE FROM fx")
	return e
}
