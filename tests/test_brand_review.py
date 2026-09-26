import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server
import visual_review as vr
import provider_probe
from reliability import StageError

PROVIDER={'name':'外部视觉提供商','base_url':'https://vision.example/v1','api_key':'private-vision-secret',
          'model':'vision-model','effort':'omit','protocol':'chat','token_field':'max_tokens'}


class BrandReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.monitor=server.Monitor(self.directory.name)
        self.monitor.save({'api_key':'private-generator-secret'})

    def tearDown(self):
        self.monitor.stopped.set();self.directory.cleanup()

    def provider(self):return self.monitor.save_review_provider(PROVIDER)

    def test_public_promotion_is_opt_in_and_contains_only_public_fields(self):
        self.assertIsNone(self.monitor.state()['promotion'])
        self.monitor.save({'promotion_enabled':True,'promotion_title':'GGUUAI API',
                           'promotion_url':'relay.example/path?q=1#start','promotion_description':'模型访问入口'})
        state=self.monitor.state()
        self.assertEqual('https://relay.example/path?q=1#start',state['promotion']['url'])
        self.assertEqual({'title','description','url','action'},set(state['promotion']))
        self.assertNotIn('private-generator-secret',json.dumps(state))
        self.monitor.save({'promotion_enabled':False})
        self.assertIsNone(self.monitor.state()['promotion'])

    def test_bad_links_and_incomplete_promotions_are_rejected_atomically(self):
        before=self.monitor.settings()
        for url in ('javascript:alert(1)','data:text/html,hello','https://user:pass@example.com',
                    'https://example.com\\evil','https://example.com\n','https://example.com:99999'):
            if url.endswith('\n'):url='https://exam\nple.com'
            with self.subTest(url=url),self.assertRaises(ValueError):self.monitor.save({'promotion_url':url})
        for update in ({'promotion_enabled':True},{'promotion_title':'x'*61},{'promotion_action':'x'*17},
                       {'promotion_enabled':'true'},{'promotion_url':[]},{'promotion_description':'line\nbreak'}):
            with self.subTest(update=update),self.assertRaises(ValueError):self.monitor.save(update)
        self.assertEqual(before,self.monitor.settings())

    def test_provider_secrets_masked_and_separate_from_generation(self):
        generation=self.monitor.settings()
        provider=self.provider()
        serialized=json.dumps(provider)+json.dumps(self.monitor.settings(public=True))+json.dumps(self.monitor.state())
        self.assertNotIn(PROVIDER['api_key'],serialized)
        self.assertNotIn(PROVIDER['base_url'],serialized)
        self.assertEqual(generation['model'],self.monitor.settings()['model'])
        self.assertEqual(1,len(self.monitor.nodes()))
        self.assertEqual('self',self.monitor.settings()['review_mode'])
        self.monitor.save_review_provider(dict(PROVIDER,base_url='',api_key='',model='updated'),provider['id'])
        with self.monitor.db() as db:
            row=db.execute('SELECT * FROM review_providers').fetchone()
            self.assertEqual(PROVIDER['api_key'],row['api_key']);self.assertEqual('updated',row['model'])
        with self.assertRaises(ValueError):
            self.monitor.save_review_provider(dict(PROVIDER,base_url='https://changed.example',api_key=''),provider['id'])

    def test_provider_validation_and_selected_provider_required(self):
        for update in ({'model':''},{'api_key':''},{'effort':'auto'},{'token_field':'evil'},
                       {'protocol':'native'},{'name':''},{'extra':True}):
            with self.subTest(update=update),self.assertRaises(ValueError):self.monitor.save_review_provider(dict(PROVIDER,**update))
        for update in ({'review_mode':'external'},{'review_mode':'external','review_provider_id':999},
                       {'review_mode':'unknown'},{'review_provider_id':True}):
            with self.assertRaises(ValueError):self.monitor.save(update)
        self.assertEqual('self',self.monitor.settings()['review_mode'])

    def test_external_and_self_mode_capture_the_correct_runtime_config(self):
        p=self.provider();self.monitor.save({'review_mode':'external','review_provider_id':p['id']})
        cfg=self.monitor.settings()
        with self.monitor.db() as db:self.monitor.resolve_judge(db,cfg)
        self.assertEqual(PROVIDER['api_key'],cfg['_judge']['api_key'])
        self.assertEqual('private-generator-secret',cfg['api_key'])
        with patch.object(threading.Thread,'start'),patch.object(self.monitor,'execute'):
            run_id=self.monitor.start_run()
            runtime=self.monitor.workers[run_id][0]._args[1]
            self.assertEqual(PROVIDER['model'],runtime['_judge']['model'])
            self.monitor.save_review_provider(dict(PROVIDER,model='new-model'),p['id'])
            self.assertEqual(PROVIDER['model'],runtime['_judge']['model'])
        self.monitor.save({'review_mode':'self'})
        cfg=self.monitor.settings()
        with self.monitor.db() as db:self.monitor.resolve_judge(db,cfg)
        self.assertNotIn('_judge',cfg)

    def test_legacy_judge_migrates_without_rewriting_nodes_and_is_idempotent(self):
        node=self.monitor.save_node({k:v for k,v in dict(PROVIDER,effort='medium').items() if k!='token_field'})
        original=self.monitor.nodes()
        with self.monitor.db() as db:
            cfg=json.loads(db.execute('SELECT value FROM settings').fetchone()[0]);cfg['judge_node_id']=node['id']
            cfg.pop('review_mode',None);cfg.pop('review_provider_id',None)
            db.execute('UPDATE settings SET value=?',(json.dumps(cfg),))
        for _ in range(2):
            resumed=server.Monitor(self.directory.name)
            self.assertEqual('external',resumed.settings()['review_mode'])
            self.assertIsNone(resumed.settings()['judge_node_id'])
            self.assertEqual(1,len(resumed.review_providers()))
            self.assertEqual(original,resumed.nodes());resumed.stopped.set()

    def test_probe_uses_actual_image_orders_and_does_not_enable_provider(self):
        p=self.provider();orders=[['red','blue','green'],['yellow','purple','red']]
        self.monitor.model_call=Mock(return_value=(json.dumps({'colors':orders}),{},'model'))
        with patch.object(provider_probe.secrets.SystemRandom,'sample',side_effect=orders):
            result=self.monitor.probe_review_provider(p['id'])
        self.assertEqual('passed',result['status'])
        cfg,content=self.monitor.model_call.call_args.args
        self.assertEqual(PROVIDER['api_key'],cfg['api_key'])
        self.assertEqual(2,sum(c['type']=='image_url' for c in content))
        self.assertNotIn('red,blue,green',content[0]['text'])
        self.assertEqual('self',self.monitor.settings()['review_mode'])
        self.assertEqual('passed',self.monitor.review_providers()[0]['verification']['status'])
        self.monitor.save_review_provider(dict(PROVIDER,model='new-model'),p['id'])
        self.assertEqual({},self.monitor.review_providers()[0]['verification'])

    def test_failed_probe_and_concurrent_edits_never_claim_success(self):
        p=self.provider()
        self.monitor.model_call=Mock(return_value=('{}',{},'model'))
        self.assertEqual('failed',self.monitor.probe_review_provider(p['id'])['status'])
        self.monitor.model_call=Mock(side_effect=StageError('request','http_401','API Key 无效'))
        self.assertEqual('error',self.monitor.probe_review_provider(p['id'])['status'])
        def change(*args):
            self.monitor.save_review_provider(dict(PROVIDER,model='changed'),p['id']);return '{}',{},'model'
        self.monitor.model_call=change
        with self.assertRaisesRegex(ValueError,'配置已变更'):self.monitor.probe_review_provider(p['id'])
        self.assertEqual({},self.monitor.review_providers()[0]['verification'])
        self.monitor.provider_probe_slot.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'正在验证'):self.monitor.probe_review_provider(p['id'])
        finally:self.monitor.provider_probe_slot.release()

    def test_external_failure_never_calls_generator_as_fallback(self):
        p=self.provider();self.monitor.save({'review_mode':'external','review_provider_id':p['id']})
        cfg=self.monitor.settings()
        with self.monitor.db() as db:self.monitor.resolve_judge(db,cfg)
        bundle={'frames':[{'time':i/4,'png':'eA=='} for i in range(12)],'metadata':{'unique_frames':12,'sampling_limited':False}}
        call=Mock(side_effect=StageError('request','http_401','unauthorized'))
        with self.assertRaises(StageError) as caught:vr.review_bundle(cfg,bundle,call)
        self.assertEqual(1,call.call_count)
        self.assertEqual(PROVIDER['api_key'],call.call_args.args[0]['api_key'])
        self.assertEqual(vr.REVIEW_PROMPT,call.call_args.args[0]['_system_prompt'])
        self.assertEqual('independent',caught.exception.review_details['judge_mode'])


