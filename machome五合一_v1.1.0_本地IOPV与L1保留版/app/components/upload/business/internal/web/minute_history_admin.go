package web

import (
	"errors"
	"fmt"
	"net/http"
	"strings"

	"newnavnav/internal/snapshot"
)

const maxMinuteHistoryDeleteDayRequestBytes = 8 << 10

type minuteHistoryDeleteDayRequest struct {
	Date string `json:"date"`
}

func (s *Server) handleNavSettingsDeleteMinuteHistoryDay(w http.ResponseWriter, r *http.Request) {
	var req minuteHistoryDeleteDayRequest
	if err := decodeStrictJSONBody(w, r, maxMinuteHistoryDeleteDayRequestBytes, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if strings.TrimSpace(req.Date) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "date is required"})
		return
	}
	result, err := s.snapshots.DeleteMinuteHistoryDay(req.Date)
	if err != nil {
		switch {
		case errors.Is(err, snapshot.ErrInvalidMinuteHistoryDay):
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		case errors.Is(err, snapshot.ErrMinuteHistoryStoreDisabled):
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": err.Error()})
		default:
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		}
		return
	}
	actor := s.debugActorName(r)
	s.snapshots.RecordEvent("warn", "minute_history", "manual minute history day deleted", map[string]any{
		"day": result.Day, "deleted": result.Deleted, "row_count": result.RowCount,
		"symbol_count": result.SymbolCount, "requested_by": actor,
	})
	message := fmt.Sprintf("%s 没有可删除的历史分时估值文件。", formatMinuteHistoryDayLabel(result.Day))
	if result.Deleted {
		message = fmt.Sprintf("已删除 %s 的历史分时估值，共 %d 个标的 / %d 条记录。",
			formatMinuteHistoryDayLabel(result.Day), result.SymbolCount, result.RowCount)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": result.OK, "day": result.Day, "deleted": result.Deleted,
		"symbol_count": result.SymbolCount, "row_count": result.RowCount,
		"warnings": result.Warnings, "message": message,
	})
}

func formatMinuteHistoryDayLabel(day string) string {
	day = strings.TrimSpace(day)
	if len(day) == 8 {
		return day[:4] + "-" + day[4:6] + "-" + day[6:]
	}
	return day
}
