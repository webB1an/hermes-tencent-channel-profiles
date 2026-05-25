/**
 * WallpaperWaifu live wallpaper downloader
 *
 * Default behavior:
 *   Download the newest listing page from https://wallpaperwaifu.com/.
 *   Videos are saved to project-folder\downloads.
 *   History is saved separately from the MoeWalls script.
 *
 * Common commands:
 *   node .\scripts\download-wallpaperwaifu-first-page.mjs
 *   node .\scripts\download-wallpaperwaifu-first-page.mjs --page 2
 *   node .\scripts\download-wallpaperwaifu-first-page.mjs --out "D:\Wallpapers\WallpaperWaifu"
 *   node .\scripts\download-wallpaperwaifu-first-page.mjs --dry-run
 */

import { mkdir, readFile, writeFile, stat } from "node:fs/promises";
import { Buffer } from "node:buffer";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = "https://wallpaperwaifu.com/";
const DOWNLOAD_ROOT = "https://go.wallpaperwaifu.com/download.php?video=";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36";
const FETCH_RETRIES = 5;
const FETCH_RETRY_DELAY_MS = 3000;
const CURL_TEXT_TIMEOUT_MS = 120000;
const CURL_DOWNLOAD_TIMEOUT_MS = 1800000; // 30 minutes
const SCRIPT_PATH = fileURLToPath(import.meta.url);
const SCRIPT_DIR = path.dirname(SCRIPT_PATH);
const PROJECT_DIR = path.dirname(SCRIPT_DIR);

function usage() {
  return `WallpaperWaifu live wallpaper downloader

Usage:
  node "${SCRIPT_PATH}"
  node "${SCRIPT_PATH}" --page 2
  node "${SCRIPT_PATH}" -p 3
  node "${SCRIPT_PATH}" --out "D:\\Wallpapers\\WallpaperWaifu"
  node "${SCRIPT_PATH}" --page 3 --out "D:\\Wallpapers\\WallpaperWaifu"
  node "${SCRIPT_PATH}" --dry-run
  node "${SCRIPT_PATH}" --help

Options:
  -p, --page <number>  Download wallpapers from a specific listing page. Default: 1.
  -o, --out <path>     Save wallpaper videos to this folder. Default: project-folder\\downloads.
  --dry-run            Parse pages and print what would be downloaded without downloading files.
  -h, --help           Show this tutorial.

Workflow:
  1. Fetch the selected WallpaperWaifu listing page.
  2. Parse wallpaper detail URLs from the wall-grid cards.
  3. Skip any detail URL already recorded in project-folder/config/downloaded-wallpaperwaifu-detail-urls.json.
  4. Fetch each new detail page.
  5. Read the "Download Wallpaper" button token from id="wf-download" data-url.
  6. Download from https://go.wallpaperwaifu.com/download.php?video=<token>.
  7. Save the latest run to project-folder/config/manifest-wallpaperwaifu.json.
`;
}

function parseArgs(argv) {
  const options = { page: 1, outDir: path.join(PROJECT_DIR, "downloads"), dryRun: false, limit: 999999 };
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
const MANIFEST = path.join(CONFIG_DIR, "manifest-wallpaperwaifu.json");
const URL_RECORD = path.join(CONFIG_DIR, "downloaded-wallpaperwaifu-detail-urls.json");

function getPageUrl(page) {
  return page === 1 ? ROOT : `${ROOT}page/${page}/`;
}

function decodeEntities(value) {
  return value
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function stripTags(value) {
  return decodeEntities(value.replace(/<[^>]*>/g, "")).trim().replace(/\s+/g, " ");
}

function sanitizeFilename(value) {
  const cleaned = value
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[. ]+$/g, "");
  return cleaned.slice(0, 180) || "wallpaper";
}

function normalizeDetailUrl(value) {
  const parsed = new URL(value, ROOT);
  parsed.hash = "";
  parsed.search = "";
  return parsed.href;
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
        name: item.name || item.pageTitle || null,
        filePath: item.filePath || null,
        status: item.status || null,
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
      reject(new Error(`${label} timed out after ${CURL_TEXT_TIMEOUT_MS / 1000}s`));
    }, CURL_TEXT_TIMEOUT_MS);

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
      "20",
      "--max-time",
      "90",
      "-A",
      USER_AGENT,
      url,
    ],
    `Fetch ${url}`,
  );
}

