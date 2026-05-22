# Evaluation methodology

The evaluation answers one question: *does FALCON actually work, and by
how much?* It's the bit most portfolio agent projects skip because it's
hard and uncomfortable.

## What gets measured

Thirty synthetic alerts (twenty genuine, ten clean) with ground-truth
labels in `eval/ground_truth.json`. Each genuine alert points at one
of fifty pre-seeded fraud scenarios across five typologies, so the
expected verdict, typology, and network membership are all known.

For every alert the harness scores six things:

| Metric | What it asks |
|---|---|
| Verdict accuracy | Did FALCON recommend the right action? |
| Typology accuracy | Did Pattern Hunter classify the fraud correctly? |
| Network recall | What fraction of the linked accounts did we find? |
| Evidence recall (lenient) | Did the case file mention the expected evidence, allowing for paraphrasing? |
| Faithfulness | Are the case file's claims supported by the underlying data? |
| Hallucination rate | What fraction of claims are unsupported by the data? |

The first four are deterministic — pure Python, no LLM, fast and
testable. The last two need a judge.

## The LLM-as-judge

The judge reads each case file alongside the raw investigation data
and scores every claim 0 (unsupported), 1 (partial), or 2 (fully
supported). Faithfulness is the mean; hallucination rate is the
fraction of zeroes.

The judge follows three rules, in order of strictness:

1. The judge is never the same model as the agents. Asking Haiku to
   grade Haiku is asking a model to mark its own homework.
2. The judge is preferably on a different vendor. Cross-vendor
   judging has no shared training signal to bias the score.
3. The judge backend is controlled by `JUDGE_BACKEND` in `.env`,
   completely independent of `LLM_BACKEND`. The factory has supported
   this since Phase 1.

The recommended setup is Vertex Gemini 2.5 Pro grading Anthropic
Haiku/Sonnet — frontier-class, cross-vendor, pinnable to
`europe-west2` for the same UK story the agents tell.

When the judge is same-vendor (we had to use Anthropic Opus for our
first run because Vertex wasn't wired locally), the dashboard
renders an orange disclosure card explaining the capability-gap
defence — Opus is strictly stronger than Haiku in the same family.
It's a defensible choice but it's not pretended to be neutral.

## The headline numbers

From the first eval run — Haiku 4.5 agents + Opus 4.7 judge, all 30
alerts:

- Verdict accuracy **24%**
- Typology accuracy **52%**
- Network recall **95%**
- Faithfulness **77%**
- Hallucination **12%**

The story those numbers tell isn't "we shipped a perfect system." It's
"the eval framework works well enough to tell us exactly what to fix
next." Pattern Hunter and Network Mapper are both performing well; the
verdict number is low because confidence is sitting in the HITL band
and the case writer is conservative about deviating from interim
confidence. The README's *Tuning knobs* section documents the
specific levers (`CONF_AUTO_CLOSE_BELOW`, `CONF_SAR_ABOVE`, the
`compute_confidence` weights, the Case Writer clamp); change those
and the verdict number moves.

That's the more honest portfolio outcome than a fabricated 95%
verdict accuracy: the framework caught a real tuning gap that you can
point at and reason about.

## How to run it

```bash
# Fast smoke (1 alert, no judge)
python -m eval.run_evaluation --limit 1 --no-judge

# Full headline run
python -m eval.run_evaluation --label haiku-4.5

# Render the dashboard
python -m eval.dashboard
# Open eval/results/index.html
```

The harness writes a self-contained JSON to `eval/results/` and rows
to `evaluation_runs` / `evaluation_results`. The static HTML
dashboard sits next to the JSON files and renders one-or-many runs
side by side. The README's "Evaluation Results" section is a
screenshot of that page.

## Why the harness auto-resumes HITL pauses

When a case pauses for review, the harness submits a neutral annotation
(`reviewer_id="eval_harness"`, no override) so the rest of the pipeline
runs and we get a case file to grade. We don't pretend a reviewer made a
decision they didn't make.

The side-effect — all clean alerts end up REVIEW because they pause
and then auto-resume into a REVIEW case — is what drives the 100%
false-positive rate in the headline numbers. A future harness mode
will record the pre-resume state as a separate "HITL_PAUSED" bucket
and score it distinctly from a real reviewer-declined case. The
README's *Tuning knobs* section calls this out alongside the
confidence-formula changes that would let clean alerts auto-close
before they hit HITL at all.
