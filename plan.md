# plan.md — living backlog

状态词：`[ ]` 候选 / `[~]` 延后（需更大 rebase）/ `[x]` 已落地 / `[-]` 无收获或按政策排除。
每批次落地后在 `module.conf` 递增 `ABK_MODULE_VERSION`。**只有 companion 的批次例外**：只 bump
`ksu/*/module.prop`（先例 `a50df6e`，Batch 25 同），否则单测里钉的内核版本对不上号。
已落地批次的完整原文（政策变更说明、落地明细表、调试/试错记录、验证结果、审计基线）已归档到 [`CHANGELOG.md`](CHANGELOG.md)，按 Batch 倒序排列；本文件里每个已落地批次只保留一行索引。

## 交付总览（九项优化，按功能清单顺序）→ 详见 CHANGELOG.md#overview-nine — 把九项功能重排成一份交付日志并往下续写第 10 项（`8a73e95` → HEAD 的四个提交）：逐项给出批次、组名与到手证据，不新增任何批次

## Batch 33(v0.35.0,已落地)→ 详见 CHANGELOG.md#batch-33 — zsmalloc `zs_free()` 的 `class->lock` 临界区收窄（mainline `7ef28e8b8142`，系列 "mm/zsmalloc: reduce lock contention in zs_free()" v6 的第 3 个 patch）：空 zspage 的页面归还 buddy 的动作搬到 `class->lock` **之外**，锁内只留 `trylock_zspage()` + `remove_zspage()` + 每类统计（`class->stats.objs[]` 是普通 `unsigned long`、`zs_stat_dec()` 用 `-=` 更新，`zs_can_compact()` 在 `class->lock` 下经 `zs_stat_get()` 读它，那条搬不出去）。**系列前两个 patch 不移植**（`9909b088b1f0` 把 class 索引编码进 obj、`59e88952a827` 据此在 64 位免 `pool->lock`）：它们要去掉的是 `pool->lock` 读侧，而 android13-5.15 的 `mm/zsmalloc.c` **整文件没有 `pool->lock`**（五条基线 grep 均为 0）——见「排除记录」。**未做性能 A/B，不主张提速**

## Batch 32(v0.34.0,已落地；未做真机验证；构建闸门未跑)→ 详见 CHANGELOG.md#batch-32 — 核对 ACK 6.12 的 `37b72d525502` 是否需要回移：**需要**，它是 mainline `b0377ee80429`（`Fixes: d38fab605c667`）的回移，而那条正是 Batch 17 移植的 compressed writeback ⇒ 本模块的 `zram_writeback_complete()` 是**修复前形状**（先 `zram_free_page()` 再回填 huge/obj_size/priority）。落到两处：① 完成路径改成 open-coded 释放（`huge_pages` 只减一次），② `zram_free_page()` 的 huge 块加 `ZRAM_WB` 守卫；`pages_stored` 两个方向一起去掉 ⇒ 净值仍是 0，与 v0.30.1 真机表的 896 MB → 896 MB 逐字一致。**该缺陷不可由那次测量判别**（多减的一次落在槽被释放时，而那轮只写回）⇒ 不主张设备侧结论。本模块又一例「改写前置组生成文本」的组（同族于 Batch 21/24），按 trap 5 给 `zram_writeback_batching` 补了自身载荷探针；**顺带修正 Batch 17 的一条历史结论**：这条修复 2026-03-19 就合入、Batch 17 是 2026-09-14 落的（本就在审计窗口内），且补丁当时就在 `research/upstream-zram/patches/` 里 —— 当时三路核对各自漏得开它（搜索串少一位、窗口晚于合入点、快照晚于合入点），且没与同目录补丁集对账；core 38 → 39 组

