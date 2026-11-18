# Tern 连接方式

查看每个 Tern 远程主机实际走的通道和延迟，例如 `远端 · 中继 · 467 ms`、`host-a · SSH隧道 · 220 ms`。

命令面板提供 **查看当前连接方式**（当前 pane 所属主机；先读一次最新日志，toast 标题为主机、方式和 RTT，正文为依据和起始时间）、**连接主机**（本机和每台远端主机，远端带方式和 RTT，当前主机打勾；选择本机直接切换，选择远端先检查该主机的 SSH 隧道、坏了才重建，再切换，或从中继换到隧道）、**检查并恢复当前主机 SSH 隧道**（只检查并按需重建，不切换连接）、**查看主机配置状态**（配置路径、加载错误或已加载的主机名）和 **查看内存占用**（与点击内存组件一致）。另有 Carly 导出 `status()`，以及紧邻的 **延迟**、**内存** 两个 widget。插件不注册状态栏段。

- `SSH隧道`：该连接 `way=over UDP to` 本机回环入口：18376 → host-a，28377 → host-b，38376 → host-c；未登记的回环端口显示 `本地转发`。
- `UDP直连`：`way=over UDP to` 非回环地址，或之后从中继升级（`remote connection now direct`）。
- `中继`：`way=through the relay`，或升级后回落（`remote connection back on the relay`）。
- `本机`：本地 pane；其中手动运行的 `ssh` 不属于 Tern remote。
- `路径未知`：当前 Tern 进程日志中没有该主机的连接记录、读取失败、证据超过 15 秒未刷新，或多个已连接主机共用同一日志线程名（例如两个 `127.0.0.1:端口`）。
- 未就绪的连接显示连接中、重连中、连接停滞、待验证或连接失败，不沿用旧选路。
- 延迟是插件 API 给出的该主机连接实时 RTT（`rtt_ms`）；没有时显示“延迟未知”。

## 主机配置

远端主机清单不写死在插件里。把 `hosts.example.json` 复制到
`~/Library/Application Support/Tern/plugin-data/connection-status/hosts.json`，
按自己的 SSH 配置填写。环境变量 `CONNECTION_STATUS_CONFIG` 可指向别处（测试也用它）。

| 字段 | 作用 |
| --- | --- |
| `name` | 显示名，也是内存样本的键；同名主机不会互相串数据。 |
| `address` | Tern 的连接地址，如 `user@host.localhost:18376` 或 endpoint ID。 |
| `fallback` | 同一隧道的第二个固定地址；`address` 本身走中继时改拨它。 |
| `alias`、`port` | 把 `127.0.0.1:端口`、别名和 endpoint ID 认作同一台机器。 |
| `endpoint` | 可选，64 位 endpoint ID；用于识别以 ID 连接的同一条目。 |
| `detail` | **连接主机** 列表里的说明。 |
| `tunnel` | 可选，自动恢复隧道所需。`summary` 是路径说明；`ssh`、`target_host`、`target_port`、`files`、`doctor` 交给 `recover_tunnel.py`。省略则只能手动修复隧道。 |
| `memory` | 可选，内存采样所需。`ssh` 是采集用的 SSH 目标；`kind` 为 `container`（cgroup 计费，含缓存）或 `linux`（`/proc/meminfo`）。省略则该主机不显示内存。 |

顶层 `tunnel_script`（默认 `~/src/tern-ssh-udp/tunnel.py`）和 `container_usage`
（默认 `~/bin/container-usage`）可覆盖辅助脚本位置。`hosts.json` 已被 `.gitignore` 忽略，
仓库只含示例；配置里找不到的主机不会被当作本机采样。

## 版本记录

1.16.0：**延迟**组件吸收 hostping 的展示方式：本机 pane 不再显示 `0 ms`；远端按 80/200 ms 分为 success／warn／error；每台远端保留最近 10 分钟、60 个 10 秒采样点，供 graph 样式绘制；悬停卡改为 Host／RTT／刷新提示加 Refresh 按钮，点击 Refresh 重新读取 Tern 当前连接估计。仍不额外探测，点击 widget 本身继续打开主机切换；RTT 解析仍只读 `cx.hosts:list()`，不经过 `hosts.json`。新增隔离窗口测试覆盖远程解析、本机隐藏、阈值、历史、卡片和 Refresh。

