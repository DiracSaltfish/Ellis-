package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"newnavnav/internal/calsync"
	"newnavnav/internal/closepremium"
	"newnavnav/internal/closesync"
	"newnavnav/internal/config"
	"newnavnav/internal/db"
	"newnavnav/internal/domain"
	"newnavnav/internal/fxsync"
	"newnavnav/internal/hkconnectfx"
	"newnavnav/internal/ingest/eastmoney"
	"newnavnav/internal/ingest/safe"
	"newnavnav/internal/ingest/sina"
	"newnavnav/internal/monitoring"
	"newnavnav/internal/navsync"
	"newnavnav/internal/privatevaluation"
	"newnavnav/internal/purchasesync"
	"newnavnav/internal/ratiofit"
	"newnavnav/internal/runtimestatus"
	"newnavnav/internal/sharesync"
	"newnavnav/internal/snapshot"
	"newnavnav/internal/web"
)

func main() {
	cfg := config.Load()
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))
	slog.SetDefault(logger)
	if cfg.UploadToken == "" {
		logger.Warn("UPLOAD_TOKEN not set; upload endpoints are publicly writable")
	}
	if cfg.IntradayRebuildAgentToken == "" {
		logger.Warn("INTRADAY_REBUILD_AGENT_TOKEN not set; intraday rebuild agent websocket is disabled")
	}
	if cfg.DataViewToken == "" {
		logger.Warn("DATA_VIEW_TOKEN not set; public data routes are unlocked")
	}
	if cfg.NavSettingsToken == "" {
		logger.Warn("NAV_SETTINGS_TOKEN not set; nav settings routes are unlocked")
	}
	if cfg.MessageAdminToken == "" {
		logger.Warn("MESSAGE_ADMIN_TOKEN not set; contact message inbox routes stay disabled")
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	var repository *db.Repository
	var privateValuationRepository privatevaluation.Repository
	if cfg.MySQLDSN != "" {
		if database, err := db.Open(ctx, cfg.MySQLDSN); err != nil {
			if os.Getenv("MACHOME_REQUIRE_MYSQL") == "1" {
				logger.Error("required mysql unavailable; refusing degraded startup")
				os.Exit(2)
			}
			logger.Warn("mysql unavailable; continuing with in-memory snapshots", "error", err)
		} else {
			defer database.Close()
			repository = db.NewRepository(database)
			if err := repository.EnsurePrivateValuationSchema(ctx); err != nil {
				logger.Warn("ensure private valuation schema failed; private persistence disabled", "error", err)
			} else {
				privateValuationRepository = repository
			}
			if err := repository.EnsureEffectiveRatioFitHistorySchema(ctx); err != nil {
				logger.Warn("ensure effective ratio fit history schema failed", "error", err)
			}
			if err := repository.EnsureManualValuationPositionOverrideSchema(ctx); err != nil {
				logger.Warn("ensure manual valuation position override schema failed", "error", err)
			}
			logger.Info("mysql connected")
		}
	} else {
		logger.Info("MYSQL_DSN not set; running without database persistence")
	}
	var visitRepository web.VisitRepository
	if repository != nil {
		visitRepository = web.NewBufferedVisitRepository(repository, ctx, cfg.VisitFlushInterval)
	}
	var valuationRepository snapshot.ValuationRepository
	if repository != nil {
		valuationRepository = repository
	}
	var fxParitySync *fxsync.Service
	if cfg.EnableFXParity && repository != nil {
		fxClient := safe.NewClient(cfg.FXParityTimeout)
		fxParitySync = fxsync.NewService(fxsync.Options{
			Client:       fxClient,
			Repository:   repository,
			Logger:       logger,
			LookbackDays: cfg.FXParityLookback,
		})
	}

	sinaClient := sina.NewClient(cfg.SinaTimeout)
	shareHistoryStore := sharesync.NewCSVStore(cfg.ShareHistoryDir)
	minuteHistoryStore := snapshot.NewMinuteHistoryStore(cfg.MinuteHistoryDir, cfg.MinuteHistoryRetentionDays)
	shareHistoryStore.SetClosePremiumLookup(closepremium.ShareHistoryLookup(minuteHistoryStore))
	snapshotService := snapshot.NewService(snapshot.ServiceOptions{
		Sina:                       sinaClient,
		Repository:                 valuationRepository,
		ShareHistory:               shareHistoryStore,
		SnapshotDataDir:            cfg.SnapshotDataDir,
		SnapshotHTMLDir:            cfg.SnapshotHTMLDir,
		MinuteHistoryDir:           cfg.MinuteHistoryDir,
		MinuteHistoryRetentionDays: cfg.MinuteHistoryRetentionDays,
		RefreshInterval:            cfg.RefreshInterval,
		EnableSina:                 cfg.EnableSina,
		EnableUploadQuotes:         cfg.EnableUploadQuotes,
	})
	runtimeTracker, trackerErr := runtimestatus.NewPersistentTracker(filepath.Join(cfg.SnapshotDataDir, "runtime-tasks"))
	if trackerErr != nil {
		logger.Error("cannot initialize durable task status", "error", trackerErr)
		os.Exit(1)
	}
	var dailyCloseSync *closesync.Service
	runtimeStatuses := func() []runtimestatus.Status {
		statuses := runtimeTracker.Statuses()
		if dailyCloseSync != nil {
			statuses = append(statuses, dailyCloseSync.Statuses()...)
		}
		return statuses
	}
	snapshotService.SetRuntimeStatusProvider(func() any {
		return runtimeStatuses()
	})

	var fxParityRunner *runtimestatus.TrackedDailySyncer
	if fxParitySync != nil {
		fxParityRunner = runtimeTracker.Register("fx_central_parity", "SAFE 外汇中间价", cfg.FXParityTime).Wrap(fxParitySync)
		if err := fxParityRunner.SyncMissing(ctx, time.Now()); err != nil {
			logger.Warn("initial safe central parity sync finished with error", "error", err)
		}
	} else if cfg.EnableFXParity {
		runtimeTracker.RegisterDisabled("fx_central_parity", "SAFE 外汇中间价", cfg.FXParityTime, "mysql repository is unavailable")
	} else {
		runtimeTracker.RegisterDisabled("fx_central_parity", "SAFE 外汇中间价", cfg.FXParityTime, "ENABLE_FX_CENTRAL_PARITY=false")
	}
	if err := snapshotService.WarmLatestQuotes(ctx); err != nil {
		logger.Warn("warm latest quote fallback failed", "error", err)
	}
	if err := snapshotService.WarmPurchaseInfos(ctx); err != nil {
		logger.Warn("warm purchase info fallback failed", "error", err)
	}
	if err := snapshotService.Refresh(ctx); err != nil {
		logger.Warn("initial snapshot refresh failed; demo fallback will be used", "error", err)
	}
	go snapshotService.Run(ctx)
	privateValuationService := privatevaluation.NewService(privateValuationRepository, snapshotService)
	if err := privateValuationService.Warm(ctx); err != nil {
		logger.Warn("warm private valuation state failed", "error", err)
	}
	go privateValuationService.Run(ctx, cfg.RefreshInterval)
	hkConnectFXDataDir := strings.TrimSpace(os.Getenv("HK_CONNECT_FX_DATA_DIR"))
	if hkConnectFXDataDir == "" {
		hkConnectFXDataDir = filepath.Join(cfg.SnapshotDataDir, "hk_connect_fx")
	}
	hkConnectFXService := hkconnectfx.NewService(hkconnectfx.Options{
		DataDir: hkConnectFXDataDir,
		Logger:  logger,
	})
	go hkConnectFXService.Run(ctx)

	purchaseStatusSync := purchasesync.NewService(purchasesync.Options{
		Client:        purchasesync.NewClient(15 * time.Second),
		Applier:       snapshotService,
		Symbols:       domain.AllSymbols(),
		Logger:        logger,
		QueryInterval: 10 * time.Second,
	})
	purchaseStatusRunner := runtimeTracker.Register("purchase_status", "东方财富申购状态", cfg.PurchaseStatusTime).Wrap(purchaseStatusSync)
	if shouldRunStartupPurchaseStatusSync(time.Now(), snapshotService.CurrentPurchaseInfos(), cfg.PurchaseStatusTime) {
		go func() {
			if err := purchaseStatusRunner.SyncMissing(ctx, time.Now()); err != nil {
				logger.Warn("startup purchase status sync finished with error", "error", err)
			}
		}()
	} else {
		logger.Info("startup purchase status sync skipped; cached data is usable or daily run is pending")
	}
	go navsync.RunDailyNamed(ctx, purchaseStatusRunner, cfg.PurchaseStatusTime, logger, "purchase status", 8, 5)

	if cfg.EnableDailyClose && repository != nil {
		dailyCloseSync = closesync.NewService(closesync.Options{
			Quotes:     snapshotService,
			Repository: repository,
			Logger:     logger,
		})
		dailyCloseSync.Run(ctx)
	} else if cfg.EnableDailyClose {
		runtimeTracker.RegisterDisabled("daily_close", "每日收盘价", "", "mysql repository is unavailable")
		logger.Warn("daily close persistence disabled because mysql repository is unavailable")
	} else {
		runtimeTracker.RegisterDisabled("daily_close", "每日收盘价", "", "ENABLE_DAILY_CLOSE_PRICES=false")
	}

	if fxParitySync != nil {
		go navsync.RunDailyNamed(ctx, fxParityRunner, cfg.FXParityTime, logger, "safe central parity", 9, 30)
		go fxParitySync.RunTodayEnsureLoop(ctx)
	} else if cfg.EnableFXParity {
		logger.Warn("safe central parity sync disabled because mysql repository is unavailable")
	}

	if cfg.EnableEastmoneyNAV && repository != nil {
		eastmoneyClient := eastmoney.NewClient(cfg.EastmoneyTimeout)
		eastmoneySync := navsync.NewEastmoneyService(navsync.EastmoneyOptions{
			Client:       eastmoneyClient,
			Repository:   repository,
			Symbols:      domain.AllSymbols(),
			LookbackDays: cfg.EastmoneyLookback,
			Delay:        cfg.EastmoneyDelay,
			Logger:       logger,
		})
		eastmoneyRunner := runtimeTracker.Register("eastmoney_nav", "东方财富每日净值", cfg.EastmoneyNAVTime).Wrap(eastmoneySync)
		snapshotService.SetNAVSyncStatusProvider(func() any {
			return eastmoneySync.Status()
		})
		go func() {
			if err := eastmoneyRunner.SyncMissing(ctx, time.Now()); err != nil {
				logger.Warn("initial eastmoney nav sync finished with error", "error", err)
			}
		}()
		go navsync.RunDaily(ctx, eastmoneyRunner, cfg.EastmoneyNAVTime, logger)
	} else if cfg.EnableEastmoneyNAV {
		logger.Warn("eastmoney nav sync disabled because mysql repository is unavailable")
		runtimeTracker.RegisterDisabled("eastmoney_nav", "东方财富每日净值", cfg.EastmoneyNAVTime, "mysql repository is unavailable")
		snapshotService.SetNAVSyncStatusProvider(func() any {
			return navsync.DisabledStatus("mysql repository is unavailable")
		})
	} else {
		runtimeTracker.RegisterDisabled("eastmoney_nav", "东方财富每日净值", cfg.EastmoneyNAVTime, "ENABLE_EASTMONEY_NAV=false")
		snapshotService.SetNAVSyncStatusProvider(func() any {
			return navsync.DisabledStatus("ENABLE_EASTMONEY_NAV=false")
		})
	}

	if cfg.EnableCalibration && repository != nil {
		calibrationSync := calsync.NewService(calsync.Options{
			Repository: repository,
			Logger:     logger,
		})
		calibrationRunner := runtimeTracker.Register("daily_calibration", "每日校准", cfg.CalibrationTime).Wrap(calibrationSync)
		go navsync.RunDailyNamed(ctx, calibrationRunner, cfg.CalibrationTime, logger, "daily calibration", 22, 45)
	} else if cfg.EnableCalibration {
		runtimeTracker.RegisterDisabled("daily_calibration", "每日校准", cfg.CalibrationTime, "mysql repository is unavailable")
		logger.Warn("daily calibration sync disabled because mysql repository is unavailable")
	} else {
		runtimeTracker.RegisterDisabled("daily_calibration", "每日校准", cfg.CalibrationTime, "ENABLE_DAILY_CALIBRATION=false")
	}

	if cfg.EnableEffectiveRatioFitHistory && repository != nil {
		effectiveRatioFitSync := ratiofit.NewService(ratiofit.Options{
			Repository:         repository,
			ClosePremiumLookup: ratiofit.MinuteHistoryClosePremiumLookup{Store: minuteHistoryStore},
			Logger:             logger,
			WindowSize:         cfg.EffectiveRatioFitWindowSize,
			BaseLagTradingDays: 2,
			LookbackDays:       cfg.EffectiveRatioFitLookbackDays,
		})
		effectiveRatioFitRunner := runtimeTracker.Register("effective_ratio_fit", "有效仓位拟合", cfg.EffectiveRatioFitTime).Wrap(effectiveRatioFitSync)
		go func() {
			if err := effectiveRatioFitRunner.SyncMissing(ctx, time.Now()); err != nil {
				logger.Warn("initial effective ratio fit sync finished with error", "error", err)
			}
		}()
		go navsync.RunDailyNamed(ctx, effectiveRatioFitRunner, cfg.EffectiveRatioFitTime, logger, "effective ratio fit", 23, 0)
	} else if cfg.EnableEffectiveRatioFitHistory {
		runtimeTracker.RegisterDisabled("effective_ratio_fit", "有效仓位拟合", cfg.EffectiveRatioFitTime, "mysql repository is unavailable")
		logger.Warn("effective ratio fit sync disabled because mysql repository is unavailable")
	} else {
		runtimeTracker.RegisterDisabled("effective_ratio_fit", "有效仓位拟合", cfg.EffectiveRatioFitTime, "ENABLE_EFFECTIVE_RATIO_FIT_HISTORY=false")
	}

	if cfg.EnableSZSEShareHistory {
		szseShareHistorySync := sharesync.NewService(sharesync.Options{
			Client:              sharesync.NewClient(cfg.SZSEShareHistoryTimeout),
			Repository:          shareHistoryStore,
			Symbols:             domain.ShareHistorySymbols(),
			Logger:              logger,
			QueryInterval:       cfg.SZSEShareHistoryQueryInterval,
			InitialLookbackDays: cfg.SZSEShareHistoryLookbackDays,
		})
		szseShareHistoryRunner := runtimeTracker.Register("szse_share_history", "深交所份额历史", cfg.SZSEShareHistoryTime).Wrap(szseShareHistorySync)
		go func() {
			if err := szseShareHistoryRunner.SyncMissing(ctx, time.Now()); err != nil {
				logger.Warn("initial szse share history sync finished with error", "error", err)
			}
		}()
		go navsync.RunDailyNamed(ctx, szseShareHistoryRunner, cfg.SZSEShareHistoryTime, logger, "szse share history", 23, 50)
	} else {
		runtimeTracker.RegisterDisabled("szse_share_history", "深交所份额历史", cfg.SZSEShareHistoryTime, "ENABLE_SZSE_SHARE_HISTORY=false")
	}

	if cfg.EnableSSEShareHistory {
		sseShareHistorySync := sharesync.NewSSEService(sharesync.SSEOptions{
			Client:              sharesync.NewSSEClient(cfg.SSEShareHistoryTimeout),
			Repository:          shareHistoryStore,
			Symbols:             domain.ShareHistorySymbols(),
			Logger:              logger,
			QueryInterval:       cfg.SSEShareHistoryQueryInterval,
			InitialLookbackDays: cfg.SSEShareHistoryLookbackDays,
		})
		sseShareHistoryRunner := runtimeTracker.Register("sse_share_history", "上交所份额历史", cfg.SSEShareHistoryTime).Wrap(sseShareHistorySync)
		go func() {
			if err := sseShareHistoryRunner.SyncMissing(ctx, time.Now()); err != nil {
				logger.Warn("initial sse share history sync finished with error", "error", err)
			}
		}()
		go navsync.RunDailyNamed(ctx, sseShareHistoryRunner, cfg.SSEShareHistoryTime, logger, "sse share history", 23, 50)
	} else {
		runtimeTracker.RegisterDisabled("sse_share_history", "上交所份额历史", cfg.SSEShareHistoryTime, "ENABLE_SSE_SHARE_HISTORY=false")
	}

	monitorConfig := monitoring.BackendConfigFromEnv(cfg.SnapshotDataDir)
	if monitorConfig.Enabled {
		backendMonitor := monitoring.NewBackendRunner(monitorConfig, monitoring.BackendSources{
			DatabaseAvailable: repository != nil,
			Snapshot:          snapshotService.DebugStatus,
			Runtime:           runtimeStatuses,
			Private: func() []privatevaluation.Snapshot {
				definitions := privatevaluation.Definitions()
				funds := make([]privatevaluation.Snapshot, 0, len(definitions))
				for _, definition := range definitions {
					fund, _ := privateValuationService.Fund(definition.Symbol)
					funds = append(funds, fund)
				}
				return funds
			},
			HKConnectFX: hkConnectFXService.Snapshot,
		}, logger)
		go backendMonitor.Run(ctx)
	}

	handler := web.NewServer(snapshotService, cfg.FrontendDist, cfg.UploadToken)
	if visitRepository != nil {
		handler = web.NewServer(snapshotService, cfg.FrontendDist, cfg.UploadToken, visitRepository)
	}
	if repository != nil {
		handler.SetContactMessageRepository(repository)
	}
	handler.SetPrivateValuationService(privateValuationService)
	handler.SetHKConnectFXService(hkConnectFXService)
	handler.SetIntradayRebuildAgentToken(cfg.IntradayRebuildAgentToken)
	if err := handler.SetIntradayRebuildStorePath(filepath.Join(cfg.SnapshotDataDir, "intraday_rebuild_jobs.json")); err != nil {
		logger.Warn("intraday rebuild job persistence unavailable; continuing in memory", "error", err)
	}
	handler.SetDataViewToken(cfg.DataViewToken)
	handler.SetNavSettingsToken(cfg.NavSettingsToken)
	handler.SetMessageAdminToken(cfg.MessageAdminToken)
	server := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		logger.Info("server listening", "addr", cfg.HTTPAddr)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server stopped unexpectedly", "error", err)
			stop()
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		logger.Error("server shutdown failed", "error", err)
	}
}

func shouldRunStartupPurchaseStatusSync(now time.Time, infos map[string]domain.PurchaseInfo, clockTime string) bool {
	if now.IsZero() {
		now = time.Now()
	}
	if purchaseInfosFreshToday(now, infos) {
		return false
	}
	year, month, day := now.Date()
	hour, minute := purchaseStatusClock(clockTime)
	scheduled := time.Date(year, month, day, hour, minute, 0, 0, now.Location())
	return !now.Before(scheduled)
}

func purchaseStatusClock(clockTime string) (int, int) {
	parsed, err := time.Parse("15:04", clockTime)
	if err != nil {
		return 8, 5
	}
	return parsed.Hour(), parsed.Minute()
}

func purchaseInfosFreshToday(now time.Time, infos map[string]domain.PurchaseInfo) bool {
	if len(infos) == 0 {
		return false
	}
	year, month, day := now.Date()
	for _, info := range infos {
		if info.FetchedAt.IsZero() {
			continue
		}
		fetched := info.FetchedAt.In(now.Location())
		fy, fm, fd := fetched.Date()
		if fy == year && fm == month && fd == day {
			return true
		}
	}
	return false
}
