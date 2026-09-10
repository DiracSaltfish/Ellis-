package config

import (
	"log/slog"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	HTTPAddr                       string
	MySQLDSN                       string
	SnapshotDataDir                string
	SnapshotHTMLDir                string
	MinuteHistoryDir               string
	ShareHistoryDir                string
	MinuteHistoryRetentionDays     int
	FrontendDist                   string
	RefreshInterval                time.Duration
	VisitFlushInterval             time.Duration
	SinaTimeout                    time.Duration
	EnableSina                     bool
	EnableUploadQuotes             bool
	UploadToken                    string
	IntradayRebuildAgentToken      string
	DataViewToken                  string
	NavSettingsToken               string
	MessageAdminToken              string
	EnableEastmoneyNAV             bool
	EastmoneyNAVTime               string
	EastmoneyTimeout               time.Duration
	EastmoneyLookback              int
	EastmoneyDelay                 time.Duration
	EnableFXParity                 bool
	FXParityTime                   string
	FXParityTimeout                time.Duration
	FXParityLookback               int
	EnableDailyClose               bool
	EnableCalibration              bool
	CalibrationTime                string
	EnableEffectiveRatioFitHistory bool
	EffectiveRatioFitTime          string
	PurchaseStatusTime             string
	EffectiveRatioFitLookbackDays  int
	EffectiveRatioFitWindowSize    int
	EnableSZSEShareHistory         bool
	SZSEShareHistoryTime           string
	SZSEShareHistoryTimeout        time.Duration
	SZSEShareHistoryQueryInterval  time.Duration
	SZSEShareHistoryLookbackDays   int
	EnableSSEShareHistory          bool
	SSEShareHistoryTime            string
	SSEShareHistoryTimeout         time.Duration
	SSEShareHistoryQueryInterval   time.Duration
	SSEShareHistoryLookbackDays    int
	LogLevel                       slog.Level
}

