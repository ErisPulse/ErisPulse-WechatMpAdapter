<div align="center">

<img src=".github/assets/ErisPulseLogo.png" width="180" alt="ErisPulse WechatMpAdapter" />

# ErisPulse WechatMpAdapter

**微信公众号适配器 —— 把公众号变成机器人。**

ErisPulse 的微信公众号（WechatMp）适配模块。遵循 ErisPulse 多账户适配器规范，通过被动回调（Webhook）接收消息，通过客服消息 / 模板消息接口主动发送，支持消息加解密与签名验证。

<p>
  <a href="https://pypi.org/project/ErisPulse-WechatMpAdapter/"><img src="https://img.shields.io/pypi/v/ErisPulse-WechatMpAdapter?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://pypi.org/project/ErisPulse-WechatMpAdapter/"><img src="https://img.shields.io/badge/Python-3.10+-FFD43B?style=for-the-badge&logo=python&logoColor=blue" alt="Python"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/ErisPulse/ErisPulse-WechatMpAdapter"><img src="https://img.shields.io/github/stars/ErisPulse/ErisPulse-WechatMpAdapter?style=for-the-badge&logo=github&color=brightgreen" alt="Stars"></a>
  <a href="https://pepy.tech/project/ErisPulse-WechatMpAdapter"><img src="https://img.shields.io/pepy/dt/ErisPulse-WechatMpAdapter?style=for-the-badge&color=blue" alt="Downloads"></a>
  <a href="https://github.com/ErisPulse/ErisPulse"><img src="https://img.shields.io/badge/Powered_by-ErisPulse-FF6B9D?style=for-the-badge&logo=bookstack&logoColor=white" alt="ErisPulse"></a>
</p>

</div>

---

## 特性

- **多账户支持**：每个账户对应一个公众号，独立配置与回调路径
- **消息加解密**：自动处理明文/兼容/安全三种模式，支持 AES-256-CBC 解密
- **签名验证**：GET 接入验证与 POST 消息回调均校验签名
- **access_token 管理**：自动获取、缓存并提前 5 分钟刷新
- **完整消息类型**：文本、图片、语音、视频、图文、音乐、模板、菜单消息
- **OneBot12 标准**：严格遵循事件转换规范，平台扩展字段统一使用 `mp_` 前缀

## 安装

```bash
epsdk install

# 选择安装适配器
# 安装 WechatMp
```

依赖：`cryptography`（用于消息加解密）。

## 快速开始

### 1. 配置

在 ErisPulse 的 `config.toml` 中添加公众号账户配置：

```toml
[WechatMpAdapter.accounts.default]
appid = "wx1234567890abcdef"
appsecret = "your_app_secret_here"
token = "your_callback_token"              # 公众号后台「基本配置」中的Token
encoding_aes_key = ""                      # 明文模式留空；安全模式需填写43位密钥
callback_path = "/mp/default"              # 回调路径
verified = true                              # 是否为认证服务号（影响消息发送策略）
enable = true
```

多账户配置：

```toml
[WechatMpAdapter.accounts.main]
appid = "wxaaaaaaaaaaaaaaaa"
appsecret = "secret1"
token = "token1"
callback_path = "/mp/main"
enable = true

[WechatMpAdapter.accounts.shop]
appid = "wxbbbbbbbbbbbbbbbb"
appsecret = "secret2"
token = "token2"
callback_path = "/mp/shop"
enable = true
```

### 2. 公众号后台配置

1. 登录微信公众平台 → 「设置与开发」→「基本配置」
2. 填写 **服务器配置**：
   - **URL**：`http://你的服务器地址:端口/<callback_path>`（例如 `/mp/default`）
   - **Token**：与配置中的 `token` 一致
   - **EncodingAESKey**：安全模式必填，与配置中的 `encoding_aes_key` 一致
   - **消息加解密方式**：明文 / 兼容 / 安全（适配器自动适配）

### 3. 处理消息

```python
from ErisPulse.Core import adapter

@adapter.on("message", platform="mp")
async def on_message(event):
    openid = event.get_openid()          # 发送者 OpenID
    msg_type = event.get_msg_type()      # text/image/voice/...
    content = event.get_content()        # 纯文本内容

    if msg_type == "text" and content == "你好":
        await event.reply(
            event.send.Text("你好，欢迎使用公众号！")
        )

@adapter.on("notice", platform="mp")
async def on_notice(event):
    mp_event = event.get_event()
    if mp_event == "subscribe":
        openid = event.get_openid()
        await event.send.Text("感谢关注！")
```

