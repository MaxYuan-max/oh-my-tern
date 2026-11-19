--!strict
local POLL_MS = 5000
local STALE_MS = 15000
local RTT_HISTORY = 60
local RTT_INTERVAL_MS = 10000
local RTT_MAX_MS = 300
-- Host names, addresses, tunnel summaries and endpoints belong to this installation, not to the plugin:
-- they live in plugin-data/connection-status/hosts.json (see hosts.example.json). An empty or missing file
-- leaves the plugin with no remote hosts rather than guessing one.
local HOST_CONFIG_PATH = (function(): string
	local configured = tern.getenv("CONNECTION_STATUS_CONFIG")
	if type(configured) == "string" and configured ~= "" then
		return configured
	end
	return tern.plugin.data .. "/hosts.json"
end)()
local hostConfig: any = nil
local hostConfigError: string? = nil
do
	if tern.fs.exists(HOST_CONFIG_PATH) then
		local ok, decoded = pcall(function()
			return tern.json.decode(tern.fs.read(HOST_CONFIG_PATH, 1048576))
		end)
		if ok and type(decoded) == "table" and type(decoded.hosts) == "table" then
			hostConfig = decoded
		elseif ok then
			hostConfigError = "hosts.json 缺少 hosts 数组"
		else
			hostConfigError = tostring(decoded)
		end
	end
end

-- Local SSH-framed UDP tunnels by loopback port, and what each reaches.
local SSH_TUNNELS: { [string]: string } = {}
-- Named tunnel entries, so the picker's loopback addresses never have to be typed or remembered.
-- `fallback`: a second pinned address of the same tunnel, dialed when the named entry itself is on the relay.
local TUNNEL_HOSTS: { any } = {}
if hostConfig then
	for _, entry in hostConfig.hosts do
		if type(entry) == "table" and type(entry.name) == "string" and entry.name ~= ""
			and type(entry.address) == "string" and entry.address ~= "" then
			local port = tostring(entry.port or "")
			local tunnel = if type(entry.tunnel) == "table" then entry.tunnel else nil
			table.insert(TUNNEL_HOSTS, {
				name = entry.name,
				address = entry.address,
				fallback = if type(entry.fallback) == "string" and entry.fallback ~= "" then entry.fallback else nil,
				alias = if type(entry.alias) == "string" then entry.alias else "",
				port = port,
				endpoint = if type(entry.endpoint) == "string" then entry.endpoint else "",
				detail = if type(entry.detail) == "string" and entry.detail ~= "" then entry.detail
					else "SSH 隧道 → " .. entry.name,
				tunnel = tunnel,
			})
			if port ~= "" then
				SSH_TUNNELS[port] = if tunnel and type(tunnel.summary) == "string" and tunnel.summary ~= ""
					then tunnel.summary
					else "本地 UDP " .. port .. " 经 SSH/TCP 转发；不是原生 UDP 直连。"
			end
		end
	end
end

-- The same machine may already be attached by tunnel name, by 127.0.0.1:port, or by endpoint ID.
local function sameTarget(address: string, target: any): boolean
	if address == target.address then
		return true
	end
	if target.endpoint ~= "" and #address > #target.endpoint and string.sub(address, -#target.endpoint) == target.endpoint then
		return true
	end
	local hostPart, port = string.match(address, "([^@]+):(%d+)$")
	return target.port ~= "" and port == target.port and (hostPart == "127.0.0.1" or (target.alias ~= "" and hostPart == target.alias))
end

local PROBE = tern.plugin.dir .. "/path_probe.py"
local MEMORY_PROBE = tern.plugin.dir .. "/memory_probe.py"
local hosts: { any } = {}
local paneHosts: { [number]: any } = {}
local labelCounts: { [string]: number } = {}
-- Latest log reading: { checked, labels, log } or { checked, reason }; cursor/labels: the last successful one.
local reading: any = nil
local cursor: any = nil
local labels: any = nil
local probing = false
local attempted = -math.huge
local memoryReadings: { [string]: any } = {}
local memoryProcessesExpanded: { [string]: boolean } = {}
local memoryBusy: { [string]: boolean } = {}
local memoryAttempted: { [string]: number } = {}
local refreshMemory: (any) -> ()

type RttRecord = { address: string?, samples: { number }, at: number }
local rttSamples: { [string]: RttRecord } = {}

-- Host ids stay stable across session switches; a changed address starts a new
-- history. Pane ids come from Tern unchanged, including remote panes' opaque ids.
local function rttKey(host: any): string?
	local id = host.id
	if type(id) == "number" then
		return "id:" .. tostring(id)
	end
	if type(host.address) == "string" and host.address ~= "" then
		return "addr:" .. host.address
	end
	return nil
end

local stateNames = {
	connecting = "连接中",
	reconnecting = "重连中",
	stalled = "连接停滞",
	verify = "待验证",
	failed = "连接失败",
}

-- Tern logs a remote host on thread `tern-remote-<label>`: an endpoint ID's first 10 hex digits and "…",
-- otherwise the address's host part.
local function logLabel(host: any): string?
	local address = host.address or ""
	local target = string.match(address, "@(.+)$") or address
	if #target == 64 and string.match(target, "^[0-9a-f]+$") then
		return string.sub(target, 1, 10) .. "…"
	end
	local name = string.match(target, "^%[(.+)%]:%d+$") or string.match(target, "^(.+):%d+$") or target
	return if name ~= "" then name else nil
end

local function updateHosts(cx: any)
	hosts = cx.hosts:list()
	paneHosts = {}
	labelCounts = {}
	local seen: { [string]: boolean } = {}
	for _, host in hosts do
		for _, pane in host.panes do
			paneHosts[pane] = host
		end
		local label = if host.kind == "remote" and host.state == "connected" then logLabel(host) else nil
		if label then
			labelCounts[label] = (labelCounts[label] or 0) + 1
		end
		if host.kind == "remote" then
			local key = rttKey(host)
			if key then
				seen[key] = true
				local record = rttSamples[key]
				if not record or record.address ~= host.address then
					rttSamples[key] = { address = host.address, samples = {}, at = 0 }
				end
			end
		end
	end
	for key in rttSamples do
		if not seen[key] then
			rttSamples[key] = nil
		end
	end
end

