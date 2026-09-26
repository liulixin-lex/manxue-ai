"""Independent test surfaces, prompt motion contract, and real-browser navigation."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server


class ResultFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.monitor=server.Monitor(self.temp.name)
        self.node=self.monitor.settings()['active_node_id']
        self.ids=[self.seed('invalid','passed'),self.seed('passed','error'),self.seed('error','passed'),self.seed(None,'passed')]

    def tearDown(self):
        self.monitor.stopped.set()
        self.temp.cleanup()

    def seed(self,candy,pelican,version=3):
        now=time.time()-20
        tests={}
        if candy:
            tests['candy']={'status':candy,'output':'最终答案：21' if candy=='passed' else '最终答案：25','scoring_version':3,'started':now,'finished':now+2}
            if candy=='error':tests['candy']['error']='糖果请求失败'
        if pelican:
            tests['pelican']={'status':pelican,'started':now,'finished':now+8,'review':{'status':pelican,'version':4,'checks':dict.fromkeys(['pelican','bicycle','riding'],True),'reason':'测试主体通过' if pelican=='passed' else '视觉审核超时'}}
            if pelican=='error':tests['pelican']['error']='视觉审核超时'
        overall='error' if 'error' in (candy,pelican) else 'invalid' if 'invalid' in (candy,pelican) else 'passed'
        with self.monitor.db() as db:
            return db.execute("""INSERT INTO runs (started,finished,status,source,model,base_url,effort,protocol,scene,nonce,prompt,svg,checks,test_version,tests,node_id,node_name)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(now,now+8,overall,'manual','test-model','https://example.com/v1','medium','responses','测试场景','TEST','测试提示词','<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>',json.dumps(dict(svg=True,animation=True,nonce=True)),version,json.dumps(tests),self.node,'测试节点')).lastrowid


class IndependentResultTests(ResultFixture):
    def test_pelican_filters_ignore_candy_verdict(self):
        passed=self.monitor.gallery(1,'passed','pelican')
        self.assertEqual({self.ids[0],self.ids[2],self.ids[3]},{r['id'] for r in passed['items']})
        self.assertEqual([self.ids[1]],[r['id'] for r in self.monitor.gallery(1,'error','pelican')['items']])
        self.assertEqual([self.ids[1]],[r['id'] for r in self.monitor.gallery(1,'passed','candy')['items']])
        self.assertEqual(3,self.monitor.gallery(1,'all','candy')['total'])
        # The compatibility API retains the whole-run verdict.
        self.assertEqual(2,self.monitor.gallery(1,'error')['total'])

    def test_stats_keep_pelican_only_records(self):
        state=self.monitor.state()
        self.assertEqual(3,state['stats']['total'])
        self.assertEqual(1,state['stats']['passed'])
        self.assertEqual(4,state['visual_stats']['total'])
        self.assertEqual(3,state['visual_stats']['passed'])

    def test_filtered_pagination_and_legacy(self):
        for _ in range(13):self.seed('error','passed')
        second=self.monitor.gallery(2,'passed','pelican')
        self.assertEqual((16,2,2,4),(second['total'],second['pages'],second['page'],len(second['items'])))
        legacy=self.seed(None,None,1)
        self.assertEqual([legacy],[r['id'] for r in self.monitor.gallery(1,'legacy','pelican')['items']])
        self.assertEqual(0,self.monitor.gallery(1,'legacy','candy')['total'])
        for test in ('invalid-test',"pelican') OR 1=1 --"):
            with self.assertRaises(ValueError):self.monitor.gallery(1,'all',test)


class PromptVariationTests(unittest.TestCase):
    def test_every_scene_has_specific_background_motion(self):
        self.assertEqual(16,len(server.SCENES))
        prompts=[server.build_pelican_prompt(scene,'TESTCODE') for scene in server.SCENES]
        for scene,prompt in zip(server.SCENES,prompts):
            self.assertIn(scene,prompt)
            self.assertIn(server.SCENE_MOTION[scene],prompt)
            self.assertIn('本次校验码：TESTCODE',prompt)
            self.assertIn('近景位移明显快于远景',prompt)
            self.assertIn('4 秒总循环',prompt)
            self.assertIn('不要 HTML、JavaScript',prompt)
            self.assertLess(len(prompt),1400)
        self.assertEqual(len(prompts),len(set(prompts)))

    def test_variants_do_not_change_core_subject_or_rubric(self):
        generated=set()
        for style in server.PELICAN_STYLES:
            for action in server.PELICAN_ACTIONS:
                with patch.object(server.secrets,'choice',side_effect=[style,action]):
                    prompt=server.build_pelican_prompt(server.SCENES[0],'NONCE')
                self.assertIn('鹈鹕',prompt);self.assertIn('自行车',prompt)
                generated.add(prompt)
        self.assertEqual(24,len(generated))
        self.assertEqual(4,server.REVIEW_VERSION)

    def test_exact_prompt_saved_and_recent_scenes_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor=server.Monitor(directory);monitor.save({'api_key':'fixture'})
            try:
                with patch.object(monitor,'execute',return_value=None):
                    ids=[]
                    for _ in range(4):
                        run_id=monitor.start_run();monitor.workers[run_id][0].join(2);ids.append(run_id)
                        with monitor.db() as db:db.execute("UPDATE runs SET status='passed' WHERE id=?",(run_id,))
                with monitor.db() as db:rows=db.execute('SELECT scene,prompt,nonce FROM runs ORDER BY id').fetchall()
                self.assertEqual(4,len({r['scene'] for r in rows}))
                for row in rows:
                    self.assertIn(row['scene'],row['prompt']);self.assertIn(row['nonce'],row['prompt'])
                    self.assertIn('本轮环境运动：',row['prompt'])
            finally:monitor.stopped.set()


