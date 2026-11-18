-- Extract session resume commands, URLs/links, and file paths from terminal lines.
-- Compatible with both Tern's Luau VM and LuaJIT for unit tests.

local Extract = {}

local SCHEMES = {
	http = true,
	https = true,
	ssh = true,
	file = true,
	ws = true,
	wss = true,
	ftp = true,
}

local ROOT_DIRS = {
	tmp = true,
	etc = true,
	var = true,
	opt = true,
	usr = true,
	bin = true,
	sbin = true,
	dev = true,
	proc = true,
	sys = true,
	home = true,
	Users = true,
	Library = true,
	Applications = true,
	System = true,
	Volumes = true,
	private = true,
	nix = true,
	run = true,
	mnt = true,
	srv = true,
	root = true,
}

local REPO_DIRS = {
	src = true,
	lib = true,
	crates = true,
	tests = true,
	test = true,
	packages = true,
	apps = true,
	bin = true,
	scripts = true,
	docs = true,
	config = true,
	include = true,
	cmd = true,
	pkg = true,
	internal = true,
	dist = true,
	build = true,
	target = true,
	out = true,
}

local EXTENSIONS = {
	py = true, luau = true, lua = true, rs = true, ts = true, tsx = true,
	js = true, jsx = true, mjs = true, cjs = true, json = true, jsonl = true,
	toml = true, yaml = true, yml = true, md = true, txt = true, log = true,
	sh = true, bash = true, zsh = true, fish = true, c = true, cc = true,
	cpp = true, cxx = true, h = true, hpp = true, cu = true, cuh = true,
	sv = true, svh = true, v = true, vh = true, rdl = true, sql = true,
	db = true, sqlite = true, html = true, css = true, scss = true, xml = true,
	svg = true, png = true, jpg = true, jpeg = true, gif = true, webp = true,
	pdf = true, zip = true, tar = true, gz = true, tgz = true, xz = true,
	zst = true, deb = true, rpm = true, dmg = true, pkg = true, pcap = true,
	pklg = true, conf = true, ini = true, cfg = true, lock = true, patch = true,
	diff = true, proto = true, graphql = true, go = true, java = true,
	kt = true, swift = true, rb = true, pl = true, pm = true, zig = true,
	cmake = true, mk = true,
}

-- Extensions that also appear as domain TLDs: standalone filenames (without '/')
-- with these extensions must have only one dot (e.g. `run.sh`, not `example.com`).
local TLD_EXTENSIONS = {
	sh = true, rs = true, py = true, md = true, pl = true, js = true, ts = true,
	cc = true, zip = true, dev = true,
}

local DOTFILES = {
	[".zshrc"] = true,
	[".zprofile"] = true,
	[".zshenv"] = true,
	[".bashrc"] = true,
	[".bash_profile"] = true,
	[".gitconfig"] = true,
	[".gitignore"] = true,
	[".env"] = true,
}

local RESUME_SPECS = {
	{ pat = "()pi%s+%-%-session([=%s]+)", prefix = "pi --session " },
	{ pat = "()claude%s+%-%-resume([=%s]+)", prefix = "claude --resume " },
	{ pat = "()codex%s+resume([=%s]+)", prefix = "codex resume " },
	{ pat = "()codex%s+%-%-resume([=%s]+)", prefix = "codex resume " },
}

local function count_char(s, ch)
	local n = 0
	local b = string.byte(ch, 1)
	for i = 1, #s do
		if string.byte(s, i) == b then
			n = n + 1
		end
	end
	return n
end

local function overlaps(spans, s, e)
	for _, span in ipairs(spans) do
		if s <= span.e and e >= span.s then
			return true
		end
	end
	return false
end

local function trim_url(url)
	while #url > 0 do
		local last = string.sub(url, -1)
		if string.find(last, "^[.,;:!'%?]$") then
			if last == "?" and string.find(url, "?", 1, true) < #url then
				break
			end
			url = string.sub(url, 1, -2)
		elseif last == ")" and count_char(url, "(") < count_char(url, ")") then
			url = string.sub(url, 1, -2)
		elseif last == "]" and count_char(url, "[") < count_char(url, "]") then
			url = string.sub(url, 1, -2)
		elseif last == "}" and count_char(url, "{") < count_char(url, "}") then
			url = string.sub(url, 1, -2)
		else
			break
		end
	end
	return url
end

