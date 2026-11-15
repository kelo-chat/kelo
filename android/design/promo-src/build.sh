#!/bin/bash
# Rebuild the Kelo promo: build.sh [WORKDIR] [STEP ...]
#   STEP = audio video mux posters gifs   (default: all, in that order)
#   WORKDIR (default ./out) holds frames, silent videos and soundtracks; finished files go to ../../dist/promo-video/.
# Scene order/lengths: plans.js (shared by ad.js and audio.py). Preview one scene: snap.py LANG FMT SCENE PREFIX T1 T2 ...
# Needs: the server venv (playwright + chromium), ffmpeg, uv (numpy/scipy for audio.py are pulled in by uv).
set -euo pipefail
cd "$(dirname "$0")"
PY="${KELO_PY:-$(cd ../../.. && pwd)/.venv/bin/python}"
W=${1:-out}; [ $# -gt 0 ] && shift
STEPS=${*:-audio video mux posters gifs}
D=../../dist/promo-video
mkdir -p "$W" "$D"
name() { [ "$1" = h ] && echo 1080p || echo vertical; }
has() { [[ " $STEPS " == *" $1 "* ]]; }

if has audio; then
  for f in h v; do uv run --quiet --no-project --with numpy --with scipy python audio.py $f "$W/aud_$f.wav"; done
fi
if has video; then
  render() { $PY render.py $1 $2 - 30 "$W/vid_$2_$1.mp4" > "$W/log_$2_$1.txt"; }
  render en h & render ko h & wait
  render en v & render ko v & wait
fi
if has mux; then
  # two-pass loudnorm to -14 LUFS integrated, -1 dBTP
  for f in h v; do
    m=$(ffmpeg -hide_banner -i "$W/aud_$f.wav" -af loudnorm=I=-14:TP=-1:LRA=11:print_format=json -f null - 2>&1 | sed -n '/^{/,/^}/p')
    get() { printf '%s' "$m" | python3 -I -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
    ln="loudnorm=I=-14:TP=-1:LRA=11:measured_I=$(get input_i):measured_TP=$(get input_tp):measured_LRA=$(get input_lra):measured_thresh=$(get input_thresh):offset=$(get target_offset):linear=true"
    for l in en ko; do
      ffmpeg -loglevel error -y -i "$W/vid_${f}_$l.mp4" -i "$W/aud_$f.wav" -map 0:v -map 1:a -c:v copy -af "$ln" -ar 48000 -c:a aac -b:a 192k -shortest -movflags +faststart "$D/kelo-ad-$l-$(name $f).mp4"
    done
  done
fi
if has posters; then
  for f in h v; do for l in en ko; do
    $PY snap.py $l $f poster "$W/poster_${f}_$l" 0 >/dev/null
    mv "$W/poster_${f}_${l}_00.00.png" "$D/kelo-ad-$l-$(name $f)-poster.png"
  done; done
fi
if has gifs; then
  ./gifs.sh en "$W" & ./gifs.sh ko "$W" & wait
  for l in en ko; do for sc in hero selfhost models install chat connectors goals; do
    out=$sc; [ $sc = chat ] && out=chat-approval
    [ -f "$W/gif_${sc}_$l.gif" ] && cp "$W/gif_${sc}_$l.gif" "$D/kelo-$out-$l.gif"
  done; done
fi
ls -la "$D"
