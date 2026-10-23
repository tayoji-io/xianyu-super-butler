# 滑块自动验证优化

## 现状诊断（2026-10-23 实测）

- `trajectory_history/strategy_stats.json`：3 次流程 × 3 次重试，**0 通过**；
- Token 接口返回 `RGV587_ERROR::SM::哎哟喂,被挤爆啦` —— 账号处于**限流 / 高风险**状态，
  不只是"需要滑块"；
- nc 的失败码是 `error:7u3jap` / `error:AadTEp`，属于**行为判定失败**；
- 对照实验：手工用 1.2~1.6 秒的慢速拟人轨迹（60~80 个采样点、轻微过冲、松手前停顿）
  在真实 punish 页上拖动，**同样失败**。

结论：账号的风险状态是主因，单靠调轨迹参数救不回来。之前"3 次重试、间隔 1 秒、
每 10 分钟再来一轮"的做法只会持续推高风险评分。

## 已做的改动

| 改动 | 说明 |
| --- | --- |
| RGV587 不再当滑块处理 | 识别到 `RGV587` / `被挤爆` / `FAIL_SYS_FLOW_LIMIT` 时直接冷却 15 分钟，**不拉起浏览器** |
| 重试次数 3 → 2 | 同一页面连续拖 3 次几乎必败，且会加重风控 |
| 重试间隔 0.5~1s → 12~20s | 并重新加载验证页，拿一个干净的滑块会话 |
| 轨迹模型轮换 | 第一次慢速最小急动度（2.5~4 秒），第二次 Ydisks 快节奏（0.55~1.05 秒） |
| 拖动前鼠标预热 | 先在页面随机移动 2~3 次，再靠近滑块，避免"鼠标凭空出现在滑块上" |
| 松手后微移动 | 人手松手后不会瞬间静止，补 1~3px 的小移动 |
| 失败冷却 5 → 15 分钟 | 平台风险评分不会在几分钟内回落 |

## 可调参数

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `SLIDER_MAX_ATTEMPTS` | `2` | 单次流程最多尝试几次（1~3） |
| `SLIDER_TRAJECTORY_MODEL` | 轮换 | 固定为 `ydisks`（快）或 `slow`（慢） |
| `SLIDER_HEADLESS` | `false` | 必须保持有头；服务器用 `xvfb-run` 提供虚拟显示 |

## 操作建议

1. **先让账号休息**：当前处于风控冷却，停 30~60 分钟，期间不要反复手动触发滑块；
2. **确认有头模式**：`SLIDER_HEADLESS=false`；服务器没有显示器时用
   `xvfb-run -a --server-args="-screen 0 1920x1080x24" python Start.py`；
3. **确认出口**：国内家宽/住宅 IP 最好，机房 IP 或频繁切换 IP 会显著降低通过率；
4. **自动失败 2 次后转人工**：人工验证通过一次通常能把风险清零，继续自动重试只会更糟；
5. **长期 0 通过**：换一个干净的浏览器 profile（`browser_data/slider_<账号id>` 改名或删除）
   再试，老 profile 可能已经带上了负面标记。

## 代理与指纹

滑块、人工验证、订单详情、浏览器池都走**账号代理**，并使用**账号固定指纹**：

| 操作 | 代理 | 指纹 |
| --- | --- | --- |
| 滑块 `XianyuSliderStealth` | `playwright_proxy(proxy_url)`，配置错误直接失败不回退 | 账号固定视口/DPR/配色/时区 + 独立持久化 profile + Patchright + 真实 Chrome UA |
| 人工验证 `manual_captcha` | `get_cookie_proxy()` | 同上 + `storage_state` |
| 订单详情 `order_detail_fetcher` | `playwright_proxy(proxy_url)` | `storage_state` + 移动端 UA/视口（h5 页面必须） |
| 浏览器池 `browser_pool` | `get_cookie_proxy()` | `context_options` + `storage_state` + 启动参数 |
| 图片上传 / 卖家接口 / 擦亮 / 扫码 | aiohttp/httpx 走代理 | 不涉及浏览器 |

**已修的指纹矛盾**：有头系统 Chrome 的原生 UA 本来就和 Client Hints 一致，
但旧代码仍用 CDP 把 UA 强制改成 Windows。macOS 上于是出现
`UA=Windows` + `navigator.platform=MacIntel` + 字体/GPU=macOS 的组合，
是明确的伪造特征。现在只有真实 UA 带 `HeadlessChrome` 时才覆盖，
并且同时设置 `platform`/`acceptLanguage`，保证 JS 内部一致。

注意：滑块的持久化 profile 是 `browser_data/slider_<账号id>`，与主浏览器池
的 profile 分开，存储互不影响，但视口/DPR/配色等身份参数是同一套。

## 人工验证链接与 `_m_h5_tk`

「在我的浏览器打开验证页」按钮会调
`POST /api/risk-control/{cookie_id}/fresh-captcha-url`，用账号 Cookie 重新请求
一次 Token 接口拿新鲜链接。

刚扫码的账号 Cookie 里可能还没有 `_m_h5_tk`（mtop 签名令牌），旧实现直接报
「请重新扫码登录」。现在：

1. 优先用运行中实例内存里的 Cookie（实例可能已经从 mtop 响应里拿到了新令牌）；
2. 没有令牌时用**空 token** 打一次 mtop：
   - 正常账号会通过 `Set-Cookie` 下发 `_m_h5_tk`，补全后继续请求；
   - 正在风控中的账号，这次响应会直接带回新的 punish 链接，直接返回给用户；
3. 滑块验证完成后，除了 `x5*`，也会把 `_m_h5_tk` / `_m_h5_tk_enc` 一起回收，
   否则验证通过了仍然无法签名、拿不到新 token。

## 后续可做

- 自动 profile 轮换：连续失败 N 次后自动换目录；
- 手动优先策略：连续 2 次自动失败后直接把人工验证推到前端；
- 采集每次拖动的 nc 失败码，按错误类型区分"轨迹问题"与"账号风险"。