func Load() Config {
	return Config{
		HTTPAddr:                       envString("HTTP_ADDR", "127.0.0.1:8080"),
		MySQLDSN:                       envString("MYSQL_DSN", ""),
		SnapshotDataDir:                envString("SNAPSHOT_DATA_DIR", "snapshots/data"),
		SnapshotHTMLDir:                envString("SNAPSHOT_HTML_DIR", "snapshots/html"),
		MinuteHistoryDir:               envString("MINUTE_HISTORY_DIR", "snapshots/minute_history"),
		ShareHistoryDir:                envString("SHARE_HISTORY_DIR", "snapshots/share_history"),
		MinuteHistoryRetentionDays:     envInt("MINUTE_HISTORY_RETENTION_DAYS", 45),
		FrontendDist:                   envString("FRONTEND_DIST", "frontend/dist"),
		RefreshInterval:                envDuration("REFRESH_INTERVAL", 5*time.Second),
		VisitFlushInterval:             envDuration("VISIT_FLUSH_INTERVAL", 30*time.Second),
		SinaTimeout:                    envDuration("SINA_TIMEOUT", 5*time.Second),
		EnableSina:                     envBool("ENABLE_SINA", false),
		EnableUploadQuotes:             envBool("ENABLE_UPLOAD_QUOTES", false),
		UploadToken:                    envString("UPLOAD_TOKEN", ""),
		IntradayRebuildAgentToken:      envString("INTRADAY_REBUILD_AGENT_TOKEN", ""),
		DataViewToken:                  envString("DATA_VIEW_TOKEN", ""),
		NavSettingsToken:               envString("NAV_SETTINGS_TOKEN", envString("DATA_VIEW_TOKEN", "")),
		MessageAdminToken:              envString("MESSAGE_ADMIN_TOKEN", envString("NAV_SETTINGS_TOKEN", envString("DATA_VIEW_TOKEN", ""))),
		EnableEastmoneyNAV:             envBool("ENABLE_EASTMONEY_NAV", true),
		EastmoneyNAVTime:               envString("EASTMONEY_NAV_SYNC_TIME", "22:30"),
		EastmoneyTimeout:               envDuration("EASTMONEY_TIMEOUT", 10*time.Second),
		EastmoneyLookback:              envInt("EASTMONEY_NAV_LOOKBACK_DAYS", 30),
		EastmoneyDelay:                 envDuration("EASTMONEY_NAV_DELAY", 5*time.Second),
		EnableFXParity:                 envBool("ENABLE_FX_CENTRAL_PARITY", true),
		FXParityTime:                   envString("FX_CENTRAL_PARITY_SYNC_TIME", "09:30"),
		FXParityTimeout:                envDuration("FX_CENTRAL_PARITY_TIMEOUT", 30*time.Second),
		FXParityLookback:               envInt("FX_CENTRAL_PARITY_LOOKBACK_DAYS", 90),
		EnableDailyClose:               envBool("ENABLE_DAILY_CLOSE_PRICES", true),
		EnableCalibration:              envBool("ENABLE_DAILY_CALIBRATION", true),
		CalibrationTime:                envString("DAILY_CALIBRATION_SYNC_TIME", "22:45"),
		EnableEffectiveRatioFitHistory: envBool("ENABLE_EFFECTIVE_RATIO_FIT_HISTORY", true),
		EffectiveRatioFitTime:          envString("EFFECTIVE_RATIO_FIT_SYNC_TIME", "23:00"),
		PurchaseStatusTime:             envString("PURCHASE_STATUS_SYNC_TIME", "08:05"),
		EffectiveRatioFitLookbackDays:  envInt("EFFECTIVE_RATIO_FIT_LOOKBACK_DAYS", 45),
		EffectiveRatioFitWindowSize:    envInt("EFFECTIVE_RATIO_FIT_WINDOW_SIZE", 5),
		EnableSZSEShareHistory:         envBool("ENABLE_SZSE_SHARE_HISTORY", true),
		SZSEShareHistoryTime:           envString("SZSE_SHARE_HISTORY_SYNC_TIME", "23:50"),
		SZSEShareHistoryTimeout:        envDuration("SZSE_SHARE_HISTORY_TIMEOUT", 15*time.Second),
		SZSEShareHistoryQueryInterval:  envDuration("SZSE_SHARE_HISTORY_QUERY_INTERVAL", 3*time.Second),
		SZSEShareHistoryLookbackDays:   envInt("SZSE_SHARE_HISTORY_LOOKBACK_DAYS", 60),
		EnableSSEShareHistory:          envBool("ENABLE_SSE_SHARE_HISTORY", true),
		SSEShareHistoryTime:            envString("SSE_SHARE_HISTORY_SYNC_TIME", "23:50"),
		SSEShareHistoryTimeout:         envDuration("SSE_SHARE_HISTORY_TIMEOUT", 20*time.Second),
		SSEShareHistoryQueryInterval:   envDuration("SSE_SHARE_HISTORY_QUERY_INTERVAL", 200*time.Millisecond),
		SSEShareHistoryLookbackDays:    envInt("SSE_SHARE_HISTORY_LOOKBACK_DAYS", 1),
		LogLevel:                       envLogLevel("LOG_LEVEL", slog.LevelInfo),
	}
}

func envString(key string, fallback string) string {
	val := strings.TrimSpace(os.Getenv(key))
	if val == "" {
		return fallback
	}
	return val
}

func envDuration(key string, fallback time.Duration) time.Duration {
	val := strings.TrimSpace(os.Getenv(key))
	if val == "" {
		return fallback
	}
	duration, err := time.ParseDuration(val)
	if err != nil {
		return fallback
	}
	return duration
}

func envBool(key string, fallback bool) bool {
	val := strings.TrimSpace(os.Getenv(key))
	if val == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(val)
	if err != nil {
		return fallback
	}
	return parsed
}

func envInt(key string, fallback int) int {
	val := strings.TrimSpace(os.Getenv(key))
	if val == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(val)
	if err != nil {
		return fallback
	}
	return parsed
}

func envLogLevel(key string, fallback slog.Level) slog.Level {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	case "info":
		return slog.LevelInfo
	default:
		return fallback
	}
}
