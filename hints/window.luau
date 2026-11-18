--!strict
local Extract = require("./extract")

local KIND_LABELS: { [string]: string } = {
	resume = "Resume · Enter 复制命令",
	link = "链接 · Enter 复制",
	path = "路径 · Enter 复制",
}

local TOAST_TITLES: { [string]: string } = {
	resume = "已复制 Resume 命令",
	link = "已复制链接",
	path = "已复制路径",
}

local function collectLines(cx: any, pane: number): { string }
	local lines: { string } = {}
	local opts = { as = "table", lines = 200, max_chars = 64000, scrollback = false }
	local ok, read = pcall(function()
		return cx.session:read(pane, opts)
	end)
	if ok and type(read) == "table" then
		if type(read.lines) == "table" then
			for _, line in read.lines do
				if type(line) == "string" then
					table.insert(lines, line)
				end
			end
		end
		if type(read.text) == "string" and read.text ~= "" then
			for line in string.gmatch(read.text .. "\n", "([^\r\n]*)\r?\n") do
				table.insert(lines, line)
			end
		end
		if type(read.path) == "string" and read.path ~= "" then
			table.insert(lines, read.path)
		end
		if type(read.url) == "string" and read.url ~= "" then
			table.insert(lines, read.url)
		end
	end
	-- Also read `as = "text"` when table form had no lines or when a TSP surface is active.
	if #lines == 0 or (ok and type(read) == "table" and read.surface) then
		local okText, text = pcall(function()
			return cx.session:read(pane, { as = "text", lines = 200, max_chars = 64000, scrollback = false })
		end)
		if okText and type(text) == "string" and text ~= "" then
			for line in string.gmatch(text .. "\n", "([^\r\n]*)\r?\n") do
				table.insert(lines, line)
			end
		end
	end
	return lines
end
local function pickHints(cx: any)
	local pane = cx.session:focused()
	if not pane then
		cx:toast("info", "屏幕提取", "当前没有聚焦的 pane")
		return
	end
	local matches = Extract.from_lines(collectLines(cx, pane))
	if #matches == 0 then
		cx:toast("info", "未找到可提取内容", "当前屏幕没有匹配的路径、链接或 Resume 命令")
		return
	end
	local items = {}
	for _, m in matches do
		table.insert(items, {
			label = m.value,
			detail = KIND_LABELS[m.kind] or m.kind,
			value = m.value,
			kind = m.kind,
		})
	end
	cx:pick({
		items = items,
		placeholder = "搜索路径、链接或 Resume 命令…",
		action = "复制",
	}):next(function(choice, err, currentCx)
		if not currentCx then
			return
		end
		if err then
			currentCx:toast("error", "屏幕提取失败", tostring(err))
			return
		end
		if not choice then
			return
		end
		currentCx:copy(choice.value)
		currentCx:toast("success", TOAST_TITLES[choice.kind] or "已复制", choice.value)
	end)
end

tern.command({
	id = "pick",
	title = "提取屏幕内容（路径／链接／Resume）",
	run = pickHints,
})

tern.bind("ctrl+shift+h", "plugin.hints.pick")

tern.carly.export("extract", {
	sig = "extract() -> {HintItem}",
	doc = "提取当前聚焦 pane 中的路径、链接和 pi/claude/codex 恢复命令（从下往上按最近出现排序）。",
}, function(cx)
	local pane = cx.session:focused()
	if not pane then
		return {}
	end
	return Extract.from_lines(collectLines(cx, pane))
end)
