# CHANGELOG.md — 已落地批次原文归档

本文件由 `plan.md` 拆分而来：每个已落地 Batch 的完整原文（政策变更说明、落地明细表、调试/试错记录、验证结果、审计基线）逐字搬运到此，按 Batch 倒序排列；`plan.md` 只保留每个批次的一行索引，以及尚未落地的候选、延后项、排除记录与禁区清单。

<a id="batch-26"></a>

## Batch 26(v0.30.0)

起因是真机复现：**Batch 21/25 的功能在这台设备上不可达**。

### 1. 症状

在 vermeer（本地构建 5.15.216，KernelSU root）上：

    su -c 'find /sys/fs/cgroup -name cgroup.pressure | wc -l'   # 0

不只是 `cgroup.pressure`：`cpu.pressure` / `memory.pressure` / `io.pressure` 一个都没有；
而 `/proc/pressure/*`（含本模块 Batch 3 的 `irq`）全在。于是 Batch 25 的两只工具给出的都是
正确但无用的答案：`abk_psi_policy.sh --status` → `no cgroup.pressure … nothing done`，
`abk_psi_bench.sh --mode ab` → `this kernel has no per-cgroup PSI switch`。

### 2. 根因不在 graft，在基线 defconfig 的一个启动参数

AOSP `android13-5.15-lts` 的 `arch/arm64/configs/gki_defconfig` 自带：

    CONFIG_CMDLINE="stack_depot_disable=on kasan.stacktrace=off kvm-arm.mode=protected cgroup_disable=pressure"
    CONFIG_CMDLINE_EXTEND=y

（本仓库 `research/config_audit/vermeer-5.15.216-ci.config:522` 是同一条值 ⇒ CI 构建也一样；
本模块自己的 `gki_defconfig.abk-orig` 快照里也有它 ⇒ 不是本模块加的。）后果两条，都在内核里：

- `psi_init()`：`if (!cgroup_psi_enabled()) static_branch_disable(&psi_cgroups_enabled);`
  ——**per-cgroup 记账整个关掉**；
- `cgroup_addrm_files()` / `cgroup_init_cftypes()`：`CFTYPE_PRESSURE` 的文件在创建阶段被跳过，
  Batch 21 的 `cgroup.pressure` 也在其中。

所以 `docs/psi_field_protocol.md` §2 那份「452 个组带 cgroup.pressure」的点名，只可能量自
**没有这个 token 的内核**；在本模板的任何构建上复现命令都返回 0。该文档已按此加注。

### 3. 被证伪的「便宜修法」

把 `cgroup.pressure` 条目的 `CFTYPE_PRESSURE` 标志去掉，节点在 token 存在时也会出现——但那是
**装饰性**的：`psi_cgroups_enabled` 静态分支仍关着，`psi_group_change()` 对非 root 组根本不推导状态，
写 0 与不写没有任何区别。**要先让记账跑起来，开关才有东西可关。**

### 4. 落地明细

| 文件 | 改动 |
|---|---|
| `scripts/abk_backport_engine.py` | 新增 `GraftContext.defconfig_drop_cmdline_token(token)`：按行改写 `CONFIG_CMDLINE="…"`，保留引号与其余 token 顺序；缺该行 → `blocked_by_missing_anchor`（不发明一行）；第二遍 → `already_present` 且逐字节不动；`KERNEL_ROOT` 之外 → `report_only` |
| `scripts/abk_stable_core.py` | 第 4 档 `ABK_515_DEFCONFIG_PSI=1`（默认关）：config lane 里去掉 `_PSI_CMDLINE_TOKEN = "cgroup_disable=pressure"`，档名进 detail 字符串 |
| `tests/stable_5_15_test.py` | `test_config_tiers` 扩到四档（只有 PSI 档碰 `CONFIG_CMDLINE`、档名进 detail、blocked 时组状态跟着变）；新增 `test_psi_cmdline_tier`：真文件上钉 token 落地 / 幂等 / CRLF / 缺行 / 越界 5 种形态 |
| `module.conf` | 0.29.0 → **0.30.0**（`GROUP_COUNTS` 不变：本批无新组） |
| `ksu/abk_runtime_tunables/module.prop` | companion v0.9.0 → **v0.9.1**（versionCode 11 → 12）：真机首跑暴露的 bench 缺陷已重写——不再读机器级 `/proc/stat` 忙 jiffies（那上面 45% 是别人的负载），改为读自己叶组的 `cpu.stat`；A/B 改成**一次引导内的两个兄弟组**（同量风暴、交替轮次），并且没有 `cgroup.pressure`、或该组没被记账时**拒绝出数**而不是报两个 0 |
| docs | 本文件、`plan.md`、`README.md`、`AGENTS.md`、`docs/porting_policy.md`、`docs/psi_field_protocol.md` |

设计判断：**默认关**。token 去掉后那 ~450 个组会各自推导/计时自己的状态，直到 companion 的
`psi.cgroup=aggressive` 把它们逐个关掉——这正是 token 免费做到的事。所以这一档是「把
Batch 21/25 变成可运行」的开关，不是性能选项，跟 `ABK_515_DEFCONFIG_ROM=1` 一样由用户显式打开。

### 5. 验证

- `python3 -m py_compile scripts/*.py tests/*.py` 通过；
- `python3 tests/stable_5_15_test.py` → **all checks passed**（新增 16 条断言，见上表；bench v2 的 8 条断言一并过）；
- 设备侧：五只脚本（含新增两只）在 vermeer 的 mksh 下 `sh -n` 全过；`abk_psi_policy.sh --selftest` PASS；`abk_psi_bench.sh --mode single` 报 `instrument live` 并正常计费（`groups left = 0`）；
- registry 未动 ⇒ `step_audit` / `implementation_audit` / `smoke.sh` 的期望状态不变：
  默认档位下 config lane 的输出与改动前逐字节相同。

### 6. 待办（真机）

- [ ] 带 `ABK_515_DEFCONFIG_PSI=1` 重编 + 刷 boot，确认
      `find /sys/fs/cgroup -name cgroup.pressure | wc -l` ≈ 452；
- [ ] 模块 root 写 `cgroup.pressure` 是否需要额外 sepolicy（Batch 25 遗留的唯一未证前提）；
- [ ] `keep` vs `aggressive` 两态 A/B，按 `docs/psi_field_protocol.md` §5 判定规则回写
      `tunables.conf` 默认值；
- [ ] §2 的点名重做一遍，这次带上构建档位做溯源。

<a id="batch-25"></a>

## Batch 25(companion v0.9.0，已落地；两态 A/B 待真机)

Batch 21 把 per-cgroup PSI 开关（`cgroup.pressure`）graft 进内核，Batch 22 收了 PSI 家族的
内部同步，**但那个开关从落地那天起没被按过一次**。这一批是它的设备侧策略：不加任何
graft（节点已经在我们自己的树上），只做「谁该关、什么时候关、关掉之后怎么证明」。

### 1. 点名先行

先量再改，结论全部写进新文档 `docs/psi_field_protocol.md`（vermeer / 5.15.216 / v0.29.0）：

| 量到的事实 | 含义 |
|---|---|
| 452 个组带 `cgroup.pressure` | Android 每个 uid、每个 pid 一组 |
| 其中 **314 个当场有任务** | 有任务是常态，不是例外 |
| 全设备打开着 pressure 文件的只有 `lmkd` / `system_server` / `mimd`，且都是 `/proc/pressure/memory` | 全局账由根组服务，分组开关碰不到它 |
| **没有任何进程打开过 per-cgroup 的 PSI 文件** | 这份工没有主 |

由此推翻我自己给的第一版默认：`auto`（只关空组）在这台机器上是**零收益的 no-op**——空组里
没有任务，就永远不会触发 `psi_group_change()` 那段被省的代码。有意义的对照只剩 `keep` vs
`aggressive`，而后者才是唯一真能省到东西的模式，也顺带是唯一可能拒绝掉「vendor 守护进程
明天开始 poll 某个组」的模式。所以出厂默认写回 `keep`，`auto` 保留并在工具头里注明它为什么
不算数。

### 2. 落地明细

| 文件 | 改动 |
|---|---|
| `tools/abk_psi_policy.sh`（新，shipped 进 `bin/`） | 一次遍历：根组**无条件**先跳（它的开关驱动 `psi_system`，写下去等于把全设备的压力信号从 lmkd 眼前摘掉）→ 前缀保护名单 → 已关的跳过（重开会白付内核侧 sync，也是周期 pass 的常态分支）→ 按 mode 决定写 `0`。**只写 0，永不写 1** |
| 同上 | `--selftest`：假树（根 / 受保护子树 / 前缀同名兄弟 / 有任务组 / 空组 / 已关组 / 不可写节点）逐条断言决策表，含「第二遍一个字都不写」。设备不在时这是唯一能证明决策正确的东西 |
| `tools/abk_psi_bench.sh`（新） | 两态 A/B 工装：固定量的 fork/wake 风暴 + `/proc/stat` 忙 jiffies；每轮用**从树里量出来的状态**贴标签（`state=nodes=N on=X off=Y`），因为一次被拒的写入就能把整轮标错 |
| `common.sh` | `psi.cgroup` / `.protect` / `.interval_sec` 进 `abk_known_keys`；`abk_psi_mode/protect/interval/pass/supervisor_main`；稳态（`disabled=0`）走 `abk_log_append` 不进 logcat；首轮「全被拒 + 一个没关成 + 一个原本没关」⇒ **自己退出**，而不是每 300 秒重走 452 个节点去再吃一次 EACCES |
| `service.sh` | `--supervise-psi` 分派；`keep` 时**根本不 spawn**（一个决策点，不留空转循环）。遍历带阻塞写，所以绝不在 `post-fs-data` 里跑 |
| `action.sh` | 新节 `-- per-cgroup pressure (PSI) --`：配置意图 + 树的实测状态两行都打；supervisors 节多一行 |
| `tunables.conf` | 三键 + 默认 `keep`；`psi.cgroup.protect=system,protect_memcg`（保护匹配是**字面前缀**，所以 `protect_memcg` 覆盖 ROM 自造的 `protect_memcg_001/002/...`，不必逐个点名） |
| `embed.conf` | 两只工具进 `bin/`（沿用「设备上只有一份实现」的约定） |
| `module.prop` | v0.8.0 → **v0.9.0**（versionCode 10 → 11） |
| `module.conf` | **不动**，仍 0.29.0。先例 `a50df6e fix(companion): ... (v0.6.2)`：companion-only 不 bump 内核版本，单测里钉的 `["0.29.0","0.29.0"]` 因此仍然成立 |
| `docs/psi_field_protocol.md`（新） | 点名数据 + 复现命令 + 两态协议 + 判定规则 + 明确「没测什么」 |
| `tests/stable_5_15_test.py` | 新断言 22 条（见 §4） |

### 3. 来源误判（这条最值得留）

我最初在四处注释里写「这个开关是从上游 5.15 继承来的，所以本批只是策略不是 graft」。
**错**——`git log` 里 `534ede0 feat(perf): Batch 21 -- per-cgroup PSI accounting switch
(cgroup.pressure)` 一句话就把我顶回来了：那是**我们自己 graft 进去的**（android13-5.15 没有
这个节点）。写错的代价不只是注释难看：它会让「老内核优雅跳过」这条分支被理解成「照顾十几年
前的内核」，而它真正照顾的是**没带 Batch 21 的 ABK 构建**和 ROM 自带内核。四处（工具头、
`common.sh`、`tunables.conf`、`module.prop`）都改了，并且把这件事本身钉成断言：
`"Batch 21" in tool and "inherited upstream" not in tool`——防止我或下一个人再写回去。

顺带一条同源的更正：上游那版开关的「重开不是可恢复状态」在**我们的移植里并不成立**（Batch 21
把状态放在 cgroup 自己的 flags 位，什么都没释放，重开有 `psi_cgroup_restart()` 逐 cpu 重建
state mask）。工具仍然只写 0，但理由换成真理由：companion 会跑在它没构建过的内核上，而它在
用户态分不清两种实现。

### 4. 验证

- `py_compile` 全量、`bash -n` + `sh -n` 全量（含两只新工具，WSL 下 dash 也过）。
- `bash tools/abk_psi_policy.sh --selftest` → **PASS**（7 条断言）。
- `tests/stable_5_15_test.py` → **all checks passed**，新增 22 条里最值钱的几条：
  `the PSI walk only ever writes 0`（按 `abk_psi_walk()` 切片断言，因为自测里合法地会重开
  自己的 fixture）、`the root group is skipped before the protect list is consulted`（钉的是
  两个计数器的先后位置）、`the pass is a supervisor and never runs at the early boot stage`、
  `psi.cgroup=keep spawns no supervisor at all`、`the PSI tool names Batch 21 as the node
  origin, not upstream`、`embed.conf contributes exactly the five device tools`。
- 四档树级审计：`step_audit` / `implementation_audit` / `smoke`。本批无 graft，registry 未动，
  `GROUP_COUNTS` 与 `sublevel_matrix.py` 不改。
- **CI 不需要重跑编译门禁**（本批一行 C 都没有）。但 v0.9.0 的 companion 是打进 AK3 zip 的，
  要让它在设备上生效需要一次**发布构建**（不是验证构建）。
- 设备侧 `su -c 'sh -n ...'`（mksh 才是真 shell）与两态 A/B **未做**：本轮 adb 掉线。

### 5. 踩过的坑（都有对应修复与闸门）

1. **默认保护名单里放了 `apps`**：看着稳妥，实际把 Android 上数量最大的一批组全保下来，收益
   直接没了。自测第一轮就因 `protected=3` 对不上而暴露。改成 `system`，并加一条「`--protect`
   放宽必须被遵守」的断言。
2. **`echo 0 > node 2>/dev/null` 压不住 `Permission denied`**：重定向失败是 **shell** 报的、
   不是 `echo` 报的，`2>/dev/null` 作用在命令上而不是重定向的建立过程。WSL 真树上漏出 28 条
   才发现，改成 `if ( echo 0 > node ) 2>/dev/null`。
3. **bench 一开始恒报 `cpu_jiffies=0`**：awk 程序写成 `'^cpu  {...}'`，少了正则斜杠，gawk 报
   语法错——而我给读数函数套了 `2>/dev/null`，于是「测量失败」长得跟「读数为 0」一模一样。现在
   用 `/^cpu  /`、不吞 stderr、并且**读不到就退出**（`refusing to print a zero as a
   measurement`）。AGENTS.md 里「绿灯的坏代码」那一类，这次轮到工装自己。
4. **指标本身要先证明可信**：1000 / 4000 / 16000 次 fork → 145 / 590 / 2330 忙 jiffies，线性、
   轮间 4%，才敢说 A/B 不是在比噪声。指标也从「总 jiffies」换成「忙 jiffies」（含 idle 的话，
   8 核手机每秒白送 ~800 tick，信号被埋）。
5. **打包门禁拦下两只新工具**：`(?<!/devices)/system\b` 命中 `/system/bin/true` 和 fixture 里的
   `$_st_root/system/uid_0`。仓库里 `tools/*.sh` 一律 `#!/bin/sh` 的原因就在这。改 PATH 查找 +
   fixture 目录换中性名（前缀匹配机制一模一样），并保留 `ABK_PROTECT="system"` 这个字面值给
   Python 侧断言。
6. **zip 命名会造出第二个模块**：顺手打的 `abk_runtime_tunables-v0.9.0.zip` 触发 packager 的
   warning——KernelSU **按 zip 名命名模块目录**，刷它等于在现有模块旁边再装一份、两份监督器
   并跑。只打规范产物 `build/ksu/abk_runtime_tunables.zip`。
7. **`read` 的 2000 行默认上限**：对 1881 行的本文件做「读-改-写」把后 1557 行删掉了（`git diff
   --numstat` 当场露出来，已回滚重做）。同一条也差点发生在 3745 行的 `stable_5_15_test.py` 上，
   那两次是写之前抛错才没出事。规则：大文件一律走完整读写的 Python 补丁脚本，改完必查
   `git diff --numstat`；`scripts/abk_stable_perf.py` 是 CRLF，更不能这么重写。

### 6. 待办

- [ ] 设备回来：`su -c 'sh -n'` 过一遍三只脚本（mksh 是唯一真裁判）。
- [ ] 模块 root 写 `cgroup.pressure` 到底要不要额外 sepolicy 规则 —— 唯一还没证的前提（Batch 18
      需要规则是因为被拒方是内核线程；这里写文件的是模块自己的 root）。`--status` 的
      `refused=` 计数 + `dmesg | grep -i avc | grep cgroup` 就是这条的裁判。
- [ ] 两态 A/B（`docs/psi_field_protocol.md` §4）→ 按 §5 的判定规则决定出厂值是 `keep` 还是
      `aggressive`，并把结果回写本文件与 `tunables.conf` 的注释。
- [ ] 发布构建（把 v0.9.0 companion 打进 AK3 zip）。

<a id="batch-24"></a>

## Batch 24(v0.29.0)

结掉 `plan.md` 上唯一一条「溯源完成、未落地」的小项：重压缩扫描的**每趟上限**
`max_pages`（mainline `34efe1c3b688`，v6.10），顺带把同函数上后来的
`2f529e73d720`（v7.1，拒绝无法识别的 `type=` 值）一起收进来。
android13-5.15 两条都没有——它的重压缩面本来就是本模块从 android15-6.6 自己生成的，
所以这两条在这里也只存在于我们生成的文本里。

### 1. 为什么要这条

companion 每 `zram.recomp.interval_sec`（默认 1800s）跑一趟重压缩。**没有上限的一趟
会按 index 顺序尝试设备上每一个 idle 条目**——满设备下这是几十秒的单核 CPU，而它跑的
时机恰恰是手机想安静的时候。上游加 `max_pages` 就是为了这个。

`2f529e73d720` 那条守卫看着像 cosmetic，其实不是：`mode` 的初值是 0，而 0 在这段
代码里的意思是「不做任何过滤」。所以打错一个字母（`type=huger`）今天的表现不是报错，
是**把整盘重压一遍**——正好是上限想避免的那件事。两条必须一起落。

### 2. 落地明细

新组 `core:zram_recompress_max_pages`（`scripts/batch24_core_zram_max_pages.py`，
6 步全 required，注册在 core 末尾）：

| 位置 | 改动 |
|---|---|
| `recompress_store()` 声明区 | 加 `u64 num_recomp_pages = ULLONG_MAX;`（上游同款） |
| 同函数参数循环 | `type` 分支内加 `if (!mode) return -EINVAL;`；分支后加 `max_pages` 解析（`kstrtoull`），带 ABK 标记注释 |
| 同函数扫描循环 | 循环体首 `if (!num_recomp_pages) break;`（在取 slot 锁**之前**）；调用点前 `num_recomp_pages--;` |
| `recompress_async_store()` | 同样三处——这个节点是 Batch 10-1 自造的，其 docstring 明说「与 recompress 同一套 type/threshold/algo 语法」，语法就不能只有一半 |

语义逐条对齐上游：**计的是「尝试」不是「成功」**。上游把减量放在
`recompress_slot()` 里 `zcomp_compress` 之后（哪怕这次压缩失败也扣，「因为我们确实
花了这份资源」）。5.15 这边 `zram_recompress()` 内部自己按优先级循环，把减量放进去
要么改签名、要么被 Batch 10-1 的 async worker 一起继承——所以减量落在调用点：
上面每个候选过滤分支都是 `goto next`，能走到这一行就等于一次尝试，位置等价。
异步节点这边扣在 `abk_zram_recomp_enqueue()` 之前，一个 job 一次尝试，同一量。

### 3. 本批真正的坑：它改写的是「别的组生成的文本」

`recompress_store()` **不是** pristine 5.15——它是 `zram_recompression` 的载荷。
`replace_once` 先查 new 块，所以只要本组动了那段文本，那两个上游组第二遍就找不到自己
的 new、却仍能找到 pristine 的 old，于是**再追加一遍**（`docs/group_recipe.md`
trap 5，Batch 21 踩过一次；这是本模块**第一个**故意破「没有组改写别的组的块」这条约定的
批次）。做法就是 Batch 21 的解法，只是这次要给**两个**组加：

| 组 | 新增探针 |
|---|---|
| `zram_recompression` | 载荷里有 `static int zram_recompress(struct zram *zram, u32 index,` → 直接 `already_present` |
| `zram_async_recompress` | 载荷里有 `static ssize_t recompress_async_store(struct device *dev,` → 同上 |

探针用的符号只有该组自己会写（5.15 任何一档都不带 zram 重压缩），所以第一遍永远照写，
只有第二遍短路。单测把「这两个组必须带自身载荷探针」钉住（`inspect.getsource`），
删掉探针当场红；本组的注册顺序也钉在两个前置组之后。

### 4. 试错记录：锚点不能靠眼睛抄

第一次跑，本组在 216 上是 `blocked_by_shape`：
`required anchor missing (...:applied x5; ...:missing_anchor)`——第 6 步没锚上。
原因：`_ASYNC_ENQUEUE_OLD` 的续行我从 registry 里 Batch 10-1 的写法「看着一样」抄了
**10 个 tab**，树里实际是 **9 个**（那行续行的缩进是 `_tabs()` 由 4 空格/层换算出来的，
人眼数不出来）。

教训与 AGENTS.md 里那条老话同型：**锚点必须从「已经打完前面各组的树」里逐字节取**，
不能从 registry 源码里目测。本轮取法：把 `tmp/b24_216`（当前 registry 全量打完的一棵
树）里目标片段用 `repr()` 打出来再照抄。顺带补上参考树缺的两只文件
（`Documentation/admin-guide/cgroup-v2.rst`、`block/blk.h`——`FETCH_FILES` 后来加过
它们，四档树都是旧的，导致 `step_audit` 直接报「reference tree is missing」）。

### 5. 验证

- 四档全绿（167/178/194/.216）：`py_compile`、`stable_5_15_test.py`（新增
  `test_batch24_zram_max_pages`：33 项检查，含六个步骤的 trap-2 互斥、两节点语义、
  第二遍逐字节幂等、缺前置组时降级为 `blocked_by_shape`）、`step_audit`
  （core 199/200/191/191 步，**第二遍 36 组全部 already_present**，说明那两个新探针
  生效）、`implementation_audit`（新增 REQUIRED_CONTENT / REQUIRED_ABSENT /
  REQUIRED_IN_FUNCTION 三条；后者按函数切片钉「上限检查在取锁之前、减量在调用点」——
  整文件 substring 匹配看不出这两条到底落在哪个节点上）、`smoke.sh`（两遍 + 回滚；
  167 档 pass1 `{'applied': 36}`，四档 pass2 全 `already_present`）。
- `GROUP_COUNTS` core 35 → **36**；`module.conf` 0.28.0 → **0.29.0**。
- companion：`zram.recomp.max_pages=16384`（= 64 MiB 页）进 `tunables.conf` /
  `abk_known_keys` / supervisor / `action.sh pass` / status 行；
  `tools/zram_recompress_trigger.sh` 加 `--max-pages N`，**不给这个选项时写出去的
  pass 字符串与改动前逐字节相同**（老内核行为不变，单测钉住），给了则
  `type=idle threshold=N max_pages=M`。模块版本 v0.7.0 → **v0.8.0**
  （versionCode 9 → 10）。四只 companion 脚本 `bash -n` + `sh -n` 全过（mksh 的
  真机复测留待下次装机；本轮改动不含算术，也没有 awk 单引号串）。
- 本地四档替代不了的仍是编译：本批引入 C，CI 记录见本节末尾/下一批。

### 6. 已知边界（写清楚，不留惊喜）

- 扫描从 index 0 起，**封顶后尾部拿不到机会**——这是上游同款行为，不是本模块的选择。
  缓解来自语义本身：一次尝试会清掉该条目的 `ZRAM_IDLE`，而 companion 的标记步骤按
  「多久没被访问」重新打，所以被尝试过的条目不会立刻重新变成候选，后续趟次自然往后走。
  要一口气把整盘压完就写 `zram.recomp.max_pages=0`（= 不发这个参数）。
- 上限是「尝试数」，所以 `type=huge` 这类大条目多的时候一趟的 CPU 差异仍然很大；
  `zram.recomp.threshold` 才是管那个的旋钮。
- 老内核收到 `max_pages=...` 会**静默忽略**（未知参数在解析循环里就是不匹配、继续下一
  个），所以 companion 的默认值对旧内核无害，但也**不会**在旧内核上产生上限。
  `--status` 与 `tunables.conf` 注释都明说了这一点。
- 本批之后 plan.md 的「溯源完成未落地」清单为空；剩下的都是需要新审计类型的多批次项目
  （per-VMA locks 读侧核）、或要新基线才能验的小项。

### 7. 编译门禁（ABK CI run 34891009037，success）

本批引入 C（`kstrtoull` 解析 + 每趟计数），本地四档审计看不到编译，以 ABK CI 为准：

- `编译内核` job run **34891009037**（同批双胞胎 34890998139），22m18s，`success`；
- 日志里的三件自证：`head_log: bb4c36d feat(core): Batch 24 -- recompression pass cap
  max_pages (v0.29.0)`（编的就是这个提交）、`[ABK module] version: 0.29.0`（版本进到了产物里）、
  `stable_backport_core/zram_recompress_max_pages: applied`（本组真的落在树上）。

一条自我更正：本节口述过的另一个号 **34897210323 不存在**，那是笔误。取号的正确方式不是记号，
是把 run 的日志 grep 一遍、确认 `version:` 与 `head_log:` 跟自己的 HEAD 对得上——对不上就说明
那次编的不是这份代码，绿灯也无意义。

<a id="batch-23"></a>

## Batch 23(v0.28.0)

起因：Batch 21/22 的 CI 编译门禁（run 34876820533）**失败**，但失败点不在 PSI —— 而是
`drivers/block/zram/zram_drv.c` 里 Batch 17 的 compressed-writeback 辅助函数：

```
zram_drv.c:2098: error: no member named 'bdev' in 'struct zram'
zram_drv.c:2114: error: no member named 'bd_reads' in 'struct zram_stats'
zram_drv.c:2163: error: no member named 'wb_compressed' in 'struct zram'
```

### 1. 根因不是代码，是**调度载荷**

`struct zram` 的 `bdev`/`backing_dev`/`wb_limit_*`/`wb_compressed` 与 `struct zram_stats` 的
`bd_count/bd_reads/bd_writes` 全都在 `#ifdef CONFIG_ZRAM_WRITEBACK` **里面**（zram_drv.h），
而树里的 `gki_defconfig` 根本没有 ZRAM 的任何符号 —— 这个配置由 ABK 的 `use_zram` /
`custom_kernel_options` 注入，**默认不带** `CONFIG_ZRAM_WRITEBACK`。上一次跑绿（run 34871776161）
的 dispatch 带 `custom_kernel_options: "CONFIG_ZRAM_WRITEBACK=y"`，而仓库里存着的
`.commandcode/dispatch.json` 是**旧版**（该字段为空），我复用了它 —— 于是模块在一份关闭了
writeback 的树上编译，Batch 17 加的那几个辅助函数就找不到字段了。

**但这暴露的是模块自己的问题，不是 dispatch 的问题**：模块把 `CONFIG_ZRAM_WRITEBACK` 当作
**可选 tier**（只有 `ABK_515_DEFCONFIG_ROM=1` 才打开，默认关），那么配置关掉时它应当**照常编译**
（特性随之消失），而不是把整个内核编译搞坏。上游自己的 writeback 代码就是带着这个 `#ifdef` 的，
Batch 17 加的三个块漏了。

### 2. 修复（把新增块放进同一道门）

