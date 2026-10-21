# swap cache 积压：现象、归因与三组内核策略（Batch 72–74）

本文记录 `android13-5.15-lts`（5.15.220）上一条完整的因果链：**延迟换出（kcompressd
offload）使换出页在写回期间被回收路径放弃，页与其 swap 槽一起滞留在 swap cache**；
以及本轮落地的三组 graft（Batch 72–74）与伴生模块改动如何把它收敛，附真机数据与复现口径。

## 1. 现象

- 设备日常使用后 `SwapCached` 稳态停在 **0.6–1.1 GB**（曾读到 662 MB 的单点），
  且 `SwapFree` 足够、`pswpout` 已停止增长——不是"换不出去"，而是"换出去了没摘"。
- `kcompressd` 三计数（`enqueued`/`swapped`/`sync`）显示队列深度 `enqueued - swapped`
  长期为 0：offload 线程跟得上，堆积不在队列里。
- 关掉 offload（`/proc/sys/vm/kcompressd=0`）后同样负载下 `SwapCached` 峰值≈起始值、
  结束回到几十 MB——滞留由 offload 的"异步完成"语义产生。

## 2. 归因

`shrink_page_list()` 的 `case PAGE_SUCCESS:` 在对匿名页调用 `pageout()` 之后检查
`PageWriteback(page)`；Batch 41 的 offload 把首次换出压缩搬到 per-node 线程，入队后
`pageout()` 立即返回、页仍带 `PG_writeback`，于是走 **pristine 的 `goto keep`**：页被放回
LRU，swap 缓存项保持分配。写回完成后 PG_writeback 清除，但**只有下一次回收扫描**才会
真正 `__remove_mapping()`；kswapd 满足水位即停止扫描，积压由此留在原地。

现场细分（`/proc/kpageflags`：`KPF_SWAPCACHE`/`KPF_WRITEBACK`/`KPF_ACTIVE`/`KPF_LRU`）：
fill 平台期 `swapcache=613,619` 页里 **`writeback=0`、`unmapped=596,838`（97%）**、
`inactive=596,838`。也就是说这些页早已写完、干净、可回收，只是没有扫描去摘它们。

## 3. Batch 72：`kcompressd_swapcache_drop`（core）

三处 `mm/vmscan.c` 编辑，标记 `sailboat_kcompressd_swapcache_drop`：

1. `shrink_page_list()` 栈上新增 `LIST_HEAD(wb_retry_pages)` 与一次性闩
   `bool wb_retry_done = false`。
2. `case PAGE_SUCCESS:` 的 `PageWriteback(page)` 分支里，在 `#ifdef CONFIG_SWAP` 内匹配
   `!wb_retry_done && PageSwapCache(page) && (page_swap_info(page)->flags & SWP_SYNCHRONOUS_IO)`
   时把页挂到 `wb_retry_pages` 并 `continue`（不 `goto keep`）；其余情况保持 pristine。
3. `/* 'page_list' is always empty here */` 之后、demote 之前插入一次重扫：
   对仍 `PageWriteback` 的页 `wait_on_page_writeback()`；**当本次回收运行在 drain 自身
   上下文（`PF_KSWAPD` 且无 `PF_SWAPWRITE`）时跳过等待**，退回旧 prefilter
   （`list_move` 到 `ret_pages`），避免 drain 在自己的 direct reclaim 里等自己；
   wait 返回后重读 `PageWriteback`，仍置位同样回退；其余页 `list_splice_tail()` 回
   `page_list`、`do_demote_pass = false`、`goto retry`。`wb_retry_done` 保证只做一次，
   循环必然终止。

设计边界：只针对同步 IO 的 swap 设备（`SWP_SYNCHRONOUS_IO`，zram 命中）；异步块设备
保持 pristine 的"稍后再看"。移除仍走内核自己的 `__remove_mapping()`，本组只改变"何时
重扫"；无新锁、无分配；等待只作用于本模块入队的页，上界是该 FIFO（≤256 页）的排空时间。
`page_swap_info()` 只在 `CONFIG_SWAP` 下存在，引用处带同名门。

