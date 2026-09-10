package snapshot

import (
	"fmt"
	"html"
	"strings"

	"newnavnav/internal/domain"
)

func RenderBranchHTML(snapshot domain.BranchSnapshot) string {
	var b strings.Builder
	hasRealtimeEstimate := false
	hasT1CloseEstimate := false
	for _, row := range snapshot.EstimateRows {
		if row.RealtimeEst != nil && row.RealtimePremium != nil {
			hasRealtimeEstimate = true
			continue
		}
		hasT1CloseEstimate = true
	}
	b.WriteString("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
	b.WriteString("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
	b.WriteString("<title>")
	b.WriteString(html.EscapeString(snapshot.Branch.NameCN))
	b.WriteString("基金估值</title>")
	b.WriteString("<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;color:#172033}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #d8dee8;padding:6px 8px;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#f3f6fb}.muted{color:#687385}.up{color:#c62828}.down{color:#16833a}</style>")
	b.WriteString("</head><body>")
	b.WriteString("<h1>")
	b.WriteString(html.EscapeString(snapshot.Branch.NameCN))
	b.WriteString("基金估值</h1>")
	b.WriteString("<p class=\"muted\">as_of: ")
	b.WriteString(html.EscapeString(snapshot.AsOf.Format("2006-01-02 15:04:05")))
	b.WriteString("，TTL 5 秒。估值优先使用持仓、fundpair、净值校准数据，缺失时降级为行情占位。</p>")
	b.WriteString("<table><thead><tr><th>代码</th><th>名称</th><th>申购限额</th><th>T-2官方公布净值</th><th>T-2官方溢价</th>")
	if hasT1CloseEstimate {
		b.WriteString("<th>T-1收盘时点估值</th><th>T-1收盘时点溢价</th>")
	}
	if hasRealtimeEstimate {
		b.WriteString("<th>实时EST</th><th>实时溢价</th>")
	}
	b.WriteString("<th>模型</th></tr></thead><tbody>")
	for _, row := range snapshot.EstimateRows {
		rowHasRealtime := row.RealtimeEst != nil && row.RealtimePremium != nil
		officialClass := premiumClass(row.OfficialPremium)
		fairClass := premiumClass(row.FairPremium)
		b.WriteString("<tr><td>")
		b.WriteString(html.EscapeString(row.Symbol))
		b.WriteString("</td><td>")
		b.WriteString(html.EscapeString(row.Name))
		b.WriteString("</td><td title=\"")
		b.WriteString(html.EscapeString(row.PurchaseStatus))
		b.WriteString("\">")
		b.WriteString(html.EscapeString(formatPurchaseLimit(row.PurchaseLimit)))
		b.WriteString("</td><td>")
		b.WriteString(fmt.Sprintf("%.4f", row.OfficialEst))
		b.WriteString("</td><td>")
		b.WriteString("<span class=\"")
		b.WriteString(officialClass)
		b.WriteString("\">")
		b.WriteString(fmt.Sprintf("%.2f%%", row.OfficialPremium))
		b.WriteString("</span></td>")
		if hasT1CloseEstimate {
			b.WriteString("<td>")
			if !rowHasRealtime {
				b.WriteString(fmt.Sprintf("%.4f", row.FairEst))
			}
			b.WriteString("</td><td>")
			if !rowHasRealtime {
				b.WriteString("<span class=\"")
				b.WriteString(fairClass)
				b.WriteString("\">")
				b.WriteString(fmt.Sprintf("%.2f%%", row.FairPremium))
				b.WriteString("</span>")
			}
			b.WriteString("</td>")
		}
		if hasRealtimeEstimate {
			b.WriteString("<td>")
			if row.RealtimeEst != nil {
				b.WriteString(fmt.Sprintf("%.4f", *row.RealtimeEst))
			}
			b.WriteString("</td><td>")
			if row.RealtimePremium != nil {
				realtimeClass := premiumClass(*row.RealtimePremium)
				b.WriteString("<span class=\"")
				b.WriteString(realtimeClass)
				b.WriteString("\">")
				b.WriteString(fmt.Sprintf("%.2f%%", *row.RealtimePremium))
				b.WriteString("</span>")
			}
			b.WriteString("</td>")
		}
		b.WriteString("<td>")
		b.WriteString(html.EscapeString(row.ModelVersion))
		b.WriteString("</td></tr>")
	}
	b.WriteString("</tbody></table></body></html>")
	return b.String()
}

func formatPurchaseLimit(value *float64) string {
	if value == nil {
		return "--"
	}
	limit := *value
	if limit <= 0 {
		return "0"
	}
	if limit >= 800000000 {
		return "无限额"
	}
	if limit < 10000 {
		return fmt.Sprintf("%.0f元", limit)
	}
	if limit < 100000000 {
		return fmt.Sprintf("%.1f万", limit/10000)
	}
	return fmt.Sprintf("%.1f亿", limit/100000000)
}

func premiumClass(value float64) string {
	if value > 0 {
		return "up"
	}
	if value < 0 {
		return "down"
	}
	return "muted"
}
