package monitoring

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/hkconnectfx"
	"newnavnav/internal/privatevaluation"
	"newnavnav/internal/runtimestatus"
	"newnavnav/internal/snapshot"
)

const (
	defaultMonitorInterval  = 5 * time.Second
	privateFreshness        = 25 * time.Second
	publicQuoteFreshness    = 90 * time.Second
	publicUploadFreshness   = 20 * time.Second
	wsHeartbeatFreshness    = 75 * time.Second
	fxFreshness             = 10 * time.Minute // Notification tolerance only; does not change valuation freshness.
	silverInputFreshness    = 90 * time.Second
	silverObservedFreshness = 3 * time.Minute
	niftyPreopenFreshness   = 3 * time.Minute
	niftyOpenFreshness      = 90 * time.Second
)

type BackendConfig struct {
	Enabled          bool
	Token            string
	Endpoint         string
	Channel          string
	StatePath        string
	Interval         time.Duration
	StartupGrace     time.Duration
	RepeatInterval   time.Duration
	PreopenTime      string
	RealtimeTime     string
	PublicStartTime  string
	PrivateStartTime string
	SkipDates        map[string]bool
	ExpectedWSUpload string
	IgnoredPrivate   map[string]bool
	AllowedDelayedIB map[string]bool
}

type BackendSources struct {
	DatabaseAvailable bool
	Snapshot          func(bool) snapshot.DebugStatus
	Runtime           func() []runtimestatus.Status
	Private           func() []privatevaluation.Snapshot
	HKConnectFX       func() hkconnectfx.Snapshot
}

type BackendRunner struct {
	config   BackendConfig
	sources  BackendSources
	manager  *IncidentManager
	logger   *slog.Logger
	location *time.Location
	now      func() time.Time
}

type backendMetrics struct {
	PCFExpected       int
	PCFHealthy        int
	PrivateExpected   int
	PrivateHealthy    int
	DomesticExpected  int
	DomesticHealthy   int
	MaxPrivateAge     time.Duration
	MaxDomesticAge    time.Duration
	CFETSAge          time.Duration
	SnapshotAge       time.Duration
	FreshUploadSource string
}

type privateProblemBucket struct {
	key, name                   string
	missing, stale, pcf, ib, fx []string
}

func BackendConfigFromEnv(snapshotDataDir string) BackendConfig {
	token := strings.TrimSpace(os.Getenv("PUSHPLUS_TOKEN"))
	statePath := strings.TrimSpace(os.Getenv("PUSHPLUS_MONITOR_STATE_FILE"))
	if statePath == "" {
		statePath = filepath.Join(snapshotDataDir, "monitor", "pushplus_backend_state.json")
	}
	return BackendConfig{
		Enabled:          envBool("PUSHPLUS_MONITOR_ENABLED", token != ""),
		Token:            token,
		Endpoint:         envString("PUSHPLUS_ENDPOINT", defaultPushPlusEndpoint),
		Channel:          envString("PUSHPLUS_CHANNEL", "wechat"),
		StatePath:        statePath,
		Interval:         envDuration("PUSHPLUS_MONITOR_INTERVAL", defaultMonitorInterval),
		StartupGrace:     envDuration("PUSHPLUS_MONITOR_STARTUP_GRACE", 3*time.Minute),
		RepeatInterval:   envDuration("PUSHPLUS_MONITOR_REPEAT_INTERVAL", 6*time.Minute),
		PreopenTime:      envString("PUSHPLUS_PREOPEN_TIME", "09:14:30"),
		RealtimeTime:     envString("PUSHPLUS_REALTIME_TIME", "09:16:30"),
		PublicStartTime:  envString("PUSHPLUS_PUBLIC_MONITOR_START", "09:20:00"),
		PrivateStartTime: envString("PUSHPLUS_PRIVATE_MONITOR_START", "09:37:00"),
		SkipDates:        parseDateSet(os.Getenv("PUSHPLUS_MONITOR_SKIP_DATES")),
		ExpectedWSUpload: envString("PUSHPLUS_EXPECTED_UPLOAD_SOURCE", "home-mac"),
		IgnoredPrivate: parseStringSet(envString(
			"PUSHPLUS_IGNORED_PRIVATE_SYMBOLS",
			"SZ159605,SZ159607,SH513050,SH513220,SZ159501,SZ159513,SZ159632,SZ159659,SZ159660,SZ159696,SZ159941,SH513100,SH513110,SH513300,SH513390,SH513870,SZ159612,SZ159655,SH513500,SH513650",
		)),
		AllowedDelayedIB: parseStringSet(envString(
			"PUSHPLUS_ALLOWED_DELAYED_IB_SYMBOLS",
			"",
		)),
	}
}

