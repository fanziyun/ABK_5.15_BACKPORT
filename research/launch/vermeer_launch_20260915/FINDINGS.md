# 冷启动 / `launch_boost` 真机诊断（vermeer / 5.15.216）

日期 2026-09-15。设备 Redmi K70（`vermeer` / `23113RKC6C`），内核
`5.15.216-android13-8-g5bfe2b8c1439`（= 本仓库 ROM tier 构建），KernelSU root，
SELinux Enforcing，无线 adb（`adb-14d93093-lnmc4L`，本机只有一个 transport 时不要写 `-s`，
它会改名；用 `adb devices | awk '$2=="device"{print $1;exit}'` 取）。

**问题（用户原话）**：冷启动过慢，查找优化 `launch_boost` 以解决；可以魔改或 backport
上游方案，但**必须真实可靠的数据、不能空实现**。

---

## 结论（先给结论）

1. **`launch_boost` 在这台机器上不是内核特性**，是小米的**用户态**应用启动预取栈，而且
   它**当前根本没在运行**。内核里既无同名节点、无符号，也没有 `CONFIG_LAUNCH_BOOST`。
   证据见 §1。所以「优化 launch_boost」不存在一个可以改的内核对象。
2. 因此加速点只能落在三类之一：**内核 readahead/预取**、**内核调度/放置**、
   **内核频率**。本批次按用户裁定「先测量归因再定」+「先只做判定观测」，
   **不引入任何 `PatchGroup`**，交付测量工装与真机会话。
3. 交付物 `tools/abk_launch_bench.sh` 已落地并接线（companion v0.11.0；v0.10.0 被并行的
   zram writeback 批次占用），9 条 selftest 全绿、真机 mksh 全绿、单测全绿。
4. **本批次的真机数据不足以指名杠杆，因此本文不指名任何杠杆。** 原因不是工装不够，
   而是这台机器的冷启动测量有一个**预条件陷阱**：测到的很可能是一台息屏或锁屏的手机，
   而那种数据看起来完全正常（§4）。工装现在会拒绝这种状态，但本会话里设备已被锁住，
   我没有办法在远端解锁。**按用户「不能空实现」的要求，宁可交白卷也不交一个漂亮但错的结论。**

---

## 1. `launch_boost` 是什么（逐条实测）

| 证据 | 结果 |
|---|---|
| `service list` / `dumpsys` 服务表 | 无 `readahead`、无 `launch` 服务注册 |
| `/system_ext/lib64/xiaomi.launch_boost.readahead-ndk.so` | AIDL `aidl.xiaomi.launch_boost.readahead.ILbReadahead`，方法 `readahead_start/finish/abort/clear/flush_all` —— 应用启动**文件预取**服务 |
| `/system_ext/etc/init/init.launch_boost.rc` | 只定义 `service iorapd /system_ext/bin/iorapd`，并 `on property:persist.sys.stability.PrereadEnable=true → start iorapd` |
| `getprop persist.sys.stability.PrereadEnable` | `false` |
| `getprop init.svc.iorapd` | `stopped` |
| `getprop persist.device_config.runtime_native_boot.iorap_readahead_enable` | `false` |
| `find /sys -iname '*launch*'`、`grep -i launch /proc/kallsyms` | 无节点、无符号 |
| `zcat /proc/config.gz \| grep -i LAUNCH` | 无 |
| 仓库全量检索（含 `research/`、`build/`、git history） | `launch_boost` 零命中 |

`.so` 的符号是直接读出来的（`exec-out cat` 拉回后 grep），不是推测：
`_ZN4aidl6xiaomi12launch_boost9readahead...` 加五个 `readahead_*` 方法名。

**推论**：本机的启动预取栈是**死的**。这不是内核能"优化"的东西；如果它本该加速冷启动，
那么冷启动现在是在**没有任何用户态预取**的情况下跑的。

---

## 2. 交付物

`tools/abk_launch_bench.sh`（Batch 27；`ksu/abk_runtime_tunables/embed.conf` 打进
companion 的 `bin/`，`action.sh launch [warm|drop] [iters]` 是入口）。

