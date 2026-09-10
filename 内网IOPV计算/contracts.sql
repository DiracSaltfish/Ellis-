-- Proposed production SQLite schema. Single Go writer, WAL, bound SQL parameters.
-- Trading date is Asia/Shanghai; timestamps retain source as-of and acquisition time.
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS pcf_versions (
 symbol TEXT NOT NULL, trade_date TEXT NOT NULL, sha256 TEXT NOT NULL,
 fetched_at TEXT NOT NULL, source_url TEXT NOT NULL, raw_path TEXT NOT NULL,
 policy_version TEXT NOT NULL, PRIMARY KEY(symbol,trade_date,sha256)
);
CREATE TABLE IF NOT EXISTS minute_points (
 symbol TEXT NOT NULL, trade_date TEXT NOT NULL, minute TEXT NOT NULL,
 fx_channel TEXT NOT NULL, fx_direction TEXT NOT NULL,
 midpoint_iopv REAL, settlement_iopv REAL, etf_price REAL,
 midpoint_premium_pct REAL, settlement_premium_pct REAL,
 calculated_at TEXT, quote_at TEXT, fx_at TEXT, pcf_sha256 TEXT NOT NULL,
 quality TEXT NOT NULL, model_version TEXT NOT NULL,
 PRIMARY KEY(symbol,trade_date,minute,fx_channel,fx_direction)
);
-- Do not rewrite old minutes with a later revised PCF or an evening final FX rate.
-- Historical recalculations use a separate run_id/table.
