"""Regressions for completed reviews lost by transport, parsing or persistence."""
import contextlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server
import visual_review as vr
from reliability import StageError, remaining, request_with_retries
from test_transport import Response


def verdict():
    return {'checks':dict.fromkeys(vr.REVIEW_CHECKS,True),'evidence':{k:[0,3,8] for k in vr.REVIEW_CHECKS}}


def done(value=None):
    return {'type':'response.output_item.done','output_index':0,'item':{'type':'message','role':'assistant',
            'status':'completed','content':[{'type':'output_text','text':json.dumps(value or verdict())}]}}


def sse(*events):
    return b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)


class RecoveryStreamTests(unittest.TestCase):
    def read(self,*events,review_frames=12):
        return server.read_response(Response(sse(*events)),Mock(),time.monotonic()+5,100000,True,review_frames)

    def test_completed_message_without_final_envelope_is_recovered(self):
        result=self.read(done())
        self.assertEqual('output_recovered',result['status'])
        self.assertFalse(result['_transport']['response_completed'])
        self.assertEqual('passed',vr.parse_review(result['output'][0]['content'][0]['text'])['status'])

    def test_completed_envelope_without_output_uses_finalized_message(self):
        result=self.read(done(),{'type':'response.completed','response':{'status':'completed','usage':{'total_tokens':42}}})
        self.assertEqual(42,result['usage']['total_tokens'])
        self.assertTrue(result['_transport']['response_completed'])
        self.assertEqual(1,len(result['output']))

    def test_recovered_advisory_observations_keep_their_meaning(self):
        for value in (False,None):
            data=verdict();data['checks']['scene']=value
            result=self.read(done(data))
            parsed=vr.parse_review(result['output'][0]['content'][0]['text'])
            self.assertEqual('passed',parsed['status'])
            self.assertIs(value,parsed['checks']['scene'])

    def test_generation_partial_refusal_and_failed_review_never_recover(self):
        cases=[([done()],0),
               ([{'type':'response.output_text.delta','delta':json.dumps(verdict())}],12),
               ([done(),{'type':'response.failed','response':{'error':{'code':'invalid_api_key'}}}],12),
               ([done(),{'type':'response.incomplete'}],12),
               ([done(),{'type':'response.output_item.added','output_index':1,'item':{}}],12)]
        refusal=done();refusal['item']['content']=[{'type':'refusal','refusal':'No'}]
        cases.append(([refusal],12))
        malformed=verdict();malformed['evidence']['motion']=[15]
        cases.append(([done(malformed)],12))
        for events,count in cases:
            with self.subTest(events=events),self.assertRaises(StageError):
                self.read(*events,review_frames=count)

    def test_reasoning_done_without_optional_status_allows_final_message(self):
        message=done();message['output_index']=1
        result=self.read({'type':'response.output_item.added','output_index':0,'item':{'type':'reasoning'}},
                         {'type':'response.output_item.done','output_index':0,'item':{'type':'reasoning','summary':[]}},message)
        self.assertEqual('output_recovered',result['status'])

    def test_upstream_cannot_forge_internal_recovery_flag(self):
        result=self.read({'type':'response.completed','response':{'status':'incomplete',
            '_transport':{'review_message_recovered':True},'output':[]}})
        self.assertNotIn('_transport',result)
        self.assertEqual('incomplete',result['status'])

    def test_idle_timeout_after_valid_message_is_recoverable(self):
        class TimeoutResponse(Response):
            def read1(self,count):
                data=super().read1(count)
                if not data:raise TimeoutError()
                return data
        sock=Mock()
        result=server.read_response(TimeoutResponse(sse(done())),sock,time.monotonic()+8,100000,True,12)
        self.assertEqual('output_recovered',result['status'])
        self.assertLessEqual(sock.settimeout.call_args[0][0],5)


