package domain

import "time"

type BranchListSnapshot struct {
	SnapshotKey       string              `json:"snapshot_key"`
	SchemaVersion     string              `json:"schema_version"`
	AsOf              time.Time           `json:"as_of"`
	TTLSeconds        int                 `json:"ttl_seconds"`
	StaleAfterSeconds int                 `json:"stale_after_seconds"`
	Branch            Branch              `json:"branch"`
	EstimateRows      []BranchEstimateRow `json:"estimate_rows"`
	Warnings          []string            `json:"warnings"`
}

type BranchEstimateRow struct {
	Symbol          string   `json:"symbol"`
	Name            string   `json:"name"`
	OfficialEst     float64  `json:"official_est"`
	FairEst         float64  `json:"fair_est"`
	RealtimeEst     *float64 `json:"realtime_est"`
	OfficialPremium float64  `json:"official_premium"`
	FairPremium     float64  `json:"fair_premium"`
	RealtimePremium *float64 `json:"realtime_premium"`
	ModelVersion    string   `json:"model_version"`
	PurchaseLimit   *float64 `json:"purchase_limit,omitempty"`
	PurchaseStatus  string   `json:"purchase_status,omitempty"`
	Note            string   `json:"note,omitempty"`
}

func NewBranchListSnapshot(snapshot BranchSnapshot) BranchListSnapshot {
	estimateRows := make([]BranchEstimateRow, 0, len(snapshot.EstimateRows))
	for _, row := range snapshot.EstimateRows {
		estimateRows = append(estimateRows, NewBranchEstimateRow(row))
	}
	return BranchListSnapshot{
		SnapshotKey:       snapshot.SnapshotKey,
		SchemaVersion:     snapshot.SchemaVersion,
		AsOf:              snapshot.AsOf,
		TTLSeconds:        snapshot.TTLSeconds,
		StaleAfterSeconds: snapshot.StaleAfterSeconds,
		Branch:            snapshot.Branch,
		EstimateRows:      estimateRows,
		Warnings:          cloneStringList(snapshot.Warnings),
	}
}

func cloneStringList(values []string) []string {
	if len(values) == 0 {
		return []string{}
	}
	return append([]string(nil), values...)
}

func NewBranchEstimateRow(row EstimateRow) BranchEstimateRow {
	return BranchEstimateRow{
		Symbol:          row.Symbol,
		Name:            row.Name,
		OfficialEst:     row.OfficialEst,
		FairEst:         row.FairEst,
		RealtimeEst:     row.RealtimeEst,
		OfficialPremium: row.OfficialPremium,
		FairPremium:     row.FairPremium,
		RealtimePremium: row.RealtimePremium,
		ModelVersion:    row.ModelVersion,
		PurchaseLimit:   row.PurchaseLimit,
		PurchaseStatus:  row.PurchaseStatus,
		Note:            row.Note,
	}
}
