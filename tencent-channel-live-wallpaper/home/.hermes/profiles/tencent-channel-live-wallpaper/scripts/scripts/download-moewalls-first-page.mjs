/**
 * MoeWalls 壁纸下载脚本
 *
 * 默认行为：
 *   下载 MoeWalls 最新一页，也就是 https://moewalls.com/ 首页里的壁纸。
 *   默认保存到“项目根目录\downloads”，历史记录保存到“项目根目录\config”。
 *   项目根目录是 scripts 文件夹的上一级。
 *
 * 常用命令：
 *   1. 下载最新一页，保存到默认 downloads 目录：
 *   node .\scripts\download-moewalls-first-page.mjs
 *
 *   2. 下载指定页，例如第 2 页：
 *   node .\scripts\download-moewalls-first-page.mjs --page 2
 *
 *   3. 下载最新一页，并自定义保存目录：
 *   node .\scripts\download-moewalls-first-page.mjs --out "D:\Wallpapers\MoeWalls"
 *
 *   4. 下载指定页，并自定义保存目录：
 *   node .\scripts\download-moewalls-first-page.mjs --page 3 --out "D:\Wallpapers\MoeWalls"
 *
 *   5. 查看帮助：
 *   node .\scripts\download-moewalls-first-page.mjs --help
 *
 * 参数说明：
 *   --page / -p   指定下载第几页；不传则默认第 1 页。
 *   --out / -o    指定壁纸保存目录；不传则保存到项目根目录下的 downloads。
 *   --help / -h   查看脚本使用教程。
 *
 * 去重规则：
 *   按“壁纸详情页 URL”去重，不按文件名去重。
 *   已下载过的详情页 URL 会记录在 config\downloaded-detail-urls.json。
 *   再次运行时，如果详情页 URL 已存在，就直接跳过，不会重复下载。
 *   即使使用 --out 修改壁纸保存目录，去重记录仍然固定读取 config 目录。
 *
 * 工作流程：
 *   1. 抓取指定页的壁纸列表。
 *   2. 逐个读取壁纸详情页 URL。
 *   3. 先用 config\downloaded-detail-urls.json 按详情页 URL 去重。
 *   4. 如果详情页有 steamcommunity.com 来源，就进入 Steam 页面获取真实名称。
 *   5. 如果没有 Steam 来源，就使用 MoeWalls 详情页标题作为名称。
 *   6. 下载壁纸视频，并用解析出的名称命名文件。
 *   7. 下载成功后把详情页 URL 写入本地记录，避免下次重复下载。
 *
 * 输出文件：
 *   <保存目录>\*.mp4                         下载的壁纸视频。
 *   config\manifest.json                     最近一次运行结果。
 *   config\downloaded-detail-urls.json       长期 URL 去重记录。
 */

import { mkdir, readFile, writeFile, stat } from "node:fs/promises";
import { Buffer } from "node:buffer";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = "https://moewalls.com/";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36";
const FETCH_RETRIES = 5;
const FETCH_RETRY_DELAY_MS = 3000;
const CURL_TEXT_TIMEOUT_MS = 120000;
const SCRIPT_PATH = fileURLToPath(import.meta.url);
const SCRIPT_DIR = path.dirname(SCRIPT_PATH);
const PROJECT_DIR = path.dirname(SCRIPT_DIR);

function usage() {
  return `MoeWalls wallpaper downloader

Usage:
  node "${SCRIPT_PATH}"
  node "${SCRIPT_PATH}" --page 2
  node "${SCRIPT_PATH}" -p 3
  node "${SCRIPT_PATH}" --out "D:\\Wallpapers\\MoeWalls"
  node "${SCRIPT_PATH}" --page 3 --out "D:\\Wallpapers\\MoeWalls"
  node "${SCRIPT_PATH}" --help

Options:
  -p, --page <number>  Download wallpapers from a specific listing page. Default: 1.
  -o, --out <path>     Save wallpaper videos to this folder. Default: project-folder\\downloads.
  -h, --help           Show this tutorial.

Workflow:
  1. Fetch the selected MoeWalls listing page.
  2. Parse wallpaper detail URLs from the page.
  3. Skip any detail URL already recorded in project-folder/config/downloaded-detail-urls.json.
  4. Fetch each new detail page.
  5. Prefer the Steam Workshop title when a steamcommunity.com source exists.
  6. Download the wallpaper video with that resolved title as the file name.
  7. Save the latest run to project-folder/config/manifest.json.
`;
}

function parseArgs(argv) {
  const options = { page: 1, outDir: path.join(PROJECT_DIR, "downloads") };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "-h" || arg === "--help") {
      options.help = true;
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
const MANIFEST = path.join(CONFIG_DIR, "manifest.json");
const URL_RECORD = path.join(CONFIG_DIR, "downloaded-detail-urls.json");

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
  const parsed = new URL(value);
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
        steamUrl: item.steamUrl || null,
        nameSource: item.nameSource || null,
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function describeFetchError(error) {
  const code = error?.cause?.code || error?.code;
  if (code === "UND_ERR_CONNECT_TIMEOUT") return "connection timed out";
  return error?.message || String(error);
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

async function fetchWithRetry(url, options, label) {
  let lastError;
  for (let attempt = 1; attempt <= FETCH_RETRIES; attempt += 1) {
    try {
      return await fetch(url, { ...options, signal: AbortSignal.timeout(90000) });
    } catch (error) {
      lastError = error;
      const reason = describeFetchError(error);
      if (attempt === FETCH_RETRIES) break;
      console.warn(`${label} failed (${reason}); retrying ${attempt}/${FETCH_RETRIES - 1}...`);
      await sleep(FETCH_RETRY_DELAY_MS * attempt);
    }
  }
  throw new Error(`${label} failed after ${FETCH_RETRIES} attempts: ${describeFetchError(lastError)}`);
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
  const sectionStart = html.indexOf("<span>Latest Videos</span>");
  const scoped = sectionStart >= 0 ? html.slice(sectionStart) : html;
  const matches = [
    ...scoped.matchAll(
      /<h3 class="[^"]*\bentry-title\b[^"]*">\s*<a href="([^"]+)"[^>]*>([\s\S]*?)<\/a>\s*<\/h3>/gi,
    ),
  ];
  const seen = new Set();
  const items = [];
  for (const match of matches) {
    const url = decodeEntities(match[1]);
    if (seen.has(url)) continue;
    seen.add(url);
    items.push({ detailUrl: url, pageTitle: stripTags(match[2]) });
    if (items.length === 15) break;
  }
  return items;
}

