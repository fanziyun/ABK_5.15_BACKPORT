# plan.md — living backlog

状态词：`[ ]` 候选 / `[~]` 延后（需更大 rebase）/ `[x]` 已落地 / `[-]` 无收获或按政策排除。
每批次落地后在 `module.conf` 递增 `ABK_MODULE_VERSION`。
已落地批次的完整原文（政策变更说明、落地明细表、调试/试错记录、验证结果、审计基线）已归档到 [`CHANGELOG.md`](CHANGELOG.md)，按 Batch 倒序排列；本文件里每个已落地批次只保留一行索引。

## Batch 18(v0.23.0,已落地)→ 详见 CHANGELOG.md#batch-18 — zram writeback 的 Enforcing 策略缺口（companion 模块 sepolicy.rule）

## Batch 17(v0.22.0,已落地)→ 详见 CHANGELOG.md#batch-17 — zram writeback bio batching + compressed writeback

### 排除项（结论；原文见 CHANGELOG.md#batch-17）
- `7c929664fddf` / `a4f506c569e1`（wb limit 存写锁改造 / 删 `wb_limit_lock`）——**不移植**：与 batching 机制无关，删锁也违反「不为通过编译而删锁」；batch14 的 `writeback_limit` 对齐护栏正锚在 `wb_limit_lock` 的 store 文本上。
- `e87ddea34567` 的重命名与 `INVALID_BDEV_BLOCK` 哨兵——只取「返回前释放保留块」的语义；重命名会波及 `zram_free_page()` 的 `ZRAM_WB` 分支（recompression 领地）。
- `1b1a4e4d6797`（读 slot blk_idx 持锁）——读路径加固，不是本特性的依赖；5.15 的同一理论窗口今天已存在，不是本批引入的回归，留作后续独立候选。
- `bf989ade270d`（read_from_bdev_async 错误传播）——无 `Fixes:` 指向本特性；本批的读回 dispatcher 自带错误传播。
- `zs_obj_read_begin/end` 与整个 zsmalloc 重写——**不需要**：5.15 `zs_map_object()` 本来就为跨页对象返回连续副本（`mm/zsmalloc.c` 的 per-cpu `vm_buf` 就是为跨页对象分配的），见 CHANGELOG.md#batch-17 的可行性证明表。
- huge_idle、6.16 writeback ABI（`cf42d4cccf0d`）、`be48c412f6eb`、Documentation 两处改写——无 5.15 消费者或与本批无关。

### 真机验证（2026-09-14 已跑完 → `research/zram/vermeer_batch17_check/`）
vermeer/23113RKC6C，内核 `5.15.216-android13-8-g5bfe2b8c1439`（= 本轮 ROM tier 构建）。
在 hot_add 出来的独立 zram1 上做（不碰在用的 zram0），每例都校验写回前/写回后读回的 md5 与原数据一致：
- **batching**：batch 1→32，64MiB×3 次 → **墙钟 17×、上下文切换 12×、写回任务 CPU 6.5×**
  （90.7→14.0 jiffy = 363→56 ms）；batch 256 再压上下文切换但不压墙钟（瓶颈转到 loop/闪存）。
- **compressed writeback**：写侧省、读侧花 —— 写回任务 CPU 低 15–25%，但把 64MiB 全部读回时
  读侧**系统级** CPU 高约 48%（解压被搬到 `system_highpri_wq`，不计在读者 syscall 上）。
  按量级盈亏平衡点约在**读回率 25–30%**，故**默认 0 是正确取舍**，模块不应强行打开。
  同时**证伪**了「后备设备少写 4K 页」：bio 恒为 `PAGE_SIZE`，`bd_writes` 两模式完全相同。
- **写回上限记账**：`writeback_limit=100` 块，batch 1/32/256 都**恰好写 100 页**（提交前扣费不超发）。
- **平台缺口（与本批无关但影响可用性）**：Enforcing 下 loop worker 读写后备文件被
  `avc: denied { write } ... scontext=u:r:kernel:s0 tcontext=u:object_r:zram_data_file:s0` 拒，
  两种文件上下文都一样 → 这台 ROM 上 **zram writeback 在 Enforcing 下必然 `-EIO`**，
  连 ROM 自己挂在 zram0 上的 loop49 也一样（`bd_stat` 至今 `0 0 0`）；内核侧无解，
  要靠 ROM 集成或 KSU sepolicy 补丁 —— **已由 Batch 18（v0.23.0）用 companion 模块的
  `sepolicy.rule` 打通并复测**（Enforcing 下 `bd=[4096 0 4096]`、零残留 AVC）。
  上面的性能数据仍是在 permissive 下测的。