| 位置 | 修复 |
|---|---|
| `_C_READ_NEW`（Batch 17 `zram_compressed_writeback`） | 新增的 `struct abk_zram_rb_req` + 四个 helper + `abk_zram_bvec_read()` 调度器整体包进 `#ifdef CONFIG_ZRAM_WRITEBACK` … `#endif`，紧接在**原函数头之前**闭合 |
| `_C_CALL1_NEW` / `_C_CALL2_NEW` | 两处 `__zram_bvec_read()` → `abk_zram_bvec_read()` 的重定向加 `#ifdef/#else/#endif`：配置关掉时回落到**pristine 那一行**（那时 WB slot 与 `wb_compressed` 都不存在） |

配置**打开**时编译结果与修复前逐字相同（`#ifdef` 透明），配置**关掉**时新增块整体消失、调用点回落
到上游原句 —— 也就是那份文件在该区域回到 pristine 形态。

### 3. 新增一道门禁：**配置门内的符号引用**

四道文本门禁全都只跑"树"，从不经过预处理器，所以这个失败它们**都看不见**（实测：报错前它们四档全绿）。
本批在 `tests/implementation_audit.py` 增加一张 `CONFIG_GATED_REFERENCES` 表与一个检查：
把 pristine 与 patched 做行级 diff，凡是**模块新增/改写**的行里出现只在该 CONFIG 门内声明的符号
（`zram->bdev`、`zram->wb_compressed`、`zram->stats.bd_*`、`zram->wb_limit_*`、`abk_zram_bvec_read(` …），
就必须有同一道门包着它，否则审计失败并打印行号。双向验过：修复后的树 0 条；把门故意铲掉后
立刻报 24 条（含 CI 那 4 行）。

### 4. 验证与后续

- 本地四档：`py_compile`、`stable_5_15_test.py`（新增 `test_batch23_zram_writeback_guard`）、
  `step_audit`、`implementation_audit`（含新检查）、`smoke.sh` 全绿。
- 修好的 dispatch 载荷已写回 `.commandcode/dispatch.json`（`custom_kernel_options:
  "CONFIG_ZRAM_WRITEBACK=y"`），并且**这一次要跑两种配置**才叫验完：带该选项的一次证明新代码与
  主路径没被改坏，不带该选项的一次证明"配置关掉也能编过"这个修复本身。run 记录见下一批或
  Batch 21 节末尾。
- 教训已写进 `AGENTS.md` 与 `docs/group_recipe.md`：**模块可以引用的符号必须是"无论如何都存在"的**，
  只在某个 CONFIG 门内存在的字段/函数，引用它的新增文本必须自己带上同一道门；树级文本门禁
  对这类错误没有分辨力。
- **本批之后 `custom_kernel_options` 不再是"能不能编过"的前提**：带不带 `CONFIG_ZRAM_WRITEBACK`
  都能编译（差别只是 compressed writeback 特性在与不在）。所以那个陈旧载荷的坑也随之消失，
  但要**真正跑到**那条 code path，dispatch 里仍需带上该选项（这也是两种配置各跑一次的原因）。
  dispatch 载荷本身在 `.commandcode/` 下（该目录被 `.gitignore` 排除），需要复现时按上面两行填
  `custom_kernel_options` 即可。

<a id="batch-22"></a>

## Batch 22(v0.27.0)

起因：Batch 21 之后，6.1 来源线 backlog 上只剩两条 —— per-VMA locks（真·架构项目）与本批这条
**PSI 内部同步**。本批把后者结掉：**TSK_ONCPU 从"任务计数"改成 state mask 里的一位**
（android14-6.1）。（未发布前就已推送的 `psi_oncpu_state_mask` 组，见 `GROUP_COUNTS` perf 21 → 22，
`module.conf` 0.26.0 → **0.27.0**。）

### 1. 为什么这不是"纯 refactor"

5.15 把 ONCPU 当成第 5 个任务计数（`psi_group_cpu::tasks[NR_ONCPU]`），于是：

- **它本来就不是计数**：一个 CPU 上只可能有一个任务在跑，计数器的唯一用途是让
  `test_state()` 能算"有 runnable 但没人跑"；一旦中途迁移/重排，计数就可能与事实不一致 ——
  这正是「`psi: task underflow!`」那类 splat 的来源；
- **它逼出了一个谎**：`psi_task_switch()` 设置 @next 的 ONCPU 时，无法从计数本身判断"这个祖先
  是不是 @prev 也占着"，于是 5.15 额外比较 `prev->psi_flags == next->psi_flags`（`identical_state`）
  来决定是否提前停 —— 状态相同才敢停，不同就一路设到根，靠 prev 分支再把计数减回去。
  ONCPU 变成位之后**不需要这个比较**：位是幂等的，设到已经置位的组就说明到了共同祖先，直接停。

换句话说：Batch 22 消掉的是一个真实的不一致窗口 + 一处为绕过它而写的启发式，而不是换个写法。

### 2. 落地明细

| 文件 | 改动 |
|---|---|
| `include/linux/psi_types.h` | 删 `NR_ONCPU` 枚举项与其注释；`NR_PSI_TASK_COUNTS` 5 → **4**；`TSK_ONCPU` 改为 `(1 << NR_PSI_TASK_COUNTS)`；新增 `PSI_ONCPU (1 << NR_PSI_STATES)` |
| `kernel/sched/psi.c` | `test_state()` 增 `bool oncpu` 形参（CPU_SOME/FULL 改问它）；`psi_group_change()` 开头按 clear/set/carry 三态设置 ONCPU 位并从计数循环里摘掉；underflow splat 少一个计数；memstall FULL 改看 `state_mask & PSI_ONCPU`；`psi_task_switch()` 去掉 `identical_state`、改探 state mask 的位；尾部条件 `if (sleep)` → `if ((prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU)` |

三条设计点：

1. **仍然不引入 `psi_group::parent`**：5.15 的 `iterate_groups()` 就是 cgroup 树走查，位化之后
   "提前停"只需要探位，父链依旧没有存在理由（`struct psi_group` 内嵌在 `struct cgroup` 里，
   加成员会移动布局）。
2. **尾部条件必须一起改**。提前停变得更容易命中（不再要求状态相同），所以"除 ONCPU 之外的
   差异"必须继续向共同祖先之上传播 —— 这正是 6.1 把 `if (sleep)` 换成
   `(prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU` 的原因；只改前半段会丢状态。
3. **与 Batch 21 的开关同一处文本**：`cgroup.pressure` 关账分支原本写 `groupc->state_mask = 0;`，
   位化后 ONCPU 是"状态"的一部分吗？——**要保留**（重开时靠它重建，6.1 同样写
   `groupc->state_mask = state_mask;`）。这条跨组编辑是安全的，因为 Batch 21 的组自带形状探针
   （`cgroup.pressure` 在 cgroup.c 里），第二遍不会重复注入 —— 也正是 Batch 21 那条陷阱 5 的
   第一个实际用例。

### 3. 未移植的一处（有意）

6.1 的 `psi_group_change()` 里还有一句 `lockdep_assert_rq_held(cpu_rq(cpu));`。它属于另一处
上游改动（把"调用者必须持 rq 锁"变成机械断言），本模块的调用点里 `psi_cgroup_restart()` 持锁、
`psi_task_change/switch` 由 scheduler 持锁，但把这条断言一起搬进来等于给全部路径加一个我无法在
本仓库内证明的前置条件 —— 故记录在案、不移植。

### 4. 验证

- 本地四档（167/178/194/216）`step_audit`/`implementation_audit`/`smoke` 全绿；本组四档都
  `applied`（无 `PRE_APPLIED`/`KNOWN_DEBT` 变化）。
- 新增单测 `test_batch22_psi_oncpu_state_mask`：**合成 fixture 里先跑 Batch 21 再跑本组**，断言
  ONCPU 位化、四个计数、`identical_state` 与 `tasks[NR_ONCPU]` 双消失、尾部传播条件、以及
  "关账组仍保留 ONCPU 位"，最后验两遍幂等。
- `implementation_audit` 侧新增 `REQUIRED_CONTENT`/`REQUIRED_ABSENT`/`REQUIRED_IN_FUNCTION`
  三张表：`NR_ONCPU`/`tasks[NR_ONCPU]`/`identical_state` 必须**不在**（这是本组的存在意义），
  而三个使用点（`test_state()`、memstall 判断、switch 探位）必须**在**。
- 新增 C 只有编译能证明：本批同样以 **ABK CI 编译**为最终门禁，见 Batch 21 节的 run 与本节
  追加的 run。

### 5. PSI 家族结清后的下一步：per-VMA locks 实测结论

backlog 上只剩 **per-VMA locks** 与 DAMON sysfs（§[~]§，价值已经评过）。本轮顺手给它做了
实测探针（§tmp/ref515mm§ vs §tmp/ref61mm§，同一批文件两边取），三条结论见 §plan.md§ 那一节，
要点：**KMI 不是阻塞点**（5.15 的 §struct vm_area_struct§ 末尾就有 4 个 §ANDROID_KABI_RESERVE§ 槽，
而 6.1 只需 §int vm_lock_seq§ + 一个**指针**），**6.1 代码不能直接搬**（长在 maple tree 上），
**真正的量在写侧**（11 个抽样文件里 §vma_start_write()§ 就 42 处、per-VMA API 83 处，5.15 侧为 0，
漏一处是静默内存损坏）。给出的推进方式是**先做只读侧**（fault 路径解除 mmap_lock 读竞争，
写者继续走 mmap_lock 写锁 → 无漏写者风险），写侧 retouch 与"枚举所有 VMA 变更点"的新审计
放在后续批次。

<a id="batch-21"></a>

## Batch 21(v0.26.0)

起因：Batch 20 结清上游 5.15.y 来源线后，backlog 上剩下的**全是架构级**候选。本批啃掉第一条
—— android14-6.1 的 **per-cgroup PSI 开关（`cgroup.pressure`）**。Batch 20 留下的可行性探针
已经把布局结论写进 `plan.md`：`struct psi_group` 是 `struct cgroup` 的**内嵌成员**（其后还有
`bpf`/`congestion_count`/`freezer`/柔性数组 `ancestor_ids[]`），所以 ACK 6.1 的 `bool enabled`
加不进任何一方；但 5.15 的 `iterate_groups()` 本来就走 cgroup 树，6.1 新加的 `parent` 指针也
不需要。于是「cgroup KMI 红线」剩下的唯一约束就是**别动布局**：本批用 cgroup 自己的 `flags`
（`unsigned long`）承载状态位 `CGRP_PSI_DISABLED`，两个结构体一个字节都不动。
`module.conf` 0.25.0 → **0.26.0**，`GROUP_COUNTS` perf **20 → 21**。

### 1. 落地：psi_cgroup_pressure_switch（android14-6.1）

语义按 ACK/上游 cgroup-v2.rst `cgroup.pressure` **逐条对齐**：

- **非层级**：关一个 cgroup 不影响其后代，也不需要从 root 逐级开启；
- **关掉后仍然数任务**：`psi_group_change()` 保留 `groupc->tasks[]` 更新（上层与下层都要读这些
  计数），只跳过 `test_state()` 推导与 `record_times()` 计时，然后 `write_seqcount_end()` 直接
  返回；开关是在 state mask 推导处生效的，不是在某个热路径到不了的 helper 里；
- **重新打开要重建**：`psi_cgroup_restart()` 对**每个 possible CPU**加 rq 锁，调
  `psi_group_change(group, cpu, 0, 0, cpu_clock(cpu), true)`，用留下来的计数重算 state mask 并
  重启状态时钟（与 6.1 同形，`for_each_possible_cpu` 而不是 online，否则关掉期间离线的 CPU
  会带着旧 mask 复活）；
- **关掉就没有压力数据**：`psi_show()` 与 `psi_trigger_create()` 返回 `-EOPNOTSUPP`。

| 位置 | 改动 |
|---|---|
| `include/linux/cgroup-defs.h` | `CGRP_KILL` 之后新增 `CGRP_PSI_DISABLED`（**位**，不动任何偏移） |
| `kernel/sched/psi.c` | `psi_system_accounting_disabled` + `psi_group_enabled()`（`container_of` 反查 cgroup）；`psi_group_change()` 开关分支；`psi_account_irqtime()` IRQ 走查跳过；`psi_show()`/`psi_trigger_create()` 拒绝；`psi_cgroup_accounting_enabled/set()` + `psi_cgroup_restart()` |
| `include/linux/psi.h` | 三个新入口声明（`#ifdef CONFIG_CGROUPS` 内，与 `psi_cgroup_alloc()` 同块） |
| `kernel/cgroup/cgroup.c` | 改名 `cgroup_pressure_write()` → `pressure_write()`（与 6.1 同名，4 步全部 required）；新增 `cgroup_pressure_show()/cgroup_pressure_write()`；`cgroup_base_files[]` 增加 `cgroup.pressure` 条目 |
| `Documentation/admin-guide/cgroup-v2.rst` | 新增 `cgroup.pressure` 手册节（上游措辞 + 本实现的两条差异说明） |

三条设计判断，以及为什么：

1. **root cgroup 走 `psi_system`**。5.15 的 `cgroup_psi()` 只是 `&cgrp->psi`，
   `iterate_groups()` 又**永不返回 root 自己的组**（root 没有 parent），root 的 `*.pressure`
   文件由 `cgroup_ino(cgrp) == 1 ? &psi_system : &cgrp->psi` 兜住。因此 `psi_system` 没有
   cgroup 可挂旗标，它的开关状态落在 psi.c 的静态变量上 —— 这是 6.1 `psi_system.enabled` 的
   等价物，不是第二条真值来源（cgroup 侧只有一个 bit）。
2. **不隐藏压力文件，改为 `-EOPNOTSUPP`**。6.1 用 `kernfs_show()` + `KERNFS_HIDDEN` +
   `cgroup_file_show()` 把 `*.pressure` 从目录里摘掉，而这三个机制**正是随本特性一起进树的**
   （5.15 的 kernfs 里没有它们，已核对 `fs/kernfs/dir.c` 与 `include/linux/kernfs.h`）。为了一个
   目录可见性的 nicety 去移植 VFS 层隐藏机制，收益与风险不成比例；读一个关了账的 cgroup 得到
   `EOPNOTSUPP` 而不是**冻结的旧数字**，对消费者是同一个信号。手册节把这条写明。
3. **`psi_types.h` 保持不动**，并把它钉进单测与审计：本组 `files` 里**没有**
   `include/linux/psi_types.h`（单测直接断言这一条），`REQUIRED_ABSENT` 再钉死
   `psi_types.h` 不得出现 `\tbool enabled;`、`cgroup-defs.h` 不得出现 `struct psi_group *psi;`
   与 `psi_files[`。

### 2. 这次改动暴露出的**陷阱 2 变体**（已写进 `docs/group_recipe.md`）

本组要改写 `psi_irq_tracking`（Batch 3）**自己加进去**的那段 IRQ 走查文本，于是它的 `new` 块
在第二遍扫描时不再逐字存在，而 `old` 锚点仍在 —— 它**又追加了一遍整个函数**，树里出现两份
`psi_account_irqtime()`。`step_audit.py` 的第二遍断言当场抓住（`psi_irq_tracking reported
applied on the patched tree, expected already_present`）。修法是给 `psi_irq_tracking` 加形状探针
（`psi_account_irqtime` 在 `psi.c` 里即 `already_present`）：`apply_steps` 是事务性的，「函数在」
就等于「本组全在」。规则：**后置组只要改写前置组新增的文本，前置组就必须有探针**，否则第二遍
不是幂等而是重复注入。

### 3. 验证

本地四档（167/178/194/216）全绿：`python3 -m py_compile scripts/*.py tests/*.py`、
`stable_5_15_test.py`（新增 `test_batch21_psi_cgroup_pressure_switch`：合成 fixture 跑满 15 步 +
KMI/顺序/幂等断言）、`step_audit.py`、`implementation_audit.py`、`smoke.sh` 各四遍；
`smoke.sh` 四档 pass1 本组都 `applied`（216 行整体 `13 applied + 8 already_present`），两遍
幂等且回滚字节一致。`config_gate_audit.py` **不需要复跑**：本组没有引入任何新的 CONFIG 门
（唯一新增的 `#ifdef` 是既有的 `CONFIG_CGROUPS`），门禁结论不变。

顺手修掉两处存量缺口（都是这套门禁自己藏着的）：

1. `tests/stable_5_15_test.py` 的「released version」钉子还停在 **0.23.0** —— Batch 19/20 各升
   一次版本都没跟，也就是这套单测**已经红了两批**而没被当成失败读；本批跟到 0.26.0。
2. `step_audit.py` 的 `/* */` 平衡检查对**所有**被改文件生效。本批第一次改 `.rst` 文档，
   而文档里的 `/proc/pressure/*` 会贡献一个未闭合的 `/*` —— 检查已限定在 `.c/.h/.S`：该检查
   的语义只对 C 源码成立，对纯文本文档是噪声。

新增 C 只有编译能证明，所以本批次仍以 **ABK CI 编译**为最终门禁（本地 WSL 已按需关闭），
run 记录见 Batch 22 前后追加的 `CI 编译门禁`条目。

<a id="batch-20"></a>

## Batch 20(v0.25.0)

起因：本批把 survey 的「Deferred backlog」清账 —— 5.15.167→.218 里剩下的候选只有三条，
本批**落地两条**、**证伪一条**，至此上游 5.15.y 这条来源线没有未结候选。
`module.conf` 0.24.0 → **0.25.0**，`GROUP_COUNTS` perf **19 → 20**（Batch 19 已把 18 → 19）。

### 1. 落地：blk_mq_quiesced_elevator_switch（`9646443f28f3`，5.15.209）

上游 `8237c01f1696`（2022）。队列重初始化时 `blk_mq_elv_switch_none()/back()` 走的是
`elevator_switch_mq()`（只 freeze 队列，**不** quiesce），而 hctx 的 `run_work` 仍可能在跑，
于是它可能拿到正在拆的 elevator 指针 —— 上游给的现场是 `kyber_has_work()` 空指针 panic，
调用栈正是 `blk_mq_run_work_fn`。改成 `elevator_switch()`（quiesced 版）后，两者互斥。

实现是**三条链一起改**（改名 → 使用点，全部 `required`，事务性）：

| 文件 | 改动 |
|---|---|
| `block/elevator.c` | `int elevator_switch_mq()` → `static int`；`static int elevator_switch()` → `int` |
| `block/blk.h` | 声明 `elevator_switch_mq()` → `elevator_switch()` |
| `block/blk-mq.c` | 注释 + 两个调用点 `elevator_switch_mq(q, NULL/t)` → `elevator_switch(q, NULL/t)` |

半改一半是**链接错误**（blk.h 声明了没人定义的函数），更糟的是留下一个已无人调用的非 static
`elevator_switch_mq()`，所以没有可选步。

- **上游形态改写，无 marker**：`.209` 之后的基线自己就是目标形态，`already_present` 探针落在
  改写后的调用点上（`elevator_switch(q, NULL);`）。
- **陷阱 1**：`int elevator_switch(struct request_queue *q, struct elevator_type *new_e)` 是
  pristine 里 `static int elevator_switch(...)` 的**子串** —— 直接 replace 会短路成
  `already_present`，编辑永远不落地。改为把上一行注释尾 ` */` 一起锚进 old/new 才生效。
- survey 原先的延后理由「与 ABI 套件改写的 elevator 路径重叠」**自 Batch 15 起作废**：
  套件禁止共注入（`AGENTS.md` 红线），而本模块收编的 `blk_mq_async_depth` 占的是
  `__blk_mq_alloc_request()` / `blk_mq_init_allocated_queue()` / `blk_mq_update_nr_requests()`，
  与本组无交集。

### 2. 落地：sched_steal_time_excess_drop（`56135262c1f9`，5.15.179）

`update_rq_clock_task()` 在 steal time 超过本次 delta 时会把超出部分**记到未来**追赶
（`rq->prev_steal_time_rq += steal;`）。读 elapsed 与采样 steal 之间存在窗口，在那里被抢占
就会造出「steal > delta」——而这份多出来的 steal 会**记到下一个任务头上**。宿主挂起时
`clock_task` 会一直冻住、正在跑的任务一直跑。上游的处置是**直接丢弃**超出量。

本批落地的文本与上游逐字一致（样例见 `research/upstream-5.15.y/patches/56135262c1f9.patch`）。

**价值边界（写在最前面，别误读为普遍收益）**：整块代码在
`CONFIG_PARAVIRT_TIME_ACCOUNTING` 之下（GKI defconfig 里**是** `=y`，因为同一份 image 会以
KVM/AVF guest 身份启动），并且还在 `static_key_false(paravirt_steal_rq_enabled)` 之下 ——
后者只有**通告 steal time 的 hypervisor** 才会打开。所以：

- 裸机上该 static key 恒为关，本组是**构造性 no-op**（行为逐字节不变）；
- 作为 guest 运行时它才生效：宿主挂起期间不再冻结 `clock_task`，也不再把这笔 steal 记给下一个任务。

### 3. 证伪：sched_to_ratio_u64（`64d9b734b6fe`，5.15.210）—— 在 arm64 上是 no-op

`to_ratio()` 由 `unsigned long` 改 `u64`、`tg_rt_schedulable()` 的 `total/sum` 由 `unsigned long`
改 `u64`。commit 自己写明收益场景：**32 位构建**（`unsigned long` 32 位时会截断/回绕）。
本模块只跑 arm64（GKI 侧只认 `arch/arm64/configs/gki_defconfig`），LP64 下 `unsigned long` 就是
64 位，三行改动**不改变任何一处的位宽或取值**。为一个不可能发生的截断去改三个文件、并给
`to_ratio()` 树内唯一的调用链留下与上游不同的类型拼写 —— 与 survey §「churn without value」
同类，**按政策排除**并记录在此，不再重议。

`newidle_balance` 改名（.196）、iov_iter 初始化器改名（.210）、inode sysctls 文件搬动（.179）
维持原判：无用户可见价值的 churn。

### 4. 门禁与 fixture 缺口（本批唯一一次「审计全绿但 smoke 红」）

新增组第一次跑 smoke 时：`step_audit` / `implementation_audit` 四档全绿，167/178/194 的 smoke 却
报 `stable_perf_backport pass1={{'applied': 18, 'blocked_by_shape': 1}}` —— 因为 smoke 有**自己第三份**
文件清单 `SMOKE_FILES`（与 `tests/fetch_sublevel_tree.sh` 的 `FETCH_FILES`、
`tests/step_audit.py` 的 `AUDIT_FILES` 并列），新组要的 `block/blk.h` 不在里面。
216 之所以没红：本组的 `already_present` 探针在触碰 blk.h 之前就短路了 —— 正好演示了
「探针先于锚点」的价值。三处清单已同步加上 `block/blk.h`，`FETCH_FILES` 的 gap-fill 语义
（已有文件不重下）使重取只补这一个文件。

本批新增的门禁：

- `tests/stable_5_15_test.py`：新增 lts KABI 形态 fixture（槽 1 被 `user_dumpable` 占用 → 认 2..8 run），
  并断言该形态仍取槽 8、且不碰槽 1。
- `tests/implementation_audit.py`：三个新组的 `REQUIRED_CONTENT` / `REQUIRED_ABSENT`
  （改名三面一致、`elevator_switch_mq` 从 blk-mq.c 与 blk.h 消失、
  `rq->prev_steal_time_rq += steal;` 消失、per-CPU kstack 变量消失）。
- `tests/smoke.sh`：两条新断言（`elevator_switch(q, NULL);`、`rq->prev_steal_time_rq = prev_steal;`），
  两者在四档基线上都成立（本模块写入或基线自带）。

### 5. 四档基线状态（本地审计）

| child | 167 | 178 | 194 | 216 |
|---|---|---|---|---|
| stable_backport_core (35) | applied 35 | applied 35 | applied 32 / already 3 | applied 29 / already 6 |
| stable_perf_backport (20) | applied 20 | applied 19 / already 1 | applied 17 / already 3 | applied 12 / already 8 |
| stable_display_fix (1) | already 1 | already 1 | applied 1 | applied 1 |

`step_audit` / `implementation_audit` / `smoke.sh`（两遍幂等 + 回滚字节一致）四档全部通过。

### 6. config_gate_audit：第一次用**真机** `.config` 跑通

`tests/config_gate_audit.py` 是唯一需要**构建产物**的门禁（它 diff 每个文件的 `.abk-orig` 快照，
把「本模块新增的 CONFIG 门」与「新增代码落在的既有门」逐条归属，再拿 `.config` 判定死活）。
此前它只在本地 WSL 构建上跑过，而本轮 WSL 已按需求关闭。改用**设备上正在运行的那个内核**的
`/proc/config.gz` —— 那正是 ABK CI run 34863020987 用当前 tier 构建、并已刷入 `boot_a` 的产物
（`5.15.216-android13-8-g5bfe2b8c1439`，含 `custom_kernel_options=CONFIG_ZRAM_WRITEBACK=y`）。
证据文件：`research/config_audit/vermeer-5.15.216-ci.config`（7016 行，8 个 `ABK_*` 符号，
provenance 见同目录 README）。

针对 `.216` 打过补丁的树运行结果是 **`CONFIG GATE AUDIT OK`**：

- **class A** 36 条本模块新增的门行 —— 符号全部为 on；
- **class B** 22 个「新增代码落在其中的既有门」—— 全部为 on；
- 两个 `DARK_GATES` 仍是原样记录（`CONFIG_ZRAM_MEMORY_TRACKING` 的 `#else` 分支、
  `CONFIG_NO_HZ_FULL` 的掩码细化），都带理由，非失败。

这条门禁的价值不在「又绿了一次」，而在于它**独立于本地构建**：只要手上有一份跑着本模块的
内核，就能复核「有没有哪段 graft 被 `.config` 编译掉了」。

### 7. 编译门禁（ABK CI run 34871776161，success）

本轮改动推送到 `origin/main` 后（`8a73e95`）用 ABK CI 复跑了一次完整构建，结论 **success**，
模块报告与本地审计逐条一致：

```
[ABK stable_515_backport] stable_perf_backport/sched_steal_time_excess_drop: already_present
[ABK stable_515_backport] stable_perf_backport/randomize_kstack_pertask: applied
[ABK stable_515_backport] stable_perf_backport/blk_mq_suspend_wakeup_abort: already_present
[ABK stable_515_backport] stable_perf_backport/blk_mq_quiesced_elevator_switch: already_present
[ABK stable_515_backport] stable_perf_backport: {"already_present": 8, "applied": 12}
Applied custom kernel option: CONFIG_ZRAM_WRITEBACK=y
```

`no member named` 0 条、` error:` 0 条（日志里 7 处 `FAILED` 全部是 workflow 源码/环境变量名，
不是构建失败）。新组的改名链（`elevator_switch` 三面）与 steal-time 文本都在真实编译里过了。

<a id="batch-19"></a>

## Batch 19(v0.24.0)

起因：`plan.md` 里 Batch 5 记下的两个「.211（android13-5.15-lts）遗留阻塞」是**唯一还剩的
降级组**：整个模块只有这两组在滚动 lts 分支上报 `blocked_by_shape`（`tests/sublevel_matrix.py`
的 `KNOWN_DEBT`）。本批逐个定位**为什么锚点失配**，两组都能落地 —— 一个是真缺一条形态分支，
另一个是探针写错了对象。`module.conf` 0.23.0 → **0.24.0**，`GROUP_COUNTS` perf **18 → 19**，
`KNOWN_DEBT` **清空**。

### 1. randomize_kstack_pertask：不是「已自带」，是锚点认不出这个形态

