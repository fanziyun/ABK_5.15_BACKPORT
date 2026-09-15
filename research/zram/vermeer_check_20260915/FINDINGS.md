# swapcache 是否在堆积 — 真机诊断（vermeer / 5.15.216）

日期 2026-09-15，无线 adb（192.168.31.194 的 mDNS 端口，`adb-14d93093-lnmc4L`），
KernelSU root，模块 `abk_runtime_tunables` v0.9.2，内核 `5.15.216-android13-8-g5bfe2b8c1439`，
uptime 3.5h。ROM tier 已重编：`CONFIG_ZRAM_WRITEBACK=y` 现在是开着的。

**问题（用户原话）**：swapcache 是不是有问题、疑似堆积过多；卡在 75% 左右不上不下；
16GB 交换分区利用率特别低，只有 26%。

## 结论

**内核 swap cache 没有缺陷、没有无界堆积。** 它是**页面缓存**，不是泄漏：

- `nr_swapcached` 72,301 → 104,986 页（282MB → 410MB，12:20 → 13:11）。
- 关键在于分母：换出总量 4.46–4.70GiB，swap cache 只占其中的 **6%–9%**；占 RAM 的 2.6%。
- `mm/swap_state.c` 是 **stock 5.15**，且在 167/178/194/216 四棵树上字节相同
  （md5 `53f4fbd8766c`）。`add_to_swap_cache` 的 `+nr`（:134）与
  `__delete_from_swap_cache` 的 `-nr`（:173）配平，错误路径（`:120 goto unlock`、
  `:143-145`）不碰计数器 → **记账不可能漂移**。
- 11 分钟 61 次采样：在 297–328MB 窄带内震荡，净 −27 页 —— 那一窗口确实是平的。
  但拉到 50 分钟看，它在**缓慢上漂**（282→410MB）。所以「平」这个说法对 11 分钟成立，
  对更长时间不成立；见下文的机制解释。

## 那两个数字分别是什么

| 数字 | 实际量 | 值 |
|---|---|---|
| 75% | `(MemTotal − MemAvailable)/MemTotal` | `1 − 3752356/15560768` = **75.9%**；即系统「内存占用」 |
| 26% | `swap used / SwapTotal` | 13:11 实测 `4673792/16777212` = 27.9%；12:20 为 29.4%；用户报 26% 时约 4.2GiB |

**不是卡住**：90 秒窗口内 `pswpout +187761` 页（+733MB）、`pswpin +69975` 页（+273MB）。
那是突发；多条独立窗口测到的持续速率只有 **0.4–1.5MB/s**（全生命周期均值 1.15MB/s），
其中约 76% 的回收是文件页而非 anon。所以「交换分区不动」是**没压力**，不是坏了：
MemAvailable 4.1–4.3GiB、`/proc/pressure/memory` full avg10 ≈ 0.4%。

**如果你是从 `top` 读到「swapcache 堆积」的，那是 top 的显示错误**：它的
`Swap: … 3174M cached` 打印的是**文件页缓存 Cached**，不是 swap cache。实测 3/3 精确吻合
（Cached 2723M↔top 2723M，2736M↔top 2736M），同期真正的 `SwapCached` 只有 ~300MB。
`free` 没有这一列。

## 为什么它会缓慢上漂（值得知道的真实细节）

本机 **memory cgroup 是 v1**（`/proc/self/cgroup` = `4:memory:/apps`，控制器在 `/dev/memcg`）。
`mm/memcontrol.c` 的 `mem_cgroup_swap_full()`：

```c
	if (vm_swap_full())
		return true;
	if (cgroup_memory_noswap || !cgroup_subsys_on_dfl(memory_cgrp_subsys))
		return false;
```

v1 下 `cgroup_subsys_on_dfl()` 为假 → **直接 return false**；而 `vm_swap_full()` 要求 swap
过半，本机只有 26–30%，也为假。于是 `vmscan.c:1862-1866` 的主动
`try_to_free_swap(page)` **永不触发** —— swap cache 只能靠常规回收顺带释放，等于停在机制
允许的较高水位。这是**上游行为 + ROM 用 v1 cgroup**，不是本仓库的 graft 缺陷，量级也小。