## Batch 16(v0.21.0,已落地)→ 详见 CHANGELOG.md#batch-16 — 空实现审计 + 清理

### 排除项（结论；原文见 CHANGELOG.md#batch-16）
- `scripts/batch15_core_swap_table.py` 仍是 `REGISTER_AS_CHILD = False`、只被 docstring 提到（未接线）：这是 Batch 15 有意的「评估留档、不接线」，不属于空实现，保持原样。

### 后续结论（原文见 CHANGELOG.md#batch-16）
- 被删的 `se->slice` 槽 4 与 nohz 四谓词 + 两导出，已回**套件原文**复核：套件里同样 0 个调用点（它自己的 smoke 只 grep 存在性），属套件自带死面，不是本模块接线遗漏；树外亦无引用（证据边界：导出符号的消费者可能在树外）。
- 同类真例只有 `offload_all` 一例，处置是**接上**（tier 启用）而非删除。
- 新增 `tests/config_gate_audit.py`：用 `.abk-orig` diff 归属，补上 `_INTRODUCED_KCONFIG` 对**依赖的既有符号**的盲区；需一份由当前 tier 产出的 `.config`。对 Batch 16 之前的 `.config` 报 1 条（RCU），按当前 tier 重建后应转绿。

## Batch 15(v0.20.0,已落地)→ 详见 CHANGELOG.md#batch-15 — 撤销 ABK_ABI_PATCH_SUITE 红线 + 收编其优化特性

### 未收编（结论；原文见 CHANGELOG.md#batch-15）
- **io_uring NOWAIT / cBPF / non-circular SQ / zcrx**：套件面向 5.18 之后拆分的 `io_uring/*.c`，而 5.15 是单文件 11,116 行 monolith，手搬是独立工程。
- **`swap_table_phase2_large_folios`**：5.15 无 `mm/swap.h`、无 `struct folio`、12 个被调用者全缺，退化成 page-first 只是行为中性的代码搬移（评估留在 `scripts/batch15_core_swap_table.py`，未接线）。
- **`bpf_timer_bpf_wq_lockless`**：`defer_timer_wq_op` / `bpf_wq` 在 5.15 **和** 6.1 都是 0 命中，在套件自己的目标形态上也不成立。
- **`zram_compressed_writeback`**：套件版本只加标志 + sysfs 属性、**没有 I/O 实现**，真特性需要两棵树都没有的 `zs_obj_read_begin/end`。
- **marker-only 组**：`sched_eevdf_runtime_state_phase3`、`io_uring_large_rx_buffer_zcrx` 只插一行注释，收编会造出 phantom 组。
- **`io_uring_support_modules`** 只做分类、不写文件；`tcp_socket_layout_reduction` / `ipv6_tcp_output_path` 套件自己就判 `blocked_by_layout`/`report_only`。

## Batch 14(v0.19.0,已落地)→ 详见 CHANGELOG.md#batch-14 — zram writeback 正确性补齐

### 已明确不做（结论；原文见 CHANGELOG.md#batch-14）
- `type=` / `page_indexes=` / 区间（6.16 `cf42d4cccf0d`）——**N/A**：纯现代用户态接口，android13-5.15 没有任何消费者，且它依赖 2024 年的 pp-slot 目标选择重写。
- writeback bio 分批 + `writeback_batch_size`（v6.19）与 **compressed writeback**（v7.0 `d38fab605c66`）——原判「**延后**」的两条理由已**全部证伪**，两者均已作为 **Batch 17（v0.22.0）**落地：batching 不需要连 pp-slot 机制一起搬（in-flight 窗口用 5.15 自己的 `ZRAM_UNDER_WB` + `ZRAM_IDLE` 表达），compressed writeback 也不卡在现代 zsmalloc 映射 API 上（5.15 `zs_map_object()` 已提供跨页对象的连续副本，`zstrm->buffer` 就是 5.15 版的 bounce buffer）。同名冲突只在共注入 `ABK_ABI_PATCH_SUITE` 时成立，而该套件自 Batch 15 起禁止共注入。
- `be48c412f6eb`（拒绝零长度 backing device，5.15.168 才进）——**不补**：只有 5.15.167 缺它，而它不是本批的编辑输入，单独成组在 178/194/216 上无法区分“基线自带”与“前面步骤加的”，留作套件候选。
- 2023+ 的 writeback 重构（`330edc2bc059` / `5e99893444a0` / `b967fa1ba72b`）——**N/A**：换设计而非修 bug，5.15 要关的那个竞态 `idle_store()` 的 `ZRAM_UNDER_WB` 检查已经关掉了。

