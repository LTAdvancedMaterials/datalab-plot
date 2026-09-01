# Syncing with the upstream internal codebase

Three modules in this repo are backports from a private Lightning Tree
application. That application started as a `datalab-plot` consumer, later
vendored the parts it used, and fixed things on the way. This file explains
what that means if you are editing them.

- `src/datalab_plot/parsers/echem.py`
- `src/datalab_plot/series.py`
- `src/datalab_plot/plots/echem.py`

The relationship is one-way. The internal application does not depend on
`datalab-plot`, nothing here is generated, and a sync is a human reading a
diff.

## Why this file exists

Three things in these modules look like accidents and are not. A cleanup that
"simplifies" any of them undoes a fix. Re-check all three on the next sync.

`_integrate_capacity` takes `only_half_cycles` and `seed_existing`; the
upstream version takes neither. This repo supports mixed-cycler stacks, where a
Neware `.ndax` is loaded alongside a BioLogic `.mpr`. Commit `8aecd98` taught
`_normalise_neware_state` to reclassify only rows carrying a genuine Neware
`Status` and to seed capacity from the existing column, so the non-Neware half
cycles keep navani's values. The upstream never sees a mixed frame.

`_warn_if_capacity_units_look_wrong` exists only here. Reading the cycler's own
capacity counters means trusting the column header, and some Neware machines
write Ah into a column named `(mAh)`. navani carries an
`expected_capacity_unit` argument for exactly this; we have no equivalent, so
the guard compares the counter total against `∫|I|·dt` and logs a warning past
5×.

`_normalise_neware_state` rebuilds the time axis only when every row is a
Neware row. In a mixed stack only the Neware rows carry a `Timestamp`, so a
global rewrite would write NaN over the other cycler's usable `Time`. The
upstream does it unconditionally.

## What keeps a backport cheap

Both codebases share a function-level API. While these names and signatures
match on both sides, porting a change is reading one file against another
rather than a redesign:

`load_echem`, `cycle_summary`, `is_cycling_file`, `filter_by_cycle`,
`split_half_cycles`, `compute_dqdv`.

Changing a signature on either side is what makes the next sync expensive.

## Checking for drift

`make drift` lists upstream commits touching the backported modules since the
last sync. It needs a checkout of the internal application, so it reads its
configuration from `sync.local.mk`, which is untracked:

```make
UPSTREAM_PATH   ?= /path/to/checkout
UPSTREAM_SYNCED ?= <commit last synced from>
UPSTREAM_PATHS  := path/to/module_a.py \
                   path/to/module_b.py
```

Then:

```sh
make drift
```

With nothing outstanding:

```
Upstream commits touching the backported modules since <commit>:
  (none - in sync)
```

After a sync, bump `UPSTREAM_SYNCED`.

There is no automated sync and no release train. The shared surface has changed
7 times in 510 upstream commits, roughly two to four times a year in discrete
features. Run this quarterly, read the commits, decide what is worth porting.

## Not ported, by decision

The upstream has a cycle-metrics module of about 840 lines. The portable half
is the part that does not reach for its domain models: `cycle_value`, `series`,
`keep_indices`, `decimate`, `rate_groups`, `at_rate`, `cycling_rate`,
`retention`, `fade`, `knee`, `capacity_collapse`, `ce_stats`. The rest needs a
cell model and roughly 1,400 lines of cell-design logic with no counterpart
here.

If it lands, the UI home already exists: the Cycle Life "Capacity table"
sub-view, listed in `_SUMMARY_VIEWS` in `gui_dash/options.py` and dispatched by
title in `gui_dash/plotting_panel.py`. No new panel needed.

The upstream's plotting UI is not portable. It is a TypeScript Electron
renderer of roughly 2,500 lines, against this repo's Dash GUI.
