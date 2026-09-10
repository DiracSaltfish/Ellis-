package palmmicro

type FundListItem struct {
	FundSymbol      string
	FundName        string
	PairSymbol      string
	PairName        string
	Position        float64
	Calibration     float64
	CalibrationDate string
	ReferenceValue  float64
}

type HoldingSnapshot struct {
	FundSymbol   string
	Position     float64
	HoldingDate  string
	NetValueDate string
	NetValue     float64
	Holdings     []HoldingItem
}

type HoldingItem struct {
	Symbol    string
	Name      string
	Ratio     float64
	BasePrice float64
	FXAdjust  float64
	Currency  string
}
