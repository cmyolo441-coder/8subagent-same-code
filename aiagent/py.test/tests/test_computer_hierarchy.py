from __future__ import annotations
import copy
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fullagent.computer.engine import Computer, validate_plan
from fullagent.computer.hierarchy import validate_children, scope_within, overlapping
from fullagent.computer.state import (Settings, Board, AGENT_IDS, CHILD_IDS, ALL_AGENT_IDS,
    ComputerError, BudgetExceeded, WorkerBudgetExceeded, Cancelled, child_ids, parent_id,
    agent_specs, inherited_model, load_checkpoint)
from fullagent.computer.governor import RequestGovernor
from fullagent.computer.research import Research
from fullagent.computer.tools import WorkspaceTools
from fullagent.computer.view import family_lines, dashboard_lines, cells, tree_text, inspect_text
from fullagent.computer.bridge import ComputerBridge
from tests.hierarchy_support import HierarchyDriver
from tests.test_computer_ui import FakeUI


class HierarchyEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'project'; self.root.mkdir()
        self.store = Path(self.tmp.name)/'state'
        self.engines = []
        self.settings = Settings(api_adaptive=False, api_max_parallel=64, api_tokens_per_minute=0, hierarchy_enabled=True, plan_rounds=1, repair_rounds=0, token_budget=3000000,
                                 work_steps=5, child_work_steps=5, child_review_steps=3, research_steps=2, request_timeout=5)

    def tearDown(self):
        for e in self.engines:
            if e.running: e.cancel()
            e.join(15)

    def engine(self, driver=None, settings=None, approve=None, listener=None):
        driver = driver or HierarchyDriver()
        e = Computer(self.root, self.store, settings or self.settings, driver.factory,
                     approve or (lambda k,d: True), listener)
        self.engines.append(e)
        return e

    def run_mission(self, e, resume=None):
        e.start('Build the 64-file deterministic hierarchy fixture', resume_id=resume)
        self.assertTrue(e.join(90), 'hierarchy did not drain')
        return e.snapshot()

    def test_all_64_children_really_write_and_72_clients_are_closed(self):
        driver = HierarchyDriver(barrier=8)
        e = self.engine(driver); d = self.run_mission(e)
        self.assertEqual(d['status'], 'completed', d.get('error'))
        self.assertEqual(len(d['agents']), 72)
        self.assertEqual(set(driver.created), set(ALL_AGENT_IDS))
        self.assertEqual(set(driver.closed), set(ALL_AGENT_IDS))
        self.assertEqual({i for i,p,n in driver.calls if p == 'child-building'}, set(CHILD_IDS))
        self.assertEqual(len(list(self.root.glob('a*/w*.py'))), 64)
        self.assertEqual(len(d['files']), 64)
        self.assertTrue(all(t['status'] == 'done' for t in d['hierarchy']['tasks'].values()))
        self.assertTrue(all(c['ok'] for c in d['checks']))
        self.assertEqual(d['reserved_tokens'], 0)
        self.assertEqual(d['charged_tokens'], sum(a['charged_tokens'] for a in d['agents'].values()))
        self.assertEqual(driver.peak, 8)
        self.assertEqual(e.tools.active_owners, set())
        self.assertTrue((e.board.path/'hierarchy-plan.json').exists())

    def test_one_pool_at_cap_two_has_no_nested_executor_deadlock(self):
        driver = HierarchyDriver()
        d = self.run_mission(self.engine(driver, replace(self.settings, max_parallel=2)))
        self.assertEqual(d['status'], 'completed', d.get('error'))
        self.assertLessEqual(driver.peak, 2)

    def test_resume_rechecks_changed_parent_scope_and_its_dependents(self):
        first_driver=HierarchyDriver(parent_deps=True)
        e=self.engine(first_driver,approve=lambda k,d:k=='plan')
        old=self.run_mission(e)
        self.assertEqual(old['status'],'needs_attention')
        (self.root/'a1/w1.py').write_text("value = 'EXTERNAL_CHANGE'\n")
        second=HierarchyDriver(parent_deps=True)
        data=self.run_mission(self.engine(second),old['id'])
        self.assertEqual(data['status'],'completed',data.get('error'))
        built={parent_id(i) for i,p,n in second.calls if p=='child-building'}
        self.assertEqual(built,{'a1','a2'})
        self.assertIn('FIXED',(self.root/'a1/w1.py').read_text())

    def test_explicit_64_concurrent_calls_are_possible_without_72_threads(self):
        driver = HierarchyDriver(barrier=64)
        d = self.run_mission(self.engine(driver, replace(self.settings, max_parallel=64, plan_only=True)))
        self.assertEqual(d['status'], 'planned', d.get('error'))
        self.assertEqual(driver.peak, 64)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_ready_families_get_one_initial_slot_each(self):
        driver = HierarchyDriver(barrier=8)
        d = self.run_mission(self.engine(driver, replace(self.settings, plan_only=True)))
        first = [i for i,p,n in driver.calls if p == 'child-research'][:8]
        self.assertEqual({parent_id(i) for i in first}, set(AGENT_IDS))
        self.assertEqual(d['status'], 'planned')

    def test_parent_and_sibling_dependency_barriers(self):
        events = []
        driver = HierarchyDriver(parent_deps=True, child_deps=True)
        d = self.run_mission(self.engine(driver, listener=lambda x: events.append(x)))
        self.assertEqual(d['status'], 'completed', d.get('error'))
        def seq(kind, who):
            return next(x['seq'] for x in events if x['kind'] == kind and x['agent'] == who)
        self.assertLess(seq('child.finished','a1.1'), seq('hierarchy.scheduled','a1.2'))
        self.assertLess(max(seq('child.finished',i) for i in child_ids('a1')), seq('hierarchy.scheduled','a1'))
        self.assertLess(seq('task.finished','a1'), seq('hierarchy.scheduled','a2.1'))

    def test_plan_denial_means_zero_child_or_parent_writes(self):
        requests = []
        def approve(k, d): requests.append((k, d)); return False
        d = self.run_mission(self.engine(approve=approve))
        self.assertEqual(d['status'], 'planned')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(len(requests[0][1]['hierarchy']['children']), 64)
        self.assertEqual(requests[0][1]['hierarchy']['parallel_cap'], 8)

    def test_one_child_failure_blocks_its_parent_not_other_families(self):
        driver = HierarchyDriver(failure=('a3.3','child-building'))
        d = self.run_mission(self.engine(driver))
        self.assertEqual(d['status'], 'needs_attention')
        self.assertEqual(d['hierarchy']['tasks']['a3.3']['status'], 'blocked')
        self.assertEqual(d['tasks']['a3']['status'], 'blocked')
        self.assertTrue(all(d['tasks'][i]['status'] == 'done' for i in AGENT_IDS if i != 'a3'))

    def test_real_failure_repair_and_retest_of_child_outputs(self):
        driver = HierarchyDriver(bug=True)
        e = self.engine(driver, replace(self.settings, repair_rounds=1))
        d = self.run_mission(e)
        self.assertEqual(d['status'], 'completed', d.get('error'))
        self.assertEqual(d['repair_round'], 1)
        self.assertIn('FIXED', (self.root/'a3/w3.py').read_text())
        self.assertTrue(any('BUG' in p.read_text() for p in (e.board.path/'backups').iterdir()))

    def test_child_review_cannot_be_overruled_by_a_lead_success_claim(self):
        d = self.run_mission(self.engine(HierarchyDriver(review_issue='a4.6')))
        self.assertTrue(all(c['ok'] for c in d['checks']))
        self.assertEqual(d['status'], 'needs_attention')
        self.assertEqual(d['reports']['child-review-0']['a4.6']['status'], 'changes_requested')

    def test_review_only_repair_targets_one_family_and_dependents(self):
        driver = HierarchyDriver(review_issue='a3.4')
        d = self.run_mission(self.engine(driver, replace(self.settings, repair_rounds=1)))
        self.assertEqual(d['status'], 'completed', d.get('error'))
        self.assertEqual(d['hierarchy']['last_repair_targets'], ['a3'])
        repaired = {parent_id(i) for i,p,n in driver.calls if p == 'child-repairing'}
        self.assertEqual(repaired, {'a3'})

    def test_readonly_child_has_no_writes_or_commands_but_real_board_work(self):
        driver = HierarchyDriver(readonly=True)
        d = self.run_mission(self.engine(driver))
        self.assertEqual(d['status'], 'completed', d.get('error'))
        self.assertEqual(len(list(self.root.glob('a*/w*.py'))), 63)
        self.assertGreater(d['agents']['a8.8']['tools'], 0)
        self.assertFalse((self.root/'a8/w8.py').exists())

    def test_invalid_child_plan_gets_repaired_before_any_approval(self):
        driver = HierarchyDriver(malformed=True)
        d = self.run_mission(self.engine(driver, replace(self.settings, plan_only=True)))
        self.assertEqual(d['status'], 'planned', d.get('error'))
        self.assertTrue(all(v == 2 for v in driver.decompositions.values()))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cancel_drains_child_calls_releases_lease_and_checkpoints(self):
        driver = HierarchyDriver(); driver.hold_phase = 'child-building'; driver.release = threading.Event()
        e = self.engine(driver); e.start('Run cancellation fixture')
        self.assertTrue(driver.reached.wait(45))
        e.cancel(); self.assertTrue(e.join(15))
        self.assertEqual(e.snapshot()['status'], 'cancelled')
        self.assertEqual(e.snapshot()['reserved_tokens'], 0)
        self.assertEqual(e.tools.active_owners, set())
        driver2 = HierarchyDriver()
        resumed = self.run_mission(self.engine(driver2), e.board.data['id'])
        self.assertEqual(resumed['status'], 'completed', resumed.get('error'))

    def test_resume_reuses_successful_child_discovery(self):
        d1 = HierarchyDriver(failure=('a1.1','child-research'))
        e = self.engine(d1); first = self.run_mission(e)
        self.assertEqual(first['status'], 'needs_attention')
        d2 = HierarchyDriver()
        final = self.run_mission(self.engine(d2), first['id'])
        self.assertEqual(final['status'], 'completed', final.get('error'))
        self.assertEqual([i for i,p,n in d2.calls if p == 'child-research'], ['a1.1'])

    def test_resume_cannot_silently_disable_hierarchy(self):
        e = self.engine(settings=replace(self.settings, plan_only=True))
        d = self.run_mission(e)
        other = self.engine(settings=replace(self.settings, hierarchy_enabled=False))
        with self.assertRaisesRegex(ComputerError, 'swarm on'):
            other.start(resume_id=d['id'])


