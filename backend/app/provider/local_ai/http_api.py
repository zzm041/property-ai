"""HTTP surface of the local AI adapter (moved verbatim, phase 1)."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .runtime_control import (
    FLOOR_ENV_VARS,
    MAX_BODY,
    MODELS,
    Runner,
    allowed_prompt_versions,
    child,
    decode,
    effective_floors,
    failure_body,
    resource_floor,
    stamp,
)

MAX_FORCE_RETRY_ACTOR=120


def force_retry_actor_from_headers(headers):
    """Read the operator header and say so when it is unusable (PA-169/F4).

    Returns ``(actor, error_code)``. The header is the only place an operator
    identity can travel: the inference body stays exactly six keys, so silently
    dropping an over-long actor made a "forced" retry look forced when it was
    not. One seam, tested directly, instead of a branch buried in the handler.
    """

    actor=(headers.get('X-Force-Retry-Actor') or '').strip()
    if len(actor)>MAX_FORCE_RETRY_ACTOR:return '', 'INVALID_FORCE_RETRY_ACTOR'
    return actor, None


def serve(config_path):
    runner=Runner(config_path);token=os.environ.get('LOCAL_AI_INTERNAL_TOKEN','')
    if len(token)<32:raise RuntimeError('INTERNAL_AUTH_REQUIRED')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def setup(self):super().setup();self.connection.settimeout(10)
        def reply(self,status,value):
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def authorized(self):
            return self.client_address[0]=='127.0.0.1' and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_GET(self):
            if not self.authorized():return self.reply(401,{'error':'AUTH_REQUIRED'})
            # PA-130: /readyz is the same readiness surface under the name the
            # delivery stack and its runbooks use; the payload is identical and
            # now reports the effective floors next to the current measurement.
            if self.path not in ('/healthz','/readyz'):return self.reply(404,{'error':'NOT_FOUND'})
            # The measurement is best-effort: a missing nvidia-smi reports an
            # observation_error instead of turning readiness into a 500, so this
            # stays usable as the launcher's liveness probe.
            return self.reply(200,{'status':'ready','inference':False,'scope':runner.scope,'models':MODELS,'reservation_gate':runner.gate(),'budget':runner.budget_usage(),'resource_floor':resource_floor(),'floors':effective_floors(),'prompt_versions':allowed_prompt_versions(),**runner.admission()})
        def do_POST(self):
            if not self.authorized():return self.reply(401,{'error':'AUTH_REQUIRED'})
            if self.path=='/admin/drain':
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length')!='0':
                    return self.reply(400,{'error':'EMPTY_BODY_REQUIRED'})
                active=runner.begin_drain()
                return self.reply(200,{'accepting_requests':False,'request_active':active})
            request_id=self.headers.get('X-Request-Id','')
            request_binding=hashlib.sha256(request_id.encode()).hexdigest() if request_id else None
            # PA-169: an operator may re-run a quarantined text on purpose. The
            # actor travels in its own header because the inference body stays
            # strictly six keys, and it is written into the reservation.
            force_actor,actor_error=force_retry_actor_from_headers(self.headers)
            # PA-169/F4: an unusable actor used to be dropped silently, so a
            # "forced" retry looked forced while it was not. Say no instead.
            if actor_error is not None:
                runner.record_pre_reservation_failure(ValueError(actor_error),stage='HTTP_ENVELOPE',request_binding=request_binding)
                return self.reply(400,{'error':actor_error})
            force_retry=({'actor':force_actor,'request_binding':request_binding} if force_actor else None)
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:
                runner.record_pre_reservation_failure(ValueError('CONTENT_LENGTH_REQUIRED'),stage='HTTP_ENVELOPE',request_binding=request_binding)
                return self.reply(400,{'error':'CONTENT_LENGTH_REQUIRED'})
            try:
                size=int(self.headers['Content-Length'])
                if not 0<size<=MAX_BODY:
                    runner.record_pre_reservation_failure(ValueError('BODY_LIMIT'),stage='HTTP_ENVELOPE',request_binding=request_binding)
                    return self.reply(413,{'error':'BODY_LIMIT'})
                body=self.rfile.read(size)
                if len(body)!=size:raise ValueError('INCOMPLETE_BODY')
                kind,value=decode(self.path,self.headers.get('Content-Type',''),body,self.headers.get('X-Prompt-Version',''),runner.scope)
            except (ValueError,TypeError,KeyError,UnicodeError) as exc:
                runner.record_pre_reservation_failure(exc,stage='HTTP_DECODE',input_hash=hashlib.sha256(body if 'body' in locals() else b'').hexdigest(),request_binding=request_binding,prompt_version=self.headers.get('X-Prompt-Version',''))
                return self.reply(422,{'error':'INVALID_REQUEST'})
            if not runner.acquire_request():return self.reply(503,{'error':'MODEL_DRAINING_OR_BUSY_NO_RETRY'})
            shared=None;shared_ready=False
            try:
                shared=runner.acquire_shared_model_lock()
                runner.assert_shared_completion_ready();shared_ready=True
                return self.reply(200,runner.run(kind,value,request_binding=request_binding,force_retry=force_retry))
            except Exception as exc:
                if not shared_ready:
                    runner.record_pre_reservation_failure(exc,stage='SHARED_COMPLETION_BARRIER' if shared is not None else 'SHARED_RESOURCE_LOCK',kind=kind,request_binding=request_binding)
                body=failure_body(exc)
                return self.reply(503,body)
            finally:
                if shared is not None:runner.release_shared_model_lock(shared)
                runner.lock.release()
    server=ThreadingHTTPServer((runner.config['host'],runner.config['port']),Handler)
    floors=effective_floors()
    banner={'pid':os.getpid(),'listen':f"{runner.config['host']}:{runner.config['port']}",'python':sys.executable,'source':str(Path(__file__).resolve()),'utc':stamp(),'health_inference':False,'prompt_versions':allowed_prompt_versions(),'floors':floors}
    print(json.dumps(banner,ensure_ascii=False),flush=True)
    print('resource floors:',json.dumps({
        'ram_mib':f"{floors['ram']['floor_mib']} ({floors['ram']['source']})",
        'gpu_mib':f"{floors['gpu']['floor_mib']} ({floors['gpu']['source']})",
        'cold_start_gpu_mib':f"{floors['cold_start_gpu']['floor_mib']} ({floors['cold_start_gpu']['source']})",
        'overrides':[FLOOR_ENV_VARS['ram'],FLOOR_ENV_VARS['gpu'],FLOOR_ENV_VARS['cold_start_gpu']],
    },ensure_ascii=False),flush=True)
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['serve', 'child'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--input')
    parser.add_argument('--job-name')
    args = parser.parse_args(argv)
    if args.mode == 'child':
        child(args.config, args.input, args.job_name)
    else:
        serve(args.config)


if __name__ == '__main__':
    main()
