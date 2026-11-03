"""Telegram bot layer: handlers, keyboards, middlewares and polling runner.

Handlers only translate between Telegram updates and service calls; all
business logic lives in ``app.services`` and all SQL in ``app.repositories``.
"""