1.15.0：内存卡片改用 Tern 0.9.27 的结构化 widget `card` API，不再把整张布局拼进 `title` 文本。每行仍是原生 `.wgc-row.wgc-plugin-row`，但摘要、双列图例、Top apps 仪表和 40px 页脚由真实 label/text/按钮节点加逐行 CSS 呈现；压力徽章落在 38px 标题带内。进程详情默认收起，点卡片里的“展开”就地切换；最多显示 18 条单行进程并注明“还有 N 个进程”，展开时用进程区替换 Top apps，保证 32 行上限内页脚仍在最后。“Task manager”是真实卡片按钮，调用内置 `new_task_manager_block`。隔离回归改为直接断言 card 行、按钮动作、字体／基线／列位置、折叠与展开顺序及 280pt 窄宽，不再读取旧 title 文本。

1.14.9：主机、隧道和内存口径从代码移到 `plugin-data/connection-status/hosts.json`，仓库只带 `hosts.example.json`；新增 **查看主机配置状态**。原来固定恢复某一条隧道的命令改为 **检查并恢复当前主机 SSH 隧道**，按聚焦 pane 所属主机执行。`memory_probe.py` 与 `recover_tunnel.py` 共用同一份配置；目标不在配置里时直接报错，绝不在本机采样后冒用远端主机名。测试使用中性主机名和临时配置；真实窗口回归改为验证固定卡片、锚点、旧画布清理与 280pt 窄宽，不再断言已移除的图表和副标题。

1.1.0：新增带连接方式的 Session 切换器（1.4.0 已移除）。

1.1.1 修复重连并发连接导致的“路径未知”：识别 `AddConnection(...)` 的首次选路事件，并将选路按客户端共享；只有最后一个活动连接关闭时才清除证据。插件重载不会主动打开选择器。

1.1.2 降低自动刷新计时器负担：SSH 探针命令在加载时构建一次，轮询从每秒调整为每 5 秒，异步回调不再重复枚举主机。此前 Tern 曾因 timer 超过 50 ms 禁用轮询；更新重载后恢复，不能把过去几小时的过期状态当作实时状态。已知本地入口 `127.0.0.1:18376` 显示“SSH隧道”，不标为原生 UDP 直连。

命令面板提供 **检查并恢复当前主机 SSH 隧道**（action：`plugin.connection-status.recover-ssh`）。先检查链路和 Tern 认证：健康时直接返回，保持原 PID、不重连；故障时才停止本机已知隧道、重建入口并验证。端口被未知程序占用时拒绝停止。不重启远端任务、不修改中继；真正重启本地隧道时会短暂中断连接。日志位于 `plugin-data/connection-status/ssh-udp.log`。旧流上限缺陷仍可能复发。

1.1.3：恢复命令以 `--spares 3 --keepalive 5` 启动隧道：预先登录 3 条 SSH，新连接首次应答约 220 ms，快于实测的中继首次应答（0.7–1.7 s）；SSH 每 5 秒保活，失效连接约 15 秒内退出并补充。host-b 隧道实测重连后改走隧道（`way=over UDP`）；已经在中继上的连接不会自动切回，需要手动重连一次。

1.1.4：`host-a.localhost:18376` 与 `127.0.0.1:18376` 一样标为“SSH隧道”。保存的主机改为 `user@host-a.localhost:18376`（host-a）和 `user@host-b.localhost:28377`（host-b），主机选择列表里可区分；两者都解析到 `127.0.0.1`，Tern 只连 IPv4。指纹按填写的主机名匹配，已为两个名字固定原有的同一指纹。

