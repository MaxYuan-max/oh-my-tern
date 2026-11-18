# Host Latency (`hostping`)

A Tern widget that shows the round-trip time Tern already measures for the
remote host of the focused session. Switching sessions or tabs switches the
reading immediately; a 10 s timer keeps it current and feeds an optional
history graph.

It adds no probes of its own: no ICMP, no address resolution, no child
processes. It only reads the connection estimates Tern keeps for each host.

## Install

Requires Tern 0.9.27 or newer.

```sh
tern plugin install github.com/MaxYuan-max/oh-my-tern/hostping
```

or, from a checkout:

```sh
tern plugin link path/to/hostping
```

Then run **Edit widgets…** from the palette and add **Host Latency**. In the
widget's editor you can pick how it draws:

- `text` — `42ms` (the default)
- `bar` / `ring` — filled against 300 ms as "full"
- `graph` — the last 10 minutes of samples as a sparkline

## Hover card

Hovering the widget shows the host's name, its RTT (or the connection state
while it is not connected) and a **Refresh** button that re-reads the
estimates now instead of waiting for the next tick.

The card needs Tern 0.9.27 or newer; older versions show the widget without
it.

## Reading

- Only remote hosts: a local session shows nothing.
- The tone follows the reading: under 80 ms is green, under 200 ms yellow,
  slower is red.
- A missing estimate means "not measured yet", not a timeout.
