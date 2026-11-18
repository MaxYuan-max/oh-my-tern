--!strict
-- Read the RTT Tern already measures on each remote connection. Session
-- switches select the corresponding host's current reading and history;
-- no additional probes, address resolution or child processes are needed.

local INTERVAL_MS = 10000 -- refresh the existing connection estimates
local HISTORY = 60 -- samples kept for the optional graph (~10 minutes)
local MAX_MS = 300 -- latency considered "full" on the bar/ring/graph scale

type Host = {
	name: string,
	addr: string?,
	state: string,
	rtt_ms: number?,
	samples: { number },
}

-- Host ids stay stable across session switches and host renames. Pane ids
-- come from Tern unchanged, including remote panes' opaque ids.
local hosts: { [number]: Host } = {}
local pane_hosts: { [number]: Host } = {}

local function push_sample(host: Host, rtt_ms: number)
	local norm = math.min(rtt_ms / MAX_MS, 1)
	table.insert(host.samples, norm)
	while #host.samples > HISTORY do
		table.remove(host.samples, 1)
	end
end

local function sync_hosts(cx: WindowCx)
	local seen: { [number]: boolean } = {}
	table.clear(pane_hosts)
	for _, info in cx.hosts:list() do
		if info.kind ~= "remote" then
			continue
		end
		local name = info.name
		local addr = info.address
		seen[info.id] = true
		local host = hosts[info.id]
		if host == nil or host.addr ~= addr then
			host = {
				name = name,
				addr = addr,
				state = info.state,
				rtt_ms = nil,
				samples = {},
			}
			hosts[info.id] = host
		end
		-- Renaming a host keeps its samples: only the address identifies it.
		host.name = name
		host.state = info.state
		host.rtt_ms = info.rtt_ms
		for _, pane in info.panes do
			pane_hosts[pane] = host
		end
	end
	for id in hosts do
		if not seen[id] then
			hosts[id] = nil
		end
	end
end

-- One refresh timer per window; keep every attached host's history even
-- while another session is selected. Missing estimates are not timeouts.
local function tick(cx: WindowCx)
	sync_hosts(cx)
	for _, host in hosts do
		if host.state == "connected" and host.rtt_ms ~= nil then
			push_sample(host, host.rtt_ms)
		end
	end
	tern.chrome.refresh()
	tern.timer(INTERVAL_MS, function(later_cx)
		if later_cx ~= nil then
			tick(later_cx)
		end
	end)
end

-- window_start is not resent after reload; bootstrap each VM independently.
tern.timer(0, function(cx)
	if cx ~= nil then
		tick(cx)
	end
end)

local function refresh_view(_ev: any, cx: WindowCx)
	sync_hosts(cx)
	tern.chrome.refresh()
end

tern.on("focus", refresh_view)
tern.on("pane_created", refresh_view)
tern.on("pane_closed", refresh_view)


tern.chrome.widget("latency", {
	name = "Host Latency",
	description = "The selected session's native Tern connection RTT",
	icon = "gauge",
	styles = { "text", "graph", "bar", "ring" },
	render = function(pane)
		-- Resolve directly from the formatter's focused pane, not a mutable
		-- "current host" left over from the previous session's focus event.
		local host = if pane ~= nil then pane_hosts[pane.pane] else nil
		if host == nil then
			return nil
		end
		local text: string
		local tone: string
		if host.state ~= "connected" then
			text = host.state
			tone = "warn"
		elseif host.rtt_ms ~= nil then
			text = tostring(math.floor(host.rtt_ms + 0.5)) .. "ms"
			tone = if host.rtt_ms < 80 then "success" elseif host.rtt_ms < 200 then "warn" else "error"
		else
			text = "…"
			tone = "muted"
		end
		-- The hover card (Tern 0.9.27): labeled rows instead of `title`'s plain
		-- lines, ending in a Refresh button. Row tones use ok/warn/error/
		-- muted, one word off the view's own "success".
		local rtt_tone = if tone == "success" then "ok" else tone
		local card: { any } = {
			{ id = "host", label = "Host", text = host.name },
		}
		if host.state ~= "connected" then
			table.insert(card, { id = "state", label = "State", text = host.state, tone = "warn" })
		elseif host.rtt_ms ~= nil then
			table.insert(card, { id = "rtt", label = "RTT", text = text, tone = rtt_tone })
		else
			table.insert(card, { id = "rtt", label = "RTT", text = "measuring…", tone = "muted" })
		end
		table.insert(card, {
			id = "refresh",
			text = "Tern RTT · 10 s refresh",
			tone = "muted",
			actions = { { id = "refresh", label = "Refresh" } },
		})
		return {
			text = text,
			tone = tone,
			card = card,
			value = if host.rtt_ms ~= nil then math.min(host.rtt_ms / MAX_MS, 1) else nil,
			series = host.samples,
		}
	end,
})

-- The card's Refresh button re-reads the hosts now instead of waiting for the
-- next 10 s tick. Returning true takes the click; nil would toast instead.
tern.on("widget_action", function(ev: any, cx: WindowCx): boolean?
	if ev.widget == "latency" and ev.row == "refresh" and ev.act == "refresh" then
		sync_hosts(cx)
		tern.chrome.refresh()
		return true
	end
	return nil
end)