它测每次启动的：`am start -W` 的 `LaunchState`/`TotalTime`/`WaitTime`、
启动窗口内每簇 `scaling_cur_freq` 与 `cap_view`（`arch × scaling_max_freq / cpuinfo_max_freq`
的精确 awk 乘除，与 `abk_fas_check.sh` 同一公式）、上限取过几个点（`ceilings`）、
**主线程落在 arch 最大簇的采样占比**（`/proc/<pid>/stat` 的 processor 字段，
与 Batch 10-6 的 `5/36 → 11/37` 同一把尺）、应用所在 cpuset 及其中是否**含**超大核、
以及这次启动的 `pswpin`/`pgpgin` 与 PSI io 停顿。

它**拒绝**做的事（这是它的全部价值）：把没测到的数报成数（无 `TotalTime` 记 `NA` 并逐出中位数；
一个都没有则非零退出而不是打印 `0 ms`）；从样本不足的窗口给放置结论（`--min-mt`）；
以及**在息屏或锁屏的机器上测量**。

### 2.1 工装自己在真机上暴露并修掉的缺陷

这些都是「本地门禁全绿、只有真机才看得见」的一类，逐条留档：

1. **`${v%%|*}` 在本机 mksh 不可靠**。实测 `y="aa|bb"` 时 `${y%%|*}` 返回空、
   `${y#*|}` 一个字符都不剥离。拆包一律走 `IFS` + `set --`。
2. **主线程 processor 字段的剥离正则是错的**，而且错得毫无症状。
   第一版写 `sub(/^[^(]*\) /, "")`（"剥到第一个 `(` 之前"），而本机进程 comm 本身带括号
   （`4821 ((ease.cloudmusic)) ...`），`(` 之后紧跟的不是 `)`，**sub 从不匹配**，
   `$37` 于是读到 `cnswap` —— 对每个进程恒为 0。四次启动、四十次启动，
   工具都报 `mt_on_big=0/714`，一个形状完美、什么也没测的结果。
   修法：剥到**最后**一个 `)`。selftest 现在用夹具钉住（括号 comm 与带空格 comm 各一条）。
3. **`echo "...\t..."` 在 POSIX sh 不展开 `\t`**，夹具会变成一列。改用 `printf`。
4. **中位数辅助函数按键写错**：调用方存的键是 `"<app> <n>"`，函数里却用循环下标直接索引，
   恒读到未定义 → 恒 0。已改为把 app 名前缀一起传进去。

### 2.2 它现在能识别的预条件陷阱（本会话的第四条真机教训）

**息屏**和**锁屏**是同一个故障戴两顶帽子：这两种状态下启动的应用**永远不会成为 top app**，
于是留在 `foreground` cpuset（cpus `0-6`，**物理上不含 cpu7**），而且 `am start -W`
不再报 `LaunchState`/`TotalTime`。两个症状合起来会产出这样一张表：
**"超大核不在应用 cpuset 里" 32/40，"某些应用 0/8 次启动拿到 TotalTime"** ——
看起来像一个结论，其实是电话躺在锁屏上。工具现在每次启动前都重查这两项并在命中时中止
（`raw_guard_run_drop.txt` 就是它正确拦下一次 mid-run dozing 的记录）。

---

## 3. 可靠的数字（与冷启动无关，但确定了杠杆的边界）

真机实测，四棵基线一致：

| | policy0 | policy3 | policy7 |
|---|---|---|---|
| `cpuinfo_max_freq` | 2016000 | 2803200 | 3187200 |
| `cpu_capacity`（arch） | 280 | 855 | 1024 |
| `cap_view @ 满血` | 280 | 855 | 1024 |

会话之间唯一变动的是**谁在写 `scaling_max_freq`**：同一天内观察到
`policy7` 上限从 `556800`（cap_view 179）到 `2956800`（cap_view 950）之间不同档位，
`ceilings` 在启动窗口内取过 1–4 个点。**即"超大核被钉住"是会话态、不是常量** ——
Batch 10-6 记录的 1843200（58%）只是其中一个档。

一条**方法学**结论（这是本批次最硬的一条，来自唯一一次两臂都完整的会话）：

> 丢掉 page cache 之后，每次启动读的页数涨了 5–340 倍，而 `TotalTime` 只涨了
> **11%–29%**。所以 **"读了很多页" 不等于 "在等这些页"**。
> 按 `pgpgin` 阈值判 "I/O 受限" 是错的，工装已据此改造：存储的份额只能由
> **两臂的延迟差**给出（`--save` / `--compare`），单臂不再允许下这个结论。

---