function parsePageItems(html) {
  const matches = [...html.matchAll(/<article\b[^>]*class="[^"]*\bwall-grid-item\b[^"]*"[^>]*>([\s\S]*?)<\/article>/gi)];
  const seen = new Set();
  const items = [];

  for (const [, articleHtml] of matches) {
    const linkMatch = articleHtml.match(/<a\b[^>]*href="([^"]+)"[^>]*title="([^"]*Live Wallpaper[^"]*)"[^>]*>/i);
    const captionMatch = articleHtml.match(/<div\b[^>]*class="[^"]*\bwall-caption\b[^"]*"[^>]*>([\s\S]*?)<\/div>/i);
    const detailUrl = linkMatch ? normalizeDetailUrl(decodeEntities(linkMatch[1])) : null;
    if (!detailUrl || seen.has(detailUrl)) continue;

    seen.add(detailUrl);
    items.push({
      detailUrl,
      pageTitle: captionMatch ? stripTags(captionMatch[1]) : stripTags(linkMatch[2]),
    });
  }

  return items;
}

function parseDetail(html) {
  const h1 = html.match(/<h1[^>]*class="[^"]*\bentry-title\b[^"]*"[^>]*>([\s\S]*?)<\/h1>/i);
  const titleMatch =
    h1 || html.match(/<meta property="og:title" content="([^"]+)"/i) || html.match(/<title>([\s\S]*?)<\/title>/i);
  const fallbackTitle = titleMatch ? stripTags(titleMatch[1]).replace(/\s+-\s+WallpaperWaifu$/i, "") : "wallpaper";

  const downloadMatch = html.match(/id="wf-download"[\s\S]*?data-url\s*=\s*"([^"]+)"/i);
  const downloadToken = downloadMatch ? decodeEntities(downloadMatch[1]) : null;

  const fileSizeMatch = html.match(/File Size:[\s\S]*?<\/span>\s*([^<]+)</i);
  const fileSize = fileSizeMatch ? stripTags(fileSizeMatch[1]) : null;

  return { fallbackTitle, downloadToken, fileSize };
}

function extensionFromHeaders(headersText) {
  const disposition = headersText.match(/^content-disposition:\s*(.+)$/im)?.[1] || "";
  const filename = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i)?.[1];
  if (filename) {
    const ext = path.extname(decodeURIComponent(filename.trim()));
    if (ext) return ext;
  }

  const type = headersText.match(/^content-type:\s*(.+)$/im)?.[1] || "";
  if (type.includes("video/mp4") || type.includes("octet-stream")) return ".mp4";
  return ".bin";
}

function contentLengthFromHeaders(headersText) {
  const value = headersText.match(/^content-length:\s*(\d+)/im)?.[1];
  return value ? Number(value) : null;
}

async function getDownloadInfo(token, referer) {
  const url = `${DOWNLOAD_ROOT}${token}`;
  const headersText = await runCurlCapture(
    [
      "-fsSIL",
      "--retry",
      String(FETCH_RETRIES),
      "--retry-delay",
      String(FETCH_RETRY_DELAY_MS / 1000),
      "--retry-all-errors",
      "--connect-timeout",
      "20",
      "--max-time",
      "90",
      "-A",
      USER_AGENT,
      "-e",
      referer,
      url,
    ],
    `Fetch download info ${referer}`,
  );

  return {
    ext: extensionFromHeaders(headersText),
    expectedSize: contentLengthFromHeaders(headersText),
  };
}

function runCurl(args) {
  return new Promise((resolve, reject) => {
    const child = spawn("curl", args, { stdio: ["ignore", "inherit", "inherit"] });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`curl timed out after ${CURL_DOWNLOAD_TIMEOUT_MS / 1000}s`));
    }, CURL_DOWNLOAD_TIMEOUT_MS);
    child.on("error", (err) => { clearTimeout(timer); reject(err); });
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(`curl exited with code ${code}`));
    });
  });
}

