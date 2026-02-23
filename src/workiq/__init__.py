"""WorkIQ integration — bridges Microsoft Work IQ CLI into ProtoForge.

Provides:
- :class:`WorkIQClient` — async subprocess wrapper around ``workiq ask``
- :class:`WorkIQResult` — structured result from a WorkIQ query
- :class:`WorkIQSelector` — human-in-the-loop result picker
"""

from src.workiq.client import WorkIQClient, WorkIQResult
from src.workiq.selector import WorkIQSelector

__all__ = [
    "WorkIQClient",
    "WorkIQResult",
    "WorkIQSelector",
]