## Batch 31(v0.33.0,已落地；四档干跑+四道门禁已过)→ 详见 CHANGELOG.md#batch-31 — 6.18 的 `143937ca51cc`（`pte_mkwrite()` 不再无条件清 `PTE_RDONLY`）**不是 6.18 独有**：上游 5.15.y 自己已 backport（`8a2375b0e9b8`，v5.15.196），而 167/178/194 仍是旧形态、216 已带上 ⇒ 目标形态取 **5.15.y 的 `pte_mkwrite()`**（5.15 没有 `pte_mkwrite_novma()`，照抄 mainline 一处锚点也匹配不上），上游形态改写不加 marker（216 逐字节不动、报 `already_present`）。core **37 → 38**（Batch 30 先取走 0.32.0/37）；这是本模块第一个 `arch/arm64` C 落点，fixture 三处（`FETCH_FILES`/`AUDIT_FILES`/`SMOKE_FILES`）已同步
## Batch 30(v0.32.0,已落地；ABK CI run 35058941428 success，模块侧已核到 head_sha + 报告行)→ 详见 CHANGELOG.md#batch-30 — 首条 **mainline 6.18 来源线**（前九项清单外）：搬 `e338d8353154`《mm: readahead: improve mmap_miss heuristic for concurrent faults》一组 `readahead_mmap_miss_race` —— 并发 fault 同一个 page 时每个线程都递减 per-file 的 `ra->mmap_miss`，计数器长期低于递增侧，于是「该文件随机访问、别再预读」的判据不再触发、内存压力下 mmap read-around 仍然开着（上游口径：Google 生产 fleet 卡在 reclaim 循环的容器数降 10–20×，**本批不主张单机提速**）。5.15 形态差异只有 `folio_test_locked`→`PageLocked`，锚点四档唯一（lts 的 ACK 钩子不影响）；刻意不搬：5.15 私有的 `FAULT_FLAG_SPECULATIVE` 那条递减（上游整条路径已删，无提交可搬）、fault-around 的 `filemap_map_pages()` 批量递减，以及后续对称系列两笔（`eb4c458a9803` 在 5.15 上其实有三处可改、`2f5e0477276b` 则无载体）。`GROUP_COUNTS` core 36→37

## Batch 29(companion v0.12.0,已落地；真机已验证)→ 详见 CHANGELOG.md#batch-29 — 修「主动回收在这台设备上**必然落空**」：v1 树的 per-UID 组一个也不在默认根下，87 个全在 `mimd/` 之下，而工具只扫根与根下 `apps/` 一层 ⇒ 默认配置下 sweep 永远 0 组（不是权限、不是挂死，是够不到）。新增 `cfr.cgroup_root`（额外根，追加而非替换）+ 两个「平台自己怎么判缓存」的过滤器：`cfr.frozen_only`/`cfr.freezer_root`（冻结档，v1 内存组自带 freezer 节点时自答，否则按组名桥接到 v2 冻结树、只认 `uid_*` 不猜厂商组）与 `cfr.cached_only`（rank 档，`oom_score_adj >= 900` = AOSP 的 `CACHED_APP_MIN_ADJ`，每个任务都要满档）；工具侧 `--frozen-only` / `--freezer-root` / `--cached-only`，`--list` 点名被排除的组。**用户随后证明那批冻结是他外装的 LSPosed 插件**（连 adj=201/410 的可感知档应用一起冻，平台不会这么做）；关掉后平台自己的冻结档**没有归零而是收窄**：开机 2–6 分钟 0 个，17 分钟后稳定 1 个（`id.gms.unstable`，adj 945，连续 7 分钟不变）⇒ frozen 是 cached 的**真子集**（同一次 1 组 vs 14 组），rank 档才是第一趟就正确、覆盖更宽的那个（75 组中选 12–14 排 61–63，`cfr_reclaim_reclaimed` 0 → 29 448 页，含可见进程的对照组只动 0.2%/0.7%）；registry 未动，只 bump companion