## 4. 为什么本批次不指名杠杆

本会话一共跑了四轮真机，逐轮留档：

| 轮 | 工具 md5 | 结果 | 处置 |
|---|---|---|---|
| 1 | `fe5e2e44…` | 首版，`mt_on_big` 恒 0 | 发现缺陷 §2.1.2，作废 |
| 2 | `00166546…` | processor 字段修好后的完整两臂：40/40 有 `LaunchState`、cset 含 cpu7、`mt_on_big 198/685 (28.9%)`、`cap_inv 7/334 (2.1%)`；drop 臂 `232/757 (30.6%)` | **数据看起来干净，但原始文件被第 3 轮覆盖，未保留 → 不可引用** |
| 3 | `4ceac61a…` | drop 臂第 1 次启动后设备落入 `Dozing`，被 guard 拦下（`raw_guard_run_drop.txt`）；warm 臂 40/40 完成但 32/40 的 cpuset 不含 cpu7、两个应用 0/8 无 `LaunchState` | 保留为**预条件陷阱的证据**，不作结论 |
| 4 | 同上 | 设备进入锁屏（`mDreamingLockscreen=true`、`mFocusedApp=null`），`wm dismiss-keyguard`/swipe/power-cycle 均无法在远端解锁 | 未测量 |

第 2 轮是唯一一次两臂都完整、且 cpuset 含 cpu7 的会话；它的 `mt_on_big 28.9%` 说明
**超大核并非"从不使用"**（与 Batch 10-6 的 5/36→11/37 同量级），但它没有留下原始日志，
而第 3 轮同样看似完整却测的是一台不可用的手机。两者的区别在原始文件里分不出来，
所以我**不引用第 2 轮的数字**。

---

## 5. 复现与下一步（给用户）

设备必须在**亮屏且已解锁**的状态；`svc power stayon true` 只对"插电"生效，
本机在电池上，所以息屏定时器仍会走，锁屏也会回来。远程预热：

```bash
adb shell 'settings put system screen_off_timeout 2147483647; input keyevent 224; wm dismiss-keyguard'
adb shell 'dumpsys window | grep mDreamingLockscreen'   # 必须是 false，否则先手动解锁
```

然后两臂（`--save` 必须是第一臂，`--compare` 必须是第二臂）：

```bash
adb shell 'sh /data/local/tmp/abk_launch_bench.sh --mode warm --iters 8 --save /data/local/tmp/lb_warm.tsv'
adb shell 'sh /data/local/tmp/abk_launch_bench.sh --mode drop --iters 8 --compare /data/local/tmp/lb_warm.tsv'
```

判据（写在工具里，不在这里另立）：

- 若 `cap_inv` 占比 ≥ 5%（`--invert-pct`）→ **ceiling bound**，杠杆是写
  `scaling_max_freq` 的那一方（本机是 `scene-daemon`），不是内核 graft；
- 否则若主线程在最大簇的占比 < 20% → **placement bound, cause not measured**，
  下一步是 ftrace（`sched_find_best_target` / `sched_switch`），Batch 10-6 的方法；
- 否则看 **两臂延迟差**：≥15% 才叫存储占得住脚，此时才是 readahead 预取的候选
  （`android_vh_ra_tuning_max_page` / `android_vh_tune_mmap_readaround` 四棵基线都已自带，
  本模块 `dynamic_readahead_lowmem` 已在用，锚点风险最低）。

还有一件与内核无关但可能更有效的事：本机的用户态预取栈（`iorapd` /
`xiaomi.launch_boost.readahead`）**是关着的**（§1）。把它打开是不是比任何内核改动都值，
应当先于内核侧投入去量。

---

## 6. 出处

- 工装：`tools/abk_launch_bench.sh`（本批次唯一新增的测量代码），
  `ksu/abk_runtime_tunables/action.sh launch`，`ksu/abk_runtime_tunables/embed.conf`。
- 单测：`tests/stable_5_15_test.py::test_batch27_launch_bench`（含 selftest 逐条断言）。
- 真机原始输出：本目录 `raw_guard_run_warm.txt` / `raw_guard_run_drop.txt`
  （第 3 轮，**作为预条件陷阱的证据保留，不作性能结论**）。
- 先前的冷启动归因：`CHANGELOG.md#batch-10-6`（ceiling 与放置的关系、A/B 的 −16%）。
