from pytest import mark

from tests.managed_sim import Sim


@mark.parametrize("seed", range(400))
def test_random_interleavings_keep_the_invariants_of_the_coordinator(seed: int) -> None:
    # two runtimes on one store with faults: lost replies, expired leases, a close at any time
    Sim(seed).run()
