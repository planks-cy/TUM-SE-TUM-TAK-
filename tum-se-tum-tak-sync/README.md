# Tum Se Tum Tak – sync QA

1. Push this repo to GitHub.
2. Actions tab → **Build sync test** → Run workflow (start 0, end 30 for a quick test; blank end = full song).
3. Download `sync-test` artifact: `sync-test.mp4`, `word-timestamps.json`, `phrases.json`, `alignment-report.json`.
4. The workflow also commits timestamps into `web/`. Import the repo on Vercel (output dir `web`) to get a live word-by-word preview with the original audio. Words in red = `review_required`.

Vercel only hosts the preview page; it cannot run the alignment/render (no GPU/ffmpeg/long jobs). GitHub Actions does that.
