import asyncio
import contextvars
import hashlib
import io
import struct
import time
import xml.etree.ElementTree as ET
from base64 import b64decode
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from ErisPulse.Core import client, router
from ErisPulse.Core.Bases.adapter import BaseAdapter
from ErisPulse.Core.Event import register_event_mixin, unregister_platform_event_methods
from ErisPulse.runtime.config_schema import BotAccountConfig

# 被动回复上下文：webhook 请求中通过此透传到 call_api
_passive_reply_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "mp_passive_reply", default=None
)
# 被动回复超时（微信要求 5 秒内响应）
_PASSIVE_REPLY_TIMEOUT = 4.5
# 被动回复文本最大长度（微信限制约 2048 字以内稳妥）
_PASSIVE_TEXT_MAX_LEN = 2000

from .Converter import WechatMpConverter

# 微信 API 基地址
MP_API_BASE = "https://api.weixin.qq.com/cgi-bin"


@dataclass
class WechatMpAccountConfig(BotAccountConfig):
    """微信公众号账户配置（每个账户对应一个公众号）"""

    appid: str = field(
        default="",
        metadata={
            "description": "公众号AppID",
            "required": True,
            "webui": {"widget": "text", "group": "basic", "order": 1},
        },
    )
    appsecret: str = field(
        default="",
        metadata={
            "description": "公众号AppSecret",
            "required": True,
            "secret": True,
            "webui": {"widget": "password", "group": "basic", "order": 2},
        },
    )
    token: str = field(
        default="",
        metadata={
            "description": "回调验证Token（公众号后台设置的Token）",
            "secret": True,
            "webui": {"widget": "password", "group": "callback", "order": 3},
        },
    )
    encoding_aes_key: str = field(
        default="",
        metadata={
            "description": "消息加解密密钥（安全模式/兼容模式，43位）",
            "secret": True,
            "webui": {"widget": "password", "group": "callback", "order": 4},
        },
    )
    callback_path: str = field(
        default="/mp/{account}",
        metadata={
            "description": "微信回调路径模板（{account}会被账户名替换）",
            "webui": {"widget": "text", "group": "callback", "order": 5},
        },
    )
    verified: bool = field(
        default=True,
        metadata={
            "description": "是否为认证服务号（认证号可用客服消息主动推送，未认证号只能被动回复）",
            "required": False,
            "webui": {"widget": "switch", "group": "basic", "order": 6},
        },
    )


class WechatMpEventMixin:
    """微信公众号事件扩展方法"""

    def get_openid(self) -> str:
        """获取发送者 OpenID（即 OneBot12 user_id）"""
        return self.get("user_id", "") or self.get("mp_from_user", "")

    def get_msg_type(self) -> str:
        """获取微信原始消息类型（text/image/voice/video/event 等）"""
        return self.get("mp_raw_type", "")

    def get_event(self) -> str:
        """获取事件类型（仅事件通知有效，如 subscribe/click/view 等）"""
        return self.get("mp_event", "")

    def get_content(self) -> str:
        """获取消息纯文本内容"""
        return self.get("alt_message", "") or ""

    def get_raw_xml(self) -> str:
        """获取原始 XML 数据"""
        return self.get("mp_raw", "")


register_event_mixin("mp", WechatMpEventMixin)


