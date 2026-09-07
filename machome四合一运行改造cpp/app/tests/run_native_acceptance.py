#!/usr/bin/env python3
"""Four native engines, random ports, private stores; no old services or credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import run_full_smoke as ipc


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=12);parser.add_argument('--bundled-upload',action='store_true');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='hub-native-',dir='/private/tmp') as temp:
        root=Path(temp);config=json.loads(ipc.CONFIG.read_text())
        config['agent_socket']=str(root/'agent.sock');config['audit_database']=str(root/'audit.db')
        ports=set()
        def port():
            while True:
                with socket.socket() as bound:
                    bound.bind(('127.0.0.1',0));value=bound.getsockname()[1]
                if value not in ports:ports.add(value);return value
        for module in config['modules']:
            key=module['id'];module['engine']='native';module['ownership']='logic'
            module['allowed_actions']=list(set(module['allowed_actions']+['set_operating_mode','stop_service','start_service','restart_service']))
            settings=module['settings'];settings['test_mode']=True
            settings['data_root']=str(root/'MachomeHub'/'data'/key)
            settings['record_only']=True
            if key=='upload':
                settings.update(sink_mode='record_only',ibkr={'enabled':False})
                if args.bundled_upload:
                    private=Path(settings['data_root'])/'config'/'upload-business.json';private.parent.mkdir(parents=True)
                    private.write_text(json.dumps({'schema_version':1,'mode':'live','website':{'enabled':False},
                        'environment':{'NNN_SERVER_URL':'http://127.0.0.1:1','NNN_UPLOAD_TOKEN':'isolated-fixture-token'},'workers':[]}));private.chmod(0o600)
                    settings.update(sink_mode='bundled_business',business_config='config/upload-business.json')
            if key=='premium':settings.update(summary_port=port(),l1_port=port(),mode='replay',tgw_helper_enabled=False,watchlist=['159217.SZ'])
            if key=='webull':settings.update(api_port=0,api_host='127.0.0.1',live_browser_enabled=False)
            if key=='redemption':settings.update(compatibility_api_enabled=True,compatibility_port=0,wind_helper_mode='disabled',pcf_network_enabled=False,qmt_backends=[])
        path=root/'modules.json';path.write_text(json.dumps(config))
        ipc.CONFIG_SHA=hashlib.sha256(path.read_bytes()).hexdigest()
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen')
        with open(root/'agent.log','w+') as log:
            agent=subprocess.Popen([str(ipc.AGENT_BINARY),'--config',str(path)],stdout=log,stderr=log,env=env)
            clients=[]
            try:
                deadline=time.monotonic()+8
                while not (root/'agent.sock').exists() and time.monotonic()<deadline:
                    if agent.poll() is not None:log.seek(0);raise AssertionError(log.read())
                    time.sleep(.05)
                def connect():
                    client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);client.connect(str(root/'agent.sock'));clients.append(client)
                    client.sendall(ipc.frame({'schema_version':1,'protocol':'module.control.v1','type':'hello','client':'native-acceptance',
                        'client_instance_id':f'local-{time.time_ns()}','config_sha256':ipc.CONFIG_SHA,'timestamp':ipc.now()}))
                    hello,_=ipc.wait_for(client,lambda m:m.get('type')=='hello');ipc.AGENT_INSTANCE=hello['instance_id']
                    assert hello['control_ready'];return client
                client=connect();observed={};revisions={}
                start=time.monotonic();latencies=[]
                while time.monotonic()-start<args.seconds or len(observed)<4:
                    message=ipc.receive(client,time.monotonic()+5)
                    if message.get('type')!='snapshot':continue
                    key=message['module_id'];payload=message['payload'];telemetry=payload['telemetry'];revisions[key]=message['control_revision']
                    engine=telemetry.get('engine',{})
                    identity=engine.get('engine') if isinstance(engine,dict) else None
                    if key=='webull':identity=telemetry.get('status',{}).get('engine')
                    if args.bundled_upload and key=='upload':
                        assert engine.get('business_engine')=='bundled_business',engine
                        assert engine.get('record_only') is False,engine
                        assert engine.get('running') is True,engine
                    if identity:observed[key]=identity
                    assert agent.poll() is None
                    if time.monotonic()-start>args.seconds+10:break
                assert observed=={'upload':'native','premium':'native_premium_a','webull':'native','redemption':'native'},observed
                for key in observed:
                    ipc.command(client,revisions,key,'set_operating_mode',{'mode':'weekend_test'})
                    ipc.command(client,revisions,key,'set_operating_mode',{'mode':'work'})
                # Single-module lifecycle commands must not restart the Agent.
                for key in observed:
                    ipc.command(client,revisions,key,'restart_service',seconds=12)
                # IPC client reconnect must preserve the same Agent and all engines.
                instance=ipc.AGENT_INSTANCE;client.close();client=connect();assert ipc.AGENT_INSTANCE==instance
                refreshed=set()
                while len(refreshed)<4:
                    m=ipc.receive(client,time.monotonic()+5)
                    if m.get('type')=='snapshot':refreshed.add(m['module_id'])
                print(json.dumps({'passed':True,'engines':observed,'seconds':round(time.monotonic()-start,2),'stores':'temporary','ports':'ephemeral','reconnect':True}))
            finally:
                for client in clients:client.close()
                ipc.terminate(agent)
                if agent.returncode not in (0,-15):log.seek(0);print(log.read()[-6000:])
if __name__=='__main__':main()