lts 从 `5.15.211` 起，AOSP 把 `task_struct` 的 KABI 槽 1 占成了 `user_dumpable` 位域：

```c
	ANDROID_KABI_USE(1, struct {
		/* Save user-dumpable when mm goes away */
		unsigned	user_dumpable:1;
		});
	ANDROID_KABI_RESERVE(2);
	...
	ANDROID_KABI_RESERVE(8);
```

于是 `ANDROID_KABI_RESERVE(1)..(8)` 这条八连锚**在这条分支上根本不存在**（2..8 才是空闲 run），
而 `_sched_h_kstack_step()` 当时只会两种形态：八连 run → 槽 8、SysVIPC 形态（`USE(6, sysv_sem)`）
→ 槽 5。两者都不匹配 → `blocked_by_shape` → **per-task kstack 随机化在整条 lts 分支上一直没被登记**
（这正是 trap 4 的形态：早返回的探针让一整组永远不跑）。

修复：新增第三条形态分支（探针 `ANDROID_KABI_USE(1, struct {` + `user_dumpable:1;`），认 2..8 run，
仍然占**槽 8**，于是 `tests/smoke.sh` 里那句 `ANDROID_KABI_USE(8` 的断言在四档基线上一致成立。
`_verify_kstack_member()`（成员必须落在 `struct task_struct` 内）继续把关。

### 2. blk_mq_suspend_wakeup_abort：内容早就在，锚点盯错了那一行

lts 分支**已经带了** `8fe7de5d1c7f`（5.15.198）的全部 payload：`pm_wakeup_pending()` 逃逸、
`clear_bit(BLK_MQ_S_INACTIVE)`、`ret = -EBUSY`、`return ret`。唯一差别是 AOSP 把
`#include <linux/suspend.h>` 包在 `#ifndef __GENKSYMS__` 里（他们靠这个保持 CRC 不变），
而本组的第一个 `required` 步锚的是**未包裹的 include 对** → 整组 `blocked_by_shape`，
报的却是「锚点缺失」，读起来像缺功能。

修复：改为**探针 payload 本身**（四行 `if (pm_wakeup_pending()) {...}` 文本），命中即
`already_present` 且一个字节都不写；216 行因此从 `KNOWN_DEBT` 移到 `PRE_APPLIED`。
该探针同时覆盖第二遍幂等（自己的 graft 也是同一段文本）。

### 3. 结论：`KNOWN_DEBT` 为空

`tests/sublevel_matrix.py` 的 `KNOWN_DEBT` 现在是空表 —— **每个 child 的每个组，在四档受支持
基线（167/178/194/216）上都必须真的落地或真的是基线自带**，没有任何「已知降级」可以借道。
`smoke.sh` 的注释同步改写：它过去写着「矩阵记录了 .211 的已知债」，现在这句不再成立。

### 4. 编译门禁（ABK CI run 34871776161，success）

ABK CI 在本轮模块树（`8a73e95`）上跑完，**这条 lts 分支真的编译通过**，模块自己的报告与本轮
本地审计逐条一致：

```
[ABK stable_515_backport] stable_perf_backport/randomize_kstack_pertask: applied
[ABK stable_515_backport] stable_perf_backport/blk_mq_suspend_wakeup_abort: already_present
[ABK stable_515_backport] stable_backport_core: {"already_present": 6, "applied": 29}
[ABK stable_515_backport] stable_perf_backport: {"already_present": 8, "applied": 12}
[ABK stable_515_backport] stable_display_fix: {"applied": 1}
```

`no member named` 0 条、` error:` 0 条 —— 也就是新的 KABI 形态（`ANDROID_KABI_USE(8, u32
kstack_offset)` 落在这条分支的 2..8 run 上）不只是审计里「结构平衡」，而是**真的编过**。

### 5. 诚实边界

- 本地审计用的是 `build/abk-trees/216` 这份**只含 75 个被触碰文件**的参考树（gitiles 抓取），
  不是完整的 lts 源码树；编译证明来自上面的 CI，不是这条参考树。

<a id="batch-18"></a>

## Batch 18(v0.23.0)

起因：Batch 17 的真机验证在最后发现一个**与本批 graft 无关、但决定它在设备上是否可用**的缺口：
Enforcing 下 loop worker 读写 zram 后备文件被拒（`avc: denied { write } ... scontext=u:r:kernel:s0
tcontext=u:object_r:zram_data_file:s0`），每页退化成 `-EIO`，写回**报成功却一页都不搬**。
本批把这条路径打通。**本批不含任何 `PatchGroup`**：它只改 distribution asset（companion 模块）、
文档与测试，因此 `GROUP_COUNTS` / `sublevel_matrix.py` 不动，内核树一行不改。
`module.conf` 0.22.0 → **0.23.0**；companion **v0.6.2 → v0.7.0**（versionCode 8 → 9）。

### 1. 为什么内核侧无解、必须由模块解决

- 被拒的一方是**内核线程**（loop worker）：不论谁 `losetup` 打开文件，读写的 `current` 都是
  `u:r:kernel:s0`，而 AOSP/ROM 策略里**没有** kernel 域对 `zram_data_file` 的 file 规则。
- 失败形态是**静默**的：`alloc_block_bdev()` 返回的块在写失败后立刻 `free_block_bdev()` 归还，
  `writeback_store()` 仍返回成功，`bd_stat` 停在 `0 0 0`。Batch 17 里 ROM 自己挂在 zram0 上的
  `loop49` 从开机起就是这个状态。
- 换文件上下文也一样（`shell_data_file` 同样被拒），所以这不是「文件放错地方」，而是策略缺口。
- 这是**设备策略**，不是内核代码：内核树里的 graft 表达不了它；而「只打开 config tier」正是当初
  产生这个静默 no-op 的原因。所以修复落在 distribution asset 上，与 Batch 11 起的 companion 定位一致。

### 2. 方案：一条最小权限 allow + post-fs-data 提交

- 新增 `ksu/abk_runtime_tunables/sepolicy.rule`，**只有一条**语句：
  `allow kernel zram_data_file file { read write }`。只给 `read`/`write` 是因为内核从不解析路径
  —— 文件由 root 域的 `losetup` 打开，内核域只对**已打开的文件**做 I/O，所以不需要
  `open`/`getattr`/`search`（32MiB 写回+全量读回，实测零残留 AVC 证实）。
- `common.sh` 新增 `abk_selinux_apply_rules()`，由 `post-fs-data.sh` 在
  `abk_apply_early_knobs` 之后调用：**必须在任何东西挂上后备设备之前**，因为从那一刻起被拒的是
  内核线程。管理器自己会加载模块 `sepolicy.rule` 时，这次提交是幂等 no-op（同一 `ksud` 重复
  apply 返回 0）；加不上时只记 WARN 继续，**绝不切换 Enforcing/放宽策略**。
- `action.sh status` 增加 `bd_stat` 与 `selinux` 两行：规则生效与否写在策略里、没有可读节点，
  所以「backing device 已挂 + `bd_stat` `0 0 0`」才是被拒的形态，之前没有任何一处能看到它。

### 3. 真机证据（2026-09-14，vermeer / `5.15.216-android13-8-g5bfe2b8c1439`，全程 Enforcing）

| 场景 | 结果 |
|---|---|
| 独立 zram1 + 32MiB 数据，**规则前** | `rc=1`、`bd=[0 0 0]`、`io=[0 0 0 0]` |
| 同一脚本，`ksud sepolicy apply` 最小规则后 | `rc=0`、`bd=[7690 7690 7690]`、md5 写回前后一致、**0 条 zram AVC**（batch 1 与 32 各一遍） |
| ROM 自己的 zram0 路径，16MiB 预算、**未标 idle** | `rc=0`、`bd=[0 0 0]`：没有候选页（ROM 的 daemon 从不写 `idle`，这是它的真实形态） |
| 同上，先 `echo all > idle`（模块 sweep 的做法） | `rc=1`（预算耗尽）、`bd=[4096 0 4096]`：**恰好 4096 页 = 预算**，0 条 AVC，限流归零，随后已把 ROM 的 `writeback_limit`/`_enable` 原值（2752512/1）复原 |
| 重复 apply 同一规则 | `rc=0`（幂等，不会让 post-fs-data 失败） |
| `ksud sepolicy check`（剥注释后） | 通过；`apply` 本身也能正确跳过 `#` 注释行 |
| **重启后不手工 apply**（v0.7.0 装进 `modules_update` 后 `adb reboot`） | 开机日志出现 `selinux: submitted … via /data/adb/ksud`；独立 zram1 32MiB 写回 `rc=0 bd=[7690 7690 7690]`、md5 一致、0 AVC；zram0 `bd=[0 0 0] → [1 0 1]`（该时刻 swap 内只有 1 页，`orig_data_size=4096`） —— 规则由模块在 post-fs-data 重新提交，**全程无手工 apply** |

脚本与原始输出：`research/zram/vermeer_batch17_check/b17_enforcing.sh`、`b17_rompath.sh`、
`raw/08-enforcing-before-rule.txt`、`raw/09-enforcing-after-rule.txt`、
`raw/10-rom-zram0-writeback.txt`、`raw/11-ksud-sepolicy-probe.txt`、`raw/12-post-reboot.txt`。

### 4. 门禁与遗漏

- `tests/stable_5_15_test.py` 新增 12 条断言：规则文件**恰好一条**语句、逐字等于最小 allow、
  不含 `setenforce`/`permissive`/`neverallow`/`dontaudit`/`auditallow`/`type_transition`/`allowx`、
  post-fs-data 确实提交、失败非致命、`action.sh` 报 `bd_stat`/`selinux`、规则文件进 zip、
  以及「relax SELinux」扫描把该文件也纳入。
- 设备侧 `sh -n`（mksh）通过全部 5 个脚本；`ksud sepolicy check` 解析该规则文件通过。
- **诚实边界**：`ksud sepolicy apply` 对**无法解析的符号**同样返回 0（实测：不存在的 type、
  不存在的 permission 都 rc=0），所以返回码只代表「语句已提交」，**不代表规则生效**；真正的
  判据只有 `bd_stat` 与 I/O 正确性（`action.sh status` 因此新增 `bd_stat` 一行）。规则只在
  vermeer/HyperOS 这一台设备、这一个 ROM 上验证过；换 ROM 后 `zram_data_file` 类型是否存在、
  还有哪些权限被拒需重测。
- KernelSU 的策略补丁是**内存态**，重启即失效 —— 这正是模块必须每个 boot 重新提交一次
  `sepolicy.rule` 的原因，也是本批把提交放在 `post-fs-data` 的原因（重启验证见 §3 末行）。
- Batch 17 的性能数据仍然来自 permissive 那一轮：本轮只证明 Enforcing 下路径**可用**，
  **没有**在 Enforcing 下重测 batching/compressed writeback 的墙钟与 CPU（写回路径本身没变，
  但这是推断，不是测量）。

<a id="batch-17"></a>

## Batch 17(v0.22.0)

起因：Batch 14 把 zram writeback 的**正确性**补齐后，把「writeback bio 分批 + `writeback_batch_size`」
与「compressed writeback」两项判为**延后**，理由是前者要连 pp-slot 机制一起搬、后者真正阻塞在
5.15 没有现代 zsmalloc 映射 API（`zs_obj_read_begin/end`）。本批把这两条理由逐条查证，**两条都被
证伪**，于是两项一并落地。`module.conf` 0.21.0 → **0.22.0**，`GROUP_COUNTS` core **32 → 35**。

### 1. 上游复核（联网，GitHub API + 真实 patch）

系列一 v6「zram: introduce writeback bio batching」（2025-11-22，6 patch，v6.19）：

| commit | 处置 |
|---|---|
| `f405066a1f0d` 机制 | **移植**（组 1） |
| `e828cccb72ed` `writeback_batch_size`（默认 32） | **移植**（字段/默认值→组 1，sysfs→组 2） |
| `7c929664fddf` / `a4f506c569e1` wb limit 存写锁 / 删 `wb_limit_lock` | 不移植（§5） |
| `e87ddea34567` 块分配重写 | 只取「返回前释放保留块」语义 |
| `1b1a4e4d6797` 读 slot blk_idx 持锁 | 不移植（读路径加固，非本特性依赖） |

该系列后续 Fixes 两条，均 `Cc: stable` 且 `Fixes: f405066a1f0d`：`bf62f69574b1`
（`zram_writeback_endio()` 里 `wb_ctl` 被提前释放的 UAF，2026-05-12）与 `3e8d8eb8d7f5`
（循环可带一个「已保留未提交」的 blk_idx 返回 → 该块永久忙，2026-05-26）。
**两条修复从第一行新代码起内建**，本树里不存在的错误形态自然也不会被引入。

系列二「zram: introduce compressed data writeback」（2025-12-01 投递，2026-01-21 合入 v7.0，
声明 7 patch）：

| commit | 处置 |
|---|---|
| `d38fab605c66` 机制（1/7，227+/53−） | **移植**（写侧→组 1，读侧→组 3） |
| `4c1d61389e8e` `writeback_compressed` 属性（2/7） | **移植**（组 3） |
| `ba4c3698e696` 改名 `compressed_writeback`（`Fixes: 4c1d61389e8e`） | 采用最终 ABI 名 |
| `3bf1c285dc40` 尾字节清零（`Fixes: d38fab605c66`） | **内建**（组 1 的 raw 读） |
| `bf989ade270d` read_from_bdev_async 错误传播 | 不移植（无 `Fixes:` 指向本特性） |

复核方法与边界：`search/commits?q=repo:torvalds/linux+"Fixes: d38fab605c66"` 得 **total=2**
（命中 d38fab605c66 自身与 `3bf1c285dc40`），即**唯一后续修复就是尾字节清零**；
`commits?path=drivers/block/zram/zram_drv.c&since=2026-01-20&until=2026-01-23` 拉出合入窗口
12 条提交，其中与本特性相关的只有 d38fab605c66 与 4c1d61389e8e（其余属别的系列）；系列另 5 个
成员不改 `drivers/block/zram/*.c`，属 Documentation/清理，未逐条审计（诚实边界）。
`since=2026-05-25` 的该文件全部提交（15 条）+ 本地 `research/upstream-zram/zram_drv_master.c`
（v7.3-rc3 期）逐行核对：`845 kfree_rcu(wb_ctl, rcu)`、`962 rcu_read_lock()`、
`1123-1124` 未用 blk_idx 释放、`1126` drain、`3091 wb_batch_size = 32` —— 没有更晚的修复。
研究工件：`research/upstream-zram/patches/` 补齐了此前缺失的 3 个 batching 系列成员。

### 2. 5.15 等价改写（为什么不是 cherry-pick）

| 上游机制 | 5.15 事实 | 本批改写 |
|---|---|---|
| batching 由 pp-slot 机制驱动（`zram_pp_ctl`/`zram_pp_slot`/`ZRAM_PP_SLOT`，v6.13） | 5.15 **0 命中** | in-flight 窗口用 5.15 自己的 `ZRAM_UNDER_WB` + `ZRAM_IDLE` 表达：前者让 `recompress_store()` / `abk_zram_recomp_work()` 在 slot lock 下跳过飞行中的槽，后者（`zram_free_page()` 入口清 IDLE、`idle_store()` 拒绝给 UNDER_WB 标 IDLE）就是「这个槽还是我读到的那一个」的可靠判据 |
| 独立 `zram_writeback_slots()` | 5.15 循环**内联在** `writeback_store()` | 保留内联，不引入上游的函数切分（那是 pp-slot 两阶段选择才需要的） |
| `zram_read_from_zspool_raw()` 用 `zs_obj_read_begin/end` | 5.15 无此 API | 用 `zs_map_object(..., ZS_MM_RO)`：`mm/zsmalloc.c:1131 __zs_map_object()` **总是**把对象拷进 per-cpu `area->vm_buf` 再返回（`1149-1154` 从两页分别 memcpy，`1156 return area->vm_buf`），而 `vm_buf` 的注释就是 "copy buffer for objects that span pages" ——**映射本身就是 bounce buffer，不需要 zsmalloc 重写** |
| `zstrm->local_copy` | 5.15 `zcomp_strm` 只有 `void *buffer`（`__get_free_pages(..., 1)` = 2 页） | 解压中转用 `zstrm->buffer` |
| `zcomp_decompress(comp, zstrm, src, size, dst)` 5 参 | 5.15 是 4 参 `zcomp_decompress(zstrm, src, src_len, dst)` | 按 5.15 4 参调用 |
| `zcomp_stream_put(zstrm)` | 5.15 是 `zcomp_stream_put(struct zcomp *comp)` | 写 `zcomp_stream_put(zram->comps[prio])` —— 本批最容易踩的「编译干净、行为错」约定 |
| 压缩槽需同时保存 obj_size + priority + WB 位 + blk_idx | `.element` 是**独立 union 成员**（`zram_set_element()` 直接写 `.element`）；`zram_set_obj_size()` 只动 `flags` 低 24 位且保留高位；priority 在 `flags` 位 30–31 | **无位域冲突**，四项共存（`zram_set_element(zram, index, req->blk_idx)`） |
| `read_from_bdev()` 增 `index` 参数（改调用点） | 调用点在 `__zram_bvec_read()` 内 = **recompression graft 的替换块**，改它会破坏该组的连续性契约 | 改为在 **pristine 的 `zram_bvec_read()`/`zram_bvec_write()`** 上做两处调用改向，指向本批自己的 dispatcher；`read_from_bdev*` 的 5 参签名**一字不动** |
| `memset_page()`（6.19 助手） | 5.15 无 | `zero_user(page, 0, PAGE_SIZE)` |

### 3. 三个组

- [x] **`zram_writeback_batching`**（组 1，8 步全 required）——`f405066a1f0d` + `bf62f69574b1` +
  `3e8d8eb8d7f5` + `e828cccb72ed` 的字段/默认值 + `d38fab605c66` **写侧** + `3bf1c285dc40`。
  新增 `struct zram_wb_ctl`/`zram_wb_req`（含 `struct rcu_head rcu`）、`init_wb_ctl()`、
  `release_wb_ctl()`（`kfree_rcu`）、`zram_account_writeback_submit/rollback()`、
  `zram_writeback_complete()`、`zram_writeback_endio()`（整体 `rcu_read_lock()`）、
  `zram_submit_wb_request()`、`zram_complete_done_reqs()`、`zram_select_idle_req()`、
  `zram_read_from_zspool_raw()`；`writeback_store()` 的声明段/页分配段/扫描循环/收尾段四处改写；
  `zram_drv.h` 增 `bool wb_compressed; u32 wb_batch_size;`（上游字段顺序），`zram_add()` 置
  `32` / `false`。
- [x] **`zram_wb_batch_size`**（组 2，3 步）——`e828cccb72ed` 的 sysfs 面 + 上限夹取（见 §5）。
- [x] **`zram_compressed_writeback`**（组 3，5 步）——`d38fab605c66` **读侧** + `4c1d61389e8e`/
  `ba4c3698e696`：`struct abk_zram_rb_req`、`abk_zram_decompress_bdev_page()`（锁内重查 `ZRAM_WB`，
  否则 `zero_user()` 清页 + `-EIO`；`ZRAM_HUGE` 不参与解压）、sync/async 读、
  `abk_zram_deferred_decompress()`（`system_highpri_wq`，因为解压可睡眠而异步读在 IRQ 完成）、
  dispatcher `abk_zram_bvec_read()`，以及 `compressed_writeback` 属性。它的 `_apply` 先探测组 1 加的
  `bool wb_compressed;`，不在则 `blocked_by_shape`——**任一组单独缺失都不会留下编不过的树**。

为什么 compressed writeback 的**写侧**并进组 1：写侧是批处理循环与 completion 里的分支，编辑它们就是
编辑组 1 的替换块，而 graft 连续性契约（见 `scripts/batch10_core_zram_async.py`）禁止后面的组这么做。

### 4. 试错记录（都留在代码注释里，供后来者避免重犯）

1. **batch14 必须重锚，而且两处都要改**。原来 `_B_POSTLOCK` 一直伸到本批拥有的
   `page = alloc_page(GFP_KERNEL);`，而 `_B_LOOP_TAIL_NEW` 的注释写着 "a blocking
   `submit_bio_wait()`"（分批后不再成立）。第一次只缩短了 `_B_POSTLOCK`（old），忘了它的
   `_B_POSTLOCK_NEW`（new）——结果 batch14 把那一行**又插了一遍**，扫描循环里出现两个
   `page = alloc_page()`，我的页分配步替换掉了 pristine 那份，留下了刚插入的那份：
   **所有文本审计全绿**（锚点合法、结构平衡、两遍幂等、实现审计通过），因为这是一个纯 C 级错误。
   修法是把 `_B_POSTLOCK_NEW` 一并缩短到 `backing_dev` 检查的 `}`，并把这个不变量钉进单元测试：
   *后面步骤的锚点不得出现在前面步骤的替换文本里*。
2. **组 3 的 shape probe 必须接受两种形态**（`step_audit` trap 4）。第一版探测 pristine 的
   `ret = __zram_bvec_read(..., true);`，该组自己把它改向之后，第二遍就报 `blocked_by_shape`。
   修法是两种形态都接受。
3. **审计反向 needle 会被自己的注释打败**。`submit_bio_wait`、`zs_obj_read_begin`、
   `zstrm->local_copy` 作为「不得残留」的 needle 全部被本批的解释性注释误伤（注释里按名字提到了
   上游 API），而 `submit_bio_wait` 还额外被读回路径合法的同步读命中。同一条 needle 在
   `implementation_audit.py` 与 `smoke.sh` 里各犯一次（共 4 处），最终全部改成带调用形态
   （`err = submit_bio_wait(&bio);`、`zs_obj_read_begin(zram->mem_pool`、
   `, zstrm->local_copy)`），并把「为什么带形态」写进注释与单元测试。
4. **收尾步重复释放块**（同日，由**真实构建**发现）。`_A_TAIL_OLD` 只从 `__free_page(page);` 起锚，
   而 batch14 的 `_B_LOOP_TAIL_NEW` 只到 `if (blk_idx)` —— pristine 的
   `free_block_bdev(zram, blk_idx);` 仍在，它就是那个 `if` 的 body。新文本又写了一遍同一行，于是
   编译出来的形状是：
   ```c
   if (blk_idx)
       free_block_bdev(zram, blk_idx);   /* pristine 的 body */
       /* ABK stable_515_backport: 3e8d8eb8d7f5 ... */
       free_block_bdev(zram, blk_idx);   /* 重复释放 + 缩进误导 */
   ```
   clang 以 `-Werror,-Wmisleading-indentation` 报错（`drivers/block/zram/zram_drv.c:1323`），
   这正是「引入 C 时编译器是唯一的门禁」的字面例证 —— **所有文本审计依旧全绿**。修法是把 pristine
   那行并入 `_A_TAIL_OLD`，让「注释 + 那一次释放」成为一个替换。
   两个 C 级错误（本条的重复释放、第 1 条的重复页分配）合起来说明：文本审计能证明锚点与结构，
   证明不了语义；本批因此把「后面步骤的锚点不得出现在前面步骤的替换文本里」钉成单元测试，并把
   真实构建写进验收流程。

### 5. 不移植的项目（逐条理由）

- `7c929664fddf` / `a4f506c569e1`：本批不需要动 wb limit 的锁；删 `wb_limit_lock` 违反「不为了
  编译通过而删锁」，而且 batch14 的 `writeback_limit` 对齐护栏正锚在它的 store 文本上。
- `e87ddea34567` 的重命名与 `INVALID_BDEV_BLOCK` 哨兵：会波及 `zram_free_page()` 的 `ZRAM_WB` 分支
  （属 `zram_recompression` 领地）；5.15 的 `0` 哨兵语义自洽，只取「返回前释放保留块」的语义。
- `1b1a4e4d6797`：读路径加固，与本特性无关；5.15 的同一理论窗口今天已存在，不是本批引入的回归。
- `bf989ade270d`：无 `Fixes:` 指向本特性；本批读回 dispatcher 自带错误传播（sync 返回 / async `bi_status`）。
- `zs_obj_read_begin/end` 与 zsmalloc 重写、pp-slot 机制、huge_idle、6.16 writeback ABI、
  `be48c412f6eb`、Documentation 两处改写：见 §2 与 §5 首条。
- **对上游的一处有意偏离**：`writeback_batch_size` 夹取到 `ZRAM_WB_BATCH_SIZE_MAX`(256)。上游存任何
  非零 u32，而 `init_wb_ctl()` 按该值逐个 `GFP_KERNEL` 分配请求与页——即用户态可触发的分配循环；
  256 个请求在 4 KiB 页下约 1.1 MiB，远超任何后备设备需要的队列深度。

### 6. 验证结果

- [x] `python3 -m py_compile`（scripts+tests 全部）、`bash -n`（setup/scripts/tests/tools）；
- [x] `python3 tests/stable_5_15_test.py`：**all checks passed**（新增 `test_batch17_zram_writeback()`：
  注册与顺序、跨组锚点不重叠不变量、两条修复只允许一种形态、5.15 约定（`zram_set_element`、
  `zcomp_stream_put(zram->comps[prio])`、`zero_user`）、默认值/夹取、并集探测、以及
  「外来树必须降级且一个字都不写」）；
- [x] `python3 tests/step_audit.py`：**167/178/194/216 全部 `STEP AUDIT OK`**（core 185–194 步，
  第二遍全 `already_present` 且字节相同）——这也是 batch14 重锚正确的证明；
- [x] `python3 tests/implementation_audit.py`：**四个基线全部 `IMPLEMENTATION AUDIT OK`**（新增 3 组
  `REQUIRED_CONTENT` + 2 组 `REQUIRED_ABSENT`）；
- [x] `bash tests/smoke.sh build/abk-trees/194`：**SMOKE OK**（pass1 `applied 32 / already_present 3`，
  pass2 全部 `already_present`，回滚字节一致；新增 5 条 grep 断言 + 2 条反向断言）；
- [x] **本地 ROM tier 编译**（`ABK_515_DEFCONFIG_ROM=1`，否则 `CONFIG_ZRAM_WRITEBACK` 关闭、本批新代码
  整块编不到）：`~/kci515` 的 `rebuild.sh --reseed --allow-dirty-template`（模块以 `set:` 形式指向本
  工作副本）→ `[rebuild] build completed`，日志里 **0 个 `error:`、0 个 zram warning**
  （`-Werror,-Wmisleading-indentation` 曾在第一轮打断构建，见 §4 第 4 条），
  `drivers/block/zram/zram_drv.o` 产出，编出的 `.config` 含 `CONFIG_ZRAM=y`、
  `CONFIG_ZRAM_WRITEBACK=y`、`CONFIG_ZRAM_MULTI_COMP=y`、`CONFIG_ZRAM_TRACK_ENTRY_ACTIME=y`，
  AnyKernel3 zip 重新产出。**这一轮构建抓到两个文本审计看不见的 C 级错误**（重复页分配、重复块释放，
  见 §4）——「引入 C 时编译器是唯一的门禁」在本批得到两次实证。
- [ ] 仍待 ABK CI 复跑：本地构建已证明可编译，CI 用于确认同一 commit 在 CI 工具链/配置下一致。
- [x] **真机已验证**（2026-09-14，vermeer / 23113RKC6C，原文见 §8）：两个节点在且语义正确
  （含边界与 `-EBUSY` 窗口）；`compressed_writeback=0/1` 两种模式读回页面与原内容**逐字节一致**；
  batch 1 vs 32 的墙钟/上下文切换/写回 CPU 全部量化；`writeback_limit` 记账在三种批大小下都不超发。
  **其中一条预期被证伪**：compressed writeback **不减少**后备设备的 4K 写次数（bio 长度恒为
  `PAGE_SIZE`，尾部 `memzero_page()` 补零，上游 master 同形），它省的是**写侧 CPU**并把解压搬到
  读侧 —— 所以「读回密集时可能持平或略增」这半句成立，「后备设备少写的 4K 页数」那半句不成立。
