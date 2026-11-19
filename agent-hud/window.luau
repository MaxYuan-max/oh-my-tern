--!strict
-- Agents 面板：悬浮面板列出各窗格里的 agent（接近原生 Agents HUD 的效果）。
-- 数据来自 cx.agents:list()；点击行 → cx.layout:focus(pane) 跳转；"+ New agent" 启动新 agent。
local ABOUT = "agent-hud:floating agents panel"
local TICK_MS = 2000
local HUDCSS: string

---------------------------------------------------------------- data helpers

local function sanitize(s: any): string
	if type(s) ~= "string" then
		return tostring(s)
	end
	local cleaned = string.gsub(s, "%c", " ")
	cleaned = string.gsub(cleaned, "%s+", " ")
	return (string.gsub(cleaned, "^%s+", ""))
end

-- Approximate display width: ASCII = 1 column, other (mostly wide) = 2.
local function nextCharAt(s: string, i: number): (number, number)
	local byte = string.byte(s, i)
	if byte < 0x80 then
		return 1, 1
	elseif byte >= 0xC0 and byte < 0xE0 then
		return 2, 2
	elseif byte >= 0xE0 and byte < 0xF0 then
		return 3, 2
	elseif byte >= 0xF0 then
		return 4, 2
	end
	return 1, 1
end

local function widthOf(s: string): number
	local width = 0
	local i = 1
	while i <= #s do
		local size, w = nextCharAt(s, i)
		width += w
		i += size
	end
	return width
end

local function truncate(s: string, maxColumns: number): string
	if widthOf(s) <= maxColumns then
		return s
	end
	local out, column, i = {}, 0, 1
	while i <= #s do
		local size, w = nextCharAt(s, i)
		if column + w > maxColumns - 1 then
			break
		end
		table.insert(out, string.sub(s, i, i + size - 1))
		column += w
		i += size
	end
	return table.concat(out) .. "…"
end

local function fmtElapsed(ms: number): string
	local seconds = math.floor(math.max(0, ms) / 1000)
	if seconds < 90 then
		return tostring(seconds) .. "s"
	end
	local minutes = math.floor(seconds / 60)
	if minutes < 120 then
		return tostring(minutes) .. "m"
	end
	local hours = math.floor(minutes / 60)
	if hours < 48 then
		return string.format("%dh", hours)
	end
	return string.format("%dd", math.floor(hours / 24))
end

local function fmtAgo(ms: number): string
	return fmtElapsed(ms) .. " ago"
end

local function basename(path: string?): string
	if not path or path == "" then
		return ""
	end
	local clean = (string.gsub(path, "/+$", ""))
	return string.match(clean, "([^/]+)$") or clean
end

-------------------------------------------------------------- module stats

-- The widget bar re-renders without `cx`; it reads this cache, which the
-- tick/event handlers refresh. Panes' stats ride the same gather call.
local hudStats = { working = 0, done = 0, total = 0 }

---------------------------------------------------------------- gathering

local function gather(cx: any): ({ any }, { [number]: any }, { [number]: any }, number?, number?, { [number]: string }, number?)
	local okAgents, agents = pcall(function()
		return cx.agents:list()
	end)
	if not okAgents or type(agents) ~= "table" then
		agents = {}
	end


	local panes: { [number]: any } = {}
	local okPanes, paneList = pcall(function()
		return cx.session:panes()
	end)
	if okPanes and type(paneList) == "table" then
		for _, pane in paneList do
			panes[pane.pane] = pane
		end
	end
	local tabs: { [number]: any } = {}
	local okTabs, tabList = pcall(function()
		return cx.session:tabs()
	end)
	if okTabs and type(tabList) == "table" then
		for _, tab in tabList do
			tabs[tab.id] = tab
		end
	end
	local focused = cx.session:focused()
	local focusedTab: number? = nil
	if focused then
		local okTab, tab = pcall(function()
			return cx.session:tab_of(focused)
		end)
		if okTab then
			focusedTab = tab
		end
	end
	local sessionNames: { [number]: string } = {}
	local currentSession: number? = nil
	local okSessions, sessionList = pcall(function()
		return cx.sessions:list()
	end)
	if okSessions and type(sessionList) == "table" then
		for _, session in sessionList do
			if type(session) == "table" and type(session.id) == "number" and type(session.name) == "string" then
				sessionNames[session.id] = session.name
				if session.current then
					currentSession = session.id
				end
			end
		end
	end
	return agents, panes, tabs, focused, focusedTab, sessionNames, currentSession
end

