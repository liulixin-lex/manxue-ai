"""Generation usage stays separate from candy and review calls, including history."""
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from test_core import SVG
from test_result_views import ResultFixture
import server


class TokenUsageTests(unittest.TestCase):
    def test_protocol_fields_zero_missing_and_untrusted_values(self):
        cases = [
            ({'input_tokens':1234,'output_tokens':0}, {'input_tokens':1234,'output_tokens':0}),
            ({'prompt_tokens':1234,'completion_tokens':50}, {'input_tokens':1234,'output_tokens':50}),
            ({'input_tokens':10,'prompt_tokens':20,'output_tokens':30,'completion_tokens':40}, {'input_tokens':10,'output_tokens':30}),
            ({'input_tokens':None,'prompt_tokens':15,'output_tokens':False}, {'input_tokens':15}),
            ({'input_tokens':-1,'output_tokens':'<script>bad</script>','total_tokens':10}, {}),
            ({'input_tokens':2**53,'output_tokens':1.5}, {}),
            ({'total_tokens':100}, {}), (None, {}), ([], {}),
        ]
        for usage, expected in cases:
            with self.subTest(usage=usage):self.assertEqual(expected,server.token_counts(usage))

    def test_generation_tokens_survive_review_and_progress_without_double_counting(self):
        progress=[]
        generation={'input_tokens':1200,'output_tokens':400,'total_tokens':1600}
        review={'status':'passed','usage':{'input_tokens':500,'output_tokens':70,'total_tokens':570}}
        with patch.object(server,'review_pelican',return_value=review):
            result=server.perform_test(server.DEFAULTS,'','TEST',lambda *args:(SVG,generation,'generator'),on_progress=progress.append)
        self.assertEqual('passed',result['status'])
        self.assertEqual({'input_tokens':1700,'output_tokens':470,'total_tokens':2170},result['usage'])
        for snapshot in [*progress,result]:
            self.assertEqual({'input_tokens':1200,'output_tokens':400},snapshot['generation_usage'])
        self.assertEqual(1200,generation['input_tokens'])

    def test_legacy_totals_subtract_review_before_normalizing_protocol_aliases(self):
        cases=[
            ({'usage':{'input_tokens':1500,'output_tokens':250},'review':{'usage':{'input_tokens':300,'output_tokens':50}}}, {'input_tokens':1200,'output_tokens':200}),
            ({'usage':{'prompt_tokens':1200,'completion_tokens':200,'input_tokens':300,'output_tokens':50},'review':{'usage':{'input_tokens':300,'output_tokens':50}}}, {'input_tokens':1200,'output_tokens':200}),
            ({'usage':{'input_tokens':1200,'output_tokens':200,'prompt_tokens':300,'completion_tokens':50},'review':{'usage':{'prompt_tokens':300,'completion_tokens':50}}}, {'input_tokens':1200,'output_tokens':200}),
            ({'usage':{'input_tokens':1500},'review':{'previous_review':{'usage':{'input_tokens':300}}}}, {'input_tokens':1200}),
            ({'usage':{'input_tokens':300,'output_tokens':50},'review':{'usage':{'input_tokens':300,'output_tokens':80}}}, {}),
            ({'usage':{'input_tokens':0,'output_tokens':20}}, {'input_tokens':0,'output_tokens':20}),
            ({'generation_usage':{},'usage':{'input_tokens':1500}}, {}),
            ({'generation_usage':{'input_tokens':0},'usage':{'input_tokens':1500},'review':{'usage':{'input_tokens':1500}}}, {'input_tokens':0}),
            ({'usage':None}, {}), ({}, {}),
        ]
        for value,expected in cases:
            with self.subTest(value=value):
                original=json.dumps(value,sort_keys=True)
                self.assertEqual(expected,server.generation_token_counts(value))
                self.assertEqual(original,json.dumps(value,sort_keys=True))

    def test_request_failure_does_not_invent_usage(self):
        def fail(*args):raise server.StageError('generation','http_401','鉴权失败')
        result=server.perform_test(dict(server.DEFAULTS,retry_count=0),'','TEST',fail)
        self.assertEqual('error',result['status']);self.assertEqual({},result['generation_usage'])


