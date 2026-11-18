# oh-my-tern

Tern plugins, each one a directory with its own `plugin.toml`.

## Plugins

| Plugin | What it does |
| --- | --- |
| [hostping](./hostping) | **Host Latency** — the focused session's native Tern connection RTT in the widget bar, with optional graph, bar and ring styles and a hover card |
| [connection-status](./connection-status) | How each remote pane is connected (SSH-framed UDP tunnel, direct UDP or relay) with live RTT, plus a memory widget and card that follow the native one. Hosts come from a local `hosts.json` |
| [agent-hud](./agent-hud) | A floating **Agents** panel like Tern's native HUD: state, elapsed time and location for every agent, with a row click that focuses its pane and a New agent button |
| [hints](./hints) | Extract paths, links and resume commands (`pi --session`, `claude --resume`, `codex resume`) from the focused pane, newest first |
| [smart-split](./smart-split) | Split the focused pane, preferring a horizontal split while the pane still has at least 46 usable rows |

## Install

Requires Tern 0.9.27 or newer (`tern --version`).

```sh
tern plugin install github.com/MaxYuan-max/oh-my-tern/hostping
tern plugin install github.com/MaxYuan-max/oh-my-tern/connection-status
```

To work on a plugin in place instead:

```sh
tern plugin link ~/src/oh-my-tern/hostping
```

Then run **Edit widgets…** from the palette to place a widget in the widget bar.

`connection-status` keeps its host list out of the repository: copy
`connection-status/hosts.example.json` to
`~/Library/Application Support/Tern/plugin-data/connection-status/hosts.json`
and fill in your own SSH destinations and tunnels. See its README for the
field reference.
