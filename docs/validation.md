# 验证记录

日期：2026-09-29。环境：Windows，Python 3.9.25。当前是探索性原型；合成场景、公开读取和编码行为分别记录。机器可读摘要见 [results.json](../evals/results.json)。

## 脚本与安装

单元测试使用真实临时文件调用校验、快照、刷新、安装和打包接口，覆盖资源缺失、路径逃逸、符号链接、Windows junction、许可状态、手改哈希、冲突整包保护、备份和替换失败恢复。开发中实际观察了新增回归的失败，再修复并重跑。

最终完整命令 `python -m unittest discover -s tests -v` 发现 93 项：91 项通过、2 项跳过、0 项失败。跳过原因分别为本机无法执行 POSIX 文件名场景、临时卷未提供 8.3 短别名；没有将这两项记为已验证。Windows junction、大小写碰撞、Unicode 和替换失败恢复均实际执行通过。

Windows 文件名大小写碰撞曾导致用户文件被覆盖，已加入真实回归并修复。独立审查又确认了 Unicode casefold 误拒：`Straße` 与 `strasse` 可以是不同文件。现已按运行平台的实际路径身份处理，并通过定向复核。发布器也由扩展名收集改为逐文件允许清单，子目录中的工作记录不会被纳入，常见凭据文件明确拒绝。

元 skill 已通过 Codex skill-creator 的基础检查和本仓库 `--skill-only` 检查。安装到临时项目后，`.agents/skills/project-skill-builder/` 的元数据与本地引用可读取。校验器使用明确的 YAML 子集；检查证据格式，不证明远程内容或执行事实。

## 受控流程场景

场景定义在 [scenarios.json](../evals/scenarios.json)，项目和三个本地候选都属于合成测试材料。

| 场景 | 实际观察 |
| --- | --- |
| 需求澄清 | 删除坏行策略后，独立任务提出一个“失败还是跳过并报告”的聚焦问题；答案为 pending，未编造回答或生成正式包 |
| 候选冲突与生成 | 采用标准库与 Decimal 方法，拒绝 pandas/float 和静默跳行；记录三者取舍与 fixture 来源，公开检索为 not_run；生成包 seal/validate 通过 |
| 手动刷新 | 项目改为整数分后更新流程与用例，29 项实际检查通过；冲突时整包原样保留并旁置候选，无冲突时保留人工补充/新增文件和完整备份后更新 |

刷新第一次输出捕获出现 Windows 编码不匹配；原包未改变，失败捕获记录保留。明确使用 UTF-8 后重跑成功，没有把捕获失败隐藏成首次通过。主代理另核对了原包完整哈希清单、人工文件字节和更新后的整数分流程。

无元 skill 的生成基线也能正确选择标准库、拒绝不兼容候选。因此本轮不把候选选择本身视为已证明的改进。新流程增加了可检查的来源、阶段和刷新基线结构，这属于本次产物的具体差异。

## 真实公开读取

独立任务使用真实网页搜索、正文读取和 GitHub 工具，定向检索三个已知来源线索，核对具体 commit 与适用许可证，生成另一个原创自包含包，seal/validate 均通过。此样例证明了本次读取与合成链路，没有测量开放生态的检索覆盖率。

| 已读来源 | 固定提交 | 适用许可 | 取舍 |
| --- | --- | --- | --- |
| [Vercel find-skills](https://github.com/vercel-labs/skills/blob/3694740352eeef5cdd689af694c485f1ff62eec3/skills/find-skills/SKILL.md) | `3694740352eeef5cdd689af694c485f1ff62eec3` | [MIT](https://github.com/vercel-labs/skills/blob/3694740352eeef5cdd689af694c485f1ff62eec3/LICENSE) | rejected：安装与榜单流程不适配本次任务 |
| [Anthropic skill-creator](https://github.com/anthropics/skills/blob/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/skill-creator/SKILL.md) | `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4` | [目录内 Apache-2.0](https://github.com/anthropics/skills/blob/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/skill-creator/LICENSE.txt) | reference：访谈、资源层次与评测思路 |
| [Superpowers writing-skills](https://github.com/obra/superpowers/blob/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/writing-skills/SKILL.md) | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | [MIT](https://github.com/obra/superpowers/blob/8ca22dba9a94f28898bbce59f2537ff4d87c747d/LICENSE) | reference：基线、场景与执行证据 |

第三方正文、模板和脚本没有复制进发布项目。公开读取包的 behavior 保持 not_run；静态通过不等于执行过其维护任务。

## 小规模编码对照

每组从同一 `examples/csv-tool` 初始状态开始，在独立新上下文中接受相同已确认需求。三组继承相同模型设置，没有设置单独 token 上限。编码代理未读取独立 grader。初始项目和 grader 在运行前固定；grader 实际调用 `summarize(path)` 检查 API、精确金额、空白、空数据、坏行、缺列、行号和标准库依赖。

| 条件 | 独立检查通过数 |
| --- | --- |
| 初始项目，未修复 | 5 / 12 |
| 直接提示修复 | 12 / 12 |
| 使用生成项目 skill 修复 | 12 / 12 |
| 使用最佳本地合成候选修复 | 12 / 12 |

观察结果是平局。每种条件只有一次运行，不能推断普遍提升或统计显著性。本地候选属于 fixture，不能视为真实第三方竞品评测。可靠的逐组总 token 与任务耗时无法取得，没有补造数据。项目原件保留不完整输入校验，用于继续复现失败。

复现时为每种条件创建独立项目副本，在新的 Codex 上下文中使用相同任务：保留 summarize(path)，只用标准库和精确 Decimal；trim category；缺类别、坏金额、缺列产生清晰带行号的 ValueError 并终止；header-only 返回空结果。完成后分别执行：

```sh
python evals/grade_csv.py PATH_TO_EACH_PROJECT_COPY
```

生成过程与编码产物的原始记录保存在本地工作目录，不随发布包提供；公开摘要保留实际结果、条件与限制。

## 发布材料检查

中英文 README 和原创 SVG 流程图已进行本地 Markdown 渲染，检查桌面 1100px 与手机 390px 布局，无水平溢出。此检查没有冒充实际 GitHub 页面验证。

发布采用明确的产品文件清单，排除设计过程、原始 agent 输出、缓存、个人配置和工作目录。开发交付检查时尚未初始化 Git，因此当时只检查文件与归档。公开首发流程另行核实 GitHub 账户与 noreply 邮箱，并检查最终文件、真实暂存区及实际将推送的提交；发布记录保存在仓库之外。

36 个发布文件已通过 Gitleaks 8.30.1 和发布预检，未发现密钥或隐私问题；流程图采用按内容哈希绑定的人工视觉复核。打包后另检查最终目录与 ZIP，核对每个归档成员和文件哈希；交付检查记录保留在成品目录之外。
