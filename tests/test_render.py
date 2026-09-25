import base64
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from visual_review import render_evidence


@unittest.skipUnless(os.environ.get('RENDER_TESTS')=='1','requires installed Chromium; set RENDER_TESTS=1')
class RendererTests(unittest.TestCase):
    def scene(self,body,attrs=''):
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 640" {attrs}>{body}</svg>'

    def test_long_motion_not_shortened_by_decoration(self):
        svg=self.scene('<circle cx="100" cy="100" r="30"><animate attributeName="cx" values="100;800;100" dur="4s" repeatCount="indefinite"/></circle><circle cx="900" cy="50" r="10"><animate attributeName="opacity" values="1;.8;1" dur=".5s" repeatCount="indefinite"/></circle>')
        result=render_evidence(svg)
        self.assertEqual(16,len(result['frames']))
        self.assertEqual(4,result['frames'][-1]['time'])
        self.assertGreater(result['metadata']['unique_frames'],4)

    def test_delayed_animation_is_observed(self):
        svg=self.scene('<circle cx="100" cy="100" r="30"><animate attributeName="cx" values="100;800;100" begin="3s" dur="2s" repeatCount="indefinite"/></circle>')
        result=render_evidence(svg)
        self.assertEqual(5,result['frames'][-1]['time'])
        self.assertGreater(result['metadata']['unique_frames'],1)

    def test_background_preserved_and_render_deterministic(self):
        body='<circle cx="100" cy="100" r="30" fill="white"/>'
        black=render_evidence(self.scene(body,'style="background:black"'))
        repeat=render_evidence(self.scene(body,'style="background:black"'))
        white=render_evidence(self.scene(body,'style="background:white"'))
        self.assertNotEqual(black['frames'][0]['png'],white['frames'][0]['png'])
        self.assertEqual(black['frames'],repeat['frames'])
        self.assertEqual(1,black['metadata']['unique_frames'])

    def test_css_alternate_and_delay(self):
        svg=self.scene('<style>@keyframes move {from {transform:translateX(0)} to {transform:translateX(300px)}} circle {animation:move 2s 1s infinite alternate linear}</style><circle cx="100" cy="100" r="30"/>')
        result=render_evidence(svg)
        self.assertEqual(5,result['frames'][-1]['time'])
        self.assertGreater(result['metadata']['unique_frames'],1)

    def test_long_timeline_is_explicitly_limited(self):
        svg=self.scene('<circle r="10"><animate attributeName="cx" values="10;900" begin="30s" dur="4s"/></circle>')
        result=render_evidence(svg)
        self.assertTrue(result['metadata']['sampling_limited'])
        self.assertEqual(12,result['frames'][-1]['time'])

    def test_intrinsic_size_without_viewbox(self):
        svg='<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="400"><circle cx="1500" cy="200" r="50" fill="red"/></svg>'
        result=render_evidence(svg)
        self.assertEqual({'width':1600,'height':400},result['metadata']['viewport'])
        png=base64.b64decode(result['frames'][0]['png'])
        self.assertEqual(960,int.from_bytes(png[16:20],'big'))
        self.assertEqual(240,int.from_bytes(png[20:24],'big'))


if __name__=='__main__':unittest.main()
