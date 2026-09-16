"""Low-space bootstrap: verified target-only internal copy before source unlink.
Internal safety copy is retained. Only RAW/2026 archive is deleted.
"""
from pathlib import Path,PurePosixPath
import json,shutil,subprocess,time,zlib,os
from extract_archives import RAW,R,SEVEN,guarded,members

def crc(p):
    v=0
    with p.open('rb') as h:
        for b in iter(lambda:h.read(4*1024**2),b''):v=zlib.crc32(b,v)
    return f'{v:08X}'
def save(p,v):
    t=p.with_suffix('.tmp');t.write_text(json.dumps(v,ensure_ascii=False,indent=2));os.replace(t,p)
def main():
    codes={s.split('.')[0]:s for s in json.loads((R.parent/'inputs/universe.json').read_text())}
    archives=[p for p in sorted((RAW/'2026').rglob('*.7z')) if p.stem.startswith('2026') and len(p.stem)==8 and time.time()-p.stat().st_mtime>90]
    if not archives:print('no stable archive',flush=True);return
    arc=guarded(archives[0]);st=arc.stat();finger=(st.st_size,st.st_mtime_ns,st.st_ino)
    chosen=[]
    for e in members(arc):
        p=PurePosixPath(e['Path'])
        if len(p.parts)<2 or p.parts[-2].split('.')[0] not in codes:continue
        assert len(p.parts)==2 or (len(p.parts)==3 and p.parts[0]==arc.stem),'unexpected nesting'
        chosen.append(e)
    assert chosen
    canonical=[codes[PurePosixPath(e['Path']).parts[-2].split('.')[0]] for e in chosen]
    assert {s.split('.')[1] for s in canonical}=={'SH','SZ'}
    destinations=[str(RAW/'港股通ETF保留'/arc.stem/s/PurePosixPath(e['Path']).name) for e,s in zip(chosen,canonical)]
    assert len(destinations)==len(set(destinations))
    stage=R/'internal_safety_copies'/arc.stem;stage.mkdir(parents=True,exist_ok=True)
    assert not stage.is_symlink() and stage.resolve()==stage
    assert shutil.disk_usage(stage).free>sum(int(e['Size']) for e in chosen)+2*1024**3
    inc=stage/'include.txt';inc.write_text('\n'.join(e['Path'] for e in chosen)+'\n')
    print('INTERNAL_EXTRACT',arc.name,len(chosen),flush=True)
    with (stage/'7zip.log').open('w') as h:subprocess.run([SEVEN,'x','-y','-bd','-bb0',str(arc),'-i@'+str(inc),'-o'+str(stage/'raw')],stdout=h,stderr=subprocess.STDOUT,check=True)
    records=[]
    for e,dst in zip(chosen,destinations):
        src=stage/'raw'/e['Path'];assert not src.is_symlink() and src.resolve()==src
        assert src.stat().st_size==int(e['Size']) and crc(src)==e['CRC']
        guarded(dst)
        records.append(dict(path=dst,safety_copy=str(src),source_member=e['Path'],bytes=int(e['Size']),crc32=e['CRC']))
    now=arc.stat();assert finger==(now.st_size,now.st_mtime_ns,now.st_ino)
    report=dict(archive=str(arc),archive_bytes=st.st_size,archive_deleted=False,verified=False,internal_copy_verified=True,files=records,markets=['SH','SZ'],deletion_verification=dict(full_source_target_set_matched=True,all_retained_crc_checked=True,verification_location='internal_safety_copy',archive_fingerprint=list(finger)))
    manifest=R/'manifests_2026'/(arc.stem+'.json');save(manifest,report)
    guarded(arc).unlink();report.update(archive_deleted=True,released_archive_bytes=st.st_size);save(manifest,report)
    print('SOURCE_DELETED_AFTER_INTERNAL_CRC',arc.name,'released_GB',st.st_size/1e9,flush=True)
    for rec in records:
        dst=guarded(rec['path']);dst.parent.mkdir(parents=True,exist_ok=True)
        if not dst.exists():shutil.copyfile(rec['safety_copy'],dst)
        assert dst.stat().st_size==rec['bytes'] and crc(dst)==rec['crc32']
    report['verified']=True;report['restored_to_external']=True;save(manifest,report)
    print('RESTORED_EXTERNAL',len(records),'free_GB',shutil.disk_usage(RAW).free/1e9,flush=True)
if __name__=='__main__':main()
