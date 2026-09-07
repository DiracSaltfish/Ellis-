package db

import (
	"context"
	"database/sql"
	"errors"
	"strings"

	mysqlDriver "github.com/go-sql-driver/mysql"

	"newnavnav/internal/domain"
)

func (r *Repository) EnsureManualValuationPositionOverrideSchema(ctx context.Context) error {
	_, err := r.db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS manual_valuation_position_overrides (
    fund_symbol_id  BIGINT UNSIGNED NOT NULL,
    position_ratio  DECIMAL(12, 6) NOT NULL,
    source          VARCHAR(64) NOT NULL DEFAULT 'manual_debug',
    updated_by      VARCHAR(64) NOT NULL DEFAULT '',
    updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (fund_symbol_id),
    CONSTRAINT fk_manual_valuation_position_symbol FOREIGN KEY (fund_symbol_id) REFERENCES symbols(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`)
	return err
}

func (r *Repository) UpsertManualValuationPositionOverride(ctx context.Context, symbol string, ratio float64, source string, updatedBy string) error {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	source = strings.TrimSpace(source)
	updatedBy = strings.TrimSpace(updatedBy)
	if symbol == "" || ratio <= 0 {
		return nil
	}
	if source == "" {
		source = "manual_debug"
	}
	if err := r.EnsureSymbols(ctx, []string{symbol}); err != nil {
		return err
	}
	_, err := r.db.ExecContext(ctx, `
INSERT INTO manual_valuation_position_overrides (fund_symbol_id, position_ratio, source, updated_by)
SELECT id, ?, ?, ? FROM symbols WHERE symbol = ?
ON DUPLICATE KEY UPDATE
  position_ratio = VALUES(position_ratio),
  source = VALUES(source),
  updated_by = VALUES(updated_by)`,
		ratio,
		source,
		updatedBy,
		symbol,
	)
	return err
}

func loadManualValuationPositionOverrides(ctx context.Context, db *sql.DB, data *domain.ValuationData) error {
	rows, err := db.QueryContext(ctx, `
SELECT s.symbol, mvpo.position_ratio, mvpo.source, COALESCE(mvpo.updated_by, ''), mvpo.updated_at
FROM manual_valuation_position_overrides mvpo
JOIN symbols s ON s.id = mvpo.fund_symbol_id`)
	if err != nil {
		if isMissingTableError(err) {
			return nil
		}
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var item domain.ManualValuationPositionOverride
		if err := rows.Scan(&item.Symbol, &item.Ratio, &item.Source, &item.UpdatedBy, &item.UpdatedAt); err != nil {
			return err
		}
		if item.Symbol == "" || item.Ratio <= 0 {
			continue
		}
		item.Symbol = strings.ToUpper(strings.TrimSpace(item.Symbol))
		data.ManualPositionOverrides[item.Symbol] = item
	}
	return rows.Err()
}

func isMissingTableError(err error) bool {
	var mysqlErr *mysqlDriver.MySQLError
	return errors.As(err, &mysqlErr) && mysqlErr.Number == 1146
}
