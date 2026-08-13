# Should you shift your flexible load? A costed answer

**GridCast — decision memo**
**Date:** 2026-08-13
**Audience:** anyone with a load they can move. No technical background assumed.

---

## The short answer

**Yes, and the saving is larger than most people expect — but not for the reason
most people think, and not at the horizon most people assume.**

Moving a 2-hour, 1.4 kWh load to the cleanest window in the next 24 hours saves
about **73 gCO₂/kWh against picking a time at random** — roughly 58%. On that
appliance, run daily, that is about **52 g of CO₂ a day, or 19 kg a year.**

Two findings matter more than the headline.

---

## Finding 1: the overnight habit does almost nothing

The common advice is to run flexible loads at night. On the forecast this memo
was written against:

| When you run it | Carbon intensity |
|---|---|
| Right now | 168.0 gCO₂/kWh |
| **At 3am, the usual advice** | **166.8 gCO₂/kWh** |
| At a random time | 125.6 gCO₂/kWh |
| **The recommended midday window** | **52.5 gCO₂/kWh** |

**Waiting until 3am is within 1% of not waiting at all.** It is worse than
choosing at random.

The reason is that the overnight habit solves a different problem. Off-peak
tariffs exist because the grid used to struggle with *demand* peaks. Carbon is
not the same thing. On a system running on wind and solar, the cleanest hours
are frequently the middle of the day, when solar output peaks and demand has not
yet risen to meet it.

Anyone shifting load for carbon reasons on the overnight heuristic is getting
close to none of the available benefit.

---

## Finding 2: the confidence you should have depends on how far ahead you ask

The saving above is a central estimate. The honest version carries a range:

| Instead of | Central saving | Could be as bad as |
|---|---|---|
| Running now | 115.5 gCO₂/kWh | **−21.5** |
| At 3am | 114.2 gCO₂/kWh | **−22.8** |

**The pessimistic end is negative.** The recommendation can turn out worse than
the alternative. That is not a caveat added for modesty — it is the 10th
percentile of the forecast's own uncertainty, and it is roughly a one-in-ten
outcome.

This is the number a decision actually turns on. A 58% average saving that is
occasionally negative is still clearly worth taking for a dishwasher. It would
not be enough to schedule an industrial process on without a hedge.

---

## What this recommendation is currently based on — and its main weakness

**The live recommendation comes from the weakest model GridCast has.**

The model serving the planner is a seasonal-naive baseline: it predicts that
each half hour will look like the same half hour yesterday. A far better model
exists — gradient boosting on weather, demand and generation-mix features, which
roughly **halves the error** in backtesting:

| | Seasonal naive (serving) | Gradient boosting (waiting) |
|---|---|---|
| 0–3 hours ahead | 1.28 | **0.46** |
| 24–48 hours ahead | 1.38 | **0.55** |

*Lower is better; 1.0 means no better than repeating yesterday.*

The better model is running live and being scored, but it is **not serving**,
because the rules for promoting it were fixed in writing before either model
existed and require about 1,440 scored forecasts per horizon before the
comparison may even be computed. That is roughly ten days of operation.

Promoting it early because it looks better would mean the promotion rule
described in the documentation was not the rule actually used. The cost of
waiting is about ten days of worse recommendations. The cost of not waiting is
that none of the accuracy claims on the site mean anything.

---

## What this memo cannot tell you

**The cost in pounds.** GridCast computes carbon only. The market price series
was switched off to stay inside a 512 MB storage budget, so a £ figure would
have to be estimated rather than measured. It is absent instead.

**Whether the recommendation is reliably right.** The planner reports a *hit
rate* — how often past recommendations landed in the cleanest third of their
window, against 33.3% for guessing. Today it reports **"not yet measurable"**,
because forecasts only become scoreable about a day after they are issued and
the live register is young.

That number is the one that should decide whether to trust this system, and it
does not exist yet. Any confident claim made before it does would be a claim
about a backtest, not about the product.

---

## What to do

**If you have a dishwasher, EV or heat pump:** stop using the overnight rule and
check the forecast. On current evidence you are leaving most of the benefit on
the table, and the middle of the day is often the answer.

**If you are scheduling something that matters:** wait for the hit rate. Ten
days of live scoring will say whether the recommendation is dependable at your
horizon, and it may show it is not at 48 hours. That result will be published
either way.

**If you want the number in pounds:** it needs the price feed turning back on,
which needs the storage headroom that now exists. It is a small piece of work,
not a research problem.

---

## Where every figure here comes from

| Claim | Source |
|---|---|
| Intensity by window | `/v1/plan`, from the champion's forecast in the append-only register |
| Saving ranges | The forecast's own q10/q90, calibrated conformally |
| Model comparison | `GridCast_M6_Summary.md` §1, 42,432 out-of-sample points |
| Promotion rule | `PREREGISTRATION.md`, committed before any model existed |
| Hit rate method | `GridCast_Design_Phase_v1.0.md` §12.1 |

Nothing above is quoted from a notebook. Every figure is reproducible from a
committed script against the warehouse.
