"""Read-only deployment evidence. The sole outbound IPC message is hello."""
import hashlib, json, socket, struct, subprocess, time
from pathlib import Path
import sys

root = Path.home() / 'Library/Application Support/MachomeHub'
output = root / 'ui-update-20260907'
config = root / 'config/modules.json'
wire = json.dumps({'schema_version': 1, 'protocol': 'module.control.v1',
    'type': 'hello', 'client': 'ui-deployment-readonly-check',
    'client_instance_id': 'ui-check-' + str(time.time_ns()),
    'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}).encode()

def receive(sock):
    def exact(n):
        result = b''
        while len(result) < n:
            part = sock.recv(n - len(result))
            if not part: raise EOFError()
            result += part
        return result
    size = struct.unpack('!I', exact(4))[0]
    if size > 1048576: raise ValueError('frame too large')
    return json.loads(exact(size))

with socket.socket(socket.AF_UNIX) as sock:
    sock.settimeout(12)
    sock.connect(str(root / 'runtime/agent.sock'))
    sock.sendall(struct.pack('!I', len(wire)) + wire)
    snapshots = {}
    hello = {}
    until = time.monotonic() + 15
    while time.monotonic() < until:
        value = receive(sock)
        if value.get('type') == 'hello': hello = value
        if value.get('type') == 'snapshot': snapshots[value['module_id']] = value
        if hello and len(snapshots) == 4: break

processes = []
for line in subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True, errors='replace').splitlines():
    parts = line.strip().split(None, 1)
    if len(parts) == 2 and '/Applications/Machome 四合一运行中心.app/' in parts[1]:
        processes.append({'pid': int(parts[0]), 'program': parts[1]})
evidence = {'at': time.time(), 'hello': hello, 'snapshots': snapshots,
            'processes': processes, 'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest()}
dest = output / (sys.argv[1] + '.json')
dest.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
dest.chmod(0o600)
summary = {'config_bound': hello.get('client_config_bound'), 'control_ready': hello.get('control_ready'),
           'instance_id': hello.get('instance_id'), 'modules': {}, 'process_count': len(processes)}
for key, message in snapshots.items():
    payload = message['payload']; telemetry = payload.get('telemetry', {})
    row = {'lifecycle': payload.get('lifecycle'), 'work_state': payload.get('work_state'),
           'last_error': payload.get('last_error')}
    if key == 'upload': row['problems'] = telemetry.get('engine', {}).get('readiness_problems')
    if key == 'premium':
        status = telemetry.get('status', {})
        row.update({k: status.get(k) for k in ['ready_symbols', 'watchlist_symbols', 'upstream_healthy']})
    if key == 'webull':
        status = telemetry.get('status', {})
        row.update({k: status.get(k) for k in ['auth_state', 'data_state', 'api_live']})
    if key == 'redemption': row['health'] = telemetry.get('health')
    summary['modules'][key] = row
print(json.dumps(summary, ensure_ascii=False))
