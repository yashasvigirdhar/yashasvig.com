# Coaching, year one: client-wins data and mockups

Working folder for the "One Year as a Fitness Coach" post
(`src/content/post/one-year-as-a-fitness-coach.mdx`). It holds the raw
client-progress data pulled from the Assistant Coach production database and
the HTML mockups for how those wins could appear in the post.

`data/` is git-ignored: it contains client names, emails and check-in text.
Nothing in it should be published as-is. Progress photos are deliberately not
collected.

## Refreshing the data

Needs the read-only tunnel from the Assistant Coach repo, its Python venv, and a
git-ignored `config.local.json` next to the script:

```json
{ "coach_email": "<coaching account email>", "self_emails": ["<your own client-account emails>"] }
```

```bash
cd ~/Projects/assistantcoach
prod/prod-db-readonly-tunnel.sh start        # AC_PROD_DB_PASSWORD must be exported
source .venv/bin/activate
python ~/Projects/yashasvig/coaching-year-one/collect.py
prod/prod-db-readonly-tunnel.sh stop
```

The script only reads (it forces a read-only transaction) and writes JSON into
`data/`. It covers every client row on the coaching account, including archived
ones.

## Files in `data/`

| File | What it holds |
|---|---|
| `roster.json` | One row per client with a `kind` (`client`, `self`, `demo`, `never_started`), an `include_suggested` flag (real client with 3+ check-ins), activity counts, first/last activity and tenure. |
| `measurements.json` | Weight and the six tape measurements per client as dated series. Each point carries `flags`; `summary` uses only unflagged points. |
| `strength.json` | Per client and exercise: the best set of every logged session. Weighted lifts carry an estimated 1RM (Epley); bodyweight moves carry reps; timed moves carry seconds. `summary.baseline_score` is the median of the first three sessions. |
| `prs.json` | Current personal-record table (best weight per rep count per exercise). |
| `goals.json` | Every goal with start/target/current values, status and the coach's closing note. |
| `checkin_text.json` | Every free-text check-in answer from both eras, with the question, a rough `category` (wins, workouts, feeling, struggles, sleep, open, intake, other) and the date. |
| `habits.json` | Per client: steps, sleep, self-reported training days per check-in; every logged workout; workouts per ISO week. |
| `coach_stats.json` | Year-in-numbers totals, check-in turnaround (2026 onwards) and a month-by-month timeline. |

## Caveats to keep in mind when picking wins

- **Two data eras.** Check-ins before late January 2026 came from Google Forms
  and were imported; their free text lives under the literal question. App-era
  check-ins use the custom check-in form. `checkin_text.json` merges both.
- **Unit slips and typos.** A few tape readings are in inches or mistyped
  (a waist of 11 cm). They are flagged, not deleted. Weight is flagged when it
  jumps more than 6% against neighbouring readings; tape measurements at 15%.
- **Strength baselines.** The raw dump keeps an estimated 1RM per session, but
  the post shows only weights actually lifted: the heaviest set in the first
  logged session against the heaviest set logged since. Rep moves use the best
  sustained over the last three sessions, so a one-off entry does not count.
  Sessions logged before April 2026 are sparse because in-app workout logging
  only started then.
- **Written replies are undercounted.** Before the app, check-in replies went
  over WhatsApp. Only 2026 check-ins carry review and reply timestamps, so the
  turnaround numbers use those.
- **`days_worked_out` is self-reported.** Prefer `workouts_per_week` from real
  logs where they exist.
- **Headcount is a judgement call.** The roster tags obvious non-clients but
  the final "people coached" number is a decision for the post, not the script.

## From raw data to the post

`build_mockup_data.py` turns `data/` into two things: `mockups/*-data.js` for
the local mockup pages, and `src/data/coaching-year-one.json`, the anonymised
export the site components read (descriptors, gender and age, and the curated
picks only; no names, ids or emails). Re-run it after editing the curation file
or refreshing the data:

```bash
python coaching-year-one/build_mockup_data.py
```

Everything that names a client lives in the git-ignored `data/curation.json`:

| Key | What it decides |
|---|---|
| `quotes` | Hand-picked check-in excerpts (`selected` marks the ones the post uses); `==phrase==` inside `text` colours a keyword. |
| `personas`, `pseudonyms` | Per-client descriptors and stand-in names for the mockup attribution modes. |
| `weight_tiles` | Which clients get a weight curve and the date each curve stops. |
| `exercise_picks`, `spotlight_order` | Which logged lifts the strength spotlight shows, and in what order. |
| `goal_progressions`, `reported_progressions` | Pull-up goals and check-in-reported lifts that stand in for missing set logs. |

The site pieces are `src/components/coaching/` (WeightCollage, Spotlight and
its Quote and Progress variants, Closing) and
`src/styles/components/collage.css`. The mockup pages in `mockups/` are local
only; the Assistant Coach repo's `.claude/launch.json` serves them on port 4321.
