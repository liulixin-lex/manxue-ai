import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
import reliability
from reliability import StageError, request_with_retries


class GuestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.monitor = server.Monitor(self.directory.name)
        self.values = dict(base_url='https://example.com', api_key='fixture-key', model='any-model')

    def tearDown(self):
        self.monitor.stopped.set()
        self.directory.cleanup()

    def test_effort_and_protocol_matrix_includes_nonreasoning_models(self):
        for effort, seconds, tokens in (('omit',1200,32768), ('none',1200,32768), ('minimal',1200,32768), ('low',1200,32768), ('medium',1200,32768), ('high',1800,49152), ('xhigh',2400,65536)):
            for protocol in ('responses','chat'):
                with self.subTest(effort=effort, protocol=protocol):
                    config, _, record = self.monitor.prepare_guest(dict(self.values, effort=effort, protocol=protocol))
                    self.assertEqual((seconds,tokens), (config['timeout_seconds'],config['max_output_tokens']))
                    self.assertEqual(seconds,record['timeout_seconds'])
                    self.assertEqual(effort,config['effort'])
                    self.assertEqual(120,config['_minimum_retry_seconds'])
                    self.assertNotIn('fixture-key',json.dumps(record))

    def test_guest_defaults_follow_admin_configuration_and_preserve_node(self):
        self.monitor.save({'guest_timeout_seconds':1800,'guest_max_output_tokens':100000,'timeout_seconds':10,'api_key':'site-secret'})
        config,_,_ = self.monitor.prepare_guest(dict(self.values,effort='xhigh'))
        self.assertEqual((3600,128000), (config['timeout_seconds'],config['max_output_tokens']))
        self.assertEqual('fixture-key',config['api_key'])
        self.assertEqual(10,self.monitor.settings()['timeout_seconds'])
        state = self.monitor.state()['settings']
        self.assertEqual(1800,state['guest_timeout_seconds'])
        self.assertNotIn('site-secret',json.dumps(state))
        self.monitor = server.Monitor(self.directory.name)
        self.assertEqual(1800,self.monitor.settings()['guest_timeout_seconds'])

    def test_manual_model_limits_are_honored_and_validated(self):
        config,_,_ = self.monitor.prepare_guest(dict(self.values, effort='xhigh', timeout_seconds=3600,max_output_tokens=4096,token_field='max_tokens'))
        self.assertEqual((3600,4096,'max_tokens'),(config['timeout_seconds'],config['max_output_tokens'],config['token_field']))
        for values in ({'timeout_seconds':3601}, {'timeout_seconds':True}, {'timeout_seconds':59}, {'max_output_tokens':128001}, {'max_output_tokens':1023}, {'token_field':'wrong'}, {'effort':'arbitrary'}, {'guest_timeout_seconds':3600}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.monitor.prepare_guest(dict(self.values,**values))

    def test_both_tests_receive_more_than_old_five_minute_budget(self):
        def slow_model(config, prompt):
            if config['timeout_seconds'] < 310:
                raise StageError('request','timeout','would exceed old 300 second budget')
            if 'SVG' not in prompt:
                return '最终答案：21', {}, 'fixture'
            nonce=re.search(r'校验码：(\w+)',prompt)[1]
            return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text x="1" y="90">{nonce}</text><circle r="5"><animate attributeName="cx" values="10;90;10" dur="2s" repeatCount="indefinite"/></circle></svg>', {}, 'fixture'
        self.monitor.model_call=slow_model
        result=self.monitor.guest_test(dict(self.values,effort='xhigh'))
        self.assertEqual('passed',result['status'])
        self.assertEqual(2400,result['timeout_seconds'])
        self.assertEqual({'passed'}, {t['status'] for t in result['tests'].values()})

    def test_short_remaining_budget_skips_billed_retry(self):
        config,_,_=self.monitor.prepare_guest(self.values)
        clock=[0.0]; attempts=[]; calls=[]
        def model(config,prompt):
            calls.append(config['timeout_seconds'])
            clock[0]+=1150
            raise StageError('request','http_524','HTTP 524：gateway',True)
        with patch.object(reliability.time,'monotonic',side_effect=lambda:clock[0]), self.assertRaises(StageError):
            request_with_retries(config,'fixture',model,'generation',attempts)
        self.assertEqual(1,len(calls))
        self.assertEqual('insufficient_stage_budget',attempts[0]['retry_skipped'])

    def test_early_temporary_error_retries_but_404_does_not(self):
        config,_,_=self.monitor.prepare_guest(dict(self.values,retry_count=1))
        model=Mock(side_effect=[StageError('request','http_524','HTTP 524：gateway',True),('21',{},'fixture')])
        attempts=[]
        with patch.object(reliability.time,'sleep'):
            self.assertEqual('21',request_with_retries(config,'fixture',model,'generation',attempts)[0])
        self.assertEqual(2,len(attempts))
        model=Mock(side_effect=StageError('request','http_404','HTTP 404：route',False))
        with self.assertRaises(StageError):
            request_with_retries(config,'fixture',model,'generation',[])
        self.assertEqual(1,model.call_count)

    def test_worker_preserves_protocol_effort_and_output_field(self):
        for protocol in ('responses','chat'):
            for effort in ('omit','none','minimal','high','xhigh'):
                config,_,_=self.monitor.prepare_guest(dict(self.values,protocol=protocol,effort=effort,token_field='max_tokens'))
                captured=[]
                def post(url,body,headers,*args):
                    captured.append((url,json.loads(body)))
                    if protocol=='responses':
                        return json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'21'}]}]}).encode()
                    return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'21'}}]}).encode()
                with self.subTest(protocol=protocol,effort=effort),patch.object(server,'public_post',side_effect=post):
                    self.assertEqual('21',server._call_model_direct(config,'fixture')[0])
                url,body=captured[0]
                self.assertTrue(url.endswith('/v1/responses' if protocol=='responses' else '/v1/chat/completions'))
                field='reasoning' if protocol=='responses' else 'reasoning_effort'
                self.assertEqual(effort!='omit',field in body)
                if protocol=='chat':
                    self.assertEqual(config['max_output_tokens'],body['max_tokens'])
                    self.assertNotIn('max_completion_tokens',body)

    def test_fixed_diagnoses_distinguish_budget_and_output_limits(self):
        for code,word in (('deadline','总时间预算'), ('generation_timeout','生成阶段'), ('queue_timeout','并发'), ('output_incomplete','Token')):
            self.assertIn(word,server.guest_error('untrusted-private-detail',code))
            self.assertNotIn('untrusted-private-detail',server.guest_error('untrusted-private-detail',code))


