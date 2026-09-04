# plan.md — living backlog

状态词：`[ ]` 候选 / `[~]` 延后（需更大 rebase）/ `[x]` 已落地 / `[-]` 无收获或按政策排除。
每批次落地后在 `module.conf` 递增 `ABK_MODULE_VERSION`。

## Batch 8（v0.10.0，page_alloc fallback + RCU NOCB 项目已落地）

本批次从 android15-6.6 / android16-6.12 筛出的长期项目中，先落地当前模块边界
内、证据最强的 `mm/page_alloc` fallback 优化和 RCU NOCB opt-in；完整 MGLRU、large folio/mTHP、
Maple Tree + per-VMA locks 需要独立 MM/VFS rebase；F2FS/UFS/EROFS 项目留在
sibling suite。AutoFDO 仍作为独立构建工程先建立整机基线。

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
