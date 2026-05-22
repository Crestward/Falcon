"""Render one or more eval/results/*.json files as a static HTML dashboard.

No React; no server. The README screenshots this page.

Usage:
    python -m eval.dashboard                       # render every file in eval/results/
    python -m eval.dashboard file1.json file2.json # render specific files (paths or run-id substrings)
    python -m eval.dashboard --out path/to.html    # override output path

The headline table is the Haiku-vs-Sonnet A/B comparison when both runs are
present. The Vertex confirmation run, if present, appears as a single-row
"UK data residency" check.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

_RESULTS_DIR = Path(__file__).resolve().parent / "results"
_DEFAULT_OUT = _RESULTS_DIR / "index.html"


def _load(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def _collect(args_files: list[str]) -> list[dict[str, Any]]:
    if not args_files:
        files = sorted(_RESULTS_DIR.glob("*.json"))
    else:
        files = []
        for a in args_files:
            p = Path(a)
            if p.exists():
                files.append(p)
                continue
            matches = sorted(_RESULTS_DIR.glob(f"*{a}*.json"))
            files.extend(matches)
    return [_load(p) for p in files]


_PILL_OK = "background:#dcfce7;color:#166534"
_PILL_WARN = "background:#fef3c7;color:#92400e"
_PILL_BAD = "background:#fee2e2;color:#991b1b"


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _pill(label: str, value: float | None, *, good_above: float = 0.8, ok_above: float = 0.6) -> str:
    if value is None:
        style = "background:#e5e7eb;color:#374151"
    elif value >= good_above:
        style = _PILL_OK
    elif value >= ok_above:
        style = _PILL_WARN
    else:
        style = _PILL_BAD
    return (
        f'<span class="pill" style="{style}">{html.escape(label)}: '
        f"<strong>{_pct(value)}</strong></span>"
    )


def _hallu_pill(value: float | None) -> str:
    # Hallucination: lower is better — invert thresholds.
    if value is None:
        style = "background:#e5e7eb;color:#374151"
    elif value <= 0.1:
        style = _PILL_OK
    elif value <= 0.25:
        style = _PILL_WARN
    else:
        style = _PILL_BAD
    return f'<span class="pill" style="{style}">Hallucination: <strong>{_pct(value)}</strong></span>'


def _render_summary_row(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    return (
        '<tr>'
        f'<td><code>{html.escape(payload["label"])}</code></td>'
        f'<td>{_pct(s.get("verdict_accuracy"))}</td>'
        f'<td>{_pct(s.get("typology_accuracy"))}</td>'
        f'<td>{_pct(s.get("network_recall_mean"))}</td>'
        f'<td>{_pct(s.get("evidence_recall_mean"))}</td>'
        f'<td>{_pct(s.get("faithfulness_mean"))}</td>'
        f'<td>{_pct(s.get("hallucination_mean"))}</td>'
        '</tr>'
    )


def _render_typology_table(payload: dict[str, Any]) -> str:
    by_typ = payload["summary"].get("by_typology", {})
    rows = []
    for typ, bucket in sorted(by_typ.items()):
        rows.append(
            f"<tr><td>{html.escape(typ)}</td>"
            f"<td>{bucket.get('n')}</td>"
            f"<td>{_pct(bucket.get('verdict_accuracy'))}</td>"
            f"<td>{_pct(bucket.get('typology_accuracy'))}</td>"
            f"<td>{_pct(bucket.get('network_recall_mean'))}</td></tr>"
        )
    return (
        '<table class="grid"><thead><tr>'
        '<th>Typology</th><th>n</th><th>Verdict</th><th>Typology</th><th>Network recall</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
    )


def _render_confusion(payload: dict[str, Any]) -> str:
    matrix = payload["summary"].get("confusion_matrix", {})
    actions = ["AUTO_CLOSE", "REVIEW", "SAR_FILE"]
    head = "<tr><th>Expected ↓ / Got →</th>" + "".join(f"<th>{a}</th>" for a in actions) + "</tr>"
    body = []
    for exp in actions:
        row_data = matrix.get(exp, {})
        cells = [f"<td>{row_data.get(g, 0)}</td>" for g in actions]
        body.append(f"<tr><th>{exp}</th>{''.join(cells)}</tr>")
    return f'<table class="grid"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


def _render_worst(payload: dict[str, Any], top_n: int = 5) -> str:
    rows = payload.get("results", [])
    # Worst = highest hallucination, then lowest faithfulness, then verdict miss.
    def _key(r: dict[str, Any]) -> tuple:
        hallu = r.get("hallucination_rate") or 0
        faith = r.get("faithfulness_score") or 1
        verdict_miss = 0 if r.get("verdict_correct") else 1
        return (-verdict_miss, -hallu, faith)

    sorted_rows = sorted(rows, key=_key)[:top_n]
    cells = []
    for r in sorted_rows:
        cells.append(
            "<tr>"
            f"<td><code>{html.escape(r.get('alert_id', '?'))}</code></td>"
            f"<td>{html.escape(str(r.get('expected_verdict')))}</td>"
            f"<td>{html.escape(str(r.get('got_verdict')))}</td>"
            f"<td>{html.escape(str(r.get('expected_typology')))}</td>"
            f"<td>{html.escape(str(r.get('got_typology')))}</td>"
            f"<td>{_pct(r.get('faithfulness_score'))}</td>"
            f"<td>{_pct(r.get('hallucination_rate'))}</td>"
            "</tr>"
        )
    return (
        '<table class="grid"><thead><tr>'
        '<th>Alert</th><th>Exp verdict</th><th>Got verdict</th>'
        '<th>Exp typology</th><th>Got typology</th>'
        '<th>Faithfulness</th><th>Hallucination</th>'
        '</tr></thead><tbody>' + "".join(cells) + '</tbody></table>'
    )


def _render_judge_disclosure(payload: dict[str, Any]) -> str:
    judge = payload["summary"].get("judge", {})
    if not judge or judge.get("enabled") is False:
        return '<p class="note">Judge disabled for this run.</p>'
    disclosure = judge.get("disclosure", "")
    same_vendor = judge.get("same_vendor", False)
    cls = "warning" if same_vendor else "info"
    return (
        f'<p class="note {cls}"><strong>Judge:</strong> agents='
        f'<code>{html.escape(judge.get("agent_backend", "?"))}</code>, '
        f'judge=<code>{html.escape(judge.get("judge_backend", "?"))}</code> — '
        f"{html.escape(disclosure)}</p>"
    )


def _render_run_section(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    pills = " ".join(
        [
            _pill("Verdict", s.get("verdict_accuracy")),
            _pill("Typology", s.get("typology_accuracy")),
            _pill("Network recall", s.get("network_recall_mean")),
            _pill("Faithfulness", s.get("faithfulness_mean")),
            _hallu_pill(s.get("hallucination_mean")),
        ]
    )
    return (
        f'<section class="run"><h2>{html.escape(payload["label"])}</h2>'
        f'<p class="meta">run_id <code>{html.escape(payload["run_id"])}</code> · '
        f'{html.escape(payload.get("started_at", ""))}</p>'
        f'<div class="pills">{pills}</div>'
        f'{_render_judge_disclosure(payload)}'
        '<h3>Per-typology breakdown</h3>'
        f'{_render_typology_table(payload)}'
        '<h3>Confusion matrix</h3>'
        f'{_render_confusion(payload)}'
        '<h3>Worst-performing cases</h3>'
        f'{_render_worst(payload)}'
        '</section>'
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>FALCON — Evaluation Results</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         margin: 0 auto; max-width: 980px; padding: 32px 24px; color: #0f172a; background: #f8fafc; }}
  h1 {{ font-size: 28px; margin: 0 0 4px; }}
  h2 {{ font-size: 20px; margin: 32px 0 8px; }}
  h3 {{ font-size: 14px; text-transform: uppercase; letter-spacing: 0.06em; color: #475569; margin: 24px 0 8px; }}
  .meta {{ color: #64748b; font-size: 12px; margin: 0 0 12px; }}
  code {{ background: #e2e8f0; padding: 1px 6px; border-radius: 4px; font-size: 12px; }}
  table.grid {{ border-collapse: collapse; width: 100%; background: white; border-radius: 6px; overflow: hidden; }}
  table.grid th, table.grid td {{ border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; font-size: 13px; }}
  table.grid thead th {{ background: #f1f5f9; font-weight: 600; }}
  .pill {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; margin-right: 6px; }}
  .pills {{ margin: 12px 0 16px; }}
  .note {{ font-size: 12px; padding: 8px 12px; border-radius: 4px; }}
  .note.warning {{ background: #fef3c7; color: #92400e; }}
  .note.info {{ background: #dbeafe; color: #1e40af; }}
  section.run {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px 22px; margin: 18px 0; }}
  .headline {{ margin: 18px 0; }}
</style>
</head>
<body>
<h1>FALCON Evaluation</h1>
<p class="meta">Generated by <code>python -m eval.dashboard</code>.
Per-alert results: <code>eval/results/*.json</code>.</p>

<section class="headline">
<h2>Headline comparison</h2>
{headline_table}
</section>

{runs_html}
</body>
</html>
"""


def render(payloads: list[dict[str, Any]], out: Path) -> Path:
    if not payloads:
        raise SystemExit("No evaluation results to render. Run eval.run_evaluation first.")
    head_rows = "".join(_render_summary_row(p) for p in payloads)
    headline = (
        '<table class="grid"><thead><tr>'
        '<th>Run</th><th>Verdict acc</th><th>Typology acc</th>'
        '<th>Network recall</th><th>Evidence recall</th>'
        '<th>Faithfulness</th><th>Hallucination</th>'
        '</tr></thead><tbody>' + head_rows + '</tbody></table>'
    )
    runs_html = "\n".join(_render_run_section(p) for p in payloads)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_PAGE.format(headline_table=headline, runs_html=runs_html), encoding="utf-8")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="*", help="Result JSON files (or substring match)")
    p.add_argument("--out", default=str(_DEFAULT_OUT))
    args = p.parse_args()
    payloads = _collect(args.files)
    path = render(payloads, Path(args.out))
    print(f"Wrote {len(payloads)} run(s) to {path}")


if __name__ == "__main__":
    main()
