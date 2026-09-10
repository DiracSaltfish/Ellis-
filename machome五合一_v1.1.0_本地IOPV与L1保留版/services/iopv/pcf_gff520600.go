package iopv

import (
	"crypto/sha256"
	"fmt"
	"html"
	"regexp"
	"strings"
	"time"
)

// 广发基金仅为 520600 发布 HTML 版 PCF。保留原始页面作为 pcf.raw，
// 并在读取缓存时走同一解析器，避免将其转换为看似官方的交易所 XML。
var (
	gffRowRE   = regexp.MustCompile(`(?is)<tr[^>]*>(.*?)</tr>`)
	gffCellRE  = regexp.MustCompile(`(?is)<td[^>]*>(.*?)</td>`)
	gffFieldRE = regexp.MustCompile(`(?is)<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*</tr>`)
	gffTagRE   = regexp.MustCompile(`(?is)<[^>]+>`)
	gffCodeRE  = regexp.MustCompile(`^[0-9]{1,5}$`)
)

func gffText(s string) string {
	s = gffTagRE.ReplaceAllString(s, " ")
	s = html.UnescapeString(s)
	s = strings.ReplaceAll(s, "\u00a0", " ")
	return strings.TrimSpace(strings.Join(strings.Fields(s), " "))
}

func gffNumber(s string) (float64, error) {
	return number(strings.ReplaceAll(strings.TrimSpace(s), ",", ""))
}

func parseGFF520600PCF(raw []byte, date string) (Basket, error) {
	if _, err := time.Parse("2006-01-02", date); err != nil {
		return Basket{}, fmt.Errorf("520600.SH: bad date")
	}
	body := string(raw)
	dayMarker := date + "日内容信息"
	day := strings.Index(body, dayMarker)
	if day < 0 {
		return Basket{}, fmt.Errorf("520600.SH: identity/date mismatch")
	}
	dayBody := body[day:]
	if end := strings.Index(strings.ToLower(dayBody), "</table>"); end >= 0 {
		dayBody = dayBody[:end+len("</table>")]
	}
	var cash, unit float64
	gotCash, gotUnit := false, false
	for _, m := range gffFieldRE.FindAllStringSubmatch(dayBody, -1) {
		label, value := gffText(m[1]), gffText(m[2])
		switch {
		case strings.Contains(label, "预估现金部分"):
			v, err := gffNumber(value)
			if err != nil {
				return Basket{}, fmt.Errorf("520600.SH: invalid T-day estimated cash")
			}
			cash, gotCash = v, true
		case strings.Contains(label, "最小申购、赎回单位") && strings.Contains(label, "单位:份"):
			v, err := gffNumber(value)
			if err != nil || v <= 0 {
				return Basket{}, fmt.Errorf("520600.SH: invalid creation unit")
			}
			unit, gotUnit = v, true
		}
	}
	if !gotCash || !gotUnit {
		return Basket{}, fmt.Errorf("520600.SH: missing required GFF PCF fields")
	}

	componentStart := strings.Index(body, "成份股信息内容")
	if componentStart < 0 {
		return Basket{}, fmt.Errorf("520600.SH: missing component table")
	}
	componentBody := body[componentStart:]
	if end := strings.Index(strings.ToLower(componentBody), "</table>"); end >= 0 {
		componentBody = componentBody[:end+len("</table>")]
	}
	b := Basket{Symbol: "520600.SH", Date: date, Unit: unit, Cash: cash, Hash: fmt.Sprintf("%x", sha256.Sum256(raw))}
	seen := map[string]bool{}
	for _, row := range gffRowRE.FindAllStringSubmatch(componentBody, -1) {
		cells := gffCellRE.FindAllStringSubmatch(row[1], -1)
		if len(cells) == 0 {
			continue
		}
		values := make([]string, len(cells))
		for i := range cells {
			values[i] = gffText(cells[i][1])
		}
		if values[0] == "证券代码" {
			continue
		}
		if len(values) != 8 || !gffCodeRE.MatchString(values[0]) {
			return Basket{}, fmt.Errorf("520600.SH: malformed GFF component")
		}
		if values[7] != "香港交易所" {
			return Basket{}, fmt.Errorf("520600.SH: unsupported component market %q", values[7])
		}
		quantity, err := gffNumber(values[2])
		if err != nil || quantity <= 0 {
			return Basket{}, fmt.Errorf("520600.SH: invalid component quantity")
		}
		flag := ""
		switch values[3] {
		case "禁止现金替代":
			flag = "0"
		case "允许现金替代":
			flag = "1"
		case "必须现金替代":
			flag = "2"
		default:
			return Basket{}, fmt.Errorf("520600.SH: unsupported substitution flag %q", values[3])
		}
		code := strings.Repeat("0", 5-len(values[0])) + values[0] + ".HK"
		if seen[code] {
			return Basket{}, fmt.Errorf("520600.SH: duplicate component")
		}
		seen[code] = true
		component := Component{Symbol: code, Quantity: quantity, RawFlag: flag}
		if flag == "2" {
			component.Mode = 2
			component.Cash, err = gffNumber(values[6])
			if err != nil || component.Cash < 0 {
				return Basket{}, fmt.Errorf("520600.SH: invalid mandatory cash")
			}
		}
		b.Components = append(b.Components, component)
	}
	if len(b.Components) == 0 {
		return Basket{}, fmt.Errorf("520600.SH: empty basket")
	}
	return b, nil
}
