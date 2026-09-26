"""Avoid retaining OAuth authorization codes in Uvicorn access logs."""

import logging


class OAuthAccessFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, path, version, status = record.args
            if isinstance(path, str) and path.startswith("/api/integrations/google/callback"):
                record.args = (address, method, path.split("?", 1)[0], version, status)
        return True


def install_access_filter():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthAccessFilter) for item in logger.filters):
        logger.addFilter(OAuthAccessFilter())