local function tickFirstSeen(pane: number): number
	local seen: { [string]: number } = tern.kv.get("agent-hud.first-seen") or {}
	local key = tostring(pane)
	local at = seen[key]
	if not at then
		at = tern.now()
		seen[key] = at
		tern.kv.set("agent-hud.first-seen", seen)
	end
	return at
end

local function pruneFirstSeen(live: { [number]: boolean })
	local seen: { [string]: number } = tern.kv.get("agent-hud.first-seen") or {}
	local changed = false
	for key in seen do
		if not live[tonumber(key) or -1] then
			seen[key] = nil
			changed = true
		end
	end
	if changed then
		tern.kv.set("agent-hud.first-seen", seen)
	end
end

local function finishedRecord(pane: any): { result: string, at: number }?
	if type(pane) ~= "number" then
		return nil
	end
	local records: { [string]: any } = tern.kv.get("agent-hud.finished") or {}
	local record = records[tostring(pane)]
	if type(record) == "table" and type(record.at) == "number" then
		return record
	end
	return nil
end

local function recordFinished(pane: number, result: string)
	local records: { [string]: any } = tern.kv.get("agent-hud.finished") or {}
	records[tostring(pane)] = { result = result, at = tern.now() }
	-- Keep the store bounded; oldest records drop first.
	local entries = {}
	for key, record in records do
		table.insert(entries, { key = key, at = type(record) == "table" and record.at or 0 })
	end
	if #entries > 128 then
		table.sort(entries, function(a, b)
			return a.at < b.at
		end)
		for i = 1, #entries - 128 do
			records[entries[i].key] = nil
		end
	end
	tern.kv.set("agent-hud.finished", records)
end

-------------------------------------------------------------- node building

local DOT: { [string]: string } = {
	working = "ahud-live",
	waiting_input = "ahud-live ahud-wait",
	done = "ahud-live ahud-done",
	idle = "ahud-live ahud-idle",
}
local SECTIONS: { { key: string, label: string } } = {
	{ key = "working", label = "Working" },
	{ key = "waiting_input", label = "Waiting on you" },
	{ key = "done", label = "Done" },
	{ key = "idle", label = "Idle" },
}

local function el(tag: string, props: any?, children: any?): any
	return tern.ui.el(tag, props, children)
end

local function agentIcon(agent: any): string
	local program = type(agent.program) == "string" and agent.program or ""
	if program == "" then
		local command = type(agent.command) == "string" and agent.command or ""
		local first = string.match(command, "^%S+") or ""
		program = basename(first)
	end
	program = string.lower(program)
	if program == "pi" or program == "omp" or program == "pi-mono" or string.find(program, "omp", 1, true) then
		return "π"
	end
	local firstLetter = string.match(program, "[%w]")
	if not firstLetter then
		return "◆"
	end
	return string.upper(firstLetter)
end

local function secondLine(agent: any, title: string): string
	local stripped = string.match(title, "^[%S]- %- (.+)$")
	if stripped and stripped ~= "" then
		return stripped
	end
	local cwd = type(agent.cwd) == "string" and agent.cwd or ""
	if cwd ~= "" then
		return basename(cwd)
	end
	return sanitize(agent.model or "")
end

local function rowNode(cx: any, agent: any, pane: any, tabs: { [number]: any }, focusedTab: number?, sessionNames: { [number]: string }, currentSession: number?): any
	local paneTitle = type(pane) == "table" and type(pane.title) == "string" and pane.title or nil
	local title = sanitize(paneTitle or agent.command or ("agent " .. tostring(agent.pane)))
	local line1 = truncate(title, 44)
	local line2 = truncate(sanitize(secondLine(agent, title)), 54)
	local record = finishedRecord(agent.pane)
	local section = agent.state
	local elapsed
	if agent.state == "working" or agent.state == "waiting_input" then
		if type(pane) == "table" and type(pane.running) == "table" and type(pane.running.ms) == "number" and pane.running.ms > 0 then
			elapsed = fmtElapsed(pane.running.ms)
		else
			elapsed = fmtElapsed(tern.now() - tickFirstSeen(agent.pane))
		end
	elseif (agent.state == "idle" and record ~= nil) or agent.state == "exited" then
		section = "done"
		-- No recorded time: show the row without inventing one.
		elapsed = if record then fmtAgo(tern.now() - record.at) else ""
	else
		section = "idle"
		elapsed = fmtElapsed(tern.now() - tickFirstSeen(agent.pane))
	end
	local location = "Elsewhere"
	local tabId = type(pane) == "table" and pane.tab or nil
	if focusedTab and tabId == focusedTab then
		location = "Here"
	elseif type(tabId) == "number" then
		local tab = tabs[tabId]
		location = if tab and type(tab.position) == "number" then "Tab " .. tostring(tab.position) else "Tab"
	end
	local agentSession = type(agent.session) == "number" and agent.session or nil
	if currentSession and agentSession and agentSession ~= currentSession then
		location = location .. " · " .. (sessionNames[agentSession] or "session")
	end
	local dot = DOT[section] or "ahud-live ahud-idle"
	return el("div", {
		class = "ahud-row",
		title = if type(agent.cwd) == "string" and agent.cwd ~= "" then agent.cwd else title,
		actions = { click = "go=" .. tostring(agent.pane) },
	}, {
		el("div", { class = "ahud-ic" }, {
			el("span", { text = agentIcon(agent) }),
			el("span", { class = dot }),
		}),
		el("div", { class = "ahud-main" }, {
			el("div", { class = "ahud-l1", text = line1 }),
			el("div", { class = "ahud-l2", text = line2 }),
		}),
		el("div", { class = "ahud-right" }, {
			el("div", { class = "ahud-elapsed", text = elapsed }),
			el("div", { class = "ahud-loc", text = location }),
		}),
	})
