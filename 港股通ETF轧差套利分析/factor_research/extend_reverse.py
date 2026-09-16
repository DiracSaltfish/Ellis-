"""隔离重建此前未分析日期；原研究输入和输出不覆盖。"""
from pathlib import Path
import shutil
import prepare as p
import build_panel as b
import aggregate_daily as a
R=Path(__file__).resolve().parent;E=R/'reverse/extension';I=E/'inputs';O=E/'results'
I.mkdir(parents=True,exist_ok=True);(O/'series').mkdir(parents=True,exist_ok=True)
for f in ['universe_board.json','settlement_sh.csv','settlement_sz.csv','hk_symbols.csv']:
    shutil.copyfile(R/'inputs'/f,I/f)
if not (I/'share_history').exists():(I/'share_history').symlink_to(R/'inputs/share_history',target_is_directory=True)
p.ROOT=E;p.OUT=I;p.START='20260701';p.END='20260813';p.MID_MONTHS=[(2026,m) for m in [6,7,8]]
p.main()
b.ROOT=E;b.IN=I;b.OUT=O;b.CUTS=['15:01'];b.main()
a.R=E;a.I=I;a.O=O;a.main()
