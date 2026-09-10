# plan.md — living backlog

状态词：`[ ]` 候选 / `[~]` 延后（需更大 rebase）/ `[x]` 已落地 / `[-]` 无收获或按政策排除。
每批次落地后在 `module.conf` 递增 `ABK_MODULE_VERSION`。

## Batch 11（v0.14.0，运行时伴随模块 + zram 算法策略）

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
- [ ] 待验（Batch 10-4 遗留）：`dmesg | grep recompression` 注册日志（ring buffer 已滚动）、
  `abk_sf` 在 `sched_pelt_multiplier=4` 下是否长期 boosting。

## Batch 12（v0.15.0，内核侧算法锁 + writeback 并存）

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

## Batch 9 候选（popsicle-w-oss 调研 + Batch 9-1 dynamic_readahead 已落地）

来源：`MiCode/Xiaomi_Kernel_OpenSource` 分支 `popsicle-w-oss`
（小米 17 系，Android W，`Makefile` = 6.11.0）。调研结论与逐项证据见
[`docs/survey_popsicle_w_611.md`](docs/survey_popsicle_w_611.md)，
diff 工件与源树元数据见 `research/popsicle_w_oss/`。本批次主体仍是调研登记；
**Batch 9-1 `dynamic_readahead_lowmem` 组已落地**（见下文进度块，代码已入
registry、三档锚点/幂等/回滚审计全绿、ABK CI 编译通过，
`GROUP_COUNTS` core 16→17，`module.conf` 已递增至 v0.11.0）。
其余候选项零落地。

要点（决定本线的可移植性结论）：

- 该分支是 bazel 增量树：tip 全树仅 2370 文件（parent 约 79k），
  `mm/` 只有 zsmalloc、`kernel/sched/` 只有 qcom `walt/` 模块，
  readahead / fair.c / cpufreq_schedutil.c / `fs/` 均不在库内。
- tip 文件语义比 parent 新（出现 6.12+ 主线形态的 bdev API、zsmalloc
  锁重构），说明小米导入基座晚于仓库 parent → 无法做干净的小米增量归因。
- zram 侧最大厂商增量 = Qualcomm **QPACE** 硬件压缩引擎接入
  （`CONFIG_QTI_PAGE_COMPRESSION_ENGINE` + `zram_comp` 线程），
  依赖 SoC 硬件；调度侧新增文件（smart_freq/pipeline/voter…）版权均为
  Qualcomm，属 WALT 套件。
- 与模块现状的交集：`zsmalloc_chain_size`、`zram_recompression`
  （多压缩 + TRACK_ENTRY_ACTIME）已在 Batch 4/6 落地。

- [ ] `zram_writeback_limit_audit`（P3，候选小项）— 核对 android13-5.15
  目标树是否自带上游 zram `writeback_limit`；无则补上游小 hunk，
  先与 ABK_ABI_PATCH_SUITE 的 zram-writeback 领地做重叠排查
- [ ] `zram_recompress_max_pages`（P3，候选小项）— 先溯源 recompress
  `max_pages` 参数是否已入 mainline；是则可作 `zram_recompression`
  组的可选追加步
- [x] QPACE / kcompressd 异步压缩：popsicle diff 已抽读完毕（异步骨架 =
  整 bio 提交 + ring + 完成回调，绑定 6.12 形态与 QTI 硬件）→ 选定
  **方案 A**：把同一套"kthread + 队列 + 完成回调"骨架嫁接到本模块已落地的
  `zram_recompress()` 重压缩路径，使其成为后台异步作业；见 **Batch 10-1**
