# Tern 屏幕提取（hints）

类似 kitty `kitten hints` 的键盘提取器：从当前聚焦 pane 的屏幕和历史输出里提取 **路径**、**链接** 和 **Session 恢复命令**（`pi --session`、`claude --resume`、`codex resume`），按屏幕从下往上（最近出现优先）去重列出，Enter 复制到剪贴板。

## 入口与快捷键

- 命令面板：**提取屏幕内容（路径／链接／Resume）**（action：`plugin.hints.pick`）
- 默认快捷键：`ctrl+shift+h`（可在 `settings.json` 的 `keybinds` 中覆盖）
- Carly 导出：`extract()`

## 提取规则

1. **Session 恢复命令（Resume）**
   - `pi --session <id|path>`
   - `claude --resume <id|"name">`
   - `codex resume <id>`（及 `codex --resume <id>`）
   - 自动剥离末尾标点与多余空白，保留引号包裹的会话名。
2. **链接（Link）**
   - `https://`、`http://`、`ssh://`、`file://`、`ws://`、`wss://`、`ftp://`
   - 自动停在中文字符、全角标点、引号或表格边框前，并按括号配对规则剥离外层 `)`、`]`、`}` 及句末标点。
   - 链接内部的 `/path` 不会重复作为文件路径提取。
3. **路径（Path）**
   - 绝对与显式路径：`/path/...`、`~/...`、`./...`、`../...`
   - 相对路径：含扩展名的 `dir/file.ext`、以 `.` 开头的隐藏路径（如 `.pi/agent/AGENTS.md`）、或 `src/`、`tests/`、`dist/` 等目录路径
   - 独立文件名：带常见代码／配置／日志／媒体扩展名的文件名（如 `window.luau`、`settings.json`、`tern.log`）
   - 自动保留编译器与 `grep -n` 的 `:line` 或 `:line:col` 位置后缀（如 `window.luau:437:10`），剥离 `:line:代码内容` 里的代码部分；过滤 `/council` 等斜杠命令、`37/40` 等表格分数、`10.0.0.9:8376` 等 IP:端口以及 `example.com` 等域名。

## 安装与测试

```sh
tern plugin install github.com/MaxYuan-max/oh-my-tern/hints
/opt/homebrew/bin/luajit tests/test_extract.lua
```
