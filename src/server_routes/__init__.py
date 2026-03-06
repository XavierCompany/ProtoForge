"""Route module registry for FastAPI server composition."""

from .chat import register_chat_routes
from .core import register_core_routes
from .github import register_github_routes
from .governance import register_governance_routes
from .system import register_system_routes
from .workiq_plan import register_workiq_and_plan_routes

__all__ = [
    "register_chat_routes",
    "register_core_routes",
    "register_github_routes",
    "register_governance_routes",
    "register_system_routes",
    "register_workiq_and_plan_routes",
]
