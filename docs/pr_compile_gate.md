# PR 编译门禁：拿别的仓库编这份 PR 的内核

本仓库（`fanziyun/ABK_5.15_BACKPORT`）是 ABK 的外部 `module_set`，改一行 `scripts/*.py`
就可能让内核编译不过。这个门禁把「**这个 PR 的代码能不能编过**」变成 PR 上的一条状态检查：
把 PR 的 commit 钉进 ABK 描述符的第三字段，派发 `fanziyun/ABK` 的「Android 内核构建-自定义」
（`kernel-custom.yml`），等它跑完，从日志取证，再把结论回写到 PR。

工作流：`.github/workflows/abk-kernel-compile.yml`（`pull_request_target`，**永不 checkout PR 代码**）。

---

## 1. PR 上会看到什么：两条检查，来源不同

| 名字 | 谁报的 | 含义 |
| --- | --- | --- |
| `Dispatch ABK kernel build` | GitHub Actions 自动挂到 PR head sha（工作流名 `ABK kernel compile gate`） | 门禁这个 job 自身：没配 token / 不认识 `;ref=` / 找不到 run / 构建超时 / 结论为红，都会让它红 |
| `ABK kernel compile (android13-5.15-lts)` | 工作流最后一步 `Report result to the PR`，`POST /repos/{repo}/statuses/{head_sha}` | **真正的编译结论**，描述是一句人话（见第 8 节），`target_url` 指向 ABK 的 run，点进去是构建日志 |

为什么结论要单独写一条 **commit status** 而不是让 Actions 自动报：真正的编译发生在
`fanziyun/ABK`，`workflow_dispatch` 的 check 挂在那边的 ref 上，**不会**出现在本仓库的 PR 里，
只能由本仓库这条 `pull_request_target` 工作流回写。两者都在 PR 的 “Checks” 区域，也都能被
分支保护 require（名字不同、命名空间不同）。

时间线（同一条 status 原地更新）：决定要跑 → `pending`「ABK 编译中（最多 90 分钟）」；
跑完 → `success` / `failure` / `error`。跳过规则的 PR 不写 `pending`（见第 3 节）。
**绿只在有取证时出现**：`head_sha` 与 `pinned_ref` 两项自证都必须过、且模块自报没有「没落地的组」，
否则一律红（第 2 节第 6 步、第 9 节）。

除检查之外，PR 上还有一条**就地更新**的评论（带 `<!-- abk-compile-gate -->` 标记，重跑改同一条）
和本次门禁 job 的 Summary，内容都含取证段：ABK 日志里的 `head_sha` 是否等于 PR head sha、
`pinned_ref` 是否等于钉的 ref、模块版本是否对得上，以及模块分组状态与首批 `error:` 行。

## 2. 链路（8 个 step）

1. `Resolve PR and gate`：API 读 PR 的 `state`/`draft`/`head.sha`/`head.repo`/改动文件 → 判定跑或跳过；
   要跑就先写 `pending`；顺手在这里卡一次 `ABK_CI_TOKEN` 是否存在（缺了就红，见第 4 节）。
2. `Build dispatch payload`：按 PR head sha 取 `module.conf`，解析 `ABK_MODULE_SET_ITEMS` 的 child，拼
   `custom_external_modules`；每个 child 的第三字段是 `;ref=refs/pull/<N>/head`。版本名固定为
   `<ABK_CI_VERSION_BASE>-pr<N>-<sha7>`，下文叫 **marker**；同时写下 `expected_ref.txt` 供取证比对。
3. `Cancel superseded run for this PR`：取消 ABK 里同一 PR 尚未完成的旧 run。
4. `Dispatch ABK build`：先验「目标 ref 上的 `kernel-custom.yml` 认识 `;ref=`」（缺 `run-name: ABK-CI` 就
   立刻红，不去白等 90 分钟），再 `workflow_dispatch`。
5. `Wait for the ABK run`：按 run-name `ABK-CI <version>` 里的 marker **子串**找 run（并用派发时刻排除旧 run），
   每 60 秒轮询到 `completed`，上限 `WAIT_MINUTES=90` 分钟。
6. `Collect evidence from the ABK log`：拉日志取证 —— `head_sha` 与 `pinned_ref` 两项自证、模块版本，以及模块
   自报的分组状态（`partial` / `blocked_by_*` 判红，那是「编过了但 graft 没落地」）；错误行会先滤掉
   GitHub 回显的脚本源码，只留真错误。
7. `Upload debug bundle`：payload、日志、取证、结论等留 14 天（排查用）。
8. `Report result to the PR`：写 commit status + 更新评论 + 写 Summary；结论红时**同时**让 job 失败，两条检查一致。

