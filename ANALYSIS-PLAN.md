# How the battery will be analysed

Written with four of the six repetitions visible. That is weaker than a blind
pre-registration and is stated plainly for that reason: the point is to fix the
remaining choices before the final data can influence them, not to claim a
purity the timing does not support. `PREDICTIONS.md` and `analysis/validate.py`
were committed before any data existed; this file only settles what to do with
the data once it is in.

## The headline test

The pre-registered question is the one in `PREDICTIONS.md`: does each of the
three bucket cells' measured overhead fall inside the range published for that
bucket? That verdict is `validate.py`'s, computed over all six repetitions, and
it stands whatever it says.

`PRE-DATA-POWER.md` already establishes that one or two cells failing is the
expected outcome even when the model is exactly right - about a 59% chance at
the noise this rig shows. So the report leads with the joint picture, not with a
per-cell tally, and a single failing cell will not be described as a failure of
the model.

## Direction of a miss is part of the finding

A cell landing below its published band and a cell landing above it are not the
same result, and will not be reported as if they were. Below means the agent
costs less than published; the published range is then conservative rather than
wrong. Above means the published range understates the cost, which is the case
that matters to a capacity plan. Every miss gets its direction stated.

## The per-span constant

Reported three ways, all of them, regardless of which is most flattering:

1. `l` alone. It is the only cell whose verdict is reliable at every noise level
   tried (`PRE-DATA-POWER.md`), it carries 20 spans against a 3.2 ms baseline,
   and it has not lost a single arm to the gate.
2. `xl` alone, with its arm-loss disclosed.
3. The slope of added CPU against span count pooled across all five cells, with
   its intercept. The intercept is the part that matters: the model claims cost
   is proportional to spans with no fixed per-request charge, so an intercept
   far from zero would be evidence against the model's shape, not just its
   constant.

If these disagree, the disagreement is the finding and gets reported as the
spread, not averaged into a single number that hides it.

## xl's rejected arms

`xl` has lost one arm per repetition to the settling gate. Its numbers will be
reported both with and without the rejected arms, because excluding them is a
judgement call that moves the answer. The within-cell evidence that its
six-minute arms are request-starved (its 15-minute arm is its most stable, at
52,200 requests against 20,880) is the reason the exclusion is defensible, and
that reasoning goes in the report so a reader can disagree with it.

## What will not happen

No cell will be dropped for landing somewhere inconvenient. No repetition will
be dropped after the fact. The 46 us constant in `validate.py` will not be
retuned to fit the result - if the measured constant is not 46, the report says
what it is and what that implies for the published buckets. The gate's accept
and reject decisions stand as the harness made them at run time.
