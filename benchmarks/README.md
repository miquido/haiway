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

## OpenTelemetry bridge

`opentelemetry_benchmark.py` measures scope entry and exit, logs below and above the threshold,
metrics, attributes and events under three setups: `bare` (no backend of its own), `logger` (the
standard library logger backend) and `otel` (the bridge on SDK providers with batch processors
writing into exporters which drop everything). Providers are claimed once per process, so it owns
its process. Results are nanoseconds per call and take the same `--json`, `--compare`, `--filter`
and `--budget` options. Most of the `otel` cost is the SDK itself - creating spans and log records.

```bash
make benchmark-otel
```

## Server integrations

`server_benchmark.py` measures the Starlette and FastAPI integrations. Requests are driven through
the ASGI interface in process, so the numbers are the cost of the application stack alone. Each
`haiway` case has a `bare` counterpart - the same application without the integration - and the
difference between them is its overhead per request. It takes the same `--json`, `--compare`,
`--filter` and `--budget` options.

```bash
make benchmark-server                                              # run with -O
make benchmark-server ARGS="--compare benchmarks/server_baseline.json"
```

It runs with `python -O`, as production does - without it observability keeps debug bookkeeping
which roughly doubles the overhead. `server_baseline.json` was recorded the same way as
`baseline.json`, in microseconds per request.

## HTTP client

`http_benchmark.py` measures the HTTP client: building a response, the `HTTPClient` facade over a
stub backend, and requests and streamed responses through `HTTPXClient`. Requests go through
`httpx2.MockTransport` in process, so the numbers are the cost of the client stack alone - what a
real round trip adds to. Each `haiway` case has a `bare` counterpart doing the same with `httpx2`
alone, and the difference between them is the overhead of the client. Stream cases are per chunk,
of 64 bytes - the worst case, as a real body arrives in chunks of kilobytes. It takes the same
`--json`, `--compare`, `--filter` and `--budget` options.

```bash
make benchmark-http                                              # run with -O
make benchmark-http ARGS="--compare benchmarks/http_baseline.json"
```

`http_baseline.json` was recorded before the facade read the URL of a request once and the
streamed body dropped its pass-through frames, in microseconds per call. Most of the cost of a
request is `httpx2` itself - parsing its URL above all; the facade adds the recording of the request
and the response.

## Context

`scope_benchmark.py` measures the context: entering and exiting scopes (root, nested, with states,
isolated, deep), looking state up, updating it, spawning tasks and sending events. The `floor`
cases are the primitives a scope is made of - a context variable, an async context manager, a task
group and a task - so the difference between them and a scope is the cost of the context itself.
It takes the same `--json`, `--compare`, `--filter` and `--budget` options.

```bash
make benchmark-scope                                              # run with -O
make benchmark-scope ARGS="--compare benchmarks/scope_baseline.json"
```

`scope_baseline.json` was recorded before scopes were made cheaper to enter and exit, in
microseconds per call. Looking state up, updating it and spawning cost what the primitives beneath
them do; a scope costs a few times its floor, spread over its parts - the identifier, observability,
state, the task group and the closing of the scope - none of which dominates.

## Native storage

`native_baseline.json` is the same set of cases taken with the compiled extension, which is what
runs - the file to measure a change against. `baseline.json` is what the pure Python implementation
measured before it was dropped, so the two files together are the speedup:

```bash
make benchmark ARGS="--compare benchmarks/native_baseline.json"
make benchmark ARGS="--compare benchmarks/baseline.json"
```

A class called with keyword arguments is built without a dictionary of them, which is why
`create/defaulted` and `create/aliased (by name)` are faster than `create/simple` and
`create/aliased (by alias)` - those unpack a mapping (`Simple(**values)`), which has to be turned
back into keywords for the call. Validating a mapping (`Simple.validate(values)`) does not pay for
it.

## Improvements outside the native code

Some of the improvements are made in Python: `read/path` (the paths of `State._`
are kept once they are made), and the identifiers of scopes and traces (a random version 4 UUID
without a system call) which the `haiway` cases of `server_benchmark.py` pay for twice a request.
