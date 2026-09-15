# plan.md — living backlog

状态词：`[ ]` 候选 / `[~]` 延后（需更大 rebase）/ `[x]` 已落地 / `[-]` 无收获或按政策排除。
每批次落地后在 `module.conf` 递增 `ABK_MODULE_VERSION`。**只有 companion 的批次例外**：只 bump
`ksu/*/module.prop`（先例 `a50df6e`，Batch 25 同），否则单测里钉的内核版本对不上号。
已落地批次的完整原文（政策变更说明、落地明细表、调试/试错记录、验证结果、审计基线）已归档到 [`CHANGELOG.md`](CHANGELOG.md)，按 Batch 倒序排列；本文件里每个已落地批次只保留一行索引。

## 交付总览（九项优化，按功能清单顺序）→ 详见 CHANGELOG.md#overview-nine — 把清单上的九项功能（内存分配 hook / 线程调度 hook / 空实现修复 `offload_all`·`se->slice` / 低内存立刻碎片回收 `abk_gfp_fastfail` / EEVDF / async_depth / zram writeback / bio batching / 重压缩）重排成一份交付日志，并往下续写第 10 项（本轮 `8a73e95` → HEAD 的四个提交：Batch 25 companion v0.9.0、Batch 26 core v0.30.0、companion v0.9.2、v0.9.3）：逐项给出批次、组名与到手证据（真机 adb、CI 构建号、审计门禁），不新增任何批次

## Batch 28(v0.31.1,已落地；真机已刷已复测；存活性修复版 v0.31.1 已刷已复测)→ 详见 CHANGELOG.md#batch-28 — EEVDF **现代版本差异审计**（`docs/survey_eevdf_gap.md`，对 Linux 6.6 → 7.3 逐提交比对）落地其 S/A 两级：Batch 15 那套是**扫描式重建**，`abk_pick_eevdf()` 每节点调 `abk_eevdf_refresh_deadline()`，而后者每次走两遍全树 ⇒ 一次 `pick_next_entity()` 约 O(n²)–O(n³) 且在选择中改写 `deadline`/`vlag`/`vruntime`；本批补上 6.6 的 `cfs_rq::avg_vruntime` 累加器（`af4cf40470c2`）、把 deadline 刷新搬进 `update_curr()`、选择器改为只读 O(n)，并加 `RUN_TO_PARITY`（`63304558ba5d`）、EEVDF 唤醒抢占（`147f3efaa241` 的 `check_preempt_wakeup` hunk）、`PREEMPT_SHORT`（`85e511df3cec`）、EEVDF yield（`79104becf42b`）、新任务放置（`c40dd90ac045`）；`sched_entity` 槽 4 由 Batch 16 的「退回 RESERVE」**翻案为重新认领** `u64 slice`（重建后真有读取点），代价是用尽保留槽 ⇒ 6.12+ 的 `min_slice`/`max_slice`/`vprot`/`sched_delayed` 无槽可放，这是 delayed dequeue 与 slice protection 记为「超出边界」的具体原因。改的是既有文件 `scripts/batch15_perf_eevdf.py`（新建文件会踩 trap 5），perf **22 → 23** 组，`module.conf` 0.30.1 → 0.31.0。真机 vermeer：`boot_a` 是纯内核分区，刷前先 `dd` 备份到设备与 PC 两处，回读 `cmp` 一致才重启；运行中的内核由 `/proc/kallsyms` 里 `abk_eevdf_preempt_short`（Batch 15 没有）证明，**`uname -r`/`/proc/version` 两侧相同、不可用作判据**；`mm/page_alloc.c` 的两条开机告警**回刷老内核 A/B 实测为旧有**（同 5 条同位置），本批零新告警；8 hog 压测下 soft lockup / hung task / RCU stall / scheduler WARN 全 0。**未做性能 A/B，不主张提速** —— 上游那个 −31% 是 x86 的 `perf bench sched messaging`，不是这台手机的数。**v0.31.1 存活审计 + 修复**：逐特性审计（44 agent）查出 4 处空实现，全为本批引入 —— ①`PREEMPT_SHORT` 恒为假（`se->slice` 两个写入点都是同一个全局常量，比较是 X≥X，开关 toggle 无效且 KABI 槽 4 花在常量上）；②`wakeup_preempt_entity`/`wakeup_gran`/`__pick_next_entity` 零调用者被 ThinLTO 删除，而注释谎称「留给树外用户」（它们是 `static`）；③**漏了上游的 `curr = NULL if !eligible` 前置门**，使 RUN_TO_PARITY 触发面偏宽、遮住 `yield_task_fair()` 设的 skip buddy，`sched_yield()` 反而比原版 CFS 更弱。按「补全而非删除」修：补回前置门；补上 `se->slice` 的**生产者**（`__setscheduler_params()` 里的 `attr->sched_runtime`，上游 `__setparam_fair()` 的等价体，钳位 100us..100ms，零编码表示「无自定义请求」因为保留槽已用尽）；恢复 EEVDF 系列自己的 `SCHED_FEAT(EEVDF)` 并把 CFS 两段阶梯放回 `!EEVDF` 那一半，三个死符号因此重新可达。审计工具同时补两处：`implementation_audit.py` 新增**跨文件不变量** `REQUIRED_PAIRING`（开关要两半都在、比较要两侧能不同），`config_gate_audit.py` 补上 `SCHED_FEAT` 覆盖。**分辨力已验证**：扩展后的审计对修复前的模块报红且诊断正确。编译零诊断，`wakeup_preempt_entity` 由「被删除」变为真实符号，导出符号计数 66→66/14→14（未新增 `EXPORT_SYMBOL`）。**仍未经真机验证**，且 §14 的 lockup 事故成因仍未定论。