## v0.30.1(zram writeback 崩溃修复；已落地；真机已复测)→ 详见 CHANGELOG.md#v0-30-1 — 往 `page_index=1` 写 `writeback` 把内核打挂：Batch 14 的 `zram_writeback_bounds` **无条件**覆盖了 PAGE 模式的单次边界，而 sweep 循环把 `nr_pages` 当**次数**用（不是 `index` 上界）⇒ index 跑到 `N + nr_pages − 1` 越界；修复是在范围检查之后恢复 PAGE 模式 `nr_pages = 1`，同批带 companion v0.10.0 的 writeback 触发器与第二条 SELinux 规则

## Batch 28(v0.31.1,已落地；已刷已复测；存活性修复版同版本已刷已复测)→ 详见 CHANGELOG.md#batch-28 — EEVDF **现代版本差异审计**（`docs/survey_eevdf_gap.md`，对 6.6 → 7.3 逐提交比对）落地 S/A 两级：`cfs_rq` 累加器让选择器 O(1)、deadline 刷新搬进 `update_curr()`、`RUN_TO_PARITY`、EEVDF 唤醒抢占、`PREEMPT_SHORT`、EEVDF yield、新任务放置；槽 4 由 Batch 16 的「退回 RESERVE」翻案重新认领 `u64 slice`（保留槽因此用尽 ⇒ 6.12+ 的 `min_slice`/`max_slice`/`vprot`/`sched_delayed` 记为超出边界）。随后的逐特性存活审计（44 agent）查出 4 处空实现且全为本批引入，按「补全而非删除」修完并真机复测；**未做性能 A/B，不主张提速**

## Batch 27(companion v0.11.0,已落地；归因待重测)→ 详见 CHANGELOG.md#batch-27 — `launch_boost` 不是内核特性（是小米用户态预取栈，且**当前完全没在运行**），故本批**不引入任何 `PatchGroup`**、不指名杠杆，只交付测量工装 `tools/abk_launch_bench.sh` + companion 接线，并把「单臂不再允许判 I/O 受限」钉进工具；重测前提是**亮屏且已解锁**，原始证据见 `research/launch/vermeer_launch_20260915/FINDINGS.md`

## Batch 26(v0.30.0,已落地；真机复测已完成)→ 详见 CHANGELOG.md#batch-26 — 修「Batch 21/25 在设备上没有节点可操作」这个真问题：AOSP lts 的 `gki_defconfig` 自带 `CONFIG_CMDLINE="… cgroup_disable=pressure"`，它同时关掉 per-cgroup 记账与全部 `CFTYPE_PRESSURE` 文件；新增第 4 档 `ABK_515_DEFCONFIG_PSI=1`（默认关）在 config lane 里去掉该 token。registry 未动，`module.conf` 0.29.0 → 0.30.0
## Batch 25(companion v0.9.0,已落地；两态 A/B 真机已做)→ 详见 CHANGELOG.md#batch-25 — Batch 21 的 `cgroup.pressure` 开关第一次被按下去：`tools/abk_psi_policy.sh`（`keep`/`auto`/`aggressive`，**只写 0 不写 1**）+ `tools/abk_psi_bench.sh`（两态 A/B 工装）；点名结果是 **`auto` 为零收益 no-op**，出厂默认仍 `keep`；registry 未动，`module.conf` 保持 0.29.0

## Batch 24(v0.29.0,已落地)→ 详见 CHANGELOG.md#batch-24 — 重压缩每趟上限 `max_pages`（`34efe1c3b688`）+ 拒绝无法识别的 `type=`（`2f529e73d720`），同步与异步两个节点一起；本模块第一个**改写别的组生成文本**的批次，故给 `zram_recompression`/`zram_async_recompress` 各加自身载荷探针（trap 5 解法）；companion v0.8.0 默认 `zram.recomp.max_pages=16384`

