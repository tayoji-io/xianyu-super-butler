# Fork 补丁：按账号分配出站代理

> 本文件只描述本 fork 相对上游（`xianyu-super-butler` 主仓库）新增的能力。
> 所有改动在源码里都带 `fork:proxy` 标记，方便每次同步上游时快速定位与重放。

## 1. 能力说明

每个闲鱼账号可以在后台单独配置一个出站代理，配置后该账号的**全部**平台流量都从
这个出口出去：

| 流量 | 实现位置 |
| --- | --- |
| IM 长连接 WebSocket | `utils/proxy_utils.open_websocket()` → `XianyuAutoAsync._create_websocket_connection()` |
| Token 刷新、商品/订单同步、发货、擦亮等 HTTP 接口 | `utils/proxy_utils.create_aiohttp_session()` |
| 卖家端接口（`XianyuSellerAPI`） | `utils/xianyu_seller_api.py` 构造时按 `cookie_id` 自动取代理 |
| 图片上传 / 商品图下载 | `utils/image_uploader.py`、`XianyuAutoAsync` |
| Cookie 刷新、账号资料抓取、商品详情抓取 | `XianyuAutoAsync._get_playwright_launch_options()` |
| 滑块验证、密码登录 | `utils/xianyu_slider_stealth.py` |
| 人工滑块验证 | `utils/manual_captcha.py` |
| 订单详情抓取 | `utils/order_detail_fetcher.py` |
| 扫码登录 + 人脸验证页 | `utils/qr_login.py` |
| 浏览器池 | `utils/browser_pool.py` |

配置为空的账号仍然直连，行为与上游一致。

## 2. 严格模式：绝不回退直连

这是本补丁最重要的一条约定，改代码时必须守住：

1. **配置了代理，就不允许任何一条链路退回直连。** 代理不可用时的正确表现是
   「账号连不上 + 明确日志」，不是「悄悄用服务器 IP 跑」。
2. **不允许 `try/except` 吞掉代理错误后继续执行。** 浏览器、HTTP、WebSocket
   三条链路里，代理配置/建立失败一律向上抛。
3. **新增任何出站请求，必须走 `utils/proxy_utils.py`。** 直接用
   `aiohttp.ClientSession()`、`websockets.connect()`、`playwright.chromium.launch()`
   都会绕过代理，等同于泄漏真实 IP。
4. **绑定关系损坏也要失败。** 账号绑定的代理被删除（`__missing__`）或被停用
   （`__disabled__`）时，`get_cookie_proxy()` 抛错，账号不会退回直连；数据库读不到
   配置时同样抛错。
5. **保存时就拦住用不了的配置。** 例如带账号密码的 SOCKS 代理，Chromium 不支持，
   保存阶段直接拒绝，避免出现「HTTP 走了代理、浏览器没走」的半吊子状态。
6. **关掉客户端的系统代理兜底。** httpx 默认 `trust_env=True`，在 macOS 上会读取
   系统代理设置（实测会走本机 Clash 的 7890），导致「直连」其实走了系统代理、
   账号代理也被绕过。扫码登录的 httpx 客户端已显式 `trust_env=False`；aiohttp
   默认不读环境代理，无需处理。

### 为什么不做回退

平台侧判断账号关联，出口 IP 是最硬的信号之一。一次「代理抖动 → 直连」就足以把
这个号与同机的其它号绑在一起，之后风控、限流、验证码会一起变多，而且很难再拆开。

## 3. 标记约定

| 标记 | 用途 |
| --- | --- |
| `# >>> fork:proxy` … `# <<< fork:proxy` | 新增的整段代码（路由、方法、导入块），合并时整段保留 |
| 行尾 `# fork:proxy` | 对上游既有代码的单行/小范围改动，合并时优先保留本 fork 版本 |
| `# fork:proxy 说明文字` | 同上，附一句为什么要改 |

