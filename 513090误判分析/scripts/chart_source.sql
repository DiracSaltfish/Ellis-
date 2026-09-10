WITH basket AS (
 SELECT date, time, SUM(quantity * price_HKD) AS stock_HKD,
        SUM(quantity * price_HKD) * 0.86482 AS stock_CNY,
        (SUM(quantity * price_HKD) * 0.86482 + 9547.19) / 500000.0 AS iopv
 FROM minute_quotes GROUP BY date, time HAVING COUNT(*) = 17
)
SELECT b.time, '中间价 IOPV' AS series, b.iopv AS value, b.date,
       b.stock_HKD, b.stock_CNY, 9547.19 AS estimated_cash_CNY,
       0.86482 AS fx, 500000 AS unit_shares
FROM basket b
UNION ALL
SELECT b.time, 'ETF成交价' AS series, e.price AS value, b.date,
       b.stock_HKD, b.stock_CNY, 9547.19, 0.86482, 500000
FROM basket b JOIN etf_quotes e ON e.date=b.date AND e.time=b.time
WHERE (b.time BETWEEN '09:30' AND '11:30') OR (b.time BETWEEN '13:00' AND '15:00')
ORDER BY time, series;