- [ ] 未覆盖：`swapoff`/`reset` 与写回**并发**的竞态、以及多日稳定性（边界见 §8 末）。

### 7. 审计基线

- 参考树：`build/abk-trees/{167,178,194,216}`（5.15.167/.178/.194/.216），四者 zram 文本一致；
- 新增 3 个组后 `GROUP_COUNTS["stable_backport_core"] = 35`（32 + 3），`PRE_APPLIED`/`KNOWN_DEBT` 不变；
- `tests/sublevel_matrix.py` 的 core 注释已同步。

### 8. 真机验证（vermeer / 23113RKC6C，2026-09-14）

内核 `5.15.216-android13-8-g5bfe2b8c1439`（= 本轮 ROM tier 构建），伴随模块 v0.6.2。完整原文、
脚本与原始输出：**`research/zram/vermeer_batch17_check/`**。全部实验在 `hot_add` 出来的独立
zram1 上做（zram0 是在用的 swap，不碰；实验期间 zram0 的 `bd_stat` 保持 `0 0 0`），
数据用 `cat /system/lib64/*.so`（页页不同且可压缩），每例校验写回前/写回后读回的 md5 与原数据一致。

**节点与语义**：`CONFIG_ZRAM_WRITEBACK=y`；`writeback_batch_size` 默认 32，`0`→EINVAL、
`257/1000000`→**夹取到 256**、`abc/-1`→EINVAL；`compressed_writeback` **只能在 `disksize` 之前写**，
已 init 一律 `-EBUSY`（上游形态）——模块 `abk_zram_attach_writeback()` 的写入顺序正因此是对的。
顺带把 Batch 12 的三条待验一并结了：`abk_{comp,lock,recomp}_algo` 存在、0444、读出 `lz4kd`/`Y`；
dmesg 有 `comp_algorithm is locked, ignoring a write of 'lzo-rle'`；`backing_dev=loop49` 已挂且
`writeback_limit` 生效。

**batching（batching 是本批的收益主体）**，64MiB × 3 次，每例 `bd=[15379 0 15379]` 完全一致：

| batch | 墙钟 | voluntary ctx switches | 写回任务 CPU（jiffy，1 j = 4 ms） |
|---|---|---|---|
| 1 | 4457 ms | 15186 | 90.7（363 ms） |
| 32 | 263 ms | 1246 | 14.0（56 ms） |

→ **17× 墙钟 / 12× 上下文切换 / 6.5× CPU**；32MiB 那轮同向（7.3–9.1× / 3.6× / 6.2×）。
batch 256 把上下文切换再压到 1/17（439 vs 7673），但墙钟不再改善 —— 瓶颈已转到 loop/闪存。

**compressed writeback：写侧省、读侧花**（64MiB，batch=32，3 次，系统级数为 8 核 jiffy、噪声较大）：

| 阶段 | cwb=0 | cwb=1 |
|---|---|---|
| 写回：任务自身 CPU（中位） | 20 j | 17 j |
| 读回：系统级 CPU（中位） | 67 j | 99 j |
| 读回：任务自身 CPU | 1 j | 1 j（解压不在读者上下文） |

即：写侧省掉那次解压（任务 CPU 低 15–25%），但把 64MiB 全部读回时**读侧系统级 CPU 高约 48%**
（解压被搬到 `system_highpri_wq`，所以不计在读者 syscall 上，但机器要付）。上游的立论是
「写回的页大多不会被读回」，按这里的量级盈亏平衡点约在**读回率 25–30%** —— 因此**默认 0 是正确取舍**，
模块不应强行替用户打开（`zram0` 上 `compressed_writeback=0` 是因为后备设备由 ROM 的 mmd 挂、
`abk_zram_has_writeback_owner()` 为真，模块按设计只保留不重写）。

**写回上限记账**：`writeback_limit=100` 块，batch 1/32/256 都**恰好写 100 页**（`bd=[100 0 100]`，
`limit` 归零）—— 这就是「提交前扣费」在真机上的证明：按完成扣费时 batch=256 最多可超发 256 页。
`rc=1`（`-EIO`）是上游形态（预算耗尽即 `break`），与 bio 错误同码。

**必须记录的平台缺口**：Enforcing 下 loop worker（`u:r:kernel:s0`）读写后备文件被 SELinux 拒，
`shell_data_file` 与 ROM 自己的 `zram_data_file` **两种上下文都一样被拒**：

```
avc: denied { write } for comm="kworker/u16:2" path="/data/per_boot/zram/b17_test.img"
     scontext=u:r:kernel:s0 tcontext=u:object_r:zram_data_file:s0 tclass=file permissive=0
```

每一页都退化成 `-EIO`，`alloc_block_bdev()` 随即归还 → `bd_stat` 全 0、写回返回 rc=1。切换
permissive 后同一脚本同一文件立刻全绿（`rc=0`、`bd=[7690 0 7690]`、md5 一致），实验结束已复原
Enforcing。含义：**这台 ROM 上 zram writeback 在 Enforcing 下不可用**，包括 ROM 自己挂在 zram0 上的
loop49（它至今没写回过一页）；这是 sepolicy 缺口，内核侧无解，要靠 ROM 集成或 KSU sepolicy 补丁。
**上面的性能数据是在 permissive 下测的** —— 它证明的是内核代码路径与代价，不代表当前设备在
Enforcing 下能拿到这个收益。

边界（没做到的也写清楚）：没在真实 swap 负载（zram0、多 GiB）下写回；没触发 `swapoff`/`reset`
与写回并发的竞态，也没做多日稳定性观察 —— bf62f69574b1 的 UAF 修复本轮**只验证了「不出错」**
（dmesg 全程无 zram 相关 splat；仅有的 5 条 `enable_irq` WARNING 是周期性设备问题，其中 2 条
早于本实验 200 秒以上）。写侧 `zram-policy.sh` 里「compressed writeback … halves the flash traffic」
的注释与实测不符，已改成 CPU 口径（纯注释，不改行为、不动版本号）。

<a id="batch-16"></a>

## Batch 16(v0.21.0)

起因：Batch 15 收编完成后复查「有没有空实现的东西」。方法不是读代码，而是拿**完整
GKI 树**当消费者语料做静态反查，再把结果与 **CI 复刻构建真实产出的 `.config`** 对齐。

语料与工具：
- 消费者语料 = `~/kci515/AOSP_Kernel_A13_5.15/common`（完整 GKI 树，30,481 个 `.c`，
  Makefile `SUBLEVEL=216`，0 个 ABK 标记 = 干净）∪ 施加后的 194 子集。子集只有 74 个
  文件，单用它判「零调用者」会有假阴性，必须用全树。
- 可达性判据 = CI 复刻构建自己的 `.config`（module 检出在 `a016643`，v0.20.0）。
- 探针脚本留在 `tmp/`（未跟踪）：`empty_impl_audit.py`、`config_gate_probe.py`、
  `reachability_probe.py`、`python_deadcode_probe.py`。

### 查出并修掉的四处真空实现

- [x] **`se->slice` 只写不读（KMI 级）**。`include/linux/sched.h:582` 用
  `ANDROID_KABI_USE(4, u64 slice)` 占槽 4；全树里该字段只出现两次——声明，
  以及 `kernel/sched/fair.c:746` 的 `se->slice = slice;`。`abk_eevdf_slice()` 写它
  然后 `return slice;` 返回的是**局部变量**，全树 0 个读取点。修法：槽 4 退回
  `ANDROID_KABI_RESERVE(4)`，删掉那次写入，并修正 fair.c 里「四个字段它都读」的错误
  注释。另三个字段（`deadline`/`vlag`/`min_vruntime`）逐个查过，都有真实读点。
- [x] **nohz 四个谓词 + 两个 `EXPORT_SYMBOL_GPL` 全树零消费者**。
  `nohz_cpu_state_test()` / `nohz_cpu_inidle()` / `nohz_cpu_idle_active()` /
  `nohz_cpu_tick_stopped()` 在完整 GKI 树里文件外调用者为 0，而
  `nohz_cpu_state_test()` 的唯一调用者就是另外三个；`nohz_cpu_state_flags()` 的树内
  消费链整条终结在这堆死代码上，`nohz_cpu_idle_calls()` 的调用点全在定义它的文件里。
  Batch 15 当时是**有意**照抄套件的「surface parity」，本轮判定为死 KMI 面：删掉
  四个谓词与两个导出，`nohz_cpu_idle_calls()` 降为 file-local `static`（它确实还被
  两个 debugfs reader 用）。真正在干活的部分保留：`enum nohz_cpu_state` +
  `abk_tick_nohz_state_flags()` + tick-sched.c 的五个读取点。
- [x] **`offload_all` 恒为 false（Batch 8 的 rcu_nocb 嫁接被编译掉）**。
  `kernel/rcu/tree_nocb.h`：`bool offload_all = false;`，两处赋值分别落在
  `#if defined(CONFIG_RCU_NOCB_CPU_DEFAULT_ALL)` 与 `#if defined(CONFIG_NO_HZ_FULL)`
  里而**两个宏都没开**，于是 `if (offload_all) cpumask_setall(...)` 编译得进去、
  永远不执行。根因很具体：该组往 `kernel/rcu/Kconfig` 加了
  `config RCU_NOCB_CPU_DEFAULT_ALL`（`default n`），但**没有任何 tier 启用它**。
  修法：加进 `_MODULE_CONFIGS`，让嫁接真正生效。
- [x] **Batch 14 在出厂配置下等于调用一个空函数**。Batch 14 的 34 行新增代码全部落在
  `#ifdef CONFIG_ZRAM_WRITEBACK` 内，而实测 `.config` 是
  `# CONFIG_ZRAM_WRITEBACK is not set`（原始 gki_defconfig 只有 `CONFIG_ZRAM=m`，
  能打开它的是 `ABK_515_DEFCONFIG_ROM=1`）。唯一穿过预处理器的 Batch 14 产物是
  `zram_remove()` 里的 `reset_bdev(zram);`，而该配置下 `reset_bdev` 就是
  `zram_drv.c:1003` 的空 inline 桩。
  **处置：保持 ROM tier opt-in，不改默认行为**，依据是模块自己已经记录的实测结论
  （`ksu/abk_runtime_tunables/tunables.conf:47-50`：目标设备上 writeback 未启用——
  内核无该 config、ROM 设 `vendor.zram.disable=1`、其 mmd 从不完成）。默认打开只会
  在任何**别的** ROM 上白送一个吃闪存寿命的能力，在目标机上毫无收益。

### 另外三处小项

- [x] `scripts/abk_common.py` 的 `replace_once_any()`：全仓库只有定义，无调用者，删。
- [x] 运行时伴侣两个死函数：`common.sh: abk_checkpoint()`、`zram-policy.sh: abk_zram_dir()`，删。
- [-] `scripts/batch15_core_swap_table.py` 仍是 `REGISTER_AS_CHILD = False`、只被
  docstring 提到。这是 Batch 15 有意的「评估留档、不接线」，不属于空实现，保持原样。

### 新增的防回归闸门

- [x] `scripts/abk_stable_core.py` 新增 `_INTRODUCED_KCONFIG`：本模块引入基线树的每个
  Kconfig 符号 → 启用它的 tier（`None` 表示 Kconfig 自带可用默认值）。
- [x] `tests/stable_5_15_test.py::test_introduced_kconfig_tiers()`：正反双向断言该表与
  `_MODULE_CONFIGS`/`_ALIGN_CONFIGS`/`_ROM_CONFIGS` 一致。**这正是能提前抓住
  Batch 8 那条的闸门**——「引入了符号但没有任何 tier 能打开它」以后会直接红。
  （反向检查只覆盖 module tier：align tier 的符号来自 6.6 GKI defconfig，不是本模块引入的。）
- [x] `tests/implementation_audit.py` 增加 `REQUIRED_ABSENT`：上述被删符号不得回归；
  `tests/smoke.sh` 的槽位断言由「必须占槽 4」改为「必须退回 `ANDROID_KABI_RESERVE(4)`」。

### 验证

- 四棵基线树（167/178/194/216）`step_audit` + `implementation_audit` + `smoke` 全绿；
  `py_compile`、`bash -n`、`sh -n`、单测全绿。
- 编译验证见本批次末尾（CI 复刻构建）。

### 后续追问：被删的两处「是不是只是没挂上」

Batch 16 把四处真空实现分成「删掉」与「接上」两类；被删的是 `se->slice` 槽 4 和 nohz 的
四个谓词 + 两个导出。追问是合理的：**如果消费者本来就该由我们的某个补丁挂进去而没挂上，
那我数的就是自己的 bug，然后把它删了。** 因此判据改回**套件原文**重取（不能在自家 graft
完的树上数调用者）：

- `ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py` 里
  `nohz_cpu_state_test`/`_inidle`/`_idle_active`/`_tick_stopped` 只出现在头文件插入块
  （:2122-2174）和它自己的 presence 自检表（:2789-2793），**套件里也 0 个调用点**；
  套件 `tests/smoke.sh:494-507` 同样是 grep 存在性——自证式检查，检查「我插入了我插入的
  东西」。`nohz_cpu_idle_calls` 的消费者只有 tick-sched.c 里两个函数，而那两个改写我们的
  port **是有的**（`batch15_perf_sched_refinements.py:314-342` + 步骤 687-688），所以它
  降为 file-local `static` 后依然在干活。
- `se->slice`：套件里唯一出现就是 `abk_eevdf_slice()` 的那一次写入（:643-649），
  `abk_eevdf_vslice()` 用的是**返回值**（:653）。即使照套件原样，该字段也只写不读。
- 树外也扫过（`ABK`、`ABK_repo`、同目录其它模块）：无引用。
  **证据边界**：导出符号的消费者可以在树外，这里能证明的只是「现有这些树里没有」。
- 结论：这两处是**套件自带的死面**，不是我们的接线遗漏。同类确有一例是真的——
  `offload_all`（见上），处置是**接上**而不是删除。

### 后续追问：把这一类做成闸门（`tests/config_gate_audit.py`）

`_INTRODUCED_KCONFIG` 只覆盖「本模块引入的符号」，对**依赖的既有符号**是盲区
（`IRQ_TIME_ACCOUNTING`/`PSI`/`ANDROID_VENDOR_HOOKS`/`NO_HZ_FULL`…）。新闸门补这一侧，
归属用 `.abk-orig` 快照做 diff，而不是「附近有没有标记」：

- **A 类**：本模块**新增的**门行（`#ifdef CONFIG_X` / `IS_ENABLED(CONFIG_X)`），符号在
  `.config` 里是关的。
- **B 类**：新增代码落在**上游既有**的门里，而该符号是关的。
- 两类的符号都必须在 `.config` 里开着，或在脚本的 `DARK_GATES` 里逐条写明理由；
  「某个 tier 声称启用它、而 `.config` 说没开」单独判硬失败——那正是 **Kconfig 依赖不满足**
  导致 tier 静默失效的特征（Batch 8 那条的形状）。

归属必须用 diff：先写的启发式版本（30 行窗口找标记）**既误报又漏报**——把上游的
`IS_ENABLED(CONFIG_FS_DAX_PMD)`（`mm/huge_memory.c:585`）和 `CONFIG_PREEMPT_RT`
（`mm/slub.c:3228`）当成我们的门，同时漏掉 RCU 那条（它的标记在 `#if` **上方**，不在门内）。
两个错误都是实测撞出来的，记在这里以免下次退回启发式。

实测（对 CI 复刻构建的 `.config`；60 个 `.abk-orig`，A 类 37 行 / B 类 18 处）：

| 结果 | 符号 | 判定 |
|---|---|---|
| 硬失败 | `RCU_NOCB_CPU_DEFAULT_ALL` | tier 声称启用、`.config` 未开。该 `.config` 早于 Batch 16，属预期；按当前 tier 重建后应转绿 |
| 记录在案 | `ZRAM_WRITEBACK` | Batch 14 的 ROM tier 决策（见上） |
| 记录在案 | `ZRAM_MEMORY_TRACKING` | 5.15 用 `#ifdef .../#else` 决定 `zram_accessed()`，两个变体都已就地改写；符号关时走 `#else` 那个（`zram_drv.c:1115-1121`），带 ACTIME 门，已逐行核对 |
| 记录在案 | `NO_HZ_FULL` | 门内新增行是该配置下的精化，符号关时不存在竞争掩码，构造上正确 |

分辨力双向验过：对原 `.config` 报 1 条（RCU）并 exit 1；只把那一行翻成 `=y` 后输出
`CONFIG GATE AUDIT OK` / exit 0。四棵基线树既有三项审计仍全绿。本闸门需要一份**由当前
tier 产出的** `.config` 才有意义，因此不并入那三项（它们不需要编译）。

### 编译验证（本地复刻构建，第一次真正编到 Batch 16）

上面那条 RCU 硬失败要转绿，必须有一份**由当前 tier 产出**的 `.config`，而此前所有
`.config` 都来自 Batch 15 代码。本次补上，同时这也是 Batch 16 的代码第一次被编译。

- 注入方式：`env.sh` 的 `CUSTOM_EXTERNAL_MODULES` 里三个 `set:` 从
  `https://github.com/fanziyun/ABK_5.15_BACKPORT` 改为**本地工作副本路径**
  `/mnt/c/Users/Administrator/StudioProjects/ABK_5.15_BACKPORT`（`rebuild.sh` 对本地路径走
  `rsync --delete` 分支，改完直接编，不需要 commit/push）。备份
  `.local-build/env.sh.bak-precswitch`。
- `./rebuild.sh --reseed --allow-dirty-template`：`REBUILD_EXIT=0`，墙钟 **12m28s**
  （user 65m12s）。不是 CI 复刻对照——本轮没有参照 run，只做本地产出与闸门验证。
- 模块装载三方对账（`CUSTOM-MODULES.md` §6 判据）：`preparing` 7 行 == `running` 7 行 ==
  `state/custom_external_modules.tsv` 7 行；本模块三个子模块的 `entry_kind` 均为
  `module_set_child`，`child_id` 为 `stable_backport_core` / `stable_perf_backport` /
  `stable_display_fix`，`repo_url` 为上述本地路径（证明走的是路径分支而不是镜像）。
- **新 `.config` 实测**（`out/android13-5.15/common/.config`，11:36:25）：
  `CONFIG_RCU_NOCB_CPU_DEFAULT_ALL=y`（Batch 16 的 tier 修复生效，且符号的 Kconfig 依赖
  未被漏掉——这正是本地闸门要判的那条）、`CONFIG_ABK_DYNAMIC_READAHEAD=y`、
  `CONFIG_ZRAM_TRACK_ENTRY_ACTIME=y`、`CONFIG_ZRAM_MULTI_COMP=y`、
  `CONFIG_ZSMALLOC_CHAIN_SIZE=8`。
- `tests/config_gate_audit.py` 对这次产出：**`CONFIG GATE AUDIT OK` / exit 0**；A 类门行
  由 37 降到 36，差值恰为 Batch 16 删掉的那个 nohz `#ifdef CONFIG_NO_HZ_COMMON`。
- 产物：`android13-5.15.216-lts-AnyKernel3.zip`（22.9 MB / 23 个成员，含
  `abk-ksu-modules/abk_runtime_tunables.zip` 伴侣模块）+ 三个 boot 镜像；
  `build-meta.txt` 的 `template_common_commit=5bfe2b8c…`、`abk_source_commit=a8c738f0…`、
  `build_system=legacy` 与参照物一致。

**边界（未做与做不到的）**：本轮没有 CI run 作对照物，因此没有 `compare-patch-outcomes` /
`verify-parity.py` 级别的逐成员对齐；`.BTF`、`Image`（自签证书每次必变）、`revision` 与
辅助产物均未逐字节比对；模块是**本地路径**注入，与远端 `set:` URL 注入路径不同（后者需要
push，当前凭据不可用，远端仍在 `a016643`）。

<a id="batch-15"></a>

## Batch 15(v0.20.0)

### 政策变更：suite-preference 作废

原规则是「每个候选先查 ABK_ABI_PATCH_SUITE 清单，它覆盖的优化本模块**不**重复实现
（改为注入套件）」，叠加 KMI 红线「不 claim `sched_entity` 槽 1–4、`request_queue`
槽 1（套件领地）」以及「不改写套件硬失败组的函数体」。这套规则让本模块**永久依赖
第二个外部模块**，优化面被拆到两个仓库。Batch 15 全部撤销，改为**收编**：

- 本模块**自己**占用 `sched_entity` 槽 1–4（EEVDF：
  `deadline`/`min_vruntime`/`vlag`/`slice`）与 `request_queue` 槽 1
  （`async_depth`）。
- **两个模块从此互斥**：同一构建不得再注入 ABK_ABI_PATCH_SUITE——双占同一 KMI
  槽是硬冲突。注入本模块**而不是**套件。
- 原「不改写套件硬失败组函数体」限制作废：`alloc_pid()`、
  `pick_file()`/`__range_close()`、`select_idle_cpu()`、`pick_next_entity()`
  现在由本模块自己接管。
- F2FS/UFS sibling 边界与「不引入 .patch 载荷」不变。

依据与逐项证据：`docs/survey_suite_absorption.md`（英文，含文件布局探针结果）；
政策文本见 `docs/porting_policy.md` 的 "Suite absorption"。

### 落地（core 27→32，perf 13→18，共 51 组）

| 组 | 子模块 | 内容 |
|---|---|---|
| `pid_alloc_hotpath_phase2` | core | `alloc_pid()` 的 `idr_preload(GFP_KERNEL)` 单次重试 |
| `fd_alloc_hotpath` | core | `abk_expand_files_needed()` 预检 + 两个调用点 |
| `close_range_hotpath` | core | `__range_close()` 改走 `open_fds` 位图（5.15 形态） |
| `slab_alloc_free_hotpath` | core | 共享 `abk_slab_next_object()` + 批量分配预取 + free 侧单次解析 |
| `hugepage_fault_alloc_fastpath` | core | 匿名 THP fault 路径的 helper 拆分（huge_memory.c + memory.c） |
| `blk_mq_async_depth` | perf | 真正的 `q->async_depth` 队列深度策略（9 文件） |
| `sched_eevdf_pick_logic` | perf | 扫描式 EEVDF 选择器（14 步，`kernel/sched/fair.c`） |
| `sched_eevdf_core_fields` | perf | **KABI 槽认领**（`ANDROID_KABI_USE(1..4)`） |
| `nohz_field_refinement` | perf | `tick_sched` 状态字段命名化 + 访问器 |
| `avg_idle_preemption_mode` | perf | 退役 `wake_avg_idle` 唤醒侧预测，SIS_PROP 预算改由 `avg_idle` 直接给 |

注册顺序有两处**是载荷相关**的：`sched_eevdf_pick_logic` 必须排在
`sched_eevdf_core_fields` **之前**——后者是反漂移闸门，只有 fair.c 真的落地
（探测本模块 marker + 三个调用点）才认领槽位，否则槽位保持 `RESERVE`。
`fd_alloc_hotpath`/`close_range_hotpath` 必须在 `fdtable_alloc_conventions`
之后（`_fd_alloc_apply()` 用形状探针强制）。

### 收编过程中修掉的四个**套件自身缺陷**（都是「编译通过但行为错」类）

1. **`alloc_pid()` 重试用 `continue;`**（`for (i = ns->level; i >= 0; i--)`）——
   `continue` 会跑循环增量，于是重试的是**父级**（且重跑 `set_tid` 记账）；
   在 `ns->level == 0`（所有普通 fork）会跳出循环、落到函数尾的
   `retval = -ENOMEM;` 并返回一个 `numbers[0].nr` **从未写入**的 pid。
   本线改为 `goto retry_preload`，并加了每级一次的 latch。
2. **bfq/kyber 的 `async_depth` 量纲错**：`q->async_depth` 是**请求数**，而
   `kqd->async_depth`/`bfqd->word_depths[][]` 是**每 word 的 bit 上限**
   （`sbitmap_queue_get_shallow()` 只在一个 word 内限 bit）。套件只给 mq-deadline
   做了换算，bfq/kyber 直接赋值——256 深的队列上默认 192 ≥ 一个 word，
   **两个消费者永不限流（死代码）**。本线在两处内联同样换算。
3. **`update_rq_avg_idle()` 无条件采样**：`delta = rq_clock(rq) - rq->idle_stamp`，
   而 5.15 的 `idle_stamp` **只由 `newidle_balance()` 装载**，所以没走过那条路的
   CPU（boot CPU、idle→idle repick）`idle_stamp == 0`，采到的是「开机至今的
   纳秒数」并直接顶到 `2*max_idle_balance_cost`。原始 `ttwu_do_wakeup()` 正因
   如此才有 `if (rq->idle_stamp)`。本线恢复该护栏。
4. **`reweight_entity()` 整函数替换在 194/216 上必静默跳过**：这两个 sublevel 在
   `update_load_set()` 与 CONFIG_SMP PELT 块之间插了
   `trace_android_vh_reweight_entity(se);`，套件的整体 old 因此**不匹配任何
   sublevel**——而它的 `if old in text:` 会静默放过并报成功。本线在该接缝处
   把锚点拆成 head/tail，hook 留在原位。

另外 5.15 语义坑（已适配，见各组 docstring）：`khugepaged_enter()` 在 5.15 返回
**int**（非 0 = 失败），6.1 改名 `khugepaged_enter_vma()` 返回 void；`slab_free()`
在 5.15 是六参数且无 `struct slab`；5.15 没有 `SIS_UTIL`/`nr_idle_scan`，
套件的 6.1 分支会让 `nr = INT_MAX` 扫整个 LLC。

### 未收编（含证据，不再重议）

- **io_uring NOWAIT / cBPF / non-circular SQ / zcrx**：套件目标是 5.18 之后拆分的
  `io_uring/*.c` 布局，而 5.15 是**单文件 11,116 行** `io_uring/io_uring.c`；
  手搬到 monolith 是独立工程，不是收编。套件自己在旧布局上也判
  `blocked_by_missing_anchor`。
- **`swap_table_phase2_large_folios`**：`mm/swap.h` 在 5.15 **不存在**，
  且 5.15 没有 `struct folio`（`grep -rn "struct folio" include/linux mm` = 0）、
  12 个被调用者全缺、读路径约定不同（5.15 `int swap_readpage(struct page*, bool)`
  无 plug/unplug）。退化成 page-first 改写只是**行为中性的代码搬移**，按本模块
  范围规则排除。评估留在 `scripts/batch15_core_swap_table.py`
  （`REGISTER_AS_CHILD = False`，`build_groups()` 返回 `[]`，**未接线**）。
- **`bpf_timer_bpf_wq_lockless`**：`defer_timer_wq_op` / `bpf_wq` 在 5.15 **和**
  6.1 都是 0 命中——该组在套件自己的目标形态上也不成立。
- **`zram_compressed_writeback`**：套件版本只加 `compressed_wb` 标志 + sysfs 属性，
  **没有 I/O 实现**；真特性需要 `zs_obj_read_begin/end`（两棵树都没有）。
  与 Batch 14 审计（`research/zram_writeback_plan.md` §6）结论一致。
- **marker-only 组**：`sched_eevdf_runtime_state_phase3`、
  `io_uring_large_rx_buffer_zcrx` 只插一行注释，收编会造出 phantom 组。