## 发送消息

```python
# 获取发送句柄（target_id = 用户 OpenID）
send = adapter.get_send(platform="mp", detail_type="private",
                        target_id="oABC123...", account_id="default")

# 文本
await send.Text("Hello World").send()

# 图片（URL / 本地路径 / bytes）
await send.Image("https://example.com/photo.jpg").send()

# 图文消息
await send.News([
    {"title": "标题", "description": "描述",
     "url": "https://example.com", "picurl": "https://example.com/pic.jpg"},
]).send()

# 音乐
await send.Music(url="https://example.com/song.mp3",
                 title="歌曲", description="歌手").send()

# 模板消息
await send.Template(
    template_id="TEMPLATE_ID",
    data={"first": {"value": "通知"}, "keyword1": {"value": "内容"}},
    url="https://example.com",
).send()

# 菜单消息
await send.Menu(
    head_content="请选择：",
    list_=[{"id": "opt1", "content": "选项一"},
           {"id": "opt2", "content": "选项二"}],
    tail_content="",
).send()
```

> **注意**：客服消息只能在用户与公众号交互后 48 小时内发送。超出时限请使用模板消息。

## 重要限制

### 1. 认证服务号 vs 未认证号

微信公众号的消息发送能力取决于**是否认证服务号**，在账户配置中通过 `verified` 字段控制：

| 能力 | 认证服务号 (`verified=true`) | 未认证号 (`verified=false`) |
|------|---------------------------|---------------------------|
| 被动回复（回调中直接响应） | ✅ | ✅ |
| 客服消息主动推送 (`message/custom/send`) | ✅ | ❌ 接口无权限 |
| 模板消息 (`message/template/send`) | ✅ | ❌ 接口无权限 |
| 单次回调可发送消息数 | 仅 1 条 | 仅 1 条 |

**未认证号只能使用被动回复**，即每次用户发消息时，适配器在 5 秒内返回一条 XML 响应。模块中的 `event.reply()` 会被自动拦截为被动回复，超出时长的回复将被丢弃。

> 提示：如果你的公众号未认证，在微信公众平台看到的 `errcode: 48001 api unauthorized` 报错是正常的——该接口未开

### 2. 被动回复文本长度

微信对被动回复 XML 中 `Content` 字段的限制：

| 内容类型 | 限制 |
|---------|------|
| 纯英文/数字/符号 | ~2048 字符 |
| 中文字符（UTF-8 每字 3 字节） | ~682 字 |
| 中英混合 | 折中计算 |

超过长度时适配器会自动截断并追加 `...（内容已截断）` 提示。

### 3. 回复时效性

被动回复（所有公众号）
- 微信要求 **5 秒内**返回 XML 响应
- 超时后微信会重试 3 次
- 适配器默认超时设为 4.5 秒，超时后回复丢弃

客服消息（仅认证服务号）
- 用户发送消息后 **48 小时内**可主动推送
- 超出时限需使用模板消息
- 受微信频率限制控制

### 4. 消息加解密

适配器支持三种微信加密模式，通过 `encoding_aes_key` 配置：

| 模式 | `encoding_aes_key` | 说明 |
|------|-------------------|------|
| 明文模式 | 留空 | 消息体明文传输，无需加解密依赖 |
| 兼容模式 | 填写 43 位密钥 | 明文 + 密文同时发送（适配器优先使用明文） |
| 安全模式 | 填写 43 位密钥 | 仅密文传输，需 `cryptography` 库 |

## 消息段类型映射

| OneBot12 消息段 | 微信消息类型 |
|----------------|------------|
| `text` | 文本 / 链接（转文本） |
| `image` | 图片（自动上传 media_id） |
| `voice` | 语音（自动上传 media_id） |
| `video` | 视频/小视频（自动上传 media_id） |
| `news` | 图文消息 |
| `music` | 音乐消息 |
| `template` | 模板消息 |
| `mp_menu` | 菜单消息 |

## 事件类型

所有事件 `platform` 为 `mp`，`detail_type` 统一为 `private`。

- **message**：用户消息（text/image/voice/video/shortvideo/location/link）
- **notice**：事件通知（subscribe/unsubscribe/scan/location_report/menu_click/menu_view/template_send_finish 等）

详见 [platform-features.md](platform-features.md)。

## 许可证

MIT License
