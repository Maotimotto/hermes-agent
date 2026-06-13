# bridge/ — Claude Agent SDK Runner

## 为什么没有 claude_sdk_runner.js

claude-agent-sdk 是 **纯 Python** 包（`pip install claude-agent-sdk`）：

- SDK 内部用 `ClaudeSDKClient` 类管理 `claude` ELF 子进程（stdio 通信）
- Python 代码 `client.query()` → `client.receive_response()` 直接拿到强类型消息
- 不需要 Node.js wrapper 脚本

与 Codex Runtime 的区别：
- Codex App Server 用 `@anthropic-ai/codex`（Node.js 包），需要 `codex_app_server.py` JSON-RPC 客户端
- Claude Agent SDK 是 Python 原生，直接在 `ClaudeAgentSdkRuntime` 中 import 使用

## 子进程通信链路

```
ClaudeAgentSdkRuntime (Python)
  → ClaudeSDKClient (Python, claude-agent-sdk 包)
    → claude ELF binary (239MB, _bundled/claude)
      → Anthropic API / 中转商 API
```

认证通过 `ClaudeAgentOptions.env` 注入到 `claude` 子进程的环境变量。
