"""Single synchronous command boundary. No model work at import time."""
import argparse
from contextlib import contextmanager, redirect_stdout
import json
import math
import os
import signal
import sys
from uuid import uuid4
from time import monotonic

from hooks.contracts import Context, Noop, Failure, ValidationError, validate_result
from json_utils import _unique_pairs

MAX_INPUT_CHARS = 65536
POST_COMPACT_INPUT_CHARS = 1024 * 1024

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValidationError('invalid_arguments', 'invocation', message)

def parse_args(argv):
    argv = list(sys.argv[1:] if argv is None else argv)
    if sum(x == '--hook' or x.startswith('--hook=') for x in argv) != 1:
        raise ValidationError('invalid_hook_argument', 'invocation', 'hook')
    p = Parser()
    p.add_argument('--hook', required=True)
    p.add_argument('--repository-root')
    p.add_argument('--config')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--log-state', action='store_true')
    mode.add_argument('--real', action='store_true')
    mode.add_argument('--observe-only', action='store_true',
                      help='record hook input and project metadata without invoking dispatchers')
    p.add_argument('--deadline', type=float, default=10.0)
    p.add_argument('--max-context-chars', type=int, default=8000)
    p.add_argument('--max-dependency-depth', type=int, default=2)
    p.add_argument('--max-dependency-chars', type=int, default=12000)
    args = p.parse_args(argv)
    if args.log_state and args.hook != "PostCompact":
        raise ValidationError("invalid_log_state_hook", "invocation", "hook")
    if not args.hook.strip():
        raise ValidationError('invalid_hook_argument', 'invocation', 'hook')
    if (not math.isfinite(args.deadline) or not 0 < args.deadline <= 300
            or args.max_context_chars <= 0 or not 0 <= args.max_dependency_depth <= 20
            or not 1 <= args.max_dependency_chars <= 1000000):
        raise ValidationError('invalid_budget', 'invocation')
    return args

def parse_payload(text, hook):
    if len(text) > (POST_COMPACT_INPUT_CHARS if hook == 'PostCompact' else MAX_INPUT_CHARS):
        raise ValidationError('input_budget_exceeded', 'input')
    try:
        payload = json.loads(text, object_pairs_hook=_unique_pairs,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError) as exc:
        raise ValidationError('invalid_json', 'input') from exc
    if not isinstance(payload, dict):
        raise ValidationError('invalid_object', 'input')
    name = payload.get('hook_event_name')
    if not isinstance(name, str) or not name.strip():
        raise ValidationError('invalid_field', 'input', 'hook_event_name')
    if name != hook:
        raise ValidationError('event_mismatch', 'input', 'hook_event_name', command_hook=hook, payload_hook=name)
    return payload

def serialize_response(result, hook):
    validate_result(result)
    if not isinstance(result, Context):
        return ''
    return json.dumps({'hookSpecificOutput': {'hookEventName': hook,
                      'additionalContext': result.text}}, ensure_ascii=False,
                      allow_nan=False, separators=(',', ':')) + '\n'

@contextmanager
def isolate_stdout():
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        with redirect_stdout(sys.stderr):
            yield
    finally:
        try:
            sys.__stdout__.flush()
        finally:
            os.dup2(saved, 1)
            os.close(saved)

def _expired(signum, frame):
    raise TimeoutError('hook deadline exceeded')

def _default_dispatch(payload, args, invocation_id, logger):
    from hooks.dispatcher import dispatch_hook
    if args.hook == 'PostCompact':
        return dispatch_hook(payload, args, invocation_id, logger)
    from hooks.project_context import enrich_project_context
    project_dir = os.environ.get('CLAUDE_PROJECT_DIR')
    return dispatch_hook(payload, args, invocation_id, logger,
                         enricher=lambda envelope: enrich_project_context(envelope, project_dir, logger))

def _default_log(level, event, **details):
    from log_utils import log
    from soh_logging import current_component
    component = current_component()
    log(level, component.component.value if component else 'hook_gateway',
        event, raise_on_error=True, **details)

