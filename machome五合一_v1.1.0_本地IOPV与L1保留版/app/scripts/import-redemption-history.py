#!/usr/bin/env python3
"""Convert an offline baseline history export into a NEW native SQLite store."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import os
from zoneinfo import ZoneInfo
p=argparse.ArgumentParser();p.add_argument('--history-export',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
if a.output.exists():raise SystemExit('Refusing to replace an existing database')
a.output.parent.mkdir(parents=True,exist_ok=True);temp=a.output.with_suffix('.importing')
if temp.exists():raise SystemExit('Incomplete import exists; inspect it before retrying')
db=sqlite3.connect(temp);count=0;manifest={}
try:
    db.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);CREATE TABLE pcf(symbol TEXT PRIMARY KEY,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL);CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT,event_day TEXT NOT NULL,event_time TEXT NOT NULL,symbol TEXT NOT NULL,payload_json TEXT NOT NULL);CREATE INDEX idx_redemption_history ON history(event_day,symbol,id DESC);')
    for file in sorted(a.history_export.glob('changes_????-??-??.jsonl')):
        if file.is_symlink():raise ValueError('Export must not contain symlinks')
        raw=file.read_bytes();manifest[file.name]=hashlib.sha256(raw).hexdigest()
        for line in raw.splitlines():
            if not line.strip():continue
            row=json.loads(line);event=datetime.fromisoformat(row['event_time'])
            if event.tzinfo is None:raise ValueError('History timestamp requires explicit timezone')
            symbol=row['windcode']
            if len(symbol)!=9 or symbol[-3:] not in ('.SH','.SZ') or not symbol[:6].isdigit():raise ValueError('Invalid history symbol')
            # Keep original nested payloads and add the native table projections.
            first=(row.get('changes') or [{}])[0];opportunity=row.get('opportunity',{})
            row.setdefault('timestamp',row['event_time']);row.setdefault('direction',opportunity.get('kind'))
            row.setdefault('old_value',first.get('old'));row.setdefault('new_value',first.get('new'))
            row.setdefault('basket_count',opportunity.get('net_baskets'));row.setdefault('status',opportunity.get('label'))
            db.execute('INSERT INTO history(event_day,event_time,symbol,payload_json) VALUES(?,?,?,?)',(event.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),row['event_time'],symbol,json.dumps(row,ensure_ascii=False)))
            count+=1
    db.commit()
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert db.execute('SELECT count(*) FROM history').fetchone()[0]==count
    db.close();temp.chmod(0o600);os.rename(temp,a.output)
    a.output.with_suffix('.migration.json').write_text(json.dumps({'imported_rows':count,'sources':manifest,'original_files_modified':False},indent=2))
    print(json.dumps({'imported_rows':count,'database':str(a.output),'started_services':False}))
except BaseException:
    db.close();temp.unlink(missing_ok=True);raise
