# 主动回收「必然落空」的真机定位与解法 — vermeer

日期 2026-09-16（无线 adb，mDNS 名 `adb-14d93093-lnmc4L`，192.168.31.194 随机端口；
宿主机 192.168.31.28）。设备 23113RKC6C/vermeer，KernelSU root，内核
`5.15.216-android13-8-g5bfe2b8c1439`，**模块 v0.10.0**（仓库已到 v0.11.0，设备落后一版），
开机 44 分钟。

起因：`action.sh status` 报

```
zram sweeps                running (pid 4811)
proactive reclaim          not running
per-cgroup PSI policy      not running
```

2026-09-13 那次检查（`research/zram/vermeer_check_20260913/FINDINGS.md`）已把 cfr 的
"not running" 定性为「`cfr.enable=0` 的配置态、非故障」。本次把结论往下压一层：**不只是配置态 ——
即使打开 `cfr.enable`，这台设备上它也不会回收一个字节。**

## 1. 两条 "not running" 的直接原因（配置态，符合设计）

设备上 `tunables.conf` 仍是出厂默认：`cfr.enable=0`（:122）、`psi.cgroup=keep`（:161）。
`service.sh` 各有一道门，两道都判假 ⇒ 不 spawn。现场：只有 `--supervise-zram`（pid 4811），
`state/cfr.pid` 与 `state/psi.pid` 都不存在。`abk_supervisor_state` 的 "not running" 条件是
「pid 不活 **且** pid 文件不存在」，所以它报的是「从未启动过」，不是「启动后挂了」。
日志里 psi 每次开机都留一行 `psi.cgroup=keep: ...`；cfr 关闭时 `service.sh` 没有 else 分支，
因此无日志。

## 2. 内核侧两个特性都具备（不是「想开也开不了」）

| 节点 | 读数 | 含义 |
|---|---|---|
| `/sys/fs/cgroup/cgroup.pressure` | `1` | Batch 21 的 `psi_cgroup_pressure_switch` 在跑 |
| `abk_psi_policy.sh --status --cgroot /sys/fs/cgroup` | `nodes=431..442 protected=250 root=1` | aggressive 下有约 180 个节点可写 |
| `/dev/memcg/memory.reclaim` | 存在 | `memcg_v1_reclaim` graft 在跑 |
| `/dev/memcg/memory.stat` 的 `cfr_reclaim_*` | 可读，初始全 0 | 计数器渲染也在 |

PSI 工具 status 里偶发的 `refused=1..8` **不是 SELinux 拦截**：连跑四次得 3/8/0/0，是组在枚举与
读取之间生灭的竞态（工具把「节点消失」记 `vanished`、「节点在但读空」记 `refused`）；
`dmesg | grep -i avc` 里没有任何 `cgroup.pressure` 相关条目。

## 3. 「必然落空」的机制：发现深度 vs. 组的实际布局

`tools/cached_freeze_reclaim.sh` 的 `discover_groups()` 只扫 `$root` 与 `$root/apps` 两层，从不下降。
这台 ROM 的三层事实：

- `/dev/memcg/apps/` **没有子组**（v1 的 `apps` 是叶子，246 MiB）；
- `/sys/fs/cgroup/apps/uid_*` 存在（v2 冻结树），但**没有 `memory.reclaim`**（内存控制器在 v1）；
- 真正的 per-UID 组 **87 个，全在 `/dev/memcg/mimd/uid_*`** —— 比默认根深一层。

⇒ 默认根下可发现的组是 **0**，`CFR_ONE_SHOT` 每次返回 1，监督器每分钟记一条
`found no reclaimable group`。这也纠正了模块 README 里一句旧话：「这台 ROM 没有 `uid_*`」
只在一层深度上成立。

`mimd`（v1，`memory.usage_in_bytes` 64 位实读 **6672 MiB**）的子组构成（2026-09-16 快照）：

| 组 | 用量 |
|---|---|
| `uid_10297` / `uid_10300` / `uid_10194` | 951 / 891 / 827 MiB |
| `critical0` / `critical2` / `critical1` | 640 / 506 / 83 MiB |
| `uid_10321`（**当时的前台应用 pixiv**） | 305 MiB |
| 其余 80 余个 `uid_*` | 1.5 GiB 合计 |

## 4. 为什么不能只是「把 mimd 加进根里」

