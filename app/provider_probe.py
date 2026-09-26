"""A small, randomized image-input check; not a benchmark of judging accuracy."""
import base64
import json
import re
import secrets
import struct
import zlib
from reliability import request_with_retries

COLORS = {'red': (230, 25, 35), 'green': (25, 175, 55), 'blue': (25, 65, 230),
          'yellow': (250, 220, 20), 'purple': (150, 40, 200)}


def color_png(colors):
    width, height = 300, 100
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            rgb = COLORS[colors[x // 100]] if 12 <= y < 88 and 12 <= x % 100 < 88 else (255, 255, 255)
            pixels.extend(rgb)
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(bytes(pixels))) + chunk(b'IEND', b'')


def probe(config, model_call):
    orders = [secrets.SystemRandom().sample(list(COLORS), 3) for _ in range(2)]
    text_type = 'input_text' if config['protocol'] == 'responses' else 'text'
    content = [{'type': text_type, 'text': '按图片顺序识别每张图片从左到右三个色块的颜色。颜色只用 red,green,blue,yellow,purple。只返回 JSON：{"colors":[["颜色1","颜色2","颜色3"],["颜色1","颜色2","颜色3"]]}。看不到图片时返回 {"colors":null}。'}]
    for colors in orders:
        url = 'data:image/png;base64,' + base64.b64encode(color_png(colors)).decode()
        content.append({'type': 'input_image', 'image_url': url, 'detail': 'high'} if config['protocol'] == 'responses'
                       else {'type': 'image_url', 'image_url': {'url': url, 'detail': 'high'}})
    text, _, _ = request_with_retries(config, content, model_call, 'review', [])
    fence = re.escape(chr(96) * 3)
    fenced = re.fullmatch(fence + r'(?:json)?\s*(.*?)\s*' + fence, text.strip(), re.S | re.I)
    try:
        result = json.loads(fenced[1] if fenced else text)
    except (ValueError, TypeError):
        return False
    return isinstance(result, dict) and result.get('colors') == orders
