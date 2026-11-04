"""终端MCP输入契约测试：恶意类型不退出进程，无效执行期限不得向终端管理器提交指令。"""
import unittest
from unittest.mock import Mock
from terminalConnection.tools.TerminalMcp import TerminalMcp


class TerminalMcpTests(unittest.TestCase):
    """验证MCP适配器边界，不连接任何真实API。"""

    def test_malformed_requests_and_invalid_timeout(self):
        """无参数；非对象报文返回协议错误，非法超时返回工具错误且不提交请求。"""
        tool = TerminalMcp({"adminURL": "http://127.0.0.1:1", "adminToken": "test"})
        tool.client = Mock()
        self.assertEqual(tool.handle_message([])["error"]["code"], -32600)
        for timeout in (0, 121, True, "30"):
            result = tool.handle_message({"id": 1, "method": "tools/call",
                "params": {"name": "remote_cmd_run", "arguments": {"target": "test", "command": "ver", "timeoutSec": timeout}}})
            self.assertTrue(result["result"]["isError"])
        tool.client.execute_command.assert_not_called()
        self.assertEqual(len(tool.handle_message({"id": 2, "method": "tools/list"})["result"]["tools"]), 5)