func NewBackendRunner(config BackendConfig, sources BackendSources, logger *slog.Logger) *BackendRunner {
	if logger == nil {
		logger = slog.Default()
	}
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		location = time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	client := &PushPlusClient{
		Token:    config.Token,
		Endpoint: config.Endpoint,
		Channel:  config.Channel,
		Template: "markdown",
	}
	return &BackendRunner{
		config:   config,
		sources:  sources,
		manager:  NewIncidentManager(client, config.RepeatInterval, config.StatePath, logger),
		logger:   logger,
		location: location,
		now:      time.Now,
	}
}

func (r *BackendRunner) Run(ctx context.Context) {
	if r == nil || !r.config.Enabled {
		return
	}
	if strings.TrimSpace(r.config.Token) == "" {
		r.logger.Warn("pushplus backend monitoring disabled because token is empty")
		return
	}
	interval := r.config.Interval
	if interval < time.Second {
		interval = defaultMonitorInterval
	}
	startupGrace := r.config.StartupGrace
	if startupGrace < 0 {
		startupGrace = 0
	}
	r.logger.Info("pushplus backend monitoring started", "interval", interval, "repeat_interval", r.config.RepeatInterval, "startup_grace", startupGrace)
	if startupGrace > 0 {
		timer := time.NewTimer(startupGrace)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
	}
	r.tick(ctx, r.now().In(r.location))
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			r.tick(ctx, now.In(r.location))
		}
	}
}

func (r *BackendRunner) tick(ctx context.Context, now time.Time) {
	debug := snapshot.DebugStatus{}
	if r.sources.Snapshot != nil {
		debug = r.sources.Snapshot(true)
	}
	runtimeStatuses := []runtimestatus.Status(nil)
	if r.sources.Runtime != nil {
		runtimeStatuses = r.sources.Runtime()
	}
	coreProblems, metrics := r.coreProblems(now, debug, runtimeStatuses)
	r.manager.ReconcileScope(ctx, "backend.core.", coreProblems)

	if !r.isMonitorDay(now) {
		r.manager.PauseScope("backend.private.")
		r.manager.PauseScope("backend.public.")
		return
	}
	minute := secondOfDay(now)
	privateStart := parseClockSeconds(r.config.PrivateStartTime, 9*3600+37*60)
	if minute >= privateStart && isPrivateMonitorWindow(now) {
		privateSnapshots := []privatevaluation.Snapshot(nil)
		if r.sources.Private != nil {
			privateSnapshots = r.sources.Private()
		}
		hkfx := hkconnectfx.Snapshot{}
		if r.sources.HKConnectFX != nil {
			hkfx = r.sources.HKConnectFX()
		}
		privateProblems, privateMetrics := r.privateProblems(now, privateSnapshots, hkfx, true, true)
		mergeMetrics(&metrics, privateMetrics)
		var paused []string
		if !isSilverCollectionWindow(now) {
			paused = append(paused, "backend.private.missing.silver", "backend.private.stale.silver",
				"backend.private.ib.silver", "backend.private.fx.silver", "backend.private.pcf.silver")
		}
		r.manager.ReconcileScope(ctx, "backend.private.", privateProblems, paused...)
	} else {
		r.manager.PauseScope("backend.private.")
	}
	publicStart := parseClockSeconds(r.config.PublicStartTime, 9*3600+20*60)
	if minute >= publicStart && isDomesticPublicMonitorWindow(now) {
		publicProblems, publicMetrics := r.publicProblems(now, debug)
		mergeMetrics(&metrics, publicMetrics)
		r.manager.ReconcileScope(ctx, "backend.public.", publicProblems)
	} else {
		r.manager.PauseScope("backend.public.")
	}

	preopenAt := parseClockSeconds(r.config.PreopenTime, 9*3600+14*60+30)
	if minute >= preopenAt && minute < 9*3600+30*60 {
		problems, reportMetrics := r.preopenProblems(now, debug, runtimeStatuses)
		title := "【NAVNAV】盘前系统正常"
		if len(problems) > 0 {
			title = "【NAVNAV】盘前检查异常"
		}
		key := "backend-preopen-" + now.Format("2006-01-02")
		r.manager.SendOnce(ctx, key, title, preopenContent(now, problems, reportMetrics))
	}

	realtimeAt := parseClockSeconds(r.config.RealtimeTime, 9*3600+16*60+30)
	if minute >= realtimeAt && minute < 9*3600+30*60 {
		problems, reportMetrics := r.realtimeProblems(now, debug)
		title := "【NAVNAV】09:16 实时行情正常"
		if len(problems) > 0 {
			title = "【NAVNAV】09:16 实时行情异常"
		}
		key := "backend-realtime-" + now.Format("2006-01-02")
		r.manager.SendOnce(ctx, key, title, realtimeContent(now, problems, reportMetrics))
	}
}

