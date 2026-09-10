package live

import (
	"fmt"
	"sort"
	"time"
)

type Suspension struct {
	Date        string    `json:"trade_date"`
	Symbol      string    `json:"symbol"`
	Status      string    `json:"status"`
	Note        string    `json:"note"`
	ConfirmedAt time.Time `json:"confirmed_at"`
}

func (s *Store) Suspensions(date string) (map[string]Suspension, error) {
	rows, e := s.DB.Query("SELECT symbol,status,note,confirmed_at FROM suspensions WHERE trade_date=?", date)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := map[string]Suspension{}
	for rows.Next() {
		v := Suspension{Date: date}
		var at string
		if e = rows.Scan(&v.Symbol, &v.Status, &v.Note, &at); e != nil {
			return nil, e
		}
		v.ConfirmedAt, _ = time.Parse(time.RFC3339Nano, at)
		out[v.Symbol] = v
	}
	return out, rows.Err()
}
func (s *Store) SetSuspension(v Suspension) error {
	if v.Status != "suspended" && v.Status != "trading" {
		return fmt.Errorf("invalid status")
	}
	_, e := s.DB.Exec("INSERT INTO suspensions VALUES(?,?,?,?,?) ON CONFLICT(trade_date,symbol) DO UPDATE SET status=excluded.status,note=excluded.note,confirmed_at=excluded.confirmed_at", v.Date, v.Symbol, v.Status, v.Note, v.ConfirmedAt.Format(time.RFC3339Nano))
	return e
}

type SuspensionImpact struct {
	Suspension
	Funds   []Candidate `json:"funds"`
	Missing bool        `json:"missing_quote"`
}

func (s *Service) SuspensionImpacts(now time.Time) ([]SuspensionImpact, error) {
	date := Day(now)
	states, e := s.store.Suspensions(date)
	if e != nil {
		return nil, e
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	names := map[string]Candidate{}
	for _, c := range s.candidates {
		names[c.Symbol] = c
	}
	out := map[string]*SuspensionImpact{}
	for symbol, b := range s.baskets {
		if b.Date != date {
			continue
		}
		for _, c := range b.Components {
			if c.Mode == 2 {
				continue
			}
			q, ok := s.componentQuote(c.Symbol, now)
			missing := !ok || q.Price <= 0 || Day(q.Observed) != date
			state, confirmed := states[c.Symbol]
			if !missing && (!confirmed || state.Status != "suspended") {
				continue
			}
			if !confirmed {
				state = Suspension{Date: date, Symbol: c.Symbol, Status: "pending", Note: "缺少当日有效报价，待早间核实；不等同于已停牌"}
			}
			v := out[c.Symbol]
			if v == nil {
				v = &SuspensionImpact{Suspension: state, Funds: []Candidate{}, Missing: missing}
				out[c.Symbol] = v
			}
			v.Funds = append(v.Funds, names[symbol])
		}
	}
	result := []SuspensionImpact{}
	for _, v := range out {
		sort.Slice(v.Funds, func(i, j int) bool { return v.Funds[i].Symbol < v.Funds[j].Symbol })
		result = append(result, *v)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Symbol < result[j].Symbol })
	return result, nil
}
