# agent-hud（Agents 面板）

复刻 Tern 原生 Agents HUD 的插件：以右上角悬浮卡列出各窗格里的 agent（本窗口与其它会话），显示状态、已运行时长与位置（Here / Tab N），点击行跳转到该 agent，底部可直接新建 agent。

## 安装

```sh
tern plugin install github.com/MaxYuan-max/oh-my-tern/agent-hud
```

## 使用

- 命令面板 → `Agents 面板`（动作 `plugin.agent-hud.toggle`）：打开 / 关闭悬浮卡。
- 小组件栏里的 `Agents 面板` 小组件：显示运行中的 agent 数，点击同样打开 / 关闭卡片。
- 建议绑个快捷键：`cx.settings:bind("ctrl+shift+a", "plugin.agent-hud.toggle")`（在 Carly 里执行即可）。
- 卡片关闭：再次运行命令、Esc / 卡片头上的关闭键。

## 效果对照（与原生 HUD）

| 原生 | 本插件 |
|---|---|
| Working 计数、分组行、进度条 | 有（Working / Waiting on you / Idle，进度条 = working/total） |
| 标题 + 子标题、已运行时长 | 有（时长 = `pane.running.ms`；窗格不报时长则为首次观察到该 agent 至今，用 `kv.json` 记住） |
| Here / Tab N 位置 | 有（同 tab 显示 Here，否则 Tab 序号） |
| 点击 agent → 跳转 | 有（`layout:focus`，跨会话的聚焦行为以当前 Tern 实现为准） |
| + New agent | 有（`cx.agents:start`，cwd 取聚焦窗格，Esc 或点外部取消） |
| 玻璃质感的原生浮窗、π 原生图标 | 做不到；卡片为插件 canvas，π 为字形文本 |

## 实现要点

- 数据：`cx.agents:list()`（跨会话，含 `state`/`title`/`program`/`cwd`/`tab`）+ `cx.session:panes()/tabs()`。
- 形态：`cx.canvas:open(beside)` 后 `cx.layout:float(pane, anchor, "tr", { size = { 0.32, 0.4 }, focus = true })` 浮成右上角 PiP。
  Glimpse 形态的 canvas 对点击穿透（验证过），不能用。
- 行点击：行是 `el("div", { actions = { click = "go=<pane>" } })`，经 `canvas_action` 事件分发，`go=<pane>` 触发 `layout:focus(pane)` 并关闭卡片。
- 刷新：`agent_state` / `agent_finished` / `pane_*` / `focus` 事件 + 2 s 定时器重建 view，`tern.chrome.refresh()` 同步小组件。
- 空状态：没有运行中的 agent 时显示"没有运行中的 agent"，底部按钮可直接新建。
- 命令面板：`Agents 面板` 命令默认 `available = false`，不占面板行；需要时在 `settings.json` 的 `keybinds` 绑定 `plugin.agent-hud.toggle`。
