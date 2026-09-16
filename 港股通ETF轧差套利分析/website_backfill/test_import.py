"""Focused regression: append-only import, explicit zero/null shares, idempotency."""
import contextlib,io,json,sqlite3,tempfile
from pathlib import Path
import import_history as m
with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
 m.R=Path(tmp);stage=sqlite3.connect(m.R/'staged.sqlite');stage.executescript(m.SCHEMA+'CREATE TABLE minute_blocks(symbol TEXT,trade_date TEXT,payload BLOB,point_count INTEGER,PRIMARY KEY(symbol,trade_date));')
 stage.executemany('INSERT INTO minute_blocks VALUES(?,?,?,?)',[('159570.SZ','2026-01-01',b'new1',1),('159570.SZ','2026-01-02',b'new2',1),('159570.SZ','2026-01-03',b'new3',1)])
 stage.execute("INSERT INTO daily_shares VALUES('159570.SZ','2026-01-03',100,0,'1navs','2026-01-03')");stage.commit();stage.close()
 db=m.R/'site.sqlite';c=sqlite3.connect(db);c.executescript('CREATE TABLE minute_blocks(symbol TEXT,trade_date TEXT,payload BLOB,point_count INTEGER,PRIMARY KEY(symbol,trade_date));CREATE TABLE minutes(symbol TEXT,trade_date TEXT,minute TEXT,payload TEXT);')
 c.execute("INSERT INTO minute_blocks VALUES('159570.SZ','2026-01-01',?,1)",(b'original',));c.execute("INSERT INTO minutes VALUES('159570.SZ','2026-01-02','09:30','legacy')");c.commit()
 with contextlib.redirect_stdout(io.StringIO()):m.apply(db);m.apply(db)
 assert c.execute('SELECT trade_date,payload FROM minute_blocks ORDER BY trade_date').fetchall()==[('2026-01-01',b'original'),('2026-01-03',b'new3')]
 assert c.execute('SELECT share_change_10k FROM daily_shares').fetchone()==(0.0,)
 assert c.execute('SELECT payload FROM minutes').fetchone()==('legacy',)
 print('PASS: existing block/legacy row preserved, missing day added, rerun idempotent, zero shares preserved')
 c.close()
