"""Create a scoped 513090 event correction without changing Luna or pilot inputs."""
import json,hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parent
RUN=Path('/Users/ellis/.codex/worktrees/e50b/工具程序开发/港股通相关性分析')
dest=OUT/'event_review_engine';dest.mkdir(exist_ok=True)
source=(RUN/'scripts/run_pilot.py').read_text()
old="elif code=='02402' and '20260401'<=date<'20260504' and code in last_observed:"
new="elif code in last_observed and any(e['code']==code and e['start_date']<=date<e['end_date_exclusive'] for e in RUN_CONFIG.get('verified_halts',[])):"
assert source.count(old)==1
(dest/'run_pilot.py').write_text(source.replace(old,new))
(dest/'validate_pilot.py').write_text((RUN/'scripts/validate_pilot.py').read_text())
events=[{'code':'01788','start_date':'20260723','end_date_exclusive':'20260810',
 'start_time':'09:00 Asia/Hong_Kong','resumption_time':'09:00 Asia/Hong_Kong',
 'halt_source':'https://www.hkexnews.hk/listedco/listconews/sehk/2026/0723/2026072300081.htm',
 'resume_source':'https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0810/2026081000081.htm',
 'policy':'Retain full PCF quantity and last observed pre-halt mark; report frozen value; no claim about tradability or event jump protection.'}]
cfg=json.loads((RUN/'config/batch/513090_SH.json').read_text())
for key in ['basket_input','fx_source','future_data_dir','event_manifest','contract_map_path']:
 cfg[key]=str(RUN/cfg[key])
cfg['data_root']=str(OUT/'513090_event_review/data')
cfg['output_root']=str(OUT/'513090_event_review')
cfg['verified_halts']=events
cfg['review_change']='Officially verified 01788 halt only; all model hyperparameters unchanged. Event correction is a new run, old OOS not overwritten.'
(OUT/'513090_event_review_config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
(OUT/'verified_01788_event.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
(OUT/'event_engine_change.json').write_text(json.dumps({'base_script':str(RUN/'scripts/run_pilot.py'),'base_sha256':hashlib.sha256(source.encode()).hexdigest(),'corrected_sha256':hashlib.sha256(source.replace(old,new).encode()).hexdigest(),'old':old,'new':new},ensure_ascii=False,indent=2))
print(OUT/'513090_event_review_config.json')
