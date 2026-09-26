"""Display-only mode is explicit, persistent, and never implies a quality pass."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
from test_core import SVG

STILL = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200"><rect width="300" height="200" fill="#9fcab0"/></svg>'


class DisplayExecutionTests(unittest.TestCase):
    def test_disabled_skips_all_quality_checks_and_review_requests(self):
        for svg in (SVG, STILL):
            with self.subTest(svg=svg), patch.object(server, 'review_pelican') as review:
                call = Mock(return_value=(svg, {'output_tokens': 80}, 'generator'))
                result = server.perform_test(dict(server.DEFAULTS, review_enabled=False), '', 'DIFFERENT', call)
                self.assertEqual('displayed', result['status'])
                self.assertEqual('display', result['evaluation_level'])
                self.assertEqual('skipped', result['review']['status'])
                self.assertEqual('', result['error'])
                self.assertFalse(result['checks']['nonce'])
                self.assertTrue(result['svg'])
                review.assert_not_called()
                self.assertEqual(1, call.call_count)

    def test_request_failure_and_unusable_svg_still_fail(self):
        for text in ('not an SVG', '<svg><script>alert(1)</script></svg>', '<svg><image href="https://example.com/a.png"/></svg>'):
            result = server.perform_test(dict(server.DEFAULTS, review_enabled=False), '', 'TEST', lambda *a: (text, {}, 'model'))
            self.assertEqual('error', result['status'])
            self.assertEqual('svg_unavailable', result['error_code'])
            self.assertFalse(result['svg'])
        call = Mock(side_effect=server.StageError('generation', 'http_401', '鉴权失败'))
        result = server.perform_test(dict(server.DEFAULTS, review_enabled=False, retry_count=0), '', 'TEST', call)
        self.assertEqual('error', result['status'])
        self.assertNotEqual('passed', (result.get('review') or {}).get('status'))

    def test_enabling_restores_review_and_guest_keeps_its_existing_checks(self):
        with patch.object(server, 'review_pelican', return_value={'status': 'invalid', 'reason': '主体不符'}) as review:
            result = server.perform_test(dict(server.DEFAULTS, review_enabled=True), '', 'TEST', lambda *a: (SVG, {}, 'model'))
            self.assertEqual('invalid', result['status'])
            review.assert_called_once()
        with patch.object(server, 'review_pelican') as review:
            result = server.perform_test(dict(server.DEFAULTS, review_enabled=False, _guest=True), '', 'TEST', lambda *a: (STILL, {}, 'model'))
            self.assertEqual('invalid', result['status'])
            self.assertEqual('basic', result['evaluation_level'])
            review.assert_not_called()

    def test_candy_scoring_remains_independent(self):
        for answer, expected in (('最终答案：21', 'displayed'), ('最终答案：22', 'invalid')):
            result = server.perform_suite(dict(server.DEFAULTS, review_enabled=False), 'draw', 'TEST',
                lambda config, prompt: (answer if prompt == server.CANDY_PROMPT else STILL, {}, 'model'))
            self.assertEqual(expected, result['status'])
            self.assertEqual('displayed', result['tests']['pelican']['status'])
            self.assertEqual('passed' if expected == 'displayed' else 'invalid', result['tests']['candy']['status'])


class DisplaySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.monitor = server.Monitor(self.temp.name)

    def tearDown(self):
        self.monitor.stopped.set()
        self.temp.cleanup()

    def test_default_persistence_and_boolean_validation(self):
        self.assertTrue(self.monitor.settings()['review_enabled'])
        for value in ('false', 0, 1, None, [], {}):
            with self.assertRaises(ValueError): self.monitor.save({'review_enabled': value})
        self.monitor.save({'review_enabled': False})
        restored = server.Monitor(self.temp.name)
        self.assertFalse(restored.settings()['review_enabled'])
        self.assertFalse(restored.state()['settings']['review_enabled'])
        restored.save({'review_enabled': True})
        self.assertTrue(restored.settings()['review_enabled'])
        restored.stopped.set()

    def test_disabled_does_not_require_judge_or_healthy_renderer(self):
        self.monitor.save({'review_enabled': False, 'review_mode': 'external', 'review_provider_id': 999})
        self.monitor.render_health = {'status': 'error'}
        self.monitor.scheduler_heartbeat = time.monotonic()
        self.assertTrue(self.monitor.health()['ready'])
        with self.assertRaisesRegex(ValueError, '审核提供商'):
            self.monitor.save({'review_enabled': True})
        self.assertFalse(self.monitor.settings()['review_enabled'])
        self.monitor.save({'review_enabled': True, 'review_mode': 'self'})
        self.assertFalse(self.monitor.health()['ready'])

    def test_journal_replay_filters_and_stats_preserve_displayed_status(self):
        self.monitor.save({'review_enabled': False, 'api_key': 'fixture-key'})
        result = server.perform_suite(self.monitor.settings(), 'draw', 'TEST',
            lambda config, prompt: ('最终答案：21' if prompt == server.CANDY_PROMPT else STILL, {}, 'model'))
        with patch.object(self.monitor, 'execute') as execute:
            run_id = self.monitor.start_run()
            for _ in range(100):
                if execute.called: break
                time.sleep(.01)
            self.assertTrue(execute.called)
        (self.monitor.result_directory / f'{run_id}.json').write_text(json.dumps(result))
        restored = server.Monitor(self.temp.name)
        displayed = restored.gallery(1, 'displayed', 'pelican')
        self.assertEqual([run_id], [row['id'] for row in displayed['items']])
        self.assertEqual('displayed', displayed['items'][0]['status'])
        self.assertEqual(0, restored.gallery(1, 'passed', 'pelican')['total'])
        state = restored.state()
        self.assertEqual(1, state['visual_stats']['displayed'])
        self.assertEqual(0, state['visual_stats']['passed'])
        self.assertEqual(0, state['visual_stats']['reviewed'])
        self.assertEqual(1, state['stats']['passed'])
        with restored.db() as db:
            self.assertEqual(0, restored.migrate_review_policy(db))
        restored.stopped.set()

    def test_only_authenticated_admin_can_change_switch(self):
        self.monitor.setup_password('fixture-admin-password')
        http = server.BoundedHTTPServer(('127.0.0.1', 0), server.Handler)
        http.monitor = self.monitor
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        try:
            def post(path, body, token=None):
                headers = {'Content-Type': 'application/json'}
                if token: headers['Authorization'] = 'Bearer ' + token
                with urlopen(Request(base + path, data=json.dumps(body).encode(), headers=headers)) as response:
                    return json.load(response)
            with self.assertRaises(HTTPError) as caught:
                post('/api/admin/settings', {'review_enabled': False})
            self.assertEqual(401, caught.exception.code); caught.exception.close()
            token = post('/api/auth/login', {'password': 'fixture-admin-password'})['token']
            self.assertFalse(post('/api/admin/settings', {'review_enabled': False}, token)['review_enabled'])
            self.assertTrue(post('/api/admin/settings', {'review_enabled': True}, token)['review_enabled'])
        finally:
            http.shutdown(); http.server_close(); thread.join()


@unittest.skipUnless(os.environ.get('RENDER_TESTS') == '1', 'requires Chromium')
class DisplayBrowserTests(unittest.TestCase):
    def test_admin_toggle_public_labels_filters_and_candy_results(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as directory:
            monitor = server.Monitor(directory, model_call=lambda config, prompt: ('最终答案：21' if prompt == server.CANDY_PROMPT else STILL, {}, 'model'))
            monitor.setup_password('fixture-admin-password')
            monitor.save({'api_key': 'fixture-key'})
            http = server.BoundedHTTPServer(('127.0.0.1', 0), server.Handler); http.monitor = monitor
            thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{http.server_port}'
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(args=['--disable-dev-shm-usage'])
                    admin = browser.new_page(); errors = []
                    admin.on('pageerror', lambda error: errors.append(str(error)))
                    def login():
                        admin.goto(base + '/admin')
                        admin.fill('#admin-token', 'fixture-admin-password'); admin.click('#auth-submit')
                        admin.wait_for_selector('#admin-content', state='visible')
                    login()
                    self.assertTrue(admin.is_checked('#review-enabled'))
                    admin.uncheck('#review-enabled'); admin.click('#admin-save')
                    admin.wait_for_function('() => config.review_enabled === false')
                    self.assertFalse(admin.locator('#review-options').is_visible())
                    login(); self.assertFalse(admin.is_checked('#review-enabled'))
                    admin.click('#admin-run')
                    for _ in range(100):
                        rows = monitor.runs()
                        if rows and rows[0]['status'] != 'running': break
                        time.sleep(.02)
                    self.assertEqual('displayed', rows[0]['status'])
                    public = browser.new_page(); public.on('pageerror', lambda error: errors.append(str(error)))
                    public.goto(base); public.wait_for_selector('#gallery .run-card')
                    self.assertIn('仅展示', public.locator('#gallery').inner_text())
                    self.assertNotIn('鹈鹕通过', public.locator('#gallery').inner_text())
                    self.assertIn('1 次仅展示', public.locator('#visual-stats').inner_text())
                    self.assertEqual(1, public.locator('#candy-grid .candy-square.passed').count())
                    public.select_option('#filter', 'displayed')
                    public.wait_for_function('() => !polling')
                    public.locator('#gallery [data-run]').click(); public.wait_for_selector('.review-detail')
                    self.assertIn('本轮未进行鹈鹕判定', public.locator('#detail-body').inner_text())
                    self.assertFalse(public.locator('.check-grid').is_visible())
                    public.keyboard.press('Escape')
                    for width in (1440, 390):
                        for page in (admin, public):
                            page.set_viewport_size({'width': width, 'height': 1000})
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        if os.environ.get('DISPLAY_MODE_SCREENSHOTS'):
                            admin.locator('.review-settings').screenshot(path=f'/qa/admin-display-{width}.png')
                            public.locator('#view-pelican').screenshot(path=f'/qa/public-display-{width}.png')
                    admin.check('#review-enabled'); admin.click('#admin-save')
                    admin.wait_for_function('() => config.review_enabled === true')
                    self.assertTrue(admin.locator('#review-options').is_visible())
                    self.assertFalse(errors, errors)
                    browser.close()
            finally:
                http.shutdown(); http.server_close(); thread.join(); monitor.stopped.set()
