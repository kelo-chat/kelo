#!/bin/bash
# GIFs: gifs.sh LANG [WORKDIR] [SCENE ...]
# Renders frames into WORKDIR (default ./out) and encodes WORKDIR/gif_SCENE_LANG.gif. Default: every GIF scene.
# Scene lengths come from plans.js (PLANS.gif). Landscape scenes use 'gl' (1600x900 -> 800x450, 15 fps),
# phone scenes use 'gp' (1200x1500 -> 800x1000, 12 fps).
cd "$(dirname "$0")"
PY="${KELO_PY:-$(cd ../../.. && pwd)/.venv/bin/python}"
l=$1; W=${2:-out}; shift; [ $# -gt 0 ] && shift
SCENES=${*:-hero selfhost models install chat connectors goals}
mkdir -p "$W"
for sc in $SCENES; do
  case $sc in hero|selfhost|install) f=gl; fps=15 ;; *) f=gp; fps=12 ;; esac
  d=$W/gf_${sc}_${l}
  rm -f "$d"/f*.png 2>/dev/null
  $PY render.py $l $f $sc $fps "$d" >/dev/null
  ffmpeg -loglevel error -y -framerate $fps -i "$d/f%05d.png" -vf "scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle" "$W/gif_${sc}_${l}.gif"
  echo "$sc $l $(stat -f %z "$W/gif_${sc}_${l}.gif")"
done
