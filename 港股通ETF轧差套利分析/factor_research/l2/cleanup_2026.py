"""User-authorized 2026 source archive cleanup after full target-set and CRC verification.
Deletes only a completed 2026 archive under RAW/2026; no recursive deletion.
"""
from pathlib import Path,PurePosixPath
import json,time,zlib,shutil,os
from extract_archives import RAW,R,guarded,members

def cleanup():
    codes={s.split('.')[0]:s for s in json.loads((R.parent/'inputs/universe.json').read_text())}
    released=0;done=[]
    for mp in sorted((R/'manifests_2026').glob('2026*.json')):
        report=json.loads(mp.read_text());arc=Path(report['archive'])
        if not arc.exists():continue
        guarded(arc)
        if RAW/'2026' not in arc.parents or arc.suffix!='.7z' or arc.stem!=mp.stem:raise ValueError('invalid archive scope')
        st=arc.stat();finger=(st.st_size,st.st_mtime_ns,st.st_ino)
        if time.time()-st.st_mtime<90:continue
        if not report.get('verified'):continue
        expected={}
        for e in members(arc):
            p=PurePosixPath(e['Path'])
            if len(p.parts)<2:continue
            code=p.parts[-2].split('.')[0]
            if code not in codes:continue
            if not (len(p.parts)==2 or (len(p.parts)==3 and p.parts[0]==arc.stem)):raise ValueError('unexpected target nesting')
            target=guarded(RAW/'港股通ETF保留'/arc.stem/codes[code]/p.name)
            if str(target) in expected:raise ValueError('canonical collision')
            expected[str(target)]=dict(source_member=e['Path'],crc32=e['CRC'],bytes=int(e['Size']))
        records={x['path']:x for x in report['files']}
        if not expected or set(records)!=set(expected) or len(records)!=len(report['files']):raise ValueError('manifest target coverage mismatch '+arc.name)
        if {Path(x).parent.suffix for x in expected}!={'.SH','.SZ'}:raise ValueError('missing market')
        for path,e in expected.items():
            rec=records[path]
            if any(rec.get(k)!=e[k] for k in ['source_member','crc32','bytes']):raise ValueError('manifest content differs from archive')
            p=guarded(path);crc=0
            if not p.is_file() or p.stat().st_size!=e['bytes']:raise ValueError('missing/incomplete target '+path)
            with p.open('rb') as h:
                for buf in iter(lambda:h.read(4*1024**2),b''):crc=zlib.crc32(buf,crc)
            if f'{crc:08X}'!=e['crc32']:raise ValueError('CRC failed '+path)
        new=arc.stat()
        if finger!=(new.st_size,new.st_mtime_ns,new.st_ino):raise ValueError('archive changed')
        report['deletion_verification']=dict(timestamp=time.strftime('%Y-%m-%dT%H:%M:%S%z'),target_count=len(expected),full_source_target_set_matched=True,all_retained_crc_checked=True,archive_fingerprint=list(finger))
        tmp=mp.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2));os.replace(tmp,mp)
        guarded(arc).unlink()
        report['archive_deleted']=True;report['released_archive_bytes']=st.st_size
        tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2));os.replace(tmp,mp)
        released+=st.st_size;done.append(arc.stem)
        print('DELETED_VERIFIED',arc.name,'GB',round(st.st_size/1e9,3),'target_files',len(expected),'free_GB',round(shutil.disk_usage(RAW).free/1e9,2),flush=True)
    print('CLEANUP_COMPLETE',len(done),'released_GB',round(released/1e9,3),flush=True)
    return done
if __name__=='__main__':cleanup()
