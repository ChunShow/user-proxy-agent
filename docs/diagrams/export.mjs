// Render documentation assets only; never starts the application or calls APIs.
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const source = new URL('architecture.html', import.meta.url);
const html = await readFile(source, 'utf8');
const svg = html.match(/<svg\b[\s\S]*?<\/svg>/)?.[0];
if (!svg) throw new Error('Architecture SVG not found in HTML source.');

const browser = await chromium.launch({
  ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
});
try {
  const page = await browser.newPage({
    viewport: { width: 960, height: 960 }, deviceScaleFactor: 2,
  });
  await page.goto(source.href);
  await page.evaluate(() => document.fonts.ready);
  await page.locator('svg').screenshot({
    path: fileURLToPath(new URL('architecture.png', import.meta.url)),
  });
  await writeFile(new URL('architecture.svg', import.meta.url),
    `<?xml version="1.0" encoding="UTF-8"?>\n${svg}\n`);
  console.log('Exported architecture.svg and architecture.png (1920 × 1920).');
} finally {
  await browser.close();
}
