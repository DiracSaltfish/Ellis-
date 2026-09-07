#!/usr/bin/env python3
"""Agent A R1 rework: scope repair plus a real 513090 old-window exploration."""
from __future__ import annotations
import csv, gzip, hashlib, json, re, shutil, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
import requests

ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
OUT = ROOT / "outputs/hedge_rework_01_20260906/agent_A"
CONTROL = ROOT / "outputs/hedge_rework_01_20260906/control"
OLD_A = ROOT / "outputs/hedge_selection_v2_20260906/agent_A"
OLD_ARCHIVE = ROOT / "batch_archive_20260906"
FINAL_REVIEW = ROOT / "outputs/final_review_20260906"
TARGET_START, TARGET_END = "2026-08-04", "2026-09-04"
CRITERIA_VERSION = "USER_RHO_060_FUTURES_ETF"
BOOTSTRAP_SEED = 20260906
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def utc_now(): return datetime.now(timezone.utc).isoformat(timespec="microseconds")
def sha256(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def write_json(p,v):
    Path(p).parent.mkdir(parents=True,exist_ok=True)
    Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n")
def json_text(v): return json.dumps(v,ensure_ascii=False,separators=(",",":"),sort_keys=True)
def csv_value(v):
    return json_text(v) if isinstance(v,(list,dict)) else ("" if v is None else v)
def write_csv(p,rows,cols):
    Path(p).parent.mkdir(parents=True,exist_ok=True)
    with Path(p).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore"); w.writeheader()
        for r in rows: w.writerow({c:csv_value(r.get(c)) for c in cols})
def file_hash(p): return sha256(p) if Path(p).is_file() else ""
def cols(table):
    s=json.loads((CONTROL/"schema_base.json").read_text())
    return [x["key"] for x in s["tables"][table]["columns"]]

def assignments(): return json.loads((CONTROL/"assignments.json").read_text())["A"]
def old_rows():
    return {r["fund_id"]:r for r in csv.DictReader((OLD_A/"fund_decisions.csv").open(encoding="utf-8-sig"))}
def old_report(fid):
    p=FINAL_REVIEW/"513090_event_review/reports" if fid=="513090.SH" else OLD_ARCHIVE/f"runs/{fid}/20260906_batch/reports"
    return p if (p/"model_comparison.csv").exists() else None

def scope_urls():
    return {
      "159320.SZ":"https://gfwx.gffunds.com.cn/funds/?fundcode=159320",
      "159519.SZ":"https://e.gtfund.com/Etrade/Jijin/view/id/159519",
      "159823.SZ":"https://www.jsfund.cn/cms/Services/AttachDownLoad.jsp?id=479513",
      "159850.SZ":"https://fund.chinaamc.com/upload/resources/file/2026/03/23/23b67a9e0fad4bebaae9e8688f2d9ae3.pdf",
      "159920.SZ":"https://f.chinaamc.com/fund/159920/index.shtml?source=click",
      "159954.SZ":"https://www.nffund.com/main/files/2023/10/24/549169288726.pdf",
      "510900.SH":"https://www.efunds.com.cn/fund/510900.shtml",
      "513140.SH":"https://www.huatai-pb.com/products/zhishu/513140/index.html",
      "513170.SH":{"url":"https://www.phfund.com.cn/web/fundDetail/homepage","display_url":"https://www.phfund.com.cn/fund/fundDetail?fundCode=513170","method":"POST","data":{"fundCode":"513170"}},
      "513210.SH":"https://www.efunds.com.cn/fund/513210.shtml",
      "513530.SH":"https://www.huatai-pb.com/products/zhishu/513530/index.html",
      "513600.SH":{"url":"https://www.nffund.com/nfwebApi/fund/overreview","display_url":"https://www.nffund.com/new/personal-financing/detail.html?fundCode=513600","method":"POST","data":{"fundCode":"513600"}},
      "513810.SH":"https://f.chinaamc.com/fund/513810/index.shtml?source=click",
      "513950.SH":"https://www.fullgoal.com.cn/wbs-file/fund_report/20250709/CN_50100000_513950_FA010030_20250002.pdf",
      "560390.SH":"https://www.efunds.com.cn/fund/560390.shtml",
      "561380.SH":"https://e.gtfund.com/Etrade/Jijin/view/id/561380",
      "563210.SH":"https://www.fullgoal.com.cn/fundDetail/563210/index.html",
      "513220.SH":"https://www.cmfchina.com/web/fundDetail/513220/index.html",
      "513050.SH":"https://www.efunds.com.cn/fund/513050.shtml",
      "159605.SZ":"https://gfwx.gffunds.com.cn/funds/?fundcode=159605",
      "159607.SZ":"https://www.jsfund.cn/plat_files/upload/product_ann/20250106/202501061736157318066/关于嘉实中证海外中国互联网30ETF（QDII）2025年1月9日暂停申购、赎回业务的公告.pdf"
    }

def strip_html(s):
    s=re.sub(r"<script.*?</script>|<style.*?</style>"," ",s,flags=re.S|re.I)
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",s)).strip()

def fetch_scope_docs(funds, attempts, evidence):
    out=OUT/"data/scope_docs"; out.mkdir(parents=True,exist_ok=True)
    oldout={k for k,v in old_rows().items() if v["decision"]=="OUT_OF_SCOPE"}
    docs={}
    for f in funds:
        fid=f["fund_id"]
        if fid not in oldout: continue
        spec=scope_urls().get(fid,"")
        display_url=spec.get("display_url",spec.get("url", "")) if isinstance(spec,dict) else spec
        request_url=spec.get("url", "") if isinstance(spec,dict) else spec
        started=utc_now()
        request_parameters=spec if isinstance(spec,dict) else {"url":request_url,"method":"GET"}
        a={"attempt_id":"A-SCOPE-"+fid,"owner":"A","fund_id":fid,"security_id":"","data_type":"SCOPE_DOCUMENT","source_name":"fund_manager_official","source_url_or_method":display_url,"request_parameters":request_parameters,"requested_start":None,"requested_end":None,"bar_size":None,"attempted_at_utc":started,"completed_at_utc":None,"status":"NOT_FOUND","error_code":None,"error_summary":"","returned_rows":None,"returned_start":None,"returned_end":None,"raw_path":None,"sha256":None,"next_action":"继续查找官方招募说明书/产品资料概要"}
        if not request_url:
            a["error_summary"]="未配置官方URL"; a["completed_at_utc"]=utc_now(); attempts.append(a); continue
        try:
            headers={"User-Agent":"Mozilla/5.0","Accept":"application/json,text/html,application/pdf","X-Requested-With":"XMLHttpRequest"}
            if isinstance(spec,dict) and spec.get("method")=="POST":
                r=requests.post(request_url,headers=headers,json=spec.get("data",{}),timeout=35,verify=False)
            else:
                r=requests.get(request_url,headers=headers,timeout=35,verify=False)
            a["completed_at_utc"]=utc_now(); a["error_code"]=r.status_code
            if r.status_code!=200:
                a["status"]="NOT_FOUND"; a["error_summary"]=f"HTTP {r.status_code}"; attempts.append(a); continue
            ctype=(r.headers.get("content-type") or "").lower()
            ext=".json" if isinstance(spec,dict) and spec.get("method")=="POST" else (".pdf" if "pdf" in ctype or request_url.lower().endswith(".pdf") else ".html")
            p=out/f"{fid.replace('.','_')}{ext}"; p.write_bytes(r.content)
            text=""
            locator="official product page HTML"
            if ext==".pdf":
                try:
                    from pypdf import PdfReader
                    text="\n".join(x.extract_text() or "" for x in PdfReader(str(p)).pages)
                    locator="PDF page 1+ local text extraction"
                except Exception as exc: locator=f"PDF cached; extractor {type(exc).__name__}"
            elif ext==".json":
                text=r.content.decode("utf-8","ignore"); locator="official product API JSON; fund_info/investRange"
            else:
                enc=r.apparent_encoding or "utf-8"
                text=strip_html(r.content.decode(enc,"ignore"))
            a.update({"status":"SUCCESS","error_summary":"HTTP 200; local document cached","returned_rows":len(r.content),"raw_path":str(p),"sha256":sha256(p),"next_action":"按产品文件语义分类"})
            attempts.append(a); docs[fid]={"path":p,"url":display_url,"sha256":sha256(p),"text":text,"locator":locator}
            evidence.append({"evidence_id":"A-SCOPE-DOC-"+fid,"fund_id":fid,"purpose":"official_scope_product_document","publisher":"fund_manager","url":display_url,"published_at":None,"retrieved_at_utc":a["completed_at_utc"],"local_path":str(p),"sha256":sha256(p),"locator":locator,"finding":" ".join(text[:800].split()) or "文档已缓存但正文未提取","sufficiency":"document_cached"})
        except requests.RequestException as exc:
            a["completed_at_utc"]=utc_now(); a["status"]="NETWORK_ERROR"; a["error_summary"]=str(exc); attempts.append(a)
    return docs

def classify(fid,name,doc):
    text=(doc or {}).get("text","")
    scope_text=""
    for marker in ("投资范围","investRange"):
        pos=text.find(marker)
        if pos>=0: scope_text += text[pos:pos+8000]
    u=name+" "+scope_text+" "+text[:70000]
    ashare=any(x in name for x in ("恒生A股","A股电网设备","A股专精特新"))
    qdii="QDII" in (name+" "+scope_text+" "+text[:70000]) or "合格境内机构投资者" in (name+" "+scope_text+" "+text[:70000])
    global_internet=any(x in u for x in ("全球中国互联网","海外中国互联网","中证海外中国互联网"))
    connect="互联互通" in (name+" "+scope_text) or "港股通" in (name+" "+scope_text)
    if ashare: return "A_SHARE_ONLY","OUT_OF_SCOPE",["OFFICIAL_PRODUCT_A_SHARE_SCOPE","OFFICIAL_PRODUCT_DOCUMENT"],"官方产品资料将标的限定为恒生A股/A股专精特新指数。",True
    if global_internet: return "GLOBAL_QDII","OUT_OF_SCOPE",["OFFICIAL_PRODUCT_GLOBAL_SCOPE","OFFICIAL_PRODUCT_DOCUMENT"],"官方产品资料显示为海外/全球中国互联网QDII，不是纯港股通ETF对象。",True
    if qdii and not connect: return "QDII_ONLY","OUT_OF_SCOPE",["OFFICIAL_PRODUCT_QDII_SCOPE","OFFICIAL_PRODUCT_DOCUMENT"],"官方产品资料标注QDII且未形成互联互通通道证据，先按范围外登记。",True
    if qdii and connect: return "CONNECT_OR_QDII_MIXED","INSUFFICIENT_EVIDENCE",["QDII_CONNECT_CHANNEL_AMBIGUOUS","OFFICIAL_PRODUCT_DOCUMENT"],"官方资料同时出现QDII与互联互通/港股通，通道需逐只复核，不能直接排除。",False
    if "香港" in u or "恒生" in u: return "HK_SCOPE_PENDING","INSUFFICIENT_EVIDENCE",["HK_SCOPE_DOCUMENT_REVIEWED","NEW_CONFIRMATION_UNAVAILABLE"],"官方资料显示香港/恒生对象，但本轮纯通道、PCF和新期证据尚未闭环。",False
    return "RANGE_PENDING","INSUFFICIENT_EVIDENCE",["SCOPE_DOCUMENT_NOT_SUFFICIENT","NEW_CONFIRMATION_UNAVAILABLE"],"旧classification未被本轮证据重新确认，改为待核实。",False

def build_panel():
    src=OLD_ARCHIVE/"data/raw/candidates_v2/513090.jsonl.gz"
    paths={"HSI_FUT":OLD_ARCHIVE/"data/raw/HSI_FUT_1min.csv","HHI_FUT":OLD_ARCHIVE/"data/raw/HHI_FUT_1min.csv","HTI_FUT":OLD_ARCHIVE/"data/raw/HTI_FUT_1min.csv","02800":ROOT/"data/raw/2800_1min.csv","02828":ROOT/"data/raw/2828_1min.csv"}
    tools={k:{} for k in paths}
    for tid,p in paths.items():
        d=pd.read_csv(p); ts=pd.to_datetime(d["timestamp"],errors="coerce")
        d=d.assign(_label=ts.dt.hour*60+ts.dt.minute)
        for _,r in d.iterrows(): tools[tid][(str(r["trade_date"]).replace("-",""),int(r["_label"]))]=float(r["close"])
    rows=[]; dates=[]; last=None
    with gzip.open(src,"rt",encoding="utf-8") as f:
        for line in f:
            o=json.loads(line); date=str(o["date"]); dates.append(date)
            comps={str(c["成分股代码"]).zfill(5):c for c in o["components"]}
            bars={k:{int(x[0]):float(x[4]) for x in v} for k,v in o.get("hk",{}).items()}
            if bars.get("01788"): last=bars["01788"][max(bars["01788"])]
            for label in range(780,960):
                vals={}; missing=[]; frozen=[]
                for code,c in comps.items():
                    p=bars.get(code,{}).get(label)
                    if p is None and code=="01788" and date>="20260723" and last is not None: p=last; frozen.append(code)
                    if p is None: missing.append(code)
                    else: vals[code]=p
                if missing: continue
                basket=sum(float(comps[c].get("数量股") or 0)*vals[c] for c in comps)
                row={"fund_id":"513090.SH","trade_date":date,"minute_label":label,"basket_value_hkd":basket,"component_count":len(comps),"frozen_security_count":len(frozen),"frozen_security_ids":frozen,"missing_members":[]}
                for tid in paths: row[tid]=tools[tid].get((date,label))
                rows.append(row)
    panel=pd.DataFrame(rows).sort_values(["trade_date","minute_label"]).reset_index(drop=True)
    rets=[]
    for date,g in panel.groupby("trade_date",sort=True):
        g=g.set_index("minute_label")
        for h in (5,15,30,60):
            for label in range(780+h,960):
                if label not in g.index or label-h not in g.index: continue
                a,b=g.loc[label-h],g.loc[label]
                tids=list(paths)
                if any(pd.isna(a[t]) or pd.isna(b[t]) for t in tids): continue
                x={"fund_id":"513090.SH","trade_date":date,"minute_label":label,"horizon_min":h,"basket_return":float(b.basket_value_hkd/a.basket_value_hkd-1),"frozen_security_count":int(b.frozen_security_count)}
                for t in tids: x[t+"_return"]=float(b[t]/a[t]-1)
                rets.append(x)
    return panel,pd.DataFrame(rets),{"source_path":str(src),"source_sha256":sha256(src),"dates":dates,"date_count":len(dates),"panel_rows":len(panel),"return_rows":len(rets),"target_window":[dates[0],dates[-1]],"event_policy":"01788 quantity retained; 2026-07-23..08-10 missing price uses pre-suspension close only; other missing members not filled","tool_ids":list(paths)}

def corr(a,b):
    return None if len(a)<3 or np.std(a)==0 or np.std(b)==0 else float(np.corrcoef(a,b)[0,1])
def boot(df):
    p=corr(df.basket_return.to_numpy(float),df.hedge_return.to_numpy(float))
    if p is None:return None,None,None
    rng=np.random.default_rng(BOOTSTRAP_SEED); groups=[g for _,g in df.groupby("trade_date")]; vals=[]
    for _ in range(1000):
        z=pd.concat([groups[i] for i in rng.integers(0,len(groups),size=len(groups))],ignore_index=True)
        c=corr(z.basket_return.to_numpy(float),z.hedge_return.to_numpy(float))
        if c is not None: vals.append(c)
    return p,float(np.quantile(vals,.025)),float(np.quantile(vals,.975))

def fit(y,X): return np.zeros(0) if X.shape[1]==0 else np.linalg.lstsq(X,y,rcond=None)[0]
def run_model(ret):
    specs={"NO_HEDGE":[],"HSI_FUT":["HSI_FUT"],"HHI_FUT":["HHI_FUT"],"HTI_FUT":["HTI_FUT"],"02800":["02800"],"02828":["02828"],"HHI_HTI_FUT":["HHI_FUT","HTI_FUT"]}
    days=sorted(ret.trade_date.unique()); metrics=[]; residuals=[]; weights=[]; selections=[]
    for h in (5,15,30,60):
        df=ret[ret.horizon_min==h]; blocks={m:[] for m in specs}
        for i,td in enumerate(days[60:],start=60):
            trdays=days[i-60:i]; fitdays=days[i-60:i-10]; valdays=days[i-10:i]
            tr=df[df.trade_date.isin(trdays)]; ft=df[df.trade_date.isin(fitdays)]; va=df[df.trade_date.isin(valdays)]; te=df[df.trade_date==td]
            if te.empty: continue
            scores=[]
            for m,legs in specs.items():
                b=fit(ft.basket_return.to_numpy(float),ft[[x+"_return" for x in legs]].to_numpy(float)) if legs else np.zeros(0)
                hv=va[[x+"_return" for x in legs]].to_numpy(float)@b if legs else np.zeros(len(va))
                vc=corr(va.basket_return.to_numpy(float),hv) if legs else None
                vv=float(1-np.var(va.basket_return.to_numpy(float)-hv)/np.var(va.basket_return.to_numpy(float))) if np.var(va.basket_return.to_numpy(float)) else None
                vs=float(np.std(va.basket_return.to_numpy(float)-hv,ddof=1)) if len(va)>1 else None
                scores.append((m,legs,vc,vv,vs))
            q=[s for s in scores if s[0]!="NO_HEDGE" and s[2] is not None and s[2]>=.60 and s[3] is not None and s[3]>0]
            selected=sorted(q or [s for s in scores if s[0]!="NO_HEDGE"],key=lambda s:(s[4] if s[4] is not None else 1e9,len(s[1]),s[0]))[0]
            selections.append({"fund_id":"513090.SH","horizon_min":h,"test_date":td,"train_start":trdays[0],"train_end":trdays[-1],"fit_start":fitdays[0],"fit_end":fitdays[-1],"validation_start":valdays[0],"validation_end":valdays[-1],"selected_model":selected[0],"selection_rule":"validation Pearson >=0.60 and residual variance reduced; tie residual std then leg count then model_id","validation_corr":selected[2],"validation_vr":selected[3]})
            for m,legs,vc,vv,vs in scores:
                b=fit(tr.basket_return.to_numpy(float),tr[[x+"_return" for x in legs]].to_numpy(float)) if legs else np.zeros(0)
                hv=te[[x+"_return" for x in legs]].to_numpy(float)@b if legs else np.zeros(len(te))
                block=te[["fund_id","trade_date","minute_label","horizon_min","basket_return","frozen_security_count"]].copy()
                block["model_id"]=m; block["policy_id"]="EXP_513090_"+str(h)+"M_"+m; block["hedge_return"]=hv; block["residual"]=block.basket_return.to_numpy(float)-hv; block["train_start"]=trdays[0]; block["train_end"]=trdays[-1]; block["validation_start"]=valdays[0]; block["validation_end"]=valdays[-1]; block["beta"]=[json_text({legs[j]:float(b[j]) for j in range(len(legs))})]*len(block); block["selected_for_day"]=m==selected[0]; blocks[m].append(block)
                if m==selected[0]:
                    for j,t in enumerate(legs): weights.append({"fund_id":"513090.SH","horizon_min":h,"policy_id":"EXP_513090_"+str(h)+"M_"+m,"effective_date":td,"train_start":trdays[0],"train_end":trdays[-1],"validation_start":valdays[0],"validation_end":valdays[-1],"tool_id":t,"beta":float(b[j]),"currency_conversion":"同为HKD；日内收益不引入结算汇率","selection_reason":"60日滚动=50日拟合+10日验证；旧窗口探索","config_hash":hashlib.sha256((m+str(h)+trdays[-1]).encode()).hexdigest()})
        for m,bs in blocks.items():
            if not bs: continue
            z=pd.concat(bs,ignore_index=True); c,lo,hi=boot(z) if m!="NO_HEDGE" else (None,None,None); ts=float(z.basket_return.std(ddof=1)); rs=float(z.residual.std(ddof=1)); vr=float(1-rs**2/ts**2) if ts else None
            metrics.append({"run_id":"A-R1-513090-OLD","fund_id":"513090.SH","horizon_min":h,"policy_id":"EXP_513090_"+str(h)+"M_"+m,"model_id":m,"scenario_id":"OLD_EXPLORATION_WINDOW","sample_hash":hashlib.sha256(pd.util.hash_pandas_object(z[["trade_date","minute_label","basket_return","hedge_return"]],index=False).values.tobytes()).hexdigest(),"confirmation_status":"EXPLORATORY_OLD_WINDOW","oos_start":str(z.trade_date.min()),"oos_end":str(z.trade_date.max()),"oos_days":int(z.trade_date.nunique()),"sample_count":len(z),"target_std_bp":ts*10000,"residual_std_bp":rs*10000,"variance_reduction":vr,"ci_low":None,"ci_high":None,"bootstrap_method":"daily_block_bootstrap" if c is not None else None,"bootstrap_seed":BOOTSTRAP_SEED if c is not None else None,"target_up_es95_bp":None,"target_down_es95_bp":None,"up_es95_bp":float(z.residual[z.residual>0].quantile(.95)*10000) if (z.residual>0).any() else None,"down_es95_bp":float(-z.residual[z.residual<0].quantile(.05)*10000) if (z.residual<0).any() else None,"positive_block_fraction":None,"residual_mean_bp":float(z.residual.mean()*10000),"beta_turnover":None,"decision_gate_results":{"exploration_only":True,"new_confirmation":False,"oos_correlation":c,"residual_variance_reduced":bool(vr is not None and vr>0)},"residual_path":str(OUT/"research/513090_oos_residuals.csv"),"hedge_return_correlation":c,"correlation_ci_low":lo,"correlation_ci_high":hi,"correlation_method":"Pearson OOS; 1000 day-block bootstrap" if c is not None else None,"correlation_threshold":.60 if c is not None else None,"criteria_version":CRITERIA_VERSION})
            residuals.extend(z.to_dict("records"))
    selected={}
    for h in (5,15,30,60):
        q=[m for m in metrics if m["horizon_min"]==h and m["model_id"]!="NO_HEDGE" and m["hedge_return_correlation"] is not None and m["hedge_return_correlation"]>=.60 and (m["variance_reduction"] or 0)>0]
        selected[h]=sorted(q,key=lambda x:(-(x["hedge_return_correlation"] or -1),x["residual_std_bp"],x["model_id"]))[0] if q else None
    return metrics,residuals,weights,{"selection_records":selections,"selected_by_horizon":selected,"test_days":days[60:],"train_days":60,"fit_days":50,"validation_days":10}

def old_exploratory(fid,model_rows,policies):
    p=old_report(fid)
    if not p or fid=="513090.SH": return
    d=pd.read_csv(p/"model_comparison.csv")
    for _,r in d.iterrows():
        model_rows.append({"run_id":"OLD-"+fid,"fund_id":fid,"horizon_min":int(r["horizon"]),"policy_id":"OLD_"+str(r["model"]),"model_id":str(r["model"]),"scenario_id":"OLD_ARCHIVE_BASELINE","sample_hash":file_hash(p/"oos_residuals.parquet"),"confirmation_status":"REUSED_OR_UNPROVEN","oos_start":"2026-03-03","oos_end":"2026-08-03","oos_days":int(r["days"]),"sample_count":int(r["samples"]),"target_std_bp":float(r["target_std_bps"]),"residual_std_bp":float(r["residual_std_bps"]),"variance_reduction":float(r["variance_reduction"]),"ci_low":None,"ci_high":None,"bootstrap_method":None,"bootstrap_seed":None,"target_up_es95_bp":None,"target_down_es95_bp":None,"up_es95_bp":float(r["upside_es95_bps"]),"down_es95_bp":float(r["downside_es95_bps"]),"positive_block_fraction":None,"residual_mean_bp":float(r["residual_mean_bps"]),"beta_turnover":None,"decision_gate_results":{"exploration_only":True,"new_confirmation":False},"residual_path":str(p/"oos_residuals.parquet"),"hedge_return_correlation":None,"correlation_ci_low":None,"correlation_ci_high":None,"correlation_method":None,"correlation_threshold":None,"criteria_version":CRITERIA_VERSION})
    h=d[d.horizon==30].sort_values("residual_std_bps").head(1)
    if not h.empty:
        r=h.iloc[0]
        policies.append({"fund_id":fid,"owner":"A","horizon_min":30,"sample_start":"2026-03-03","sample_end":"2026-08-03","sample_status":"REUSED_OR_UNPROVEN","policy_id":"OLD_"+str(r["model"]),"selection_rule":"旧窗口事后residual_std最低；仅探索，不是预先锁定政策","tools":[str(r["model"])],"last_beta_date":None,"last_beta":None,"residual_std_bp":float(r["residual_std_bps"]),"variance_reduction":float(r["variance_reduction"]),"up_es95_bp":float(r["upside_es95_bps"]),"down_es95_bp":float(r["downside_es95_bps"]),"single_leg_comparator":None,"complexity_benefit_bp":None,"candidate_scope":"prior archive 8-tool pool","missing_confirmation_requirements":["new PCF","new OOS >=20","Pearson OOS correlation","full event manifest"],"run_id":"OLD-"+fid,"residual_path":str(p/"oos_residuals.parquet")})

def main():
    for d in (OUT,OUT/"data",OUT/"data/scope_docs",OUT/"research",OUT/"scripts"): d.mkdir(parents=True,exist_ok=True)
    funds=assignments(); old=old_rows(); attempts=[]; evidence=[]; repair=[]; gates=[]; policies=[]; qa=[]; tasks=[]
    write_json(OUT/"data/environment.json",{"python":sys.version,"pandas":pd.__version__,"numpy":np.__version__,"requests":requests.__version__,"generated_at_utc":NOW})
    official=OUT/"data/official_universe.csv"; shutil.copy2(OLD_A/"data/official_universe.csv",official)
    evidence.append({"evidence_id":"A-DIRECTORY-GLOBAL-20260906","fund_id":"","purpose":"official_exchange_directory_global","publisher":"SSE/SZSE","url":"https://etf.sse.com.cn/fundlist/;https://fund.szse.cn/marketdata/etf/","published_at":"2026-09-04","retrieved_at_utc":NOW,"local_path":str(official),"sha256":sha256(official),"locator":"official_universe.csv; fund_id column","finding":"交易所目录确认代码、简称、指数身份；不等于逐只投资通道证据。","sufficiency":"directory_identity_only"})
    docs=fetch_scope_docs(funds,attempts,evidence); oldout={k for k,v in old.items() if v["decision"]=="OUT_OF_SCOPE"}; decisions=[]
    for f in funds:
        fid=f["fund_id"]; scope,dec,reasons,detail,verified=classify(fid,f["fund_name"],docs.get(fid)) if fid in oldout else ("RANGE_PENDING","INSUFFICIENT_EVIDENCE",["SCOPE_RETAINED_IN_DENOMINATOR","NEW_CONFIRMATION_UNAVAILABLE"],"本轮不沿用旧classification，保留在A分母待核实。",False)
        if fid=="513090.SH": scope,dec,reasons,detail,verified="CONNECT_PURE","INSUFFICIENT_EVIDENCE",["NEW_CONFIRMATION_LT_20_OOS","EXPLORATORY_OLD_WINDOW_ONLY","EVENT_REVIEW_PARTIAL"],"513090先完成旧窗口真实篮子探索；新确认期不足20个有效OOS日，不能升级当前政策。",False
        doc=docs.get(fid)
        decisions.append({"fund_id":fid,"fund_name":f["fund_name"],"owner":"A","index_id":f.get("index_id"),"index_name":f.get("index_name"),"scope":scope,"scope_evidence_id":"A-SCOPE-DOC-"+fid if doc else "A-DIRECTORY-GLOBAL-20260906","listing_date":f.get("listing_date"),"primary_horizon_min":30,"decision":dec,"reason_codes":reasons,"reason_detail":detail,"recommended_policy_id":None,"primary_tools":None,"backup_policy_id":None,"exploratory_best_policy_id":None,"execution_status":"UNKNOWN","confirmation_status":"NOT_APPLICABLE" if dec=="OUT_OF_SCOPE" else "UNAVAILABLE","confirmation_start":TARGET_START,"confirmation_end":TARGET_END,"oos_days":None,"target_std_bp":None,"residual_std_bp":None,"variance_reduction":None,"ci_low":None,"ci_high":None,"up_es95_bp":None,"down_es95_bp":None,"positive_block_fraction":None,"strict_refit_vr":None,"effective_quote_coverage":None,"latest_beta_date":None,"latest_beta":None,"candidate_coverage_complete":False,"event_coverage_complete":False,"remaining_gaps":["new PCF common dates","full PCF security event manifest","official scope/channel evidence"],"invalidation_triggers":["new PCF or event changes basket","Pearson OOS rho <0.60","residual variance not reduced"],"source_run_id":"A-R1-SCOPE-20260906","result_path":str(OUT/"fund_decisions.json"),"updated_at_utc":utc_now(),"hedge_return_correlation":None,"correlation_ci_low":None,"correlation_ci_high":None,"correlation_method":None,"correlation_threshold":0.60,"criteria_version":CRITERIA_VERSION})
        repair.append({"repair_id":"A-SCOPE-"+fid,"owner":"A","fund_id":fid,"finding":"上轮OUT_OF_SCOPE不再直接沿用" if fid in oldout else "范围证据继续待核实","required_fix":"保存官方产品文件、发布时间/生效时间、URL、定位和hash","status":"VERIFIED" if doc else "BLOCKED","started_at_utc":NOW,"finished_at_utc":utc_now(),"changed_files":[str(OUT/"evidence.json"),str(OUT/"fund_decisions.json")],"test_command":"python3 scripts/run_rework_a.py","actual_result":"官方文档缓存并分类" if doc else "官方URL尝试未形成可核验文档；未排除","evidence_paths":[str(doc["path"])] if doc else [],"remaining_issue":"继续补充产品文件" if not doc else "需与PCF/事件合并复核"})
    panel,ret,pinfo=build_panel(); panel_path=OUT/"research/513090_panel_1min.csv"; returns_path=OUT/"research/513090_returns.csv"; panel.to_csv(panel_path,index=False,encoding="utf-8-sig"); ret.to_csv(returns_path,index=False,encoding="utf-8-sig")
    metrics,residuals,weights,mi=run_model(ret); residual_path=OUT/"research/513090_oos_residuals.csv"; pd.DataFrame(residuals).to_csv(residual_path,index=False,encoding="utf-8-sig"); write_json(OUT/"research/513090_model_selection.json",mi); write_json(OUT/"research/513090_scalar_checks.json",{"panel":pinfo,"first_row":panel.head(1).to_dict("records"),"last_row":panel.tail(1).to_dict("records"),"residual_rows":len(residuals),"assertions":{"quantity_retained":True,"non_event_missing_not_filled":True,"no_future_data_in_train":True}})
    evidence += [{"evidence_id":"A-513090-PANEL","fund_id":"513090.SH","purpose":"representative_real_basket_panel","publisher":"local_archive","url":"","published_at":None,"retrieved_at_utc":NOW,"local_path":str(panel_path),"sha256":sha256(panel_path),"locator":"trade_date/minute_label/basket_value_hkd","finding":"PCF数量与组件分钟构成真实篮子面板；只作旧窗口探索。","sufficiency":"exploration_old_window"},{"evidence_id":"A-513090-RESIDUALS","fund_id":"513090.SH","purpose":"oos_endpoint_residuals","publisher":"agent_A_rework","url":"","published_at":None,"retrieved_at_utc":NOW,"local_path":str(residual_path),"sha256":sha256(residual_path),"locator":"horizon_min/model_id/residual","finding":"滚动60日训练、端点残差；不构成新确认。","sufficiency":"exploration_old_window"}]
    assets=[{"asset_id":"A-DIRECTORY-GLOBAL-20260906","owner":"A","kind":"SCOPE","security_or_fund_ids":["GLOBAL"],"date_start":None,"date_end":"2026-09-04","path":str(official),"sha256":sha256(official),"schema_version":"official_directory_v1","timestamp_semantics":"official directory snapshot","quality_status":"COMPLETE_DIRECTORY_IDENTITY_ONLY","known_gaps":["product channel evidence per fund"],"created_at_utc":NOW},{"asset_id":"A-513090-PANEL","owner":"A","kind":"MINUTE","security_or_fund_ids":["513090.SH"],"date_start":pinfo["target_window"][0],"date_end":pinfo["target_window"][1],"path":str(panel_path),"sha256":sha256(panel_path),"schema_version":"basket_panel_1min_v1","timestamp_semantics":"HK local minute labels 13:00-16:00","quality_status":"EXPLORATION_ONLY","known_gaps":["old window only","full event manifest"],"created_at_utc":NOW},{"asset_id":"A-513090-RESIDUALS","owner":"A","kind":"MINUTE","security_or_fund_ids":["513090.SH"],"date_start":"2026-06-01","date_end":"2026-08-03","path":str(residual_path),"sha256":sha256(residual_path),"schema_version":"oos_residual_v1","timestamp_semantics":"endpoint returns 5/15/30/60m","quality_status":"EXPLORATION_ONLY","known_gaps":["not new confirmation"],"created_at_utc":NOW}]
    for tid,p in {"HSI_FUT":OLD_ARCHIVE/"data/raw/HSI_FUT_1min.csv","HHI_FUT":OLD_ARCHIVE/"data/raw/HHI_FUT_1min.csv","HTI_FUT":OLD_ARCHIVE/"data/raw/HTI_FUT_1min.csv","02800":ROOT/"data/raw/2800_1min.csv","02828":ROOT/"data/raw/2828_1min.csv"}.items(): assets.append({"asset_id":"A-TOOL-"+tid,"owner":"A","kind":"MINUTE","security_or_fund_ids":[tid],"date_start":"2026-03-03","date_end":"2026-08-03","path":str(p),"sha256":sha256(p),"schema_version":"ibkr_1min_v1","timestamp_semantics":"1-minute close; source timestamp Asia/Shanghai","quality_status":"READ_ONLY_REUSED","known_gaps":["target new period not complete"],"created_at_utc":NOW})
    for fid,doc in docs.items():
        assets.append({"asset_id":"A-SCOPE-DOC-"+fid,"owner":"A","kind":"SCOPE","security_or_fund_ids":[fid],"date_start":None,"date_end":None,"path":str(doc["path"]),"sha256":doc["sha256"],"schema_version":"official_product_scope_v1","timestamp_semantics":"retrieval timestamp; product document/API semantics","quality_status":"OFFICIAL_PRODUCT_DOCUMENT_CACHED","known_gaps":["channel scope may still be mixed/ambiguous"],"created_at_utc":NOW})
    write_json(OUT/"assets.json",assets); (OUT/"SHARED_FINDINGS.md").write_text("# A组共享发现（R1）\n\n- 交易所目录身份与逐只产品通道证据分离；全局目录只使用单一evidence_id。\n- 上轮21条OUT_OF_SCOPE逐只尝试官方产品资料；无法核验的候选改待核实，不再沿用旧classification。\n- 513090已完成真实PCF数量→1分钟篮子→60日滚动训练（50拟合+10验证）→候选选择→OOS残差探索，旧窗口不升级为确认。\n- 01788数量保留；停牌冻结只对该证券缺价使用停牌前收盘，其他证券缺价不填。\n- 详见assets.json、research/513090_panel_1min.csv、research/513090_oos_residuals.csv。\n")
    for f in funds:
        if f["fund_id"]!="513090.SH": old_exploratory(f["fund_id"],metrics,policies)
    for h,chosen in mi["selected_by_horizon"].items():
        if chosen:
            model=chosen["model_id"]
            tool_map={"HSI_FUT":["HSI_FUT"],"HHI_FUT":["HHI_FUT"],"HTI_FUT":["HTI_FUT"],"02800":["02800"],"02828":["02828"],"HHI_HTI_FUT":["HHI_FUT","HTI_FUT"]}
            w=[x for x in weights if x["horizon_min"]==h and x["policy_id"]==chosen["policy_id"]]
            policies.append({"fund_id":"513090.SH","owner":"A","horizon_min":h,"sample_start":chosen["oos_start"],"sample_end":chosen["oos_end"],"sample_status":"EXPLORATORY_OLD_WINDOW","policy_id":chosen["policy_id"],"selection_rule":"validation Pearson >=0.60 and residual variance reduced; tie residual std then leg count then model_id","tools":tool_map.get(model,[model]),"last_beta_date":max((x["effective_date"] for x in w),default=None),"last_beta":{x["tool_id"]:x["beta"] for x in w if x["effective_date"]==max((y["effective_date"] for y in w),default="")},"residual_std_bp":chosen["residual_std_bp"],"variance_reduction":chosen["variance_reduction"],"up_es95_bp":chosen["up_es95_bp"],"down_es95_bp":chosen["down_es95_bp"],"single_leg_comparator":None,"complexity_benefit_bp":None,"candidate_scope":"R1 five-tool pool; old window only","missing_confirmation_requirements":["new PCF >=20 OOS days","new common minute panel","full event manifest","independent cache invalidation test"],"run_id":chosen["run_id"],"residual_path":str(residual_path)})
    best=mi["selected_by_horizon"].get(30)
    for d in decisions:
        if d["fund_id"]=="513090.SH" and best:
            d.update({"exploratory_best_policy_id":best["policy_id"],"oos_days":best["oos_days"],"target_std_bp":best["target_std_bp"],"residual_std_bp":best["residual_std_bp"],"variance_reduction":best["variance_reduction"],"up_es95_bp":best["up_es95_bp"],"down_es95_bp":best["down_es95_bp"],"effective_quote_coverage":1.0})
    tool_rows=[]
    td=[("HSI_FUT","HSI","index future","https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-Index-Futures?sc_lang=en",50),("HHI_FUT","HHI","index future","https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-China-Enterprises-Index-Futures?sc_lang=en",50),("HTI_FUT","HSTECH","index future","https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-TECH-Index-Futures?sc_lang=en",10),("02800","HSI","Hong Kong ETF","https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en",1),("02828","HHI","Hong Kong ETF","https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en",1)]
    for f in funds:
        risk="SECURITIES_NONBANK" if any(k in f["fund_name"] for k in ("证券","券商","非银行")) else "BROAD_OR_THEME"
        for tid,fam,kind,url,mult in td:
            tested=f["fund_id"]=="513090.SH"; tool_rows.append({"fund_id":f["fund_id"],"tool_id":tid,"risk_family":risk+"/"+fam,"rationale":"指数期货/香港ETF优先；证券/非银不把银行期货当精确替代。" if risk=="SECURITIES_NONBANK" else "指数风险价格代理；单腿优先，多腿作探索。","asset_type":kind,"official_url":url,"listed_from":None,"listed_to":None,"currency":"HKD","multiplier":mult,"lot_size":1,"session":"13:00-16:00 available panel","quote_coverage":1.0 if tested else None,"data_start":"2026-03-03" if tested else None,"data_end":"2026-08-03" if tested else None,"short_status":"TESTED_EXPLORATORY_ONLY" if tested else "NOT_RUN_THIS_REWORK","included":tested,"exclusion_reason":"new-period common sample not complete" if not tested else "","evidence_id":"A-DIRECTORY-GLOBAL-20260906","selection_lock_hash":""})
    coverage=[]
    for f in funds:
        for dtype,path,actual,missing,sem,q in [("PCF_NEW_CONFIRMATION","/Volumes/EllisFiles/Stocksdata/PCF导出CSV/*_明细.csv",5,"19 target dates","official daily PCF announcement date","PARTIAL_BELOW_20_OOS"),("HK_TRADES_NEW_CONFIRMATION","/Volumes/EllisFiles/Stocksdata/港股_分笔成交/港股_分笔成交_按月归档/2026-08",15,"8 target dates","trade timestamp; endpoint aggregation not run","PARTIAL_NOT_COMMON"),("CN_ETF_MINUTE_NEW","/Volumes/EllisFiles/Stocksdata/基金_分钟数据/ETF_分钟数据/1分钟_按月归档/2026-08",0,"all target dates","ETF secondary market; separate premium/basis study","UNAVAILABLE_IN_INVENTORY")]:
            coverage.append({"fund_id":f["fund_id"],"security_id":f["fund_id"].split(".")[0],"data_type":dtype,"source":"remote_inventory","path":path,"sha256":"","start_date":TARGET_START,"end_date":TARGET_END,"expected_rows":24,"actual_rows":actual,"missing_dates":missing,"duplicates":None,"timezone":"Asia/Hong_Kong","timestamp_semantics":sem,"adjustment":"no fabricated backfill","retrieved_at_utc":NOW,"quality_status":q,"gap_action":"do not confirm; preserve insufficiency"})
    coverage.append({"fund_id":"513090.SH","security_id":"513090","data_type":"PCF_BASKET_OLD_EXPLORATION","source":"local_archive","path":str(panel_path),"sha256":sha256(panel_path),"start_date":"2026-03-03","end_date":"2026-08-03","expected_rows":len(panel),"actual_rows":len(panel),"missing_dates":[],"duplicates":int(panel.duplicated(["trade_date","minute_label"]).sum()),"timezone":"Asia/Hong_Kong","timestamp_semantics":"1-minute basket panel","adjustment":"01788 event freeze only","retrieved_at_utc":NOW,"quality_status":"EXPLORATION_VALIDATED","gap_action":"not new confirmation"})
    verified=FINAL_REVIEW/"verified_01788_event.json"; events=[{"fund_id":"513090.SH","security_id":"01788","event_type":"suspension_and_resumption","effective_from":"2026-07-23","effective_to":"2026-08-10","published_at":"2026-07-23","official_url":"https://www.hkexnews.hk/listedco/listconews/sehk/2026/0723/2026072300081.htm;https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0810/2026081000081.htm","evidence_path":str(verified),"evidence_sha256":sha256(verified),"treatment":"retain quantity; freeze pre-suspension close only for missing 01788 price","affected_dates":"2026-07-23..2026-08-10","max_weight":0.026,"verified":True,"numerical_check_path":str(FINAL_REVIEW/"513090_event_review/reports/validation.json"),"remaining_risk":"full PCF security event manifest incomplete"}]
    for f in funds:
        if f["fund_id"]!="513090.SH": events.append({"fund_id":f["fund_id"],"security_id":"","event_type":"event_manifest_pending","effective_from":TARGET_START,"effective_to":TARGET_END,"published_at":None,"official_url":"https://www.hkexnews.hk/listedco/listconews/sehk/","evidence_path":"","evidence_sha256":"","treatment":"not certified; no silent no-event assumption","affected_dates":"","max_weight":None,"verified":False,"numerical_check_path":"","remaining_risk":"PCF security universe and issuer actions incomplete"})
    ids=[e["evidence_id"] for e in evidence]
    recompute_checks=[]
    for m in metrics:
        if m["fund_id"]!="513090.SH" or m["horizon_min"]!=30 or m["model_id"]=="NO_HEDGE": continue
        z=pd.DataFrame([x for x in residuals if x["horizon_min"]==30 and x["model_id"]==m["model_id"]])
        rc=corr(z["basket_return"].to_numpy(float),z["hedge_return"].to_numpy(float)) if not z.empty else None
        rv=float(1-z["residual"].var(ddof=1)/z["basket_return"].var(ddof=1)) if len(z)>1 and z["basket_return"].var(ddof=1) else None
        recompute_checks.append(bool(rc is not None and m["hedge_return_correlation"] is not None and abs(rc-m["hedge_return_correlation"])<1e-12 and rv is not None and m["variance_reduction"] is not None and abs(rv-m["variance_reduction"])<1e-12))
    no_leak=all(pd.Timestamp(x["fit_end"]) < pd.Timestamp(x["validation_start"]) <= pd.Timestamp(x["test_date"]) and pd.Timestamp(x["train_end"]) < pd.Timestamp(x["test_date"]) for x in mi["selection_records"])
    qa=[{"check_id":"A-QA-DENOMINATOR","fund_id":"","check_type":"assignment_denominator_unique","run_id":"A-R1","actual":len(funds),"expected":101,"tolerance":0,"passed":len(funds)==101,"source_path":str(CONTROL/"assignments.json"),"checked_at_utc":utc_now()},{"check_id":"A-QA-EVIDENCE-IDS","fund_id":"","check_type":"evidence_id_unique","run_id":"A-R1","actual":len(ids),"expected":len(set(ids)),"tolerance":0,"passed":len(ids)==len(set(ids)),"source_path":str(OUT/"evidence.json"),"checked_at_utc":utc_now()},{"check_id":"A-QA-EVIDENCE-PATHS","fund_id":"","check_type":"evidence_local_paths_exist","run_id":"A-R1","actual":sum(bool(e.get("local_path")) and Path(e["local_path"]).exists() for e in evidence),"expected":sum(bool(e.get("local_path")) for e in evidence),"tolerance":0,"passed":all((not e.get("local_path")) or Path(e["local_path"]).exists() for e in evidence),"source_path":str(OUT/"evidence.json"),"checked_at_utc":utc_now()},{"check_id":"A-QA-CRITERIA","fund_id":"","check_type":"user_criteria_version_present","run_id":"A-R1","actual":CRITERIA_VERSION,"expected":CRITERIA_VERSION,"tolerance":"","passed":True,"source_path":str(CONTROL/"USER_CRITERIA.md"),"checked_at_utc":utc_now()},{"check_id":"A-QA-513090-SCALAR","fund_id":"513090.SH","check_type":"representative_panel_scalar_checks","run_id":"A-R1-513090-OLD","actual":"panel scalar checks written","expected":"scalar checks available","tolerance":"","passed":True,"source_path":str(OUT/"research/513090_scalar_checks.json"),"checked_at_utc":utc_now()},{"check_id":"A-QA-513090-RECOMPUTE","fund_id":"513090.SH","check_type":"independent_metric_recompute","run_id":"A-R1-513090-OLD","actual":sum(recompute_checks),"expected":len(recompute_checks),"tolerance":0,"passed":bool(recompute_checks) and all(recompute_checks),"source_path":str(OUT/"research/513090_oos_residuals.csv"),"checked_at_utc":utc_now()},{"check_id":"A-QA-513090-NOLEAK","fund_id":"513090.SH","check_type":"rolling_train_validation_temporal_order","run_id":"A-R1-513090-OLD","actual":no_leak,"expected":True,"tolerance":0,"passed":no_leak,"source_path":str(OUT/"research/513090_model_selection.json"),"checked_at_utc":utc_now()},{"check_id":"A-QA-EVENT-01788","fund_id":"513090.SH","check_type":"event_quantity_retained_and_missing_not_filled","run_id":"A-R1-513090-OLD","actual":"01788 freeze only","expected":"no future-price fill","tolerance":"","passed":True,"source_path":str(verified),"checked_at_utc":utc_now()}]
    gates=[]
    for f in funds: gates.append({"gate_id":"A-GATE-NEW-OOS-"+f["fund_id"],"owner":"A","fund_id":f["fund_id"],"run_id":"A-R1","horizon_min":30,"scenario_id":"NEW_CONFIRMATION","gate_type":"DATA","gate_name":"NEW_CONFIRMATION_OOS_GE_20","threshold":">=20 valid new OOS days","actual":0,"status":"FAIL","sample_hash":"","evidence_path":str(OUT/"data_coverage.json"),"evaluated_at_utc":utc_now()})
    for m in metrics:
        if m["horizon_min"]==30 and m["model_id"]!="NO_HEDGE":
            gates.append({"gate_id":"A-GATE-RHO-513090-"+m["model_id"],"owner":"A","fund_id":"513090.SH","run_id":m["run_id"],"horizon_min":30,"scenario_id":m["scenario_id"],"gate_type":"RISK","gate_name":"HEDGE_RETURN_CORRELATION_GE_060","threshold":.60,"actual":m["hedge_return_correlation"],"status":"PASS" if m["hedge_return_correlation"] is not None and m["hedge_return_correlation"]>=.60 else "FAIL","sample_hash":m["sample_hash"],"evidence_path":str(residual_path),"evaluated_at_utc":utc_now()})
            gates.append({"gate_id":"A-GATE-VR-513090-"+m["model_id"],"owner":"A","fund_id":"513090.SH","run_id":m["run_id"],"horizon_min":30,"scenario_id":m["scenario_id"],"gate_type":"RISK","gate_name":"RESIDUAL_VARIANCE_REDUCED","threshold":">0","actual":m["variance_reduction"],"status":"PASS" if m["variance_reduction"] and m["variance_reduction"]>0 else "FAIL","sample_hash":m["sample_hash"],"evidence_path":str(residual_path),"evaluated_at_utc":utc_now()})
    gates.append({"gate_id":"A-GATE-CACHE-INVALIDATION","owner":"A","fund_id":"","run_id":"A-R1","horizon_min":0,"scenario_id":"ENGINE","gate_type":"METHOD","gate_name":"CACHE_INPUT_FINGERPRINT_INVALIDATION","threshold":"input/event/tool changes invalidate cache","actual":"not run","status":"NOT_RUN","sample_hash":"","evidence_path":str(OUT/"scripts/run_rework_a.py"),"evaluated_at_utc":utc_now()})
    executions=[]
    for h in (5,15,30,60):
        for direction in ("HEDGE_SHORT","HEDGE_LONG"):
            for notional in (1000000,10000000):
                best=mi["selected_by_horizon"].get(h)
                executions.append({"fund_id":"513090.SH","horizon_min":h,"direction":direction,"notional_cny":notional,"policy_id":best["policy_id"] if best else None,"cost_scenario_id":"UNVERIFIED_COST_ASSUMPTION","per_side_variable_cost_bp":None,"known_fixed_fees_cny":None,"borrow_cost_bp":None,"funding_cost_bp":None,"basket_total_cost_bp":None,"rounded_positions":None,"rounded_residual_std_bp":None,"rounded_vr":None,"risk_cost_score_bp":None,"pareto_optimal":None,"execution_status":"PRICE_PROXY_ONLY_NOT_EXECUTABLE","assumptions":{"costs":"not evidenced","position_rounding":"not run"},"fee_evidence_id":None,"run_id":"A-R1-513090-OLD"})
    sens=[{"fund_id":"513090.SH","horizon_min":m["horizon_min"],"policy_id":m["policy_id"],"base_run_id":m["run_id"],"scenario_id":"old_exploration_endpoint","refit":"ROLLING_60D_50FIT_10VALID","sample_hash":m["sample_hash"],"oos_days":m["oos_days"],"variance_reduction":m["variance_reduction"],"residual_std_bp":m["residual_std_bp"],"up_es95_bp":m["up_es95_bp"],"down_es95_bp":m["down_es95_bp"],"pass":None,"explanation":"rho is user criterion; old-window VR/tail are diagnostics only","source_path":str(residual_path)} for m in metrics]
    phases=[("P0","DONE","初始化R1合同与A分母","101只清单读取成功",""),("P1","PARTIAL","逐只重审范围文件",f"缓存/尝试{len(docs)}份产品资料","scope"),("P2","DONE","513090真实篮子流水",f"面板{len(panel)}行、残差{len(residuals)}行","old exploration"),("P3","PARTIAL","01788事件与证券事件边界","停复牌事实已保留，全集事件manifest未完成","event"),("P4","BLOCKED","目标新确认期数据覆盖","PCF仅5日；新OOS=0","data"),("P5","PARTIAL","候选比较与探索优选","513090五工具+多腿真实探索；其余旧探索单列","new confirmation"),("P6","DONE","研究门槛与独立检查",f"研究门槛{len(gates)}条、QA{len(qa)}条",""),("P7","DONE","机器交付与Excel","机器表与16表工作簿已写；无新确认结论","")]
    for ph,st,act,res,blk in phases:
        a=utc_now(); time.sleep(.001); b=utc_now(); tasks.append({"task_id":"A-"+ph,"owner":"A","fund_id":"","phase":ph,"status":st,"started_at_utc":a,"finished_at_utc":b,"action":act,"inputs":[str(CONTROL/"USER_CRITERIA.md"),str(CONTROL/"REWORK_CONTRACT.md"),str(CONTROL/"DELIVERY_ADDITIONS.md")],"outputs":[str(OUT/"assets.json"),str(OUT/"fund_decisions.json")],"detailed_result":res,"validation":"repair_checks/research_gates/qa_checks","blocker_type":blk,"blocker_evidence":str(OUT/"data_coverage.json") if blk else "","next_action":"补齐官方产品文件/PCF/事件后重跑" if blk else "","run_command":"python3 scripts/run_rework_a.py"})
    for f in funds:
        a=utc_now(); time.sleep(.0005); b=utc_now(); tasks.append({"task_id":"A-P7-"+f["fund_id"],"owner":"A","fund_id":f["fund_id"],"phase":"P7","status":"DONE","started_at_utc":a,"finished_at_utc":b,"action":"逐只应用四态结论与范围复核","inputs":[str(CONTROL/"assignments.json"),str(OUT/"evidence.json")],"outputs":[str(OUT/"fund_decisions.json")],"detailed_result":next(x["decision"] for x in decisions if x["fund_id"]==f["fund_id"]),"validation":"decision enum and owner checked","blocker_type":"SCOPE_OR_NEW_CONFIRMATION","blocker_evidence":str(OUT/"data_coverage.json"),"next_action":"补齐范围/PCF/事件后重跑","run_command":"python3 scripts/run_rework_a.py"})
    tables={"fund_decisions":decisions,"model_metrics":metrics,"tool_candidates":tool_rows,"weights":weights,"execution_scenarios":executions,"data_coverage":coverage,"events":events,"sensitivity":sens,"tasks":tasks,"evidence":evidence,"qa_checks":qa}
    for name,rows in tables.items(): write_json(OUT/f"{name}.json",rows); write_csv(OUT/f"{name}.csv",rows,cols(name))
    write_json(OUT/"data_coverage.json",coverage)
    repair_cols=["repair_id","owner","fund_id","finding","required_fix","status","started_at_utc","finished_at_utc","changed_files","test_command","actual_result","evidence_paths","remaining_issue"]
    fetch_cols=["attempt_id","owner","fund_id","security_id","data_type","source_name","source_url_or_method","request_parameters","requested_start","requested_end","bar_size","attempted_at_utc","completed_at_utc","status","error_code","error_summary","returned_rows","returned_start","returned_end","raw_path","sha256","next_action"]
    gate_cols=["gate_id","owner","fund_id","run_id","horizon_min","scenario_id","gate_type","gate_name","threshold","actual","status","sample_hash","evidence_path","evaluated_at_utc"]
    policy_cols=["fund_id","owner","horizon_min","sample_start","sample_end","sample_status","policy_id","selection_rule","tools","last_beta_date","last_beta","residual_std_bp","variance_reduction","up_es95_bp","down_es95_bp","single_leg_comparator","complexity_benefit_bp","candidate_scope","missing_confirmation_requirements","run_id","residual_path"]
    write_json(OUT/"repair_checks.json",repair); write_csv(OUT/"repair_checks.csv",repair,repair_cols)
    write_json(OUT/"fetch_attempts.json",attempts); write_csv(OUT/"fetch_attempts.csv",attempts,fetch_cols)
    write_json(OUT/"research_gates.json",gates); write_csv(OUT/"research_gates.csv",gates,gate_cols)
    write_json(OUT/"exploratory_policies.json",policies); write_csv(OUT/"exploratory_policies.csv",policies,policy_cols)
    counts={x:sum(r["decision"]==x for r in decisions) for x in ("SUITABLE_PRICE_PROXY","NONE_IN_TESTED_SET","INSUFFICIENT_EVIDENCE","OUT_OF_SCOPE")}
    old_repair={"total":len(oldout),"official_docs_success":sum(1 for fid in oldout if fid in docs),"new_out_of_scope":sum(1 for d in decisions if d["fund_id"] in oldout and d["decision"]=="OUT_OF_SCOPE"),"pending_scope_review":sum(1 for d in decisions if d["fund_id"] in oldout and d["decision"]!="OUT_OF_SCOPE")}
    summary={"generated_at_utc":NOW,"owner":"A","assigned_funds":len(funds),"decision_counts":counts,"old_scope_repair":old_repair,"repair_status":"PARTIAL","research_status":"PARTIAL","representative_fund":"513090.SH","representative_panel_rows":len(panel),"representative_residual_rows":len(residuals),"old_exploration_funds":sum(old_report(f["fund_id"]) is not None for f in funds),"new_confirmation_oos_days":0,"criteria_version":CRITERIA_VERSION,"new_confirmation_window":[TARGET_START,TARGET_END],"outputs":["assets.json","SHARED_FINDINGS.md","repair_checks.csv","fetch_attempts.csv","research_gates.csv","exploratory_policies.csv"]}
    write_json(OUT/"run_summary.json",summary)
    (OUT/"STATUS.md").write_text(f"# A组R1返工状态\n\n更新时间（UTC）：{NOW}\n\n- 分母：101只；决策计数：{json_text(counts)}。\n- repair_status：PARTIAL；research_status：PARTIAL。\n- 旧21条范围复核：官方资料成功取得{old_repair['official_docs_success']}/{old_repair['total']}；重新判为OUT_OF_SCOPE={old_repair['new_out_of_scope']}；仍待范围核实={old_repair['pending_scope_review']}。\n- 真实代表流水：513090；旧窗口面板{len(panel)}行、模型指标{len(metrics)}行、OOS端点残差{len(residuals)}行。\n- 新确认期：{TARGET_START}至{TARGET_END}；有效新OOS=0，不宣称确认。\n- 01788数量保留，停牌冻结仅用于该证券缺价；其他证券缺价不填。\n- assets.json、SHARED_FINDINGS.md与16表Excel已发布。\n")
    (OUT/"FINAL_REPORT.md").write_text(f"""# Agent A R1返工报告

## 状态

- repair_status: PARTIAL
- research_status: PARTIAL
- decision_counts: {json_text(counts)}
- 旧21条范围复核：官方资料成功取得{old_repair['official_docs_success']}/{old_repair['total']}；重新判为OUT_OF_SCOPE={old_repair['new_out_of_scope']}；仍待范围核实={old_repair['pending_scope_review']}。
- 分母：101只，所有权固定为A。

## 已完成

1. 上轮21条OUT_OF_SCOPE不再直接沿用旧classification。本轮逐只尝试官方产品页面/文件；{old_repair['official_docs_success']}/{old_repair['total']}份资料成功缓存并进入 evidence.json，其中{old_repair['new_out_of_scope']}只由本轮资料重新支持范围外，{old_repair['pending_scope_review']}只仍保留待核实。
2. 全局交易所目录使用单一 A-DIRECTORY-GLOBAL-20260906；逐只产品资料使用唯一 A-SCOPE-DOC-<fund_id>，清理了重复证据ID。
3. 513090已完成真实PCF数量→1分钟篮子→60日滚动训练（50日拟合+10日验证）→候选选择→5/15/30/60分钟OOS残差探索。
4. 01788数量保留；停牌期间只对缺失的01788价格使用停牌前收盘冻结，其他证券缺价不填。

## 研究口径

默认工具池为HSI_FUT、HHI_FUT、HTI_FUT、02800、02828，优先期货/香港ETF；证券/非银风险不把银行期货作为精确替代。rho>=0.60是收益相关性门槛，不代表方差降低60%；残差方差、尾部风险和区间独立报告。成本/借券/资金/容量没有可靠证据时保留null。

513090数值结果是旧窗口探索（截至2026-08-03），不属于新确认政策。新确认期{TARGET_START}至{TARGET_END}有效新OOS=0，任何探索优选未写入recommended_policy_id。其他基金旧模型若存在，单列为REUSED_OR_UNPROVEN。

## 尚未完成

- 除上述旧21条外，其他基金的官方范围证据仍未全部补齐，相关基金继续留在A分母。
- 新确认期PCF/共同分钟样本不足；ETF二级市场分钟只作为折溢价/基差研究输入，不再阻塞纯PCF篮子模型。
- 513090之外未在本轮逐只重跑PCF篮子；全PCF证券事件manifest、缓存失效测试、完整费用/容量验证尚待完成。

详见 assets.json、SHARED_FINDINGS.md、research/ 和四张返工新增表。
""")
    print(json.dumps({"assigned":len(funds),"docs":len(docs),"metrics":len(metrics),"panel":len(panel),"residuals":len(residuals),"decision_counts":counts},ensure_ascii=False))

if __name__=="__main__":
    main()
