# Batch 8 — long-term performance backport pool

状态：`pagealloc_fallback_reuse` 和 `rcu_nocb_cpu_default_all` 的源码组已实现并
注册；RCU 的产品默认开关仍按设备 benchmark 决定，其余项目仍按条件、构建工程
或独立 MM/VFS 分支处理。Batch 8 的源码项目只有完成 5.15 形状审计、实现、矩阵
测试和 CI 编译后，才注册为正式 `PatchGroup`。

范围：对照 Android Common `android15-6.6` / `android16-6.12`，筛选对
android13-5.15 有实际性能收益、且值得承担长期维护成本的项目。安全修复、
纯观测能力和没有产品消费者的 vendor hook 不纳入。

## A. 当前模块优先项

### [x] `pagealloc_fallback_reuse` — P1

来源：6.6/6.12 的 `mm/page_alloc` fallback 系列，核心包括：

- `rmqueue_bulk()` 记住上一次成功的 fallback mode，避免在 zone lock 内逐页
  重复搜索 fallback；
- `find_suitable_fallback()` 的 claimability 判断清理及 compaction 配套调整。

Android 的 backport 记录了 `vm-scalability::lru-file-mmap-read` 吞吐约 31.6%
提升，最坏 zone-lock 时间从约 280ms 降到 8ms。5.15 的
`find_suitable_fallback()`、`__rmqueue_fallback()` 和迁移类型接口较旧，已按
5.15 形状适配。实现边界在 `mm/page_alloc.c`、`mm/compaction.c` 和
`mm/internal.h`：保留 AOSP vendor hooks 与 `steal_suitable_fallback()` 契约，
拆分 claim/steal 阶段并在 `rmqueue_bulk()` 持锁批处理中复用阶段状态。

验证门槛：zone-lock/irq-off 时间、文件 mmap 压力吞吐、page-fault latency、
低内存 cold launch；若目标设备没有 allocator contention，降为条件项目。当前
代码级门槛已由三档 AOSP baseline 的 step/implementation/smoke 审计覆盖；设备
收益仍需单独实测。

参考：

- https://android.googlesource.com/kernel/common/%2B/e8400a074123812e1488ec1fe55666b3e2eead17
- https://android.googlesource.com/kernel/common/%2B/refs/tags/android15-6.6-2025-06_r7%5E!/

### [x] `rcu_nocb_cpu_default_all` — P1（源码已落地，产品启用条件）

6.12 GKI 默认让启用 `RCU_NOCB_CPU` 的 CPU 全部 offload callback；5.15 已有
RCU lazy 基础，但没有该默认策略。它可能降低轻负载/idle 的 RCU 干扰和功耗，
但收益强依赖 CPU topology、callback rate 和调度策略，不应只凭配置差异落地。

源码组已增加 `CONFIG_RCU_NOCB_CPU_DEFAULT_ALL`、启动参数优先级说明和
5.15 `cpumask_available(rcu_nocb_mask)` 形状适配；Kconfig 默认仍为 `n`，因此
不会在缺少设备数据时改变现有产品行为。产品开启前仍需验证 idle power、RCU
callback backlog、wakeup latency 和前后台切换。

参考：https://lkml.indiana.edu/hypermail/linux/kernel/2206.2/04927.html

### [ ] `autofdo_515_profile` — P1（构建工程，不是源码 graft）

Android 官方 AutoFDO profile 在 Pixel 的 boot、cold app launch、Binder 等路径
观察到可见收益。Batch 8 保留该项目，是因为它可能比源码 backport 更快产生整体
收益；但必须针对精确的 5.15 内核、工具链、编译参数和目标设备重新采集 profile，
禁止把 6.6/6.12 profile 直接套到 5.15。

验证门槛：同一设备、同一 workload 的 boot、cold/warm launch、Binder、功耗和
镜像体积 A/B；产物不注册到 `stable_backport_core` 或 `stable_perf_backport`。

参考：https://android.googlesource.com/kernel/common/+/refs/heads/android-mainline/gki/aarch64/afdo

## B. 独立长期 MM/VFS 分支

### [~] `mglru_612_refresh` — P2

不是单独 cherry-pick 一个 MGLRU hunk，而是完整刷新 6.12 的 aging feedback、
workingset、refault detection、type selection、deactivation 以及大 folio 相关
路径。它最可能改善内存压力下的 reclaim stall、应用停顿和响应性，但 5.15 的
MGLRU 仍是早期 page-based 形态，6.12 已深度 folio 化。

建议先做 `mm/swap.c` deactivation 的独立实验，再决定是否继续完整系列；不进入
当前 bounded anchor graft，需独立分支和低内存 Android workload。

