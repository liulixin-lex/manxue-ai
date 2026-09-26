"""Canvas presentation must never change stored drawings or audit evidence."""
import copy
import json
import os
import threading
import unittest
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from test_result_views import ResultFixture
import server
from svg_display import display_svg

SVG='''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="600" viewBox="0 0 960 600">
<defs><clipPath id="canvas"><rect width="960" height="600" rx="24"/></clipPath>
<pattern id="pattern"><rect width="960" height="600" rx="16"/></pattern></defs>
<g clip-path="url(#canvas)"><rect width="960" height="600" fill="#246b47"/>
<rect id="label" x="400" y="300" width="120" height="30" rx="6" fill="white" stroke="black"/>
<circle id="wheel" cx="300" cy="200" r="30"><animate attributeName="r" values="30;32;30" dur="4s" repeatCount="indefinite"/></circle></g>
<rect id="frame" x="13" y="13" width="934" height="574" fill="none" stroke="white" stroke-width="12"/></svg>'''


class CanvasTests(unittest.TestCase):
    def test_canvas_edges_only_and_animation_retained(self):
        original=ET.fromstring(SVG);presented=ET.fromstring(display_svg(SVG))
        ns={'s':'http://www.w3.org/2000/svg'}
        self.assertEqual('0',presented.find('.//s:clipPath/s:rect',ns).get('rx'))
        self.assertEqual('16',presented.find('.//s:pattern/s:rect',ns).get('rx'))
        for identifier in ('label','wheel'):
            before=original.find(f'.//*[@id="{identifier}"]');after=presented.find(f'.//*[@id="{identifier}"]')
            self.assertEqual(ET.tostring(before),ET.tostring(after))
        self.assertIn('stroke:none!important',presented.find('.//*[@id="frame"]').get('style'))
        self.assertEqual('24',original.find('.//s:clipPath/s:rect',ns).get('rx'))
        self.assertEqual(original.get('viewBox'),presented.get('viewBox'))

    def test_percent_coordinates_and_root_radius(self):
        source='<svg xmlns="http://www.w3.org/2000/svg" width="900px" height="600px" style="background:green;border-radius:20px"><rect width="100%" height="100%" rx="24"/></svg>'
        root=ET.fromstring(display_svg(source))
        self.assertEqual('0',root[0].get('rx'))
        self.assertIn('background:green',root.get('style'))
        self.assertIn('border-radius:0!important',root.get('style'))

    def test_nonzero_viewbox_and_transformed_art(self):
        source='<svg xmlns="http://www.w3.org/2000/svg" viewBox="10 20 900 600"><rect x="10" y="20" width="900" height="600" rx="24"/><g transform="scale(.5)"><rect x="10" y="20" width="900" height="600" rx="24"/></g></svg>'
        root=ET.fromstring(display_svg(source))
        self.assertEqual('0',root[0].get('rx'));self.assertEqual('24',root[1][0].get('rx'))

    def test_unsupported_viewport_does_not_guess_or_break_the_image(self):
        for source in ('bad xml','<svg/>','<svg viewBox="0 0 NaN 640"/>','<svg width="100%" height="100%"/>'):
            self.assertEqual(source,display_svg(source))


class DisplayAPITests(ResultFixture):
    def test_public_and_guest_previews_leave_downloads_and_database_original(self):
        guest_id='a'*24
        with self.monitor.db() as db:
            db.execute('UPDATE runs SET svg=? WHERE id=?',(SVG,self.ids[0]))
            db.execute('INSERT INTO guest_results VALUES (?,?,?)',(guest_id,server.time.time(),json.dumps({'svg':SVG})))
        http=server.BoundedHTTPServer(('127.0.0.1',0),server.Handler);http.monitor=self.monitor
        worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
        try:
            for path in (f'/api/runs/{self.ids[0]}/svg',f'/api/guest/results/{guest_id}/svg'):
                base=f'http://127.0.0.1:{http.server_port}'+path
                with urlopen(base) as response:self.assertEqual(SVG,response.read().decode())
                with urlopen(base+'?display=1') as response:
                    self.assertIn('image/svg+xml',response.headers['Content-Type'])
                    preview=response.read().decode()
                    self.assertIn('rx:0!important',preview)
                with urlopen(base) as response:self.assertEqual(SVG,response.read().decode())
            with self.monitor.db() as db:self.assertEqual(SVG,db.execute('SELECT svg FROM runs WHERE id=?',(self.ids[0],)).fetchone()[0])
        finally:http.shutdown();http.server_close();worker.join()

    @unittest.skipUnless(os.environ.get('RENDER_TESTS')=='1','requires Chromium')
    def test_edge_to_edge_gallery_detail_and_manual_pass(self):
        from playwright.sync_api import sync_playwright
        with self.monitor.db() as db:
            db.execute('UPDATE runs SET svg=?',(SVG,))
        http=server.BoundedHTTPServer(('127.0.0.1',0),server.Handler);http.monitor=self.monitor
        worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(args=['--disable-dev-shm-usage'])
                page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page.goto(f'http://127.0.0.1:{http.server_port}')
                page.wait_for_selector('#gallery img')
                page.wait_for_function('()=>[...document.querySelectorAll("#gallery img")].every(el=>el.complete && el.naturalWidth)')
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':1000})
                    measured=page.locator('#gallery .run-card').first.evaluate('''el=>({radius:getComputedStyle(el).borderRadius,
                      fit:getComputedStyle(el.querySelector('img')).objectFit,border:getComputedStyle(el.querySelector('.preview')).borderBottomWidth,
                      overflow:document.documentElement.scrollWidth>innerWidth})''')
                    self.assertEqual({'radius':'0px','fit':'cover','border':'0px','overflow':False},measured)
                    self.assertTrue(page.locator('#gallery img').first.get_attribute('src').endswith('?display=1'))
                # Actual SVG image corners are painted, not transparent rounded cutouts.
                corners=page.locator('#gallery img').first.evaluate('''el=>{const c=document.createElement('canvas');c.width=el.naturalWidth;c.height=el.naturalHeight;
                  const ctx=c.getContext('2d');ctx.drawImage(el,0,0);return [[1,1],[c.width-2,1],[1,c.height-2],[c.width-2,c.height-2]].map(([x,y])=>Array.from(ctx.getImageData(x,y,1,1).data));}''')
                self.assertEqual([[36,107,71,255]]*4,corners)
                page.locator('#gallery .preview').first.click();page.wait_for_selector('#detail-image')
                page.wait_for_function('()=>document.getElementById("detail-image").naturalWidth>0')
                ratio=page.locator('#detail-image').evaluate('el=>({shown:el.clientWidth/el.clientHeight,native:el.naturalWidth/el.naturalHeight,radius:getComputedStyle(el).borderRadius})')
                self.assertAlmostEqual(ratio['native'],ratio['shown'],delta=.02);self.assertEqual('0px',ratio['radius'])
                html=page.evaluate("reviewDetail({pelican:{review:{status:'passed',manual_override:{status:'passed'},previous_review:{reason:'原审核待复核'}}}})")
                self.assertIn('已手动标记通过',html);self.assertIn('原审核待复核',html)
                browser.close()
        finally:http.shutdown();http.server_close();worker.join()
