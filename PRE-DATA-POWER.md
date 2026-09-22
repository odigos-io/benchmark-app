# What a failing cell would and would not mean

Written and committed before the battery's results were looked at, for the same
reason `PREDICTIONS.md` was: a criterion is only meaningful if it is fixed in
advance, and so is the question of what failing it proves.

`analysis/power.py` plants the per-span cost at exactly the published 46 us,
adds the between-arm noise this rig actually shows, and runs the battery twenty
thousand times through `validate.py`'s own statistics. Everything below is the
answer to one question: **when the model is true, how often does each cell say
so?**

At 1.9% between-arm noise - the figure the two consecutive uninstrumented arms
of repetition 1 imply - with six repetitions:

| cell | true effect | effect / standard error | says so when true |
|---|---|---|---|
| s | 3.00% | 3.9 | 70% |
| schatty | 3.17% | 4.1 | 99% |
| m | 1.51% | 1.9 | 78% |
| l | 28.53% | 36.8 | 100% |
| xl | 6.77% | 8.7 | 75% |

Across plausible noise levels:

| between-arm noise | s | schatty | m | l | xl | all five pass |
|---|---|---|---|---|---|---|
| 1.0% | 84% | 100% | 93% | 100% | 97% | 76% |
| 1.9% | 70% | 99% | 78% | 100% | 75% | 41% |
| 3.0% | 64% | 94% | 67% | 100% | 53% | 21% |

**The consequence, stated before the data: if the model is perfectly correct,
there is roughly a 59% chance that at least one of the five cells still fails
its check.** A single failing cell is the expected behaviour of this experiment,
not evidence against the model. Only a pattern - several cells failing in the
same direction, or `l` failing - carries information.

Why the criteria are hard to hit is worth being precise about, because it is not
a lack of statistical power in the usual sense:

- `s` sits at 3.00% against a published band whose lower edge is 2.6%. The true
  value is close to the edge, so ordinary downward noise pushes it out. The band
  is narrow *where this cell happens to land*, not overall.
- `m` is the cell declared marginal in advance. Its true effect, 1.51%, is
  smaller than the noise on a single measurement; six repetitions bring the
  standard error to about 0.8%, which is still a fifth of its band.
- `xl` must land a per-span cost inside 40 to 52 us, a window of plus or minus
  13% around the truth, while its own standard error is about 11%.
- `l` is the load-bearing cell. Its effect is 28.5% against roughly 2% noise, so
  it returns the right answer essentially always, at every noise level tried.
  If `l` fails, that is a real finding.

This is the honest reading of the design, and it is the reason the battery has
five cells rather than one. `l` can falsify the per-span constant. `schatty` can
falsify it. `s`, `m` and `xl` can corroborate, and their failure - individually -
would tell us about the ruler rather than the thing being measured.

Reproduce with:

```sh
python3 analysis/power.py --reps 6 --trials 20000 --noise-pct 1.9
```
