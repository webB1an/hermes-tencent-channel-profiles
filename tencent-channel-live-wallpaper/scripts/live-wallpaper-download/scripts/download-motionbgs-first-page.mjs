/**
 * MotionBGs live wallpaper downloader
 *
 * Default behavior:
 *   Download the newest listing page from https://motionbgs.com/.
 *   Videos are saved to project-folder\downloads.
 *   History is saved in project-folder\config\downloaded-motionbgs-detail-urls.json.
 *
 * Common commands:
 *   node .\scripts\download-motionbgs-first-page.mjs
 *   node .\scripts\download-motionbgs-first-page.mjs --page 2
 *   node .\scripts\download-motionbgs-first-page.mjs --out "D:\Wallpapers\MotionBGs"
 *   node .\scripts\download-motionbgs-first-page.mjs --dry-run
 *   node .\scripts\download-motionbgs-first-page.mjs --quality hd
 */

import { mkdir, readFile, writeFile, stat, rename, unlink } from "node:fs/promises";
import { Buffer } from "node:buffer";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = "https://motionbgs.com/";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36";
const FETCH_RETRIES = 5;
const FETCH_RETRY_DELAY_MS = 3000;
const CURL_CONNECT_TIMEOUT_SECONDS = 60;
const CURL_REQUEST_TIMEOUT_SECONDS = 300;
const CURL_PROCESS_TIMEOUT_MS = (CURL_REQUEST_TIMEOUT_SECONDS + 30) * 1000;
const CURL_DOWNLOAD_TIMEOUT_SECONDS = 1800;
const CURL_IP_VERSION = "--ipv4";
const SCRIPT_PATH = fileURLToPath(import.meta.url);
const SCRIPT_DIR = path.dirname(SCRIPT_PATH);
const PROJECT_DIR = path.dirname(SCRIPT_DIR);

function usage() {
  return `MotionBGs live wallpaper downloader

Usage:
  node "${SCRIPT_PATH}"
  node "${SCRIPT_PATH}" --page 2
  node "${SCRIPT_PATH}" -p 3
  node "${SCRIPT_PATH}" --out "D:\\Wallpapers\\MotionBGs"
  node "${SCRIPT_PATH}" --page 3 --out "D:\\Wallpapers\\MotionBGs"
  node "${SCRIPT_PATH}" --quality hd
  node "${SCRIPT_PATH}" --dry-run
  node "${SCRIPT_PATH}" --help

Options:
  -p, --page <number>   Download wallpapers from a specific listing page. Default: 1.
  -o, --out <path>      Save wallpaper videos to this folder. Default: project-folder\\downloads.
  -q, --quality <4k|hd> Video quality to download. Default: 4k.
  --dry-run             Parse pages and print what would be downloaded without downloading files.
  -h, --help            Show this tutorial.

Workflow:
  1. Fetch the selected MotionBGs listing page.
  2. Parse wallpaper slugs and IDs from thumbnail URLs (/media/{ID}/ pattern).
  3. Skip any wallpaper slug already recorded in project-folder\\config\\downloaded-motionbgs-detail-urls.json.
  4. Download directly from /dl/{quality}/{ID} - no detail page visit needed.
  5. Save the latest run to project-folder\\config\\manifest-motionbgs.json.
`;
}

