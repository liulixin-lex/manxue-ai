import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
import visual_review
from artifacts import save_evidence
from reliability import StageError, request_with_retries

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text x="10" y="90">TEST</text><circle r="10"><animate attributeName="cx" values="10;90;10" dur="2s" repeatCount="indefinite"/></circle></svg>'


class ValidationTests(unittest.TestCase):
    def test_nested_svg_and_fenced_output(self):
        nested = SVG.replace('<circle', '<svg><circle').replace('</circle>', '</circle></svg>')
        svg, checks, error = server.inspect_svg('```svg\n'+nested+'\n```', 'TEST')
        self.assertTrue(all(checks.values()), error)
        self.assertIn('</svg>', svg)

    def test_hidden_nonce(self):
        for hidden in ('display="none"', 'style="opacity:0"', 'visibility="hidden"'):
            with self.subTest(hidden=hidden):
                _,checks,_ = server.inspect_svg(SVG.replace('<text ',f'<text {hidden} '),'TEST')
                self.assertFalse(checks['nonce'])
        _,checks,_ = server.inspect_svg(SVG.replace('<text ', '<g display="none"><text ').replace('</text>', '</text></g>'),'TEST')
        self.assertFalse(checks['nonce'])

    def test_unsafe_svg_stays_rejected(self):
        for element in ('<script>alert(1)</script>', '<image href="https://example.com/a.png"/>',
                        '<animate attributeName="href" to="https://example.com"/>',
                        '<rect onclick="alert(1)"/>', '<style>@import "x";</style>'):
            with self.subTest(element=element):
                svg,_,_ = server.inspect_svg(SVG.replace('</svg>',element+'</svg>'),'TEST')
                self.assertFalse(svg)

    def test_depth_limit(self):
        svg = SVG.replace('<text ', '<g>'*130+'<text ').replace('</text>','</text>'+'</g>'*130)
        self.assertFalse(server.inspect_svg(svg,'TEST')[0])

    def test_candy_final_answer(self):
        for answer in ('21','最终答案：21。','推理中考虑过 25。\n最终答案：21'):
            self.assertTrue(server.candy_passes(answer),answer)
        for answer in ('不是 21，最终答案是 25。','可能是 21 或 25。','最终答案：25','21.5','-21','错误答案：21','不是最终答案：21'):
            self.assertFalse(server.candy_passes(answer),answer)

    def test_uncertain_is_not_request_error(self):
        with patch.object(server,'review_pelican',return_value={'status':'uncertain','checks':{},'reason':'证据不足'}):
            result=server.perform_test(server.DEFAULTS,'','TEST',lambda *a:(SVG,{},'fake'))
        self.assertEqual('uncertain',result['status'])
        self.assertEqual('uncertain',server.combine_tests({'pelican':result})['status'])

    def test_partial_svg_saved_before_review(self):
        partial=[]
        with patch.object(server,'review_pelican',side_effect=StageError('render','render_timeout','截图超时')):
            result=server.perform_test(server.DEFAULTS,'','TEST',lambda *a:(SVG,{},'fake'),on_progress=partial.append)
        self.assertTrue(partial[0]['svg'])
        self.assertEqual('running',partial[0]['status'])
        self.assertEqual('render_timeout',result['error_code'])

    def test_evidence_survives_failure_after_render_progress(self):
        metadata = {'run_id':7,'frames':[{'index':0,'time':0}]}
        def fail_after_progress(config,svg,model_call):
            config['_evidence_progress'](metadata)
            raise StageError('task','deadline','整轮检测超过时间预算')
        with patch.object(server,'review_pelican',side_effect=fail_after_progress):
            result=server.perform_test(server.DEFAULTS,'','TEST',lambda *a:(SVG,{},'fake'))
        self.assertEqual('error',result['status'])
        self.assertEqual(metadata,result['review']['render'])
        self.assertTrue(result['svg'])

    def test_guest_marks_basic_scope(self):
        result=server.perform_test(dict(server.DEFAULTS,_guest=True),'','TEST',lambda *a:(SVG,{},'fake'))
        self.assertEqual('basic',result['evaluation_level'])