## Batch 13(v0.18.0,已落地)→ 详见 CHANGELOG.md#batch-13 — `android_vh_customize_alloc_gfp` 挂点 + 高阶慢路径快速失败策略

### 未结项（原文见 CHANGELOG.md#batch-13）
待验证：ABK CI 编译（本批引入 C，编译是唯一真门禁）；真机上
`/sys/module/page_alloc/parameters/abk_gfp_fastfail*` 三节点存在、低内存高压下
`compact_failures`/direct reclaim 停顿与 THP 成功率的对照另立后续批次
（distribution asset，不进 registry）。

## Batch 12(v0.15.0,已落地)→ 详见 CHANGELOG.md#batch-12 — 内核侧算法锁 + writeback 并存

### 未结项（原文见 CHANGELOG.md#batch-12）
- [x] **已验**（2026-09-14，vermeer / 内核 `5.15.216-android13-8-g5bfe2b8c1439`）：
  `/sys/module/zram/parameters/abk_{comp,lock,recomp}_algo` 三节点存在且 **0444**（读出 `lz4kd` / `Y`）；
  dmesg 有 `zram: comp_algorithm is locked, ignoring a write of 'lzo-rle'`（开机时 ROM 自己试写被拒）；
  `ABK_515_DEFCONFIG_ROM=1` build 上 `backing_dev=/dev/block/loop49` 已挂，且
  `writeback_limit=2752512` + `writeback_limit_enable=1` 生效。
  证据：`research/zram/vermeer_batch17_check/raw/01-capability-and-nodes.txt`。
- [ ] 待验（本模块刷入即可，无需新内核）：60 s 内把被改掉的算法改回；日志出现
  `rewrite: size=…`；`action.sh status` 的 `policy` 行。

## Batch 11(v0.14.0,已落地)→ 详见 CHANGELOG.md#batch-11 — 运行时伴随模块 + zram 算法策略

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

- [x] `zram_writeback_limit_audit`（P3，候选小项）— **已核对并落地**：
  android13-5.15 目标树**自带**上游 zram `writeback_limit` / `_enable` / `bd_stat`
  （与 5.16 原始形态逐字节等价），缺的只是主线 `writeback_limit_store()` 里的
  `val = rounddown(val, PAGE_SIZE / 4096);` 对齐护栏；已作为 Batch 14
  `zram_wb_limit_align` 落地（`GROUP_COUNTS` core 24→27，v0.19.0）。
  完整证据、上游溯源与全部裁决见
  [`research/zram_writeback_plan.md`](research/zram_writeback_plan.md)，
  上游哈希复核结果见 `research/zram_wb_audit/verified_commits.tsv`。
- [x] `zram_recompress_max_pages`（P3）— **溯源完成，未落地**：参数确在
  mainline `recompress_store()`——`research/upstream-zram/zram_drv_master.c:2580`
  解析 `max_pages` 到 `num_recomp_pages`（初值 `ULLONG_MAX`），
  `recompress_slot()` 逐页递减，扫描循环在归零时 `break`；
  `research/upstream-zram/zram_drv_linux-5.15.y.c` 与 android13-5.15 都没有。
  **为什么不当"可选追加步"落地**：该函数在 pristine 5.15 里**根本不存在**
  （实测 `abk515_ref_167/drivers/block/zram/zram_drv.c` 无 `recompress_store`），
  它是本模块 `zram_recompression` 组自己生成的文本。所以这里的锚点不是
  pristine 锚点，按 `docs/group_recipe.md` 的 trap 4，它应当是**注册在
  `zram_recompression` 之后的独立组**，而不是该组的可选步。留作候选。
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

## Batch 9-1(v0.11.0,已落地)→ 详见 CHANGELOG.md#batch-9-1 — dynamic_readahead_lowmem

## Batch 10（三线调研 → 移植设计，进行中）

调研工件：popsicle walt 源码抽读件 `research/popsicle_w_oss/walt_extract/`
（smart_freq/pipeline/voter/cpufreq_walt）；LRPB/smart_freq PELT 化报告
`research/popsicle_w_oss/walt_pelt_survey.md`；QPACE 骨架
`research/popsicle_w_oss/zram_drv_tip_vs_parent.diff`；MFZ 全仓普查结论见上。

## Batch 10-1(v0.12.0,已落地)→ 详见 CHANGELOG.md#batch-10-1 — zram_async_recompress（方案 A）

## Batch 10-2(v0.12.0,已落地)→ 详见 CHANGELOG.md#batch-10-2 — schedutil smart_freq-PELT 策略层

## Batch 10-3(v0.12.0,已落地)→ 详见 CHANGELOG.md#batch-10-3 — cached_freeze_reclaim（通用名替代 MFZ）

