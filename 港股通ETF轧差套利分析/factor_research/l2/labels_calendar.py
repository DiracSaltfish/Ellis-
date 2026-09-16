"""Use observed PCF trading dates, excluding website holiday carry-forward share rows.
Only bridge omitted rows if their shares are unchanged and net changes are zero.
"""
from pathlib import Path
import json
import numpy as np

def labels():
    F=Path(__file__).resolve().parent.parent
    pcf=Path('/Volumes/EllisFiles/Stocksdata/PCF导出CSV')
    dates={p.name[:4]+'-'+p.name[4:6]+'-'+p.name[6:8] for p in pcf.glob('*_主表.csv')}
    out={};bridges=[]
    for f in (F/'inputs/share_history').glob('*.json'):
        raw=sorted(json.loads(f.read_text()).get('rows',[]),key=lambda x:x['share_date'])
        assert len({r['share_date'] for r in raw})==len(raw)
        rows=[r for r in raw if r['share_date'] in dates]
        for i,r in enumerate(rows):
            if i==0 or 'share_change_10k' not in r:continue
            prev=rows[i-1]
            intermediate=[v for v in raw if prev['share_date']<v['share_date']<r['share_date']]
            if any(abs(v['shares_10k']-prev['shares_10k'])>.021 or abs(v.get('share_change_10k',0))>.021 for v in intermediate):continue
            if abs(r['shares_10k']-prev['shares_10k']-r['share_change_10k'])>.021:continue
            if intermediate:bridges.append(dict(symbol=f.stem,date=r['share_date'],skipped=[v['share_date'] for v in intermediate]))
            out[(f.stem,r['share_date'])]=dict(net_shares=r['share_change_10k']*10000,prev_shares=prev['shares_10k']*10000,label_prev_date=prev['share_date'],lag_flow_pct=prev.get('share_change_pct',np.nan),lag5_flow_pct=sum(v.get('share_change_pct',0) for v in rows[max(0,i-5):i]))
    (F/'l2/calendar_bridges.json').write_text(json.dumps(bridges,ensure_ascii=False))
    return out
