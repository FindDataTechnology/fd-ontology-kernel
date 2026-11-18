"""Keys: clause addressing and rule-driven folding, moved out of the domain pack."""

from __future__ import annotations

import pytest

from fd_ontology_kernel import keys
from fd_ontology_kernel.keys import (
    FoldRule,
    chinese_to_int,
    clause_key,
    clause_no,
    concept_shape_ok,
    fold,
    slug,
    strip_trailing_qualifier,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("第十二条", 12),
        ("第12条", 12),
        ("第一百二十三条", 123),
        ("第两百条", 200),
        ("第十二条 合同的订立", 12),
        ("序言", None),
        ("序　言", None),
        ("目录", None),
        ("附则", None),
        ("第二章 合同的订立", None),
        ("第三节", None),
        ("没有任何条款", None),
    ],
)
def test_clause_no(text: str, expected: int | None) -> None:
    assert clause_no(text) == expected


def test_structural_names_win_over_the_numeral_path() -> None:
    # "第二章" contains 二, which would otherwise read as clause 2.
    assert chinese_to_int("二") == 2
    assert clause_no("第二章") is None


def test_clause_key_requires_a_parent() -> None:
    assert clause_key("合同法", 12) == "合同法#第12条"
    with pytest.raises(ValueError):
        clause_key("", 12)
    with pytest.raises(ValueError):
        clause_key("合同法", 0)


def test_fold_is_ordered_and_explainable() -> None:
    rules = (
        FoldRule("drop-prc", "prefix", "中华人民共和国"),
        FoldRule("drop-suffix", "suffix", "（2018年修正文本）"),
    )
    value, applied = fold("中华人民共和国宪法（2018年修正文本）", rules)
    assert value == "宪法"
    assert applied == ["drop-prc", "drop-suffix"]


def test_fold_does_nothing_without_rules() -> None:
    value, applied = fold("中华人民共和国宪法")
    assert value == "中华人民共和国宪法"
    assert applied == []


def test_strip_trailing_qualifier_only_once() -> None:
    assert strip_trailing_qualifier("宪法（2018年修正文本）") == "宪法"
    assert strip_trailing_qualifier("宪法（2018）（试行）") == "宪法（2018）"


def test_slug_is_stable_and_bounded() -> None:
    assert slug("合同法 / 第十二条") == "合同法-第十二条"
    assert slug("") == "unnamed"
    assert len(slug("字" * 500)) == 80


def test_concept_shape_rejects_sentences_and_overlong_names() -> None:
    # The kernel owns generic shape only: a noun phrase, short, no sentence
    # punctuation. Domain markers ("数额", "被告", "决定") belong to the pack —
    # asserting them here would put domain vocabulary back into the kernel.
    assert concept_shape_ok("违约责任")
    assert not concept_shape_ok("行政处罚违反法定程序，应当撤销")
    assert not concept_shape_ok("责任" * 13)
    assert not concept_shape_ok("")


def test_rule_registry_defaults_to_no_rules() -> None:
    assert keys.rules_for("no-such-domain") == ()
    assert keys.registered() == [] or "no-such-domain" not in keys.registered()


def test_registering_rules_is_per_domain() -> None:
    keys.register_rules("test-domain", (FoldRule("x", "prefix", "甲"),))
    assert len(keys.rules_for("test-domain")) == 1
    assert keys.rules_for("other-domain") == ()
