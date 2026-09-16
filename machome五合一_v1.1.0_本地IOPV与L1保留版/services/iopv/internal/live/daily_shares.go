package live

import (
	"database/sql"
	"net/http"
	"regexp"
	"time"
)

var shareSymbol = regexp.MustCompile(`^[0-9]{6}\.(SH|SZ)$`)

type DailyShares struct {
	Symbol  string   `json:"symbol"`
	Date    string   `json:"trade_date"`
	Shares  *float64 `json:"shares_10k"`
	Change  *float64 `json:"share_change_10k"`
	Source  string   `json:"source,omitempty"`
	Updated string   `json:"source_updated_at,omitempty"`
}

func (s *Service) dailySharesHandler(w http.ResponseWriter, r *http.Request) {
	symbol, date := r.URL.Query().Get("symbol"), r.URL.Query().Get("date")
	if _, err := time.Parse("2006-01-02", date); err != nil || !shareSymbol.MatchString(symbol) {
		http.Error(w, "bad symbol or date", http.StatusBadRequest)
		return
	}
	v := DailyShares{Symbol: symbol, Date: date}
	err := s.store.DB.QueryRow("SELECT shares_10k,share_change_10k,source,source_updated_at FROM daily_shares WHERE symbol=? AND trade_date=?", symbol, date).Scan(&v.Shares, &v.Change, &v.Source, &v.Updated)
	if err != nil && err != sql.ErrNoRows {
		http.Error(w, "storage unavailable", 503)
		return
	}
	writeJSON(w, v)
}
