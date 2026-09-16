from pathlib import Path
import zipfile,json,struct
R=Path(__file__).resolve().parent;dest=R/'raw2025';dest.mkdir(exist_ok=True)
with zipfile.ZipFile(R/'candidates_2025.zip') as z:
 for member in z.infolist():
  # Windows 7za stores CP936 names plus the standard Unicode Path extra field.
  name=member.filename;extra=member.extra
  while len(extra)>=4:
   tag,size=struct.unpack('<HH',extra[:4]);value=extra[4:4+size];extra=extra[4+size:]
   if tag==0x7075 and len(value)>=5 and value[0]==1:name=value[5:].decode('utf-8')
  p=(dest/name).resolve()
  assert p!=dest.resolve() and dest.resolve() in p.parents,member.filename
  legacy=(dest/member.filename).resolve()
  if legacy.exists() and legacy!=p:legacy.rename(p)
  elif not p.exists():
   member.filename=name;z.extract(member,dest)
files=json.loads((R/'transfer_files.json').read_text())
for f in files:assert Path(f['path']).stat().st_size==f['bytes'],f['path']
print('EXTRACTED',len(files),'requested files; byte sizes match',flush=True)
