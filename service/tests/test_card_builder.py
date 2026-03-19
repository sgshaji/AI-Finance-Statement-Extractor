"""Tests for Adaptive Card builder module."""

import pytest
from extractor.card_builder import (
    build_navigator_card,
    build_statement_review_card,
    parse_card_submission,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_test_statement(num_rows=5, columns=None):
    cols = columns or [
        {"column_index": 0, "label": "2025"},
        {"column_index": 1, "label": "2024"},
    ]
    rows = []
    for i in range(num_rows):
        rows.append(
            {
                "row_index": i,
                "label_raw": f"Item {i}",
                "row_type": "line_item",
                "indent_level": 0,
                "section": "current_assets",
                "canonical_group": "assets",
                "values": [
                    {
                        "raw": str((i + 1) * 100),
                        "normalized": float((i + 1) * 100),
                        "is_null": False,
                        "column_index": j,
                    }
                    for j in range(len(cols))
                ],
            }
        )
    return {
        "rows": rows,
        "columns": cols,
        "statement_metadata": {"page_range": {"start": 1, "end": 2}},
        "validation": {"warnings": [], "errors": []},
    }


def _make_test_confidence(level="medium", flagged=None):
    return {
        "score": {"high": 0.92, "medium": 0.72, "low": 0.45}[level],
        "level": level,
        "flagged_rows": flagged or [],
        "signals": {},
    }


def _make_summary():
    return [
        {"statement": "balance_sheet", "rows": 42},
        {"statement": "income_statement", "rows": 20},
        {"statement": "cash_flow", "rows": 15},
    ]


def _make_confidence_map():
    return {
        "balance_sheet": {
            "score": 0.72,
            "level": "medium",
            "flagged_rows": [5, 12],
            "signals": {},
        },
        "income_statement": {
            "score": 0.92,
            "level": "high",
            "flagged_rows": [],
            "signals": {},
        },
        "cash_flow": {
            "score": 0.45,
            "level": "low",
            "flagged_rows": [1, 3, 7],
            "signals": {},
        },
    }


# ── helpers for card introspection ───────────────────────────────────────────


def _collect_texts(obj):
    """Recursively collect all 'text' values in a nested dict/list structure."""
    texts = []
    if isinstance(obj, dict):
        if "text" in obj:
            texts.append(obj["text"])
        for v in obj.values():
            texts.extend(_collect_texts(v))
    elif isinstance(obj, list):
        for item in obj:
            texts.extend(_collect_texts(item))
    return texts


def _collect_elements(obj, element_type):
    """Recursively collect all elements matching a given 'type'."""
    results = []
    if isinstance(obj, dict):
        if obj.get("type") == element_type:
            results.append(obj)
        for v in obj.values():
            results.extend(_collect_elements(v, element_type))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_collect_elements(item, element_type))
    return results


def _collect_actions(obj):
    """Recursively collect all action objects (type starts with 'Action.')."""
    results = []
    if isinstance(obj, dict):
        t = obj.get("type", "")
        if isinstance(t, str) and t.startswith("Action."):
            results.append(obj)
        for v in obj.values():
            results.extend(_collect_actions(v))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_collect_actions(item))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Navigator card tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestNavigatorCard:
    def test_navigator_card_structure(self):
        card = build_navigator_card("Acme", "GBP", "thousands", _make_confidence_map(), _make_summary())
        assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.5"
        assert "body" in card
        assert "actions" in card

    def test_navigator_shows_all_statements(self):
        card = build_navigator_card("Acme", "GBP", "thousands", _make_confidence_map(), _make_summary())
        texts = _collect_texts(card)
        all_text = " ".join(texts)
        assert "Balance Sheet" in all_text
        assert "Income Statement" in all_text
        assert "Cash Flow" in all_text

    def test_navigator_high_confidence_shows_checkmark(self):
        card = build_navigator_card("Acme", "GBP", "thousands", _make_confidence_map(), _make_summary())
        texts = _collect_texts(card)
        found = [t for t in texts if "High" in t and "\u2713" in t]
        assert len(found) >= 1, "Expected at least one '✓ High' text"

    def test_navigator_medium_shows_warning(self):
        card = build_navigator_card("Acme", "GBP", "thousands", _make_confidence_map(), _make_summary())
        texts = _collect_texts(card)
        found = [t for t in texts if "Medium" in t and "\u26a0" in t and "issues" in t.lower()]
        assert len(found) >= 1, "Expected at least one '⚠ Medium (N issues)' text"

    def test_navigator_actions(self):
        card = build_navigator_card("Acme", "GBP", "thousands", _make_confidence_map(), _make_summary())
        actions = card.get("actions", [])
        titles = [a["title"] for a in actions]
        assert "Start Review" in titles
        assert "Skip Review & Generate Excel" in titles


