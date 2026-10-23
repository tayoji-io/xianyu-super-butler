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

图片是异步加载的，列表高度会在滚动之后继续变化，只滚一次会停在半路。这里没有
把 DOM 逆序（`column-reverse`）：消息列表还有分页加载与读屏顺序的考虑，逆序后
历史消息、时间分隔线的处理都会变复杂。

改用「贴底跟随」：

- 监听滚动，距底部 80px 内视为贴底；用户上翻阅读时不再抢滚动；
- 内容容器挂 `ResizeObserver`，高度变化（图片加载）时若仍贴底就再滚一次；
- 切换会话时重置为贴底。

## 7. 后续可做

暂无。
