from copy import copy, deepcopy
from datetime import datetime
from uuid import uuid4

from pytest import raises

from haiway.utils.metadata import META_EMPTY, Meta, _validated_meta_value


def test_meta_empty_is_singleton():
    # META_EMPTY is only returned for None, not empty dict
    assert Meta.of(None) is META_EMPTY
    assert not META_EMPTY
    assert len(META_EMPTY) == 0

    # Empty dict creates a new instance (but still empty)
    empty_from_dict = Meta.of({})
    assert not empty_from_dict
    assert len(empty_from_dict) == 0


def test_meta_basic_construction():
    meta = Meta({"kind": "test", "name": "example"})
    assert meta["kind"] == "test"
    assert meta["name"] == "example"
    assert len(meta) == 2
    assert bool(meta) is True


def test_meta_construction_validates_values():
    # Direct constructor doesn't validate - use of() or from_mapping()
    meta = Meta.from_mapping({"tags": ["a", "b"], "nested": {"key": "value"}})
    assert meta["tags"] == ("a", "b")  # converted to tuple
    assert meta["nested"] == {"key": "value"}


def test_meta_of_with_none_returns_empty():
    result = Meta.of(None)
    assert result is META_EMPTY


def test_meta_of_with_existing_meta_returns_same():
    original = Meta({"kind": "test"})
    result = Meta.of(original)
    assert result is original


def test_meta_of_with_mapping_validates():
    result = Meta.of({"tags": ["a", "b"], "active": True})
    assert result["tags"] == ("a", "b")
    assert result["active"] is True


def test_meta_from_mapping():
    result = Meta.from_mapping({"kind": "user", "count": 42})
    assert result.kind == "user"
    assert result["count"] == 42


def test_meta_from_json_valid():
    json_data = '{"kind": "test", "tags": ["a", "b"], "active": true}'
    result = Meta.from_json(json_data)
    assert result.kind == "test"
    assert result["tags"] == ("a", "b")
    assert result["active"] is True


def test_meta_from_json_invalid():
    with raises(ValueError, match="Invalid json"):
        Meta.from_json('"not an object"')


def test_meta_to_json():
    meta = Meta({"kind": "test", "active": True})
    json_str = meta.to_json()
    assert "kind" in json_str
    assert "test" in json_str


def test_meta_to_mapping():
    original_mapping = {"kind": "test", "active": True}
    meta = Meta(original_mapping)
    result = meta.to_mapping()
    assert result == original_mapping


def test_meta_kind_property():
    meta = Meta({"kind": "user"})
    assert meta.kind == "user"

    empty_meta = Meta({})
    assert empty_meta.kind is None

    with raises(TypeError, match="Unexpected value 'int' for kind, expected 'str'"):
        invalid_meta = Meta({"kind": 123})
        _ = invalid_meta.kind


def test_meta_with_kind():
    meta = Meta({"name": "test"})
    updated = meta.with_kind("user")
    assert updated.kind == "user"
    assert updated["name"] == "test"
    assert meta.kind is None  # original unchanged


def test_meta_name_property():
    meta = Meta({"name": "John"})
    assert meta.name == "John"

    empty_meta = Meta({})
    assert empty_meta.name is None


def test_meta_with_name():
    meta = Meta({"kind": "user"})
    updated = meta.with_name("Jane")
    assert updated.name == "Jane"
    assert updated.kind == "user"


def test_meta_description_property():
    meta = Meta({"description": "A test object"})
    assert meta.description == "A test object"

    empty_meta = Meta({})
    assert empty_meta.description is None


def test_meta_with_description():
    meta = Meta({"name": "test"})
    updated = meta.with_description("Test description")
    assert updated.description == "Test description"
    assert updated.name == "test"


def test_meta_identifier_property():
    test_uuid = uuid4()
    meta = Meta({"identifier": str(test_uuid)})
    assert meta.identifier == test_uuid

    empty_meta = Meta({})
    assert empty_meta.identifier is None

    with raises(ValueError, match="badly formed hexadecimal UUID string"):
        invalid_meta = Meta({"identifier": "not-a-uuid"})
        _ = invalid_meta.identifier


def test_meta_with_identifier():
    test_uuid = uuid4()
    meta = Meta({"name": "test"})
    updated = meta.with_identifier(test_uuid)
    assert updated.identifier == test_uuid
    assert updated["identifier"] == str(test_uuid)


def test_meta_tags_property():
    meta = Meta({"tags": ["active", "verified"]})
    assert meta.tags == ("active", "verified")

    empty_meta = Meta({})
    assert empty_meta.tags == ()

    with raises(TypeError, match="Unexpected value 'int' for tag, expected 'str'"):
        mixed_meta = Meta({"tags": ["valid", 123, "also_valid"]})
        _ = mixed_meta.tags


