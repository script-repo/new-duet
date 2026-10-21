// Renders the Tandem icon (two linked rings on clay) to PNG with no dependencies: analytic shapes,
// 4x4 supersampling, and a minimal PNG encoder over node:zlib. Run: npm run icons
import { writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';

const BG = [0xb2, 0x53, 0x2e];
const FG = [0xff, 0xfa, 0xf6];
// geometry in the same 64-unit space as public/icon.svg
const RINGS = [
  { cx: 25, cy: 32, r: 11, w: 5, alpha: 1 },
  { cx: 39, cy: 32, r: 11, w: 5, alpha: 0.8 },
];

function render(size, { rounded }) {
  const px = Buffer.alloc(size * size * 4);
  const S = 4;
  const k = 64 / size;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let r = 0, g = 0, b = 0, a = 0;
      for (let sy = 0; sy < S; sy++) {
        for (let sx = 0; sx < S; sx++) {
          const ux = (x + (sx + 0.5) / S) * k;
          const uy = (y + (sy + 0.5) / S) * k;
          if (rounded && !inRoundRect(ux, uy, 64, 16)) continue;
          let c = BG.slice();
          for (const ring of RINGS) {
            const d = Math.hypot(ux - ring.cx, uy - ring.cy);
            if (Math.abs(d - ring.r) <= ring.w / 2) c = c.map((v, i) => v * (1 - ring.alpha) + FG[i] * ring.alpha);
          }
          r += c[0]; g += c[1]; b += c[2]; a += 255;
        }
      }
      const n = S * S;
      const o = (y * size + x) * 4;
      const cov = a / n / 255;
      px[o] = cov ? Math.round(r / n / cov) : 0;
      px[o + 1] = cov ? Math.round(g / n / cov) : 0;
      px[o + 2] = cov ? Math.round(b / n / cov) : 0;
      px[o + 3] = Math.round(a / n);
    }
  }
  return png(size, size, px);
}

function inRoundRect(x, y, s, rad) {
  const cx = Math.min(Math.max(x, rad), s - rad);
  const cy = Math.min(Math.max(y, rad), s - rad);
  return Math.hypot(x - cx, y - cy) <= rad;
}

const CRC = new Int32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c;
});
function crc32(buf) {
  let c = -1;
  for (const byte of buf) c = CRC[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(w, h, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) rgba.copy(raw, y * (w * 4 + 1) + 1, y * w * 4, (y + 1) * w * 4);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const out = new URL('../public/', import.meta.url);
// full-bleed squares: safe as maskable (rings sit inside the 80% safe zone) and for iOS, which rounds itself
writeFileSync(new URL('icon-192.png', out), render(192, { rounded: false }));
writeFileSync(new URL('icon-512.png', out), render(512, { rounded: false }));
writeFileSync(new URL('apple-touch-icon.png', out), render(180, { rounded: false }));
console.log('icons written to public/');