function parseDetail(html) {
  const h1 = html.match(/<h1[^>]*class="[^"]*\bentry-title\b[^"]*"[^>]*>([\s\S]*?)<\/h1>/i);
  const titleMatch =
    h1 || html.match(/<meta property="og:title" content="([^"]+)"/i) || html.match(/<title>([\s\S]*?)<\/title>/i);
  const fallbackTitle = titleMatch ? stripTags(titleMatch[1]).replace(/\s+-\s+MoeWalls$/i, "") : "wallpaper";

  const downloadMatch = html.match(/id="moe-download"[\s\S]*?data-url="([^"]+)"/i);
  const downloadToken = downloadMatch ? decodeEntities(downloadMatch[1]) : null;

  const jumpMatch = html.match(/<form[^>]+action="\/jump\.php\?url=([^"]+)"[^>]*>[\s\S]*?steamcommunity\.com/i);
  const steamUrl = jumpMatch ? Buffer.from(decodeEntities(jumpMatch[1]), "base64").toString("utf8") : null;

  return { fallbackTitle, downloadToken, steamUrl };
}

function parseSteamTitle(html) {
  const workshop = html.match(/<div class="workshopItemTitle">([\s\S]*?)<\/div>/i);
  if (workshop) return stripTags(workshop[1]);
  const title = html.match(/<title>Steam Workshop::([\s\S]*?)<\/title>/i);
  return title ? stripTags(title[1]) : null;
}

function extensionFromHeaders(response) {
  const disposition = response.headers.get("content-disposition") || "";
  const filename = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i)?.[1];
  if (filename) {
    const ext = path.extname(decodeURIComponent(filename.trim()));
    if (ext) return ext;
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("video/mp4") || type.includes("octet-stream")) return ".mp4";
  return ".bin";
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

async function getDownloadInfo(token) {
  const url = `https://go.moewalls.com/download.php?video=${token}`;
  const response = await fetchWithRetry(
    url,
    {
      method: "HEAD",
      headers: {
        "user-agent": USER_AGENT,
        referer: ROOT,
        accept: "*/*",
      },
    },
    "Fetch download info",
  );
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} for download`);
  return {
    ext: extensionFromHeaders(response),
    expectedSize: Number(response.headers.get("content-length")) || null,
  };
}

async function downloadFile(token, title, index) {
  const url = `https://go.moewalls.com/download.php?video=${token}`;
  const { ext, expectedSize } = await getDownloadInfo(token);
  const base = sanitizeFilename(title);
  const filePath = path.join(OUT_DIR, `${base}${ext}`);
  try {
    const existing = await stat(filePath);
    if (!expectedSize || existing.size === expectedSize) {
      return { filePath, size: existing.size, skipped: true };
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
    ROOT,
    "-o",
    filePath,
    url,
  ]);
  const size = (await stat(filePath)).size;
  return { filePath, size, skipped: false };
}

await mkdir(OUT_DIR, { recursive: true });
await mkdir(CONFIG_DIR, { recursive: true });

const pageUrl = getPageUrl(options.page);
console.log(`Selected page: ${options.page}`);
console.log(`Listing URL: ${pageUrl}`);

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

  const detailHtml = await fetchText(item.detailUrl);
  const detail = parseDetail(detailHtml);
  let name = detail.fallbackTitle || item.pageTitle;
  let nameSource = "moewalls";

  if (detail.steamUrl?.startsWith("https://steamcommunity.com/")) {
    try {
      const steamHtml = await fetchText(detail.steamUrl);
      const steamTitle = parseSteamTitle(steamHtml);
      if (steamTitle) {
        name = steamTitle;
        nameSource = "steam";
      }
    } catch (error) {
      detail.steamError = error.message;
    }
  }

  if (!detail.downloadToken) {
    results.push({ ...item, detailUrl, page: options.page, pageUrl, ...detail, name, nameSource, status: "missing-download-token" });
    console.log(`[${i + 1}/${items.length}] skipped, missing download token: ${name}`);
    continue;
  }

  console.log(`[${i + 1}/${items.length}] downloading: ${name}`);
  const download = await downloadFile(detail.downloadToken, name, i + 1);
  const status = download.skipped ? "skipped-existing" : "downloaded";
  urlRecords.set(detailUrl, {
    detailUrl,
    page: options.page,
    pageUrl,
    name,
    filePath: download.filePath,
    steamUrl: detail.steamUrl || null,
    nameSource,
    recordedAt: new Date().toISOString(),
    status,
  });
  results.push({
    ...item,
    detailUrl,
    page: options.page,
    pageUrl,
    ...detail,
    name,
    nameSource,
    ...download,
    status,
  });
}

await writeFile(MANIFEST, JSON.stringify(results, null, 2), "utf8");
await saveUrlRecords(urlRecords);
console.log(`Done. Manifest: ${MANIFEST}`);
console.log(`URL record: ${URL_RECORD}`);