- **`io_uring_support_modules`** 只做分类、不写文件；`tcp_socket_layout_reduction`
  /`ipv6_tcp_output_path` 套件自己就判 `blocked_by_layout`/`report_only`。

### 顺带修掉一个**审计漏洞**（影响 Batch 14）

`tests/step_audit.py` 通过猴补丁 `module.apply_steps` 采集每步状态，但
`scripts/batchNN_*.py` 用的是 `from abk_backport_engine import apply_steps`——
在 import 期就把**原函数**绑进了自己的模块全局，猴补丁够不到。后果：这些组的
步骤既不进 `group_steps`，于是 trap 1b（步骤级 `already_present`）、注释/括号/
`#ifdef` 平衡检查、以及**第二遍字节一致性**检查**全部静默跳过**，而组级状态断言
照常通过——**Batch 14 的三个组一直没被审计过**。修法：把仍指向原函数的已加载
模块一并重绑（并在 finally 恢复）。修复后 core 步数 151→177、perf 83→114，
四棵树仍全绿，Batch 14 首次真正过审。

审计基线：`step_audit.py` / `implementation_audit.py` 在 5.15.167/.178/.194/.216
四棵树全绿（`GROUP_COUNTS` core 27→32、perf 13→18）；`module.conf` 0.19.0→0.20.0。

<a id="batch-14"></a>

## Batch 14(v0.19.0)

来源：一次纯审计+上游溯源（`research/zram_writeback_plan.md`，英文，含逐提交
证据、依赖图、N/A 裁决与性能分轴评估；上游哈希复核见
`research/zram_wb_audit/verified_commits.tsv`）。结论：android13-5.15 的 zram
**writeback 原语是完整的**（backing_dev + 空闲块位图、huge/idle writeback、
`writeback_limit`/`_enable`、`bd_stat`、零长度设备拒绝都有），缺的是 2022 年
之后上游对这些代码的三处**修正**——而且 `linux-5.15.y`（SUBLEVEL 220）同样没有。

落地三组，全部只在 `drivers/block/zram/zram_drv.c`：

- [x] `zram_wb_teardown`（`74363ec674cb`，v6.16，Cc: stable）——**P0**：
  在 `disksize` 之前挂上 backing_dev 后，若设备从未 init 就 reset/移除，
  `zram_reset_device()` 的 `if (!init_done(zram))` 提前返回会跳过 `reset_bdev()`，
  于是独占的 `blkdev_get_by_dev` 持有者与 `struct file` 一直不释放，
  把 backing 块设备和 zram 自己钉死到模块卸载。上游删掉了那个提前返回；
  **本线不能照做**（见下），改为在 `zram_remove()` 里紧跟
  `zram_reset_device(zram);` 调一次 `reset_bdev(zram);`——语义等价
  （提前返回唯一漏掉的正是这一次释放），且 `CONFIG_ZRAM_WRITEBACK=n` 时
  `reset_bdev()` 是内联空实现，无条件调用安全。同事务带上
  `zram_meta_free()` 的 `if (!zram->table) return;` 护栏与 `zram->table = NULL;`
  （防止该函数被以未初始化设备进入时 `zs_destroy_pool(NULL)` 解引用）。
- [x] `zram_writeback_bounds`（`894913e2d35c`，v7.3，Cc: stable，Fixes:
  a939888ec38b + `424d0e5828ad`，v6.14）——**P0/P1**：`writeback_store()` 在
  `down_read(init_lock)` **之前**就把 `nr_pages` 从 `zram->disksize` 算好，
  而 `zram_reset_device()` 是在写锁下改写 `disksize` 并重建 `zram->table` 的；
  两者之间夹一次「reset 成更小 disksize」就会让扫描边界描述旧表、
  循环越界访问新表（Android 的 mmd/recompress 守护进程正好是这个模式）。
  上游补丁是写给 2024 年 `dev_lock` + `lo/hi` 形态的，本组按 5.15 的
  `init_lock` + `index/nr_pages` 形态逻辑移植：拆掉锁前的初值与范围检查，
  在 `init_done()` 检查之后、读锁内重新取边界并复检；同组带上
  `cond_resched()`（一次扫描可能遍历数 GiB 磁盘的全部 slot，且每页一次
  `alloc_page` + 解压 + 阻塞 `submit_bio_wait`）。
- [x] `zram_wb_limit_align`（主线 `writeback_limit_store()` 的对齐护栏）——
  **P1**：`bd_wb_limit` 每页扣 `1UL << (PAGE_SHIFT - 12)`，所以
  `PAGE_SIZE > 4KiB` 时预算 1..3 会在第一次成功回写后 u64 回绕成无限，
  磨损上限静默失效；加 `val = rounddown(val, PAGE_SIZE / 4096);`。
  当前 4K 页构建上不可触发，属保险（Android GKI 正在往 16K 页走）。

**一个必须记住的锚点冲突（不要"修回去"）**：`zram_recompression` 组注册在本批
**之前**，它重写 `zram_reset_device()` 且锚点就是该函数的**整段原始函数体**。
按上游删掉提前返回，等于改掉那个锚点 → 第二遍 `step_audit` 里
`zram_recompression` 报 `blocked_by_shape`（文件字节没变，但"可证幂等"不再成立）。
两个方向都试过（在 teardown 侧删、在 recompression 侧留标记），互相破坏。
所以本批**不动 `zram_reset_device()`**，改在 `zram_remove()` 闭环。

**已明确不做、不再重议**（理由逐条见 §4/§5/§10.5）：

- `type=` / `page_indexes=` / 区间（6.16 `cf42d4cccf0d`）——**N/A**：
  纯现代用户态接口，android13-5.15 没有任何消费者，且它依赖 2024 年的
  pp-slot 目标选择重写。
- writeback bio 分批 + `writeback_batch_size`（v6.19）与
  **compressed writeback**（v7.0 `d38fab605c66`）——**延后**：前者要连
  pp-slot 机制一起搬（4 个 series），且必须同时带上它自己的 UAF/泄漏修复
  （`bf62f69574b1`、`3e8d8eb8d7f5`）；后者的真实阻塞是 5.15 没有现代
  zsmalloc 映射 API（`zs_obj_read_begin/end`），并且与本模块
  `zram_recompression` 的 `zram_read_from_zspool()`、以及
  ABK_ABI_PATCH_SUITE 的 `compressed_writeback` 控制面**同名冲突**。
- `be48c412f6eb`（拒绝零长度 backing device，5.15.168 才进）——**不补**：
  5.15.167 是唯一缺它的基线，而它**不是**本批的编辑输入（teardown 不依赖它），
  单独成组也做不干净（替换文本必然包含原始护栏块，在 178/194/216 上无法区分
  "基线自带"与"前面步骤加的"，会误报 `applied`）。留作套件候选。
- 2023+ 的 writeback 重构（`330edc2bc059` / `5e99893444a0` / `b967fa1ba72b`）——
  **N/A**：换设计而非修 bug，5.15 要关的那个竞态 `idle_store()` 的
  `ZRAM_UNDER_WB` 检查已经关掉了。

审计基线：`step_audit.py` / `implementation_audit.py` / `smoke.sh` 在
5.15.167/.178/.194/.216 四棵树上全绿；`GROUP_COUNTS` core 24→27；
`module.conf` 0.18.0→0.19.0。

<a id="batch-13"></a>

## Batch 13(v0.18.0)

起因：调研两个 GKI vendor hook（全文见 `research/hooks_gfp_vs_wake_up_new_task.md`，
上游补丁存档 `research/upstream-5.15.y/patches/4466afd69452.patch`）。

- `android_rvh_wake_up_new_task`（受限钩子，`wake_up_new_task()` 首条语句，
  `include/trace/hooks/sched.h` 声明 + `kernel/sched/core.c` 调用）：**四条基线
  （.167/.178/.194/lts）逐字自带**，形状与 6.x 相同 → 不建 group；未来的
  FAS fast_start / 初始放置 payload 直接 `register_trace_android_rvh_wake_up_new_task`
  （与 Batch 10 挂 `android_vh_scheduler_tick` 同族）。已排除，不再重议。
- `android_vh_customize_alloc_gfp`（android15-6.6 引入，commit `4466afd69452`，
  Bug 337192903，OPPO；6.1 与 5.15 全线没有）：慢路径入口把**即将用于
  `__alloc_pages_slowpath()` 的 gfp** 按指针交给回调改写。落地两组：

- [x] `customize_alloc_gfp_vh` —— 3-hunk **upstream-shape** 逐字移植
  （`include/trace/hooks/mm.h` 声明 + `mm/page_alloc.c` 调用 +
  `drivers/android/vendor_hooks.c` 导出），不加 ABK 标记 ⇒ 未来基线自带该
  commit 时自动 `already_present` 且零写入。可行性前提已实测：ACK 早已把 6.1 的
  cpuset 快路径系列收进 android13-5.15，慢路径入口块与 6.6 逐字相同
  （变量即 `alloc_gfp`），三处锚点在 .167/.178/.194/lts 上各唯一。
- [x] `gfp_pressure_fastfail` —— ABK 策略载荷（`mm/page_alloc.c` 文件尾，
  `#ifdef CONFIG_ANDROID_VENDOR_HOOKS` 内）：`__alloc_pages()` 快路径失败进入
  慢路径前，若 `si_mem_available()` < high 水位和的 `abk_gfp_fastfail_pct`%
  （默认 50，水位和每秒采样一次，沿用 Batch 9-1 的门形状），order ≥
  `abk_gfp_fastfail_order`（默认 9 = 4K-page arm64 的 THP 级）的尝试加上
  `__GFP_NORETRY|__GFP_NOWARN`。5.15 的 NORETRY 语义（实测 `.167` 的
  `__alloc_pages_slowpath` @5408）= 各允许一轮直接回收+压缩、**不进**
  compact/reclaim 重试循环，请求快速失败回落到调用方既有 fallback（THP→4K 页、
  宽容调用方拿 -ENOMEM），消除碎片化近满内存下的毫秒级分配停顿。**默认档
  不是 no-op（评审质疑已实测排除）**：`.167` 的 `GFP_TRANSHUGE_LIGHT` 只带
  `__GFP_NOWARN`、不带 `__GFP_NORETRY`（`include/linux/gfp.h` @365），默认
  defrag=madvise 下 madvised fault 走 `LIGHT | __GFP_DIRECT_RECLAIM`、
  khugepaged defrag 走 `GFP_TRANSHUGE`（`vma_thp_gfp_mask` @688、
  `khugepaged.c` @837）——全是可重试类，正被本门捕获；外加 hugetlb 运行期
  扩池与驱动 order-9。`abk_gfp_fastfail_pct=0` = 移除压力门（恒快速失败），
  关开关用 `page_alloc.abk_gfp_fastfail=0`；启动期 CMA/hugetlb 池建立
  **故意**不在覆盖范围（`late_initcall` 之前发生，那类分配该重试）。
  knobs 全部
  0644，落在 `/sys/module/page_alloc/parameters/`（obj-y 按文件基名的
  module_param 归属约定，Batch 9-1 的 `readahead.dynamic_readahead` 已验证该
  形状）。两组都先探测三要素——mm.h 声明、page_alloc.c 调用行、同文件
  `#include <trace/hooks/mm.h>`；gate 的两次全局量读写走
  READ_ONCE/WRITE_ONCE（hook 从分配器上下文无同步触发）——缺一即
  `blocked_by_shape` 且零写入（守 AGENTS.md 陷阱 5 族：文本
  门禁全绿而编译死在 undefined `register_trace_`，或声明了却永不触发）。
- [x] KMI 自查：只**新增** `__tracepoint_android_vh_customize_alloc_gfp` 与
  key 的导出（纯增量），不动任何导出结构体字段、不占 KABI 槽，现有符号 CRC
  不变 ⇒ 既有 GKI vendor 模块照常加载；`CONFIG_ANDROID_VENDOR_HOOKS=y` 基线
  gki_defconfig 自带（.167 实测），config lane 无动作。
- [x] 配套：`tests/fetch_sublevel_tree.sh` FETCH_FILES 与 `step_audit.py`
  AUDIT_FILES 补 `include/trace/hooks/mm.h`、`drivers/android/vendor_hooks.c`
  两文件；`GROUP_COUNTS(core)` 22→24；单测三组（含 hook 顺序守卫、carried 树
  `already_present` 零写入、缺 include / 缺调用行的 `blocked_by_shape`
  负例、`module_param` 名≡变量名扫描、载荷 idempotency，以及
  wake_up_new_task 的**负面 pin**——pin 住"四条基线逐字自带、不建组"的
  调研结论，防止将来误建重复 graft）；`implementation_audit.py` 加两组
  REQUIRED_CONTENT + hook 组的 per-file REQUIRED_ABSENT（mm.h / vendor_hooks.c
  两文件必须零 ABK 标记——upstream-shape 的机检化；REQUIRED_ABSENT 因此
  新增 `[file, needle]` 作用域形态，page_alloc.c 混有他批标记不能整 blob
  扫）+ 载荷 handler 门与压力门（含 READ_ONCE/WRITE_ONCE、禁 racy
  `+=`）的 REQUIRED_IN_FUNCTION；lts 树已滚动到 SUBLEVEL **216**，
  `sublevel_matrix` 的 PRE_APPLIED/KNOWN_DEBT 行键 211→216（重取树后按
  Makefile 重新对键是 policy 既有要求）；`docs/porting_policy.md` 基线表
  core pass-1 计数随 registry 移动（17→19 / 14+3→16+3）。

待验证：ABK CI 编译（本批引入 C，编译是唯一真门禁）；真机上
`/sys/module/page_alloc/parameters/abk_gfp_fastfail*` 三节点存在、低内存高压下
`compact_failures`/direct reclaim 停顿与 THP 成功率的对照另立后续批次
（distribution asset，不进 registry）。

<a id="batch-12"></a>

## Batch 12(v0.15.0)

真机复勘（同一台 vermeer，`5.15.215`）暴露三个新事实，本批次按事实改设计：

| # | 事实 | 证据（adb 实测） |
|---|---|---|
| 1 | **算法仍可被 root 改掉** | 某次 boot 后 `comp_algorithm` 停在 `[deflate]`；`CONFIG_ZRAM_DEF_COMP="lz4kd"`，即 deflate 是**被写进去的**；`echo lz4hc > comp_algorithm` 返回 `EBUSY`（`dmesg: Can't change algorithm for initialized device`），唯一改写路径是 `swapoff + reset + 重写`，而模块当时要 30 min 才复检一次 |
| 2 | **ROM 的 zram 主不是 mmd** | `/product/etc/build.prop: vendor.zram.disable=1`；`mmd.setup_complete` 未设置、`init.svc.mmd` 不存在；真实 owner 是 `/vendor/bin/init.kernel.post_boot.sh: configure_zram_parameters()`（`disksize` + `mkswap` + `swapon -p 32758`，**完全不写算法**） |
| 3 | writeback 互斥的根因是窗口 | `backing_dev` 与 `comp_algorithm`/`recomp_algorithm` 都只在 `disksize` 之前可写，而 `zram_reset_device()` → `reset_bdev()` 在 reset 时关闭 backing device：所以"修算法"必然要重挂 backing device，旧模块选择了放弃一边 |

### 落地

- [x] 新 graft `zram_algo_lock`（`scripts/batch11_core_zram_algo_lock.py`，core `GROUP_COUNTS` 21→22）：
  - `zram.abk_comp_algo`（0444，默认 `lz4kd`）：`zram_add()` 里在创建磁盘前选定主算法；
    该 build 没有该 backend 时保留 `CONFIG_ZRAM_DEF_COMP` 并 `pr_warn`。
  - `zram.abk_lock_algo`（0444，默认 `Y`）：`late_initcall` 把
    `dev_attr_comp_algorithm.store` / `dev_attr_recomp_algorithm.store` 指向
    "记录并返回成功"的 store —— **接受写入但保留锁定值**。不用 `-EPERM`：Android 16 的
    `mmd_setup` 在算法写失败时会放弃整条 zram bring-up（含 writeback 与
    `mmd.setup_complete`），拒绝反而会重建本批次要消除的互斥。
  - 锁在 `reset` 后仍然有效：`zram_destroy_comps()` 只清 `comps[]`，不动 `comp_algs[]`。
  - 锚点全部落在**其它组的块边界或 pristine 文本**上（`default_compressor`、
    `zram_debugfs_register(zram);`、`module_init(zram_init);` 之后），不修改任何已有组的
    替换文本 —— 否则那些组的第二遍幂等会失效（实测踩过：把锁插进
    `__comp_algorithm_store` 会让 `zram_recompression` 报 `blocked_by_shape`）。
- [x] config 分档：新增 ROM 档 `ABK_515_DEFCONFIG_ROM=1` → `CONFIG_ZRAM_WRITEBACK=y`
  （默认关；`_config_enablement_apply` 现在报 `[module-owned symbols only, ROM integration]`）。
- [x] 模块侧（`ksu/abk_runtime_tunables`，v0.2.0）：
  - `abk_zram_ensure()`：只有"策略确实不成立"（算法不对 / 未初始化 / 无 swap /
    writeback 该有而没有）才改写；内核带锁时**完全不碰 bring-up**，只重设 `mem_limit`。
  - writeback 不再是"有节点就让位"：改写前先记住 `backing_dev` + `writeback_limit`，
    reset 后在同一个 pre-`disksize` 窗口**原样挂回**（同一个 loop，不新建）；
    内核支持且无人占用时按 `zram.writeback=auto` 自己建稀疏 `backing file`
    （`/data/per_boot/zram/zram_swap`，`losetup`，限额 4 KiB 单位，
    有 `compressed_writeback` 则打开）。
  - swap 在用 + 存在活跃 writeback 时明确记日志"keeping the live writeback device"并拒绝改写。
  - 监督循环改为 tick：`zram.reassert_interval_sec`（默认 60 s）复检策略、
    `zram.recomp.interval_sec` 跑 sweep；未加锁内核上算法被改在 60 s 内被改回。
  - `action.sh unlock` 说明锁只能从 cmdline 关（0444 参数）。
- [x] 测试：`test_batch11_zram_algo_lock`（单测夹具 + 幂等 + 空树降级）、
  `test_config_tiers`（三档互斥与内容）；模块夹具新增 5 个场景（自动建立 writeback、
  活跃 writeback 跨 reset 保留、活跃 writeback + swap 在用拒绝、锁内核零改写、
  监督 tick）；`implementation_audit` 新增 `core:zram_algo_lock` 整文件/函数级断言。
- [x] 门禁：`py_compile`、`bash -n`、单测 360 项全绿；`step_audit` /
  `implementation_audit` / `smoke` 在 167/178/194 全部 OK。
- [x] CI 编译修复：ABK CI run 34497599075（`5.15.X-android13-lts`）在 `编译内核`
  失败，clang 报 `drivers/block/zram/zram_drv.c:75:14: error: use of undeclared
  identifier 'abk_lock_algo'` —— `module_param(name, ...)` 会把 `name` **当作变量名**
  编译，而变量叫 `abk_zram_lock_algo`。已改用两名字形式
  `module_param_named(abk_lock_algo, abk_zram_lock_algo, bool, 0444)`。
  这类"文本门禁全绿、编译才炸"的错误已写进 `AGENTS.md` 的 step-authoring traps（第 5 条），
  并在单测里加了一条扫描：文件里每个 `module_param(X, ...)` 都必须有同名变量声明
  （真机树上验证：只有 `num_devices` 用单名字形式，且已声明）。
- [ ] 待验（需刷入新内核）：`zram.abk_lock_algo` / `zram.abk_comp_algo` 出现且 0444；
  root 写 `comp_algorithm=deflate` 后节点仍为 `[lz4kd]`（dmesg 有 "is locked to" 一行）；
  `ABK_515_DEFCONFIG_ROM=1` build 上 `backing_dev` 被模块挂上且 `writeback_limit` 生效。
- [ ] 待验（本模块刷入即可，无需新内核）：60 s 内把被改掉的算法改回；日志出现
  `rewrite: size=…`；`action.sh status` 的 `policy` 行。

### 本轮顺带修正（旧实现的两处判断）

- 旧模块把"内核暴露了 `writeback` 节点"当成"ROM 的 mmd 在管 writeback"而整体让位，
  在本机（节点不存在）看不出来，一旦开了 `CONFIG_ZRAM_WRITEBACK` 就会把算法策略
  白送出去：现在只有 `backing_dev` 非 `none` 或 `mmd.setup_complete=true` 才算"有人在用"。
- 旧模块每次 boot 都无条件接管，撞上 ROM 的 `init.kernel.post_boot.sh`（本机 32 s）
  就会 `swapon`/`disksize` 双双失败并留下半套状态（真机日志：`swapon failed` →
  `write failed: disksize` → `RECOVERY FAILED`）。现在算法已正确就根本不动设备。

### 真机验证（本轮已跑完，均为未加锁内核上的模块侧行为）

| 验证 | 结果 |
|---|---|
| 装 v0.2.0 模块 | `ksud module install` + 就地应用 `modules_update`，`action.sh status` 打出新字段（`zram.abk_lock_algo absent (module enforces the policy instead)`、`policy in force`） |
| 监督 tick | 日志每 60 s 一条（`reassert=60s`），与 `zram.reassert_interval_sec` 一致 |
| **用户切断算法** | 模拟 root 用户 `swapoff + reset + echo deflate + disksize + swapon`：60 s 内被模块发现并改写回 `[lz4kd]`，日志 `algorithms changed behind the module (primary=deflate …)` → `rewrite: size=17179869184 …` → `zram ready` |
| 容量保持 | 同一次改写把 ROM/用户的 **16 GiB 原样保留**（`/proc/swaps` 16777212 KB） |
| `mem_limit` | `mm_stat` f4 = `3983622144`（RAM 25%）真机写成功 |
| writeback 原语 | 在本机（无 `CONFIG_ZRAM_WRITEBACK`）直接调用 `abk_zram_create_backing_dev`：生成稀疏 1 GiB 文件（`ls` 显示 1073741824 B，仅占 ~1 MiB 块）、`losetup -f` 取到 `/dev/block/loop49`、挂载成功、`losetup -d` 卸载干净 |
| 未验证 | 内核锁本身（需要刷入用当前仓库重建的内核）；`backing_dev` 节点的真正写入（需要 `CONFIG_ZRAM_WRITEBACK`） |

### 真机踩到的两个 mksh 32 位陷阱（已修 + 测试锁定）
本机 `/system/bin/sh`（Android mksh）的算术与 `[ -gt ]` 都是 **32 位**：
`15561024 * 1024` = `-1245380608`、`$(( 17179869184 ))` = `0`、`[ 17179869184 -gt 0 ]`
为假；KernelSU 用的是自带 busybox ash（64 位），所以只有从终端/`adb shell`/ROM init
路径进入时才暴露。

1. `mem_limit`：`abk_mem_total_bytes` 与 `* 25 / 100` 在 shell 里算 → 得到负值 →
   内核拒绝写入 → **压缩内存上限静默失效**（真机日志 `write failed: … <- -10697441`）。
   现已改为 `abk_mul_div` / `abk_mem_pct_bytes`（awk 64 位）。
2. `disksize` 保持：`[ "$_sd_live" -gt 0 ]` 对 16 GiB 为假 → 模块把健康设备当成"没有容量"
   而回退到 `MemTotal/2`，**把 16 GiB 砍成 7.97 GiB**（真机实测）。现改为 `abk_gt`
   / `abk_le`（awk）。
   同类问题也在 `tools/cached_freeze_reclaim.sh`（`--quota-mb` 的 `* 1024 * 1024` 与
   每组的 `memory.current` 字节比较）里修掉。

### Batch 11 附带修复（两个既有缺陷）

- [x] `tools/autofdo_515_profile.sh` **在仓库里从未提交过**（README/plan 记为已落地，
  单测因此固定 7 项失败并在该处中断；测试自身还传 `C:\...` 路径给 WSL bash，在
  Windows 上根本无法运行该工具）。本次补齐完整实现：`init`（只接受 5.15 身份的树、
  写 `manifest.env`，记录 kernel 版本 / vmlinux sha256 / .config 指纹 / source revision /
  toolchain / `scripts/Makefile.autofdo` 是否存在）、`record`（adb + simpleperf，落
  `perf.data` 并记设备指纹）、`convert`（create_llvm_prof，可选 simpleperf inject 与
  llvm-profdata）、`validate`（重算 vmlinux sha256，哈希不符即失败）、`build-env`
  （打印 `CONFIG_AUTOFDO_CLANG=y` / `CLANG_AUTOFDO_PROFILE=`）。测试像其它 shell 测试
  一样做 WSL 路径映射；工具对 CRLF 的 Makefile/manifest 也能解析（Windows checkout）。
- [x] `tools/cached_freeze_reclaim.sh` 原本是 `#!/bin/bash` + `set -euo pipefail` +
  bash 数组 + 只认 cgroup v2，在这台把 memory 控制器挂在 **v1** 的设备
  （`/dev/memcg`、`memory.usage_in_bytes`、`freezer.state`）上完全跑不起来。已改为
  POSIX sh 并同时支持两种布局（v2：`memory.current`/`cgroup.freeze`；v1：
  `memory.usage_in_bytes`/`freezer.state`），新增 `--list`（只报不写，用于诊断
  "为什么 cfr 什么都不做"）、`--cgroup-root` 可重复、默认根为
  `/sys/fs/cgroup` + `/dev/memcg`；无组可扫时明确失败而不是静默成功。
  真机实测还发现两点并已修/接：这台 ROM 的 v1 **没有 `uid_*` 组**，用的是命名组
  （`freeze-app` / `game` / `mimd` / `protect_memcg_*`，`memory.reclaim` 由本模块的
  `memcg_v1_reclaim` 移植提供 ✓），因此新增 `--group NAME` 与 `cfr.group` 配置项
  （显式列出，绝不擅自扫厂商组）；`--list` 对"已发现但当前为 0 字节"的组也会打印
  （否则"无输出 + 退出 0"会被误读成"没找到"）。变量名 `GROUPS` 与 bash 内建的
  用户组列表冲突，已改名 `GROUP_NAMES`（默认分支曾被它污染）。
  模块改为把该工具打进 `bin/`（`embed.conf`）并在 `CFR_ONE_SHOT=1` 下调度，
  **删掉了此前模块内重复的 sweep 实现**，CLI 与模块共用一份代码。

### Batch 12 伴生模块 v0.4.0：门控 zsmalloc 压缩整理（真机实验驱动）

- [x] 来源：vermeer（5.15.215-FanZiyun）"极限挤压"真机实验：杀掉全部用户进程后
  `mem_used_total` 352 MB vs `compr_data_size` 186 MB（**89% 是 zsmalloc 碎片**），
  一次全量 `compact` 用时 <1 s、返还 105 MB；整理后开销降到 3.3%。
- [x] `zram-policy.sh` 新增 `abk_zram_compact_if_fragmented()`：双门（浪费字节
  > `zram.compact.min_waste_mb` **且** 开销 > `zram.compact.waste_pct`%）同时满足
  才写 `echo 100 > compact`；判定整体走 awk（操作数可越过 2^31，见上文 mksh
  32 位陷阱）；无 `compact` 节点的内核静默跳过。
- [x] 挂接在监督器 sweep tick 之后（搭重压缩时钟）：`zram.recomp.enable=0`
  同时停掉该门，配置注释与 README 均已写明。
- [x] `tunables.conf` 新键 + `abk_known_keys` 登记：`zram.compact.enable=1`
  （默认开）、`zram.compact.min_waste_mb=50`、`zram.compact.waste_pct=15`。
