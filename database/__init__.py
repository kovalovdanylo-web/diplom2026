from database.db import (
    Database, db,
    init_db, register_user,
    save_receipt, find_existing_receipt,
    delete_receipt, get_total_stats, log_api_usage,
    auth_create, auth_get_by_telegram, auth_get_by_email,
    auth_set_logged_in, auth_is_logged_in, auth_switch_telegram,
)

__all__ = [
    "Database", "db",
    "init_db", "register_user",
    "save_receipt", "find_existing_receipt",
    "delete_receipt", "get_total_stats", "log_api_usage",
    "auth_create", "auth_get_by_telegram", "auth_get_by_email",
    "auth_set_logged_in", "auth_is_logged_in", "auth_switch_telegram",
]
