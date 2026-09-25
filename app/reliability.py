"""Bounded retries and public-safe diagnoses shared by generation and review."""
import http.client
import contextlib
import hashlib
import json
import random
import re
import socket
import ssl
import time
import threading
from urllib import error

BACKGROUND_SLOTS = threading.BoundedSemaphore(2)
GUEST_SLOTS = threading.BoundedSemaphore(4)
NODE_LOCK = threading.Lock()
NODE_SLOTS = {}


@contextlib.contextmanager
def request_slot(config):
    global_slot = GUEST_SLOTS if config.get('_guest') else BACKGROUND_SLOTS
    deadline = time.monotonic() + remaining(config)
    if not global_slot.acquire(timeout=remaining(config)):
        raise StageError('request','queue_timeout','请求并发队列等待超时')
    key = (config['base_url'], hashlib.sha256(config['api_key'].encode()).digest())
    acquired = False
    with NODE_LOCK:
        entry = NODE_SLOTS.setdefault(key,[threading.BoundedSemaphore(2),0])
        entry[1] += 1
    try:
        acquired = entry[0].acquire(timeout=max(0,deadline-time.monotonic()))
        if not acquired:
            raise StageError('request','queue_timeout','节点并发队列等待超时')
        yield dict(config, timeout_seconds=remaining(config,deadline-time.monotonic()))
    finally:
        if acquired:
            entry[0].release()
        with NODE_LOCK:
            entry[1] -= 1
            if entry[1] == 0:
                NODE_SLOTS.pop(key,None)
        global_slot.release()


class StageError(ValueError):
    def __init__(self, stage, code, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.stage, self.code = stage, code
        self.retryable, self.retry_after = retryable, retry_after


def diagnose(exc, stage):
    if isinstance(exc, StageError):
        return exc
    if isinstance(exc, error.URLError):
        return diagnose(exc.reason, stage)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return StageError(stage, 'tls_certificate', 'API HTTPS 证书校验失败')
    if isinstance(exc, socket.gaierror):
        return StageError(stage, 'dns', 'API 域名解析失败', True)
    if isinstance(exc, TimeoutError):
        return StageError(stage, 'timeout', '阶段执行超时', True)
    if isinstance(exc, (ConnectionError, http.client.IncompleteRead, ssl.SSLError)):
        return StageError(stage, 'connection', 'API 连接中断', True)
    # Only known local messages are safe to publish; never include arbitrary exceptions.
    message = str(exc)
    match = re.match(r'HTTP (\d{3})[：:]', message)
    if match:
        status = int(match[1])
        return StageError(stage, f'http_{status}', f'HTTP {status}：上游接口请求失败',
                          status in (408, 429) or 500 <= status < 600)
    for marker, code, public, retry in (
        ('超时', 'timeout', '上游响应超时', True),
        ('限流', 'rate_limit', '上游请求限流', True),
        ('额度不足', 'quota', '上游额度不足', False),
        ('鉴权失败', 'authentication', '上游鉴权失败', False),
        ('重定向', 'redirect', 'API 返回重定向，请填写最终地址', False),
        ('空响应', 'empty_response', '上游返回空响应', True),
        ('未返回可用', 'empty_response', '上游未返回可用文本', True),
        ('HTML/XML', 'protocol', '上游返回网页而非 API 响应', False),
        ('缺少完整结果', 'stream_incomplete', '上游流式响应中断', True),
        ('未完成', 'output_incomplete', '上游输出未完成或达到输出上限', False),
        ('无效 JSON', 'protocol', '上游返回无效 JSON', True),
        ('上游生成失败', 'upstream_failure', '上游生成失败', True),
        ('超过', 'response_limit', '上游响应超过大小限制', False),
        ('公网', 'address_policy', '访客检测仅支持公网 HTTPS 地址', False),
        ('内网', 'address_policy', '访客检测不允许访问本机或内网', False),
    ):
        if marker in message:
            return StageError(stage, code, public, retry)
    return StageError(stage, 'internal', f'阶段执行异常（{type(exc).__name__}）')


def event(stage, code, **fields):
    print(json.dumps(dict(stage=stage, code=code, **fields), ensure_ascii=False), flush=True)


def remaining(config, ceiling=None):
    budget = config.get('timeout_seconds', 300) if ceiling is None else ceiling
    budget = min(budget, config.get('_deadline', float('inf')) - time.monotonic())
    if config.get('_cancel') and config['_cancel'].is_set():
        raise StageError('task', 'cancelled', '检测已取消')
    if budget <= 0:
        raise StageError('task', 'deadline', '整轮检测超过时间预算')
    return budget


def request_with_retries(config, prompt, model_call, stage, attempts):
    deadline = time.monotonic() + remaining(config)
    for index in range(config.get('retry_count', 2) + 1):
        started = time.monotonic()
        attempt = {'stage': stage, 'attempt': index + 1}
        attempts.append(attempt)
        try:
            budget = remaining(config, deadline - time.monotonic())
            with request_slot(dict(config, timeout_seconds=budget)) as request_config:
                result = model_call(request_config, prompt)
            if isinstance(result[1],dict) and isinstance(result[1].get('_transport'),dict):
                attempt['transport'] = result[1]['_transport']
            attempt.update(status='passed', duration_seconds=round(time.monotonic()-started, 3))
            return result
        except Exception as exc:
            failure = diagnose(exc, stage)
            if failure.stage == 'request':
                failure.stage = stage
            attempt.update(status='error', error_code=failure.code,
                           duration_seconds=round(time.monotonic()-started, 3))
            event(stage, failure.code, attempt=index+1)
            delay = max(random.uniform(.5, 1) * min(2**index, 8), failure.retry_after or 0)
            if (not failure.retryable or index >= config.get('retry_count', 2)
                    or deadline - time.monotonic() <= delay):
                raise failure from None
            cancel = config.get('_cancel')
            if cancel:
                cancel.wait(delay)
            else:
                time.sleep(delay)
