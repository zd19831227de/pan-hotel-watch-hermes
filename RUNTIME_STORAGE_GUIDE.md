# 运行时文件与磁盘占用说明

本文档说明 Pan Hotel Watch 运行过程中生成的截图、浏览器档案、缓存和本地模型文件，供后续清理、限额和运行维护使用。

## 1. 当前占用概况

以下数据为 2026-08-03 的本机盘点结果，后续会随浏览器读取和定时任务变化。

| 目录 | 文件数量 | 约占用 | 主要内容 | 是否业务核心数据 |
|---|---:|---:|---|---|
| `output/playwright` | 548 | 1.34GB | Playwright 页面截图 | 否 |
| `.runtime/browser-state` | 21,271 | 8.48GB | Chromium 持久化浏览器档案 | 部分是登录态 |

实际存在的浏览器档案目录是：

```text
D:\work\pan-hotel-watch\.runtime\browser-state
```

`D:\work\pan-hotel-watch.runtime\browser-state` 不存在。

## 2. `output/playwright`

### 2.1 文件性质

该目录全部是 `.png` 图片，主要文件名类似：

```text
ctrip-ebk-{account}-{timestamp}.png
```

它们由浏览器读取流程保存，用于：

- 核对是否跳转到登录页；
- 发生页面结构变化时人工复核；
- 读取异常时保留现场证据；
- 调价流程中保存操作前、操作后的页面证据。

### 2.2 不承担的功能

截图不是当前价格解析的主要数据源，也没有发现“截图后 OCR 再读取价格”的生产逻辑。

当前读取链路主要使用：

1. 页面 DOM 可见文本；
2. 页面元素和结构化房价节点；
3. 解析器从页面文本中提取日期、房型和价格；
4. 同时额外保存截图作为证据。

相关实现：

- `backend/app/domain/browser/ctrip_ebooking.py` 的 `_capture_screenshot()`；
- `backend/app/domain/browser/ctrip_ebooking_read_flows.py` 的 `_safe_visible_text()`；
- `backend/app/domain/browser/ctrip_public_price_reader.py` 的页面文本和结构化房价读取。

### 2.3 清理建议

旧截图可以删除，不会影响：

- 携程、同程、飞猪的登录态；
- 浏览器档案；
- 数据库中的酒店、价格和趋势数据；
- Hermes 或 LLM 配置。

删除前应确认这些截图不再需要作为审计证据。建议后续增加：

- 默认保留最近 7 至 30 天；
- 每个账号限制最大截图数量；
- 成功读取只保留最近一张，失败、登录异常和页面结构异常保留更多；
- 通过 `cleanup-runtime.bat` 执行预览、清理和结果汇总；
- 清理过程不删除当前正在写入的文件。

## 3. `.runtime/browser-state`

### 3.1 文件性质

这是 Playwright 使用的 Chromium 持久化 `user-data-dir`，不是代码目录，也不是测试历史。它用于保存各 OTA 浏览器账号的独立档案。

当前主要档案分类：

| 子目录 | 约占用 | 用途 |
|---|---:|---|
| `meituan-ebooking` | 4.90GB | Meituan EBooking 浏览器档案 |
| `ctrip-ebooking` | 3.15GB | Ctrip EBooking 和相关公开页档案 |
| `fliggy-ebooking` | 0.43GB | Fliggy EBooking 浏览器档案 |

### 3.2 可能包含的内容

浏览器档案内部通常包含：

- 登录 Cookie；
- Local Storage、IndexedDB 和站点会话数据；
- 浏览器 Preferences；
- 页面 Cache、Code Cache、GPUCache；
- Service Worker 缓存；
- Chromium 扩展和 `component_crx_cache`；
- `screen_ai`、`OptGuideOnDeviceModel`、`optimization_guide_model_store` 等浏览器模型或组件；
- 浏览器日志、崩溃记录和临时数据库文件。

其中登录态和站点会话数据属于敏感运行状态，不能输出到日志、接口或文档中。

