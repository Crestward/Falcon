# demo_cache/

Pre-recorded investigation runs, one per fraud typology. The recruiter-facing
demo on the landing page replays these instead of triggering live LLM calls,
so:

- Recruiter clicks → animation plays in ~6–10 seconds (vs. ~30 seconds live)
- Zero API spend per click — malicious traffic can't drain the Anthropic quota
- The demo always works, even if the Anthropic key is rotated or rate-limited

## Files

`STRUCTURING.json`, `LAYERING.json`, `ACCOUNT_TAKEOVER.json`,
`MULE_NETWORK.json`, `PEP_EXPOSURE.json` — one per typology that the
dashboard's typology cards expose.

## Schema

```json
{
  "typology": "STRUCTURING",
  "captured_at": "2026-05-21T20:00:00+00:00",
  "alert_id": "ALERT0008",
  "investigation": { /* same shape as GET /investigations/{id} */ },
  "events":   [ /* same shape as GET /investigations/{id}/events */ ],
  "traces":   [ /* same shape as GET /investigations/{id}/traces */ ]
}
```

## Refreshing the cache

```powershell
# All five typologies, ~$1–2 of Anthropic credit per full run
python -m scripts.capture_demo_runs

# Or just one
python -m scripts.capture_demo_runs --typology STRUCTURING

# Or pick a specific alert id
python -m scripts.capture_demo_runs --typology MULE_NETWORK --alert ALERT0003
```

The script picks the strongest matching alert for each typology and runs a
fresh investigation in-process, auto-resuming HITL pauses with a neutral
annotation so every capture lands with a case file. If any capture errors,
the previous JSON for that typology is left untouched.

## Notes

- The seed data is synthetic — no real PII is ever cached. The PII rail
  scrubs payloads at write time anyway; this is belt-and-braces.
- The dashboard exposes a "▶ Run live" toggle on each typology brief for
  the owner of the deployment; the default click stays cache-only.
- If a cache file is missing, the dashboard falls back to a live trigger
  for that typology.
