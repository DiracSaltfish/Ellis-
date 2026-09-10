package iopv

import (
	"crypto/sha256"
	"encoding/xml"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
)

type node struct {
	XMLName  xml.Name
	Text     string `xml:",chardata"`
	Children []node `xml:",any"`
}

func (n node) value(key string) string {
	for _, c := range n.Children {
		if c.XMLName.Local == key {
			return strings.TrimSpace(c.Text)
		}
	}
	return ""
}
func number(s string) (float64, error) {
	v, e := strconv.ParseFloat(s, 64)
	if e != nil || math.IsNaN(v) || math.IsInf(v, 0) {
		return 0, fmt.Errorf("invalid number %q", s)
	}
	return v, nil
}

type Component struct {
	Name     string  `json:"name,omitempty"`
	Symbol   string  `json:"symbol"`
	Quantity float64 `json:"quantity"`
	Mode     int     `json:"mode"`
	Cash     float64 `json:"cash_cny"`
	RawFlag  string  `json:"raw_flag"`
}
type Basket struct {
	Symbol     string      `json:"symbol"`
	Date       string      `json:"trade_date"`
	Unit       float64     `json:"creation_unit"`
	Cash       float64     `json:"estimated_cash_cny"`
	Hash       string      `json:"pcf_sha256"`
	Components []Component `json:"components"`
}

// This policy covers the first-batch PCFs only. Unknown substitution flags fail closed.
func ParsePCF(raw []byte, symbol, date string) (Basket, error) {
	fail := func(s string) (Basket, error) { return Basket{}, fmt.Errorf("%s: %s", symbol, s) }
	if len(raw) > 8<<20 {
		return fail("PCF exceeds size limit")
	}
	if symbol == "520600.SH" && strings.Contains(string(raw), "广发基金") {
		return parseGFF520600PCF(raw, date)
	}
	var root node
	if err := xml.Unmarshal(raw, &root); err != nil {
		return Basket{}, err
	}
	if _, err := time.Parse("2006-01-02", date); err != nil {
		return fail("bad date")
	}
	sh := strings.HasSuffix(symbol, ".SH")
	sz := strings.HasSuffix(symbol, ".SZ")
	if !sh && !sz {
		return fail("unknown ETF market")
	}
	codeKey, cashKey, listKey := "SecurityID", "EstimateCashComponent", "Components"
	if sh {
		codeKey, cashKey, listKey = "FundInstrumentID", "EstimatedCashComponent", "ComponentList"
		if root.XMLName.Local != "SSEPortfolioCompositionFile" {
			return fail("unexpected SSE XML root")
		}
	} else if root.XMLName.Local != "PCFFile" {
		return fail("unexpected SZSE XML root")
	}
	if root.value(codeKey)+symbol[len(symbol)-3:] != symbol || root.value("TradingDay") != strings.ReplaceAll(date, "-", "") {
		return fail("identity/date mismatch")
	}
	unit, err := number(root.value("CreationRedemptionUnit"))
	if err != nil || unit <= 0 {
		return fail("invalid creation unit")
	}
	cash, err := number(root.value(cashKey))
	if err != nil {
		return fail("missing/invalid T-day estimated cash")
	}
	b := Basket{Symbol: symbol, Date: date, Unit: unit, Cash: cash, Hash: fmt.Sprintf("%x", sha256.Sum256(raw))}
	seen := map[string]bool{}
	count := 0
	for _, list := range root.Children {
		if list.XMLName.Local != listKey {
			continue
		}
		for _, r := range list.Children {
			if r.XMLName.Local != "Component" {
				return fail("unknown component record")
			}
			count++
			code, market, qty, flag, fixed := r.value("UnderlyingSecurityID"), r.value("UnderlyingSecurityIDSource"), r.value("ComponentShare"), r.value("SubstituteFlag"), r.value("CreationCashSubstitute")
			if sh {
				code, market, qty, flag, fixed = r.value("InstrumentID"), r.value("UnderlyingSecurityID"), r.value("Quantity"), r.value("SubstitutionFlag"), r.value("SubstitutionCashAmount")
			}
			q, e := number(qty)
			if e != nil || q < 0 {
				return fail("invalid component quantity")
			}
			if sz && code == "159900" && market == "102" && flag == "2" && q == 0 && r.value("UnderlyingSymbol") == "申赎现金" {
				continue
			}
			suffix := map[string]string{"101": ".SH", "102": ".SZ", "103": ".HK"}[market]
			if suffix == "" {
				return fail("unsupported component market")
			}
			width := 6
			if market == "103" {
				width = 5
			}
			if len(code) == 0 || len(code) > width {
				return fail("invalid component code")
			}
			for _, digit := range code {
				if digit < '0' || digit > '9' {
					return fail("non-numeric component code")
				}
			}
			code = strings.Repeat("0", width-len(code)) + code
			name := r.value("UnderlyingSymbol")
			if sh {
				name = r.value("InstrumentName")
			}
			c := Component{Symbol: code + suffix, Name: name, Quantity: q, RawFlag: flag}
			if seen[c.Symbol] {
				return fail("duplicate component")
			}
			seen[c.Symbol] = true
			switch flag {
			case "0", "1":
				if q <= 0 {
					return fail("zero marked quantity")
				}
				if market != "103" {
					c.Mode = 1
				}
			case "2":
				c.Mode = 2
				c.Cash, e = number(fixed)
				if e != nil || c.Cash < 0 {
					return fail("invalid mandatory cash")
				}
				if sz {
					red, e := number(r.value("RedemptionCashSubstitute"))
					if e != nil || red != c.Cash {
						return fail("asymmetric mandatory cash requires fund-specific policy")
					}
				}
			default:
				return fail("unsupported substitution flag " + flag)
			}
			b.Components = append(b.Components, c)
		}
	}
	countKey := "TotalRecordNum"
	if sh {
		countKey = "RecordNumber"
	}
	expected, e := strconv.Atoi(root.value(countKey))
	if e != nil || expected != count {
		return fail("record count mismatch")
	}
	if len(b.Components) == 0 {
		return fail("empty basket")
	}
	return b, nil
}