-- Keep one point per host every 10 s, independent of the 5 s refresh timer.
-- Missing estimates stay gaps; they are not zero-RTT samples.
local function sampleRtt(now: number)
	for _, host in hosts do
		if host.kind == "remote" and host.state == "connected" and type(host.rtt_ms) == "number" then
			local key = rttKey(host)
			local record = if key then rttSamples[key] else nil
			if record and (#record.samples == 0 or now - record.at >= RTT_INTERVAL_MS) then
				table.insert(record.samples, math.clamp(host.rtt_ms / RTT_MAX_MS, 0, 1))
				if #record.samples > RTT_HISTORY then
					table.remove(record.samples, 1)
				end
				record.at = now
			end
		end
	end
end

-- Tern's live RTT of the host's connection.
local function latency(host: any): string
	if host.kind == "remote" and host.state == "connected" and host.rtt_ms then
		return string.format("%.0f ms", host.rtt_ms)
	end
	return "延迟未知"
end

local function unknown(result: any, reason: string): any
	result.mode, result.label, result.tone, result.reason = "unknown", "路径未知", "warn", reason
	return result
end

local function view(host: any): any
	local result: any = { host = host.name, state = host.state, address = host.address, rtt_ms = host.rtt_ms }
	if host.kind == "local" then
		result.mode, result.label, result.tone = "local", "本机", "muted"
		result.reason = "该 pane 属于本机；其中手动运行的 ssh 不属于 Tern remote 连接。"
		return result
	end
	if host.state ~= "connected" then
		result.mode, result.label = "unknown", stateNames[host.state] or "未连接"
		result.tone = if host.state == "failed" then "error" else "warn"
		result.reason = if host.detail then host.detail.error else "连接尚未就绪"
		return result
	end
	local label = logLabel(host)
	if not label then
		return unknown(result, "主机地址无法对应 Tern 日志线程")
	end
	if (labelCounts[label] or 0) > 1 then
		return unknown(result, "多个已连接主机共用日志线程 tern-remote-" .. label .. "，无法区分")
	end
	if not reading then
		return unknown(result, "尚未读取 Tern 日志")
	end
	result.checked_age_ms = math.max(0, tern.now() - reading.checked)
	if result.checked_age_ms > STALE_MS then
		return unknown(result, "日志证据已过期，等待刷新")
	end
	if reading.reason then
		return unknown(result, reading.reason)
	end
	local way = reading.labels[label]
	if type(way) ~= "table" then
		return unknown(result, "当前 Tern 进程日志中没有该主机的连接记录")
	end
	result.since, result.log = way.since, reading.log
	if way.mode == "loopback" then
		local reason = SSH_TUNNELS[way.port]
		result.mode, result.tone, result.path = "ssh", "accent", "over UDP to " .. way.addr
		result.label = if reason then "SSH隧道" else "本地转发"
		result.reason = reason or ("经本机回环 UDP 入口 " .. way.addr .. "；不是原生 UDP 直连。")
	elseif way.mode == "direct" then
		result.mode, result.label, result.tone, result.path = "direct", "UDP直连", "ok", way.addr
		result.reason = (if way.upgraded then "已从中继升级为原生 UDP 直连 " else "按地址经 UDP 直连 ") .. way.addr
	elseif way.mode == "relay" then
		result.mode, result.label, result.tone, result.path = "relay", "中继", "warn", way.relay
		result.reason = "经中继 " .. way.relay
	else
		return unknown(result, "该连接的日志没有 way= 选路字段")
	end
	return result
end

local function status(cx: any): { any }
	updateHosts(cx)
	local rows = {}
	for _, host in hosts do
		table.insert(rows, view(host))
	end
	return rows
end

-- Widgets are cached per pane input, but hosts, RTT and readings change outside it: redraw after each update.
-- Only from timers, events, commands and process callbacks (a formatter calling refresh raises).
local function redraw()
	local ok, failure = pcall(tern.chrome.refresh)
	if not ok then
		tern.log.warn("connection-status: widget refresh failed:", failure)
	end
end

-- Callbacks waiting for a log reading that starts after they registered.
local waiters: { (any) -> () } = {}

-- Reads new lines of this Tern process's log; one probe at a time, at most once per poll unless forced.
local function probe(force: boolean?)
	if probing or (not force and tern.now() - attempted < POLL_MS - 100) then
		return
	end
	probing = true
	attempted = tern.now()
	local notify = waiters
	waiters = {}
	local function finish(cx: any)
		probing = false
		redraw()
		for _, waiter in notify do
			waiter(cx)
		end
		if cx and #waiters > 0 then
			probe(true)
		end
	end
	local request = tern.json.encode({ cursor = cursor or {}, labels = labels or {} })
	local ok, failure = pcall(function()
		tern.process.run({ "/usr/bin/python3", PROBE }, { stdin = request, timeout_ms = 8000 }, function(process, cx)
			local decoded, answer = pcall(tern.json.decode, process.stdout)
			if process.status == 0 and decoded and type(answer) == "table" and type(answer.labels) == "table" then
				cursor, labels = answer.cursor, answer.labels
				reading = { checked = tern.now(), labels = answer.labels, log = answer.log }
			else
				local reason = if process.timed_out then "读取 Tern 日志超时"
					elseif decoded and type(answer) == "table" and type(answer.reason) == "string" then answer.reason
					else (string.gsub(process.stderr or "", "%s+$", ""))
				reading = { checked = tern.now(), reason = string.sub(if reason ~= "" then reason else "读取 Tern 日志失败", 1, 300) }
			end
			finish(cx)
		end)
	end)
	if not ok then
		reading = { checked = tern.now(), reason = tostring(failure) }
		finish(nil)
	end
end

-- Runs `fn` with a fresh cx (nil when the probe could not start) after a reading that starts from now.
local function afterReading(fn: (any) -> ())
	table.insert(waiters, fn)
	probe(true)
end

local function refresh(cx: any)
	updateHosts(cx)
	sampleRtt(tern.now())
	refreshMemory(cx)
	redraw()
	for _, host in hosts do
		if host.kind == "remote" and host.state == "connected" then
			probe()
			return
		end
	end
end

local function tick(cx: any)
	if cx then
		refresh(cx)
	end
	tern.timer(POLL_MS, tick)
end


local function showDetails(cx: any)
	updateHosts(cx)
	local pane = cx.session:focused()
	local host = if pane then paneHosts[pane] else nil
	if not host then
		cx:toast("info", "连接方式", "当前 pane 不属于已知主机")
		return
	end
	local info = view(host)
	local title = host.name .. " · " .. info.label
	if host.kind == "remote" then
		title ..= " · " .. latency(host)
	end
	local detail = info.reason or "尚未获得选路证据"
	if info.since then
		detail ..= "；" .. (string.match(info.since, "T(%d+:%d+:%d+)") or info.since) .. " 起"
	end
	cx:toast(if info.mode == "unknown" then "info" else "success", title, detail)
end

tern.command({
	id = "details",
	title = "查看当前连接方式",
	run = function(cx)
		-- Always on a fresh reading. A probe that cannot start answers with no cx; `cx` is only live until run returns.
		local sync = true
		afterReading(function(freshCx)
			local live = freshCx or (if sync then cx else nil)
			if live then
				showDetails(live)
			end
		end)
		sync = false
	end,
})

-- recover_tunnel.py leaves a healthy tunnel alone and rebuilds a broken one, after checking that SSH reaches the
-- host (that needs the VPN). Exit status 0: Tern authenticates through the tunnel. One run per tunnel at a time.
local RECOVER_TIMEOUT_MS = 120000
local recovering: { [string]: boolean } = {}

-- Calls `done(ok, output, cx)` when the check of tunnel `name` (a TUNNEL_HOSTS name) has ended.
local function recoverTunnel(cx: any, name: string, done: (boolean, string, any) -> ())
	if recovering[name] then
		cx:toast("info", "正在检查 " .. name .. " 隧道", "请等待上一次操作完成")
		return
	end
	recovering[name] = true
	cx:toast("info", "正在检查 " .. name .. " SSH 隧道", "健康时不重启；故障时重建并验证，通常十几秒")
	tern.process.run({ "/usr/bin/python3", tern.plugin.dir .. "/recover_tunnel.py", name }, { timeout_ms = RECOVER_TIMEOUT_MS }, function(result, currentCx)
		recovering[name] = false
		if not currentCx then
			return
		end
		local output = if result.timed_out then "检查超时，已被终止；已启动的隧道仍在运行，请稍后再试" else string.sub(result.stdout .. result.stderr, 1, 1200)
		done(result.status == 0, output, currentCx)
	end)
end

tern.command({
	id = "recover-ssh",
	title = "检查并恢复当前主机 SSH 隧道",
	run = function(cx)
		updateHosts(cx)
		local focused = cx.session:focused()
		local host = if focused then paneHosts[focused] else nil
		local target = nil
		if host then
			for _, candidate in TUNNEL_HOSTS do
				if candidate.tunnel and sameTarget(host.address or "", candidate) then
					target = candidate
				end
			end
		end
		if not target then
			cx:toast("info", "当前主机没有 SSH 隧道", if host then host.name else "没有聚焦的 pane")
			return
		end
		recoverTunnel(cx, target.name, function(ok, output, currentCx)
			currentCx:toast(if ok then "success" else "error", if ok then "SSH 隧道可用" else "隧道恢复未通过验证", output)
		end)
	end,
})

tern.command({
	id = "hosts-config",
	title = "查看主机配置状态",
	run = function(cx)
		if hostConfigError then
			cx:toast("error", "主机配置无法读取", HOST_CONFIG_PATH .. "\n" .. hostConfigError)
		elseif #TUNNEL_HOSTS == 0 then
			cx:toast("info", "尚未配置远端主机", HOST_CONFIG_PATH .. "\n参考插件目录的 hosts.example.json")
		else
			local names = {}
			for _, target in TUNNEL_HOSTS do
				table.insert(names, target.name)
			end
			cx:toast("success", "主机配置已加载", HOST_CONFIG_PATH .. "\n" .. table.concat(names, " · "))
		end
	end,
})

-- Tunnel entries come from hosts.json above; `recover-ssh` works on the focused pane's tunnel.
local function attachedTo(cx: any, target: any): { any }
	updateHosts(cx)
	local found = {}
	for _, host in hosts do
		if host.kind == "remote" and sameTarget(host.address or "", target) then
			table.insert(found, host)
		end
	end
	return found
end

-- The configured tunnel name for any address of that machine, else Tern's host name.
local function displayName(host: any): string
	if host.kind == "local" then
		return "本机"
	end
	for _, target in TUNNEL_HOSTS do
		if sameTarget(host.address or "", target) then
			return target.name
		end
	end
	return host.name
end

-- Canonical machine keys keep a late sample from one host out of another host's widget.
local function memoryKey(host: any): string?
	if host.kind == "local" then
		return "local"
	end
	for _, target in TUNNEL_HOSTS do
		if sameTarget(host.address or "", target) then
			return target.name
		end
	end
	return nil
end

local function currentMemory(host: any): any
	local key = memoryKey(host)
	local sample = if key then memoryReadings[key] else nil
	if host.state ~= "connected" or not sample or not sample.text or tern.now() - sample.checked > STALE_MS then
		return nil
	end
	return sample
end

local function memoryBytes(bytes: number): string
	return if bytes >= 1073741824 then string.format("%.1f GB", bytes / 1073741824)
		elseif bytes >= 1048576 then string.format("%.0f MB", bytes / 1048576)
		elseif bytes >= 1024 then string.format("%.0f KB", bytes / 1024)
		else string.format("%.0f B", bytes)
end

local MEMORY_CARD = ".wgc[data-widget='connection-status/memory']"
-- Rows Tern generates from a card-less widget's `title` (the unavailable fallback).
local MEMORY_TITLE_ROW = MEMORY_CARD .. " .wgc-sec > .wgc-row"
local MEMORY_COLORS = {
	"rgb(from var(--t1) r g b / 78%)",
	"rgb(from var(--t1) r g b / 52%)",
	"rgb(from var(--t1) r g b / 32%)",
	"rgb(from var(--t1) r g b / 15%)",
	"rgb(from var(--t1) r g b / 8%)",
}

local function memoryNumber(value: any): number?
	return if type(value) == "number" and value == value and value >= 0 and value < math.huge then value else nil
end

local function memoryText(value: any, fallback: string): string
	return if type(value) == "string" and value ~= "" then (string.gsub(value, "%c", " ")) else fallback
end

local function memoryPressureStyle(pressure: any, someValue: any, fullValue: any): (string, string)
	local levels: { [string]: { string } } = {
		normal = { "正常", "var(--ok)" },
		warning = { "偏高", "var(--warn)" },
		critical = { "严重", "var(--bad)" },
		unknown = { "未知", "var(--t3)" },
	}
	local level = if type(pressure) == "string" then levels[pressure] or levels.unknown else levels.unknown
	local label = "● " .. level[1]
	local some = memoryNumber(someValue)
	local full = memoryNumber(fullValue)
	if level == levels.unknown and some and some <= 100 then
		label = string.format("PSI %.2f%%", some)
		if full and full <= 100 then
			label ..= string.format(" · full %.2f%%", full)
		end
	end
	return label, level[2]
end

local function memoryColor(index: number): string
	return MEMORY_COLORS[math.min(index, #MEMORY_COLORS)]
end

local function memoryPartColor(index: number, part: any): string
	-- Cached files have their own muted segment on macOS. Unused system
	-- memory and remaining container quota are track, not occupied memory.
	if not part.included and part.id ~= "cached" then
		return "var(--l1)"
	end
	return memoryColor(index)
end

-- The collector's categories are disjoint; excluded categories are outside charged/used memory.
local function memoryBreakdown(answer: any, used: number, total: number): { any }
	local parts = {}
	local included, excluded = 0, 0
	if type(answer) ~= "table" then
		return parts
	end
	for _, part in answer do
		if type(part) ~= "table" then
			return {}
		end
		local bytes = memoryNumber(part.bytes)
		if not bytes or type(part.included) ~= "boolean" or type(part.label) ~= "string" or part.label == "" then
			return {}
		end
		table.insert(parts, { id = part.id, label = memoryText(part.label, "—"), bytes = bytes, included = part.included })
		if part.included then
			included += bytes
		else
			excluded += bytes
		end
	end
	if included > used or excluded > math.max(0, total - used) then
		return {}
	end
	if #parts > 0 and included < used then
		table.insert(parts, { label = "未分类", bytes = used - included, included = true })
	end
	return parts
end

local function memorySplit(parts: { any }, used: number, total: number): string
	local stops = {}
	local offset = 0
	local function segment(bytes: number, color: string)
		local from = math.clamp(offset / total * 100, 0, 100)
		offset += bytes
		local to = math.clamp(offset / total * 100, 0, 100)
		if to > from then
			table.insert(stops, string.format("%s %.4f%%, %s %.4f%%", color, from, color, to))
		end
	end
	if #parts == 0 then
		segment(used, memoryColor(1))
	else
		-- Keep all used segments before the disjoint categories outside used.
		for index, part in parts do
			if part.included then
				segment(part.bytes, memoryPartColor(index, part))
			end
		end
		for index, part in parts do
			if not part.included then
				segment(part.bytes, memoryPartColor(index, part))
			end
		end
	end
	local endPercent = math.clamp(offset / total * 100, 0, 100)
	table.insert(stops, string.format("var(--l1) %.4f%%, var(--l1) 100%%", endPercent))
	return "linear-gradient(to right, " .. table.concat(stops, ", ") .. ")"
end

-- One native widget card serves both hover and click-to-pin. The card API
-- yields real label/text nodes; CSS places each row, so the layout no longer
-- depends on generated title text or character-width guesses.
local MEMORY_ROW = MEMORY_CARD .. " .wgc-plugin-rows > .wgc-plugin-row"

tern.css("memory-card", [[
.wgc[data-widget='connection-status/memory'] {
	position: relative;
	width: 320px;
	max-width: calc(100vw - 24px);
	max-height: calc(100vh - 32px);
	overflow-y: auto;
	scrollbar-width: none;
}
.wgc[data-widget='connection-status/memory'] .wgc-head {
	min-height: 38px;
}
.wgc[data-widget='connection-status/memory'] .wgc-sec {
	display: grid;
	grid-template-columns: repeat(4, minmax(0, 1fr));
	column-gap: 6px;
	padding-top: 0;
	border-top: 0;
}
.wgc[data-widget='connection-status/memory'] .wgc-sec > .wgc-row {
	grid-column: 1 / 5;
	min-width: 0;
}
.wgc[data-widget='connection-status/memory'] .wgc-plugin-row {
	padding: 0;
	background: transparent;
}
.wgc[data-widget='connection-status/memory'] .wgc-plugin-row .wgc-k {
	max-width: none;
}
]])

local function memoryRowRule(rules: { string }, index: number, declarations: string)
	table.insert(rules, MEMORY_ROW .. ":nth-child(" .. tostring(index) .. ") { " .. declarations .. " }")
end

local function memoryCellRule(rules: { string }, index: number, selector: string, declarations: string)
	table.insert(rules, MEMORY_ROW .. ":nth-child(" .. tostring(index) .. ") " .. selector .. " { " .. declarations .. " }")
end

local function memoryCssString(text: string): string
	return '"' .. (string.gsub(text, '[\\"]', "\\%0")) .. '"'
end

-- Card rows are bounded at 200 characters. Cut on a UTF-8 boundary so a long
-- process name cannot fail the JSON reply.
local function memoryClip(text: string, limit: number): string
	if #text <= limit then
		return text
	end
	local cut = limit - 1
	while cut > 0 and not utf8.len(string.sub(text, 1, cut)) do
		cut -= 1
	end
	return string.sub(text, 1, cut) .. "…"
end

local function memoryCard(key: string, sample: any): ({ any }, string)
	local pressure, pressureColor = memoryPressureStyle(sample.pressure, sample.pressure_some_avg10, sample.pressure_full_avg10)
	local rows: { any } = {}
	local rules: { string } = {}
	local function push(row: any): number
		table.insert(rows, row)
		return #rows
	end
	local function style(index: number, declarations: string)
		memoryRowRule(rules, index, declarations)
	end
	local function cell(index: number, selector: string, declarations: string)
		memoryCellRule(rules, index, selector, declarations)
	end
	local function hide(index: number)
		cell(index, ".wgc-k", "display: none;")
		cell(index, ".wgc-v", "display: none;")
	end
	local function divider(name: string): number
		local index = push({ id = name, text = "" })
		style(index, "grid-column: 1 / 5; height: 1px; min-height: 1px; line-height: 1px; margin: 8px -12px 0; background: var(--l1);")
		hide(index)
		return index
	end
	local function heading(name: string, label: string, text: string): number
		local index = push({ id = name, label = label, text = text })
		style(index, "grid-column: 1 / 5; align-items: center; min-height: 18px; line-height: 18px; margin-top: 8px; margin-bottom: 2px;")
		cell(index, ".wgc-k", "flex: none; font: 550 11.5px/16px var(--sans); color: var(--t3);")
		cell(index, ".wgc-v", "flex: 1; overflow: visible; text-align: right; font: 400 11px/16px var(--mono); font-variant-numeric: tabular-nums; color: var(--t4);")
		return index
	end

	-- Pressure badge: an absolute row escapes the scrolling section and sits in
	-- the 38px header band (10px down, 12px from the right), like the built-in.
	local pressureRow = push({ id = "pressure", label = pressure, text = "" })
	style(pressureRow, "position: absolute; top: 10px; right: 12px; width: max-content; height: 20px; min-height: 20px; padding: 0 7px; border-radius: 999px; background: var(--l1); line-height: 20px;")
	cell(pressureRow, ".wgc-k", "flex: none; overflow: visible; white-space: nowrap; font-size: 10px; color: " .. pressureColor .. ";")

	-- Remote identity stays visible; "已用" is implied by the summary below.
	local subtitle: string? = nil
	if key ~= "local" then
		subtitle = key .. (if sample.scope == "container-cache" then " · 含缓存" else "")
	end
	local subtitleRow = push({ id = "subtitle", label = if subtitle then memoryClip(subtitle, 200) else "", text = "" })
	if subtitle then
		style(subtitleRow, "grid-column: 1 / 5; align-items: center; min-height: 16px; line-height: 16px; margin-bottom: 2px;")
		cell(subtitleRow, ".wgc-k", "flex: none; font: 400 11.5px/16px var(--sans); color: var(--t3);")
	else
		style(subtitleRow, "display: none;")
	end

	-- Native summary baseline: large used number, small unit/capacity, and
	-- available memory right-aligned in the non-interactive action button.
	local usedNumber, usedUnit = string.match(memoryBytes(sample.used_bytes), "^(%S+)%s+(.+)$")
	local capacity = string.gsub(memoryBytes(sample.total_bytes), "%.0 ", " ")
	local available = memoryNumber(sample.available_bytes)
	local availableLabel = if sample.scope == "container-cache" then "headroom" else "available"
	local availableText = (if available then memoryBytes(available) else "—") .. " " .. availableLabel
	local summaryRow = push({
		id = "summary",
		label = usedNumber or "—",
		text = (usedUnit or "B") .. " of " .. capacity,
		actions = { { id = "noop", label = availableText } },
	})
	style(summaryRow, "grid-column: 1 / 5; display: flex; align-items: baseline; gap: 6px; min-height: 28px; line-height: 28px; margin-top: 2px;")
	cell(summaryRow, ".wgc-k", "flex: none; overflow: visible; white-space: nowrap; font: 550 24px/28px var(--mono); font-variant-numeric: tabular-nums; letter-spacing: -0.03em; color: var(--t1);")
	cell(summaryRow, ".wgc-v", "flex: none; min-width: 0; overflow: visible; text-align: left; white-space: nowrap; font: 400 11.5px/20px var(--mono); font-variant-numeric: tabular-nums; color: var(--t3);")
	cell(summaryRow, ".wgc-btn", "margin-left: auto; height: auto; padding: 0; border: 0; border-radius: 0; background: none; font: 400 11.5px/20px var(--mono); font-variant-numeric: tabular-nums; color: var(--t3); pointer-events: none;")
	table.insert(rules, "@media (max-width: 359px) { " .. MEMORY_ROW .. ":nth-child(" .. tostring(summaryRow) .. ") { flex-wrap: wrap; } "
		.. MEMORY_ROW .. ":nth-child(" .. tostring(summaryRow) .. ") .wgc-btn { margin-left: 0; width: 100%; text-align: right; } }")

	-- Segmented bar: App, Wired, Compressed, Cached (Linux: Used, Free).
	local parts = memoryBreakdown(sample.breakdown, sample.used_bytes, sample.total_bytes)
	if sample.platform == "linux" and sample.scope == "system" then
		parts = { { id = "used", label = "Used", bytes = sample.used_bytes, included = true } }
		if available and available <= math.max(0, sample.total_bytes - sample.used_bytes) then
			table.insert(parts, { id = "available", label = "Free", bytes = available, included = false })
		end
	end
	local bar = push({ id = "bar", text = "" })
	style(bar, "grid-column: 1 / 5; height: 8px; min-height: 8px; line-height: 8px; margin: 10px 0 8px; border-radius: 3px; overflow: hidden; box-shadow: inset 0 0 0 0.5px var(--l2); background: " .. memorySplit(parts, sample.used_bytes, sample.total_bytes) .. ";")
	hide(bar)

	-- Four fixed legend slots keep row indices stable; unused slots are hidden.
	local slots: { any } = {}
	for index, part in parts do
		slots[index] = part
	end
	if #slots == 0 then
		slots[1] = { placeholder = true, label = "分项 —" }
	end
	for slot = 1, 4 do
		local part = slots[slot]
		local index = push({
			id = "legend-" .. tostring(slot),
			label = if part then memoryClip(memoryText(part.label, "—"), 200) else "",
			text = if part and not part.placeholder then memoryBytes(part.bytes) else "",
		})
		if not part then
			style(index, "display: none;")
		elseif part.placeholder then
			style(index, "grid-column: 1 / 5; align-items: center; height: 20px; min-height: 20px; line-height: 20px;")
			cell(index, ".wgc-k", "flex: none; font: 400 11.5px/20px var(--sans); color: var(--t3);")
			cell(index, ".wgc-v", "display: none;")
		else
			local left = slot % 2 == 1
			style(index, "grid-column: " .. (if left then "1 / 3" else "3 / 5") .. "; align-items: center; gap: 6px; height: 20px; min-height: 20px; line-height: 20px; "
				.. (if left then "margin-right: 5px;" else "margin-left: 5px;"))
			cell(index, ".wgc-k", "flex: 1; font: 400 12px/16px var(--sans); color: var(--t3);")
			cell(index, ".wgc-v", "flex: none; font: 400 11.5px/20px var(--mono); font-variant-numeric: tabular-nums; color: var(--t1);")
			local outline = if not part.included and part.id ~= "cached" then "box-shadow: inset 0 0 0 0.5px var(--l3);" else ""
			table.insert(rules, MEMORY_ROW .. ":nth-child(" .. tostring(index) .. ")::before { content: ''; flex: none; width: 8px; height: 8px; border-radius: 2px; background: " .. memoryPartColor(slot, part) .. "; " .. outline .. " }")
		end
	end

	-- No history chart: swap sits directly below the breakdown.
	local swapUsed, swapTotal = memoryNumber(sample.swap_used_bytes), memoryNumber(sample.swap_total_bytes)
	local swap = if not swapUsed and not swapTotal then "—"
		else (if swapUsed then memoryBytes(swapUsed) else "—") .. " of " .. (if swapTotal then memoryBytes(swapTotal) else "—")
	local swapRow = push({ id = "swap", label = "Swap used", text = swap })
	style(swapRow, "grid-column: 1 / 5; align-items: baseline; min-height: 22px; line-height: 22px;")
	cell(swapRow, ".wgc-k", "flex: 1; font: 400 12px/22px var(--sans); color: var(--t3);")
	cell(swapRow, ".wgc-v", "flex: none; font: 400 11.5px/22px var(--mono); font-variant-numeric: tabular-nums; color: var(--t1); text-align: right;")

	local spacer = push({ id = "spacer", text = "" })
	style(spacer, "grid-column: 1 / 5; height: 6px; min-height: 6px; line-height: 6px;")
	hide(spacer)

	local processesExpanded = memoryProcessesExpanded[key] == true
	local count = memoryNumber(sample.process_count)
	if count and count ~= math.floor(count) then
		count = nil
	end

	-- Top apps: bundle-grouped, name bright with a dim count, then the RSS
	-- gauge and value. Expansion replaces this block so the footer stays in the
	-- 32-row card budget.
	if not processesExpanded then
		divider("divider")
		local appsHeader = heading("apps", "Top apps", if count then tostring(count) .. " processes" else "—")
		local apps = {}
		if type(sample.apps) == "table" then
			for _, app in sample.apps do
				if #apps == 5 then break end
				if type(app) == "table" then
					local rss, appCount = memoryNumber(app.rss_bytes), memoryNumber(app.count)
					if rss and appCount and appCount > 0 and appCount == math.floor(appCount) and type(app.name) == "string" and app.name ~= "" then
						table.insert(apps, { name = memoryClip(memoryText(app.name, "—"), 190), rss_bytes = rss, count = appCount })
					end
				end
			end
		end
		local largest = 0
		for _, app in apps do
			largest = math.max(largest, app.rss_bytes)
		end
		if #apps == 0 then
			local empty = push({ id = "apps-empty", label = if count == 0 and type(sample.apps) == "table" then "没有可读进程" else "—", text = "" })
			style(empty, "grid-column: 1 / 5; align-items: center; min-height: 22px; line-height: 22px;")
			cell(empty, ".wgc-k", "flex: none; font: 400 11.5px/22px var(--sans); color: var(--t3);")
			cell(empty, ".wgc-v", "display: none;")
		else
			for index, app in apps do
				local appRow = push({ id = "app-" .. tostring(index), label = app.name, text = memoryBytes(app.rss_bytes) })
				style(appRow, "grid-column: 1 / 5; position: relative; align-items: center; min-height: 22px; line-height: 22px;")
				cell(appRow, ".wgc-k", "flex: 1; min-width: 0; font: 400 12px/22px var(--sans); color: var(--t1);")
				if app.count > 1 then
					table.insert(rules, MEMORY_ROW .. ":nth-child(" .. tostring(appRow) .. ") .wgc-k::after { content: " .. memoryCssString(" ×" .. tostring(app.count)) .. "; font: 400 11px/22px var(--mono); color: var(--t4); }")
				end
				cell(appRow, ".wgc-v", "flex: none; width: 50px; overflow: visible; text-align: right; font: 400 11.5px/22px var(--mono); font-variant-numeric: tabular-nums; color: var(--t1);")
				local percent = if largest > 0 then math.clamp(app.rss_bytes / largest * 100, 0, 100) else 0
				table.insert(rules, MEMORY_ROW .. ":nth-child(" .. tostring(appRow) .. ")::after { content: ''; position: absolute; right: 56px; top: 50%; width: 48px; height: 4px; margin-top: -2px; border-radius: 999px; background: linear-gradient(to right, rgb(from var(--t1) r g b / 55%) 0 "
					.. string.format("%.3f", percent) .. "%, var(--l1) " .. string.format("%.3f", percent) .. "% 100%); }")
			end
		end
	end

	-- Process detail is real per-host state. Remote cards keep the folded
	-- heading; local cards hide it until the palette command asks for details.
	if key ~= "local" or processesExpanded then
		local processes = {}
		if type(sample.processes) == "table" then
			for _, process in sample.processes do
				if #processes == 20 then break end
				if type(process) == "table" then
					local pid, rss = memoryNumber(process.pid), memoryNumber(process.rss_bytes)
					if pid and pid > 0 and pid == math.floor(pid) and rss then
						table.insert(processes, process)
					end
				end
			end
		end
		divider("divider-processes")
		local shown = math.min(#processes, 18)
		local processCount = if type(sample.processes) ~= "table" then "—"
			elseif processesExpanded then tostring(shown) .. (if count then " / " .. tostring(count) else "")
			elseif count then tostring(#processes) .. " / " .. tostring(count) else tostring(#processes)
		local actions = { { id = "toggle-processes", label = if processesExpanded then "收起" else "展开" } }
		local processHeader = push({ id = "processes", label = "进程", text = processCount, actions = actions })
		style(processHeader, "grid-column: 1 / 5; align-items: center; min-height: 18px; line-height: 18px; margin-top: 8px; margin-bottom: 2px;")
		cell(processHeader, ".wgc-k", "flex: none; font: 550 11.5px/16px var(--sans); color: var(--t3);")
		cell(processHeader, ".wgc-v", "flex: 1; overflow: visible; text-align: right; margin-right: 6px; font: 400 11px/16px var(--mono); font-variant-numeric: tabular-nums; color: var(--t4);")
		cell(processHeader, ".wgc-btn", "height: 20px; min-height: 20px; padding: 0 7px; border: 0; border-radius: 5px; background: var(--l1); font: 500 10.5px/20px var(--sans); color: var(--t2);")
		if processesExpanded then
			if #processes == 0 then
				local empty = push({ id = "processes-empty", label = if type(sample.processes) == "table" then "没有可读进程" else memoryText(sample.process_reason, "进程不可用"), text = "" })
				style(empty, "grid-column: 1 / 5; align-items: center; min-height: 22px; line-height: 22px;")
				cell(empty, ".wgc-k", "flex: none; font: 400 10.5px/22px var(--sans); color: var(--t4);")
				cell(empty, ".wgc-v", "display: none;")
			else
				-- One row per process; 18 rows keep a remote card at the 32-row cap.
				for index = 1, shown do
					local process = processes[index]
					local processRow = push({
						id = "process-" .. tostring(index),
						label = memoryClip(memoryText(process.name, "—"), 100),
						text = string.format("PID %.0f · %s", process.pid, memoryBytes(process.rss_bytes)),
					})
					style(processRow, "grid-column: 1 / 5; align-items: center; min-height: 22px; line-height: 22px;")
					cell(processRow, ".wgc-k", "flex: 1; min-width: 0; font: 400 11.5px/22px var(--sans); color: var(--t1);")
					cell(processRow, ".wgc-v", "flex: none; font: 400 10.5px/22px var(--mono); font-variant-numeric: tabular-nums; color: var(--t4); text-align: right;")
				end
				local total = if count then count elseif type(sample.processes) == "table" then #sample.processes else shown
				local remaining = math.max(0, total - shown)
				if remaining > 0 then
					local more = push({ id = "more", label = string.format("还有 %.0f 个进程", remaining), text = "" })
					style(more, "grid-column: 1 / 5; align-items: center; min-height: 18px; line-height: 18px;")
					cell(more, ".wgc-k", "flex: none; font: 400 10.5px/18px var(--sans); color: var(--t4);")
					cell(more, ".wgc-v", "display: none;")
				end
			end
		end
	end

	-- Full-width divider, 40px band and the native activity mark on the right.
	local footer = push({
		id = "footer",
		label = string.format("Updated every %g s", POLL_MS / 1000),
		text = "",
		actions = { { id = "task-manager", label = "Task manager" } },
	})
	style(footer, "grid-column: 1 / 5; display: flex; align-items: center; gap: 8px; min-height: 40px; line-height: 16px; margin: 8px -12px -8px; padding: 7px 14px 8px 12px; border-top: 1px solid var(--l1);")
	cell(footer, ".wgc-k", "flex: 1; font: 400 11.5px/16px var(--sans); color: var(--t3);")
	cell(footer, ".wgc-v", "display: none;")
	cell(footer, ".wgc-btn", "display: inline-flex; align-items: center; gap: 5px; height: 24px; min-height: 24px; padding: 0; border: 0; background: none; font: 500 12px/16px var(--sans); color: var(--t2);")
	local function activityIcon(color: string): string
		return tern.base64.encode("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M1.75 8.5h2.5l1.75-4.75 3 8.5 1.75-3.75h3.5' fill='none' stroke='" .. color .. "' stroke-width='1.45' stroke-linecap='round' stroke-linejoin='round'/></svg>")
	end
	table.insert(rules, MEMORY_ROW .. ":nth-child(" .. tostring(footer) .. ") .wgc-btn::before { content: ''; flex: none; width: 13px; height: 13px; background-image: url(\"data:image/svg+xml;base64," .. activityIcon("#adb1b9") .. "\"); background-size: 100% 100%; background-repeat: no-repeat; }")
	table.insert(rules, "@media (prefers-color-scheme: light) { " .. MEMORY_ROW .. ":nth-child(" .. tostring(footer) .. ") .wgc-btn::before { background-image: url(\"data:image/svg+xml;base64," .. activityIcon("#5e626b") .. "\"); } }")

	return rows, table.concat(rules, "\n")
end

-- Card-less fallback (no focused host or no sample): style the title's three
-- lines as host, pressure badge and reason.
local memoryDefaultCss = string.format([[
%s:first-child { grid-row: 1; grid-column: 1 / 3; }
%s:last-child { grid-row: 2; grid-column: 1 / 5; }
%s:nth-child(2) { grid-row: 1; grid-column: 3 / 5; justify-self: end; align-self: center; padding: 0 7px; min-height: 20px; line-height: 20px; border-radius: 999px; background: var(--l1); }
%s:nth-child(2) .wgc-k { flex: none; overflow: visible; white-space: nowrap; font-size: 10px; color: var(--t3); }
]], MEMORY_TITLE_ROW, MEMORY_TITLE_ROW, MEMORY_TITLE_ROW, MEMORY_TITLE_ROW)
local memoryAppliedCss: string? = nil
local function applyMemoryStyle(style: string)
	if style ~= memoryAppliedCss then
		tern.css("memory-data", style)
		memoryAppliedCss = style
	end
end

-- Dynamic styles belong to the window, so resolve the current focus every time they change.
local function syncMemoryStyle(cx: any)
	local focused = cx.session:focused()
	local host = if focused then paneHosts[focused] else nil
	local sample = if host then currentMemory(host) else nil
	applyMemoryStyle(if sample then sample.css else memoryDefaultCss)
end
applyMemoryStyle(memoryDefaultCss)


local function nonemptyMemoryEnv(name: string): string?
	local value = tern.getenv(name)
	return if value ~= "" then value else nil
end

local memoryControl = nonemptyMemoryEnv("TERN_EXE") or "tern"
if memoryControl == "tern" and tern.runtime.os == "macos" then
	local installed = "/Applications/Tern.app/Contents/MacOS/tern"
	if tern.fs.exists(installed) then
		memoryControl = installed
	end
end
local memoryConfig = string.match(tern.plugin.data, "^(.*)[/\\][^/\\]+[/\\][^/\\]+$")
local memorySocket = nonemptyMemoryEnv("TERN_DAEMON_SOCKET") or nonemptyMemoryEnv("TERN_PANE_SOCKET")
	or (if memoryConfig then memoryConfig .. "/daemon.sock" else nil)

-- Palette access pins the same native card as clicking the widget; nothing is typed into a pane.
local function showMemoryCard(cx: any)
	if not memorySocket then
		cx:toast("error", "内存卡片未打开", "无法定位本机 Tern daemon")
		return
	end
	local ok, failure = pcall(function()
		tern.process.run({ memoryControl, "ctl", "widgets", "card", "connection-status/memory" }, {
			env = { TERN_PANE_SOCKET = memorySocket, TERN_WINDOW_KEY = cx:window_key() },
			timeout_ms = 2000,
		}, function(result, currentCx)
			if not currentCx then
				return
			end
			local decoded, answer = pcall(tern.json.decode, result.stdout)
			local success = result.status == 0 and decoded and type(answer) == "table" and answer.ok == true
			local reason = if result.timed_out then "打开内存卡片超时"
				elseif decoded and type(answer) == "table" and answer.error then tostring(answer.error)
				else result.stderr
			if not success then
				currentCx:toast("error", "内存卡片未打开", reason)
			end
		end)
	end)
	if not ok then
		cx:toast("error", "内存卡片未打开", tostring(failure))
	end
end

local function retireMemoryPanels(cx: any)
	-- Remove only this plugin's obsolete 1.12.x detail canvases, including restored remote replicas.
	for _, canvas in cx.canvas:list() do
		if canvas.owner == tern.plugin.id and string.match(canvas.about, "^内存详情：[%w%-]+$") then
			local info = cx.session:pane(canvas.pane)
			local live = if info and info.block == "tern.canvas" then cx.canvas:get(canvas.pane) else nil
			if live and live.owner == tern.plugin.id and live.about == canvas.about then
				cx.layout:close(canvas.pane)
			end
		end
	end
end

local function sampleMemory(host: any)
	local key = memoryKey(host)
	local now = tern.now()
	if not key or host.state ~= "connected" or memoryBusy[key]
		or now - (memoryAttempted[key] or -math.huge) < POLL_MS - 100 then
		return
	end
	memoryBusy[key] = true
	memoryAttempted[key] = now
	local ok, failure = pcall(function()
		tern.process.run({ "/usr/bin/python3", MEMORY_PROBE, key }, { timeout_ms = 10000 }, function(process, cx)
			memoryBusy[key] = nil
			local decoded, answer = pcall(tern.json.decode, process.stdout)
			local used = if decoded and type(answer) == "table" then memoryNumber(answer.used_bytes) else nil
			local total = if decoded and type(answer) == "table" then memoryNumber(answer.total_bytes) else nil
			if process.status == 0 and used and total and total > 0 then
				local checked = tern.now()
				local sample = {
					checked = checked,
					used_bytes = used,
					total_bytes = total,
					available_bytes = answer.available_bytes,
					text = string.format("%.0f%%", used / total * 100),
					ratio = used / total,
					scope = answer.scope,
					platform = answer.platform,
					breakdown = answer.breakdown,
					pressure = answer.pressure,
					pressure_some_avg10 = answer.pressure_some_avg10,
					pressure_full_avg10 = answer.pressure_full_avg10,
					swap_used_bytes = answer.swap_used_bytes,
					swap_total_bytes = answer.swap_total_bytes,
					apps = answer.apps,
					processes = if type(answer.processes) == "table" then answer.processes else nil,
					process_count = answer.process_count,
					process_reason = answer.process_reason,
				}
				sample.card, sample.css = memoryCard(key, sample)
				memoryReadings[key] = sample
			else
				memoryReadings[key] = {
					checked = tern.now(),
					reason = if process.timed_out then "读取内存超时"
						elseif decoded and type(answer) == "table" then answer.reason else "读取内存失败",
				}
			end
			if cx then
				updateHosts(cx)
				syncMemoryStyle(cx)
			end
			redraw()
		end)
	end)
	if not ok then
		memoryBusy[key] = nil
		memoryReadings[key] = { checked = now, reason = tostring(failure) }
		redraw()
	end
end

refreshMemory = function(cx: any)
	retireMemoryPanels(cx)
	local focused = cx.session:focused()
	local host = if focused then paneHosts[focused] else nil
	if host then
		sampleMemory(host)
	end
	syncMemoryStyle(cx)
end


tern.command({
	id = "memory-details",
	title = "查看内存占用",
	run = function(cx)
		showMemoryCard(cx)
	end,
})

tern.command({
	id = "memory-processes",
	title = "展开／收起内存进程",
	run = function(cx)
		updateHosts(cx)
		local focused = cx.session:focused()
		local host = if focused then paneHosts[focused] else nil
		local key = if host then memoryKey(host) else nil
		local sample = if host then currentMemory(host) else nil
		if not key or not sample then
			cx:toast("info", "进程详情不可用", "当前主机还没有有效的内存样本")
			return
		end
		memoryProcessesExpanded[key] = not memoryProcessesExpanded[key]
		sample.card, sample.css = memoryCard(key, sample)
		syncMemoryStyle(cx)
		redraw()
		showMemoryCard(cx)
	end,
})


local function onRelay(host: any): boolean
	return host.state == "connected" and view(host).mode == "relay"
end

local function brief(info: any): string
	return info.label .. (if info.rtt_ms then string.format(" · %.0f ms", info.rtt_ms) else "")
end

-- Calls `done(info, cx)` once the log shows how host `id` is connected (info nil: the host is gone),
-- trying up to `attempts` readings a second apart while the way is still unknown.
local function awaitWay(id: number, attempts: number, done: (any, any) -> ())
	afterReading(function(cx)
		if not cx then
			return
		end
		updateHosts(cx)
		local info = nil
		for _, host in hosts do
			if host.id == id then
				info = view(host)
			end
		end
		if info and info.mode == "unknown" and attempts > 1 then
			tern.timer(1000, function()
				awaitWay(id, attempts - 1, done)
			end)
			return
		end
		done(info, cx)
	end)
end

-- Tern raises for a host it cannot show or drop (a failed one, say): report it instead of failing the hook.
local function hostOp(cx: any, title: string, op: () -> ()): boolean
	local ok, failure = pcall(op)
	if not ok then
		cx:toast("error", title, tostring(failure))
	end
	return ok
end

-- Dials `address`; only once the log shows the new connection off the relay does it switch there and
-- disconnect `replace` (the relay connections to the same machine).
local function dial(cx: any, target: any, address: string, replace: { any })
	local kept = if #replace > 0 then "；原中继连接未断开" else ""
	cx:toast("info", "正在连接 " .. target.name, "经 SSH 隧道 " .. address .. (if #replace > 0 then "；确认不走中继后再断开中继连接" else ""))
	cx.hosts:add(address):next(function(added, err, addedCx)
		if not addedCx then
			return
		end
		if err or not added then
			addedCx:toast("error", target.name .. " 连接失败", tostring(err or "没有返回主机") .. kept)
			return
		end
		for _, old in replace do
			if old.id == added.id then
				addedCx:toast("error", target.name .. " 未重连", "Tern 把 " .. address .. " 并入了原来的中继连接" .. kept)
				return
			end
		end
		awaitWay(added.id, 4, function(info, doneCx)
			if not info then
				doneCx:toast("error", target.name .. " 新连接已断开", "请重试" .. kept)
				return
			end
			if info.mode == "relay" and #replace > 0 then
				hostOp(doneCx, target.name .. " 无法断开新连接", function()
					doneCx.hosts:disconnect(added.id)
				end)
				doneCx:toast("error", target.name .. " 隧道没有先应答", "新连接也走了中继，已断开它，保留原连接；检查／恢复 SSH 隧道后再试")
				return
			end
			if not hostOp(doneCx, target.name .. " 无法切换到新连接", function()
				doneCx.hosts:switch(added.id)
			end) then
				return
			end
			if info.mode == "unknown" then
				doneCx:toast("info", target.name .. " 已连接", "日志未确认新连接的方式：" .. info.reason .. kept)
				return
			end
			local dropped = 0
			for _, old in replace do
				if hostOp(doneCx, target.name .. " 无法断开中继连接", function()
					doneCx.hosts:disconnect(old.id)
				end) then
					dropped += 1
				end
			end
			local detail = if #replace > 0 then string.format("已断开 %d 条中继连接，远端程序继续运行", dropped) else info.reason
			doneCx:toast(if info.mode == "relay" then "info" else "success", target.name .. " · " .. brief(info), detail)
		end)
	end)
end

-- Connects `target` through its tunnel. Dialing a dead tunnel falls back to the relay and looks like a success,
-- so the tunnel is checked first (`checked`: it was, just now, and works).
local function connectTarget(cx: any, target: any, checked: boolean)
	local relayed = {}
	local pending = nil
	for _, host in attachedTo(cx, target) do
		if onRelay(host) then
			table.insert(relayed, host)
		elseif host.state == "connected" then
			hostOp(cx, target.name .. " 无法切换", function()
				cx.hosts:switch(host.id)
			end)
			return
		else
			pending = pending or host
		end
	end
	if not checked and target.tunnel then
		-- SSH reaches tunneled hosts only with the VPN up: without it the check says so and changes nothing.
		recoverTunnel(cx, target.name, function(ok, output, doneCx)
			if not ok then
				doneCx:toast("error", target.name .. " 隧道不可用，未连接", output)
				return
			end
			-- The check took seconds: decide again on a fresh reading.
			afterReading(function(freshCx)
				if freshCx then
					connectTarget(freshCx, target, true)
				end
			end)
		end)
		return
	end
	if #relayed == 0 then
		if pending then
			-- Attached but not connected (reconnecting, failed, …): show it as it is.
			hostOp(cx, target.name .. " · " .. view(pending).label, function()
				cx.hosts:switch(pending.id)
			end)
		else
			dial(cx, target, target.address, {})
		end
		return
	end
	-- Tern keeps one host per address, so when the named tunnel entry itself fell back to the relay,
	-- dial the tunnel's other pinned address.
	local address = target.address
	for _, host in relayed do
		if host.address == target.address then
			address = target.fallback
		end
	end
	if not address then
		cx:toast("error", target.name .. " 未重连", target.address .. " 本身正走中继，且没有备用地址；未断开")
		return
	end
	dial(cx, target, address, relayed)
end

tern.command({
	id = "connect",
	title = "连接主机（本机 / 已配置远端）",
	run = function(cx)
		local current = cx.hosts:current()
		updateHosts(cx)
		local items = {}
		for _, host in hosts do
			if host.kind == "local" then
				table.insert(items, { label = "本机", detail = "本地 · 选择后切换", group = "主机", host_id = host.id, current = host.id == current.id })
			end
		end
		for _, target in TUNNEL_HOSTS do
			-- Same priority as connectTarget: a connected non-relay host, then relay ones, then unready ones.
			local connected, relayed, pending = nil, nil, nil
			local isCurrent = false
			for _, host in attachedTo(cx, target) do
				isCurrent = isCurrent or host.id == current.id
				if onRelay(host) then
					relayed = relayed or host
				elseif host.state == "connected" then
					connected = connected or host
				else
					pending = pending or host
				end
			end
			local state = if connected then brief(view(connected)) .. " · 选择后切换"
				elseif relayed then "中继 · " .. latency(relayed) .. " · 选择后" .. (if target.tunnel then "检查隧道，换到隧道并断开中继" else "直连并断开中继")
				elseif pending then view(pending).label .. " · 选择后切换"
				elseif target.tunnel then "未连接 · 选择后检查隧道并连接"
				else "未连接 · 选择后直连"
			-- Way and RTT first: the picker cuts the dim detail off at the row's end.
			table.insert(items, { label = target.name, detail = state .. " · " .. target.detail, group = "主机", target_name = target.name, current = isCurrent })
		end
		cx:pick({ items = items, placeholder = "选择主机…", action = "切换" }):next(function(choice, err, currentCx)
			if not currentCx then
				return
			end
			if err then
				currentCx:toast("error", "连接主机失败", err)
				return
			end
			if not choice then
				return
			end
			if choice.host_id then
				hostOp(currentCx, "无法切换到本机", function()
					currentCx.hosts:switch(choice.host_id)
				end)
				return
			end
			local target = nil
			for _, candidate in TUNNEL_HOSTS do
				if candidate.name == choice.target_name then
					target = candidate
				end
			end
			if not target then
				return
			end
			-- Decide on a fresh reading, not the list's snapshot.
			afterReading(function(freshCx)
				if freshCx then
					connectTarget(freshCx, target, false)
				end
			end)
		end)
	end,
})

tern.carly.export("status", {
	sig = "status() -> {ConnectionStatus}",
	doc = "读取当前窗口每个主机的连接方式、RTT和实际选路证据；不更改连接。",
}, function(cx)
	return status(cx)
end)

-- Widget bar entry (palette › Edit widgets…): `connection-status/rtt`.
-- Formatters see only the focused pane; its host comes from updateHosts. Like
-- the standalone host-latency widget, it hides on local panes and keeps a
-- 10-minute RTT history.
tern.chrome.widget("rtt", {
	name = "延迟",
	icon = "gauge",
	description = "当前 pane 的 Tern 连接 RTT；保留 10 分钟曲线，点击切换主机",
	styles = { "text", "graph", "bar", "ring" },
	render = function(pane)
		if not pane then
			return nil
		end
		local host = paneHosts[pane.pane]
		if not host or host.kind ~= "remote" then
			return nil
		end
		local info = view(host)
		local text, tone = "…", "muted"
		if host.state ~= "connected" then
			text, tone = info.label, "warn"
		elseif type(host.rtt_ms) == "number" then
			text = latency(host)
			tone = if host.rtt_ms < 150 then "success" elseif host.rtt_ms < 300 then "warn" else "error"
		end
		local key = rttKey(host)
		local record = if key then rttSamples[key] else nil
		local card: { any } = {
			{ id = "host", label = "Host", text = displayName(host) },
		}
		if host.state ~= "connected" then
			table.insert(card, { id = "state", label = "State", text = info.label, tone = "warn" })
		elseif type(host.rtt_ms) == "number" then
			table.insert(card, { id = "rtt", label = "RTT", text = text, tone = if tone == "success" then "ok" else tone })
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
			command = "plugin.connection-status.connect",
			value = if type(host.rtt_ms) == "number" then math.clamp(host.rtt_ms / RTT_MAX_MS, 0, 1) else nil,
			series = if record then record.samples else nil,
		}
	end,
})

-- Native meters use --wg-fill independently of the label's tone; keep both on theme colors.
tern.css("memory-colors", [[
.wg[data-widget='connection-status/memory'][data-tone='ok'] { --wg-fill: var(--ok); }
.wg[data-widget='connection-status/memory'][data-tone='warn'] { --wg-fill: var(--warn); }
.wg[data-widget='connection-status/memory'][data-tone='error'] { --wg-fill: var(--bad); }
]])

tern.chrome.widget("memory", {
	name = "内存",
	icon = "ram",
	description = "当前主机内存与进程占用；悬停查看，点击固定卡片",
	styles = { "ring", "bar", "text" },
	render = function(pane)
		local host = if pane then paneHosts[pane.pane] else nil
		if not host then
			return nil
		end
		local sample = currentMemory(host)
		return {
			text = if sample then sample.text else "—",
			value = if sample then math.clamp(sample.ratio, 0, 1) else nil,
			tone = if not sample then "muted"
				elseif sample.ratio >= 0.9 then "error"
				elseif sample.ratio >= 0.7 then "warn"
				else "ok",
			card = if sample then sample.card else nil,
			title = if sample then nil else memoryText(displayName(host), "未知主机") .. "\n● 未知\n内存不可用",
		}
	end,
})

-- Card buttons: the process heading toggles details in place; the footer
-- opens Tern's own Task Manager block. Rebuild the card before refreshing so
-- a pinned or hovered card shows the new row set immediately.
tern.on("widget_action", function(event, cx)
	if event.widget == "rtt" and event.row == "refresh" and event.act == "refresh" then
		updateHosts(cx)
		sampleRtt(tern.now())
		redraw()
		return true
	end
	if event.widget ~= "memory" then
		return false
	end
	if event.act == "toggle-processes" then
		updateHosts(cx)
		local focused = cx.session:focused()
		local host = if focused then paneHosts[focused] else nil
		local key = if host then memoryKey(host) else nil
		local sample = if host then currentMemory(host) else nil
		if key and sample then
			memoryProcessesExpanded[key] = not memoryProcessesExpanded[key]
			sample.card, sample.css = memoryCard(key, sample)
			syncMemoryStyle(cx)
			redraw()
		end
		return true
	elseif event.act == "task-manager" then
		cx:command("new_task_manager_block")
		return true
	elseif event.act == "noop" then
		return true
	end
	return false
end)

for _, event in { "focus", "pane_created", "pane_closed", "tab_created", "tab_closed" } do
	tern.on(event, function(_event, cx)
		refresh(cx)
	end)
end
tern.on("window_start", function(cx)
	refresh(cx)
end)
tern.timer(0, tick)