> 第 2/5 步依赖 ABK 侧的 `;ref=` 第三字段与 `run-name: ABK-CI ${{ inputs.version }}`，两者都在
> `fanziyun/ABK` 的 PR #5（分支 `ci/module-ref-pin`）里。合并前必须把 var `ABK_CI_WORKFLOW_REF`
> 指到 `ci/module-ref-pin`；合并后删掉这个 var（回落默认 `dev`）。指错了第 4 步会立刻红。

## 3. 哪些 PR 不编（跳过规则）

| 情况 | 判定 | 写不写 status |
| --- | --- | --- |
| 同仓库分支 PR | 自动编 | — |
| fork PR | 需要维护者打 `ci:compile` 标签（打这个标签本身会触发） | **不写**（未验证） |
| draft PR | 跳过（`ready_for_review` 时自动再跑） | **不写**（未验证） |
| 已关闭 / 非 open | 跳过 | **不写** |
| `labeled` 事件但标签不是 `ci:compile` | 跳过 | **不写** |
| 改动全在 `docs/` `research/` `tmp/` `.claude/` `.github/` `CHANGELOG.md` | 跳过（不碰内核树，编不出名堂） | `success`「无可验证内容」 |

两种跳过的区别是刻意的：

- **no-change**（改动没碰内核树）**写绿**：这次确实没有可验证的东西，绿是诚实的。
- **not-verified**（fork 未打标 / draft / 已关闭 / 无关标签事件）**不写 status**，只更新一条评论说明原因。
  写绿等于「没编过也能绿灯」，把这条 status 放上分支保护就成了摆设；不写则让 required 检查停在
  `Expected — Waiting for status to be reported`，该拦的照样拦。
- 任何跳过都**不会覆盖**同 sha 上已写下的真实结论：commit status 按 `context` 取最新一条，所以已编红的 PR
  不会因为有人加个标签就变绿（第 8 节）。
- 无关标签事件走独立的 concurrency 组，不会把正在等的那次真门禁取消掉。

## 4. 一次性前置（**配齐之前不要合并到 main**）

1. **secret `ABK_CI_TOKEN`** —— fine-grained PAT：**Repository access 只选 `fanziyun/ABK`**，
   **Repository permissions 只勾 `Actions: Read and write`**，别的一个都不要给。
   `pull_request_target` 自带的 `GITHUB_TOKEN` 只能写本仓库，派发不了别的仓库的工作流，所以必须要它。

   它在这个工作流里只干三件事：**派发** ABK 构建、**读** ABK 的 run/jobs/日志、取消**它自己派发过**的被顶替 run。

   | 权限 | 给不给 | 原因 |
   | --- | --- | --- |
   | `Actions: Read and write` | ✅ 给 | 派发构建 + 读 run/日志 + 取消自己那次被顶替的 run |
   | `Metadata: Read-only` | 自动带上 | fine-grained PAT 的强制项，只读仓库元数据 |
   | `Contents` | ❌ 不给 | 读 `module.conf` 用本仓库的 `github.token`；读 ABK 侧工作流源码走匿名 CDN（第 7 节第 4 条） |
   | `Pull requests` | ❌ 不给 | 评论与 status 全部用本仓库 `github.token` 写 |
   | `Workflows` / `Administration` / `Secrets` / `Variables` / `Webhooks` | ❌ 不给 | 本工作流不碰 |

   一句话：**它只能触发/取消 `fanziyun/ABK` 的 Actions 并读它的日志，写不了任何代码，也开不了、审不了、
   合不了 PR**（那是 `Contents` 与 `Pull requests` 权限，都没给）。过期时间挑 30–90 天，到期换新。
2. **var `ABK_CI_WORKFLOW_REF`** —— 目前填 `ci/module-ref-pin`；ABK PR #5 合并后删掉（不设即默认 `dev`）。
3. **var `ABK_CI_EXTRA_MODULES`**（可选）—— 与出厂构建同形的其它模块描述符，换行分隔，留空＝只注入本仓库 child
   （**留空不等于出厂组合**，见第 9 节）。
4. **var `ABK_CI_VERSION_BASE` / `ABK_CI_BUILD_TIME`**（可选）—— 版本名前缀（默认 `-202609202-FanZiyun`）与固定构建时间。
5. **label `ci:compile`** —— fork PR 重跑用；本仓库目前**没有**这个标签，需要建。
6. **合并到默认分支 `main`** —— `pull_request_target` 只在**默认分支上存在该工作流文件**时触发，
   在此之前任何 PR 的 Checks 里都不会出现这两条检查。

