"""Validate real duplicate Wind rows and reject ambiguous/truncated multi-row frames."""
import copy,json,struct,subprocess,sys,tempfile
from pathlib import Path
from test_wind_probe_helper import raw_capture

def decode(binary,capture):
    with tempfile.TemporaryDirectory() as directory:
        p=Path(directory)/'captures';p.mkdir();(p/'frame.json').write_text(json.dumps(capture))
        result=subprocess.run([binary,'--stdio','--mode','fixture','--data-root',directory],input='{"action":"subscribe"}\n{"action":"quit"}\n',text=True,capture_output=True,timeout=15)
        assert result.returncode==0,result.stderr
        return [json.loads(line) for line in result.stdout.splitlines() if json.loads(line).get('type') in ['capture','error']]

def main(binary):
    one=raw_capture();raw=bytes.fromhex(one['buffer_58']['hex']);row=raw[12:]
    duplicate=copy.deepcopy(one);duplicate['buffer_58']['hex']=(struct.pack('>III',2,len(row)*2,0)+row+row).hex()
    result=decode(binary,duplicate);assert len(result)==1 and result[0]['type']=='capture';assert result[0]['payload']['values']['etfsellamount']==2_000_000
    conflict=copy.deepcopy(duplicate);other=bytearray(row);other[0]^=1;conflict['buffer_58']['hex']=(struct.pack('>III',2,len(row)*2,0)+row+other).hex()
    assert decode(binary,conflict)[0]['type']=='error'
    truncated=copy.deepcopy(duplicate);truncated['buffer_58']['hex']=truncated['buffer_58']['hex'][:-2];assert decode(binary,truncated)[0]['type']=='error'
    zero=copy.deepcopy(one);zero['buffer_58']['hex']=(struct.pack('>III',0,len(row),0)+row).hex();assert decode(binary,zero)[0]['type']=='error'
    print('duplicate rows, conflicting rows, truncation and zero rows: PASS')
if __name__=='__main__':main(sys.argv[1])
