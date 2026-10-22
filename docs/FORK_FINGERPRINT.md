# Fork 补丁：账号级浏览器身份

> 所有改动带 `fork:fingerprint` 标记，与 `fork:proxy` 相互独立，可单独回退。

## 1. 为什么需要

同机跑多个闲鱼账号时，如果所有账号共用同一套 UA、视口、缓存目录和浏览器
存储，平台侧看到的几乎是同一台设备。实测现状（改动前）：

| 指纹层 | 状态 |
| --- | --- |
| UA / sec-ch-ua | 全局常量，所有账号相同 |
| 视口 / 缩放 / 配色 | 写死 1920×1080，所有账号相同 |
| 浏览器 profile | Cookie 刷新、资料抓取每次都是全新临时 profile |
| deviceId | 每次启动重新随机，且不同模块各自生成 |
| 磁盘缓存 | 无（或全机共用） |

代理解决的是出口 IP，这一层解决的是"看起来像不像同一台设备"。

## 2. 做了什么

### 2.1 deviceId 按账号稳定派生

`utils/xianyu_utils.generate_device_id()` 原实现每次调用都 `random`：

- 账号每次重启都换一台"新设备"；
- `reply_server` 刷新风控状态、`manual_captcha` 拿验证 URL 时各自再生成一个，
  同一账号在不同请求里的 deviceId 不一致。

改为由账号标识 `sha256` 派生，保持原来的 `<uuid>-<unb>` 形状。同一账号在任何
进程、任何重启后都得到同一个值，不同账号之间仍然不同。

### 2.2 账号级浏览器特征

新增 `utils/account_identity.py`：

- `fingerprint_for(account_id)`：从一组真实桌面分辨率、缩放比、配色中
  **确定性**取一套。同一账号每次一致，不同账号之间有稳定差异；
- `context_options(account_id, base)`：把视口 / `device_scale_factor` /
  `color_scheme` / `locale` / `timezone_id` 合进 Playwright 上下文参数，
  调用方显式指定的值不覆盖；
- 已接入：资料抓取、商品详情、扫码后刷新 Cookie、页面刷新、定时 Cookie 刷新、
  浏览器池、人工滑块验证、滑块验证、密码登录、订单详情抓取。

**刻意不做 UA 伪造**：伪造 UA 只改 JS/请求头字符串，Client Hints 与真实内核
对不上，反而更像机器人。项目之前已经踩过这个坑（见 `user_agents.py` 注释）。

### 2.3 账号级缓存与浏览器存储

- 磁盘缓存：`--disk-cache-dir=browser_data/accounts/<id>/cache`。
  这个参数不占用 `user-data-dir` 的单例锁，因此不用把浏览器改成持久化上下文
  就能按账号隔离缓存；
- storage_state：`browser_data/accounts/<id>/storage_state.json`，
  保存 Cookie 与 localStorage。上下文创建时自动加载，流程结束前落盘。
  实测一个真实账号的 goofish 源下有 13 条 localStorage、22 个 Cookie；
- 滑块验证与密码登录本来就用 `browser_data/slider_<id>`、`user_<id>` 持久化
  目录，保持不变，只是把视口/配色改为按账号派生。

目录结构：

```
browser_data/
├── accounts/<账号id>/cache/            # 磁盘缓存
├── accounts/<账号id>/storage_state.json
├── slider_<账号id>/                    # 滑块持久化 profile
└── user_<账号id>/                      # 密码登录持久化 profile
```

`browser_data/**` 已在 `.gitignore` 中。

## 3. 边界（没做的事）

- **TLS / HTTP2 指纹仍然共享**：所有账号都走 Python `aiohttp` / `websockets`，
  ClientHello 完全一致。这是比 UA 更强的关联信号，但改造需要换 HTTP 栈或给
  WebSocket 加 uTLS，成本高，留待有明确风控证据时再评估。
- **GPU / Canvas / 字体**：同机天然相同，只能靠一账号一容器/一机器解决。
- **UA 不伪造**：见 2.2。
- **时区固定 `Asia/Shanghai`**：当前代理都是国内出口。若以后接入海外代理，
  需要按代理归属地调整 `fingerprint_for()` 的 `timezone_id`/`locale`。

## 4. 合并上游时注意

1. 新增文件 `utils/account_identity.py` 不冲突；
2. `generate_device_id()` 被整体替换，冲突时保留本 fork 版本；
3. 浏览器上下文创建处是"整块替换"，冲突时保留本 fork 的
   `account_identity.context_options(...)` 写法，并把上游新增的上下文参数
   合进 `base` 字典；
4. 上游若新增浏览器启动路径，记得补
   `account_identity.launch_args(account_id)` 与 storage_state 读写。

## 5. 验证

```bash
python -m pytest tests/test_account_identity.py -q
```

跑一次「刷新闲鱼资料」或等定时 Cookie 刷新后检查：

```bash
find browser_data/accounts -maxdepth 2 -type d
ls -l browser_data/accounts/<账号id>/storage_state.json
```
