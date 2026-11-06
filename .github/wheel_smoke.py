# Run by cibuildwheel against each built wheel, from outside the checkout. The test suite runs
# in CI against the checkout - a wheel only has to load its extension and build a working State,
# which is what differs between the wheels.
import os

# the native implementation is opt-in - this checks the wheel carries a working one
os.environ["HAIWAY_NATIVE"] = "1"

from haiway import State  # noqa: E402
from haiway._native import NATIVE  # noqa: E402

assert NATIVE, "the wheel carries no working native extension"


class Point(State):
    x: int


point = Point(x=1)
assert point.updating(x=2).x == 2
assert point.to_mapping() == {"x": 1}

try:
    object.__setattr__(point, "x", 3)

except AttributeError:
    pass

else:
    raise AssertionError("the native State can be modified")

print("wheel smoke test passed")
