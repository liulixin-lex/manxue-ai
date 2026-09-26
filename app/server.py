"""Personal pelican benchmark. Chromium/Playwright are used for visual review."""
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
import queue
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import socket
import sqlite3
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, parse, request
import xml.etree.ElementTree as ET
from visual_review import review_pelican, parse_review, reassess_legacy_review, REVIEW_VERSION
from reliability import StageError, diagnose, event, remaining, request_with_retries
from artifacts import save_evidence, prune_evidence, atomic_write
from svg_display import display_svg

ROOT = Path(__file__).resolve().parent
INTERVAL = 30 * 60
MAX_RESPONSE = 2 * 1024 * 1024
# Cap total SSE bytes; individual events are parsed and discarded incrementally.
MAX_STREAM_RESPONSE = 16 * 1024 * 1024
DEFAULTS = dict(base_url="https://api.example.com/v1", model="gpt-6-astra",
                effort="medium", protocol="responses", api_key="", enabled=False, next_run=None,
                interval_minutes=30, timeout_seconds=300, max_output_tokens=16000, guest_enabled=True, retry_count=2, review_timeout_seconds=240, review_effort="inherit", review_format="auto", judge_node_id=None,
                review_mode="self", review_provider_id=None, promotion_enabled=False,
                promotion_title="GGUUAI API", promotion_description="", promotion_url="", promotion_action="访问中转站")
NODE_FIELDS = ("base_url", "api_key", "model", "effort", "protocol")
PROVIDER_FIELDS = (*NODE_FIELDS, "token_field")
# Scene variation does not change the subject-based review rubric.
SCENE_MOTION = {
    "海边木栈道": "近处栈道木纹、栏杆向后滚动，远处海浪和帆船缓慢漂移。",
    "秋日林道": "近处落叶和路边树干向后移动，远处金色树林慢速横移，少量叶片飘落。",
    "春日草坡": "前景花草和小径标记连续向后掠过，远处草坡与云朵缓慢移动。",
    "雨后湿地": "近处芦苇、水洼倒影向后移动，远处水鸟缓慢掠过，水面有轻微涟漪。",
    "黄昏公路": "路面虚线快速向后滚动，远处电线杆、山丘慢速横移，夕阳保持柔和。",
    "湖畔风车": "岸边草丛和石子连续向后移动，远处湖岸缓慢横移，风车叶片匀速旋转。",
    "热带海岛": "前景棕榈叶和沙地纹理向后掠过，远处椰树与海面缓慢漂移。",
    "雪山谷地": "近处雪地刻痕、松树向后移动，远处雪峰慢速横移，少量雪花飘落。",
    "竹林小径": "近处竹竿、地面光斑快速向后滑动，远处竹林慢速横移，竹叶轻摆。",
    "樱花河堤": "路旁护栏和近处樱树向后掠过，远处河岸慢速移动，零星花瓣随风飘落。",
    "沙漠绿洲": "近处碎石和沙纹向后滑动，远处沙丘缓慢横移，棕榈叶轻轻摆动。",
    "葡萄园乡道": "近处木桩和藤蔓向后移动，远处起伏田野慢速横移，风吹叶片。",
    "灯塔海岸": "近处道路标记与草丛持续向后移动，远处灯塔海岸慢速横移，海面波纹流动。",
    "运河石桥": "近处石板和桥栏向后滑动，远处河岸房屋缓慢移动，小船轻轻漂移。",
    "高原花田": "近处野花与车辙向后掠过，远处连绵丘陵慢速横移，云影缓缓移动。",
    "城市滨江": "近处道路划线和栏杆向后滚动，远处楼群慢速横移，江面反光轻微流动。",
}
SCENES = tuple(SCENE_MOTION)
PELICAN_STYLES = (
    "清爽矢量",
    "柔和童书",
    "线描平涂",
    "分层剪纸",
    "复古旅行海报",
    "明快卡通",
)
PELICAN_ACTIONS = (
    "轻快巡游，身体随踩踏微动",
    "悠闲骑行，偶尔转头",
    "迎风骑行，围巾轻摆",
    "平稳前行，翅膀轻调平衡",
)
PROMPT = """生成鹈鹕骑自行车的 SVG 循环动画，场景：{scene}。
橙色长嘴、喉囊清晰，鸟和车完整居中；坐在车座上连续踩踏，车轮同步转动。
画幅3:2，背景铺满四边，无留白、边框和圆角。
侧面跟拍，路面与远景向左移动，近快远慢；重复铺排，4 秒无缝自动循环。
右下角固定显示文本校验码：{nonce}。只输出完整 SVG，可用 CSS/SMIL，不用 JavaScript 或外部资源。"""


def build_pelican_prompt(scene, nonce):
    """Persist the exact sampled prompt so any run can be inspected later."""
    environment = SCENE_MOTION.get(scene, "近处路面后移，远景慢速横移。")
    style = secrets.choice(PELICAN_STYLES)
    action = secrets.choice(PELICAN_ACTIONS)
    return PROMPT.format(scene=scene, nonce=nonce) + f"\n环境：{environment}\n画风：{style}。动作：{action}。"



CANDY_PROMPT = """在一个黑色的袋子里放有三种口味的糖果，每种糖果有两种不同的形状（圆形和五角星形，不同的形状靠手感可以分辨）。现已知不同口味的糖和不同形状的数量统计如下表。参赛者需要在活动前决定摸出的糖果数目，那么，最少取出多少个糖果才能保证手中同时拥有不同形状的苹果味和桃子味的糖？（同时手中有圆形苹果味匹配五角星桃子味糖果，或者有圆形桃子味匹配五角星苹果味糖果都满足要求）

| 形状 | 苹果味 | 桃子味 | 西瓜味 |
| 圆形 | 7 | 9 | 8 |
| 五角星形 | 7 | 6 | 4 |
最后一行请严格写成：最终答案：整数。"""


def candy_passes(text):
    # Version 3: score an unambiguous final answer, not a number in the reasoning.
    match = re.search(r'(?:^|\n)\s*(?:最终答案|答案|final answer)\s*[：:]\s*(\d+)\s*(?:[个颗]?(?:糖果|糖)?[。.!！]?)\s*$', text.strip(), re.I)
    if match:
        return int(match[1]) == 21
    return re.fullmatch(r'21\s*(?:[个颗]?(?:糖果|糖)?[。.!！]?)', text.strip()) is not None


def next_slot(now, interval=INTERVAL):
    return (math.floor(now / interval) + 1) * interval


def mask(value):
    return value[:3] + "****" + value[-3:] if len(value) > 8 else "****"


def mask_url(value):
    host = parse.urlsplit(value).hostname or ""
    return host[:2] + "****" + host[-2:] if len(host) > 5 else "****"


def redact(value, config):
    for secret, replacement in ((config.get("api_key"), "****"),
                                (config.get("base_url"), mask_url(config.get("base_url", ""))),
                                (parse.urlsplit(config.get("base_url", "")).hostname, mask_url(config.get("base_url", "")))):
        if secret:
            value = value.replace(secret, replacement)
    return value