1.1.5：`host-c.localhost:38376`（及 `127.0.0.1:38376`）也标为“SSH隧道”，经 SSH 转发至 host-c 的 `127.0.0.1:8376`。host-c 与 host-a 共用 NFS 家目录，其 Tern 服务使用独立身份与目录（`~/.config/tern-host-c`、`~/.local/state-host-c`），只能用 `~/.local/bin/tern-serve-host-c` 启动；不要在 host-c 上运行默认的 `tern remote serve` 或 `tern remote setup`，否则会占用 host-a 的身份与 daemon 套接字。恢复隧道操作仍只针对 host-a。

注意：`~/Library/Application Support/Tern/hosts` 由 Tern 按当前窗口已连接的主机重写，不是持久书签。断开后，上面的 `*.localhost` 条目会从主机选择列表消失（2026-11-03 13:50:07 实测只剩 endpoint 版 host-a）。

1.1.6：命令面板新增 **连接主机（host-a / host-b / host-c）**（action：`plugin.connection-status.connect`），按名字选择，不用记地址。已连接（隧道名、`127.0.0.1:端口` 或 endpoint ID 任一形式）则直接切换到该主机；未连接则用 `hosts:add` 经对应 SSH 隧道连接（`user@host-a.localhost:18376`、`user@host-b.localhost:28377`、`user@host-c.localhost:38376`），不断开其它主机。指纹已固定，不会要求再确认。可在 settings.json 的 `keybinds` 里为它绑定快捷键。

1.2.0：连接方式改为读取本机 Tern 日志里的选路记录，适配 Tern 0.9.9+ 原生传输（远端已不再记录 iroh `path::selected`，客户端身份也不再是 `iroh:<id>`）。按 endpoint ID 连接的主机（如 `远端`）也能显示 SSH隧道／UDP直连／中继；“查看当前连接方式”的标题带 RTT。移除远端 SSH 日志探针和 `targets.json`，补上 28377（host-b 隧道）。

1.3.0：**连接主机** 发现该机器正走中继时一步换到隧道，按“先连后断”：

1. 选择时先重新读取一次日志，不按列表快照判断。已有不走中继的已连接条目（隧道、直连）则直接切换；只有未就绪条目（重连中、连接失败）则切换过去显示其状态；未连接则经隧道新连。
2. 只有中继连接时，经隧道地址新连一条。中继连接本身就是 `host-a.localhost`／`host-b.localhost` 条目时（常见于切 VPN 后自动重连到中继），再连同一地址只会落回这个主机，所以改拨同一隧道的另一个已固定地址 `127.0.0.1:18376`／`127.0.0.1:28377`；host-c 没有备用地址，只提示、不断开。
3. 读到新连接的 `way=` 后：不走中继就切换过去并断开所有中继连接（远端程序继续运行）；新连接也走了中继就断开新连接、保留原连接；最多约 3 秒仍读不到方式，则两条都保留并提示。连接失败、Tern 把新地址并入原连接或无法切换时，原中继连接都不断开。

列表中每个主机显示当前状态，例如 `正走中继 · 472 ms，选择后经隧道重连并断开中继`、`已连接 · SSH隧道 · 220 ms，选择后切换过去`。

1.4.0：精简入口。移除 **刷新连接方式**（“查看当前连接方式”改为每次先读最新日志）、**检查 host-a SSH 隧道状态**（“恢复”本身先检查，健康时不重启）和 **切换 Session（连接方式）**（“连接主机”已按主机显示方式和 RTT），连同 `tunnel_health.py`、`session_picker.luau`。

1.4.1：“连接主机”列表里方式和 RTT 放在每行说明的最前面，例如 `SSH隧道 · 243 ms · 选择后切换 · SSH 隧道 → host-a`、`中继 · 480 ms · 选择后换到隧道并断开中继 · …`；此前主机说明在前，窄窗口里 RTT 被截断看不到。

1.5.0：**连接主机** 选择后先检查并按需重建该主机的 SSH 隧道，再按 1.3.0 的方式切换或换到隧道。此前它只拨已有的入口：Mac 重启、睡眠或换网后隧道进程不会自己回来，Tern 拨不通 `*.localhost`／`127.0.0.1` 就悄悄回落到中继并显示“已连接”，所以反复断开、重连也修不好。

