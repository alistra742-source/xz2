"""Solver accuracy/speed bench on synthetic Zefoy-style word captchas.

Draws random dictionary words the way Zefoy does — coloured glyphs with
per-character jitter, noise dots, 1-2 strike-through lines, slight rotation —
then runs core.solver.solve on each and reports exact-match accuracy, mean
and p95 latency.
"""
import io
import random
import statistics
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from core import solver

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def synth(word, seed):
    rnd = random.Random(seed)
    W, H = 230, 80
    bg = rnd.randint(205, 250)
    img = Image.new("RGB", (W, H), (bg, bg - rnd.randint(0, 12), bg - rnd.randint(0, 20)))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, rnd.randint(38, 46))
    ink = (rnd.randint(0, 70), rnd.randint(0, 40), rnd.randint(0, 70))
    x = rnd.randint(8, 16)
    for ch in word:
        d.text((x, rnd.randint(10, 22)), ch, font=f, fill=ink)
        x += int(f.getlength(ch)) + rnd.randint(-3, 1)
    for _ in range(rnd.randint(90, 220)):                      # noise dots
        d.point((rnd.randint(0, W - 1), rnd.randint(0, H - 1)),
                fill=(rnd.randint(0, 255),) * 3)
    for _ in range(rnd.randint(1, 2)):                        # strike lines
        d.line([(0, rnd.randint(15, 65)), (W, rnd.randint(15, 65))],
               fill=(rnd.randint(0, 130),) * 3, width=rnd.randint(1, 3))
    if rnd.random() < 0.6:                                    # slight skew
        img = img.rotate(rnd.uniform(-4, 4), expand=False,
                         fillcolor=(bg, bg, bg), resample=Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main(n=80):
    random.seed(7)
    words = [w for w in random.sample(solver.WORDS or solver._FALLBACK_WORDS, n)]
    ok = 0
    times = []
    misses = []
    for i, w in enumerate(words):
        blob = synth(w, i)
        t0 = time.time()
        got = solver.solve(blob, deadline=8.0)
        dt = time.time() - t0
        times.append(dt)
        if got == w:
            ok += 1
        else:
            misses.append((w, got))
    times.sort()
    print(f"accuracy {ok}/{n} = {100 * ok / n:.1f}%")
    print(f"latency  mean {statistics.mean(times)*1000:.0f} ms  "
          f"p50 {times[len(times)//2]*1000:.0f} ms  p95 {times[int(n*0.95)-1]*1000:.0f} ms")
    for w, g in misses[:12]:
        print(f"  miss {w} -> {g!r}")
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 80))