def promotion_url(value):
    """A public outbound link, never an HTML fragment or a server-side fetch."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("推广链接格式不正确")
    value = value.strip()
    if not value:
        return ""
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("推广链接不能包含空格、换行或反斜杠")
    if "://" not in value:
        value = "https://" + value
    url = parse.urlsplit(value)
    if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
        raise ValueError("请填写完整的 HTTP 或 HTTPS 站点链接")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("推广链接端口无效")
    return value


def validate_settings(values, old):
    if not isinstance(values, dict):
        raise ValueError("设置必须为 JSON 对象")
    new = old.copy()
    for key in ("base_url", "model", "effort", "protocol", "api_key"):
        if key in values:
            if not isinstance(values[key], str) or len(values[key]) > 4096:
                raise ValueError("设置字段格式不正确")
            value = values[key].strip()
            if key in ("api_key", "base_url") and not value:
                continue
            if key in ("api_key", "base_url") and "****" in value:
                raise ValueError("请填入新的完整值，或留空保留已保存的配置")
            new[key] = value
    base = new["base_url"]
    if "://" not in base:
        base = "https://" + base.removeprefix("//")
    if any(c.isspace() or ord(c) < 32 for c in base) or "\\" in base:
        raise ValueError("API 地址不能包含空格、换行或反斜杠")
    url = parse.urlsplit(base)
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1"))) or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("请填写 HTTPS API 地址，不要包含密钥、查询参数或账号密码")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("API 地址端口无效")
    base = base.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    if not parse.urlsplit(base).path:
        base += "/v1"
    new["base_url"] = base
    if not new["model"] or len(new["model"]) > 120:
        raise ValueError("请输入有效的模型名称")
    if new["effort"] not in ("low", "medium", "high", "xhigh") or new["protocol"] not in ("responses", "chat"):
        raise ValueError("思考强度或接口协议不正确")
    if any(ord(c) < 32 or ord(c) > 126 for c in new["api_key"]):
        raise ValueError("API Key 格式不正确")
    for key in ("enabled", "guest_enabled", "promotion_enabled"):
        if key in values:
            if not isinstance(values[key], bool):
                raise ValueError("开关必须为布尔值")
            new[key] = values[key]
    for key, low, high in (("interval_minutes", 1, 1440), ("timeout_seconds", 10, 600), ("max_output_tokens", 1024, 64000), ("retry_count", 0, 5), ("review_timeout_seconds", 10, 300)):
        if key in values:
            if type(values[key]) is not int or not low <= values[key] <= high:
                raise ValueError(f"{key} 必须为 {low}–{high} 的整数")
            new[key] = values[key]
    for key, choices in (('review_effort',('inherit','low','medium','high','xhigh')), ('review_format',('auto','prompt','json_schema'))):
        if key in values:
            if values[key] not in choices:
                raise ValueError('审核强度或输出格式不正确')
            new[key] = values[key]
    if "review_mode" in values:
        if values["review_mode"] not in ("self", "external"):
            raise ValueError("请选择同模型自评或外部模型审核")
        new["review_mode"] = values["review_mode"]
        new["judge_node_id"] = None
    if "review_provider_id" in values:
        provider_id = values["review_provider_id"]
        if provider_id is not None and (type(provider_id) is not int or provider_id <= 0):
            raise ValueError("审核提供商编号无效")
        new["review_provider_id"] = provider_id
    for key, limit in (("promotion_title", 60), ("promotion_description", 120), ("promotion_action", 16)):
        if key in values:
            value = values[key]
            if not isinstance(value, str) or len(value.strip()) > limit or any(ord(c) < 32 for c in value):
                raise ValueError(f"推广文字过长或包含无效字符（最多 {limit} 字符）")
            new[key] = value.strip()
    if "promotion_url" in values:
        new["promotion_url"] = promotion_url(values["promotion_url"])
    if new.get("promotion_enabled") and not all(new.get(k) for k in ("promotion_title", "promotion_action", "promotion_url")):
        raise ValueError("请先填写推广标题、按钮文字和跳转链接")
    if "judge_node_id" in values:
        node_id = values["judge_node_id"]
        if node_id is not None and (type(node_id) is not int or node_id <= 0):
            raise ValueError("审核节点编号无效")
        new["judge_node_id"] = node_id
        if node_id is None and 'review_mode' not in values:
            new['review_mode'] = 'self'
    if new["api_key"] and new["base_url"] != old["base_url"] and not values.get("api_key", "").strip():
        raise ValueError("更换 API 地址时，请重新填写该地址的 API Key")
    if new["enabled"] and not new["api_key"]:
        raise ValueError("请先填写 API Key，再启用自动检测")
    reset = new["interval_minutes"] != old.get("interval_minutes", 30) or not old["enabled"]
    new["next_run"] = (next_slot(time.time(), new["interval_minutes"] * 60) if reset or not old.get("next_run") else old["next_run"]) if new["enabled"] else None
    return new


def public_addresses(url):
    parsed = parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("访客检测仅支持公网 HTTPS 地址")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    ips = [entry[4][0] for entry in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips):
        raise ValueError("访客检测不允许访问本机、内网或保留地址")
    return parsed, ips


def read_response(response, sock, deadline, limit, streaming=False, review_frames=0):
    chunks, size = [], 0
    pending = b''
    stream_mode = None
    state = {'items':{}, 'started':set(), 'model':'未返回','frame_count':review_frames} if review_frames else None
    def recover():
        return recover_review_stream(state,review_frames) if state else None
    while size <= limit and not response.isclosed():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            recovered = recover()
            if recovered is not None:
                return recovered
            raise TimeoutError()
        # A completed review message may survive a gateway dropping the final event.
        sock.settimeout(min(remaining, 5 if recover() is not None else 90))
        try:
            chunk = response.read1(min(65536, limit + 1 - size))
        except (TimeoutError, ConnectionError, http.client.IncompleteRead):
            recovered = recover()
            if recovered is not None:
                return recovered
            raise
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise StageError('request','response_limit','上游响应超过大小限制')
        if not streaming:
            chunks.append(chunk)
            continue
        pending += chunk
        if stream_mode is None:
            head = pending.lstrip(b'\xef\xbb\xbf \t\r\n')
            if not head:
                continue
            if head[:1] in (b'{', b'[', b'<'):
                stream_mode = False
            elif b'\n' in head or b'\r' in head:
                stream_mode = True
        if stream_mode is True:
            while match := re.search(rb'\r\n\r\n|\n\n|\r\r',pending):
                block,pending = pending[:match.start()],pending[match.end():]
                complete = stream_event(block,state)
                if complete is not None:
                    return complete
        if len(pending) > MAX_RESPONSE:
            raise StageError('request','response_limit','上游单个事件或 JSON 超过大小限制')
    if streaming:
        if stream_mode:
            complete = stream_event(pending,state)
            if complete is not None:
                return complete
            recovered = recover()
            if recovered is not None:
                return recovered
            raise StageError('request','stream_incomplete','上游流式响应中断',True)
        return pending
    return b"".join(chunks)


def recover_review_stream(state, frame_count):
    if not state or not state['items'] or state['started'] - state['items'].keys():
        return None
    items = [state['items'][key] for key in sorted(state['items'])]
    if any(item.get('type') not in ('message','reasoning') for item in items):
        return None
    messages = [item for item in items if item.get('type') == 'message']
    if len(messages) != 1 or messages[0].get('role') != 'assistant' or messages[0].get('status') != 'completed':
        return None
    content = messages[0].get('content',[])
    if not content or any(part.get('type') != 'output_text' for part in content):
        return None
    text = ''.join(part.get('text','') for part in content)
    try:
        parse_review(text,frame_count,strict=True)
    except (StageError,TypeError):
        return None
    return {'status':'output_recovered','output':items,'model':state['model'],
            '_transport':{'review_message_recovered':True,'response_completed':False},'usage':{}}


def stream_event(block, state=None):
    try:
        lines=block.decode('utf-8-sig').splitlines()
        payload='\n'.join(line[5:].removeprefix(' ') for line in lines if line.startswith('data:'))
        if not payload or payload=='[DONE]':
            return None
        data=json.loads(payload)
        if not isinstance(data,dict):
            raise StageError('request','stream_format','上游流式事件格式无效')
        kind=data.get('type') or next((line[6:].strip() for line in lines if line.startswith('event:')),'')
        if data.get('error') or kind in ('error','response.failed'):
            raise diagnose(ValueError(upstream_failure(data)),'request')
        if kind=='response.incomplete':
            raise StageError('request','output_incomplete','上游输出未完成或达到输出上限')
        if state is not None:
            if kind in ('response.created','response.in_progress'):
                response = data.get('response',{})
                if not isinstance(response,dict):
                    raise StageError('request','stream_format','上游流式事件格式无效')
                state['model'] = response.get('model',state['model'])
            if kind == 'response.output_item.added' and type(data.get('output_index')) is int:
                state['started'].add(data['output_index'])
            if kind == 'response.output_item.done' and type(data.get('output_index')) is int:
                item = data.get('item')
                if (isinstance(item,dict) and (item.get('status') == 'completed'
                        or (item.get('type') == 'reasoning' and item.get('status') is None))):
                    state['items'][data['output_index']] = item
        if kind=='response.completed':
            result=data.get('response')
            if not isinstance(result,dict):
                raise StageError('request','stream_format','上游流式结果格式无效')
            result = dict(result)
            result.pop('_transport',None)
            if state and not result.get('output') and result.get('status') == 'completed':
                recovered = recover_review_stream(state,state["frame_count"])
                if recovered:
                    result = dict(result,output=recovered['output'],
                                  _transport={'review_message_recovered':True,'response_completed':True})
            return result
    except (UnicodeDecodeError,json.JSONDecodeError,TypeError):
        raise StageError('request','stream_format','上游流式事件格式无效') from None
    return None


def public_post(url, body, headers, timeout, limit=MAX_RESPONSE, streaming=False, review_frames=0):
    # Pin the checked IP while preserving hostname certificate verification; no DNS rebinding or proxy bypass.
    parsed, ips = public_addresses(url)
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        conn.sock = ssl.create_default_context().wrap_socket(
            socket.create_connection((ips[0], parsed.port or 443), timeout), server_hostname=parsed.hostname)
        sock = conn.sock
        conn.request("POST", parsed.path, body=body, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            delay = retry_delay(response.getheader('Retry-After'))
            raise StageError('request',f'http_{response.status}',f"HTTP {response.status}：上游请求失败",response.status in (408,429) or 500 <= response.status < 600,delay)
        return read_response(response, sock, deadline, limit, streaming, review_frames)
    finally:
        conn.close()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API 返回重定向，请填写最终 API 地址；未转发密钥")


def retry_delay(value):
    from email.utils import parsedate_to_datetime
    try:
        delay=float(value)
    except (ValueError,TypeError):
        try:
            delay=parsedate_to_datetime(value).timestamp()-time.time()
        except (ValueError,TypeError,AttributeError,OverflowError):
            return None
    return min(600,max(0,delay)) if math.isfinite(delay) else None


def upstream_failure(data):
    response = data.get("response")
    detail = data.get("error") or (response.get("error") if isinstance(response, dict) else None) or {}
    if not isinstance(detail, dict):
        detail = {}
    # Classify upstream details without publishing bodies that may contain credentials.
    code = str(detail.get("code", ""))
    message = str(detail.get("message", "")).lower()
    if code in ("timeout", "upstream_timeout", "gateway_timeout") or re.search(r"\b(?:524|504|timeout|timed out)\b", message):
        return "上游网关或模型响应超时，请稍后重试或联系接口服务商"
    if code == "insufficient_quota":
        return "上游额度不足，请检查接口账户余额或限额"
    if code == "rate_limit_exceeded":
        return "上游请求限流，请稍后重试"
    if code == "invalid_api_key":
        return "上游鉴权失败，请检查 API Key"
    return "上游生成失败，请稍后重试或联系接口服务商"


def parse_model_response(raw):
    if not raw.strip():
        raise ValueError("上游返回空响应，请检查接口服务状态")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    if raw.lstrip().startswith(b"<"):
        raise ValueError("上游返回 HTML/XML 页面而非 JSON，请检查 API 地址或网关状态")
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        completed = None
        streaming = False
        for block in text.split("\n\n"):
            lines = block.splitlines()
            payload = "\n".join(line[5:].removeprefix(" ") for line in lines if line.startswith("data:"))
            if not payload:
                continue
            streaming = True
            if payload == "[DONE]":
                continue
            event = json.loads(payload)
            if not isinstance(event, dict):
                raise ValueError("上游返回无效的流式事件")
            kind = event.get("type") or next((line[6:].strip() for line in lines if line.startswith("event:")), "")
            if event.get("error") or kind in ("error", "response.failed"):
                raise ValueError(upstream_failure(event))
            if kind == "response.incomplete":
                raise ValueError("上游响应未完成，可能达到输出上限或被中断")
            if kind == "response.completed":
                completed = event.get("response")
        if completed is not None:
            return completed
        if streaming:
            raise ValueError("上游流式响应缺少完整结果，可能已中断")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    raise ValueError("上游返回无效 JSON，请检查接口协议或网关状态")


def _call_model_direct(config, prompt):
    deadline = time.monotonic() + config["timeout_seconds"]
    streaming = config["protocol"] == "responses"
    common = dict(model=config["model"], stream=streaming)
    limit = MAX_STREAM_RESPONSE if streaming else MAX_RESPONSE
    if config["protocol"] == "responses":
        path = "/responses"
        body = dict(common, input=[dict(role="user", content=prompt)],
                    max_output_tokens=config["max_output_tokens"], store=False)
        if config['effort'] != 'omit':
            body['reasoning'] = dict(effort=config['effort'])
        if config.get('_system_prompt'):
            body['instructions'] = config['_system_prompt']
    else:
        path = "/chat/completions"
        body = dict(common, messages=[dict(role="user", content=prompt)])
        body[config.get('token_field', 'max_completion_tokens')] = config['max_output_tokens']
        if config['effort'] != 'omit':
            body['reasoning_effort'] = config['effort']
        if config.get('_system_prompt'):
            body['messages'].insert(0, dict(role='system', content=config['_system_prompt']))
    if config.get('_output_schema'):
        spec = {'name':'pelican_review','strict':True,'schema':config['_output_schema']}
        if streaming:
            body['text'] = {'format':dict(spec,type='json_schema')}
        else:
            body['response_format'] = {'type':'json_schema','json_schema':spec}
    req = request.Request(config["base_url"] + path, json.dumps(body).encode(),
                          {"Authorization": "Bearer " + config["api_key"], "Content-Type": "application/json", "Accept": "text/event-stream" if streaming else "application/json", "User-Agent": "PelicanWatch/1.0"})
    try:
        if config.get("_guest"):
            raw = public_post(req.full_url, req.data, dict(req.header_items()), config["timeout_seconds"], limit, streaming, config.get("_review_frame_count",0))
        else:
            with request.build_opener(NoRedirect()).open(req, timeout=config["timeout_seconds"]) as response:
                raw = read_response(response, response.fp.raw._sock, deadline, limit, streaming, config.get("_review_frame_count",0))
    except error.HTTPError as exc:
        # Narrow compatibility fallback only; never publish upstream error bodies.
        if config.get('_output_schema') and exc.code in (400,422):
            detail = exc.read(65536).decode('utf-8','replace').lower()
            if (re.search(r'json_schema|response_format|text[.\s]+format',detail)
                    and re.search(r'unsupported|not supported|not support|unknown parameter|unrecognized',detail)):
                raise StageError('request','structured_unsupported','上游不支持结构化审核输出') from None
        reasons = {401: "API Key 无效或已过期", 403: "无权访问该模型", 404: "接口或模型不存在，请检查协议", 429: "额度不足或触发限流", 524: "上游网关等待模型响应超时"}
        retry_after = retry_delay(exc.headers.get('Retry-After'))
        raise StageError('request', f'http_{exc.code}', f"HTTP {exc.code}：{reasons.get(exc.code, '上游接口请求失败')}",
                         exc.code in (408,429) or 500 <= exc.code < 600, retry_after) from None
    except TimeoutError:
        raise StageError('request','timeout','上游响应超时，请稍后重试或检查接口服务状态',True) from None
    except socket.gaierror:
        raise StageError('request','dns','API 域名解析失败，请检查地址拼写或 DNS',True) from None
    except ssl.SSLCertVerificationError:
        raise StageError('request','tls_certificate','API HTTPS 证书校验失败，请检查接口证书') from None
    except (ConnectionError, ssl.SSLError):
        raise StageError('request','connection','无法建立或保持 API 连接，请检查接口服务和网络',True) from None
    if len(raw) > limit:
        raise ValueError(f"上游响应超过 {limit // (1024 * 1024)} MB 限制")
    data = raw if isinstance(raw,dict) else parse_model_response(raw)
    transport = data.get('_transport',{}) if isinstance(raw,dict) else {}
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError(upstream_failure(data) if isinstance(data, dict) else "上游返回错误响应")
    if len(json.dumps(data).encode()) > MAX_RESPONSE:
        raise ValueError("上游完整结果超过 2 MB 限制")
    if config["protocol"] == "responses":
        if data.get("status") != "completed" and not (config.get('_review_frame_count') and transport.get('review_message_recovered')):
            raise ValueError("上游响应未完成，可能达到输出上限或被中断")
        text = "\n".join(part.get("text", "") for item in data.get("output", [])
                         if item.get("type") == "message" for part in item.get("content", [])
                         if part.get("type") == "output_text")
    else:
        choice = data.get("choices", [{}])[0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("上游响应未完成，可能达到输出上限或被中断")
        text = choice.get("message", {}).get("content", "")
        if isinstance(text, list):
            text = "\n".join(p.get("text", "") for p in text if p.get("type") == "text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("上游未返回可用的文本内容")
    if transport.get('review_message_recovered'):
        parse_review(text,config['_review_frame_count'],strict=True)
    usage = dict(data.get('usage') or {})
    if transport:
        usage['_transport'] = transport
    return text, usage, str(data.get("model", "未返回"))


def call_model(config, prompt):
    # Credentials travel over stdin only. A process deadline also covers blocking DNS.
    public_config = {k:config[k] for k in (*NODE_FIELDS, 'timeout_seconds', 'max_output_tokens')}
    # Leave the worker time to return a finalized message before its hard process deadline.
    process_budget = remaining(config)
    public_config['timeout_seconds'] = max(.01,process_budget-min(1,process_budget*.05))
    public_config['_guest'] = bool(config.get('_guest'))
    for key in ('_output_schema','_review_frame_count','_system_prompt','token_field'):
        if key in config:
            public_config[key] = config[key]
    process = subprocess.Popen([sys.executable, str(ROOT / 'model_worker.py')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic()+remaining(config)
        payload = json.dumps({'config': public_config, 'prompt': prompt}).encode()
        while True:
            try:
                budget = remaining(config,deadline-time.monotonic())
                raw, _ = process.communicate(payload,timeout=min(.25,budget))
                break
            except subprocess.TimeoutExpired:
                payload = None
        if process.returncode != 0:
            raise StageError('request', 'worker_exit', '请求进程异常退出', True)
        data = json.loads(raw)
        if 'error' in data:
            e = data['error']
            raise StageError('request', e['code'], e['message'], e['retryable'], e['retry_after'])
        return tuple(data['result'])
    except subprocess.TimeoutExpired:
        raise StageError('request', 'timeout', '上游响应超时', True) from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for pipe in (process.stdin, process.stdout):
            if pipe:
                pipe.close()


def inspect_svg(output, nonce):
    checks = dict(svg=False, animation=False, nonce=False)
    match = re.search(r"<svg\b", output, re.I)
    if not match:
        return "", checks, "未找到完整 SVG"
    source = output[match.start():]
    svg = ""
    try:
        if len(output.encode()) > MAX_RESPONSE:
            raise ValueError("SVG 超过大小预算")
        if re.search(r"<!DOCTYPE|<!ENTITY|<\?", source, re.I):
            raise ValueError("SVG 包含不允许的 XML 声明")
        parser = ET.XMLPullParser(events=('start', 'end'))
        depth, offset, nodes = 0, 0, 0
        root = None
        for boundary in re.finditer('>', source):
            end = boundary.end()
            parser.feed(source[offset:end])
            offset = end
            for kind, element in parser.read_events():
                if kind == 'start':
                    depth += 1
                    nodes += 1
                    if nodes > 20000 or depth > 128:
                        raise ValueError("SVG 结构超过复杂度预算")
                else:
                    depth -= 1
                    if depth == 0:
                        root, svg = element, source[:end]
            if root is not None:
                break
        if root is None:
            raise ValueError("未找到完整 SVG")
        if root.tag not in ("svg", "{http://www.w3.org/2000/svg}svg"):
            raise ValueError("SVG 根元素命名空间不正确")
        hidden = set()
        for el in root.iter():
            style = el.get('style', '')
            invisible = (el in hidden or el.get('display') == 'none' or el.get('visibility') in ('hidden', 'collapse')
                         or el.get('opacity') == '0' or re.search(r'(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|opacity\s*:\s*0(?:[;\s]|$))', style, re.I))
            if invisible:
                hidden.update(el.iter())
            tag = el.tag.split("}")[-1].lower()
            if tag in ("script", "foreignobject", "iframe", "object", "embed", "image", "audio", "video", "a"):
                raise ValueError("SVG 包含脚本、外部资源或不安全元素")
            for attr, value in el.attrib.items():
                name = attr.split("}")[-1].lower()
                if name.startswith("on") or (name in ("href", "src") and not value.startswith("#")):
                    raise ValueError("SVG 包含事件或外部引用")
                if name == "attributename" and (value.lower().startswith("on") or value.lower() in ("href", "xlink:href", "src")):
                    raise ValueError("SVG 动画不允许修改事件或资源引用")
            if tag == "text" and not invisible and nonce in "".join(el.itertext()):
                checks["nonce"] = True
            if tag in ("animate", "animatetransform", "animatemotion"):
                checks["animation"] = True
        refs = re.findall(r"url\((.*?)\)", svg, re.I | re.S)
        if re.search(r"@import|javascript:|expression\s*\(", svg, re.I) or any(not ref.strip().strip("\"'").startswith("#") for ref in refs):
            raise ValueError("SVG 包含外部或不安全样式")
        if re.search(r"@keyframes\s", svg) and re.search(r"animation(?:-name)?\s*:", svg):
            checks["animation"] = True
        checks["svg"] = True
        if not root.get("xmlns") and root.tag == "svg":
            root.set("xmlns", "http://www.w3.org/2000/svg")
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        svg = ET.tostring(root, encoding="unicode")
    except (ET.ParseError, ValueError) as exc:
        return "", checks, str(exc)
    missing = []
    if not checks["animation"]:
        missing.append("未发现动画声明")
    if not checks["nonce"]:
        missing.append("校验码缺失或不一致")
    return svg, checks, "；".join(missing)


def perform_test(config, prompt, nonce, model_call, kind="pelican", on_progress=None):
    started = time.time()
    attempts_log = []
    output, svg, checks, usage, returned_model = "", "", {}, {}, ""
    review, error_code, stage = None, None, 'generation'
    config = dict(config, _nonce=nonce)
    if '_deadline' not in config:
        config['_deadline'] = time.monotonic() + config['timeout_seconds'] + 45 + config.get('review_timeout_seconds',240)
    def snapshot(status, message='', current_stage=None):
        safe_usage = {k:v for k,v in usage.items() if k in ('input_tokens','output_tokens','total_tokens','prompt_tokens','completion_tokens') and type(v) is int} if isinstance(usage,dict) else {}
        for key,value in (review or {}).get('usage',{}).items():
            safe_usage[key] = safe_usage.get(key,0)+value
        return dict(status=status, started=started, finished=None if status=='running' else time.time(),
            attempts=len(attempts_log), attempts_log=list(attempts_log), output=output, svg=svg,
            checks=checks, error=redact(redact(message,config),config.get('_judge',{}))[:1000], error_code=error_code,
            stage=current_stage or stage, usage=safe_usage, returned_model=redact(returned_model,config)[:200],
            review=review, evaluation_level='basic' if config.get('_guest') else 'visual', scoring_version=3)
    def evidence_progress(metadata):
        nonlocal review
        review = {'status':'running','checks':{},'render':metadata,'version':REVIEW_VERSION}
        if on_progress:
            on_progress(snapshot('running',current_stage='review'))
    config['_evidence_progress'] = evidence_progress
    try:
        output, usage, returned_model = request_with_retries(config,prompt,model_call,'generation',attempts_log)
        output = redact(output,config)
        stage = 'validation'
        if kind == 'candy':
            checks = {'answer_21':candy_passes(output)}
            message = '' if checks['answer_21'] else '最终答案不是明确的 21，或未按要求提供最终答案'
        else:
            svg,checks,message = inspect_svg(output,nonce)
        status = 'passed' if all(checks.values()) else 'invalid'
        if kind == 'pelican' and status == 'passed' and not config.get('_guest'):
            stage = 'render'
            if on_progress:
                on_progress(snapshot('running'))
            try:
                review = review_pelican(config,svg,model_call)
                stage, status = 'review', review['status']
                message = '' if status == 'passed' else review['reason']
                if 'returned_model' in review:
                    review['returned_model'] = redact(redact(review['returned_model'],config),config.get('_judge',{}))[:200]
                if 'judge_model' in review:
                    review['judge_model'] = redact(redact(review['judge_model'],config),config.get('_judge',{}))[:200]
            except Exception as exc:
                failure = diagnose(exc,'review')
                stage, error_code = failure.stage, failure.code
                message = str(failure)
                details = dict(review or {})
                details.update(getattr(exc,'review_details',{}))
                review = dict(details, status='error',checks={},reason=message,
                              error_code=error_code, stage=stage, version=REVIEW_VERSION)
                status = 'error'
    except Exception as exc:
        failure = diagnose(exc,stage)
        stage,error_code,message,status = failure.stage,failure.code,str(failure),'error'
    return snapshot(status,message)


def guest_error(message):
    # Only publish fixed diagnoses, never arbitrary exception text or upstream bodies.
    if match := re.match(r"HTTP (\d{3})：", message):
        reasons = {401:"API Key 无效或已过期", 403:"接口拒绝访问，请检查 Key 权限或网关限制",
                   404:"接口不存在，请检查地址和协议", 429:"请求限流或额度不足", 524:"上游网关响应超时"}
        code = int(match[1])
        return f"HTTP {code}：{reasons.get(code, '上游接口请求失败')}"
    for marker, text in (("域名解析失败", "API 域名解析失败，请检查地址拼写或 DNS"),
                         ("证书校验失败", "API HTTPS 证书校验失败，请检查接口证书"),
                         ("本机、内网或保留地址", "访客检测仅支持公网地址，不支持本机、内网或保留地址"),
                         ("超时", "上游响应超时，请稍后重试或检查接口服务"),
                         ("鉴权失败", "API Key 鉴权失败，请检查密钥"),
                         ("额度不足", "上游额度不足，请检查账户余额或限额"),
                         ("限流", "上游请求限流，请稍后重试"),
                         ("HTML/XML", "接口返回网页，请检查 API 地址或网关状态"),
                         ("无效 JSON", "接口返回格式错误，请检查地址和协议"),
                         ("未完成", "模型输出未完成，可能达到输出上限或被中断"),
                         ("缺少完整结果", "上游流式响应中断，未收到完整结果"),
                         ("空响应", "上游未返回内容，请检查接口服务")):
        if marker in message:
            return text
    return "API 连接或生成失败，请检查接口服务、网络和协议"


def combine_tests(results):
    statuses = [r["status"] for r in results.values()]
    status = next((s for s in ("running", "error", "invalid", "uncertain") if s in statuses), "passed")
    pelican = results.get("pelican", {})
    usage = {}
    for result in results.values():
        for key, value in result.get("usage", {}).items():
            usage[key] = usage.get(key, 0) + value
    return dict(status=status, finished=None if status == "running" else max(r["finished"] for r in results.values()),
                output=pelican.get("output", ""), svg=pelican.get("svg", ""), checks=pelican.get("checks", {}),
                error="；".join(("鹈鹕：" if name == "pelican" else "糖果：") + r["error"] for name, r in results.items() if r.get("error")),
                usage=usage, returned_model=pelican.get("returned_model", results.get("candy", {}).get("returned_model", "")),
                tests={name: {k: v for k, v in r.items() if k != "svg" and not (name == "pelican" and k == "output")} for name, r in results.items()})


def perform_suite(config, prompt, nonce, model_call, test_type="both", on_result=None):
    kinds = ('pelican','candy') if test_type == 'both' else (test_type,)
    results = {name:{'status':'running'} for name in kinds}
    progress_lock = threading.RLock()
    def progress(name, value):
        with progress_lock:
            results[name] = value
            result = combine_tests(results)
            if on_result:
                on_result(result)
            return result
    with ThreadPoolExecutor(max_workers=len(kinds)) as pool:
        tasks = {pool.submit(perform_test,config,CANDY_PROMPT if name=='candy' else prompt,nonce,model_call,name,
                             lambda value, name=name:progress(name,value)):name for name in kinds}
        for future in as_completed(tasks):
            result = progress(tasks[future],future.result())
    return result


class Monitor:
    def __init__(self, directory, model_call=call_model):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "monitor.sqlite3"
        self.model_call = model_call
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.guest_queue = queue.Queue(maxsize=20)
        self.guest_workers = []
        self.sessions = {}
        self.login_failures = []
        self.workers = {}
        self.pending_failures = {}
        self.pending_results = {}
        self.result_lock = threading.RLock()
        self.result_directory = directory / "pending-results"
        self.result_directory.mkdir(exist_ok=True,mode=0o700)
        self.scheduler_heartbeat = time.monotonic()
        self.render_health = {'status':'pending', 'checked_at':None}
        self.render_probe = None
        self.provider_probe_slot = threading.BoundedSemaphore(1)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS nodes (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE CHECK(length(name) BETWEEN 1 AND 60),
                    base_url TEXT NOT NULL, api_key TEXT NOT NULL, model TEXT NOT NULL,
                    effort TEXT NOT NULL, protocol TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)));
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_node ON nodes(active) WHERE active=1;
                CREATE TABLE IF NOT EXISTS review_providers (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE CHECK(length(name) BETWEEN 1 AND 60),
                    base_url TEXT NOT NULL, api_key TEXT NOT NULL, model TEXT NOT NULL,
                    effort TEXT NOT NULL, protocol TEXT NOT NULL, token_field TEXT NOT NULL DEFAULT 'max_tokens',
                    verification TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, started REAL NOT NULL, finished REAL,
                    status TEXT NOT NULL, source TEXT NOT NULL, model TEXT NOT NULL,
                    base_url TEXT NOT NULL, effort TEXT NOT NULL, protocol TEXT NOT NULL,
                    scene TEXT NOT NULL, nonce TEXT NOT NULL, prompt TEXT NOT NULL,
                    output TEXT DEFAULT '', svg TEXT DEFAULT '', checks TEXT DEFAULT '{}',
                    error TEXT DEFAULT '', usage TEXT DEFAULT '{}', returned_model TEXT DEFAULT '');
                CREATE INDEX IF NOT EXISTS runs_started ON runs(started);
                CREATE TABLE IF NOT EXISTS admin_auth (id INTEGER PRIMARY KEY CHECK(id=1), salt TEXT NOT NULL, password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS guest_results (id TEXT PRIMARY KEY, created REAL NOT NULL, result TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS guest_created ON guest_results(created);
            """)
            # Credentials are deliberately memory-only; restart never silently retries a billed request.
            for row in db.execute("SELECT id,result FROM guest_results WHERE json_extract(result,'$.status') IN ('queued','running')").fetchall():
                result = json.loads(row["result"])
                self.fail_guest(result, "服务重启，访客任务已中断，请重新提交")
                db.execute("UPDATE guest_results SET created=?,result=? WHERE id=?", (time.time(), json.dumps(result), row["id"]))
            columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
            if 'lease_until' not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN lease_until REAL")
            if "test_version" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN test_version INTEGER NOT NULL DEFAULT 1")
            if "tests" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN tests TEXT NOT NULL DEFAULT '{}'")
            if "node_id" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN node_id INTEGER REFERENCES nodes(id)")
                db.execute("ALTER TABLE runs ADD COLUMN node_name TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS runs_node ON runs(node_id,id)")
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (json.dumps(DEFAULTS),))
            config = dict(DEFAULTS, **json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0]))
            if not db.execute("SELECT 1 FROM nodes").fetchone():
                db.execute("INSERT INTO nodes(name,base_url,api_key,model,effort,protocol,active) VALUES (?,?,?,?,?,?,1)",
                           ("默认节点", *(config[key] for key in NODE_FIELDS)))
            self.migrate_judge_node(db, config)
            self.store_settings(db, config)
            # Replay durable final results before marking interrupted workers as errors.
            restored = []
            for path in self.result_directory.glob('*.json'):
                if not path.stem.isdigit() or path.is_symlink() or path.stat().st_size > 8*1024*1024:
                    continue
                try:
                    result = json.loads(path.read_text())
                    if result['status'] not in ('passed','invalid','uncertain','error'):
                        continue
                    self.write_result(db,int(path.stem),result)
                    restored.append(path)
                except (KeyError,ValueError,TypeError):
                    event('persist','invalid_result_journal')
            for row in db.execute("SELECT id,tests FROM runs WHERE status='running'").fetchall():
                tests = json.loads(row["tests"])
                for test in tests.values():
                    if test["status"] == "running":
                        test.update(status="error", finished=time.time(), error="服务重启，本项检测中断")
                db.execute("UPDATE runs SET status='error', finished=?, error='服务重启，上一轮检测中断；未自动重试',tests=? WHERE id=?", (time.time(), json.dumps(tests), row["id"]))
            self.migrate_review_policy(db)
        with contextlib.closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute('PRAGMA journal_mode=WAL')
        os.chmod(self.path, 0o600)
        for path in restored:
            path.unlink(missing_ok=True)

    @staticmethod
    def migrate_review_policy(db):
        changed = 0
        for row in db.execute("SELECT id,tests FROM runs WHERE status!='running' AND test_version=3").fetchall():
            tests = json.loads(row['tests'])
            pelican = tests.get('pelican',{})
            if pelican.get('status') not in ('passed','invalid','uncertain'):
                continue
            revised = reassess_legacy_review(pelican.get('review'))
            if revised is None:
                continue
            pelican.update(review=revised,status=revised['status'],stage='review',error_code=None,
                           error='' if revised['status']=='passed' else revised['reason'])
            statuses = [test['status'] for test in tests.values()]
            status = next((value for value in ('running','error','invalid','uncertain') if value in statuses),'passed')
            error = '；'.join(('鹈鹕：' if name=='pelican' else '糖果：')+test['error']
                             for name,test in tests.items() if test.get('error'))
            db.execute('UPDATE runs SET status=?,error=?,tests=? WHERE id=?',
                       (status,error,json.dumps(tests),row['id']))
            changed += 1
        if changed:
            event('review','policy_reclassified',count=changed,version=REVIEW_VERSION)
        return changed

    def password_configured(self):
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM admin_auth WHERE id=1").fetchone())

    def setup_password(self, password):
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError("管理密码需要 12–256 个字符")
        with self.lock, self.db() as db:
            if db.execute("SELECT 1 FROM admin_auth WHERE id=1").fetchone():
                raise ValueError("管理密码已经设置，请使用密码登录")
            salt = secrets.token_hex(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
            db.execute("INSERT INTO admin_auth VALUES (1,?,?)", (salt,digest))

    def login(self, password):
        with self.lock:
            now = time.time()
            self.login_failures = [at for at in self.login_failures if at > now-60]
            if len(self.login_failures) >= 8:
                raise ValueError("登录失败次数过多，请 1 分钟后重试")
            with self.db() as db:
                auth = db.execute("SELECT salt,password_hash FROM admin_auth WHERE id=1").fetchone()
            valid = False
            if auth and isinstance(password,str) and len(password) <= 256:
                digest = hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(auth["salt"]),600000).hex()
                valid = hmac.compare_digest(digest, auth["password_hash"])
            if not valid:
                self.login_failures.append(now)
                raise ValueError("管理密码不正确")
            self.login_failures.clear()
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now+7200
            return token

    def authenticated(self, token):
        with self.lock:
            expiry = self.sessions.get(token,0)
            if expiry <= time.time():
                self.sessions.pop(token,None)
                return False
            return True

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token,None)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=3000")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self, public=False):
        with self.db() as db:
            config = dict(DEFAULTS, **json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0]))
            node = db.execute("SELECT * FROM nodes WHERE active=1").fetchone()
            config.update({key: node[key] for key in NODE_FIELDS})
            config.update(active_node_id=node["id"], node_name=redact(node["name"], config))
        if public:
            key = config.pop("api_key")
            config["has_key"] = bool(key)
            config["api_key_masked"] = mask(key) if key else "尚未配置"
            config["base_url"] = mask_url(config["base_url"])
        return config

    @staticmethod
    def migrate_judge_node(db, config):
        """Copy the legacy judge once; generation nodes remain untouched."""
        if config.get('judge_node_id') is None:
            return
        node = db.execute('SELECT * FROM nodes WHERE id=?', (config['judge_node_id'],)).fetchone()
        if not node or not node['api_key']:
            raise ValueError('请选择已配置密钥的审核节点')
        name = f"原审核节点 {node['id']} · {node['name']}"[:60]
        saved = db.execute('SELECT id FROM review_providers WHERE name=?', (name,)).fetchone()
        if saved:
            provider_id = saved['id']
        else:
            provider_id = db.execute('INSERT INTO review_providers(name,base_url,api_key,model,effort,protocol,token_field) VALUES (?,?,?,?,?,?,?)',
                (name, *(node[key] for key in NODE_FIELDS), 'max_completion_tokens')).lastrowid
        config.update(review_mode='external', review_provider_id=provider_id, judge_node_id=None)

    @staticmethod
    def resolve_judge(db, config):
        config.pop('_judge', None)
        if config.get('review_mode', 'self') == 'external':
            judge = db.execute('SELECT * FROM review_providers WHERE id=?', (config.get('review_provider_id'),)).fetchone()
            if not judge or not judge['api_key']:
                raise ValueError('请选择已配置密钥的外部审核提供商')
            config['_judge'] = {key: judge[key] for key in PROVIDER_FIELDS}
            config['_judge']['provider_id'] = judge['id']

    def review_providers(self):
        with self.db() as db:
            rows = db.execute('SELECT * FROM review_providers ORDER BY id').fetchall()
        return [dict(id=row['id'], name=redact(row['name'], dict(row)), base_url=mask_url(row['base_url']),
                     has_key=bool(row['api_key']), api_key_masked=mask(row['api_key']) if row['api_key'] else '尚未配置',
                     model=redact(row['model'], dict(row)), effort=row['effort'], protocol=row['protocol'],
                     token_field=row['token_field'], verification=json.loads(row['verification'])) for row in rows]

    def save_review_provider(self, values, provider_id=None):
        if not isinstance(values, dict) or set(values) - {'name', *PROVIDER_FIELDS}:
            raise ValueError('审核提供商字段不正确')
        name = values.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
            raise ValueError('请输入 1–60 字符的提供商名称')
        with self.lock, self.db() as db:
            old = db.execute('SELECT * FROM review_providers WHERE id=?', (provider_id,)).fetchone() if provider_id else None
            if provider_id is not None and not old:
                raise ValueError('审核提供商不存在')
            if old is None and any(not isinstance(values.get(k), str) or not values[k].strip() for k in ('base_url', 'api_key', 'model')):
                raise ValueError('新增审核提供商需填写 API 地址、API Key 和模型名称')
            effort = values.get('effort', old['effort'] if old else 'omit')
            token_field = values.get('token_field', old['token_field'] if old else 'max_tokens')
            if effort not in ('omit', 'low', 'medium', 'high', 'xhigh') or token_field not in ('max_tokens', 'max_completion_tokens'):
                raise ValueError('审核提供商的思考强度或 Token 参数无效')
            base = dict(DEFAULTS, **({k: old[k] for k in NODE_FIELDS} if old else {}))
            base['effort'] = 'medium'
            config = validate_settings(dict(values, effort='medium' if effort == 'omit' else effort), base)
            config.update(effort=effort, token_field=token_field)
            fields = (name.strip(), *(config[k] for k in PROVIDER_FIELDS))
            try:
                if old:
                    db.execute("UPDATE review_providers SET name=?,base_url=?,api_key=?,model=?,effort=?,protocol=?,token_field=?,verification='{}' WHERE id=?", (*fields, provider_id))
                else:
                    provider_id = db.execute('INSERT INTO review_providers(name,base_url,api_key,model,effort,protocol,token_field) VALUES (?,?,?,?,?,?,?)', fields).lastrowid
            except sqlite3.IntegrityError:
                raise ValueError('审核提供商名称已存在') from None
        return next(row for row in self.review_providers() if row['id'] == provider_id)

    def probe_review_provider(self, provider_id):
        from provider_probe import probe
        if not self.provider_probe_slot.acquire(blocking=False):
            raise ValueError('已有提供商正在验证，请稍后再试')
        try:
            with self.db() as db:
                row = db.execute('SELECT * FROM review_providers WHERE id=?', (provider_id,)).fetchone()
            if not row:
                raise ValueError('审核提供商不存在')
            config = dict(DEFAULTS, **{key: row[key] for key in PROVIDER_FIELDS},
                          timeout_seconds=60, max_output_tokens=1500, retry_count=0, _deadline=time.monotonic()+60)
            try:
                passed = probe(config, self.model_call)
                result = dict(status='passed' if passed else 'failed', checked_at=time.time(),
                              message='图片读取验证通过' if passed else '图片内容未识别正确，请确认模型支持图片输入')
            except Exception as exc:
                failure = diagnose(exc, 'review')
                result = dict(status='error', checked_at=time.time(), error_code=failure.code,
                              message=redact(str(failure), config)[:300])
            with self.lock, self.db() as db:
                current = db.execute('SELECT * FROM review_providers WHERE id=?', (provider_id,)).fetchone()
                if not current or any(current[key] != row[key] for key in PROVIDER_FIELDS):
                    raise ValueError('提供商配置已变更，请对新配置重新验证')
                db.execute('UPDATE review_providers SET verification=? WHERE id=?', (json.dumps(result), provider_id))
            return result
        finally:
            self.provider_probe_slot.release()

    @staticmethod
    def store_settings(db, config):
        global_config = {k:config[k] for k in DEFAULTS if k not in NODE_FIELDS}
        db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(global_config),))

    def nodes(self):
        with self.db() as db:
            rows = db.execute("SELECT * FROM nodes ORDER BY id").fetchall()
            latest = {r["node_id"]:dict(r) for r in db.execute("SELECT id,node_id,status,started,finished,error FROM runs WHERE id IN (SELECT MAX(id) FROM runs WHERE node_id IS NOT NULL GROUP BY node_id)")}
        return [dict(id=row["id"], name=redact(row["name"], dict(row)), active=bool(row["active"]),
                     base_url=mask_url(row["base_url"]), has_key=bool(row["api_key"]),
                     api_key_masked=mask(row["api_key"]) if row["api_key"] else "尚未配置",
                     model=redact(row["model"], dict(row)), effort=row["effort"], protocol=row["protocol"],
                     last_run=latest.get(row["id"])) for row in rows]

    def save_node(self, values, node_id=None):
        if not isinstance(values, dict) or set(values) - {"name", *NODE_FIELDS}:
            raise ValueError("节点字段不正确")
        name = values.get("name", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c)<32 for c in name):
            raise ValueError("请输入 1–60 字符的节点名称")
        with self.lock, self.db() as db:
            old = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone() if node_id is not None else None
            if node_id is not None and not old:
                raise ValueError("节点不存在")
            if old is None and any(not isinstance(values.get(k), str) or not values[k].strip() for k in ("base_url", "api_key")):
                raise ValueError("新增节点需填写 API 地址和 API Key")
            config = validate_settings(values, dict(DEFAULTS, **({k:old[k] for k in NODE_FIELDS} if old else {})))
            fields = (name.strip(), *(config[k] for k in NODE_FIELDS))
            try:
                if old:
                    db.execute("UPDATE nodes SET name=?,base_url=?,api_key=?,model=?,effort=?,protocol=? WHERE id=?", (*fields,node_id))
                else:
                    node_id = db.execute("INSERT INTO nodes(name,base_url,api_key,model,effort,protocol) VALUES (?,?,?,?,?,?)", fields).lastrowid
            except sqlite3.IntegrityError:
                raise ValueError("节点名称已存在，请使用其他名称") from None
        return next(n for n in self.nodes() if n["id"] == node_id)

    def activate_node(self, node_id):
        with self.lock, self.db() as db:
            node = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not node:
                raise ValueError("节点不存在")
            if not node["api_key"]:
                raise ValueError("请先为该节点配置 API Key")
            db.execute("UPDATE nodes SET active=0 WHERE active=1")
            db.execute("UPDATE nodes SET active=1 WHERE id=?", (node_id,))
        return self.settings(public=True)

    def save(self, values):
        with self.lock:
            config = validate_settings(values, self.settings())
            with self.db() as db:
                self.migrate_judge_node(db, config)
                self.resolve_judge(db, config)
                db.execute("UPDATE nodes SET base_url=?,api_key=?,model=?,effort=?,protocol=? WHERE active=1", tuple(config[k] for k in NODE_FIELDS))
                self.store_settings(db, config)
        return self.settings(public=True)

    def start_run(self, source="manual", now=None, node_id=None):
        now = time.time() if now is None else now
        self.recover_runs()
        with self.lock, self.db() as db:
            config = self.settings()
            self.resolve_judge(db, config)
            if node_id is not None:
                if source != "manual":
                    raise ValueError("指定节点仅支持手动测试")
                node = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
                if not node:
                    raise ValueError("节点不存在")
                config.update({key:node[key] for key in NODE_FIELDS})
                config.update(active_node_id=node["id"], node_name=redact(node["name"], config))
            if not config["api_key"]:
                raise ValueError("请先在检测设置中填写 API Key")
            if source == "scheduled" and (not config["enabled"] or (config["next_run"] or float("inf")) > now):
                return None
            if db.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():
                if source == "scheduled":
                    return None
                raise ValueError("已有检测正在运行，请等待完成")
            recent = {row[0] for row in db.execute("SELECT scene FROM runs WHERE node_id=? ORDER BY id DESC LIMIT 3", (config["active_node_id"],))}
            scene = secrets.choice(tuple(name for name in SCENES if name not in recent))
            nonce = secrets.token_hex(4).upper()
            prompt = build_pelican_prompt(scene, nonce)
            row = db.execute("""INSERT INTO runs (started,status,source,model,base_url,effort,protocol,scene,nonce,prompt,test_version,tests,node_id,node_name)
                VALUES (?,'running',?,?,?,?,?,?,?,?,3,?,?,?)""", (now, source, config["model"], config["base_url"], config["effort"], config["protocol"], scene, nonce, prompt, json.dumps({"pelican":{"status":"running"},"candy":{"status":"running"}}),config["active_node_id"],config["node_name"]))
            run_id = row.lastrowid
            budget = config['timeout_seconds'] + 45 + config.get('review_timeout_seconds',240)
            config.update(_deadline=time.monotonic()+budget, _cancel=threading.Event(), _scene=scene)
            db.execute('UPDATE runs SET lease_until=? WHERE id=?',(time.time()+budget+5,run_id))
            if source == "scheduled":
                config["next_run"] = next_slot(now, config["interval_minutes"] * 60)
                self.store_settings(db, config)
        with self.lock:
            worker = threading.Thread(target=self.execute, args=(run_id, config, prompt, nonce), daemon=True)
            self.workers[run_id] = (worker, config['_cancel'])
            try:
                worker.start()
            except Exception:
                self.workers.pop(run_id,None)
                self.fail_run(run_id,'worker_start','无法启动检测线程，请检查进程配额')
                raise ValueError('无法启动检测线程，请检查进程配额') from None
        return run_id

    def fail_run(self, run_id, code, message):
        # A failed write is retried by the scheduler; do not lose the recovery intent.
        self.pending_failures[run_id] = (code,message)
        try:
            with self.db() as db:
                row = db.execute("SELECT tests FROM runs WHERE id=? AND status='running'",(run_id,)).fetchone()
                if row:
                    tests = json.loads(row['tests'])
                    now = time.time()
                    for test in tests.values():
                        if test['status'] == 'running':
                            test.update(status='error',finished=now,error=message,error_code=code)
                    db.execute("UPDATE runs SET status='error',finished=?,error=?,tests=?,lease_until=NULL WHERE id=? AND status='running'",
                               (now,message,json.dumps(tests),run_id))
            self.pending_failures.pop(run_id,None)
        except sqlite3.Error:
            event('persist','recovery_pending',run_id=run_id)

    def recover_runs(self):
        with self.lock:
            with self.result_lock:
                for run_id,result in list(self.pending_results.items()):
                    self.persist_result(run_id,result)
            for run_id,(code,message) in list(self.pending_failures.items()):
                self.fail_run(run_id,code,message)
            with self.db() as db:
                rows = db.execute("SELECT id,started,lease_until FROM runs WHERE status='running'").fetchall()
            for row in rows:
                if row['id'] in self.pending_results and self.pending_results[row['id']]['status'] != 'running':
                    continue
                item = self.workers.get(row['id'])
                if row['lease_until'] and row['lease_until'] <= time.time():
                    if item:
                        item[1].set()
                    self.fail_run(row['id'],'task_deadline','检测超过整轮时间预算，已有结果已保留')
                elif (not item and row['started'] < time.time()-10) or (item and not item[0].is_alive()):
                    self.fail_run(row['id'],'worker_lost','检测线程已退出，已有结果已保留')
            self.workers = {key:value for key,value in self.workers.items() if value[0].is_alive()}

    @staticmethod
    def write_result(db, run_id, result):
        db.execute("UPDATE runs SET status=?,finished=?,output=?,svg=?,checks=?,error=?,usage=?,returned_model=?,tests=? WHERE id=? AND status='running'",
                   (result['status'],result['finished'],result['output'],result['svg'],json.dumps(result['checks']),
                    result['error'],json.dumps(result['usage']),result['returned_model'],json.dumps(result['tests']),run_id))

    def persist_result(self, run_id, result):
        # Progress snapshots are immutable. The final verdict is journaled before the DB write.
        with self.result_lock:
            self.pending_results[run_id] = result
            journal = self.result_directory / f'{run_id}.json'
            if result['status'] != 'running':
                try:
                    atomic_write(journal,json.dumps(result,ensure_ascii=False).encode())
                except OSError:
                    event('persist','journal_unavailable',run_id=run_id)
            try:
                with self.db() as db:
                    self.write_result(db,run_id,result)
            except sqlite3.Error:
                event('persist','result_pending',run_id=run_id)
                return
            self.pending_results.pop(run_id,None)
            try:
                journal.unlink(missing_ok=True)
            except OSError:
                event('persist','journal_cleanup_pending',run_id=run_id)

    def execute(self, run_id, config, prompt, nonce):
        def persist(result):
            pelican = result.get('tests',{}).get('pelican',{})
            if (pelican.get('stage') == 'render' and pelican.get('status') == 'error'
                    and pelican.get('error_code') in ('render_resources','browser_missing','render_process')):
                self.render_health = {'status':'error','checked_at':time.time(),'error_code':pelican.get('error_code')}
            self.persist_result(run_id,result)
        def evidence(svg, frames, metadata):
            try:
                with self.db() as db:
                    protected = [row[0] for row in db.execute("SELECT id FROM runs WHERE status='running'")]
                prune_evidence(self.path.parent,protected,reserve=24*1024*1024)
                manifest = save_evidence(self.path.parent,run_id,svg,frames,metadata)
                self.render_health = {'status':'passed','checked_at':time.time()}
                return dict(manifest, run_id=run_id)
            except Exception:
                raise StageError('persist','evidence_write','审核截图保存失败，请检查磁盘空间和权限') from None
        def receipt(text, metadata):
            safe_text = redact(redact(text,config),config.get('_judge',{}))
            root = self.path.parent / 'artifacts' / str(run_id)
            atomic_write(root / f"review-{metadata['attempt']}.json",
                         json.dumps(dict(metadata,text=safe_text),ensure_ascii=False).encode())
        config = dict(config,_save_evidence=evidence,_save_review_receipt=receipt)
        try:
            perform_suite(config,prompt,nonce,self.model_call,on_result=persist)
        except Exception as exc:
            event('task','execution_failed',run_id=run_id,exception=type(exc).__name__)
            with self.lock:
                self.fail_run(run_id,'execution_failed','检测执行或保存异常，已有结果已保留')

    def probe_renderer(self):
        from visual_review import render_evidence
        try:
            render_evidence('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60"><circle cx="30" cy="30" r="10"/></svg>',30)
            self.render_health = {'status':'passed','checked_at':time.time()}
        except Exception as exc:
            self.render_health = {'status':'error','checked_at':time.time(),'error_code':diagnose(exc,'render').code}

    def health(self):
        try:
            with self.db() as db:
                db.execute('SELECT 1').fetchone()
            database = True
        except sqlite3.Error:
            database = False
        scheduler = time.monotonic()-self.scheduler_heartbeat < 30
        return dict(ready=database and scheduler and self.render_health['status']=='passed' and not self.pending_failures and not self.pending_results,
                    database=database,scheduler=scheduler,renderer=self.render_health,
                    recovery_pending=len(self.pending_failures)+len(self.pending_results))

    def prepare_guest(self, values):
        if not self.settings()["guest_enabled"]:
            raise ValueError("访客检测暂未开放")
        allowed = {"base_url", "api_key", "model", "effort", "protocol", "test_type", "retry_count"}
        if set(values) - allowed:
            raise ValueError("访客请求包含不支持的字段")
        if any(not isinstance(values.get(k),str) or not values[k].strip() for k in ("base_url","api_key")):
            raise ValueError("请填写你自己的 API 地址和 API Key")
        config = validate_settings(values, DEFAULTS)
        test_type = values.get("test_type", "both")
        if test_type not in ("both", "pelican", "candy"):
            raise ValueError("请选择有效的检测项目")
        if parse.urlsplit(config["base_url"]).scheme != "https":
            raise ValueError("访客检测仅支持公网 HTTPS 地址")
        config["_guest"] = True
        scene, nonce = secrets.choice(SCENES), secrets.token_hex(4).upper()
        kinds = ("pelican", "candy") if test_type == "both" else (test_type,)
        host = parse.urlsplit(config["base_url"]).hostname or ""
        published = dict(id=secrets.token_hex(12), status="queued", submitted=time.time(), started=None, finished=None,
                         scene=scene, model=redact(config["model"], config), effort=config["effort"], test_type=test_type,
                         api_masked=mask(host), checks={}, svg="", tests={name:{"status":"queued"} for name in kinds})
        return config, nonce, published

    def submit_guest(self, values):
        config, nonce, published = self.prepare_guest(values)
        with self.lock:
            if self.guest_queue.full():
                raise queue.Full()
            if not self.guest_workers:
                for _ in range(4):
                    worker = threading.Thread(target=self.guest_worker, daemon=True)
                    worker.start()
                    self.guest_workers.append(worker)
            with self.db() as db:
                db.execute("INSERT INTO guest_results VALUES (?,?,?)", (published["id"], published["submitted"], json.dumps(published)))
            accepted = self.guest_view(published)
            self.guest_queue.put_nowait((config, nonce, published))
        return accepted

    def guest_worker(self):
        while not self.stopped.is_set():
            try:
                config, nonce, published = self.guest_queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                self.execute_guest(config, nonce, published)
            except Exception:
                self.fail_guest(published, "访客任务执行异常，请重新提交")
                try:
                    self.store_guest(published)
                except Exception:
                    print("Guest task persistence failed", flush=True)
            finally:
                config.clear()
                self.guest_queue.task_done()

    @staticmethod
    def fail_guest(published, reason):
        published.update(status="error", finished=time.time())
        for test in published["tests"].values():
            if test["status"] in ("queued", "running"):
                test.update(status="error", finished=published["finished"], error=reason)

    def store_guest(self, published):
        terminal = published["status"] not in ("queued", "running")
        with self.db() as db:
            db.execute("UPDATE guest_results SET created=?,result=? WHERE id=?",
                       (published["finished"] if terminal else published["submitted"], json.dumps(published), published["id"]))

    def execute_guest(self, config, nonce, published):
        if time.time()-published['submitted'] > 600:
            self.fail_guest(published,'访客任务排队超过 10 分钟，请重新提交')
            self.store_guest(published)
            return self.guest_view(published)
        config.update(_cancel=self.stopped, _deadline=time.monotonic()+config['timeout_seconds'])
        published.update(started=time.time(), status="running")
        published["tests"] = {name:{"status":"running"} for name in published["tests"]}
        self.store_guest(published)
        def persist(result):
            published.update({key: result[key] for key in ("status", "finished", "checks", "svg")})
            # Explicit public whitelist: no full address, key, raw upstream error or visual review.
            for name, test in result["tests"].items():
                public_test = {key: test[key] for key in ("status", "checks", "started", "finished", "attempts", "scoring_version", "evaluation_level") if key in test}
                if name == "candy":
                    public_test["output"] = test.get("output", "")
                if test.get("error"):
                    public_test["error"] = guest_error(test["error"]) if test["status"] == "error" else ("最终答案不是明确的 21" if name == "candy" else "SVG 基础校验未通过")
                published["tests"][name] = public_test
            self.store_guest(published)
        perform_suite(config, build_pelican_prompt(published["scene"], nonce), nonce, self.model_call,
                      published["test_type"], on_result=persist)
        self.prune_guests()
        return self.guest_view(published)

    def guest_test(self, values):
        """Synchronous core for local checks; HTTP submissions always use the bounded queue."""
        config, nonce, published = self.prepare_guest(values)
        with self.db() as db:
            db.execute("INSERT INTO guest_results VALUES (?,?,?)",(published["id"],time.time(),json.dumps(published)))
        return self.execute_guest(config, nonce, published)

    def prune_guests(self):
        with self.db() as db:
            terminal = "json_extract(result,'$.status') NOT IN ('queued','running')"
            db.execute("DELETE FROM guest_results WHERE " + terminal + " AND created<?",(time.time()-3600,))
            db.execute("DELETE FROM guest_results WHERE " + terminal + " AND id NOT IN (SELECT id FROM guest_results WHERE " + terminal + " ORDER BY created DESC LIMIT 100)")

    @staticmethod
    def guest_view(result):
        public = dict(result)
        public["has_svg"] = bool(public.pop("svg"))
        public["tests"] = {name: {key:value for key,value in test.items() if key != "review"} for name,test in public.get("tests", {}).items()}
        public["error"] = "；".join(("鹈鹕：" if name == "pelican" else "糖果：") + test["error"] for name,test in public.get("tests",{}).items() if test.get("error"))
        return public

    def guest_results(self):
        with self.db() as db:
            rows = db.execute("SELECT result FROM guest_results WHERE created>=? OR json_extract(result,'$.status') IN ('queued','running') ORDER BY created DESC",(time.time()-3600,)).fetchall()
        results = [self.guest_view(json.loads(row[0])) for row in rows]
        queued = sorted((r for r in results if r['status'] == 'queued'), key=lambda r:(r['submitted'],r['id']))
        for position, result in enumerate(queued, 1):
            result['queue_position'] = position
        return results

    def scheduler(self):
        last_cleanup = 0
        while not self.stopped.wait(2):
            self.scheduler_heartbeat = time.monotonic()
            try:
                self.recover_runs()
                if not self.workers and (not self.render_probe or not self.render_probe.is_alive()) and (not self.render_health['checked_at'] or time.time()-self.render_health['checked_at'] > 300):
                    self.render_probe = threading.Thread(target=self.probe_renderer,daemon=True)
                    self.render_probe.start()
                if time.time()-last_cleanup > 60:
                    self.prune_guests()
                    with self.db() as db:
                        protected = [row[0] for row in db.execute("SELECT id FROM runs WHERE status='running'")]
                    prune_evidence(self.path.parent,protected)
                    last_cleanup = time.time()
                if self.settings()["enabled"]:
                    self.start_run("scheduled")
            except Exception as exc:
                print(f"Scheduler error: {type(exc).__name__}", flush=True)

    def runs(self, before=None):
        with self.db() as db:
            rows = db.execute("SELECT * FROM runs WHERE id < ? ORDER BY id DESC LIMIT 48", (before or 2**63-1,)).fetchall()
        return [self.serialize(row) for row in rows]

    def gallery(self, page=1, status="all", test="all"):
        if type(page) is not int or not 1 <= page <= 1000000 or status not in ("all","passed","invalid","error","uncertain","running","legacy"):
            raise ValueError("画廊分页或筛选参数无效")
        if test not in ("all","pelican","candy"):
            raise ValueError("检测项目无效")
        clauses, args = [], []
        if test != "all":
            path = '$.'+test+'.status'
            clauses.append("(test_version=1 OR json_extract(tests, ?) IS NOT NULL)" if test == 'pelican'
                           else "(test_version>=2 AND json_extract(tests, ?) IS NOT NULL AND json_extract(tests, ?)!='not_run')")
            args.extend([path] if test == 'pelican' else [path,path])
        if status == "legacy":
            clauses.append("test_version=1")
        elif status != "all":
            clauses.append("test_version>=2 AND status=?" if test == 'all'
                           else "test_version>=2 AND json_extract(tests, ?)=?")
            args.extend([status] if test == 'all' else ['$.'+test+'.status',status])
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.db() as db:
            total = db.execute("SELECT COUNT(*) FROM runs"+where,args).fetchone()[0]
            pages = max(1, math.ceil(total/12))
            page = min(page,pages)
            rows = db.execute("SELECT * FROM runs"+where+" ORDER BY id DESC LIMIT 12 OFFSET ?",[*args,(page-1)*12]).fetchall()
        return dict(items=[self.serialize(row) for row in rows],total=total,page=page,pages=pages,test=test)

    @staticmethod
    def serialize(row, detail=False):
        result = dict(row)
        result["has_svg"] = bool(result.pop("svg"))
        config = {"base_url": result["base_url"]}
        for key in ("output", "error", "returned_model", "model"):
            result[key] = redact(result[key], config)
        result["base_url"] = mask_url(result["base_url"])
        for key in ("checks", "usage", "tests"):
            result[key] = json.loads(result[key])
        result["candy_status"] = result["tests"].get("candy",{}).get("status","not_run") if result["test_version"] >= 2 else "not_run"
        if result["test_version"] == 1:
            result["tests"] = {"pelican": {"status": result["status"]}, "candy": {"status": "not_run"}}
            result["status"] = "legacy"
        for test in result["tests"].values():
            for key in ("output", "error", "returned_model"):
                if key in test:
                    test[key] = redact(test[key], config)
        if detail and result["test_version"] >= 2:
            result["candy_prompt"] = CANDY_PROMPT if result['test_version'] >= 3 else CANDY_PROMPT.split('最后一行请严格写成')[0].rstrip()
        if not detail:
            for key in ("output", "prompt"):
                result.pop(key)
            for test in result["tests"].values():
                test.pop("output", None)
        return result

    def state(self):
        now = time.time()
        with self.db() as db:
            active = db.execute('SELECT id FROM nodes WHERE active=1').fetchone()[0]
            rows = db.execute("SELECT * FROM runs WHERE started>=? AND node_id=? ORDER BY id DESC", (now-86400,active)).fetchall()
            running = db.execute("SELECT id FROM runs WHERE status='running'").fetchone()
        results = [self.serialize(r) for r in rows]
        current = [r for r in results if r["test_version"] >= 3 and r["candy_status"] != "not_run"]
        completed = [r for r in current if r["candy_status"] != "running"]
        settings = self.settings(public=True)
        public = {key: settings[key] for key in ("base_url", "model", "effort", "enabled", "next_run", "interval_minutes", "guest_enabled", "node_name", "active_node_id")}
        promotion = {key.removeprefix('promotion_'): settings[key] for key in
                     ('promotion_title', 'promotion_description', 'promotion_action', 'promotion_url')} if settings['promotion_enabled'] else None
        pelican_runs = [r for r in results if r['test_version'] >= 3 and r['tests'].get('pelican')]
        pelicans = [r['tests']['pelican'] for r in pelican_runs]
        visual = [t for t in pelicans if t.get('review')]
        visual_stats = dict(total=len(pelicans), generated=sum(r['has_svg'] for r in pelican_runs),
            reviewed=sum(t['review']['status'] in ('passed','invalid','uncertain') for t in visual),
            passed=sum(t['review']['status']=='passed' for t in visual),
            invalid=sum(t['review']['status']=='invalid' for t in visual),
            uncertain=sum(t['review']['status']=='uncertain' for t in visual),
            errors=sum(t.get('status')=='error' for t in pelicans))
        return dict(settings=public, promotion=promotion, server_time=now, visual_stats=visual_stats, scoring_version=3, review_policy_version=REVIEW_VERSION,
                    running=running[0] if running else None, candy_running=any(r["candy_status"]=="running" for r in current), timeline=results,
                    stats=dict(total=len(current), legacy=len(results)-len(current), passed=sum(r["candy_status"] == "passed" for r in completed),
                               completed=len(completed), errors=sum(r["candy_status"] == "error" for r in completed),
                               invalid=sum(r["candy_status"] == "invalid" for r in completed)))


