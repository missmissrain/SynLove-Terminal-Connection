"""完成通知正文清理：仅删除收件探测问句，保留真实结果及需要用户决策的问题；无注册参数。"""
import re


class NoticeText:
    """使用确定性文本规则，避免为通知清理额外调用模型。"""

    @staticmethod
    def clean_text(text):
        """text为最终摘要；去掉“能收到消息吗”类完整问句，不删除普通任务决策。"""
        kept = []
        for part in re.split(r"(?<=[。！？!?；;\n])", str(text or "")):
            probe = re.search(r"能(?:否|不能)?收到|收得到|收到.{0,24}(?:消息|通知|结果)|"
                r"(?:消息|通知|结果).{0,12}收到|(?:QQ|微信).{0,16}收到", part)
            question = re.search(r"[？?]|(?:吗|没|没有)[。！!\s]*$", part)
            if not (probe and question):
                kept.append(part)
        return "".join(kept).strip()