def test_meta_with_tags_new():
    meta = Meta({})
    updated = meta.with_tags(["new", "tags"])
    assert updated.tags == ("new", "tags")


def test_meta_with_tags_append():
    meta = Meta({"tags": ["existing"]})
    updated = meta.with_tags(["new", "existing"])  # existing should be deduplicated
    assert "existing" in updated.tags
    assert "new" in updated.tags


def test_meta_has_tags():
    meta = Meta({"tags": ["active", "verified", "premium"]})
    assert meta.has_tags(["active"]) is True
    assert meta.has_tags(["active", "verified"]) is True
    assert meta.has_tags(["nonexistent"]) is False
    assert meta.has_tags(["active", "nonexistent"]) is False


def test_meta_creation_property():
    now = datetime.now()
    meta = Meta({"creation": now.isoformat()})
    # Compare by converting both back to isoformat to handle precision
    assert meta.creation.isoformat() == now.isoformat()

    empty_meta = Meta({})
    assert empty_meta.creation is None

    with raises(ValueError, match="Invalid isoformat string: 'not-a-date'"):
        invalid_meta = Meta({"creation": "not-a-date"})
        _ = invalid_meta.creation


def test_meta_with_creation():
    now = datetime.now()
    meta = Meta({})
    updated = meta.with_creation(now)
    assert updated.creation is not None
    assert updated["creation"] == now.isoformat()


def test_meta_merged_with():
    original = Meta({"name": "original", "kind": "test"})
    updates = {"name": "updated", "description": "merged"}

    merged = original.merged_with(updates)
    assert merged["name"] == "updated"
    assert merged["kind"] == "test"  # preserved
    assert merged["description"] == "merged"  # added


def test_meta_merged_with_none():
    original = Meta({"name": "test"})
    result = original.merged_with(None)
    assert result is original  # same instance returned


def test_meta_merged_with_meta_instance():
    original = Meta({"name": "original"})
    other = Meta({"name": "updated", "kind": "test"})

    merged = original.merged_with(other)
    assert merged["name"] == "updated"
    assert merged["kind"] == "test"


def test_meta_excluding():
    original = Meta({"name": "test", "kind": "user", "description": "desc"})

    result = original.excluding("kind", "description")
    assert "name" in result
    assert "kind" not in result
    assert "description" not in result


def test_meta_excluding_none():
    original = Meta({"name": "test"})
    result = original.excluding()
    assert result is original  # same instance


def test_meta_updated():
    original = Meta({"name": "original"})
    updated = original.updated(name="updated", kind="test")
    assert updated["name"] == "updated"
    assert updated["kind"] == "test"


def test_meta_immutability():
    meta = Meta({"name": "test"})

    with raises(AttributeError, match="Can't modify immutable"):
        meta.name = "changed"  # type: ignore

    with raises(AttributeError, match="Can't modify immutable"):
        del meta.name  # type: ignore

    with raises(AttributeError, match="Can't modify immutable"):
        meta["name"] = "changed"  # type: ignore

    with raises(AttributeError, match="Can't modify immutable"):
        del meta["name"]  # type: ignore


def test_meta_mapping_interface():
    meta = Meta({"a": 1, "b": 2, "c": 3})

    # Test iteration
    keys = list(meta)
    assert set(keys) == {"a", "b", "c"}

    # Test containment
    assert "a" in meta
    assert "d" not in meta

    # Test get method (inherited from Mapping)
    assert meta.get("a") == 1
    assert meta.get("d") is None
    assert meta.get("d", "default") == "default"


def test_meta_copy_returns_same_instance():
    meta = Meta({"name": "test"})
    assert copy(meta) is meta
    assert deepcopy(meta) is meta


def test__validated_meta_value_primitives():
    assert _validated_meta_value(None) is None
    assert _validated_meta_value("string") == "string"
    assert _validated_meta_value(42) == 42
    assert _validated_meta_value(3.14) == 3.14
    assert _validated_meta_value(True) is True
    assert _validated_meta_value(False) is False


def test__validated_meta_value_collections():
    # Lists become tuples
    assert _validated_meta_value([1, 2, 3]) == (1, 2, 3)

    # Nested validation
    result = _validated_meta_value([1, [2, 3], {"a": "b"}])
    assert result == (1, (2, 3), {"a": "b"})

    # Dicts are validated recursively
    result = _validated_meta_value({"list": [1, 2], "nested": {"key": "value"}})
    assert result == {"list": (1, 2), "nested": {"key": "value"}}


def test__validated_meta_value_invalid():
    class CustomObject:
        pass

    with raises(TypeError, match="Invalid Meta value"):
        _validated_meta_value(CustomObject())

    with raises(TypeError, match="Invalid Meta value"):
        _validated_meta_value(object())
