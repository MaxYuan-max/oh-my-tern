local dir = debug.getinfo(1, "S").source:sub(2):match("^(.*)/tests/") or "."
local Extract = dofile(dir .. "/extract.luau")

local function assert_eq(actual, expected, msg)
	if actual ~= expected then
		error(string.format("%s: expected %q, got %q", msg or "assert_eq", tostring(expected), tostring(actual)), 2)
	end
end

local function values(items, kind)
	local out = {}
	for _, item in ipairs(items) do
		if not kind or item.kind == kind then
			table.insert(out, item.value)
		end
	end
	return out
end

local function assert_list(actual, expected, msg)
	assert_eq(#actual, #expected, (msg or "list") .. " length (" .. table.concat(actual, ", ") .. ")")
	for i = 1, #expected do
		assert_eq(actual[i], expected[i], (msg or "list") .. "[" .. i .. "]")
	end
end

-- 1. Session resume commands: pi --session, claude --resume, codex resume
do
	local text = table.concat({
		"To continue: pi --session 01a0a0de-5e40-766e-a1de-10e4b3fc10d9.",
		"Or run `claude --resume 9376bfb9-cd8f-4140-a621-6851927f390e`",
		'Named session: claude --resume "FD cov"',
		"Path session: ~/.pi/agent/bin/pi --session=/tmp/pi-e2e/session.jsonl --no-mcp",
		"Codex: codex resume 0196a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6b)",
		"Ignore prose: pi --session/claude --resume/codex resume and --resume=<value> or from-claude --resume foo",
	}, "\n")
	local got = values(Extract.from_text(text), "resume")
	assert_list(got, {
		"codex resume 0196a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6b",
		"pi --session /tmp/pi-e2e/session.jsonl",
		'claude --resume "FD cov"',
		"claude --resume 9376bfb9-cd8f-4140-a621-6851927f390e",
		"pi --session 01a0a0de-5e40-766e-a1de-10e4b3fc10d9",
	}, "resume extraction")
end

-- 2. Links / URLs with balanced parens, markdown, CJK punctuation, and no path double-extraction
do
	local text = table.concat({
		"See [docs](https://example.com/docs/guide?x=1&y=2)，以及 (http://127.0.0.1:8376/health).",
		"Wiki: https://en.wikipedia.org/wiki/Rust_(programming_language))",
		"Remote: ssh://build-box/home/user/.config/tern/settings.json;",
	}, "\n")
	local all = Extract.from_text(text)
	assert_list(values(all, "link"), {
		"ssh://build-box/home/user/.config/tern/settings.json",
		"https://en.wikipedia.org/wiki/Rust_(programming_language)",
		"https://example.com/docs/guide?x=1&y=2",
		"http://127.0.0.1:8376/health",
	}, "link extraction")
	-- URL internals must not be re-extracted as paths.
	assert_list(values(all, "path"), {}, "no duplicate path inside link")
end

-- 3. Paths and false-positive rejection (slash commands, fractions, model names, domains)
do
	local text = table.concat({
		"[Context]",
		"  .pi/agent/AGENTS.md",
		"[Prompts]",
		"  /council, /gather-context-and-clarify, /parallel-cleanup, /review-loop",
		"[Extensions]",
		"  @juicesharp/rpiv-todo, branch-minimap.ts, pi-background-tasks@latest:dist/extensions/background-tasks.js,",
		"  pi-intercom@0.18.1, pi-subagents:bundle",
		"Model: vendor/model-a · vendor/model-b",
		"Table: 37/40 | 40/40 | 30/578 | 1/0 | F4b/F6 | ctrl+c/ctrl+d | example.com | 10.0.0.9:8376 | /home/scratch.user_gpu/... | /home/scratch.user_gpu/.tmp/claude-... | /Users/example/src…",
		"Grep: src/tern-connection-status/window.luau:437:10:local state = 1 and tunnel.py:54:process",
		"Dirs: ~/Library/Logs/Tern/tern.log, ./scripts/run.sh, /home/scratch.user_gpu/display_units/hw/nvip, hindsight-research/pilot-step2-results.md。",
	}, "\n")
	local got = values(Extract.from_text(text), "path")
	assert_list(got, {
		"~/Library/Logs/Tern/tern.log",
		"./scripts/run.sh",
		"/home/scratch.user_gpu/display_units/hw/nvip",
		"hindsight-research/pilot-step2-results.md",
		"src/tern-connection-status/window.luau:437:10",
		"tunnel.py:54",
		"branch-minimap.ts",
		"dist/extensions/background-tasks.js",
		".pi/agent/AGENTS.md",
	}, "path extraction")
end

print("ALL TESTS PASSED")