@unittest.skipUnless(os.environ.get('RENDER_TESTS')=='1','requires Chromium')
class BrandAdminBrowserTests(unittest.TestCase):
    def test_configure_promotion_and_review_provider_through_admin(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as directory:
            monitor=server.Monitor(directory,model_call=lambda *args:('{}',{},'fake'))
            monitor.setup_password('fixture-admin-password')
            monitor.save({'api_key':'fixture-generator-key'})
            http=server.BoundedHTTPServer(('127.0.0.1',0),server.Handler);http.monitor=monitor
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{http.server_port}'
            try:
                for route in ('/api/admin/review-providers','/api/admin/review-providers/1/probe'):
                    req=Request(base+route,data=b'{}' if route.endswith('probe') else None,headers={'Content-Type':'application/json'})
                    with self.assertRaises(HTTPError) as caught:urlopen(req)
                    self.assertEqual(401,caught.exception.code);caught.exception.close()
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(args=['--disable-dev-shm-usage'])
                    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(base);page.wait_for_function("() => state !== undefined && state !== null")
                    self.assertIn('GGUUAI',page.locator('.brand').inner_text())
                    self.assertFalse(page.locator('#promotion').is_visible())
                    page.goto(base+'/admin');page.fill('#admin-token','fixture-admin-password');page.click('#auth-submit')
                    page.wait_for_selector('#admin-content',state='visible')
                    page.check('#promotion-enabled');page.fill('#promotion-title-input','GGUUAI API')
                    page.fill('#promotion-description-input','模型访问入口');page.fill('#promotion-url-input','https://relay.example/start')
                    page.click('#add-provider')
                    page.fill('#provider-name','独立视觉审核');page.fill('#provider-model','vision-model')
                    page.fill('#provider-url',PROVIDER['base_url']);page.fill('#provider-key',PROVIDER['api_key'])
                    page.click('#provider-save');page.wait_for_selector('.provider-row')
                    self.assertFalse(page.locator('#provider-editor').is_visible())
                    page.select_option('#review-mode','external')
                    page.click('#admin-save');page.wait_for_function("() => config.review_mode === 'external' && config.promotion_enabled")
                    self.assertEqual('模型访问入口',page.input_value('#promotion-description-input'))
                    page.click('[data-provider-probe]');page.wait_for_function("() => document.querySelector('#provider-feedback').textContent.includes('未识别正确')")
                    page.click('[data-provider-edit]');self.assertEqual('',page.input_value('#provider-key'))
                    page.click('#provider-cancel')
                    public=browser.new_page();public.goto(base);public.wait_for_selector('#promotion',state='visible')
                    self.assertEqual('https://relay.example/start',public.locator('#promotion').get_attribute('href'))
                    self.assertEqual('GGUUAI API',public.locator('#promotion-title').inner_text())
                    for width in (1440,768,390,320):
                        for tab in (public,page):
                            tab.set_viewport_size({'width':width,'height':900})
                            self.assertTrue(tab.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
                    public.evaluate("state.promotion={title:'bad',action:'bad',url:'javascript:alert(1)'};renderPromotion()")
                    self.assertFalse(public.locator('#promotion').is_visible())
                    public.evaluate("state.promotion={title:'<img src=x onerror=alert(1)>',action:'查看',url:'https://relay.example'};renderPromotion()")
                    self.assertEqual(0,public.locator('#promotion-title img').count())
                    page.select_option('#review-mode','self');page.uncheck('#promotion-enabled');page.click('#admin-save')
                    page.wait_for_function("() => config.review_mode === 'self' && !config.promotion_enabled")
                    public.reload();public.wait_for_function("() => state !== undefined && state !== null")
                    self.assertFalse(public.locator('#promotion').is_visible())
                    self.assertFalse(errors,errors);browser.close()
            finally:http.shutdown();http.server_close();thread.join();monitor.stopped.set()


if __name__=='__main__':unittest.main()