class WechatMpAdapter(BaseAdapter):
    """微信公众号适配器

    多账户适配器，每个账户对应一个公众号。通过被动回调（Webhook）接收用户消息，
    通过客服消息接口主动发送消息。
    """

    AccountConfigClass = WechatMpAccountConfig

    class Send(BaseAdapter.Send):
        def __init__(self, adapter, target_type=None, target_id=None, account_id=None):
            super().__init__(adapter, target_type, target_id, account_id)

        def Text(self, text: str):
            return self.Raw_ob12([{"type": "text", "data": {"text": text}}])

        def Image(self, file, caption: str = ""):
            return self.Raw_ob12(
                [{"type": "image", "data": {"file": file, "caption": caption}}]
            )

        def Voice(self, file):
            return self.Raw_ob12([{"type": "voice", "data": {"file": file}}])

        def Video(self, file, title: str = "", description: str = ""):
            return self.Raw_ob12(
                [
                    {
                        "type": "video",
                        "data": {
                            "file": file,
                            "title": title,
                            "description": description,
                        },
                    }
                ]
            )

        def Music(
            self,
            url: str,
            title: str = "",
            description: str = "",
            hq_url: str = "",
            thumb_media_id: str = "",
        ):
            return self.Raw_ob12(
                [
                    {
                        "type": "music",
                        "data": {
                            "url": url,
                            "hq_url": hq_url or url,
                            "title": title,
                            "description": description,
                            "thumb_media_id": thumb_media_id,
                        },
                    }
                ]
            )

        def News(self, articles: list):
            """发送图文消息

            :param articles: 图文列表，每项 {"title":..., "description":..., "url":..., "picurl":...}
            """
            return self.Raw_ob12([{"type": "news", "data": {"articles": articles}}])

        def Template(self, template_id: str, data: dict, url: str = ""):
            """发送模板消息

            :param template_id: 模板ID
            :param data: 模板数据（如 {"first": {"value": "..."}}）
            :param url: 点击跳转URL
            """
            return self.Raw_ob12(
                [
                    {
                        "type": "template",
                        "data": {
                            "template_id": template_id,
                            "data": data,
                            "url": url,
                        },
                    }
                ]
            )

        def Menu(self, head_content: str, list_: list, tail_content: str = ""):
            """发送菜单消息（客服菜单消息）

            :param head_content: 头部文本
            :param list_: 菜单项列表 [{"id":..., "content":...}]
            :param tail_content: 尾部文本
            """
            return self.Raw_ob12(
                [
                    {
                        "type": "mp_menu",
                        "data": {
                            "head_content": head_content,
                            "list": list_,
                            "tail_content": tail_content,
                        },
                    }
                ]
            )

        def Raw_ob12(self, message: list, **kwargs):

            async def _send():
                ctx = self.send_context
                account_id = ctx.get("account_id")
                target_id = ctx.get("target_id")

                results = []
                for segment in message:
                    seg_type = segment.get("type", "")
                    seg_data = segment.get("data", {})

                    call = await self._convert_ob12_to_mp(
                        seg_type, seg_data, target_id, account_id
                    )
                    if call is None:
                        continue
                    result = await self._do_send(call, account_id)
                    results.append(result)

                self._reset_modifiers()
                return results[-1] if results else None

            return asyncio.create_task(_send())

        async def _convert_ob12_to_mp(
            self, seg_type: str, seg_data: dict, touser: str, account_id: str
        ) -> Optional[Dict]:
            """将单个 OneBot12 消息段转换为微信客服消息调用"""
            params: Dict[str, Any] = {"touser": touser}

            if seg_type == "text":
                params["msgtype"] = "text"
                params["text"] = {"content": seg_data.get("text", "")}
                return {
                    "endpoint": "message/custom/send",
                    "params": params,
                }

            if seg_type == "image":
                file_val = seg_data.get("file", "")
                media_id = await self._ensure_media_id(file_val, "image", account_id)
                params["msgtype"] = "image"
                params["image"] = {"media_id": media_id}
                return {
                    "endpoint": "message/custom/send",
                    "params": params,
                }

            if seg_type == "voice":
                file_val = seg_data.get("file", "")
                media_id = await self._ensure_media_id(file_val, "voice", account_id)
                params["msgtype"] = "voice"
                params["voice"] = {"media_id": media_id}
                return {
                    "endpoint": "message/custom/send",
                    "params": params,
                }

            if seg_type == "video":
                file_val = seg_data.get("file", "")
                media_id = await self._ensure_media_id(file_val, "video", account_id)
                params["msgtype"] = "video"
                params["video"] = {
                    "media_id": media_id,
                    "title": seg_data.get("title", ""),
                    "description": seg_data.get("description", ""),
                }
                return {
                    "endpoint": "message/custom/send",
                    "params": params,
                }

            if seg_type == "music":
                params["msgtype"] = "music"
                params["music"] = {
                    "musicurl": seg_data.get("url", ""),
                    "hqmusicurl": seg_data.get("hq_url", seg_data.get("url", "")),
                    "title": seg_data.get("title", ""),
                    "description": seg_data.get("description", ""),
                    "thumb_media_id": seg_data.get("thumb_media_id", ""),
                }
                return {
                    "endpoint": "message/custom/send",
                    "params": params,
                }

            if seg_type == "news":
                params["msgtype"] = "news"
                params["news"] = {"articles": seg_data.get("articles", [])}
                return {
                    "endpoint": "message/custom/send",
                    "params": params,
                }

            if seg_type == "mp_news":
                params["msgtype"] = "mp_news"
                params["mp_news"] = {"media_id": seg_data.get("media_id", "")}
                return {
                    "endpoint": "message/custom/send",
                    "params": params,
                }

            if seg_type == "template":
                template_params: Dict[str, Any] = {
                    "touser": touser,
                    "template_id": seg_data.get("template_id", ""),
                    "data": seg_data.get("data", {}),
                }
                if seg_data.get("url"):
                    template_params["url"] = seg_data["url"]
                return {
                    "endpoint": "message/template/send",
                    "params": template_params,
                }

            if seg_type == "mp_menu":
                menu_params: Dict[str, Any] = {
                    "touser": touser,
                    "msgtype": "msgmenu",
                    "msgmenu": {
                        "head_content": seg_data.get("head_content", ""),
                        "list": seg_data.get("list", []),
                        "tail_content": seg_data.get("tail_content", ""),
                    },
                }
                return {
                    "endpoint": "message/custom/send",
                    "params": menu_params,
                }

            # 不支持的消息段类型：作为纯文本提示
            params["msgtype"] = "text"
            params["text"] = {"content": f"[不支持的消息类型: {seg_type}]"}
            return {
                "endpoint": "message/custom/send",
                "params": params,
            }

        async def _do_send(self, call: Dict, account_id: str) -> Dict:
            endpoint = call["endpoint"]
            params = call["params"]
            return await self._adapter.call_api(
                endpoint=endpoint, _account_id=account_id, **params
            )

        async def _ensure_media_id(
            self, file_val: str, media_type: str, account_id: str
        ) -> str:
            """确保获得 media_id。

            - 若 file_val 已是 media_id（以 'media:' 前缀标记，或无 http/本地路径特征）则直接使用
            - 否则上传文件获取 media_id
            """
            if not file_val:
                return ""

            # 显式标记的 media_id
            if isinstance(file_val, str) and file_val.startswith("media:"):
                return file_val[6:]

            # 形似 media_id 的字符串（非 URL 且非明显文件路径）直接使用
            if isinstance(file_val, str) and not file_val.startswith(
                ("http://", "https://")
            ):
                # 不含路径分隔符且无明显扩展名，视为 media_id
                if not any(sep in file_val for sep in ("/", "\\")):
                    return file_val

            # 需要上传
            media_id = await self._adapter._upload_media_for_send(
                file_val, media_type, account_id
            )
            return media_id or ""

        def _reset_modifiers(self):
            pass

    # ==================== 适配器主类 ====================

    def __init__(self, sdk_ref=None):
        super().__init__(sdk_ref)
        self._converters: Dict[str, WechatMpConverter] = {}
        self._token_cache: Dict[str, Dict] = {}
        self._token_locks: Dict[str, asyncio.Lock] = {}
        self._running = False
        self._registered_routes: List[tuple] = []
        self._register_event_methods()

    def _get_config_key(self) -> str:
        return "WechatMpAdapter"

    def _load_accounts(self) -> dict:
        from ErisPulse.Core.config import config as config_mgr
        from ErisPulse.runtime.config_schema import dict_to_dataclass

        key = "WechatMpAdapter.accounts"
        data = config_mgr.getConfig(key)

        if not data:
            old_config = config_mgr.getConfig("WechatMpAdapter")
            if old_config and "appid" in old_config:
                self.logger.warning("检测到旧格式配置，建议迁移到新格式")
                self.logger.warning(
                    "迁移方法：将现有配置移动到 WechatMpAdapter.accounts.default 下"
                )
                data = {
                    "default": {
                        "appid": old_config.get("appid", ""),
                        "appsecret": old_config.get("appsecret", ""),
                        "token": old_config.get("token", ""),
                        "encoding_aes_key": old_config.get("encoding_aes_key", ""),
                        "callback_path": old_config.get(
                            "callback_path", "/mp/{account}"
                        ),
                        "verified": True,
                        "enabled": True,
                    }
                }
                self.logger.warning("已临时加载旧配置为默认账户，请尽快迁移到新格式")
            else:
                self.logger.info("未找到配置文件，创建默认账户配置")
                data = {
                    "default": {
                        "appid": "",
                        "appsecret": "",
                        "token": "",
                        "encoding_aes_key": "",
                        "callback_path": "/mp/{account}",
                        "verified": True,
                        "enabled": True,
                    }
                }
                try:
                    config_mgr.setConfig(key, data)
                except Exception as e:
                    self.logger.error(f"保存默认账户配置失败: {str(e)}")

        accounts = {}
        for name, account_data in data.items():
            if not isinstance(account_data, dict):
                continue

            if "appid" not in account_data or not account_data["appid"]:
                self.logger.error(f"账户 {name} 缺少appid配置，已跳过")
                continue

            instance = dict_to_dataclass(WechatMpAccountConfig, account_data)
            instance.name = name

            if not instance.callback_path:
                instance.callback_path = "/mp/{account}"
            instance.callback_path = instance.callback_path.replace("{account}", name)

            accounts[name] = instance

        self.logger.info(f"微信公众号适配器初始化完成，共加载 {len(accounts)} 个账户")
        return accounts

    def _register_event_methods(self):
        try:
            pass
        except Exception as e:
            self.logger.warning(f"注册微信公众号事件扩展方法失败: {e}")

    # ==================== access_token 管理 ====================

    def _get_token_lock(self, account_name: str) -> asyncio.Lock:
        if account_name not in self._token_locks:
            self._token_locks[account_name] = asyncio.Lock()
        return self._token_locks[account_name]

    async def _get_access_token(self, account_name: str) -> Optional[str]:
        """获取指定账户的 access_token，自动缓存并提前刷新"""
        account = self.accounts.get(account_name)
        if not account:
            return None

        lock = self._get_token_lock(account_name)
        async with lock:
            cached = self._token_cache.get(account_name)
            now = time.time()
            # 提前 5 分钟刷新
            if cached and cached.get("expires_at", 0) - 300 > now:
                return cached.get("access_token")

            try:
                url = (
                    f"{MP_API_BASE}/token?grant_type=client_credential"
                    f"&appid={account.appid}&secret={account.appsecret}"
                )
                resp = await client.get(url)
                data = await resp.json()

                if "access_token" not in data:
                    errcode = data.get("errcode", "")
                    errmsg = data.get("errmsg", "unknown")
                    self.logger.error(
                        f"账户 {account_name} 获取access_token失败: "
                        f"[{errcode}] {errmsg}"
                    )
                    return None

                token = data["access_token"]
                expires_in = data.get("expires_in", 7200)
                self._token_cache[account_name] = {
                    "access_token": token,
                    "expires_at": now + expires_in,
                }
                self.logger.debug(
                    f"账户 {account_name} access_token 已刷新，有效期 {expires_in} 秒"
                )
                return token
            except Exception as e:
                self.logger.error(f"账户 {account_name} 获取access_token异常: {e}")
                return None

    # ==================== API 调用 ====================

    def _format_mp_response(self, raw_response: Any) -> Dict:
        """格式化微信 API 响应为标准响应"""
        if not isinstance(raw_response, dict):
            return self.make_error(
                retcode=34000,
                message=f"API 返回了意外格式: {type(raw_response)}",
                raw=raw_response,
            )

        errcode = raw_response.get("errcode", 0)
        errmsg = raw_response.get("errmsg", "")

        if errcode == 0:
            resp = self.make_response(
                status="ok",
                retcode=0,
                data=raw_response,
                message_id=str(raw_response.get("msg_id", "")),
                message="",
                raw=raw_response,
            )
        else:
            resp = self.make_error(
                retcode=34000 + errcode,
                message=errmsg,
                raw=raw_response,
            )
            # message_id 始终存在
            resp["message_id"] = str(raw_response.get("msg_id", ""))

        resp["mp_raw"] = raw_response
        return resp

    async def call_api(
        self, endpoint: str, _account_id: Optional[str] = None, **params
    ):
        """调用微信 API，自动附加 access_token

        :param endpoint: API 端点（如 message/custom/send）
        :param _account_id: 账户名
        :param params: API 参数（不含 access_token）
        """
        account_name, account = self._resolve_account(_account_id)
        echo = params.pop("echo", None)

        # 非认证号：检查是否在被动回复上下文中
        if not account.verified and endpoint in (
            "message/custom/send",
            "message/template/send",
        ):
            passive_ctx = _passive_reply_ctx.get()
            if passive_ctx is not None:
                # 在 webhook 被动回复上下文中——拦截为被动回复
                passive_ctx["replied"] = True
                passive_ctx["params"] = params
                passive_ctx["done"].set()
                self.logger.debug(
                    f"账户 {account_name} 已截获为被动回复: "
                    f"msgtype={params.get('msgtype')}"
                )
                return {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"passive_reply": True},
                    "message_id": "",
                    "message": "passive_reply",
                }
            # 不在被动回复上下文中（如定时任务）→ 报错
            error_resp = self.make_error(
                retcode=34003,
                message=f"账户 {account_name} 未认证，无法使用客服消息主动推送，"
                f"请在配置中设置 verified=true（认证服务号）或改用被动回复",
                raw=None,
            )
            if echo:
                error_resp["echo"] = echo
            return error_resp

        access_token = await self._get_access_token(account_name)
        if not access_token:
            error_resp = self.make_error(
                retcode=34001,
                message=f"账户 {account_name} 无法获取access_token",
                raw=None,
            )
            if echo:
                error_resp["echo"] = echo
            return error_resp

        url = f"{MP_API_BASE}/{endpoint}?access_token={access_token}"

        try:
            self.logger.debug(f"微信公众号API请求: {endpoint}")
            resp = await client.post(url, json=params)
            raw_response = await resp.json()
            self.logger.debug(f"微信公众号API响应: {raw_response}")

            response = self._format_mp_response(raw_response)
            if echo:
                response["echo"] = echo
            return response

        except Exception as e:
            self.logger.error(f"调用微信公众号API失败: {e}")
            error_resp = self.make_error(
                retcode=33001,
                message=f"API调用失败: {e}",
                raw=None,
            )
            if echo:
                error_resp["echo"] = echo
            return error_resp

    async def _upload_media_for_send(
        self, file_val: Union[str, bytes], media_type: str, account_id: str
    ) -> Optional[str]:
        """上传临时素材，返回 media_id"""
        account_name, account = self._resolve_account(account_id)
        access_token = await self._get_access_token(account_name)
        if not access_token:
            return None

        # 读取文件内容
        if isinstance(file_val, bytes):
            file_bytes = file_val
            filename = f"upload.{media_type}"
        elif isinstance(file_val, str):
            if file_val.startswith(("http://", "https://")):
                try:
                    resp = await client.get(file_val)
                    file_bytes = await resp.read()
                    filename = file_val.rsplit("/", 1)[-1] or f"upload.{media_type}"
                except Exception as e:
                    self.logger.error(f"下载媒体文件失败: {e}")
                    return None
            else:
                try:
                    with open(file_val, "rb") as f:
                        file_bytes = f.read()
                    filename = file_val.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                except Exception as e:
                    self.logger.error(f"读取本地媒体文件失败: {e}")
                    return None
        else:
            return None

        url = (
            f"{MP_API_BASE}/media/upload?access_token={access_token}&type={media_type}"
        )

        try:
            import aiohttp

            form = aiohttp.FormData()
            form.add_field(
                "media",
                io.BytesIO(file_bytes),
                filename=filename,
                content_type="application/octet-stream",
            )
            resp = await client.post(url, data=form)
            data = await resp.json()

            if data.get("media_id"):
                return data["media_id"]
            self.logger.error(
                f"上传媒体失败: [{data.get('errcode')}] {data.get('errmsg')}"
            )
            return None
        except Exception as e:
            self.logger.error(f"上传媒体异常: {e}")
            return None

    # ==================== 签名验证与加解密 ====================

    @staticmethod
    def _verify_signature(
        token: str, timestamp: str, nonce: str, signature: str, encrypt: str = ""
    ) -> bool:
        """验证微信回调签名

        明文/兼容模式：sha1(sort([token, timestamp, nonce]))
        安全模式：sha1(sort([token, timestamp, nonce, encrypt]))
        """
        parts = (
            sorted([token, timestamp, nonce, encrypt])
            if encrypt
            else sorted([token, timestamp, nonce])
        )
        raw = "".join(parts)
        expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return expected == signature

    @staticmethod
    def _decrypt_message(encoding_aes_key: str, encrypt: str) -> Optional[str]:
        """解密安全模式下的消息（AES-256-CBC）

        :param encoding_aes_key: 43位加解密密钥
        :param encrypt: Base64编码的密文
        :return: 解密后的 XML 字符串
        """
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            raise ImportError("解密消息需要 cryptography 库，请确保已安装 dependencies")

        try:
            key = b64decode(encoding_aes_key + "=")
            iv = key[:16]
            cipher_text = b64decode(encrypt)

            cipher = Cipher(
                algorithms.AES(key), modes.CBC(iv), backend=default_backend()
            )
            decryptor = cipher.decryptor()
            plain = decryptor.update(cipher_text) + decryptor.finalize()

            # 去除 PKCS#7 填充
            pad_len = plain[-1]
            content = plain[:-pad_len]

            # 内容结构：16字节随机串 + 4字节大端消息长度 + 消息体 + AppID
            msg_len = struct.unpack("!I", content[16:20])[0]
            msg = content[20 : 20 + msg_len]
            return msg.decode("utf-8")
        except Exception as e:
            raise ValueError(f"消息解密失败: {e}")

    @staticmethod
    def _extract_encrypt(xml_str: str) -> Optional[str]:
        """从加密的回调 XML 中提取 Encrypt 字段"""
        try:
            root = ET.fromstring(xml_str)
            encrypt_node = root.find("Encrypt")
            if encrypt_node is not None and encrypt_node.text:
                return encrypt_node.text
        except ET.ParseError:
            pass
        return None

    # ==================== Webhook 路由 ====================

    def _make_verify_handler(self, account_name: str):
        """创建 GET 验证处理器（URL 接入验证）"""

        async def verify_handler(request):
            account = self.accounts.get(account_name)
            if not account:
                try:
                    from fastapi.responses import PlainTextResponse

                    return PlainTextResponse("account not found", status_code=404)
                except Exception:
                    return "account not found"

            signature = request.query_params.get("signature", "")
            timestamp = request.query_params.get("timestamp", "")
            nonce = request.query_params.get("nonce", "")
            echostr = request.query_params.get("echostr", "")

            if self._verify_signature(account.token, timestamp, nonce, signature):
                self.logger.info(f"账户 {account_name} 接入验证成功")
                try:
                    from fastapi.responses import PlainTextResponse

                    return PlainTextResponse(content=echostr)
                except Exception:
                    return echostr

            self.logger.warning(f"账户 {account_name} 接入验证签名失败")
            try:
                from fastapi.responses import PlainTextResponse

                return PlainTextResponse("signature error", status_code=403)
            except Exception:
                return "signature error"

        return verify_handler

    def _make_message_handler(self, account_name: str):
        """创建 POST 消息处理器（接收用户消息/事件）"""

        async def message_handler(request):
            account = self.accounts.get(account_name)
            if not account:
                return {"error": "account not found"}

            signature = request.query_params.get("signature", "")
            msg_signature = request.query_params.get("msg_signature", "")
            timestamp = request.query_params.get("timestamp", "")
            nonce = request.query_params.get("nonce", "")

            raw_body = await request.body()
            xml_str = raw_body.decode("utf-8", errors="ignore")

            # 检测是否为加密消息
            encrypt = self._extract_encrypt(xml_str)

            if encrypt:
                # 安全模式/兼容模式：需验证 msg_signature 并解密
                if not self._verify_signature(
                    account.token, timestamp, nonce, msg_signature, encrypt
                ):
                    self.logger.warning(
                        f"账户 {account_name} 消息签名验证失败(安全模式)"
                    )
                    return "success"
                if account.encoding_aes_key:
                    try:
                        xml_str = self._decrypt_message(
                            account.encoding_aes_key, encrypt
                        )
                    except Exception as e:
                        self.logger.error(f"账户 {account_name} 消息解密失败: {e}")
                        return "success"
                else:
                    self.logger.warning(
                        f"账户 {account_name} 收到加密消息但未配置encoding_aes_key"
                    )
                    return "success"
            else:
                # 明文模式：验证 signature
                if account.token and not self._verify_signature(
                    account.token, timestamp, nonce, signature
                ):
                    self.logger.warning(
                        f"账户 {account_name} 消息签名验证失败(明文模式)"
                    )
                    return "success"

            # 转换并 emit 事件
            converter = self._get_converter(account_name)
            onebot_event = converter.convert(xml_str) if xml_str else None
            if onebot_event:
                self.logger.debug(
                    f"账户 {account_name} 收到事件: "
                    f"{onebot_event.get('type')}/{onebot_event.get('mp_raw_type')}"
                )
                try:
                    from ErisPulse.Core import adapter as adapter_mgr

                    if account.verified:
                        # 认证号：异步 emit，后续通过客服消息主动推送
                        await adapter_mgr.emit(onebot_event)
                    else:
                        # 未认证号：被动回复——emit 后等待模块回复，5 秒内返回 XML
                        passive_done = asyncio.Event()
                        passive_ctx: dict = {
                            "replied": False,
                            "params": None,
                            "done": passive_done,
                        }
                        token = _passive_reply_ctx.set(passive_ctx)
                        try:
                            await adapter_mgr.emit(onebot_event)
                            # 等待模块处理器完成（contextvar 已透传到子 Task）
                            await asyncio.wait_for(
                                passive_done.wait(),
                                timeout=_PASSIVE_REPLY_TIMEOUT,
                            )
                            if passive_ctx["replied"] and passive_ctx["params"]:
                                openid = onebot_event.get("user_id", "")
                                original_id = onebot_event.get(
                                    "mp_to_user", account.appid
                                )
                                xml = self._build_passive_reply_xml(
                                    to_user=openid,
                                    from_user=original_id,
                                    params=passive_ctx["params"],
                                )
                                try:
                                    from fastapi.responses import Response

                                    return Response(
                                        content=xml,
                                        media_type="application/xml",
                                    )
                                except Exception:
                                    return "success"
                        except asyncio.TimeoutError:
                            self.logger.warning(f"账户 {account_name} 被动回复超时")
                        finally:
                            _passive_reply_ctx.reset(token)
                except Exception as e:
                    self.logger.error(f"emit事件失败: {e}")

            # 无被动回复时返回 success
            return "success"

        return message_handler

    def _get_converter(self, account_name: str) -> WechatMpConverter:
        """获取指定账户的转换器（懒加载）"""
        if account_name not in self._converters:
            account = self.accounts.get(account_name)
            self_id = account.appid if account else ""
            self._converters[account_name] = WechatMpConverter(self_id)
        return self._converters[account_name]

    @staticmethod
    def _build_passive_reply_xml(to_user: str, from_user: str, params: dict) -> str:
        """将客服消息 params 转为被动回复 XML"""
        timestamp = int(time.time())
        msgtype = params.get("msgtype", "text")

        if msgtype == "text":
            content = params.get("text", {}).get("content", "")
            if len(content) > _PASSIVE_TEXT_MAX_LEN:
                content = content[:_PASSIVE_TEXT_MAX_LEN] + "\n\n...（内容已截断）"
            return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""

        if msgtype == "image":
            media_id = params.get("image", {}).get("media_id", "")
            return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[image]]></MsgType>