## Batch 27(companion v0.11.0,已落地；归因待重测)→ 详见 `research/launch/vermeer_launch_20260915/FINDINGS.md` — 用户要求「查找优化 launch_boost 解决冷启动过慢」。真机逐条核实：**`launch_boost` 不是内核特性**，是小米的用户态应用启动预取栈（`xiaomi.launch_boost.readahead-ndk.so` 的 AIDL `ILbReadahead`：`readahead_start/finish/abort/clear/flush_all` + `init.launch_boost.rc` 拉起 `iorapd`），而且**当前完全没在运行**（`iorapd=stopped`、`PrereadEnable=false`、AIDL 服务未注册、无内核节点、无 `CONFIG_LAUNCH_BOOST`、`kallsyms` 无符号）。按用户裁定「先测量归因再定」+「先只做判定观测」，本批**不引入任何 `PatchGroup`**（registry 与 `GROUP_COUNTS` 未动；`module.conf` 本批不改，工作树里的 0.30.1 是并行的 zram writeback 修复批次改的；companion 只 bump `module.prop`，沿用 Batch 25 先例，且取 **v0.11.0** 以避开那个批次已声明的 v0.10.0），交付测量工装 `tools/abk_launch_bench.sh` + companion 接线（`embed.conf`/`action.sh launch`/README）+ 单测 `test_batch27_launch_bench`。工装测每次启动的 `TotalTime`、启动窗口内每簇 `cap_view`/`ceilings`、主线程落在最大簇的占比、应用 cpuset 是否含超大核、`pswpin`/`pgpgin` 与 PSI io；**拒绝**把没测到的数报成数、拒绝从样本不足的窗口下结论、**拒绝在息屏或锁屏的机器上测量**。真机上抓到并修掉四个自身缺陷（`${v%%|*}` 在本机 mksh 不可靠；processor 字段剥离正则在本机 comm 下永不匹配、`$37` 读到恒 0 的 `cnswap`，导致四十次启动报「0/714 在超大核」；`echo "\t"` 不展开；中位数函数键前缀错）。一条**方法学**结论已钉进工具：丢 page cache 后读页数涨 5–340 倍而 `TotalTime` 只涨 11–29%，故「读了很多页」≠「在等这些页」，存储份额只能由 `--save`/`--compare` 的**两臂延迟差**给出，单臂不再允许判 I/O 受限。**本批不指名杠杆**：唯一一次两臂完整的会话（`mt_on_big 28.9%`、`cap_inv 2.1%`）原始日志被后一轮覆盖，而后一轮同样「看似完整」却测的是一台锁屏手机 —— 两者在原始文件里分不出来，故不引用。**重测前提**：设备必须亮屏且已解锁（`mDreamingLockscreen=false`），`svc power stayon true` 在电池上无效，详见 FINDINGS.md §5 的两条命令与判据。

