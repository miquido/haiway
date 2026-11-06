import uuid
from collections.abc import Mapping, Sequence

from haiway import State
from haiway.context.identifier import ContextIdentifier, random_identifier


class Leaf(State):
    number: int


class Branch(State):
    leaf: Leaf
    leaves: Sequence[Leaf]
    named: Mapping[str, Leaf]


def test_path_reached_the_same_way_is_the_same_path() -> None:
    assert Branch._.leaf is Branch._.leaf  # pyright: ignore[reportAttributeAccessIssue]
    assert Branch._.leaf.number is Branch._.leaf.number  # pyright: ignore[reportAttributeAccessIssue]
    assert Branch._.leaves[0] is Branch._.leaves[0]  # pyright: ignore[reportAttributeAccessIssue]
    assert Branch._.named["a"].number is Branch._.named["a"].number  # pyright: ignore[reportAttributeAccessIssue]


def test_paths_which_differ_are_different_paths() -> None:
    assert Branch._.leaves[0] is not Branch._.leaves[1]  # pyright: ignore[reportAttributeAccessIssue]
    assert Branch._.named["1"] is not Branch._.named[1]  # pyright: ignore[reportAttributeAccessIssue]
    assert Branch._.leaf is not Branch._.leaves  # pyright: ignore[reportAttributeAccessIssue]


def test_cached_path_reads_and_updates_every_time() -> None:
    branch = Branch(
        leaf=Leaf(number=1),
        leaves=[Leaf(number=2), Leaf(number=3)],
        named={"a": Leaf(number=4)},
    )
    path = Branch._.leaves[1].number  # pyright: ignore[reportAttributeAccessIssue]

    assert path(branch) == 3
    assert Branch._.leaves[1].number(branch) == 3  # pyright: ignore[reportAttributeAccessIssue]

    updated = path(branch, 30)

    assert updated.leaves[1].number == 30
    assert branch.leaves[1].number == 3
    assert Branch._.leaves[1].number(updated) == 30  # pyright: ignore[reportAttributeAccessIssue]
    assert str(Branch._.named["a"].number) == "named[a].number"  # pyright: ignore[reportAttributeAccessIssue]


def test_path_still_refuses_what_is_not_an_attribute() -> None:
    try:
        Branch._.missing  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]

    except AttributeError, AssertionError:
        pass

    else:
        raise AssertionError("an attribute which does not exist was reached")


def test_random_identifier_is_a_version_four_uuid() -> None:
    identifier = random_identifier()

    assert isinstance(identifier, uuid.UUID)
    assert identifier.version == 4
    assert identifier.variant == uuid.RFC_4122


def test_random_identifiers_do_not_repeat() -> None:
    identifiers = {random_identifier() for _ in range(20_000)}

    assert len(identifiers) == 20_000


def test_scopes_are_given_identifiers_of_that_kind() -> None:
    scope = ContextIdentifier.scope("test")

    assert scope.scope_id.version == 4
    assert scope.parent_id == scope.scope_id  # the root is its own parent
