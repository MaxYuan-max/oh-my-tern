# oh-my-tern

Tern plugins, each one a directory with its own `plugin.toml`.

## Plugins

| Plugin | What it does |
| --- | --- |
| [hostping](./hostping) | **Host Latency** — the focused session's native Tern connection RTT in the widget bar, with optional graph, bar and ring styles and a hover card |

## Install

Requires Tern 0.9.27 or newer (`tern --version`).

```sh
tern plugin install github.com/MaxYuan-max/oh-my-tern/hostping
```

To work on a plugin in place instead:

```sh
tern plugin link ~/src/oh-my-tern/hostping
```

Then run **Edit widgets…** from the palette to place it in the widget bar.