`mimd/uid_*` 是 **per-UID 树，不是 cached-app 树**。88 个组里有前台应用
（`com.miui.home` uid_10154，以及当时的 pixiv uid_10321），而工具的 sweep 会对**每个发现的组**
写 `memory.reclaim` —— 对前台应用就是把它的工作集换出去、下次触碰再换回来。AOSP 的
CachedAppOptimizer 正是为此只回收 cached 应用。所以「够得到」与「限得住」是同一个改动。

## 5. 现有可用的保守路径，以及它的上限

`cfr.group=freeze-app game`（出厂 conf 注释里给的路子）实测可用：

```
$ sh bin/cached_freeze_reclaim.sh --list --group freeze-app --group game \
    --cgroup-root /sys/fs/cgroup --cgroup-root /dev/memcg
/dev/memcg/freeze-app  0 bytes
/dev/memcg/game  0 bytes
EXIT=0
```

但两组常年 0 字节（ROM 只在冻结瞬间把应用挂进去），**回收量接近零**。
（注意 `cfr.group` 是空格分隔、监督器给每个词加一个 `--group`；手写
`--group freeze-app game` 会被当成 `unknown arg`，那是用法错误不是缺陷。）

## 6. 解法与真机验证（Batch 29 / companion v0.12.0）

改动：工具新增 `--frozen-only` / `--freezer-root` / `--cached-only`；companion 新增
`cfr.cgroup_root` / `cfr.frozen_only` / `cfr.freezer_root` / `cfr.cached_only` 四个键。要点：

- **额外根追加而非替换**：`--cgroup-root /dev/memcg/mimd` 让 87 个组进入发现范围；
- **两个「平台自己怎么判缓存」的过滤器，合取**：

| 过滤器 | 问的是 | 语义 |
|---|---|---|
| `--frozen-only` + `--freezer-root` | 冻结 | 平台**当前冻结着**这个组。组自带 `cgroup.freeze`/`freezer.state` 时自答；本机 v1 内存组两个节点都没有，于是按**组名**桥接到会冻结的那棵树（v1 `mimd/uid_N` 是否冻结 = v2 `apps/uid_N/pid_*/cgroup.freeze` 是否有 `1`），且**只认 `uid_*`**，命名组不猜 |
| `--cached-only` | rank | **每个任务**的 `oom_score_adj >= 900`（AOSP 的 `CACHED_APP_MIN_ADJ`）。任何用户看得见的进程都到不了这一档 |

两者都要求「组内每个任务都满足」而不是「有一个满足」—— 写入作用于整组。

真机结果（**插件还开着时**）：

```
$ sh cfr.sh --list --cgroup-root /dev/memcg/mimd --frozen-only --freezer-root /sys/fs/cgroup
/dev/memcg/mimd/uid_10085  735555584 bytes
...
EXIT=0
                                        # 88 组中选中 7 个，排除 81 个
```

前台 `com.miui.home`（uid_10154）在排除之列（`--list` 把它打成 `(not frozen)`）。

有界真回收（`--quota-mb 16`）：

| 计数 | 前 | 后 |
|---|---|---|
| `cfr_reclaim_attempts` | 1 | 12 |
| `cfr_reclaim_requested` | 8 192 | 28 163 页 |
| `cfr_reclaim_reclaimed` | 8 349 | **35 480 页** |

改动前的单组对照（证明 graft 与内核链路本来就通）：对已冻结的 `mimd/uid_10194` 回收 32 MiB，
`memory.usage_in_bytes` 866 934 784 → 832 737 280，三个计数器同步增长
（`requested 8192` / `reclaimed 8349` 页）。

监督器 argv 拼装的本地夹具验证（假工具抓 argv）：

```
--cgroup-root /sys/fs/cgroup --cgroup-root /dev/memcg --cgroup-root /dev/memcg/mimd \
  --frozen-only --freezer-root /sys/fs/cgroup
# cfr.frozen_only=0 时：--cgroup-root /sys/fs/cgroup --cgroup-root /dev/memcg
#   （freezer_root 被丢掉，而不是原样传下去）
```

## 6b. 反转：先前那批冻结不是平台行为，是用户外装的 LSPosed 插件

用户随后说明：第一批测到的 9 个 v2 冻结 pid 来自他装的一个 LSPosed 插件。重启关掉插件后，
冻结档**没有归零，而是大大收窄** —— 这比「平台不冻结」更值得记：