- 检查由 `recover_tunnel.py <host-a|host-b|host-c>` 完成，**检查并恢复当前主机 SSH 隧道** 共用它（1.1.5 起“只针对 host-a”的限制取消）。入口在监听且 `tern remote doctor` 能经隧道认证：什么都不动，PID 不变、不断线。否则先用新开的非交互 SSH 登录确认主机可达（需要 VPN），可达才停掉旧隧道并以 `--spares 3 --keepalive 5` 重建；每 2 秒轮询 doctor，至多 45 秒，认证通过才算成功。
- 检查未通过（SSH 登录不上、端口被其他程序占用、隧道起不来或认证不过）：toast 给出原因，不连接，也不断开现有连接，避免 Tern 拨到死隧道后回落中继却显示成功。只停止 `--host`、`--port`、`--target-host` 与该隧道完全一致的 `tunnel.py client`，其他占用端口的程序一律不动。
- 每条隧道一把锁（`plugin-data/connection-status/<前缀>-recovery.lock`），不会并发重建；检查期间再选同一主机只提示“正在检查”。检查结束后重新读一次日志再决定切换、新连还是断开中继，不沿用检查前的快照。已有不走中继的已连接条目时直接切换，不检查。
- 耗时：健康时约 5 秒，启动或重建 12–17 秒，最坏约 115 秒（插件在 120 秒时终止脚本）。每条隧道的日志和 PID 前缀取自配置的 `tunnel.files`，位于 `plugin-data/connection-status/<前缀>.{log,pid}`。隧道仍不会随 Mac 重启自启，也没有 LaunchAgent：下一次选择“连接主机”时才重建。

1.6.0：新增两个 widget（Tern 0.9.17+），在命令面板 › Edit widgets… 里放到 widget bar，或在 settings.json 的 `widgets` 里写 `{"id": "connection-status/rtt"}`、`{"id": "connection-status/cwd"}`：

- **主机与延迟**（`connection-status/rtt`）：当前 pane 所属主机和该连接的实时 RTT，例如 `host-a · 247 ms`；未就绪时显示 `host-a · 重连中` 等状态，本地 pane 显示 `本机`。颜色沿用方式的语气（SSH隧道 accent、中继 warn、连接失败 error），悬停显示方式和依据，点击等同 **查看当前连接方式**。
- **目录（含主机）**（`connection-status/cwd`）：仿内置 cwd widget，远端 pane 显示 `host-a  /home/user`，本地只显示目录；超过 40 个字符的路径只留最后两级（`…/hw/nvip`），悬停看完整路径。主机名用隧道名（host-a／host-b／host-c），按隧道地址、`127.0.0.1:端口` 或 endpoint ID 都能认出；其他主机用 Tern 的主机名。插件 widget 只能画一段文字，没有内置 widget 的主机标签框和目录分级灰度。
- widget 是按 pane 缓存的纯格式化函数，主机列表、RTT 和日志读数在 5 秒轮询、pane／tab 事件和每次读日志后用 `tern.chrome.refresh()` 重绘，所以 RTT 最多约 5 秒更新一次。

1.6.1：**目录** widget（`connection-status/cwd`，原名“目录（含主机）”）只显示目录，不再带主机名；主机已在 **主机与延迟** 里显示。

1.7.0：点击 **主机与延迟** widget 打开 **连接主机** 列表来切换主机（此前点击是查看连接方式，该功能仍在命令面板）。列表新增 **本机**，选择后直接切回本地；当前主机打勾；回车动作改名为“切换”。选择远端主机的行为不变（已连接且不走中继则直接切换，否则先检查隧道）。

1.8.0：目录并入 **主机与延迟**：显示 `host-b · 245 ms · /home/user`（本地 `本机 · /Users/example`，未就绪 `host-a · 重连中 · …`），长路径仍只留最后两级，悬停卡片里有完整路径。单独的 **目录** widget（`connection-status/cwd`）删除，已从 widget bar 移除。

1.8.1：悬停卡片精简：远端只写方式（如 `SSH隧道`），路径被截短时再加一行完整路径；本地 pane 只在路径被截短时显示完整路径。去掉依据说明和“点击切换主机”。

