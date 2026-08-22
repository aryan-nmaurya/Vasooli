"""API request/response DTOs. Separate from `app.models` on purpose.

Serializing SQLModel tables directly leaks whatever happens to be on the row —
including fields added later that nobody meant to expose. Phase 2 has a concrete
instance of this risk: eval ground-truth labels must never reach a prompt or an API
response, and an explicit DTO makes that structural rather than a code-review promise.

DTOs also carry presentation the DB has no business storing, such as the
`amount_display: "₹42,000"` string the dashboard renders.
"""
