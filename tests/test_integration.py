import base64
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server
import visual_review


@unittest.skipUnless(os.environ.get('RENDER_TESTS')=='1','requires Chromium')
class EndToEndTests(unittest.TestCase):
    def test_background_suite_persists_frames_and_serves_evidence(self):
        def model(config,prompt):
            if isinstance(prompt,list):
                count=sum(part['type']=='input_image' for part in prompt)
                self.assertGreaterEqual(count,12)
                verdict={'checks':dict.fromkeys(visual_review.REVIEW_CHECKS,True),
                         'evidence':{key:[0,3,8] for key in visual_review.REVIEW_CHECKS}}
                return json.dumps(verdict),{'total_tokens':10},'fake-judge'
            if 'SVG' not in prompt:return '最终答案：21',{'total_tokens':2},'fake'
            nonce=re.search(r'校验码：(\w+)',prompt)[1]
            return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 640"><text x="800" y="620">{nonce}</text><circle cx="100" cy="100" r="30"><animate attributeName="cx" values="100;800;100" dur="2s" repeatCount="indefinite"/></circle></svg>',{'total_tokens':3},'fake'
        with tempfile.TemporaryDirectory() as directory:
            monitor=server.Monitor(directory,model_call=model)
            monitor.save({'api_key':'fake'})
            judge=monitor.save_node({'name':'judge','base_url':'https://example.org/v1','api_key':'judge-secret','model':'judge'})
            monitor.save({'judge_node_id':judge['id'],'enabled':True})
            # Scheduled path must not serialize callbacks, secrets or cancellation objects.
            run_id=monitor.start_run(source='scheduled',now=time.time()+1900)
            monitor.workers[run_id][0].join(35)
            self.assertFalse(monitor.workers[run_id][0].is_alive())
            with monitor.db() as db:
                row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
                result=monitor.serialize(row,detail=True)
                settings=db.execute('SELECT value FROM settings').fetchone()[0]
            self.assertNotIn('judge-secret',settings)
            self.assertEqual('passed',result['status'],result['error'])
            self.assertEqual(3,result['test_version'])
            self.assertEqual('independent',result['tests']['pelican']['review']['judge_mode'])
            frames=result['tests']['pelican']['review']['render']['frames']
            self.assertGreaterEqual(len(frames),12)
            http=server.BoundedHTTPServer(('127.0.0.1',0),server.Handler);http.monitor=monitor
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{http.server_port}/api/runs/{run_id}/frames/0') as response:
                    self.assertEqual('image/png',response.headers['Content-Type'])
                    self.assertTrue(response.read().startswith(b'\x89PNG'))
                with urlopen(f'http://127.0.0.1:{http.server_port}/api/state') as response:
                    state=json.load(response)
                    self.assertEqual(1,state['visual_stats']['passed'])
                from playwright.sync_api import sync_playwright
                errors=[]
                with sync_playwright() as p:
                    browser=p.chromium.launch(args=['--disable-dev-shm-usage'])
                    page=browser.new_page()
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{http.server_port}/?run={run_id}')
                    page.wait_for_selector('#detail-image')
                    page.wait_for_function('()=>document.getElementById("detail-image").naturalWidth>0')
                    self.assertEqual(0,page.locator('.evidence-grid, .review-detail, .quality-corner, .quality-flag').count())
                    self.assertNotIn('降智',page.locator('#detail-body').inner_text())
                    self.assertNotRegex(page.locator('#detail-body').inner_text(),r'视觉审核|细节观察|审核截图|结构质量复核|主体通过')
                    self.assertIn('鹈鹕通过',page.locator('#detail-body').inner_text())
                    self.assertTrue(page.locator('#download-svg').is_visible())
                    page.set_viewport_size({'width':390,'height':844})
                    self.assertTrue(page.locator('#detail-image').is_visible())
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.goto(f'http://127.0.0.1:{http.server_port}/admin')
                    page.wait_for_selector('#auth-dialog[open]')
                    self.assertFalse(errors,errors)
                    browser.close()
                (Path(directory)/'artifacts'/str(run_id)/'0.png').unlink()
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f'http://127.0.0.1:{http.server_port}/api/runs/{run_id}/frames/0')
                self.assertEqual(404,caught.exception.code)
                caught.exception.close()
            finally:
                http.shutdown();http.server_close();thread.join();monitor.stopped.set()


if __name__=='__main__':unittest.main()