一处**未能在仓库内证实**的前提：`SWP_SYNCHRONOUS_IO` 的置位在 `mm/swapfile.c`，
而该文件在四棵精简树里都不存在。只能间接佐证：`/sys/block/zram0/queue/rotational` = 0。
`mm/memory.c:3815` 确实测试这个 flag 并走「skip swapcache」快路（:3817-3843）。

## 真正值得注意的（都不是 swapcache）

1. **zram writeback 挂着但从不动**：`backing_dev=/dev/block/loop49`、`compressed_writeback=1`、
   `writeback_limit_enable=1`，但 `bd_stat` 恒为 `0 0 0`，模块日志从无 writeback 行。
   唯一触发方式是写 `/sys/block/zram0/writeback`（`zram_drv.c:626` writeback_store），
   而模块按设计**只 attach 不触发**（README 原则 2 / tunables.conf:46-58），ROM 的 `mmd`
   又是 disabled、`vendor.zram.disable=1`。**后果：那 6GiB 磁盘后备完全没用上，
   swap 全部留在 RAM 里。**
2. **被 preserve 的 writeback_limit 是 ROM 的值**：2752512 blocks（4KiB 单位，已验证
   `zram_drv.c:777`）= 10.5GiB，来源 `persist.miui.extm.daily_flush_count`（精确相等）。
   loop49 是 ROM 的 extm（`/data/extm/extm_file`，6GiB），**不是**模块自己的
   `/data/per_boot/zram/zram_swap`（该目录是空的）。limit(10.5GiB) > 后备(6GiB)，
   真跑起来会先撞 `-ENOSPC`（`zram_drv.c:688`），所以这个「闪存磨损上限」约束不住。
   模块自己的公式 `size_mb*256`（zram-policy.sh:221/224）= 262144 blocks = 1GiB 是对的，
   只是这次它选择了 preserve 而不是新建。
3. **重压缩/整理在正常跑**：sweep 每 ~30 分钟一次（09:17/09:49/10:21/10:52/11:22/11:53/12:23），
   `zram_recompd` 内核线程存活；`pages_compacted` 主要来自 zsmalloc shrinker。
4. zram 真实内存代价 1.67–1.88GB / 上限 3.98GB（25% RAM）= **42–47%**，压缩比 ~2.9:1，
   离上限还远。与 09-11 的结论一致：看 `mm_stat` f3（物理代价），不要看槽位用量。
5. 非内存项：`crtc_commit:167` 曾短暂 D 态、dmesg 显示它占 7–9% 内核 CPU —— 是显示/DRM
   commit 线程，与回收无关。ROM 自己的 `low_free_memory_kill(CE)` 也加载着。

## 复检命令（只读，单条）

```bash
adb shell 'su -c "echo ---SWAPS---; cat /proc/swaps; echo ---MEM---; grep -E \"^MemTotal|^MemAvailable|^SwapCached|^AnonPages\" /proc/meminfo; echo ---VMSTAT---; grep -E \"^nr_swapcached |^pswpout |^pswpin \" /proc/vmstat; echo ---ZRAM_mmstat---; cat /sys/block/zram0/mm_stat; echo ---BD_STAT---; cat /sys/block/zram0/bd_stat; echo ---PSI---; cat /proc/pressure/memory"'
```

判读：`nr_swapcached×4KiB` 就是 swap cache 字节数。**要警惕的是比值不是绝对值**——
`SwapCached / swap_used` 若长期 >30% 且持续单调上升，才值得查；目前 6–9%。
`bd_stat` 若一直是 `0 0 0`，说明 writeback 依然一次都没跑过。

## 若要压小 swap cache（可选，非修复）

唯一有效杠杆是**少换出**：ROM 把 `vm.swappiness` 设为 100、memcg apps/system=100。
模块已留了入口（`tunables.conf` 的 `vm.swappiness=`，当前为空 = 不接管），设为 60 会显著
减少 anon 换出、从而压小 swap cache；代价是后台驻留/秒开变差 —— HyperOS 本意就是内存扩展。

## 证据来源

4 个并行 recon 视角 + 12 个对抗性验证 agent（Workflow `swapcache-device-diagnosis`）。
其中 3 条结论 STANDS（75%/26% 都正常），另 1 条关于「staging 是正常稳态」的表述被
[medium] 反驳 —— 反驳理由即本文「为什么它会缓慢上漂」一节，已采纳。