## 4. Batch 73：`swapin_readahead_window`（core）

六步，标记 `sailboat_swapin_readahead_window`：`mm/readahead.c` 加
`#include <linux/moduleparam.h>` 与文件末尾策略块；`mm/swap_state.c` 去
`static enable_vma_readahead`、在 `__swapin_nr_pages()` 与 `swapin_nr_pages()` 的窗口
计算终值处各插一次 clamp；`mm/internal.h` 加两条 extern。整块在 `#ifdef CONFIG_SWAP` 内。

参数（默认值，`0644`，`/sys/module/readahead/parameters/`）：

| 参数 | 默认 | 语义 |
|---|---|---|
| `swapin_window_enable` | 1 | 总开关；0 返回内核原值 |
| `swapin_boost_enable` | 1 | 打开"地板"抬升 |
| `swapin_boost_pages` | 8 | 地板值，向上取 2 的幂 |
| `swapin_shrink_enable` | 1 | 打开"天花板"收缩 |
| `swapin_shrink_pages` | 1 | 天花板值，向下取 2 的幂 |
| `swapin_window_background_only` | 1 | 收缩只作用于后台任务 |
| `swapin_vma_ra` | 1 | 内核 `enable_vma_readahead` 的第二句柄 |

语义要点：**boost 是地板不是上限**——窗口比地板小就被抬到地板，默认 8 在
`page_cluster=3` 下把共享页的换入窗口从自适应的 1–4 页抬到 8 页；收缩侧默认 1 页、
且只在 Batch 9-1 的后台分类器（`CONFIG_ABK_DYNAMIC_READAHEAD` 内）判定为后台时生效，
分类器未编入时门保持关闭（不误伤前台）。窗口强制 2 的幂（cluster 路径以 `pages-1`
作对齐掩码），boost 取幂前夹到 `1 << SWAP_RA_ORDER_CEILING`（32），shrink 0 视作 1；
`swapin_vma_ra` 与 `/sys/kernel/mm/swap/vma_ra_enabled` 写同一变量，二者不会分叉。

## 5. Batch 74：`fault_around_app_policy`（core）

两步，标记 `sailboat_fault_around_app_policy`，只改 `mm/memory.c`：

1. `do_read_fault()` 上方插入策略块：90,000 位位图（`DIV_ROUND_UP(90000, BITS_PER_LONG)`，
   约 11.25 KiB `.bss`）、写侧 mutex、读侧 lockless `test_bit`、四个参数
   `fault_around_enable`（默认 1）、`fault_around_sys_uid`（1）、`fault_around_app_only`
   （0）、`fault_around_app`（自定义 set/get：写 0 清空、写 uid∈[10000,99999] 置位、
   读回前 64 个逗号分隔）。
2. vendor hook `trace_android_vh_should_fault_around(vmf, &should_around)` 与它喂的
   `if` 之间插入 `abk_fa_adjust(&should_around);`。**hook 行本身一个字节不改**，因此
   同点位的其它 handler probe 仍命中，注册顺序无约束。

判定（在 hook 给出答案之后，只收紧不放松）：`enable` 关 → 不动；`uid < 10000` 且
`sys_uid` → 抑制；`uid > 99999` → 不动；否则 `app_only=0` 且位图置位 → 抑制
（`app_only=1` 表示完全不查列表）。`from_kuid(&init_user_ns, current_uid())` 的
`(uid_t)-1` 由 `uid > 99999` 早退封住。默认配置下**只有系统 uid 失去 fault-around**，
app 行为不变，除非把 uid 写进列表。

## 6. 伴生模块（v0.23.0）

`ksu/abk_runtime_tunables`：

