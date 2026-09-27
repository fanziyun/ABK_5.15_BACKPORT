# ABK 5.15 LTS Backport

> [English](README_en.md)

## 特色功能

### 内存与交换

| 功能 | 说明 |
|---|---|
| **zram 重压缩** | 闲置达标的页以 zstd 二次压缩，同等内存容纳更多应用 |
| **压缩算法锁** | 主/副压缩算法固定为实测组合且**只读**，ROM 后续无法改写 |
| **zram writeback** | 上游在 5.15 分支冻结后追加的修正移植：重置设备不越界、扫描边界在锁内推导、写入预算按页对齐 |
| **主动回收增强** | `memory.reclaim` 单次回收量衰减、支持 `swappiness=` 参数、休眠不被回收阻塞 |
| **kcompressd 卸载** | 压缩与换出移出 kswapd 至每节点独立线程，回收时延不含压缩耗时 |
| **低内存快速失败** | 内存压力下 THP 类高阶分配一次尝试即回退调用方，不阻塞 direct reclaim |
| **动态预读** | 后台任务预读窗口减半，低内存机型节省内存 |
| **MGLRU** | 多代 LRU 的老化反馈、refault 判定与工作集清理采用上游 v6.14 版本 |
| **页面分配 / cgroup / 缺页路径** | cpuset 异常配置提前退出、percpu freelist 无锁读、per-memcg 主动回收、巨页缺页快速路径等 |

### 调度与响应

- **EEVDF 调度器**——5.15 原生 CFS 不具备公平调度器，含唤醒抢占与 `PREEMPT_SHORT`；
- **NOHZ 空闲均衡 / RT 扫描优化 / steal time 统计** ；
- **PSI 增强**——IRQ 压力跟踪、按 cgroup 的压力账户开关；
- **schedutil 智能调频**——突发负载维持频率下限，可选钳制上限（默认关闭）；
- **blk-mq 异步深度**，以及锁、信号量与 socket 释放路径的精简。

### 文件系统与进程

- 文件描述符分配、`close_range`、slab 分配/释放、缺页分配的热点路径优化；
- erofs 读路径的临时 bounce 页改用 `GFP_NOWAIT`，预读不再进入直接回收；readmore
  预读循环在 EOF 处收口，大页偏移的小文件读不再空转整个文件的页数；
- arm64 LSE percpu 原子操作、TLB 刷新批量合并、页缓存影子项批量清理、FUSE 写路径
  预缺页移出关键路径。

### 显示修复

`stable_display_fix` 移除 5.15.185 起 `drm: Add valid clones check`——该检查令
每笔厂商 `msm_drm` atomic commit 以 `-EINVAL` 失败

---

## 子模块

| child id | 内容 | 组数 |
|---|---|---:|
| `stable_backport_core` | 内存 / 回收 / zram / fs-mm 热点路径 | 67 |
| `stable_perf_backport` | 调度 / PSI / 块设备 / 调频策略 | 24 |
| `stable_display_fix` | drm 黑屏修复 | 1 |

共 **92 个移植组**，同一串注入在 `android13-5.15-lts` 上使用。

支持基线：**`android13-5.15-lts`（滚动分支，矩阵键为当前 Makefile `SUBLEVEL`，2026-09 为 216）**。
Batch 44 起不再支持 `5.15.167 / .178 / .194` 三个发布基线 —— 本仓库从来只按锚点门控，
不按版本号门控，因此撤档不改变任何组的形态。

---

## 如何使用

### 1. 注入

填入 `custom_external_modules`（CI 按输入顺序执行，均位于 `after_patch` 阶段）：

```
set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_backport_core;after_patch|set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_perf_backport;after_patch|set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_display_fix;after_patch
```

仅单模块：

```
set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_display_fix;after_patch
```
### 2. 可选

| 变量 | 作用 |
|---|---|
| `ABK_515_DEFCONFIG_ALIGN=1` | 追加 6.6-GKI config 差异（LRU_GEN、BBR、BLK_WBT 等，基线代码已具备） |
| `ABK_515_DEFCONFIG_ROM=1` | ROM 整合档：启用 `CONFIG_ZRAM_WRITEBACK`（默认关） |
| `ABK_515_DEFCONFIG_PSI=1` | per-cgroup PSI 档：移除 `cgroup_disable=pressure`（默认关） |
| `ABK_515_KSU_MODULE=0` | 跳过 KernelSU 模块打包 |

