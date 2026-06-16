import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional


class WechatMpConverter:
    """
    微信公众号事件转换器

    将微信公众号回调的 XML 数据转换为 OneBot12 标准事件。

    核心原则：
    1. 严格兼容：标准字段完全遵循 OneBot12 规范
    2. 明确扩展：平台特有功能添加 mp_ 前缀
    3. 数据完整：原始 XML 保留在 mp_raw 字段中
    4. 时间统一：CreateTime 转为 10 位 Unix 时间戳（秒级）
    """

    def __init__(self, self_id: str):
        self.platform = "mp"
        self.self_id = self_id or ""

    def convert(self, raw_xml: str) -> Optional[Dict]:
        """
        将微信公众号原始 XML 转换为 OneBot12 标准格式事件

        :param raw_xml: 微信回调的原始 XML 字符串
        :return: OneBot12 标准格式事件，解析失败返回 None
        """
        if not raw_xml:
            return None

        data = self._xml_to_dict(raw_xml)
        if not data:
            return None

        msg_type = data.get("MsgType", "")
        from_user = data.get("FromUserName", "")
        to_user = data.get("ToUserName", "")
        create_time = self._safe_int(data.get("CreateTime"), int(time.time()))
        msg_id = data.get("MsgId", "")

        if msg_type == "event":
            return self._convert_event(data, from_user, to_user, create_time)
        else:
            return self._convert_message(
                data, msg_type, from_user, to_user, create_time, msg_id
            )

    # ==================== 基础事件构建 ====================

    def _create_base_event(
        self,
        event_type: str,
        raw_xml: str,
        raw_data: Dict,
        from_user: str,
        to_user: str,
        create_time: int,
        msg_id: str = "",
        mp_event: str = "",
    ) -> Dict:
        """创建基础事件结构

        公众号场景下所有会话均为私聊（用户与公众号 1v1），
        因此 detail_type 统一为 private。
        """
        event_id = str(msg_id) if msg_id else f"{create_time}_{from_user}"
        return {
            "id": event_id,
            "time": create_time,
            "type": event_type,
            "detail_type": "private",
            "platform": self.platform,
            "self": {
                "platform": self.platform,
                "user_id": self.self_id,
            },
            "user_id": from_user,
            "mp_raw": raw_xml,
            "mp_raw_type": raw_data.get("MsgType", ""),
            "mp_msg_id": str(msg_id) if msg_id else "",
            "mp_event": mp_event,
            "mp_to_user": to_user,
            "mp_from_user": from_user,
            "mp_data": raw_data,
        }

    # ==================== 消息事件 ====================

    def _convert_message(
        self,
        data: Dict,
        msg_type: str,
        from_user: str,
        to_user: str,
        create_time: int,
        msg_id: str,
    ) -> Dict:
        """处理消息事件"""
        event = self._create_base_event(
            event_type="message",
            raw_xml=data.get("_raw_xml", ""),
            raw_data=data,
            from_user=from_user,
            to_user=to_user,
            create_time=create_time,
            msg_id=msg_id,
        )

        segments = self._parse_message_content(data, msg_type)
        event["message"] = segments
        event["alt_message"] = self._generate_alt_message(segments)

        return event

    def _parse_message_content(self, data: Dict, msg_type: str) -> List[Dict]:
        """解析消息内容为 OneBot12 消息段列表"""
        segments: List[Dict] = []

        if msg_type == "text":
            segments.append(
                {
                    "type": "text",
                    "data": {"text": data.get("Content", "")},
                }
            )

        elif msg_type == "image":
            segments.append(
                {
                    "type": "image",
                    "data": {
                        "file": data.get("PicUrl", ""),
                        "file_id": data.get("MediaId", ""),
                    },
                }
            )

        elif msg_type == "voice":
            seg_data = {
                "file": data.get("MediaId", ""),
                "file_id": data.get("MediaId", ""),
            }
            recognition = data.get("Recognition", "")
            if recognition:
                seg_data["text"] = recognition
            segments.append({"type": "voice", "data": seg_data})

        elif msg_type == "video":
            segments.append(
                {
                    "type": "video",
                    "data": {
                        "file": data.get("MediaId", ""),
                        "file_id": data.get("MediaId", ""),
                        "thumbnail": data.get("ThumbMediaId", ""),
                    },
                }
            )

        elif msg_type == "shortvideo":
            segments.append(
                {
                    "type": "video",
                    "data": {
                        "file": data.get("MediaId", ""),
                        "file_id": data.get("MediaId", ""),
                        "thumbnail": data.get("ThumbMediaId", ""),
                        "mp_shortvideo": True,
                    },
                }
            )

        elif msg_type == "location":
            segments.append(
                {
                    "type": "location",
                    "data": {
                        "latitude": self._safe_float(data.get("Location_X"), 0.0),
                        "longitude": self._safe_float(data.get("Location_Y"), 0.0),
                        "scale": self._safe_float(data.get("Scale"), 0.0),
                        "title": data.get("Label", ""),
                    },
                }
            )

        elif msg_type == "link":
            title = data.get("Title", "")
            description = data.get("Description", "")
            url = data.get("Url", "")
            text = f"{title}\n{description}\n{url}" if title or description else url
            segments.append(
                {
                    "type": "text",
                    "data": {"text": text},
                }
            )

        else:
            segments.append(
                {
                    "type": "text",
                    "data": {"text": f"[不支持的消息类型: {msg_type}]"},
                }
            )

        return segments

    # ==================== 事件通知 ====================

    def _convert_event(
        self,
        data: Dict,
        from_user: str,
        to_user: str,
        create_time: int,
    ) -> Dict:
        """处理事件（Event）通知

        公众号事件包括：subscribe/unsubscribe/SCAN/LOCATION/CLICK/VIEW/
        TEMPLATESENDJOBFINISH/MASSSENDJOBFINISH 等。
        """
        event_key = data.get("Event", "")
        event_key_lower = event_key.lower() if event_key else ""

        event = self._create_base_event(
            event_type="notice",
            raw_xml=data.get("_raw_xml", ""),
            raw_data=data,
            from_user=from_user,
            to_user=to_user,
            create_time=create_time,
            msg_id="",
            mp_event=event_key,
        )

        if event_key_lower in ("subscribe", "unsubscribe", "scan"):
            event["mp_event"] = event_key_lower
            event["mp_event_key"] = data.get("EventKey", "")
            ticket = data.get("Ticket", "")
            if ticket:
                event["mp_ticket"] = ticket

        elif event_key_lower == "location":
            # 上报地理位置事件
            event["mp_event"] = "location_report"
            event["latitude"] = self._safe_float(data.get("Latitude"), 0.0)
            event["longitude"] = self._safe_float(data.get("Longitude"), 0.0)
            event["precision"] = self._safe_float(data.get("Precision"), 0.0)

        elif event_key_lower == "click":
            # 自定义菜单点击事件
            event["mp_event"] = "menu_click"
            event["mp_event_key"] = data.get("EventKey", "")

        elif event_key_lower == "view":
            # 菜单跳转事件
            event["mp_event"] = "menu_view"
            event["mp_event_key"] = data.get("EventKey", "")

        elif event_key_lower == "templatesendjobfinish":
            event["mp_event"] = "template_send_finish"
            event["mp_msg_id"] = data.get("MsgID", "")
            event["mp_status"] = data.get("Status", "")

        elif event_key_lower == "masssendjobfinish":
            event["mp_event"] = "mass_send_finish"
            event["mp_msg_id"] = data.get("MsgID", "")
            event["mp_status"] = data.get("Status", "")
            event["mp_total_count"] = self._safe_int(data.get("TotalCount"), 0)
            event["mp_filter_count"] = self._safe_int(data.get("FilterCount"), 0)
            event["mp_sent_count"] = self._safe_int(data.get("SentCount"), 0)
            event["mp_error_count"] = self._safe_int(data.get("ErrorCount"), 0)

        else:
            event["mp_event"] = event_key_lower
            event["mp_event_key"] = data.get("EventKey", "")

        return event

    # ==================== 工具方法 ====================

    def _generate_alt_message(self, segments: List[Dict]) -> str:
        """根据消息段生成纯文本备用内容"""
        parts = []
        for seg in segments:
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {})
            if seg_type == "text":
                parts.append(seg_data.get("text", ""))
            elif seg_type == "image":
                parts.append("[图片]")
            elif seg_type == "voice":
                text = seg_data.get("text", "")
                parts.append(text if text else "[语音]")
            elif seg_type == "video":
                parts.append(
                    "[视频]" if not seg_data.get("mp_shortvideo") else "[小视频]"
                )
            elif seg_type == "location":
                parts.append(f"[位置] {seg_data.get('title', '')}".strip())
            else:
                parts.append(f"[{seg_type}]")
        return "".join(parts) if parts else ""

    def _xml_to_dict(self, raw_xml: str) -> Optional[Dict]:
        """解析 XML 为字典

        同时在返回的字典中附加 _raw_xml 保留原始字符串。
        """
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            return None

        result: Dict[str, str] = {}
        for child in root:
            result[child.tag] = child.text or ""
        result["_raw_xml"] = raw_xml
        return result

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