function parseArgs(argv) {
  const options = { page: 1, outDir: path.join(PROJECT_DIR, "downloads"), dryRun: false, limit: 999999, quality: "4k" };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "-h" || arg === "--help") {
      options.help = true;
      continue;
    }
    if (arg === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (arg === "-p" || arg === "--page") {
      const value = argv[i + 1];
      if (!value) throw new Error(`${arg} requires a page number.`);
      options.page = Number(value);
      i += 1;
      continue;
    }
    if (arg.startsWith("--page=")) {
      options.page = Number(arg.slice("--page=".length));
      continue;
    }
    if (arg === "-o" || arg === "--out") {
      const value = argv[i + 1];
      if (!value) throw new Error(`${arg} requires a folder path.`);
      options.outDir = path.resolve(value);
      i += 1;
      continue;
    }
    if (arg.startsWith("--out=")) {
      options.outDir = path.resolve(arg.slice("--out=".length));
      continue;
    }
    if (arg === "-q" || arg === "--quality") {
      const value = argv[i + 1]?.toLowerCase();
      if (!value || !["4k", "hd"].includes(value)) {
        throw new Error(`${arg} requires "4k" or "hd".`);
      }
      options.quality = value;
      i += 1;
      continue;
    }
    if (arg.startsWith("--quality=")) {
      const value = arg.slice("--quality=".length).toLowerCase();
      if (!["4k", "hd"].includes(value)) {
        throw new Error(`--quality must be "4k" or "hd", got "${value}".`);
      }
      options.quality = value;
      continue;
    }
    if (arg === "-l" || arg === "--limit") {
      const value = argv[i + 1];
      if (!value) throw new Error(`${arg} requires a number.`);
      options.limit = Number(value);
      i += 1;
      continue;
    }
    if (arg.startsWith("--limit=")) {
      options.limit = Number(arg.slice("--limit=".length));
      continue;
    }
    throw new Error(`Unknown option: ${arg}\n\n${usage()}`);
  }

  if (!Number.isInteger(options.page) || options.page < 1) {
    throw new Error("Page must be a positive integer.");
  }

  return options;
}

const options = parseArgs(process.argv.slice(2));
if (options.help) {
  console.log(usage());
  process.exit(0);
}

const OUT_DIR = options.outDir;
const CONFIG_DIR = path.join(PROJECT_DIR, "config");
const MANIFEST = path.join(CONFIG_DIR, "manifest-motionbgs.json");
const URL_RECORD = path.join(CONFIG_DIR, "downloaded-motionbgs-detail-urls.json");

function getPageUrl(page) {
  return page === 1 ? ROOT : `${ROOT}${page}/`;
}

function normalizeDetailUrl(value) {
  const parsed = new URL(value, ROOT);
  parsed.hash = "";
  parsed.search = "";
  return parsed.href;
}

function slugFromUrl(url) {
  try {
    const parsed = new URL(url, ROOT);
    const segments = parsed.pathname.split("/").filter(Boolean);
    return segments[segments.length - 1] || null;
  } catch {
    return null;
  }
}

async function readJsonFile(filePath, fallback) {
  try {
    return JSON.parse(await readFile(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

async function loadUrlRecords() {
  const records = await readJsonFile(URL_RECORD, []);
  const manifest = await readJsonFile(MANIFEST, []);
  const byUrl = new Map();

  for (const record of records) {
    if (record?.detailUrl) {
      byUrl.set(normalizeDetailUrl(record.detailUrl), {
        ...record,
        detailUrl: normalizeDetailUrl(record.detailUrl),
      });
    }
  }

  for (const item of manifest) {
    if (!item?.detailUrl || !["downloaded", "skipped-existing", "skipped-detail-url"].includes(item.status)) continue;
    const detailUrl = normalizeDetailUrl(item.detailUrl);
    if (!byUrl.has(detailUrl)) {
      byUrl.set(detailUrl, {
        detailUrl,
        name: item.name || item.slug || null,
        filePath: item.filePath || null,
        status: item.status || null,
        wallpaperId: item.wallpaperId || null,
        recordedAt: new Date().toISOString(),
        migratedFromManifest: true,
      });
    }
  }

  return byUrl;
}

async function saveUrlRecords(recordsByUrl) {
  const records = [...recordsByUrl.values()].sort((a, b) => a.detailUrl.localeCompare(b.detailUrl));
  await writeFile(URL_RECORD, JSON.stringify(records, null, 2), "utf8");
}

function runCurlCapture(args, label) {
  return new Promise((resolve, reject) => {
    const stdout = [];
    const stderr = [];
    const child = spawn("curl", args, { stdio: ["ignore", "pipe", "pipe"] });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`${label} timed out after ${CURL_PROCESS_TIMEOUT_MS / 1000}s`));
    }, CURL_PROCESS_TIMEOUT_MS);

    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve(Buffer.concat(stdout).toString("utf8"));
        return;
      }
      const message = Buffer.concat(stderr).toString("utf8").trim();
      reject(new Error(`${label} failed with curl exit code ${code}${message ? `: ${message}` : ""}`));
    });
  });
}

