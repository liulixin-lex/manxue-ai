"""Recognizable cartoon subjects, bounded confirmation, and auditable migration."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server
import visual_review as vr
from reliability import StageError


def verdict(**changes):
    checks=dict.fromkeys(vr.REVIEW_CHECKS,True);checks.update(changes)
    return {'checks':checks,'evidence':{k:[] if v is None else [0,4,8] for k,v in checks.items()}}


class SubjectPolicyTests(unittest.TestCase):
    def setUp(self):
        self.bundle={'frames':[{'time':i/4,'png':'eA=='} for i in range(12)],
                     'metadata':{'unique_frames':12,'sampling_limited':False}}

    def review(self,*values,config=None,bundle=None):
        call=Mock(side_effect=[(json.dumps(v),{'total_tokens':10},'judge') if isinstance(v,dict) else v for v in values])
        result=vr.review_bundle(config or server.DEFAULTS,bundle or self.bundle,call)
        return result,call

    def test_every_advisory_can_fail_or_be_unknown_without_veto(self):
        for key in vr.ADVISORY_CHECKS:
            for value in (False,None):
                with self.subTest(key=key,value=value):
                    result,call=self.review(verdict(**{key:value}))
                    self.assertEqual('passed',result['status'])
                    self.assertEqual(1,call.call_count)
                    self.assertTrue(all(result['checks'][k] for k in vr.CORE_CHECKS))
                    self.assertIn(key,[a['check'] for a in result['advisories']])

    def test_unverified_loop_is_unknown_even_if_judge_claims_failure(self):
        result,_=self.review(verdict(loop=False))
        self.assertEqual('passed',result['status'])
        self.assertIsNone(result['checks']['loop'])
        self.assertFalse(result['loop_boundary_verified'])
        self.assertFalse(result['assessments'][0]['checks']['loop'])

    def test_static_or_limited_frames_still_allow_subject_recognition(self):
        for update in ({'unique_frames':1},{'sampling_limited':True}):
            bundle=copy.deepcopy(self.bundle);bundle['metadata'].update(update)
            result,call=self.review(verdict(),bundle=bundle)
            self.assertEqual('passed',result['status'])
            self.assertIsNone(result['checks']['motion'])
            self.assertEqual(1,call.call_count)

    def test_core_rejection_needs_two_distinct_frame_indices(self):
        for frames in ([0],[0,0],[]):
            data=verdict(pelican=False);data['evidence']['pelican']=frames
            result=vr.parse_review(json.dumps(data))
            self.assertEqual('uncertain',result['status'])
            self.assertIsNone(result['checks']['pelican'])
        self.assertEqual('uncertain',vr.parse_review(json.dumps(verdict(pelican=False)))['status'])

    def test_agreed_negative_is_invalid_with_shared_frames_and_budget(self):
        result,call=self.review(verdict(pelican=False),verdict(pelican=False))
        self.assertEqual('invalid',result['status'])
        self.assertEqual(['pelican'],result['confirmed_failures'])
        self.assertEqual('confirmed',result['confirmation']['status'])
        self.assertEqual(20,result['usage']['total_tokens'])
        self.assertEqual(['initial','confirm_core'],[a['purpose'] for a in result['attempts']])
        a,b=call.call_args_list
        self.assertEqual(a.args[0]['_stage_deadline'],b.args[0]['_stage_deadline'])
        self.assertEqual(a.args[1][1:],b.args[1][1:])
        self.assertNotIn('pelican: false',b.args[1][0]['text'])
        self.assertEqual(2,len(result['assessments']))

    def test_disagreement_never_cherry_picks_a_pass(self):
        for second in (True,None):
            result,call=self.review(verdict(riding=False),verdict(riding=second))
            self.assertEqual('uncertain',result['status'])
            self.assertIsNone(result['checks']['riding'])
            self.assertEqual('disagreement',result['confirmation']['status'])
            self.assertEqual([],result['confirmed_failures'])
            self.assertEqual(2,call.call_count)

    def test_confirmed_absent_bicycle_cannot_have_confirmed_riding(self):
        result,_=self.review(verdict(bicycle=False),verdict(bicycle=False))
        self.assertEqual('invalid',result['status'])
        self.assertIsNone(result['checks']['riding'])

    def test_confirmation_failure_preserves_first_observation_as_uncertain(self):
        for error in (StageError('request','http_524','timeout',True),('bad',{},'judge')):
            result,call=self.review(verdict(riding=False),error)
            self.assertEqual('uncertain',result['status'])
            self.assertEqual('unavailable',result['confirmation']['status'])
            self.assertFalse(result['checks']['riding'])
            self.assertEqual(2,call.call_count)

    def test_confirmation_cannot_exceed_global_attempt_cap(self):
        result,call=self.review(verdict(riding=False),config=dict(server.DEFAULTS,retry_count=0))
        self.assertEqual('uncertain',result['status'])
        self.assertEqual('confirmation_budget',result['confirmation']['error_code'])
        self.assertEqual(1,call.call_count)

    def test_schema_repair_uses_confirmation_budget(self):
        result,call=self.review(('bad',{},'judge'),verdict(riding=False),
                                config=dict(server.DEFAULTS,retry_count=1))
        self.assertEqual('uncertain',result['status'])
        self.assertEqual(2,call.call_count)
        self.assertEqual(2,len(result['attempts']))

    def test_successful_subject_does_not_set_execution_error(self):
        from test_core import SVG
        result,_=self.review(verdict(scene=False))
        with patch.object(server,'review_pelican',return_value=result):
            test=server.perform_test(server.DEFAULTS,'','TEST',lambda *args:(SVG,{},'judge'))
        self.assertEqual('passed',test['status'])
        self.assertEqual('',test['error'])


class LoopBoundaryTests(unittest.TestCase):
    def metadata(self,end=6,**update):
        result={'frame_times':[0,1,2,end],'sampling_limited':False,
                'animations':[{'begin':0,'duration':2,'repeating':True},
                              {'begin':0,'duration':3,'repeating':True}]}
        result.update(update);return result

    def test_common_period_is_required_not_largest_period(self):
        self.assertTrue(vr.verified_loop_boundary(self.metadata()))
        self.assertFalse(vr.verified_loop_boundary(self.metadata(end=3)))

    def test_unknown_finite_delayed_limited_and_alternate_periods(self):
        for update in ({'repeating':False},{'repeating':None},{'begin':1},{'duration':None},{'alternate':True}):
            data=self.metadata();data['animations'][0].update(update)
            self.assertFalse(vr.verified_loop_boundary(data))
        self.assertFalse(vr.verified_loop_boundary(self.metadata(sampling_limited=True)))
        self.assertFalse(vr.verified_loop_boundary(self.metadata(animations=[])))


class PolicyMigrationTests(unittest.TestCase):
    def legacy(self,**changes):
        return dict(verdict(**changes),version=3,status='invalid',reason='旧标准：循环未通过',
                    render={'frames':[{'index':i} for i in range(12)]},receipts=[{'sha256':'retained'}])

    def test_advisory_reclassification_retains_original_and_is_idempotent(self):
        original=self.legacy(loop=False);before=copy.deepcopy(original)
        result=vr.reassess_legacy_review(original)
        self.assertEqual('passed',result['status'])
        self.assertEqual(before,result['previous_review'])
        self.assertEqual(before,original)
        self.assertEqual('retained_observations',result['policy_reassessment']['method'])
        self.assertIsNone(vr.reassess_legacy_review(result))

    def test_old_strict_core_failure_never_becomes_a_pass_or_confirmed_failure(self):
        result=vr.reassess_legacy_review(self.legacy(riding=False))
        self.assertEqual('uncertain',result['status'])
        self.assertEqual([],result['confirmed_failures'])
        self.assertIsNone(result['checks']['riding'])
        self.assertFalse(result['previous_review']['checks']['riding'])

    def test_execution_errors_and_missing_observations_are_untouched(self):
        for review in (None,{},dict(self.legacy(),status='error'),{'status':'passed','version':1,'checks':{}}):
            self.assertIsNone(vr.reassess_legacy_review(review))

    def test_database_migration_preserves_candy_output_timing_and_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor=server.Monitor(directory)
            try:
                monitor.save({'api_key':'secret-fixture'})
                with patch.object(threading.Thread,'start'):run_id=monitor.start_run()
                candy={'status':'invalid','error':'最终答案不是明确的 21','output':'25'}
                tests={'pelican':{'status':'invalid','review':self.legacy(loop=False),'error':'旧标准','svg':'original SVG'},'candy':candy}
                with monitor.db() as db:
                    db.execute('UPDATE runs SET status=?,tests=?,finished=?,output=? WHERE id=?',
                               ('invalid',json.dumps(tests),123456.5,'original output',run_id))
                    nodes=[tuple(row) for row in db.execute('SELECT * FROM nodes')]
                    self.assertEqual(1,monitor.migrate_review_policy(db))
                    self.assertEqual(0,monitor.migrate_review_policy(db))
                    row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
                    self.assertEqual(nodes,[tuple(row) for row in db.execute('SELECT * FROM nodes')])
                revised=json.loads(row['tests'])
                self.assertEqual('invalid',row['status'])
                self.assertEqual('passed',revised['pelican']['status'])
                self.assertEqual(candy,revised['candy'])
                self.assertEqual(tests['pelican']['review'],revised['pelican']['review']['previous_review'])
                self.assertEqual('original output',row['output'])
                self.assertEqual(123456.5,row['finished'])
                self.assertEqual('original SVG',revised['pelican']['svg'])
            finally:monitor.stopped.set()


if __name__=='__main__':unittest.main()
