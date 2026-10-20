"""Constants for T3 Usage Monitor."""

from datetime import timedelta

DOMAIN = "t3_code"
NAME = "T3 Usage Monitor"
CONF_CREDENTIAL = "credential"
CONF_CREDENTIAL_TYPE = "credential_type"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SHOW_DASHBOARD = "show_dashboard"
DEFAULT_SCAN_INTERVAL = 300
MAX_QUOTA_AGE = timedelta(hours=2)
SCOPES = "orchestration:read diagnostics:read"