## Batch 23(v0.28.0,已落地)→ 详见 CHANGELOG.md#batch-23 — 修 CI 暴露的真问题：zram compressed-writeback 新增块放进 `CONFIG_ZRAM_WRITEBACK` 门内（配置关掉也能编译），并新增"配置门内符号引用"审计

## Batch 22(v0.27.0,已落地)→ 详见 CHANGELOG.md#batch-22 — PSI 内部同步收口：`TSK_ONCPU` 改为 state mask 的位（去掉 `NR_ONCPU` 计数与 `identical_state` 启发式），仍不引入 `psi_group::parent`

## Batch 21(v0.26.0,已落地)→ 详见 CHANGELOG.md#batch-21 — android14-6.1 的 per-cgroup PSI 开关（`cgroup.pressure`）：KMI 中性实现（cgroup `flags` 位承载状态，`struct psi_group`/`struct cgroup` 一字节不动），语义与上游逐条对齐；顺带补上「后置组改写前置组新增文本 → 前置组必须有探针」这条陷阱变体

## Batch 20(v0.25.0,已落地)→ 详见 CHANGELOG.md#batch-20 — 上游 5.15.y 剩余候选清账：`blk_mq_quiesced_elevator_switch`（`9646443f28f3`）与 `sched_steal_time_excess_drop`（`56135262c1f9`）落地，`64d9b734b6fe` 按 arm64 no-op 排除 —— 这条来源线至此无未结候选

## Batch 19(v0.24.0,已落地)→ 详见 CHANGELOG.md#batch-19 — 关闭 lts 行的两个降级组（kstack KABI 槽形态分支 + blk-mq suspend 的 already_present 探针），`KNOWN_DEBT` 清空：四档基线上不再有任何「已知降级」

## Batch 18(v0.23.0,已落地)→ 详见 CHANGELOG.md#batch-18 — zram writeback 的 Enforcing 策略缺口（companion 模块 sepolicy.rule）

## Batch 17(v0.22.0,已落地)→ 详见 CHANGELOG.md#batch-17 — zram writeback bio batching + compressed writeback（§9 在 Enforcing 下直接测到并发深度；**§10 记明本机 writeback 根本不会被触发**，这两个特性在这台设备上属于未被执行过的代码）

## Batch 16(v0.21.0,已落地)→ 详见 CHANGELOG.md#batch-16 — 空实现审计 + 清理

## Batch 15(v0.20.0,已落地)→ 详见 CHANGELOG.md#batch-15 — 撤销 ABK_ABI_PATCH_SUITE 红线 + 收编其优化特性

## Batch 14(v0.19.0,已落地)→ 详见 CHANGELOG.md#batch-14 — zram writeback 正确性补齐

## Batch 13(v0.18.0,已落地)→ 详见 CHANGELOG.md#batch-13 — `android_vh_customize_alloc_gfp` 挂点 + 高阶慢路径快速失败策略

## Batch 12(v0.15.0,已落地)→ 详见 CHANGELOG.md#batch-12 — 内核侧算法锁 + writeback 并存

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

- [x] `zram_writeback_limit_audit`（P3）— **Batch 14(v0.19.0) 已落地**（组名
  `zram_wb_limit_align`，原文见 CHANGELOG.md#batch-14）。完整证据、上游溯源与全部裁决见
  [`research/zram_writeback_plan.md`](research/zram_writeback_plan.md)，
  上游哈希复核结果见 `research/zram_wb_audit/verified_commits.tsv`。
- [x] `zram_recompress_max_pages`（P3）— **Batch 24(v0.29.0) 已落地**（组名同名，原文见
  CHANGELOG.md#batch-24 §3/§4，含「为什么它必须是注册在 `zram_recompression` 之后的独立组、
  以及两个前置组为什么各自要先加自身载荷探针」的 trap 4/5 依据）。上游锚点：
  `research/upstream-zram/zram_drv_master.c:2580` 解析 `max_pages`，
  而 `research/upstream-zram/zram_drv_linux-5.15.y.c` 与 android13-5.15 都没有。
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

