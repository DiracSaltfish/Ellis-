"""Consistent online SQLite backup; safe while the standalone service is writing."""
import sqlite3,datetime
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root/'data/backups';out.mkdir(parents=True,exist_ok=True)
target=out/('iopv-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite')
with sqlite3.connect(root/'data/iopv.sqlite') as src,sqlite3.connect(target) as dst:
 src.backup(dst)
 assert dst.execute('pragma quick_check').fetchone()[0]=='ok'
print(target)