async function downloadFile(token, title, referer) {
  const url = `${DOWNLOAD_ROOT}${token}`;
  const { ext, expectedSize } = await getDownloadInfo(token, referer);
  const base = sanitizeFilename(title);
  const filePath = path.join(OUT_DIR, `${base}${ext}`);

  try {
    const existing = await stat(filePath);
    if (!expectedSize || existing.size === expectedSize) {
      return { filePath, size: existing.size, expectedSize, skipped: true };
    }
  } catch {
    // File does not exist yet; download it below.
  }

  await runCurl([
    "-fL",
    "--retry",
    "6",
    "--retry-delay",
    "2",
    "--retry-all-errors",
    "-C",
    "-",
    "-A",
    USER_AGENT,
    "-e",
    referer,
    "-o",
    filePath,
    url,
  ]);

  const size = (await stat(filePath)).size;
  return { filePath, size, expectedSize, skipped: false };
}

await mkdir(OUT_DIR, { recursive: true });
await mkdir(CONFIG_DIR, { recursive: true });

const pageUrl = getPageUrl(options.page);
console.log(`Selected page: ${options.page}`);
console.log(`Listing URL: ${pageUrl}`);
if (options.dryRun) console.log("Dry run: downloads will be skipped.");

const home = await fetchText(pageUrl);
const items = parsePageItems(home);
if (!items.length) throw new Error(`No wallpaper items found on page ${options.page}.`);

const urlRecords = await loadUrlRecords();
const results = [];

for (let i = 0; i < items.length; i += 1) {
  const item = items[i];
  const detailUrl = normalizeDetailUrl(item.detailUrl);
  if (urlRecords.has(detailUrl)) {
    results.push({
      ...item,
      detailUrl,
      page: options.page,
      pageUrl,
      record: urlRecords.get(detailUrl),
      status: "skipped-detail-url",
    });
    console.log(`[${i + 1}/${items.length}] skipped, detail URL already recorded: ${item.pageTitle}`);
    continue;
  }

  const detailHtml = await fetchText(detailUrl);
  const detail = parseDetail(detailHtml);
  const name = detail.fallbackTitle || item.pageTitle;

  if (!detail.downloadToken) {
    results.push({ ...item, detailUrl, page: options.page, pageUrl, ...detail, name, status: "missing-download-token" });
    console.log(`[${i + 1}/${items.length}] skipped, missing download token: ${name}`);
    continue;
  }

  if (options.dryRun) {
    results.push({ ...item, detailUrl, page: options.page, pageUrl, ...detail, name, status: "dry-run" });
    console.log(`[${i + 1}/${items.length}] would download: ${name}${detail.fileSize ? ` (${detail.fileSize})` : ""}`);
    if (i + 1 >= options.limit) { console.log(`Reached limit of ${options.limit}, stopping dry-run.`); break; }
    continue;
  }

  console.log(`[${i + 1}/${items.length}] downloading: ${name}${detail.fileSize ? ` (${detail.fileSize})` : ""}`);
  const download = await downloadFile(detail.downloadToken, name, detailUrl);
  const status = download.skipped ? "skipped-existing" : "downloaded";
  urlRecords.set(detailUrl, {
    detailUrl,
    page: options.page,
    pageUrl,
    name,
    filePath: download.filePath,
    fileSize: detail.fileSize || null,
    recordedAt: new Date().toISOString(),
    status,
  });
  results.push({
    ...item,
    detailUrl,
    page: options.page,
    pageUrl,
    ...detail,
    ...download,
    status,
  });
  // Stop after first successful download (used by Hermes bot for one-at-a-time posting)
  if (i + 1 >= options.limit) {
    console.log(`Reached limit of ${options.limit}, stopping.`);
    break;
  }
}

await writeFile(MANIFEST, JSON.stringify(results, null, 2), "utf8");
await saveUrlRecords(urlRecords);
console.log(`Done. Manifest: ${MANIFEST}`);
console.log(`URL record: ${URL_RECORD}`);