local function scan_resumes(line, spans, out)
	for _, spec in ipairs(RESUME_SPECS) do
		local init = 1
		while init <= #line do
			local s, sep, after = string.match(line, spec.pat .. "()", init)
			if not s then
				break
			end
			local prev = if_or(s > 1, string.sub(line, s - 1, s - 1), "")
			local bin_prefix = s > 4 and string.sub(line, s - 4, s - 1) == "bin/"
			if prev == "" or bin_prefix or not string.find(prev, "^[%w_%-./]$") then
				local first = string.sub(line, after, after)
				local arg, arg_e
				if first == '"' or first == "'" then
					local _, qe = string.find(line, "^" .. first .. "[^" .. first .. "\r\n]+" .. first, after)
					if qe then
						arg = string.sub(line, after, qe)
						arg_e = qe
					end
				else
					local _, te = string.find(line, "^[%w_~./][%w_%-./:~+@]*", after)
					if te then
						local token = string.sub(line, after, te)
						token = string.gsub(token, "[.,;:!?)%]}]+$", "")
						if #token >= 2 and not string.find(token, "^%-") and string.find(token, "[%d_%-./]") then
							arg = token
							arg_e = after + #token - 1
						end
					end
				end
				if arg and arg_e and not overlaps(spans, s, arg_e) then
					local item = { s = s, e = arg_e, value = spec.prefix .. arg, kind = "resume" }
					table.insert(spans, item)
					table.insert(out, item)
					init = arg_e + 1
				else
					init = after
				end
			else
				init = after
			end
		end
	end
end

-- Helper so we don't rely on Luau-only `if a then b else c` in LuaJIT tests.
function if_or(cond, a, b)
	if cond then
		return a
	end
	return b
end