class BoundedHTTPServer(ThreadingHTTPServer):
    request_queue_size = 32
    def __init__(self,*args,**kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args,**kwargs)

    def process_request(self, request, client_address):
        request.settimeout(15)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self,request,client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, content_type="application/json; charset=utf-8", status=200, svg=False):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        policy = "sandbox; default-src 'none'; style-src 'unsafe-inline'" if svg else "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        self.send_header("Content-Security-Policy", policy)
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return self.server.monitor.authenticated(self.headers.get("Authorization", "").removeprefix("Bearer "))

    def local_setup(self):
        host = parse.urlsplit("http://" + self.headers.get("Host", "")).hostname
        return (self.server.server_address[0] in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost", "::1")
                and self.client_address[0] in ("127.0.0.1", "::1") and not self.headers.get("Forwarded") and not self.headers.get("X-Forwarded-For"))

    def do_GET(self):
        url = parse.urlsplit(self.path)
        if url.path.startswith("/api/"):
            monitor = self.server.monitor
            if url.path == '/api/health':
                health = monitor.health()
                return self.send(health,status=200 if health['ready'] else 503)
            frame_match = re.fullmatch(r'/api/runs/([1-9]\d{0,17})/frames/([0-9]|1[0-5])',url.path)
            if frame_match:
                with monitor.db() as db:
                    exists = db.execute('SELECT 1 FROM runs WHERE id=?',(int(frame_match[1]),)).fetchone()
                path = monitor.path.parent / 'artifacts' / frame_match[1] / (frame_match[2]+'.png')
                if exists:
                    try:
                        png = path.read_bytes()
                    except FileNotFoundError:
                        pass  # Retention cleanup may remove a frame during this request.
                    else:
                        return self.send(png,'image/png')
                return self.send({'error':'截图证据不存在'},status=404)
            if url.path == "/api/auth/status":
                return self.send({"configured": monitor.password_configured(), "setup_allowed": self.local_setup()})
            if url.path == "/api/guest/results":
                return self.send(monitor.guest_results())
            guest_match = re.fullmatch(r"/api/guest/results/([0-9a-f]{24})(/svg)?",url.path)
            if guest_match:
                with monitor.db() as db:
                    row = db.execute("SELECT result FROM guest_results WHERE id=? AND (created>=? OR json_extract(result,'$.status') IN ('queued','running'))",(guest_match[1],time.time()-3600)).fetchone()
                if row:
                    result = json.loads(row[0])
                    if not guest_match[2]:
                        return self.send(monitor.guest_view(result))
                    if svg := result.get("svg"):
                        if parse.parse_qs(url.query).get('display')==['1']:svg=display_svg(svg)
                        return self.send(svg.encode(),"image/svg+xml; charset=utf-8",svg=True)
                return self.send({"error":"结果已过期或不存在"},status=404)
            if url.path.startswith("/api/admin/"):
                if not self.authorized():
                    return self.send({"error": "请输入管理口令"}, status=401)
                if url.path == "/api/admin/settings":
                    return self.send(monitor.settings(public=True))
                if url.path == "/api/admin/nodes":
                    return self.send(monitor.nodes())
                if url.path == "/api/admin/review-providers":
                    return self.send(monitor.review_providers())
            if url.path == "/api/state":
                return self.send(monitor.state())
            if url.path == "/api/runs":
                params = parse.parse_qs(url.query)
                if "page" in params:
                    try:
                        return self.send(monitor.gallery(int(params["page"][0]), params.get("status",["all"])[0], params.get("test",["all"])[0]))
                    except ValueError:
                        return self.send({"error":"画廊分页或筛选参数无效"},status=400)
                before = parse.parse_qs(url.query).get("before", [None])[0]
                if before and (not before.isdigit() or len(before) > 18):
                    return self.send({"error": "无效分页参数"}, status=400)
                return self.send(monitor.runs(int(before) if before else None))
            match = re.fullmatch(r"/api/runs/(\d{1,18})(/svg)?", url.path)
            if match:
                with monitor.db() as db:
                    row = db.execute("SELECT * FROM runs WHERE id=?", (int(match[1]),)).fetchone()
                if row:
                    if match[2] and row["svg"]:
                        svg=redact(row["svg"], {"base_url":row["base_url"]})
                        if parse.parse_qs(url.query).get('display')==['1']:svg=display_svg(svg)
                        return self.send(svg.encode(), "image/svg+xml; charset=utf-8", svg=True)
                    if not match[2]:
                        return self.send(monitor.serialize(row, detail=True))
            return self.send({"error": "未找到记录"}, status=404)
        files = {"/": "index.html", "/admin": "admin.html", "/admin/": "admin.html", "/admin.js": "admin.js", "/providers.js": "providers.js", "/privacy.js": "privacy.js", "/app.js": "app.js", "/style.css": "style.css", "/observatory.css": "observatory.css", "/vendor/gsap.min.js": "vendor/gsap.min.js", "/favicon.svg": "favicon.svg"}
        name = files.get(url.path)
        if name:
            types = {"html": "text/html", "js": "text/javascript", "css": "text/css", "svg": "image/svg+xml"}
            return self.send((ROOT / "web" / name).read_bytes(), types[name.split(".")[-1]] + "; charset=utf-8")
        self.send({"error": "页面不存在"}, status=404)

    def do_POST(self):
        if self.path not in ("/api/guest/run", "/api/auth/login", "/api/auth/setup") and not self.authorized():
            return self.send({"error": "请输入管理口令"}, status=401)
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send({"error": "仅接受 JSON 请求"}, status=415)
        origin = self.headers.get("Origin")
        if origin and parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self.send({"error": "不接受跨站请求"}, status=403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("请求大小无效")
            values = json.loads(self.rfile.read(length))
            if not isinstance(values, dict):
                raise ValueError("请求必须为 JSON 对象")
            if self.path == "/api/auth/setup":
                if not self.local_setup():
                    return self.send({"error":"首次设置密码仅允许在服务所在机器完成"},status=403)
                self.server.monitor.setup_password(values.get("password"))
                return self.send({"configured":True})
            if self.path == "/api/auth/login":
                try:
                    return self.send({"token":self.server.monitor.login(values.get("password"))})
                except ValueError as exc:
                    return self.send({"error":str(exc)},status=401)
            if self.path == "/api/auth/logout":
                self.server.monitor.logout(self.headers.get("Authorization", "").removeprefix("Bearer "))
                return self.send({"ok":True})
            if self.path == "/api/admin/settings":
                return self.send(self.server.monitor.save(values))
            if self.path == "/api/admin/nodes":
                return self.send(self.server.monitor.save_node(values), status=201)
            if self.path == "/api/admin/review-providers":
                return self.send(self.server.monitor.save_review_provider(values), status=201)
            provider_action = re.fullmatch(r"/api/admin/review-providers/([1-9]\d{0,17})(/probe)?", self.path)
            if provider_action:
                if provider_action[2]:
                    return self.send(self.server.monitor.probe_review_provider(int(provider_action[1])))
                return self.send(self.server.monitor.save_review_provider(values, int(provider_action[1])))
            node_action = re.fullmatch(r"/api/admin/nodes/([1-9]\d{0,17})(?:/(activate|run))?", self.path)
            if node_action:
                node_id = int(node_action[1])
                if node_action[2] == "activate":
                    return self.send(self.server.monitor.activate_node(node_id))
                if node_action[2] == "run":
                    return self.send({"id":self.server.monitor.start_run(node_id=node_id)}, status=202)
                return self.send(self.server.monitor.save_node(values,node_id))
            if self.path == "/api/admin/run":
                return self.send({"id": self.server.monitor.start_run()}, status=202)
            if self.path == "/api/guest/run":
                try:
                    result = self.server.monitor.submit_guest(values)
                except queue.Full:
                    return self.send({"error": "访客队列已满，请稍后再试"}, status=429)
                return self.send(result, status=202)
            return self.send({"error": "接口不存在"}, status=404)
        except (ValueError, TypeError) as exc:
            self.send({"error": str(exc)}, status=400)


def main():
    os.umask(0o077)
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8765"))
    token = os.environ.get("ADMIN_TOKEN", "")
    directory = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ponytail: one process owns scheduling; use a distributed lease before running multiple replicas.
    instance_lock = (directory / "server.lock").open("a")
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("此数据目录已有检测服务运行，不能启动第二个调度进程") from None
    server = BoundedHTTPServer((host, port), Handler)
    monitor = Monitor(directory)
    if token and not monitor.password_configured():
        monitor.setup_password(token)
    if host not in ("127.0.0.1", "localhost") and not monitor.password_configured():
        raise SystemExit("请先在本机设置管理密码，或使用 ADMIN_TOKEN 初始化密码")
    server.monitor = monitor
    threading.Thread(target=monitor.scheduler, daemon=True).start()
    print(f"Pelican Watch: http://{host}:{port}", flush=True)
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stopped.set()
        workers = list(monitor.workers.items())
        for run_id,(worker,cancel) in workers:
            cancel.set()
            monitor.fail_run(run_id,'shutdown','服务停止，已有结果已保留；未自动重发请求')
        until = time.monotonic()+3
        for _,(worker,_) in workers:
            worker.join(max(0,until-time.monotonic()))
        server.server_close()


if __name__ == "__main__":
    main()
