"""
Sanoid Manager API Routers
"""

from . import auth
from . import nodes
from . import snapshots
from . import sync_jobs
from . import vms
from . import logs
from . import settings

__all__ = [
    "auth",
    "nodes",
    "snapshots",
    "sync_jobs",
    "vms",
    "logs",
    "settings"
]
