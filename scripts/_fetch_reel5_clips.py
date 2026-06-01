"""One-shot helper: fetch Pexels clips for Reel #5 (3 Countries).

11 sub-clips, each cut to align with a specific text reveal in
reels/index.html. Layout:

  hook  (0.0 – 3.0)   airplane sky
  --- DUBAI (3-10) ---
  duba_flag    (3.0 – 5.0)
  duba_plane   (5.0 – 7.0)
  duba_scene   (7.0 – 10.0)
  --- PORTUGAL (10-20) ---
  port_flag    (10.0 – 12.0)
  port_plane   (12.0 – 14.0)
  port_scene   (14.0 – 20.0)
  --- BALI (20-27) ---
  bali_temple  (20.0 – 22.0)
  bali_villa   (22.0 – 24.0)
  bali_pool    (24.0 – 27.0)
  --- CTA (27-30) ---
  cta          (27.0 – 30.0)
"""
from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from pexels_fetcher import (  # noqa: E402
    search_and_download_videos,
    PEXELS_VIDEO_DIR,
)

CLIPS = [
    ("airplane window clouds sky", 3.0, "hook"),
    ("uae flag waving", 2.0, "duba_flag"),
    ("airplane landing runway", 2.0, "duba_plane"),
    ("dubai burj khalifa skyline", 3.0, "duba_scene"),
    ("portugal flag waving", 2.0, "port_flag"),
    ("airplane sky window", 2.0, "port_plane"),
    ("lisbon portugal yellow tram", 6.0, "port_scene"),
    ("bali temple sunrise", 2.0, "bali_temple"),
    ("bali villa infinity pool", 2.0, "bali_villa"),
    ("tropical beach palm trees", 3.0, "bali_pool"),
    ("airplane wing sunset", 3.0, "cta"),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    for query, dur, slug_part in CLIPS:
        slug_temp = f"_reel5_{slug_part}_tmp"
        search_and_download_videos(query, [dur], slug=slug_temp)
        src = PEXELS_VIDEO_DIR / f"{slug_temp}_b1.mp4"
        dst = PEXELS_VIDEO_DIR / f"reel5_{slug_part}.mp4"
        shutil.move(str(src), str(dst))
        print(f"OK {slug_part}: {dst.name}  ({query!r})")


if __name__ == "__main__":
    main()