async function fetchText(url) {
  return runCurlCapture(
    [
      "-fsSL",
      "--retry",
      String(FETCH_RETRIES),
      "--retry-delay",
      String(FETCH_RETRY_DELAY_MS / 1000),
      "--retry-all-errors",
      "--connect-timeout",
      String(CURL_CONNECT_TIMEOUT_SECONDS),
      "--max-time",
      String(CURL_REQUEST_TIMEOUT_SECONDS),
      CURL_IP_VERSION,
      "-A",
      USER_AGENT,
      url,
    ],
    `Fetch ${url}`,
  );
}

function parsePageItems(html) {
  const seen = new Set();
  const items = [];

  // Find all wallpaper cards: <a title="..." href="/slug"> ... /media/{ID}/...
  // href value may or may not be quoted
  const cardPattern = /<a\s+title="([^"]+)"\s+href=("?)(\/[^>\s]+)\2[^>]*>[\s\S]*?\/media\/(\d+)\//gi;
  const matches = [...html.matchAll(cardPattern)];

  for (const [, title, , href, mediaId] of matches) {
    const detailUrl = normalizeDetailUrl(href);
    const slug = slugFromUrl(detailUrl);
    if (seen.has(detailUrl)) continue;
    seen.add(detailUrl);
    items.push({
      detailUrl,
      slug,
      wallpaperId: mediaId,
      pageTitle: title || slug || "MotionBGs Wallpaper",
    });
  }

  return items;
}

function extensionFromHeaders(headersText, url) {
  const disposition = headersText.match(/^content-disposition:\s*(.+)$/im)?.[1] || "";
  const filename = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i)?.[1];
  if (filename) {
    const ext = path.extname(decodeURIComponent(filename.trim()));
    if (ext) return ext;
  }

  const urlExt = path.extname(new URL(url).pathname);
  if (urlExt) return urlExt;

  const type = headersText.match(/^content-type:\s*(.+)$/im)?.[1] || "";
  if (type.includes("video/mp4") || type.includes("octet-stream")) return ".mp4";
  return ".bin";
}

function contentLengthFromHeaders(headersText) {
  const value = headersText.match(/^content-length:\s*(\d+)/im)?.[1];
  return value ? Number(value) : null;
}

function sanitizeFilename(value) {
  const cleaned = value
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[. ]+$/g, "");
  return cleaned.slice(0, 180) || "wallpaper";
}

async function getDownloadInfo(url) {
  const headersText = await runCurlCapture(
    [
      "-fsSIL",
      "--retry",
      String(FETCH_RETRIES),
      "--retry-delay",
      String(FETCH_RETRY_DELAY_MS / 1000),
      "--retry-all-errors",
      "--connect-timeout",
      String(CURL_CONNECT_TIMEOUT_SECONDS),
      "--max-time",
      String(CURL_REQUEST_TIMEOUT_SECONDS),
      CURL_IP_VERSION,
      "-A",
      USER_AGENT,
      url,
    ],
    `Fetch download info for ${url}`,
  );

  return {
    ext: extensionFromHeaders(headersText, url),
    expectedSize: contentLengthFromHeaders(headersText),
  };
}

function runCurl(args) {
  return new Promise((resolve, reject) => {
    const child = spawn("curl", args, { stdio: ["ignore", "inherit", "inherit"] });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`curl exited with code ${code}`));
    });
  });
}

