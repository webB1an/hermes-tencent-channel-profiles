# 动态壁纸下载工具

批量下载动态壁纸的 Node.js 脚本集合，当前支持：

- MoeWalls
- WallpaperWaifu
- DesktopHut

脚本默认下载对应网站的最新一页，支持指定页码、自定义壁纸保存目录、按详情页 URL 去重，并使用 `curl.exe` 下载以支持重试和断点续传。

## 目录结构

```text
.
├─ downloads\   下载的壁纸视频，本目录不提交到 Git
├─ config\      下载历史记录和最近一次运行结果，本目录只保留占位文件
├─ scripts\     脚本文件
└─ README.md    使用文档
```

## 环境要求

需要本机可以运行：

```powershell
node --version
curl.exe --version
```

## 常用命令

### MoeWalls

下载最新一页：

```powershell
node .\scripts\download-moewalls-first-page.mjs
```

下载指定页，例如第 2 页：

```powershell
node .\scripts\download-moewalls-first-page.mjs --page 2
```

自定义壁纸保存目录：

```powershell
node .\scripts\download-moewalls-first-page.mjs --out "D:\Wallpapers\MoeWalls"
```

### WallpaperWaifu

下载最新一页：

```powershell
node .\scripts\download-wallpaperwaifu-first-page.mjs
```

下载指定页：

```powershell
node .\scripts\download-wallpaperwaifu-first-page.mjs --page 2
```

只解析页面、不下载文件：

```powershell
node .\scripts\download-wallpaperwaifu-first-page.mjs --dry-run
```

### DesktopHut

下载最新一页：

```powershell
node .\scripts\download-desktophut-first-page.mjs
```

下载指定页：

```powershell
node .\scripts\download-desktophut-first-page.mjs --page 2
```

只解析页面、不下载文件：

```powershell
node .\scripts\download-desktophut-first-page.mjs --dry-run
```

## 通用参数

```text
--page / -p   指定下载第几页；不传默认第 1 页
--out / -o    指定壁纸视频保存目录；不传默认保存到 downloads
--dry-run     WallpaperWaifu 和 DesktopHut 脚本可用；只解析页面，不下载文件
--help / -h   查看脚本帮助
```

## 去重规则

脚本按“壁纸详情页 URL”去重，不按文件名去重。

运行时会在 `config` 目录生成长期去重记录：

```text
config\downloaded-detail-urls.json
config\downloaded-wallpaperwaifu-detail-urls.json
config\downloaded-desktophut-detail-urls.json
```

这些 JSON 文件是本地运行状态，默认不会提交到 Git。即使使用 `--out` 修改壁纸保存目录，去重记录仍然固定使用项目根目录下的 `config`。

## 大文件和慢下载

动态壁纸文件较大时，下载可能比较慢。脚本使用 `curl.exe` 下载，并开启：

```text
--retry 6
--retry-delay 2
--retry-all-errors
-C -
```

含义：

- 网络断开会自动重试。
- 支持断点续传。
- 下载失败不会写入历史去重记录。
- 下次重新运行脚本时，会继续尝试下载未完成的壁纸。

## 输出文件

下载的壁纸视频默认保存到：

```text
downloads
```

最近一次运行结果保存在：

```text
config\manifest.json
config\manifest-wallpaperwaifu.json
config\manifest-desktophut.json
```

这些运行结果文件同样不会提交到 Git。
