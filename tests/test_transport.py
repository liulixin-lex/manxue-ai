import io
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server
from reliability import StageError


class Response:
    def __init__(self,data,step=7):self.data=io.BytesIO(data);self.step=step
    def read1(self,count):return self.data.read(min(count,self.step))
    def isclosed(self):return False


class StreamTests(unittest.TestCase):
    def read(self,raw,limit=1024*1024):
        return server.read_response(Response(raw),Mock(),time.monotonic()+5,limit,True)

    def test_crlf_sse_split_at_arbitrary_bytes(self):
        expected={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'鹈鹕'}]}]}
        data=b': keepalive\r\n\r\ndata: '+json.dumps({'type':'response.completed','response':expected},ensure_ascii=False).encode()+b'\r\n\r\n'
        self.assertEqual(expected,self.read(data))

    def test_plain_json_fallback(self):
        raw=b'{"status":"completed"}'
        self.assertEqual(raw,self.read(raw))

    def test_missing_completion_and_done_are_errors(self):
        with self.assertRaises(StageError) as caught:self.read(b'data: [DONE]\n\n')
        self.assertEqual('stream_incomplete',caught.exception.code)

    def test_empty_payload_does_not_swallow_error(self):
        raw=b'data: {"type":"response.failed","response":{"error":{"code":"invalid_api_key"}}}\n\n'
        with self.assertRaises(StageError) as caught:self.read(raw)
        self.assertFalse(caught.exception.retryable)

    def test_limit_is_applied_before_completed_event(self):
        with self.assertRaises(StageError):self.read(b'data: '+b'x'*200,limit=100)

    def test_retry_after_date_and_bad_values(self):
        from email.utils import formatdate
        self.assertGreater(server.retry_delay(formatdate(time.time()+20,usegmt=True)),10)
        for value in (None,'garbage','inf','nan'):self.assertIsNone(server.retry_delay(value))


class HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.path.startswith('/slow'):
            time.sleep(1)
        raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'最终答案：21'}}],
                        'model':'fake','usage':{'total_tokens':7}}).encode()
        try:
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass


class RequestProcessTests(unittest.TestCase):
    def setUp(self):
        self.http=ThreadingHTTPServer(('127.0.0.1',0),HTTPHandler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.config=dict(server.DEFAULTS,base_url=f'http://127.0.0.1:{self.http.server_port}',protocol='chat',timeout_seconds=3)

    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join()

    def test_request_process_success(self):
        result=server.call_model(self.config,'test')
        self.assertEqual('最终答案：21',result[0])
        self.assertEqual(7,result[1]['total_tokens'])

    def test_request_process_hard_deadline(self):
        config=dict(self.config,base_url=self.config['base_url']+'/slow',timeout_seconds=.15)
        start=time.monotonic()
        with self.assertRaises(StageError):server.call_model(config,'test')
        self.assertLess(time.monotonic()-start,.8)

    def test_cancel_interrupts_process(self):
        cancel=threading.Event()
        timer=threading.Timer(.1,cancel.set);timer.start()
        start=time.monotonic()
        try:
            with self.assertRaises(StageError):
                server.call_model(dict(self.config,base_url=self.config['base_url']+'/slow',_cancel=cancel),'test')
        finally:timer.join()
        self.assertLess(time.monotonic()-start,.8)


class DiagnosisTests(unittest.TestCase):
    def test_network_failures_keep_retry_policy_without_raw_exception(self):
        import ssl
        config=dict(server.DEFAULTS,base_url='https://example.org/v1',protocol='chat')
        for exc,code,retry in ((socket.gaierror('private host details'),'dns',True),
                               (ConnectionResetError('private connection details'),'connection',True),
                               (ssl.SSLCertVerificationError('private certificate details'),'tls_certificate',False)):
            with self.subTest(code=code),patch.object(server.request,'build_opener') as opener:
                opener.return_value.open.side_effect=exc
                with self.assertRaises(StageError) as caught:
                    server._call_model_direct(config,'test')
                self.assertEqual(code,caught.exception.code)
                self.assertEqual(retry,caught.exception.retryable)
                self.assertNotIn('private',str(caught.exception))

if __name__=='__main__':unittest.main()
