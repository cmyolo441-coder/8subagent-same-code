"""Deterministic TEST clients only. Production has no simulated worker mode.

These clients drive the actual scheduler, file tools, scope leases and
subprocess acceptance checks. They do not measure a live model's ability.
"""
from __future__ import annotations
import json
import re
import sys
import threading
import time
from types import SimpleNamespace

from fullagent.computer.state import AGENT_IDS, CHILD_IDS, child_ids, parent_id
from tests.support import answer, tool


class HierarchyDriver:
    def __init__(self, barrier=0, failure=None, bug=False, review_issue=None,
                 parent_deps=False, child_deps=False, readonly=False, malformed=False):
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.created, self.closed, self.calls = [], [], []
        self.barrier = threading.Barrier(barrier) if barrier else None
        self.failure, self.bug, self.review_issue = failure, bug, review_issue
        self.parent_deps, self.child_deps = parent_deps, child_deps
        self.readonly, self.malformed = readonly, malformed
        self.decompositions = {}
        self.release = None
        self.hold_phase = None
        self.reached = threading.Event()

    def file(self, who):
        lead, number = who.split('.')
        return f'{lead}/w{number}.py'

    def parent_plan(self):
        expected = [self.file(i) for i in CHILD_IDS if not (self.readonly and i == 'a8.8')]
        script = f"from pathlib import Path; files={expected!r}; assert all('FIXED' in Path(f).read_text() for f in files), 'Missing or broken child output'"
        tasks = [{"id": i, "title": f"Integrate family {i}", "instructions": f"Deliver and inspect the eight distinct modules for family {i}",
                  "files": [i+'/'], "depends_on": ['a1'] if self.parent_deps and i == 'a2' else []} for i in AGENT_IDS]
        return {"summary": "Deterministic 64-file hierarchy TEST fixture; not live AI research",
                "tasks": tasks, "checks": [{"kind": "command", "description": "Check actual child output files",
                                             "argv": [sys.executable, '-c', script], "cwd": '.'}]}

    def child_plan(self, lead):
        tasks = []
        for who in child_ids(lead):
            read_only = self.readonly and who == 'a8.8'
            tasks.append({"id": who, "title": f"Deliver module {who}",
                          "instructions": f"{'Inspect shared board evidence for' if read_only else 'Implement and inspect'} distinct module {who}; report real outcomes",
                          "files": [] if read_only else [self.file(who)],
                          "depends_on": ['a1.1'] if self.child_deps and who == 'a1.2' else []})
        return {"tasks": tasks}

    def factory(self, who):
        driver = self
        with self.lock:
            self.created.append(who)
        class Client:
            model = SimpleNamespace(id='deterministic-hierarchy-test-client')
            provider = SimpleNamespace(key='test-provider')
            def chat(self, messages, schemas, max_tokens, control, on_update=None):
                control()
                seed = messages[1]['content']
                match = re.search(r'PHASE: ([^\n]+)', seed)
                phase = match.group(1) if match else 'planning'
                outputs = [m for m in messages if m['role'] == 'tool']
                with driver.lock:
                    driver.active += 1
                    driver.peak = max(driver.peak, driver.active)
                    driver.calls.append((who, phase, len(outputs)))
                try:
                    if phase == driver.hold_phase:
                        driver.reached.set()
                        if driver.release:
                            while not driver.release.wait(.03):
                                control()
                    if driver.failure == (who, phase):
                        raise RuntimeError('Intentional fixture provider failure')
                    if phase == 'child-research':
                        if driver.barrier:
                            driver.barrier.wait(timeout=12)
                        time.sleep(.003)
                        return answer(f'Fixture observation by {who}: split distinct owned modules and validate actual output. No public source was retrieved.')
                    if phase == 'planning':
                        return answer(json.dumps(driver.parent_plan()))
                    if phase == 'decomposing':
                        with driver.lock:
                            n = driver.decompositions.get(who, 0) + 1
                            driver.decompositions[who] = n
                        value = driver.child_plan(who)
                        if driver.malformed and n == 1:
                            value['tasks'][0]['files'] = ['escaped.py']
                        return answer(json.dumps(value))
                    if phase == 'research' or phase.startswith('refine-'):
                        time.sleep(.002)
                        return answer(f'Fixture lead {who} inspected bounded family findings; propose eight distinct modules for this family.')
                    if phase in ('child-building', 'child-repairing'):
                        if driver.readonly and who == 'a8.8':
                            if not outputs:
                                return tool('read_board', {})
                            return answer(json.dumps({'status': 'done', 'summary': 'Read-only fixture inspected actual board state', 'issues': []}))
                        path = driver.file(who)
                        if not outputs:
                            return tool('read_file', {'path': path})
                        if len(outputs) == 1:
                            read = json.loads(outputs[-1]['content'])
                            value = 'BUG' if driver.bug and who == 'a3.3' and phase == 'child-building' else 'FIXED'
                            return tool('write_file', {'path': path, 'expected_sha256': read.get('sha256', 'MISSING'),
                                                       'content': f"value = '{value}'\n"})
                        result = json.loads(outputs[-1]['content'])
                        return answer(json.dumps({'status': 'done' if result.get('ok') else 'blocked',
                                                  'summary': 'Fixture used the real file-writing tool', 'issues': [] if result.get('ok') else [str(result)]}))
                    if phase in ('building', 'repairing'):
                        if not outputs:
                            return tool('read_file', {'path': who+'/w1.py'})
                        value = json.loads(outputs[-1]['content'])
                        return answer(json.dumps({'status': 'done' if 'sha256' in value else 'blocked',
                                                  'summary': 'Parent inspected actual child output after the barrier',
                                                  'issues': [] if 'sha256' in value else [str(value)]}))
                    if phase.startswith('child-review-') or phase.startswith('review-'):
                        if not outputs:
                            if driver.readonly and who == 'a8.8':
                                return tool('read_board', {})
                            return tool('read_file', {'path': driver.file(who) if who in CHILD_IDS else who+'/w1.py'})
                        value = json.loads(outputs[-1]['content'])
                        issue = who == driver.review_issue and phase == 'child-review-0'
                        ok = value.get('ok') is not False and not issue
                        return answer(json.dumps({'status': 'pass' if ok else 'changes_requested',
                                                  'summary': 'Fixture reviewer read current integrated evidence',
                                                  'issues': [] if ok else ['Fixture review found an unresolved requirement']}))
                    raise AssertionError('Unexpected fixture phase: '+phase)
                finally:
                    with driver.lock:
                        driver.active -= 1
            def close(self):
                with driver.lock:
                    driver.closed.append(who)
        return Client()
