"""The local web API (E5-1): FastAPI over the analytics, memory, classify and ingest modules.

No business logic lives here or in the frontend: every number comes from :mod:`coach.analytics`, :mod:`coach.memory`,
:mod:`coach.classify` and :mod:`coach.ingest`. See docs/ui.md for the security model.

AGENTS MUST NOT CALL THIS API. It exists for the human at the keyboard: accepting a memory proposal here is the human
decision that the CLI reserves for an interactive terminal. The API needs a session cookie that only a browser that
loaded the page holds, plus a CSRF header, and proposal acceptance additionally needs the short code typed by the user.
"""
