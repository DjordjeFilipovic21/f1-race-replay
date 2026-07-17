# Browser replay-engine runtime semantics

This document describes the implemented browser runtime. The delivery contract
and committed deterministic fixture remain unchanged: both are **v1**. Runtime
sampling is a read-only interpretation of delivered chunks; it does not rewrite,
resample, or publish new delivery data.

Canonical generation remains complete, but browser delivery is cropped to the
race. Its first chunk starts at the earliest non-null canonical Lap 1
`lap_start_time_ms`; timestamps and events before that point are not delivered
to browser chunks. The crop avoids making pre-race session activity part of the
replay timeline. Global weather and status at race start still resolve from
earlier canonical observations, so cropping delivery does not discard the
canonical source needed for initial state. If Lap 1 is missing, delivery fails
closed.

See [Replay Data Contract](replay-data-contract.md) and [Browser delivery
interface freeze](browser-delivery-interface-freeze.md) for the underlying
artifact rules.

## Why shared-timeline adjacency is invalid

Production delivery uses the exact union of valid source timestamps. A row can
be present because another field or driver contributed that timestamp, while a
particular field remains `null`. Bahrain evidence makes adjacent-index
sampling unsafe:

- 44,747 authoritative timestamps and 575 race-only chunks are delivered.
- Immediate shared-timeline endpoints were both valid only **8.31–9.91%** of
  the time.
- Per-field valid gaps reached **1,301 ms** for coordinates and **1,319 ms**
  for car telemetry (p99: 480 ms).

The sampler therefore never treats the next shared-timeline row as the bound
for a field merely because it is adjacent.

## Timeline, ownership, and sampling

- Time is an integer millisecond.
- Chunk ownership is half-open: `[startMs, endMs)`.
- Entries before `authoritativeStartIndex` are overlap-only references.
- For duplicate timestamps, the chunk whose interval owns the time wins. The
  overlap copy is available for handoff bounds, not ownership.
- The active window is previous/current/next. The adjacent chunk is required at
  a handoff because the current chunk can contain the lower bound while the
  next chunk supplies the upper bound.

For each driver field, the sampler searches the authoritative timeline for the
nearest non-null lower and upper values **for that field**:

- Continuous numeric fields (`x`, `y`, `speed`, `throttle`, `brake`,
  `trackDistanceMeters`, `gapToLeaderMs`) are linearly interpolated only when
  both bounds exist and their interval is at most **1,000 ms**.
- A missing bound, or a bound interval over 1,000 ms, returns `null`.
- There is no extrapolation before the first valid value or after the last.
- Discrete, categorical, and boolean fields use the nearest previous non-null
  value (step semantics); they never look forward or invent a value.
- Global leaderboard order, track status, and weather also use previous
  non-null semantics. Events remain sparse point records.

Delivered `sessionTimeMs` values remain absolute integer milliseconds. The
engine and seek API use those absolute values. The web controls separately
display elapsed time from the manifest's first chunk start, so Lap 1 appears as
`0:00.000` without rebasing engine time or serialized timestamps.

The result is an immutable `ReplaySnapshot`; arrays and event payloads are
copied/frozen before publication.

## Chunk cache and loading

`createReplayController` loads a bounded working set rather than all chunks:

1. A seek loads the requested chunk plus previous and next chunks in parallel
   when they exist.
2. Requests for the same sequence share one in-flight promise.
3. After a successful seek, chunks outside the previous/current/next window
   are evicted.
4. `retry()` repeats the last requested sequence/window after an error.
5. A seek revision makes completions from an abandoned seek inert: stale
   completions do not replace the published snapshot or ready window.

The controller reports `loading`, `ready`, or `error` and keeps the error for
the current request. Playback does not resume from a failed load until the
request succeeds (or the caller retries).

## Clock and controller behavior

Supported speeds are `0.25x`, `0.5x`, `1x`, `2x`, and `4x`; reverse playback is
not supported. The controller:

- clamps seeks to the replay start/end bounds;
- pauses when the end is reached;
- uses integer milliseconds for published time while advancing from the
  scheduler's monotonic time;
- caps one frame's elapsed wall time at **1,000 ms**. A background-tab rAF gap
  is therefore not fully caught up as a surprise jump;
- accepts an injected scheduler for deterministic tests. The browser scheduler
  is resolved lazily and requires `performance`, `requestAnimationFrame`, and
  `cancelAnimationFrame`.

Seeking suppresses event crossings for the resulting update. A backward seek
resets the crossing cursor; replaying forward from the new position can cross
events again.

## Events

There are two distinct event views:

- `replay.events` contains events whose `sessionTimeMs` exactly equals the
  sampled time. It is empty at all other times.
- `crossedEvents` contains forward crossings in the half-open movement window
  `(previousTime, currentTime]`. It is empty for backward or stationary time,
  and is suppressed for seeks.

Events are selected from the active chunk window and are never interpolated.

## React boundary

The engine core is React-free. `ReplayStore<T>` exposes only a cached
`getSnapshot`, `subscribe`, `publish`, and `dispose` contract, suitable for a
thin React `useSyncExternalStore` adapter. React controls should subscribe to
the controller/store; they should not own clock, cache, timeline, or sampling
state.

## Current production limitations

The following fields have no v1 production canonical source and are therefore
`null` in Bahrain delivery: `trackDistanceMeters`, `gapToLeaderMs`, and
`position`. Their fixture values remain compatibility coverage, but the runtime
must not infer production values for them. More generally, a `null` value is
preserved; the 1,000 ms rule is runtime policy, not a guarantee that every
production field can be interpolated.

For current race-only Bahrain delivery, the measured range is `3,599,911` to
`9,374,320` ms (displayed duration `5,774,409 ms`), with 4,453 overlap rows.
The previous whole-session count of 72,015 timestamps and 935 chunks is
historical context, not the current browser-delivery size.

## Public controller example

The replay-engine barrel currently exports `createReplayController` and
`createBrowserPlaybackScheduler`:

```ts
import { createReplayController } from './replay-engine'

const controller = createReplayController({ index })
const unsubscribe = controller.subscribe(() => {
  const snapshot = controller.getSnapshot()
  render(snapshot.replay, snapshot.crossedEvents, snapshot.status)
})

controller.setSpeed(2)
controller.start()
// Later: controller.seek(120_000); controller.pause(); unsubscribe();
```

`index` is a loaded `ReplayIndex` with manifest, track assets, and a
`loadChunk(sequence)` function. The initial controller state is loading until
the first active window is available.
