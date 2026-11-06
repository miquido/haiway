# Benchmarks

Micro benchmarks of `State` operations - creation, validation, updates, reads, equality, hashing,
type checks, conversion (mapping, basic object, JSON, schema), representation, copying and class
definition. The script depends on the standard library only and is not a part of the test run.

```bash
make benchmark                                      # run every case
make benchmark ARGS="--filter update/"              # cases whose name contains the text
make benchmark ARGS="--json mine.json"              # save the results
make benchmark ARGS="--compare benchmarks/baseline.json"   # ratio against saved results
```

Each case reports the best of five repeats in nanoseconds per call.

## Comparing revisions

Single runs are noisy - the same code can swing by a factor of two when the machine is busy, which
is more than most changes are worth. To tell a real change from noise:

1. Run the old and the new revision **alternately**, several times each (a `git worktree` of the old
   revision with `PYTHONPATH=<worktree>/src` selects it without touching the environment).
1. Compare the **best** result of each side per case.
1. Treat differences under about 5-10% as noise unless they repeat.

## Baseline

`baseline.json` holds the best of several full runs taken before any optimization, to measure
improvements against. It is machine specific - the numbers are meaningful as ratios between runs
made on the same machine, so re-take it when the machine changes:

```bash
make benchmark ARGS="--json benchmarks/baseline.json"   # repeat and keep the best of each case
```

Recorded with CPython 3.15.1 on an Apple silicon laptop (macOS).

## Reading the numbers

The `reference/` cases are a frozen dataclass doing the same work without validation - the floor a
`State` operation can not go below. Conversion cases (`to_basic_object`, `to_json`) are the slowest
relative to the size of the data, and the first candidates for optimization.