def _main(argv=None, *, dispatcher=None, logger=None, invocation_id):
    started=monotonic()
    log_impl=logger or _default_log
    args=None
    result=Failure('not_started','gateway')
    written=0
    timer_started=False
    previous_handler=None
    expired=False
    logging_disabled=False

    def expire(signum,frame):
        nonlocal expired
        expired=True
        raise TimeoutError('hook deadline exceeded')

    def log(level,event,**details):
        if not timer_started or expired or logging_disabled:
            return False
        from soh_logging import current_context
        metadata={'invocation_id':invocation_id,'hook_event_name':args.hook,
                  'session_id':current_context().session_id,**details}
        try:
            with isolate_stdout():
                log_impl(level,event,**metadata)
            return True
        except TimeoutError:
            raise
        except Exception as exc:
            print(f'laya logging failed: {type(exc).__name__}',file=sys.stderr)
            return False

    def safe_log(level,event,**details):
        nonlocal logging_disabled
        try:
            log(level,event,**details)
        except TimeoutError:
            logging_disabled=True
            print(f'laya hook logging deadline exceeded [{invocation_id}]',file=sys.stderr)

    def recover_output():
        # Only used after a broken pipe or timeout interrupts protocol emission.
        try:
            null_fd=os.open(os.devnull,os.O_WRONLY)
            try:os.dup2(null_fd,1)
            finally:os.close(null_fd)
            sys.stdout.flush()
        except Exception:
            print('laya hook error: interrupted stdout cleanup failed',file=sys.stderr)

    try:
        try:
            with isolate_stdout():
                args=parse_args(argv)
                args.deadline_at = started + args.deadline
                remaining=args.deadline-(monotonic()-started)
                if remaining<=0:
                    expired=True
                    raise TimeoutError('hook deadline exceeded during argument parsing')
                previous_handler=signal.signal(signal.SIGALRM,expire)
                signal.setitimer(signal.ITIMER_REAL,remaining)
                timer_started=True
                log('INFO','gateway_started',process_cwd=os.getcwd(),
                    claude_project_dir=os.environ.get('CLAUDE_PROJECT_DIR'),
                    repository_root_argument=args.repository_root,
                    mode='log_state' if args.log_state else ('observe_only' if args.observe_only else ('real' if args.real else 'disabled')))
                input_limit = POST_COMPACT_INPUT_CHARS if args.hook == 'PostCompact' else MAX_INPUT_CHARS
                text=sys.stdin.read(input_limit+1)
                log('INFO','gateway_input_received',raw_input=None if args.hook == 'PostCompact' else text[:MAX_INPUT_CHARS],
                    raw_input_truncated=len(text)>input_limit,
                    input_chars_read=len(text),input_limit_chars=input_limit)
                observed_input = False
                if args.observe_only:
                    observed_input = log('INFO','observation_input_received',
                        raw_input=None if args.hook == 'PostCompact' else text[:MAX_INPUT_CHARS],
                        raw_input_truncated=len(text)>input_limit,
                        input_chars_read=len(text),input_limit_chars=input_limit)
                payload=parse_payload(text,args.hook)
                log('INFO','gateway_received',input_chars=len(text),fields=sorted(payload),raw_payload=None if args.hook == 'PostCompact' else payload)
                if args.observe_only:
                    from hooks.project_context import resolve_project_directory
                    project_value=os.environ.get('CLAUDE_PROJECT_DIR')
                    project_dir,status=resolve_project_directory(project_value)
                    session=payload.get('session_id')
                    recorded=log('INFO','hook_observed',raw_payload=None if args.hook == 'PostCompact' else payload,
                        session_id=session if isinstance(session,str) else None,
                        claude_project_dir=project_value,
                        derived={'project_dir':project_dir},project_dir_status=status,
                        project_dir_source='CLAUDE_PROJECT_DIR',
                        process_cwd=os.getcwd(),payload_cwd=payload.get('cwd'),
                        repository_root_argument=args.repository_root,
                        would_invoke_dispatcher='hooks.dispatcher.dispatch_hook',
                        event_dispatcher=('hooks.user_prompt_submit.dispatcher.'
                            'dispatch_user_prompt_submit' if args.hook=='UserPromptSubmit' else None),
                        supported_event=args.hook in ('UserPromptSubmit', 'PostCompact'),dispatcher_invoked=False)
                    if not observed_input or not recorded:
                        raise ValidationError('observation_log_failed','observation')
                    result=Noop('observe_only',feature_id='hook_observation')
                else:
                    log('INFO','gateway_dispatch_started',destination='hook_dispatcher')
                    result=validate_result((dispatcher or _default_dispatch)(payload,args,invocation_id,log))
                    log('INFO','gateway_dispatch_finished',result_type=type(result).__name__)
                if expired:
                    raise TimeoutError('hook deadline exceeded')
                output=serialize_response(result,args.hook)
                if isinstance(result,Failure):
                    log('ERROR','gateway_error',code=result.code,stage=result.stage,field=result.field)
                    print(f'laya hook error: {result.stage}: {result.code} {result.message}',file=sys.stderr)
                elif isinstance(result,Noop) and not args.observe_only:
                    log('INFO','gateway_noop',reason=result.reason)
                    print(f'laya hook noop: {result.reason}',file=sys.stderr)
                if output:
                    log('DEBUG','response_prepared',serialized_bytes=len(output.encode('utf-8')))
            if output:
                written=None
                sys.stdout.write(output)
                sys.stdout.flush()
                written=len(output.encode('utf-8'))
        except (Exception,SystemExit) as exc:
            interrupted=written is None and isinstance(exc,(BrokenPipeError,TimeoutError))
            if interrupted:
                recover_output()
            if isinstance(exc,BrokenPipeError) and interrupted:
                result=Failure('stdout_write_failed','output',message=f'{type(exc).__name__}: {exc}')
            else:
                result=Failure(exc.code if isinstance(exc,ValidationError) else
                    ('deadline_exceeded' if isinstance(exc,TimeoutError) else 'gateway_error'),
                    getattr(exc,'stage','gateway'),getattr(exc,'field',None),f'{type(exc).__name__}: {exc}')
            safe_log('ERROR','gateway_error',code=result.code,stage=result.stage,field=result.field,
                     **getattr(exc,'evidence',{}))
            partial='; stdout_bytes_written=unknown' if written is None else ''
            print(f'laya hook error: {result.code}: {result.message}{partial} [{invocation_id}]',file=sys.stderr)
        safe_log('INFO','gateway_finished',outcome='error' if isinstance(result,Failure) else
            ('context' if isinstance(result,Context) else
             ('observed' if args is not None and args.observe_only else 'noop')),
            reason=result.reason if isinstance(result,Noop) else None,
            code=result.code if isinstance(result,Failure) else None,
            response_present=isinstance(result,Context) and bool(written),
            stdout_bytes_written=written,exit_code=0)
        return 0
    finally:
        if timer_started:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,previous_handler)

def main(argv=None, *, dispatcher=None, logger=None):
    from soh_logging import HookEvent, invocation
    invocation_id = str(uuid4())
    selected = None
    try:
        raw = list(sys.argv[1:] if argv is None else argv)
        for index, value in enumerate(raw):
            if value == '--hook' and index + 1 < len(raw):
                selected = raw[index + 1]
            elif value.startswith('--hook='):
                selected = value.split('=', 1)[1]
        hook = HookEvent(selected) if selected in HookEvent._value2member_map_ else None
    except Exception:
        hook = None
    with invocation(invocation_id, hook):
        return _main(argv, dispatcher=dispatcher, logger=logger, invocation_id=invocation_id)


if __name__=='__main__':
    raise SystemExit(main())
