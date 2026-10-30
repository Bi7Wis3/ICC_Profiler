# ICC Profiler

Build custom ICC printer profiles by printing a generated test chart, measuring
the printed output, and feeding the result through [ArgyllCMS](https://argyllcms.com).
This project is orchestration/glue only — all color science is delegated to
ArgyllCMS's own command-line tools (`targen`, `printtarg`, `scanin`, `colprof`,
`profcheck`); nothing here reimplements profiling math.

A local web UI (and an equivalent CLI) walks you through: generating a patch
chart (optionally including specific colors you care about as real measured
patches), printing it, measuring the print, and building + verifying the
resulting `.icc` profile.

![Main page](docs/images/mainpage.png)

## Two measurement methods

| | **Scanner** | **Colorimeter CSV import** |
|---|---|---|
| Hardware | A flatbed/consumer scanner you likely already own | A real colorimeter/spectrophotometer not natively driven by ArgyllCMS (e.g. CHNSpec DS-173, 3nh PS110) |
| Extra prerequisite | A physical IT8.7 reference target + its vendor CGATS reference data | None |
| Effort per profile | One scan of the chart + one scan of the IT8 target | Measuring every chart patch individually by hand |
| Accuracy | Capped by scanner characterization quality | Meaningfully higher — real colorimetric measurement |

Pick per job. Both produce the same `.ti3` measurement format and feed the
same `colprof`/`profcheck` steps.

> **Colorimeter users: set the device to D50 / 2°.** ICC profiles and
> ArgyllCMS expect L\*a\*b\* relative to the D50 illuminant and the 2°
> standard observer. Handheld colorimeters such as the 3nh PS110 and
> CHNSpec DS-173 often default to **D65 / 10°**. Readings taken that way
> shift every color in the profile, and `verify` cannot notice, because it
> checks the profile against those same readings. Set the instrument to
> D50 / 2° before measuring, then confirm that on the job page (or set
> `measurement.colorimeter_csv.lab_reference` to `"D50_2"`).
> `import-measurements` refuses to run until you do. D65 / 2° readings are
> also accepted (`"D65_2"`) and converted to D50 with the Bradford chromatic
> adaptation. A 10° observer can't be converted without spectral data, so
> re-measure in that case.

