"""
extractor/stages — 5-stage extraction pipeline.

Stages:
  1. analyze  — CU Locator + markdown + page map
  2. select   — Score-based statement selection
  3. extract  — Page-constrained table location + HTML parse
  4. enrich   — Translate rows + columns + verify company name
  5. validate — Structural validation gates
"""