func (r *BackendRunner) coreProblems(now time.Time, debug snapshot.DebugStatus, statuses []runtimestatus.Status) ([]Problem, backendMetrics) {
	var problems []Problem
	metrics := backendMetrics{}
	if !r.sources.DatabaseAvailable {
		problems = append(problems, Problem{Key: "backend.core.database", Title: "数据库不可用", Detail: "后端未连接 MySQL，当前只能使用内存快照", Severity: "critical"})
	}
	lastRefresh := valueTime(debug.SnapshotStatus["last_refresh_at"])
	if lastRefresh.IsZero() {
		problems = append(problems, Problem{Key: "backend.core.snapshot", Title: "快照尚未生成", Detail: "snapshot last_refresh_at 为空", Severity: "critical"})
	} else {
		metrics.SnapshotAge = positiveAge(now, lastRefresh)
		refreshInterval := valueFloat(debug.SnapshotStatus["refresh_interval_seconds"])
		limit := 20 * time.Second
		if candidate := time.Duration(refreshInterval*3) * time.Second; candidate > limit {
			limit = candidate
		}
		if metrics.SnapshotAge > limit {
			problems = append(problems, Problem{Key: "backend.core.snapshot", Title: "后端快照停止刷新", Detail: fmt.Sprintf("最近刷新距今 %s，阈值 %s", compactDuration(metrics.SnapshotAge), compactDuration(limit)), Severity: "critical"})
		}
	}
	if lastError := strings.TrimSpace(valueString(debug.SnapshotStatus["last_error"])); lastError != "" {
		problems = append(problems, Problem{Key: "backend.core.snapshot_source", Title: "后端数据源刷新失败", Detail: lastError, Severity: "warning"})
	}
	for _, status := range statuses {
		if !status.Enabled || status.Status != "error" {
			continue
		}
		problems = append(problems, Problem{
			Key:      "backend.core.task." + status.Key,
			Title:    status.Name + "任务失败",
			Detail:   strings.TrimSpace(status.LastError),
			Severity: "warning",
		})
	}
	return problems, metrics
}

func (r *BackendRunner) preopenProblems(now time.Time, debug snapshot.DebugStatus, statuses []runtimestatus.Status) ([]Problem, backendMetrics) {
	problems, metrics := r.coreProblems(now, debug, statuses)
	problems = append(problems, preopenTaskProblems(now, statuses)...)
	return dedupeProblems(problems), metrics
}

func (r *BackendRunner) realtimeProblems(now time.Time, debug snapshot.DebugStatus) ([]Problem, backendMetrics) {
	coreProblems, metrics := r.coreProblems(now, debug, nil)
	publicProblems, publicMetrics := r.publicProblems(now, debug)
	problems := append(coreProblems, publicProblems...)
	mergeMetrics(&metrics, publicMetrics)
	return dedupeProblems(problems), metrics
}

