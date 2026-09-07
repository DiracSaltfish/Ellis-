package privatevaluation

import (
	"fmt"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

// SilverAGContractForDate selects the nearest even-month SHFE AG contract and
// rolls it on calendar day 10. This intentionally does not apply a basis
// adjustment: the new contract's own previous settlement becomes the divisor.
func SilverAGContractForDate(value time.Time) string {
	local := value.In(shanghaiLocation)
	year, month := local.Year(), int(local.Month())
	if month%2 != 0 {
		month++
	} else if local.Day() >= 10 {
		month += 2
	}
	if month > 12 {
		month -= 12
		year++
	}
	return fmt.Sprintf("AG%02d%02d", year%100, month)
}

func (input Input) validateSilverSettlement(definition FundDefinition) error {
	if definition.Symbol != SZ161226Symbol || input.Silver == nil {
		return fmt.Errorf("silver settlement input is required")
	}
	if input.India != nil || input.LOF != nil || hasPCFInput(input.PCF) || hasFXInput(input.FX) ||
		len(input.FXRates) != 0 || len(input.MarketQuotes) != 0 || input.IB.Symbol != "" {
		return fmt.Errorf("silver settlement input must not contain PCF, FX or IB fields")
	}
	silver := input.Silver
	if !finitePositive(silver.BaseNAV) || silver.BaseNAVDate == "" || silver.BaseNAVSource == "" || silver.BaseNAVFetchedAt.IsZero() {
		return fmt.Errorf("silver base_nav, base_nav_date and source audit are required")
	}
	baseDate, err := time.ParseInLocation("2006-01-02", silver.BaseNAVDate, shanghaiLocation)
	if err != nil {
		return fmt.Errorf("invalid silver.base_nav_date: %w", err)
	}
	tradingDay, err := time.ParseInLocation("2006-01-02", silver.TradingDay, shanghaiLocation)
	if err != nil {
		return fmt.Errorf("invalid silver.trading_day: %w", err)
	}
	if tradingDay.Before(baseDate) {
		return fmt.Errorf("silver.trading_day must not precede silver.base_nav_date")
	}
	if silver.PreviousSettlementDate != silver.BaseNAVDate {
		return fmt.Errorf("silver.previous_settlement_date must equal silver.base_nav_date")
	}
	validPreviousSettlementSource := silver.PreviousSettlementSource == SilverPreviousSettlementSource ||
		silver.PreviousSettlementSource == SilverEastmoneyPreviousSettlementSource
	if !finitePositive(silver.PreviousSettlement) || !validPreviousSettlementSource {
		return fmt.Errorf("silver previous settlement must be positive and use a supported dated SHFE source")
	}
	if silver.ContractSelectionVersion != SilverContractSelectionVersion {
		return fmt.Errorf("silver.contract_selection_version must be %s", SilverContractSelectionVersion)
	}
	if expected := SilverAGContractForDate(tradingDay); silver.Contract != expected {
		return fmt.Errorf("silver.contract must be %s for %s", expected, silver.TradingDay)
	}
	if !finitePositive(silver.FuturesPrice) || !finitePositive(silver.IntradayAverage) {
		return fmt.Errorf("silver futures_price and intraday_average must be positive")
	}
	if silver.IntradayAverageBasis != SilverIntradayAverageBasis &&
		silver.IntradayAverageBasis != SilverOfficialSettlementBasis &&
		silver.IntradayAverageBasis != SilverEastmoneyIntradayAverageBasis {
		return fmt.Errorf("unsupported silver.intraday_average_basis %q", silver.IntradayAverageBasis)
	}
	if silver.ObservedAt.IsZero() || silver.ObservedAt.In(shanghaiLocation).Format("2006-01-02") != silver.TradingDay ||
		!IsMinuteHistoryTradingSessionForSymbol(SZ161226Symbol, silver.ObservedAt) {
		return fmt.Errorf("silver.observed_at must be within 09:15-11:30 or 13:00-15:00 Asia/Shanghai on trading_day")
	}
	if strings.TrimSpace(silver.Source) == "" || input.GeneratedAt.IsZero() || strings.TrimSpace(input.Source) == "" {
		return fmt.Errorf("silver source, generated_at and input source are required")
	}
	return nil
}

func calculateSilverSettlement(snapshot Snapshot, input Input, domestic domain.Quote, domesticBid, domesticAsk domain.Level, now time.Time) Snapshot {
	silver := input.Silver
	if silver == nil {
		snapshot.Warnings = append(snapshot.Warnings, "161226 白银结算输入不完整")
		return snapshot
	}
	settlementNAV := silver.BaseNAV / silver.PreviousSettlement * silver.IntradayAverage
	tradingNAV := silver.BaseNAV / silver.PreviousSettlement * silver.FuturesPrice
	if !finitePositive(settlementNAV) || !finitePositive(tradingNAV) {
		snapshot.Warnings = append(snapshot.Warnings, "161226 白银双估值计算结果无效")
		return snapshot
	}
	marketPrice := domestic.Price
	if !finitePositive(marketPrice) {
		marketPrice = (domesticBid.Price + domesticAsk.Price) / 2
	}
	valuation := BasketValuation{
		RedemptionUnit:           1,
		BasketBidCNY:             round(tradingNAV, 10),
		BasketAskCNY:             round(tradingNAV, 10),
		NAVBid:                   round(tradingNAV, 10),
		NAVAsk:                   round(tradingNAV, 10),
		BuyDirectionPremiumRate:  round(domesticAsk.Price/tradingNAV-1, 10),
		SellDirectionPremiumRate: round(domesticBid.Price/tradingNAV-1, 10),
		Formula:                  "昨日单位净值 ÷ 所选 AG 合约昨日结算价 × 今日盘中累计均价 / 当前交易价",
	}
	snapshot.Valuation = &valuation
	snapshot.SilverValuation = &SilverSettlementValuation{
		BaseNAV:                  round(silver.BaseNAV, 10),
		BaseNAVDate:              silver.BaseNAVDate,
		BaseNAVSource:            silver.BaseNAVSource,
		BaseNAVFetchedAt:         silver.BaseNAVFetchedAt,
		TradingDay:               silver.TradingDay,
		Contract:                 silver.Contract,
		ContractSelectionVersion: silver.ContractSelectionVersion,
		PreviousSettlement:       round(silver.PreviousSettlement, 6),
		PreviousSettlementDate:   silver.PreviousSettlementDate,
		PreviousSettlementSource: silver.PreviousSettlementSource,
		FuturesPrice:             round(silver.FuturesPrice, 6),
		IntradayAverage:          round(silver.IntradayAverage, 6),
		IntradayAverageBasis:     silver.IntradayAverageBasis,
		ObservedAt:               silver.ObservedAt,
		Source:                   silver.Source,
		SettlementNAV:            round(settlementNAV, 10),
		TradingNAV:               round(tradingNAV, 10),
		SettlementPremiumRate:    round(marketPrice/settlementNAV-1, 10),
		TradingPremiumRate:       round(marketPrice/tradingNAV-1, 10),
	}
	snapshot.OrderBook = buildOrderBook(domestic, tradingNAV, tradingNAV)
	snapshot.Ready = true
	snapshot.Actionable = appendSilverSettlementFreshnessWarnings(&snapshot, input, domestic, now)
	return snapshot
}

func appendSilverSettlementFreshnessWarnings(snapshot *Snapshot, input Input, domestic domain.Quote, now time.Time) bool {
	silver := input.Silver
	if silver == nil {
		return false
	}
	localNow := now.In(shanghaiLocation)
	if !IsMinuteHistoryTradingSessionForSymbol(SZ161226Symbol, localNow) {
		snapshot.Warnings = append(snapshot.Warnings, "当前不在白银基金共同交易时段（工作日 09:15-11:30、13:00-15:00；不含夜盘）")
	}
	today := localNow.Format("2006-01-02")
	if silver.TradingDay != today {
		snapshot.Warnings = append(snapshot.Warnings, fmt.Sprintf("AG 行情交易日为 %s，并非当前交易日 %s", silver.TradingDay, today))
	}
	if input.GeneratedAt.IsZero() || now.Sub(input.GeneratedAt) > 30*time.Second || input.GeneratedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "161226 东财 SSE AG 采集心跳超过 30 秒未更新")
	}
	if domestic.FetchedAt.IsZero() || now.Sub(domestic.FetchedAt) > 90*time.Second || domestic.FetchedAt.Sub(now) > 30*time.Second {
		snapshot.Warnings = append(snapshot.Warnings, "公共 Sina 161226 行情超过 90 秒未更新")
	}
	// This is an indicative LOF estimate, not an order-routing signal. Keep the
	// status non-actionable even when every source is healthy.
	snapshot.Warnings = append(snapshot.Warnings, "结算口径使用东财 SSE 盘中累计成交均价近似当日上期所结算价；两个口径均为指示性估值")
	return false
}