<Image><MediaId><![CDATA[{media_id}]]></MediaId></Image>
</xml>"""

        if msgtype == "voice":
            media_id = params.get("voice", {}).get("media_id", "")
            return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[voice]]></MsgType>
<Voice><MediaId><![CDATA[{media_id}]]></MediaId></Voice>
</xml>"""

        if msgtype == "video":
            video = params.get("video", {})
            return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[video]]></MsgType>
<Video>
<Title><![CDATA[{video.get("title", "")}]]></Title>
<Description><![CDATA[{video.get("description", "")}]]></Description>
<MediaId><![CDATA[{video.get("media_id", "")}]]></MediaId>
</Video>
</xml>"""

        if msgtype == "music":
            music = params.get("music", {})
            return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[music]]></MsgType>
<Music>
<Title><![CDATA[{music.get("title", "")}]]></Title>
<Description><![CDATA[{music.get("description", "")}]]></Description>
<MusicUrl><![CDATA[{music.get("musicurl", "")}]]></MusicUrl>
<HQMusicUrl><![CDATA[{music.get("hqmusicurl", music.get("musicurl", ""))}]]></HQMusicUrl>
</Music>
</xml>"""

        # 兜底：纯文本提示
        return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[[不支持的回复类型: {msgtype}]]]></Content>
