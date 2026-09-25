"""Actual Chromium lifecycle regression; never calls a model or touches production data."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from visual_review import render_evidence
from reliability import StageError


def resources():
    zombies=0
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:zombies += (p/'stat').read_text().split(') ',1)[1].split()[0]=='Z'
            except (OSError,IndexError):pass
    return {'zombies':zombies,'fds':len(list(Path('/proc/self/fd').iterdir()))}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--iterations',type=int,default=20)
    count=parser.parse_args().iterations
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><circle cx="20" cy="80" r="15"><animate attributeName="cx" values="20;200;20" dur="2s" repeatCount="indefinite"/></circle></svg>'
    baseline=resources();start=time.monotonic()
    for index in range(count):
        result=render_evidence(svg)
        assert len(result['frames'])>=12
        if index%5==0:
            try:render_evidence(svg,.15)
            except StageError:pass
            else:raise AssertionError('Expected a forced renderer timeout')
        time.sleep(.1)
        current=resources()
        assert current['zombies']==baseline['zombies'],current
        assert current['fds']<=baseline['fds']+1,current
        if index%10==0 or index==count-1:
            print(json.dumps({'renders':index+1,'resources':current,'elapsed':round(time.monotonic()-start,2)}),flush=True)


if __name__=='__main__':main()