1.8.2：目录始终显示完整路径，不再截短；悬停卡片因此只剩方式（本地 pane 无卡片内容）。

1.9.0：点击 **主机与延迟** 弹出的列表（即 **连接主机**）顶部新增“目录”组的 **复制路径**：复制当前 pane 的完整工作目录到剪贴板，并提示“已复制路径”。主机行归入“主机”组；回车动作改为“选择”。插件 widget 的悬停卡片不支持按钮，所以复制放在这个列表里。

1.9.1：删除切换主机列表中误加的“复制路径”及对应处理，列表仅用于切换主机；widget 的完整路径显示保持不变。

1.9.2：widget 去掉目录，仅显示主机名与延迟（如 `host-a · 244 ms`；本地显示 `本机`），使用 Tern 默认颜色，不再按连接方式着色；点击切换主机保持不变。

1.10.0：**主机状态** widget（沿用 `connection-status/rtt`）增加当前主机的 RAM 数值和原生内存进度条。默认 `bar`，在 Edit widgets… 中可选 `ring` 或 `text`；条／环的填充比例就是内存使用率，不是用文字字符模拟。文字保持默认颜色，点击仍打开原来的主机切换列表，不添加目录或复制操作。

- raw 和 host-c 容器通过既有 `~/bin/container-usage --json --interval 0.1` 取值，与 `btop-container` 使用同一个 cgroup-v1 读数实现：RAM 是包含缓存的 charged usage，分母是动态有效容器限额；不扣除 inactive file cache，不读取宿主机 MemTotal 充数。悬停只补充“含缓存”。
- host-b 用该主机的 `/proc/meminfo`，已用为 `MemTotal - MemAvailable`。本机 macOS 按 btop 的 active+wired 口径读取 `vm_stat`，使用其实际页大小与 `hw.memsize`。
- `memory_probe.py` 在后台约每 5 秒采样当前焦点 pane 所属主机，SSH 仅运行只读采样命令并复用已有连接配置，严格检查已知 host key；不在 pane 中输入，不重连 Tern，不重启会话。缓存按实际机器分开，切换主机不显示另一台的数值；断线、错误或超过 15 秒的样本显示 `RAM —`，不使用本机数据代替。未知主机未配置采样入口时同样显示未知。

1.11.0：按原生状态组件拆分视觉层级：`connection-status/rtt` 只显示主机与 RTT，保留点击切换；紧邻的 `connection-status/memory` 默认用小圆环和整数百分比显示内存占用。容量只放在内存悬停卡片，容器再补“含缓存”；两者均使用默认颜色，采样口径不变。内存也可在 Edit widgets… 里改为条形或纯文字。

1.11.1：仅内存组件按实际占用比例使用主题语义颜色：低于 70% 为正常（`ok`），70% 起为警告（`warn`），90% 起为高占用（`error`）；数据未知时用 `muted`。颜色作用于内存圆环和百分比，主机名与延迟仍保持默认颜色。原生仪表的 `--wg-fill` 独立于文字的 `tone`，所以插件用仅限内存 widget 的主题色绑定让二者一致；不修改字体、字号或尺寸。

1.12.0：**内存** 的悬停与点击都显示进程占用详情。

- 悬停卡显示容量，以及按 RSS 降序的前 5 个进程：用量、名称和 PID。点击打开原生 `glimpse` 临时面板，显示前 20 个进程及可读进程总数；悬停进程行可看完整可执行文件路径。面板标明主机，不新建分屏；用原生关闭按钮、Esc 或点击面板外收起。再次打开复用当前 tab 的同主机面板。
- 悬停与面板共用约 5 秒一次的真实样本。缓存和在途请求按机器隔离；打开的面板固定到原主机，后台响应不会在切换后贴到另一台主机名下。进程读取失败会明确显示不可用，独立测得的内存总量仍保留。
- 本机 macOS 使用 `ps` 的 RSS；Linux 读取 `/proc`，raw／host-c 仅包含当前 memory cgroup 及其子组，不混入邻近容器。远端用一次既有 SSH 请求流式运行同一份只读采集代码，兼容主机的 Python 3.6，不部署文件或服务。总内存仍沿用 1.10.0 的口径。RSS 含共享页，不能相加当作总用量；容器总用量还包含缓存。
- 当前 Tern 0.9.18 的自定义 widget 卡实测将 `title` 中的 Markdown／HTML 原样显示，返回 `hovercard`／`card` 节点树也不会被采用。这不等于 Tern 不支持富悬停：普通 UI 节点的 `hovercard` 和内置内存卡支持富内容。