- [x] 测试：模块夹具新增场景 10（go / healthy-skip / disable / 无节点四路径）
  与监督 tick 断言（碎片态下 compact 节点被写 `100`）。
- [x] 元数据：伴生模块 `module.prop` author 更正为 `FanZiyun`（原为仓库镜像署名），
  versionCode 2→4、v0.2.0→v0.4.0（code review 后直接以 0.4.0 落地）；
  `module.conf` 0.15.0→0.16.0。
- [x] code review 修复：README "唯一默认开启" 旧句更正 + 作业清单补记 compact；
  enable 解析并入仓库 `= "1"` 白名单约定；两个数值键改 `abk_clamp_uint` +
  范围文档（1..1024 / 1..500）+ 非法值告警；判定/日志合并为单次 awk 解析、
  `after` 走 `abk_read`；`tunables.conf` 注释去重（测量叙事以 README/plan 为准）；
  测试锁定 shipped 默认值（`zram.compact.enable=1`/`50`/`15` 静态 pin +
  监督 up 行 `compact=1>50MB+15%` 动态 pin）。
- [x] 真机验证（就地升级安装中模块，v0.3.0）：监督器 up 行出现
  `compact=1>50MB+15%`；健康态（5.7MB 开销 < 50MB 门）门控**零日志零写入**；
  临时压门槛到 1MB/1% 单调用成功写真实 `compact` 节点（su 上下文无 avc 拒绝），
  日志出现 `zsmalloc compaction: used …`，实测额外回收 5.4MB
  （181.0→175.6MB，pages_compacted 43382→44699）。

<a id="batch-11"></a>

## Batch 11(v0.14.0)

真机勘查（vermeer / Redmi K70，`5.15.215-android13-8-g6c35ef5f7a13`，KernelSU
`ksud 3.3.0`，SELinux Enforcing，HyperOS 移植 ROM）：**嫁接已进内核且活着，
问题是没有任何用户态去驱动它**。

| # | 结论 | 证据 |
|---|---|---|
| 1 | 嫁接活着 | `abk_recomp_algo` 存在；`recomp_algorithm=#1: … [lz4hc]`；`recompress_async`/`idle`/`compact`/`mem_limit` 都在；`dynamic_readahead=Y`；`abk_sf_*` 都在；`/dev/memcg/memory.reclaim` 可写、`memory.stat` 有 `cfr_reclaim_*` |
| 2 | 重压缩从未执行 | 无人写 `idle`/`recompress*`；`mm_stat = 4096 62 …`（只存过 1 页），`io_stat` 全 0 |
| 3 | 主算法被 ROM 锁在最差档 | `comp_algorithm` 括号在 `lz4hc`，而 `CONFIG_ZRAM_DEF_COMP="lz4kd"`；启动后再写返回 `EBUSY`，用户态无法修复 |
| 4 | ROM mmd 整链死亡 | `CONFIG_ZRAM_WRITEBACK` 未开 → `init.rc` chown 的 `writeback*` 节点不存在 → `mmd_setup`（Android 16 Rust 实现）在 writeback 步失败 → `mmd.setup_complete` 永不设置、`mmd` 永不 enable |
| 5 | zram 大而不用 | `disksize=16 GiB`、`SwapFree=SwapTotal`、`Used=0`；`pgsteal_anon=0` / `pgsteal_file=2041656`、`pswpout=759`、`Cached≈4.9 GB` |
| 6 | 其他"编进去了但不动" | `transparent_hugepage=[never]`（MADV_COLLAPSE 无载体）、`lru_gen/enabled=0x0000`（MGLRU 有代码未开）、`ZRAM_WRITEBACK` 未开 |
| 7 | 本机多余的项 | cmdline 已带 `rcu_nocbs=0-7` → `CONFIG_RCU_NOCB_CPU_DEFAULT_ALL` 在本机无意义 |
| 8 | 根权限可用、无需 sepolicy | `su -c` 下 `idle`/`compact`/`mem_limit`/`recompress_async`/`swappiness` 写入成功，`dmesg` 无 ksu 域拒绝 |

### 算法实测（64 MiB 定长 ELF 语料，单核，`hot_add` 临时 zram 设备，不动 swap）

| 算法 | 压缩后 | 压缩率 | 压缩 | 解压 | 重复文本 64 MiB |
|---|---|---|---|---|---|
| `lz4` | 31.19 MB | 2.15× | 0.60 s (107 MB/s) | 865 MB/s | — |
| **`lz4kd`（主算法）** | **31.06 MB** | **2.16×** | **0.57 s (112 MB/s)** | **955 MB/s** | **901 KB** |
| `lz4hc` | 29.31 MB | 2.29× | 1.13 s (57 MB/s) | 901 MB/s | 999 KB |
| **`zstd`（二级算法）** | **24.21 MB** | **2.77×** | 0.73 s (88 MB/s) | 330 MB/s | **868 KB** |
| `deflate` | 23.51 MB | 2.85× | 2.29 s (28 MB/s) | 208 MB/s | — |

结论：`lz4hc` 在主/二级两个位置都是劣解（热路径压缩成本翻倍只换 5.6% 压缩率；
重复数据上甚至不如 `lz4kd`）。主算法取 `lz4kd`、二级取 `zstd`，
且**不允许用户修改**：内核参数只读、模块无任何算法/容量配置项。

### Batch 11 落地进度

- [x] 内核侧：`zram_secondary_comp` 默认 `lz4hc` → `zstd`，参数 `0644` → **`0444`**
  （真机实测 0444 连 KernelSU root 写入都是 `Permission denied` → 运行时锁死）；
  同步 `abk_stable_core.py` 组描述、`implementation_audit.py`（含 0444 断言）、
  `smoke.sh`、`stable_5_15_test.py` 夹具、`tools/` 帮助文本、`module.conf`。
- [x] 新资产 `ksu/abk_runtime_tunables/`（KernelSU 模块，非 graft、不写内核树）：
  `common.sh` / `zram-policy.sh` / `post-fs-data.sh` / `service.sh` / `action.sh` /
  `tunables.conf` / `embed.conf` / `README.md`。策略硬编码（主 `lz4kd`、二级 `zstd`、
  `mem_limit`=RAM 25%、容量继承 ROM）；接管序列
  `swapoff → reset → 算法 → disksize → mem_limit → mkswap → swapon -p`，
  带安全门（swap 在用则不改写）、writeback 内核自动让位、`state/` pidfile 与监督循环。
- [x] 打包与注入：`scripts/build_ksu_module.py`（确定性 zip；`embed.conf` 把
  `tools/zram_recompress_trigger.sh` 注入 `bin/`，保持单一实现）、
  `scripts/ak3_bundle_ksu_module.py`（AnyKernel3 树/成品 zip 两种注入 + 幂等围栏 + `verify`）；
  `after_patch` 自动执行，无 AK3 树则告警跳过，`ABK_515_KSU_MODULE=0` 关闭。
- [x] `tools/zram_recompress_trigger.sh`：新增 `--idle-age SECS`（年龄语义）、
  标记与 pass 分离（`--daemon` 不再每轮重标）、`--mark-each-pass` 显式化、
  同算法护栏（退出码 3）、参数校验统一退出码 2。
- [x] 测试：`test_runtime_tunables_module`（打包确定性 / AK3 注入幂等与校验 /
  策略常量与接管顺序 / 夹具行为：接管改写、swap 在用拒绝、writeback 让位、
  监督循环在失败后仍继续、未知键告警、数值钳制）。
- [x] 门禁：`py_compile`、`bash -n`（含 `ksu/*/*.sh`）、单测全绿；
  接管序列已在真机逐条实测通过（含用 `swapon -p 0` 保持 ROM 的 swap 优先级）。
- [x] 真机复验（刷入本模块后）：`action.sh status`；重启后 +60/+120 s 算法未被 ROM 改回；
  swap 正常；`mm_stat.compr_data_size` 在冷页被重压缩后下降；无 `u:r:ksu:s0` 拒绝。
  （复验结论见 Batch 12：接管在某些 boot 会与 ROM 的 `init.kernel.post_boot.sh` 撞车，
  已被 Batch 12 的"无需改写就不改写 + 60 s 复检"取代。）
- [x] ~~可选（需另一次刷机，且与算法强制互斥）~~ → Batch 12 已解决互斥：内核锁使算法
  不再依赖抢占 pre-`disksize` 窗口，`ABK_515_DEFCONFIG_ROM=1` 的
  `CONFIG_ZRAM_WRITEBACK=y` 现在与算法策略并存。
- [x] ~~待验（Batch 10-4 遗留）：`abk_sf` 在 walt governor 下是否长期 boosting~~ → **会，而且必须禁止**。
  已在 Batch 10-5 收口：真机 `ftrace schedwalt/waltgov_next_freq` 抓到 governor 算出 766 MHz
  而簇恒为 1785600，`total_trans` 十余秒不动；`abk_sf_enable` 默认已改 `false` 并加三条
  调频权 gate，`bin/abk_fas_check.sh --probe` 可在 20 秒内判定。
  仍留一项（与本问题无关）：`dmesg | grep recompression` 注册日志因 ring buffer 滚动未取到。

<a id="batch-10-6"></a>

## Batch 10-6(v0.17.1)

### Batch 10-6 落地进度（超大核"不被调用"结案：上限持有者同时在决定放置，v0.17.1）

症状（用户报）：刷机后打开应用时超大核很闲，负载基本落在别的核上。
设备 Redmi K70（vermeer / SM8550），`5.15.215-202609201-FanZiyun`，无线 adb + su。

**结案链（每一步都是真机实测，不是推理）**

| # | 事实 | 证据 |
|---|---|---|
| 1 | ABK 的 floor **在本机是执行的**，本批次一度误判为惰性（已还原并记录） | 三个 policy 的 governor 全是 `walt`，但载荷故意挂在 `android_vh_scheduler_tick` + `android_vh_cpufreq_resolve_freq`（与 governor 无关：见 `scripts/batch10_perf_sched_policy.py` 的模块 docstring 与 `_POLICY_V1` 注释、`late_initcall(abk_sf_init)`）；`/sys/module/cpufreq_schedutil/parameters/abk_sf_*` 存在且 `abk_sf_boosting` **不存在** ⇒ 刷机内核是 v1 载荷，无归属闸门，enable=Y 时会棘轮。真机验证：临时 `echo Y` 后本工具立即 RC=1 并给出修法，`echo N` 复原后 RC=0 |
| 2 | 谁在钉频：Scene 的 profile，不是 metis、不是内核 | `scene-daemon`（`/data/user/0/com.omarea.vtools/files/scene-daemon`）的 fd 里持有 `policy0/3/7/scaling_max_freq`（写）与 `scaling_cur_freq`（读），外加 `/proc/sys/walt/sched_per_task_boost`、`/dev/cpuset/top-app/3-7/tasks`；`/data/user/0/com.omarea.vtools/files/profile.json` 的 `features.limiter` 逐档给出每簇 `{max,min,margins}`：`idle` 档超大核 max=**1843200**、`inactive` 档 **2092800**、`p1..p4` 2476800/2476800/2726400/2995200（满血 3187200，即 58%~94%） |
| 3 | **上限会改写调度器眼中的核大小**（这是症状的直接机制） | `schedwalt/update_cpu_capacity` tracepoint 直读：`cpu=7 arch_capacity=1024 thermal_cap=1024 fmax_capacity=277 max_freq=864000 max_possible_freq=3187200 rq_cpu_capacity_orig=277`，同一时刻 `cpu=3 ... rq_cpu_capacity_orig=503..749`。即 **EAS/WALT 的 capacity 按 `scaling_max_freq/cpuinfo_max_freq` 缩放**，上限被压到 27% 的超大核在放置器眼里比中核还小 |
| 4 | 上限以 ~12.5 Hz 抖动，放置决策读到的是抖动快照 | 4 秒内 `update_cpu_capacity` 137 次、`cpu_frequency_limits` 44 次；超大核 `total_trans` 已达 5.4e5（little 1.3e5、mid 1.0e5），`--sample 10` 实测 trans/s ≈ 64~96 |
| 5 | 冷启动实测：主线程基本不上 prime | 4 个真实应用（网易云/微信/酷安/高德）冷启动，`sched_find_best_target` 的 `most_spare_cap` 直方图里 app 线程几乎全是 3/4/5/6；`start_cpu=7` 的扫描每次都退回非 prime；主线程 50 ms 采样落在 cpu7 的比例 1/40~16/40；启动瞬间超大核上限常为 729600~998400（23%~31%） |
| 6 | **A/B 证明归因** | `kill -STOP scene-daemon` + `echo 3187200 > policy7/scaling_max_freq`：该 policy 在整次启动窗口内 `cpu_frequency_limits` **0 次**（上限不再抖动），`rq_cpu_capacity_orig` 稳定 1024；网易云冷启动主线程在 50ms 采样里落在 prime 的次数 **5/36 → 11/37**，`TotalTime` 2661 ms → **2227 ms（−16%）**，cpu7 占用从"最低"变成与中核持平（91% vs 93%）。随后 `kill -CONT`，Scene 已在数秒内重新接管（下一轮 p7 上限又变回 1708800/864000） |
| 7 | 另有两处 Scene 主动改写、非 ABK | `/proc/sys/walt/sched_upmigrate` 从 `60 95` 变成 `70 70`（实验中途被 Scene 写入，我最后一轮已交还给 Scene）；cpuset：`common_app` = `0-2 / 0-6 / 0-6 / 0-7`、`common_gaming` = `0-2 / 0-5 / 0-5 / 0-7`，实测王者荣耀 236 个线程全在 `top-app/0-5`（物理上碰不到 cpu6/7） |

**本批落地的修法（不越红线：ABK 不抢调频权，只把这件事变成可判定、可回归的观测）**

- [x] `tools/abk_fas_check.sh` 新增**容量翻转判定**：每 policy 计算
      `cap_view = cpu_capacity × scaling_max_freq / cpuinfo_max_freq`（就是
      `rq_cpu_capacity_orig`），快照给出 WARN，`--sample` 统计"最大核不再是最大核"
      的轮询占比，超过 `--invert-pct`（默认 5%）判 **新退出码 4** 并指名去改上限的持有者；
      表格新增 `ceilings=`（上限换了几个点，看的是"爬升"还是"钉死"）与 `cap_view=min-max`。
- [x] 修掉上一轮工具自身的两个缺陷（都在真机暴露）：
      ① 负载门控原来用 `/proc/loadavg`，1 分钟均值滞后会让空转手机被判
      `FROZEN_UNDER_LOAD`（实测误报）；现在用采样窗口内 `/proc/stat` 的**实测 busy 百分比**
      （新增 `--min-busy`，默认 5），loadavg 只作上下文打印；
      ② 轮询太重：`rd()` 原来是 `cat|tr` 两个进程，`policies()`/`cpuinfo_*`/`cpu_capacity`
      每轮重复读 —— 10 秒窗口只跑 3 轮，改成 `head -n 1` + 常量缓存 `CONSTS`/`POLICY_LIST`
      后同样 10 秒跑 16 轮；
      ③ **32 位溢出**：`cpu_capacity × scaling_max_freq` = 855×2803200 = 2.4e9 在 mksh 里
      溢出成负数（实测 `cap_view=-677`）。第一版改成 kHz 先除 1000 再乘，但那会在某些
      点上与内核的整数商（`arch × max / imax`）截断不一致；最终形态两侧都走 awk 精确
      乘除——工具 `cap_view()` 与 companion `abk_mul_div()` 同一公式——真机夹具
      855/949/1188 全对；
      ④ `usage()` 的 `sed -n '2,43p'` 改成 `# ----8<---- end of help` 标记，加选项不再悄悄截断帮助。
- [x] companion `abk_report_dvfs_state()`：每 policy 增记 `arch=`/`cap_view=`，翻转时 WARN；
      并把 `abk_sf_enable` 的判定改成诚实的三分支（无 schedutil policy → 惰性，只 log；
      schedutil policy 被钉 min==max → WARN；否则不提）。**companion v0.5.0 → v0.6.0**。
- [x] `docs/porting_policy.md` 红线 5 扩写：交出调频权要**同时**交出 `scaling_max_freq`，
      因为那个节点同时是容量节点；并记录"放置失败是静默的"这一事实。
- [x] 纠正 `tools/abk_fas_check.sh` 头部、companion README、根 README 里"非 schedutil governor
      会让 floor 棘轮"的错误说法（本表第 1 行）。
- [x] 单测 +10 条断言（RC=4 存在、busy 门控取代 loadavg、32 位安全公式、常量出循环、
      `abk_report_dvfs_state` 的 `cap_view=`/WARN/三分支/glob 路径）；版本 0.17.0 → **0.17.1**。
- [x] 真机 4 分支回归：healthy→RC=0（`cap inversion: 0/5`）、starved→RC=4（5/5）、
      ramp（上限来回翻）→RC=4（4/6=66%）且 `--invert-pct 60` 时降为提示 RC=0、
      live（本机现状）→RC=4（5/16=31%，worst `policy7=548/1024k vs policy3=626/855k`）；
      companion 报告在真机打印 `arch=/cap_view=` 正常。
- [x] 门禁：`bash -n`（含 `tools/`、`ksu/*/*.sh`）、`compileall`、单测全绿；
      本轮未动 registry（`scripts/*.py` 无改动），故沿用上批三档树级结果。
- [x] 待验（与上批同一项）：ABK CI 编译。——已验：本批载荷自 `a48d069` 起指纹不变
      （`4e2a7a19be82aba2`），其后 `a1d38d8` / `8b8bf5d` 两次构建均 success（见 10-6 节
      CI 表），编译结论对 10-5 载荷成立。

**交给用户在 Scene 侧的执行项（本模块不能替它改，改了就是抢调频权）**

1. `profile.json` → `features.limiter` 里第三个条目（= `policy7`）的 `max` 提到 3187200，
   至少保证 `cap_view(超大核) > cap_view(中核)`：即 `max7 > max3 × 1024/855`；
   `idle/inactive` 两档同样处理（现在分别是 1843200/2092800，是启动瞬间最容易撞到的档）。
2. `margins` 是 Scene 自己的爬升限速（`"300 1977600:150"`），启动窗口的第一秒正落在
   它还没爬上去的时候；调小或允许超大核一步到位。
3. 若希望轻负载用上超大核，`common_app` 的 `@cpuset` 第二/三项（0-6）里至少给一个含 7 的组；
   游戏档 `common_gaming`（0-5）是刻意把游戏线程关在超大核之外的，那是它的稳帧策略，按需取舍。
4. `sched_upmigrate` 现在被 Scene 写成 `70 70`（原来是 `60 95`）——这一项本身无害，
   但在上限不放开的前提下它没有生效机会（实测改阈值对放置无影响，见上批实验）。

回归阈值（下次再看"超大核积极不积极"就按这几条量）：`abk_fas_check.sh --sample 20`
必须 RC≠4，且 `cap inversion: 0/N polls`；`policy7` 的 `ceilings` 在启动窗口内 ≤3、
`cap_view` 下界 ≥ 中核 `cap_view` 上界；网易云冷启动 `TotalTime` ≤2300 ms。

### Batch 10-6 审查收口（/code-review 两轴：Standards × Spec，fixed point = `beb3b4f`）

审查方式：`git diff HEAD`（16 文件 / +1397）交给两个互不共享上下文的子代理并行做——
Standards 轴对照 AGENTS.md、docs/group_recipe.md、docs/porting_policy.md 加 Fowler 味道基线；
Spec 轴以本文件的两节 + 用户当次指令为规格，**逐条去代码里验，不信声明**。

**Standards 轴 3 条硬伤（全部已修）**

| # | 问题 | 为什么是硬伤 | 修法 |
|---|---|---|---|
| S1 | 迁移路径只认 v1 一种旧形状 | 带 **Batch 10-2** 载荷（`android_vh_map_util_freq_new` 形状，commit `3cd05e8` 曾上线并编译通过）的树既不匹配当前载荷也不匹配 v1 锚点，却仍匹配 `_TAIL_OLD` ⇒ 走普通插入 ⇒ 同一文件**两份 `abk_sf_*` 定义**，而组状态照样报 `applied`。这正是 group_recipe §2「事务性：部分树不可能构建成功」与 AGENTS.md 陷阱 5 说的那一类：文本门禁全绿，只有编译会炸 | 新增 `has_unknown_policy()`：树里已声明 `static bool abk_sf_enable` 而两种已知形状都不匹配 ⇒ 组返回 `blocked_by_shape` 并**一字不写**；单测用 10-2 形状夹具钉住状态、无写入、且 v1/当前形状不被误伤 |
| S2 | 只读分区门禁的作用域自相矛盾 | 旧正则把 sysfs 的 `/devices/system/` 也当成 `/system` 分区，逼出 module 脚本里 `devices/*/cpu/` 的 glob 变形；而扫描只走 `module_dir.glob("*.sh")`，**漏掉 embed.conf 打进 `bin/` 的工具**——同一模块里两套规矩，规矩还被绕过了 | 规则改准：`/(vendor|odm|product)\b|(?<!/devices)/system\b`，并把同一套断言扩到归档里的 `bin/*.sh`（含 SELinux 那条）；common.sh / action.sh 里为躲旧正则而写的 glob 全部还原成字面路径 |
| S3 | 没有随组扩 `tests/smoke.sh` | group_recipe §3 要求"组落了承重标记就扩 smoke 断言"，而 `abk_sf_dvfs_owned` / `abk_sf_boosting` 一条都没有；smoke 的日志断言只比组状态，而载荷默认是**关**的，所以默认状态下"树里是哪一代载荷"完全没有门禁证据 | smoke 增三条标记断言（闸门函数、`= false` 默认、`module_param_cb` 可观测节点），并按 `sublevel_matrix` 的 debt/pre_applied 规则在该基线不该有标记时跳过 |

**Standards 轴判断类**：采纳 4 条 —— `cap_view` 三处公式不一致（现统一为**精确值**：工具与 companion 都用 awk 乘除，可直接与内核 `rq_cpu_capacity_orig` 对照，截断只剩在采样循环内部的相互比较中）；action.sh 内联 `abk_read \| tr` 改用同批新增的 `abk_read_flat`；提示路径由相对 `bin/...` 改成本文件既有约定 `$MODDIR/bin/...`；`_POLICY = _POLICY_V2` 死别名删除，而 `policy_sha256()` 从"导出却没人调用"改成拼进 graft 报告的 detail（载荷文本自身不能带摘要，带上就改变摘要），报告从此直接回答"这次构建写进去的是哪一代载荷"。
暂缓 2 条并记录理由 —— 载荷注释仍写 "Batch 10-4"（`implementation_audit.py` 把这行当承重钉在用，改名收益低于风险）；big/others 容量比较级联在三处重复（POSIX sh 没有跨文件共享库，强行抽出会造出 `tools/` 依赖 `common.sh` 的假耦合）。

**Spec 轴 5 条**

- **P1（缺失，已补）**：我声称扩写了 `docs/porting_policy.md` 红线 5，实际文件里只有 10-5 的旧文本——**这条当时根本没写进去**。现已写真：`scaling_max_freq` 同时是容量节点，以及"放置失败是静默的，没有任何节点会报告这个核不可选"。
- **P2（本批最严重）**：Batch 10-6 的"更正"本身是错的（见上一节的证据行 1）。它让一份未提交的批次里同时存在两种矛盾叙述：工具/README/plan 说"惰性"，而载荷 docstring、`_POLICY_V1` 注释、10-5 自己的 A/B 说"会执行"。现已全部收敛为一种说法，并补上可操作的判别法（`abk_sf_boosting` 是否存在）。
- **P3（真机缺陷，已修）**：工具采样临时文件写死 `${TMPDIR:-/data/local/tmp}`，在没有该目录的测试机上被 `set -e` 直接打死 ⇒ 改为 TMPDIR → /data/local/tmp → /tmp → `.` 取第一个存在者。
- **P4**：即 S1，两轴各自独立发现同一件事。
- **P5（范围，保留）**：action.sh 的 "dvfs owners" / "fas health" 两行不在任何声明里，属小幅扩围；保留，因为它们是 companion 的展示面且全程只读。

**门禁与真机**

- `compileall` 0、`bash -n` 0、单测全绿（含 S1/S2/S3 的新钉）；`step_audit` / `implementation_audit` / `smoke` 在 **167 / 178 / 194** 三档全绿（本批动了 registry：新增形状守卫与载荷注释，故重跑）。
- 新增一道**设备 shell 语法门**，并已写进 AGENTS.md 的 Verification 节：`tools/*.sh` 与 `ksu/**/*.sh` 必须额外过真机 `sh -n`。理由是本批实测——单引号 awk 程序里一个撇号（`module's`）提前终结了字符串，`bash -n` 放过、本地全绿，设备上第一次进采样循环就 `syntax error: unexpected '('`。构建机专用脚本（`abk_rollback.sh` 等）按 `#!/usr/bin/env bash` 设计排除。
- 真机回归（Redmi K70）：healthy ⇒ RC=0 且 `cap_view=1024` 精确、starved ⇒ RC=4（277 vs 749）、**armed v1 载荷 ⇒ RC=1 并给出修法**、复原 ⇒ RC=0、companion 报告 `arch=` / `cap_view=` 正常。
- 载荷指纹：v1 锚点 `5b341516e8ca1e56` **未变**（逐字节冻结仍然成立），当前载荷 `4e2a7a19be82aba2`。

**ABK CI（本批的编译验，`待验` 项）**：按 run `34517053732` 的**完全相同输入**在 `fanziyun/ABK` 的 `dev` 分支重新派发 `Android 内核构建-自定义`（workflow id 276392595；android13 / 5.15 / X / lts / ksu None / zram+full_algo / ntsync / virt 678 / 同一条 7 段 `custom_external_modules`）。CI 克隆本仓库默认分支，故必须先推送 `main`。
构建结果：**run `34553441530` 一次通过（success）**，job
`103120927194`（`5.15.X-android13-lts / 5.15.X-android13-lts-None`）2026-09-11T02:08:16Z →
02:28:37Z，产物 `None_kernel-android13-5.15-X.zip`（80,649,905 B，artifact 10182112517）。
关键日志（逐条从 job log 取的，不是推断）：

- `[ABK module] version: 0.17.1` ×3 个子模块 ⇒ CI 克隆到的就是本批推送的 `main`；
- `stable_perf_backport/schedutil_smart_policy: applied` ⇒ v2 载荷（指纹
  `4e2a7a19be82aba2`）确实写进了这棵树，并且**编译通过**：AGENTS.md 陷阱 5 那一类
  （`module_param_cb` / `kernel_param_ops` / `strcmp` / `BITS_PER_LONG`）到此关闭；
- `ok: abk_runtime_tunables is bundled and its installer block is present` ⇒ companion
  v0.6.0 随 AnyKernel3 打包成功（本批改过它的 `common.sh` / `action.sh` / `embed.conf`）；
- perf 子模块汇总 `{already_present: 5, applied: 7, blocked_by_shape: 1}`，唯一的
  `blocked_by_shape` 是 `blk_mq_suspend_wakeup_abort`（lts 基线的既有债务，与本批无关）；
- 全日志无 `error:` 编译诊断。

复跑记录（同一条注入串、同一份 `dispatch.json`，只换被构建的提交）：