## Batch 10-5(v0.17.0,已落地)→ 详见 CHANGELOG.md#batch-10-5 — smart-freq 与 FAS 的调频权收口

## Batch 10-6(v0.17.1,已落地)→ 详见 CHANGELOG.md#batch-10-6 — 超大核“不被调用”结案：上限持有者同时在决定放置（含交给用户在 Scene 侧的执行项与回归阈值）

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

## Batch 5(v0.6.0,已落地)→ 详见 CHANGELOG.md#batch-5 — 多 sublevel 兼容 167/.178/.194（含 `.211` 两条遗留阻塞，**已由 Batch 19(v0.24.0) 全部关闭**，原文见 CHANGELOG.md#batch-19）

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

- [-] `9909b088b1f0` + `59e88952a827`（mainline「mm/zsmalloc: reduce lock contention in zs_free()」
  v6 的 patch 1/2）：**排除（前提不存在）**。两条的目标是把现代树的 `read_lock(&pool->lock)`
  读侧去掉——patch 1 把 size_class 索引编码进 obj，patch 2 据此在 64 位下不再取 `pool->lock`。
  而 android13-5.15 的 `mm/zsmalloc.c` **没有 `pool->lock`**：167/178/194/211/lts 五条基线
  整文件 grep 均为 **0**，`zs_free()` 走 `pin_tag(handle)` → `obj_to_location()` →
  `get_zspage_mapping()` → `pool->size_class[class_idx]`。三套锁设计必须分清，否则会把
  「更早、更细的设计」误读成「已经优化过」：5.15/5.10 = per-zspage `migrate_read_lock()`
  + pin bit（最细）；6.1/6.6 = 单把 `spin_lock(&pool->lock)` 管整条 free/alloc 路径（最粗）；
  2026 那棵现代树 = 第三种（`pool->lock` rwlock 读侧 + `class->lock`）。patch 1/2 优化的是
  **第三种**。硬移植 patch 1 还要动 handle 编码，而 5.15 的 obj bit 0 是 `HANDLE_PIN_BIT`
  （迁移路径 `trypin_tag()` 取到后才改写 PFN），等于零收益纯风险。Batch 33 只取 patch 3。
  证据：`research/zsmalloc_lockfree/*.patch` + 本节 §2 的逐符号核对
- [-] 4edae3ff6d4e mark_victim tracepoint：AOSP 2024-11 树已自带
- [-] `1119609dce0875`（ACK：「EEVDF scheduling fail → 取 leftmost」）：**已覆盖**。选择器的 `if (!best)` leftmost 回退早在 `scripts/batch15_perf_eevdf.py:1138`（效果等价于它的 hunk 1），hunk 2 依赖 5.15 没有的 `se->sched_delayed`，而它那条 `printk_deferred` 照抄会误报（我们的 skip 判定在扫描**内**）；本次只补上缺的钉子。逐调用点核对与理由见 `docs/survey_eevdf_gap.md` §7.1
- [-] mm/kfence：5.15.y 无特性提交
- [-] timer_shutdown 全套 / NLM_F_BULK / PTP / netns defer free / dst 访问器改名 / hugetlb 系 / 纯重命名类：政策排除（见 survey）

## 6.1 来源线后续批次（backlog）

- [~] per-VMA locks（android14-6.1 全量移植；5.15 需 RCU VMA 生命周期 + fault 路径改造 + vma KABI 槽位，参照 rbtree 时代 RFC 设计）
  - **2026-09-14 可行性探针的三条结论与推荐推进方式已归档**（实测，Batch 21/22 后）：
    见 CHANGELOG.md#batch-22 §5。要点：**KMI 不是阻塞点**、**真正的工作量在写侧**
    （漏一处是静默的内存损坏，不是"降级"）、**6.1 的实现长在 maple tree 上不能直接搬**；
    建议先做**只读侧**，写侧 retouch 与新审计一起作为后续批次。
  - 结论：不是"能不能"的问题，而是**验证策略**的问题 —— 现有四道门禁里没有一道能证明
    "**没有漏掉写者**"，要先加一类新审计（枚举树里所有 VMA 变更点并与
    `vma_start_write()` 调用点做集合比对），属"多批次项目"，不是单批 bounded graft。
