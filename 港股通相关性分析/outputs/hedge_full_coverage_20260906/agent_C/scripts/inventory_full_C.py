#!/usr/bin/env python3
"""Build the C full-coverage inventory and candidate map.

The inventory is intentionally an evidence ledger, not a conclusion table.
It checks the local archived panels and PCF inputs for every assigned fund and
records the three independent target pathways required by the full-coverage
contract.
"""
from __future__ import annotations

import csv, gzip, hashlib, json, shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
FULL = ROOT / 'outputs/hedge_full_coverage_20260906'
OUT = FULL / 'agent_C'
CONTROL = FULL / 'control'
ASSIGNMENTS = json.loads((CONTROL/'assignments.json').read_text(encoding='utf-8'))['C']
OLD = ROOT / 'outputs/hedge_rework_01_20260906/agent_C'
ARCHIVE = ROOT / 'batch_archive_20260906'
RUNS = ARCHIVE / 'runs'
NEW_INPUT = OLD / 'data/input_new_period_c_pcf_hk.jsonl.gz'
NOW = datetime.now(timezone.utc).isoformat()


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def read_new():
    rows=[]
    with gzip.open(NEW_INPUT,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
    by={}
    for r in rows: by.setdefault(str(r.get('fund_id')),[]).append(r)
    return by


def read_old_raw(code):
    p=ARCHIVE/'data/raw/candidates_v2'/f'{code}.jsonl.gz'
    rows=[]
    if p.exists():
        with gzip.open(p,'rt',encoding='utf-8') as f:
            for line in f:
                if line.strip(): rows.append(json.loads(line))
    return p,rows


def panel_stats(p):
    if not p.exists(): return {'exists':False,'rows':0,'days':0,'start':None,'end':None,'etf_price_rows':0,'basket_rows':0,'columns':[],'sha256':None}
    try:
        import pandas as pd
        df=pd.read_parquet(p)
        return {'exists':True,'rows':len(df),'days':int(df['date'].nunique()) if 'date' in df else 0,'start':str(df['date'].min()) if 'date' in df else None,'end':str(df['date'].max()) if 'date' in df else None,'etf_price_rows':int(df['etf_price'].notna().sum()) if 'etf_price' in df else 0,'basket_rows':int(df['basket_hkd'].notna().sum()) if 'basket_hkd' in df else 0,'columns':list(df.columns),'sha256':sha(p)}
    except Exception as e:
        return {'exists':True,'rows':None,'days':None,'start':None,'end':None,'etf_price_rows':None,'basket_rows':None,'columns':[],'sha256':sha(p),'error':f'{type(e).__name__}: {e}'}


def candidates(index_name):
    n=index_name or ''
    if '生物科技' in n or '创新药' in n:
        return [
            {'tool_id':'HBI_FUT','asset_type':'HKFE_FUTURES','family':'HSBIO','reason':'恒生生物科技行业期货；直接行业风险因子'},
            {'tool_id':'03069','asset_type':'HKEX_ETF','family':'HSBIO','reason':'恒生生物科技香港ETF；行业同指数代理'},
            {'tool_id':'03174','asset_type':'HKEX_ETF','family':'HSBIO','reason':'恒生生物科技香港ETF；行业同指数代理'},
            {'tool_id':'HTI_FUT','asset_type':'HKFE_FUTURES','family':'HSTECH','reason':'创新药/生物科技的科技风险备选'},
            {'tool_id':'HHI_FUT','asset_type':'HKFE_FUTURES','family':'HHI','reason':'港股大型成分的宽基备选'},
            {'tool_id':'02828','asset_type':'HKEX_ETF','family':'HHI','reason':'恒生国企香港ETF宽基备选'},
        ]
    if '医疗' in n:
        return [
            {'tool_id':'HBI_FUT','asset_type':'HKFE_FUTURES','family':'HSBIO','reason':'医疗生物行业风险备选'},
            {'tool_id':'03069','asset_type':'HKEX_ETF','family':'HSBIO','reason':'生物科技行业香港ETF备选'},
            {'tool_id':'03174','asset_type':'HKEX_ETF','family':'HSBIO','reason':'生物科技行业香港ETF备选'},
            {'tool_id':'HTI_FUT','asset_type':'HKFE_FUTURES','family':'HSTECH','reason':'医疗成长股科技风格备选'},
            {'tool_id':'HHI_FUT','asset_type':'HKFE_FUTURES','family':'HHI','reason':'大型港股宽基备选'},
            {'tool_id':'02828','asset_type':'HKEX_ETF','family':'HHI','reason':'恒生国企香港ETF宽基备选'},
        ]
    if '消费' in n:
        return [
            {'tool_id':'HSI_FUT','asset_type':'HKFE_FUTURES','family':'HSI','reason':'恒生指数宽基消费风险备选'},
            {'tool_id':'HHI_FUT','asset_type':'HKFE_FUTURES','family':'HHI','reason':'大型国企消费风险备选'},
            {'tool_id':'HTI_FUT','asset_type':'HKFE_FUTURES','family':'HSTECH','reason':'成长消费风险备选'},
            {'tool_id':'02800','asset_type':'HKEX_ETF','family':'HSI','reason':'恒生指数香港ETF宽基代理'},
            {'tool_id':'02828','asset_type':'HKEX_ETF','family':'HHI','reason':'恒生国企香港ETF宽基代理'},
            {'tool_id':'03032','asset_type':'HKEX_ETF','family':'HSTECH','reason':'恒生科技香港ETF风格备选'},
            {'tool_id':'03033','asset_type':'HKEX_ETF','family':'HSTECH','reason':'恒生科技香港ETF风格备选'},
        ]
    return [
        {'tool_id':'HSI_FUT','asset_type':'HKFE_FUTURES','family':'HSI','reason':'恒生指数宽基代理'},
        {'tool_id':'HHI_FUT','asset_type':'HKFE_FUTURES','family':'HHI','reason':'恒生国企宽基代理'},
        {'tool_id':'HTI_FUT','asset_type':'HKFE_FUTURES','family':'HSTECH','reason':'恒生科技风格备选'},
        {'tool_id':'02800','asset_type':'HKEX_ETF','family':'HSI','reason':'恒生指数香港ETF代理'},
        {'tool_id':'02828','asset_type':'HKEX_ETF','family':'HHI','reason':'恒生国企香港ETF代理'},
        {'tool_id':'03032','asset_type':'HKEX_ETF','family':'HSTECH','reason':'恒生科技香港ETF代理'},
        {'tool_id':'03033','asset_type':'HKEX_ETF','family':'HSTECH','reason':'恒生科技香港ETF代理'},
    ]


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'data').mkdir(exist_ok=True)
    shutil.copy2(NEW_INPUT,OUT/'data/input_new_period_c_pcf_hk.jsonl.gz')
    new=read_new()
    inv=[]; cmap=[]; tasks=[]
    for i,a in enumerate(ASSIGNMENTS,1):
        fid=a['fund_id']; code=fid.split('.')[0]; raw_path,old=read_old_raw(code); new_rows=new.get(code,[]); panel=RUNS/fid/'20260906_batch/data/normalized/pilot_minutes.parquet'; ps=panel_stats(panel)
        all_dates=sorted({str(x.get('date')) for x in old+new_rows})
        pcf_qty_missing=sum(1 for x in old+new_rows for c in (x.get('components') or []) if not (c.get('数量股') or c.get('qty')))
        pcf_components=sum(len(x.get('components') or []) for x in old+new_rows)
        new_dates=sorted({str(x.get('date')) for x in new_rows})
        panel_target='ETF_MARKET_PRICE' if ps['etf_price_rows'] else None
        basket_target='PCF_BASKET' if ps['basket_rows'] or (old or new_rows) else None
        struct=candidates(a.get('index_name'))
        item={'fund_id':fid,'fund_name':a['fund_name'],'owner':'C','index_id':a.get('index_id'),'index_name':a.get('index_name'),'scope_status':'UNVERIFIED','scope_evidence_ids':[a.get('scope_evidence_id')],'listing_date':a.get('listing_date'),'inventory_at_utc':NOW,'pcf_old_path':str(raw_path) if old else None,'pcf_old_exists':bool(old),'pcf_old_rows':len(old),'pcf_new_rows':len(new_rows),'pcf_new_dates':new_dates,'pcf_all_dates':all_dates,'pcf_component_rows':pcf_components,'pcf_quantity_missing_rows':pcf_qty_missing,'panel_path':str(panel) if ps['exists'] else None,'panel_stats':ps,'target_paths_attempted':['PCF_BASKET','ETF_MARKET_PRICE','INDEX_STRUCTURAL'],'target_paths_with_data':[x for x in [basket_target,panel_target] if x]+['INDEX_STRUCTURAL'],'economic_candidates':struct,'status':'INVENTORIED'}
        inv.append(item)
        for c in struct: cmap.append({'fund_id':fid,'target_type':'PCF_BASKET' if basket_target else ('ETF_MARKET_PRICE' if panel_target else 'INDEX_STRUCTURAL'),'candidate_id':c['tool_id'],'asset_type':c['asset_type'],'risk_family':c['family'],'economic_reason':c['reason'],'source_status':'LOCK_PENDING_B_ENGINE','actual_data_status':'AVAILABLE' if c['tool_id'] in ['HSI_FUT','HHI_FUT','HTI_FUT'] or panel_target else 'TO_BE_TESTED','tested':False,'sample_group_id':None,'notes':'candidate map frozen before candidate metrics'})
        for phase,action in [('SCOPE','official product/range evidence and index identity'),('CANDIDATES','economic candidate map before performance'),('INVENTORY','PCF, ETF market-price and structural pathway inventory'),('COMPUTE','run all available target paths through common engine'),('EVENTS','PCF constituent event review'),('QA','row-level results and method checks'),('DELIVER','mapping and workbook delivery')]:
            tasks.append({'task_id':f'C-{fid}-{phase}','fund_id':fid,'phase':phase,'status':'DONE' if phase in ('CANDIDATES','INVENTORY') else 'TODO','started_at_utc':NOW if phase in ('CANDIDATES','INVENTORY') else None,'finished_at_utc':NOW if phase in ('CANDIDATES','INVENTORY') else None,'input_paths':[str(CONTROL/'assignments.json'),str(NEW_INPUT)],'command':'python inventory_full_C.py','output_paths':[str(OUT/'data/inventory.jsonl'),str(OUT/'data/candidate_map.jsonl')],'result_summary':f"{fid}: old_pcf_rows={len(old)}, new_pcf_rows={len(new_rows)}, panel_etf_price_rows={ps['etf_price_rows']}",'next_action':'run common engine or record exact unavailable request'})
        if i%10==0 or i==len(ASSIGNMENTS):
            (OUT/'STATUS.md').write_text(f'# C full coverage status\n\n更新时间：{datetime.now(timezone.utc).isoformat()}\n\n- inventory_processed={i}/55\n- candidate_maps={i}/55\n- remaining_inventory={len(ASSIGNMENTS)-i}\n- compute_status=NOT_STARTED\n- research_status=RUNNING\n',encoding='utf-8')
    def write_jsonl(path,rows):
        with path.open('w',encoding='utf-8') as f:
            for r in rows: f.write(json.dumps(r,ensure_ascii=False)+'\n')
    write_jsonl(OUT/'data/inventory.jsonl',inv); write_jsonl(OUT/'data/candidate_map.jsonl',cmap); write_jsonl(OUT/'data/tasks.jsonl',tasks)
    (OUT/'data/inventory_summary.json').write_text(json.dumps({'generated_at_utc':NOW,'fund_count':len(inv),'pcf_old_fund_count':sum(x['pcf_old_exists'] for x in inv),'new_pcf_fund_count':sum(x['pcf_new_rows']>0 for x in inv),'etf_market_panel_fund_count':sum(x['panel_stats']['etf_price_rows']>0 for x in inv),'basket_path_data_fund_count':sum('PCF_BASKET' in x['target_paths_with_data'] for x in inv),'structural_candidate_fund_count':len(inv),'assignment_hash':sha(CONTROL/'assignments.json')},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(json.loads((OUT/'data/inventory_summary.json').read_text()),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
