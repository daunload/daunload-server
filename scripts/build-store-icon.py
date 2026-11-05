"""Export the current brand artwork using Chrome Web Store's 96px + 16px padding guide.

Run: .venv/bin/python scripts/build-store-icon.py
Preserves a full-size artwork source so repeated exports do not shrink the icon.
"""
import asyncio
import base64
import json
import struct
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'extensions' / 'ssu-downloader'
STORE = ROOT / 'store-assets' / 'chrome-web-store'


async def main():
    source = EXTENSION / 'branding' / 'store-icon-artwork.png'
    url = 'data:image/png;base64,' + base64.b64encode(source.read_bytes()).decode()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width': 560, 'height': 280}, device_scale_factor=1)
            result = await page.evaluate('''async url => {
                const art = new Image(); art.src = url; await art.decode();
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = 128;
                const ctx = canvas.getContext('2d');
                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                ctx.drawImage(art, 16, 16, 96, 96);
                const pixels = ctx.getImageData(0, 0, 128, 128).data;
                let minX = 128, minY = 128, maxX = -1, maxY = -1;
                for (let y=0; y<128; y++) for (let x=0; x<128; x++) {
                    if (pixels[(y*128+x)*4+3]) {
                        minX=Math.min(minX,x); minY=Math.min(minY,y);
                        maxX=Math.max(maxX,x); maxY=Math.max(maxY,y);
                    }
                }
                return {png: canvas.toDataURL('image/png').split(',')[1], bounds:[minX,minY,maxX,maxY]};
            }''', url)
            assert result['bounds'] == [16, 16, 111, 111], result['bounds']
            png = base64.b64decode(result['png'])
            assert png[:8] == b'\x89PNG\r\n\x1a\n'
            assert struct.unpack('>II', png[16:24]) == (128, 128)
            assert png[24:26] == bytes([8, 6]), 'Expected 8-bit RGBA PNG'
            STORE.mkdir(parents=True, exist_ok=True)
            for target in [EXTENSION / 'icons' / 'icon-128.png', STORE / 'store-icon-128.png']:
                target.write_bytes(png)
            encoded = 'data:image/png;base64,' + result['png']
            await page.set_content('''<html><head><style>
                *{box-sizing:border-box}body{margin:0;font:14px system-ui;display:flex}
                section{width:280px;height:280px;display:grid;place-content:center;justify-items:center;gap:16px}
                .light{background:#fff;color:#222}.dark{background:#18222c;color:#fff}img{display:block;width:128px;height:128px}
                p{margin:0}
                </style></head><body>'''+f'<section class="light"><img src="{encoded}" alt=""><p>밝은 배경</p></section><section class="dark"><img src="{encoded}" alt=""><p>어두운 배경</p></section></body></html>')
            await page.screenshot(path='/tmp/ssu-store-icon-background-check.png')
        finally:
            await browser.close()
    print(json.dumps({'size':[128,128], 'artwork':[96,96], 'transparent_padding':16,
                      'alpha_bounds': result['bounds'], 'format':'RGBA PNG'}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
