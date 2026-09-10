package domain

import (
	"sort"
	"strings"
)

// ChinaBroadMarketETF is an A-share broad-market ETF used only for research
// share-history tracking. It is deliberately separate from Branches and
// AllSymbols so it never enters the live valuation, quote, or redemption-board
// pipelines.
type ChinaBroadMarketETF struct {
	Symbol    string `json:"symbol"`
	Name      string `json:"name"`
	IndexName string `json:"index_name"`
}

var chinaBroadMarketETFs = []ChinaBroadMarketETF{
	{Symbol: "SZ159238", Name: "沪深300增强ETF景顺", IndexName: "沪深300"},
	{Symbol: "SZ159300", Name: "沪深300ETF富国", IndexName: "沪深300"},
	{Symbol: "SZ159330", Name: "沪深300ETF东财", IndexName: "沪深300"},
	{Symbol: "SZ159337", Name: "中证500ETF东财", IndexName: "中证500"},
	{Symbol: "SZ159393", Name: "沪深300ETF万家", IndexName: "沪深300"},
	{Symbol: "SZ159500", Name: "中证500ETF富国", IndexName: "中证500"},
	{Symbol: "SZ159610", Name: "中证500增强ETF景顺", IndexName: "中证500"},
	{Symbol: "SZ159629", Name: "中证1000ETF富国", IndexName: "中证1000"},
	{Symbol: "SZ159633", Name: "中证1000ETF易方达", IndexName: "中证1000"},
	{Symbol: "SZ159673", Name: "沪深300ETF鹏华", IndexName: "沪深300"},
	{Symbol: "SZ159677", Name: "中证1000增强ETF银华", IndexName: "中证1000"},
	{Symbol: "SZ159678", Name: "500增强ETF博时", IndexName: "中证500"},
	{Symbol: "SZ159679", Name: "中证1000增强ETF国泰", IndexName: "中证1000"},
	{Symbol: "SZ159680", Name: "中证1000增强ETF招商", IndexName: "中证1000"},
	{Symbol: "SZ159685", Name: "1000增强ETF天弘", IndexName: "中证1000"},
	{Symbol: "SZ159820", Name: "中证500ETF天弘", IndexName: "中证500"},
	{Symbol: "SZ159845", Name: "中证1000ETF华夏", IndexName: "中证1000"},
	{Symbol: "SZ159919", Name: "沪深300ETF嘉实", IndexName: "沪深300"},
	{Symbol: "SZ159922", Name: "中证500ETF嘉实", IndexName: "中证500"},
	{Symbol: "SZ159925", Name: "沪深300ETF南方", IndexName: "沪深300"},
	{Symbol: "SZ159935", Name: "中证500ETF景顺", IndexName: "中证500"},
	{Symbol: "SZ159968", Name: "中证500ETF博时", IndexName: "中证500"},
	{Symbol: "SZ159982", Name: "中证500ETF鹏华", IndexName: "中证500"},
	{Symbol: "SH510050", Name: "上证50ETF华夏", IndexName: "上证50"},
	{Symbol: "SH510100", Name: "上证50ETF易方达", IndexName: "上证50"},
	{Symbol: "SH510190", Name: "上证50ETF华安", IndexName: "上证50"},
	{Symbol: "SH510300", Name: "沪深300ETF华泰柏瑞", IndexName: "沪深300"},
	{Symbol: "SH510310", Name: "沪深300ETF易方达", IndexName: "沪深300"},
	{Symbol: "SH510320", Name: "沪深300ETF中金", IndexName: "沪深300"},
	{Symbol: "SH510330", Name: "沪深300ETF华夏", IndexName: "沪深300"},
	{Symbol: "SH510350", Name: "沪深300ETF工银", IndexName: "沪深300"},
	{Symbol: "SH510360", Name: "沪深300ETF广发", IndexName: "沪深300"},
	{Symbol: "SH510370", Name: "沪深300ETF兴业", IndexName: "沪深300"},
	{Symbol: "SH510380", Name: "沪深300ETF国寿", IndexName: "沪深300"},
	{Symbol: "SH510390", Name: "沪深300ETF平安", IndexName: "沪深300"},
	{Symbol: "SH510500", Name: "中证500ETF南方", IndexName: "中证500"},
	{Symbol: "SH510510", Name: "中证500ETF广发", IndexName: "中证500"},
	{Symbol: "SH510530", Name: "中证500ETF工银", IndexName: "中证500"},
	{Symbol: "SH510550", Name: "中证500ETF方正富邦", IndexName: "中证500"},
	{Symbol: "SH510560", Name: "中证500ETF国寿", IndexName: "中证500"},
	{Symbol: "SH510570", Name: "中证500ETF兴业", IndexName: "中证500"},
	{Symbol: "SH510580", Name: "中证500ETF易方达", IndexName: "中证500"},
	{Symbol: "SH510590", Name: "中证500ETF平安", IndexName: "中证500"},
	{Symbol: "SH510600", Name: "上证50ETF申万菱信", IndexName: "上证50"},
	{Symbol: "SH510680", Name: "上证50ETF万家", IndexName: "上证50"},
	{Symbol: "SH510710", Name: "上证50ETF博时", IndexName: "上证50"},
	{Symbol: "SH510800", Name: "上证50ETF建信", IndexName: "上证50"},
	{Symbol: "SH510850", Name: "上证50ETF工银", IndexName: "上证50"},
	{Symbol: "SH510950", Name: "上证50ETF广发", IndexName: "上证50"},
	{Symbol: "SH512100", Name: "中证1000ETF南方", IndexName: "中证1000"},
	{Symbol: "SH512500", Name: "中证500ETF华夏", IndexName: "中证500"},
	{Symbol: "SH512510", Name: "中证500ETF华泰柏瑞", IndexName: "中证500"},
	{Symbol: "SH515130", Name: "沪深300ETF博时", IndexName: "沪深300"},
	{Symbol: "SH515190", Name: "中证500ETF中银证券", IndexName: "中证500"},
	{Symbol: "SH515310", Name: "沪深300ETF汇添富", IndexName: "沪深300"},
	{Symbol: "SH515330", Name: "沪深300ETF天弘", IndexName: "沪深300"},
	{Symbol: "SH515350", Name: "沪深300ETF民生加银", IndexName: "沪深300"},
	{Symbol: "SH515360", Name: "沪深300ETF方正富邦", IndexName: "沪深300"},
	{Symbol: "SH515380", Name: "沪深300ETF泰康", IndexName: "沪深300"},
	{Symbol: "SH515390", Name: "沪深300ETF华安", IndexName: "沪深300"},
	{Symbol: "SH515530", Name: "中证500ETF泰康", IndexName: "中证500"},
	{Symbol: "SH515550", Name: "中证500ETF国联", IndexName: "中证500"},
	{Symbol: "SH515660", Name: "沪深300ETF国联安", IndexName: "沪深300"},
	{Symbol: "SH516300", Name: "中证1000ETF华泰柏瑞", IndexName: "中证1000"},
	{Symbol: "SH530000", Name: "上证50ETF天弘", IndexName: "上证50"},
	{Symbol: "SH530050", Name: "上证50ETF东财", IndexName: "上证50"},
	{Symbol: "SH560010", Name: "中证1000ETF广发", IndexName: "中证1000"},
	{Symbol: "SH560100", Name: "中证500增强ETF南方", IndexName: "中证500"},
	{Symbol: "SH560110", Name: "中证1000ETF汇添富", IndexName: "中证1000"},
	{Symbol: "SH560590", Name: "中证1000增强ETF鹏华", IndexName: "中证1000"},
	{Symbol: "SH560950", Name: "中证500增强ETF汇添富", IndexName: "中证500"},
	{Symbol: "SH561000", Name: "沪深300增强ETF华安", IndexName: "沪深300"},
	{Symbol: "SH561280", Name: "中证1000增强ETF工银", IndexName: "中证1000"},
	{Symbol: "SH561300", Name: "沪深300增强ETF国泰", IndexName: "沪深300"},
	{Symbol: "SH561350", Name: "中证500ETF国泰", IndexName: "中证500"},
	{Symbol: "SH561550", Name: "中证500增强ETF华泰柏瑞", IndexName: "中证500"},
	{Symbol: "SH561590", Name: "中证1000增强ETF华泰柏瑞", IndexName: "中证1000"},
	{Symbol: "SH561780", Name: "1000增强ETF博时", IndexName: "中证1000"},
	{Symbol: "SH561930", Name: "沪深300ETF招商", IndexName: "沪深300"},
	{Symbol: "SH561950", Name: "中证500增强ETF招商", IndexName: "中证500"},
	{Symbol: "SH561990", Name: "沪深300增强ETF招商", IndexName: "沪深300"},
	{Symbol: "SH562070", Name: "沪深300指增ETF华宝", IndexName: "沪深300"},
	{Symbol: "SH563030", Name: "中证500增强ETF易方达", IndexName: "中证500"},
	{Symbol: "SH563090", Name: "上证50增强ETF易方达", IndexName: "上证50"},
	{Symbol: "SH563520", Name: "沪深300ETF永赢", IndexName: "沪深300"},
	{Symbol: "SH563750", Name: "中证500ETF汇添富", IndexName: "中证500"},
}