class RetryTests(unittest.TestCase):
    def test_review_retries_transient_errors(self):
        calls=[]
        def call(*args):
            calls.append(1)
            if len(calls)==1:
                raise StageError('request','http_503','temporary',True)
            return 'ok',{},'fake'
        attempts=[]
        with patch('reliability.time.sleep'):
            result=request_with_retries(server.DEFAULTS,'',call,'review',attempts)
        self.assertEqual('ok',result[0])
        self.assertEqual(['error','passed'],[a['status'] for a in attempts])

    def test_auth_errors_not_retried_and_no_exception_body(self):
        attempts=[]
        with self.assertRaises(StageError) as caught:
            request_with_retries(server.DEFAULTS,'',lambda *a:(_ for _ in ()).throw(ValueError('HTTP 401: secret-value')),'review',attempts)
        self.assertEqual(1,len(attempts))
        self.assertNotIn('secret-value',str(caught.exception))

    def test_deadline_and_cancellation(self):
        cancel=threading.Event();cancel.set()
        for config in (dict(server.DEFAULTS,_deadline=time.monotonic()-1),dict(server.DEFAULTS,_cancel=cancel)):
            with self.assertRaises(StageError):
                request_with_retries(config,'',lambda *a:self.fail('must not call'),'generation',[])

    def test_retry_after_cannot_overrun_budget(self):
        attempts=[]
        with self.assertRaises(StageError):
            request_with_retries(dict(server.DEFAULTS,timeout_seconds=.1),'',
                lambda *a:(_ for _ in ()).throw(StageError('request','http_429','限流',True,60)),'review',attempts)
        self.assertEqual(1,len(attempts))


