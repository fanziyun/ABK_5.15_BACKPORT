# plan.md — living backlog

状态词：`[ ]` 候选 / `[~]` 延后（需更大 rebase）/ `[x]` 已落地 / `[-]` 无收获或按政策排除。
每批次落地后在 `module.conf` 递增 `ABK_MODULE_VERSION`。

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
- [ ] DAMON sysfs 控制面（中等体量、价值一般）
- [ ] MADV_COLLAPSE（UAPI-only，THP 开启才有价值）
- [ ] zram recompression（6.2 来源，6.1.y 未收）
- [ ] PSI 内部全量同步（NR_ONCPU 移除 / TSK_ONCPU 掩码 / 父链）— 与 KMI 卡点纠缠

## 禁区清单（与两个 sibling 模块的硬边界）

- 不 claim `sched_entity` KABI 槽 1–4、`request_queue` 槽 1（ABI 套件已占用）
- 不改写 ABI 套件硬失败组的函数体：`alloc_pid()`、`pick_file()/__range_close()`、
  `select_idle_cpu()`、`pick_next_entity()`（除非探测到其 marker 后走跳过分支）
- 不在本模块内回滚/前向改写 `fs/f2fs`、`drivers/scsi/ufs`（F2FS 套件领地）
- 不引入 .patch 载荷；全部嫁接保持 anchor 脚本形态