class ReviewPolicyTests(unittest.TestCase):
    def setUp(self):
        self.bundle={'frames':[{'time':i/4,'png':'eA=='} for i in range(12)],
                     'metadata':{'unique_frames':12,'sampling_limited':False}}

    def test_extra_metadata_and_markdown_do_not_erase_valid_verdict(self):
        data=verdict();data['summary']='ignored untrusted output'
        result=vr.parse_review(chr(96)*3+'json\n'+json.dumps(data)+'\n'+chr(96)*3)
        self.assertEqual('passed',result['status'])
        self.assertNotIn('summary',result)

    def test_invalid_json_and_duplicate_keys_remain_errors(self):
        for text in ('not json','{}','{"checks":{},"checks":{},"evidence":{}}'):
            with self.assertRaises(StageError):vr.parse_review(text)

    def test_unknown_dependency_cannot_pass_downstream(self):
        data=verdict();data['checks']['bicycle']=None
        result=vr.parse_review(json.dumps(data))
        self.assertIsNone(result['checks']['riding']);self.assertIsNone(result['checks']['motion'])
        self.assertEqual('uncertain',result['status'])

    def test_uncertainty_names_the_pending_check(self):
        data=verdict();data['checks']['loop']=None
        result=vr.parse_review(json.dumps(data))
        self.assertEqual(['loop'],result['pending_checks'])
        self.assertEqual('passed',result['status'])
        self.assertEqual('loop',result['advisories'][0]['check'])

    def test_schema_repair_once_same_frames_shared_deadline(self):
        configs=[];prompts=[];receipts=[]
        def call(config,prompt):
            configs.append(dict(config));prompts.append(json.dumps(prompt))
            return ('bad' if len(configs)==1 else json.dumps(verdict())),{},'fake'
        result=vr.review_bundle(dict(server.DEFAULTS,_save_review_receipt=lambda *args:receipts.append(args)),self.bundle,call)
        self.assertEqual('passed',result['status']);self.assertEqual(2,len(configs))
        self.assertEqual(configs[0]['_stage_deadline'],configs[1]['_stage_deadline'])
        self.assertEqual(2,len(receipts));self.assertEqual([1,2],[a['attempt'] for a in result['attempts']])
        self.assertEqual([p['image_url'] for p in json.loads(prompts[0]) if p['type']=='input_image'],
                         [p['image_url'] for p in json.loads(prompts[1]) if p['type']=='input_image'])

    def test_advisory_failure_preserved_without_extra_requests(self):
        data=verdict();data['checks']['scene']=False
        call=Mock(return_value=(json.dumps(data),{},'fake'))
        result=vr.review_bundle(server.DEFAULTS,self.bundle,call)
        self.assertEqual('passed',result['status']);self.assertEqual(1,call.call_count)
        self.assertFalse(result['checks']['scene'])

    def test_unsupported_schema_falls_back_once_within_budget(self):
        seen=[]
        def call(config,prompt):
            seen.append(dict(config))
            if '_output_schema' in config:raise StageError('request','structured_unsupported','unsupported')
            return json.dumps(verdict()),{},'fake'
        result=vr.review_bundle(server.DEFAULTS,self.bundle,call)
        self.assertEqual('passed',result['status']);self.assertEqual(2,len(seen))
        self.assertEqual(seen[0]['_stage_deadline'],seen[1]['_stage_deadline'])
        with self.assertRaises(StageError):vr.review_bundle(dict(server.DEFAULTS,review_format='json_schema'),self.bundle,call)

    def test_receipt_disk_error_does_not_erase_valid_review(self):
        result=vr.review_bundle(dict(server.DEFAULTS,_save_review_receipt=Mock(side_effect=OSError())),self.bundle,
                                lambda *args:(json.dumps(verdict()),{},'fake'))
        self.assertEqual('passed',result['status']);self.assertFalse(result['receipts'][0]['saved'])

    def test_review_budget_is_not_mislabeled_as_overall_deadline(self):
        now=time.monotonic()
        with self.assertRaises(StageError) as caught:
            remaining(dict(server.DEFAULTS,_stage='review',_deadline=now+100,_stage_deadline=now-1))
        self.assertEqual('review',caught.exception.stage);self.assertEqual('review_timeout',caught.exception.code)

    def test_per_attempt_ceiling_and_minimum_retry_budget(self):
        seen=[];attempts=[]
        def call(config,prompt):
            seen.append(config['timeout_seconds'])
            raise StageError('request','http_524','gateway',True)
        with self.assertRaises(StageError) as caught:
            request_with_retries(dict(server.DEFAULTS,timeout_seconds=20,_attempt_timeout_seconds=10,_minimum_retry_seconds=60),'',call,'review',attempts)
        self.assertEqual('http_524',caught.exception.code);self.assertLessEqual(seen[0],10)
        self.assertEqual(1,len(seen));self.assertEqual('insufficient_stage_budget',attempts[0]['retry_skipped'])


class DurableResultTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.monitor=server.Monitor(self.directory.name)
        self.monitor.save({'api_key':'fake'})
        with patch.object(threading.Thread,'start'):self.run_id=self.monitor.start_run()
        self.result=server.combine_tests({'pelican':{'status':'passed','finished':time.time(),'checks':{'svg':True},
                            'review':{'status':'passed'},'svg':'<svg/>','output':'<svg/>'}})

    def tearDown(self):
        self.monitor.stopped.set();self.directory.cleanup()

    @contextlib.contextmanager
    def broken(self):
        raise sqlite3.OperationalError('locked')
        yield

    def test_final_verdict_survives_db_failure_and_dead_worker(self):
        with patch.object(self.monitor,'db',self.broken):self.monitor.persist_result(self.run_id,self.result)
        self.assertIn(self.run_id,self.monitor.pending_results)
        self.monitor.recover_runs()
        with self.monitor.db() as db:row=db.execute('SELECT status,tests FROM runs WHERE id=?',(self.run_id,)).fetchone()
        self.assertEqual('passed',row['status']);self.assertEqual('passed',json.loads(row['tests'])['pelican']['review']['status'])
        self.assertFalse(self.monitor.pending_results)
        self.assertFalse(list(self.monitor.result_directory.glob('*.json')))

    def test_restart_replays_final_journal_before_worker_interruption(self):
        with patch.object(self.monitor,'db',self.broken):self.monitor.persist_result(self.run_id,self.result)
        resumed=server.Monitor(self.directory.name)
        try:
            with resumed.db() as db:self.assertEqual('passed',db.execute('SELECT status FROM runs WHERE id=?',(self.run_id,)).fetchone()[0])
            self.assertFalse(list(resumed.result_directory.glob('*.json')))
        finally:resumed.stopped.set()

    def test_journal_never_overwrites_terminal_failure(self):
        self.monitor.fail_run(self.run_id,'cancelled','cancelled')
        self.monitor.persist_result(self.run_id,self.result)
        with self.monitor.db() as db:self.assertEqual('error',db.execute('SELECT status FROM runs WHERE id=?',(self.run_id,)).fetchone()[0])

class WorkerProtocolTests(unittest.TestCase):
    def setUp(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.requests.append(body)
                if self.server.mode == 'unsupported':
                    self.send_response(400);raw=b'{"error":{"message":"text.format json_schema is not supported"}}'
                elif self.server.mode == 'bad_request':
                    self.send_response(400);raw=b'{"error":{"message":"private diagnostic"}}'
                elif self.path.endswith('/responses'):
                    self.send_response(200);raw=sse(done(),{'type':'response.completed','response':{'status':'completed','model':'test'}})
                else:
                    self.send_response(200);raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(verdict())}}]}).encode()
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.http.requests=[];self.http.mode='ok'
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.config=dict(server.DEFAULTS,base_url=f'http://127.0.0.1:{self.http.server_port}',timeout_seconds=3,
                         _output_schema=vr.review_schema(12),_review_frame_count=12)

    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join()

    def test_schema_and_recovery_survive_worker_boundary(self):
        text,usage,_=server.call_model(self.config,'test')
        self.assertEqual('passed',vr.parse_review(text)['status'])
        self.assertTrue(usage['_transport']['review_message_recovered'])
        self.assertTrue(self.http.requests[-1]['text']['format']['strict'])
        self.assertEqual(vr.review_schema(12),self.http.requests[-1]['text']['format']['schema'])
        server.call_model(dict(self.config,protocol='chat'),'test')
        self.assertTrue(self.http.requests[-1]['response_format']['json_schema']['strict'])

    def test_only_explicit_unsupported_format_is_eligible_for_fallback(self):
        for mode,code in (('unsupported','structured_unsupported'),('bad_request','http_400')):
            self.http.mode=mode
            with self.subTest(mode=mode),self.assertRaises(StageError) as caught:
                server.call_model(self.config,'test')
            self.assertEqual(code,caught.exception.code)
            self.assertNotIn('private',str(caught.exception))

    def test_external_provider_options_and_trusted_rules_survive_worker(self):
        for protocol in ('chat','responses'):
            cfg=dict(self.config,protocol=protocol,effort='omit',token_field='max_tokens',_system_prompt=vr.REVIEW_PROMPT)
            server.call_model(cfg,[{'type':'text','text':'untrusted image context'}])
            body=self.http.requests[-1]
            self.assertNotIn('reasoning_effort',body)
            self.assertNotIn('reasoning',body)
            if protocol=='chat':
                self.assertEqual(vr.REVIEW_PROMPT,body['messages'][0]['content'])
                self.assertEqual('system',body['messages'][0]['role'])
                self.assertEqual('user',body['messages'][1]['role'])
                self.assertEqual(cfg['max_output_tokens'],body['max_tokens'])
                self.assertNotIn('max_completion_tokens',body)
            else:
                self.assertEqual(vr.REVIEW_PROMPT,body['instructions'])
                self.assertEqual(cfg['max_output_tokens'],body['max_output_tokens'])


if __name__=='__main__':unittest.main()
