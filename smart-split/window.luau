--!strict
-- Ctrl+Shift+Enter: split the focused pane's longer pixel dimension in half.
-- Preference: prefer a horizontal split (split_down) whenever the pane still
-- has at least MIN_ROWS text rows, so both sides stay usable. Below that,
-- fall back to the longer pixel dimension. Rows are taken from PaneInfo when
-- present (cols/rows), else estimated from the pane's pixel height and the
-- configured font size.
local MIN_ROWS = 46
-- A text row is about 1.15 × the point size in pixels (comic mono at 20px
-- draws 23.5px rows ≈ 1.175 × size). Tunable in case a custom font is far
-- from that; kept here so the estimate is one place to adjust.
local ROW_FACTOR = 1.175
local DEFAULT_FONT_SIZE = 13
-- Native split commands preserve Tern's host, cwd and new-block preferences.

local function nonemptyEnv(name: string): string?
	local value = tern.getenv(name)
	return if value ~= "" then value else nil
end

local executable = nonemptyEnv("TERN_EXE") or "tern"
if executable == "tern" and tern.runtime.os == "macos" then
	local installed = "/Applications/Tern.app/Contents/MacOS/tern"
	if tern.fs.exists(installed) then
		executable = installed
	end
end

-- Window plugins run locally even when their focused pane is on a remote host.
-- Route the CLI through that local daemon and this window's key, not the pane's host.
local configDir = string.match(tern.plugin.data, "^(.*)[/\\][^/\\]+[/\\][^/\\]+$")
local daemonSocket = nonemptyEnv("TERN_DAEMON_SOCKET") or nonemptyEnv("TERN_PANE_SOCKET")
if not daemonSocket and configDir then
	daemonSocket = configDir .. "/daemon.sock"
end

-- An intervening focus change cancels the asynchronous geometry read, even if
-- focus returns to the original pane before it completes.
local focusRevision = 0
tern.on("focus", function()
	focusRevision += 1
end)

-- Best-effort estimate of how many text rows a pane shows. `pane` is a
-- PaneInfo (cols/rows when the host reports them); `rectH` is its pixel
-- height. Returns nil when no estimate is possible.
local function estimatedRows(cx: any, pane: any, rectH: number): number?
	if type(pane) == "table" and type(pane.rows) == "number" and pane.rows > 0 then
		return pane.rows
	end
	local okSize, size = pcall(function()
		return cx.settings:get("terminal.font_size")
	end)
	if not okSize or type(size) ~= "number" or size <= 0 then
		size = DEFAULT_FONT_SIZE
	end
	return rectH / (size * ROW_FACTOR)
end

local function smartSplit(cx: any)
	if not cx.session:focused() then
		cx:toast("info", "没有可分屏的窗格")
		return
	end
	if not daemonSocket then
		cx:toast("error", "智能分屏失败", "无法定位 Tern daemon")
		return
	end
	local focused = cx.session:focused()
	local paneInfo = if focused then cx.session:pane(focused) else nil

	local revision = focusRevision
	local ok, failure = pcall(function()
		-- PaneInfo exposes no pixel bounds in Tern 0.9.18; use its rendered layout.
		tern.process.run({ executable, "ctl", "dump", ".tn-pane.on:not(.off)" }, {
			env = { TERN_PANE_SOCKET = daemonSocket, TERN_WINDOW_KEY = cx:window_key() },
			timeout_ms = 2000,
		}, function(result, currentCx)
			if not currentCx or revision ~= focusRevision then
				return
			end
			if result.status ~= 0 then
				currentCx:toast("error", "智能分屏失败", result.stderr)
				return
			end

			local decoded, snapshot = pcall(tern.json.decode, result.stdout)
			if not decoded or type(snapshot) ~= "table" or snapshot.ok ~= true
				or type(snapshot.elements) ~= "table" or #snapshot.elements ~= 1 then
				currentCx:toast("error", "无法读取当前窗格尺寸")
				return
			end
			local pane = snapshot.elements[1]
			local rect = pane.rect
			if not pane.visible or type(rect) ~= "table"
				or type(rect[3]) ~= "number" or type(rect[4]) ~= "number"
				or rect[3] <= 0 or rect[4] <= 0 then
				currentCx:toast("error", "无法读取当前窗格尺寸")
				return
			end

			local rows = estimatedRows(currentCx, paneInfo, rect[4])
			local direction = "split_right"
			if rows and rows >= MIN_ROWS then
				-- Preference: enough rows for both halves → split horizontally.
				direction = "split_down"
			elseif not rows or rows < MIN_ROWS then
				-- Too few rows: split whichever way keeps the most usable area.
				if rect[3] >= rect[4] then
					direction = "split_right"
				else
					direction = "split_down"
				end
			end
			currentCx:command(direction)
		end)
	end)
	if not ok then
		cx:toast("error", "智能分屏失败", tostring(failure))
	end
end

tern.command({
	id = "split",
	title = "智能分屏",
	available = function(cx)
		return cx.session:focused() ~= nil
	end,
	run = smartSplit,
})

tern.bind("ctrl+shift+enter", "plugin.smart-split.split")
