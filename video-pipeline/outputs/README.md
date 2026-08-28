# Sample outputs

After running `python run.py`, this directory is populated with:

- `audit.jsonl` — structured event log of every action
- `final_state.json` — full agent state at the end of the run
- `report.html` — self-contained HTML summary
- `logs/pipeline.log` — full text log
- `raw/` — original downloaded videos
- `transformed/` — re-encoded/branded videos
- `published/videos/` — final destination (LocalPublisher output)
- `published/manifests/` — JSON sidecars with AI-enriched metadata

A successful run on 5 short CC videos produces ~40 MB of MP4 files.
