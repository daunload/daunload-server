"""Capture current extension UI with disclosed sample data for Chrome Web Store.

Run: .venv/bin/python scripts/build-store-assets.py
Requires the project's Playwright development environment and Chrome.
No school login, network download, or runtime extension file changes are used.
"""
import argparse
import asyncio
import json
import shutil
import struct
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'extensions' / 'ssu-downloader'
DEFAULT_OUTPUT = ROOT / 'store-assets' / 'chrome-web-store'

SAMPLES = [
    ('프로그래밍 기초 1주차 · 변수와 자료형.mp4', 240_000_000),
    ('프로그래밍 기초 2주차 · 조건문과 반복문.mp4', 320_000_000),
    ('자료구조 1주차 · 배열과 리스트.mp4', 280_000_000),
    ('자료구조 2주차 · 스택과 큐.mp4', 360_000_000),
]


def sample_jobs(states):
    result = {}
    for index, state in enumerate(states):
        name, size = SAMPLES[index]
        result[f'sample-{index}'] = {
            'filename': name, 'state': 'interrupted' if state == 'cancelled' else state,
            'totalBytes': size, 'bytesReceived': size if state == 'complete' else int(size * (.62 if index == 0 else .28)) if state == 'in_progress' else 0,
            'cancelled': state == 'cancelled',
            'error': '학교 서버에 연결하지 못했습니다. 대시보드에서 다시 시도해 주세요.' if state == 'interrupted' else '',
        }
    return result


async def capture(output: Path, executable: str | None):
    output.mkdir(parents=True, exist_ok=True)
    screenshots = output / 'screenshots'
    screenshots.mkdir(exist_ok=True)
    icon = output / 'store-icon-128.png'
    shutil.copyfile(EXTENSION / 'icons' / 'icon-128.png', icon)
    png = icon.read_bytes()
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    assert struct.unpack('>II', png[16:24]) == (128, 128)
    assert png[24:26] == bytes([8, 6]), 'Expected RGBA PNG icon with transparent store padding'
    scenes = [
        ('01-folder-choice.jpg', False, ['queued', 'queued']),
        ('02-bulk-queue.jpg', True, ['in_progress', 'in_progress', 'queued', 'queued']),
        ('03-completed.jpg', True, ['complete', 'complete', 'complete', 'complete']),
        ('04-cancel-and-error.jpg', True, ['complete', 'interrupted', 'cancelled']),
    ]
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome' if not executable else None, executable_path=executable, headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 800}, device_scale_factor=1, color_scheme='light')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            # Only manager-list is used during initialisation. The preview never starts file transfers.
            await page.add_init_script('''
                window.chrome = { runtime: { sendMessage: async () => ({result: {}}) },
                    storage: { onChanged: { addListener: () => {} } } };
            ''')
            await page.goto((EXTENSION / 'manager.html').as_uri())
            await page.wait_for_function('typeof render === "function"')
            await page.add_style_tag(content='body { zoom: .8; }')
            await page.evaluate('''() => {
                const note = document.createElement('div');
                note.textContent = '예시 화면 · 강의 정보와 진행률은 예시입니다';
                note.style.cssText = 'position:fixed;right:20px;bottom:10px;padding:5px 10px;border:1px solid #a0b1b1;border-radius:6px;background:#fff;color:#333;font-size:13px;z-index:10';
                document.body.append(note);
            }''')
            for name, folder, states in scenes:
                await page.evaluate('''({folder, jobs}) => {
                    directory = folder ? {name: '내 강의 모음'} : null;
                    active.clear();
                    for (const [key, job] of Object.entries(jobs)) {
                        if (job.state === 'in_progress') active.set(key, new AbortController());
                    }
                    document.querySelector('#folder').textContent = folder ? '저장 폴더: 내 강의 모음' : '폴더를 선택하면 대기 중인 영상이 자동으로 저장됩니다.';
                    render(jobs);
                    window.scrollTo(0, 0);
                }''', {'folder': folder, 'jobs': sample_jobs(states)})
                await page.evaluate('document.fonts.ready')
                assert await page.locator('#jobs > li').count() == len(states)
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                # Capture the actual full-bleed viewport; JPEG has no alpha channel.
                await page.screenshot(path=str(screenshots / name), type='jpeg', quality=95, full_page=False)
            assert not errors, errors
        finally:
            await browser.close()
    manifest = {
        'source_extension_version': json.loads((EXTENSION / 'manifest.json').read_text())['version'],
        'icon': {'file': icon.name, 'width': 128, 'height': 128, 'format': 'PNG', 'color': 'RGBA 32-bit', 'alpha': True, 'artwork_width': 96, 'artwork_height': 96, 'transparent_padding': 16},
        'screenshots': [{'file': f'screenshots/{name}', 'width': 1280, 'height': 800, 'format': 'JPEG', 'alpha': False} for name, _, _ in scenes],
        'sample_data': True,
        'note': 'Actual extension UI with fictional course names, sizes and states; screenshot disclosure is visible.',
    }
    (output / 'asset-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    archive_path = output / 'store-upload-assets.zip'
    with ZipFile(archive_path, 'w', compression=ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file() and path != archive_path:
                archive.write(path, path.relative_to(output))
    print(json.dumps({'output': str(output), 'icon': str(icon), 'screenshots': len(scenes), 'archive': str(archive_path)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--browser-executable')
    args = parser.parse_args()
    asyncio.run(capture(args.out.resolve(), args.browser_executable))
