from pathlib import Path
import sys,shutil
R=Path(__file__).resolve().parent;F=R.parent;sys.path.insert(0,str(F))
import prepare as p,build_panel as b,aggregate_daily as a
from labels_calendar import labels
b.labels=labels;a.labels=labels
E=R/'valuation';I=E/'inputs';O=E/'results'
I.mkdir(parents=True,exist_ok=True);(O/'series').mkdir(parents=True,exist_ok=True)
for f in ['universe_board.json','settlement_sh.csv','settlement_sz.csv','hk_symbols.csv']:shutil.copyfile(F/'inputs'/f,I/f)
if not (I/'share_history').exists():(I/'share_history').symlink_to(F/'inputs/share_history',target_is_directory=True)
p.ROOT=E;p.OUT=I;p.START='20250101';p.END=max(p.stem for p in (R/'manifests').glob('*.json'));p.MID_MONTHS=[(2024,12),(2025,1),(2025,2)];p.main()
b.ROOT=E;b.IN=I;b.OUT=O;b.CUTS=['15:01'];b.main()
a.R=E;a.I=I;a.O=O;a.main()
