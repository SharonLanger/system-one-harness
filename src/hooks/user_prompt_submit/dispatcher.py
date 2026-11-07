"""Validate user-prompt evidence and call exactly one feature."""
from dataclasses import dataclass
from time import monotonic

from hooks.contracts import Context, Noop, Failure, ValidationError, validate_result
from hooks.dispatcher import HookEnvelope, require_string
from soh_logging import ComponentName, ComponentType, component_scope

@dataclass(frozen=True)
class PromptRequest:
    prompt: str
    envelope: HookEnvelope
    config: object

def _default_handler(request,logger):
    from flows.first_method_context import handle_prompt
    return handle_prompt(request,logger)

@component_scope(ComponentType.DISPATCHER, ComponentName.USER_PROMPT_SUBMIT)
def dispatch_user_prompt_submit(envelope,args,logger,*,handler=None,clock=monotonic):
    result=None
    logger('INFO','event_dispatcher_started',hook_event_name=envelope.hook_name,
           session_id=envelope.common.get('session_id'),common=envelope.common,
           derived=envelope.derived,fields=sorted(envelope.raw_payload))
    try:
        if envelope.hook_name!='UserPromptSubmit':
            raise ValidationError('event_mismatch','user_prompt_submit','hook_event_name')
        prompt=require_string(envelope.raw_payload,'prompt','user_prompt_submit',allow_empty=True)
        request=PromptRequest(prompt,envelope,args)
        logger('INFO','hook_input_ingested',session_id=envelope.common['session_id'],
               fields=sorted(envelope.raw_payload),prompt_chars=len(prompt),cwd=envelope.common['cwd'],
               project_dir=envelope.derived.get('project_dir'),
               project_dir_source='CLAUDE_PROJECT_DIR' if 'project_dir' in envelope.derived else None,
               project_root_source='explicit_configuration' if getattr(args,'repository_root',None) else None)
        logger('INFO','feature_started',session_id=envelope.common['session_id'],
               feature_id='prompt_context_enrichment')
        started=clock()
        result=None
        try:
            result=validate_result((handler or _default_handler)(request,logger))
            return result
        finally:
            logger('INFO' if isinstance(result,(Context,Noop)) else 'ERROR','feature_finished',
                   session_id=envelope.common['session_id'],feature_id='prompt_context_enrichment',
                   outcome='context' if isinstance(result,Context) else ('noop' if isinstance(result,Noop) else 'error'),
                   reason=result.reason if isinstance(result,Noop) else None,
                   code=result.code if isinstance(result,Failure) else ('feature_exception' if result is None else None),
                   duration_seconds=max(0,clock()-started))
    except ValidationError as exc:
        logger('ERROR','event_input_rejected',code=exc.code,stage=exc.stage,field=exc.field)
        result=Failure(exc.code,exc.stage,exc.field,str(exc))
        return result
    finally:
        logger('ERROR' if result is None or isinstance(result,Failure) else 'INFO',
               'event_dispatcher_finished',result_type=type(result).__name__,
               outcome='context' if isinstance(result,Context) else ('noop' if isinstance(result,Noop) else 'error'),
               reason=result.reason if isinstance(result,Noop) else None,
               code=result.code if isinstance(result,Failure) else ('dispatcher_exception' if result is None else None))