end

local function buildView(cx: any): (any, any, number)
	local agents, panes, tabs, _focused, focusedTab, sessionNames, currentSession = gather(cx)
	local groups: { [string]: { any } } = {}
	for _, section in SECTIONS do
		groups[section.key] = {}
	end
	for _, agent in agents do
		if type(agent) == "table" then
			local bucket: string? = nil
			if agent.state == "working" or agent.state == "waiting_input" then
				bucket = agent.state
			elseif agent.state == "idle" or agent.state == "exited" then
				local record = finishedRecord(agent.pane)
				if record or agent.state == "exited" then
					bucket = "done"
				else
					bucket = "idle"
				end
			end
			if bucket then
				table.insert(groups[bucket], agent)
			end
		end
	end
	for key, bucket in groups do
		if key == "done" then
			table.sort(bucket, function(a, b)
				local ra, rb = finishedRecord(a.pane), finishedRecord(b.pane)
				return (ra and ra.at or 0) > (rb and rb.at or 0)
			end)
		else
			table.sort(bucket, function(a, b)
				local pa, pb = panes[a.pane], panes[b.pane]
				local ra, rb = pa and type(pa.running) == "table" and pa.running.ms or -1, pb and type(pb.running) == "table" and pb.running.ms or -1
				return ra > rb
			end)
		end
	end
	local visible = 0
	for _, bucket in groups do
		visible += #bucket
	end
	local working = #groups.working
	local done = #groups.done
	local total = visible
	local livePanes: { [number]: boolean } = {}
	for _, bucket in groups do
		for _, agent in bucket do
			if type(agent.pane) == "number" then
				livePanes[agent.pane] = true
			end
		end
	end
	pcall(pruneFirstSeen, livePanes)
	hudStats.working = working
	hudStats.done = done
	hudStats.total = total

	local children: { any } = {
		el("div", { class = "ahud-head" }, {
			el("span", { class = "ahud-sparkle", text = "✦" }),
			el("span", { class = "ahud-title", text = "Agents" }),
			el("span", { class = "ahud-count", text = tostring(total) }),
		}),
		el("div", { class = "ahud-bar" }, {
			el("i", { class = "ahud-fill-work" }),
			el("i", { class = "ahud-fill-done" }),
		}),
		el("div", { class = "ahud-working" }, {
			el("span", { class = if working > 0 then "ahud-wd" else "ahud-wd ahud-wd-off" }),
			el("span", { text = tostring(working) .. " working" }),
			el("span", { class = "ahud-dd" }),
			el("span", { text = tostring(done) .. " done" }),
		}),
	}
	if total == 0 then
		table.insert(children, el("div", { class = "ahud-empty", text = "没有运行中的 agent" }))
	else
		for _, section in SECTIONS do
			local bucket = groups[section.key]
			if #bucket > 0 then
				table.insert(children, el("div", { class = "ahud-sec" }, {
					el("span", { text = section.label }),
					el("span", { text = tostring(#bucket) }),
				}))
				local rows: { any } = {}
				for _, agent in bucket do
					table.insert(rows, rowNode(cx, agent, panes[agent.pane], tabs, focusedTab, sessionNames, currentSession))
				end
				table.insert(children, el("div", { class = "ahud-list" }, rows))
			end
		end
	end

	local fillWork = if total > 0 then math.clamp(working / total * 100, 0, 100) else 0
	local fillDone = if total > 0 then math.clamp(done / total * 100, 0, 100) else 0
	tern.css("agents-hud", HUDCSS .. string.format("\n.ahud-fill-work { width: %.2f%%; }\n.ahud-fill-done { width: %.2f%%; }\n", fillWork, fillDone))

	table.insert(children, el("div", { class = "ahud-foot" }, {
		el("span", { class = "ahud-hint", text = "Click an agent to go to it" }),
		el("div", { class = "ahud-new" }, {
			tern.ui.button("+ New agent", { variant = "quiet", size = "sm", actions = { click = "new" } }),
		}),
	}))

	return el("div", { class = "ahud" }, children), nil, total
end

-------------------------------------------------------------------- css

-- The css lives here; the variable is declared at the top of the file.
HUDCSS = [[
.ahud { display: flex; flex-direction: column; gap: 6px; padding: 6px 4px 4px; width: 400px; max-width: 100%; }
.ahud-head { display: flex; align-items: center; gap: 7px; padding: 0 4px; }
.ahud-sparkle { color: var(--accent); font-size: 12px; line-height: 1; background: var(--l1); border-radius: 6px; padding: 4px 6px; }
.ahud-title { font-weight: 650; font-size: 14px; letter-spacing: .01em; color: var(--t1); }
.ahud-count { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--t3); font-size: 12px; font-weight: 600; }
.ahud-bar { display: flex; height: 3px; border-radius: 2px; background: var(--l1); overflow: hidden; margin: 0 4px; }
.ahud-bar i { display: block; height: 100%; }
.ahud-fill-work { background: var(--accent); }
.ahud-fill-done { background: var(--ok); }
.ahud-working { display: flex; align-items: center; gap: 7px; padding: 0 4px; color: var(--t2); font-size: 12px; min-height: 18px; }
.ahud-wd, .ahud-dd { flex: none; width: 7px; height: 7px; border-radius: 50%; }
.ahud-wd { background: var(--accent); }
.ahud-wd-off { background: var(--t3); opacity: .45; }
.ahud-dd { background: var(--ok); }
.ahud-sec { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 7px 4px 3px; border-top: 1px solid var(--l1); color: var(--t3); font-size: 10.5px; font-weight: 700; letter-spacing: .07em; }
.ahud-list { display: flex; flex-direction: column; gap: 1px; }
.ahud-row { display: flex; align-items: center; gap: 10px; padding: 6px 6px; border-radius: 8px; cursor: pointer; }
.ahud-row:hover { background: var(--l1); }
.ahud-ic { position: relative; flex: none; width: 26px; height: 26px; border-radius: 7px; background: var(--l1); display: flex; align-items: center; justify-content: center; font-family: var(--mono); font-size: 14px; color: var(--t1); }
.ahud-live { position: absolute; right: -3px; bottom: -3px; width: 9px; height: 9px; border-radius: 50%; background: var(--accent); }
.ahud-wait { background: var(--warn); }
.ahud-done { background: var(--ok); }
.ahud-idle { background: var(--t3); opacity: .55; }
.ahud-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.ahud-l1 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 13px; line-height: 17px; color: var(--t1); }
.ahud-l2 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 11px; line-height: 14px; color: var(--t3); }
.ahud-right { flex: none; text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
.ahud-elapsed { font-variant-numeric: tabular-nums; font-size: 12px; line-height: 16px; color: var(--t2); }
.ahud-loc { font-size: 11px; line-height: 13px; color: var(--t3); }
.ahud-empty { padding: 16px 8px; text-align: center; color: var(--t3); font-size: 12px; }
.ahud-foot { display: flex; align-items: center; gap: 8px; padding: 8px 4px 2px; margin-top: 2px; border-top: 1px solid var(--l1); }
.ahud-hint { color: var(--t3); font-size: 12px; }
.ahud-new { margin-left: auto; }
]]

------------------------------------------------------------ canvas handling

local function findHud(cx: any): any?
	local ok, list = pcall(function()
		return cx.canvas:list()
	end)
	if not ok or type(list) ~= "table" then
		return nil
	end
	for _, canvas in list do
		if canvas.owner == tern.plugin.id and canvas.about == ABOUT then
			return canvas
		end
	end
	return nil
end

local function closeHud(cx: any, pane: number?)
	local target = pane
	if not target then
		local found = findHud(cx)
		target = found and found.pane
	end
	if target then
		pcall(function()
			cx.layout:close(target)
		end)
	end
end

local function openHud(cx: any)
	local anchor = cx.session:focused()
	local okView, view = pcall(buildView, cx)
	if not okView then
		cx:toast("error", "Agents 面板未打开", tostring(view))
		return
	end
	local ok, pane, err = pcall(function()
		return cx.canvas:open({
			title = "Agents",
			about = ABOUT,
			view = view,
			how = "beside",
			focus = true,
		})
	end)
	if not ok or not pane then
		cx:toast("error", "Agents 面板未打开", tostring(err or pane))
		return
	end
	-- A glimpse panel is see-through to clicks, so the HUD opens as a beside
	-- canvas and floats in the top-right corner instead: small, movable,
	-- fully interactive, closed with Esc or the window's close button.
	local okFloat, floated = pcall(function()
		return cx.layout:float(pane, anchor, "tr", { size = { 0.32, 0.4 }, focus = true })
	end)
	if not okFloat then
		tern.log.warn("agent-hud: float raised:", tostring(floated))
	else
		pcall(function()
			cx.layout:focus(pane)
		end)
	end
end

local function toggleHud(cx: any)
	local existing = findHud(cx)
	if existing then
		closeHud(cx, existing.pane)
		return
	end
	openHud(cx)
end

local function refreshHud(cx: any)
	local existing = findHud(cx)
	if not existing then
		return
	end
	local view = buildView(cx)
	pcall(function()
		cx.canvas:set(existing.pane, { title = "Agents", about = ABOUT, view = view })
	end)
end

--------------------------------------------------------------- commands

local function newAgent(cx: any)
	local focused = cx.session:focused()
	local cwd: string? = nil
	if focused then
		local ok, info = pcall(function()
			return cx.session:pane(focused)
		end)
		if ok and type(info) == "table" and type(info.cwd) == "string" and info.cwd ~= "" then
			cwd = info.cwd
		end
	end
	cwd = cwd or tern.getenv("HOME") or "/"
	cx:prompt({ placeholder = "让 agent 做什么…", action = "Start" }):next(function(text, err, currentCx)
		if not currentCx then
			return
		end
		if err or type(text) ~= "string" or sanitize(text) == "" then
			return
		end
		local ok, failure = pcall(function()
			return currentCx.agents:start({ prompt = text, cwd = cwd })
		end)
		if ok then
			currentCx:toast("success", "已启动 agent", cwd)
			closeHud(currentCx)
		else
			currentCx:toast("error", "启动 agent 失败", tostring(failure))
		end
	end)
end

tern.command({
	id = "toggle",
	title = "Agents 面板",
	icon = "sparkle",
	available = function(_cx) return false end,
	run = toggleHud,
})

---------------------------------------------------------------- events

tern.on("canvas_action", function(ev: any, cx: any)
	if type(ev) ~= "table" or ev.owner ~= tern.plugin.id then
		return nil
	end
	if ev.act == "go" and ev.value then
		local pane = tonumber(ev.value)
		if pane then
			pcall(function()
				cx.layout:focus(pane)
			end)
		end
		if type(ev.pane) == "number" then
			closeHud(cx, ev.pane)
		end
		return true
	end
	if ev.act == "new" then
		newAgent(cx)
		return true
	end
	return nil
end)

tern.on("agent_finished", function(ev: any, cx: any)
	if type(ev) == "table" and type(ev.pane) == "number" and type(ev.result) == "string" then
		pcall(recordFinished, ev.pane, ev.result)
	end
	local ok = pcall(refreshHud, cx)
	if ok then
		pcall(tern.chrome.refresh)
	end
end)

for _, event in { "agent_state", "pane_created", "pane_closed", "focus" } do
	tern.on(event, function(_ev, cx)
		local ok = pcall(refreshHud, cx)
		if ok then
			pcall(tern.chrome.refresh)
		end
	end)
end

local function tick(cx: any?)
	if cx then
		local ok, failure = pcall(refreshHud, cx)
		if not ok then
			tern.log.warn("agent-hud: refresh failed:", failure)
		else
			pcall(tern.chrome.refresh)
		end
	end
	tern.timer(TICK_MS, tick)
end
tern.timer(0, tick)

---------------------------------------------------------------- widget

tern.chrome.widget("agents", {
	name = "Agents 面板",
	description = "运行中的 agent 数量；点击打开或关闭悬浮面板",
	icon = "sparkle",
	styles = { "text" },
	render = function(_pane)
		local working = hudStats.working
		return {
			text = tostring(working) .. " working",
			tone = if working > 0 then "warn" else "muted",
			title = string.format("%d 个 agent 在工作，%d 个已完成 — 点击打开面板", working, hudStats.done),
			command = "plugin.agent-hud.toggle",
		}
	end,
})