| 提交 | 改了什么 | run | 结果 |
|---|---|---|---|
| `a48d069` | 本批全部代码 | 34553441530 | **success**（job 103120927194） |
| `a1d38d8` | 更正启动采样计数 + 记录本结果 | 34591231732 | **success**（job 103236756742） |
| `8b8bf5d` | 两处注释/文档里的数字对齐 | 34593035429 | **success**（job 103242443028） |
| `5692b56` | 补跑设备门 + 工具 off 分支（`tools/` 进产物，载荷不动） | 34599579302 | **success**（job 103263329417） |
| `a50df6e` | 真机 status 快照揪出的 awk `%d` 钳位修复，companion **v0.6.2**（`common.sh` + 进包的 `tools/cached_freeze_reclaim.sh`，载荷不动） | 34611016147 | **success** |

第四跑（构建 `5692b56`，日志 `head_log` 行确认模块克隆就停在这个提交）的日志：`[ABK module] version: 0.17.1`、
`stable_perf_backport/schedutil_smart_policy: applied`、
`stable_perf_backport: {already_present: 5, applied: 7, blocked_by_shape: 1}`、
`ok: abk_runtime_tunables is bundled and its installer block is present`（这次随包打进的是
companion **v0.6.1**），过滤掉工作流自身回显之后编译诊断计数为 **0**——全日志唯一含
`error:` 的行是 `continue_on_error: false` 的配置回显。
前四次构建的载荷指纹都是 `4e2a7a19be82aba2`：`git diff a48d069..HEAD -- scripts/` 为空，
载荷字节未变（`5692b56` 改的是进包的 `tools/` 与 companion 版本号，不触载荷），
所以编译结论对 HEAD 成立。第五跑（`a50df6e`，v0.6.2）同样核验了这条 diff 为空，
指纹链延续成立。此后仅 `plan.md`
这类不进产物的文件再变化时，不再需要重跑。

- [x] 设备侧 shell 门（AGENTS.md 新增的 `sh -n`）已在无线调试重连后补跑，对 HEAD
      （含其后注释块改动，及本轮 off 分支新逻辑）：`su -c 'sh -n'` RC=0；容量夹具
      `855×2803200/2803200=855`、`1024×2956800/3187200=949`、溢出位
      `855×2803200/2016000=1188`、非数字输入 →0，四条全对；`--sample 8`（就是当初
      awk 撇号炸掉的那条循环路径）与 `--probe`（4s 加载 + 8s 衰减）均走通、RC=0。
      本机快照：三 policy 全 `walt`、`/proc/fas` uid:0、`abk_sf_enable=N`，无钉死无翻转。
- [x] 补跑时确认并修掉工具的一处口径缺口：`abk_sf_enable` 读出 off 时工具原先完全沉默，
      但那是**运行时状态**——10-5 之前的载荷没有所有权门控，而默认翻 N 之前的构建重启就
      重新武装。现在 off 分支在缺 `abk_sf_boosting` 节点（即 pre-10-5 载荷）时 WARN 并
      `flag 1`，指向 `tunables.conf` 的 `sched.abk_sf_enable=0` 或升级载荷；10-5 载荷上
      保持静默（本机实测：`enable=N` + `boosting` 节点存在 → 无 WARN、RC=0，正是设计意图）。
      单测加钉（`runtime-only` / `no abk_sf_boosting node`）。
- [x] 真机 CI 测试：刷入 `5692b56` 构建（`5.15.216-202609202-FanZiyun`，companion 随包
      装为 v0.6.1），首启 status 快照逐项判读全部符合设计——锁回读 `Y/lz4kd/zstd`、
      三 policy 全 `walt`（10-5 交接成立）、`abk_sf_enable=N`、sweeps 存活、
      `proactive reclaim` 按默认关。唯一异常恰是它的价值：boot 22:15:00 的
      `mem_limit readback is '536870912', wanted 536870911`。`536870911 =
      floor(2147483647×25/100)`——`abk_mul_div` 的 `printf "%d"` 要经过 awk 构建的
      int 转换，这一次（且仅这一次）服务上下文的 awk 把 `MemTotal` 字节
      `15889203200` 钳到了 INT32_MAX；内核又把 512MiB−1 向上取整回 512MiB，
      60 s 重设即自愈（快照与后来实机 `mm_stat f4` 都是真 25%）。属 README 记载的
      32 位陷阱家族，但依赖「awk `%d` 恰好 64 位」这个假设本身就不该留：
      字节输出的格式全部改 `%.0f`（double 对 2^53 以下整数精确，与 int 宽度无关），
      `cached_freeze_reclaim.sh` 的 4 GiB 配额换算（今天就会越 2^31）同改；
      `cap_view` 只打印容量（≤1024）保留 `%d`。设备门重过：`su -c 'sh -n'` 两文件、
      mksh 下 `15516800×1024`、`×25/100`、`4096×1048576=4294967296` 夹具全精确。
      companion **v0.6.2**（`a50df6e`），第五跑（34611016147）成功：载荷 `scripts/`
      与 `module.conf` 相对 `a48d069` 仍零改动（指纹链保持成立），`head_log` 确认
      模块克隆在 `a50df6e`，随包 companion 为 v0.6.2，perf 汇总不变，编译器格式的
      `error:` 诊断 **0**（31 处命中全部是工作流 YAML 自身的 `echo "::error::"`
      模板回显，非诊断）。「随包 companion 为 v0.6.2」的产物级证据链（构建 job
      103301236345 日志）：`head_log` 行即 `a50df6e …(v0.6.2)`，随后
      `[ABK module] injected abk-ksu-modules/abk_runtime_tunables.zip and the
      installer block into .../AnyKernel3/anykernel.sh` + `ok: bundled and its
      installer block is present`，打包行 `adding: abk-ksu-modules/abk_runtime_tunables.zip`，
      产物 `None_kernel-android13-5.15-X`（80,650,903 B）。
- [x] 第五跑真机验收（23:19 开机、23:20 快照）：刷入的正是这条流水线的 AnyKernel3，
      模块日志头第一行报 **v0.6.2**，内核版本串与第四跑构建完全同名
      （`5.15.216-202609202-FanZiyun`）——本身就是「载荷字节未变」主张的旁证。
      修复得到 before/after 闭环：v0.6.1 boot 记
      `mem_limit readback is '536870912', wanted 536870911`；v0.6.2 boot 直接
      `zram mem_limit=3972300800 bytes (25% of RAM)`（= `15889203200` 的精确
      25%），60 s 后 reassert `3983572992` 无 readback 不一致，快照 `mm_stat f4`
      与之一致；`disksize 17179869184` 等字节行在 mksh 下全部正常，无格式化报错。
      其余逐项同第四跑结论：三 policy 全 `walt`（2016000/2592000/864000，
      report-only 行工作正常）、锁回读 `Y/lz4kd/zstd`、`abk_sf_enable=N`、
      `floor_pct=85`、`boosting=0x0`、sweeps 存活、proactive 按默认关、pelt×4。
      本批真机 CI 至此闭环。

顺带一条副产品：这次构建的是 **android13-5.15-lts（SUBLEVEL 已被 ABK 解析为 X）**，
也就是本仓库 `sublevel_matrix.py` 还没有 216 条目的那条线——载荷在它上面 applied
且编译通过，说明"给 lts 补矩阵条目"只是审计覆盖问题，不是正确性问题（仍是独立待办）。

<a id="batch-10-5"></a>

## Batch 10-5(v0.17.0)

### Batch 10-5 落地进度（smart-freq 与 FAS 的调频权收口，v0.17.0）

真机（Redmi K70 / kalama / SM8550，`5.15.215-...-FanZiyun`，无线 adb + su）压测暴露：
**Batch 10-4c 把策略改成"governor 无关"是反向修复**——它让原本休眠的策略在高通
WALT/FAS 设备上真的活了，然后立刻变成缺陷。全部结论来自实测，不是推断。

| # | 事实 | 证据 |
|---|---|---|
| 1 | FAS 的调频方式是**把 `policy->min` 和 `policy->max` 同时写成自己的目标** | `ftrace schedwalt/waltgov_next_freq` 持续输出 `policy_min_freq=policy_max_freq=cached_raw_freq`，且 `abk_sf_enable=0` 时同样成立（所以 `min==max` 是 FAS 的正常签名，不是故障签名） |
| 2 | 于是 ABK 的 floor（`85% × cpuinfo.max` 再被 `policy->max` 夹住）退化成 **"目标 = 上一个目标"** | `waltgov` 按簇需求算出 **766 MHz**（util 187/855≈22%），实际恒为 **1785600**；`stats/total_trans` 4 秒只 +4（同窗 p0 +25、p7 +133） |
| 3 | 空载 A/B 直接闭环（息屏，唯一变量 = `abk_sf_enable`） | `1`：p0 恒 1555200 / p3 恒 1920000（= 当时的簇上限）、`total_trans` 十余秒零跳变 → `0`：4 个采样周期内落到 556800 / 614400 并恢复跳变 → 再 `1`：空载下频率自己爬回 1228800 / 1651200 |
| 4 | 棘轮几乎不可解除的两个放大器 | `boost_start` 只在 `<70% 且未 boosting` 时清零 ⇒ 70–90% 死区持续累积，"持续 300ms"实际退化为"很久以前高过一次"；解除只在 tick 里判定 ⇒ NO_HZ_IDLE 下 CPU 一 idle 就不再有 tick，flag 永久冻结 |
| 5 | 但游戏里那局 1785 **不是** ABK 造成的（本批次据此更正归因） | 出现同一 pin 时 `abk_sf_enable` 已是 `N`；放开 `policy3/scaling_max_freq` 后 mid 立刻铺开 13 个频点。同游戏同 profile 的 FAS 开/关对照：开=占用 67.9% 跑 1253 MHz（45% 上限）/3.97 W/57.2 ℃，关=占用 60.1% 跑 1868 MHz（67% 上限，1785 众数 67%）/5.04 W/67.9 ℃ |
| 6 | `sceneFAS` 的实际开关面 | `/proc/fas`（metis 的 `proc_show_fas`/`proc_write_fas`）：光遇那局恒为 `forground app uid:0`（未注册），王者局为 `uid:10085=com.tencent.tmgp.sgame`。**uid 非 0 才是"已注册"**，且退出游戏不会清空 |
| 7 | userspace 抢不到调频权 | `scaling_governor` / `scaling_min_freq` 权限位被**运行时反复 chmod**（观测到 0444↔0664、属主 root↔system，mtime 就在采样当下），root 直接写会 `EACCES` |

修法（`scripts/batch10_perf_sched_policy.py` 载荷 v2）：

- [x] `abk_sf_enable` **默认 false**：FAS/WALT 设备由厂商 FAS 独占调频；
- [x] 三条归属权 gate：`governor != schedutil` ⇒ 跳过、`policy->min == policy->max` ⇒ 跳过、
      `floor <= policy->min || floor >= policy->max` ⇒ 跳过（最后一条是**不变量**：
      `__resolve_freq()` 本就把 target 夹在 `[min,max]`，所以这两个边界外的 floor
      要么已满足、要么没有余量——"没有余量"正是把下限变成锁的唯一途径）；
- [x] `boost_start` 改真正滑动窗口（低于进入阈值的样本一律重置）；
- [x] 解除改为墙钟 `boost_release`，tick 与 resolve 两侧都判（补上 idle CPU 不再 tick 的空洞）；
- [x] 新增只读 `abk_sf_boosting`（`module_param_cb`，0444）：reason 位图。此前只能从
      频率反推状态，是这次排障绕了一圈的真正原因；
- [x] **旧树原地升级**：保留 `_POLICY_V1` 原文作锚点 + `build_upgrade_steps()`。
      单锚 `_TAIL_OLD` 在已 graft 的树上仍然命中（它就是载荷被追加的那行），
      走普通路径会在旧载荷**前面再插一份** → 重复定义、编译失败。
      单测 `the upgrade branch is load-bearing (plain insert duplicates)` 把这点钉住；
- [x] companion v0.5.0：`tunables.conf` 显式 `sched.abk_sf_enable=0`；
      `abk_report_dvfs_state()` 开机记录每簇 `gov/cur/min/max/total_trans` +
      `/proc/fas` + `abk_sf_enable/boosting`，并在 `enable=Y` 遇到非 schedutil governor 时 WARN；
- [x] 新工具 `tools/abk_fas_check.sh`（随模块打包为 `bin/abk_fas_check.sh`，只读不写）：
      `--sample N` 用"频点数/众数占比/跳变速率 + `/proc/loadavg` 负载门控"区分
      `MOVING / IDLE / PARKED_IDLE / FROZEN_UNDER_LOAD`，`--probe` 主动加负载再验回落
      （这是唯一能区分"park"与"lock"的手段）。已在真机两分支验证：空载 RC=0、
      `abk_sf_enable=Y` 撞 walt 时 RC=1 并给出修法。（Batch 10-6 一度把这条判据连同归因一起改掉，理由是"governor 是 walt 所以 floor 不执行"——**那个理由是错的**：10-4c 起载荷挂在 `android_vh_scheduler_tick` 与 `android_vh_cpufreq_resolve_freq` 上，两者与 governor 无关；现已还原，并加了一件事：按只读节点 `abk_sf_boosting` 是否存在区分两代载荷，v1 无闸门⇒FAIL，10-5 有闸门⇒降级为提示。）
- [x] 审计同步：`implementation_audit.py` 加 required/absent/function 三档钉（含
      `if (floor > policy->max)` 与 `static bool abk_sf_enable = true;` 必须消失）；
      单测新增 20 条断言（含 `module_param()` 与变量声明同名的扫描）。
- [x] 门禁：`compileall`、`bash -n`（含 `tools/`、`ksu/*/*.sh`）、单测全绿；
      `step_audit` / `implementation_audit` / `smoke` 在 **167 / 178 / 194 三档全绿**
      （`schedutil_smart_policy` 三档均 `applied`，二次 `already_present` 且字节一致；
      167 core 147 步、178 148 步、194 139 步）。
- [x] 顺带修 `tests/fetch_sublevel_tree.sh`（审计基础设施缺陷，本轮实测踩到）：它无条件
      `curl | base64 -d > "$dst"`，所以 gitiles 限流返回的短错误体会**覆盖已经下好的文件**
      ——"re-run to fill the gaps"实际上会制造新的空洞（实测 194 树里 `init/main.c`
      变 6 字节乱码、`kernel/fork.c` 变 0 字节，第二次重试又把 5 个好文件打成 6 字节，
      含本组要读的 `kernel/sched/cpufreq_schedutil.c`，表现为 `step_audit` 抛
      `UnicodeDecodeError`）。现在改为：先 `acceptable()` 判"是否已是源码"
      （≥200 B、无 NUL、开头不是 HTML），已合格的文件跳过，下载写进 `*.part`
      且只有合格才 `mv` 到位。完整树重跑从"重下 53 个文件并可能损坏"变成 1 秒 no-op。
- [x] 待验：ABK CI 编译（本批改的是 C，文本审计看不见 `module_param_cb`/`strcmp` 这类
      编译期问题，AGENTS.md 陷阱 5 的同类风险）。——已验：`a48d069` 起四次构建全绿
      （run 34553441530 / 34591231732 / 34593035429 / 34599579302，见 10-6 节 CI 表），
      载荷指纹不变。

遗留（超出本模块边界，记录以免重蹈）：FAS 未注册时 mid 簇停在 1785600 这一
"高位 park"是否由 Scene 的 profile 写死，需要在 Scene 侧核对每簇上限配置；
内核侧已经做到的是"ABK 绝不参与抢调频"，以及把这件事变成一条命令可查。
——**本问题已在 Batch 10-6 结案**（就是 Scene 的 limiter 写的）。

<a id="batch-10-4"></a>

## Batch 10-4(v0.13.0)

### Batch 10-4 落地进度（让已落地特性在真机上真正生效）

真机核查（vermeer，`5.15.215-android13-8-g6c35ef5f7a13`，即 run 34473748528
的构建）暴露出三个「编进去了但当前不生效」的机制性问题。三处都已修，
并补上触发工具。**注意这与编译正确性无关**：三处都能编过、三档锚点审计
全绿，问题在运行时触发条件。

- [x] **zram 重压缩空转（Batch 4 + 10-1，影响最大）**：
  `ZRAM_MULTI_COMP` 只提供机制。`recomp_algorithm` 为空时
  `zram->comps[1..3]` 全为 NULL，`zram_recompress()` 的
  `if (!zram->comps[prio]) continue;` 把每个算法都跳过 → `zstrm` 保持
  NULL → 直接 `return 0`。而 `recomp_algorithm_store()` 在设备已初始化后
  返回 `-EBUSY`，Android 开机早期就写了 `disksize`，用户态永远来不及。
  真机实测：`recomp_algorithm=[]`；写 `algo=zstd priority=1` → `rc=1`；
  dmesg `zram: Can't change algorithm for initialized device`；
  `[zram_recompd]` 线程确实被创建（说明派发链路通），但没有一页被重压缩。
  **修法**：新组 `zram_secondary_comp`（core）在 `zram_add()` 里、任何
  `disksize` 写入之前注册第二压缩算法；模块参数 `zram.abk_recomp_algo`
  （默认 `lz4hc`，置空关闭，可用内核 cmdline 覆盖）。
  选 `lz4hc` 的理由：比 lz4 / vendor lz4kd 压得更好，但解码与 lz4 同速，
  重压缩后的页不会拖慢缺页读；要更高压缩率可设 `zstd`（解码更慢）。
  `zram_destroy_comps()` 只清 `comps[]`、不清 `comp_algs[]`，所以 `reset`
  之后依然有效。
- [x] **schedutil 策略休眠（Batch 10-2）**：采样原挂在
  `android_vh_map_util_freq_new`——那是 schedutil 专用路径
  （`get_next_freq()`），而真机 governor 是高通 `walt`，钩子永不触发 →
  没有任何 CPU 进入 boosting → resolve 侧地板永不生效（参数可见但恒不动作）。
  **修法**：改到 `android_vh_scheduler_tick` 采样（对所有 governor 都触发）。
  关键前提已在源码核实：`scheduler_tick()` 里该钩子在 `rq_unlock()` **之后**
  调用（core.c 5437 解锁 → 5449 触发），所以 tick 上下文里无锁读 PELT 安全。
  状态按 CPU 记录，resolve 时按 `policy->cpus` 聚合。
- [x] **cgroup v1 缺口（Batch 5 + 10-3）**：真机 memory 控制器挂在 **v1**
  （`/dev/memcg`，`/sys/fs/cgroup/cgroup.controllers` 为空），而
  `memory.reclaim`（Batch 5）与 `cfr_reclaim_*`（Batch 10-3）都只挂在 v2
  路径（`memory_files[]` / `memory_stat_format()`）上。后果：
  `tools/cached_freeze_reclaim.sh` 在这台设备上完全跑不起来，
  计数器也读不到（厂商自己在 v1 上有 `memory.reclaim_once`）。
  **修法**：新组 `memcg_v1_reclaim`（core）—— 在
  `mem_cgroup_legacy_files[]` 前前置声明并复用 Batch 5 的
  `memory_reclaim()`（复用而非复制；它设置 `MEMCG_RECLAIM_PROACTIVE`，
  因此 v1 路径**自动**计入 Batch 10-3 的计数器），并在
  `memcg_stat_show()` 输出 `cfr_reclaim_*`（v1 的 stat 渲染器与 v2 不同）。
- [x] **触发工具 `tools/zram_recompress_trigger.sh`**：`--mark-idle` 把当前
  存活页标为冷（`idle=all`），之后周期性 pass 只重压缩期间没被读过的页
  （读到即清标志，正是 ZRAM_IDLE 语义），默认走 `recompress_async`。
  **第二压缩算法未注册时直接报错退出**——正是这次踩到的静默 no-op，
  工具的价值就在把它变成显式失败。`--sys-root` 供测试注入。
- [x] 验证：py_compile + `bash -n` 通过；单测新增 4 组夹具全绿
  （`zram_secondary_comp` / `memcg_v1_reclaim` / 改造后的 sched policy /
  trigger 脚本，含"未注册时必须失败"用例）；step_audit /
  implementation_audit / smoke 在 167/178/194 三档全绿
  （core 144/145/136 步，二次幂等；smoke core 21 组全 applied）。
- [x] 编译验证：ABK CI（run 34482384766，android13/5.15-X/lts）after_patch +
  编译内核 + Boot/AnyKernel3/签名 Bundle 全绿；已按惯例 bump `module.conf`
  至 v0.13.0，Batch 10-4 正式落地。
- [x] 真机复验（Batch 11 期间部分完成）：`/sys/module/zram/parameters/abk_recomp_algo`
  存在（Batch 11 起为**只读 `zstd`**）；`/sys/block/zram0/recomp_algorithm` priority 1
  = `[zstd]`（Batch 11 的模块接管后）；`/dev/memcg/memory.reclaim` 可写、
  `/dev/memcg/memory.stat` 的 `cfr_reclaim_*` 可读；`tools/zram_recompress_trigger.sh
  --status` 报 armed。
- [ ] 待验（Batch 11 遗留）：`dmesg | grep recompression` 注册日志（ring buffer 已滚动）、
  `abk_sf` 在 walt governor 下是否进入 boosting（`sched_pelt_multiplier=4` 有长期
  boosting 风险，见 Batch 11）。

<a id="batch-10-3"></a>

## Batch 10-3(v0.12.0)

### Batch 10-3 语义定义（cached_freeze_reclaim，通用名替代 MFZ）

MFZ 更名为通用名 `cached_freeze_reclaim`（组名），C 前缀 `abk_cfr_`。
范围：内核记账 + 守护进程，两件。**语义按"当前内核可无 KABI 改动实现"界定**，
不做超出实现能力的承诺（写明这一点是为了让 spec 与 registry 一致）。

内核件（观测，不做自动策略，不碰 KABI 导出结构）：

- 主动回收记账：在 `mm/vmscan.c` 的 `try_to_free_mem_cgroup_pages()` 中，
  **仅当 `MEMCG_RECLAIM_PROACTIVE`（即 memory.reclaim 路径）**时累加
  `abk_cfr_reclaim_{attempts,requested,reclaimed}` 三个进程级计数器，经
  `mm/memcontrol.c` 的 `memory_stat_format()` 输出到 `memory.stat` 文本。
  刻意不做 per-memcg 字段：`struct mem_cgroup` 属厂商可达结构，加字段有
  KMI 风险；计数器只做全局观测，per-UID 归属由守护进程侧按目录读取区分。
- freezer 观测：**不新增 tracepoint**。`kernel/cgroup/freezer.c` 的
  `cgroup_update_frozen()` 两个 `CGRP_FROZEN` 迁移点本就发上游
  `TRACE_CGROUP_PATH(notify_frozen, cgrp, frozen)`，Perfetto Freezer 轨迹与
  `am freeze/unfreeze` 验证消费的就是它。曾试过并列加
  `abk_cfr_freeze`/`abk_cfr_thaw` 私有事件，已回滚：
  (a) 与既有事件在同一迁移点重复；(b) 在 freezer.c 里写 `TRACE_EVENT`
  链接不到符号 —— 该文件 include `<trace/events/cgroup.h>` 但没有
  `CREATE_TRACE_POINTS`，`__tracepoint_/__traceiter_abk_cfr_*` 只有声明没有
  定义，ABK CI 在 `ld.lld` 报
  `undefined symbol: __tracepoint_abk_cfr_freeze`（run 34471008778）。

守护进程件（`tools/cached_freeze_reclaim.sh`，分发件，非树内 graft）：

- 默认只回收：按 cached UID 目录把 `memory.current` 全额写入
  `memory.reclaim`（= AOSP `CachedAppOptimizer` 的最大回收语义）；
  `--quota-mb N` 可选封顶（`配额`即此上限，默认 0 = 不封顶）。
- `--freeze` 可选：先 `cgroup.freeze=1` 静默该组，回收，再 `cgroup.freeze=0`
  解冻 —— 冻结是可选项，且总在同一轮内解除，不会把应用长期挂起。
  生命周期解冻（intent/job/activity 恢复）仍归平台 ActivityManager，
  脚本不重新实现它。
- 组不存在 `memory.reclaim`（未启用 memcg v2 接口）时跳过。

可观测标准（真机验证）：`memory.stat` 里 `cfr_reclaim_*` 增长、
`dumpsys activity` 冻结合集、`am freeze/compact` 手动复现、
Perfetto Freezer 轨迹有 Freeze/Unfreeze 切片。

### Batch 10-3 落地进度（cached_freeze_reclaim，已落地 + 三档审计全绿）

- [x] 实现：`cached_freeze_reclaim`（stable_backport_core，步骤脚本
  `scripts/batch10_core_cached_freeze_reclaim.py`，接线见
  `abk_stable_core.py` 尾部）；集团计数 core 18→19
  （`tests/sublevel_matrix.py` `GROUP_COUNTS`）。
- [x] 内核件：`mm/vmscan.c` 的 `try_to_free_mem_cgroup_pages()` 仅在
  `MEMCG_RECLAIM_PROACTIVE`（即 memory.reclaim 路径）时累加
  `abk_cfr_reclaim_{attempts,requested,reclaimed}`，经
  `mm/memcontrol.c` 的 `memory_stat_format()` 输出到 `memory.stat`；
  freezer 侧复用上游 `notify_frozen` 事件（不新增 tracepoint，理由见上）。
  **全部锚点均为 pristine 文本**，刻意避开 `memcg_memory_reclaim` 组安装的
  替换块 —— 否则会把该组二次幂等打断（同 zram 重压缩组的坑）。
  唯一跨组依赖是 `MEMCG_RECLAIM_PROACTIVE`（由 memcg 组定义，本组注册在其后）；
  若 memcg 组降级，本组的计数条件宏缺失会编译失败而非静默半打补丁。
- [x] 编译验证：第一轮 ABK CI（run 34471008778）在 `编译内核` 挂掉，实锤私有
  tracepoint 的 undefined symbol 问题；已按上述回滚 freezer.c 步骤并复跑全部
  本地门禁。第二轮 CI 见下方"编译验证"。
- [x] 守护进程：`tools/cached_freeze_reclaim.sh`（默认全额回收、
  `--quota-mb` 封顶、`--freeze` 可选静默后解除、`--dry-run` 演练、
  `CFR_ONE_SHOT=1` 单步可测）。
- [x] 验证：py_compile + 单测全绿（新增 cfr 夹具 + 守护进程夹具）；
  step_audit / implementation_audit / smoke 在 167/178/194 三档全绿
  （core 139/140/131 步，二次幂等；smoke core 19 组全 applied）。
- [x] 编译验证：第二轮 ABK CI（run 34473748528）after_patch + 编译内核 +
  Boot/AnyKernel3/签名 Bundle 全绿；已按惯例 bump `module.conf` 至 v0.12.0，
  本组正式落地。

<a id="batch-10-2"></a>

## Batch 10-2(v0.12.0)

### Batch 10-2 设计草案（schedutil smart_freq-PELT 策略层，未立项）

- 范围认定（据 walt_pelt_survey.md）：只保留 reason 选举 + 去激活滞回 +
  freq 上限钳制 + per-cluster 阈值表的"控制层子集"；pipeline 钉核/IPC-FMAX
  (AMU)/per-ms LRPB 闩锁不迁移。
- GKI 挂点：`android_vh_cpufreq_resolve_freq`（smart_freq 上限钳制最佳位，
  覆盖 fast/slow 两路）+ `android_vh_map_util_freq(_new)`（util→freq 改写）
  + `android_vh_scheduler_tick`/`android_rvh_tick_entry`（自维护 16ms 环形
  busy 记账窗口，替代 WALT per-ms bitmap）。per-cluster 状态放自有全局数
  组，不碰 KABI。
- 产出形态：GKI 内置策略模块（新文件 + Kconfig，仿 dynamic_readahead 的
  built-in 注册式），**明确标注为"受 smart_freq 启发的自行设计"**，非逐位
  复刻。待 Batch 10-1 落地后立项。

