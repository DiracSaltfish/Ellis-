"""Score already-built v2 as-of feature rows with a trusted local model."""
from pathlib import Path
import argparse,json
import pandas as pd,joblib
from model import score_v2

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--model',required=True);ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);args=ap.parse_args();model=joblib.load(args.model);p=Path(args.input)
 d=pd.read_parquet(p) if p.suffix=='.parquet' else pd.read_csv(p,dtype={'date':str,'symbol':str,'cutoff':str});out=score_v2(d,model);dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True);out.to_csv(dest,index=False);print(json.dumps(dict(rows=len(out),candidate_count=int(out.candidate.sum()),mode='research_only')))
if __name__=='__main__':main()