1.12.1：重做内存详情 UI，替换 1.12.0 的大面积 `glimpse` 预览和会压缩数值的表格。

- 点击改为当前 pane 上的紧凑原生浮窗，不再覆盖整个 tab；再次点击内存组件收起，也可用浮窗原生关闭按钮。前 20 个进程在窗内滚动，原有 pane 和会话不变。
- 使用 SDK 的 Geist 无衬线排版和原生内存条。进程名称与 RSS 分居同一行，PID 放在下一行；RSS 不参与压缩，PID 不截断。只在必要时省略进程名称，完整可执行文件路径仍保留在悬停信息中。不修改全局字体或主题，也不改变采样口径。
- 新增隔离原生布局回归测试：480×850 窗口、终端 19／界面 17 字号，检查十位 PID 和 GiB 数值完整可见，关闭浮窗后原有 pane 集合不变；未安装 macOS Tern 的环境跳过此 UI 测试。

1.12.2：修正内存浮窗的默认锚点。按当前 tab 的分屏树选择底部靠左的 pane，使浮窗出现在底栏一侧，不再跟随键盘焦点跑到上方 pane；打开时保留原焦点。原生回归补上“上下分屏、焦点在上方”的场景，并检查关闭后原有 pane 集合不变。

1.13.0：悬停和点击统一为同一张底栏原生内存卡片，删除独立 Canvas／PiP 及其定位逻辑，消除双层遮挡。

- 悬停显示，点击固定同一张卡片；卡片由 Tern 锚定到内存组件，不再依赖焦点 pane。固定后可在卡内滚动查看前 20 个进程；容量、PID 和 RSS 保持完整。命令面板入口也激活这个组件，不创建新 pane。
- 每个进程显示名称和对齐的 PID／RSS。悬停对应进程时，完整可执行文件路径直接在卡片内展开，不再打开另一层窗口；路径换行而不缩短。采样间隔、按主机隔离的缓存及容器内存口径不变。
- 自动清理本插件 1.12.x 遗留的内存浮窗；关闭前重新核实当前 pane 类型、Canvas 所有者与用途，不动终端、Agent 或其他插件的 pane。
- 原生回归覆盖窄窗口、上方 pane 获焦时的底栏锚定、悬停／固定同一张卡、十位 PID、GiB 数值、完整长路径展开、滚动到第 20 项、关闭和旧窗迁移，检查始终没有 PiP 或额外工作 pane。

1.14.0：内存卡片改为原生风格的单页概览，增加分类堆叠条、压力状态、交换、近期曲线、应用聚合和进程排行；悬停、点击固定、滚动及展开完整路径仍使用同一张卡。

- 本机使用 `vm_stat` 的匿名内存（扣除可清除页）、Wired 和物理压缩页估算已用；Cached 属于可用部分，不重复计入已用。压力来自 macOS 的系统压力状态，交换来自 `vm.swapusage`。
- Linux 主机显示 Anonymous、Other 和 Available；有 `/proc/pressure/memory` 时显示最近 10 秒 PSI 停滞比例，不把它臆测成 macOS 压力等级。cgroup v1 容器保留含缓存的实际用量与限额，显示 RSS、Cache、Other 和剩余限额；没有容器压力数据或独立交换总量时显示未知。
- 同一应用按 macOS `.app` 或可执行文件路径聚合，列出 RSS 前 5 组和前 20 个进程。RSS 包含共享页，应用之和不是物理内存总用量。曲线按主机保留至多 5 分钟的真实样本，采样中断留空，不跨主机混用；插件重载后重新积累。

