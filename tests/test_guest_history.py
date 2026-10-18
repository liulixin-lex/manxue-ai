import contextlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from urllib import request, error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
from reliability import StageError


class GuestHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.monitor = server.Monitor(self.directory.name, model_call=self.model)

    def tearDown(self):
        self.monitor.stopped.set()
        self.directory.cleanup()

    @staticmethod
    def model(config, prompt):
        if 'SVG' not in prompt:
            return '最终答案：21', {'input_tokens': 3, 'output_tokens': 5}, 'fixture'
        raise StageError('generation', 'http_524', 'HTTP 524：do-not-publish upstream body', False)

    def submit(self, **values):
        return self.monitor.guest_test(dict(base_url='https://private-provider.example/v1', api_key='secret-visitor-key', model='fixture-model', effort='xhigh', **values))

    def test_subtests_diagnostics_and_credentials_boundary(self):
        public = self.submit()
        self.assertNotIn('_diagnostics', public)
        self.assertNotIn('attempts_log', public['tests']['pelican'])
        record = self.monitor.admin_guest_history()['items'][0]
        self.assertTrue(record['diagnostics_available'])
        self.assertEqual('responses', record['protocol'])
        self.assertEqual(2400, record['timeout_seconds'])
        self.assertEqual('error', record['status'])
        self.assertEqual('passed', record['tests']['candy']['status'])
        self.assertEqual('http_524', record['tests']['pelican']['error_code'])
        self.assertEqual('http_524', record['tests']['pelican']['attempts_log'][0]['error_code'])
        self.assertIn('duration_seconds', record['tests']['pelican']['attempts_log'][0])
        serialized = json.dumps(record)
        for secret in ('secret-visitor-key', 'private-provider.example', 'do-not-publish', '最终答案：21', '<svg', 'api_key', 'base_url'):
            self.assertNotIn(secret, serialized)

    def test_history_survives_public_expiry_and_restart(self):
        public = self.submit(test_type='candy')
        with self.monitor.db() as db:
            result = json.loads(db.execute('SELECT result FROM guest_results').fetchone()[0])
            result['submitted'] = result['started'] = result['finished'] = time.time()-7200
            db.execute('UPDATE guest_results SET created=?,result=?', (result['finished'], json.dumps(result)))
            db.execute('DELETE FROM guest_history')
            self.monitor.write_guest_history(db, result)
        self.monitor.prune_guests()
        self.assertEqual([], self.monitor.guest_results())
        self.assertEqual(public['id'], self.monitor.admin_guest_history()['items'][0]['id'])
        restored = server.Monitor(self.directory.name, model_call=self.model)
        self.assertEqual(1, restored.admin_guest_history()['total'])

    def test_legacy_backfill_preserves_unknowns_and_interruptions(self):
        now = time.time()
        legacy = dict(id='a'*24, submitted=now, started=now, finished=None, status='running', test_type='candy', model='legacy', effort='xhigh', api_masked='pri****ple', scene='', svg='', tests={'candy':{'status':'running'}})
        with self.monitor.db() as db:
            db.execute('INSERT INTO guest_results VALUES (?,?,?)', (legacy['id'], now, json.dumps(legacy)))
        restored = server.Monitor(self.directory.name, model_call=self.model)
        record = restored.admin_guest_history()['items'][0]
        self.assertFalse(record['diagnostics_available'])
        self.assertIsNone(record['protocol'])
        self.assertEqual('error', record['status'])
        self.assertEqual('interrupted', record['tests']['candy']['error_code'])
        self.assertEqual([], record['tests']['candy']['attempts_log'])

    def test_pagination_filters_and_retention_boundaries(self):
        now = time.time()
        with self.monitor.db() as db:
            for index in range(26):
                record = dict(id=f'{index:024x}', submitted=now-index, status='passed' if index % 2 else 'error', test_type='candy' if index % 2 else 'both', tests={})
                self.monitor.write_guest_history(db, record)
            for index, status in ((50, 'passed'), (51, 'queued')):
                self.monitor.write_guest_history(db, dict(id=f'{index:024x}', submitted=now-31*86400, status=status, test_type='candy', tests={}))
        self.monitor.prune_guests()
        first = self.monitor.admin_guest_history()
        second = self.monitor.admin_guest_history(2)
        self.assertEqual((27, 2, 20, 7), (first['total'], first['pages'], len(first['items']), len(second['items'])))
        self.assertFalse(set(r['id'] for r in first['items']) & set(r['id'] for r in second['items']))
        self.assertEqual(13, self.monitor.admin_guest_history(test='pelican')['total'])
        self.assertEqual(13, self.monitor.admin_guest_history(status='passed')['total'])
        self.assertEqual(2, self.monitor.admin_guest_history(999)['page'])
        for kwargs in ({'page':0}, {'status':"error' OR 1=1"}, {'test':'svg'}, {'page':True}):
            with self.assertRaises(ValueError):
                self.monitor.admin_guest_history(**kwargs)
        with patch.object(server, 'GUEST_HISTORY_LIMIT', 5):
            self.monitor.prune_guests()
        self.assertEqual(6, self.monitor.admin_guest_history()['total'])

    def test_submission_metadata_is_visible_while_queued(self):
        # Keep workers dormant to check the transaction before any model call.
        self.monitor.guest_workers.append(object())
        result = self.monitor.submit_guest({'base_url':'https://example.com', 'api_key':'secret-visitor-key', 'model':'fixture', 'protocol':'chat'})
        record = self.monitor.admin_guest_history()['items'][0]
        self.assertEqual(result['id'], record['id'])
        self.assertEqual(('queued', 'chat'), (record['status'], record['protocol']))

    @contextlib.contextmanager
    def http(self):
        http = server.BoundedHTTPServer(('127.0.0.1', 0), server.Handler)
        http.monitor = self.monitor
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{http.server_port}'
        finally:
            http.shutdown(); http.server_close(); thread.join()

    def test_admin_endpoint_authentication_and_validation(self):
        self.submit()
        self.monitor.setup_password('fixture-password-1234')
        token = self.monitor.login('fixture-password-1234')
        with self.http() as base:
            for suffix, token_value, expected in (('', '', 401), ('?page=0', token, 400), ('?page=1&status=error', token, 200)):
                req = request.Request(base+'/api/admin/guest-records'+suffix, headers={'Authorization':'Bearer '+token_value})
                try:
                    with request.urlopen(req) as response:
                        self.assertEqual(expected, response.status)
                        self.assertEqual(1, json.load(response)['total'])
                except error.HTTPError as exc:
                    self.assertEqual(expected, exc.code)
            self.monitor.logout(token)
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(request.Request(base+'/api/admin/guest-records', headers={'Authorization':'Bearer '+token}))
            self.assertEqual(401, caught.exception.code)

    @unittest.skipUnless(os.environ.get('RENDER_TESTS') == '1', 'requires Chromium')
    def test_browser_filters_pagination_mobile_and_logout(self):
        from playwright.sync_api import sync_playwright
        self.monitor.setup_password('fixture-password-1234')
        self.submit()
        with self.monitor.db() as db:
            original = json.loads(db.execute('SELECT result FROM guest_results').fetchone()[0])
            for index in range(24):
                record = dict(original, id=f'{index:024x}', submitted=time.time()-index-1, model='<img src=x onerror=alert(1)>', test_type='candy', status='passed', tests={'candy':original['tests']['candy']})
                self.monitor.write_guest_history(db, record)
        with self.http() as base, sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=['--disable-dev-shm-usage'])
            page = browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base+'/admin')
            page.locator('#admin-token').fill('fixture-password-1234')
            page.locator('#auth-submit').click()
            page.wait_for_function("() => document.querySelector('#guest-records-summary').textContent.includes('共 25 条')")
            self.assertEqual(20, page.locator('#guest-records-list > tr').count())
            self.assertEqual(0, page.locator('#guest-records-list img').count())
            page.locator('#guest-records-list details summary').first.click()
            self.assertIn('http_524', page.locator('#guest-records-list details').first.inner_text())
            screenshots = os.environ.get('GUEST_HISTORY_SCREENSHOTS')
            page.locator('#guest-records-next').click()
            page.wait_for_function("() => document.querySelector('#guest-records-page').textContent.includes('第 2 / 2')")
            self.assertEqual(5, page.locator('#guest-records-list > tr').count())
            page.select_option('#guest-records-status', 'error')
            page.wait_for_function("() => document.querySelector('#guest-records-summary').textContent.includes('共 1 条')")
            page.locator('#guest-records-list details summary').first.click()
            if screenshots:
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=screenshots+'/desktop.png', full_page=True)
                page.locator('#guest-records').screenshot(path=screenshots+'/desktop-records.png')
            for width in (390, 768):
                page.set_viewport_size({'width':width, 'height':844})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                if screenshots and width == 390:
                    page.evaluate('scrollTo(0,0)')
                    page.screenshot(path=screenshots+'/mobile.png', full_page=True)
                    page.locator('#guest-records').screenshot(path=screenshots+'/mobile-records.png')
            page.select_option('#guest-records-status', 'running')
            page.wait_for_selector('#guest-records-empty:visible')
            self.assertIn('筛选', page.locator('#guest-records-empty').inner_text())
            page.locator('#logout').click()
            page.wait_for_selector('#auth-dialog[open]')
            self.assertEqual(0, page.locator('#guest-records-list tr').count())
            self.assertFalse(errors, errors)
            browser.close()


if __name__ == '__main__':
    unittest.main()
