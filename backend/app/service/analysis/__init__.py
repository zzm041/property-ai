"""Work-order AI analysis (package restructure phase 1: split, no behaviour change).

`app.service.analysis` keeps every public name it had, so callers and tests are
unaffected. The implementation lives in orchestrator / vision / validation /
persistence; the shared patchable surface lives in context.
"""
from . import context  # noqa: F401
from .context import (  # noqa: F401  (the shared surface, re-exported)
    MEDIA_CHECKPOINT_PROTOCOL_VERSION,
    WORK_ORDER_ANALYSIS_PROMPT_VERSION,
    AnalysisSnapshot,
    MediaRef,
    ProviderBundle,
    _STAFF_ROLES,
    begin_owned_transaction,
    bind_observations,
    logger,
    owns_attempt,
    require_execution_lease,
    utc_now,
)
from .orchestrator import AnalysisService  # noqa: F401
from .validation import _status_for_error  # noqa: F401
