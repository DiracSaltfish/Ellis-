package palmmicro

import (
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"golang.org/x/net/html"
)

type tableCell struct {
	Text      string
	Title     string
	Links     []string
	LinkTexts []string
}

type tableRow struct {
	Cells []tableCell
}

func ParseFundList(raw string) ([]FundListItem, error) {
	rows, err := tableRowsByID(raw, "fundlisttable")
	if err != nil {
		return nil, err
	}
	var out []FundListItem
	for _, row := range rows {
		if len(row.Cells) < 5 {
			continue
		}
		fundSymbol := symbolFromCell(row.Cells[0])
		pairSymbol := symbolFromCell(row.Cells[1])
		if fundSymbol == "" || pairSymbol == "" || fundSymbol == "代码" || fundSymbol == "全部" {
			continue
		}
		out = append(out, FundListItem{
			FundSymbol:      fundSymbol,
			FundName:        row.Cells[0].Title,
			PairSymbol:      pairSymbol,
			PairName:        row.Cells[1].Title,
			Position:        parseFloat(row.Cells[2].Text),
			Calibration:     parseFloat(row.Cells[3].Text),
			CalibrationDate: cleanText(row.Cells[4].Text),
			ReferenceValue:  optionalCellFloat(row, 5),
		})
	}
	return out, nil
}

func ParseHoldingSnapshot(symbol string, raw string) (HoldingSnapshot, error) {
	snapshot := HoldingSnapshot{FundSymbol: symbol, Position: 1}
	pageText := cleanText(stripHTML(raw))
	if strings.Contains(pageText, "登录帐号") {
		return snapshot, nil
	}
	if match := regexp.MustCompile(`值使用([0-9.]+)`).FindStringSubmatch(pageText); len(match) == 2 {
		snapshot.Position = parseFloat(match[1])
	}
	if match := regexp.MustCompile(`基金持仓\s*更新于([0-9]{4}-[0-9]{2}-[0-9]{2})`).FindStringSubmatch(pageText); len(match) == 2 {
		snapshot.HoldingDate = match[1]
	}

	netRows, err := tableRowsByID(raw, "netvaluehistorytable")
	if err != nil {
		return snapshot, err
	}
	for _, row := range netRows {
		if len(row.Cells) < 2 || cleanText(row.Cells[0].Text) == "日期" {
			continue
		}
		snapshot.NetValueDate = cleanText(row.Cells[0].Text)
		snapshot.NetValue = parseFloat(row.Cells[1].Text)
		break
	}

	holdingRows, err := tableRowsByID(raw, "holdingstable")
	if err != nil {
		return snapshot, err
	}
	for _, row := range holdingRows {
		if len(row.Cells) < 3 {
			continue
		}
		holdingSymbol := symbolFromCell(row.Cells[0])
		if holdingSymbol == "" || holdingSymbol == "代码" || holdingSymbol == "全部" {
			continue
		}
		ratio := parseFloat(row.Cells[1].Text)
		basePrice := parseFloat(row.Cells[2].Text)
		if ratio <= 0 || basePrice <= 0 {
			continue
		}
		snapshot.Holdings = append(snapshot.Holdings, HoldingItem{
			Symbol:    holdingSymbol,
			Name:      row.Cells[0].Title,
			Ratio:     ratio,
			BasePrice: basePrice,
			FXAdjust:  optionalCellFloat(row, 6),
			Currency:  inferCurrency(holdingSymbol),
		})
	}
	return snapshot, nil
}

func tableRowsByID(raw string, id string) ([]tableRow, error) {
	doc, err := html.Parse(strings.NewReader(raw))
	if err != nil {
		return nil, err
	}
	table := findNodeByID(doc, "table", id)
	if table == nil {
		return []tableRow{}, nil
	}
	var rows []tableRow
	var walk func(*html.Node)
	walk = func(node *html.Node) {
		if node.Type == html.ElementNode && node.Data == "tr" {
			row := parseTableRow(node)
			if len(row.Cells) > 0 {
				rows = append(rows, row)
			}
			return
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(table)
	return rows, nil
}

func findNodeByID(node *html.Node, tag string, id string) *html.Node {
	if node.Type == html.ElementNode && node.Data == tag && attr(node, "id") == id {
		return node
	}
	for child := node.FirstChild; child != nil; child = child.NextSibling {
		if found := findNodeByID(child, tag, id); found != nil {
			return found
		}
	}
	return nil
}

func parseTableRow(rowNode *html.Node) tableRow {
	var row tableRow
	for child := rowNode.FirstChild; child != nil; child = child.NextSibling {
		if child.Type != html.ElementNode || (child.Data != "td" && child.Data != "th") {
			continue
		}
		cell := tableCell{
			Text:  cleanText(textContent(child)),
			Title: attr(child, "title"),
		}
		collectLinks(child, &cell)
		row.Cells = append(row.Cells, cell)
	}
	return row
}

func collectLinks(node *html.Node, cell *tableCell) {
	if node.Type == html.ElementNode && node.Data == "a" {
		if href := attr(node, "href"); href != "" {
			cell.Links = append(cell.Links, href)
			cell.LinkTexts = append(cell.LinkTexts, cleanText(textContent(node)))
		}
	}
	for child := node.FirstChild; child != nil; child = child.NextSibling {
		collectLinks(child, cell)
	}
}

func textContent(node *html.Node) string {
	if node.Type == html.TextNode {
		return node.Data
	}
	var b strings.Builder
	for child := node.FirstChild; child != nil; child = child.NextSibling {
		b.WriteString(textContent(child))
		b.WriteString(" ")
	}
	return b.String()
}

func stripHTML(raw string) string {
	doc, err := html.Parse(strings.NewReader(raw))
	if err != nil {
		return raw
	}
	return textContent(doc)
}

func attr(node *html.Node, name string) string {
	for _, attr := range node.Attr {
		if attr.Key == name {
			return attr.Val
		}
	}
	return ""
}

func symbolFromCell(cell tableCell) string {
	for _, href := range cell.Links {
		if symbol := symbolFromHref(href); symbol != "" {
			return symbol
		}
	}
	for _, text := range cell.LinkTexts {
		if symbol := normalizeSymbol(text); symbol != "" {
			return symbol
		}
	}
	return normalizeSymbol(cell.Text)
}

func symbolFromHref(href string) string {
	parsed, err := url.Parse(href)
	if err != nil {
		return ""
	}
	return normalizeSymbol(parsed.Query().Get("symbol"))
}

func normalizeSymbol(value string) string {
	value = cleanText(value)
	if value == "" {
		return ""
	}
	if strings.Contains(value, " ") {
		value = strings.Fields(value)[0]
	}
	return strings.ToUpper(value)
}

func cleanText(value string) string {
	value = strings.ReplaceAll(value, "\u00a0", " ")
	return strings.Join(strings.Fields(value), " ")
}

func parseFloat(value string) float64 {
	value = strings.TrimSpace(strings.ReplaceAll(value, "%", ""))
	if value == "" {
		return 0
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil {
		return 0
	}
	return parsed
}

func optionalCellFloat(row tableRow, idx int) float64 {
	if idx >= len(row.Cells) {
		return 0
	}
	return parseFloat(row.Cells[idx].Text)
}

func inferCurrency(symbol string) string {
	switch {
	case regexp.MustCompile(`^\d{5}$`).MatchString(symbol):
		return "HKD"
	case strings.HasPrefix(symbol, "SH"), strings.HasPrefix(symbol, "SZ"), strings.HasPrefix(symbol, "BJ"):
		return "CNY"
	default:
		return "USD"
	}
}