### 3. 刷入后

构建产物含 AnyKernel3 时，**KernelSU 模块随刷机自动安装**：固定 zram 策略、驱动
重压缩扫描、按需切换压力开关。无 AK3 时该步骤仅告警并继续，也可手动刷入
`build/ksu/abk_runtime_tunables.zip`。

可调参数：

- `/sys/module/page_alloc/parameters/abk_gfp_fastfail*` —— 高阶分配快速失败
- `/sys/module/cpufreq_schedutil/parameters/abk_sf_*` / `abk_sc_*` —— 智能调频下限与上限
- `/sys/module/readahead/parameters/dynamic_readahead` —— 动态预读
- `zram.abk_comp_algo` / `zram.abk_lock_algo` / `zram.abk_recomp_algo` —— 算法锁
- `vm.kcompressd` —— 卸载线程软上限

完整 knob 表见 [ksu/abk_runtime_tunables/README.md](ksu/abk_runtime_tunables/README.md)。

---

## 验证

```bash
python3 -m py_compile scripts/*.py tests/*.py                  # 语法门禁
bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh ksu/*/*.sh # shell 语法门禁
python3 tests/stable_5_15_test.py                              # 单测（无需内核树）
bash tests/fetch_sublevel_tree.sh android13-5.15-lts build/abk-trees/216  # 拉取参考树
python3 tests/implementation_audit.py build/abk-trees/216      # 内容审计
python3 tests/step_audit.py build/abk-trees/216                # 逐步锚点审计
bash tests/smoke.sh build/abk-trees/216                        # 端到端 + 回滚
```
---

## 仓库结构

| 路径 | 内容                            |
|---|-------------------------------|
| `scripts/` | **移植本体**：锚点引擎 + 三个 child 的组注册表 |
| `tests/` | 单测、参考树抓取脚本                    |
| `docs/` | 项目文档、开源声明                     |
| `tools/` | 5 个设备侧 CLI                    |
| `ksu/` | KernelSU 模块源码                 |
| `plan.md` | 实现清单                          |
| `CHANGELOG.md` | 各批次报告                         |
| `research/` | 上游 patch 归档与参考树|

---

## 索引

- 实测数据 —— [CHANGELOG.md](CHANGELOG.md)
- 某功能的调研与取舍过程 —— `docs/survey_*.md`
- 移植规则 —— [docs/porting_policy.md](docs/porting_policy.md)
- 新增移植组的方法 —— [docs/group_recipe.md](docs/group_recipe.md)

---

## 关于

上游 Linux 提交的独立、非官方移植，与任何厂商或发行版的官方内核发布无关。

本内核整合并适配了以下开发者与项目的工作（排名不分先后）：

| 开发者 / 项目 | 贡献 |
|---|---|
| Sergey Senozhatsky（Google） | zram 重压缩、writeback 系列 |
| Mel Gorman | page_alloc 系列 |
| Yu Zhao（Google） | MGLRU 系列、页缓存影子项清理 |
| Minchan Kim | zram 后备设备、idle/huge 页写回 |
| Jiayuan Chen | memcg dying 提前退出（4 项） |
| Richard Chang（Google） | zram 压缩写回、主动回收休眠中断 |
| K Prateek Nayak（AMD） | 调度 idle 负载均衡 |
| Shakeel Butt | memcg 统计与 shadow 优化（3 项） |
| firelzrd | Kcompressd-Unofficial |
| Qualcomm Technologies / QuIC | WALT smart_freq |
| OPLUS | `mi_dynamic_readahead` |
| OPPO | `android_vh_customize_alloc_gfp` vendor hook |

本项目自有代码以 `GPL-2.0` 发布


完整版权归属、逐 commit 署名与作者清单见 [docs/attribution.md](docs/attribution.md)

本项目使用了 AI 辅助开发。
