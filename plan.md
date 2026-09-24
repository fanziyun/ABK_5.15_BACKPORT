# plan.md — living backlog

状态词：`[ ]` 候选 / `[~]` 延后（需更大 rebase）/ `[x]` 已落地 / `[-]` 无收获或按政策排除。
每批次落地后在 `module.conf` 递增 `ABK_MODULE_VERSION`。**只有 companion 的批次例外**：只 bump
`ksu/*/module.prop`（先例 `a50df6e`，Batch 25 同），否则单测里钉的内核版本对不上号。
已落地批次的完整原文（政策变更说明、落地明细表、调试/试错记录、验证结果、审计基线）已归档到 [`CHANGELOG.md`](CHANGELOG.md)，按 Batch 倒序排列；本文件里每个已落地批次只保留一行索引。

## 交付总览（九项优化，按功能清单顺序）→ 详见 CHANGELOG.md#overview-nine — 把九项功能重排成一份交付日志并往下续写第 10 项（`8a73e95` → HEAD 的四个提交）：逐项给出批次、组名与到手证据，不新增任何批次


## Batch 38(v0.43.0,已落地)→ 详见 CHANGELOG.md#batch-38 — 主题「Linux 7.2 MM/Reclaim 筛选后首批落地」，来源 `docs/survey_7_2_mm_reclaim.md`（v7.2 触及 `mm/` 的 commit 共 366 条，用 tag 可达性而非 committer-date 窗口统计）。落地 **6 组**（全 core，core 57 → 63 组）：① `huge_memory_imap_split_uaf`（`e923bd21058e`/5.15.y `f87c08060818`）：`__split_huge_page()` 在释放 after-split 子页**之前**先解 `i_mmap_lock`，堵住并发 `evict()`/`iput()` 把 inode 释放掉之后的 use-after-free；三条 required 步骤，anon 路径 `mapping==NULL` 天然跳过，file 路径调用点随即 `mapping = NULL` 防 `out_unlock` 二次解锁；**lts(216) 已含此补丁 ⇒ PRE_APPLIED**。② `swap_readahead_lru_add_drain`（`a4519e5b648a`）：删掉 swap-in readahead 尾部多余的 `lru_add_drain()`（lru-drain 系列五条里唯一有 5.15 载体的），页面经调用方 per-CPU pagevec 自然入 LRU，省掉每次 swap-in 缺页一次 `lruvec_lock`。③ `vmscan_tasks_rcu_qs`（`25f52e812168`/5.15.y `4cdc1bdf4094`）：`shrink_lruvec()` 扫描循环改报 Tasks-RCU 静止状态；真机核查（vermeer，`5.15.216-...-pr22-4152336`）后**更正**：`/proc/config.gz` 给出 `CONFIG_TASKS_RCU=y` + `CONFIG_TASKS_TRACE_RCU=y`，`call_rcu_tasks`/`synchronize_rcu_tasks` 是真实全局符号，所以 classic holdout 清除确实编进去了——**本组在这台设备上有真实作用**，初版「编译后等价、不主张收益」的说法是错的（方向是少报而非多报）；仍未测速，不主张具体数字。④ `filemap_mmap_miss_tried`（`9b0fcac3cfe7`）：`filemap_map_pages()` 不再把 `FAULT_FLAG_TRIED` 重试计成 mmap hit，修掉 `mmap_miss` 长期低于递增侧导致随机 mmap 预读关不掉的病征（上游随机访问预读 I/O 降约 21×，x86 服务器数据）；与 Batch 30 的 `readahead_mmap_miss_race` 同一条记账线的延续。⑤ `memcg_dying_bailout`（`0beeaf14e7b9`/`e13f634f50d5`/`757dd8193f6c`/`10228e0a5123`）：`memory.high`/`memory.max`/`memory.reclaim`/memcg-v1 四个同步回收写入口在 memcg 已 dying 时提前退出，堵住 `cgroup_rmdir()` 持 `cgroup_mutex` 等在 `kernfs_drain()` 造成的整机卡顿（上游实测 159 s `cgdelete`、182 s 无关读者）；5.15 无 `memcg_is_dying()`，按上游位置补进 `include/linux/memcontrol.h`（CONFIG_MEMCG 块 + `#else` 存根），memcg-v1 两处按 5.15 布局重锚到 `mm/memcontrol.c`；主动回收那条**必须插在 Batch 37 `proactive_reclaim_suspend_abort` 的 `new` 块之后**，插在中间会劈坏该组的幂等探针（trap 5，`step_audit` 第二遍断言抓到）。⑥ `buddyinfo_nolock`（`aaa98b100ea8`）：读 `/proc/buddyinfo` 不再取每个 zone 的 `spin_lock_irqsave`——打印回调只读 `zone->free_area[order].nr_free`，而 lmkd/dumpsys 与厂商碎片监控恰好在分配器最热时轮询该文件；`walk_zones_in_node()` 第四参即 `nolock`，5.15 树上 `pagetypeinfo_showmixedcount()` 已有 `nolock=true` 先例，需要锁的同族回调（`pagetypeinfo_showfree_print`）保持 `false` 不动。**顺带修掉两个既有 fixture/断言缺口**：`tests/fetch_sublevel_tree.sh`+`tests/smoke.sh` 补 `drivers/of/address.c`（overlay 无条件要求该文件存在，新取的树必炸 smoke）、`tests/smoke.sh` 删掉 v0.42.0 移除 `madvise_pt_reclaim`/`madvise_batch_tlb_flush` 后残留的六条陈旧断言。**补上 `tests/implementation_audit.py` 的行为门禁**（recipe §3 要求、本批初版漏了）：六组全部进 `REQUIRED_IN_FUNCTION`，`memcg_dying_bailout` 另在 `REQUIRED_CONTENT` 钉住 `memcg_is_dying()` 的 CONFIG_MEMCG 体与 `#else` 存根；已做反向验证（把组 4 的锚点改回错误的那个 decrement，审计报 missing/forbidden 并退出 1）。**本地 code review 抓到的真问题**：survey 初版把 5.15 的 `mmap_miss` 递减站点写错了行号与函数名（`mm/filemap.c:3119-3125` 属于 `filemap_fault()`，不是 `filemap_map_pages()`），第一版 graft 因此把 guard 加到了错误的 decrement 上；已改到 `filemap_map_pages()` 的 per-PTE 递减并用 `must_not_have` 钉住。**已知局限：只跑了 194 一档**，167/178/216 未取树。**MGLRU 改为默认开启**（用户要求「该 PR 的优化全部默认开启」）：`LRU_GEN_ENABLED` 从 `_ALIGN_CONFIGS` 挪进 `_MODULE_CONFIGS`，`/sys/kernel/mm/lru_gen/enabled` 开机即非零。理由不是偏好而是正确性：基线本来就 `CONFIG_LRU_GEN=y`，MGLRU 编得进去，但这个符号决定 `mm/vmscan.c` 里 `lru_gen_caps` 是 `DEFINE_STATIC_KEY_ARRAY_TRUE` 还是 `_FALSE`——不设它 MGLRU 每条分支初始为假、跑的是经典 LRU。真机实测（vermeer，本 PR 构建）`LRU_GEN=y` / `LRU_GEN_ENABLED` 未设 / `enabled=0x0000`。**后果：Batch 37 落地的 6 个 MGLRU 组（v6.14 系列）此前一直运行时空转，现在才第一次真正生效**——与 Batch 8 RCU graft 编出去是同一类失败，只是低一层 config。新增 `tests/stable_5_15_test.py::test_mglru_is_enabled_by_the_default_tier` 钉住该放置（反向验证：挪回 align 层 → 6 个检查失败），`_INTRODUCED_KCONFIG` 同步登记。**仍未落地**：v7.2 的 12 条 MGLRU 回收循环重写，所以打开后跑的是 6.1 最小实现 + v6.14 那 6 组，不是 v7.2 那版。其余 opt-in 层（BBR/BLK_WBT/BLK_DEV_THROTTLING/TASK_DELAY_ACCT、per-cgroup PSI、`abk_sf_enable`、伴生 `zram.writeback.trigger`）按用户划定范围**不动**。**未做设备 A/B，不主张提速**。落地脚本 `scripts/batch38_core_mm_safety_perf.py`
## Batch 41(v0.46.0,已落地；六道门禁四档实测全绿；**ABK CI 首轮编译失败已修**（`mm/page_io.o` 四个 error 同源成两个 C 级缺陷：引擎 append 在 `swap_writepage()` 之后导致 `abk_kcompressd_store` 缺前向声明；数组与线程函数同名不同类符号 ⇒ 数组改名 `abk_kcompressd_nodes`。文本审计对此类一律瞎眼，见 CHANGELOG.md#batch-41 §14），复跑待推)→ 详见 [CHANGELOG.md#batch-41](#batch-41) — 主题「kcompressd：把 kswapd 的首次换出压缩搬出回收线程」，来源是 [firelzrd/kcompressd-unofficial](https://github.com/firelzrd/kcompressd-unofficial) 0.5（Masahito Suzuki fork 自 MediaTek Qun-Wei Lin 的 Kcompressd）。**上游没有 5.15 版 patch**，只发行 6.12.44 / 6.18 / 7.1-rc1 三份 ⇒ 本批是**改写载体（re-carriage）而不是回移（backport）**，理由已查实：上游往 `struct pglist_data` 加 4 个字段（`kcompressd_wait`/`kcompressd`/`kcompress_fifo`/`kcompress_fifo_lock`），而 `android/abi_gki_aarch64.xml` 把该结构**完整钉死**（`size-in-bits=56320`、22 个成员各带 `layout-offset-in-bits`），且它**没有 `ANDROID_KABI_RESERVE` 运行段**（4 个空槽在 `struct zone`），唯一看着空着的 `ANDROID_OEM_DATA(1)` 是 `#ifdef CONFIG_ANDROID_VENDOR_OEM_DATA` 下的单个 `u64` 且归 OEM 私有 ⇒ 加字段必改 offset 或 size = KMI 破坏。于是整包落 `mm/page_io.c` 一个文件：pglist_data 4 字段 → 模块私有 `static struct abk_kcompressd_node abk_kcompressd_nodes[MAX_NUMNODES]`（BSS 零初始化、`page_to_nid()` 索引；arm64 `NODES_SHIFT` 4 ⇒ NUMA 开 16 项 / 关 1 项，`include/linux/numa.h` 在 5.15 还是 `CONFIG_NODES_SHIFT` 老形状）；kswapd_run()/kswapd_stop() 建拆线程 → 一个 `late_initcall`（**`mm/vmscan.c` 一行不碰**，顺带消掉上游 `kswapd_run()` 在本树的陷阱——它是 `void`、无 `__meminit`、无 `pgdat_kswapd_lock()`，且 `trace_android_vh_kswapd_per_node()` 可能提前 return）；`kernel/sysctl.c` 的 `vm_table[]` 项 → `register_sysctl("vm", ...)`；`include/linux/swap.h` 的 extern → 文件内 `static`。core 65 → 66 组。**5.15 逐条适配**（详见报告）：folio→page；`do_swapout()` 的 `zswap_store()` → `frontswap_store()`（5.15 的 zswap 是 frontswap 后端）；`__swap_writepage(folio,&wbc)` → 三参 `__swap_writepage(page,&wbc,end_swap_bio_write)`；`scoped_guard` → 手写 `spin_lock_irqsave`；四道上游门被改两道——`mem_cgroup_zswap_writeback_enabled()` 5.15 无此符号 ⇒ 换成**本模块自己的政策** Batch 38 `memcg_dying_bailout` 的 `memcg_is_dying()`（经 `swap.h` 已可达，零新 include）;`zswap_is_enabled()` 5.15 无此符号（6.12 才改名）⇒ 换成 `frontswap_enabled()`。**准入第 5 道门在 zram 上确实是通的**（否则整套在 Android 上就是空转）：`mm/swapfile.c:3252` 的 `p->bdev->bd_disk->fops->rw_page` 被 zram 的 `.rw_page = zram_rw_page` 命中 ⇒ `SWP_SYNCHRONOUS_IO` 置位；纯 zram、没开 zswap 的设备也照样 offload。**本批修掉上游一个潜伏双解锁**：`shrink_page_list()` 的 `cannot_free` 分支落进 `keep_locked:`（`mm/vmscan.c:1874`），那一句**会解锁页**并把页放回 LRU，所以 kcompressd 接手时 `PG_locked` 已无人持有，而 `__swap_writepage()` 是 `->writepage` 实现、每个出口都解锁（`unlock_page()` 有 `VM_BUG_ON_PAGE(!PageLocked())`，要 `CONFIG_DEBUG_VM` 才可见）⇒ `do_swapout()` 先 `lock_page(page)`。上游 6.12 的 `keep_locked:`（`vmscan.c:1531`）同样解锁，所以这个潜伏缺陷上游自己带着。**未闭合风险已标出而非略过**：`try_to_free_swap()`（`mm/swapfile.c`）只看 `page_swapped()`、不认识本队列，理论上 FIFO 排队期间 swap 项仍可能被摘走；已在 `do_swapout()` 起手 `pr_warn_once(!PageSwapCache())` 把这个"理论上"变成显式失败而不是覆盖别人的换出数据（用 `pr_warn_once` 而不是 WARN 家族：WARN 是 oops，`panic_on_oops` 下会把自己装的"不可能"守卫变成 panic，比它守的条件更糟）。**节点生命周期已做完**：`memory_notifier` 在 `MEM_ONLINE` 建线程、`MEM_OFFLINE` 拆线程，三点硬约束都落实了——拆除点必须对齐 `mm/memory_hotplug.c:2070` 的 `kswapd_stop()`（早一步到 `MEM_GOING_OFFLINE` 会踩到正在被 isolate 的页）、`wake_up_interruptible()` 必须先于 `kthread_stop()`（后者只置位不唤醒，上游那个不含 `kthread_should_stop()` 的谓词会持写模式的 `mem_hotplug_lock` 死锁）、拆完前必须 drain（ring 里每个 entry 都是一个页引用加一个活着的 swap slot）。**报告同时逐条给出**：§4 的 PG_writeback 由谁置（`block/bdev.c` 的 `bdev_write_page()` 里 `set_page_writeback()`）、页锁由谁放、端到端链、`end_page_writeback()` 的 `BUG()` 触发条件（因 `bdev_write_page` 已置位故不触发）、以及"只给 kswapd"的完整源码级理由（`page_ref_freeze()` 精确相等 → `cannot_free` 不累加 `nr_reclaimed` → direct reclaim 会走到 OOM killer）。**未做设备 A/B，不主张提速**。落地脚本 `scripts/batch41_core_vm_kcompressd.py`，上游 patch 副本 `research/upstream-5.15.y/patches/kcompressd-unofficial-0.5-linux6.12.44.patch`
## Batch 40(v0.45.0,已落地；六道门禁四档实测，167/178 上另有一处**既有**红项见末段)→ 详见 CHANGELOG.md#batch-40 — 主题「erofs 预读解压临时缓冲放开」，来源是对「上游有没有 erofs 优化补丁能提升 zram 压缩性能」这个问题的调查。**答案是「这条桥不存在」**，五条证据：① erofs 的解压全在 `fs/erofs/` 内（`decompressor.c` + `decompressor_lzma.c`），zram 走 crypto API（`drivers/block/zram/zcomp.c` 的 `crypto_alloc_comp()` → `crypto/lz4.c`），唯一共用的是 `lib/lz4`；② 上游 `lib/lz4` 在 `v5.15..v7.2` 只有 **6 个**提交、**零个性能优化**（逐条：宏搬移「No logic changes」、treewide 头文件改名、符号导出、`static` 化、OOB 安全修复、头文件清理），方法用 cgit tag 可达性而非 committer-date 窗口；③ **跨越方向是反的**——`751884743025`（导出 `LZ4_resetStreamHC`）是 Sergey Senozhatsky 的 **zram 字典系列**，`f0ef073e213a` 那次宏搬移才是 erofs 侧清理，即「是 zram 动了 lib/lz4，不是 erofs」；④ erofs 自身的「压缩优化」全是**读路径解压**，且 `EROFS_FS_PCPU_KTHREAD(_HIPRI)` 早在 ACK 5.15 就有；⑤ 真机 `CONFIG_ZRAM_DEF_COMP="lz4kd"`（厂商编解码器）+ 二级 `zstd`，连 `lib/lz4` 都够不着。于是改落**上游唯一在 5.15 ARM64 Android 真机实测过的 erofs 解压优化**：`d9281660ff3f`（v6.9，Chunhai Guo/vivo）落成 **1 组**（core，core 64 → 65）：`erofs_readahead_relaxed_gfp` —— 给 `struct z_erofs_decompress_req` 加 `gfp_t gfp`，LZ4/LZMA 的原地解压临时 bounce 页（`Z_EROFS_SHORTLIVED_PAGE`）在**预读路径**改用 `GFP_NOWAIT|__GFP_NORETRY`，**同步读**保持 `GFP_KERNEL|__GFP_NOFAIL`；上游实测（ARM64 Android 5.15、EROFS 4k pcluster）多应用启动 3364→2684 ms（64k 窗 −20.2%）/2079→1610 ms（16k 窗 −22.6%），镜像体积几乎不变 —— **那是上游口径、本批不主张设备侧提速**。**真机前提按 inode 逐条核，不看 superblock**：`LZ4_0PADDING` 与 `lz4_max_distance` 的默认值会让未压缩镜像看起来一样（本机 `feature_incompat=0x1`，无 `COMPR_CFGS`/`BIG_PCLUSTER`），所以拉了 16 MB 头部、按 5.15 的 on-disk 格式（**12 字节 `struct erofs_dirent`**、**12 字节 `erofs_xattr_ibody_header`** —— 按上游后来的 16 字节走会错位）走目录树，取到被目录项真实引用的 `/vendor/build.prop`（nid 110，16314 B，`compressed_blocks=2`）标记为 `EROFS_INODE_FLAT_COMPRESSION` ⇒ **解压路径在真机上确实是活的**（64 KiB 滑窗、4 KiB pcluster，落在上游 64k 窗那一列）。**5.15 形态六处差异**：① pcluster 结构在 `fs/erofs/zdata.h`（上游把它搬进 `zdata.c`），槽位由 `struct_size()` 自动增长、`kmem_cache_zalloc()` 保证初值 false；② **不需要上游新加的 `bool ra` 参数** —— 5.15 的 `struct z_erofs_decompress_frontend` 本来就有 `bool readahead`（`DECOMPRESS_FRONTEND_INIT()` 不初始化即 false，只有 `z_erofs_readahead()` 置 true，`z_erofs_pcluster_readmore()` 已在用），直接读 `fe->readahead` 即可，**签名与调用点一个不动**，比上游形态更小；`fe->pcl` 在 5.15 也不存在，pcluster 是 `clt->pcl`（`z_erofs_collector_begin()` 在返回 0 前的 `out:` 块自己就解引用它，故非空可证）；③ `z_erofs_decompress_pcluster()` 里的 rq 字面量末项是无逗号的 `.partial_decoding = partial`，要补逗号（上游末项是 5.15 没有的 `.fillgaps`）；④ 重置点跟 `cl->nr_pages = 0; cl->vcnt = 0;` 走（5.15 没有上游那个 pcluster 状态块）；⑤ `decompressor_deflate.c` 与 LZMA 的 `rq->fillgaps` 去重分支两个 hunk **无 5.15 载体，不落**；⑥ LZMA 参数改名（`pagepool`→`pgpl`）是化妆品，不落。**极性陷阱**：上游字段名 `besteffort` 与它自己的注释相反 —— true 表示**必须成功**（`|= !ra`，同步读置真 → NOFAIL），照名字抄就会把两侧对调，故单测与 `implementation_audit` 都按**标志串**钉而不是按字段名。**安全性按调用图追过**：预读时 `z_erofs_lz4_prepare_dstpages()` 返回 `-ENOMEM` ⇒ 页保持 not-uptodate（`SetPageError()`），下一次同步访问走 `z_erofs_readpage()`，它既不检查 error 位也不看 `readahead`，以 `GFP_KERNEL|__GFP_NOFAIL` 重读 —— 不能失败的那条路仍然不能失败（上游设计，非独立测量）。**另一条上游补丁改判不可移植**：`0f6273ab4637`（同作者的 LZ4 预留页池，v6.10，默认 `reserved_pages=0` 即关闭）的全部上下文是上游 v6.8 的全局 `z_erofs_gbufpool`/`zutil.c`，而本基线 `fs/erofs/` 里 `gbuf`/`global_buffers`/`z_erofs_gbuf_init` **grep 全为 0**（5.15 是 per-call 局部 pagepool）⇒ 属前置链，不是单批 bounded graft。非性能项一并记录不落：`cf7f2732b4b8`（per-CPU kthread 时默认开 HIPRI）是 1 行 Kconfig，且真机 config 早已显式选它；`1001042e54ef`（短生命周期页不再用 refcount 记账）是 memdescs 铺垫重构、无收益主张。上游戏列还查过 `lib/zstd`（v5.16/v6.4/v6.15 三次整库导入），属 ~40 文件子系统重写且只惠及二级 zstd，记为独立项目、不在本批。fixture 三处同步（`FETCH_FILES`/`AUDIT_FILES`/`SMOKE_FILES`，五个 erofs 文件）；**未做设备 A/B，不主张提速**。落地脚本 `scripts/batch40_core_erofs_readahead.py`；真机核查记录 `research/erofs_readahead/vermeer_check_20260922.md` + 可复现探针 `research/erofs_readahead/erofs_probe_inode.py`。**已知局限**：167/178 档 `step_audit.py`/`smoke.sh` 仍各红一处，是 **Batch 38 的 `memcg_dying_bailout` 既有缺口**（`include/linux/memcontrol.h` 的 `mem_cgroup_init()` 声明/存根锚点只在 194/216 存在，167/178 计数为 0），**非本批引入** —— 已用 pristine HEAD 在同一批参考树上复现同一失败（167：applied 63 + blocked 1 vs 期望 64；本批后 applied 64 + blocked 1 vs 期望 65），194/216 档本批六道门禁全绿
## Batch 39(v0.44.0,已落地)→ 详见 CHANGELOG.md#batch-39 — 主题「v7.2 分配器批量清页」，来源 `docs/survey_7_2_mm_reclaim.md` §3（`b001cf7d16dd`）。落地 **1 组**（core，core 63 → 64 组）：`pagealloc_batch_clear`——`kernel_init_free_pages()` 在 `!CONFIG_HIGHMEM` 下把 numpages 次 `kmap_atomic()`/`clear_page()`/`kunmap_atomic()` 换成对整个连续区间的一次 `memset()`；HIGHMEM 分支逐字节保留 5.15 原循环。上游数字（8192×2MB HugeTLB −62.7%、Graph500 内核态 −50.3%/−39.0%）是服务器负载，**不主张设备侧提速**。**实现时推翻了 survey 初版两个结论**：① 5.15 的连续清页原语不是 `include/linux/highmem.h` 的 `clear_huge_page()`（它在 `include/linux/mm.h:3266` 声明、`mm/memory.c:5899` 定义，内部仍按 subpage 走 `clear_user_highpage()`，不是连续清），5.15 根本没有 `clear_pages()`，只能内联；② 「CONFIG_INIT_ON_ALLOC_DEFAULT_ON + KASAN_HW_TAGS ⇒ 每次分配都走这个循环」是错的——5.15 的 `kasan_has_integrated_init()` 就是 `kasan_hw_tags_enabled()`（`include/linux/kasan.h:86`），HW-tags KASAN 开着时 `post_alloc_hook()` 由 KASAN 自己逐页清零并清掉 `init`，本函数根本不被调用；只有 HW-tags KASAN 关闭时才活（`CONFIG_INIT_ON_FREE_DEFAULT_ON` 未设，free 路径两条都不通）。**移植仍然安全且可证明**：唯一语义差异是去掉逐页 KASAN tag 对，而 `page_kasan_tag_reset()` 即 `page_kasan_tag_set(page, 0xff)`、`page_kasan_tag_set()` 用 `try_cmpxchg` 写回原值（`include/linux/mm.h:1571/1587`），`page->flags` 终点即起点；reset 也不可能影响传给 `clear_highpage()` 的地址，因为 arm64 的 `page_address()` 是 `lowmem_page_address()`（`:1702`）不带 tag。「KASAN 开着且本函数仍被调用」这一唯一危险 regime 不可达：绕过 `kasan_has_integrated_init()` 只能靠 `should_skip_kasan_unpoison()` 末行 `init_tags || (flags & __GFP_SKIP_KASAN_UNPOISON)`（`mm/page_alloc.c:2537`），其中 `__GFP_ZEROTAGS` 被上面 `init_tags` 分支自己消费（已置 `init = false`），`__GFP_SKIP_KASAN_UNPOISON` 全树无用户（已 grep）。**不改函数名**（上游 `kernel_init_pages`→`clear_highpages_kasan_tagged` 的改名零收益、却要在最热的分配路径上多两个锚点）。落地脚本 `scripts/batch39_core_pagealloc_batch_clear.py`
## Batch 37(v0.39.0,已落地；**编号由 34 让位而来**，见 CHANGELOG.md#batch-37 抬头)→ 详见 CHANGELOG.md#batch-37 — 内存回收路径六组（`0388536ac291`、`287d5fedb377`、`410abb20acae`、`68cd9050d871`、`dc37771a43d4`、`9669b87065a6`）：① 批量精度限 `SWAP_CLUSTER_MAX`、② 衰减批量 `(reclaim-reclaimed)/4`、③ `MIN_SWAPPINESS`/`MAX_SWAPPINESS` 宏、④ `memory.reclaim` 的 `swappiness=` 嵌套键（改写 UAPI、同步改手册 `cgroup-v2.rst`，借由 `sc_swappiness()` 穿透给 `get_scan_count()`/`get_swappiness()`）、⑤ suspend 冻结提早中止主动回收（`should_abort_scan()` 加 `sc->proactive && signal_pending`，返回 `-ERESTARTSYS` 使恢复后透明重启，针对 MGLRU 实测超时）、⑥ `lru_add` 批次提早释放死页面（`page_ref_freeze(page, 1)` 截断直进释放队列，省去两次 lruvec lock）。全链落入 `scripts/batch37_core_reclaim_paths.py`，三层改写前置生成文本（同族 trap 5，给 `memcg_memory_reclaim` 及被取代组各补自身探针）；`mm/swap.c` 首次入树，四档单测/审计全绿；core 46 → 52 组
## Batch 37(v0.39.0,已落地；六道门禁四档全绿（四档实测）；ABK CI 首轮编译失败已修、待复跑)→ 详见 CHANGELOG.md#batch-37-mglru — 主题「MGLRU v6.14 性能优化系列」，重新定性延后项 `mglru_612_refresh`（不是 6.12，是 torvalds v6.14 的 "mm/mglru: performance optimizations, v4"，封面 `9cbfd1c3c83b` 0/7 + 7 补丁）。基线 MGLRU 是 6.1「最小实现」回移（page 基 `lru_gen_struct`/`lists`/`sort_page`/`evict_pages`，四档逐字节同形），系列的 folio 期 diff 全部按 5.15 形状重写锚点。**落地 6 组**（全 core）：`mglru_clean_workingset`（`9cbfd1c3c83b` 可携带余量：锁断言进 `workingset_refault()` 入口，`workingset_test_recent` 重构无载体不搬）、`mglru_optimize_deactivation`（`cc8ec7be78ff`，pagevec 形状重写：`lru_gen_clear_refs()` 原地清 refs、最老代免搬移）、`mglru_rework_aging_feedback`（`798c0330c2ca`，`protected[]` 补齐 tier 0、`can_swap:bool`→`swappiness:int` 带 `>MAX_SWAPPINESS` 哨兵、aging feedback 简化、`MIN/MAX_SWAPPINESS` 进 swap.h）、`mglru_rework_type_selection`（`37a260870f2c`，逐 type 求和的 `read_ctrl_pos()` + 2:3 margin + 总 tier 比较，依赖前组、trap 5 已探针）、`mglru_rework_refault_detection`（`b1a71694fb00`，recency 改 `max_seq` 窗口 `MAX_NR_GENS` 距离——TPC-C −57% workingset_refault_file 的主角；基线无 `abs_diff()` 就地内联）、`mglru_wake_flushers`（`1bc542c6a0d1`，v6.13：MGLRU 路径全程无 flusher 唤醒，dirty-tail memcg OOM 防护）。**放弃 2 条**（用户拍板「6、7 收益不高难度大」）：`4d5d14a01e2c` workingset protection（`LRU_REFS_FLAGS` 语义重定义，四处文件联动）与 `a52dcec56c5b` PTE-mapped large folios（收益依赖大 folio，本机 4KB + madvise THP ≈ 0，且要 6.12/6.13 基础设施，挂回 `large_folio_mthp_substrate` 后再议）。**already_present 一条**：`3af0191a594d`（workingset accounting，LMKD 依赖的 refault 统计）逐行核对 ACK 回移已带修复后记账，不注册。**KMI**：唯一布局位移是 `lru_gen_struct.protected[]` 3→4 项（+64B，`lruvec` 内后移），无 KABI 槽可用，经用户确认接受；页标志布局零变化。core 46 → 53（45 + 并行两批 Batch 36 各 1 组 + 本批 6 组；合并 origin/main 后按注册表实数重算）。**ABK CI 首轮编译失败已定位并修复**（`mm/swap.c` 两处，见 CHANGELOG.md#batch-37 §6），待复跑
## Batch 36(v0.38.0,已落地)→ 详见 CHANGELOG.md#batch-36 — 文件系统批次（FUSE + erofs），**只落 FUSE 一条**：mainline `faa794dd2e17`（v6.16「fuse: Move prefaulting out of hot write path」，Dave Hansen，Miklos Szeredi 收）把 `fuse_fill_write_pages()` 的源缓冲区预缺页从重试循环**头部**挪进**无进展分支**（`copy_page_from_iter_atomic()` 返回 0 那支），于是经 daemon 的每次 `write(2)` 在快路径上只碰用户态一次而不是两次；重试路径保留 fault-in，前进保证不变。上游的理由是「与 `generic_perform_write()` 同形」—— 那只对**上游 6.15+** 成立，5.15 自己的 `generic_perform_write()` **仍在循环头预缺页**，差异与为什么这不影响安全见 CHANGELOG.md#batch-35 §6。上游 hunk 是 6.x 的 folio 形态（`__filemap_get_folio`/`copy_folio_from_iter_atomic`），本组按 5.15 的 page 形态（`grab_cache_page_write_begin`/`copy_page_from_iter_atomic`）重写同样两处 ⇒ 上游形态改写、**不加深色 marker**。**erofs 半边（file-backed mount 的 `fb176750266a` + `6422cde1b0d5`）整条排除**，`770c8d55c428`（lib/iov_iter）不适用，FUSE passthrough 按决定不做 —— 三条都在「排除记录」。core 40 → **46** 组（并行落地两批：arm64 LSE +1、page-cache/page-table +4，再加本组 +1；本批因此两次改名、终为 36）
## Batch 36(v0.38.0,已落地；六道门禁四档全绿（隔离 worktree 验证）；ABK CI 真编译与构建闸门未跑)→ 详见 CHANGELOG.md#batch-36-memcg — 主题「memcg 统计结构的 per-cpu 瘦身」：`70a64b7919cb` + `ff48c71c26aa`（v6.10，Shakeel Butt，两条一组：动态分配单独上是纯开销）落成 **1 组** `memcg_stats_percpu_slim`（mm/memcontrol.c + include/linux/memcontrol.h，19 步全 required）。**KMI 实测推翻原判**：android13-5.15 的 `abi_gki_aarch64.xml` 对 `mem_cgroup`（vmstats 内嵌）/`mem_cgroup_per_node`（lruvec_stats 内嵌）/`memcg_vmstats`/`lruvec_stats` 全部带完整布局追踪，且两结构无 `ANDROID_KABI_RESERVE` 槽、缩小无法掩护 ⇒ 按用户决定**不接 KMI break**，改 **KMI 中性私有改写**：头文件结构体逐字节不动（ABI XML 零差别），只压两个 percpu 堆对象（私有 `struct abk_vmstats_percpu`/`abk_lruvec_stats_percpu` + `__alloc_percpu_gfp`，指针字段类型不变）；聚合侧保持原始下标（KMI 冻结），rstat flush 逐槽映射回 item；`lruvec_page_state_local()` 出线（树内调用者仅 memcontrol.c/workingset.c，均 built-in）。两张 item 表为 5.15 重推导（26+3 state 项 ↔ `memory_stats[]`/`memcg1_stats[]` 读出侧与 6.10 自己的表双向吻合；15 事件项 = 整棵 mm/ 树写者枚举闭合、覆盖全部读者），漏项=静默归零 ⇒ 逐项钉进 implementation_audit，访问点逐函数钉（REQUIRED_IN_FUNCTION）。收益 ≈ **13.2 KB/memcg**（单 node 8 核；事件数组 90→15 是大头，忠实移植两条在这棵树上反而只有 ~4 KB）。core 45 → 46。**落地时工作树同存未提交的 Batch 37（MGLRU v4）在制品**：本批提交经临时 index 只含 Batch 36 文件集，门禁在 HEAD+仅本批的隔离 worktree 验证，见 CHANGELOG.md#batch-36 §5
## Batch 35(v0.37.0,已落地；**MADV_DONTNEED 页表对于 v0.42.0 移除**，见 CHANGELOG.md#batch-35 抬头)→ 详见 CHANGELOG.md#batch-35 — 主题「页缓存、readahead 与缺页/页表路径」：候选 7 条，**现存落地 2 条、排除 3 条、移除 2 条**。现存的是一对两组，都在 core：影子项清账（`61c663e020d2` 一次持锁清整个 pagevec + `d3db2c042591` 再一次遍历走完索引区间）——上游那次 **11 秒 soft-lockup** 就出在这里（`clear_shadow_entry` → `invalidate_mapping_pages` → `invalidate_bdev`）；无 `folio_batch` ⇒ 影子项 helper 用 `pagevec`，**保留 `__clear_shadow_entry()`**（5.15 的 truncate 路径还在用）。**已移除**：`MADV_DONTNEED` 的页表回收 `madvise_pt_reclaim`（`6375e95f381e`）与其配套 `madvise_batch_tlb_flush`（`43c4cfde7e37`）——5.15 的 smaps/reclaim 页表遍历者不重校验 pmd（无 `pmdp_get_lockless()`/RCU `pte_offset_map*`），只持 `mmap_read_lock` 就经 `try_to_free_pte()` 释放空 PTE 页会与之竞态，真机 vermeer 上崩在 `smaps_pte_range`；arm64 在 v6.14 也没选 `ARCH_SUPPORTS_PT_RECLAIM`，且收益只在超大稀疏映射的服务器负载显著，手机上可忽略（详见 CHANGELOG）。排除了 `7a1eb89f7918`/`d5ea5e5e50df`（5.15 的 `read_pages()` 不缩窗、整文件没有 `page_cache_ra_order()`）与 `0faa77afe72b`（没有 `filemap_map_folio_range()`/`filemap_map_order0_folio()`，净引用已是 1）——见「排除记录」。core 41 → 45（含同一合并进来的 Batch 34 的 `arm64_lse_percpu_load_atomics`）→ v0.42.0 移除两组后 57
## Batch 34(v0.36.0,已落地)→ 详见 CHANGELOG.md#batch-34 — arm64 非返回型 per-CPU 原子改用 load LSE 原子(mainline `535fdfc5a228`,v6.18,Catalin Marinas,arm64-fixes):`__PERCPU_OP_CASE()` 的 LSE 分支加一个未使用(但非 XZR)的 `[tmp]` 目的寄存器,`stadd`/`stclr`/`stset` 三个实例化翻成 `ldadd`/`ldclr`/`ldset` —— store 形态倾向「远」执行(互联/内存子系统),load 形态「近」执行(L1),起因是 Paul E. McKenney 在 `srcu_read_{lock,unlock}*()` 的背靠背 STADD 上发现的开销。纯头文件 asm 宏改写,KMI 中性(不动任何结构体);未进 linux-5.15.y,四档基线全部适用。**反面证据已核**:上游后来在 bpf-next(merge `c2f2f005a1c2`)证实该 commit 造成 fentry 基准回归(Neoverse-V2 revert=51.770 M/s vs 含修复=43.271 M/s;x86-64 启用回归 30%),但 **5.15 的 arm64 没有 BPF trampoline**(`arch/arm64/net/bpf_jit_comp.c` 无 trampoline 代码、Kconfig 无能力 select),递归检测的 enter/exit 辅助函数在 arm64 上不可达,回归面不适用 —— 取舍已记入 CHANGELOG。**全部上游证据来自 Neoverse V2 服务器核,Snapdragon 迁移性未验证,未做设备 A/B,不主张提速**(Batch 28 先例)。core 40 → 41 组;trap 6 全开:asm 内联文本审计看不见,四档 ABK CI 编译必须全绿才算数

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

  > **2026-09 更正（Batch 41）**：本条曾被读成"kcompressd 这条线已由 Batch 10-1
  > 接走"，那是不成立的判断，`kswapd 被压缩卡住`这一侧因此一直空着。三条理由：
  > ① popsicle 里那个 kcompressd 是 Qualcomm **QPACE 硬件引擎**版，需 SoC 硬件，
  > 本模块搬不了；② Batch 10-1 落的是"**已入库页的二次重压缩**"（省已用内存），
  > kcompressd 落的是"**首次换出时的压缩**"（省回收延迟 / 分配停顿），两者对象不同、
  > 不互相覆盖；③ 真正可用的上游件是 firelzrd 的
  > [kcompressd-unofficial](https://github.com/firelzrd/kcompressd-unofficial)
  > 0.5（Masahito Suzuki fork 自 MediaTek Qun-Wei Lin 的 Kcompressd），**只发行
  > 6.12.44 / 6.18 / 7.1-rc1 三个内核的 patch，没有 5.15 版**，所以必须重写载体。
  > 已按 Batch 41 落地（组名 `vm_kcompressd_swapout`，见 CHANGELOG.md#batch-41）。
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
- [x] `mglru_612_refresh`（P2，独立 MM 分支）→ **Batch 37 落地**（v0.39.0）；重新定性：
  不是 6.12，是 v6.14 的 7 补丁系列；落地 6 组，workingset protection 与
  PTE-mapped large folios 两条按用户决定放弃，见 CHANGELOG.md#batch-37
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

- [-] **erofs 优化补丁 → zram 压缩性能：无桥**（Batch 40 调查的主要结论，后续若再被提议按此条驳回）。五条证据：① erofs 的解压实现全在 `fs/erofs/` 内（`decompressor.c` 的 `z_erofs_lz4_decompress()` → `LZ4_decompress_safe_partial()`，及 `decompressor_lzma.c`），而 zram 经 crypto 层（`drivers/block/zram/zcomp.c` 的 `crypto_alloc_comp()` → `crypto/lz4.c`，后者 `#include <linux/lz4.h>`），两者唯一真正共用的是 `lib/lz4`；② 上游 `lib/lz4` 在 `v5.15..v7.2` 只有 **6 个**提交、**零个性能优化** —— `f0ef073e213a`（宏搬移，提交信息明写 "No logic changes"）、`5f60d5f6bbc1`（treewide `asm/unaligned.h` → `linux/unaligned.h`）、`751884743025`（导出 `LZ4_resetStreamHC`）、`2d8867f3e083`（`LZ4_decompress_safe_forceExtDict()` 改 `static`，消 build warning）、`eafc0a02391b`（`LZ4_decompress_safe_partial()` 越界读安全修复，5.15.y 已带、且按政策安全修复出局）、`22c033989c3e`（头文件清理）。方法：`git.kernel.org` cgit 按 **tag 可达性**（`log/lib/lz4/?id=v5.15..v7.2`）逐页走完，不是 committer-date 窗口；③ **跨越方向是反的** —— `751884743025` 是 Sergey Senozhatsky 的 **zram 字典系列**（同系列 `eb826a01909a`「zram: recalculate zstd compression params once」是第 15 篇），`f0ef073e213a` 那次宏搬移才是 Gao Xiang 的 erofs 侧清理；即「是 zram 动了 `lib/lz4`，不是 erofs」；④ erofs 自身的「压缩优化」全是**读路径解压**（LZ4 预留页池、per-CPU 解压 kthread、compressed large folio、Zstd/DEFLATE 解压器支持 —— `7c35de4df105`/`ffa09b3bd024`），且 `EROFS_FS_PCPU_KTHREAD`/`_HIPRI` **ACK 5.15 早已自带**（真机均为 y）；⑤ 真机 `CONFIG_ZRAM_DEF_COMP="lz4kd"`（厂商编解码器，`CONFIG_CRYPTO_LZ4KD=y`）+ 二级 `zstd` —— 即便 `lib/lz4` 有优化，也落在「既不是主算法、也不是 lz4 二级」的位置。**顺带说明**：`lib/zstd` 的三次整库导入（v5.16 的 1.4.10、v6.4 的 1.5.2、v6.15 的 1.5.7）确实会惠及二级 zstd，但那是 ~40 文件的库替换 + `crypto/zstd.c` API 改写，属子系统重写，且与 erofs 无关；要做另立项目，不并入本批
- [-] `0f6273ab4637`（mainline「erofs: add a reserved buffer pool for lz4 decompression」，v6.10，Chunhai Guo/vivo，默认 `reserved_pages=0` 即**关闭**）：**不可移植（前提不存在）**。它的全部上下文是上游 v6.8 才有的**全局** `z_erofs_gbufpool`（`zutil.c` 的 `z_erofs_gbuf_init()`、`module_param_named(global_buffers, ...)`），而 android13-5.15 的 `fs/erofs/` 里 `gbuf` / `global_buffers` / `z_erofs_gbuf_init` / `z_erofs_gbufpool` **grep 全为 0**（四档 `internal.h`/`zdata.c`/`compress.h`/`pcpubuf.c` 逐文件核对）—— 5.15 是 **per-call 局部 pagepool**（`zdata.c` 的 `z_erofs_readpage()`/`z_erofs_readahead()` 各自 `struct page *pagepool = NULL;`，`erofs_allocpage()` 定义在 `fs/erofs/utils.c`）。硬移植要先补上游 v6.8 的全局缓冲池基底 ⇒ **前置链，不是单批 bounded graft**，同 `fb176750266a`（erofs file-backed mount）与 `9909b088b1f0` （zsmalloc `pool->lock`）的裁决形态。收益也只有相机冷启动平均 150 ms（上游口径），且默认关闭、要设 `erofs.reserved_pages` 才生效
- [-] erofs file-backed mount（mainline `fb176750266a`「erofs: add file-backed mount support」v6.12 + `6422cde1b0d5`「erofs: use buffered I/O for file-backed mounts by default」v6.13）：**排除（前提不存在）**。这两条只是该特性的**挂载管道**与**默认行为开关**，真正的数据通路夹在它们中间、本批未点名的两条里（`ce63cb62d794` + `283213718f5d` —— `fs/erofs/fileio.c` 正是它们新建的，而 `6422cde1b0d5` 改写的就是这个文件）。只落点名的两条，结果是**挂载能成功、而每个 inode 的数据都返回 `-EOPNOTSUPP`**：那是上游自己的中间态，代码里写着 `XXX: data I/Os will be implemented in the following patches`。那是桩，不是嫁接，所以排除的是整个特性。要在 android13-5.15 上真正跑起来，得先回移 5.15 → 6.12 的整段 erofs 演进（`erofs_buf`/`erofs_bread` 元数据层 —— 上游约 5.19 起就有、5.15 没有，且上游那些 hunk 的**上下文**本身就是它 → `fileio.c` 本身。注意别误读形态：file-backed mount 是**并列**加一套 `erofs_fileio_aops` 与无 bdev 提交路径，**不是取代 iomap** —— 上游 v6.12 乃至 v6.16 的 bdev 通路仍走 `erofs_read_folio()` → `iomap_read_folio(..., &erofs_iomap_ops)`；fscache/ondemand 也**不是**前置，上游把两者当互斥模式），属子系统重写而非单批 bounded graft。因此这不是「永远不做」，而是**要有一个前置批次**：
  `[~]` erofs 前置链（`erofs_buf`/`erofs_bread` metabuf 层 → 并列的 `erofs_fileio_aops` + 无 bdev
  提交路径，即 `fileio.c`）作为独立的多批项目推进，做完整链后才谈 `EROFS_FS_BACKED_BY_FILE`。
  fscache/ondemand **不在链上**（它与 fileio 是互斥模式，不是前置）。实测前提（167/178/194/216 **四条基线**的 `fs/erofs/` grep，四条 `internal.h` 同为 15955 字节且逐字节相同）：`erofs_is_fscache_mode` / `erofs_bread` / `erofs_buf` / `erofs_read_metabuf` / `devs->flatdev` / `s_fscache` / `packed_inode` / `erofs_pos` / `erofs_fill_from_devinfo` / `super_set_sysfs_name_generic` / `fs/erofs/fileio.c` —— **出现次数全部为 0**。ACK 的 5.15 erofs 是 iomap 时代：元数据由 `erofs_get_meta_page()` 直接读 `sb->s_bdev->bd_inode->i_mapping`（没有可指向文件 mapping 的间接层），数据由 `erofs_iomap_begin()` 把 `iomap->bdev = mdev.m_bdev` 交给 iomap（无 bdev 的映射无处提交），`struct erofs_device_info` 里是 `struct block_device *bdev` 而不是 `struct file *bdev_file`。因此本批**也不单独落** `fs/super.c` 的 `get_tree_bdev_flags()`（上游 `4021e685139d`）：它的唯一消费者就是 erofs，而 5.15 的 `get_tree_bdev()` 走 `blkdev_get_by_path()` → `lookup_bdev()`，对非块设备本来就以 `-ENOTBLK` 返回（实测：`lookup_bdev()` 里写着 `error = -ENOTBLK; if (!S_ISBLK(inode->i_mode)) goto out_path_put;`。**注意这仓的块设备代码在 `block/bdev.c`，不是上游的 `fs/block_dev.c`** —— 后者在本树里根本不存在，按上游路径去找会 404），该 helper 在上游只解决「误导性的 Can't lookup blockdev 打印」，在没有消费者的树里就是个死 helper。证据：`research/upstream-5.15.y/patches/fb176750266a.patch`、`6422cde1b0d5.patch`、`4021e685139d.patch`；守卫见 `tests/stable_5_15_test.py::test_batch35_fuse_prefault_out_of_write_path` 的
  「no group grafts erofs file-backed mounts」
- [-] `770c8d55c428`（mainline「lib/iov_iter: fix to increase non slab folio refcount」）：**不适用**。它修的是 `b9c0e49abfca`（"mm: decline to manipulate the refcount on a slab page"）引入的回归，而 5.15 的 `lib/iov_iter.c` 里 `page_folio()` 与 `folio_test_slab()` **一个都没有** —— 缺陷不存在，改也无处可改。
- [-] FUSE passthrough（mainline v6.9）：**按决定不做**。android13-5.15 已自带另一套实现，ioctl 编号是 `_IOW(229,126)`（上游是 `_IOW(229,1)` / `_IOW(229,2)`）；两者不冲突，但 MediaProvider 的 FuseDaemon 没有迁到上游 API，装了也没有调用者 ⇒ 等于往树里加第二份不可达实现，还多一份要维护的 KMI 面。本批次之后若有人提议（它为 FUSE 侧，与本批同域，最容易顺手带上），按此条驳回。

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
- [-] `7a1eb89f7918` + `d5ea5e5e50df`（mainline readahead 窗口系列「Reintroduce fix for
  improper RA window sizing」，v6.14，Jan Kara）：**排除（前提不存在）**。这两条针对的是
  5.18 readahead 重构之后的形态——patch 1 要删掉 `read_pages()` 里「回调没读满就缩窗」的
  `rac->ra->size -= nr`，patch 2 要修 `page_cache_ra_order()` 的 fallback 分支。而
  android13-5.15 的 `mm/readahead.c`：**`read_pages()` 里一处 `ra->size`/`async_size` 写入都没有**
  （167/178/194/216 四档 `grep` 均为 0），**整文件没有 `page_cache_ra_order()`**（该函数 5.18 才有）。
  5.15 的对应路径是 `page_cache_ra_unbounded()`，它自己就按
  `i = ractl->_index + ractl->_nr_pages - index - 1` 跳过已在页缓存里的页。两条都没有可改的文本，
  故不注册组
- [-] `0faa77afe72b`（mainline「filemap: optimize folio refount update in filemap_map_pages」，
  v6.18，Jinjiang Tu）：**排除（前提不存在）**。它省的是 `filemap_map_folio_range()` /
  `filemap_map_order0_folio()` 里「先 `folio_ref_add`」与 `filemap_map_pages()` 里
  「再 `folio_put`」那对重复更新。5.15 的 `filemap_map_pages()` 仍是单页 `head` /
  `first_map_page()` 形态，**没有那两个 helper**（四档 `grep` 均为 0），页引用由
  `next_uptodate_page()` 取走后直接转移给 PTE 映射（成功路径不 `put_page()`，失败路径才放），
  净引用本来就是 1。无载体，故不注册组
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