func ChinaBroadMarketETFs() []ChinaBroadMarketETF {
	items := make([]ChinaBroadMarketETF, len(chinaBroadMarketETFs))
	copy(items, chinaBroadMarketETFs)
	return items
}

func ChinaBroadMarketETFBySymbol(symbol string) (ChinaBroadMarketETF, bool) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	for _, fund := range chinaBroadMarketETFs {
		if fund.Symbol == symbol {
			return fund, true
		}
	}
	return ChinaBroadMarketETF{}, false
}

func ChinaBroadMarketSymbols() []string {
	symbols := make([]string, 0, len(chinaBroadMarketETFs))
	for _, fund := range chinaBroadMarketETFs {
		symbols = append(symbols, fund.Symbol)
	}
	return symbols
}

// ShareHistorySymbols combines normal display symbols with the isolated
// research universe. Callers that calculate value must continue using
// AllSymbols instead.
func ShareHistorySymbols() []string {
	seen := make(map[string]bool)
	symbols := make([]string, 0, len(AllSymbols())+len(chinaBroadMarketETFs))
	for _, group := range [][]string{AllSymbols(), ChinaBroadMarketSymbols()} {
		for _, symbol := range group {
			symbol = strings.ToUpper(strings.TrimSpace(symbol))
			if symbol == "" || seen[symbol] {
				continue
			}
			seen[symbol] = true
			symbols = append(symbols, symbol)
		}
	}
	sort.Strings(symbols)
	return symbols
}
