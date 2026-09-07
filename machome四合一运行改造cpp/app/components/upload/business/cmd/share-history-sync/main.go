package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	"newnavnav/internal/closepremium"
	"newnavnav/internal/domain"
	"newnavnav/internal/sharesync"
	"newnavnav/internal/snapshot"
)

func main() {
	market := flag.String("market", "szse", "market to sync: szse, sse, or all")
	startDate := flag.String("start", "", "start date in YYYY-MM-DD")
	endDate := flag.String("end", "", "end date in YYYY-MM-DD")
	dir := flag.String("dir", "snapshots/share_history", "share history CSV directory")
	minuteHistoryDir := flag.String("minute-history-dir", "snapshots/minute_history", "minute history directory used to fill close premium")
	minuteHistoryRetention := flag.Int("minute-history-retention-days", 45, "minute history retention window")
	szseInterval := flag.Duration("szse-interval", 3*time.Second, "delay between SZSE symbols")
	sseInterval := flag.Duration("sse-interval", 200*time.Millisecond, "delay between SSE daily ETF/LOF requests")
	timeout := flag.Duration("timeout", 20*time.Second, "HTTP timeout")
	flag.Parse()

	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	ctx := context.Background()
	store := sharesync.NewCSVStore(*dir)
	store.SetClosePremiumLookup(closepremium.ShareHistoryLookup(snapshot.NewMinuteHistoryStore(*minuteHistoryDir, *minuteHistoryRetention)))
	selected := strings.ToLower(strings.TrimSpace(*market))
	if selected != "all" && selected != "szse" && selected != "sse" {
		fatalf("unsupported market %q", *market)
	}

	total := 0
	if selected == "all" || selected == "szse" {
		targetStart, targetEnd := normalizeRange(*startDate, *endDate, 60)
		service := sharesync.NewService(sharesync.Options{
			Client:              sharesync.NewClient(*timeout),
			Repository:          store,
			Symbols:             domain.ShareHistorySymbols(),
			Logger:              logger,
			QueryInterval:       *szseInterval,
			InitialLookbackDays: 60,
		})
		rows, err := service.SyncRange(ctx, targetStart, targetEnd)
		if err != nil {
			fatalf("sync szse share history failed: %v", err)
		}
		total += len(rows)
		logger.Info("szse share history sync done", "rows", len(rows), "start", targetStart, "end", targetEnd)
	}
	if selected == "all" || selected == "sse" {
		targetStart, targetEnd := normalizeRange(*startDate, *endDate, 1)
		service := sharesync.NewSSEService(sharesync.SSEOptions{
			Client:              sharesync.NewSSEClient(*timeout),
			Repository:          store,
			Symbols:             domain.ShareHistorySymbols(),
			Logger:              logger,
			QueryInterval:       *sseInterval,
			InitialLookbackDays: 60,
		})
		rows, err := service.SyncRange(ctx, targetStart, targetEnd)
		if err != nil {
			fatalf("sync sse share history failed: %v", err)
		}
		total += len(rows)
		logger.Info("sse share history sync done", "rows", len(rows), "start", targetStart, "end", targetEnd)
	}
	logger.Info("share history sync finished", "rows", total, "dir", *dir)
}

func normalizeRange(start string, end string, lookbackDays int) (string, string) {
	start = strings.TrimSpace(start)
	end = strings.TrimSpace(end)
	if end == "" {
		end = time.Now().Format("2006-01-02")
	}
	if start == "" {
		parsed, err := time.Parse("2006-01-02", end)
		if err != nil {
			parsed = time.Now()
		}
		start = parsed.AddDate(0, 0, -lookbackDays+1).Format("2006-01-02")
	}
	return start, end
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
