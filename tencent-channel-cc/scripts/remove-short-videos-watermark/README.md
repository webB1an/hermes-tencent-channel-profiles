# Short video remove watermark downloader

本目录目前支持抖音、小红书、快手分享链接解析下载。

`douyin.py` 复刻并增强了这个脚本的核心流程：

https://github.com/VideoData/DY-Data/blob/main/%E6%8A%96%E9%9F%B3%E5%8E%BB%E6%B0%B4%E5%8D%B0/douyin.py

`xiaohongshu.py` 参考了这个仓库的小红书解析方式：

https://github.com/jiuhunwl/short_videos/tree/main/api/xiaohongshu

`kuaishou.py` 参考了这个仓库的快手解析方式：

https://github.com/jiuhunwl/short_videos/tree/main/api/kuaishou

## 安装依赖

```powershell
python -m pip install -r requirements.txt
```

## 抖音使用

直接粘贴整段分享文案即可：

```powershell
python douyin.py "9.79 11/09 Ago:/ :1pm S@l.pq 春天会抵达 所有未完成的约定.# 转场# 歌曲春  https://v.douyin.com/AySVGKi_838/ 复制此链接，打开Dou音搜索，直接观看视频！"
```

下载文件默认保存在 `downloads` 文件夹。

## 小红书使用

同样支持直接粘贴整段分享文案：

```powershell
python xiaohongshu.py "59 【做一回西湖画中人  - 梁婧娴 | 小红书 - 你的生活兴趣社区】 😆 Rgqny3fxY91zwZU 😆 https://www.xiaohongshu.com/discovery/item/69f2b5d200000000360335a2?source=webshare&xhsshare=pc_web&xsec_token=AB-q1Nta2FgwXx-B01sTvvX1GNd3hpM0L0ej21kyOXpuI=&xsec_source=pc_share"
```

视频会保存为 `.mp4`；图文笔记会按顺序保存为 `.jpg`。

## 快手使用

直接粘贴整段快手分享文案：

```powershell
python kuaishou.py "https://v.kuaishou.com/JZwy3g3t 我想说 遇见你真好 希望四季循环 我们一直是我们 @ 该作品在快手被播放过401.5万次，点击链接，打开【快手】直接观看！"
```

视频会保存为 `.mp4`；图集或单图会保存为 `.jpg`。

## 解析方式

抖音脚本会优先使用 `iesdouyin.com/share/video/{id}` 分享页里的 `window._ROUTER_DATA`，这条路径通常不需要登录。

小红书脚本会读取公开页面里的 `window.__INITIAL_STATE__`，视频优先下载 `h265` / `h264` 的 `masterUrl`，图片会尝试转换为更干净的 CDN 地址。

快手脚本会读取公开页面里的 `window.INIT_STATE`，必要时回退 `window.__APOLLO_STATE__`，从页面状态中提取视频或图片地址。

如果分享页解析失败，脚本才会回退到 Douyin Web API 和 `yt-dlp`。如果看到 `Fresh cookies are needed` 或 `encrypt_data_miss`，说明当前链接/接口要求浏览器 cookie/签名。可以尝试：

```powershell
python douyin.py "https://v.douyin.com/AySVGKi_838/" --backend yt-dlp --cookies-from-browser chrome
```

或者先用浏览器插件导出 Netscape 格式的 `cookies.txt`，再运行：

```powershell
python douyin.py "https://v.douyin.com/AySVGKi_838/" --backend yt-dlp --cookies ".\cookies.txt"
```

仅下载你有权保存和使用的视频内容。