### Batch 10-2 落地进度（schedutil_smart_policy，已落地 + ABK CI 编译通过）

- [x] 实现：`schedutil_smart_policy`（stable_perf_backport，步骤脚本
  `scripts/batch10_perf_sched_policy.py`，接线见 `abk_stable_perf.py` 尾部）；
  集团计数 core 18（含 10-1）、perf 12→13（`tests/sublevel_matrix.py`
  `GROUP_COUNTS`）。
- [x] 验证：与 Batch 10-1 同轮审计 + 同轮 ABK CI（run 34418417022）全绿。

<a id="batch-10-1"></a>

## Batch 10-1(v0.12.0)

### Batch 10-1 设计草案（zram_async_recompress，方案 A，未注册）

- 语义：`recompress_store` 保持现扫描逻辑，但命中候选页后不再同步执行
  `zram_recompress()`，而是按现参（threshold/prio/prio_max）把
  {index, 参数} 提交到 per-device 作业队列，由专用 kthread 逐项执行；
  调用侧不再等待压缩完成（sysfs 返回"已排程"）。
- 复用 QPACE 骨架的通用件：作业队列 + kref 完成计数 + 溢出 list + 背压
  唤醒（去 QTI）；不引入 crypto_acomp —— 方案 A 的"异步"由自有 kthread
  承担（软件 scomp 的 acomp 本质同步，无收益还添 workqueue 上下文切换）。
- 5.15 锚点（全部为 `zram_recompression` 组已落地文本，跨 167/178/194
  同形）：`struct zram` 尾部（comps[] 之后）追加 per-device worker 字段；
  `recompress_store()` 的参数解析段与主循环体；`zram_reset_device()` 内
  comps 销毁前 flush 作业；`zram_add()`/destroy 建/销 worker。
- 安全要点：作业持自身 scratch page（不复用 store 的共享 page）；reset 与
  recompress 的互斥仍靠 init_lock（store 持 down_read 排程 → 返回前只排不
  等；kthread 执行时再取 down_read？—— 需按"reset down_write 先 flush 已
  排作业再销毁 comps"顺序锁死）；KMI 零改动（struct zram 非 ABI 面）。
- 开关：`type=…` 语法外新加 `async=1` 参（默认同步，行为零回归），或另立
  sysfs 属性；Kconfig 沿用 ABK 引擎行追加（参考 dynamic_readahead 惯例）。
- 验证：step/implementation/smoke 三审（重压缩组锚点已被多档断言覆盖，
  新组继承）+ ABK CI 编译门；CI 绿后 bump version 落 plan。

### Batch 10-1 落地进度（zram_async_recompress，已落地 + ABK CI 编译通过）

- [x] 实现：`zram_async_recompress`（stable_backport_core，步骤脚本
  `scripts/batch10_core_zram_async.py`，接线见 `abk_stable_core.py` 尾部）。
- [x] 验证：py_compile + 单测全绿；step_audit / implementation_audit /
  smoke 在 167/178/194 三档全绿；ABK CI（run 34418417022，
  android13/5.15-X/lts，实际 5.15.215）after_patch + 编译内核全绿。

<a id="batch-9-1"></a>

## Batch 9-1(v0.11.0)

### Batch 9-1 落地进度（dynamic_readahead_lowmem，已落地 + ABK CI 编译通过）

- [x] 来源实锤：`MiCode/Xiaomi_Kernel_OpenSource` 的
  `xiaomi-modules/mi_dynamic_readahead/dynamic_readahead.c`
  （GitLab 镜像 `MiCode-mirror/xiaomi_kernel_opensource`，`dijun-v-oss` 等
  分支），版权 © 2020-2022 Oplus。模块只注册两个 android vendor hook；
  android13-5.15 的 `include/trace/hooks/mm.h` + `mm/readahead.c`
  `ondemand_readahead()` + `mm/filemap.c` readaround 调用点均已自带
  （167/194 快照逐字节核实）→ 无需补 hook 声明/调用点。
- [x] 新组 `dynamic_readahead_lowmem`（stable_backport_core，mm/readahead.c
  + mm/Kconfig）：core_initcall 注册 `android_vh_ra_tuning_max_page` /
  `android_vh_tune_mmap_readaround` 回调，低内存 + cpuset "background"
  任务时预读上限减半、mmap readaround 收缩；剥离 xring 依赖，保留
  `readahead.dynamic_readahead=0` 运行时开关与 `CONFIG_ABK_DYNAMIC_READAHEAD`
  （defconfig lane 使能，进 `_MODULE_CONFIGS`；水位和每秒刷新跟踪
  min_free_kbytes/热插拔变化）。
- [x] 验证：py_compile + 单测全绿（新增 batch9 夹具：applied/标记/幂等）；
  step_audit OK（167 132 步 / 178 133 步 / 194 124 步 core，二次幂等）；
  implementation_audit OK（REQUIRED_CONTENT 新增断言，无 xring 残留）；
  smoke OK 三档（167/178/194：pass1 applied、pass2 already_present、
  回滚通过，核心计数 core 16→17）。
- [x] 编译验证：ABK CI（`fanziyun/ABK` run
  <https://github.com/fanziyun/ABK/actions/runs/34407686264>，
  android13/5.15-X/lts，实际 5.15.215）编译内核 + Boot 打包全绿；
  已按惯例 bump `module.conf` 至 v0.11.0，本组正式落地。

<a id="batch-8"></a>

## Batch 8(v0.10.1)

本批次从 android15-6.6 / android16-6.12 筛出的长期项目中，先落地当前模块边界
内、证据最强的 `mm/page_alloc` fallback 优化和 RCU NOCB opt-in；完整 MGLRU、large folio/mTHP、
Maple Tree + per-VMA locks 需要独立 MM/VFS rebase；F2FS/UFS/EROFS 项目留在
sibling suite。AutoFDO 仍作为独立构建工程先建立整机基线。

v0.10.1 修复：`pagealloc_fallback_reuse` 移植的 `find_suitable_fallback()`
在 5.15 基线（含 android13-5.15-lts 5.15.211）编译失败 —— 上游 6.x 使用
`MIGRATE_FALLBACKS`，5.15 无此枚举，改为按 5.15 本族的 `MIGRATE_TYPES` 哨兵
终止 fallback 遍历（`fallbacks[migratetype][i] != MIGRATE_TYPES`），保持组
`applied` 且幂等/回滚审计通过。

v0.10.2 修复：`zsmalloc_chain_size` 只搬了 sizing，漏搬了 6.2 重做里与之配套的
`ISOLATED_BITS` 3→5。`struct zspage.isolated` 是 3-bit 位域（max 7），而
`ZS_MAX_PAGES_PER_ZSPAGE` 改为 `CONFIG_ZSMALLOC_CHAIN_SIZE`（默认 8）后，8 页
zspage 的第 8 个子页隔离会让 `isolated` 7→0 回绕、`is_zspage_isolated()` 误判，
内存压缩迁移时 `putback_zspage()` 对已在链表头的 zspage 二次 `list_add` →
`kernel BUG at lib/list_debug.c:35` panic（红米 K70/vermeer 5.15.211 实测，
运行 ~4.7h 后 kcompactd 触发）。补第 5 步 `#define ISOLATED_BITS 3`→`5`
（与 android15-6.6 对齐），`implementation_audit` 的 `REQUIRED_CONTENT`
增加 `#define ISOLATED_BITS\t5` 断言。

详细来源、收益证据、依赖、验证门槛和排除项见
[`docs/batch8_long_term.md`](docs/batch8_long_term.md)。

- [x] `pagealloc_fallback_reuse`（P1，当前模块）— `rmqueue_bulk()` fallback
  mode 复用 + `find_suitable_fallback()` 清理；Android backport 测试报告
  `vm-scalability` 吞吐约 +31.6%、最坏 zone-lock 约 280ms→8ms；按 5.15
  AOSP vendor-hook 形状适配，覆盖 `mm/page_alloc.c`、`mm/compaction.c`、
  `mm/internal.h`，并完成矩阵、幂等、回滚审计
- [x] `rcu_nocb_cpu_default_all`（P1，源码已落地，默认不启用）— 增加
  `CONFIG_RCU_NOCB_CPU_DEFAULT_ALL` 和 5.15 形状的 all-CPU mask 初始化；
  `rcu_nocbs=` / `nohz_full=` 显式参数优先。目标产品是否在 defconfig 开启，仍需
  idle power、callback backlog、wakeup latency 和前后台切换 benchmark
- [x] `autofdo_515_profile`（P1，构建工程）— 针对精确 5.15/toolchain/device
  重新采集，不作为源码 graft；`tools/autofdo_515_profile.sh` 已落地（init→record→
  convert→validate→build-env，5.15 身份 + vmlinux hash 双重硬门，不注册 PatchGroup、
  不改内核树），真机采集与 A/B 基准仍属设备工程
- [~] `mglru_612_refresh`（P2，独立 MM 分支）— 先验证 deactivation，再评估完整
  aging/workingset/refault/type-selection 系列
- [~] `large_folio_mthp_substrate`（P2，独立 MM/VFS 分支）— 完成 page cache、
  readahead、THP、rmap/migration 和 mapping order 基础设施后再接文件系统
- [~] `maple_tree_per_vma_lock`（P3，独立 MM 分支）— 高收益但涉及 VMA 生命周期、
  fault path 和 KABI，当前模块不做 bounded graft
- [~] `f2fs_readonly_large_folio` / `erofs_large_folio_zstd`（P2/P3，文件系统
  sibling suite，按镜像格式和 CPU 预算条件启用）

<a id="batch-7"></a>

## Batch 7(v0.8.0)

上游 5.15.185 合入 `drm: Add valid clones check`（Concurrent Writeback 系列，
`drivers/gpu/drm/drm_atomic_helper.c` 的 `drm_atomic_check_valid_clones()`）：
校验 CRTC `encoder_mask` 里每个 encoder 的 `possible_clones` 必须覆盖整个
mask，否则 `-EINVAL`。vendor `msm_drm`（按 5.15.178 之前 KMI 编译）的双 pipe
分屏 + CWB 拓扑不满足该校验 → 5.15.185+（2025-07/09/12 月度分支）与 lts
分支上 `drmModeAtomicCommit` 全部返回 -22，SDM 死循环提交失败，屏幕黑但
触摸/指纹/系统正常（Xiaomi 13 fuxi / 14 vermeer 实测）。

- [x] 新 child `stable_display_fix`，单组 `drm_valid_clones_revert`：两步
  required 删除（函数定义 + `drm_atomic_helper_check_modeset()` 内调用点），
  老块携带前后文使新块非空且唯一（纯空替换会撞 `replace_once` 的幂等前置
  检查永不生效；另一处 `drm_atomic_add_affected_planes()` 调用在
  `drm_atomic_helper_disable_all()` 内，锚定后续注释隔离）
- [x] 基线语义：167/.178 从未带该校验 → 组报 `already_present` 零写入
  （目标形态即 pre-185 上游形态）；194/lts 真正 `applied`；未知形态
  `blocked_by_shape` 不半打补丁
- [x] 逐分支核实 2025-07/09/12 与 lts 的函数/调用点字节一致（md5 相同），
  2025-05 确认 pre-185 形态与替换文本逐字节吻合
- [x] 矩阵/测试：GROUP_COUNTS +1，PRE_APPLIED 记录 167/.178 的
  `drm_valid_clones_revert`，`stable_5_15_test.py` 新增三夹具单测（185+ 形态
  applied/二次幂等、pre-185 already_present 零写入、未知形态降级零写入），
  "167 all-applied" 断言对 revert 子模块豁免并注明原因；
  step_audit/implementation_audit/smoke/fetch 全链路挂上新文件与新 child，
  `REQUIRED_ABSENT` 断言检查符号零残留
- [x] 注入方式：`set:...ABK_5.15_backport.git#stable_display_fix;after_patch`
  可单独注入；README/module.conf 版本与描述同步

<a id="batch-6.1"></a>

## Batch 6.1(v0.7.1)

CI run 33582771814 在「编译内核」步骤挂在 `mm/khugepaged.c` 4 个错误上
（`use of undeclared identifier 'res'`、`too many arguments to function call,
expected 4, have 5`）。组状态是 `applied`、10 步里 9 applied + 1
already_present，所以三档 step_audit / smoke 全绿也没拦住。

- [x] 根因：`_MC_FILE_STUB_NEW` 由 `_MC_FILE_SIG_NEW` 字符串拼出，桩函数先写
  之后文件里已字面包含下一步的替换文本，`replace_once` 的幂等前置检查
  （`abk_common.py:80-82`）直接返回 `already_present` → `CONFIG_SHMEM=y` 下真正
  生效的 `khugepaged_scan_file()` 定义留在 4 参数，函数体的 `*res = result`
  与两个调用点却已按 5 参数落地。**调换步序无效**（`replace_once` 无论如何都
  先看替换文本）；改成两种不同折行的同一 C 签名，文本不再可能撞车
- [x] 顺带修一个能编译但逻辑错的移植：5.15 的 `hugepage_vma_revalidate()`
  成功返回 0，6.1 返回 `SCAN_SUCCEED`（本枚举 = 1）。移植进来的
  `if (result != SCAN_SUCCEED)` 让每次释放 `mmap_lock` 后的重校验即使成功也
  退出 → 跨 PMD 区间的 `MADV_COLLAPSE` 几乎必然返回 `-EINVAL`。改为与同文件
  其它调用者一致的 `if (result)`；隔离编译 A/B 实测：旧式 3-PMD 返回 -22，
  新式返回 0
- [x] CI 拦截能力补齐（这次是编译当第一道真检查，不能再有第二次）：
  - `step_audit.py` 新增 trap-1b：消费 `apply_steps` 的逐步返回值，必须真正
    应用的组里任何一步报 `already_present` 直接失败（trap-1 只看原始文件，
    看不见同组前一步现场造出来的冲突）；`record_steps()` 同步支持
  - `stable_5_15_test.py` +11 项：`_MC_*_NEW` 两两不得互相包含、双定义夹具
    端到端两步都必须 `applied`、revalidate 约定断言
  - `implementation_audit.py` 的 `REQUIRED_CONTENT` 增加两个 scan_file 签名与
    revalidate 的 5.15 约定串（唯一能抓「编译过但按源内核语义跑」的机制）
  - `smoke.sh` 断言 4 参数定义零残留、`khugepaged_scan_file` 定义恰好 2 处
- [x] `.211` 矩阵刷新：lts 是滚动分支，已自带 5.15.202 的三个 RT hunk 与
  5.15.212 的 dst-group 统计修正，`sched_rt_optimizations` /
  `sched_dst_group_allowed_stats` 从「漂移债务」改记为 `PRE_APPLIED`
- [x] 验证：四档 step_audit 全绿（core 108/109/100/100，perf 81/81/80/80）、
  四档 implementation_audit OK、167/.178/.194 三档 smoke 双跑幂等 + 回滚、
  单测全绿；三处回归注入实测能被拦下

<a id="batch-6"></a>

## Batch 6(v0.7.0)

先把审查发现的三处"承诺没兑现"收口，再落三个新组；core 从 11 组到 14 组。
逐步审计步数：core 108/109/100/100（167/.178/.194/.211），perf 81/81/80/80；
四档 step_audit 全绿，167/.178/.194 三档 smoke 双跑幂等 + defconfig 回滚通过。

审查收口：

- [x] `--defconfig` 真正启用：`enable_configs()` 支持三种形态（已是目标值 /
  `# CONFIG_x is not set` 或异值改写 / 缺失追加），只写 `KERNEL_ROOT` 内的
  defconfig，越界 `report_only`，`.abk-orig` 快照可回滚
- [x] family 真门控：非 android13-5.15 时全部 `report_only` 且零读零写；
  `ABK_515_ALLOW_UNSUPPORTED=1` / `--allow-unsupported` 显式放行
- [x] 静默 no-op 双方向堵塞：`apply_steps()` 空 steps/全未命中返回
  `blocked_by_missing_anchor`；`run_child()` 同时拒绝"降级却写盘"与
  "报 applied 却没改任何文件"
- [x] 死代码/状态词收口：删 `count_needles` / `snapshot_original`，
  `skip_f2fs_rolled_back` 从状态词表移除，`block_rolled_back` 标注 informational，
  `report_only` 有了真实生产者

新组：

- [x] `config_enablement`（core）— 默认开 `ZRAM_TRACK_ENTRY_ACTIME=y` +
  `ZRAM_MULTI_COMP=y`（Batch 4 终于不是"代码在、开关没有"）；对齐档
  `ABK_515_DEFCONFIG_ALIGN=1` 追加 6.6 GKI 的 6 项（LRU_GEN_ENABLED / BBR /
  BLK_WBT / BLK_DEV_THROTTLING / TASK_DELAY_ACCT）
- [x] `zsmalloc_chain_size`（core，android15-6.6 来源）— 6.2 的 zspage chain
  定界重做：`CONFIG_ZSMALLOC_CHAIN_SIZE`（default 8 / range 4 16）+ 最小绝对
  浪费的 `calculate_zspage_chain_size()`；6.1.176 实测没有，5.15 四档基线
  `ZS_MAX_PAGES_PER_ZSPAGE`/`get_pages_per_zspage` 同形，单锚点集全兼容
- [x] `madvise_collapse`（core，android14-6.1 来源）— UAPI `MADV_COLLAPSE 25` +
  `madvise_behavior_valid()`/`madvise_need_mmap_write()`/`madvise_vma_behavior()`
  三处接线 + 5.15 形状的 `madvise_collapse()`/`madvise_collapse_errno()`
  （`khugepaged_scan_pmd/scan_file` 增加可选 `res` 出参，kthread 传 NULL，
  行为逐字节不变）；不引入 folio_walk，复用 5.15 既有 helper
  。声明点用上游同一位置 `include/linux/huge_mm.h`（`mm/madvise.c` 经
  `linux/mm.h` 已可见该头，无需再 graft include；这也是本轮审查补上的
  P0 —— 原先声明放在 `include/linux/khugepaged.h`，而 5.15 的 madvise.c
  并不 include 它，会撞 implicit-function-declaration）

多版本兼容底座（本轮实测）：

- `mm/zsmalloc.c` 167/178/194/211 只差 11–15 行、`ZS_MAX_ZSPAGE_ORDER` 每档
  3 命中；`mm/khugepaged.c` 178↔194 差 17 行、collapse helper 每档 6 命中
- 8 个 defconfig 符号四档全缺 → 追加分支三档一致；`TRANSPARENT_HUGEPAGE=y`
  四档都有，MADV_COLLAPSE 不会空转
- `.211` 成为第四夹具：matrix 记录两个已知债务（`randomize_kstack_pertask` /
  `blk_mq_suspend_wakeup_abort` = blocked_by_shape）与
  `sched_rt_optimizations` 的部分预置漂移，step_audit 可跑可预期

本轮实测排除（写进两份 survey，不再重议）：

- [x] `RT_SOFTIRQ_AWARE_SCHED`：基线已有，仅改名成
  `CONFIG_RT_SOFTINT_OPTIMIZATION` / `task_may_not_preempt()`
- [x] MGLRU 6.1 代际改进：167 与 6.1.176 的 `mm/vmscan.c` 同代
- [x] ACK 厂商特性 warp / cpu.exstat / taskhint / memory.async_fork /
  cpu.identity / id_boost / dl_server：6.1 与 6.6 两线都不存在
- [x] DAMON SYSFS/LRU_SORT：需要 6.1 core 长大（`core.c` 27→46KB，sysfs 系列
  约 10 万字节），维持延后

<a id="batch-5"></a>

## Batch 5(v0.6.0)

目标：一条注入串同时覆盖 CI `build.yml` 里 android13-5.15 的三个合法组合
（167/2024-11、178/2025-03、194/2025-12）。结论是**不需要 sublevel 门控**：
引擎的判定全在文本锚点，`ctx.sub_level` 只进报告；22 组里 21 组开箱即兼容。
真实 AOSP 树（三个分支各 40 个文件）实测缺口只有四个：

- [x] `pagealloc_cpuset_bailout` 的 `static_branch_enable` 改为上游一致的
  `static_branch_enable_cpuslocked`。194/.211 基线已带 .191 的 cpuset 改造，
  仅这一个 token 导致第 5 步 `missing_anchor` → 整组 `blocked_by_shape`；
  另外 `cpuset_write_resmask()` 路径已持 `cpu_hotplug_lock`，`_cpuslocked`
  才是正确变体
- [x] 拆出 `fdtable_replace_fd_errno`（`ff8ec0dbe0150`, 5.15.195）独立成组。
  原先是 fdtable 组的 optional 第 9 步，而该组在
  `fdtable_upstream_shape()` 为真时（.191 起）立即 `already_present` 返回，
  这个 .195 才有的 hunk 在 .191–.194 目标上永远落不下去
- [x] `memcg_memory_reclaim` 补 3 个调用点：`mem_cgroup_force_empty()` /
  `memory_high_write()` / `memory_max_write()` 仍传 `true`（=1），而
  `MEMCG_RECLAIM_MAY_SWAP` 是 `(1 << 1)`，`vmscan` 里
  `!!(reclaim_options & MEMCG_RECLAIM_MAY_SWAP)` 得 0 → **这三条回收路径
  静默丢掉换页**。与 sublevel 无关，167 生产基线同样中招
- [x] 测试设施参数化：新增 `tests/sublevel_matrix.py`（期望矩阵）与
  `tests/fetch_sublevel_tree.sh`（gitiles 只拉 40 个目标文件）；
  `smoke.sh` / `step_audit.py` 按被测树 Makefile 的 `SUBLEVEL` 取期望
  （`ABK_TEST_SUB_LEVEL` 可覆盖）。`step_audit.py` 的 trap-1 断言改为
  跳过"基线已自带"的组，并新增大括号与 `#if/#endif` 配平检查
- [x] 验证：三棵树 step_audit 全绿（core 94/95/86 步，perf 81/81/80 步）、
  smoke 双跑幂等 + 回滚、单测 +12 项新检查、结构配平（注释/括号/ifdef）
  在三棵树上均保持；CI 三次编译待跑

顺带修正文档中的一处误判：AOSP android13-5.15 线**从未收上游 .171 的 Gorman
ALLOC_HARDER→ALLOC_MIN_RESERVE 改造**（167/.178/.194/.211 四棵树
`mm/internal.h` 都还是 `ALLOC_HARDER 0x10`、`gfp_to_alloc_flags` 单参数），
所以两个 page_alloc 组在本基线族全线 `applied`，不随 sublevel 漂移。

### .211（android13-5.15-lts）遗留阻塞（后续批次）

- [ ] `randomize_kstack_pertask` — .211 已占用 `ANDROID_KABI_RESERVE(1)`
  （`user_dumpable` 位域），8 连 RESERVE 锚点失配，需补该形态的槽位分支
- [ ] `blk_mq_suspend_wakeup_abort` — .211 已自带
  `#ifndef __GENKSYMS__ #include <linux/suspend.h> #endif` 与
  `pm_wakeup_pending()` 逻辑，需补 `already_present` 探针

<a id="batch-4"></a>

## Batch 4(v0.5.0)

来源：android15-6.6 ACK 分支（survey 见 `docs/survey_6_6_ack.md`）。
ABK_ABI_PATCH_SUITE 覆盖对照：其余候选（EEVDF / io_uring / slab / hugepage / fdtable /
pid / zram-writeback）均属套件领地，排除；本轮仅落地 zram 重组（套件只覆盖 writeback）。

- [x] `zram_recompression`（core）— android15-6.6 / 6.2 系列：
  `ZRAM_MULTI_COMP` + `ZRAM_TRACK_ENTRY_ACTIME`，`comps[]`/`comp_algs[]`/
  `num_active_comps`（保留 `ZRAM_FLAG_SHIFT=24`），`zram_read_from_zspool`、
  `zram_recompress`、`recompress_store` + sysfs、`mark_idle` 龄期标记、
  comp_algorithm/recomp_algorithm 多 comp 机制、多 comp 初始化；zsmalloc 新增
  `zs_lookup_class_index()`
- [x] 验证：step_audit 通过（91 步 core，幂等）、smoke 计数更新（core 9→10）、
  py_compile + 单测全绿；CI 编译验证随 run 33309011902（后随 CI 结果更新）

<a id="batch-3"></a>

## Batch 3(v0.4.0)

来源：android14-6.1 ACK 分支（6.1 唯一 ACK 线），survey 见 `docs/survey_6_1_ack.md`。
ABK_ABI_PATCH_SUITE 覆盖对照：五组均未被 suite 覆盖（suite 特性来源为 7.0.12），
suite 已覆盖的热点路径（fdtable/close_range/pid/slab/hugepage/io_uring/zram-wb/EEVDF）列入排除清单、构建时注入 suite。

- [x] `memcg_memory_reclaim`（core）— 6.1 `memory.reclaim` 主动回收 + `MEMCG_RECLAIM_*` 选项替换 may_swap
- [x] `psi_irq_tracking`（perf）— 6.1 PSI_IRQ 中断压力（52b1364 形态适配 iterate_groups 步行）
- [x] `psi_trigger_kernfs_polling`（perf）— ACK 6.1 kernfs 轮询重构 backport（psi_trigger_ext/pending_event/1us 窗口；psi_group 布局不动）
- [x] `sched_lazy_preemption_hooks`（perf）— ACK 6.1 lazy preemption 厂商钩子族（含基线缺的 set_tsk_need_resched_lazy + resched_curr 门）
- [x] `locking_wakeup_patch_hooks`（perf）— ACK 6.1 mutex/rwsem 唤醒后 fixup 钩子
- [x] 验证：step_audit 146 步、smoke 双跑幂等 + 回滚、dry-run 全绿、WSL repo manifest 同步后本地 GKI 编译

<a id="batch-1"></a>

## Batch 1(v0.1.0)

- [x] `fdtable_alloc_conventions`（core）— 5.15.191 调用约定 + INT_MAX 防护；与 ABI 套件探测器握手
- [x] `pagealloc_min_reserve_semantics`（core）— 5.15.171 ALLOC_HIGH→ALLOC_MIN_RESERVE + RT 任务语义
- [x] `pagealloc_thisnode_thp_noreclaim`（core）— 5.15.202 THP __GFP_THISNODE 只压缩不回收
- [x] `pagealloc_cpuset_bailout`（core）— 5.15.191 cpuset 禁区早退
- [x] `pagealloc_high_fraction_lockfree`（core）— 5.15.200 sysctl 读路径去锁
- [x] `cgroup_root_list_rcu`（core）— 5.15.168 root_list RCU 化
- [x] `cgroup_destroy_wq_split`（core）— 5.15.194 销毁 wq 三分
- [x] `sched_nohz_idle_balance_series`（perf）— 5.15.174 四连
- [x] `sched_psi_flags_migration`（perf）— 5.15.179 psi_flags 差量
- [x] `sched_rt_optimizations`（perf）— 5.15.202/.202 RT 扫描自跳 + RT_PUSH_IPI 默认关
- [x] `randomize_kstack_pertask`（perf）— 5.15.210 每任务偏移（KABI 槽 8；CI 修正：ABK 的 Kernel 特定补丁会把槽 6/7/8 改造成 SysVIPC 形态，该形态自动改用仍空闲的槽 5，并带 task_struct 范围硬校验）
- [x] `release_sock_cond_resched`（perf）— 5.15.197 __release_sock 每 16 包让出
- [x] `semaphore_wake_q`（perf）— 5.15.180 唤醒移出临界区
- [x] `blk_mq_suspend_wakeup_abort`（perf）— 5.15.198 挂起遇 wakeup 中止
