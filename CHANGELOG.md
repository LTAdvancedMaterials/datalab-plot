# Changelog

Notable changes to `datalab-plot`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Changed: every Neware capacity number moves

Read this before comparing a plot made with this version against one made with
an earlier one. If you recorded a formation capacity, a coulombic efficiency or
a cycle-life curve from a Neware `.nda` or `.ndax`, the number was wrong and
will now differ.

`.nda` and `.ndax` files are read directly through `NewareNDA` instead of
`navani.echem.echem_file_loader`. navani's Neware reader rebuilds the time axis
by grouping on `Step_Index`, which is the protocol step *definition* number and
repeats on every loop, so each occurrence of a step inherits the first cycle's
base timestamp. Elapsed time is then wrong at every step boundary, and this
package integrated capacity as `∫|I|·dt` against that axis. Reported upstream
as [navani#62](https://github.com/be-smith/navani/issues/62).

Measured on the synthetic two-cycle CCCV formation in
`tests/conftest.py:make_neware_raw_df`:

| | before | after (correct) | error in the old value |
|---|---|---|---|
| cycle 1 charge (mAh) | 0.9938 | 1.0500 | −5.4 % |
| cycle 1 discharge | 0.7733 | 0.8000 | −3.3 % |
| cycle 2 charge | 0.8796 | 0.8250 | +6.6 % |
| cycle 2 discharge | 0.7540 | 0.7800 | −3.3 % |
| cycle 2 CE | 0.8572 | 0.9455 | −9.3 % |
| elapsed (h) | 1.926 | 3.587 | −46 % |

Sign and magnitude depend on the protocol's step layout, so the error reads as
plausible rather than as an obvious failure. Your files will not show these
exact figures.

Three consequences you will see:

- Time-domain x axes move. navani's `Time` came out non-monotonic, which
  silently pushed `series.cumulative_time_hours` onto its clamped-diff fallback
  for every Neware file.
- V-Q segments start at a small nonzero capacity, the cycler's first counter
  sample, rather than a pinned zero. Segments stay monotonic and still reset at
  each half-cycle boundary.
- Idle gaps between stitched files collapse. Multi-file loads now rebase file
  *n+1* onto the end of file *n*; navani preserved a genuine gap between
  sessions.

Other formats (`.mpr`, `.res`, `.xls`, `.xlsx`, `.csv`, `.txt`) still go
through navani and are unaffected.

### Fixed

- `cycle_summary` no longer mutates the DataFrame handed to it.
  `navani.echem.cycle_summary` writes `full cycle` back onto its input
  (`navani/echem.py:729`), flipping the column from int64 to float64 on the
  frame the GUI keeps in its parse cache. This package now computes the summary
  itself.
- Corrected an inverted charge/discharge convention in `cycle_summary`'s
  fallback aggregation. Charge is `state == 0` and discharge is `state == 1`;
  the fallback had them swapped.
- Added `_warn_if_capacity_units_look_wrong`. Reading the cycler's own capacity
  counters means trusting the column header, and some Neware machines write Ah
  into a column named `(mAh)`. The guard compares the counter total against
  `∫|I|·dt` and logs a warning past 5×.

### Added

- `newarenda>=2024.8.1` as a direct dependency. It was already present
  transitively through navani, so this does not change what gets installed. The
  PyPI name is `newarenda`; the import name is `NewareNDA`.
- GitHub Actions CI running lint, types, tests and a Dash boot check on 3.12.
- `SYNC.md` and `make drift`, which record the backported modules and check
  them for upstream drift.
- `CONTRIBUTING.md`.

### Removed

- `DEFAULT_URL` no longer ships a hardcoded datalab hostname. It is empty, so
  the GUI Connect modal pre-fills from a saved credential or `DATALAB_URL`
  instead of pointing every install at one team's server.