func (r *BackendRunner) privateProblems(now time.Time, funds []privatevaluation.Snapshot, hkfx hkconnectfx.Snapshot, requirePCF bool, realtime bool) ([]Problem, backendMetrics) {
	metrics := backendMetrics{}
	buckets := make(map[string]*privateProblemBucket)
	for _, fund := range funds {
		normalizedSymbol := strings.ToUpper(strings.TrimSpace(fund.Symbol))
		if r.config.IgnoredPrivate[normalizedSymbol] {
			continue
		}
		if isNikkei225PrivateSymbol(normalizedSymbol) && !isNikkei225FuturesMonitorWindow(now) {
			continue
		}
		if normalizedSymbol == "SZ161226" && !isSilverCollectionWindow(now) {
			continue
		}
		definition, hasDefinition := privatevaluation.Definition(fund.Symbol)
		pcfMode := hasDefinition && isPCFMode(definition.CalculationMode)
		if !realtime && !pcfMode {
			continue
		}
		groupKey, groupName := privateMonitorGroup(normalizedSymbol)
		bucket := buckets[groupKey]
		if bucket == nil {
			bucket = &privateProblemBucket{key: groupKey, name: groupName}
			buckets[groupKey] = bucket
		}
		metrics.PrivateExpected++
		if pcfMode {
			metrics.PCFExpected++
		}
		input := fund.Input
		if input == nil {
			bucket.missing = append(bucket.missing, fund.Symbol)
			continue
		}
		inputAge := positiveAge(now, input.GeneratedAt)
		if inputAge > metrics.MaxPrivateAge {
			metrics.MaxPrivateAge = inputAge
		}
		inputFreshness := privateFreshness
		if normalizedSymbol == "SZ161226" {
			inputFreshness = silverInputFreshness
		}
		if input.GeneratedAt.IsZero() || inputAge > inputFreshness {
			bucket.stale = append(bucket.stale, fmt.Sprintf("%s(%s)", fund.Symbol, compactDuration(inputAge)))
			continue
		}
		fundHealthy := true
		if pcfMode {
			if requirePCF && input.PCF.TradingDay != now.Format("2006-01-02") {
				bucket.pcf = append(bucket.pcf, fmt.Sprintf("%s(%s)", fund.Symbol, input.PCF.TradingDay))
				fundHealthy = false
			} else if input.PCF.TradingDay == now.Format("2006-01-02") {
				metrics.PCFHealthy++
			}
		}
		if reason := invalidLiveInput(now, input, r.config.AllowedDelayedIB[normalizedSymbol]); reason != "" {
			bucket.ib = append(bucket.ib, fund.Symbol+"("+reason+")")
			fundHealthy = false
		}
		if age, found := newestCurrentFXAge(now, input); found {
			if metrics.CFETSAge == 0 || age > metrics.CFETSAge {
				metrics.CFETSAge = age
			}
			if age > fxFreshness {
				bucket.fx = append(bucket.fx, fmt.Sprintf("%s(%s)", fund.Symbol, compactDuration(age)))
				fundHealthy = false
			}
		}
		if fundHealthy {
			metrics.PrivateHealthy++
		}
	}
	var problems []Problem
	groupKeys := make([]string, 0, len(buckets))
	for key := range buckets {
		groupKeys = append(groupKeys, key)
	}
	sort.Strings(groupKeys)
	for _, key := range groupKeys {
		bucket := buckets[key]
		suffix := "." + bucket.key
		appendGroupedProblem(&problems, "backend.private.missing"+suffix, bucket.name+"私有估值输入缺失", bucket.missing, "critical")
		appendGroupedProblem(&problems, "backend.private.stale"+suffix, bucket.name+"私有 upload 输入过期", bucket.stale, "critical")
		appendGroupedProblem(&problems, "backend.private.pcf"+suffix, bucket.name+"当日 PCF 不完整", bucket.pcf, "critical")
		appendGroupedProblem(&problems, "backend.private.ib"+suffix, bucket.name+"IB 行情异常", bucket.ib, "critical")
		appendGroupedProblem(&problems, "backend.private.fx"+suffix, bucket.name+"CFETS 汇率过期", bucket.fx, "critical")
	}

	if quote, ok := hkfx.FXQuotes["USD/CNY"]; ok {
		if quote.ObservedAt == nil || quote.ObservedAt.IsZero() {
			problems = append(problems, Problem{Key: "backend.private.hkfx_usdcny", Title: "后端 CFETS USD/CNY 不健康", Detail: strings.TrimSpace(quote.Error), Severity: "warning"})
		} else {
			// A failed poll retains the previous validated quote and ObservedAt.
			// Allow that cache for the same ten-minute notification tolerance as
			// uploader inputs, rather than alerting on one failed refresh.
			age := positiveAge(now, *quote.ObservedAt)
			if metrics.CFETSAge == 0 || age > metrics.CFETSAge {
				metrics.CFETSAge = age
			}
			if age > fxFreshness {
				detail := fmt.Sprintf("距今 %s", compactDuration(age))
				if !quote.Healthy && strings.TrimSpace(quote.Error) != "" {
					detail += "；最近刷新失败：" + strings.TrimSpace(quote.Error)
				}
				problems = append(problems, Problem{Key: "backend.private.hkfx_usdcny", Title: "后端 CFETS USD/CNY 过期", Detail: detail, Severity: "warning"})
			}
		}
	} else {
		problems = append(problems, Problem{Key: "backend.private.hkfx_usdcny", Title: "后端 CFETS USD/CNY 缺失", Detail: "USD/CNY 当前汇率尚未获取", Severity: "warning"})
	}
	return problems, metrics
}