```bash
R=fanziyun/ABK_5.15_BACKPORT
gh secret set ABK_CI_TOKEN --repo $R          # 粘贴 PAT（只授权 fanziyun/ABK，Actions read/write）
gh variable set ABK_CI_WORKFLOW_REF --repo $R --body ci/module-ref-pin
gh variable set ABK_CI_EXTRA_MODULES --repo $R --body ""   # 可选
gh label create ci:compile --repo $R --description "fork PR 触发 ABK 编译门禁" --color 0e8a16
```

建好 PAT 后自己验一遍「它做不到什么」。公开仓库本来就人人可读，所以真正要守住的是**写**：

```bash
P=<新 PAT>
# 应当成功 —— 这正是门禁唯一需要的能力
GH_TOKEN=$P gh api "repos/fanziyun/ABK/actions/runs?per_page=1" --jq '.workflow_runs[0].id'
# 以下都应当被拒（404/403）——写代码、开/合 PR、改仓库设置
GH_TOKEN=$P gh api -X PUT repos/fanziyun/ABK/contents/abk-ci-probe.txt -f message=probe -f content=eA==
GH_TOKEN=$P gh pr close 3 --repo fanziyun/ABK
GH_TOKEN=$P gh api -X PATCH repos/fanziyun/ABK -f description=probe
```

前置没配齐（或 PAT 到期）时，**只有「真的要编」的 PR 会红**，描述是 `没配置 ABK_CI_TOKEN`；
跳过类事件（draft、doc-only、非 `ci:compile` 标签）本来就不需要用凭据，所以不连坐 —— 否则 PAT 一到期，
连只改文档的 PR 都会红。这是有意的：宁可红着喊出声、也不让编译门禁假装通过，但也不给不需要编译的 PR 制造噪音。

**轮换 / 到期。** PAT 到期或轮换那天，只需要重跑一次上面那条 `gh secret set`（交互输入新值），
不需要动任何别的东西：门禁的其余部分与本仓库的 `GITHUB_TOKEN` 无关。换完验一次的方法：
给任意一个改到内核树、还没结论的 PR 打/重打 `ci:compile` 标签，看那条
`ABK kernel compile (android13-5.15-lts)` 是否从 `pending` 走到终态。
期间 draft / 纯文档 PR 照常不受影响 —— 那是第 9 号改动特意保证的：跳过类事件不需要凭据，不连坐。

## 5. 手动重跑

- 给 PR 打 / 重打 `ci:compile` 标签（同仓库 PR 也认，是最轻的重跑方式）；
- close → reopen，或推一个新 commit（走 `synchronize`）；
- Actions → `ABK kernel compile gate` → *Run workflow*，填 `pr_number`（可选 `abk_ref` 覆盖本次 ABK ref）。

同一 PR 只跑最新一次：`concurrency` 组是 `abk-compile-<PR号>`、`cancel-in-progress: true`，新门禁会取消旧的，
并顺手取消 ABK 里被顶替的 run。被取消的那次可能把 `pending` 留在**旧 sha** 上 —— 旧 sha 不再是 PR head，
不再阻塞合并；当前 head 会由新一轮门禁自己收尾。

## 6. 分支保护

require 选 **`ABK kernel compile (android13-5.15-lts)`**（有编译结论的那条）。`Dispatch ABK kernel build`
建议**不要** require：它红了意味着门禁自己崩了（超时、没配 token、ABK 侧不支持 `;ref=`），而结论那条
已经会把真实结果报出来。

## 7. 安全边界（改这个文件之前先读这一段）

1. 本工作流**永不 checkout PR 代码**，只用 GitHub API 读元数据；PR 代码只在 ABK 的构建 job 里被执行，
   那里的权限模型与 App/CLI 派发构建时一致。因此这里用 `pull_request_target`（拿 write token + secrets）是安全的。
   **任何往本文件加 `actions/checkout`（PR ref）的改动都必须否决**：那等于把 write token 和 secret 交给 PR 内容。
2. PAT 只进需要它的 step：job 级 `GH_TOKEN` 是 `ABK_CI_TOKEN`，而**读 PR 内容**（`module.conf`）和
   **解析日志**的两步把它覆盖成 `github.token`；写 PR 评论/状态的步骤也用 `github.token`。
   往 job 级 env 里塞新东西（或给这两步改回 PAT）之前，先想清楚这一步会不会拿到 PR 控制的输入。
