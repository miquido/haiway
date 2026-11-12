import copy
from uuid import UUID

import pytest

from haiway import Paginated, Pagination, PaginationToken


def test_pagination_first_builds_empty_token_request() -> None:
    pagination = Pagination(limit=10, arguments={"sort": "asc"})

    assert pagination.token is None
    assert pagination.limit == 10
    assert pagination.arguments == {"sort": "asc"}
    assert pagination.has_token is False


def test_pagination_with_token_and_argument_merge() -> None:
    pagination = Pagination(limit=20, arguments={"sort": "asc"})
    next_page = pagination.with_token("cursor-2").with_arguments(region="eu", sort="desc")

    assert next_page.token == "cursor-2"
    assert next_page.arguments == {"sort": "desc", "region": "eu"}
    assert pagination.token is None
    assert pagination.arguments == {"sort": "asc"}
    assert next_page.to_mapping() == {
        "token": "cursor-2",
        "limit": 20,
        "arguments": {"sort": "desc", "region": "eu"},
    }


def test_pagination_limit_validation() -> None:
    pagination = Pagination(limit=0, arguments={})
    assert pagination.limit == 0

    updated = Pagination(limit=10, arguments={}).with_limit(-5)
    assert updated.limit == -5


def test_paginated_is_sequence_and_immutable() -> None:
    page = Paginated([1, 2, 3], pagination=Pagination(limit=3, arguments={}))

    assert len(page) == 3
    assert list(page) == [1, 2, 3]
    assert page[1] == 2
    assert page.token is None
    assert page.has_next_page is True

    with pytest.raises(AttributeError):
        page.items = (4, 5)

    assert isinstance(page.items, tuple)

    with pytest.raises(TypeError):
        page.items[0] = 4

    with pytest.raises(AttributeError):
        page.items.append(4)


def test_pagination_of_builds_state_from_keyword_arguments() -> None:
    pagination = Pagination.of(token="abc", limit=5, sort="asc", active=True)

    assert pagination.token == "abc"
    assert pagination.limit == 5
    assert pagination.arguments == {"sort": "asc", "active": True}
    assert pagination.has_token is True


def test_pagination_of_defaults_to_first_page() -> None:
    pagination = Pagination.of(limit=5)

    assert pagination.token is None
    assert pagination.arguments == {}


@pytest.mark.parametrize("token", ["cursor", 7, 0, UUID("12345678-1234-5678-1234-567812345678")])
def test_pagination_accepts_all_token_kinds(token: PaginationToken) -> None:
    pagination = Pagination.of(token=token, limit=1)

    assert pagination.token == token
    assert pagination.has_token is True


def test_pagination_with_token_can_clear_token_and_keeps_original() -> None:
    original = Pagination.of(token="a", limit=3)

    cleared = original.with_token(None)

    assert cleared.token is None
    assert cleared.has_token is False
    assert cleared.limit == 3
    assert original.token == "a"


def test_pagination_with_limit_keeps_other_fields() -> None:
    original = Pagination.of(token="a", limit=3, sort="asc")

    updated = original.with_limit(50)

    assert updated.limit == 50
    assert updated.token == "a"
    assert updated.arguments == {"sort": "asc"}
    assert original.limit == 3


def test_pagination_with_arguments_without_values_returns_same_instance() -> None:
    pagination = Pagination.of(limit=3, sort="asc")

    assert pagination.with_arguments() is pagination


def test_pagination_with_arguments_does_not_modify_original() -> None:
    original = Pagination.of(limit=3, sort="asc")

    updated = original.with_arguments(region="eu")

    assert updated.arguments == {"sort": "asc", "region": "eu"}
    assert original.arguments == {"sort": "asc"}


def test_paginated_exposes_items_and_pagination() -> None:
    pagination = Pagination.of(token="next", limit=2)
    page: Paginated[str] = Paginated(iter(["a", "b"]), pagination=pagination)

    assert page.items == ("a", "b")
    assert page.pagination is pagination
    assert page.token == "next"
    assert len(page) == 2
    assert list(page) == ["a", "b"]
    assert page[0] == "a"
    assert page[-1] == "b"
    assert page[:1] == ("a",)
    assert "a" in page
    assert list(reversed(page)) == ["b", "a"]


def test_paginated_does_not_follow_source_mutations() -> None:
    source = [1, 2]
    page: Paginated[int] = Paginated(source, pagination=Pagination.of(limit=10))

    source.append(3)

    assert page.items == (1, 2)


@pytest.mark.parametrize(
    ("items", "token", "limit", "expected"),
    [
        (3, "cursor", 10, True),  # token wins over a short page
        (3, 0, 10, True),  # falsy token is still a token
        (3, None, 10, False),  # short page without a token is the last one
        (10, None, 10, True),  # full page may continue
        (11, None, 10, True),  # oversized page may continue
        (0, None, 10, False),
        (0, None, 0, True),  # zero limit - every page counts as full
    ],
)
def test_paginated_has_next_page(
    items: int,
    token: PaginationToken | None,
    limit: int,
    expected: bool,
) -> None:
    page: Paginated[int] = Paginated(
        range(items), pagination=Pagination.of(token=token, limit=limit)
    )

    assert page.has_next_page is expected


def test_paginated_empty_page_is_falsy() -> None:
    page: Paginated[int] = Paginated((), pagination=Pagination.of(limit=1))

    assert not page
    assert len(page) == 0


def test_paginated_rejects_attribute_changes() -> None:
    page: Paginated[int] = Paginated([1], pagination=Pagination.of(limit=1))

    with pytest.raises(AttributeError, match="cannot be modified"):
        page.items = ()  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError, match="cannot be deleted"):
        del page.pagination  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        page.other = 1  # pyright: ignore[reportAttributeAccessIssue]


def test_paginated_str_repr_and_copies() -> None:
    page: Paginated[int] = Paginated([1, 2], pagination=Pagination.of(limit=2))

    assert str(page).startswith("Paginated(items: (1, 2), pagination: ")
    assert repr(page) == str(page)
    assert copy.copy(page) is page
    assert copy.deepcopy(page) is page
