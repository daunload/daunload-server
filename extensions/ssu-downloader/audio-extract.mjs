// CDN mp4에서 소리만 뽑아 16 kHz 모노 Float32로 내보낸다.
// 환경 독립: fetch와 WebCodecs만 쓴다(DOM·chrome.* 없음). 영상 전체를 메모리에 쌓지 않고
// 소비자가 다음 조각을 당길 때만 다음 Range를 읽는다.
import { createFile, MP4BoxBuffer } from "./vendor/mp4box.all.mjs";

const MESSAGES = {
  no_audio: "이 영상에는 소리 트랙이 없습니다.",
  unsupported_codec: "이 브라우저가 이 영상의 소리 형식을 읽지 못합니다.",
  http: "학교 서버가 영상을 주지 않았습니다. 목록을 새로고침한 뒤 다시 시도하세요.",
};
const EXPIRED = new Set([403, 404, 410]);

export class AudioExtractError extends Error {
  constructor(code, options) {
    super(MESSAGES[code], options);
    this.name = "AudioExtractError";
    this.code = code;
  }
}

// Range로 읽는 원본. 주소가 만료되면(403/404/410) refresh()로 한 번 바꿔 같은 범위를 다시 요청한다.
export function openCdnSource(url, { signal, refresh }) {
  let current = url;
  let total = null;

  async function request(start, end) {
    const response = await fetch(current, { headers: { Range: `bytes=${start}-${end}` }, signal });
    if (response.status !== 206) await response.body?.cancel();
    return response;
  }

  async function read(start, end) {
    let response = await request(start, end);
    if (EXPIRED.has(response.status)) {
      current = await refresh();
      response = await request(start, end);
    }
    if (response.status !== 206) throw new AudioExtractError("http");
    const match = /\/(\d+)\s*$/.exec(response.headers.get("Content-Range") || "");
    if (match) total = Number(match[1]);
    return new Uint8Array(await response.arrayBuffer());
  }

  return {
    async size() {
      if (total === null) await read(0, 0);
      return total;
    },
    read,
  };
}

// 상태를 가진 선형 보간. 조각을 어떻게 나눠 넣어도 이어 붙인 결과는 같다.
export function makeResampler(inRate, outRate) {
  const step = inRate / outRate;
  let t = 0; // 다음 출력 위치(현재 입력 조각 기준, -1이면 직전 조각의 마지막 샘플)
  let previous = 0;
  return (input) => {
    if (!input.length) return new Float32Array(0);
    const last = input.length - 1;
    const output = new Float32Array(Math.max(0, Math.ceil((last - t) / step)));
    for (let k = 0; k < output.length; k++, t += step) {
      const i = Math.floor(t);
      const a = i < 0 ? previous : input[i];
      output[k] = a + (input[i + 1] - a) * (t - i);
    }
    t -= input.length;
    previous = input[last];
    return output;
  };
}

// start..end(포함)를 chunkBytes 이하 Range로 나눠 읽는다.
async function readRange(source, start, end, chunkBytes, signal) {
  const out = new Uint8Array(end - start + 1);
  for (let at = start; at <= end; at += chunkBytes) {
    signal?.throwIfAborted();
    out.set(await source.read(at, Math.min(end, at + chunkBytes - 1)), at - start);
  }
  return out;
}

// 최상위 상자 머리만 건너뛰며 읽어 moov 위치를 찾는다(mdat 내용은 읽지 않는다).
async function findMoov(source, signal) {
  const size = await source.size();
  for (let at = 0; at + 8 <= size; ) {
    signal?.throwIfAborted();
    const head = await source.read(at, Math.min(size - 1, at + 15));
    const view = new DataView(head.buffer, head.byteOffset, head.byteLength);
    const type = String.fromCharCode(...head.subarray(4, 8));
    let length = view.getUint32(0);
    if (length === 1) length = Number(view.getBigUint64(8));
    else if (length === 0) length = size - at;
    if (length < 8) break;
    if (type === "moov") return { start: at, end: at + length - 1 };
    at += length;
  }
  throw new AudioExtractError("no_audio");
}

// mp4box에 moov만 넘긴다. getInfo()가 ftyp를 요구해 최소 ftyp를 앞에 붙인다(샘플 위치는 stco의 절대값이라 상관없다).
const FTYP = new Uint8Array([0, 0, 0, 16, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d, 0, 0, 0, 0]);

function parseMoov(moov) {
  const bytes = new Uint8Array(FTYP.length + moov.length);
  bytes.set(FTYP);
  bytes.set(moov, FTYP.length);
  const file = createFile();
  let info = null;
  file.onReady = (ready) => { info = ready; };
  file.appendBuffer(MP4BoxBuffer.fromArrayBuffer(bytes.buffer, 0));
  file.flush();
  if (!info) throw new AudioExtractError("no_audio");
  return { file, info };
}

