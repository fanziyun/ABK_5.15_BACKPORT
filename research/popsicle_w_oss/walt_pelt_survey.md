# WALT smart_freq / LRPB → PELT + schedutil 移植调研报告

- 调研对象：`MiCode/Xiaomi_Kernel_OpenSource` 分支 `popsicle-w-oss`（小米 17 系，6.11.0）
  `kernel/sched/walt/` 中 5 个文件（Qualcomm 版权 2022–2025）：
  `smart_freq.c` / `pipeline.c` / `voter.c` / `voter.h` / `cpufreq_walt.c`
  （本仓库抽取件：`research/popsicle_w_oss/walt_extract/`）。
- 移植目标：AOSP **android13-5.15 GKI** 的 PELT + schedutil（`kernel/sched/cpufreq_schedutil.c`）。
- 结论先行：**未见任何把 smart_freq/LRPB 逻辑 PELT 化的公开先例**；"控制层保留 + 信号层换 PELT"
  只对"reason→freq 选举 + 上限钳制"这类自包含控制逻辑近似成立，**不是原样移植**；
  LRPB 的本体（per-ms busy bitmap、pipeline_cpu 钉核、WALT 窗口/AMU 周期计数）依赖 WALT 全栈，GKI 下无对应物，需自行设计替代信号。