func privateMonitorGroup(symbol string) (string, string) {
	switch symbol {
	case "SH513000", "SH513520", "SH513880", "SZ159866":
		return "nikkei225", "日经225 "
	case "SH513030", "SZ159561":
		return "germany", "德国 "
	case "SZ159518", "SH513350", "SZ162411":
		return "xop", "XOP "
	case "SZ164824":
		return "india", "印度 "
	case "SZ161226":
		return "silver", "白银 "
	default:
		return strings.ToLower(symbol), symbol + " "
	}
}

func (r *BackendRunner) publicProblems(now time.Time, debug snapshot.DebugStatus) ([]Problem, backendMetrics) {
	metrics := backendMetrics{}
	quoteFrozen := isDomesticAuctionFreeze(now) || isDomesticLunchMonitorPause(now)
	required := make(map[string]bool)
	for _, symbol := range debug.Required.Symbols {
		if isDomesticSymbol(symbol) {
			required[symbol] = true
		}
	}
	metrics.DomesticExpected = len(required)
	uploaded := make(map[string]bool)
	var stale []string
	for _, quote := range debug.UploadStatus.Quotes {
		if !required[quote.Symbol] {
			continue
		}
		uploaded[quote.Symbol] = true
		age := positiveAge(now, quote.FetchedAt)
		if age > metrics.MaxDomesticAge {
			metrics.MaxDomesticAge = age
		}
		if quote.QuoteDate != now.Format("2006-01-02") || !publicQuoteStatusHealthy(quote) || quote.FetchedAt.IsZero() || (!quoteFrozen && age > publicQuoteFreshness) {
			stale = append(stale, fmt.Sprintf("%s(%s/%s)", quote.Symbol, quote.QuoteDate, compactDuration(age)))
			continue
		}
		metrics.DomesticHealthy++
	}
	var missing []string
	for symbol := range required {
		if !uploaded[symbol] {
			missing = append(missing, symbol)
		}
	}
	sort.Strings(missing)
	var problems []Problem
	appendGroupedProblem(&problems, "backend.public.missing", "国内实时行情缺失", missing, "critical")
	appendGroupedProblem(&problems, "backend.public.stale", "国内实时行情过期", stale, "critical")

	wsHealthy := false
	for _, client := range debug.WSClients {
		if !client.Active || positiveAge(now, client.LastSeenAt) > wsHeartbeatFreshness {
			continue
		}
		if r.config.ExpectedWSUpload == "" || strings.Contains(strings.ToLower(client.Source), strings.ToLower(r.config.ExpectedWSUpload)) {
			wsHealthy = true
			break
		}
	}
	if !wsHealthy {
		problems = append(problems, Problem{Key: "backend.public.websocket", Title: "公共行情 WebSocket 离线", Detail: "没有发现新鲜的预期 upload WebSocket 客户端", Severity: "critical"})
	}
	freshSource := ""
	for _, source := range debug.UploadSources {
		if positiveAge(now, source.LastUploadAt) <= publicUploadFreshness {
			freshSource = source.Source
			break
		}
	}
	metrics.FreshUploadSource = freshSource
	if freshSource == "" && !isDomesticLunchMonitorPause(now) {
		problems = append(problems, Problem{Key: "backend.public.upload", Title: "公共行情上传停止", Detail: fmt.Sprintf("最近 %s 内没有成功上传", compactDuration(publicUploadFreshness)), Severity: "critical"})
	}
	return problems, metrics
}

func publicQuoteStatusHealthy(quote domain.Quote) bool {
	if quote.RealtimeStatus == "realtime" {
		return true
	}
	// UploadedQuoteStatus intentionally marks unchanged quotes stale after 30s
	// for request-time display fallback. The monitor has its own 90s SLA because
	// the uploader sends a full snapshot every 60s and only changed symbols in
	// between. Keep genuine upstream stale/unsupported statuses unhealthy.
	return quote.RealtimeStatus == "stale" && strings.HasPrefix(quote.StaleReason, "last uploaded quote older than ")
}

func isDomesticAuctionFreeze(now time.Time) bool {
	secondOfDay := now.Hour()*3600 + now.Minute()*60 + now.Second()
	return secondOfDay >= 9*3600+25*60 && secondOfDay < 9*3600+30*60
}

func isDomesticLunchMonitorPause(now time.Time) bool {
	second := secondOfDay(now)
	return second >= 11*3600+30*60 && second < 13*3600+60
}

