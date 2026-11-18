# Tern 智能分屏（smart-split）

`ctrl+shift+enter`（可在 `settings.json` 的 `keybinds` 里覆盖）把聚焦窗格一分为二，并自动选择方向。

## 规则

- 窗格仍有至少 **46 行**文本时优先水平分割（`split_down`），两边都保持可用高度。
- 低于 46 行时按较长的像素边决定方向，避免分完一侧只剩几行。
- 行数取 `PaneInfo` 的 `rows`；没有时按窗格像素高度和字号估算（约 1.175 × 字号）。
- 通过本机 daemon 执行原生分屏命令，保留 Tern 的主机、工作目录和新块偏好；远端 pane 也分在远端。

## 安装

```sh
tern plugin install github.com/MaxYuan-max/oh-my-tern/smart-split
```

然后在 `settings.json` 绑定按键：

```json
{ "keybinds": { "ctrl+shift+enter": "plugin.smart-split.split" } }
```