## Batch 10-4(v0.13.0,已落地)→ 详见 CHANGELOG.md#batch-10-4 — 让已落地特性在真机上真正生效

### 未结项（原文见 CHANGELOG.md#batch-10-4）
- [ ] 待验（Batch 11 遗留）：`dmesg | grep recompression` 注册日志（ring buffer 已滚动）、
  `abk_sf` 在 walt governor 下是否进入 boosting（`sched_pelt_multiplier=4` 有长期
  boosting 风险，见 Batch 11）。

## Batch 10-5(v0.17.0,已落地)→ 详见 CHANGELOG.md#batch-10-5 — smart-freq 与 FAS 的调频权收口

## Batch 10-6(v0.17.1,已落地)→ 详见 CHANGELOG.md#batch-10-6 — 超大核“不被调用”结案：上限持有者同时在决定放置

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

## Batch 8(v0.10.1,已落地)→ 详见 CHANGELOG.md#batch-8 — page_alloc fallback + RCU NOCB

### 延后项（原文见 CHANGELOG.md#batch-8）
- [~] `mglru_612_refresh`（P2，独立 MM 分支）— 先验证 deactivation，再评估完整
  aging/workingset/refault/type-selection 系列
- [~] `large_folio_mthp_substrate`（P2，独立 MM/VFS 分支）— 完成 page cache、
  readahead、THP、rmap/migration 和 mapping order 基础设施后再接文件系统
- [~] `maple_tree_per_vma_lock`（P3，独立 MM 分支）— 高收益但涉及 VMA 生命周期、
  fault path 和 KABI，当前模块不做 bounded graft
- [~] `f2fs_readonly_large_folio` / `erofs_large_folio_zstd`（P2/P3，文件系统
  sibling suite，按镜像格式和 CPU 预算条件启用）

## Batch 7(v0.8.0,已落地)→ 详见 CHANGELOG.md#batch-7 — display 修复子模块 stable_display_fix

## Batch 6.1(v0.7.1,已落地)→ 详见 CHANGELOG.md#batch-6.1 — 修 Batch 6 的 MADV_COLLAPSE 半应用

## Batch 6(v0.7.0,已落地)→ 详见 CHANGELOG.md#batch-6 — 审查收口 + 6.1/6.6 三新组

## Batch 5(v0.6.0,已落地)→ 详见 CHANGELOG.md#batch-5 — 多 sublevel 兼容 167/.178/.194

### .211（android13-5.15-lts）遗留阻塞（后续批次）

- [ ] `randomize_kstack_pertask` — .211 已占用 `ANDROID_KABI_RESERVE(1)`
  （`user_dumpable` 位域），8 连 RESERVE 锚点失配，需补该形态的槽位分支
- [ ] `blk_mq_suspend_wakeup_abort` — .211 已自带
  `#ifndef __GENKSYMS__ #include <linux/suspend.h> #endif` 与
  `pm_wakeup_pending()` 逻辑，需补 `already_present` 探针

## Batch 4(v0.5.0,已落地)→ 详见 CHANGELOG.md#batch-4 — android15-6.6 来源线

## Batch 3(v0.4.0,已落地)→ 详见 CHANGELOG.md#batch-3 — android14-6.1 来源线

## Batch 1(v0.1.0,已落地)→ 详见 CHANGELOG.md#batch-1

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

## 禁区清单（与 sibling 模块的硬边界）

**Batch 15 起 ABI 套件红线已撤销**（依据见 `docs/porting_policy.md` 的
"Suite absorption"）：套件的优化特性全部并入本模块，因此下面两条原红线作废。

- 本模块**自己**占用 `sched_entity` KABI 槽 1–3（EEVDF：
  `deadline`/`min_vruntime`/`vlag`）与 `request_queue` 槽 1
  （`async_depth`）。槽 4 已在 Batch 16 退回 `ANDROID_KABI_RESERVE`（套件原把它
  占成 `u64 slice`，而全树从无读取）。同一构建**不得**再注入
  ABK_ABI_PATCH_SUITE，否则两个模块争同一槽位（KMI 硬冲突）——注意套件是无条件
  占 1–4 的，所以「本模块不再用槽 4」并不等于可以共存。
- 原「不改写 ABI 套件硬失败组函数体」的限制作废：`alloc_pid()`、
  `pick_file()`/`__range_close()`、`select_idle_cpu()`、`pick_next_entity()`
  现在由本模块自己接管。
- 不在本模块内回滚/前向改写 `fs/f2fs`、`drivers/scsi/ufs`（F2FS 套件领地）
- 不引入 .patch 载荷；全部嫁接保持 anchor 脚本形态