class HierarchySafetyTests(unittest.TestCase):
    def setUp(self):
        self.driver = HierarchyDriver()
        self.parent = self.driver.parent_plan()['tasks'][0]
        self.good = self.driver.child_plan('a1')

    def test_exact_topology_and_invalid_deeper_recursion(self):
        self.assertEqual(len(agent_specs()),72)
        self.assertEqual(len(set(ALL_AGENT_IDS)),72)
        self.assertEqual(len(child_ids('a8')),8)
        for invalid in ('a1.1.1','a0','a9.1','a1.9','root'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): parent_id(invalid)

    def test_child_model_overrides_inherit_parent_then_global(self):
        values={'a1':'lead-model','a1.2':'child-model'}
        self.assertEqual(inherited_model('a1.1',values,'default'),'lead-model')
        self.assertEqual(inherited_model('a1.2',values,'default'),'child-model')
        self.assertEqual(inherited_model('a2.1',values,'default'),'default')

    def test_valid_child_plan_strips_untrusted_permissions(self):
        self.good['tasks'][0].update(approved=True,max_parallel=999,children=[{}])
        tasks=validate_children(self.good,self.parent)
        self.assertEqual(len(tasks),8)
        self.assertNotIn('approved',tasks[0]); self.assertNotIn('children',tasks[0])

    def test_scope_escape_prefix_case_and_traversal_are_rejected(self):
        for scope in ('outside.py','a10/file.py','A1/w1.py','../secret.py','/tmp/x','a1/.env'):
            with self.subTest(scope=scope):
                value=copy.deepcopy(self.good);value['tasks'][0]['files']=[scope]
                with self.assertRaises((ComputerError,ValueError)):validate_children(value,self.parent)

    def test_sibling_overlap_and_cycle_are_rejected(self):
        for mode in ('overlap','directory','case','cycle','ancestor','foreign','duplicate'):
            value=copy.deepcopy(self.good)
            if mode=='overlap':value['tasks'][1]['files']=['a1/w1.py']
            if mode=='directory':value['tasks'][0]['files']=['a1/']
            if mode=='case':value['tasks'][1]['files']=['a1/W1.py']
            if mode=='cycle':
                value['tasks'][0]['depends_on']=['a1.2'];value['tasks'][1]['depends_on']=['a1.1']
            if mode=='ancestor':value['tasks'][0]['depends_on']=['a1']
            if mode=='foreign':value['tasks'][0]['id']='a2.1'
            if mode=='duplicate':value['tasks'][0]['instructions']=value['tasks'][1]['instructions']
            with self.subTest(mode=mode), self.assertRaises(ComputerError):validate_children(value,self.parent)

    def test_parent_scope_case_overlap_is_rejected(self):
        value=self.driver.parent_plan();value['tasks'][1]['files']=['A1/W2.py']
        with self.assertRaises(ComputerError):validate_plan(value)

    def test_settings_have_safe_defaults_and_explicit_64_cap(self):
        self.assertTrue(Settings().hierarchy_enabled)
        self.assertEqual(Settings().max_parallel,8)
        self.assertEqual(Settings(max_parallel=64).max_parallel,64)
        for kwargs in ({'max_parallel':65},{'hierarchy_enabled':1},{'child_work_steps':0},
                       {'requests_per_minute':-1},{'child_token_budget':0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):Settings(**kwargs)


class HierarchyResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project';self.root.mkdir()
        self.board=Board(self.root,Path(self.tmp.name)/'state','resource fixture',Settings(child_token_budget=2000))
        self.tools=WorkspaceTools(self.root,self.board,None,lambda:None,lambda k,d:True)
        self.tools.scopes={'a1':['a1/'],'a1.1':['a1/x.py'],'a1.2':['a1/y.py']}
        self.tools.approved_plan=True;self.tools.active_owners=set()

    def test_parent_and_child_cannot_hold_execution_leases_together(self):
        self.tools.activate_owner('a1.1')
        with self.assertRaises(ComputerError):self.tools.activate_owner('a1')
        self.tools.activate_owner('a1.2')
        self.tools.deactivate_owner('a1.1');self.tools.deactivate_owner('a1.2')
        self.tools.activate_owner('a1')
        with self.assertRaises(ComputerError):self.tools.activate_owner('a1.1')

    def test_inactive_worker_cannot_write_or_run_host_code(self):
        with self.assertRaises(ComputerError):self.tools.write_file('a1.1','a1/x.py','value=1','MISSING')
        with self.assertRaises(ComputerError):self.tools.run_command('a1.1',['python','-V'])
        self.tools.activate_owner('a1.1')
        result=self.tools.write_file('a1.1','a1/x.py','value=1','MISSING')
        self.assertTrue(result['ok'])
        with self.assertRaises(ComputerError):self.tools.write_file('a1.1','a1/y.py','value=2','MISSING')

    def test_child_budget_is_local_global_budget_is_shared(self):
        t=self.board.reserve(1500,'a1.1')
        with self.assertRaises(WorkerBudgetExceeded):self.board.reserve(600,'a1.1')
        other=self.board.reserve(1000,'a2.1')
        self.board.settle(t,'a1.1',{'prompt_tokens':100,'completion_tokens':50})
        self.board.settle(other,'a2.1',None)
        self.assertEqual(self.board.data['charged_tokens'],1150)
        self.assertEqual(self.board.data['agents']['a2.1']['estimated_tokens'],1000)
        with self.assertRaises(BudgetExceeded):self.board.reserve(400000,'a3.1')

    def test_reservation_cannot_be_settled_against_another_child(self):
        t=self.board.reserve(1000,'a1.1')
        with self.assertRaises(ValueError):self.board.settle(t,'a1.2',None)
        self.board.settle(t,'a1.1',None)
        self.assertEqual(self.board.data['reserved_tokens'],0)

    def test_crash_charges_outstanding_tokens_to_the_actual_child(self):
        self.board.reserve(1200,'a1.1')
        before=load_checkpoint(self.board.path.parent,self.board.data['id'])
        restored=Board(self.root,self.board.path.parent,'ignored',self.board.settings,previous=before)
        self.assertEqual(restored.data['charged_tokens'],1200)
        self.assertEqual(restored.data['agents']['a1.1']['charged_tokens'],1200)
        self.assertEqual(restored.data['agents']['a1.1']['reserved_tokens'],0)
        self.assertEqual(restored.data['inflight'],{})

    def test_dashboard_projection_excludes_large_reports_and_fingerprints(self):
        self.board.data['reports']['huge']={'a1.1':{'text':'x'*100000}}
        self.board.data['tasks']['a1']={'status':'pending','fingerprint':{'file':'x'*100000}}
        snap=self.board.dashboard_snapshot()
        self.assertNotIn('reports',snap)
        self.assertNotIn('fingerprint',snap['tasks']['a1'])
        self.assertLess(len(json.dumps(snap)),50000)

    def test_shared_board_filters_family_handoffs_and_rejects_impersonation(self):
        self.tools.share_note('a2.1','Other family detail','a2')
        self.tools.share_note('a1.2','Useful sibling result','a1.1')
        board=self.tools.read_board('a1.1')
        self.assertEqual([n['message'] for n in board['notes']],['Useful sibling result'])
        with self.assertRaises(ComputerError):self.tools.execute('a1.1','read_board',{'agent_id':'a2'})

    def test_approval_fails_closed_instead_of_hiding_a_truncated_scope(self):
        with self.assertRaises(ComputerError):self.tools.approval('plan',{'scopes':'x'*240001})

    def test_research_singleflight_coalesces_actual_concurrent_fetches(self):
        research=Research(self.board,lambda:None)
        called=[];entered=threading.Event();release=threading.Event()
        def fetch():called.append(1);entered.set();release.wait(5);return [{'title':'fixture result'}]
        with ThreadPoolExecutor(max_workers=8) as pool:
            work=[pool.submit(research._cached,('source','query'),fetch) for _ in range(8)]
            self.assertTrue(entered.wait(2));time.sleep(.03);release.set()
            values=[f.result(3) for f in work]
        self.assertEqual(len(called),1)
        values[0][0]['title']='mutated'
        self.assertEqual(values[1][0]['title'],'fixture result')
        self.assertFalse(research.inflight)

    def test_singleflight_failure_does_not_repeat_requests_or_fake_results(self):
        research=Research(self.board,lambda:None);called=[]
        def fail():called.append(1);raise ComputerError('fixture source unavailable')
        for _ in range(3):
            with self.assertRaises(ComputerError):research._cached(('bad','source'),fail)
        self.assertEqual(len(called),1)
        self.assertFalse(research.inflight)

    def test_provider_pacing_and_shared_cooldown(self):
        now=[0.0]
        governor=RequestGovernor(60,clock=lambda:now[0],sleeper=lambda n:now.__setitem__(0,now[0]+n))
        governor.admit('p',lambda:None);governor.admit('p',lambda:None)
        self.assertGreaterEqual(now[0],1)
        governor.penalize('p',3);self.assertGreater(governor.status()['p'],0)
        governor.admit('p',lambda:None);self.assertGreaterEqual(now[0],4)
        before=now[0];governor.admit('another-provider',lambda:None);self.assertEqual(now[0],before)

    def test_governor_wait_is_cancellation_aware(self):
        governor=RequestGovernor();governor.penalize('p',10)
        def cancel():raise Cancelled('fixture cancellation')
        with self.assertRaises(Cancelled):governor.admit('p',cancel)


class HierarchyViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.engine=Computer(self.root,self.root/'state',Settings(),lambda i:None)
        self.snap=self.engine.snapshot()

    def test_overview_and_family_are_bounded_at_small_terminal_sizes(self):
        self.snap['agents']['a1.1']['activity']='測試 🚀 '*300
        for width,height in ((120,21),(80,19),(60,18),(20,6)):
            for fn in (lambda:dashboard_lines(self.snap,width,height),lambda:family_lines(self.snap,'a1',width,height)):
                out=fn();self.assertLessEqual(len(out),height)
                self.assertTrue(all(cells(line)<=width for _,line in out))

    def test_tree_exposes_64_children_without_fake_activity(self):
        text=tree_text(self.snap)
        for i in CHILD_IDS:self.assertIn(i+' ',text)
        self.assertIn('task=unassigned',text)
        self.assertIn('tokens=0',text)
        self.assertIn('8 leads + 64 workers', '\n'.join(t for _,t in dashboard_lines(self.snap,120,21)))

    def test_blocked_parent_is_red_even_if_its_latest_review_passed(self):
        from fullagent.computer.view import RED
        self.snap['agents']['a3']['status']='done'
        self.snap['tasks']['a3']={'status':'blocked'}
        style,line=next((style,line) for style,line in dashboard_lines(self.snap,120,21) if line.startswith(' a3 '))
        self.assertEqual(style,RED)
        self.assertIn('blocked',line)
        self.assertIn('task=blocked',tree_text(self.snap))

    def test_family_labels_session_and_task_and_does_not_greenlight_pending_work(self):
        from fullagent.computer.view import DIM
        self.snap['agents']['a3.2']['status']='done'
        self.snap['hierarchy']['tasks']['a3.2']={'status':'pending'}
        lines=family_lines(self.snap,'a3',120,19)
        text='\n'.join(t for _,t in lines)
        self.assertIn('SESSION',text);self.assertIn('TASK',text)
        style,line=next((s,t) for s,t in lines if t.startswith(' a3.2'))
        self.assertEqual(style,DIM)

    def test_inspection_has_model_and_actual_state_not_hidden_reasoning(self):
        text=inspect_text(self.snap,'a1.3')
        self.assertIn('"model"',text);self.assertIn('"assignment": null',text)
        with self.assertRaises(ValueError):inspect_text(self.snap,'a1.3.8')

    def test_bridge_switches_family_view_and_preserves_inherited_models(self):
        ui=FakeUI();bridge=ComputerBridge(ui);bridge.on(str(self.root))
        ui.cfg.extra['computer_models']={'a1':'lead','a1.3':'child'}
        bridge._freeze_models();self.assertEqual(bridge._frozen_models['a1.1'],'lead')
        self.assertEqual(bridge._frozen_models['a1.3'],'child')
        bridge.command('tree a1');self.assertEqual(bridge.focus_lead,'a1')
        self.assertIn('FAMILY a1',''.join(t for _,t in bridge.dashboard()))
        bridge.command('tree overview');self.assertIsNone(bridge.focus_lead)
        with patch.object(ui.cfg,'save'):
            bridge.command('swarm off')
        self.assertFalse(bridge.engine.settings.hierarchy_enabled)
