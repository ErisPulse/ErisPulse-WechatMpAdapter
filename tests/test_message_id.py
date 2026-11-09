"""基线协议回归：公众号消息事件顶层 message_id 必在"""

from WechatMpAdapter.Converter import WechatMpConverter

TEXT_XML = """<xml>
<ToUserName><![CDATA[gh_test]]></ToUserName>
<FromUserName><![CDATA[o_user1]]></FromUserName>
<CreateTime>1700000000</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[hello]]></Content>
<MsgId>123456789</MsgId>
</xml>"""


def test_message_event_has_message_id():
    conv = WechatMpConverter("gh_test")
    event = conv.convert(TEXT_XML)
    assert event is not None
    assert event["message_id"] == "123456789"
    assert event["id"]