class TokenPersistenceTests(ResultFixture):
    def test_public_history_preserves_database_and_ignores_candy_usage(self):
        with self.monitor.db() as db:
            tests=json.loads(db.execute('SELECT tests FROM runs WHERE id=?',(self.ids[0],)).fetchone()[0])
            tests['pelican']['usage']={'input_tokens':1500,'output_tokens':250}
            tests['pelican']['review']['usage']={'input_tokens':300,'output_tokens':50}
            tests['candy']['usage']={'input_tokens':9999,'output_tokens':8888}
            db.execute('UPDATE runs SET tests=?,usage=? WHERE id=?',(json.dumps(tests),json.dumps({'input_tokens':11499,'output_tokens':9138}),self.ids[0]))
            original=db.execute('SELECT * FROM runs WHERE id=?',(self.ids[0],)).fetchone()
            before=tuple(original)
        expected={'input_tokens':1200,'output_tokens':200}
        for detailed in (False,True):
            self.assertEqual(expected,self.monitor.serialize(original,detailed)['tests']['pelican']['generation_usage'])
        for records in (self.monitor.gallery(1,'all','pelican')['items'],self.monitor.state()['timeline']):
            self.assertEqual(expected,next(r for r in records if r['id']==self.ids[0])['tests']['pelican']['generation_usage'])
        with self.monitor.db() as db:
            self.assertEqual(before,tuple(db.execute('SELECT * FROM runs WHERE id=?',(self.ids[0],)).fetchone()))

    def test_future_runs_persist_usage_across_restart(self):
        self.monitor.save({'api_key':'fixture','review_enabled':False})
        self.monitor.model_call=lambda config,prompt:('21',{'input_tokens':9000,'output_tokens':900},'candy') if prompt==server.CANDY_PROMPT else (SVG,{'prompt_tokens':1200,'completion_tokens':200},'generator')
        run_id=self.monitor.start_run()
        self.monitor.workers[run_id][0].join(5)
        self.assertFalse(self.monitor.workers[run_id][0].is_alive())
        restored=server.Monitor(self.temp.name)
        try:
            row=next(r for r in restored.gallery()['items'] if r['id']==run_id)
            self.assertEqual({'input_tokens':1200,'output_tokens':200},row['tests']['pelican']['generation_usage'])
            self.assertEqual('displayed',row['tests']['pelican']['status'])
            with restored.db() as db:
                saved=json.loads(db.execute('SELECT tests FROM runs WHERE id=?',(run_id,)).fetchone()[0])
            self.assertEqual({'input_tokens':1200,'output_tokens':200},saved['pelican']['generation_usage'])
        finally:restored.stopped.set()

    def test_guest_whitelist_publishes_numeric_usage_only(self):
        self.monitor.model_call=lambda *args:(SVG,{'prompt_tokens':450,'completion_tokens':890,'api_key':'private-usage-field'},'generator')
        result=self.monitor.guest_test({'base_url':'https://example.com/v1','api_key':'fixture-secret','model':'fixture','test_type':'pelican'})
        self.assertEqual({'input_tokens':450,'output_tokens':890},result['tests']['pelican']['generation_usage'])
        self.assertNotIn('private-usage-field',json.dumps(result))
        self.assertNotIn('fixture-secret',json.dumps(result))
        restored=self.monitor.guest_results()[0]
        self.assertEqual(result['tests']['pelican']['generation_usage'],restored['tests']['pelican']['generation_usage'])


@unittest.skipUnless(os.environ.get('RENDER_TESTS')=='1','requires Chromium')
class TokenBrowserTests(ResultFixture):
    def test_gallery_detail_guest_and_mobile_usage(self):
        from playwright.sync_api import sync_playwright
        with self.monitor.db() as db:
            for index,fields in enumerate([{'generation_usage':{'input_tokens':12345,'output_tokens':6789}}, {'usage':{'prompt_tokens':222,'completion_tokens':0}}, {'usage':{'input_tokens':5000,'output_tokens':3000}}]):
                row=db.execute('SELECT tests FROM runs WHERE id=?',(self.ids[index],)).fetchone()
                tests=json.loads(row[0]);tests['pelican'].update(fields)
                if index==2:tests['pelican']['review']['usage']={'input_tokens':4000,'output_tokens':2000}
                db.execute('UPDATE runs SET tests=? WHERE id=?',(json.dumps(tests),self.ids[index]))
        self.monitor.model_call=lambda *args:(SVG,{'prompt_tokens':450,'completion_tokens':890},'generator')
        self.monitor.guest_test({'base_url':'https://example.com/v1','api_key':'fixture-secret','model':'fixture','test_type':'pelican'})
        http=server.BoundedHTTPServer(('127.0.0.1',0),server.Handler);http.monitor=self.monitor
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(args=['--disable-dev-shm-usage'])
                page=browser.new_page(reduced_motion='reduce');errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{http.server_port}')
                page.wait_for_selector('#gallery .token-usage')
                expected=[['输入 token 12,345','输出 token 6,789'],['输入 token 222','输出 token 0'],['输入 token 1,000','输出 token 1,000'],['输入 token —','输出 token —']]
                for run_id,counts in zip(self.ids,expected):
                    self.assertEqual(counts,page.locator(f'#gallery [data-id="{run_id}"] .token-usage>span').all_text_contents())
                for width in (1440,390,320):
                    page.set_viewport_size({'width':width,'height':1000})
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    card=page.locator(f'#gallery [data-id="{self.ids[0]}"]')
                    if os.environ.get('TOKEN_USAGE_SCREENSHOTS') and width!=320:
                        card.screenshot(path=f'/qa/token-card-{width}.png')
                    card.locator('.preview').click();page.wait_for_selector('#detail-body .token-usage')
                    self.assertEqual(expected[0],page.locator('#detail-body .token-usage>span').all_text_contents())
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    self.assertNotIn('视觉审核',page.locator('#detail-body').inner_text())
                    if os.environ.get('TOKEN_USAGE_SCREENSHOTS') and width!=320:
                        page.locator('#detail-dialog').screenshot(path=f'/qa/token-detail-{width}.png')
                    page.keyboard.press('Escape')
                page.click('#tab-guest')
                self.assertEqual(['输入 token 450','输出 token 890'],page.locator('#guest-results .token-usage>span').all_text_contents())
                page.locator('.guest-preview').click();page.wait_for_selector('#detail-body .token-usage')
                self.assertEqual(['输入 token 450','输出 token 890'],page.locator('#detail-body .token-usage>span').all_text_contents())
                self.assertFalse(errors,errors)
                browser.close()
        finally:http.shutdown();http.server_close();thread.join()
