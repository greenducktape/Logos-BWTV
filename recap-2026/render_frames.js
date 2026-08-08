/**
 * Rendert die Recap-Szene Frame fuer Frame deterministisch aus.
 *
 * Statt CSS-Animationen in Echtzeit laufen zu lassen, setzt der Renderer die
 * Szenenzeit pro Frame explizit ueber window.__setT(t). Damit ist jeder Frame
 * exakt reproduzierbar und unabhaengig von der Rendergeschwindigkeit.
 *
 * Konfiguration kommt ueber Environment-Variablen aus build_recap.py.
 */
const path = require('path');
const { chromium } = require('playwright');

const HTML = process.env.RECAP_HTML;
const OUT = process.env.RECAP_FRAMES;
const W = parseInt(process.env.RECAP_W, 10);
const H = parseInt(process.env.RECAP_H, 10);
const FPS = parseInt(process.env.RECAP_FPS, 10);
const N = parseInt(process.env.RECAP_N, 10);

(async () => {
  const browser = await chromium.launch({
    args: ['--force-device-scale-factor=1', '--hide-scrollbars',
           '--disable-lcd-text', '--font-render-hinting=none'],
  });
  const page = await browser.newPage({
    viewport: { width: W, height: H },
    deviceScaleFactor: 1,
  });

  await page.goto('file://' + HTML, { waitUntil: 'load' });
  await page.evaluate(() => window.__ready);

  // Die Seite spielt sich beim Laden selbst ab. Fuer den Export wird sie
  // angehalten und unskaliert gestellt; danach setzt der Renderer jeden
  // Zeitpunkt einzeln, damit die Frames unabhaengig vom Tempo exakt sind.
  await page.evaluate(() => window.__capture());
  const stage = page.locator('#stage');

  for (let i = 0; i < N; i++) {
    const t = i / FPS;
    await page.evaluate(t => window.__setT(t), t);
    await stage.screenshot({
      path: path.join(OUT, 'f' + String(i + 1).padStart(4, '0') + '.png'),
      animations: 'disabled',
    });
    if ((i + 1) % 25 === 0 || i + 1 === N) {
      process.stdout.write(`  frame ${i + 1}/${N}\n`);
    }
  }

  await browser.close();
})().catch(err => { console.error(err); process.exit(1); });
