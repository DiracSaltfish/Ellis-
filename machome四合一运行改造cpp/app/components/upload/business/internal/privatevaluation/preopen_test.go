package privatevaluation

import (
	"encoding/json"
	"fmt"
	"newnavnav/internal/domain"
	"testing"
	"time"
)

func TestPreopenIndexFamiliesEnforceFreshInputsAndExpiry(t *testing.T) {
	now := time.Date(2026, 9, 7, 9, 25, 0, 0, shanghaiLocation)
	count := 0
	for _, def := range Definitions() {
		if !isIndexProxyMode(def.CalculationMode) {
			continue
		}
		count++
		t.Run(def.Symbol, func(t *testing.T) {
			input := validNQInput(now)
			switch def.CalculationMode {
			case CalculationModeN225MProxy:
				input = validN225MInput(now)
			case CalculationModeDAXProxy:
				input = validDAXInput(now)
			}
			input.Symbol, input.ModelVersion, input.IB.Symbol = def.Symbol, def.ModelVersion, def.ReferenceSymbol
			input.PCF.SecurityID = def.Symbol[2:]
			unit := def.ExpectedRedemptionUnit
			input.PCF.CreationRedemptionUnit = &unit
			if def.ExpectedSecurityComponentCount > 0 {
				c := input.PCF.Components[0]
				input.PCF.Components = nil
				for i := 0; i < def.ExpectedSecurityComponentCount; i++ {
					v := c
					v.Symbol = fmt.Sprintf("X%d", i)
					input.PCF.Components = append(input.PCF.Components, v)
				}
				input.PCF.ComponentCount = len(input.PCF.Components)
			}
			input.PCF.TradingDay, input.PCF.PreTradingDay = "2026-09-07", "2026-09-04"
			input.FX.TradingDay, input.FX.QuoteTime, input.FX.Source = "2026-09-04", "14:58", CFETSPreopenFallbackSource
			input.FX.SourceObservedAt = time.Date(2026, 9, 4, 14, 58, 0, 0, shanghaiLocation)
			input.FX.FetchedAt = input.FX.SourceObservedAt
			input.FX.FallbackReason = "CURRENT_DAY_CFETS_UNAVAILABLE"
			domestic := validQuotes(now)[TargetSymbol]
			domestic.Symbol = def.Symbol
			quotes := map[string]domain.Quote{def.Symbol: domestic}
			got := CalculateForSymbol(def.Symbol, &input, quotes, now)
			if !got.Ready || got.Valuation == nil || got.Actionable || got.CalculationState != "preopen_fx_fallback" {
				t.Fatalf("valid fallback: %+v warnings=%v", got, got.Warnings)
			}
			wire, err := json.Marshal(input)
			if err != nil {
				t.Fatal(err)
			}
			var restored Input
			if err = json.Unmarshal(wire, &restored); err != nil {
				t.Fatal(err)
			}
			if !restored.FX.SourceObservedAt.Equal(input.FX.SourceObservedAt) || restored.FX.FallbackReason != input.FX.FallbackReason {
				t.Fatal("JSON persistence lost provenance")
			}
			cached := expireCachedPreopen(got, now.Add(16*time.Second))
			if cached.Ready || cached.Valuation != nil || !got.Ready || got.Valuation == nil {
				t.Fatal("read expiry must clear stale result without mutating cached original")
			}
			for name, mutate := range map[string]func(*Input){
				"old pcf":                  func(v *Input) { v.PCF.TradingDay = "2026-09-04" },
				"wrong prior day":          func(v *Input) { v.PCF.PreTradingDay = "2026-09-05" },
				"old bid ask fresh stream": func(v *Input) { v.IB.ObservedAt = now.Add(-16 * time.Second); v.IB.StreamCheckedAt = now },
				"delayed":                  func(v *Input) { v.IB.MarketDataType = "Delayed" },
				"future quote":             func(v *Input) { v.IB.ObservedAt = now.Add(time.Second) },
				"old upload":               func(v *Input) { v.GeneratedAt = now.Add(-16 * time.Second) },
				"lost source time":         func(v *Input) { v.FX.SourceObservedAt = time.Time{} },
			} {
				t.Run(name, func(t *testing.T) {
					v := input
					mutate(&v)
					got := CalculateForSymbol(def.Symbol, &v, quotes, now)
					if got.Ready || got.Valuation != nil || got.Actionable {
						t.Fatalf("bad fallback displayed: %+v", got)
					}
				})
			}
			for _, minute := range []int{554, 575, 600} {
				at := time.Date(2026, 9, 7, minute/60, minute%60, 0, 0, shanghaiLocation)
				v := input
				v.GeneratedAt = at
				v.IB.ObservedAt = at
				if got := CalculateForSymbol(def.Symbol, &v, quotes, at); got.Ready || got.Valuation != nil {
					t.Fatalf("fallback leaked at %v", at)
				}
			}
			// Realtime must replace fallback and expire on SOURCE time, not a new fetch timestamp.
			input.FX.Source, input.FX.TradingDay, input.FX.QuoteTime = CFETSSpotRateSource, "2026-09-07", "09:25"
			input.FX.SourceObservedAt, input.FX.FetchedAt = now, now
			if got := CalculateForSymbol(def.Symbol, &input, quotes, now); !got.Ready || got.CalculationState != "realtime" || got.Actionable {
				t.Fatalf("realtime transition failed: %+v", got)
			}
			input.FX.SourceObservedAt = now.Add(-181 * time.Second)
			if got := CalculateForSymbol(def.Symbol, &input, quotes, now); got.Ready {
				t.Fatal("fetch time hid stale source")
			}
		})
	}
	if count != 22 {
		t.Fatalf("expected 22 index symbols, got %d", count)
	}
}

func TestPreopenRejectsNonIndexModels(t *testing.T) {
	now := time.Date(2026, 9, 7, 9, 25, 0, 0, shanghaiLocation)
	input := validInput(now)
	input.FX.Source = CFETSPreopenFallbackSource
	if input.Validate() == nil {
		t.Fatal("XOP must not accept preopen fallback")
	}
}
