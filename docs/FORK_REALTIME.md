# Fork 补丁：聊天消息实时推送

> 改动带 `fork:realtime` 标记。

## 1. 问题

网页「消息中心」原先靠轮询：消息列表与会话列表每 10 秒各拉一次，服务端还有
3 秒结果缓存。结果是：

- 买家发来消息，网页最坏要等 10 秒才显示；
- 手动刷新时，3 秒内重复请求会直接返回旧列表 —— 表现为「明明收到了，页面还是旧的」；
- 把轮询调快会适得其反：每次拉取都要经 WebSocket 透传到闲鱼，3 秒一轮容易触发
  429（`flow controled`）。

实测（2026-10-23）：平台消息时间戳到服务端日志 0.44~0.92 秒，说明瓶颈不在推送，
而在「服务端已经拿到了，网页还不知道」。

## 2. 方案

后端收到消息就推事件，网页收到事件再拉一次最新数据。

```
闲鱼 WS ──► XianyuLive.handle_message ──► chat_event_bus.publish
                                              │
                          SSE /chat/events ◄──┘
                                  │
             网页收到事件 ──► fresh=true 拉消息/会话
```

### 2.1 事件总线

`app/chat_events.py`。关键点：账号任务跑在 Start.py 的主事件循环，uvicorn 在
**另一个线程**跑自己的循环。直接跨线程写 `asyncio.Queue` 会静默丢事件，所以
订阅时记录订阅方 loop，publish 时用 `call_soon_threadsafe` 投递。队列满时丢弃
事件而不是阻塞消息处理（前端还有轮询兜底）。

### 2.2 SSE 接口

`GET /chat/events`：

- 标准 `text/event-stream`，15 秒一次 `: ping` 心跳，避免代理掐空闲连接；
- 响应头带 `X-Accel-Buffering: no`，否则 nginx 会缓冲事件；
- 事件带 `user_id`，只推给账号所属用户；
- 前端用 `fetch` + `ReadableStream` 读取，Authorization 走请求头，不把 token
  放进 URL。

事件格式：

```json
{"type":"message","cookie_id":"636699409","user_id":1,"chat_id":"66242219090",
 "direction":"in","item_id":"1081638876394","preview":"你好"}
```

### 2.3 绕过缓存

`/chat/messages`、`/chat/conversations` 新增 `fresh=true`：跳过 3 秒结果缓存，
仍保留并发请求合并，并把新结果写回缓存。前端在两种场景使用：

- 收到 SSE 事件后；
- 发送消息后（原先会被缓存挡住，自己发的消息要等下一轮轮询才出现）。

## 3. 降级

- SSE 断线每 3 秒重连；
- 轮询保留作为兜底，间隔从 10 秒放宽到 30 秒（实时性由 SSE 负责）；
- 事件只带会话标识，不带消息正文，最终数据仍以拉取结果为准，避免事件与列表
  不一致。

## 4. 验证

```bash
# 订阅事件流（应看到 : connected 和 : ping）
curl -N -H "Authorization: Bearer <token>" http://127.0.0.1:8080/chat/events

# 用另一个账号给该账号发消息，事件应立刻出现：
# data: {"type":"message","cookie_id":"...","chat_id":"...","preview":"..."}
```

实测 4 条测试消息产生 4 个事件；消息中心打开时会建立 `/chat/events` 订阅。

## 5. 重连期间的 503

账号每小时刷新 Token 时会主动关闭 WebSocket 重连（实测断开约 0.2 秒，网络抖动时
更久）。这几秒内 `/chat/*` 会返回 503，属于短暂状态而不是故障。

前端处理：

- axios 拦截器把后端 `detail` 透传成 `error.message`，不再只显示
  `Request failed with status code 503`；
- 502/503/504 视为可恢复：只提示一次「账号正在重连，稍后自动刷新」（30 秒内不重复），
  3 秒后自动重试，不弹红色报错。

## 6. 消息列表滚动

图片是异步加载的，列表高度会在滚动之后继续变化，只 `scrollIntoView` 一次会停在
半路。改用 **column-reverse** 方案（参考 `fmz_web_next` 的 `chat-body.tsx`）：

- 滚动容器 `flex flex-col-reverse`，`scrollTop = 0` 就是底部；
- 消息仍按时间正序渲染在单个内容容器里，容器 `mb-auto` 保证短会话贴顶；
- 新消息 / 图片撑高时，浏览器自动保持贴底，不需要 `scrollIntoView` 或
  `ResizeObserver`；
- 用户上翻后 `scrollTop` 变为负值，浏览器会保持视口不动（实测位移 0px）；
- 贴底判断用 `Math.abs(scrollTop) < 80`，兼容不同浏览器的正负约定。

为什么不用 `scrollIntoView`：它会留下非 0 的 `scrollTop`，破坏 column-reverse
容器的天然贴底锚点，内容一变高就差一点点。

### 历史消息分页

后端接口一直支持游标分页（`get_im_messages(cid, start_timestamp, limit)`，返回
`hasMore` / `nextCursor`），但前端此前只拉最新 50 条、没有上滑加载 —— 这是原有
行为，不是本次改动引入的。

现在补上：

- 进入会话首次加载时记录 `nextCursor`，之后轮询/SSE 刷新不会重置它；
- 滚动到顶部 120px 内触发加载，用 `cursor` 取更早的一页；
- 新旧两页按 `messageId` 去重合并、按时间排序，上限 2000 条；
- 顶部显示「正在加载更早的消息…」，没有更多时提示「没有更早的消息了」；
- **不手动补偿 scrollTop**：column-reverse 容器里 Chrome 的滚动锚定会自动把
  视口固定在原消息上（实测加载 20 条后位移 0px），再按高度差补偿一次会双重
  调整、反而跳 1200 多像素。

## 7. 买家头像

IM 的会话/消息接口不返回头像（`avatar` / `avatarUrl` / `senderAvatar` 都是空），
前端此前只能显示首字母占位。补充：

- 新增 `GET /chat/users/{cookie_id}/{user_id}/profile`，走
  `mtop.idle.web.user.page.head`（结构化接口，不需要浏览器）；
- 缓存分两层：进程内 7 天 + SQLite `chat_user_profiles` 7 天，重启不会重新
  批量请求；被平台处置的用户空资料负缓存 1 天；
- 前端按 `userId` 懒加载：单队列、每批最多 8 个、间隔 500~800ms，结果存内存；
- 账号处于风控冷却期时直接返回空头像（缓存 5 分钟），不重试、不报错；
- 平台默认占位头像（`gtms03.alicdn.com` 旧 CDN，HTTPS 已不可用）视为无头像；
- 没有头像时统一显示本地默认头像 `frontend/public/default-avatar.webp`
  （构建后为 `/static/default-avatar.webp`），不再用首字母色块。

关于风控：`mtop.idle.web.user.page.head` 是普通只读接口，单次调用风险很低，
但所有 `XianyuSellerAPI` 调用共用账号级令牌桶（默认 30 次/分钟）和熔断。
头像属于装饰性请求，必须让路给消息/订单：上面的批量与间隔就是为了不把配额
吃光，也不会在风控期间继续撞接口。

## 8. 后续可做

暂无。