- `kcompressd` 观测：`abk_kcompressd_state_line()` 读阈值与三计数并给出
  `depth = enqueued - swapped`（`sync` 是未入队量，不参与），supervisor 每个
  `kcompressd.monitor_interval_sec`（默认 600 s）打一行；稳态 tick 静默。
- 两条不变量告警：连续两次 `depth <= threshold && SwapCached >= swapcached_warn_mb`
  = "写完未摘"形状；`depth >= threshold` = 队列饱和（head-drain 点）。
- 策略接管：新增 `fault_around.{enable,sys_uid,app_only,app_uids}` 与
  `swapin.{window_enable,boost_enable,boost_pages,shrink_enable,shrink_pages,background_only,vma_ra}`
  与 `policy.reassert_interval_sec`（默认 300 s）。开机 apply、supervisor 周期重断言，
  **只在读回值与期望不一致时写并记一行**；bool 统一归一为内核读回的 `Y/N`；
  `app_uids` 去重、限 64、0 只用于清空；空字符串表示"清空列表"。
- 12 个键成为单一事实来源：修改内核默认值必须同步 `tunables.conf`，否则被重断言改回。

## 7. 真机证据（SM8550 / 5.15.220，offload=24）

| 场景 | 改前 | 改后 |
|---|---|---|
| 8 GB fill 的 `SwapCached` 峰值 / 结束 | 3.0–3.3 GB / 0.28–0.40 GB | 7 MB / 7 MB |
| 连续两轮 fill 后 | 累积上升 | 7.6 MB，稳定 |
| 1 小时 soak（无线 adb，换出 10.1 GB） | — | 峰值 60 MB、60/60 采样 <100 MB、`depth` 全程 0 |
| 16 GB 并发压力 | — | 存活，峰值 70 MB |
| 24 GB 并发压力（容量内） | — | 存活，峰值 8–82 MB，`depth` 瞬时 2.9k–7.2k 页后回 0 |
| 32 GB 并发压力（超 RAM+swap） | — | 重启；offload 开/关均复现，无 kernel panic（`init` SIGABRT、`crash_dump` 无法 exec、温度 92–96 °C） |
| `verify_swap` | — | 655,360 页 0 corrupt（含 12 GB 持压下 131,072 页 0 corrupt） |

结论：容量内应用的滞留问题是 offload 语义造成的，Batch 72 把它收敛到 MB 级；32 GB
重启是设备容量/热极限，与 offload 开关无关，也不是内核 panic。

## 8. 复现与验证

```bash
# 构建（本仓库的 ABK 模块注入）
bash setup.sh                     # after_patch；随后按 ABK 流程编译

# 门禁
python3 -m py_compile scripts/*.py tests/*.py
bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh ksu/*/*.sh
python3 tests/stable_5_15_test.py
python3 tests/step_audit.py <tree>
python3 tests/implementation_audit.py <tree>
bash tests/smoke.sh <tree>
```

真机侧：写 `/proc/sys/vm/kcompressd` 选 offload 开关，用
`/proc/meminfo` 的 `SwapCached` 与 `/proc/vmstat` 的 `pswpout` 观察滞留；逐页指纹校验见
独立工具 `verify_swap`（本次基线 5×512 MB = 655,360 页）。

## 9. 风险与开关

- Batch 72 的等待依赖 drain 线程存活；已用 drain 上下文判别避免自等待，32 GB 过载下
  也未出现 hung task。若某类设备上 drain 被长期饿死，把 `kcompressd` 设 0 即回到同步
  换出（零重编）。
- 仅同步 IO swap 设备受益；异步设备保持内核原语义。
- `fault_around_sys_uid=1` 是相对基线的真实行为变化（系统 uid 失去 fault-around），
  默认值应按设备 A/B 定稿；`swapin_boost_pages=8` 同理（它抬升的是共享页换入窗口）。
- 伴生模块的 12 个键与内核默认值必须同步，见 §6。
