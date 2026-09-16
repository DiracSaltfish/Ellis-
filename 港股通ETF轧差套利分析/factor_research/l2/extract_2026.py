"""2026 selective extractor. Match authoritative six-digit ETF code; preserve 7z.
Original vendor names kept in manifest; canonical folders use website exchange.
"""
from pathlib import Path,PurePosixPath
import json,time,subprocess,zlib,shutil,argparse
from extract_archives import RAW,R,SEVEN,guarded,members

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cleanup',action='store_true');args=parser.parse_args()
    if args.cleanup:
        from cleanup_2026 import cleanup
        cleanup()
    codes={s.split('.')[0]:s for s in json.loads((R.parent/'inputs/universe.json').read_text())}
    out=R/'manifests_2026';out.mkdir(exist_ok=True)
    archives=[p for p in sorted((RAW/'2026').rglob('*.7z')) if len(p.stem)==8 and p.stem.startswith('2026') and time.time()-p.stat().st_mtime>90]
    (R/('snapshot_2026_'+time.strftime('%Y%m%d_%H%M%S')+'.json')).write_text(json.dumps([str(p) for p in archives],ensure_ascii=False))
    for arc in archives:
        guarded(arc);st=arc.stat();finger=(st.st_size,st.st_mtime_ns,st.st_ino);manifest=out/(arc.stem+'.json')
        if manifest.exists() and json.loads(manifest.read_text()).get('verified'):continue
        ent=members(arc);chosen=[]
        for e in ent:
            p=PurePosixPath(e['Path'])
            if (len(p.parts)==2 or (len(p.parts)==3 and p.parts[0]==arc.stem)) and p.parts[-2].split('.')[0] in codes:chosen.append(e)
        assert chosen,'no target files'
        markets={codes[PurePosixPath(e['Path']).parts[-2].split('.')[0]].split('.')[1] for e in chosen}
        assert markets=={'SH','SZ'},'incomplete exchange coverage: retain source and investigate'
        canonical=[str(PurePosixPath(arc.stem,codes[PurePosixPath(e['Path']).parts[-2].split('.')[0]],PurePosixPath(e['Path']).name)) for e in chosen]
        assert len(canonical)==len(set(canonical)),'canonical file collision'
        size=sum(int(e['Size']) for e in chosen)
        assert shutil.disk_usage(RAW).free>size*2+2*1024**3,'insufficient disk space'
        stage=guarded(RAW/'ETF提取暂存'/arc.stem);stage.mkdir(parents=True,exist_ok=True)
        inc=out/(arc.stem+'_include.txt');inc.write_text('\n'.join(e['Path'] for e in chosen)+'\n')
        for e in chosen:guarded(stage/e['Path'])
        print('extract',arc.stem,len(chosen),round(size/1e9,3),'GB',flush=True)
        with (out/(arc.stem+'_7zip.log')).open('w') as h:subprocess.run([SEVEN,'x','-y','-bd','-bb0','-scsUTF-8',str(arc),'-i@'+str(inc),'-o'+str(stage)],stdout=h,stderr=subprocess.STDOUT,check=True)
        records=[]
        for e,cp in zip(chosen,canonical):
            src=guarded(stage/e['Path']);crc=0
            with src.open('rb') as h:
                for buf in iter(lambda:h.read(2**20),b''):crc=zlib.crc32(buf,crc)
            assert src.stat().st_size==int(e['Size']) and f'{crc:08X}'==e['CRC'],'CRC/size mismatch'
            dst=guarded(RAW/'港股通ETF保留'/cp);dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists():assert dst.read_bytes()==src.read_bytes(),'existing canonical data differs'
            else:src.rename(dst) # Verified file moved within authorized raw root; archive retained.
            records.append(dict(path=str(dst),source_member=e['Path'],bytes=int(e['Size']),crc32=e['CRC'],suffix_corrected=PurePosixPath(e['Path']).parts[-2]!=PurePosixPath(cp).parts[1]))
        now=arc.stat();assert finger==(now.st_size,now.st_mtime_ns,now.st_ino),'archive changed'
        manifest.write_text(json.dumps(dict(archive=str(arc),archive_bytes=st.st_size,archive_deleted=False,verified=True,files=records,markets=sorted(markets)),ensure_ascii=False,indent=2))
        print('verified',arc.stem,'SH',len({Path(x['path']).parent.name for x in records if Path(x['path']).parent.name.endswith('.SH')}),'SZ',len({Path(x['path']).parent.name for x in records if Path(x['path']).parent.name.endswith('.SZ')}),'source retained pending verification',flush=True)
        if args.cleanup:cleanup()
if __name__=='__main__':main()
