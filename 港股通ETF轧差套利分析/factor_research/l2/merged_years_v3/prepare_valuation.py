"""Build only missing valuation inputs in an isolated research directory."""
from pathlib import Path
import sys,json,shutil
R=Path(__file__).resolve().parent;F=R.parent.parent;sys.path[:0]=[str(F),str(R.parent/'study_v2')]
import prepare as p,build_panel as b
from build_dataset import labels
E=R/'valuation';I=E/'inputs';O=E/'results';I.mkdir(parents=True,exist_ok=True);(O/'series').mkdir(parents=True,exist_ok=True)
for name in ['universe_board.json','settlement_sh.csv','settlement_sz.csv','hk_symbols.csv']:shutil.copyfile(F/'inputs'/name,I/name)
if not (I/'share_history').exists():(I/'share_history').symlink_to(F/'inputs/share_history',target_is_directory=True)
# Original raw midpoint responses are not changed.
p.ROOT=E;p.OUT=I;p.MID_MONTHS=[(2024,12)]+[(2025,m) for m in range(1,6)]+[(2026,7)]
p.START='20250101';p.END='20250630';p.main()
p.START='20260701';p.END='20260707';p.main()
mid=json.loads((F/'inputs/midpoint.json').read_text());mid.update(json.loads((I/'midpoint.json').read_text()));(I/'midpoint.json').write_text(json.dumps(mid))
for folder,name in [('baskets','20260115.json.gz'),('etf','20260115.zip')]:
 src=F/'inputs'/folder/name;dst=I/folder/name
 if src.exists() and not dst.exists():dst.symlink_to(src)
lab,_=labels();b.labels=lambda:lab;b.ROOT=E;b.IN=I;b.OUT=O;b.CUTS=['14:45'];b.main()
