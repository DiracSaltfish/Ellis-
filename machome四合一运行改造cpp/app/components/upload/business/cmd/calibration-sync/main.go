package main

import (
	"context"
	"log/slog"
	"os"
	"time"

	"newnavnav/internal/calsync"
	"newnavnav/internal/config"
	"newnavnav/internal/db"
)

func main() {
	cfg := config.Load()
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))
	slog.SetDefault(logger)

	if cfg.MySQLDSN == "" {
		logger.Error("MYSQL_DSN is required")
		os.Exit(1)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	database, err := db.Open(ctx, cfg.MySQLDSN)
	if err != nil {
		logger.Error("mysql unavailable", "error", err)
		os.Exit(1)
	}
	defer database.Close()

	service := calsync.NewService(calsync.Options{
		Repository: db.NewRepository(database),
		Logger:     logger,
	})
	if err := service.SyncMissing(ctx, time.Now()); err != nil {
		logger.Error("daily calibration sync failed", "error", err)
		os.Exit(1)
	}
}
