"""Publish the existing accepted ledger without changing any conclusions."""
import csv,json,hashlib,datetime,argparse,io
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('source',type=Path);a=p.parse_args()
root=Path(__file__).resolve().parents[1]
raw=a.source.read_bytes();rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
index={}
for row in rows:
 symbol=row['code']+'.'+row['market'];assert symbol not in index,symbol
 index[symbol]={k:v for k,v in row.items() if k not in ['prospectus_file','prospectus_text_file']}
u=json.loads((root/'universe.json').read_text())['candidates']
records={c['symbol']:index.get(c['symbol']) for c in u}
out={'source_name':a.source.name,'source_sha256':hashlib.sha256(raw).hexdigest(),'imported_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'matched':sum(v is not None for v in records.values()),'total':len(records),'records':records}
(root/'web/ledger.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print({k:out[k] for k in ['matched','total','source_sha256']});print('unmatched',[k for k,v in records.items() if v is None])
