# Video draft and capture plan

## What exists

`output/video/dolos-silent-draft.mp4` is a 114-second silent draft at 1920 by 1080 pixels and 24 frames per second. The seven numbered MP4 files are separate clips. `docs/video-script.md` contains the timed narration. `output/video/narration.srt` contains matching subtitles. `output/video/evidence.json` contains the actual local model outputs used for the figures.

This is a data animation, not a recording of the website. Every scene states that fact. The browser and native-app connection was not available in this session. No screen-recording tool was exposed. No face, voice or music has been generated.

The primary visual track can play under your narration. The separate clips can also serve as B-roll. A-roll normally means a presenter speaking to camera. You do not need a face-camera track for this draft.

## Record the narration

1. Open the silent draft and the script.
2. Use a quiet room and a microphone close to your mouth.
3. Read one timed section at a time. Keep the pauses between sections.
4. Record WAV or M4A audio. Keep the complete recording within 114 seconds.
5. Listen for clipping, loud breaths and words that are not clear.
6. Add the narration with the command below.

```sh
nix develop
uv run --extra pdf-llm --with imageio-ffmpeg --with fonttools --with brotli \
  python tools/build_video_draft.py --voiceover /absolute/path/to/your-narration.wav
```

The command writes `output/video/dolos-with-voiceover.mp4`. It keeps the existing visual track. It pads short audio with silence and cuts audio after 114 seconds. Retake a section if the cut would remove words. No narration recording is included now.

## Replace scenes with real website footage

The strongest final submission will show the actual interface. When screen recording is available, capture the clips below. Use a clean browser window and keep secrets and unrelated tabs out of view. Record at 1080p. Test the deployed API before capture.

| Draft time | Real interface shot to capture |
| --- | --- |
| 0:00-0:12 | Open the mixed-network example. Show the readable transaction graph. |
| 0:12-0:29 | Show the static GraphSAGE-to-XGBoost pipeline label. Click **Analyze network** once. Keep the actual waiting state. |
| 0:29-0:46 | Select a positive result. Open its model context. Show the actual earlier transfers. |
| 0:46-1:08 | Show **Credit for this finding**. Keep the signed values readable. Scroll to the actual tree paths. |
| 1:08-1:22 | Select **Ordinary payment comparison** and run analysis. Show its eight non-positive scores. |
| 1:22-1:39 | Upload all four synthetic PDFs in a new investigation. Show the accepted row count and resulting graph. If you cut waiting time, add the visible label **Processing time shortened**. |
| 1:39-1:54 | Return to the graph. End on the prototype statement and website address. |

Do not substitute the animation for evidence that the deployed website works. Keep its animation label if you use the generated clips. Do not add a false cursor, progress bar or browser frame.

## What the figures mean

- The selected transfer is red. Other paths show source transfers or history.
- The graph groups repeated source-target paths. A count label states how many records share that path.
- Contribution bars show signed raw-margin contributions. They are not percentages.
- The graph-feature credit comes from the GraphSAGE-derived inputs to the final XGBoost classifier. It is not a separate vote.
- The ordinary-payment result concerns this generated example only. It does not certify real transfers as safe.
- PDF thumbnails show the existing synthetic statement pack. The displayed upload totals come from the recorded local verification report.
- The website and this draft use synthetic teaching-model weights. They do not claim the saved IBM benchmark results.

## Rebuild the draft

The renderer uses the project fonts from the frontend dependencies. Install them before the first build.

```sh
nix develop
pnpm --dir frontend install --frozen-lockfile
uv run --extra pdf-llm --with imageio-ffmpeg --with fonttools --with brotli \
  python tools/build_video_draft.py
```

The build requests fresh local hybrid scores, checks contribution sums, renders the figures and PDFs, and encodes the clips. It does not call an extraction provider or the deployed website. `manifest.json` records the run ID, duration and scene times. A contact sheet supports visual review.
