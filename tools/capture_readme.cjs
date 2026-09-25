#!/usr/bin/env node
// Photograph the landing page's reTerminal for the README hero.
//
// GitHub renders no CSS in a README, so the frame has to be part of the image.
// Rather than drawing a second, slightly different frame, this takes the one
// on the page: the served site, one frame painted without animation, every
// other element hidden and the background transparent, so the picture sits on
// GitHub's light and dark themes alike.
//
//   tools/build_site.sh --serve 8000 &
//   NODE_PATH=/path/to/node_modules CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//     node tools/capture_readme.cjs [http://127.0.0.1:8000/]
//
// It is the only thing here that wants Node and playwright-core.

const path = require('path');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright-core');

const URL = process.argv[2] || 'http://127.0.0.1:8000/';
const OUT = path.join(__dirname, '..', '.github', 'images', 'dashboard.png');
// Rendered at twice the README's 760 px, so the panel stays sharp on a 2x
// screen. The margin only keeps the 1 px outline inside the image.
const WIDTH = 760;
const MARGIN = 2;

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(URL, { waitUntil: 'networkidle' });

  await page.addStyleTag({
    content: `
      html, body, .hero { background: transparent !important; }
      .hero::before, .top, .hero-copy, .rt-caption, main > section:not(.hero), .foot { display: none !important; }
      .hero { display: block !important; min-height: 0 !important; padding: ${MARGIN}px !important; }
      .hero-demo { opacity: 1 !important; transform: none !important; }
      .rt { width: ${WIDTH}px !important; max-width: none !important; margin: 0 !important; }
      /* On the page the device casts a soft shadow and a faint yellow glow.
         Cut out, those become a translucent haze to the edge of the image,
         which reads as a dirty background on either GitHub theme. Keep only
         the device's own edges. */
      .rt-face {
        box-shadow:
          inset 0 0 0 calc(var(--mm) * 0.35) rgba(255, 255, 255, 0.55),
          inset 0 calc(var(--mm) * -0.5) calc(var(--mm) * 0.4) rgba(0, 0, 0, 0.06),
          0 0 0 1px rgba(0, 0, 0, 0.35) !important;
      }
    `,
  });

  // The page's own state and repaint, so this frame is exactly what a
  // visitor sees: the summer morning in German, page one, no transition.
  await page.evaluate(async () => {
    state.season = 'summer';
    state.lang = 'de';
    state.page = 1;
    await repaint('instant');
  });
  await page.waitForTimeout(300);

  const box = await page.locator('#panel').boundingBox();
  await page.screenshot({
    path: OUT,
    omitBackground: true,
    clip: { x: box.x - MARGIN, y: box.y - MARGIN, width: box.width + MARGIN * 2, height: box.height + MARGIN * 2 },
  });
  await browser.close();

  // A straight capture is about 2 MB. The panel is a handful of inks and the
  // frame a few greys, so 256 colours lose nothing visible and a quarter of
  // the weight. Pillow does it; it is already a dependency of everything here.
  execFileSync('python3', ['-c', [
    'import sys',
    'from PIL import Image',
    'im = Image.open(sys.argv[1]).convert("RGBA")',
    // Chrome leaves the body at alpha 254 and the empty corners at 1 or 2;
    // snap both, so the device is solid and everything around it is clear.
    'a = im.getchannel("A").point(lambda v: 0 if v < 8 else 255 if v > 247 else v)',
    'im.putalpha(a)',
    'q = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)',
    // The quantizer averages alpha again inside its palette; snap it there too.
    'pal = q.getpalette(rawmode="RGBA")',
    'pal[3::4] = [0 if v < 8 else 255 if v > 247 else v for v in pal[3::4]]',
    'q.putpalette(pal, rawmode="RGBA")',
    'q.save(sys.argv[1], optimize=True)',
  ].join('\n'), OUT]);
  console.log(`wrote ${OUT}`);
})();
