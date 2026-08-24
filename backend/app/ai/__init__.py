"""AI reasoning layer. Phase 6, Doc §10.

Four advisory tasks: diagnose a reason category, draft reminder copy, extract a
promise from a customer reply, and describe a dispute raised in one. Each returns a
validated object and each has a deterministic fallback that needs no model at all.

Import rule (enforced by tests/architecture/test_layering.py): may NOT import
app.integrations.email, app.integrations.razorpay_client, app.services, or
app.core.db.

This is the project's central claim made structural — the model cannot send an email,
cannot move money, and cannot write invoice status, because the code it would need is
not reachable from here. It recommends; app.policy decides; app.services acts.

Customer replies reaching this layer are UNTRUSTED input: data to extract from, never
instructions to follow.
"""

from app.ai.client import LLMClient, LLMResult, get_llm_client
from app.ai.diagnosis import Diagnosis, DiagnosisInputs, diagnose, rule_based_diagnosis
from app.ai.dispute_analysis import DisputeAnalysis, analyse_dispute, rule_based_analysis
from app.ai.drafting import Draft, DraftInputs, draft_reminder, template_draft
from app.ai.promise_extraction import ExtractedPromise, extract_promise

__all__ = [
    "Diagnosis",
    "DiagnosisInputs",
    "DisputeAnalysis",
    "Draft",
    "DraftInputs",
    "ExtractedPromise",
    "LLMClient",
    "LLMResult",
    "analyse_dispute",
    "diagnose",
    "draft_reminder",
    "extract_promise",
    "get_llm_client",
    "rule_based_analysis",
    "rule_based_diagnosis",
    "template_draft",
]
