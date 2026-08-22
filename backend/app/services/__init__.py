"""Orchestration layer. The only layer that writes.

Composes the others: read state, ask `app.ai` for a recommendation, put it through
`app.policy`, and — only if approved — call an integration and persist the result
with an audit entry.

This is the only layer permitted to hold a DB session AND call an integration in the
same function. That concentration is deliberate: every money-adjacent side effect in
the system happens in files under this directory, so there is one place to audit.
"""
