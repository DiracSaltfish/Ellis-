"""Emergency 2026 extractor when the source disk cannot hold a staging copy.

Target archives remain under RAW. Selected members are extracted to a temporary
directory on Upan, CRC checked, moved into the authorized retained-data tree,
then the unchanged original archive is deleted. The temporary directory only
contains data extracted by this run and is removed after each archive.
"""
from pathlib import Path, PurePosixPath
import json, time, subprocess, zlib, shutil, os
from extract_archives import RAW, R, SEVEN, guarded, members

STAGE_ROOT = Path('/Volumes/Upan/.codex_l2_stage_2026')

def safe_stage(p: Path) -> Path:
    p = Path(p)
    if p == STAGE_ROOT or STAGE_ROOT in p.parents:
        if any(x.is_symlink() for x in p.parents if x != p):
            raise ValueError('symlink in temporary stage')
        return p
    raise ValueError('temporary path outside stage root')

def main():
    codes = {s.split('.')[0]: s for s in json.loads((R.parent / 'inputs/universe.json').read_text())}
    out = R / 'manifests_2026'
    out.mkdir(exist_ok=True)
    STAGE_ROOT.mkdir(parents=True, exist_ok=True)
    archives = [p for p in sorted((RAW / '2026').rglob('*.7z'))
                if len(p.stem) == 8 and p.stem.startswith('2026')
                and time.time() - p.stat().st_mtime > 120]
    for arc in archives:
        manifest = out / (arc.stem + '.json')
        if manifest.exists() and json.loads(manifest.read_text()).get('verified'):
            continue
        guarded(arc)
        st = arc.stat(); fingerprint = (st.st_size, st.st_mtime_ns, st.st_ino)
        entries = members(arc)
        selected = []
        for e in entries:
            q = PurePosixPath(e['Path'])
            if (len(q.parts) == 2 or (len(q.parts) == 3 and q.parts[0] == arc.stem)) and q.parts[-2].split('.')[0] in codes:
                selected.append(e)
        if not selected:
            raise ValueError('no target files in ' + str(arc))
        markets = {codes[PurePosixPath(e['Path']).parts[-2].split('.')[0]].split('.')[1] for e in selected}
        if markets != {'SH', 'SZ'}:
            raise ValueError('incomplete market coverage in ' + str(arc))
        total = sum(int(e['Size']) for e in selected)
        if shutil.disk_usage(STAGE_ROOT).free < total * 1.25 + 2 * 1024**3:
            raise RuntimeError('insufficient temporary-stage space')
        stage = safe_stage(STAGE_ROOT / arc.stem)
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        include = out / (arc.stem + '_offload_include.txt')
        include.write_text('\n'.join(e['Path'] for e in selected) + '\n')
        log = out / (arc.stem + '_offload_7zip.log')
        print('offload_extract', arc.stem, len(selected), round(total / 1e9, 3), 'GB', flush=True)
        with log.open('w') as h:
            subprocess.run([SEVEN, 'x', '-y', '-bd', '-bb0', '-scsUTF-8', str(arc),
                            '-i@' + str(include), '-o' + str(stage)], stdout=h,
                           stderr=subprocess.STDOUT, check=True)
        records = []
        for e in selected:
            q = PurePosixPath(e['Path'])
            src = safe_stage(stage / Path(*q.parts))
            crc = 0
            if not src.is_file():
                raise ValueError('missing extracted target ' + str(src))
            with src.open('rb') as h:
                for buf in iter(lambda: h.read(2**20), b''):
                    crc = zlib.crc32(buf, crc)
            if src.stat().st_size != int(e['Size']) or f'{crc:08X}' != e['CRC']:
                raise ValueError('CRC/size mismatch ' + str(src))
            code = q.parts[-2].split('.')[0]
            dst = guarded(RAW / '港股通ETF保留' / arc.stem / codes[code] / q.name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                if dst.stat().st_size != src.stat().st_size or dst.read_bytes() != src.read_bytes():
                    raise ValueError('canonical target collision ' + str(dst))
                src.unlink()
            else:
                shutil.move(str(src), str(dst))
            records.append(dict(path=str(dst), source_member=e['Path'], bytes=int(e['Size']), crc32=e['CRC'],
                                suffix_corrected=q.parts[-2] != codes[code]))
        now = arc.stat()
        if fingerprint != (now.st_size, now.st_mtime_ns, now.st_ino):
            raise ValueError('archive changed during offload extraction')
        report = dict(archive=str(arc), archive_bytes=st.st_size, archive_deleted=False,
                      verified=True, offloaded=True, files=records, markets=sorted(markets))
        tmp = manifest.with_suffix('.tmp')
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2)); os.replace(tmp, manifest)
        guarded(arc).unlink()
        report['archive_deleted'] = True; report['released_archive_bytes'] = st.st_size
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2)); os.replace(tmp, manifest)
        # Some macOS archives leave resource-fork entries after all selected
        # CSVs have moved. They are temporary extraction artifacts; ignore a
        # disappearing entry while removing only this run's stage directory.
        shutil.rmtree(stage, ignore_errors=True)
        try: include.unlink()
        except FileNotFoundError: pass
        print('offload_verified_deleted', arc.name, 'target_files', len(records),
              'free_GB', round(shutil.disk_usage(RAW).free / 1e9, 2), flush=True)

if __name__ == '__main__':
    main()
