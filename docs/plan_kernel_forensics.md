# Plan — 内核版本 / vermagic 取证收口与 ABK_5.15_BACKPORT 反哺

> 依据：codex 线程 `8d2832e6-9816-4079-b2cb-8256bdc7bf94`
> （4 轮：查手机真实内核 → root 后核实 → 查 "official" 5.15.178 vermagic → 判定是否伪装）。
> 状态词沿用 plan.md：`[ ]` 候选 / `[~]` 延后 / `[x]` 已落地 / `[-]` 排除。

## 一、已确认结论（线程已落地，不再重做）

### 1. 设备画像
- Redmi K70（`vermeer` / 型号 23113RKC6C），骁龙 8 Gen 2（`kalama`），
  Android 17 / SDK 37 的 HyperOS 深度定制；跑 KernelSU + Zygisk/LSPosed。
- `ro.kernel.version = 5.15`（平台登记基准），与实测 5.15.x 吻合。

### 2. 三个内核的定性（核心结论）

| # | 内核 | 版本 / vermagic | 性质 |
|---|---|---|---|
| A | 设备运行内核 | `5.15.195-MorizukiNeko-260620LTSC`（clang 22，2026-06-20） | 第三方定制内核，**真实**、非伪造 |
| B | 目录叫 "Official" 的 5.15.178 | `5.15.178-202609202-FanZiyun`（clang 14.0.7，2024-12-01，`build-user@build-host`） | **真实可引导**，但**非小米原厂**——是打上个人标签 `FanZiyun` 的自定义编译 |
| C | 小米原厂 `boot_a.img` | 未解包 | K70 OTA 里的真原厂（待取证） |

### 3. "5.15.194 是伪装的"——证伪
- 全机内核二进制里 `5.15.194` 计数 = **0**；真实版本是 `5.15.195`。
- `uname` / `/proc/version` / `/proc/sys/kernel/{osrelease,version}` / boot_a 分区内核
  镜像内嵌字符串 / 模块 vermagic 五路全部一致指向 `5.15.195-MorizukiNeko-260620LTSC`。
- 结论：`.194` 大概率是 App 层（LSPosed/Xposed）hook 出来的假值，非真实内核。

### 4. "official" bundle 的真实身份（关键新发现）
`Official_kernel-android13-5.15-178/.../ABK_BUNDLE_MANIFEST.json` 显示该 boot-gz.img
**本身是 ABK 构建产物**：`artifact_type=KERNEL_IMG`、`run_id=33650918785`、
payload 名 `android13-5.15.178-2025-03-boot-gz.img`。即目录名里的 "Official" 是
误导，它是本仓库 CI（`android13-5.15-2025-03` 基线）编译出来的内核，标签 `FanZiyun`
正是构建者身份——**"伪装成官方" 属实，但 "假内核/坏文件" 不属实**。

### 5. vold / 存储侧（线程已抽查）
- `dumpsys mount`：真实 256GB 内部存储、真实 emulated volume、UUID `140C-0D0F`，
  存储栈是真实硬件，非模拟。**但 vold 二进制/版本本身未做深度取证**（见 Phase A-5）。

## 二、待办清单

### Phase A — 取证收口（把线程没做完的三方对比做完）

- [ ] **A-1 解包小米原厂 `boot_a.img` / `boot_b.img` 取 vermagic + banner**
  - 路径：`~/Downloads/K70/P-vermeer-ota_images-v4.0.7-OS4.0.0.24.XNKCNXM-user-17.0/images/boot_a.img`（100MB，另有 `boot_b.img`、`init_boot.img`、`vendor_boot.img`）
  - 方法同线程：设备端 `magiskboot unpack`（或宿主机 `magiskboot`/`unpack_bootimg`）→ `strings kernel | grep -m1 "Linux version"` + 取 vermagic 完整串
  - 验收：拿到原厂内核的 `vermagic` 完整串 + 内嵌 banner + `os_version`（应为非 0，与 "official" 的 `os_version=0` 对照）