class GuestIdleBudgetTests(unittest.TestCase):
    def test_ninety_five_second_silent_reasoning_is_allowed_for_guest(self):
        clock=[0.0]
        sock=Mock()
        class Response:
            sent=False
            def isclosed(self):return False
            def read1(self,count):
                if self.sent:return b''
                if sock.settimeout.call_args.args[0] < 95:raise TimeoutError()
                clock[0]+=95;self.sent=True
                return b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        with patch.object(server.time,'monotonic',side_effect=lambda:clock[0]):
            result=server.read_response(Response(),sock,1200,10000,True,idle_timeout=None)
        self.assertEqual('completed',result['status'])
        self.assertEqual(95,clock[0])
        clock[0]=0
        with patch.object(server.time,'monotonic',side_effect=lambda:clock[0]),self.assertRaises(TimeoutError):
            server.read_response(Response(),sock,1200,10000,True)

    def test_public_post_uses_task_budget_and_preserves_tls_and_address_checks(self):
        config=dict(server.DEFAULTS,_guest=True,timeout_seconds=1200)
        response=Mock(status=200)
        context=Mock();sock=Mock();context.wrap_socket.return_value=sock
        with patch.object(server,'public_addresses',return_value=(server.parse.urlsplit('https://api.example.com/v1/responses'),['8.8.8.8'])) as addresses,patch.object(server.socket,'create_connection') as connect,patch.object(server.ssl,'create_default_context',return_value=context),patch.object(server.http.client,'HTTPSConnection') as connection,patch.object(server,'read_response',return_value=b'{}') as read:
            connection.return_value.getresponse.return_value=response
            server.public_post('https://api.example.com/v1/responses',b'{}',{},1200)
            self.assertIsNone(read.call_args.kwargs['idle_timeout'])
            addresses.assert_called_once()
            self.assertEqual('api.example.com',context.wrap_socket.call_args.kwargs['server_hostname'])
            self.assertEqual(('8.8.8.8',443),connect.call_args.args[0])