function decoderConfig(file, track) {
  const config = { sampleRate: track.audio.sample_rate, numberOfChannels: track.audio.channel_count };
  if (track.codec.toLowerCase() === "opus") return { ...config, codec: "opus" };
  const entry = file.getTrackById(track.id).mdia.minf.stbl.stsd.entries[0];
  const data = (entry.esds ?? entry.wave?.esds)?.esd?.descs?.[0]?.descs?.[0]?.data;
  return { ...config, codec: track.codec, ...(data ? { description: data } : {}) };
}

async function supported(config) {
  try {
    return (await AudioDecoder.isConfigSupported(config)).supported === true;
  } catch {
    return false;
  }
}

function toMono(audio) {
  const frames = audio.numberOfFrames;
  const channels = audio.numberOfChannels;
  const mono = new Float32Array(frames);
  const plane = new Float32Array(frames);
  for (let c = 0; c < channels; c++) {
    audio.copyTo(plane, { planeIndex: c, format: "f32-planar" });
    for (let i = 0; i < frames; i++) mono[i] += plane[i];
  }
  if (channels > 1) for (let i = 0; i < frames; i++) mono[i] /= channels;
  return mono;
}

function concat(parts) {
  if (parts.length === 1) return parts[0];
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

export async function* extractPcm(source, { signal, rate = 16000, chunkBytes = 2 * 1024 * 1024 } = {}) {
  const moov = await findMoov(source, signal);
  const { file, info } = parseMoov(await readRange(source, moov.start, moov.end, chunkBytes, signal));
  const track = info.audioTracks[0];
  if (!track) throw new AudioExtractError("no_audio");
  const config = decoderConfig(file, track);
  if (!(await supported(config))) throw new AudioExtractError("unsupported_codec");
  const samples = file.getTrackById(track.id).samples;

  const ready = [];
  let failure = null;
  let wake = null;
  let resample = null;
  // 디코더 오류는 비동기로 오고 코덱을 닫는다. 원래 오류를 cause로 담아 사용자 문구로 바꾼다.
  const codecError = (error) => new AudioExtractError("unsupported_codec", { cause: failure ?? error });
  const check = () => { if (failure) throw codecError(failure); };
  const guard = async (step) => {
    check();
    try {
      return await step();
    } catch (error) {
      throw codecError(error);
    }
  };
  const decoder = new AudioDecoder({
    output: (audio) => {
      try {
        resample ??= makeResampler(audio.sampleRate, rate);
        ready.push(resample(toMono(audio)));
      } finally {
        audio.close();
      }
    },
    error: (error) => { failure = error; wake?.(); },
  });
  const drain = () => {
    check();
    const parts = ready.splice(0).filter((p) => p.length);
    return parts.length ? concat(parts) : null;
  };
  const settled = () => new Promise((resolve) => {
    if (decoder.decodeQueueSize === 0 || failure) return resolve();
    const done = () => {
      if (decoder.decodeQueueSize > 0 && !failure) return;
      decoder.removeEventListener("dequeue", done);
      wake = null;
      resolve();
    };
    wake = done;
    decoder.addEventListener("dequeue", done);
  });

  try {
    await guard(() => decoder.configure(config));
    // 오디오 샘플을 파일 순서대로 묶어 chunkBytes 이하 Range 하나로 읽는다(사이에 낀 영상 바이트는 버린다).
    for (let i = 0; i < samples.length; ) {
      signal?.throwIfAborted();
      const start = samples[i].offset;
      let j = i + 1;
      while (j < samples.length && samples[j].offset >= start
        && samples[j].offset + samples[j].size - start <= chunkBytes) j++;
      const last = samples[j - 1];
      const bytes = await source.read(start, last.offset + last.size - 1);
      signal?.throwIfAborted();
      await guard(() => {
        for (; i < j; i++) {
          const sample = samples[i];
          decoder.decode(new EncodedAudioChunk({
            type: "key",
            timestamp: Math.round((sample.cts * 1e6) / sample.timescale),
            duration: Math.round((sample.duration * 1e6) / sample.timescale),
            data: bytes.subarray(sample.offset - start, sample.offset - start + sample.size),
          }));
        }
      });
      await settled();
      const pcm = drain();
      if (pcm) {
        yield pcm;
        signal?.throwIfAborted();
      }
    }
    await guard(() => decoder.flush());
    const pcm = drain();
    if (pcm) yield pcm;
    return { duration: track.duration / track.timescale };
  } finally {
    if (decoder.state !== "closed") decoder.close();
  }
}