### 3.3 当前最大占用来源

本次盘点发现最大文件约 4.07GB：

```text
meituan-ebooking\...\OptGuideOnDeviceModel\...\weights.bin
```

这是 Chromium 的本地浏览器模型，不是 Hermes 使用的 LLM，也不是酒店业务模型。删除后浏览器可能重新下载。

其他较大来源包括：

- Ctrip 各账号的 `Default\Cache\Cache_Data`；
- `component_crx_cache`；
- `screen_ai` 浏览器组件；
- Chromium 的优化模型缓存；
- 各账号重复保存的浏览器缓存。

## 4. 可清理边界

| 内容 | 是否可清理 | 影响 |
|---|---|---|
| `output/playwright` 旧截图 | 可以 | 失去历史截图证据 |
| `Default\Cache` | 通常可以 | 首次访问可能重新加载资源 |
| `Code Cache`、`GPUCache` | 通常可以 | 浏览器会重新生成 |
| `component_crx_cache` | 通常可以 | 相关组件可能重新下载 |
| `screen_ai`、`OptGuideOnDeviceModel` | 通常可以 | 浏览器可能重新下载，首次启动变慢 |
| `Cookies` | 不建议删除 | 账号可能掉登录 |
| `Local Storage`、`IndexedDB` | 不建议删除 | 站点会话、配置和登录状态可能丢失 |
| `Preferences` | 不建议删除 | 浏览器档案设置可能重置 |
| 整个 `.runtime/browser-state` | 不建议直接删除 | 所有 OTA 账号可能需要重新登录 |

“通常可以”表示清理前仍需确认对应浏览器档案没有正在使用。不能在 Chrome、Playwright、后台 worker 或相关服务运行时直接删除。

## 5. 推荐的后续优化顺序

### 第一阶段：控制截图增长

新增运行时配置，例如：

```text
PLAYWRIGHT_ARTIFACT_RETENTION_DAYS=14
PLAYWRIGHT_ARTIFACT_MAX_FILES_PER_ACCOUNT=100
```

截图写入后或 worker 启动时执行一次限额清理。清理时优先删除最旧的成功截图，保留登录失败、页面结构变化和调价前后证据。

### 第二阶段：增加浏览器缓存清理任务

建议提供 `cleanup-runtime.bat`，支持：

```text
cleanup-runtime.bat report
cleanup-runtime.bat preview
cleanup-runtime.bat screenshots
cleanup-runtime.bat browser-cache
```

默认只报告，不删除。真正清理前必须：

1. 停止 backend、worker 和调度器；
2. 确认没有 Chrome 或 Playwright 进程占用档案；
3. 只清理缓存和模型目录；
4. 不清理 Cookie、Local Storage、IndexedDB 和 Preferences；
5. 重启后检查账号登录态和一次只读读取流程。

### 第三阶段：增加磁盘监控

建议在状态页或日志中记录：

- `output/playwright` 当前大小和文件数；
- 各 OTA 档案大小；
- 可清理缓存估算值；
- 最近一次清理时间和结果；
- 清理失败原因。

当截图目录超过 2GB，或浏览器档案总大小超过设定阈值时，给出运维提示，不自动删除登录态。

## 6. 安全注意事项

- 不要把 `.runtime/browser-state` 提交到 Git。
- 不要把截图直接上传到公共位置；截图可能包含价格、账号页面或运营信息。
- 不要在 API 响应中暴露绝对截图路径、Cookie、浏览器档案路径或登录信息。
- 不要使用“删除整个 `.runtime`”作为常规清理方式。
- 清理前应保留必要的数据库和浏览器档案备份。

## 7. 代码配置位置

默认目录配置位于：

- `backend/app/core/settings.py`
- `.env.example`
- `docs/windows-local-deploy.md`

默认配置为：

```text
BROWSER_STATE_DIR=.runtime/browser-state
PLAYWRIGHT_ARTIFACT_DIR=output/playwright
```