- 原始文件路径实际位于 `C:\Users\Administrator\StudioProjects\ABK_5.15_BACKPORT\research\popsicle_w_oss\walt_extract\`
  （工作区目录名用下划线，非任务书里的连字符）。

---

## 1. LRPB / smart_freq 语义与算法要点

### 1.1 它们是什么

- **WALT** = Window-Assisted Load Tracing：按窗口（8/12/16 ms，对应 120/90/60 fps 刷新节奏）
  累加 runnable+running 时间。全部频控输入都是"窗口和/每窗口统计 + 任务级每-ms busy 位图 + 硬件周期计数"，
  与 PELT 的指数衰减完全不同。
- **smart_freq** = 在 WALT 频控（cpufreq governor "walt"）之上叠加的一套 **per-cluster 频率上限选举器**：
  每个 cluster 维护一组布尔 **reason**，每个 reason 绑一个 `freq_allowed`（kHz）+ `hyst_ns`（去激活滞后）；
  每窗口 rollover 评估各 reason 现态，做过滞后的置/清位后，取"激活/保持中 reason 的 freq_allowed 最大值"
  写入 `freq_cap[SMART_FREQ][cluster->id]`，governor 把最终频率 `freq = min(算得频率, 该上限)`。
  reason 位宽在 `enum smart_freq_legacy_reason`（6.11 形态，见 popsicle `walt.h:159-172`）：
  NO_REASON(0)、BOOST(1)、SUSTAINED_HIGH_UTIL(2)、BIG_TASKCNT(3)、TRAILBLAZER(4)、SBT(5)、
  PIPELINE_60FPS_OR_LESSER(6)、PIPELINE_90FPS(7)、PIPELINE_120FPS_OR_GREATER(8)、
  THERMAL_ROTATION(9)、**LRPB_SMART_FREQ(10)**、LEGACY_SMART_FREQ(11)。
- **LRPB**：`walt.h` 对字段的注释即 `/* lrb = long_running_boost */`（popsicle `walt.h:293`），
  代码中无更完整的 LRPB 全称展开；结合用途可读作 Qualcomm 内部的
  "long-running(pipeline) boost"（持续多毫秒忙的渲染管线任务 → 管线核/簇频点抬升）。
  与 pipeline（重任务选取/钉核）是"配套但不同层"的关系。

### 1.2 LRPB 确切机制（代码证据，popsicle 6.11 `walt.c`）

1. 任务级 **busy_bitmap**：每 ms 一个 bit，该 ms 内 running 时间 >500us 置位（`walt.c:2496 set_bits`，
   `walt.c:2777 update_busy_bitmap`，16bit，`period_contrib_run` 累加）。
2. 触发条件（`walt.c:2792-2892`）：`pipeline_in_progress()`（`walt.h:883`，= 手动 pipeline 或有
   `sysctl_sched_heavy_nr`/`sysctl_sched_pipeline_util_thres` 且 `have_heavy_list`，且 `sched_boost_type==0`）
   且任务是被选中的 pipeline 任务（`wts->pipeline_cpu != -1`）且仍在 runnable；按当前 ravg 窗口档位
   检查最近活跃 ms 数 `hweight16(busy_bitmap…) ≥ sysctl_sched_lrpb_active_ms[0/1/2]`
   （档位 0=≤8ms/120fps，1=12ms/90fps，2=≥16ms/60fps）。
3. 命中后置 task 标志 `WALT_LRB_PIPELINE_BIT` + CPU 级闩锁 `wrq->lrb_pipeline_start_time = wallclock`
   （`walt.c:2891-2892`）；闩锁超时自动清（阈值 4ms@≤8ms 窗口否则 8ms，`walt.c:2792-2794`），任务睡眠清位。
4. 状态翻转（0→1 或 1→0）时 `waltgov_run_callback(rq, WALT_CPUFREQ_PIPELINE_BUSY_BIT)`
   （`walt.c:2957-2964`）→ governor 重算。
5. 频域效果有两处：
   - util 膨胀：`wrq->lrb_pipeline_start_time != 0` 时 load ×`(100+sysctl_pipeline_busy_boost_pct)/100`，
     reason=PIPELINE_BUSY（`walt.c:752-755`）；
   - util→freq 映射的 capacity 换值：`cluster_in_smart_lrpb()` 为真时用 `pre_smart_freq_capacity`
     代替 `capacity_orig_of(cpu)`（`walt.c:773-774` / `869-870`）。
6. smart_freq 侧把"簇内任一 CPU 的 `lrb_pipeline_start_time` 非 0"作为 reason：
   `cluster_in_smart_lrpb()`（抽取件 `smart_freq.c:14-30`）→ `LRPB_SMART_FREQ` 置位
   （`smart_freq.c:672-676`）→ 该 reason 的 `freq_allowed` 参与上限选举；governor 在
   `cluster_in_smart_lrpb()` 时跳过 smart-freq 钳制（`cpufreq_walt.c:241`）。

### 1.3 smart_freq reason 的投票方/输入信号（`smart_freq.c:547-685`）

| reason 位 | 现态判定 | 依赖的 WALT 信号 |
|---|---|---|
| NO_REASON | 恒置，作基线上限 | — |
| BOOST | `is_storage_boost() \|\| is_full_throttle_boost()` | WALT boost 框架（`boost.c` FULL_THROTTLE/STORAGE refcount） |
| TRAILBLAZER | `trailblazer_boost_state_ns` | trailblazer（预测性高载）状态机 |
| SBT | `prev_is_sbt` | core_ctl 单大线程检测（`core_ctl.c:1400-1475`） |
| BIG_TASKCNT | `nr_big>=big_task_cnt(6) && wakeup_ctr_sum<100` | 每窗口 nr_big_tasks + 每-窗口 wakeup 计数 |
| SUSTAINED_HIGH_UTIL | `thres_based_uncap()` | per-cpu `wrq->util` ≥90%目标容量持续 ≥300ms（`smart_freq.c:504-534`） |
| PIPELINE_60/90/120 | `pipeline_in_progress() && sched_ravg_window` 对应 ≥16/==12/≤8ms | 窗口档位(=fps) |
| THERMAL_ROTATION | `oscillate_cpu != -1` | 热轮转状态 |
| LRPB | `cluster_in_smart_lrpb()` | per-cpu `wrq->lrb_pipeline_start_time` |

- 去激活滞回：`smart_freq_update_one_cluster`（`smart_freq.c:433-502`）按 reason 各自的
  `legacy_reason_config[i].hyst_ns`（sysctl 可设，默认 4ms，`walt_config.c:70-77`）在**tick/窗口边界**才清位。
- 选举输出：`freq_cap[SMART_FREQ][cluster->id]`；并进入 `has_internal_freq_limit_changed`
  （`smart_freq.c:371-404`）= `min()` 各 cap（PARTIAL_HALT_CAP/SMART_FREQ/HIGH_PERF_CAP，
  `walt.h:75-80`）再与 IPC 上限 `max()`，簇间 `SYNC_FREQ_CAP` 同步（大簇不得低于小簇），变了就
  `waltgov_run_callback(WALT_CPUFREQ_SMART_FREQ_BIT)` 重算并 `update_cpu_capacity_helper`。
- **调用时机**：`core_ctl.c:1077`（`update_running_avg()`，在 `walt_irq_work` 的窗口 rollover 路径里，
  `walt.c:4646-4729`）以 `(window_start, 全簇 big 任务和, 全系统 wakeup_ctr_sum)` 每窗口调用一次。

### 1.4 IPC 型 smart FMAX（与 LRPB 无关的另一条）

- per-cpu `ipc_level` 由 `calculate_ipc()`（`walt.c:5316-5340`）读取 **ARM AMU 硬件计数器**
  `AMEVCNTR0_CORE_EL0`(cycles)/`AMEVCNTR0_INST_RET_EL0`(instructions)，`delta_cycl > min_cycles`
  时 `ipc = inst*100/cycles`；在 `android_vh_scheduler_tick`（`walt.c:5432-5514`）每 tick 刷新，
  8ms 无新采样按 tickless 处理；`get_cluster_ipc_level_freq()`（抽取件 `smart_freq.c:332-369`）取簇内
  最高 `ipc_level` → `ipc_reason_config[level].freq_allowed`。
- IPC 阈值/频率用 sysctl 对（IPC_A..E）配置（如 SUN cluster0: 120/180/220/260/300 → 对应频率），
  `min_cycles`（如 5806080/7004160）是硬件专用门限。

### 1.5 pipeline（重任务/渲染管线）的升/降频、rate-limit、boost 逻辑到底在哪个文件

注意分工：
- **pipeline.c** 只做"选 1–3 个 top-app 重任务（heavy_wts/MAX_NR_PIPELINE=3）、在 gold/prime 间钉核/换核
  （config1/2 能量比较、IPC_DEGRADATION_FACTOR=115、PIPELINE_2L_FACTOR=95、REARRANGE_HYST 100ms）、
  通过 core_ctl unisolate/boost 拉开簇"（`pipeline.c:290-1060`），**不直接算频点**。
  对外只给下游提供 `pipeline_in_progress()`、`pipeline_cpu`、每任务 busy 状态。
- **升/降频与 rate-limit** 全部在 governor `cpufreq_walt.c`（governor 名 "walt"，`cpufreq_walt.c:1715`）：
  - rate limit：`waltgov_should_update_freq`（min-rate）+ `waltgov_up_down_rate_limit`
    （**升/降分开**，`up_rate_limit_us` / `down_rate_limit_us`，`cpufreq_walt.c:30-92`）。
  - 每簇驱动 CPU 聚合各 CPU util（`waltgov_next_freq_shared`）→ `get_next_freq`
    （`cpufreq_walt.c:295-454`）里按序叠加：
    *floor 侧*：target-load 通胀 `walt_map_util_freq`（`cpufreq_walt.c:177-213`，1.25×fmax 及 zone 通胀）
    → rtg_boost_freq → hispeed_freq（含 hispeed_load 条件）→ PL（predicted load）→ trailblazer floor
    → adaptive_level_1/low/high 阶梯；
    *cap 侧*：`get_smart_freq_limit`（`cpufreq_walt.c:234-273`，legacy 与 IPC 上限取大再对 freq 求 min，
    但 `cluster_in_smart_lrpb()` 时整段跳过）→ HIGH_PERF_CAP → PARTIAL_HALT_CAP → uclamp。
  - 一次 util 更新入口：`waltgov_update_freq`（`cpufreq_walt.c:651-696`），smart_freq 变更走
    `waltgov_update_smart_freq`（625-649）；最终 `cpufreq_driver_resolve_freq()` + fast_switch 或
    deferred kthread（`waltgov_work`）。
- **每秒/窗口的"重活"判断（BIG_TASKCNT/SUSTAINED 等）**与 boost/thermal/pause 由其它 walt 模块喂信号
  （`core_ctl.c`、`boost.c`、`walt_halt.c`、`walt_cycles.c`…），cpufreq_walt 只是消费端。

### 1.6 对外 knobs

sysctl（`/proc/sys/walt/...`，注册见 popsicle `sysctl.c:2475-2528`；表项 `sysctl.c:2232-2367` 等）：
- 每簇 smart_freq（`walt/clusterN/smart_freq/`）：`legacy_freq_levels`（reason,freq 成对写）、
  `ipc_levels`、`ipc_freq_levels`、`sched_smart_freq_dump_legacy_reason`、`sched_smart_freq_dump_ipc_reason`
  （handler 见抽取件 `smart_freq.c:33-329`）。
- pipeline/LRPB：`sched_heavy_nr`、`sched_pipeline_cpus`、`sched_pipeline_util_thres`、
  `sched_lrpb_active_ms`（3 元）、`sched_pipeline_rearrange_delay_ms`、`sched_pipeline_busy_boost_pct`、
  `sched_pipeline_special(_task_util_thres)`、`sched_pipeline_non_special_task_util_thres`、
  `sched_single_thread_pipeline`、`sched_high_perf_cluster_freq_cap`、`sched_max_freq_partial_halt`、
  `sched_sbt_enable`/`sched_sbt_pause_cpus`/`sched_sbt_delay_windows`、`sched_ed_boost`、`sched_boost`、
  `sched_ravg_window_nr_ticks`（默认由 `walt_config.c` 注入，如 `sysctl_sched_lrpb_active_ms[0..2]`，
  `walt_config.c:112-114`）。
- governor sysfs（policy 下 "walt" governor 的 attr，抽取件 `cpufreq_walt.c:1364-1379`）：
  `up_rate_limit_us`、`down_rate_limit_us`、`hispeed_load`、`hispeed_freq`、`hispeed_cond_freq`、
  `rtg_boost_freq`、`pl`、`target_loads`、`boost`、`adaptive_level_1`、`adaptive_low_freq`、
  `adaptive_high_freq`、`zone_max_util_pct`。
- voter 框架本身无 sysctl；见 §1.7。

### 1.7 voter 框架

`voter.c/voter.h`（抽取件）是 Qualcomm 的通用 **votable 选举引擎**（VOTE_MIN / VOTE_MAX /
VOTE_SET_ANY，8 个 client 槽，`vote()`/`get_effective_result`/`rerun_election`/`create_votable`），
在 6.11 树中带 trace（`sched_client_vote`/`sched_votable_result`，popsicle `trace.h:2087-2146`）。
**注意**：这 5 个抽取文件本身没有 create_votable 的调用方；voter 的"投票方"实例注册在 walt 目录
其它文件（该目录共 37 个文件，见 popsicle `kernel/sched/walt/` 列表）。在本抽取集内可观察到的
“投票/选举”语义实际上由三套并存的机制承担：
1. `freq_cap[MAX_FREQ_CAP][cluster]` 的 min/max 算术（PARTIAL_HALT_CAP/SMART_FREQ/HIGH_PERF_CAP）——
   形如一个 MIN 型 votable（`smart_freq.c:371-404`）；
2. CPU pause/halt 的 per-client vote mask（`walt_halt.c:31 client_vote_mask[MAX_PAUSE_TYPE]`，
   client 含 `PAUSE_THERMAL`、`PAUSE_SBT` 等，`cpus_halted_by_client()` 被 cpufreq_walt 每次更新查询）；
3. boost refcount 优先级聚合（NO/FULL_THROTTLE/CONSERVATIVE/RESTRAINED/STORAGE/BALANCE，`boost.c:165-272`）。
若需在移植文档里引用 voter 语义，应引用抽取件 `voter.c:256-326 vote()` 与 `voter.h:36-41`，并把
“具体投票方”标注为外部（不在 5 文件内）注册。

### 1.8 版本对照（同文件跨内核）

- popsicle-w-oss（6.11.0）：有 `smart_freq.c`/`pipeline.c`/`voter.c`/`voter.h`/`cpufreq_walt.c`；
  legacy reason 含 LRPB 位。
- Xiaomi `kunzite-v-oss`（msm-6.6.56）：有 `smart_freq.c`/`pipeline.c`/`cpufreq_walt.c`，
  **无 `voter.c/voter.h`**，legacy reason **无 LRPB 位**（`walt.h:156-168`）；但已有
  `sysctl_sched_lrpb_active_ms` 与 per-ms busy LRPB 闩锁（6.6 `walt.c:2550-2668`）。
- Xiaomi `spring-v-oss`（msm-6.1.93）与 `guitar-w-oss`（msm-5.15.185）：`kernel/sched/walt/` 里
  **只有 `cpufreq_walt.c`，没有 `smart_freq.c`/`pipeline.c`/`voter.*`**（kernel/sched/ 根下也没有）。
  即：smart_freq/pipeline 是 msm-6.6→6.11 才铺开的新形态；Qualcomm msm-5.15/6.1 自身就没有同文件可对照。
  （URL 均见 Sources。）

---

## 2. WALT 输入 → PELT 映射表

目标基线信号（android13-5.15）：
`sugov_get_util()` = `effective_cpu_util(cpu, cpu_util_cfs(rq), arch_scale_cpu_capacity, FREQUENCY_UTIL)`
（`schedutil.c:201-210`）；`get_next_freq()` = `map_util_perf(util) → map_util_freq(util, fmax, max) →
cpufreq_driver_resolve_freq`（`schedutil.c:177-199`）；聚合取簇内最大 util（`sugov_next_freq_shared`，
`schedutil.c:446-468`）。util 都带 uclamp 与 iowait 处理。

| WALT 输入（含位置） | PELT/AOSP 对应物 | 可移植性 |
|---|---|---|
| per-cpu `wrq->util`=窗口累计 runnable 折算（`walt.c:777-805`；`smart_freq.c:518-529` 用其判 90% 阈值持续） | `cpu_util_cfs(rq)` / `effective_cpu_util(...,FREQUENCY_UTIL)`，容量 `arch_scale_cpu_capacity` | **可直接映射**；SUSTAINED_HIGH_UTIL(300ms≥90%) 用 tick hook 或自己的滑动窗口实现 |
| 簇/系统 `nr_big_tasks`、`wakeup_ctr_sum`（窗口级，`core_ctl.c:1065-1077`） | 无现成 PELT 量。近似：cgroup/cpuset "top-app" 组内按 `task_util_avg`/`se.avg` 超过阈值的任务数 + 每窗口唤醒次数自维护 | 无对应 → 自维护小窗口计数（top-app 探测可借 `android_rvh_enqueue/dequeue_task` 的 task group） |
| `sched_ravg_window` 8/12/16ms（fps 档） | 无（PELT 没有窗口/刷新档概念） | 无对应 → 用渲染帧节奏自己估（SurfaceFlinger/vsync 或 top-app 唤醒周期 EWMA），或静态档位 |
| 任务 busy_bitmap / `lrb_pipeline_start_time`（per-ms busy 闩锁，`walt.c:2777-2898`） | 无（PELT 是指数衰减，不提供"最近 N ms 内运行≥M ms"布尔） | 无对应 → 自维护 16ms 环形 busy 表（每个 tick/hook 记账 rq->curr 的 delta），再按 fps 档比对阈值 |
| `pipeline_cpu`/heavy list（`pipeline.c`，任务钉核/换核的前提） | 无（GKI 无 walt 任务侧字段与迁移钩子） | 无对应 → 只能退化为"top-app cgroup 内最大 util 任务所在 CPU"近似，语义显著弱化 |
| AMU `AMEVCNTR0_*` cycles/inst → `ipc_level`（`walt.c:5316-5340`） | 无（GKI sched 不读 AMU；`perf_event` 有但开销与语义不同） | 无对应 → 建议丢弃 IPC-FMAX 或改"每窗口 runnable 时间占比"当"伪 IPC"重标定 |
| `walt_load.pl`（pred demand）、`nl`、非 boost load（`cpufreq_walt.c:529-592`） | 无；PELT 近似的“预测”常用 `se.avg.util_avg + 最近唤醒增量` | 部分重造：可用 `util_est`（`task_util_est`/`cpu_util_est`）替代 predicted load |
| boost/flags（rtg/hispeed/PL/early-det/trailblazer/SBT/oscillate/storage/full-throttle，`cpufreq_walt.c:337-425`） | 无对应 walt 状态机；AOSP 有 `android_vh_setscheduler_uclamp`、iowait boost、`android_rvh_set_iowait` 等少量原生 boost | 状态机本身自包含 → 可"保留控制层"，但输入换源（见 §4） |
| 簇与核型（gold/prime、容量、`capacity_orig_of`） | `arch_scale_cpu_capacity` + `cpu_capacity`/`perf_domain` 可用；但 "pipeline cpu 集 + core_ctl 隔离" 无 | 部分可映射（簇=cpufreq policy/perf domain） |

结论：真正"可以直接喂进 schedutil 既有 util 聚合/next_freq"的只有 **CPU util 类**信号；
窗口 demand、任务 per-ms busy、唤醒计数、AMU cycles、频点历史（`avg_cap`/`curr_cycles`，`cpufreq_walt.c:102-159`）
**在 PELT 下均无现成对应**，要么自维护小窗口 EWMA/环形表，要么放弃。

---

## 3. android13-5.15 `kernel/sched/cpufreq_schedutil.c` 可挂载点

以下均基于 aosp-mirror/kernel_common `android13-5.15` 逐字节核实（Sources 附 URL）：

A. **策略注入点（改树/就地 graft）**
- `get_next_freq()`（`schedutil.c:177-199`）：所有 shared/single 频控请求的**唯一汇聚点**
  （`sugov_update_single_freq` L385 与 `sugov_next_freq_shared` L468 都调它）；
  在此处 map_util_freq 之后、resolve_freq 之前插入"reason 上限/下限"最干净，等价于 `get_smart_freq_limit`。
- `sugov_get_util()`（`schedutil.c:201-210`）：对每 CPU 的 PELT util 做一次"策略 util 变换"
  （等价 waltgov_walt_adjust 的 target-load/boost 通胀）的位置。
- `sugov_update_next_freq()`（`schedutil.c:115` 起，含 L137 `trace_android_rvh_set_sugov_update`）：
  已有 restricted hook 可改 `should_update` → 可做“策略态变化时强制更新 / 常规更新抑制”。
- `sugov_update_shared`（L472）/ `sugov_update_single_freq`（L374）/ `sugov_update_single_perf`（L414）：
  事件入口；若想按“本策略只改 freq 不改 perf(adjust_perf)”需在此分流。
- `sugov_policy`/`sugov_cpu` 是 `cpufreq_schedutil.c` 内 static 结构：策略状态（reason 位图、
  per-cluster 上限、busy 闩锁）可放进自己的全局 per-cluster 数组，不需动 KABI。

B. **android 现有 vendor hook（名称与所在文件均已核实存在于 android13-5.15）**
`include/trace/hooks/sched.h`：
- `android_vh_map_util_freq`（L253）、`android_vh_map_util_freq_new`（L259）——**调用点就在**
  schedutil `get_next_freq()` L186-188，可整段改写 util→freq 映射（含上限/下限、做 1.25×/zone 通胀）；
- `android_rvh_set_sugov_update`（L144，调用点 schedutil L137）——是否放行/强制一次频更；
- `android_vh_set_sugov_sched_attr`（L135，调用点 L637）；
- `android_rvh_effective_cpu_util`（L402，restricted）——能影响 schedutil 与 EAS 共同消费的
  effective_cpu_util（util 通胀层）；
- `android_vh_scheduler_tick`（L34）、`android_rvh_tick_entry`——LRPB/SUSTAINED 这类“周期记账”
  的宿主（WALT 自己也是挂在 android_vh_scheduler_tick / android_rvh_tick_entry 上做的，
  见 popsicle `walt.c:5342/5432`）；
- `android_rvh_enqueue_task`/`android_rvh_dequeue_task`（L38/42）、`android_rvh_cpu_overutilized`
  （fair.c L5753 调用）——任务级事件源。

`include/trace/hooks/cpufreq.h`：
- `android_vh_cpufreq_resolve_freq`（调用点 `drivers/cpufreq/cpufreq.c:542 __resolve_freq()`，
  policy min/max clamp 之后、查 freq table 之前）——**做"smart_freq 上限钳制"的最佳落点**，
  覆盖 fast_switch 与 slow 两条路径（schedutil 每次都会 resolve）；
- `android_vh_cpufreq_fast_switch`（cpufreq.c:2142）、`android_vh_cpufreq_target`（cpufreq.c:2301，
  __cpufreq_driver_target）、`android_rvh_cpufreq_transition`、`android_vh_freq_table_limits`、
  `android_vh_cpufreq_acct_update_power`。
`include/trace/hooks/power.h`：`android_vh_freq_qos_add/update/remove_request` —— 走 pm_qos 做钳制的备选。

C. **要点与坑**
- android13-5.15 schedutil **只有单一 `rate_limit_us`**（无 walt 的分向上/向下 limit）；
  若要复刻 `waltgov_up_down_rate_limit`，只能在策略层自持时间戳自限（不改 KABI 的前提下）。
- `android_vh_map_util_freq_new`/`android_rvh_set_sugov_update` 属 hook，GKI 允许 vendor 模块注册；
  但策略需要跨 CPU 的簇级状态（per-cluster），建议做成一个小内核模块 + `android_vh_*` 回调，
  或按 ABK 的 bounded-graft 就地改 schedutil/fair。
- android14-6.1 同文件仍为同样结构（`get_next_freq` 内单一 `android_vh_map_util_freq`，`schedutil_61.c:165/174`），
  6.x 的锚点形状与 5.15 兼容。

---

## 4. 可行性结论

1. **"控制层(voter/pipeline reason 选举)原样保留 + 信号层换 PELT"只在很窄的意义上成立**：
   可保留的只有 (a) `smart_freq_update_reason_common` 式的 reason 位图+去激活滞回选举，
   (b) `freq_cap[] → freq = min(freq, cap)` 钳制，(c) per-cluster 状态/阈值表；
   这些不碰 PELT 语义，属于自包含布尔控制。
   不能保留的：(i) `pipeline.c` 的重任务选取/钉核/换核（依赖 WALT 每任务 demand/`pipeline_cpu`/
   core_ctl 隔离）；(ii) LRPB 的 per-ms busy 闩锁（依赖 WALT 每-ms 记账与任务 busy_bitmap）；
   (iii) IPC-FMAX（依赖 AMU）；(iv) trailblazer/SBT/BOOST/early-det 等整套 WALT 状态机。
2. **GKI 上可行的最小近似**：一个注册 `android_vh_*` 的频控策略模块，在 PELT util 基础上做
   “SUSTAINED_HIGH_UTIL 式持续高载 uncap + top-app(pipeline) busy 时按 fps 档 floor/通胀 +
   cpufreq resolve/fast_switch 钩子做 smart_freq 上限”。LRPB 忙态用自维护 16ms 环形记账替代。
   需要明确声明：这不是“把 Qualcomm 代码搬过来”，而是**受其启发的自行设计**，行为曲线（尤其
   LRPB 的 4/8ms 闩锁窗口与 pipeline 双核换核）不可能等价复现。
3. **先例**：公开渠道未见任何人把 smart_freq/LRPB 移植到 PELT 或 AOSP schedutil。
   能检索到的 smart_freq/cpufreq_walt/pipeline 全部存在于带完整 WALT 的高通 msm 树
   （realme、Xiaomi、OnePlus sm8735/sm8845、freak07/sched_walt、自定义内核如 Capybara 等），
   且都是“整树带 WALT”，没有只搬频控策略到 PELT 的案例；OPLUS 在 vendor 侧有另一套独立
   `smart_freq` 模块（与 WALT 这份无代码关系）。→ **“未见 PELT 化先例，需自行设计”**成立。
4. 与 ABK 既有评估一致：本仓库 `plan.md` 已把该线记为“AOSP GKI 无 WALT、无锚点；类 WALT 效果
   属独立调度器工程”；本调研把“无锚点”精确化为“无 **WALT 侧**锚点，但 GKI 侧有
   `android_vh_map_util_freq(_new)`/`android_rvh_set_sugov_update`/`android_vh_cpufreq_*`/
   `android_vh_scheduler_tick` 等现有 hook 可作为近似策略的挂点”。若产品目标是逐位复刻
   smart_freq/LRPB 行为，唯一现实路径仍是整树 WALT（非 GKI）。

---

## 局限与假设

- web_search 在调研期间多次 502（上游不稳定）；"无先例"结论基于成功返回的多轮检索 +
  对源码的直接核查，无法承诺穷尽所有私有仓库。
- "voter 具体投票方"在抽取的 5 文件之外注册，本报告只给出可观察到的三类选举机制与所在文件/行，
  未下载全 37 个 walt 文件逐一核对（已核对核心_ctl/boost/halt/config/sysctl/trace 等）。
- 文件行号为对应分支/版本实际行号；本地抽取件见 `research/popsicle_w_oss/walt_extract/`。

## Sources（URL 与引用文件）

- 本地抽取件（本报告 §1 引用的 smart_freq.c/pipeline.c/voter.c/voter.h/cpufreq_walt.c 行号均指这些）：
  `research/popsicle_w_oss/walt_extract/*.c|h`
- popsicle-w-oss 6.11 补充文件（walt.h/walt.c/core_ctl.c/sysctl.c/walt_config.c/walt_halt.c/boost.c/trace.h），
  https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/popsicle-w-oss/kernel/sched/walt
  （raw 前缀 https://raw.githubusercontent.com/MiCode/Xiaomi_Kernel_OpenSource/popsicle-w-oss/kernel/sched/walt/<file>）
- msm-6.6 对照：https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/kunzite-v-oss/kernel/sched/walt
- msm-6.1 对照：https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/spring-v-oss/kernel/sched/walt
- msm-5.15 对照：https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/guitar-w-oss/kernel/sched/walt
- ACK android13-5.15 schedutil / fair / cpufreq：
  https://raw.githubusercontent.com/aosp-mirror/kernel_common/android13-5.15/kernel/sched/cpufreq_schedutil.c
  https://raw.githubusercontent.com/aosp-mirror/kernel_common/android13-5.15/kernel/sched/fair.c
  https://raw.githubusercontent.com/aosp-mirror/kernel_common/android13-5.15/drivers/cpufreq/cpufreq.c
  https://raw.githubusercontent.com/aosp-mirror/kernel_common/android13-5.15/include/trace/hooks/sched.h
  https://raw.githubusercontent.com/aosp-mirror/kernel_common/android13-5.15/include/trace/hooks/cpufreq.h
  https://raw.githubusercontent.com/aosp-mirror/kernel_common/android13-5.15/include/trace/hooks/power.h
- ACK android14-6.1 schedutil：
  https://raw.githubusercontent.com/aosp-mirror/kernel_common/android14-6.1/kernel/sched/cpufreq_schedutil.c
- 其它同文件树（检索命中，均为整树 WALT）：
  realme_neo8-AndroidB: https://github.com/realme-kernel-opensource/realme_neo8-AndroidB-kernel-source/blob/master/kernel/sched/walt/smart_freq.c
  OnePlus sm8845: https://github.com/lineageos-personal/android_kernel_oneplus_sm8845/blob/oneplus/sm8845_b_16.0.0_oneplus_15r/kernel/sched/walt/smart_freq.c
  OnePlus sm8735: https://github.com/OnePlusOSS/android_kernel_oneplus_sm8735/blob/oneplus/sm8735_b_16.0.0_turbo_6/kernel/sched/walt/smart_freq.c
  freak07/sched_walt: https://github.com/freak07/sched_walt/blob/master/cpufreq_walt.c
  Capybara-Revived: https://github.com/Lavax88/Capybara-Revived/blob/main/kernel/sched/walt/smart_freq.c
- WALT vs PELT 背景（无移植先例佐证）：
  https://github.com/oplus-kona/kona-playground/blob/main/docs/subsystems/scheduler_walt_vs_pelt.md
  https://docs.kernel.org/scheduler/schedutil.html
  https://blog.csdn.net/lei7143/article/details/130076267 （WALT 负载统计说明）
