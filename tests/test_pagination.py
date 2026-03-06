import pytest

from haiway import Paginated, Pagination


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
    page = Paginated[int].of([1, 2, 3], pagination=Pagination(limit=3, arguments={}))

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
