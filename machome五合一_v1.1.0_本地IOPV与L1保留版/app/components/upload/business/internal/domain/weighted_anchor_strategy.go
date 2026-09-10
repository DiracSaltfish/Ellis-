package domain

import (
	"fmt"
	"strings"
	"time"
)

type WeightedAnchorPointRule struct {
	Key                  string
	Label                string
	Weight               float64
	Timezone             string
	LocalTime            string
	LocalDateOffsetDays  int
	CaptureWindowSeconds int
}

type WeightedAnchorStrategy struct {
	FundSymbol       string
	ReferenceSymbol  string
	Label            string
	FXPair           string
	InvestmentRatio  float64
	StaticRatioLabel string
	Points           []WeightedAnchorPointRule
}

var weightedAnchorStrategies = map[string]WeightedAnchorStrategy{
	"SH501300": {
		FundSymbol:       "SH501300",
		ReferenceSymbol:  "HF_ZN",
		Label:            "ZN",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.765,
		StaticRatioLabel: "0.2350",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513000": {
		FundSymbol:       "SH513000",
		ReferenceSymbol:  "HF_NK",
		Label:            "NK",
		FXPair:           "JPYCNY",
		InvestmentRatio:  0.99,
		StaticRatioLabel: "0.0100",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "jp_close",
				Label:                "日本收盘",
				Weight:               1,
				Timezone:             "Asia/Tokyo",
				LocalTime:            "15:30",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513520": {
		FundSymbol:       "SH513520",
		ReferenceSymbol:  "HF_NK",
		Label:            "NK",
		FXPair:           "JPYCNY",
		InvestmentRatio:  0.965,
		StaticRatioLabel: "0.0350",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "jp_close",
				Label:                "日本收盘",
				Weight:               1,
				Timezone:             "Asia/Tokyo",
				LocalTime:            "15:30",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513880": {
		FundSymbol:       "SH513880",
		ReferenceSymbol:  "HF_NK",
		Label:            "NK",
		FXPair:           "JPYCNY",
		InvestmentRatio:  0.955,
		StaticRatioLabel: "0.0450",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "jp_close",
				Label:                "日本收盘",
				Weight:               1,
				Timezone:             "Asia/Tokyo",
				LocalTime:            "15:30",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ159866": {
		FundSymbol:       "SZ159866",
		ReferenceSymbol:  "HF_NK",
		Label:            "NK",
		FXPair:           "JPYCNY",
		InvestmentRatio:  0.975,
		StaticRatioLabel: "0.0250",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "jp_close",
				Label:                "日本收盘",
				Weight:               1,
				Timezone:             "Asia/Tokyo",
				LocalTime:            "15:30",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513500": {
		FundSymbol:       "SH513500",
		ReferenceSymbol:  "HF_ES",
		Label:            "ES",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.97,
		StaticRatioLabel: "0.0300",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513650": {
		FundSymbol:       "SH513650",
		ReferenceSymbol:  "HF_ES",
		Label:            "ES",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9575,
		StaticRatioLabel: "0.0425",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513290": {
		FundSymbol:       "SH513290",
		ReferenceSymbol:  "IBB",
		Label:            "IBB",
		FXPair:           "USDCNY",
		InvestmentRatio:  1.0,
		StaticRatioLabel: "0.0000",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ159612": {
		FundSymbol:       "SZ159612",
		ReferenceSymbol:  "HF_ES",
		Label:            "ES",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.965,
		StaticRatioLabel: "0.0350",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ161125": {
		FundSymbol:       "SZ161125",
		ReferenceSymbol:  "HF_ES",
		Label:            "ES",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9125,
		StaticRatioLabel: "0.0875",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ159655": {
		FundSymbol:       "SZ159655",
		ReferenceSymbol:  "HF_ES",
		Label:            "ES",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.92,
		StaticRatioLabel: "0.0800",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513350": {
		FundSymbol:       "SH513350",
		ReferenceSymbol:  "XOP",
		Label:            "XOP",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9975,
		StaticRatioLabel: "0.0025",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ159518": {
		FundSymbol:       "SZ159518",
		ReferenceSymbol:  "XOP",
		Label:            "XOP",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.995,
		StaticRatioLabel: "0.0050",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ162411": {
		FundSymbol:       "SZ162411",
		ReferenceSymbol:  "XOP",
		Label:            "XOP",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.955,
		StaticRatioLabel: "0.0450",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513400": {
		FundSymbol:       "SH513400",
		ReferenceSymbol:  "DIA",
		Label:            "DIA",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9925,
		StaticRatioLabel: "0.0075",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ160140": {
		FundSymbol:       "SZ160140",
		ReferenceSymbol:  "RWR",
		Label:            "RWR",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.95,
		StaticRatioLabel: "0.0500",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ161126": {
		FundSymbol:       "SZ161126",
		ReferenceSymbol:  "RSPH",
		Label:            "RSPH",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9475,
		StaticRatioLabel: "0.0525",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ161127": {
		FundSymbol:       "SZ161127",
		ReferenceSymbol:  "XBI",
		Label:            "XBI",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9425,
		StaticRatioLabel: "0.0575",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ161128": {
		FundSymbol:       "SZ161128",
		ReferenceSymbol:  "VGT",
		Label:            "VGT",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9125,
		StaticRatioLabel: "0.0875",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ162415": {
		FundSymbol:       "SZ162415",
		ReferenceSymbol:  "XLY",
		Label:            "XLY",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.94,
		StaticRatioLabel: "0.0600",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ159502": {
		FundSymbol:       "SZ159502",
		ReferenceSymbol:  "XBI",
		Label:            "XBI",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.995,
		StaticRatioLabel: "0.0050",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ160416": {
		FundSymbol:       "SZ160416",
		ReferenceSymbol:  "IXC",
		Label:            "IXC",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.8025,
		StaticRatioLabel: "0.1975",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ162719": {
		FundSymbol:       "SZ162719",
		ReferenceSymbol:  "IEO",
		Label:            "IEO",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9375,
		StaticRatioLabel: "0.0625",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ163208": {
		FundSymbol:       "SZ163208",
		ReferenceSymbol:  "XLE",
		Label:            "XLE",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.99,
		StaticRatioLabel: "0.0100",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH513850": {
		FundSymbol:       "SH513850",
		ReferenceSymbol:  "OEF",
		Label:            "OEF",
		FXPair:           "USDCNY",
		InvestmentRatio:  1.0975,
		StaticRatioLabel: "-0.0975",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ159577": {
		FundSymbol:       "SZ159577",
		ReferenceSymbol:  "MGC",
		Label:            "MGC",
		FXPair:           "USDCNY",
		InvestmentRatio:  1.1,
		StaticRatioLabel: "-0.1000",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SH501018": {
		FundSymbol:       "SH501018",
		ReferenceSymbol:  "HF_CL",
		Label:            "CL",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.8,
		StaticRatioLabel: "0.2000",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "jp_close",
				Label:                "日本收盘",
				Weight:               0.15,
				Timezone:             "Asia/Tokyo",
				LocalTime:            "15:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "eu_close",
				Label:                "欧洲收盘",
				Weight:               0.48,
				Timezone:             "Europe/London",
				LocalTime:            "16:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               0.37,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ160723": {
		FundSymbol:       "SZ160723",
		ReferenceSymbol:  "HF_CL",
		Label:            "CL",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.675,
		StaticRatioLabel: "0.3250",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "hk_close",
				Label:                "香港收盘",
				Weight:               0.0222,
				Timezone:             "Asia/Hong_Kong",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "jp_close",
				Label:                "日本收盘",
				Weight:               0.0575,
				Timezone:             "Asia/Tokyo",
				LocalTime:            "15:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "eu_close",
				Label:                "欧洲/英国收盘",
				Weight:               0.4956,
				Timezone:             "Europe/London",
				LocalTime:            "16:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               0.4246,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ161129": {
		FundSymbol:       "SZ161129",
		ReferenceSymbol:  "HF_CL",
		Label:            "CL",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.7175,
		StaticRatioLabel: "0.2825",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "hk_close",
				Label:                "香港收盘",
				Weight:               0.1351,
				Timezone:             "Asia/Hong_Kong",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "eu_close",
				Label:                "欧洲/英国收盘",
				Weight:               0.4135,
				Timezone:             "Europe/London",
				LocalTime:            "16:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               0.4514,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ160719": {
		FundSymbol:       "SZ160719",
		ReferenceSymbol:  "HF_GC",
		Label:            "GC",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.95,
		StaticRatioLabel: "0.0500",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "ch_close",
				Label:                "瑞士收盘",
				Weight:               0.4498,
				Timezone:             "Europe/Zurich",
				LocalTime:            "17:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               0.5502,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ161116": {
		FundSymbol:       "SZ161116",
		ReferenceSymbol:  "HF_GC",
		Label:            "GC",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9348964094754192,
		StaticRatioLabel: "0.0651",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "ch_close",
				Label:                "瑞士收盘",
				Weight:               0.1901386308140265,
				Timezone:             "Europe/Zurich",
				LocalTime:            "17:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               0.8098613691859736,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ165513": {
		FundSymbol:       "SZ165513",
		ReferenceSymbol:  "HF_GC",
		Label:            "GC",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.965,
		StaticRatioLabel: "0.0350",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "jp_close",
				Label:                "日本收盘",
				Weight:               0.026280559453056896,
				Timezone:             "Asia/Tokyo",
				LocalTime:            "15:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "eu_close",
				Label:                "欧洲/英国收盘",
				Weight:               0.08587014298432005,
				Timezone:             "Europe/London",
				LocalTime:            "16:30",
				CaptureWindowSeconds: 180,
			},
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               0.887849297562623,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
	"SZ164701": {
		FundSymbol:       "SZ164701",
		ReferenceSymbol:  "HF_GC",
		Label:            "GC",
		FXPair:           "USDCNY",
		InvestmentRatio:  0.9725,
		StaticRatioLabel: "0.0275",
		Points: []WeightedAnchorPointRule{
			{
				Key:                  "us_close",
				Label:                "美国收盘",
				Weight:               1,
				Timezone:             "America/New_York",
				LocalTime:            "16:00",
				CaptureWindowSeconds: 180,
			},
		},
	},
}

func WeightedAnchorStrategyForFund(symbol string) (WeightedAnchorStrategy, bool) {
	strategy, ok := weightedAnchorStrategies[strings.ToUpper(strings.TrimSpace(symbol))]
	return strategy, ok
}

func WeightedAnchorStrategies() []WeightedAnchorStrategy {
	out := make([]WeightedAnchorStrategy, 0, len(weightedAnchorStrategies))
	for _, strategy := range weightedAnchorStrategies {
		out = append(out, strategy)
	}
	return out
}

func (rule WeightedAnchorPointRule) TargetAt(anchorDate string) (time.Time, bool) {
	day, err := time.Parse("2006-01-02", strings.TrimSpace(anchorDate))
	if err != nil {
		return time.Time{}, false
	}
	loc, err := time.LoadLocation(strings.TrimSpace(rule.Timezone))
	if err != nil {
		return time.Time{}, false
	}
	hour, minute, ok := parseHHMM(rule.LocalTime)
	if !ok {
		return time.Time{}, false
	}
	localDay := day.AddDate(0, 0, rule.LocalDateOffsetDays)
	return time.Date(localDay.Year(), localDay.Month(), localDay.Day(), hour, minute, 0, 0, loc), true
}

func ValuationAnchorSetKey(fundSymbol string, anchorDate string, referenceSymbol string) string {
	return fmt.Sprintf(
		"%s|%s|%s",
		strings.ToUpper(strings.TrimSpace(fundSymbol)),
		strings.TrimSpace(anchorDate),
		strings.ToUpper(strings.TrimSpace(referenceSymbol)),
	)
}

func parseHHMM(value string) (int, int, bool) {
	parts := strings.Split(strings.TrimSpace(value), ":")
	if len(parts) != 2 {
		return 0, 0, false
	}
	var hour, minute int
	if _, err := fmt.Sscanf(parts[0], "%d", &hour); err != nil {
		return 0, 0, false
	}
	if _, err := fmt.Sscanf(parts[1], "%d", &minute); err != nil {
		return 0, 0, false
	}
	if hour < 0 || hour > 23 || minute < 0 || minute > 59 {
		return 0, 0, false
	}
	return hour, minute, true
}
