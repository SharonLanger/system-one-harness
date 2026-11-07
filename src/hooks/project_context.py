"""Per-invocation project metadata; never selects the feature's source root."""
from dataclasses import replace
from pathlib import Path
from soh_logging import ComponentName, ComponentType, component_scope


def resolve_project_directory(project_dir):
    """Return canonical directory and status without reading project source."""
    directory = None
    status = 'missing' if project_dir is None else 'invalid'
    if isinstance(project_dir, str) and project_dir.strip():
        try:
            candidate = Path(project_dir)
            if candidate.is_absolute():
                candidate = candidate.resolve(strict=True)
                if candidate.is_dir():
                    directory = str(candidate)
                    status = 'available'
        except TimeoutError:
            raise
        except (OSError, ValueError, RuntimeError):
            pass
    return directory, status


@component_scope(ComponentType.DISPATCHER, ComponentName.PROJECT_CONTEXT)
def enrich_project_context(envelope, project_dir, logger):
    directory, status = resolve_project_directory(project_dir)
    enriched = replace(envelope, derived={**envelope.derived, 'project_dir': directory})
    logger('INFO', 'project_context_ingested',
           session_id=envelope.common['session_id'],
           claude_project_dir=project_dir, project_dir=directory,
           project_dir_source='CLAUDE_PROJECT_DIR', project_dir_status=status,
           cwd=envelope.common['cwd'])
    return enriched