func isDomesticPublicMonitorWindow(now time.Time) bool {
	return secondOfDay(now) < 14*3600+57*60 && !isDomesticLunchMonitorPause(now)
}

func isPrivateMonitorWindow(now time.Time) bool {
	return secondOfDay(now) < 15*3600
}

func isNikkei225FuturesMonitorWindow(now time.Time) bool {
	return secondOfDay(now) < 14*3600+40*60
}

func isNikkei225PrivateSymbol(symbol string) bool {
	switch strings.ToUpper(strings.TrimSpace(symbol)) {
	case "SZ159866", "SH513000", "SH513520", "SH513880":
		return true
	default:
		return false
	}
}

func isSilverCollectionWindow(now time.Time) bool {
	if now.Weekday() == time.Saturday || now.Weekday() == time.Sunday {
		return false
	}
	second := secondOfDay(now)
	return second >= 9*3600+15*60 && second < 10*3600+15*60 ||
		second >= 10*3600+31*60 && second < 11*3600+30*60 ||
		second >= 13*3600+31*60 && second < 15*3600
}

func preopenTaskProblems(now time.Time, statuses []runtimestatus.Status) []Problem {
	required := map[string]bool{
		"purchase_status":     true,
		"eastmoney_nav":       true,
		"daily_calibration":   true,
		"effective_ratio_fit": true,
		"szse_share_history":  true,
		"sse_share_history":   true,
	}
	seen := make(map[string]bool)
	var bad []string
	for _, status := range statuses {
		if !required[status.Key] {
			continue
		}
		seen[status.Key] = true
		if !status.Enabled {
			bad = append(bad, status.Key+"(disabled)")
			continue
		}
		if status.Running {
			bad = append(bad, status.Key+"(running)")
			continue
		}
		if !status.LastSuccess || status.LastFinishedAt == nil {
			bad = append(bad, status.Key+"(unverified)")
			continue
		}
		finished := status.LastFinishedAt.In(now.Location())
		if status.Key == "purchase_status" {
			if finished.Format("2006-01-02") != now.Format("2006-01-02") {
				bad = append(bad, status.Key+"("+finished.Format("01-02 15:04")+")")
			}
		} else if now.Sub(finished) > 40*time.Hour {
			bad = append(bad, status.Key+"("+finished.Format("01-02 15:04")+")")
		}
	}
	for key := range required {
		if !seen[key] {
			bad = append(bad, key+"(missing)")
		}
	}
	if len(bad) == 0 {
		return nil
	}
	sort.Strings(bad)
	return []Problem{{Key: "backend.core.preopen_tasks", Title: "盘前定时任务未就绪", Detail: summarizeSymbols(bad), Severity: "critical"}}
}

func invalidLiveInput(now time.Time, input *privatevaluation.Input, allowDelayed bool) string {
	if input == nil {
		return "missing"
	}
	if input.IB.Symbol != "" {
		if reason := invalidIBQuote(now, input.IB, allowDelayed); reason != "" {
			return input.IB.Symbol + ":" + reason
		}
	}
	for _, quote := range input.MarketQuotes {
		if !strings.Contains(strings.ToUpper(quote.Source), "IBKR") {
			continue
		}
		at := quote.StreamCheckedAt
		if at.IsZero() {
			at = quote.ObservedAt
		}
		if !allowedMarketDataType(quote.MarketDataType, allowDelayed) {
			return quote.Symbol + ":" + quote.MarketDataType
		}
		if at.IsZero() || positiveAge(now, at) > privateFreshness {
			return quote.Symbol + ":stale"
		}
		if quote.Bid == nil || quote.Ask == nil || *quote.Bid <= 0 || *quote.Ask < *quote.Bid {
			return quote.Symbol + ":invalid-book"
		}
	}
	if input.India != nil && input.India.NiftyBridge != nil {
		quote := input.India.NiftyBridge.Nifty
		if reason := invalidIBQuoteWithFreshness(now, quote, allowDelayed, niftyQuoteFreshness(now)); reason != "" {
			return quote.Symbol + ":" + reason
		}
	}
	if input.Silver != nil {
		if input.Silver.ObservedAt.IsZero() || positiveAge(now, input.Silver.ObservedAt) > silverObservedFreshness {
			return "silver:stale"
		}
	}
	return ""
}

func invalidIBQuote(now time.Time, quote privatevaluation.IBQuoteInput, allowDelayed bool) string {
	return invalidIBQuoteWithFreshness(now, quote, allowDelayed, privateFreshness)
}