class ReviewTests(unittest.TestCase):
    def verdict(self):
        return {'checks':dict.fromkeys(visual_review.REVIEW_CHECKS,True),
                'evidence':{k:[0,3,8] for k in visual_review.REVIEW_CHECKS}}

    def test_valid_schema_and_evidence(self):
        self.assertEqual('passed',visual_review.parse_review(json.dumps(self.verdict()))['status'])

    def test_missing_evidence_out_of_range_and_inconsistent_verdict(self):
        for failure in ('missing','range','contradiction','type'):
            value=self.verdict()
            if failure=='missing':value['evidence']['riding']=[]
            if failure=='range':value['evidence']['motion']=[12]
            if failure=='contradiction':value['checks']['bicycle']=False
            if failure=='type':value['checks']['motion']='true'
            with self.subTest(failure=failure):
                result = visual_review.parse_review(json.dumps(value))
                self.assertEqual('uncertain' if failure in ('missing','contradiction') else 'passed',result['status'])
                self.assertTrue(result['checks']['pelican'])
                self.assertTrue(result['checks']['scene'])
                self.assertTrue(result['schema_warnings'])
                if failure == 'contradiction':
                    self.assertFalse(result['checks']['bicycle'])
                    self.assertIsNone(result['checks']['riding'])
                self.assertIsNone(result['checks']['motion'])
                with self.assertRaises(StageError):
                    visual_review.parse_review(json.dumps(value),strict=True)

    def test_unknown_remains_unknown(self):
        value=self.verdict();value['checks']['motion']=None;value['evidence']['motion']=[]
        result=visual_review.parse_review(json.dumps(value))
        self.assertEqual('passed',result['status'])
        self.assertIsNone(result['checks']['motion'])

    def test_independent_judge_config(self):
        bundle={'frames':[{'time':0,'png':'eA=='}]*12,'metadata':{'unique_frames':2,'sampling_limited':False}}
        config=dict(server.DEFAULTS,_judge=dict(server.DEFAULTS,model='independent'),_scene='海边',_nonce='TEST')
        seen=[]
        def call(config,prompt):
            seen.append(config['model']);return json.dumps(self.verdict()),{},'returned-judge'
        with patch.object(visual_review,'render_evidence',return_value=bundle):
            result=visual_review.review_pelican(config,SVG,call)
        self.assertEqual(['independent'],seen)
        self.assertEqual('independent',result['judge_mode'])
        self.assertEqual('returned-judge',result['returned_model'])

    def test_deadline_after_render_keeps_evidence(self):
        bundle={'frames':[{'time':0,'png':'eA=='}]*12,'metadata':{'unique_frames':2,'sampling_limited':False}}
        config=dict(server.DEFAULTS)
        config['_evidence_progress']=lambda _:config.update(_deadline=time.monotonic()-1)
        with patch.object(visual_review,'render_evidence',return_value=bundle),self.assertRaises(StageError) as caught:
            visual_review.review_pelican(config,SVG,lambda *a:self.fail('must not call judge'))
        self.assertEqual('deadline',caught.exception.code)
        self.assertEqual(bundle['metadata'],caught.exception.review_details['render'])


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.monitor=server.Monitor(self.directory.name,model_call=lambda *a:('最终答案：21',{},'fake'))
        self.monitor.save({'api_key':'fake-key'})

    def tearDown(self):
        self.monitor.stopped.set()
        self.directory.cleanup()

    def make_pending(self):
        with patch.object(threading.Thread,'start'):
            return self.monitor.start_run()

    def status(self,run_id):
        with self.monitor.db() as db:
            return db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()[0]

    def test_thread_start_failure_finishes_record(self):
        with patch.object(threading.Thread,'start',side_effect=RuntimeError()),self.assertRaises(ValueError):
            self.monitor.start_run()
        self.assertEqual('error',self.status(1))
        self.assertEqual(2,self.make_pending())

    def test_lost_worker_recovery(self):
        run_id=self.make_pending();self.monitor.recover_runs()
        self.assertEqual('error',self.status(run_id))

    def test_persistence_failure_recovery_is_retried(self):
        run_id=self.make_pending()
        @contextlib.contextmanager
        def broken():
            raise sqlite3.OperationalError('database is locked')
            yield
        with patch.object(self.monitor,'db',broken):
            self.monitor.fail_run(run_id,'persist','写入失败')
        self.assertIn(run_id,self.monitor.pending_failures)
        self.monitor.recover_runs()
        self.assertEqual('error',self.status(run_id))
        self.assertFalse(self.monitor.pending_failures)

    def test_late_worker_cannot_overwrite_terminal_status(self):
        run_id=self.make_pending();self.monitor.fail_run(run_id,'timeout','timeout')
        self.monitor.execute(run_id,dict(server.DEFAULTS,_guest=True),'','TEST')
        self.assertEqual('error',self.status(run_id))

    def test_expired_lease_cancels_worker(self):
        run_id=self.make_pending()
        cancel=self.monitor.workers[run_id][1]
        with self.monitor.db() as db:db.execute('UPDATE runs SET lease_until=? WHERE id=?',(time.time()-1,run_id))
        self.monitor.recover_runs()
        self.assertTrue(cancel.is_set())
        self.assertEqual('error',self.status(run_id))

    def test_stats_are_scoped_to_active_node(self):
        run_id=self.make_pending();self.monitor.fail_run(run_id,'test','test')
        node=self.monitor.save_node(dict(name='second',base_url='https://example.org/v1',api_key='fake'))
        self.monitor.activate_node(node['id'])
        self.assertEqual([],self.monitor.state()['timeline'])

    def test_judge_validation(self):
        with self.assertRaises(ValueError):self.monitor.save({'judge_node_id':999})

    def test_health_includes_scheduler_and_renderer(self):
        self.assertFalse(self.monitor.health()['ready'])
        self.monitor.render_health={'status':'passed','checked_at':time.time()}
        self.assertTrue(self.monitor.health()['ready'])
        self.monitor.scheduler_heartbeat=time.monotonic()-60
        self.assertFalse(self.monitor.health()['ready'])

    def test_oversized_svg_does_not_mark_renderer_unhealthy(self):
        run_id=self.make_pending()
        self.monitor.render_health={'status':'passed','checked_at':time.time()}
        self.monitor.model_call=lambda *args:(SVG,{},'fake')
        with patch.object(server,'review_pelican',side_effect=StageError('render','render_dimensions','SVG 尺寸超过截图预算')):
            self.monitor.execute(run_id,server.DEFAULTS,'','TEST')
        self.assertEqual('error',self.status(run_id))
        self.assertEqual('passed',self.monitor.render_health['status'])


if __name__=='__main__':unittest.main()
