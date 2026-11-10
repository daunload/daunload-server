// 배포용 웹 파일을 만든다: app/web → app/web-dist (또는 첫 인자로 받은 폴더).
//
// 원본(app/web)은 빌드 없이도 그대로 동작한다. 이 스크립트는 같은 동작을 유지한 채 전송량만 줄인다.
// - index.html의 <script src> 순서대로 이어 붙여 main.min.js 하나로 압축한다. 모두 일반 스크립트라 전역을 공유하고,
//   이어 붙여도 실행 순서와 전역 이름이 그대로다(최상위 이름은 압축해도 바뀌지 않는다).
// - <template id="scripts-NAME"> 안의 스크립트는 NAME.min.js로 묶어 처음 필요할 때만 받게 둔다(assets.js의 loadScripts).
// - style.css는 같은 이름으로 압축한다(privacy.html도 같은 파일을 쓴다).
// - 나머지 파일(이미지, privacy.html, Whisper 워커 등)은 그대로 복사한다.
import { transform } from "esbuild";
import { cp, mkdir, readFile, rm, writeFile, readdir } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "app", "web");
const out = path.resolve(process.argv[2] || path.join(root, "app", "web-dist"));
// 실제 사용자 브라우저(PC 크롬 중심, 최신 모바일). 이보다 낮추면 esbuild가 문법을 바꿔 크기가 커진다.
const target = ["chrome110", "edge110", "firefox115", "safari16"];
const SCRIPT = /<script src="\/([\w.-]+\.js)"><\/script>/g;

const read = (name) => readFile(path.join(src, name), "utf8");

async function bundle(names, outName) {
  const code = (await Promise.all(names.map(read))).join(";\n");
  const result = await transform(code, { loader: "js", minify: true, target, legalComments: "none", charset: "utf8" });
  await writeFile(path.join(out, outName), result.code);
  return outName;
}

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await cp(src, out, { recursive: true, filter: (p) => !path.basename(p).startsWith(".") });

let html = await read("index.html");
const bundled = new Set();

// 1) 나중에 받는 묶음: <template id="scripts-NAME">…</template>
const templates = [...html.matchAll(/<template id="scripts-([\w-]+)">([\s\S]*?)<\/template>/g)];
for (const [whole, name, body] of templates) {
  const names = [...body.matchAll(SCRIPT)].map((m) => m[1]);
  names.forEach((n) => bundled.add(n));
  const file = await bundle(names, `${name}.min.js`);
  html = html.replace(whole, `<template id="scripts-${name}"><script src="/${file}"></script></template>`);
}

// 2) 처음 받는 묶음: 남은 <script src>를 적힌 순서대로
const main = [...html.replace(/<template[\s\S]*?<\/template>/g, "").matchAll(SCRIPT)].map((m) => m[1]);
if (!main.length) throw new Error("index.html에서 스크립트를 찾지 못했습니다.");
main.forEach((n) => bundled.add(n));
const mainFile = await bundle(main, "main.min.js");
// 첫 스크립트 자리에 묶음 하나를 두고 나머지 태그는 지운다(템플릿 안의 *.min.js는 그대로).
let first = true;
html = html.replace(/[ \t]*<script src="\/([\w.-]+\.js)"><\/script>\n?/g, (tag, name) => {
  if (!main.includes(name)) return tag;
  if (!first) return "";
  first = false;
  return `  <script src="/${mainFile}"></script>\n`;
});
await writeFile(path.join(out, "index.html"), html);

// 3) CSS
const css = await transform(await read("style.css"), { loader: "css", minify: true, target, legalComments: "none", charset: "utf8" });
await writeFile(path.join(out, "style.css"), css.code);

// 4) 묶은 원본 파일은 배포 폴더에서 뺀다(버전 계산과 전송 대상에서 제외)
await Promise.all([...bundled].map((n) => rm(path.join(out, n))));

// 크기 보고
const rows = [];
for (const name of (await readdir(out)).sort()) {
  if (!/\.(js|mjs|css|html)$/.test(name)) continue;
  const body = await readFile(path.join(out, name));
  rows.push(`${name.padEnd(22)} ${String(body.length).padStart(7)} B  gzip ${String(gzipSync(body, { level: 9 }).length).padStart(6)} B`);
}
console.log(`built ${path.relative(root, out) || out}\n${rows.join("\n")}`);