func invalidIBQuoteWithFreshness(now time.Time, quote privatevaluation.IBQuoteInput, allowDelayed bool, freshness time.Duration) string {
	if !allowedMarketDataType(quote.MarketDataType, allowDelayed) {
		return "market-data-" + strings.ToLower(quote.MarketDataType)
	}
	at := quote.StreamCheckedAt
	if at.IsZero() {
		at = quote.ObservedAt
	}
	if at.IsZero() || positiveAge(now, at) > freshness {
		return "stale"
	}
	if quote.Bid == nil || quote.Ask == nil || *quote.Bid <= 0 || *quote.Ask < *quote.Bid {
		return "invalid-book"
	}
	return ""
}

func niftyQuoteFreshness(now time.Time) time.Duration {
	// NSE cash trading begins at 09:15 IST, which is 11:45 in Shanghai.
	// The SGX/GIFT NIFTY book is materially less active before that open.
	if secondOfDay(now) < 11*3600+45*60 {
		return niftyPreopenFreshness
	}
	return niftyOpenFreshness
}

func allowedMarketDataType(value string, allowDelayed bool) bool {
	normalized := strings.ToLower(strings.TrimSpace(value))
	return normalized == "live" || (allowDelayed && strings.HasPrefix(normalized, "delayed"))
}

func newestCurrentFXAge(now time.Time, input *privatevaluation.Input) (time.Duration, bool) {
	if input == nil {
		return 0, false
	}
	values := []privatevaluation.FXInput{input.FX}
	values = append(values, input.FXRates...)
	if input.India != nil {
		values = append(values, input.India.CurrentFX)
	}
	if input.LOF != nil {
		values = append(values, input.LOF.CurrentFX)
	}
	var oldest time.Duration
	found := false
	for _, value := range values {
		if value.Rate == nil || value.FetchedAt.IsZero() {
			continue
		}
		if !strings.Contains(strings.ToUpper(value.Source), "CFETS") {
			continue
		}
		age := positiveAge(now, value.FetchedAt)
		if !found || age > oldest {
			oldest = age
		}
		found = true
	}
	return oldest, found
}

func isPCFMode(mode privatevaluation.CalculationMode) bool {
	switch mode {
	case privatevaluation.CalculationModeXOPProxy,
		privatevaluation.CalculationModeNQProxy,
		privatevaluation.CalculationModeESProxy,
		privatevaluation.CalculationModeN225MProxy,
		privatevaluation.CalculationModeDAXProxy,
		privatevaluation.CalculationModeFullCashSubstitutionPCF:
		return true
	default:
		return false
	}
}

func preopenContent(now time.Time, problems []Problem, metrics backendMetrics) string {
	status := "正常"
	if len(problems) > 0 {
		status = "异常"
	}
	lines := []string{
		"## NAVNAV 盘前检查：" + status,
		"",
		"- 检查时间：" + now.Format("2006-01-02 15:04:05 MST"),
		fmt.Sprintf("- 后端快照：%s", compactDuration(metrics.SnapshotAge)),
		"- 检查范围：后端快照与盘前定时任务",
	}
	return appendProblemLines(lines, problems)
}

func realtimeContent(now time.Time, problems []Problem, metrics backendMetrics) string {
	status := "正常"
	if len(problems) > 0 {
		status = "异常"
	}
	lines := []string{
		"## NAVNAV 实时行情首检：" + status,
		"",
		"- 检查时间：" + now.Format("2006-01-02 15:04:05 MST"),
		fmt.Sprintf("- 国内行情：%d/%d，最大年龄 %s", metrics.DomesticHealthy, metrics.DomesticExpected, compactDuration(metrics.MaxDomesticAge)),
		fmt.Sprintf("- 公共 upload 来源：%s", metrics.FreshUploadSource),
		fmt.Sprintf("- 后端快照：%s", compactDuration(metrics.SnapshotAge)),
	}
	return appendProblemLines(lines, problems)
}

func appendProblemLines(lines []string, problems []Problem) string {
	if len(problems) == 0 {
		lines = append(lines, "", "所有门禁条件均已通过。")
		return strings.Join(lines, "\n")
	}
	lines = append(lines, "", "### 未通过项目")
	for _, problem := range problems {
		lines = append(lines, "- "+problem.Title+"："+problem.Detail)
	}
	return strings.Join(lines, "\n")
}

