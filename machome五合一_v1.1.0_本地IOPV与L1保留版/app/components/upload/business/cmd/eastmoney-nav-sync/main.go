package main

import (
	"context"
	"log/slog"
	"os"
	"time"

	"newnavnav/internal/config"
	"newnavnav/internal/db"
	"newnavnav/internal/domain"
	"newnavnav/internal/ingest/eastmoney"
	"newnavnav/internal/navsync"
)

func main() {
	cfg := config.Load()
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))
	slog.SetDefault(logger)
	if cfg.MySQLDSN == "" {
		logger.Error("MYSQL_DSN is required")
		os.Exit(1)
	}

	ctx := context.Background()
	database, err := db.Open(ctx, cfg.MySQLDSN)
	if err != nil {
		logger.Error("mysql unavailable", "error", err)
		os.Exit(1)
	}
	defer database.Close()

	service := navsync.NewEastmoneyService(navsync.EastmoneyOptions{
		Client:       eastmoney.NewClient(cfg.EastmoneyTimeout),
		Repository:   db.NewRepository(database),
		Symbols:      domain.AllSymbols(),
		LookbackDays: cfg.EastmoneyLookback,
		Delay:        cfg.EastmoneyDelay,
		Logger:       logger,
	})
	if err := service.SyncMissing(ctx, time.Now()); err != nil {
		logger.Error("eastmoney nav sync failed", "error", err)
		os.Exit(1)
	}
}