参考：https://android.googlesource.com/kernel/prebuilts/6.12/x86-64/%2B/ac78f37f4676278bb0256f92715d1e598a29f67a

### [~] `large_folio_mthp_substrate` — P2

建立 file-backed large folio / mTHP 的共同基础设施：mapping folio-order、
page cache、readahead、THP、rmap、migration 以及 VFS/XArray 配套。mTHP 能减少
page fault 次数和 latency spikes，长期收益覆盖启动、文件读取和内存压力场景。

这是基础设施项目，不应先单独移植 F2FS 或 EROFS 的调用点。完成基础设施后，
再由对应 sibling suite 接文件系统适配。

参考：

- https://android.googlesource.com/kernel/common/+/b5a24181e461e8bfa8cdf35e1804679dc1bebcdd/Documentation/admin-guide/mm/transhuge.rst
- https://android.googlesource.com/kernel/common/%2B/419f09a5afbe2e0b7ead64aafa74121507916772/Documentation/filesystems/iomap/operations.rst

### [~] `maple_tree_per_vma_lock` — P3

per-VMA lock 对高线程 mmap 竞争有明确收益，但 5.15 没有 `vma_lock`，需要同时
引入 Maple Tree、VMA RCU 生命周期、fault path 转换和 `vm_area_struct` KABI
处理。它是高收益、高风险的完整内存管理重构，不符合当前模块的 KMI 红线和
bounded graft 定位。

参考：

- https://source.android.com/docs/core/architecture/kernel/release-notes?authuser=108
- https://origin.kernel.org/doc/html/latest/core-api/maple_tree.html

## C. 值得做但属于 sibling suite

### [~] `f2fs_readonly_large_folio` — P2（F2FS suite）

F2FS 只读、不可变文件的大 folio 读路径有明确性能收益，但依赖 large-folio
基础设施，且当前模块明确不改写 `fs/f2fs`。

参考：https://www.kernel.org/doc./html/next/filesystems/f2fs.html

### [ ] `f2fs_lookup_mode_perf` — P1/P2（F2FS suite）

casefold 目录查找发生线性 fallback 时可能产生严重性能回退；6.6 增加的
`lookup_mode=perf` 使用 hash-only 路径，投入小于完整 large-folio 项目。适合有
大量 casefold 目录的产品优先验证。

参考：https://android.googlesource.com/kernel/common/%2B/refs/tags/android15-6.6-2025-07_r15

### [ ] `ufs_command_priority_rt` — P2（UFS/storage suite）

UFS Command Priority 可让实时请求优先于普通请求执行，目标是降低前台应用 I/O
latency；收益依赖 UFS 控制器和设备是否支持，当前模块不改写
`drivers/scsi/ufs`。

参考：https://android.googlesource.com/kernel/common/%2B/7eb4d8ceda46fab6d7c0537e9b9d7bc36c8fe9cd%5E%21/

### [~] `erofs_large_folio_zstd` — P2/P3（EROFS suite，条件）

large folio 适合 EROFS 只读读取；ZSTD 则是用 CPU 换压缩率，只有系统镜像实际
使用 ZSTD 且 CPU 预算允许时才值得做。

## D. 暂不进入 Batch 8 实现池

- `SCHED_CLASS_EXT` / proxy execution：框架或实验性调度能力，缺少通用收益证据，
  且涉及 scheduler/KMI/锁语义；
- `MEM_ALLOC_PROFILING`、BPF 相关新增：主要是观测和调试能力；
- io_uring 新增能力、EEVDF：ABK ABI suite 领地或需要更大基础设施；
- DAMON VADDR/SYSFS：5.15 已有 DAMON reclaim，没有用户态策略消费者时收益有限；
- 没有机制消费者的 Android MM vendor hooks；
- 通用 BBR 更新：未发现 6.6/6.12 中足以支撑独立 5.15 backport 的新性能增量。

## Batch 8 执行顺序

1. AutoFDO 先建立整机收益基线。
2. `pagealloc_fallback_reuse` 和 `rcu_nocb_cpu_default_all` 源码组已落地；RCU
   只在目标设备 benchmark 通过后把 defconfig 开关改为 `y`。
3. 另开 MM 分支推进 `mglru_612_refresh`，再评估 `large_folio_mthp_substrate`。
4. F2FS/UFS/EROFS 项目留在各自 sibling suite，不注册到本模块。

`pagealloc_fallback_reuse` 落地后递增 `ABK_MODULE_VERSION`；后续项目仍遵循
`docs/group_recipe.md` 的 group、矩阵、幂等、回滚和 CI 编译要求。
