"""Stage audited minute archives, then append missing fund/days to the website DB.
Only --apply writes the site. SQLite backup and manifest make the import reviewable.
Existing minute/day records are never overwritten. The original minute labels are
restored by reversing the research panel's one-minute availability shift.
"""
from pathlib import Path
import argparse,datetime as dt,gzip,hashlib,json,math,sqlite3,zlib
import pandas as pd
R=Path(__file__).resolve().parent;F=R.parent/'factor_research'
SCHEMA='''CREATE TABLE IF NOT EXISTS daily_shares(symbol TEXT NOT NULL,trade_date TEXT NOT NULL,shares_10k REAL,share_change_10k REAL,source TEXT NOT NULL,source_updated_at TEXT NOT NULL,PRIMARY KEY(symbol,trade_date)) WITHOUT ROWID;'''

def numeric(v):
    if v is None:return None
    v=float(v)
    return v if math.isfinite(v) else None

def stage():
    path=R/'staged.sqlite'
    if path.exists():raise SystemExit('Stage exists; retain it or choose a new run directory.')
    c=sqlite3.connect(path);c.executescript(SCHEMA+'CREATE TABLE minute_blocks(symbol TEXT,trade_date TEXT,payload BLOB,point_count INTEGER,PRIMARY KEY(symbol,trade_date)) WITHOUT ROWID;')
    count=points=0
    for i,file in enumerate(sorted((F/'results/series').glob('*.parquet'))):
        day=pd.read_parquet(file);baskets=json.load(gzip.open(F/'inputs/baskets'/(file.stem+'.json.gz'),'rt'))
        for symbol,s in day.groupby('symbol',sort=False):
            b=baskets[symbol];out=[];digest=hashlib.sha256(json.dumps(b,sort_keys=True).encode()).hexdigest()
            for r in s.itertuples(index=False):
                t=dt.datetime.fromisoformat(r.date+'T'+r.minute)-dt.timedelta(minutes=1);minute=t.strftime('%H:%M')
                if not ('09:30'<=minute<='12:00' or '13:00'<=minute<='16:00'):continue
                prices=[numeric(x) for x in [r.etf,r.mid,r.actual_settlement_buy,r.actual_settlement_sell]]
                if any(v is None or v<=0 for v in prices[1:]):continue
                if '11:30'<minute<'13:00' or minute>'15:00':prices[0]=None
                packed=[int(math.floor(v*(1000 if j==0 else 10000)+.5)) if v is not None else None for j,v in enumerate(prices)]
                stamp=t.isoformat(timespec='seconds')+'+08:00'
                q=dict(symbol=symbol,name=b['name'],trade_date=r.date,minute=minute,calculated_at=stamp,
                    pcf_sha256=digest,channel='shenzhen' if symbol.endswith('.SZ') else 'shanghai',
                    midpoint_fx=r.midpoint_fx,settlement_buy_fx=r.actual_buy_fx,
                    settlement_sell_fx=(r.actual_settlement_sell*r.unit-r.cash)/r.hkd_assets,
                    fx_model='official-final-daily',fx_status='historical_final',fx_actionable=False,
                    components=len(b['components']),priced=len(b['components']),unit=b['unit'],cash=b['cash'],
                    eligible_for_signal=False,mode='historical_reconstruction',run_id='local-pcf-minutes-20260912',
                    reasons=['HISTORICAL_FINAL_FX'],missing=[],stale=[],suspended=[],suspension_pending=[])
                out.append(dict(q=q,p=packed,v=[prices[0] is not None]*3))
            if not out:continue
            raw=json.dumps(out,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
            assert len(raw)<32*1024*1024
            payload=b'IOPZ1'+zlib.compress(raw,6)
            c.execute('INSERT INTO minute_blocks VALUES(?,?,?,?)',(symbol,b['date'],payload,len(out)));count+=1;points+=len(out)
        c.commit()
        if i%15==0:print('staged',i+1,'dates',count,'funddays',points,'points',flush=True)
    shares=0
    for file in sorted((F/'inputs/share_history').glob('*.json')):
        for r in json.loads(file.read_text())['rows']:
            date=r['share_date'];dt.date.fromisoformat(date)
            if date>='2026-09-12':continue
            c.execute('INSERT INTO daily_shares VALUES(?,?,?,?,?,?)',(file.stem,date,numeric(r.get('shares_10k')),numeric(r.get('share_change_10k')),'1navs/share-history',r.get('updated_at') or ''));shares+=1
    c.commit();assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok';c.close()
    summary=dict(fund_days=count,minute_points=points,share_days=shares,restored_source_minute_labels=True)
    (R/'stage_summary.json').write_text(json.dumps(summary,indent=2));print(summary,flush=True)

def apply(db):
    source=sqlite3.connect(R/'staged.sqlite');target=sqlite3.connect(db,timeout=30)
    tag=dt.datetime.now().strftime('%Y%m%d-%H%M%S');backup=R/f'backup-{tag}.sqlite'
    with sqlite3.connect(backup) as dst:target.backup(dst)
    target.executescript(SCHEMA)
    before={(s,d):hashlib.sha256(p).hexdigest() for s,d,p in target.execute('SELECT symbol,trade_date,payload FROM minute_blocks')}
    manifest=[];skipped=0
    for symbol,date,payload,n in source.execute('SELECT * FROM minute_blocks'):
        target.execute('BEGIN IMMEDIATE')
        exists=target.execute('SELECT 1 FROM minute_blocks WHERE symbol=? AND trade_date=? UNION ALL SELECT 1 FROM minutes WHERE symbol=? AND trade_date=? LIMIT 1',(symbol,date,symbol,date)).fetchone()
        if exists:skipped+=1
        else:
            target.execute('INSERT INTO minute_blocks VALUES(?,?,?,?)',(symbol,date,payload,n));manifest.append([symbol,date,n,hashlib.sha256(payload).hexdigest()])
        target.commit()
    with target:
        target.executemany('''INSERT INTO daily_shares VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET shares_10k=excluded.shares_10k,share_change_10k=excluded.share_change_10k,source=excluded.source,source_updated_at=excluded.source_updated_at WHERE excluded.source_updated_at>=daily_shares.source_updated_at''',source.execute('SELECT * FROM daily_shares'))
    for key,h in before.items():
        assert hashlib.sha256(target.execute('SELECT payload FROM minute_blocks WHERE symbol=? AND trade_date=?',key).fetchone()[0]).hexdigest()==h,'Existing block modified'
    assert target.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    summary=dict(backup=str(backup),inserted_fund_days=len(manifest),inserted_points=sum(x[2] for x in manifest),skipped_existing=skipped,existing_blocks_preserved=len(before),share_days=target.execute('SELECT COUNT(*) FROM daily_shares').fetchone()[0],inserted=manifest)
    (R/f'import-{tag}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print({k:v for k,v in summary.items() if k!='inserted'},flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',action='store_true');p.add_argument('--apply',type=Path);a=p.parse_args()
    if a.stage:stage()
    elif a.apply:apply(a.apply)
    else:p.error('Choose --stage or --apply DATABASE')