## Batch 26(v0.30.0,已落地；真机复测已完成)→ 详见 CHANGELOG.md#batch-26 — 修「Batch 21/25 在设备上没有节点可操作」这个真问题：AOSP lts 的 `gki_defconfig` 自带 `CONFIG_CMDLINE="… cgroup_disable=pressure"`，它同时关掉 per-cgroup 记账（`psi_cgroups_enabled` 静态分支）与全部 `CFTYPE_PRESSURE` 文件（含 `cgroup.pressure`）。新增第 4 档 `ABK_515_DEFCONFIG_PSI=1`（默认关）在 config lane 里把该 token 去掉：先把记账打开，开关才有东西可关；「去掉 `CFTYPE_PRESSURE` 让节点可见」的便宜修法被证伪为装饰性。registry 未动（无新组），`GROUP_COUNTS` 不变，`module.conf` 0.29.0 → 0.30.0
## Batch 25(companion v0.9.0,已落地；两态 A/B 真机已做)→ 详见 CHANGELOG.md#batch-25 — Batch 21 的 `cgroup.pressure` 开关第一次被按下去：新增 `tools/abk_psi_policy.sh`（`keep`/`auto`/`aggressive`，**只写 0 不写 1**，根组无条件先跳，前缀保护名单）+ `tools/abk_psi_bench.sh`（两态 A/B 工装，忙 jiffies 指标已标定线性）+ 假树 `--selftest`；点名结果 452 组 / 314 有任务 / 读者只有全局 PSI 的 `lmkd`·`system_server`·`mimd` ⇒ **`auto` 是零收益 no-op**，真对照是 `keep` vs `aggressive`，出厂默认因此仍 `keep`，协议与新文档 `docs/psi_field_protocol.md` 的判定规则等真机数据；registry 未动（本批一行 C 都没有），`module.conf` 保持 0.29.0

## Batch 24(v0.29.0,已落地)→ 详见 CHANGELOG.md#batch-24 — 重压缩每趟上限 `max_pages`（`34efe1c3b688`）+ 拒绝无法识别的 `type=`（`2f529e73d720`），同步与异步两个节点一起；本模块第一个**改写别的组生成文本**的批次，故给 `zram_recompression`/`zram_async_recompress` 各加自身载荷探针（trap 5 解法）；companion v0.8.0 默认 `zram.recomp.max_pages=16384`

## Batch 23(v0.28.0,已落地)→ 详见 CHANGELOG.md#batch-23 — 修 CI 暴露的真问题：zram compressed-writeback 新增块放进 `CONFIG_ZRAM_WRITEBACK` 门内（配置关掉也能编译），并新增"配置门内符号引用"审计

## Batch 22(v0.27.0,已落地)→ 详见 CHANGELOG.md#batch-22 — PSI 内部同步收口：`TSK_ONCPU` 改为 state mask 的位（去掉 `NR_ONCPU` 计数与 `identical_state` 启发式），仍不引入 `psi_group::parent`

## Batch 21(v0.26.0,已落地)→ 详见 CHANGELOG.md#batch-21 — android14-6.1 的 per-cgroup PSI 开关（`cgroup.pressure`）：KMI 中性实现（cgroup `flags` 位承载状态，`struct psi_group`/`struct cgroup` 一字节不动），语义与上游逐条对齐；顺带补上「后置组改写前置组新增文本 → 前置组必须有探针」这条陷阱变体

## Batch 20(v0.25.0,已落地)→ 详见 CHANGELOG.md#batch-20 — 上游 5.15.y 剩余候选清账：`blk_mq_quiesced_elevator_switch`（`9646443f28f3`）与 `sched_steal_time_excess_drop`（`56135262c1f9`）落地，`64d9b734b6fe` 按 arm64 no-op 排除 —— 这条来源线至此无未结候选