@unittest.skipUnless(os.environ.get('RENDER_TESTS')=='1','requires Chromium')
class ResultBrowserTests(ResultFixture):
    def test_tabs_squares_filters_details_and_motion(self):
        from playwright.sync_api import sync_playwright
        http=server.BoundedHTTPServer(('127.0.0.1',0),server.Handler);http.monitor=self.monitor
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            root=f'http://127.0.0.1:{http.server_port}'
            with self.assertRaises(HTTPError) as caught:urlopen(root+'/api/runs?page=1&test=unknown')
            self.assertEqual(400,caught.exception.code);caught.exception.close()
            with sync_playwright() as p:
                browser=p.chromium.launch(args=['--disable-dev-shm-usage'])
                page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(root);page.wait_for_selector('#candy-grid button')
                self.assertEqual(3,page.locator('#candy-grid button').count())
                self.assertFalse(page.locator('#view-pelican').is_visible())
                self.assertFalse(page.locator('#view-guest').is_visible())
                colors={'passed':'rgb(35, 132, 95)','invalid':'rgb(237, 189, 64)','error':'rgb(206, 59, 64)'}
                for status,color in colors.items():
                    box=page.locator('#candy-grid .'+status)
                    measured=box.evaluate('(el)=>({w:el.offsetWidth,h:el.offsetHeight,color:getComputedStyle(el).backgroundColor})')
                    self.assertEqual(measured['w'],measured['h']);self.assertEqual(color,measured['color'])
                page.locator(f'#candy-grid [data-run="{self.ids[1]}"]').click()
                page.wait_for_selector('.candy-detail')
                self.assertIn('成功',page.locator('#detail-body').inner_text())
                self.assertNotIn('视觉审核超时',page.locator('#detail-body').inner_text())
                self.assertEqual(0,page.locator('#detail-body img').count())
                page.keyboard.press('Escape');page.locator('#tab-candy').focus();page.keyboard.press('ArrowRight')
                self.assertEqual('pelican',page.evaluate('document.activeElement.dataset.view'))
                self.assertTrue(page.locator('#view-pelican').is_visible())
                page.select_option('#filter','passed');page.wait_for_function('()=>document.querySelectorAll("#gallery .run-card").length===3')
                self.assertNotIn('糖果',page.locator('#gallery').inner_text())
                page.locator(f'#gallery [data-run="{self.ids[0]}"]').click();page.wait_for_selector('.review-detail')
                self.assertNotIn('糖果',page.locator('#detail-body').inner_text())
                self.assertIn('鹈鹕通过',page.locator('#detail-body').inner_text());page.keyboard.press('Escape')
                page.click('#tab-guest');self.assertTrue(page.locator('#guest-form').is_visible())
                self.assertFalse(page.locator('#gallery').is_visible())
                page.emulate_media(reduced_motion='no-preference')
                page.evaluate("selectView('candy');selectView('pelican');selectView('candy')")
                page.wait_for_timeout(250)
                self.assertEqual('1',page.locator('#view-candy').evaluate('el=>getComputedStyle(el).opacity'))
                self.assertEqual(0,page.evaluate('motionTargets.size'))
                page.emulate_media(reduced_motion='reduce')
                for width in (1440,1024,768,390,320):
                    page.set_viewport_size({'width':width,'height':900})
                    for view in ('candy','pelican','guest'):
                        page.evaluate('(v)=>selectView(v)',view)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'overflow {view} {width}')
                # Polling keeps data during an error and restores the UI after retry.
                page.route('**/api/state',lambda route:route.abort())
                page.evaluate('refresh()');self.assertTrue(page.locator('#sync-error').is_visible())
                self.assertEqual(3,page.locator('#candy-grid button').count())
                page.unroute('**/api/state');page.evaluate('refresh()')
                self.assertFalse(page.locator('#sync-error').is_visible())
                # Missing optional motion library must not prevent rendering/navigation.
                page.route('**/vendor/gsap.min.js',lambda route:route.abort())
                page.reload();page.wait_for_selector('#tab-guest');page.click('#tab-pelican')
                self.assertTrue(page.locator('#view-pelican').is_visible())
                self.assertFalse(errors,errors)
                browser.close()
        finally:http.shutdown();http.server_close();thread.join()
