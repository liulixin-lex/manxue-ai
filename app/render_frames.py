"""Disposable, network-blocked renderer with reproducible multi-window evidence."""
import base64
import hashlib
import json
import math
import sys
from playwright.sync_api import sync_playwright
from server import inspect_svg, MAX_RESPONSE

RENDER_VERSION = 2
FRACTIONS = (0, .07, .17, .23, .31, .43, .51, .61, .73, .83, .93, 1)
MAX_FRAMES = 16


def sample_times(animations):
    ends = []
    windows = []
    incomplete = False
    for a in animations:
        begin, duration = a.get('begin'), a.get('duration')
        if (not isinstance(begin, (int, float)) or not isinstance(duration, (int, float))
                or not math.isfinite(begin) or not math.isfinite(duration) or duration <= 0):
            incomplete = True
            continue
        # Include the entire cycle and its boundary, without unbounded LCM sampling.
        end = max(0, begin) + duration * (2 if a.get('alternate') else 1)
        ends.append(end)
        windows.append((max(0,begin),duration))
        incomplete |= end > 12
    window = min(12, max(ends, default=4))
    window = max(.5, window)
    times = {round(window*f, 6) for f in FRACTIONS}
    # Retain a second, shorter time window so long scenery cycles cannot hide fast pedaling.
    shorter = sorted(set(windows), key=lambda item:item[1])
    if shorter and shorter[0][1] < window/2:
        begin,duration = shorter[0]
        for fraction in (.13,.37,.67,.91):
            point = round(begin+duration*fraction,6)
            if point <= 12:
                times.add(point)
    return sorted(times)[:MAX_FRAMES], incomplete


def capture(svg):
    svg, checks, _ = inspect_svg(svg, '')
    if not svg or not checks['svg']:
        raise ValueError('Unsafe SVG')
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 960, 'height': 640},
                                      device_scale_factor=1, reduced_motion='no-preference',
                                      service_workers='block', accept_downloads=False)
        context.route('**/*', lambda route: route.abort())
        page = context.new_page()
        page.set_default_timeout(8000)
        page.route('http://render.invalid/scene.svg', lambda route: route.fulfill(
            content_type='image/svg+xml', body=svg,
            headers={'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; script-src 'none'"}))
        page.goto('http://render.invalid/scene.svg', wait_until='load')
        page.evaluate('document.fonts.ready')
        bounds = page.evaluate("""() => {
            const r = document.documentElement.getBoundingClientRect();
            return {width: Math.ceil(r.width), height: Math.ceil(r.height)};
        }""")
        if not all(0 < bounds[k] <= 4096 for k in ('width', 'height')):
            raise ValueError('SVG viewport exceeds render budget')
        # Resize the surrounding viewport, never overwrite the SVG's background/viewBox/styles.
        page.set_viewport_size(bounds)
        animations = page.evaluate(r"""() => {
            const svg = document.documentElement;
            const clock = v => {
                if (!v) return 0;
                if (!/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:ms|s)?$/.test(v)) return null;
                return parseFloat(v) / (v.endsWith('ms') ? 1000 : 1);
            };
            svg.pauseAnimations();
            const records = [...svg.querySelectorAll('animate,animateTransform,animateMotion')].map(a => {
                let duration = null;
                try { duration = a.getSimpleDuration(); } catch {}
                return {kind:'smil', begin:clock(a.getAttribute('begin')), duration};
            });
            document.getAnimations().forEach(a => {
                a.pause();
                const t = a.effect.getTiming();
                records.push({kind:'css', begin:t.delay / 1000,
                    duration:typeof t.duration === 'number' ? t.duration / 1000 : null,
                    alternate:t.direction.startsWith('alternate')});
            });
            return records;
        }""")
        times, limited = sample_times(animations)
        session = context.new_cdp_session(page)
        frames = []
        for seconds in times:
            page.evaluate("""async seconds => {
                document.documentElement.setCurrentTime(seconds);
                document.getAnimations().forEach(a => { a.pause(); a.currentTime = seconds * 1000; });
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""", seconds)
            shot = session.send('Page.captureScreenshot', {
                'format':'png', 'captureBeyondViewport':False,
                'clip':{'x':0, 'y':0, **bounds, 'scale':min(1, 960/bounds['width'], 640/bounds['height'])}})
            frames.append({'time':seconds, 'png':shot['data']})
        browser_version = browser.version
        context.close()
        browser.close()
        hashes = [hashlib.sha256(base64.b64decode(f['png'])).hexdigest() for f in frames]
        return {'frames':frames, 'metadata':{'renderer_version':RENDER_VERSION,
                'browser_version':browser_version, 'viewport':bounds, 'animations':animations,
                'sampling_limited':limited, 'unique_frames':len(set(hashes)),
                'frame_times':times}}


if __name__ == '__main__':
    raw = sys.stdin.buffer.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError('SVG too large')
    print(json.dumps(capture(raw.decode())))