- [~] DAMON sysfs 控制面（实测需要 6.1 core 长大：`core.c` 27→46KB + sysfs
  约 10 万字节，不再是"中等体量"，价值一般）
- [x] per-cgroup PSI 开关（cgroup.pressure enable/disable）→ **Batch 21(v0.26.0) 已落地**
  （组名 `psi_cgroup_pressure_switch`）：卡点从来不是「cgroup KMI 红线」本身，而是
  **不能给 `struct psi_group` 加成员**（它内嵌在 `struct cgroup` 里），故用 cgroup 自己的
  `flags` 承载 `CGRP_PSI_DISABLED` 位，两个结构体一个字节不动。设计输入（Batch 20 后的
  探针结论）与落地时发现的 root/`psi_system` 面，见 CHANGELOG.md#batch-21 §1。
- [x] MADV_COLLAPSE（Batch 6 已落地，按 5.15 helper 重写，非 UAPI-only）
- [x] zram recompression（Batch 4 落地）+ zsmalloc chain-size（Batch 6 落地；
  6.2 来源、6.1.y 未收，来源线取 android15-6.6）
- [x] PSI 内部全量同步（NR_ONCPU 移除 / TSK_ONCPU 掩码 / 父链）→ **Batch 22(v0.27.0) 已落地**
  （组名 `psi_oncpu_state_mask`）：ONCPU 从"任务计数"改成 state mask 的一位，`NR_ONCPU` 与
  `tasks[]` 第 5 个计数一起消失，`identical_state` 启发式随之作废。**父链依旧不移植**
  （5.15 的 `iterate_groups()` 本来就是 cgroup 树走查）；6.1 同处的
  `lockdep_assert_rq_held()` **有意未移植** —— 三条设计点见 CHANGELOG.md#batch-22 §1-§3。

## 禁区清单（与 sibling 模块的硬边界）

**Batch 15 起 ABI 套件红线已撤销**（依据见 `docs/porting_policy.md` 的
"Suite absorption"）：套件的优化特性全部并入本模块，因此下面两条原红线作废。

- 本模块**自己**占用 `sched_entity` KABI 槽 1–4（EEVDF：
  `deadline`/`min_vruntime`/`vlag`/`slice`）与 `request_queue` 槽 1
  （`async_depth`）。槽 4 的归属改过两次：Batch 15 收编时认领、Batch 16 因「全树
  只写不读」退回 `ANDROID_KABI_RESERVE`、**Batch 28 重建 EEVDF 载荷后重新认领**
  （`se->slice` 有了真实读取点），于是保留槽用尽 —— 6.12+ 的
  `min_slice`/`max_slice`/`vprot`/`sched_delayed` 无槽可放，这是它们被记为
  「超出边界」的具体原因。同一构建**不得**再注入 ABK_ABI_PATCH_SUITE：套件无条件
  占 1–4，两个模块争同一槽位是 KMI 硬冲突，所以「本模块怎么用槽 4」都与共存无关。
- 原「不改写 ABI 套件硬失败组函数体」的限制作废：`alloc_pid()`、
  `pick_file()`/`__range_close()`、`select_idle_cpu()`、`pick_next_entity()`
  现在由本模块自己接管。
- 不在本模块内回滚/前向改写 `fs/f2fs`、`drivers/scsi/ufs`（F2FS 套件领地）
- 不引入 .patch 载荷；全部嫁接保持 anchor 脚本形态
