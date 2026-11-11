"""Work order services (S6).

`app.service.work_order` used to be a 3876-line module holding one 90-method class.
S6 split it by responsibility into mixins while keeping the class, its MRO and every
method body byte-identical. Importing this package yields the same attribute surface
as the old module: the class, the module helpers and the shared imports.

The package replaces the module, so there is no separate shim for `app.service.work_order`
itself — a package and a same-named module cannot coexist.
"""

from . import context  # noqa: F401
from .assign import WorkOrderAssignMixin
from .context import *  # noqa: F401,F403
from .lifecycle import WorkOrderLifecycleMixin
from .media import WorkOrderMediaMixin
from .queries import WorkOrderQueriesMixin


class WorkOrderService(
    WorkOrderLifecycleMixin,
    WorkOrderAssignMixin,
    WorkOrderMediaMixin,
    WorkOrderQueriesMixin,
):
    """Facade over the four method groups; behaviour is unchanged by the split."""

# The old module also exposed these module-level helpers to a few call sites; keep them.
from .context import (  # noqa: E402,F401
    _content_matches_mime,
    _employee_scope_denial,
    _filename_matches_mime,
    _registration_content_hash,
    eligibility_denied,
)
