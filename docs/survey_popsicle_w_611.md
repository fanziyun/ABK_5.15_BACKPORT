# popsicle-w-oss(小米 6.11 线)候选调研

状态:`docs/survey_popsicle_w_611.md` · 目标:android13-5.15 GKI
(167/.178/.194/.211 锚点形状)· 结论为调研性质,**未注册任何 PatchGroup**。

来源与谱系、方法、limitation 见 `research/popsicle_w_oss/README.md`;
diff 工件在同目录。要点:来源分支是 bazel 增量树(全树 2370 文件),
不含 mm/sched 核心;小米导入基座比仓库 parent 新,无法做干净的小米增量归因。
以下每条 verdict 只依据"文件在 popsicle-w-oss 分支的实际形态"。

## 逐项判定

| # | 用户特性描述 | popsicle-w-oss 实际载体 | 归属 | android13-5.15 GKI 可移植性 | verdict |
|---|---|---|---|---|---|
| 1 | 同步主线标准 ZRAM / writeback 落盘 / 回写限速 / 后端 IO 统计 | `drivers/block/zram/zram_drv.c`(已含 writeback + `wb_limit` 系 32 处引用);多处为 6.12+ 主线形态(`filp_open_block`/`zram->bdev`、per-slot `spinlock_t`、`BLK_FEAT_*`) | 上游演进为主;小米私有痕迹集中在 QPACE 分支 | writeback_limit 属上游 5.16+ 特性,需核对 android13-5.15 是否自带;6.12 bdev API 重构依赖 5.15 没有的块层接口,不可整搬 | **候选(小)**/部分延后 —— 先核对目标树 writeback_limit;与 ABK_ABI_PATCH_SUITE 的 zram-writeback 覆盖做重叠排查 |
| 2 | kcompressd 异步压缩线程 | 无软件 kcompressd;替代物是 **QPACE** 硬件压缩引擎接入:`zram_comp` kthread、`CONFIG_QTI_PAGE_COMPRESSION_ENGINE`、ring/descriptor 队列、`qpace_*` 调用链 | Qualcomm SoC 专用 | GKI 无 qpace 驱动/硬件 → 锚点必 miss | **排除**(需 SoC 硬件);若意图是软件后台压缩线程,本分支无载体,另找来源 |
| 3 | MFZ 内存冻结/页面冻结配额/压缩统计/专属 IO 计数 | 全树零载体:文件名与内容均无 freeze/mfz/冻结逻辑(zram 与 mm/zsmalloc 均无) | — | — | **无载体** —— 该特性不在本分支;若语义 ≈ mainline idle/mark_idle 龄期冻结,已随 `zram_recompression` 落地 |
| 4 | 小米定制内存追踪(页生命周期/压缩率峰值/写回调试) | 无独立 tracker 文件;zram 侧统计为上游 stats + QPACE 路径计数 | 非小米自研可搬物 | `ZRAM_TRACK_ENTRY_ACTIME`(已落地)与上游 stats 覆盖其中一部分 | **无独立载体**/部分已覆盖 |
| 5 | zsmalloc 链长优化 | tip `mm/zsmalloc.c` = 6.12+ 主线重构形态:`ZS_MAX_PAGES_PER_ZSPAGE = CONFIG_ZSMALLOC_CHAIN_SIZE`(默认 8)与模块已落地组一致;`ISOLATED_BITS` 位域及按页隔离计数被上游整块移除(5.15 的 isolated 回绕 list_add BUG 地雷在此线不存在) | 上游 6.12+ 锁重构 + 极少小米痕迹 | 锁重构跨 mm 生命周期,属独立 MM rebase | **链长已覆盖**;整套锁重构 → 延后(独立 MM 分支) |
| 6 | 保留多算法 / 关闭重压缩 | `comps[]`/recompress 保留,另带 `max_pages` 重压缩上限参数 | 上游线演进 | 多算法已随 `zram_recompression` 落地;关闭重压缩属运行时策略(sysfs 不触发即关闭) | **已覆盖**;`max_pages` 参数可选小步(需先溯源是否上游 mainline) |
| 7 | dynamic_readahead(低内存后台减预读 / 后台半减上限 / mmap 环绕收缩 / UID 策略) | 全树无载体:无任何 readahead 相关文件,`fs/` 整目录不在库内 | — | — | **无载体** —— 特性不在本分支;若来自其它设备分支(如红米 msm-5.15 内测线),需提供对应分支再调研 |
| 8 | schedutil 调度优化 | kernel/sched 只有 `walt/` 模块;新文件(smart_freq.c / pipeline.c / voter.c / sysctl_walt_stats.c …)版权均为 Qualcomm(Linux Foundation / Qualcomm Innovation Center 2022-2025);核心 fair.c / cpufreq_schedutil.c 不在库内 | Qualcomm WALT 套件,非小米自研 | android13-5.15 AOSP GKI **无 WALT**(仅 qcom msm 厂商内核有);目标树无锚点 | **排除**(对本模块 GKI 目标);类 WALT 效果属独立调度器工程,远超模块边界 |

## 结论

1. 用户点名的三条线中,**本模块在 popsicle-w-oss 上找不到干净、可锚定、
   且非 vendor 依赖的移植项**:
   - zram 侧唯一大增量是 QPACE(SoC 硬件),GKI 不可移植;
   - schedutil/调度侧是 qcom WALT 套件,AOSP GKI 无 WALT;
   - dynamic_readahead / MFZ / mm tracker 在本分支**无任何载体**,
     该系列特性描述与 popsicle 源不符(疑出自其它小米分支)。
2. 与本模块现状的交集确认:`zsmalloc_chain_size`、`zram_recompression`
   (多压缩 + TRACK_ENTRY_ACTIME + config 使能)已在 Batch 4/6 落地,
   即用户清单里"链长"与"多算法接口"两条已兑现。
3. 可留作候选的仅极小项:
   - 核对 android13-5.15 目标树是否自带 zram `writeback_limit`
     (无则补上游小 hunk;先与 ABK_ABI_PATCH_SUITE 的 zram-writeback 领地协调);
   - 溯源 recompress `max_pages` 参数是否 upstream mainline,是则可作
     `zram_recompression` 组的可选小步。
4. 排除/延后理由均已写明,不再重议;若用户目标是"类 WALT/类 QPACE"行为,
   需要换来源(对应 msm-5.15 或 msm-6.6 设备分支)或另立独立工程。

## 备注

- 来源 = 分支当前形态,非单一 commit(小米整包 squash,无法按 commit 引用)。
- 本调研本身零代码改动;dynamic_readahead 的落地单独发生在 Batch 9-1
  (组 `dynamic_readahead_lowmem`,见 plan.md),不在本调研范围内。