| 采样 | 冻结 pid 数 |
|---|---|
| 开机后 2–6 分钟（6 次采样） | **0** |
| 开机后 17 分钟起，连续 7 分钟（每分钟一次） | **1**，7 次都是同一个 |

唯一被冻结的是 `id.gms.unstable`（uid 10259，`oom_score_adj=945`）—— **平台自己的
CachedAppOptimizer 在正常工作**：它只 park 真正进了缓存档的进程，而且要有延迟。那个插件则连
`adj=201/410`（可感知档）的应用一起冻，那不是平台会做的事。

`device_config get cached_apps_freezer` 在本机返回过 `device_default`，也返回过
`Bad arguments`（rc=255），**不足为凭，只有实测算数**。

同一时刻两个过滤器的选择面：

| 过滤器 | 选中组数 |
|---|---|
| `--frozen-only --freezer-root /sys/fs/cgroup` | **1**（uid_10259，156 MiB） |
| `--cached-only` | **14** |

即 **frozen 是 cached 的真子集**，且要等平台先动手；cached 在第一趟就正确。两者都保留：
frozen 是更强的承诺（冻结进程**不可能**把页换回来），cached 是更宽的覆盖。

**教训**：先前「平台会冻结缓存应用，可以用冻结当 cached 判据」这一步**并没有错**，错的是**样本** ——
它被一个第三方扩展放大了，以至于「冻结 ⊂ 缓存」这个真实关系看起来像等号。判据要问平台自己的排序
（`oom_score_adj`），冻结状态只当**加强**用，不当**判据**用。

有界回收（`--quota-mb 16`）：`cfr_reclaim_reclaimed` **0 → 29 448 页**
（`attempts` 0 → 89，`requested` 0 → 41 689）。

**安全对照（本批最关键的一次测量）**：两个含可见进程的组在一次 sweep 前后

| 组 | 组内最小 `oom_score_adj` | 前 | 后 | 变化 |
|---|---|---|---|---|
| `uid_10142` | **0**（组里有前台进程） | 107 438 080 | 107 192 320 | −0.2% |
| `uid_10205` | 100（可见） | 91 746 304 | 91 082 752 | −0.7% |

是运行时噪声，不是回收 —— 若被回收，每组最多会掉 16 MiB（当时的 quota）。两者在 `--list` 里
都标为 `(not fully cached)`。

## 7. 一个仍然悬着的问题：ROM 自己已经在做这件事

设备上 `persist.sys.mimd.reclaim.enable=true`，`persist.miui.extm.enable=1`，miui extm 版本 4.0，
`vendor.xiaomi.hardware.mimd@2.0-service`（pid 2132）**打开着 `/proc/pressure/memory`**（PSI 监听），
`/proc/mimdlog` 里有 `{ global_reclaim }` 记录；v1 上厂商另有自己的 `memory.reclaim_once` 节点。

也就是说 HyperOS 的 mimd 本身就是一个 PSI 驱动的内存回收守护进程。**本批没有去判定它与本模块的
cfr 是否重叠、谁更有效** —— 这需要一个两态 A/B（关掉 cfr / 打开 cfr，比 mimd 的回收量与
`workingset_refault`），本轮没做，所以**不主张本模块的 cfr 打开后比现状更好**。
出厂默认仍保持 `cfr.enable=0`。

## 8. 复现用的一行命令

```bash
adb shell 'dumpsys activity activities | grep -m1 topResumedActivity'   # 前台是谁
adb shell 'ls -d /dev/memcg/mimd/uid_* | wc -l'                          # 真 per-UID 组数
adb shell 'cat /dev/memcg/memory.stat | grep cfr_reclaim'                # graft 计数器
adb shell 'cat /sys/fs/cgroup/apps/uid_*/pid_*/cgroup.freeze 2>/dev/null | sort -u'  # 有没有冻结
adb shell 'device_config get cached_apps_freezer'                        # 平台自己的冻结开关
adb shell 'for t in $(cat /dev/memcg/mimd/uid_10321/tasks); do cat /proc/$t/oom_score_adj; done'  # 某组缓存档
adb shell 'sh /data/adb/modules/abk_runtime_tunables/bin/cached_freeze_reclaim.sh \
  --list --cgroup-root /dev/memcg/mimd --cached-only'
```

设备上留了一份改动前的测试副本 `/data/local/tmp/cfr_new.sh`（Batch 29 的工具本体）；
模块 `<bin/>` 里的那份要等下次升到 v0.12.0 才会更新。
