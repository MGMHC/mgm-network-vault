"""All web pages and JSON endpoints, on the one "web" blueprint (see web.py). Importing this registers them."""
from . import (admin_views, alerts_views, auth_views, backup_views, device_views, ipam_views,  # noqa: F401
               lifecycle_views, log_views, operations_views, rack_views, schedule_views)
from .web import bp  # noqa: F401