async function downloadFile(url, title, referer) {
  const { ext, expectedSize } = await getDownloadInfo(url);
  const base = sanitizeFilename(title);
  const filePath = path.join(OUT_DIR, `${base}${ext}`);
  const partPath = `${filePath}.part`;

  try {
    const existing = await stat(filePath);
    if (expectedSize && existing.size === expectedSize) {
      return { filePath, size: existing.size, expectedSize, skipped: true };
    }
  } catch {
    // File does not exist yet; download it below.
  }

  await unlink(partPath).catch(() => {});
  await runCurl([
    "-fL",
    "--retry",
    "6",
    "--retry-delay",
    "2",
    "--retry-all-errors",
    "--connect-timeout",
    String(CURL_CONNECT_TIMEOUT_SECONDS),
    "--max-time",
    String(CURL_DOWNLOAD_TIMEOUT_SECONDS),
    CURL_IP_VERSION,
    "-C",
    "-",
    "-A",
    USER_AGENT,
    "-e",
    referer,
    "-o",
    partPath,
    url,
  ]);

  const size = (await stat(partPath)).size;
  if (expectedSize && size !== expectedSize) {
    throw new Error(`download size mismatch: expected ${expectedSize}, got ${size}`);
  }
  if (size <= 0) {
    throw new Error("download produced an empty file");
  }
  await rename(partPath, filePath);
  return { filePath, size, expectedSize, skipped: false };
}

await mkdir(OUT_DIR, { recursive: true });
await mkdir(CONFIG_DIR, { recursive: true });

const pageUrl = getPageUrl(options.page);
console.log(`Selected page: ${options.page}`);
console.log(`Listing URL: ${pageUrl}`);
console.log(`Quality: ${options.quality.toUpperCase()}`);
if (options.dryRun) console.log("Dry run: downloads will be skipped.");

const home = await fetchText(pageUrl);
const items = parsePageItems(home);
if (!items.length) throw new Error(`No wallpaper items found on page ${options.page}.`);

console.log(`Found ${items.length} wallpapers on this page.`);

const urlRecords = await loadUrlRecords();
const results = [];
let actionableCount = 0;

for (let i = 0; i < items.length; i += 1) {
  const item = items[i];
  const detailUrl = item.detailUrl;

  if (urlRecords.has(detailUrl)) {
    results.push({
      ...item,
      page: options.page,
      pageUrl,
      record: urlRecords.get(detailUrl),
      status: "skipped-detail-url",
    });
    console.log(`[${i + 1}/${items.length}] skipped, already recorded: ${item.pageTitle}`);
    continue;
  }

  if (!item.wallpaperId) {
    results.push({ ...item, page: options.page, pageUrl, status: "missing-wallpaper-id" });
    console.log(`[${i + 1}/${items.length}] skipped, missing wallpaper ID: ${item.pageTitle}`);
    continue;
  }

  const downloadUrl = `${ROOT}dl/${options.quality}/${item.wallpaperId}`;
  const name = item.slug || item.pageTitle || `wallpaper-${item.wallpaperId}`;

  if (options.dryRun) {
    results.push({ ...item, page: options.page, pageUrl, downloadUrl, name, status: "dry-run" });
    console.log(`[${i + 1}/${items.length}] would download: ${name} (${options.quality.toUpperCase()})`);
    actionableCount += 1;
    if (actionableCount >= options.limit) {
      console.log(`Reached limit of ${options.limit}, stopping.`);
      break;
    }
    continue;
  }

  console.log(`[${i + 1}/${items.length}] downloading: ${name} (${options.quality.toUpperCase()})`);
  let download;
  try {
    download = await downloadFile(downloadUrl, name, ROOT);
    const status = download.skipped ? "skipped-existing" : "downloaded";
    urlRecords.set(detailUrl, {
      detailUrl,
      page: options.page,
      pageUrl,
      name,
      slug: item.slug,
      wallpaperId: item.wallpaperId,
      filePath: download.filePath,
      quality: options.quality,
      recordedAt: new Date().toISOString(),
      status,
    });
    results.push({
      ...item,
      page: options.page,
      pageUrl,
      downloadUrl,
      name,
      ...download,
      status,
    });
  } catch (err) {
    results.push({ ...item, page: options.page, pageUrl, downloadUrl, name, status: "download-failed", error: err.message });
    console.log(`[${i + 1}/${items.length}] failed: ${name} - ${err.message}`);
  }

  actionableCount += 1;
  if (actionableCount >= options.limit) {
    console.log(`Reached limit of ${options.limit}, stopping.`);
    break;
  }
}

await writeFile(MANIFEST, JSON.stringify(results, null, 2), "utf8");
await saveUrlRecords(urlRecords);
console.log(`Done. Manifest: ${MANIFEST}`);
console.log(`URL record: ${URL_RECORD}`);