func appendGroupedProblem(problems *[]Problem, key, title string, values []string, severity string) {
	if len(values) == 0 {
		return
	}
	sort.Strings(values)
	*problems = append(*problems, Problem{Key: key, Title: title, Detail: summarizeSymbols(values), Severity: severity})
}

func summarizeSymbols(values []string) string {
	if len(values) <= 12 {
		return strings.Join(values, ", ")
	}
	return strings.Join(values[:12], ", ") + fmt.Sprintf(" 等 %d 项", len(values))
}

func dedupeProblems(values []Problem) []Problem {
	seen := make(map[string]bool)
	out := make([]Problem, 0, len(values))
	for _, value := range values {
		if seen[value.Key] {
			continue
		}
		seen[value.Key] = true
		out = append(out, value)
	}
	return out
}

func mergeMetrics(target *backendMetrics, value backendMetrics) {
	if value.PCFExpected > 0 {
		target.PCFExpected = value.PCFExpected
		target.PCFHealthy = value.PCFHealthy
	}
	if value.PrivateExpected > 0 {
		target.PrivateExpected = value.PrivateExpected
		target.PrivateHealthy = value.PrivateHealthy
	}
	if value.DomesticExpected > 0 {
		target.DomesticExpected = value.DomesticExpected
		target.DomesticHealthy = value.DomesticHealthy
	}
	if value.MaxPrivateAge > target.MaxPrivateAge {
		target.MaxPrivateAge = value.MaxPrivateAge
	}
	if value.MaxDomesticAge > target.MaxDomesticAge {
		target.MaxDomesticAge = value.MaxDomesticAge
	}
	if value.CFETSAge > target.CFETSAge {
		target.CFETSAge = value.CFETSAge
	}
	if value.SnapshotAge > target.SnapshotAge {
		target.SnapshotAge = value.SnapshotAge
	}
	if value.FreshUploadSource != "" {
		target.FreshUploadSource = value.FreshUploadSource
	}
}

func (r *BackendRunner) isMonitorDay(now time.Time) bool {
	if now.Weekday() == time.Saturday || now.Weekday() == time.Sunday {
		return false
	}
	return !r.config.SkipDates[now.Format("2006-01-02")]
}

func isDomesticSymbol(symbol string) bool {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	return len(symbol) == 8 && (strings.HasPrefix(symbol, "SH") || strings.HasPrefix(symbol, "SZ") || strings.HasPrefix(symbol, "BJ"))
}

func positiveAge(now, observed time.Time) time.Duration {
	if observed.IsZero() {
		return 365 * 24 * time.Hour
	}
	age := now.Sub(observed.In(now.Location()))
	if age < 0 {
		return 0
	}
	return age
}

func compactDuration(value time.Duration) string {
	if value <= 0 {
		return "0s"
	}
	return value.Round(time.Second).String()
}

func secondOfDay(value time.Time) int {
	return value.Hour()*3600 + value.Minute()*60 + value.Second()
}

func parseClockSeconds(value string, fallback int) int {
	for _, layout := range []string{"15:04:05", "15:04"} {
		parsed, err := time.Parse(layout, strings.TrimSpace(value))
		if err == nil {
			return parsed.Hour()*3600 + parsed.Minute()*60 + parsed.Second()
		}
	}
	return fallback
}

func valueTime(value any) time.Time {
	switch typed := value.(type) {
	case time.Time:
		return typed
	case *time.Time:
		if typed != nil {
			return *typed
		}
	case string:
		parsed, _ := time.Parse(time.RFC3339Nano, typed)
		return parsed
	}
	return time.Time{}
}

func valueFloat(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case int:
		return float64(typed)
	case int64:
		return float64(typed)
	}
	return 0
}

func valueString(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}

func parseDateSet(value string) map[string]bool {
	result := make(map[string]bool)
	for _, item := range strings.Split(strings.ReplaceAll(value, "，", ","), ",") {
		item = strings.TrimSpace(item)
		if _, err := time.Parse("2006-01-02", item); err == nil {
			result[item] = true
		}
	}
	return result
}

func parseStringSet(value string) map[string]bool {
	result := make(map[string]bool)
	for _, item := range strings.Split(strings.ReplaceAll(value, "，", ","), ",") {
		if normalized := strings.ToUpper(strings.TrimSpace(item)); normalized != "" {
			result[normalized] = true
		}
	}
	return result
}

func envString(key string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func envBool(key string, fallback bool) bool {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func envDuration(key string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil {
		return fallback
	}
	return parsed
}