检查当前所有标记：

```bash
grep -rn "fork:proxy" --include="*.py" --include="*.txt" --include="*.md" .
```

## 4. 改动清单

### 新增文件（上游没有，合并时不会冲突）

- `utils/proxy_utils.py` —— 代理解析/校验/脱敏、aiohttp 会话、WebSocket 隧道、
  Playwright 配置、连通性自检、账号代理读取（带 30s 缓存）。
- `docs/FORK_PROXY.md` —— 本文件。

### 数据库

- `app/db_manager.py`
  - 新增 `proxies` 表（代理库）：`id / user_id / name / url / remark / enabled / created_at / updated_at`；
  - `cookies` 表新增 `proxy_id TEXT DEFAULT ''`，指向 `proxies.id`（空串=直连）；
  - 迁移逻辑挂在既有的 `profile_columns` 循环里（`fork:proxy` 标记行）；
  - `get_cookie_details()` 返回值新增 `proxy_id / proxy_name / proxy_url`；
  - 新增代理 CRUD：`list_proxies` / `get_proxy` / `create_proxy` / `update_proxy` /
    `delete_proxy`；
  - 新增账号绑定：`update_cookie_proxy_id` / `get_cookie_proxy_id` /
    `get_cookie_proxy`（解析成实际 URL，代理缺失返回 `__missing__`、停用返回
    `__disabled__`，由上层报错而不是当直连）；
  - `save_cookie()` 由 `INSERT OR REPLACE` 改为 UPSERT：REPLACE 会先删行再插入，
    未列出的列（含 `proxy_id`）会被重置为默认值，而 Cookie 每次刷新都会调用它，
    账号就会悄悄退回直连。这个修复同时保住了 `auto_confirm` / `remark` /
    `pause_duration` 等既有设置。

### 后端接口

- `app/reply_server.py`
  - 新增 `AccountProxyUpdate`、`ProxyIn`、`ProxyCheckIn`、`QRGenerateIn` 请求体；
  - 新增代理库接口：`GET/POST /proxies`、`PUT/DELETE /proxies/{pid}`、
    `POST /proxies/check`；
  - 新增账号绑定接口：`PUT /cookies/{cid}/proxy`（传 `proxy_id`，空串=直连）、
    `GET /cookies/{cid}/proxy`；
  - `/cookies/details` 返回 `proxy_id / proxy_name / proxy_url`；
  - `POST /cookies` 支持添加账号时直接带 `proxy_id`；
  - `POST /qr-login/generate` 支持 `proxy_id` / `proxy_url`：扫码与后续人脸验证
    都走该出口，建号时自动绑定同一个代理；
  - 两处账号级 `aiohttp.ClientSession()` 改为走代理；
  - 密码登录的 `XianyuSliderStealth` 传入账号代理。
- `app/cookie_manager.py`
  - 新增 `restart_cookie_task()`：改代理后重建 WebSocket（旧连接仍走老出口）。

### 账号运行时

- `XianyuAutoAsync.py`
  - `__init__` 读取 `self.proxy_url`（非法配置直接抛错，不退回直连）；
  - `create_session()`、Token 刷新、商品图下载、确认发货临时 session 走代理；
  - `_create_websocket_connection()` 改为 `open_websocket()`；
  - `_get_playwright_launch_options()` 注入 Playwright `proxy`；
  - `ImageUploader`、订单详情抓取、两处滑块实例传入代理。

### 工具模块

- `utils/xianyu_seller_api.py`：`proxy_url` 参数 + 按 `cookie_id` 自动解析。
- `utils/image_uploader.py`：`proxy_url` 参数。
- `utils/item_polish.py`：擦亮会话走代理。
- `utils/manual_captcha.py`：惩罚 URL 查询 + 浏览器走代理。
- `utils/browser_pool.py`：浏览器启动注入代理；代理变更时强制重建池化实例。
- `utils/order_detail_fetcher.py`：`proxy_url` 参数贯穿到浏览器启动。
- `utils/xianyu_slider_stealth.py`：`proxy_url` 参数；滑块与密码登录均注入。
- `utils/qr_login.py`：会话级代理（httpx + 验证页浏览器），`QR_LOGIN_PROXY` 作为
  默认值；httpx 客户端显式 `trust_env=False`，避免被系统代理劫持。