## Batch 19(v0.24.0,已落地)→ 详见 CHANGELOG.md#batch-19 — 关闭 lts 行的两个降级组（kstack KABI 槽形态分支 + blk-mq suspend 的 already_present 探针），`KNOWN_DEBT` 清空：四档基线上不再有任何「已知降级」

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
  上面的性能数据仍是在 permissive 下测的——**已于同日用 `b17_inflight.sh` 在 Enforcing 下
  重测**（FINDINGS.md §7）：在飞并发峰值精确等于 batch（1/8/32；batch=256 被 loop 的
  `nr_requests=128` 封顶），请求数/扇区数/`bd_stat` 全部不变（7690 / 61520），上下文切换与
  任务 CPU 各降到约 1/3.7；墙钟在本机波动 430–1970ms，**不是可靠判据**。cwb 只复现方向
  （读侧系统级 CPU 中位 +39%，写侧落在 jiffy 分辨率内），`bd_writes` 两模式仍完全相同。
- **可用性现实（同次会话发现，FINDINGS.md §8）**：本机 writeback **根本不会被触发**——
  `mmd` 不在运行、`vendor.zram.disable=1`、zram0 的 loop49 后备文件已被 unlink
  （`losetup -a` 显示空目标），companion 也只挂/保后备设备、从不写 `writeback` 节点。
  这两个特性在本机是**未被执行过的代码**；「连续打开 20 个应用」不可能测到它们。

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
- [x] `zram_recompress_max_pages`（P3）— **Batch 24(v0.29.0) 已落地**
  （组名 `zram_recompress_max_pages`，原文见 CHANGELOG.md#batch-24）。溯源记录保留如下，
  它是这条为什么必须独立成组的依据：参数确在 mainline `recompress_store()`——
  `research/upstream-zram/zram_drv_master.c:2580` 解析 `max_pages` 到
  `num_recomp_pages`（初值 `ULLONG_MAX`），`recompress_slot()` 逐页递减，扫描循环在
  归零时 `break`（另有一条同函数的 `2f529e73d720` 拒绝无法识别的 `type=`，一并收）；
  `research/upstream-zram/zram_drv_linux-5.15.y.c` 与 android13-5.15 都没有。
  **为什么不当"可选追加步"落地**：该函数在 pristine 5.15 里**根本不存在**
  （实测 `abk515_ref_167/drivers/block/zram/zram_drv.c` 无 `recompress_store`），
  它是本模块 `zram_recompression` 组自己生成的文本。所以锚点不是 pristine 锚点，
  按 `docs/group_recipe.md` 的 trap 4/5 它必须是**注册在 `zram_recompression` 之后
  的独立组**，而且那两个生成了被改写文本的前置组（`zram_recompression`、
  `zram_async_recompress`）要先各自加上自身载荷探针，否则第二遍会再追加一份函数。
  落地时还多带了一条 companion 旋钮 `zram.recomp.max_pages`（默认 16384 次尝试）——
  内核侧的上限没人调就等于没有。
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

### .211（android13-5.15-lts）遗留阻塞 — **Batch 19(v0.24.0) 已全部关闭**

- [x] `randomize_kstack_pertask` — 已补「槽 1 被 `user_dumpable` 占用」形态：认 2..8 的
  RESERVE run，仍占槽 8（原文见 CHANGELOG.md#batch-19 §1）
- [x] `blk_mq_suspend_wakeup_abort` — 改为探 payload 本身，216 行转入 `PRE_APPLIED`
  （原文见 CHANGELOG.md#batch-19 §2）；`KNOWN_DEBT` 因此清空

## Batch 4(v0.5.0,已落地)→ 详见 CHANGELOG.md#batch-4 — android15-6.6 来源线

## Batch 3(v0.4.0,已落地)→ 详见 CHANGELOG.md#batch-3 — android14-6.1 来源线

## Batch 1(v0.1.0,已落地)→ 详见 CHANGELOG.md#batch-1

## Batch 2 候选（延后，见 survey 的 Deferred 节）

- [x] Gorman 深水区：ca8527f25736（AOSP 已自带拆分）→ c1b8856c5a7d → 17dedfd6de69 → 85f58ee33c6c → 4c4e238d3ada → 735457683e23 → `pagealloc_highatomic_reserve_semantics`（12 步，vendor CMA 块与 trace 保留）
- [x] d99f14f8b142（.212）sched/fair dst-group 统计跳过 → `sched_dst_group_allowed_stats`（AOSP fair.c 锚点无漂移）
- [x] 9646443f28f3（.209）blk-mq quiesced elevator 切换 → **Batch 20(v0.25.0) 已落地**
  （`blk_mq_quiesced_elevator_switch`；原「与 ABI 套件重叠」的延后理由自 Batch 15 起作废）