- [ ] **A-2 三方对比表（A/B/C）**
  - 列：vermagic、banner、编译者、clang 版本、构建日期、boot 头 `os_version`、机型标识（`vermeer`/`xiaomi`/`redmi` 计数）
  - 验收：一张表能一眼看出「真原厂 vs ABK 产物(FanZiyun) vs 第三方(MorizukiNeko)」的差异
- [ ] **A-3 验证 B（"official" 5.15.178）是否 "官方全库"**
  - 若该 bundle 配套有 `.ko` 模块集：逐模块 `modinfo`/`vermagic` 比对，确认是否严格 `5.15.178-202609202-FanZiyun`；不匹配即拼凑，不是全库
  - 验收：明确回答线程结尾的遗留问题——"这个镜像是不是真的官方全库"
- [ ] **A-4 三路关闭 "5.15.194" 疑点**
  - 对 A/B/C 三个内核二进制各 `grep -a -c "5\.15\.194"` 与 `grep -a -o "5\.15\.19[0-9][0-9]"` 计数
  - 验收：三路均无 `.194` 残留，书面结论可引用
- [ ] **A-5 vold 取证收口（线程 turn-1 的 "查看 vold 等东西" 只做了一半）**
  - `adb shell` 下：`vold --version` / 读 `vold` 进程 `/proc/<pid>/cmdline` 与 build 版本、
    `dumpsys mount`、`/proc/mounts` 的 emulated/fuse 挂载点、`/data/system/packages.xml` 的
    `me.weishu.kernelsu` / LSPosed 组件 → 判定 vold 是否也被应用层 hook 过
  - 验收：能区分「vold 真实」与「vold 被伪装」的证据链

### Phase B — 反哺 ABK_5.15_BACKPORT 项目

- [ ] **B-1 确认 ABK 构建产物的 `FanZiyun` 标签来源**
  - 在本仓库/CI 配置里定位 `run_id=33650918785` 对应工作流，确认 boot-gz.img 是
    `android13-5.15-2025-03` 基线的构建输出，且 `FanZiyun` 是构建 user 标签而非内核源改动
  - 验收：能一句话说明 "official 5.15.178 = 本仓库 2025-03 基线的 ABK CI 构建产物"
- [ ] **B-2 判定设备真实上游基线 sublevel**
  - 用 A-1 的原厂 `boot_a.img` vermagic 对照 AOSP `android13-5.15` 各分支（167/178/194/211）
    确定这台 K70 原厂内核对应哪个 sublevel，作为后续 backport 的「真基准」
  - 验收：写明设备原厂内核的 sublevel 与 `Makefile SUBLEVEL` 对应关系
- [ ] **B-3 是否新增 5.15.195 关注项**
  - 运行内核是 `5.15.195`，且项目已引用 5.15.195（`fdtable_replace_fd_errno`，
    `ff8ec0dbe0150`）。评估：是否需要为「定制 5.15.195」加一档 fixture/探针，
    或仅作为文档记录不新增分支
  - 验收：明确 decision + 理由，写入 plan.md 候选区或排除区
- [ ] **B-4 文档沉淀**
  - 把 Phase A 取证结论 + B-2/B-3 决策写回 `docs/`（survey 或新 doc），并在 `plan.md`
    增加一行指向本计划，避免后续重复争论 ".194 是否伪装"
  - 验收：`plan.md` 与 `docs/` 有一处可检索的权威结论，后续无需重跑取证

### 排除 / 延后（不重议）

- [-] 继续从运行内核深挖 "5.15.194 是否存在于某个分区"：已在 A 内核二进制 grep 计数=0，
  且五路证据一致，视为关闭。
- [~] 对 `init_boot.img` / `vendor_boot.img` 做完整 unpack 取证：除非 A-1/B-2 需要
  vendor 模块 vermagic 佐证，否则延后。
