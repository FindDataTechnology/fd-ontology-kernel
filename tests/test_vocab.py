"""Vocabularies: closed sets, additive releases, one URI constructor."""

from __future__ import annotations

import pytest

from fd_ontology_kernel.errors import VocabEntryRemoved, VocabOutOfSet
from fd_ontology_kernel.vocab import (
    Entry,
    Vocabulary,
    VocabularySet,
    assert_additive,
    uri_for,
)


def _vocabulary(release: str, *, types: tuple[str, ...], rels: tuple[str, ...]) -> Vocabulary:
    return Vocabulary.from_values(
        release=release,
        values={"entity_types": types, "relationship_kinds": rels},
    )


def test_closed_set_refuses_and_lists_the_alternatives() -> None:
    vocabulary = _vocabulary("r1", types=("law", "clause"), rels=("cites_law",))
    with pytest.raises(VocabOutOfSet) as caught:
        vocabulary.require("entity_types", "penalty", scope_id="d1")
    assert caught.value.context["allowed"] == ["law", "clause"]
    assert caught.value.context["scope_id"] == "d1"


def test_unknown_set_is_also_refused() -> None:
    vocabulary = _vocabulary("r1", types=("law",), rels=("cites_law",))
    with pytest.raises(VocabOutOfSet):
        vocabulary.require("event_types", "law")


def test_additive_release_is_accepted() -> None:
    old = _vocabulary("r1", types=("law", "clause"), rels=("cites_law",))
    new = _vocabulary("r2", types=("law", "clause", "concept"), rels=("cites_law", "defines"))
    assert_additive(old, new)  # no raise


def test_removing_an_entry_fails_and_names_it() -> None:
    old = _vocabulary("r1", types=("law", "clause", "concept"), rels=("cites_law",))
    new = _vocabulary("r2", types=("law", "clause"), rels=("cites_law",))
    with pytest.raises(VocabEntryRemoved) as caught:
        assert_additive(old, new)
    assert caught.value.context["entries"] == [{"set": "entity_types", "id": "concept"}]
    assert "concept" in str(caught.value)


def test_renaming_an_id_reads_as_a_removal() -> None:
    old = _vocabulary("r1", types=("law",), rels=("cites_law",))
    new = _vocabulary("r2", types=("statute",), rels=("cites_law",))
    with pytest.raises(VocabEntryRemoved) as caught:
        assert_additive(old, new)
    assert caught.value.context["entries"] == [{"set": "entity_types", "id": "law"}]


def test_retiring_with_a_successor_is_accepted() -> None:
    old = Vocabulary(
        release="r1",
        sets={
            "entity_types": VocabularySet(
                name="entity_types",
                values=("law",),
                entries={"law": Entry(id="law", set_name="entity_types")},
            )
        },
    )
    new = Vocabulary(
        release="r2",
        sets={
            "entity_types": VocabularySet(
                name="entity_types",
                values=("law", "statute"),
                entries={
                    "law": Entry(id="law", set_name="entity_types", superseded_by="statute"),
                    "statute": Entry(id="statute", set_name="entity_types"),
                },
            )
        },
    )
    assert_additive(old, new)
    assert new.set("entity_types").entry("law").retired is True


def test_first_release_skips_the_comparison() -> None:
    assert_additive(None, _vocabulary("r1", types=("law",), rels=("cites_law",)))


def test_removing_a_whole_set_fails() -> None:
    old = _vocabulary("r1", types=("law",), rels=("cites_law",))
    new = Vocabulary.from_values(release="r2", values={"entity_types": ["law"]})
    with pytest.raises(VocabEntryRemoved) as caught:
        assert_additive(old, new)
    assert caught.value.context["sets"] == ["relationship_kinds"]


def test_uri_constructor_is_one_rule_for_everyone() -> None:
    assert uri_for("concept", "GDP") == "https://schema.finddata.tech/concept/GDP"
    assert uri_for("/entity-type/", "drug") == "https://schema.finddata.tech/entity-type/drug"
    with pytest.raises(ValueError):
        uri_for("", "x")


def test_entry_exposes_its_uri_and_retirement() -> None:
    entry = Entry(id="law", set_name="entity-type", label_en="Law", superseded_by="statute")
    assert entry.uri == "https://schema.finddata.tech/entity-type/law"
    assert entry.retired


def test_vocabulary_needs_a_release_and_a_set() -> None:
    with pytest.raises(ValueError):
        Vocabulary(release="", sets={})
    with pytest.raises(ValueError):
        Vocabulary(release="r1", sets={})
