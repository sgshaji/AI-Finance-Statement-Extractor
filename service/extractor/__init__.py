"""
extractor/
----------
Core library for the Financial Statement Extraction Pipeline.

Modules:
    azure_cu_client    — Azure Content Understanding REST API wrapper
    statement_detector — Statement location, parsing, and JSON assembly
    llm_reconciler     — Optional LLM post-processing for label quality

Public API (re-exported for convenience):
    from extractor import submit_document, poll_result
    from extractor import locate_statements, build_statement_json, build_summary
    from extractor import reconcile
"""

from .azure_cu_client import submit_document, poll_result
from .statement_detector import locate_statements, build_statement_json, build_summary
from .llm_reconciler import reconcile

__all__ = [
    "submit_document",
    "poll_result",
    "locate_statements",
    "build_statement_json",
    "build_summary",
    "reconcile",
]
