"""Actual scheduler/localhost protocol fixtures; no live-cloud or full-TTY claim."""
from pathlib import Path
import json, sys, time, threading, tempfile, subprocess, resource, os
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from fullagent.computer.engine import Computer
from fullagent.computer.state import Settings
from fullagent.computer.view import dashboard_lines, family_lines, cells, api_text
from tests.api_support import AcceleratedClock, ModelHTTPFixture, FaultDriver, CommandDriver
from tests.hierarchy_support import HierarchyDriver

out = Path(os.environ['FULLAGENT_QA_OUTPUT']) if os.environ.get('FULLAGENT_QA_OUTPUT') else Path(tempfile.mkdtemp(prefix='fullagent-api-qa-')); out.mkdir(parents=True, exist_ok=True)
fontfile = subprocess.check_output(['fc-match','-f','%{file}','monospace'], text=True).strip()
font, small = ImageFont.truetype(fontfile,16), ImageFont.truetype(fontfile,14)
cell = font.getlength('M')

def capture(snapshot, name, cols=120, rows=21, family=None):
    lines = family_lines(snapshot,family,cols,rows) if family else dashboard_lines(snapshot,cols,rows)
    assert len(lines) <= rows and all(cells(text) <= cols for _,text in lines)
    width = int(cols*cell)+48
    image = Image.new('RGB',(width,len(lines)*25+113),'#191919')
    draw = ImageDraw.Draw(image)
    draw.text((24,16),'FULLAGENT 3.4 /on — ADAPTIVE API CONTROL',font=small,fill='#EAEAEA')
    draw.text((24,39),'LOCAL FIXTURE · VIRTUAL SCHEDULER TIME · NOT LIVE CLOUD / FULL TTY',font=small,fill='#DE9255')
    draw.line((24,64,width-24,64),fill='#444444')
    for index,(style,text) in enumerate(lines):
        draw.text((24,77+25*index),text,font=font,fill=style.split()[-1])
    image.save(out/(name+'.png')); image.close()
    (out/(name+'.txt')).write_text('\n'.join(text for _,text in lines))
    (out/(name+'.json')).write_text(json.dumps(snapshot,indent=2))

ready_root = Path(tempfile.mkdtemp(prefix='ready-',dir=out))
ready = Computer(ready_root,out/'state',Settings(),lambda i:None)
capture(ready.dashboard_snapshot(),'api-ready-120')

settings = Settings(hierarchy_enabled=False, plan_rounds=1, repair_rounds=0, work_steps=6,
                    token_budget=3000000, api_cooldown_seconds=1)
clock = AcceleratedClock(40)
faults = FaultDriver(CommandDriver(), {('a3','building',1):4}, delay=20)
project = Path(tempfile.mkdtemp(prefix='http-project-',dir=out))
captured = set()
max_threads = 0
with ModelHTTPFixture(faults, latency=.08) as server:
    e = Computer(project,out/'state',settings,server.factory,lambda k,d:True,governor_factory=clock.factory)
    e.start('Local recovery fixture: eight actual files, four API faults, one counter command')
    deadline = time.monotonic()+150
    try:
        while e.running and time.monotonic()<deadline:
            d = e.dashboard_snapshot(); api = d.get('api',{})
            if e.pool is not None: max_threads = max(max_threads,len(e.pool._threads))
            if 'queue' not in captured and api.get('queued',0)>=4 and api.get('active',0):
                capture(d,'api-queue-80',80,19); captured.add('queue')
            if 'cooldown' not in captured and api.get('recovering') and api.get('active')==0 and api.get('queued'):
                capture(d,'api-cooldown-120'); captured.add('cooldown')
            if 'probe' not in captured and any(r['mode']=='half_open' for r in api.get('domains',[])) and server.active:
                capture(d,'api-probe-120'); captured.add('probe')
            time.sleep(.01)
        assert e.join(2), 'HTTP fixture did not drain'
        data = e.snapshot()
        assert data['status']=='completed', data.get('error')
        assert captured=={'queue','cooldown','probe'},captured
        assert len(faults.faults)==4
        assert 'run_count = 1' in (project/'a3.py').read_text()
        assert len(list(project.glob('a*.py')))==8
        capture(e.dashboard_snapshot(),'api-completed-120')
        (out/'api-status.txt').write_text(api_text(data))
        http_peak = server.peak
        assert http_peak <= settings.api_max_parallel
    finally:
        if e.running: e.cancel(); e.join(10)

blocked_root = Path(tempfile.mkdtemp(prefix='auth-blocked-',dir=out))
auth_fault = FaultDriver(rules={('a1','research',0):-1}, status=401)
with ModelHTTPFixture(auth_fault, latency=.02) as auth_server:
    blocked = Computer(blocked_root,out/'state',settings,auth_server.factory,lambda k,d:True,governor_factory=clock.factory)
    blocked.start('Local authentication-failure fixture: no repeated bad-key calls')
    try:
        assert blocked.join(30), 'Authentication fixture did not drain'
        b = blocked.snapshot()
        assert b['status']=='needs_attention',b.get('error')
        assert b['api']['blocked']==1 and len(auth_fault.faults)==1
        capture(blocked.dashboard_snapshot(),'api-auth-blocked-120')
    finally:
        if blocked.running: blocked.cancel(); blocked.join(10)

family_root = Path(tempfile.mkdtemp(prefix='hierarchy-',dir=out))
driver = HierarchyDriver(); driver.hold_phase='child-research'; driver.release=threading.Event()
h = Computer(family_root,out/'state',Settings(plan_only=True),driver.factory,lambda k,d:True)
h.start('Hold a real scheduler fixture to inspect queued child sessions')
try:
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        d=h.dashboard_snapshot()
        if d.get('api',{}).get('queued',0)>=56: break
        time.sleep(.02)
    assert h.running and d['api']['queued']>=56,d.get('api')
    assert driver.active==1,driver.active
    capture(d,'api-hierarchy-queued-120')
    capture(d,'api-family-queued-120',120,19,family='a3')
finally:
    h.cancel(); driver.release.set(); assert h.join(10)

metrics={'scope':'Deterministic localhost model responses with actual HTTP, file writes, one approved counter command and real subprocess checks; accelerated scheduler time. Plus a held 72-session scheduler. Not live cloud, full TTY, speed or 4 GB hardware validation.',
    'logical_agents_in_http_fixture':8, 'actual_output_files':8, 'simulated_api_failures_recovered':len(faults.faults),
    'counter_command_executions':1, 'peak_actual_localhost_http_requests':http_peak,
    'configured_api_cap_per_domain':settings.api_max_parallel, 'observed_worker_pool_threads':max_threads,
    'hierarchical_sessions':len(d['agents']), 'held_hierarchy_active_fixture_calls':1,
    'held_hierarchy_queued_sessions':d['api']['queued'], 'actual_checks_passed':all(c['ok'] for c in data['checks']),
    'terminal_status':data['status'],'fixture_process_peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),
    'permanent_authentication_faults':len(auth_fault.faults), 'permanent_authentication_status':b['status'],
    'rendered_states':8}
(out/'runtime-fixture.json').write_text(json.dumps(metrics,indent=2))
print(json.dumps(metrics,indent=2))