1.14.1：**延迟**组件只显示 RTT，不显示主机名；本机显示 `—`，连接未就绪时保留连接状态。悬停方式、点击切换主机及约 5 秒刷新间隔不变。

1.14.2：本机的延迟组件固定显示 `0 ms`；该值不是测得的网络 RTT。

1.14.3：远端 RTT 超过 250 ms 时使用主题警告色；250 ms 及以下、本机和未知延迟保持默认颜色。

1.14.4：容量摘要对齐内置内存卡的字号与基线：只有已用数值采用 24px 大字，单位及总量（例如 `GB of 24 GB`）使用 11.5px 小字；可用量在同一行右对齐。窄窗口才将可用量换到下一行。删除历史折线图、Last 和 Peak，保留分类、交换与应用信息；采集脚本和远端 cgroup 口径不变。`tests/test_memory_summary.py` 在隔离 Tern 实例中比较内置与插件的字体属性，并检查同排对齐及窄窗口布局，不操作用户窗口。

1.14.5：Top apps 对齐内置卡片的 22px 行距、常规字重和 6px 名称／计数间隔；单进程不显示 `×1`。进程总数使用更暗的等宽字并右对齐；用量条固定 48px、RSS 列至少 50px，互不受最长应用名影响。应用区与底栏使用通栏分隔线；底栏按原生 40px 高度排版，用波形图标替换箭头。更新间隔显示真实的 5 秒，不再误写为 2 秒，采样频率未变。任务管理器文字仍是自定义 title 卡里的展示项，不是可点击的原生按钮。隔离测试以同一份应用名称／计数数据比较内置卡片的字体、列位置、行距和底栏几何，并覆盖放大的界面字号。

1.14.6：移除本机卡片的“本机 · 已用”副标题及其占位，将压力徽标移到现有“内存”标题栏。数值行到分类条的实际间距从 20px 收紧为与内置卡片一致的 10px，分类条高度对齐为 8px；修正小字容器额外的最小行高，不靠缩小数字掩盖空白。远程主机名与容器“含缓存”标记保留。隔离回归同时检查常规／放大界面下的数字与分类条间距、压力徽标位于标题栏内且副标题不再留下空行。

1.14.7：远端也采用紧凑标题栏和上移的压力徽标，移除多余“已用”，保留主机名及容器“含缓存”说明。远端进程详情真正默认收起，只显示 `▸ 进程 · RSS` 概览；命令面板 **展开／收起内存进程**（`plugin.connection-status.memory-processes`）切换当前主机的详情，并固定同一张卡片。本机默认仍只显示 Top apps，也可用此命令查看进程。展开状态按主机独立保存于当前窗口，重载后恢复收起；不是悬停展开。widget 的纯文本 title 卡不提供行点击回调，因此标题上的三角标识本身不是点击按钮。详情每行显式分配布局行，修复底栏插入进程列表中间的问题；完整路径仍仅在已展开进程的对应行悬停时显示。卡片打开入口改用原生 `widgets card` 固定操作，避免重复调用把它关掉。Linux／容器的隔离回归覆盖默认收起、展开、再收起、按主机隔离和底栏排序；采集脚本与 cgroup 口径不变。

1.14.8：普通 Linux 主机的概览对齐原生 Used / Free；这里的 Free 仍对应 `MemAvailable`，不是较窄的 `MemFree`，总量和已用字节的计算未变。剩余可用量及容器剩余限额改为暗色轨道和描边图例，不再画成大块已占用色段；macOS 的 Cached 仍使用原生的低透明度独立色段。分类图例改为两个等宽组，标签和值各自在组内对齐，避免 Used / Free 短标签使列宽失衡。采样结果新增显式平台字段，区分 Linux 系统概览、macOS 分类及容器 cgroup 分类。Linux 的 Top apps 改按 `/proc/<pid>/comm` 上报的程序名聚合，避免把 pi 并入其 node 运行时，或把 `python3.12 (deleted)` 当作程序名；不同路径但同名的 Linux 程序会合为一个展示组，这不代表其二进制身份相同。原始进程的 PID、RSS、exe 路径（包括 deleted 后缀）全部保留，cgroup 及子组过滤不变。验证包含 33 项采集回归、隔离卡片布局／折叠回归，以及 host-b 一次只读采样确认 `pi ×3` 独立列出；不强行匹配不同时间快照的用量和进程数。