@unittest.skipUnless(os.environ.get('RENDER_TESTS')=='1','requires Chromium')
class GuestBudgetBrowserTests(unittest.TestCase):
    def test_budget_preview_manual_overrides_and_admin_save(self):
        from playwright.sync_api import sync_playwright
        import threading
        with tempfile.TemporaryDirectory() as directory:
            monitor=server.Monitor(directory,model_call=lambda *args:('最终答案：21',{},'fixture'))
            monitor.setup_password('fixture-password-1234')
            http=server.BoundedHTTPServer(('127.0.0.1',0),server.Handler);http.monitor=monitor
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                with sync_playwright() as p:
                    browser=p.chromium.launch(args=['--disable-dev-shm-usage'])
                    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
                    page.on('pageerror',lambda exc:errors.append(str(exc)))
                    page.goto(f'http://127.0.0.1:{http.server_port}/#guest')
                    page.wait_for_function("() => document.querySelector('#guest-budget-note').textContent.includes('20 分钟')")
                    page.select_option('#effort','xhigh')
                    self.assertIn('40 分钟',page.locator('#guest-budget-note').inner_text())
                    self.assertIn('65,536',page.locator('#guest-budget-note').inner_text())
                    page.select_option('#guest-timeout','3600');page.select_option('#guest-max-tokens','4096')
                    self.assertIn('60 分钟',page.locator('#guest-budget-note').inner_text())
                    page.select_option('#protocol','chat');self.assertTrue(page.locator('#guest-token-field').is_visible())
                    page.select_option('#effort','omit');page.select_option('#guest-token-field','max_tokens')
                    screenshots=os.environ.get('GUEST_BUDGET_SCREENSHOTS')
                    for width,name in ((1440,'guest-desktop.png'),(390,'guest-mobile.png')):
                        page.set_viewport_size({'width':width,'height':1000});page.evaluate('() => scrollTo(0,0)')
                        self.assertTrue(page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'))
                        if screenshots:page.screenshot(path=screenshots+'/'+name,full_page=True)
                    page.select_option('#test-type','candy')
                    page.locator('#base-url').fill('https://example.com');page.locator('#api-key').fill('fixture-secret')
                    page.locator('#run').click()
                    page.wait_for_function("() => document.querySelector('#form-message').textContent.includes('60 分钟')")
                    record=monitor.admin_guest_history()['items'][0]
                    self.assertEqual((3600,4096,'omit','max_tokens'),(record['timeout_seconds'],record['max_output_tokens'],record['effort'],record['token_field']))
                    page.goto(f'http://127.0.0.1:{http.server_port}/admin')
                    page.locator('#admin-token').fill('fixture-password-1234');page.locator('#auth-submit').click()
                    page.wait_for_selector('#admin-content:visible')
                    page.locator('#guest-default-timeout').fill('1800');page.locator('#guest-default-tokens').fill('40000')
                    page.locator('#admin-save').click()
                    page.wait_for_function("() => document.querySelector('#admin-message').textContent.includes('配置已保存')")
                    self.assertEqual(1800,monitor.settings()['guest_timeout_seconds'])
                    for width,name in ((1440,'admin-desktop.png'),(390,'admin-mobile.png')):
                        page.set_viewport_size({'width':width,'height':1000});page.evaluate('() => scrollTo(0,0)')
                        self.assertTrue(page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'))
                        if screenshots:page.screenshot(path=screenshots+'/'+name,full_page=True)
                    page.goto(f'http://127.0.0.1:{http.server_port}/#guest')
                    page.wait_for_function("() => document.querySelector('#guest-budget-note').textContent.includes('30 分钟')")
                    page.select_option('#effort','xhigh')
                    self.assertIn('60 分钟',page.locator('#guest-budget-note').inner_text())
                    self.assertFalse(errors,errors)
                    browser.close()
            finally:
                monitor.stopped.set();http.shutdown();http.server_close();thread.join()
