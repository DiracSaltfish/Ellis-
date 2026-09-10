from pathlib import Path
import xml.etree.ElementTree as E
import json,urllib.request,concurrent.futures,hashlib,datetime
R=Path(__file__).resolve().parents[1];(R/'raw/hk').mkdir(exist_ok=True)
master={};checks=[]
for p in sorted((R/'raw/pcf').glob('*.xml')):
    tree=E.fromstring(p.read_bytes());top={n.tag.split('}')[-1]:(n.text or '').strip() for n in tree if len(n)==0}
    ok=top.get('TradingDay')=='20260908' and top.get('SecurityID')==p.name.split('_')[0]
    checks.append({'file':p.name,'identity_date_ok':ok,'top':top})
    if not ok:continue
    for n in tree.iter():
        if n.tag.split('}')[-1]!='Component':continue
        d={x.tag.split('}')[-1]:(x.text or '').strip() for x in n}
        c=d.get('UnderlyingSecurityID','')
        if d.get('UnderlyingSecurityIDSource')=='103' and c.isdigit() and len(c)==5:
            master.setdefault(c,{'name':d.get('UnderlyingSymbol'),'funds':[]})['funds'].append(top['SecurityID'])
(R/'pcf_identity_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2));(R/'inputs/hk_union_from_sz_pcf.json').write_text(json.dumps(master,ensure_ascii=False,indent=2))
def fetch(c):
    u=f'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=hk{c}';out={'code':c,'url':u,'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        baseline=R/f'baseline_513090/tencent_hk{c}_minute.json'
        if baseline.exists():b=baseline.read_bytes();out['reused_baseline']=True
        else:b=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0'}),timeout=12).read()
        p=R/f'raw/hk/hk{c}_minute.json';p.write_bytes(b);d=json.loads(b)['data'][f'hk{c}']['data']
        out.update(date=d.get('date'),rows=len(d.get('data',[])),sha256=hashlib.sha256(b).hexdigest(),path=str(p.relative_to(R)),status='TARGET_DATE' if d.get('date')=='20260908' else 'DATE_MISMATCH')
    except Exception as e:out.update(status='ERROR',error_type=type(e).__name__)
    return out
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:results=list(ex.map(fetch,sorted(master)))
(R/'hk_prefetch_manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
from collections import Counter
print(json.dumps({'pcf_identity_pass':sum(x['identity_date_ok'] for x in checks),'unique_hk':len(master),'minute_status':dict(Counter(x['status'] for x in results))}))