3. `;ref=refs/pull/<N>/head` 只让 ABK 去 clone PR 的 ref，不改变上面两条。secret 只授 `fanziyun/ABK` 的
   `Actions` 读写 —— **不要顺手给它 `Contents`**：PAT 能写代码，这个 secret 就从「能派发构建」升级成
   「能改 fanziyun/ABK 的源码」，而门禁一行都不需要它（第 4 节第 1 条的权限表）。
4. **凭据最小化是刻意做出来的，别改回去**：契约预检读的是 ABK 侧工作流的源码，走的是
   `raw.githubusercontent.com` 的**匿名** CDN（公开仓库人人可读），不用 token；
   早先那版用 `gh api .../contents`，那需要 `Contents: read`。同理，读本仓库的 `module.conf` 也只用
   `github.token`。加任何新的跨仓库读取之前先问一句：这一步非用那个 PAT 不可吗？
5. `ABK_CI_EXTRA_MODULES` 是「把任意模块描述符塞进 ABK 构建」的开关（仓库 variable 只有维护者能改）—— 改它等于
   决定 ABK 构建机上会跑谁的代码，所以它和 secret 一样是可信输入，别开放给 PR 作者。
6. 评论内容含 ABK 日志原文，而日志里有 PR 作者控制的部分：证据行会先剥掉 markdown 围栏与 `<!--` 注释，
   就地更新只认 `github-actions[bot]` 自己发的评论，避免被劫持。

## 8. 排查表（描述 → 原因 → 处理）

| status 描述 | 多半是 | 处理 |
| --- | --- | --- |
| `没配置 ABK_CI_TOKEN（见 docs/pr_compile_gate.md）` | secret 没配 | 第 4 节第 1 条 |
| `ABK@<ref> 的 kernel-custom.yml 不认识 ;ref=（…）` | `ABK_CI_WORKFLOW_REF` 指到了不含 `;ref=` 支持的 ref | 指到 `ci/module-ref-pin`，等 ABK PR #5 合并 |
| `ABK 构建 failure（失败 job: …）` | 真的编不过；失败 job 名会写出来（同 run 还有 get-manager 等 job） | 看 ABK run 日志 / 评论里的 `error:` 段 |
| `编的不是这份代码：ABK 日志里的 head_sha 对不上` | 日志里没有本 PR 的 head sha | 看评论取证段与 ABK 日志；多半是 ref 钉错或被顶替 |
| `ABK 没按钉住的 ref 检出模块（日志里没有对应的 pinned_ref）` | ABK 忽略了第三字段（旧版工作流） | 同第二条 |
| `编译通过但模块版本对不上，八成编的不是这份代码` | 编的是别的分支的模块 | 同上 |
| `ABK 构建成功但取证失败，无法证明编的是这份代码` | 日志下载/取证步骤失败 | 看门禁 job 日志与 artifact；这是**故意的红** |
| `ABK 构建超时未完成` | 构建超过 `WAIT_MINUTES`(90) | 看 ABK run；必要时改工作流里的 `WAIT_MINUTES` 与 `timeout-minutes` |
| `没在 ABK 里找到对应的 run` | ABK 侧没接单：dispatch 权限、队列、run-name 变了 | 去 `fanziyun/ABK` 的 Actions 看有没有新 run |
| `取证失败：ABK 日志里没有 head_sha 行，拿不到归属证据` | 日志格式变了或没下全 | 看门禁 job 与 artifact；这是**故意的红** |
| `编译通过但有没落地的组：<组>` | 编过了，但 graft 没落地（本地审计盯的就是这类） | 看评论里的分组状态段 |
| `内核编译失败（job: …）` | 内核编译 job 自己失败 | 看 ABK run 日志 |
| `ABK 侧其它 job 失败：…（内核编译结果未知）` | 同 run 的 get-ksu-manager 等 job 失败 | 去 ABK 看那个 job；内核没编到，所以不给绿 |
| `ABK 编译门禁内部错误` | 门禁自身出问题（缺文件、API 失败） | 看 job 日志 |
| 评论说「未验证」但 checks 里没有这条 status | 命中 not-verified 跳过（第 3 节） | 打 `ci:compile`，或把 draft 转正 |
| `跳过：…（改动没碰内核树…）` 且是绿的 | 命中 no-change 跳过 | 正常；真要编就打 `ci:compile` |
| 长期 `pending` | 正在编（最多 90 分钟）；或被取消/超时后留在旧 sha 上的 pending | 等，或推新 commit；旧 sha 不再阻塞 |

## 9. 已知限制