- [x] MFZ 内存冻结 / 页面配额：MiCode 全仓 264 分支 + 厂商模块目录普查
  （dijun/violin 的 xiaomi-modules 全递归）= **零 freeze 载体**；5.15 kalama
  分支（fuxi/sheng 等）mm/freezer/zram 全部为 upstream 原样。媒体证据指向
  HyperOS"后台冻结"为用户态框架行为 → 内核侧无源可搬，已按 AOSP 官方
  冻结器链条（frozen cgroup + `memory.reclaim` 最大回收）自行定义语义，
  更名为通用名 **`cached_freeze_reclaim`**（C 前缀 `abk_cfr_`，节点前缀
  `cfr_`，范围：内核记账 + 守护进程；见 **Batch 10-3**）—— 已落地
- [x] dynamic_readahead：已落地 —— 见上方 **Batch 9-1 落地进度**
- [x] schedutil smart_freq / LRPB：walt 源码已抽读并落盘
  （`research/popsicle_w_oss/walt_extract/` + 报告 `walt_pelt_survey.md`）；
  结论：控制层子集可保留、信号层必须自造，GKI 侧有现成
  `android_vh_map_util_freq(_new)`/`android_vh_cpufreq_resolve_freq`/
  `android_vh_scheduler_tick` 等 hook 作策略挂点，**无 PELT 化先例需自行设计**
  —— 已按 GKI 策略模块落地，见 **Batch 10-2**

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

## Batch 10（三线调研 → 移植设计，进行中）

调研工件：popsicle walt 源码抽读件 `research/popsicle_w_oss/walt_extract/`
（smart_freq/pipeline/voter/cpufreq_walt）；LRPB/smart_freq PELT 化报告
`research/popsicle_w_oss/walt_pelt_survey.md`；QPACE 骨架
`research/popsicle_w_oss/zram_drv_tip_vs_parent.diff`；MFZ 全仓普查结论见上。

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

### Batch 10-1 落地进度（zram_async_recompress，已落地 + ABK CI 编译通过）

- [x] 实现：`zram_async_recompress`（stable_backport_core，步骤脚本
  `scripts/batch10_core_zram_async.py`，接线见 `abk_stable_core.py` 尾部）。
- [x] 验证：py_compile + 单测全绿；step_audit / implementation_audit /
  smoke 在 167/178/194 三档全绿；ABK CI（run 34418417022，
  android13/5.15-X/lts，实际 5.15.215）after_patch + 编译内核全绿。

### Batch 10-2 落地进度（schedutil_smart_policy，已落地 + ABK CI 编译通过）

- [x] 实现：`schedutil_smart_policy`（stable_perf_backport，步骤脚本
  `scripts/batch10_perf_sched_policy.py`，接线见 `abk_stable_perf.py` 尾部）；
  集团计数 core 18（含 10-1）、perf 12→13（`tests/sublevel_matrix.py`
  `GROUP_COUNTS`）。
- [x] 验证：与 Batch 10-1 同轮审计 + 同轮 ABK CI（run 34418417022）全绿。

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

## Batch 8（v0.10.1，page_alloc fallback + RCU NOCB 项目已落地）

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

## Batch 7（v0.8.0，display 修复子模块 stable_display_fix，已落地）

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

## Batch 6.1（v0.7.1，修 Batch 6 的 MADV_COLLAPSE 半应用，已落地）

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

## Batch 6（v0.7.0，审查收口 + 6.1/6.6 三新组，已落地）

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

## Batch 5（v0.6.0，多 sublevel 兼容 167/.178/.194，已落地）

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

## Batch 4（v0.5.0，android15-6.6 来源线，已落地）

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

## Batch 3（v0.4.0，android14-6.1 来源线，已落地）

来源：android14-6.1 ACK 分支（6.1 唯一 ACK 线），survey 见 `docs/survey_6_1_ack.md`。
ABK_ABI_PATCH_SUITE 覆盖对照：五组均未被 suite 覆盖（suite 特性来源为 7.0.12），
suite 已覆盖的热点路径（fdtable/close_range/pid/slab/hugepage/io_uring/zram-wb/EEVDF）列入排除清单、构建时注入 suite。

