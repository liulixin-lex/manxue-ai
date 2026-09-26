import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from artifacts import save_evidence, prune_evidence, load_evidence


class ArtifactTests(unittest.TestCase):
    def test_atomic_manifest_hash_and_no_overwrite(self):
        png=b'\x89PNG\r\n\x1a\n'+b'fixture'
        with tempfile.TemporaryDirectory() as directory:
            result=save_evidence(directory,1,'<svg/>',[{'time':0,'png':base64.b64encode(png).decode()}],{'renderer_version':2})
            root=Path(directory)/'artifacts'/'1'
            self.assertEqual(png,(root/'0.png').read_bytes())
            self.assertEqual(hashlib.sha256(png).hexdigest(),result['frames'][0]['sha256'])
            self.assertEqual(result,json.loads((root/'manifest.json').read_text()))
            self.assertEqual(0o600,(root/'0.png').stat().st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                save_evidence(directory,1,'<svg/>',[],{})

    def test_replay_requires_matching_svg_and_every_frame_hash(self):
        png=b'\x89PNG\r\n\x1a\nfixture'
        frames=[{'time':index/4,'png':base64.b64encode(png).decode()} for index in range(12)]
        with tempfile.TemporaryDirectory() as directory:
            save_evidence(directory,1,'<svg/>',frames,{'unique_frames':1,'sampling_limited':False})
            self.assertEqual(frames,load_evidence(directory,1,'<svg/>')['frames'])
            with self.assertRaises(ValueError):load_evidence(directory,1,'<svg>different</svg>')
            (Path(directory)/'artifacts'/'1'/'3.png').write_bytes(png+b'changed')
            with self.assertRaises(ValueError):load_evidence(directory,1,'<svg/>')

    def test_expiration_and_capacity_preserve_active_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'artifacts'
            for index in range(1,4):
                path=root/str(index);path.mkdir(parents=True)
                (path/'0.png').write_bytes(b'x'*100)
                os.utime(path,(time.time()-1000+index,time.time()-1000+index))
            prune_evidence(directory,protected=[1],max_bytes=250,max_age=2000)
            self.assertTrue((root/'1').exists())
            self.assertFalse((root/'2').exists())
            self.assertTrue((root/'3').exists())
            prune_evidence(directory,protected=[1],max_age=500)
            self.assertTrue((root/'1').exists())
            self.assertFalse((root/'3').exists())

    def test_budget_failure_does_not_delete_protected_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'artifacts'/'1';path.mkdir(parents=True)
            (path/'0.png').write_bytes(b'x'*100)
            with self.assertRaises(OSError):prune_evidence(directory,protected=[1],max_bytes=50)
            self.assertTrue((path/'0.png').exists())


if __name__=='__main__':unittest.main()
