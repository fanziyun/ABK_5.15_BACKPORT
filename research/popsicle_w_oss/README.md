# popsicle-w-oss 调研工作区

来源:`MiCode/Xiaomi_Kernel_OpenSource` 分支 `popsicle-w-oss`
(Xiaomi 17 / 17 Pro / 17 Pro Max,Android W)。

## 版本与谱系(已核实)

- `Makefile`:6.11.0。
- tip 提交:`45705be1220b4cfa8100516ad86711656c0b634e`
  (2026-03-09,v-lijiangtao3,整包 squash "Kernel: Xiaomi kernel changes…")。
- tip 的 parent:`dfa34f4f6e160b39cb12840141769d3b5738e43b`
  (qcom 提交 "qcom: socinfo: Add feature code for subparts",2024-06 作者 /
  2024-09 committer)。
- 结构:小米仓库为 **bazel 增量树** —— tip 全树仅 2370 个文件
  (parent 约 79k),只保留小米/qcom 动过的文件 + `modules.bzl` 构建胶水。
  `mm/` 下只有 `zsmalloc.c`+`modules.bzl`;`kernel/sched/` 下只有
  `walt/` 模块;`mm/readahead.c`、`kernel/sched/fair.c`、
  `kernel/sched/cpufreq_schedutil.c`、`fs/` 整目录都不在库内。
- 含义:该仓库无法提供"小米 vs 基线的干净增量";
  且 tip 内文件语义比 parent 新(出现 6.12+ 主线形态的 bdev API、
  zsmalloc 锁重构),说明小米导入基座 ≠ 仓库 parent。
  因此本调研只按"文件在当前分支的实际形态"评估,不声称逐 hunk 归属。

## 分析方法

- blobless + depth 2 克隆到本地
  (`git clone --filter=blob:none --depth 2 --no-checkout --branch popsicle-w-oss …`)。
- 关键文件 diff:`git diff 45705be~1 45705be -- <path>`(parent 只作参照,
  语义以 tip 内容为准)。
- 特性关键词定位:git grep / ls-tree 全树文件名扫描。

## 产物

| 文件 | 内容 |
|---|---|
| `zram_drv_tip_vs_parent.diff` | drivers/block/zram/zram_drv.c 参照 diff(962 行) |
| `zsmalloc_tip_vs_parent.diff` | mm/zsmalloc.c 参照 diff(948 行) |
| `walt_zram_stat.txt` | 相关路径改动统计 |
| `../../docs/survey_popsicle_w_611.md` | 正式调研结论(verdict 矩阵) |

## 结论速览(细节见 survey)

- **QPACE**:zram_drv.c 中最大的厂商增量是 Qualcomm QPACE 硬件压缩引擎
  接入(`#include "../../soc/qcom/qpace/qpace.h"`、`zram_comp` 内核线程、
  ring/descriptor 队列、`CONFIG_QTI_PAGE_COMPRESSION_ENGINE`),
  依赖 SoC 专用硬件,非软件 kcompressd。
- **walt**:kernel/sched/walt 下的新文件(smart_freq.c / pipeline.c /
  voter.c …)版权均为 Qualcomm(Linux Foundation / Qualcomm Innovation
  Center),属 msm WALT 套件,非小米自研;目标 GKI 无 WALT。
- **zsmalloc**:tip 形态 = 6.12+ 主线锁重构(class->lock / migrate_lock /
  PG_zsmalloc,移除 isolated 位域计数),小米私有痕迹少;链长
  `CONFIG_ZSMALLOC_CHAIN_SIZE` 与本模块已落地组一致。
- **无载体项**:dynamic_readahead、MFZ、小米 mm tracker 在本分支无任何
  文件/符号载体,无法以本分支为来源移植。

参考 commit(分支 cpufreq_schedutil.c 文件历史中出现的上游链,
说明该文件曾存在后被 WALT 布局取代):
`9c0b4bb7f630`(schedutil 性能估计重构)、`f12560779f9d`(iowait boost 重构)、
`b3edde44e5d4`(arch_scale_freq_ref)、`e37617c8e53a`(非 invariant 频率修复)。
