"""A disposable API process bounds DNS, TLS, headers and body with one deadline."""
import json
import sys

from server import _call_model_direct
from reliability import diagnose


if __name__ == '__main__':
    payload = json.loads(sys.stdin.buffer.read(24 * 1024 * 1024))
    try:
        result = _call_model_direct(payload['config'], payload['prompt'])
        response = {'result': result}
    except Exception as exc:
        failure = diagnose(exc, 'request')
        response = {'error': {'code': failure.code, 'message': str(failure),
                              'retryable': failure.retryable, 'retry_after': failure.retry_after}}
    print(json.dumps(response, ensure_ascii=False))