Example colorimeter: the [3nh PS110](https://www.3nh.com) handheld
spectrocolorimeter (D/8, SCI, CIE LAB, USB/Bluetooth, ships with the SQCX PC
app) — measure each patch with its own app/software, export the readings as
CSV, then upload that CSV per the colorimeter-CSV workflow below. Any
device that can export a CSV of per-patch readings works the same way; this
project talks to it only through that CSV, not through any vendor API.

## Install

### ArgyllCMS

- **WSL / Ubuntu**: `sudo apt update && sudo apt install argyll` (pulls in
  `argyll-ref` for reference/chart-template data). Optionally
  `sudo apt install argyll-doc` for local HTML docs — read
  `doc/scanin.html` and `doc/Scenarios.html` before a real profiling run; a
  few exact flags in this project are marked unverified (see Limitations).
- **Native Windows**: download the official build from
  [argyllcms.com](https://argyllcms.com), extract it (e.g. to `C:\Argyll\`),
  and either add `C:\Argyll\bin` to PATH or set `"argyll": {"bin_dir": "C:\\Argyll\\bin"}`
  in your job config.

Run `python -m icc_profiler doctor` (or the "doctor" check before your first
job) to confirm all required tools resolve.

### This project

```
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on native Windows
pip install -r requirements.txt
```

## Usage

### CLI

```
python -m icc_profiler init-job --config configs/example_job_scanner.json
python -m icc_profiler run --job example_scanner_job
python -m icc_profiler status --job example_scanner_job
```

`run` executes every remaining stage and stops cleanly (exit 0, not an error)
whenever it's waiting on a human step — printing the chart, scanning it,
or measuring patches with a colorimeter. Re-run `run` once that's done to
continue.

`run --force` re-runs every stage *after* the chart (re-reading or
re-importing the measurements, rebuilding and re-verifying the profile). It
keeps the chart, because regenerating it reshuffles which color is printed
under each label and invalidates anything already printed and measured.
`run --regenerate-chart` starts over from a new chart.

Paths in a job's `config.json` may be relative to the job folder (e.g.
`02_measurements/export.csv`); the web UI stores them that way so a job
keeps working if the project folder moves or runs under native Windows
instead of WSL.

### Web UI

```
uvicorn icc_profiler.webapp.app:app --reload
```

Or, to skip activating the venv yourself: `./start_profiler.sh` (runs the
same command via the project's `.venv`; any extra arguments are passed
through to uvicorn, e.g. `./start_profiler.sh --port 8080`).

Open `http://localhost:8000`. Create a job, pick a measurement method, add
any custom target colors, generate the chart, print it, upload your
scan(s)/CSV export, run the remaining stages, and view the results.

Stages run in the background: the job page shows what's running and
updates itself when it finishes, so a long `colprof` run no longer leaves
the browser spinning. Only one stage runs per job at a time (a second click,
or the CLI on the same job, is refused until the first finishes).

The server only answers to `localhost` / `127.0.0.1` and rejects form posts
sent from other websites. To open the UI from another machine, start it
with `--host 0.0.0.0` and list the names you'll use, e.g.
`ICC_PROFILER_ALLOWED_HOSTS="localhost,192.168.1.20" ./start_profiler.sh --host 0.0.0.0`.

## Keeping results consistent

Each stage consumes the previous stage's output, so whenever a stage runs,
or one of its inputs changes, every later stage goes back to "pending":

- Re-running `generate-chart`, changing chart settings, or adding/removing a
  custom color resets the chart and everything after it.
- Uploading a new IT8 scan or reference data resets `build-scanner-profile`
  onwards; a new chart scan or CSV resets `read-chart` / `import-measurements`
  onwards; changing profile settings resets `build-profile` onwards.

Measurements are also tied to the exact chart they were taken from.
Regenerating the chart reshuffles which color sits under each printed label
(A1, A2, ...), so a CSV, scan, or guided-scan readings made for an older
print are refused instead of silently producing a wrong profile. (For a
CSV or scan path typed into `config.json` by hand for the CLI, the file
just has to be newer than the chart.) Old jobs whose `state.json` shows a
stage "done" before an earlier stage re-ran have those stages shown as out
of date.

## CMYK ink limits

For CMYK jobs, set a total ink limit (e.g. 300%) under Edit settings
(`chart.ink_limit`). It goes to `targen -l`, so the chart never asks for
more ink than the paper takes, and to `colprof -l`, so the profile respects
it too. An optional black limit (`output.black_ink_limit`) goes to
`colprof -L`. Changing the ink limit means regenerating and reprinting the
chart. (ArgyllCMS 3.1.0's `targen` hangs for minutes with exactly `-l300`,
so 300% is passed as 299.9%.)

## CSV exports

The CSV import accepts UTF-8 (with or without BOM), UTF-16 and Windows-1252
files, `,` / `;` / tab separators, and decimal commas (`52,3`) in `;`- or
tab-separated files. Column headers are matched case-insensitively.
Readings are checked before they reach `colprof`: L\* must be 0–100 and
a\*/b\* within ±150, and the error names the line and patch.

## Tests

```
pip install -r requirements-dev.txt
pytest                 # everything (Argyll tests skip if ArgyllCMS isn't installed)
pytest -m argyll       # just the end-to-end runs against a real ArgyllCMS
ruff check .
```

The unit tests use a fake ArgyllCMS, so they run anywhere in about two seconds.

## A note on `output.quality`

`colprof`'s quality setting (`low`/`medium`/`high`/`ultra`) has a real
performance cliff: `high`/`ultra` were observed taking several minutes even
on a 15-21 patch synthetic test during development, apparently much worse
on data with many duplicate/near-duplicate points (e.g. a small or
degenerate patch set). `medium` is colprof's own documented default and
was fast in the same tests — the example configs and the web UI's job
creation form both default to it. Reach for `high`/`ultra` deliberately for
a final production profile once your patch set is large and realistic, not
as a default for every run.

The scanner correction profile built in `build-scanner-profile` has its own
setting (`measurement.scanner.profile_quality`, default `medium`, under Edit
settings for scanner jobs), since a shaper+matrix input profile gains little
from `high`/`ultra`.

### SQCX (3nh PS110) and other software that can't rename readings

The colorimeter upload also takes `.xlsx` exports. Set **"Readings are
matched to patches: by measuring order"** on the job page and measure the
chart down each printed column: A1, A2, … to the end of column A, then
B1, … Rows named `Standard…` are skipped, only the chosen SCI/SCE mode is
used, and an Ill/Obs column that doesn't match the job's setting is refused.

## Check chart (real accuracy test)

Colorimeter jobs only. After the profile is built, make a small second
chart on the job page (or `python -m icc_profiler check-chart --job NAME`).
Its patches are random points that fall between the main chart's patches,
and its strips are labelled P, Q, … so the two sheets can't be mixed up.
Print it the same way, measure it with the same settings, upload the export
and click "Check the profile" (or `check-profile --job NAME --export FILE`).
The result is the profile's error on colors it has never seen. As a rough
guide, an average ΔE under 2 is very good and 2–3 is good. Files go to
`06_check/` and never touch the main pipeline.

## Spot colors (Pantone and other named colors)

Colorimeter jobs, once the profile is built: "Spot colors (Pantone)" on the
job page. Add colors by uploading a colorimeter export of your swatch book
(with the names listed in measuring order if the software calls them
Sample1, Sample2, …) or by typing `name; L; a; b` in D50/2°. Each color gets
a CMYK recipe looked up backwards in the profile (`xicclu -fif`, relative
colorimetric, respecting the ink limits) and an expected ΔE2000; above 1 the
color is outside the printer's gamut and the recipe is the closest it can
print. Then make a proof sheet (strips S, T, …), print and measure it, and
"Correct the recipes that missed" adds each color's measured error to its
next lookup. Recipes download as CSV. Files go to `07_spot/`.

The recipes only help where the spot color is typed in as CMYK. To make
the profile itself accurate around these colors (for anything printed
through the driver, RIP or Photoshop), "Add these readings to the profile"
appends the proof readings to the chart's as extra patches
(`04_measurements/<job>_extra.ti3`), rebuilds the profile from both
(`<job>_plus.ti3`; the chart's own `.ti3` is unchanged) and keeps the
previous `.icc` as `<job>.before-<time>.icc`. Only use a proof printed
exactly like the chart, with color management off. "Take them out and
rebuild" renames the extra file and rebuilds from the chart alone.

## Limitations (read before trusting a profile)

- **Scanner path**: a flatbed/consumer scanner is not a colorimetric
  instrument. Its own color response must be corrected using the physical
  IT8 reference scan, and that scan plus the test-chart scan **must use
  identical scanner settings** (resolution, color mode, no auto-levels/
  auto-color, ideally the same session). Drift between the two degrades
  everything downstream. Even corrected, expect a lower accuracy ceiling
  than a real spectrophotometer.
- **Colorimeter CSV path**: meaningfully more accurate, and skips the IT8
  target entirely, but is NOT driven automatically by ArgyllCMS (checked
  directly against argyllcms.com's instrument list) — every patch is
  measured and named by hand, matched against the strip/row label
  ArgyllCMS actually prints next to each patch (e.g. "A14" — confirmed
  visually against real chart output), not any internal patch name. A
  mis-typed/misordered patch name will be caught at import (every printed
  label must have a matching, uniquely-named reading) but a *wrong* reading
  under a *correct* name will not be.
- Custom target colors are genuinely measured as chart patches (not just
  checked afterward), but aren't exempt from either method's accuracy
  ceiling.
- `verify` (`profcheck`) checks fit against the profile's own training
  data, not independent accuracy. For that, use the check chart (below).
- The full command pipeline (`targen` → `printtarg` → `scanin` →
  `colprof` → `profcheck`, both measurement paths) was validated end to
  end against a real installed ArgyllCMS 3.1.0 using synthetic data —
  see comments in `icc_profiler/stages.py` for specifics confirmed along
  the way (e.g. `targen`'s real `-d` device-class codes, `printtarg`
  needing `-s`/`-t<dpi>` to produce a usable `.cht`/TIFF at all, `scanin`'s
  `-O` output-redirect only working in some modes, and the real
  `profcheck` summary-line format). What that synthetic testing could
  *not* validate is real-world accuracy or behavior with an actual
  printer/scanner/colorimeter — the first real run should still be treated
  as a shakedown.
- The colorimeter CSV column layout (`measurement.colorimeter_csv.column_map`)
  is a guess until checked against a real export file from your device.
