package domain

var staticFundPairs = []FundPair{
	{FundSymbol: "SH501300", PairSymbol: "AGG", PairType: "qdii_us_static", Note: "旧站 QDII 静态参考标的"},
	{FundSymbol: "SH513400", PairSymbol: "DIA", PairType: "qdii_us_static", Note: "旧站 ^DJI 以 DIA 做可交易参考"},
	{FundSymbol: "SZ160140", PairSymbol: "RWR", PairType: "qdii_us_static", Note: "季报确认道琼斯美国精选 REIT，可交易回退代理改为 RWR"},
	{FundSymbol: "SZ160416", PairSymbol: "IXC", PairType: "qdii_us_static", Note: "旧站 QDII 静态参考标的"},
	{FundSymbol: "SZ161126", PairSymbol: "RSPH", PairType: "qdii_us_static", Note: "旧站 QDII 静态参考标的"},
	{FundSymbol: "SZ161128", PairSymbol: "VGT", PairType: "qdii_us_static", Note: "旧站 QDII 静态参考标的"},
	{FundSymbol: "SZ162415", PairSymbol: "XLY", PairType: "qdii_us_static", Note: "旧站 QDII 静态参考标的"},
	{FundSymbol: "SZ162719", PairSymbol: "IEO", PairType: "qdii_us_static", Note: "旧站 QDII 静态参考标的"},
	{FundSymbol: "SZ164906", PairSymbol: "KWEB", PairType: "qdii_us_static", Note: "旧站 QDII 静态参考标的"},
	{FundSymbol: "SZ163208", PairSymbol: "XLE", PairType: "qdii_us_static", Note: "季报确认能源股主暴露，XLE 作为回退代理"},
	{FundSymbol: "SH513850", PairSymbol: "OEF", PairType: "qdii_us_static", Note: "MSCI 美国50 回退代理使用 OEF"},
	{FundSymbol: "SZ159577", PairSymbol: "MGC", PairType: "qdii_us_static", Note: "MSCI 美国50 回退代理使用 MGC"},

	{FundSymbol: "SZ159502", PairSymbol: "XBI", PairType: "qdii_us_static", Note: "旧站 XBI 分组参考标的"},
	{FundSymbol: "SZ161127", PairSymbol: "XBI", PairType: "qdii_us_static", Note: "旧站 XBI 分组参考标的"},

	{FundSymbol: "SH513100", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SH513110", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SH513390", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SH513870", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ159501", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ159513", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ159632", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ159659", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ159660", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ159696", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ159941", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SZ161130", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},
	{FundSymbol: "SH513300", PairSymbol: "HF_NQ", PairType: "qdii_us_static", Note: "纳指 QDII 统一改用 NQ 连续期货做可实时参考"},

	{FundSymbol: "SH513800", PairSymbol: "znb_TPX", PairType: "qdii_jp_static", Note: "旧站日本 QDII 静态参考标的"},

	{FundSymbol: "SH513080", PairSymbol: "znb_CAC", PairType: "qdii_eu_static", Note: "旧站法国 CAC 分组参考标的"},
	{FundSymbol: "SH513030", PairSymbol: "znb_DAX", PairType: "qdii_eu_static", Note: "旧站德国 DAX 分组参考标的"},
	{FundSymbol: "SZ159561", PairSymbol: "znb_DAX", PairType: "qdii_eu_static", Note: "旧站德国 DAX 分组参考标的"},

	{FundSymbol: "SH501025", PairSymbol: "SH000869", PairType: "qdii_hk_static", Note: "旧站港股银行 QDII 静态参考标的"},

	{FundSymbol: "SH513010", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH513130", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH513180", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH513260", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH513380", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH513580", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH513890", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH520570", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH520590", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SH520920", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SZ159740", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SZ159741", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},
	{FundSymbol: "SZ159742", PairSymbol: "03032", PairType: "qdii_hk_static", Note: "恒生科技分组以港股 HSTECH ETF 做可拉取参考"},

	{FundSymbol: "SH510900", PairSymbol: "02828", PairType: "qdii_hk_static", Note: "H 股分组以港股 HSCEI ETF 做可拉取参考"},
	{FundSymbol: "SZ159850", PairSymbol: "02828", PairType: "qdii_hk_static", Note: "H 股分组以港股 HSCEI ETF 做可拉取参考"},
	{FundSymbol: "SZ159954", PairSymbol: "02828", PairType: "qdii_hk_static", Note: "H 股分组以港股 HSCEI ETF 做可拉取参考"},
	{FundSymbol: "SZ159960", PairSymbol: "02828", PairType: "qdii_hk_static", Note: "H 股分组以港股 HSCEI ETF 做可拉取参考"},
	{FundSymbol: "SZ160717", PairSymbol: "02828", PairType: "qdii_hk_static", Note: "H 股分组以港股 HSCEI ETF 做可拉取参考"},
	{FundSymbol: "SZ161831", PairSymbol: "02828", PairType: "qdii_hk_static", Note: "H 股分组以港股 HSCEI ETF 做可拉取参考"},

	{FundSymbol: "SZ161124", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SH501302", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SH513210", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SH513600", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SH513660", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SZ159312", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SZ159920", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SZ160924", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
	{FundSymbol: "SZ164705", PairSymbol: "02800", PairType: "qdii_hk_static", Note: "恒生分组以港股 Tracker Fund 做可拉取参考"},
}

func ApplyStaticValuationData(data *ValuationData) {
	if data == nil {
		return
	}
	if data.FundPairs == nil {
		data.FundPairs = map[string][]FundPair{}
	}
	for _, pair := range staticFundPairs {
		prependFundPairIfMissing(data, pair)
	}
}

func prependFundPairIfMissing(data *ValuationData, pair FundPair) {
	for _, existing := range data.FundPairs[pair.FundSymbol] {
		if existing.PairSymbol == pair.PairSymbol && existing.PairType == pair.PairType {
			return
		}
	}
	data.FundPairs[pair.FundSymbol] = append([]FundPair{pair}, data.FundPairs[pair.FundSymbol]...)
}