</xml>"""

    async def register_routes(self):
        """为每个已启用账户注册微信回调路由"""
        enabled = self.enabled_accounts
        if not enabled:
            self.logger.warning("没有配置任何启用的账户，将不会注册回调路由")
            return

        for account_name, account in enabled.items():
            # 统一使用适配器名作为命名空间前缀，路径取账户级部分
            module_id = self._get_config_key()
            path = account.callback_path or f"/{account_name}"

            # GET 接入验证 + POST 接收消息（同一路径，不同方法）
            verify_handler = self._make_verify_handler(account_name)
            message_handler = self._make_message_handler(account_name)
            try:
                router.register_http_route(
                    module_id, path, verify_handler, methods=["GET"]
                )
                router.register_http_route(
                    module_id, path, message_handler, methods=["POST"]
                )
            except Exception as e:
                self.logger.error(
                    f"注册账户 {account_name} 回调路由 {path} 失败: {str(e)}"
                )
                continue
            self._registered_routes.append((module_id, path))

            self.logger.info(
                f"已注册账户 {account_name} 的回调路由: {path} (GET验证 + POST消息)"
            )

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True

        await self.register_routes()

        for account_name, account in self.enabled_accounts.items():
            try:
                await self.emit_meta(
                    "connect",
                    account.appid,
                    user_name=account.appid,
                    nickname=f"公众号:{account_name}",
                )
                self.logger.info(f"账户 {account_name} (appid: {account.appid}) 已启动")
            except Exception as e:
                self.logger.warning(f"账户 {account_name} emit connect 失败: {e}")

        self.logger.info(
            f"微信公众号适配器启动完成，共 {len(self.enabled_accounts)} 个账户"
        )

    async def shutdown(self):
        self._running = False

        for module_id, path in self._registered_routes:
            try:
                router.unregister_http_route(module_id, path)
            except Exception:
                pass
        self._registered_routes.clear()

        for account_name, account in self.enabled_accounts.items():
            try:
                await self.emit_meta("disconnect", account.appid)
            except Exception:
                pass

        try:
            unregister_platform_event_methods("mp")
        except Exception:
            pass

        self.logger.info("微信公众号适配器已关闭")
