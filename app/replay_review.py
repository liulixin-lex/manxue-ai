"""Replay one historical review using hash-verified frames; never rewrite its run."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import time
from artifacts import atomic_write, load_evidence
from reliability import StageError
from server import DEFAULTS, NODE_FIELDS, call_model, redact
from visual_review import review_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',default=os.getenv('DATA_DIR','/data'))
    parser.add_argument('--run-id',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=240,choices=range(60,301),metavar='60..300')
    parser.add_argument('--effort',choices=('inherit','low','medium','high','xhigh'),default='inherit')
    parser.add_argument('--format',choices=('auto','prompt','json_schema'),default='auto')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; keep each replay immutable')
    with contextlib.closing(sqlite3.connect(Path(args.data_dir,'monitor.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT id,svg,scene,nonce,node_id,status FROM runs WHERE id=?',(args.run_id,)).fetchone()
        if not row or not row['svg']:
            parser.error('Run does not exist or has no SVG')
        config=dict(DEFAULTS,**json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0]))
        node=db.execute('SELECT * FROM nodes WHERE id=?',(row['node_id'],)).fetchone()
        if not node:
            parser.error('The original node no longer exists')
        config.update({key:node[key] for key in NODE_FIELDS})
        if config.get('judge_node_id'):
            judge=db.execute('SELECT * FROM nodes WHERE id=?',(config['judge_node_id'],)).fetchone()
            if not judge:
                parser.error('The configured judge node no longer exists')
            config['_judge']={key:judge[key] for key in NODE_FIELDS}
    bundle=load_evidence(args.data_dir,args.run_id,row['svg'])
    config.update(review_timeout_seconds=args.timeout,review_effort=args.effort,review_format=args.format,
                  _scene=row['scene'],_nonce=row['nonce'],_deadline=time.monotonic()+args.timeout)
    def receipt(text,metadata):
        text=redact(redact(text,config),config.get('_judge',{}))
        atomic_write(args.output.with_suffix(f'.reply-{metadata["attempt"]}.json'),
                     json.dumps(dict(metadata,text=text),ensure_ascii=False).encode())
    config['_save_review_receipt']=receipt
    started=time.time()
    try:
        result=review_bundle(config,bundle,call_model)
        for key in ('judge_model','returned_model'):
            if key in result:
                result[key]=redact(redact(result[key],config),config.get('_judge',{}))[:200]
    except StageError as exc:
        result=dict(getattr(exc,'review_details',{}),status='error',stage=exc.stage,error_code=exc.code,reason=str(exc))
    report=dict(run_id=args.run_id,original_status=row['status'],started=started,finished=time.time(),
                elapsed_seconds=round(time.time()-started,3),svg_sha256=bundle['metadata']['svg_sha256'],
                frame_hashes=[f['sha256'] for f in bundle['metadata']['frames']],review=result)
    atomic_write(args.output,json.dumps(report,ensure_ascii=False,indent=2).encode())
    print(json.dumps({k:report[k] for k in ('run_id','original_status','elapsed_seconds')},ensure_ascii=False))
    print(json.dumps({k:result.get(k) for k in ('status','checks','reason','attempts','error_code','judge_effort','usage')},ensure_ascii=False))
    return int(result['status']=='error')


if __name__=='__main__':
    raise SystemExit(main())