- [x] `memcg_memory_reclaim`（core）— 6.1 `memory.reclaim` 主动回收 + `MEMCG_RECLAIM_*` 选项替换 may_swap
- [x] `psi_irq_tracking`（perf）— 6.1 PSI_IRQ 中断压力（52b1364 形态适配 iterate_groups 步行）
- [x] `psi_trigger_kernfs_polling`（perf）— ACK 6.1 kernfs 轮询重构 backport（psi_trigger_ext/pending_event/1us 窗口；psi_group 布局不动）
- [x] `sched_lazy_preemption_hooks`（perf）— ACK 6.1 lazy preemption 厂商钩子族（含基线缺的 set_tsk_need_resched_lazy + resched_curr 门）
- [x] `locking_wakeup_patch_hooks`（perf）— ACK 6.1 mutex/rwsem 唤醒后 fixup 钩子
- [x] 验证：step_audit 146 步、smoke 双跑幂等 + 回滚、dry-run 全绿、WSL repo manifest 同步后本地 GKI 编译

## Batch 1（v0.1.0，已落地）

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

## Batch 2 候选（延后，见 survey 的 Deferred 节）

- [x] Gorman 深水区：ca8527f25736（AOSP 已自带拆分）→ c1b8856c5a7d → 17dedfd6de69 → 85f58ee33c6c → 4c4e238d3ada → 735457683e23 → `pagealloc_highatomic_reserve_semantics`（12 步，vendor CMA 块与 trace 保留）
- [x] d99f14f8b142（.212）sched/fair dst-group 统计跳过 → `sched_dst_group_allowed_stats`（AOSP fair.c 锚点无漂移）
- [~] 9646443f28f3（.209）blk-mq quiesced elevator 切换
- [ ] 56135262c1f9（.179）steal time 追赶封顶（虚拟化场景才有收益）
- [ ] 64d9b734b6fe（.210）带宽比值 u64 化

## 排除记录（不再重议）

- [-] 4edae3ff6d4e mark_victim tracepoint：AOSP 2024-11 树已自带
- [-] mm/kfence：5.15.y 无特性提交
- [-] timer_shutdown 全套 / NLM_F_BULK / PTP / netns defer free / dst 访问器改名 / hugetlb 系 / 纯重命名类：政策排除（见 survey）

## 6.1 来源线后续批次（backlog）

- [ ] per-VMA locks（android14-6.1 全量移植；5.15 需 RCU VMA 生命周期 + fault 路径改造 + vma KABI 槽位，参照 rbtree 时代 RFC 设计）
- [ ] per-cgroup PSI 开关（cgroup.pressure enable/disable）— 被 psi_group 指针/父链重构（cgroup KMI 红线）卡住
- [~] DAMON sysfs 控制面（实测需要 6.1 core 长大：`core.c` 27→46KB + sysfs
  约 10 万字节，不再是"中等体量"，价值一般）
- [x] MADV_COLLAPSE（Batch 6 已落地，按 5.15 helper 重写，非 UAPI-only）
- [x] zram recompression（Batch 4 落地）+ zsmalloc chain-size（Batch 6 落地；
  6.2 来源、6.1.y 未收，来源线取 android15-6.6）
- [ ] PSI 内部全量同步（NR_ONCPU 移除 / TSK_ONCPU 掩码 / 父链）— 与 KMI 卡点纠缠

## 禁区清单（与两个 sibling 模块的硬边界）

- 不 claim `sched_entity` KABI 槽 1–4、`request_queue` 槽 1（ABI 套件已占用）
- 不改写 ABI 套件硬失败组的函数体：`alloc_pid()`、`pick_file()/__range_close()`、
  `select_idle_cpu()`、`pick_next_entity()`（除非探测到其 marker 后走跳过分支）
- 不在本模块内回滚/前向改写 `fs/f2fs`、`drivers/scsi/ufs`（F2FS 套件领地）
- 不引入 .patch 载荷；全部嫁接保持 anchor 脚本形态
