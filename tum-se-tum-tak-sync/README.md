# Tum Se Tum Tak – sync QA

1. Push this repo to GitHub.
2. Actions tab → **Build sync test** → Run workflow (start 0, end 30 for a quick test; blank end = full song).
3. Download `sync-test` artifact: `sync-test.mp4`, `word-timestamps.json`, `phrases.json`, `alignment-report.json`.
4. The workflow also commits timestamps into `web/`. Import the repo on Vercel (output dir `web`) to get a live word-by-word preview with the original audio. Words in red = `review_required`.

Vercel only hosts the preview page; it cannot run the alignment/render (no GPU/ffmpeg/long jobs). GitHub Actions does that.

## Motion graphics
Run the workflow with `mode = cinematic` (or `both`). `motion.py` renders `cinematic.mp4` (1080x1920, 30 fps) from the same word timestamps: dark palette, film grain, haze, crimson light leaks, particles, and per-line motifs (cage, TUM-to-TAK axis, split divider, clock marks, warm lights, crimson accents). Presets: fade_reveal, tracking_expand, vertical_drift, masked_reveal, letter_fragment, soft_blur_in, kinetic_cut, split_composition, slow_scale, fade_to_black. Edit `RULES` in `motion.py` to change which line gets which preset/motif. Full song takes roughly 20-30 min on a GitHub runner; try a 30 s range first. Review the sync test before the final render.