- **run 级结论 + job 归因**：判定读 ABK run 的 `.status/.conclusion`（它聚合同 run 的所有 job，`kernel-custom.yml`
  还有 `get-ksu-manager`），红了再去 `/runs/<id>/jobs` 看是谁红的：名字以 `<kernel>.<sub>-<android>-<os_patch>`
  （默认 `5.15.X-android13-lts`，见 `ABK_JOB_PREFIX`）开头的那个才是内核编译 job —— 它红报 `failure`，
  别的 job 红报 `error`「内核编译结果未知」。
- **模块自报状态参与判定**：日志里 `[ABK stable_515_backport] <child>/<group>: <status>` 的 status 不在
  `applied / already_present / skip_suite_processed / report_only` 之内（例如 `partial`、`blocked_by_*`）就判红，
  因为那意味着「编过了但 graft 没落地」。一行分组状态都没有（日志被截断）时不判红：无证据 ≠ 有罪。
- **模块组合**：默认 `ABK_CI_EXTRA_MODULES` 为空＝只注入本仓库的 3 个 child。出厂构建是多个模块共存，
  而 KMI 槽位/anchor 冲突只在共存时才暴露，所以「默认配置绿」不等于「出厂能编」。要与出厂同形就把出厂描述符
  填进 `ABK_CI_EXTRA_MODULES`。
- **模块开关档位**：门禁只通过 `custom_kernel_options` 传 `CONFIG_ZRAM_WRITEBACK=y`；模块自己的 tier 开关
  （`ABK_515_DEFCONFIG_ROM` / `_PSI` / `_ALIGN` 等）是**本地环境变量**，ABK 的构建没有传这些 env 的通道，
  所以门禁编的是「模块默认档 + 内核片段」这个组合，不等于任何一种线上档位。要覆盖某一档，得先让 ABK 侧
  支持把这些 env 传进去。
- **超时**：90 分钟用尽时门禁会顺手取消那个 ABK run（别继续烧 CI），按「超时未完成」报红；门禁 job 自己撞上
  `timeout-minutes: 150` 被杀时，那条 `pending` 会留在被杀的 sha 上（旧 sha 不再阻塞合并）。
- **`refs/pull/<N>/head` 是可变 ref**：门禁记下 head sha 后再去 ABK clone，中间若有人 push，可能编到新提交而
  断言对旧 sha（红，偏保守）；若 ABK 已 clone 完才 push，绿写在被顶替的旧 sha 上（无害，新 commit 会触发新门禁）。
- **取证是「附条件的证据」**：`head_sha:` / `pinned_ref:` 行由 ABK 在 clone 后打印，但同一 job 日志里也混着模块
  自己的 stdout，所以它是给维护者看的证据、不是密码学意义上的不可伪造证明；真的防伪要靠 ABK 侧的构建链。
- **时间与保留**：等待上限 90 分钟、job 上限 150 分钟（`WAIT_MINUTES` / `timeout-minutes` 都是工作流里的字面量，
  改它要改文件）；debug artifact 保留 14 天。没有缓存、没有增量：每次都是整颗内核完整构建。
- **门禁只回答「编不编得过」**，不回答「行为对不对」——那仍然靠 `tests/step_audit.py`、`tests/implementation_audit.py`、
  `tests/smoke.sh` 三条本地审计。
- payload 里的 kernel/Android/os_patch 与 21 个开关是 ABK `kernel-custom.yml` 契约的显式快照，钉住是为了让门禁
  不随 ABK 默认值漂移；ABK 加了新 input 时这里要跟着补。

## 10. 自己改这个门禁时怎么验

最省事的自检（本机 Windows + WSL 亦可）：

```bash
# YAML 合法性
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/abk-kernel-compile.yml'))"
# 每个 run: 块的 shell 语法（等于 CI 的语义检查）
python3 - <<'PY'
import yaml, pathlib, subprocess, tempfile, os
wf = yaml.safe_load(pathlib.Path(".github/workflows/abk-kernel-compile.yml").read_text(encoding="utf-8"))
for i, step in enumerate(wf["jobs"]["abk-compile"]["steps"]):
    if "run" not in step:
        continue
    fh = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8")
    fh.write(step["run"])
    fh.close()
    subprocess.run(["bash", "-n", fh.name], check=True)
    os.unlink(fh.name)
    print("ok", i, step.get("name"))
PY
```

链路真跑一次的最小样本：拿一个只动 `scripts/*.py` 的 PR（例如曾经的 `ci/gate-selftest` 正对照分支，
它给 `scripts/abk_stable_perf.py` 末尾加一行 marker），打 `ci:compile` 标签，然后确认 PR 上出现上面那两条检查、
评论里 `head_sha` 与 `pinned_ref` 自证都是 ✅。**用完把那条 marker 分支删掉**（它不是产品代码）。
