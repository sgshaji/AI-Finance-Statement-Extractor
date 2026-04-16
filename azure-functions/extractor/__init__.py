"""
extractor/
----------
Core library for the Financial Statement Extraction Pipeline.

Modules:
    azure_cu_client    — Azure Content Understanding REST API wrapper (legacy)
    di_client          — Azure Document Intelligence client (current default)
    statement_detector — Statement location, parsing, and JSON assembly
    llm_reconciler     — Optional LLM post-processing for label quality

All imports are lazy to avoid requiring env vars at import time.
Use direct imports: from extractor.di_client import analyze_document
"""