- [x] 56135262c1f9（.179）steal time 追赶封顶 → **Batch 20 已落地**
  （`sched_steal_time_excess_drop`；裸机上 static key 恒关故为构造性 no-op，KVM/AVF guest 内生效）
- [-] 64d9b734b6fe（.210）带宽比值 u64 化 → **排除**：commit 自己写明收益场景是 32 位构建，
  本模块只跑 arm64（LP64 下 `unsigned long` 即 64 位），三行改动不改变任何位宽 —— 见
  CHANGELOG.md#batch-20 §3

## 排除记录（不再重议）

- [-] 4edae3ff6d4e mark_victim tracepoint：AOSP 2024-11 树已自带
- [-] mm/kfence：5.15.y 无特性提交
- [-] timer_shutdown 全套 / NLM_F_BULK / PTP / netns defer free / dst 访问器改名 / hugetlb 系 / 纯重命名类：政策排除（见 survey）

## 6.1 来源线后续批次（backlog）

- [~] per-VMA locks（android14-6.1 全量移植；5.15 需 RCU VMA 生命周期 + fault 路径改造 + vma KABI 槽位，参照 rbtree 时代 RFC 设计）
  - **2026-09-14 可行性探针（Batch 21/22 后，实测）**：三条结论，前两条与旧记载**不同**。
    1. **KMI 不是阻塞点**：5.15 的 `struct vm_area_struct` 末尾就有 4 个 `ANDROID_KABI_RESERVE` 槽
       （`include/linux/mm_types.h:431-434`，共 32 字节），而 6.1 的方案只需要 `int vm_lock_seq` +
       `struct vma_lock *vm_lock`（锁体是**单独分配**的，不内联）——空间够，且用法正是模块已有的
       `ANDROID_KABI_USE` 规则。
    2. **真正的工作量在写侧**：6.1 里 `vma_start_write()` 有 **42 处调用点**（仅在本次抽样的 11 个
       文件里就有），per-VMA 锁 API 合计 **83 处**（`mm/mmap.c` 27、`mm/userfaultfd.c` 17、`mm/memory.c` 14、
       `include/linux/mm.h` 15、`mmap_lock.h` 5、其余分散）。5.15 侧**一处都没有**（零基础设施）。
       真实总数还要加上本次未抽样的 `fs/userfaultfd.c`、`mm/mlock.c`、`mm/mempolicy.c`、
       `arch/arm64/mm/fault.c` 等 —— 也就是**上百处**需要正确插入写锁的 VMA 变更点，漏一处就是
       **静默的内存损坏**，不是"降级"。
    3. **6.1 的实现不能直接搬**：它长在 maple tree 上（`vma_lookup()`/`mas_walk()`，`mm.h` 里 4 处），
       5.15 是 rbtree —— 必须按 rbtree 时代的 RFC 形态写：`find_vma()` 在 RCU 下读 VMA，
       `vma->vm_lock_seq` 判定可读性，fault 入口仍走 `arch/arm64/mm/fault.c`。
  - **结论**：不是"能不能"的问题，而是**验证策略**的问题。现有四道门禁里只有 `implementation_audit`
    能证明"内容在"，没有一道能证明"**没有漏掉写者**"。要动这个项目，得先加一类新审计：
    枚举树里所有 VMA 变更点（`vm_start`/`vm_end`/`vm_flags`/`vm_pgoff`… 的写者）并与
    `vma_start_write()` 调用点做集合比对，任何差集即失败。规模估计：`mm/` + `arch/arm64/mm/` +
    `fs/userfaultfd.c` 约 15-20 个文件、100+ 步，属于"多批次项目"，不是单批 bounded graft。
  - **建议**：若要推进，先做**只读侧的核**（`lock_vma_under_rcu()` + `vma_start_read/end_read` +
    `vm_lock` 分配）并让写侧仍走 mmap_lock 写锁（即"per-VMA lock 只用于读，写者暂不 retouch"）——
    这样可编译、可验证、fault 路径立刻受益，且**没有漏写者的静默风险**；写侧 retouch 作为后续批次，
    与新审计一起落地。