### 前端

- `frontend/components/ProxyManager.tsx`（新增）—— 代理管理页：列表、新增/编辑、
  连通性测试、删除；删除时提示会影响多少个账号。
- `frontend/components/Sidebar.tsx` —— 「系统」分组新增「代理管理」入口。
- `frontend/App.tsx` —— 挂载 `proxies` 页面。
- `frontend/components/AccountList.tsx` —— 编辑账号时从代理库下拉选择；
  账号卡片展示当前代理徽标。
- `frontend/services/api.ts`、`frontend/types.ts` —— 代理 CRUD / 绑定接口与类型。

### 依赖

- `requirements.txt`：`python-socks[asyncio]`（WebSocket 隧道）、`aiohttp-socks`
  （socks5 的 HTTP 请求）、`socksio`（httpx 的 socks 支持）。

## 5. 与上游同步的推荐流程

```bash
# 一次性：把上游加为 remote
git remote add upstream https://github.com/<上游作者>/xianyu-super-butler.git

# 每次同步
git fetch upstream
git checkout -b sync/upstream-$(date +%Y%m%d)
git merge upstream/main
```

冲突处理原则：

1. **整段标记（`>>>` / `<<<`）**：保留本 fork 的整段，除非上游实现了同类能力；
   若上游也加了代理，优先切换到上游实现，再删掉本 fork 的标记块。
2. **行尾标记**：保留本 fork 的行，把上游对同一行的其它修改合并进来。
3. **新文件**：`utils/proxy_utils.py` 不参与冲突，但上游若改了
   `websockets` 版本约束（目前 `<13`），需要检查 `open_websocket()` 的版本分支
   是否仍然正确。
4. 合并后跑一遍第 6 节的自检。

## 6. 合并后自检

```bash
# 1) 所有账号级出站入口都还带着代理
grep -rn "fork:proxy" --include="*.py" . | wc -l   # 数量不应减少

# 2) 不应该再出现裸的会话/连接创建（白名单：通知 webhook、AI、公告、公共搜索）
grep -rn "aiohttp.ClientSession()\|websockets.connect(" --include="*.py" . | grep -v tests

# 3) 单元测试
python -m pytest tests/test_proxy_utils.py -q
```

## 7. 运维注意事项

- **必须用国内住宅/4G 代理。** 机房 IP、海外 IP 在闲鱼侧基本等于自曝；
  `docs/deployment.md` 里也写了「用 VPN、代理或海外服务器时风控明显收紧」。
- **一个账号固定一个出口。** 频繁切换 IP 比固定 IP 更可疑；换代理后账号任务会
  重启，属于正常现象。
- **不要给代理加 DNS 泄漏口子。** HTTP 代理与 `socks5h` 都是远端解析域名；
  `socks4` 会本地解析，除非确有需要否则不要用。
- **代理挂了账号就掉线。** 这是有意为之：宁可掉线也不泄漏 IP。请给代理做好
  可用性监控，或准备同出口的备用地址后手动切换。
- **Chromium 不支持带认证的 SOCKS。** 这类配置在保存时就会被拒绝，请改用
  HTTP 代理或免认证 SOCKS5。
- **删除代理前要先解绑。** 仍被账号引用的代理不允许删除（接口返回 409），
  必须先到账号管理把这些账号切到其它代理或直连，避免它们被动失去出口。
- **停用代理等于让使用它的账号下线。** 这是严格模式的预期行为；重新启用后
  重启账号即可恢复。