## 安装和维护

```sh
tern plugin install ~/src/tern-connection-status
# 修改源文件后更新已安装副本：
tern plugin install ~/src/tern-connection-status --force
# 卸载：
tern plugin remove connection-status
```

源码：`~/src/tern-connection-status/`。安装副本：`~/Library/Application Support/Tern/plugins/connection-status/`。

## 数据来源与限制

Tern 0.9.12 的插件 API（`ManagedHost`）只有地址、状态、身份和 RTT，不提供传输路径。有已连接的远程主机时，插件约每 5 秒在本机运行 `path_probe.py`，增量读取 `~/Library/Logs/Tern/tern.log` 中当前 Tern 进程（探针的父进程）写下的行：

- 线程 `tern-remote-<名>` 的 `remote client connected|reconnected ... way=...` 给出该连接的通道。endpoint 主机的线程名是 endpoint 前 10 位加 `…`，地址主机是地址里的主机部分。
- `tern-relay` 的 `remote connection now direct host=<endpoint>`／`back on the relay` 只改变经中继连到该 endpoint 的连接。
- `remote connection lost`、`remote client disconnected`、`remote session ended` 清除该连接的通道，直到下一次 (re)connected。

`path_probe.py` 只读取完整行；Tern 重启（PID 变化）或日志文件被替换时从头重读。通道判断不连远端、不用 SSH、不抓包、不修改连接或设置；不观察 shell 内的其他远程工具。内存采样独立使用只读 SSH，当前口径与失败处理见 1.14.0 版本记录。

验证（1.5.0，2026-11-10）：健康的 host-a 隧道运行脚本，5 秒返回、PID 不变；在真实窗口里用 `tern ctl plugins run plugin.connection-status.recover-ssh` 触发“恢复 host-a SSH 隧道”，先出现“正在检查 host-a SSH 隧道”，约 5 秒后出现“SSH 隧道可用：host-a 隧道正常，Tern 认证通过；未重启，也未中断现有连接。”。临时端口 18399 上：全新启动 12 秒；已健康则不动；隧道转发到死端口时重建（旧进程退出）；SSH 登录不上时保留旧隧道并报错；端口被其他程序占用时拒绝停止；隧道启动但认证不过时带 doctor 失败行报错。真实端口 28377 上 host-b 从无监听到通过认证 12 秒。host-c 只验证了 SSH 预检（4.3 秒），没有启动它。持有者解析和“只停止完全匹配的隧道”有单元测试（`tests/test_recover_tunnel.py`）。“连接主机”的切换部分沿用 1.3.0 的验证，这次没有在真实窗口重走（它会切换窗口并断开中继会话）。

验证（1.3.0）：真实窗口里先把 host-b 换成 endpoint ID 中继连接，列表显示 `正走中继 · 480 ms，选择后经隧道重连并断开中继`；选择后日志依次为 `tern-remote-host-b.localhost ... way=over UDP to 127.0.0.1:28377 answered_ms=246`，47 ms 后中继条目 `remote client disconnected`，最终只剩隧道条目。已连接隧道的主机显示 `已连接 · SSH隧道 · 246 ms，选择后切换过去`，Esc 取消不做任何改动。

验证（1.2.0，Tern 0.9.12）：真实窗口的 Session 切换器显示 `host-a  远端 · 中继 · 472 ms`，与 `tern.log` 的 `way=through the relay` 及 nettop 观测一致；对真实日志运行探针得到 远端 中继、host-b 从中继升级的 UDP 直连。日志解析的隧道／直连／中继判断、按 endpoint 的升级与回落、断线清除、进程隔离及不完整行边界有回归测试：

```sh
python3 -m unittest discover -s tests -v
```
