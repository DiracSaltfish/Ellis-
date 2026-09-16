"""Select target ETF entries; CRC-verify every retained file before deleting a stable archive.
Deletion is hard-bounded to RAW. Never extracts unrelated shares or touches partial downloads.
"""
from pathlib import Path, PurePosixPath
import json,subprocess,zlib,time,os,shutil,argparse
R=Path(__file__).resolve().parent
RAW=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔')
SEVEN='/opt/homebrew/bin/7zz'
def guarded(p):
    p=Path(p)
    if p.is_symlink() or RAW.is_symlink() or RAW.resolve()!=RAW:raise ValueError('symlink root/file')
    if RAW not in p.resolve().parents:raise ValueError('outside raw root: '+str(p))
    for a in p.parents:
        if a==RAW:break
        if a.is_symlink():raise ValueError('symlink ancestor')
    return p

def members(archive):
    out=subprocess.check_output([SEVEN,'l','-slt',str(archive)],text=True)
    entries=[]
    for block in out.split('----------\n',1)[1].split('\n\n'):
        d=dict(x.split(' = ',1) for x in block.splitlines() if ' = ' in x)
        if 'Path' not in d:continue
        q=PurePosixPath(d['Path'])
        if q.is_absolute() or '..' in q.parts or '\\' in d['Path'] or any(k in d for k in ['Symbolic Link','Hard Link']):raise ValueError('unsafe archive member')
        if not d.get('CRC'):continue
        entries.append(d)
    return entries

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--delete-verified',action='store_true');ap.add_argument('--limit',type=int,default=0);args=ap.parse_args()
    universe=json.loads((R.parent/'inputs/universe.json').read_text())
    codes={s.split('.')[0]:s for s in universe}
    if args.delete_verified:raise RuntimeError('Archive deletion disabled after suffix mismatch; retain source archives')
    (R/'manifests').mkdir(exist_ok=True)
    snapshot=[p for p in sorted(RAW.rglob('*.7z')) if p.stem.isdigit() and len(p.stem)==8 and time.time()-p.stat().st_mtime>120]
    if args.limit:snapshot=snapshot[:args.limit]
    (R/('archive_snapshot_'+time.strftime('%Y%m%d_%H%M%S')+'.json')).write_text(json.dumps([dict(path=str(p),bytes=p.stat().st_size,mtime=p.stat().st_mtime) for p in snapshot],indent=2))
    for arc in snapshot:
        guarded(arc);st=arc.stat();fingerprint=(st.st_size,st.st_mtime_ns,st.st_ino)
        ent=members(arc)
        selected=[e for e in ent if len(PurePosixPath(e['Path']).parts)==3 and PurePosixPath(e['Path']).parts[0]==arc.stem and PurePosixPath(e['Path']).parts[1].split('.')[0] in codes]
        if not selected:raise ValueError('no target entries '+str(arc))
        total=sum(int(e['Size']) for e in selected)
        if shutil.disk_usage(RAW).free<total*1.2+2*1024**3:raise RuntimeError('insufficient extraction space')
        dest=guarded(RAW/'港股通ETF保留');dest.mkdir(exist_ok=True)
        for e in selected:guarded(dest/e['Path'])
        inc=R/'manifests'/f'{arc.stem}_include.txt';inc.write_text('\n'.join(e['Path'] for e in selected)+'\n')
        log=R/'manifests'/f'{arc.stem}_7zip.log'
        print('extract',arc.name,'files',len(selected),'GB',round(total/1e9,3),flush=True)
        with log.open('w') as h:subprocess.run([SEVEN,'x','-y','-bd','-bb0','-scsUTF-8',str(arc),'-i@'+str(inc),'-o'+str(dest)],stdout=h,stderr=subprocess.STDOUT,check=True)
        records=[]
        for e in selected:
            p=guarded(dest/e['Path']);crc=0
            with p.open('rb') as h:
                for buf in iter(lambda:h.read(1024*1024),b''):crc=zlib.crc32(buf,crc)
            assert p.stat().st_size==int(e['Size']) and f'{crc:08X}'==e['CRC'],str(p)
            records.append(dict(path=str(p),source_member=e['Path'],bytes=p.stat().st_size,crc32=f'{crc:08X}'))
        for rec in records:
            old=Path(rec['path']);canon=codes[old.parent.name.split('.')[0]]
            target=guarded(old.parent.parent/canon/old.name)
            if target!=old:
                target.parent.mkdir(exist_ok=True)
                if target.exists():
                    if target.read_bytes()!=old.read_bytes():raise ValueError('canonical collision')
                else:shutil.copyfile(old,target)
                rec['path']=str(target)
        new=arc.stat();assert fingerprint==(new.st_size,new.st_mtime_ns,new.st_ino),'archive changed during extraction'
        report=dict(archive=str(arc),archive_bytes=st.st_size,archive_mtime_ns=st.st_mtime_ns,verified=True,archive_deleted=False,files=records,unrelated_entries_not_extracted=len(ent)-len(selected),archive_symbol_count=len({PurePosixPath(e['Path']).parts[1] for e in ent if len(PurePosixPath(e['Path']).parts)==3}),archive_sh_etf_symbols=sorted({PurePosixPath(e['Path']).parts[1] for e in ent if len(PurePosixPath(e['Path']).parts)==3 and PurePosixPath(e['Path']).parts[1].startswith('5') and PurePosixPath(e['Path']).parts[1].endswith('.SH')}))
        manifest=R/'manifests'/f'{arc.stem}.json';manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        # SevenZip checks selected solid blocks; discarded files need not be expanded/tested.
        if args.delete_verified:
            guarded(arc).unlink();report['archive_deleted']=True;manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print('verified',arc.stem,'retained',len(records),'deleted_archive',report['archive_deleted'],flush=True)
if __name__=='__main__':main()