local function scan_links(line, spans, out)
	local init = 1
	while init <= #line do
		local s, e, scheme = string.find(line, "([%a][%w+.-]*)://", init)
		if not s then
			break
		end
		local lower = string.lower(scheme)
		local prev = if_or(s > 1, string.sub(line, s - 1, s - 1), "")
		if SCHEMES[lower] and (prev == "" or not string.find(prev, "^[%w_]$")) then
			local cur = e + 1
			while cur <= #line do
				local b = string.byte(line, cur)
				-- Printable ASCII 0x21..0x7E excluding < > " ' `
				if b < 33 or b > 126 or b == 34 or b == 39 or b == 60 or b == 62 or b == 96 then
					break
				end
				cur = cur + 1
			end
			local raw_url = string.sub(line, s, cur - 1)
			local url = trim_url(raw_url)
			local url_e = s + #url - 1
			local rest = string.sub(url, #scheme + 4)
			if #rest >= 2 and string.find(rest, "^[%w%[/~.]") and not overlaps(spans, s, url_e) then
				local item = { s = s, e = url_e, value = url, kind = "link" }
				table.insert(spans, item)
				table.insert(out, item)
			end
			init = cur
		else
			init = e + 1
		end
	end
end

local function has_known_extension(base)
	local name, ext = string.match(base, "([^/]+)%.([%a][%w]*)$")
	if not name or not ext then
		return false, nil
	end
	local lower = string.lower(ext)
	return EXTENSIONS[lower] == true, lower
end

local function valid_relative_slashes(base)
	local slashes = 0
	for part in string.gmatch(base, "[^/]+") do
		if string.find(part, "^%d+$") or not string.find(part, "^[%w_.@~]") then
			return false, 0
		end
		slashes = slashes + 1
	end
	return slashes >= 2, slashes - 1
end

local function parse_path_candidate(raw)
	-- Reject ellipsized/truncated paths like `/home/scratch.user_gpu/...` or `claude-...`
	-- before stripping trailing punctuation, so they never turn into a parent directory.
	if string.find(raw, "...", 1, true) then
		return nil
	end
	-- Trim a single trailing sentence punctuation mark.
	raw = string.gsub(raw, "[.,;:]$", "")
	raw = string.gsub(raw, "^[:+]+", "")
	if #raw < 2 or string.find(raw, "^%-") or string.find(raw, "%.%.$") then
		return nil
	end
	-- If `prefix:path` where `prefix` has no '/' and `path` has '/', take `path`
	-- (e.g. `pi-background-tasks@latest:dist/extensions/foo.js` or `host:/path/to/file`).
	local prefix, after_prefix = string.match(raw, "^([^/:]+):(.+)$")
	if prefix and after_prefix and prefix ~= "~" and prefix ~= "." and prefix ~= ".." then
		if string.find(after_prefix, "/", 1, true) then
			raw = after_prefix
		end
	end

	-- Separate `base` and optional `:line[:col]` suffix.
	local base = raw
	local suffix = ""
	local colon = string.find(raw, ":", 1, true)
	if colon then
		base = string.sub(raw, 1, colon - 1)
		local rest = string.sub(raw, colon + 1)
		local l, c = string.match(rest, "^(%d+):(%d+)$")
		if not l then
			l, c = string.match(rest, "^(%d+):(%d+):.*$")
		end
		if l and c then
			suffix = ":" .. l .. ":" .. c
		else
			local l1 = string.match(rest, "^(%d+)$") or string.match(rest, "^(%d+):.*$")
			if l1 then
				suffix = ":" .. l1
			end
		end
	end

	-- Strip trailing `/` or `.` from `base`.
	base = string.gsub(base, "[/.]+$", "")
	if #base < 2 or not string.find(base, "[%a]") then
		return nil
	end
	if string.find(base, "//", 1, true) or string.find(base, "...", 1, true) then
		return nil
	end
	-- `~` is only valid at index 1 as `~/...`.
	local tilde = string.find(base, "~", 1, true)
	if tilde and (tilde ~= 1 or string.sub(base, 2, 2) ~= "/") then
		return nil
	end

	-- 1. Explicit prefix: `/`, `~/`, `./`, `../`
	if string.find(base, "^~/") or string.find(base, "^%.?%./") then
		if not string.find(base, "[%w_]") then
			return nil
		end
		return base .. suffix
	end
	if string.sub(base, 1, 1) == "/" then
		local first_seg, more = string.match(base, "^/([^/]+)(/.*)$")
		if first_seg and more then
			if string.find(first_seg, "^%d+$") then
				return nil
			end
			return base .. suffix
		end
		local single = string.match(base, "^/([%w_%-.]+)$")
		if single and (ROOT_DIRS[single] or has_known_extension(base)) then
			return base .. suffix
		end
		return nil
	end

	-- 2. Relative path containing `/`
	if string.find(base, "/", 1, true) then
		local ok, slash_count = valid_relative_slashes(base)
		if not ok then
			return nil
		end
		local has_ext = has_known_extension(base)
		local first_dir = string.match(base, "^([^/]+)/")
		if has_ext or string.sub(base, 1, 1) == "." or (first_dir and REPO_DIRS[first_dir] and slash_count >= 1) then
			return base .. suffix
		end
		return nil
	end

	-- 3. Standalone filename (no `/`)
	if DOTFILES[base] then
		return base .. suffix
	end
	if string.sub(base, 1, 1) == "." or string.find(base, "@", 1, true) then
		return nil
	end
	local has_ext, ext = has_known_extension(base)
	if not has_ext then
		return nil
	end
	if TLD_EXTENSIONS[ext] and count_char(base, ".") > 1 then
		return nil
	end
	-- Reject version strings like `v1.2.3.txt` or numeric prefixes.
	local stem = string.match(base, "^(.+)%.[%a][%w]*$")
	if not stem or not string.find(stem, "[%a]") or string.find(stem, "^v?%d+[.%d]*$") then
		return nil
	end
	return base .. suffix
end

local function scan_paths(line, spans, out)
	local init = 1
	while init <= #line do
		local s, e = string.find(line, "[%w_%-./~+@:]+", init)
		if not s then
			break
		end
		if not overlaps(spans, s, e) then
			-- Skip tokens immediately followed by UTF-8 ellipsis `…` (\226\128\166).
			if string.sub(line, e + 1, e + 3) ~= "\226\128\166" then
				local raw = string.sub(line, s, e)
				local parsed = parse_path_candidate(raw)
				if parsed then
					local item = { s = s, e = e, value = parsed, kind = "path" }
					table.insert(spans, item)
					table.insert(out, item)
				end
			end
		end
		init = e + 1
	end
end

function Extract.from_line(line)
	local spans = {}
	local out = {}
	scan_resumes(line, spans, out)
	scan_links(line, spans, out)
	scan_paths(line, spans, out)
	table.sort(out, function(a, b)
		return a.s < b.s
	end)
	return out
end

-- Scans lines from bottom (most recent) to top, deduplicating by exact value.
function Extract.from_lines(lines)
	local seen = {}
	local items = {}
	for i = #lines, 1, -1 do
		local line = lines[i]
		if type(line) == "string" and #line > 0 then
			local matches = Extract.from_line(line)
			-- Reverse within the line or keep left-to-right? Left-to-right on the same line is natural.
			for _, m in ipairs(matches) do
				if not seen[m.value] then
					seen[m.value] = true
					table.insert(items, { value = m.value, kind = m.kind, line = i })
				end
			end
		end
	end
	return items
end

function Extract.from_text(text)
	local lines = {}
	for line in string.gmatch(text .. "\n", "([^\r\n]*)\r?\n") do
		table.insert(lines, line)
	end
	return Extract.from_lines(lines)
end

return Extract