- [x] per-cgroup PSI 开关（cgroup.pressure enable/disable）→ **Batch 21(v0.26.0) 已落地**
  （组名 `psi_cgroup_pressure_switch`）。结论：卡点从来不是「cgroup KMI 红线」本身，而是
  **不能给 `struct psi_group` 加成员**（它内嵌在 `struct cgroup` 里，其后还有 `bpf`/
  `congestion_count`/`freezer`/`ancestor_ids[]`）。最终实现用 cgroup 自己的 `flags`（`unsigned long`）
  承载 `CGRP_PSI_DISABLED` 位，`struct psi_group`/`struct cgroup` 一个字节不动；ACK 6.1 的
  `psi_group::parent` 也不需要（5.15 的 `iterate_groups()` 本来就走 cgroup 树）。唯一有意的
  行为差异：5.15 没有 `kernfs_show()`/`KERNFS_HIDDEN`（那套机制随本特性一起进树），所以关掉账的
  cgroup **不隐藏** `*.pressure`，而是让读取返回 `-EOPNOTSUPP`（不返回冻结旧数字）。详见
  CHANGELOG.md#batch-21
  - 探针结论（Batch 20 后记录）保留如下，作为设计输入：5.15 树上 `struct cgroup` / `struct psi_group` /
    `psi_group_cpu` **零 KABI 标记**（`cgroup-defs.h` / `psi_types.h` 里都没有 `ANDROID_KABI_*`），
    对照 `include/linux/sched.h` 的 17 处 —— 也就是说「cgroup KMI 红线」这条理由**没有被机械检查支撑**，
    它约束的是 out-of-tree 模块对 `struct cgroup` 布局的假设，而不是本模块自己的 KABI 台账。
    真正的约束是布局：`struct psi_group psi;` 在 `struct cgroup` 里不是最后一个成员
    （其后还有 `struct cgroup_bpf bpf;`、`atomic_t congestion_count;`、`struct cgroup_freezer_state
    freezer;` 和柔性数组 `u64 ancestor_ids[];`），所以开关只能复用已有的位宽空间。
  - 落地时发现的额外面：5.15 的 `cgroup_psi()` 只是 `&cgrp->psi`，而 root 的 `*.pressure` 由
    `cgroup_ino(cgrp) == 1 ? &psi_system : &cgrp->psi` 兜住（`iterate_groups()` 永不返回 root 自己的组），
    所以 root 的开关要落在 `psi_system` 上 —— 即 psi.c 里的一个静态布尔（等价于 6.1 的
    `psi_system.enabled`）。
- [~] DAMON sysfs 控制面（实测需要 6.1 core 长大：`core.c` 27→46KB + sysfs
  约 10 万字节，不再是"中等体量"，价值一般）
- [x] MADV_COLLAPSE（Batch 6 已落地，按 5.15 helper 重写，非 UAPI-only）
- [x] zram recompression（Batch 4 落地）+ zsmalloc chain-size（Batch 6 落地；
  6.2 来源、6.1.y 未收，来源线取 android15-6.6）
- [x] PSI 内部全量同步（NR_ONCPU 移除 / TSK_ONCPU 掩码 / 父链）→ **Batch 22(v0.27.0) 已落地**
  （组名 `psi_oncpu_state_mask`）：ONCPU 从"任务计数"改成 state mask 的一位，`NR_ONCPU` 枚举项与
  `psi_group_cpu::tasks[]` 的第 5 个计数一起消失；`psi_task_switch()` 的 `identical_state` 启发式
  （"状态相同才敢在共同祖先提前停"）随之作废 —— 位是幂等的，探到已置位的组就是共同祖先。
  **父链依旧不移植**（也没有移植理由）：5.15 的 `iterate_groups()` 本来就是 cgroup 树走查，
  位化之后"提前停"只需要探位。尾部条件必须一起改（`if (sleep)` →
  `(prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU`），否则提前停止会吞掉其它状态差异。
  6.1 同处的 `lockdep_assert_rq_held(cpu_rq(cpu));` 属另一处上游改动，**有意未移植**
  （见 CHANGELOG.md#batch-22）。

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
