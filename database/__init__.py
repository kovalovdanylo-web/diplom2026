from database.db import init_db, register_user, save_receipt, find_existing_receipt, log_api_usage
from database.db import get_total_stats
from database.db import auth_create, auth_get_by_telegram, auth_get_by_email, auth_set_logged_in, auth_is_logged_in, auth_switch_telegram

__all__ = [
    "init_db",
    "register_user",
    "save_receipt",
    "find_existing_receipt",
    "log_api_usage",
    "get_total_stats",
    "auth_create",
    "auth_get_by_telegram",
    "auth_get_by_email",
    "auth_set_logged_in",
    "auth_is_logged_in",
    "auth_switch_telegram",
]