# ═══════════════════════════════════════════════════════════════════════════════
# Statement review card tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatementReviewCard:
    def test_review_card_has_header(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [0])
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        texts = _collect_texts(card)
        all_text = " ".join(texts)
        assert "Balance Sheet" in all_text
        assert "Medium" in all_text

    def test_flagged_rows_have_input_fields(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [0, 1])
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        inputs = _collect_elements(card, "Input.Text")
        # flagged rows 0 and 1 should each have label + N value inputs
        ids = [inp["id"] for inp in inputs]
        assert "row_0_label" in ids
        assert "row_1_label" in ids
        assert "row_0_val_0" in ids

    def test_flagged_rows_have_section_dropdown(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [0])
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        choice_sets = _collect_elements(card, "Input.ChoiceSet")
        ids = [cs["id"] for cs in choice_sets]
        assert "row_0_section" in ids

    def test_flagged_row_ids_match_pattern(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [2])
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        inputs = _collect_elements(card, "Input.Text")
        choice_sets = _collect_elements(card, "Input.ChoiceSet")
        all_ids = [el["id"] for el in inputs + choice_sets]
        # All IDs for row 2 should start with "row_2_"
        row2_ids = [i for i in all_ids if i.startswith("row_2_")]
        assert len(row2_ids) >= 3  # label + val_0 + val_1 at minimum

    def test_clean_rows_in_show_card(self):
        stmt = _make_test_statement(num_rows=10)
        conf = _make_test_confidence("medium", [0])
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        # Should have an Action.ShowCard containing clean rows
        show_cards = _collect_elements(card, "Action.ShowCard")
        assert len(show_cards) >= 1, "Expected at least one Action.ShowCard for clean rows"

    def test_high_confidence_readonly(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("high")
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3, editable=False)
        inputs = _collect_elements(card, "Input.Text")
        assert len(inputs) == 0, "Read-only card should have no Input.Text elements"
        # Should have TextBlock elements for values
        text_blocks = _collect_elements(card, "TextBlock")
        assert len(text_blocks) > 0

    def test_high_confidence_has_edit_button(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("high")
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3, editable=False)
        actions = _collect_actions(card)
        titles = [a.get("title", "") for a in actions]
        assert "Edit" in titles

    def test_corrections_prepopulate_values(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [0])
        corrections = {"row_0": {"label": "Trade debtors"}}
        card = build_statement_review_card("balance_sheet", stmt, conf, corrections, 1, 3)
        inputs = _collect_elements(card, "Input.Text")
        label_input = [inp for inp in inputs if inp["id"] == "row_0_label"]
        assert len(label_input) == 1
        assert label_input[0]["value"] == "Trade debtors"

    def test_navigation_actions_step1(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [0])
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        actions = card.get("actions", [])
        titles = [a["title"] for a in actions]
        assert any("Next" in t for t in titles)
        assert not any("Previous" in t for t in titles)

    def test_navigation_actions_step2(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [0])
        card = build_statement_review_card("income_statement", stmt, conf, {}, 2, 3)
        actions = card.get("actions", [])
        titles = [a["title"] for a in actions]
        assert any("Previous" in t for t in titles)
        assert any("Next" in t for t in titles)

    def test_navigation_actions_last_step(self):
        stmt = _make_test_statement()
        conf = _make_test_confidence("medium", [0])
        card = build_statement_review_card("cash_flow", stmt, conf, {}, 3, 3)
        actions = card.get("actions", [])
        titles = [a["title"] for a in actions]
        assert any("Previous" in t for t in titles)
        assert any("Submit" in t for t in titles)
        assert not any("Next" in t for t in titles)

    def test_empty_statement_shows_no_data(self):
        stmt = _make_test_statement(num_rows=0)
        conf = _make_test_confidence("medium")
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        texts = _collect_texts(card)
        all_text = " ".join(texts)
        assert "No data extracted" in all_text

    def test_card_max_clean_rows_15(self):
        stmt = _make_test_statement(num_rows=30)
        conf = _make_test_confidence("medium", [0])
        card = build_statement_review_card("balance_sheet", stmt, conf, {}, 1, 3)
        # Find the ShowCard for clean rows
        show_cards = _collect_elements(card, "Action.ShowCard")
        assert len(show_cards) >= 1
        inner_card = show_cards[0].get("card", {})
        inner_inputs = _collect_elements(inner_card, "Input.Text")
        # 15 clean rows * (1 label + 2 values) = 45 input fields max
        # Each row has: label + num_columns values
        max_inputs = 15 * 3  # label + val_0 + val_1
        assert len(inner_inputs) <= max_inputs
        # Also check that a "Showing 15 of" note exists
        texts = _collect_texts(inner_card)
        all_text = " ".join(texts)
        assert "Showing 15 of" in all_text


# ═══════════════════════════════════════════════════════════════════════════════
# Parse submission tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseSubmission:
    def test_parse_extracts_action(self):
        stmt = _make_test_statement()
        payload = {"action": "next"}
        action, corrections = parse_card_submission(payload, stmt)
        assert action == "next"

    def test_parse_detects_changed_label(self):
        stmt = _make_test_statement()
        payload = {"action": "next", "row_0_label": "Trade debtors"}
        action, corrections = parse_card_submission(payload, stmt)
        assert "row_0" in corrections
        assert corrections["row_0"]["label"] == "Trade debtors"

    def test_parse_ignores_unchanged_values(self):
        stmt = _make_test_statement()
        # Original label for row 0 is "Item 0", original val_0 is "100"
        payload = {"action": "next", "row_0_label": "Item 0", "row_0_val_0": "100"}
        action, corrections = parse_card_submission(payload, stmt)
        assert "row_0" not in corrections

    def test_parse_detects_changed_value(self):
        stmt = _make_test_statement()
        payload = {"action": "next", "row_0_val_0": "999"}
        action, corrections = parse_card_submission(payload, stmt)
        assert "row_0" in corrections
        assert corrections["row_0"]["val_0"] == "999"

    def test_parse_detects_changed_section(self):
        stmt = _make_test_statement()
        payload = {"action": "next", "row_0_section": "equity"}
        action, corrections = parse_card_submission(payload, stmt)
        assert "row_0" in corrections
        assert corrections["row_0"]["section"] == "equity"

    def test_parse_empty_payload_no_corrections(self):
        stmt = _make_test_statement()
        payload = {"action": "next"}
        action, corrections = parse_card_submission(payload, stmt)
        assert corrections == {}
